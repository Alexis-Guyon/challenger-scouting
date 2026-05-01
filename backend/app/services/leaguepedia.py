"""
Leaguepedia integration via the MediaWiki Cargo API.

Uses mwclient to handle MediaWiki conventions and (optionally) authenticate
against a Fandom bot account for relaxed rate-limits. Anonymous use is heavily
throttled (~1 req/min after a few calls).

Set LP_USERNAME / LP_PASSWORD in .env to authenticate. Get a bot password at
https://lol.fandom.com/wiki/Special:BotPasswords (recommended, scoped credentials).

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


def _calc_age(birthdate: str | None) -> Optional[int]:
    if not birthdate:
        return None
    try:
        d = datetime.fromisoformat(birthdate.split("T")[0]).date()
        today = date.today()
        return today.year - d.year - ((today.month, today.day) < (d.month, d.day))
    except Exception:
        return None


def _connect() -> mwclient.Site:
    site = mwclient.Site(LP_HOST, path="/", clients_useragent=USER_AGENT)
    user = os.getenv("LP_USERNAME")
    pw = os.getenv("LP_PASSWORD")
    if user and pw:
        try:
            site.login(user, pw)
            logger.info("Leaguepedia: logged in as %s", user)
        except Exception as exc:
            logger.warning("Leaguepedia login failed (%s) — falling back to anonymous", exc)
    else:
        logger.info("Leaguepedia: anonymous (set LP_USERNAME/LP_PASSWORD for higher rate-limits)")
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
    now = datetime.now(timezone.utc)
    matched = unmatched = fa_count = 0
    players = db.query(Player).all()
    for p in players:
        key = _normalize_name(p.summoner_name or "")
        rec = lookup.get(key)

        meta = db.get(PlayerMeta, p.puuid)
        if not meta:
            meta = PlayerMeta(puuid=p.puuid)
            db.add(meta)

        if rec:
            matched += 1
            current_team = (rec.get("Team") or "").strip()
            if current_team == "":
                fa_count += 1

            meta.leaguepedia_id = rec.get("Player")
            overview = rec.get("OverviewPage") or rec.get("Player")
            meta.leaguepedia_url = (
                f"https://lol.fandom.com/wiki/{overview.replace(' ', '_')}"
                if overview else None
            )
            meta.country = rec.get("Country") or None
            meta.nationality_primary = rec.get("NationalityPrimary") or None
            meta.residency = rec.get("Residency") or None
            meta.birthdate = rec.get("Birthdate") or None
            meta.age = _calc_age(meta.birthdate)
            meta.role = rec.get("Role") or None
            meta.current_team = current_team
            raw_retired = str(rec.get("IsRetired", "")).strip()
            meta.is_retired = bool(int(raw_retired)) if raw_retired else False
            meta.contract_end = rec.get("ContractEnd") or None
            meta.is_pro = True
        else:
            unmatched += 1
            meta.is_pro = False
        meta.last_synced = now

    db.commit()
    return {"matched": matched, "unmatched": unmatched, "fa": fa_count}


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
