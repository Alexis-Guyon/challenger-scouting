"""
Leaguepedia integration via the MediaWiki Cargo API.

Uses mwclient to handle MediaWiki conventions and (optionally) authenticate
against a Fandom bot account for relaxed rate-limits. Anonymous use is heavily
throttled (~1 req/min after a few calls).

Set FANDOM_USERNAME / FANDOM_PASSWORD in .env to authenticate (legacy
LP_USERNAME / LP_PASSWORD names still work). Get a bot password at
https://lol.fandom.com/wiki/Special:BotPasswords (scoped credentials).

Cargo tables: https://lol.fandom.com/wiki/Special:CargoTables
"""
import logging
import os
import re
import time
from datetime import date, datetime, timezone
from typing import Iterable, Optional

import mwclient
from sqlalchemy.orm import Session

from ..models import Player, PlayerMeta

logger = logging.getLogger(__name__)

LP_HOST = "lol.fandom.com"
USER_AGENT = "ChallengerScoutingBot/0.1 (internal scouting tool)"
PAGE_SIZE = 500  # Cargo hard cap


class LeaguepediaError(Exception):
    pass


def _normalize_name(s: str) -> str:
    """Strip riot tag, lowercase, drop non-alphanum for matching."""
    if not s:
        return ""
    s = s.split("#")[0]
    s = re.sub(r"^(twtv|trainer|coach|sub)\s+", "", s, flags=re.I)
    s = re.sub(r"[^a-z0-9]", "", s.lower())
    return s


def _candidate_normalizations(s: str) -> list[str]:
    """
    Return every plausible normalized form of a Riot in-game name.
    Pro players often prefix their SoloQ name with a team tag ("G2 Hans Sama")
    or a smurf marker ("KC NEXT ADKING"), and Leaguepedia stores only the
    canonical IGN ("hanssama", "adking"). We try multiple strip strategies
    so the team-prefixed name still matches.
    """
    if not s:
        return []
    base = s.split("#")[0].strip()
    out: list[str] = []
    seen: set[str] = set()

    def push(x: str):
        n = re.sub(r"[^a-z0-9]", "", x.lower())
        if n and n not in seen:
            seen.add(n)
            out.append(n)

    # 1. Full name as-is
    push(base)

    # 2. Strip role/community prefixes
    no_prefix = re.sub(r"^(twtv|trainer|coach|sub)\s+", "", base, flags=re.I).strip()
    if no_prefix != base:
        push(no_prefix)

    # 3. Strip leading TEAM TAG (1-5 alnum chars followed by space).
    #    Examples: "G2 Hans Sama" -> "Hans Sama", "MKOI Skewmond" -> "Skewmond"
    m = re.match(r"^([A-Z0-9]{1,5})\s+(.+)$", base)
    if m:
        push(m.group(2))
        # Also try team tag + everything after first word (some pros use 2-word IGNs)
        push(m.group(2).split(" ")[-1])

    # 4. Strip trailing markers: "NEXT", "academy", numeric suffixes, "smurf"
    no_suffix = re.sub(r"\s+(NEXT|academy|smurf|alt|main|\d+)$", "", base, flags=re.I).strip()
    if no_suffix != base:
        push(no_suffix)

    # 5. Combination: strip prefix AND suffix
    if m:
        post_prefix = m.group(2)
        cleaned = re.sub(r"\s+(NEXT|academy|smurf|alt|main|\d+)$", "", post_prefix, flags=re.I).strip()
        push(cleaned)
        # Also the last word of the cleaned remainder
        push(cleaned.split(" ")[-1])

    # 6. Just the last word (handles "Some Long Name SomePro" -> "SomePro")
    parts = base.split(" ")
    if len(parts) > 1:
        push(parts[-1])

    return out


def _calc_age(birthdate: str | None) -> Optional[int]:
    if not birthdate:
        return None
    try:
        d = datetime.fromisoformat(birthdate.split("T")[0]).date()
        today = date.today()
        return today.year - d.year - ((today.month, today.day) < (d.month, d.day))
    except Exception:
        return None


def _file_path_url(filename: str | None) -> str | None:
    """Build a public URL for a Leaguepedia file. lol.fandom.com Special:FilePath
    redirects to the actual CDN-hosted image — works for <img src=...>."""
    if not filename:
        return None
    from urllib.parse import quote
    f = filename.replace(" ", "_")
    return f"https://lol.fandom.com/wiki/Special:FilePath/{quote(f)}"


def _connect() -> mwclient.Site:
    """
    Connect to lol.fandom.com (Leaguepedia is just the wiki's display name).
    Credentials: a Fandom account's bot password — create one at
    https://lol.fandom.com/wiki/Special:BotPasswords. Anonymous use is
    aggressively rate-limited (~1 req / 60s after a handful of calls), so
    setting credentials is strongly recommended for any real usage.

    We accept FANDOM_USERNAME/FANDOM_PASSWORD (preferred) and fall back to
    legacy LP_USERNAME/LP_PASSWORD names so existing .env files keep working.
    """
    site = mwclient.Site(LP_HOST, path="/", clients_useragent=USER_AGENT)
    user = os.getenv("FANDOM_USERNAME") or os.getenv("LP_USERNAME")
    pw = os.getenv("FANDOM_PASSWORD") or os.getenv("LP_PASSWORD")
    if user and pw:
        try:
            site.login(user, pw)
            logger.info("lol.fandom.com: logged in as %s", user)
        except Exception as exc:
            logger.warning("lol.fandom.com login failed (%s) — falling back to anonymous", exc)
    else:
        logger.info("lol.fandom.com: anonymous (set FANDOM_USERNAME/FANDOM_PASSWORD for higher rate-limits)")
    return site


def _cargo_query(site: mwclient.Site, **kwargs) -> list[dict]:
    """Wrap site.api with Cargo + retry on rate-limit."""
    params = {**kwargs, "action": "cargoquery", "format": "json"}
    for attempt in range(5):
        try:
            data = site.api(**params)
        except mwclient.errors.APIError as exc:
            if "ratelimited" in str(exc):
                wait = 30 * (attempt + 1)
                logger.warning("Leaguepedia rate-limited, sleeping %ds (attempt %d/5)", wait, attempt + 1)
                time.sleep(wait)
                continue
            raise
        if "error" in data:
            code = data["error"].get("code")
            if code == "ratelimited":
                wait = 30 * (attempt + 1)
                logger.warning("Leaguepedia rate-limited, sleeping %ds (attempt %d/5)", wait, attempt + 1)
                time.sleep(wait)
                continue
            raise LeaguepediaError(f"Cargo: {data['error']}")
        return [row["title"] for row in data.get("cargoquery", [])]
    raise LeaguepediaError("Cargo: rate-limit retries exhausted")


def fetch_active_pros(residencies: Iterable[str] = ("Europe",)) -> list[dict]:
    site = _connect()
    res_clause = " OR ".join(f'Residency="{r}"' for r in residencies)
    where = f"({res_clause}) AND IsRetired=0"

    fields = ",".join([
        "Player",
        "OverviewPage",
        "Country",
        "NationalityPrimary",
        "Residency",
        "Birthdate",
        "Role",
        "Team",
        "IsRetired",
        "SoloqueueIds",
        "ContractEnd",
        "Image",  # filename of the player's headshot, served via Special:FilePath
    ])

    out: list[dict] = []
    offset = 0
    while True:
        rows = _cargo_query(
            site,
            tables="Players",
            fields=fields,
            where=where,
            limit=PAGE_SIZE,
            offset=offset,
        )
        if not rows:
            break
        out.extend(rows)
        if len(rows) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
        time.sleep(1.0)  # gentle pacing between pages
    logger.info("Leaguepedia: fetched %d active pros for residencies=%s", len(out), list(residencies))
    return out


def build_lookup(pros: list[dict]) -> dict[str, dict]:
    """Map normalized_name -> pro_record. Indexes Player + every SoloqueueIds entry."""
    lookup: dict[str, dict] = {}
    for r in pros:
        candidates = set()
        if r.get("Player"):
            candidates.add(r["Player"])
        sq = r.get("SoloqueueIds") or ""
        if sq:
            for tok in re.split(r"[;,]", sq):
                tok = tok.strip()
                if tok:
                    candidates.add(tok)
        for c in candidates:
            key = _normalize_name(c)
            if key and key not in lookup:
                lookup[key] = r
    return lookup


def sync_players_with_lookup(db: Session, lookup: dict[str, dict]) -> dict:
    """
    ADDITIVE sync — fills in fields Leaguepedia is best at (birthdate→age,
    contract_end, player photo, leaguepedia_id) without clobbering data
    Lolpros may already have populated (current_team, country, role, residency).

    The rule: Lolpros is more current for roster/team data; Leaguepedia is
    richer for biographical/contract data. Run Lolpros sync first, then this.
    """
    now = datetime.now(timezone.utc)
    matched = unmatched = images_found = 0
    players = db.query(Player).all()
    for p in players:
        rec = None
        for candidate in _candidate_normalizations(p.summoner_name or ""):
            if candidate in lookup:
                rec = lookup[candidate]
                break

        meta = db.get(PlayerMeta, p.puuid)
        if not meta:
            meta = PlayerMeta(puuid=p.puuid)
            db.add(meta)

        if rec:
            matched += 1
            # Always set: leaguepedia identity
            meta.leaguepedia_id = rec.get("Player") or meta.leaguepedia_id
            overview = rec.get("OverviewPage") or rec.get("Player")
            if overview and not meta.leaguepedia_url:
                meta.leaguepedia_url = f"https://lol.fandom.com/wiki/{overview.replace(' ', '_')}"

            # Biographical data — Leaguepedia is the canonical source
            meta.birthdate = rec.get("Birthdate") or meta.birthdate
            meta.age = _calc_age(meta.birthdate)
            meta.contract_end = rec.get("ContractEnd") or meta.contract_end
            meta.nationality_primary = rec.get("NationalityPrimary") or meta.nationality_primary

            # Image — Leaguepedia stores filenames; we serve them via Special:FilePath
            image_filename = (rec.get("Image") or "").strip()
            if image_filename:
                meta.player_image_url = _file_path_url(image_filename)
                images_found += 1

            # Fallback fields — only fill if Lolpros didn't already
            if not meta.country:
                meta.country = rec.get("Country") or None
            if not meta.residency:
                meta.residency = rec.get("Residency") or None
            if not meta.current_team:
                meta.current_team = (rec.get("Team") or "").strip() or None

            # Retired flag is reliable on Leaguepedia
            raw_retired = str(rec.get("IsRetired", "")).strip()
            if raw_retired:
                meta.is_retired = bool(int(raw_retired))

            meta.is_pro = True
        else:
            unmatched += 1
            # Don't downgrade is_pro = False — Lolpros may have caught them.
        meta.last_synced = now

    db.commit()
    return {"matched": matched, "unmatched": unmatched, "images_found": images_found}


def run_leaguepedia_sync_sync(db: Session) -> dict:
    """Synchronous full sync — meant to be called from a background task."""
    pros = fetch_active_pros(residencies=("Europe",))
    lookup = build_lookup(pros)
    stats = sync_players_with_lookup(db, lookup)
    stats["pros_in_lookup"] = len(lookup)
    stats["raw_records_fetched"] = len(pros)
    return stats


# Backwards-compat async wrapper for the existing admin endpoint.
async def run_leaguepedia_sync(db: Session) -> dict:
    import asyncio as _asyncio
    return await _asyncio.to_thread(run_leaguepedia_sync_sync, db)
