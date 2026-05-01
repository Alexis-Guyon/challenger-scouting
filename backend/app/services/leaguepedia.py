"""
Leaguepedia integration via the MediaWiki Cargo API.

Uses mwclient to handle MediaWiki conventions and optionally authenticate
against a Fandom bot account for relaxed rate-limits.

Set FANDOM_USERNAME / FANDOM_PASSWORD in .env to authenticate.
Use a Fandom bot password, not the regular account password.
"""
import logging
import os
import re
import time
from datetime import date, datetime, timezone
from typing import Iterable, Optional

import mwclient
from sqlalchemy.orm import Session

from ..config import settings
from ..models import Player, PlayerMeta

logger = logging.getLogger(__name__)

LP_HOST = "lol.fandom.com"
USER_AGENT = "ChallengerScoutingBot/0.1 (internal scouting tool)"
PAGE_SIZE = 500


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

    push(base)

    no_prefix = re.sub(
        r"^(twtv|trainer|coach|sub)\s+",
        "",
        base,
        flags=re.I,
    ).strip()
    if no_prefix != base:
        push(no_prefix)

    m = re.match(r"^([A-Z0-9]{1,5})\s+(.+)$", base)
    if m:
        push(m.group(2))
        push(m.group(2).split(" ")[-1])

    no_suffix = re.sub(
        r"\s+(NEXT|academy|smurf|alt|main|\d+)$",
        "",
        base,
        flags=re.I,
    ).strip()
    if no_suffix != base:
        push(no_suffix)

    if m:
        post_prefix = m.group(2)
        cleaned = re.sub(
            r"\s+(NEXT|academy|smurf|alt|main|\d+)$",
            "",
            post_prefix,
            flags=re.I,
        ).strip()
        push(cleaned)
        push(cleaned.split(" ")[-1])

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
    """
    Build a public URL for a Leaguepedia file.
    lol.fandom.com Special:FilePath redirects to the real CDN-hosted image.
    """
    if not filename:
        return None

    from urllib.parse import quote

    f = filename.replace(" ", "_")
    return f"https://lol.fandom.com/wiki/Special:FilePath/{quote(f)}"


_AUTH_STATE: dict = {
    "authed": False,
    "as": None,
    "error": None,
}


def _connect() -> mwclient.Site:
    """
    Connect to lol.fandom.com.

    Requires a Fandom bot password.
    Username format is usually: MainAccount@bot-label
    """
    site = mwclient.Site(
        LP_HOST,
        path="/",
        clients_useragent=USER_AGENT,
    )

    user = (
        settings.fandom_username
        or settings.lp_username
        or os.getenv("FANDOM_USERNAME")
        or os.getenv("LP_USERNAME")
    )
    pw = (
        settings.fandom_password
        or settings.lp_password
        or os.getenv("FANDOM_PASSWORD")
        or os.getenv("LP_PASSWORD")
    )

    _AUTH_STATE.update(
        authed=False,
        **{"as": None},
        error=None,
    )

    if user and pw:
        try:
            site.login(user, pw)
            _AUTH_STATE.update(
                authed=True,
                **{"as": user},
                error=None,
            )
            logger.info("lol.fandom.com: logged in as %s", user)
        except Exception as exc:
            err = f"{type(exc).__name__}: {exc}"

            if "@" not in user:
                err += (
                    " | Hint: FANDOM_USERNAME must be a bot-password user "
                    "like 'MainAccount@bot-label'. The regular Fandom password "
                    "does not work for the API."
                )

            _AUTH_STATE["error"] = err
            logger.warning(
                "lol.fandom.com login failed (%s) — falling back to anonymous",
                err,
            )
    else:
        _AUTH_STATE["error"] = "no FANDOM_USERNAME/FANDOM_PASSWORD set"
        logger.info(
            "lol.fandom.com: anonymous "
            "(set FANDOM_USERNAME/FANDOM_PASSWORD for higher rate-limits)"
        )

    return site


def _is_transient_lp_error(exc: Exception) -> bool:
    s = str(exc).lower()

    return (
        "ratelimited" in s
        or "internal_api_error" in s
        or "mwexception" in s
        or isinstance(exc, mwclient.errors.InvalidResponse)
    )


def _cargo_query_raw(params: dict) -> list[dict]:
    """
    Anonymous fallback when mwclient's authenticated session triggers
    `internal_api_error_MWException`. We use plain httpx with a clean session
    (no cookies, no continuation tokens). Fandom's anon Cargo accepts the same
    queries but with stricter rate-limits.
    """
    import httpx
    full_params = {**params, "action": "cargoquery", "format": "json"}
    with httpx.Client(timeout=20.0, headers={"User-Agent": USER_AGENT}) as client:
        r = client.get(f"https://{LP_HOST}/api.php", params=full_params)
        r.raise_for_status()
        data = r.json()
        if "error" in data:
            raise LeaguepediaError(f"Cargo (anon): {data['error']}")
        return [row["title"] for row in data.get("cargoquery", [])]


def _cargo_query(site: mwclient.Site, **kwargs) -> list[dict]:
    """
    Wrapper around MediaWiki Cargo API.

    Retries on:
    - rate limits
    - Fandom internal_api_error_MWException (may indicate a bug in the
      authenticated session — we fall back to anonymous httpx if mwclient
      keeps tripping it)
    - invalid / blocked responses
    """
    params = {
        **kwargs,
        "action": "cargoquery",
        "format": "json",
    }

    waits = [10, 30, 60, 120, 240]

    mw_failures_in_a_row = 0
    for attempt, wait in enumerate(waits):
        # Quick fallback to anonymous httpx after 2 mwclient MWException hits
        # in a row — that error pattern means the authenticated session itself
        # is the trigger, not the query. Anon doesn't share the bug.
        if mw_failures_in_a_row >= 2:
            try:
                rows = _cargo_query_raw(params)
                logger.info("Leaguepedia: fell back to anonymous httpx — got %d rows", len(rows))
                return rows
            except LeaguepediaError as exc:
                logger.warning("Leaguepedia anonymous fallback also failed: %s", exc)
                # Reset and continue retrying mwclient
                mw_failures_in_a_row = 0

        try:
            data = site.api(**params)

        except (mwclient.errors.APIError, mwclient.errors.InvalidResponse) as exc:
            if _is_transient_lp_error(exc):
                if "MWException" in str(exc) or "internal_api_error" in str(exc):
                    mw_failures_in_a_row += 1
                logger.warning(
                    "Leaguepedia transient Cargo/API error, sleeping %ds "
                    "(attempt %d/%d): %s",
                    wait,
                    attempt + 1,
                    len(waits),
                    exc,
                )
                time.sleep(wait)
                continue

            raise LeaguepediaError(f"Cargo API failed: {exc}") from exc

        if "error" in data:
            code = data["error"].get("code", "")

            if code == "ratelimited" or code.startswith("internal_api_error"):
                if code.startswith("internal_api_error"):
                    mw_failures_in_a_row += 1
                logger.warning(
                    "Leaguepedia Cargo error %s, sleeping %ds "
                    "(attempt %d/%d)",
                    code,
                    wait,
                    attempt + 1,
                    len(waits),
                )
                time.sleep(wait)
                continue

            raise LeaguepediaError(f"Cargo: {data['error']}")
        # Success — reset counter
        mw_failures_in_a_row = 0

        return [row["title"] for row in data.get("cargoquery", [])]

    raise LeaguepediaError("Cargo retries exhausted")


_FIELDS = ",".join(
    [
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
        "Image",
    ]
)


def _quote_for_cargo(s: str) -> str:
    """
    Cargo where IN (...) uses double-quoted strings.
    Escape internal quotes.
    """
    return '"' + s.replace('"', '\\"') + '"'


def fetch_active_pros(residencies: Iterable[str] = ("Europe",)) -> list[dict]:
    """
    Bulk fetch active pros for a residency.
    Prefer fetch_pros_by_name() when possible.
    """
    site = _connect()

    res_clause = " OR ".join(f'Residency="{r}"' for r in residencies)
    where = f"({res_clause}) AND IsRetired=0"

    out: list[dict] = []
    offset = 0

    while True:
        rows = _cargo_query(
            site,
            tables="Players",
            fields=_FIELDS,
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
        time.sleep(2.0)

    logger.info(
        "Leaguepedia: fetched %d active pros for residencies=%s",
        len(out),
        list(residencies),
    )

    return out


def fetch_pros_by_name(names: Iterable[str], chunk_size: int = 1,
                        per_query_pace_sec: float = 3.0) -> list[dict]:
    """
    Targeted fetch from Cargo Players table.

    Uses smaller chunks and recursively splits failing chunks because Fandom
    sometimes throws internal_api_error_MWException on large IN clauses or
    problematic player names.
    """
    site = _connect()

    names = [n.strip() for n in (names or []) if n and n.strip()]

    out: list[dict] = []
    seen_players: set[str] = set()

    def fetch_chunk(chunk: list[str]) -> list[dict]:
        # Single-name fast path: `Player="X"` is way more stable than IN clauses
        # (Fandom's IN-clause SQL handler throws MWException on edge cases —
        # apostrophes, accented characters, very long names, etc.).
        if len(chunk) == 1:
            where = f"Player={_quote_for_cargo(chunk[0])}"
        else:
            in_clause = ",".join(_quote_for_cargo(n) for n in chunk)
            where = f"Player IN ({in_clause})"

        try:
            return _cargo_query(
                site,
                tables="Players",
                fields=_FIELDS,
                where=where,
                limit=PAGE_SIZE,
            )

        except LeaguepediaError as exc:
            if len(chunk) == 1:
                logger.warning(
                    "Leaguepedia: skipping failing player %r: %s",
                    chunk[0],
                    exc,
                )
                return []

            mid = len(chunk) // 2

            logger.warning(
                "Leaguepedia: splitting failing chunk of %d names: %s",
                len(chunk),
                exc,
            )

            time.sleep(2.0)

            return fetch_chunk(chunk[:mid]) + fetch_chunk(chunk[mid:])

    n_processed = 0
    for i in range(0, len(names), chunk_size):
        chunk = names[i : i + chunk_size]
        rows = fetch_chunk(chunk)
        for r in rows:
            key = r.get("Player")
            if key and key not in seen_players:
                seen_players.add(key)
                out.append(r)
        n_processed += len(chunk)
        if n_processed % 20 == 0:
            logger.info("Leaguepedia: %d/%d processed (%d matched so far)",
                        n_processed, len(names), len(out))
        time.sleep(per_query_pace_sec)

    logger.info(
        "Leaguepedia: fetched %d profiles targeted from %d names "
        "(chunk_size=%d, pace=%.1fs)",
        len(out), len(names), chunk_size, per_query_pace_sec,
    )

    return out


def build_lookup(pros: list[dict]) -> dict[str, dict]:
    """
    Map normalized_name -> pro_record.
    Indexes Player + every SoloqueueIds entry.
    """
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
    Additive sync.

    Fills Leaguepedia fields without clobbering Lolpros data.
    """
    now = datetime.now(timezone.utc)

    matched = 0
    unmatched = 0
    images_found = 0

    players = db.query(Player).all()

    for processed, p in enumerate(players, start=1):
        if processed % 200 == 0:
            db.commit()

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

            meta.leaguepedia_id = rec.get("Player") or meta.leaguepedia_id

            overview = rec.get("OverviewPage") or rec.get("Player")
            if overview and not meta.leaguepedia_url:
                meta.leaguepedia_url = (
                    f"https://lol.fandom.com/wiki/{overview.replace(' ', '_')}"
                )

            meta.birthdate = rec.get("Birthdate") or meta.birthdate
            meta.age = _calc_age(meta.birthdate)
            meta.contract_end = rec.get("ContractEnd") or meta.contract_end
            meta.nationality_primary = (
                rec.get("NationalityPrimary") or meta.nationality_primary
            )

            image_filename = (rec.get("Image") or "").strip()
            if image_filename:
                meta.player_image_url = _file_path_url(image_filename)
                images_found += 1

            if not meta.country:
                meta.country = rec.get("Country") or None

            if not meta.residency:
                meta.residency = rec.get("Residency") or None

            if not meta.current_team:
                meta.current_team = (rec.get("Team") or "").strip() or None

            raw_retired = str(rec.get("IsRetired", "")).strip()
            if raw_retired:
                try:
                    meta.is_retired = bool(int(raw_retired))
                except ValueError:
                    pass

            meta.is_pro = True

        else:
            unmatched += 1

        meta.last_synced = now

    db.commit()

    return {
        "matched": matched,
        "unmatched": unmatched,
        "images_found": images_found,
    }


def run_leaguepedia_sync_sync(db: Session) -> dict:
    """
    Synchronous Leaguepedia sync.

    Strategy:
    1. Prefer targeted names from PlayerMeta.leaguepedia_id.
    2. Fallback to active European pros if no targeted names exist.
    """
    targets = (
        db.query(PlayerMeta.leaguepedia_id)
        .filter(
            PlayerMeta.is_pro == True,  # noqa: E712
            PlayerMeta.leaguepedia_id.isnot(None),
        )
        .distinct()
        .all()
    )

    target_names = sorted({r[0] for r in targets if r[0]})

    if target_names:
        logger.info(
            "Leaguepedia: targeted sync for %d names from Lolpros matches",
            len(target_names),
        )
        pros = fetch_pros_by_name(target_names)
    else:
        logger.info(
            "Leaguepedia: no Lolpros-matched names yet, "
            "falling back to bulk EU residency fetch"
        )
        pros = fetch_active_pros(residencies=("Europe",))

    lookup = build_lookup(pros)

    stats = sync_players_with_lookup(db, lookup)

    stats["pros_in_lookup"] = len(lookup)
    stats["raw_records_fetched"] = len(pros)
    stats["targeted_names"] = len(target_names)
    stats["authenticated"] = _AUTH_STATE.get("authed", False)

    if not _AUTH_STATE.get("authed"):
        stats["auth_error"] = _AUTH_STATE.get("error") or "anonymous"

    return stats


async def run_leaguepedia_sync(db: Session) -> dict:
    """
    Async wrapper for FastAPI admin endpoint.
    """
    import asyncio as _asyncio

    return await _asyncio.to_thread(run_leaguepedia_sync_sync, db)