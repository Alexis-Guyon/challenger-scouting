"""
Ingestion pipeline:
1. Pull Challenger league → players
2. For each player, pull match history (SoloQ only)
3. For each new match, pull match + timeline, parse, persist
4. Compute team-level shares (damage_share, kill_participation) at insert
"""
import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..config import settings
from ..db import SessionLocal
from ..models import Match, MatchParticipant, Player, RankSnapshot
from .riot_client import RiotClient
from .timeline_parser import parse_match_advanced

logger = logging.getLogger(__name__)


def patch_from_version(game_version: str) -> str:
    """'14.9.575.1234' -> '14.9'"""
    parts = game_version.split(".")
    return ".".join(parts[:2]) if len(parts) >= 2 else game_version


async def _ingest_league_entries(
    client: RiotClient, db: Session, entries: list[dict], tier_label: str,
) -> list[Player]:
    """Persist a list of league/v4 entries as Player + RankSnapshot rows.

    Commits in batches of 25 so the DB is queryable mid-run — without this
    a 200-player Master ingest would buffer 8 minutes of inserts and only
    surface them at the very end (which made progress polling lie).
    """
    players: list[Player] = []
    now = datetime.now(timezone.utc)
    BATCH = 25
    for idx, e in enumerate(entries, start=1):
        puuid = e.get("puuid")
        sid = e.get("summonerId")
        if not puuid and not sid:
            logger.warning("entry has neither puuid nor summonerId: %s", e)
            continue

        summ = None
        try:
            if puuid:
                summ = await client.summoner_by_puuid(puuid)
            else:
                summ = await client.summoner_by_id(sid)
        except Exception as exc:
            logger.warning("summoner fetch failed: %s", exc)
        if summ:
            puuid = summ.get("puuid", puuid)
            sid = summ.get("id", sid)
        if not puuid:
            continue

        p = db.get(Player, puuid)
        if not p:
            p = Player(puuid=puuid)
            db.add(p)

        riot_name = None
        try:
            acct = await client.account_by_puuid(puuid)
            if acct:
                gn = acct.get("gameName") or ""
                tl = acct.get("tagLine") or ""
                riot_name = f"{gn}#{tl}" if gn else None
        except Exception as exc:
            logger.warning("account fetch failed: %s", exc)

        p.summoner_id = sid
        p.summoner_name = (
            riot_name
            or (summ.get("name") if summ else None)
            or e.get("summonerName")
            or (puuid[:8] if puuid else "unknown")
        )
        p.region = client.platform
        p.account_level = summ.get("summonerLevel", 0) if summ else 0
        p.last_updated = now

        snap = RankSnapshot(
            puuid=puuid,
            tier=tier_label,
            rank=e.get("rank", "I"),
            lp=e.get("leaguePoints", 0),
            wins=e.get("wins", 0),
            losses=e.get("losses", 0),
            snapshot_date=now,
        )
        db.add(snap)
        players.append(p)
        # Flush every BATCH entries so the DB reflects partial progress
        # (mid-run polling no longer reports zero rows for 8 minutes).
        if idx % BATCH == 0:
            db.commit()
            logger.info("ingest %s: %d/%d committed", tier_label.lower(), idx, len(entries))
    db.commit()
    logger.info("ingested %d %s players", len(players), tier_label.lower())
    return players


async def ingest_tier_players(
    client: RiotClient, db: Session, tier: str, limit: int = 50,
) -> list[Player]:
    """
    Pull players for a given tier (challenger / grandmaster / master).
    Master leagues are huge (~30k entries on EUW); always cap with `limit`.
    """
    tier = (tier or "challenger").lower()
    if tier == "challenger":
        league = await client.challenger_league()
        label = "CHALLENGER"
    elif tier in ("grandmaster", "gm"):
        league = await client.grandmaster_league()
        label = "GRANDMASTER"
    elif tier == "master":
        league = await client.master_league()
        label = "MASTER"
    else:
        raise ValueError(f"unknown tier: {tier}")

    entries = league.get("entries", [])
    # Sort by LP desc so when we cap at `limit` we keep the strongest players,
    # not a random alphabetic slice.
    entries.sort(key=lambda x: -(x.get("leaguePoints") or 0))
    entries = entries[:limit]
    return await _ingest_league_entries(client, db, entries, label)


# Back-compat alias used by older callers
async def ingest_challenger_players(client: RiotClient, db: Session, limit: int = 50) -> list[Player]:
    return await ingest_tier_players(client, db, "challenger", limit=limit)


async def ingest_player_matches(client: RiotClient, db: Session, puuid: str, count: int = 30):
    """Fetch match list for a player and persist each match + participants."""
    match_ids = await client.match_ids(puuid, count=count, queue=420)
    new_count = 0
    for mid in match_ids:
        existing = db.get(Match, mid)
        if existing:
            continue
        try:
            match_data = await client.match(mid)
            timeline_data = await client.match_timeline(mid)
        except Exception as exc:
            logger.warning("match fetch failed %s: %s", mid, exc)
            continue
        if not match_data or not timeline_data:
            continue

        info = match_data.get("info", {})
        if info.get("queueId") != 420:
            continue

        m = Match(
            match_id=mid,
            region=settings.region,
            patch=patch_from_version(info.get("gameVersion", "")),
            game_creation=datetime.fromtimestamp(info.get("gameCreation", 0) / 1000, tz=timezone.utc),
            game_duration_sec=info.get("gameDuration", 0),
            queue_id=info.get("queueId"),
            blue_win=any(t.get("teamId") == 100 and t.get("win") for t in info.get("teams", [])),
        )
        db.add(m)

        parsed = parse_match_advanced(match_data, timeline_data)
        team_dmg = {100: 0, 200: 0}
        team_kills = {100: 0, 200: 0}
        for pp in parsed:
            team_dmg[pp["team_id"]] = team_dmg.get(pp["team_id"], 0) + pp["damage_to_champs"]
            team_kills[pp["team_id"]] = team_kills.get(pp["team_id"], 0) + pp["kills"]

        for pp in parsed:
            tdmg = team_dmg.get(pp["team_id"], 1) or 1
            tkills = team_kills.get(pp["team_id"], 0)
            dmg_share = pp["damage_to_champs"] / tdmg if tdmg else 0
            kp = (pp["kills"] + pp["assists"]) / tkills if tkills else 0
            kda = (pp["kills"] + pp["assists"]) / max(pp["deaths"], 1)

            mp = MatchParticipant(
                match_id=mid,
                puuid=pp["puuid"],
                team_id=pp["team_id"],
                role=pp["role"],
                champion_id=pp["champion_id"],
                champion_name=pp["champion_name"],
                win=pp["win"],
                kills=pp["kills"],
                deaths=pp["deaths"],
                assists=pp["assists"],
                cs_total=pp["cs_total"],
                gold_earned=pp["gold_earned"],
                damage_to_champs=pp["damage_to_champs"],
                damage_taken=pp["damage_taken"],
                vision_score=pp["vision_score"],
                wards_placed=pp["wards_placed"],
                wards_killed=pp["wards_killed"],
                control_wards=pp["control_wards"],
                solo_kills=pp["solo_kills"],
                objective_dmg=pp["objective_dmg"],
                dragon_kills=pp["dragon_kills"],
                baron_kills=pp["baron_kills"],
                turret_kills=pp["turret_kills"],
                gd_at_15=pp["gd_at_15"],
                xpd_at_15=pp["xpd_at_15"],
                csd_at_15=pp["csd_at_15"],
                cs_at_10=pp["cs_at_10"],
                cs_at_15=pp["cs_at_15"],
                early_deaths=pp["early_deaths"],
                damage_share=dmg_share,
                kill_participation=kp,
                kda=kda,
            )
            db.add(mp)

            # Auto-create stub Player rows for opponents so we can scout them too
            existing_pp = db.get(Player, pp["puuid"])
            if not existing_pp:
                # Stub player auto-imported as opponent. Use the current
                # client's platform so cross-region match histories don't
                # mislabel opponents as the wrong region.
                db.add(Player(puuid=pp["puuid"], summoner_name="(unknown)", region=client.platform))
        db.commit()
        new_count += 1
    logger.info("player %s: %d new matches", puuid[:8], new_count)
    return new_count


async def run_ingestion(
    player_limit: int = 30,
    matches_per_player: int | None = None,
    progress_cb=None,
    tiers: list[str] | None = None,
    regions: list[str] | None = None,
):
    """
    Run the full SoloQ ingestion pipeline across one or more regions.

    `tiers` selects which league(s) to pull from. Defaults to ['challenger'].
    Supported: 'challenger', 'grandmaster', 'master'.
    `regions` is a list of platform codes (e.g. ['euw1', 'kr', 'na1']).
    Defaults to [settings.platform] for back-compat.

    `player_limit` is applied PER TIER PER REGION. So player_limit=200,
    tiers=[chall,gm], regions=[euw1,kr] = up to 200×2×2 = 800 players.

    Regions are run SEQUENTIALLY (not in parallel) because Riot's rate
    limit is per-API-key — running multiple regions in parallel would
    just hit the cap faster without speeding anything up. Within a
    region, a shared RateLimiter is used.

    Resumability: ingest_player_matches() already skips matches already
    in DB, so re-running an interrupted ingestion picks up where it stopped.
    """
    from .riot_client import PLATFORM_TO_REGION, RateLimiter

    matches_per_player = matches_per_player or settings.match_history_count
    tiers = [t.lower() for t in (tiers or ["challenger"])]
    regions = [r.lower() for r in (regions or [settings.platform])]

    # One shared limiter across all regional clients (Riot's quota is
    # per-API-key; a separate limiter per region would over-fire).
    shared_limiter = RateLimiter()

    db = SessionLocal()
    try:
        all_targets: list[tuple[str, Player]] = []  # (platform, Player)
        seen: set[str] = set()

        for platform in regions:
            super_region = PLATFORM_TO_REGION.get(platform)
            if not super_region:
                logger.warning("unknown platform %s — skipping", platform)
                continue
            async with RiotClient(platform=platform, region=super_region, limiter=shared_limiter) as client:
                for tier in tiers:
                    try:
                        tier_players = await ingest_tier_players(client, db, tier, limit=player_limit)
                    except Exception as exc:
                        logger.exception("league fetch failed for region=%s tier=%s: %s",
                                         platform, tier, exc)
                        continue
                    for p in tier_players:
                        if p.puuid not in seen:
                            seen.add(p.puuid)
                            all_targets.append((platform, p))

        total = len(all_targets)
        # Group by platform so we can reuse one client per region for
        # all match fetches (saves on client setup; same shared limiter).
        from collections import defaultdict
        by_platform: dict[str, list[Player]] = defaultdict(list)
        for plat, p in all_targets:
            by_platform[plat].append(p)

        idx = 0
        for platform, players in by_platform.items():
            super_region = PLATFORM_TO_REGION.get(platform, settings.region)
            async with RiotClient(platform=platform, region=super_region, limiter=shared_limiter) as client:
                for p in players:
                    idx += 1
                    new_count = 0
                    try:
                        new_count = await ingest_player_matches(client, db, p.puuid, count=matches_per_player)
                    except Exception as exc:
                        logger.exception("matches ingest failed for %s: %s", p.summoner_name, exc)
                    if progress_cb:
                        try:
                            progress_cb(idx, total, f"[{platform.upper()}] {p.summoner_name}", new_count)
                        except Exception:
                            pass
    finally:
        db.close()


def _resolve_keys() -> list[str]:
    """Return the list of API keys configured. Falls back to the single-
    key `riot_api_key` when `riot_api_keys` is empty."""
    raw = (settings.riot_api_keys or "").strip()
    if raw:
        keys = [k.strip() for k in raw.split(",") if k.strip()]
        if keys:
            return keys
    return [settings.riot_api_key]


def _partition_work(
    tiers: list[str],
    regions: list[str],
    keys: list[str],
    strategy: str,
) -> list[tuple[str, list[tuple[str, str]]]]:
    """Split (tier × region) cells across keys.

    Returns: [(api_key, [(tier, region), ...]), ...]
    Each key gets a list of work units to process sequentially with its
    own RateLimiter. The list of (key, units) tuples is what the multi-
    key runner executes in parallel.
    """
    cells = [(t, r) for t in tiers for r in regions]
    n_keys = len(keys)
    out: list[tuple[str, list[tuple[str, str]]]] = []

    if n_keys == 1 or strategy == "round_robin":
        # Stripe cells across keys
        groups: dict[int, list[tuple[str, str]]] = {i: [] for i in range(n_keys)}
        for i, cell in enumerate(cells):
            groups[i % n_keys].append(cell)
        for i, k in enumerate(keys):
            if groups[i]:
                out.append((k, groups[i]))
        return out

    if strategy == "tier":
        # 1 key per tier — cycle through keys if more tiers than keys
        for i, t in enumerate(tiers):
            k = keys[i % n_keys]
            units = [(t, r) for r in regions]
            # Merge into existing entry for this key (preserves order)
            existing = next((e for e in out if e[0] == k), None)
            if existing:
                existing[1].extend(units)
            else:
                out.append((k, units))
        return out

    if strategy == "region":
        for i, r in enumerate(regions):
            k = keys[i % n_keys]
            units = [(t, r) for t in tiers]
            existing = next((e for e in out if e[0] == k), None)
            if existing:
                existing[1].extend(units)
            else:
                out.append((k, units))
        return out

    if strategy == "tier_region":
        for i, cell in enumerate(cells):
            k = keys[i % n_keys]
            existing = next((e for e in out if e[0] == k), None)
            if existing:
                existing[1].append(cell)
            else:
                out.append((k, [cell]))
        return out

    raise ValueError(f"unknown partition strategy {strategy!r}")


async def _ingest_one_unit(
    api_key: str,
    tier: str,
    platform: str,
    player_limit: int,
    matches_per_player: int,
    progress_cb=None,
) -> dict:
    """Fetch the tier's ladder for `platform` then ingest each player's
    recent matches. Uses a key-scoped RateLimiter (independent budget).

    Returns {tier, region, players, matches_added}.
    """
    from .riot_client import PLATFORM_TO_REGION, RateLimiter

    super_region = PLATFORM_TO_REGION.get(platform, settings.region)
    limiter = RateLimiter()  # per-(unit) — actually per-key, see runner below
    matches_added = 0
    n_players = 0

    db = SessionLocal()
    try:
        async with RiotClient(api_key=api_key, platform=platform,
                              region=super_region, limiter=limiter) as client:
            try:
                players = await ingest_tier_players(client, db, tier, limit=player_limit)
            except Exception as exc:
                logger.exception("league fetch failed for tier=%s region=%s: %s",
                                 tier, platform, exc)
                return {"tier": tier, "region": platform, "players": 0,
                        "matches_added": 0, "error": str(exc)}
            for p in players:
                n_players += 1
                try:
                    added = await ingest_player_matches(client, db, p.puuid,
                                                         count=matches_per_player)
                    matches_added += added
                except Exception as exc:
                    logger.exception("matches ingest failed for %s: %s",
                                     p.summoner_name, exc)
                if progress_cb:
                    try:
                        progress_cb(tier, platform, n_players, len(players),
                                    p.summoner_name, matches_added)
                    except Exception:
                        pass
    finally:
        db.close()

    return {"tier": tier, "region": platform, "players": n_players,
            "matches_added": matches_added}


async def run_multi_key_ingestion(
    tiers: list[str],
    regions: list[str],
    keys: list[str] | None = None,
    partition: str = "tier",
    player_limit: int = 500,
    matches_per_player: int = 30,
    progress_cb=None,
) -> dict:
    """Parallel multi-key ingest.

    Each key gets its own RateLimiter (Riot quota is per-key) so the
    keys run TRULY in parallel. Within one key, work units are processed
    sequentially.

    Defaults:
      - keys: from settings (.env RIOT_API_KEYS, falls back to RIOT_API_KEY)
      - partition: "tier" (1 key per tier — see _partition_work for options)
    """
    from .riot_client import PLATFORM_TO_REGION, RateLimiter

    keys = keys or _resolve_keys()
    if not keys:
        raise RuntimeError("no API keys configured")
    tiers = [t.lower() for t in tiers]
    regions = [r.lower() for r in regions]

    plan = _partition_work(tiers, regions, keys, partition)

    logger.info(
        "Multi-key ingest: %d key(s), %d tier(s), %d region(s), partition=%s",
        len(keys), len(tiers), len(regions), partition,
    )
    for i, (key, units) in enumerate(plan):
        logger.info("  key #%d (%s…) → %d unit(s): %s",
                    i + 1, key[:10], len(units),
                    ", ".join(f"{t}/{r}" for t, r in units))

    # Each key gets its OWN limiter — keys run in parallel without
    # stepping on each other's quota.
    async def _run_for_key(api_key: str, units: list[tuple[str, str]]) -> list[dict]:
        per_key_limiter = RateLimiter()
        results: list[dict] = []
        for tier, platform in units:
            super_region = PLATFORM_TO_REGION.get(platform, settings.region)
            db = SessionLocal()
            try:
                async with RiotClient(api_key=api_key, platform=platform,
                                      region=super_region,
                                      limiter=per_key_limiter) as client:
                    try:
                        players = await ingest_tier_players(client, db, tier,
                                                             limit=player_limit)
                    except Exception as exc:
                        logger.exception("league fetch failed tier=%s region=%s",
                                         tier, platform)
                        results.append({"tier": tier, "region": platform,
                                        "players": 0, "matches_added": 0,
                                        "error": str(exc)})
                        continue
                    matches_added = 0
                    for i, p in enumerate(players, start=1):
                        try:
                            added = await ingest_player_matches(client, db, p.puuid,
                                                                 count=matches_per_player)
                            matches_added += added
                        except Exception as exc:
                            logger.warning("matches failed for %s: %s",
                                           p.summoner_name, exc)
                        if progress_cb:
                            try:
                                progress_cb(tier, platform, i, len(players),
                                            p.summoner_name, matches_added)
                            except Exception:
                                pass
                    results.append({"tier": tier, "region": platform,
                                    "players": len(players),
                                    "matches_added": matches_added})
            finally:
                db.close()
        return results

    # Launch one task per key, gather in parallel
    tasks = [_run_for_key(key, units) for key, units in plan]
    all_results = await asyncio.gather(*tasks, return_exceptions=False)

    flat: list[dict] = []
    for sub in all_results:
        flat.extend(sub)
    summary = {
        "keys_used": len(plan),
        "units_completed": len(flat),
        "total_players": sum(r.get("players", 0) for r in flat),
        "total_matches_added": sum(r.get("matches_added", 0) for r in flat),
        "results": flat,
    }
    logger.info("Multi-key ingest done: %s", summary)
    return summary


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_ingestion())
