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

from ..config import settings
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


_AUTH_STATE: dict = {"authed": False, "as": None, "error": None}


def _connect() -> mwclient.Site:
    """
    Connect to lol.fandom.com.

    REQUIRES a *bot password* (not the regular Fandom account password).
    Get one at https://lol.fandom.com/wiki/Special:BotPasswords. The username
    you receive is `<MainAccount>@<label>` and the password is a long hash.

    Anonymous use is rate-limited to ~1 req/min and basically unusable for
    syncing 2000+ pros.
    """
    site = mwclient.Site(LP_HOST, path="/", clients_useragent=USER_AGENT)
    user = settings.fandom_username or settings.lp_username \
        or os.getenv("FANDOM_USERNAME") or os.getenv("LP_USERNAME")
    pw = settings.fandom_password or settings.lp_password \
        or os.getenv("FANDOM_PASSWORD") or os.getenv("LP_PASSWORD")

    _AUTH_STATE.update(authed=False, as_=None, error=None)
    if user and pw:
        try:
            site.login(user, pw)
            _AUTH_STATE.update(authed=True, **{"as": user})
            logger.info("lol.fandom.com: logged in as %s", user)
        except Exception as exc:
            err = f"{type(exc).__name__}: {exc}"
            _AUTH_STATE["error"] = err
            if "@" not in (user or ""):
                _AUTH_STATE["error"] += (
                    " | Hint: FANDOM_USERNAME must be a *bot password* "
                    "user of the form 'MainAccount@bot-label' (created at "
                    "https://lol.fandom.com/wiki/Special:BotPasswords). The "
                    "regular Fandom account password does NOT work for the API."
                )
            logger.warning("lol.fandom.com login failed (%s) — falling back to anonymous", _AUTH_STATE["error"])
    else:
        _AUTH_STATE["error"] = "no FANDOM_USERNAME/FANDOM_PASSWORD set"
        logger.info("lol.fandom.com: anonymous (set FANDOM_USERNAME/FANDOM_PASSWORD for higher rate-limits)")
    return site


def _cargo_query(site: mwclient.Site, **kwargs) -> list[dict]:
    """Wrap site.api with Cargo + retry on rate-limit / transient errors.

    Uses an exponential backoff: 60s, 120s, 240s, 480s, 600s. Total worst-case
    wait is ~25 min per call — long, but Fandom's rate-limit window is
    similarly long after a heavy session. Better to wait than to bail out.
    """
    params = {**kwargs, "action": "cargoquery", "format": "json"}
    waits = [60, 120, 240, 480, 600]
    for attempt, wait in enumerate(waits):
        try:
            data = site.api(**params)
        except (mwclient.errors.APIError, mwclient.errors.InvalidResponse) as exc:
            if "ratelimited" in str(exc) or isinstance(exc, mwclient.errors.InvalidResponse):
                logger.warning("Leaguepedia rate-limited / blocked, sleeping %ds (attempt %d/%d)",
                               wait, attempt + 1, len(waits))
                time.sleep(wait)
                continue
            raise
        if "error" in data:
            code = data["error"].get("code")
            if code == "ratelimited":
                logger.warning("Leaguepedia rate-limited, sleeping %ds (attempt %d/%d)",
                               wait, attempt + 1, len(waits))
                time.sleep(wait)
                continue
            raise LeaguepediaError(f"Cargo: {data['error']}")
        return [row["title"] for row in data.get("cargoquery", [])]
    raise LeaguepediaError("Cargo: rate-limit retries exhausted (>25 min). "
                           "Wait 30 min before retrying — your IP is in cooldown.")


_FIELDS = ",".join([
    "Player", "OverviewPage", "Country", "NationalityPrimary", "Residency",
    "Birthdate", "Role", "Team", "IsRetired", "SoloqueueIds", "ContractEnd",
    "Image",
])


def _quote_for_cargo(s: str) -> str:
    """Cargo's `where IN (...)` uses double-quoted strings. Escape internal quotes."""
    return '"' + s.replace('"', '\\"') + '"'


def fetch_active_pros(residencies: Iterable[str] = ("Europe",)) -> list[dict]:
    """
    Bulk fetch all active pros for a residency. Slow + rate-limit prone — use
    fetch_pros_by_name() instead when possible.
    """
    site = _connect()
    res_clause = " OR ".join(f'Residency="{r}"' for r in residencies)
    where = f"({res_clause}) AND IsRetired=0"

    out: list[dict] = []
    offset = 0
    while True:
        rows = _cargo_query(
            site, tables="Players", fields=_FIELDS, where=where,
            limit=PAGE_SIZE, offset=offset,
        )
        if not rows:
            break
        out.extend(rows)
        if len(rows) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
        time.sleep(2.0)
    logger.info("Leaguepedia: fetched %d active pros for residencies=%s", len(out), list(residencies))
    return out


def fetch_pros_by_name(names: Iterable[str], chunk_size: int = 30) -> list[dict]:
    """
    Targeted fetch: pulls Cargo Players rows for an explicit list of canonical
    names (e.g. ["Hans sama", "Caps", "Faker"]). Way faster + lighter than a
    paginated full residency dump — typically 2-3 queries vs 8+.
    """
    site = _connect()
    names = [n for n in (names or []) if n and n.strip()]
    out: list[dict] = []
    seen_players: set[str] = set()
    n_chunks = (len(names) + chunk_size - 1) // chunk_size
    for i in range(0, len(names), chunk_size):
        chunk = names[i:i + chunk_size]
        in_clause = ",".join(_quote_for_cargo(n) for n in chunk)
        where = f"Player IN ({in_clause})"
        try:
            rows = _cargo_query(site, tables="Players", fields=_FIELDS, where=where, limit=PAGE_SIZE)
        except LeaguepediaError as exc:
            logger.error("Leaguepedia: stopping after partial fetch (%d/%d chunks): %s",
                         i // chunk_size, n_chunks, exc)
            break
        for r in rows:
            key = r.get("Player")
            if key and key not in seen_players:
                seen_players.add(key)
                out.append(r)
        # Pacing between chunks — important to avoid tripping the limiter
        time.sleep(3.0)
    logger.info("Leaguepedia: fetched %d profiles (targeted %d names, %d chunks)",
                len(out), len(names), n_chunks)
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
    processed = 0
    for p in players:
        processed += 1
        if processed % 200 == 0:
            db.commit()  # incremental commit so we don't lose work on a crash
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
    """
    Synchronous full sync.

    Strategy: targeted-first. We try to query Leaguepedia ONLY for pros we
    already matched via Lolpros (we have their canonical name in
    PlayerMeta.leaguepedia_id). That's a single Cargo query instead of 8+.
    Falls back to the bulk EU residency dump if no Lolpros matches yet.
    """
    # Targeted: pull canonical names from Lolpros-matched pros
    targets = (
        db.query(PlayerMeta.leaguepedia_id)
        .filter(PlayerMeta.is_pro == True, PlayerMeta.leaguepedia_id.isnot(None))  # noqa: E712
        .distinct()
        .all()
    )
    target_names = sorted({r[0] for r in targets if r[0]})

    if target_names:
        logger.info("Leaguepedia: targeted sync for %d names from Lolpros matches", len(target_names))
        pros = fetch_pros_by_name(target_names)
    else:
        logger.info("Leaguepedia: no Lolpros-matched names yet, falling back to bulk EU residency fetch")
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


# Backwards-compat async wrapper for the existing admin endpoint.
async def run_leaguepedia_sync(db: Session) -> dict:
    import asyncio as _asyncio
    return await _asyncio.to_thread(run_leaguepedia_sync_sync, db)
