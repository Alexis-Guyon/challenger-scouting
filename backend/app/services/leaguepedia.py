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


# ----------------- Wikitext fallback (when Cargo is dead) -----------------
#
# Fandom's Cargo extension is intermittently broken — it returns
# `internal_api_error_MWException` for ALL queries on ALL tables. The standard
# MediaWiki action=query / action=parse endpoints still work though, so we
# parse player pages directly from their wikitext infobox.
#
# Infobox shape on lol.fandom.com (Player template):
#   {{Infobox Player
#    |id=Caps
#    |name=Rasmus Borregaard Winther
#    |country=Denmark
#    |residency=EMEA
#    |birth_date_year=1999
#    |birth_date_month=November
#    |birth_date_day=17
#    |role=Mid
#    |checkboxAutoImage=Yes
#    |contract=2025-11-15
#    ...
#   }}
#
# When checkboxAutoImage=Yes, the photo is at <id>.png (e.g. Caps.png).
# When |image= is explicit, we use that instead.

_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}


def _parse_infobox(wikitext: str) -> dict[str, str]:
    """Extract key=value pairs from the first {{Infobox ...}} template in wikitext."""
    if not wikitext:
        return {}
    # Find the infobox start
    start = wikitext.find("{{Infobox ")
    if start < 0:
        return {}
    # Find matching closing braces (depth-aware to skip nested {{...}})
    depth = 0
    i = start
    while i < len(wikitext):
        if wikitext[i:i+2] == "{{":
            depth += 1
            i += 2
        elif wikitext[i:i+2] == "}}":
            depth -= 1
            i += 2
            if depth == 0:
                break
        else:
            i += 1
    body = wikitext[start:i]
    # Parse |key=value lines, ignoring nested templates
    out: dict[str, str] = {}
    for line in body.split("\n"):
        line = line.strip()
        if not line.startswith("|"):
            continue
        if "=" not in line:
            continue
        key, _, value = line[1:].partition("=")
        out[key.strip().lower()] = value.strip()
    return out


def _build_birthdate(infobox: dict) -> str | None:
    """Combine birth_date_year/month/day fields into ISO YYYY-MM-DD."""
    y = infobox.get("birth_date_year") or ""
    m = infobox.get("birth_date_month") or ""
    d = infobox.get("birth_date_day") or ""
    if not (y and m and d):
        return None
    try:
        year = int(y)
        if m.isdigit():
            month = int(m)
        else:
            month = _MONTHS.get(m.lower())
        day = int(d)
        if not (year and month and day):
            return None
        return f"{year:04d}-{month:02d}-{day:02d}"
    except Exception:
        return None


def _resolve_image_filename(infobox: dict, page_title: str) -> str | None:
    """
    Return the image filename hint (without prefix). Just a guess at this stage —
    the wiki may store the headshot as .jpg, .png, .webp, or under a slightly
    different name. _resolve_image_url_for() does the actual lookup.
    """
    explicit = infobox.get("image")
    if explicit:
        explicit = explicit.split("|")[0].strip()
        explicit = explicit.removeprefix("File:").removeprefix("Image:")
        return explicit
    if infobox.get("checkboxautoimage", "").lower() in ("yes", "true", "1"):
        return page_title.replace("_", " ")  # caller appends extensions
    return None


def _resolve_image_url_for(name_or_filename: str) -> str | None:
    """
    Given a player canonical name OR a filename hint, try a list of common
    extensions and the auto-image patterns and return the first CDN URL that
    actually exists on the wiki. Calls action=query&prop=imageinfo to verify.

    Returns the direct static.wikia.nocookie.net URL (no Special:FilePath
    redirect, no 403 from anti-bot heuristics).
    """
    if not name_or_filename:
        return None
    import httpx

    # Strip any extension to get the base name, then try multiple variants.
    base = name_or_filename
    for ext in (".jpg", ".png", ".webp", ".jpeg", ".gif"):
        if base.lower().endswith(ext):
            base = base[: -len(ext)]
            break

    # Build candidate filenames
    candidates: list[str] = []
    for ext in ("jpg", "png", "webp"):
        candidates.append(f"{base}.{ext}")
        if " " in base:
            candidates.append(f"{base.replace(' ', '_')}.{ext}")
        # Capitalize each word ("Hans Sama" / "Hans sama")
        cap = " ".join(w.capitalize() for w in base.split(" "))
        if cap != base:
            candidates.append(f"{cap}.{ext}")

    titles = "|".join(f"File:{c.replace(' ', '_')}" for c in candidates[:9])  # API caps at ~50

    try:
        with httpx.Client(timeout=10.0, headers={"User-Agent": USER_AGENT}) as client:
            r = client.get(
                f"https://{LP_HOST}/api.php",
                params={
                    "action": "query",
                    "titles": titles,
                    "prop": "imageinfo",
                    "iiprop": "url",
                    "format": "json",
                    "formatversion": "2",
                },
            )
            r.raise_for_status()
            data = r.json()
    except Exception as exc:
        logger.warning("image lookup failed for %r: %s", base, exc)
        return None

    for p in data.get("query", {}).get("pages", []) or []:
        if p.get("missing"):
            continue
        info = p.get("imageinfo") or []
        if info and info[0].get("url"):
            return info[0]["url"]
    return None


def _find_image_via_page_search(canonical_name: str) -> str | None:
    """
    Last-resort fallback when no direct <id>.{jpg,png,webp} file exists:
    list all images embedded on the player's wiki page and return the first
    one whose filename contains the player name (so we skip team logos,
    splash arts, audio files, etc.).
    """
    if not canonical_name:
        return None
    import httpx

    title = canonical_name.replace(" ", "_")
    name_token = canonical_name.split()[0].lower()  # primary name token
    try:
        with httpx.Client(timeout=12.0, headers={"User-Agent": USER_AGENT}) as client:
            r = client.get(
                f"https://{LP_HOST}/api.php",
                params={
                    "action": "parse",
                    "page": title,
                    "prop": "images",
                    "format": "json",
                    "redirects": "1",
                },
            )
            r.raise_for_status()
            data = r.json()
    except Exception as exc:
        logger.warning("page-image search failed for %r: %s", canonical_name, exc)
        return None

    images = (data.get("parse") or {}).get("images") or []
    candidates = [
        img for img in images
        if name_token in img.lower()
        and not any(skip in img.lower() for skip in ("logo", ".mp3", ".ogg", ".svg", "square", "icon"))
        and img.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))
    ]
    if not candidates:
        return None

    # Prefer the most recent / portrait-style image (heuristic: shorter names
    # like "Hans_sama.jpg" beat verbose "Hans_sama_2024_Split_2_Valentine.jpg")
    candidates.sort(key=lambda x: (len(x), x))

    # Now resolve the first candidate to its CDN URL
    pick = candidates[0]
    try:
        with httpx.Client(timeout=10.0, headers={"User-Agent": USER_AGENT}) as client:
            r = client.get(
                f"https://{LP_HOST}/api.php",
                params={
                    "action": "query",
                    "titles": f"File:{pick}",
                    "prop": "imageinfo",
                    "iiprop": "url",
                    "format": "json",
                    "formatversion": "2",
                },
            )
            r.raise_for_status()
            data = r.json()
    except Exception:
        return None
    pages = data.get("query", {}).get("pages", [])
    if pages and not pages[0].get("missing"):
        info = pages[0].get("imageinfo") or []
        if info:
            return info[0].get("url")
    return None


def fetch_player_via_parse(canonical_name: str) -> dict | None:
    """
    Bypass Cargo. Pull the player's wiki page wikitext via action=query and
    parse the infobox locally. Returns a dict with the same keys our Cargo
    code expects (Player, Country, Birthdate, Role, IsRetired, ContractEnd,
    Image), or None if the page doesn't exist.
    """
    if not canonical_name:
        return None
    import httpx

    title = canonical_name.replace(" ", "_")
    try:
        with httpx.Client(timeout=20.0, headers={"User-Agent": USER_AGENT}) as client:
            r = client.get(
                f"https://{LP_HOST}/api.php",
                params={
                    "action": "query",
                    "prop": "revisions",
                    "rvprop": "content",
                    "rvslots": "main",
                    "titles": title,
                    "format": "json",
                    "formatversion": "2",
                    "redirects": "1",
                },
            )
            r.raise_for_status()
            data = r.json()
    except Exception as exc:
        logger.warning("Wikitext fetch failed for %s: %s", canonical_name, exc)
        return None

    pages = data.get("query", {}).get("pages", []) or []
    if not pages or pages[0].get("missing"):
        return None
    page = pages[0]
    revs = page.get("revisions") or []
    if not revs:
        return None
    content = (revs[0].get("slots") or {}).get("main", {}).get("content", "")
    info = _parse_infobox(content)
    if not info:
        return None

    # Try to detect retired status from common fields
    is_retired = info.get("isretired", "no").lower() in ("yes", "true", "1")

    # We deliberately don't fetch the player's headshot here.
    # The UI uses the Riot in-game profile icon instead — it's more reliable
    # (always present), more current, and saves 1-2 API calls per pro.
    page_title = page.get("title") or canonical_name
    image_hint = None
    image_url = None

    return {
        "Player": info.get("id") or page_title.replace("_", " "),
        "OverviewPage": page_title.replace(" ", "_"),
        "Country": info.get("country") or None,
        "NationalityPrimary": info.get("nationalityprimary") or info.get("country") or None,
        "Residency": info.get("residency") or None,
        "Birthdate": _build_birthdate(info),
        "Role": info.get("role") or None,
        "Team": info.get("team") or "",
        "IsRetired": "1" if is_retired else "0",
        "SoloqueueIds": info.get("ids", "").replace("\n", ";"),
        "ContractEnd": info.get("contract") or info.get("contractend") or None,
        "Image": image_hint,         # raw filename hint (back-compat)
        "ImageUrl": image_url,       # verified direct CDN URL (preferred)
    }


def fetch_pros_via_parse(names: list[str], pace_sec: float = 1.0) -> list[dict]:
    """Bulk wikitext fetch — much more reliable than Cargo when Cargo is broken."""
    out: list[dict] = []
    for i, name in enumerate(names, start=1):
        rec = fetch_player_via_parse(name)
        if rec:
            out.append(rec)
        if i % 20 == 0:
            logger.info("Wikitext: %d/%d processed (%d matched)", i, len(names), len(out))
        time.sleep(pace_sec)
    logger.info("Wikitext: fetched %d profiles from %d names", len(out), len(names))
    return out


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
        _AUTH_STATE["error"] = (
            "no FANDOM_USERNAME/FANDOM_PASSWORD set in .env — running anonymous "
            "(50 titles/req, ~1 req/min, image fetch heavily rate-limited). "
            "Set both env vars and restart uvicorn for full pro coverage."
        )
        logger.warning(
            "lol.fandom.com: ANONYMOUS — set FANDOM_USERNAME / FANDOM_PASSWORD "
            "in .env then restart uvicorn. Anonymous mode caps batches to 50 "
            "and limits ~1 req/min, which drops ~40%% of the targeted pros and "
            "all photo URLs."
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

            # Prefer the verified direct CDN URL (set by the wikitext path —
            # we already confirmed the file exists). Fall back to Special:FilePath
            # for Cargo-derived filenames, which may 404 silently.
            verified_url = rec.get("ImageUrl")
            image_filename = (rec.get("Image") or "").strip()
            if verified_url:
                meta.player_image_url = verified_url
                images_found += 1
            elif image_filename:
                meta.player_image_url = _file_path_url(image_filename)
                images_found += 1
            else:
                meta.player_image_url = None  # explicitly clear when no photo found

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


def run_leaguepedia_sync_sync(db: Session, prefer_wikitext: bool = True) -> dict:
    """
    Synchronous Leaguepedia sync.

    Strategy:
    1. Targeted names come from PlayerMeta.leaguepedia_id (Lolpros-matched).
    2. Default path: wikitext infobox parse via action=query (Cargo bypass).
       Fandom's Cargo extension has been intermittently down with
       internal_api_error_MWException; the wikitext path uses standard
       MediaWiki action=query which keeps working.
    3. If wikitext yields 0, fall back to Cargo (Player= equality, slow).
    4. If no targeted names exist, bulk Cargo fetch with Residency filter.
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

    pros: list[dict] = []
    used_path = "none"

    if target_names and prefer_wikitext:
        logger.info(
            "Leaguepedia: wikitext-parse sync for %d names (Cargo bypass)",
            len(target_names),
        )
        pros = fetch_pros_via_parse(target_names, pace_sec=1.0)
        used_path = "wikitext"
        if len(pros) < 5 and len(target_names) > 10:
            # Wikitext path failed too — try Cargo as a last resort.
            logger.warning("wikitext returned only %d/%d — trying Cargo", len(pros), len(target_names))
            try:
                pros = fetch_pros_by_name(target_names)
                used_path = "cargo_targeted_after_wikitext_failure"
            except Exception as exc:
                logger.warning("Cargo also failed: %s", exc)
    elif target_names:
        logger.info("Leaguepedia: targeted Cargo sync for %d names", len(target_names))
        try:
            pros = fetch_pros_by_name(target_names)
            used_path = "cargo_targeted"
        except Exception as exc:
            logger.warning("Cargo failed (%s) — falling back to wikitext", exc)
            pros = fetch_pros_via_parse(target_names, pace_sec=1.0)
            used_path = "wikitext_fallback"
    else:
        logger.info("Leaguepedia: no Lolpros-matched names — bulk Cargo fetch")
        try:
            pros = fetch_active_pros(residencies=("Europe",))
            used_path = "cargo_bulk"
        except Exception as exc:
            logger.warning("Cargo bulk failed: %s — no fallback for unsupervised mode", exc)

    lookup = build_lookup(pros)

    stats = sync_players_with_lookup(db, lookup)

    stats["pros_in_lookup"] = len(lookup)
    stats["raw_records_fetched"] = len(pros)
    stats["targeted_names"] = len(target_names)
    stats["path"] = used_path
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