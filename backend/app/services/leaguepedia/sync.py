"""
Leaguepedia sync orchestration.

Top half of the original leaguepedia.py monolith. Pulls fetched records
from `.sources`, builds a normalized-name lookup, then walks every
Player row in the DB and writes matched data into PlayerMeta.

The only entry point external callers should use is
`run_leaguepedia_sync_sync` (sync) or its async wrapper
`run_leaguepedia_sync`.
"""
import logging
import re
import time
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ...models import Player, PlayerMeta
from ..name_matching import strict_name_candidates as _candidate_normalizations
from .sources import (
    _AUTH_STATE,
    _calc_age,
    _cargo_to_record,
    _connect,
    _file_path_url,
    _parse_soloqueue_ids,
    fetch_active_pros,
    fetch_all_active_pros,
    fetch_pros_via_cargo,
    fetch_pros_via_parse,
)

logger = logging.getLogger(__name__)


def build_lookup(pros: list[dict]) -> dict[str, dict]:
    """
    Map normalized_name -> pro_record.
    Indexes Player + every SoloqueueIds entry + parsed Cargo soloqueue
    candidates (set by the EMEA bulk pass).
    """
    lookup: dict[str, dict] = {}

    for r in pros:
        candidates = set()

        if r.get("Player"):
            candidates.add(str(r["Player"]))

        sq = r.get("SoloqueueIds") or ""

        if sq:
            for tok in re.split(r"[;,]", str(sq)):
                tok = tok.strip()
                if tok:
                    candidates.add(tok)

        # Parsed Cargo SoloqueueIds (set by 3rd-pass EMEA bulk fetch)
        for cand in r.get("_soloqueue_candidates") or []:
            candidates.add(cand)

        # All _candidate_normalizations() variants of every candidate so
        # "Reeker#KZN" → "reeker", "BIG Reeker" → "reeker", etc. all map.
        for c in candidates:
            for key in _candidate_normalizations(str(c)):
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

    # Build a puuid → record index from any record that has Lolpros
    # `_lolpros_puuids` attached (set by the 4th pass). This gives us a
    # perfect match against Player.puuid — no name normalization needed.
    puuid_index: dict[str, dict] = {}
    for r in lookup.values():
        for puuid in r.get("_lolpros_puuids") or []:
            if puuid and puuid not in puuid_index:
                puuid_index[puuid] = r
    if puuid_index:
        logger.info(
            "sync_players_with_lookup: built puuid index with %d entries from Lolpros profiles",
            len(puuid_index),
        )

    for processed, p in enumerate(players, start=1):
        if processed % 200 == 0:
            db.commit()

        meta = db.get(PlayerMeta, p.puuid)

        if not meta:
            meta = PlayerMeta(puuid=p.puuid)
            db.add(meta)

        rec = None

        # Priority 0: perfect puuid match (Lolpros profile gave us the
        # encrypted_puuid for every account of every pro it tracks).
        # Reeker#KZN's Riot puuid matches the puuid stored on his Lolpros
        # account → instant reliable identification, no name parsing.
        if not rec:
            rec = puuid_index.get(p.puuid)

        # Region-aware matching. Each Cargo record is tagged with its
        # Residency. We match a Riot account only against records whose
        # residency is consistent with the Riot account's platform —
        # this kills the cross-region collision (Faker's KR
        # "Hide on bush#KR1" was matching sOAZ's KR alt
        # "Baguette on bush" because both indexed the 'bush' fragment).
        PLATFORM_TO_RESIDENCY = {
            "euw1": "EMEA", "eun1": "EMEA", "tr1": "EMEA", "ru": "EMEA",
            "kr": "Korea",
            "na1": "North America",
            "br1": "Brazil",
            "jp1": "Asia Pacific", "oc1": "Asia Pacific",
            "la1": "Latin America", "la2": "Latin America",
        }
        expected_residency = PLATFORM_TO_RESIDENCY.get((p.region or "").lower())

        def _residency_matches(record: dict) -> bool:
            if not expected_residency:
                return True  # Unknown platform — don't restrict
            r_res = record.get("_residency") or record.get("Residency") or ""
            # Accept legacy 'Europe' as EMEA
            if expected_residency == "EMEA" and r_res in ("EMEA", "Europe"):
                return True
            return r_res == expected_residency

        # Priority 1: if Lolpros already mapped this Riot ID to a
        # Leaguepedia canonical name, use that — but only if the
        # residency matches. Without this check, a stale (wrong)
        # leaguepedia_id from a previous bug-affected sync survives
        # and re-matches an EMEA pro to a KR Riot account.
        if not rec and meta.leaguepedia_id:
            for cand in _candidate_normalizations(meta.leaguepedia_id):
                hit = lookup.get(cand)
                if hit and _residency_matches(hit):
                    rec = hit
                    break

        # Priority 2: summoner-name candidates, BUT only accept records
        # whose Residency matches the Riot account's platform region.
        if not rec:
            for candidate in _candidate_normalizations(p.summoner_name or ""):
                hit = lookup.get(candidate)
                if hit and _residency_matches(hit):
                    rec = hit
                    break

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

            # If a Lolpros profile was attached (4th pass), use its data
            # to fill the lolpros_slug + lolpros_profile_json fields too.
            # This means a single sync now populates BOTH wiki + Lolpros
            # data for any pro that exists on either source.
            lolpros_profile = rec.get("_lolpros_profile")
            if lolpros_profile:
                import json as _json
                meta.lolpros_slug = lolpros_profile.get("slug") or meta.lolpros_slug
                meta.lolpros_profile_json = _json.dumps(lolpros_profile)
                # Pull the team from Lolpros if Leaguepedia didn't have it
                team = lolpros_profile.get("team") or {}
                if not meta.current_team and team.get("name"):
                    meta.current_team = team["name"]
                if not meta.current_team_tag and team.get("tag"):
                    meta.current_team_tag = team["tag"]
                logo = ((team.get("logo") or {}).get("url") or "").strip()
                if logo and not meta.current_team_logo_url:
                    meta.current_team_logo_url = logo.replace("http://", "https://", 1)

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


def run_leaguepedia_sync_sync(
    db: Session,
    with_lolpros_bulk: bool = False,
) -> dict:
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

        # 2nd pass: backfill missing birthdates / native names / teams
        # via Special:CargoExport. The wikitext infobox often only stores
        # `birth_date_year` (or nothing); Cargo's Players table holds the
        # full ISO date for many more pros.
        try:
            logger.info("Cargo backfill: probing %d targets via Special:CargoExport", len(target_names))
            cargo_rows = fetch_pros_via_cargo(target_names)
            # Normalize keys: strip + lower + collapse underscores → spaces
            # (wikitext OverviewPage = "Adam_(Adam_Maanane)", Cargo returns
            #  "Adam (Adam Maanane)" — same page, different casing of separator)
            def _norm(s) -> str:
                return str(s or "").replace("_", " ").strip().lower()
            cargo_by_name = {_norm(t.get("Player")): t for t in cargo_rows.values()}
            cargo_by_overview = {_norm(t.get("OverviewPage")): t for t in cargo_rows.values()}
            backfilled_birth = 0
            backfilled_team = 0
            backfilled_native = 0
            for r in pros:
                key1 = _norm(r.get("Player"))
                key2 = _norm(r.get("OverviewPage"))
                crow = cargo_by_overview.get(key2) or cargo_by_name.get(key1)
                if not crow:
                    continue
                if not r.get("Birthdate") and crow.get("Birthdate"):
                    r["Birthdate"] = crow["Birthdate"]
                    backfilled_birth += 1
                if not r.get("Team") and crow.get("Team"):
                    r["Team"] = crow["Team"]
                    backfilled_team += 1
                if not r.get("Name") and crow.get("NativeName"):
                    r["Name"] = crow["NativeName"]
                    backfilled_native += 1
            logger.info(
                "Cargo backfill: +%d birthdates, +%d teams, +%d native names",
                backfilled_birth, backfilled_team, backfilled_native,
            )
            used_path = "wikitext+cargo_export"
        except Exception as exc:
            logger.warning("Cargo backfill skipped (%s)", exc)

        # 3rd pass: pick up pros NOT in Lolpros via bulk Cargo fetch.
        # We pull ALL major residencies (EMEA + Korea + NA + Brazil +
        # Asia Pacific) so the lookup covers every region our Riot
        # ingest hits, not just EU.
        try:
            global_rows = fetch_all_active_pros(
                residencies=["EMEA", "Korea", "North America", "Brazil", "Asia Pacific"],
            )
            if global_rows:
                seen_overview = {str(r.get("OverviewPage") or "").lower() for r in pros}
                added = 0
                for row in global_rows:
                    op = str(row.get("OverviewPage") or "").lower()
                    if op in seen_overview:
                        continue
                    rec = _cargo_to_record(row)
                    rec["_soloqueue_candidates"] = _parse_soloqueue_ids(row.get("SoloqueueIds"))
                    # Tag with residency so the matcher can scope correctly
                    rec["_residency"] = row.get("Residency") or rec.get("Residency")
                    pros.append(rec)
                    seen_overview.add(op)
                    added += 1
                logger.info("3rd pass (Cargo global bulk): +%d pros", added)
                used_path = "wikitext+cargo_export+global_bulk"
        except Exception as exc:
            logger.warning("3rd pass (global bulk) skipped (%s)", exc)

        # 4th pass (OPTIONAL — only when with_lolpros_bulk=True).
        # Bulk-crawl every active EMEA pro's Lolpros profile. Slow
        # (~5 min for 4875 fetches at concurrency 8) but unlocks perfect
        # puuid-based matching + Lolpros team / slug / accounts data.
        # Skipped by default so the regular Sync Leaguepedia button
        # stays at ~75 s. Trigger via /admin/sync-leaguepedia-full.
        if with_lolpros_bulk:
            try:
                from ..lolpros import (
                    fetch_lolpros_profiles_bulk,
                    lolpros_slug_guess,
                    best_account_in_profile,
                    extract_puuids_from_profile,
                )
                import asyncio as _asyncio

                slug_to_rec: dict[str, dict] = {}
                for rec in pros:
                    slug = lolpros_slug_guess(rec.get("Player"))
                    if slug and slug not in slug_to_rec:
                        slug_to_rec[slug] = rec
                slugs = list(slug_to_rec.keys())
                logger.info(
                    "4th pass: probing %d Lolpros profiles (concurrency=8)",
                    len(slugs),
                )
                t0 = time.time()
                profiles = _asyncio.run(fetch_lolpros_profiles_bulk(slugs, concurrency=8))
                logger.info(
                    "4th pass: got %d/%d Lolpros profiles in %.1fs",
                    len(profiles), len(slugs), time.time() - t0,
                )

                n_with_acc = 0
                n_filled_ids = 0
                for slug, profile in profiles.items():
                    rec = slug_to_rec.get(slug)
                    if not rec:
                        continue
                    rec["_lolpros_profile"] = profile
                    rec["_lolpros_puuids"] = [
                        p for p, _ in extract_puuids_from_profile(profile)
                    ]
                    if rec["_lolpros_puuids"]:
                        n_with_acc += 1
                    # When wikitext didn't give us SoloqueueIds, fall back
                    # to the Lolpros best-ranked account's IGN (per user
                    # request: "tu prend celui de leur LolPros avec le
                    # meilleur Rank").
                    if not rec.get("SoloqueueIds"):
                        best = best_account_in_profile(profile)
                        if best and best.get("summoner_name"):
                            rec["SoloqueueIds"] = best["summoner_name"]
                            rec["_soloqueue_candidates"] = [best["summoner_name"].split("#")[0]]
                            n_filled_ids += 1
                logger.info(
                    "4th pass: +%d puuid-matched, +%d SoloqueueIds backfilled",
                    n_with_acc, n_filled_ids,
                )
                used_path = "wikitext+cargo_export+emea_bulk+lolpros_profiles"
            except Exception as exc:
                logger.warning("4th pass (Lolpros profiles) skipped (%s)", exc)
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