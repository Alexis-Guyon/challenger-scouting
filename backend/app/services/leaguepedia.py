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


def fetch_pros_via_cargo(
    names: list[str],
    site: "mwclient.Site | None" = None,
    chunk_size: int = 30,
) -> dict[str, dict]:
    """Bulk Cargo fetch — primary source for player metadata.

    Cargo's `Players` table has 1 row per pro (disambig-aware via
    `OverviewPage`) and exposes the cleanly-stored Birthdate, Team,
    Country, Residency, Role, IsRetired, SoloqueueIds, ContractEnd,
    NationalityPrimary, Image. Way more reliable than parsing the raw
    wikitext infobox (some fields like full birthdate are stored
    only in Cargo, not in the infobox params).

    Returns a dict {Player_display_name -> row_dict}. When multiple pros
    share the same display name (Adam, Caps, etc.), the LAST one wins
    in this dict — caller should use OverviewPage as the unique key
    if it cares about disambiguation.
    """
    if not names:
        return {}
    import httpx

    out: dict[str, dict] = {}
    authed = site is not None and getattr(site, "logged_in", False)

    chunks = [names[i:i + chunk_size] for i in range(0, len(names), chunk_size)]
    logger.info(
        "Leaguepedia Cargo: %d names in %d chunk(s) (auth=%s)",
        len(names), len(chunks), authed,
    )

    fields = (
        "OverviewPage,Player,Name,NativeName,Birthdate,Country,"
        "NationalityPrimary,Residency,Team,Role,IsRetired,IsRetiredFromGame,"
        "SoloqueueIds,ContractEnd,Image,FavCharacter1,FavCharacter2,"
        "FavCharacter3,FavCharacter4,FavCharacter5,Stream,Twitter,"
        "Instagram,Youtube,Discord,LolPros"
    )

    for ci, chunk in enumerate(chunks, start=1):
        # Quote-escape names; Cargo IN clause = "x","y","z"
        in_clause = ",".join('"' + n.replace('"', '\\"') + '"' for n in chunk)
        params = {
            "action": "cargoquery",
            "tables": "Players",
            "fields": fields,
            "where": f"Player IN ({in_clause})",
            "limit": "500",
            "format": "json",
        }
        # Retry up to 3 times on Cargo MWException — Fandom's Cargo
        # extension is intermittently flaky on larger IN clauses.
        rows = []
        for attempt in range(3):
            try:
                if authed:
                    r = site.connection.get(f"https://{LP_HOST}/api.php", params=params, timeout=30)
                else:
                    r = httpx.get(f"https://{LP_HOST}/api.php", params=params, timeout=30,
                                   headers={"User-Agent": USER_AGENT})
                data = r.json()
            except Exception as exc:
                logger.warning("Cargo chunk %d/%d attempt %d failed: %s", ci, len(chunks), attempt+1, exc)
                time.sleep(1.5 ** attempt)
                continue

            err = data.get("error") or {}
            err_code = err.get("code", "")
            if err_code == "internal_api_error_MWException":
                if attempt < 2:
                    logger.info("Cargo chunk %d/%d MWException, retry %d/3 after %ds", ci, len(chunks), attempt+1, 1 + attempt)
                    time.sleep(1 + attempt)
                    continue
                # Final attempt: split chunk in half and recurse — usually
                # the smaller IN clause survives.
                if len(chunk) > 1:
                    mid = len(chunk) // 2
                    logger.info("Cargo chunk %d/%d MWException after retries — splitting %d→%d+%d",
                                ci, len(chunks), len(chunk), mid, len(chunk)-mid)
                    sub_a = fetch_pros_via_cargo(chunk[:mid], site=site, chunk_size=mid)
                    sub_b = fetch_pros_via_cargo(chunk[mid:], site=site, chunk_size=len(chunk)-mid)
                    out.update(sub_a)
                    out.update(sub_b)
                    rows = []  # Already merged via recursion
                    break
                else:
                    logger.warning("Cargo single-name failed irrevocably for: %s", chunk[0])
                    break
            elif err_code == "ratelimited":
                wait = 5 + 5 * attempt
                logger.warning("Cargo chunk %d/%d rate-limited, waiting %ds (attempt %d/3)",
                               ci, len(chunks), wait, attempt+1)
                time.sleep(wait)
                continue
            elif err_code:
                logger.warning("Cargo chunk %d/%d API error: %s — %s",
                               ci, len(chunks), err_code, err.get("info"))
                break
            rows = data.get("cargoquery", []) or []
            break

        for row in rows:
            t = row.get("title") or {}
            key = t.get("OverviewPage") or t.get("Player") or ""
            if key:
                out[key] = t

        logger.info("Cargo chunk %d/%d: %d rows", ci, len(chunks), len(rows))
        if ci < len(chunks):
            # Cargo's per-IP rate limit on lol.fandom.com is roughly 1 req/sec
            # for authenticated users. We pace at 1.5s with linear back-off
            # whenever a `ratelimited` error is hit.
            time.sleep(1.5)

    logger.info("Cargo: %d unique pros from %d names", len(out), len(names))
    return out


def _cargo_to_record(t: dict) -> dict:
    """Normalize a Cargo Players row into the same shape as wikitext records."""
    overview = t.get("OverviewPage") or ""
    is_retired = (t.get("IsRetired") or "0").strip() in ("1", "Yes", "yes", "true")
    return {
        "Player": t.get("Player") or overview.replace("_", " "),
        "OverviewPage": overview,
        "Country": t.get("Country") or None,
        "NationalityPrimary": t.get("NationalityPrimary") or t.get("Country") or None,
        "Residency": t.get("Residency") or None,
        "Birthdate": t.get("Birthdate") or None,
        "Role": t.get("Role") or None,
        "Team": t.get("Team") or "",
        "IsRetired": "1" if is_retired else "0",
        "SoloqueueIds": (t.get("SoloqueueIds") or "").replace("\n", ";"),
        "ContractEnd": t.get("ContractEnd") or None,
        "Image": t.get("Image") or None,
        "ImageUrl": None,  # filled by a follow-up pageimages call
        # Bonus fields surfaced by Cargo (currently unused downstream but
        # easy to read by future callers — see PlayerMeta TODO).
        "Name": t.get("Name") or None,
        "FavChampions": [
            t.get(f"FavCharacter{i}") for i in range(1, 6)
            if t.get(f"FavCharacter{i}")
        ],
        "Twitter": t.get("Twitter") or None,
        "Twitch": t.get("Stream") or None,
        "Instagram": t.get("Instagram") or None,
        "Youtube": t.get("Youtube") or None,
    }


def _record_from_page(page: dict, fallback_name: str = "") -> dict | None:
    """Build a flat record from a single MediaWiki action=query page object.

    The page MUST have been queried with at least:
        prop=revisions|pageimages
        rvprop=content
        rvslots=main
        piprop=original|name
    """
    if not page or page.get("missing") or page.get("invalid"):
        return None
    revs = page.get("revisions") or []
    if not revs:
        return None
    content = (revs[0].get("slots") or {}).get("main", {}).get("content", "")
    info = _parse_infobox(content)
    if not info:
        return None

    page_title = page.get("title") or fallback_name
    is_retired = info.get("isretired", "no").lower() in ("yes", "true", "1")

    # Photo URL: Leaguepedia attaches the player's portrait as the page's
    # "page image" via {{PageImage}} in the infobox. action=query &
    # prop=pageimages gives us the direct CDN URL with no extra round-trip.
    image_url = None
    image_hint = None
    original = page.get("original") or {}
    if original.get("source"):
        image_url = original["source"]
        image_hint = page.get("pageimage")  # bare filename
    # If pageimages didn't return one, try the infobox `image=` field.
    if not image_url:
        infobox_img = info.get("image") or info.get("portrait")
        if infobox_img:
            image_hint = infobox_img
            image_url = _file_path_url(infobox_img)

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
        "Image": image_hint,
        "ImageUrl": image_url,
        # Bonus fields the wikitext infobox carries — most pros have at
        # least a few. Stored on PlayerMeta when populated.
        "Name": info.get("name") or None,                    # Real name
        "Pronoun": info.get("pronoun") or None,
        "AltNames": info.get("compid1") or None,             # Old IGN
        "FavChampions": [
            info.get(f"favchamp{i}" if i > 0 else "favchamp")
            for i in range(0, 6)
            if info.get(f"favchamp{i}" if i > 0 else "favchamp")
        ],
        # Social media — values are usernames OR full URLs depending on
        # the field. The frontend normalizes them.
        "Twitter": info.get("twitter") or None,
        "Twitch": info.get("stream") or None,
        "Instagram": info.get("instagram") or None,
        "Youtube": info.get("youtube") or None,
        "Tiktok": info.get("tiktok") or None,
    }


def fetch_pros_combined(
    names: list[str],
    site: "mwclient.Site | None" = None,
) -> list[dict]:
    """Best-of-both fetch: Cargo for player metadata + pageimages for photos.

    Cargo gives us 100% of the structured fields (birthdate, team, role,
    contract, nationality, ids…) in a single bulk query. But Cargo's
    `Image` column is often empty, so we follow up with `prop=pageimages`
    on the OverviewPages we got back to grab the direct CDN URLs.
    """
    if not names:
        return []
    import httpx

    cargo_rows = fetch_pros_via_cargo(names, site=site)
    records = [_cargo_to_record(t) for t in cargo_rows.values()]
    if not records:
        return []

    # Resolve images via pageimages on every fetched OverviewPage
    overview_pages = [r["OverviewPage"] for r in records if r.get("OverviewPage")]
    image_urls = _resolve_page_images(overview_pages, site=site)
    for r in records:
        url = image_urls.get(r["OverviewPage"])
        if url:
            r["ImageUrl"] = url
            if not r.get("Image"):
                # pageimages returns the bare filename too; keep it as a hint
                r["Image"] = url.rsplit("/", 1)[-1].split("?")[0]

    return records


def _resolve_page_images(
    overview_pages: list[str],
    site: "mwclient.Site | None" = None,
    chunk_size: int = 50,
) -> dict[str, str]:
    """Return {OverviewPage: image_cdn_url} via prop=pageimages."""
    if not overview_pages:
        return {}
    import httpx

    authed = site is not None and getattr(site, "logged_in", False)
    out: dict[str, str] = {}
    chunks = [overview_pages[i:i + chunk_size]
              for i in range(0, len(overview_pages), chunk_size)]

    for ci, chunk in enumerate(chunks, start=1):
        titles = "|".join(t.replace(" ", "_") for t in chunk)
        params = {
            "action": "query", "titles": titles,
            "prop": "pageimages", "piprop": "original|name",
            "format": "json", "formatversion": "2", "redirects": "1",
        }
        try:
            if authed:
                r = site.connection.get(f"https://{LP_HOST}/api.php", params=params, timeout=30)
            else:
                r = httpx.get(f"https://{LP_HOST}/api.php", params=params, timeout=30,
                               headers={"User-Agent": USER_AGENT})
            data = r.json()
        except Exception as exc:
            logger.warning("pageimages chunk %d/%d failed: %s", ci, len(chunks), exc)
            continue

        if data.get("error"):
            continue

        # Build forward map for normalized + redirect rewrites
        q = data.get("query") or {}
        title_map: dict[str, str] = {}
        for rule in q.get("normalized") or []:
            title_map[rule["from"]] = rule["to"]
        for rule in q.get("redirects") or []:
            title_map[rule["from"]] = rule["to"]
        def _final(t: str) -> str:
            seen = set()
            while t in title_map and t not in seen:
                seen.add(t)
                t = title_map[t]
            return t

        page_by_title = {p.get("title", ""): p for p in q.get("pages") or []}
        for original in chunk:
            normalized = original.replace(" ", "_")
            final_title = _final(normalized)
            page = page_by_title.get(final_title) or page_by_title.get(final_title.replace("_", " "))
            if not page:
                continue
            original_img = (page.get("original") or {}).get("source")
            if original_img:
                out[original] = original_img

        if ci < len(chunks):
            time.sleep(0.3)

    logger.info("pageimages: resolved %d/%d", len(out), len(overview_pages))
    return out


def fetch_player_via_parse(canonical_name: str) -> dict | None:
    """Single-page fetch — kept for back-compat. Prefer fetch_pros_via_parse
    which batches up to 50 titles per request."""
    if not canonical_name:
        return None
    results = fetch_pros_via_parse([canonical_name])
    return results[0] if results else None


def fetch_pros_via_parse(
    names: list[str],
    site: "mwclient.Site | None" = None,
    pace_sec: float = 0.6,
) -> list[dict]:
    """Bulk wikitext + image fetch using the MediaWiki batch API.

    MediaWiki accepts up to 50 titles per `action=query` (500 if
    authenticated). Combined with `prop=revisions|pageimages` we get
    wikitext + photo URL in one round-trip per chunk — orders of magnitude
    faster than the old one-by-one path (8 min → ~10 s for 469 pros).

    `site`: an authenticated mwclient.Site reuses its cookies for the
    higher 500-title limit. When None we fall back to anonymous httpx
    (still works, just capped at 50 titles per chunk and ~1 req/sec).
    """
    if not names:
        return []
    import httpx

    authed = site is not None and getattr(site, "logged_in", False)
    # MediaWiki caps `titles=` at 50 by default. The 500 limit is gated by
    # the `apihighlimits` right which only sysops + flagged bots have. Our
    # bot account (ChallengerScouting) has neither, so we stay at 50 even
    # when authenticated. Auth still buys us a higher per-IP rate limit
    # and faster CDN routing.
    chunk_size = 50
    base_params = {
        "action": "query",
        "prop": "revisions|pageimages",
        "rvprop": "content",
        "rvslots": "main",
        "piprop": "original|name",
        "format": "json",
        "formatversion": "2",
        "redirects": "1",
    }

    out: list[dict] = []
    title_to_query: dict[str, str] = {}  # original_input -> normalized title
    for n in names:
        if n:
            title_to_query[n] = n.replace(" ", "_")

    chunks = [list(title_to_query.items())[i:i + chunk_size]
              for i in range(0, len(title_to_query), chunk_size)]

    logger.info(
        "Leaguepedia batch: %d names in %d chunk(s) (size=%d, %s)",
        len(title_to_query), len(chunks), chunk_size,
        "authenticated" if authed else "anonymous",
    )

    for ci, chunk in enumerate(chunks, start=1):
        titles_param = "|".join(t for _, t in chunk)
        params = {**base_params, "titles": titles_param}

        try:
            if authed:
                # Reuse mwclient's authenticated requests session
                resp = site.connection.get(
                    f"https://{LP_HOST}/api.php",
                    params=params,
                    timeout=30,
                )
                resp.raise_for_status()
                data = resp.json()
            else:
                with httpx.Client(timeout=30.0, headers={"User-Agent": USER_AGENT}) as client:
                    r = client.get(f"https://{LP_HOST}/api.php", params=params)
                    r.raise_for_status()
                    data = r.json()
        except Exception as exc:
            logger.warning("Leaguepedia batch %d/%d failed: %s", ci, len(chunks), exc)
            time.sleep(pace_sec * 3)
            continue

        # Surface MediaWiki errors instead of silently swallowing them
        # (we just got bitten by an unhandled "toomanyvalues" error
        # returning {"error": {...}} but no "query" key, leading to
        # 0 matches with no log line).
        if data.get("error"):
            err = data["error"]
            logger.warning(
                "Leaguepedia batch %d/%d API error: %s — %s",
                ci, len(chunks), err.get("code"), err.get("info"),
            )
            time.sleep(pace_sec * 2)
            continue
        if data.get("warnings"):
            for module, w in (data.get("warnings") or {}).items():
                logger.info("Leaguepedia batch %d warning [%s]: %s", ci, module, w)

        # MediaWiki returns:
        #   query.normalized: [{from: input, to: normalized_title}, ...]
        #   query.redirects:  [{from: ..., to: ...}, ...]
        #   query.pages:      [{title, missing, revisions, pageimage, original, ...}]
        # We need to track the input → final-title chain to match results.
        q = data.get("query") or {}

        # Build a forward map: any-name-we-encounter -> final page title
        title_map: dict[str, str] = {}
        for rule in q.get("normalized") or []:
            title_map[rule["from"]] = rule["to"]
        for rule in q.get("redirects") or []:
            title_map[rule["from"]] = rule["to"]

        # Resolve each chunk entry to its final title (apply chain transitively)
        def _final_title(title: str) -> str:
            seen = set()
            t = title
            while t in title_map and t not in seen:
                seen.add(t)
                t = title_map[t]
            return t

        page_by_title: dict[str, dict] = {p.get("title", ""): p for p in q.get("pages") or []}

        chunk_matched = 0
        for original_input, normalized_title in chunk:
            final_title = _final_title(normalized_title)
            page = page_by_title.get(final_title) or page_by_title.get(final_title.replace("_", " "))
            if not page:
                continue
            rec = _record_from_page(page, fallback_name=original_input)
            if rec:
                out.append(rec)
                chunk_matched += 1

        logger.info(
            "Leaguepedia batch %d/%d: %d/%d matched",
            ci, len(chunks), chunk_matched, len(chunk),
        )
        if ci < len(chunks):
            time.sleep(pace_sec)

    logger.info("Leaguepedia batch: %d total profiles from %d names", len(out), len(names))

    # ---- 2nd pass: resolve disambiguation pages ----
    # When multiple pros share the same canonical name (Ace, Adam, Alvaro,
    # Akuma…), Leaguepedia returns a {{DisambigPage |player1=… |player2=…}}
    # template that has no infobox. We batch-refetch ALL disambig candidates
    # (every targeted name we couldn't match), parse the template params,
    # and recurse to grab the real player pages.
    fetched_keys = {p["OverviewPage"].lower() for p in out}
    fetched_keys |= {p["Player"].lower() for p in out}
    candidate_disambigs = [
        (orig, norm) for orig, norm in title_to_query.items()
        if orig.lower() not in fetched_keys
    ]
    disambig_targets: list[str] = []
    if candidate_disambigs:
        import re as _re
        # Re-batch them with prop=revisions just like the main pass
        for ci, chunk in enumerate(
            [candidate_disambigs[i:i + chunk_size]
             for i in range(0, len(candidate_disambigs), chunk_size)],
            start=1,
        ):
            titles_param = "|".join(t for _, t in chunk)
            params2 = {
                "action": "query", "titles": titles_param,
                "prop": "revisions", "rvprop": "content", "rvslots": "main",
                "format": "json", "formatversion": "2", "redirects": "1",
            }
            try:
                if authed:
                    d = site.connection.get(f"https://{LP_HOST}/api.php", params=params2, timeout=30).json()
                else:
                    with httpx.Client(timeout=30.0, headers={"User-Agent": USER_AGENT}) as c:
                        d = c.get(f"https://{LP_HOST}/api.php", params=params2).json()
            except Exception:
                continue
            if d.get("error"):
                continue
            for p in (d.get("query") or {}).get("pages") or []:
                revs = p.get("revisions") or []
                if not revs:
                    continue
                content = (revs[0].get("slots") or {}).get("main", {}).get("content", "")
                if "{{DisambigPage" not in content and "{{Disambig" not in content:
                    continue
                # Template params: |playerN=Page Title (Real Name)
                for m in _re.finditer(r"\|\s*player\d*\s*=\s*([^|}\n]+)", content, _re.IGNORECASE):
                    lnk = m.group(1).strip()
                    if lnk and lnk not in disambig_targets:
                        disambig_targets.append(lnk)
                # Fallback: plain wiki-links (older disambig style)
                for lnk in _re.findall(r"\[\[([^|\[\]]+?)\|[^\[\]]+?\]\]", content):
                    lnk = lnk.strip()
                    if "(" in lnk and ")" in lnk and lnk not in disambig_targets:
                        disambig_targets.append(lnk)
            if ci < (len(candidate_disambigs) + chunk_size - 1) // chunk_size:
                time.sleep(pace_sec)

    if disambig_targets:
        logger.info(
            "Leaguepedia 2nd pass: %d disambig links to resolve",
            len(disambig_targets),
        )
        # Recurse with just the disambig targets — these are real player pages
        # so they'll resolve cleanly. Pass site=None to avoid an infinite
        # disambig loop (these targets will themselves never be disambigs).
        extra = fetch_pros_via_parse(disambig_targets, site=site, pace_sec=pace_sec)
        out.extend(extra)
        logger.info("Leaguepedia 2nd pass: %d more profiles fetched", len(extra))

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

            # Bonus enrichment fields from the wikitext infobox.
            # We always overwrite — these fields don't have a canonical
            # source elsewhere, so the latest sync is always authoritative.
            if rec.get("Name"):
                meta.real_name = rec["Name"]
            if rec.get("AltNames"):
                meta.alt_names = rec["AltNames"]
            fav = rec.get("FavChampions") or []
            if fav:
                meta.fav_champions = ",".join(fav)
            if rec.get("Twitter"):
                meta.twitter_handle = rec["Twitter"]
            if rec.get("Twitch"):
                meta.twitch_url = rec["Twitch"]
            if rec.get("Instagram"):
                meta.instagram_handle = rec["Instagram"]
            if rec.get("Youtube"):
                meta.youtube_url = rec["Youtube"]
            if rec.get("Tiktok"):
                meta.tiktok_handle = rec["Tiktok"]

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

    # Connect upfront so the wikitext path can reuse the authed session
    # (5x larger batches: 500 titles/req vs 50 anonymous).
    try:
        site = _connect()
    except Exception as exc:
        logger.warning("Leaguepedia connect failed: %s — wikitext will run anonymous", exc)
        site = None

    if target_names:
        # Primary path: batched wikitext (action=query, prop=revisions+
        # pageimages) — fast (~73s for 471 names), reliable, gives 100% of
        # social media + alt names + 90% of birthdates.
        logger.info(
            "Leaguepedia: batched wikitext for %d names (auth=%s)",
            len(target_names), _AUTH_STATE.get("authed", False),
        )
        pros = fetch_pros_via_parse(target_names, site=site, pace_sec=0.5)
        used_path = "wikitext"

        # NOTE: we previously tried a Cargo opportunistic pass to backfill
        # missing birthdates, but Fandom's Cargo extension is rate-limited
        # to ~1 req/sec AND throws intermittent MWException on bigger IN
        # clauses (hangs the sync for 5+ min on a typical batch).
        # Wikitext alone gets 107/471 birthdates; Cargo could top that up
        # to maybe ~140 but isn't worth the unreliability today. Re-enable
        # by calling fetch_pros_via_cargo() if Fandom fixes the throttle.
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