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
