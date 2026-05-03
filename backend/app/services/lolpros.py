"""
Lolpros.gg integration via their (unofficial) public API.

Endpoints used (publicly served at api.lolpros.gg):
  - GET /es/ladder?page=N            paginated list of all tracked pros (20 / page)
  - GET /es/teams                    list of all teams
  - GET /es/teams/{slug}             team details with current roster

The ladder entries already carry the summoner_name (Riot ID) + current team +
country + position, which is exactly what we need to populate PlayerMeta.
This avoids the heavy Cargo rate-limit problems of the Leaguepedia path.

Schema we rely on (excerpt from /es/ladder):
  {
    "name": "SkewMond",
    "slug": "skewmond",
    "country": "FR",
    "position": "20_jungle",          # 10_top / 20_jungle / 30_mid / 40_adc / 50_support
    "team": {"name": "G2 Esports", "tag": "G2", "slug": "g2-esports"},
    "account": {"summoner_name": "G2 SkewMond#3327", "tier": "00_challenger", ...},
    ...
  }

There's no public auth required. Rate-limit is gentle (we still pace requests).
"""
import logging
import re
import time
from datetime import datetime, timezone
from typing import Iterable

import httpx
from sqlalchemy.orm import Session

from ..models import Player, PlayerMeta

logger = logging.getLogger(__name__)

API_BASE = "https://api.lolpros.gg"
USER_AGENT = "ChallengerScoutingBot/0.1 (internal)"
REQUEST_DELAY_SEC = 0.4  # ~2.5 req/s — polite

POSITION_TO_ROLE = {
    "10_top": "TOP",
    "20_jungle": "JGL",
    "30_mid": "MID",
    "40_adc": "ADC",
    "50_support": "SUP",
}


def _normalize(s: str) -> str:
    if not s:
        return ""
    s = s.split("#")[0]
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _candidates(s: str) -> list[str]:
    """Same multi-strategy normalization as the Leaguepedia matcher."""
    if not s:
        return []
    base = s.split("#")[0].strip()
    out, seen = [], set()

    def push(x: str):
        n = re.sub(r"[^a-z0-9]", "", x.lower())
        if n and n not in seen:
            seen.add(n)
            out.append(n)

    push(base)
    push(re.sub(r"^(twtv|trainer|coach|sub)\s+", "", base, flags=re.I).strip())
    m = re.match(r"^([A-Z0-9]{1,5})\s+(.+)$", base)
    if m:
        push(m.group(2))
        push(m.group(2).split(" ")[-1])
    no_suffix = re.sub(r"\s+(NEXT|academy|smurf|alt|main|\d+)$", "", base, flags=re.I).strip()
    if no_suffix != base:
        push(no_suffix)
    if m:
        cleaned = re.sub(r"\s+(NEXT|academy|smurf|alt|main|\d+)$", "", m.group(2), flags=re.I).strip()
        push(cleaned)
        push(cleaned.split(" ")[-1])
    parts = base.split(" ")
    if len(parts) > 1:
        push(parts[-1])
    return out


def fetch_ladder(server: str = "EUW", max_pages: int = 200) -> list[dict]:
    """
    Walk /es/ladder until pages are empty. Filter by server (default EUW).
    Returns a flat list of ladder entry dicts.
    """
    out: list[dict] = []
    with httpx.Client(timeout=20.0, headers={"User-Agent": USER_AGENT}) as client:
        for page in range(1, max_pages + 1):
            r = client.get(f"{API_BASE}/es/ladder", params={"page": page})
            if r.status_code == 429:
                wait = float(r.headers.get("Retry-After", "5"))
                logger.warning("lolpros 429, sleeping %.1fs", wait)
                time.sleep(wait)
                continue
            if r.status_code == 404:
                break
            r.raise_for_status()
            entries = r.json() or []
            if not entries:
                break
            for e in entries:
                if (e.get("account") or {}).get("server") == server:
                    out.append(e)
            time.sleep(REQUEST_DELAY_SEC)
            # Heuristic: ladder pages return 20 entries; partial page = end.
            if len(entries) < 20:
                break
    logger.info("Lolpros: fetched %d ladder entries on server=%s", len(out), server)
    return out


def fetch_team_rosters(slugs: Iterable[str]) -> dict[str, list[dict]]:
    """For every team slug, pull current_members. Returns {team_slug: [member, ...]}."""
    out: dict[str, list[dict]] = {}
    with httpx.Client(timeout=20.0, headers={"User-Agent": USER_AGENT}) as client:
        for slug in slugs:
            try:
                r = client.get(f"{API_BASE}/es/teams/{slug}")
                if r.status_code != 200:
                    continue
                data = r.json() or {}
                out[slug] = data.get("current_members") or []
                time.sleep(REQUEST_DELAY_SEC)
            except Exception as exc:
                logger.warning("lolpros team %s: %s", slug, exc)
    return out


def fetch_profile(slug: str) -> dict | None:
    """Pull the full /es/profiles/<slug> document — includes social_media + previous_teams + peak + seasons."""
    if not slug:
        return None
    try:
        with httpx.Client(timeout=20.0, headers={"User-Agent": USER_AGENT}) as client:
            r = client.get(f"{API_BASE}/es/profiles/{slug}")
            if r.status_code == 429:
                time.sleep(5)
                r = client.get(f"{API_BASE}/es/profiles/{slug}")
            if r.status_code != 200:
                return None
            return r.json()
    except Exception as exc:
        logger.warning("lolpros profile %s: %s", slug, exc)
        return None


# Lolpros rank tier strings sort lexicographically WRONG ("00_challenger"
# is the highest, but "90_unranked" comes after it alphabetically). This
# table maps tier prefix → ordinal rank (higher = better).
_RANK_TIER_ORDER = {
    "00_challenger": 13, "01_grandmaster": 12, "10_grandmaster": 12,
    "02_master": 11, "20_master": 11,
    "03_diamond": 10, "30_diamond": 10,
    "04_emerald": 9,  "40_emerald": 9,
    "05_platinum": 8, "50_platinum": 8,
    "06_gold": 7,     "60_gold": 7,
    "07_silver": 6,   "70_silver": 6,
    "08_bronze": 5,   "80_bronze": 5,
    "09_iron": 4,     "90_iron": 4,
    "90_unranked": 0, "unranked": 0,
}

_DIVISION_ORDER = {"i": 4, "ii": 3, "iii": 2, "iv": 1, "1": 4, "2": 3, "3": 2, "4": 1}


def _rank_score(rank: dict | None) -> int:
    """Comparable integer for a Lolpros rank dict. Higher = better.

    Rank dict shape: {"tier": "10_grandmaster", "division": "1", "league_points": 865}
    """
    if not rank:
        return -1
    tier = (rank.get("tier") or "").lower()
    base = _RANK_TIER_ORDER.get(tier, 0) * 10000
    div = _DIVISION_ORDER.get(str(rank.get("division") or "").lower(), 0) * 1000
    lp = int(rank.get("league_points") or 0)
    return base + div + lp


def lolpros_slug_guess(player_name: str) -> str | None:
    """Best-effort slug guess from a Leaguepedia Player display name.

    Lolpros's URL slug convention is lowercase Player name with spaces →
    hyphens and apostrophes / accents stripped. Works for ~90% of pros.
    """
    if not player_name:
        return None
    s = str(player_name).lower().strip()
    # Strip diacritics
    import unicodedata
    s = "".join(
        c for c in unicodedata.normalize("NFKD", s)
        if not unicodedata.combining(c)
    )
    # Replace spaces with hyphens, strip everything else except [a-z0-9-]
    s = re.sub(r"\s+", "-", s)
    s = re.sub(r"[^a-z0-9\-]", "", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s or None


async def fetch_lolpros_profile_async(client: "httpx.AsyncClient", slug: str) -> dict | None:
    """Async profile fetch. Returns None on 404/error. Used by bulk crawl."""
    try:
        r = await client.get(f"{API_BASE}/es/profiles/{slug}")
        if r.status_code == 200:
            return r.json()
        if r.status_code == 429:
            # Lolpros rate-limited — back off and retry once
            import asyncio
            await asyncio.sleep(3)
            r = await client.get(f"{API_BASE}/es/profiles/{slug}")
            if r.status_code == 200:
                return r.json()
        return None
    except Exception:
        return None


async def fetch_lolpros_profiles_bulk(
    slugs: list[str],
    concurrency: int = 8,
    pace_sec: float = 0.05,
    progress_cb=None,
    chunk_size: int = 200,
) -> dict[str, dict]:
    """Fetch many Lolpros profiles concurrently.

    `slugs` should be unique. Returns {slug: profile_dict} for the ones
    that returned 200; missing slugs (404 / network error) are silently
    skipped — callers should treat absence as "no Lolpros profile".

    Processes in chunks of `chunk_size` so progress is visible (and so
    a network hiccup doesn't waste 5 minutes of work). With chunk_size=
    200 and concurrency=8, each chunk takes ~10-25 s and the caller
    gets a progress log per chunk.
    """
    import asyncio
    out: dict[str, dict] = {}
    if not slugs:
        return out

    chunks = [slugs[i:i + chunk_size] for i in range(0, len(slugs), chunk_size)]

    async with httpx.AsyncClient(
        headers={"User-Agent": USER_AGENT},
        timeout=httpx.Timeout(15.0, connect=10.0),
    ) as client:
        for ci, chunk in enumerate(chunks, start=1):
            sem = asyncio.Semaphore(concurrency)

            async def fetch_one(slug: str) -> tuple[str, dict | None]:
                async with sem:
                    try:
                        r = await client.get(f"{API_BASE}/es/profiles/{slug}")
                        if r.status_code == 429:
                            await asyncio.sleep(3)
                            r = await client.get(f"{API_BASE}/es/profiles/{slug}")
                        profile = r.json() if r.status_code == 200 else None
                    except Exception:
                        profile = None
                    await asyncio.sleep(pace_sec)
                    return slug, profile

            results = await asyncio.gather(*[fetch_one(s) for s in chunk])
            n_ok = 0
            for slug, profile in results:
                if profile:
                    out[slug] = profile
                    n_ok += 1
            logger.info(
                "lolpros bulk chunk %d/%d: %d/%d profiles fetched",
                ci, len(chunks), n_ok, len(chunk),
            )
            if progress_cb:
                progress_cb(len(out), len(slugs))

    return out


def extract_puuids_from_profile(profile: dict) -> list[tuple[str, dict]]:
    """Return [(encrypted_puuid, account_dict), ...] for every Lolpros account.

    Each tuple lets the caller match Player.puuid → account directly. The
    account_dict carries the IGN, rank, peak, server.
    """
    out = []
    if not profile:
        return out
    accounts = (profile.get("league_player") or {}).get("accounts", []) or []
    for acc in accounts:
        puuid = acc.get("encrypted_puuid")
        if puuid:
            out.append((puuid, acc))
    return out


def best_account_in_profile(profile: dict) -> dict | None:
    """Return the highest-ranked account from a Lolpros profile.

    'Best' = max of (current rank, peak rank). When all accounts are
    unranked we return the FIRST one (Lolpros sorts them by primary
    on the page itself, so [0] is already the canonical account).
    """
    if not profile:
        return None
    accounts = (profile.get("league_player") or {}).get("accounts", []) or []
    if not accounts:
        return None

    def score(acc: dict) -> int:
        return max(
            _rank_score(acc.get("rank")),
            _rank_score(acc.get("peak")),
        )
    return max(accounts, key=score, default=accounts[0])


def build_lookup(entries: list[dict]) -> dict[str, dict]:
    """
    Map normalized_name -> ladder entry. The summoner_name field is the Riot ID
    used in SoloQ ("G2 SkewMond#3327"), so we generate multiple candidates per
    entry to handle variants (with/without team prefix).
    """
    lookup: dict[str, dict] = {}
    for e in entries:
        sn = (e.get("account") or {}).get("summoner_name") or ""
        # Generate candidates from BOTH the SoloQ summoner_name and the player's
        # canonical "name" field — Lolpros sometimes stores them differently.
        cands = set(_candidates(sn))
        cands.update(_candidates(e.get("name") or ""))
        for k in cands:
            if k and k not in lookup:
                lookup[k] = e
    return lookup


def sync_with_lookup(db: Session, lookup: dict[str, dict], fetch_profiles: bool = True) -> dict:
    """For each player in DB, try to match against Lolpros lookup, upsert PlayerMeta.

    When `fetch_profiles=True`, also pull /es/profiles/<slug> for matched players
    to cache social media, previous teams, peak rank, and seasons history.
    """
    import json as _json
    now = datetime.now(timezone.utc)
    matched, unmatched, profiles_fetched = 0, 0, 0

    for p in db.query(Player).all():
        rec = None
        for cand in _candidates(p.summoner_name or ""):
            if cand in lookup:
                rec = lookup[cand]
                break

        meta = db.get(PlayerMeta, p.puuid)
        if not meta:
            meta = PlayerMeta(puuid=p.puuid)
            db.add(meta)

        if rec:
            matched += 1
            team = rec.get("team") or {}
            current_team_name = (team.get("name") or "").strip()
            tag = (team.get("tag") or "").strip()
            logo_url = ((team.get("logo") or {}).get("url") or "").strip()
            # Some Lolpros logos come back as http://res.cloudinary.com/... which
            # browsers block as mixed-content over https. Cloudinary supports
            # both — force https.
            if logo_url.startswith("http://"):
                logo_url = "https://" + logo_url[len("http://"):]
            position = rec.get("position") or ""
            role = POSITION_TO_ROLE.get(position)
            slug = rec.get("slug")

            meta.country = rec.get("country") or meta.country
            meta.residency = "Europe" if (rec.get("account") or {}).get("server") == "EUW" else meta.residency
            meta.role = role or meta.role
            meta.current_team = current_team_name
            meta.current_team_tag = tag or None
            meta.current_team_logo_url = logo_url or None
            meta.is_pro = True
            meta.is_retired = False
            meta.lolpros_slug = slug
            meta.leaguepedia_id = meta.leaguepedia_id or rec.get("name")
            meta.leaguepedia_url = meta.leaguepedia_url or f"https://lolpros.gg/player/{slug}"

            # Pull full profile (social + prev teams + peak) once per sync
            if fetch_profiles and slug:
                profile = fetch_profile(slug)
                if profile:
                    meta.lolpros_profile_json = _json.dumps(profile)
                    profiles_fetched += 1
                    time.sleep(REQUEST_DELAY_SEC)
        else:
            unmatched += 1
        meta.last_synced = now

    db.commit()
    return {"matched": matched, "unmatched": unmatched, "profiles_fetched": profiles_fetched}


def run_lolpros_sync_sync(db: Session, server: str = "EUW") -> dict:
    """Sync entry point — synchronous, called from a background task."""
    entries = fetch_ladder(server=server)
    lookup = build_lookup(entries)
    stats = sync_with_lookup(db, lookup)
    stats["ladder_entries"] = len(entries)
    stats["unique_normalized_keys"] = len(lookup)
    return stats
