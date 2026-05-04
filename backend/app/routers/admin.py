"""Admin endpoints to trigger ingestion / aggregation / scoring."""
import asyncio
import logging

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from sqlalchemy.orm import Session

from ..auth import require_admin
from ..config import settings
from ..db import SessionLocal, get_db
from ..services.aggregation import (
    aggregate_all_players,
    compute_champion_distributions,
    compute_lobby_lp,
    compute_role_distributions,
)
from ..services.ingestion import run_ingestion
from ..services.jobs import create_job, get_job, list_jobs, next_job_id, update_job
from ..services.leaguepedia import run_leaguepedia_sync_sync
from ..services.lolpros import run_lolpros_sync_sync
from ..services.scoring import score_all, score_all_champions, score_all_smurfs
from ..services.tournament_ingestion import DEFAULT_LEAGUE_SLUGS, run_tournament_sync_sync

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])
logger = logging.getLogger(__name__)


def _run_pipeline_job(
    job_id: str,
    player_limit: int,
    matches_per_player: int,
    auto_resolve_names: bool = True,
    auto_resolve_max: int = 500,
    send_alerts: bool = True,
    tiers: list[str] | None = None,
    regions: list[str] | None = None,
):
    update_job(job_id, status="running", step="ingest")

    def _on_player_done(idx, total, summoner_name, new_matches):
        update_job(job_id, progress={
            "phase": "ingest",
            "player_idx": idx, "player_total": total,
            "current_player": summoner_name,
            "new_matches_last": new_matches,
        })

    try:
        # Auto-route to multi-key parallel ingest when ≥2 keys are configured
        # (RIOT_API_KEYS env var). Falls back to the legacy single-key path
        # when only RIOT_API_KEY is set.
        from ..services.ingestion import _resolve_keys, run_multi_key_ingestion

        keys = _resolve_keys()
        if len(keys) >= 2:
            update_job(job_id, extras_merge={
                "mode": "multi-key",
                "keys_used": len(keys),
                "partition": settings.daily_ingest_partition,
            })

            def _multi_progress(tier, region, i, n, name, matches_added):
                pct = round(i / max(n, 1) * 100, 1)
                update_job(job_id, progress={
                    "phase": "ingest",
                    "tier": tier, "region": region,
                    "player_idx": i, "player_total": n,
                    "current_player": name,
                    "matches_added": matches_added,
                    "step": f"{tier.upper()}/{region.upper()} {i}/{n} ({pct}%)",
                })

            summary = asyncio.run(run_multi_key_ingestion(
                tiers=tiers or ["challenger"],
                regions=regions or [settings.platform],
                keys=keys,
                partition=settings.daily_ingest_partition,
                player_limit=player_limit,
                matches_per_player=matches_per_player,
                progress_cb=_multi_progress,
            ))
            update_job(job_id, extras_merge={"multi_key_summary": summary})
        else:
            update_job(job_id, extras_merge={"mode": "single-key"})
            asyncio.run(run_ingestion(
                player_limit=player_limit,
                matches_per_player=matches_per_player,
                progress_cb=_on_player_done,
                tiers=tiers,
                regions=regions,
            ))

        if auto_resolve_names:
            update_job(job_id, step="resolve_names")
            try:
                rn_stats = asyncio.run(_resolve_unknown_names_async(auto_resolve_max))
                update_job(job_id, extras_merge={"resolve_names": rn_stats})
            except Exception as exc:
                logger.warning("auto-resolve names failed: %s", exc)
                update_job(job_id, extras_merge={"resolve_names_error": str(exc)})

        update_job(job_id, step="aggregate")
        db = SessionLocal()
        try:
            compute_lobby_lp(db)
            aggregate_all_players(db)
            update_job(job_id, step="distributions")
            compute_role_distributions(db, min_games=1)
            compute_champion_distributions(db)
            update_job(job_id, step="smurf_scoring")
            try:
                from ..services.smurf_ml import train_and_score_all
                ml_stats = train_and_score_all(db)
                update_job(job_id, extras_merge={"smurf_ml": ml_stats})
            except Exception as exc:
                logger.warning("smurf_ml failed (%s) — falling back to heuristic", exc)
                score_all_smurfs(db)
            update_job(job_id, step="scoring")
            score_all(db, min_games=1)
            update_job(job_id, step="champion_scoring")
            score_all_champions(db)

            update_job(job_id, step="rising_stars")
            try:
                from ..services.rising_stars import annotate_rising_stars_in_aggregates
                update_job(job_id, extras_merge={"rising_stars": annotate_rising_stars_in_aggregates(db)})
            except Exception as exc:
                logger.warning("rising stars annotation failed: %s", exc)

            if send_alerts:
                update_job(job_id, step="alerts")
                try:
                    from ..services.alerts import run_alerts_check, run_alert_rules
                    sent = run_alerts_check(db)
                    rules_sent = run_alert_rules(db)
                    update_job(job_id, extras_merge={
                        "alerts_sent": sent,
                        "alert_rules_fired": rules_sent,
                    })
                except Exception as exc:
                    logger.warning("alerts check failed: %s", exc)
                    update_job(job_id, extras_merge={"alerts_error": str(exc)})
        finally:
            db.close()

        update_job(job_id, status="done", step="done")
    except Exception as exc:
        logger.exception("pipeline failed")
        update_job(job_id, status="error", error=str(exc))


@router.post("/ingest")
def start_ingest(
    background: BackgroundTasks,
    player_limit: int = Query(default=20, ge=1, le=2000),
    matches_per_player: int = Query(default=20, ge=1, le=100),
    tiers: str = Query(
        default="challenger",
        description="Comma-separated tiers: challenger,grandmaster,master",
    ),
    regions: str = Query(
        default="",
        description="Comma-separated platforms: euw1,kr,na1,eun1,br1,jp1,oc1,la1,la2,tr1,ru. Empty = settings.platform.",
    ),
    auto_resolve_names: bool = Query(default=True),
    auto_alerts: bool = Query(default=True),
):
    """
    Pull players + their match history.

    `tiers` selects which league(s) (challenger/grandmaster/master).
    `regions` selects platform codes (euw1, kr, na1, ...). Empty = the
    server-configured default (settings.platform). Multi-region runs
    are sequential (Riot's rate limit is per-key).

    `player_limit` is applied PER TIER PER REGION — e.g. player_limit=200,
    tiers=[chall,gm], regions=[euw1,kr] = up to 200×2×2 = 800 players.
    """
    from ..services.riot_client import PLATFORM_TO_REGION
    tier_list = [t.strip().lower() for t in tiers.split(",") if t.strip()]
    valid_tiers = {"challenger", "grandmaster", "master"}
    invalid_t = [t for t in tier_list if t not in valid_tiers]
    if invalid_t:
        from fastapi import HTTPException
        raise HTTPException(400, f"unknown tier(s): {invalid_t}; valid: challenger, grandmaster, master")

    region_list = [r.strip().lower() for r in regions.split(",") if r.strip()]
    if region_list:
        invalid_r = [r for r in region_list if r not in PLATFORM_TO_REGION]
        if invalid_r:
            from fastapi import HTTPException
            raise HTTPException(400, f"unknown region(s): {invalid_r}; valid: {sorted(PLATFORM_TO_REGION.keys())}")
    else:
        region_list = None  # ingestion will fall back to settings.platform

    job_id = next_job_id("job")
    create_job(job_id, "ingest", params={
        "player_limit": player_limit,
        "matches_per_player": matches_per_player,
        "tiers": tier_list,
        "regions": region_list,
    })
    background.add_task(
        _run_pipeline_job, job_id, player_limit, matches_per_player,
        auto_resolve_names, 500, auto_alerts, tier_list, region_list,
    )
    return {"job_id": job_id, "status": "started", "tiers": tier_list, "regions": region_list}


@router.get("/jobs/{job_id}")
def job_status(job_id: str):
    return get_job(job_id)


@router.get("/jobs")
def jobs_history(limit: int = Query(default=30, ge=1, le=200)):
    """Recent jobs across all kinds (ingest, tournaments, lolpros, ...)."""
    return {"jobs": list_jobs(limit=limit)}


# In-process lock so two simultaneous /admin/recompute clicks don't both
# try to rebuild aggregates and deadlock SQLite. The first request wins
# the lock and runs in the background; the second gets a 409 with the
# already-running job_id.
_recompute_lock = False
_recompute_current_job: str | None = None


def _run_recompute_job(job_id: str, min_games: int):
    global _recompute_lock, _recompute_current_job
    update_job(job_id, status="running", step="lobby_lp")
    db = SessionLocal()
    try:
        n_lobby = compute_lobby_lp(db)
        update_job(job_id, step="aggregate", extras_merge={"matches_lobby_lp_updated": n_lobby})
        n_aggs = aggregate_all_players(db)
        update_job(job_id, step="role_distributions", extras_merge={"aggregated_players": n_aggs})
        compute_role_distributions(db, min_games=max(1, min_games))
        update_job(job_id, step="champion_distributions")
        compute_champion_distributions(db)
        update_job(job_id, step="smurf_scoring")
        n_smurf = score_all_smurfs(db)
        update_job(job_id, step="scoring", extras_merge={"smurf_suspect": n_smurf})
        n_scored = score_all(db, min_games=max(1, min_games))
        update_job(job_id, step="champion_scoring", extras_merge={"scored_aggregates": n_scored})
        n_champ = score_all_champions(db)
        update_job(job_id, status="done", step="done", extras_merge={"champion_baselines": n_champ})
    except Exception as exc:
        logger.exception("recompute failed")
        update_job(job_id, status="error", error=str(exc))
    finally:
        db.close()
        _recompute_lock = False
        _recompute_current_job = None


@router.post("/recompute")
def recompute(
    background: BackgroundTasks,
    min_games: int = Query(default=None),
):
    """Recompute aggregates, distributions, smurf scores, and CSS.

    Runs as a background job (was synchronous; took 5–10 min and timed out
    HTTP clients, plus stacked recomputes deadlocked SQLite). Use the
    returned job_id with /admin/jobs/<id> to poll.

    Returns 409 if another recompute is already running.
    """
    global _recompute_lock, _recompute_current_job
    if _recompute_lock:
        from fastapi import HTTPException
        raise HTTPException(409, f"recompute already running as {_recompute_current_job}")

    min_games = min_games if min_games is not None else settings.min_games
    job_id = next_job_id("rc")
    create_job(job_id, "recompute", params={"min_games": min_games})
    _recompute_lock = True
    _recompute_current_job = job_id
    background.add_task(_run_recompute_job, job_id, min_games)
    return {"job_id": job_id, "status": "started"}


@router.post("/smurf/retrain")
def smurf_retrain(db: Session = Depends(get_db)):
    """
    Retrain the smurf-detection logistic regression on the current DB state
    and re-score every player. Returns the learned weights + suspect count.
    """
    from ..services.smurf_ml import train_and_score_all
    return train_and_score_all(db)


@router.post("/alerts/test")
def alerts_test():
    """Send a test ping to all configured webhooks (Discord + Slack)."""
    from ..services.alerts import send_test_alert
    n = send_test_alert()
    return {"sent": n}


@router.post("/alerts/run")
def alerts_run(db: Session = Depends(get_db)):
    """Run the alerts engine right now without re-ingesting."""
    from ..services.alerts import run_alerts_check
    n = run_alerts_check(db)
    return {"alerts_sent": n}


@router.get("/stats")
def system_stats(db: Session = Depends(get_db)):
    from ..models import (
        CurrentLECRoster,
        Match, MatchParticipant, OfficialMatch, OfficialMatchParticipant,
        Player, PlayerAggregate, PlayerMeta, ProTeam, Tournament,
    )
    return {
        "soloq": {
            "players": db.query(Player).count(),
            "matches": db.query(Match).count(),
            "participations": db.query(MatchParticipant).count(),
            "aggregates": db.query(PlayerAggregate).count(),
        },
        "leaguepedia": {
            "matched_pros": db.query(PlayerMeta).filter(PlayerMeta.is_pro == True).count(),  # noqa
        },
        "tournaments": {
            "tournaments": db.query(Tournament).count(),
            "pro_teams": db.query(ProTeam).count(),
            "official_matches": db.query(OfficialMatch).count(),
            "official_participants": db.query(OfficialMatchParticipant).count(),
            "lec_roster": db.query(CurrentLECRoster).count(),
        },
    }


def _sync_leaguepedia_job(job_id: str, with_lolpros_bulk: bool = False):
    from .tournaments import invalidate_resolution_cache
    update_job(job_id, status="running", step="fetching")
    try:
        db = SessionLocal()
        try:
            stats = run_leaguepedia_sync_sync(db, with_lolpros_bulk=with_lolpros_bulk)
        finally:
            db.close()
        invalidate_resolution_cache()
        update_job(job_id, status="done", step="done", extras_merge={"stats": stats})
    except Exception as exc:
        logger.exception("leaguepedia sync failed")
        update_job(job_id, status="error", error=str(exc))


def _sync_tournaments_job(job_id: str, league_slugs: list[str], max_events: int):
    from .tournaments import invalidate_resolution_cache
    update_job(job_id, status="running", step="fetching")
    try:
        stats = run_tournament_sync_sync(league_slugs=league_slugs, max_events_per_league=max_events)
        invalidate_resolution_cache()
        update_job(job_id, status="done", step="done", extras_merge={"stats": stats})
    except Exception as exc:
        logger.exception("tournament sync failed")
        update_job(job_id, status="error", error=str(exc))


@router.get("/scheduler/status")
def scheduler_status():
    """Inspect the daily-ingest scheduler config + key count + next run.
    When rotation is enabled, also surfaces today's + tomorrow's tier."""
    from datetime import datetime, timedelta
    from ..services.ingestion import _resolve_keys
    from ..services.scheduler import _job_in_flight, _scheduler_task

    keys = _resolve_keys()
    enabled = settings.daily_ingest_enabled
    hour = settings.daily_ingest_hour
    minute = settings.daily_ingest_minute

    next_run = None
    if enabled:
        now = datetime.now()
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target <= now:
            target = target + timedelta(days=1)
        next_run = target.isoformat(timespec="seconds")

    all_tiers = [t.strip() for t in settings.daily_ingest_tiers.split(",") if t.strip()]
    rotation = None
    if settings.daily_ingest_rotate_tiers and len(all_tiers) > 1:
        today_doy = datetime.now().timetuple().tm_yday
        tomorrow_doy = (datetime.now() + timedelta(days=1)).timetuple().tm_yday
        rotation = {
            "enabled": True,
            "today_tier": all_tiers[today_doy % len(all_tiers)],
            "tomorrow_tier": all_tiers[tomorrow_doy % len(all_tiers)],
            "cycle_days": len(all_tiers),
        }
    else:
        rotation = {"enabled": False}

    return {
        "enabled": enabled,
        "running": bool(_scheduler_task and not _scheduler_task.done()),
        "in_flight": _job_in_flight,
        "trigger": f"{hour:02d}:{minute:02d} (server local)",
        "next_run_at": next_run,
        "keys_configured": len(keys),
        "regions": [r.strip() for r in settings.daily_ingest_regions.split(",") if r.strip()],
        "tiers": all_tiers,
        "rotation": rotation,
        "players_per_tier": settings.daily_ingest_players_per_tier,
        "games_per_player": settings.daily_ingest_games_per_player,
        "partition": settings.daily_ingest_partition,
    }


@router.post("/scheduler/trigger-now")
async def scheduler_trigger_now(background: BackgroundTasks):
    """Run the daily ingest right now (instead of waiting for 4am).
    Returns immediately — the job runs as a background task."""
    from ..services.scheduler import trigger_now, _job_in_flight
    if _job_in_flight:
        raise HTTPException(409, "another daily ingest run is already in flight")
    background.add_task(trigger_now)
    return {"ok": True, "message": "daily ingest started in background — see /admin/jobs"}


@router.post("/sync-tournaments")
def sync_tournaments(
    background: BackgroundTasks,
    leagues: str = Query(default="", description="Comma-separated slugs; empty = defaults"),
    max_events: int = Query(default=200, description="Max events per league"),
):
    slugs = [s.strip() for s in leagues.split(",") if s.strip()] or list(DEFAULT_LEAGUE_SLUGS)
    job_id = next_job_id("tn")
    create_job(job_id, "tournaments", params={"leagues": slugs, "max_events": max_events})
    background.add_task(_sync_tournaments_job, job_id, slugs, max_events)
    return {"job_id": job_id, "status": "started", "leagues": slugs}


def _sync_lolpros_job(job_id: str, server: str):
    from .tournaments import invalidate_resolution_cache
    update_job(job_id, status="running", step="fetching")
    try:
        db = SessionLocal()
        try:
            stats = run_lolpros_sync_sync(db, server=server)
        finally:
            db.close()
        invalidate_resolution_cache()
        update_job(job_id, status="done", step="done", extras_merge={"stats": stats})
    except Exception as exc:
        logger.exception("lolpros sync failed")
        update_job(job_id, status="error", error=str(exc))


@router.post("/sync-lolpros")
def sync_lolpros(background: BackgroundTasks, server: str = Query(default="EUW")):
    """Pull pro player metadata from lolpros.gg (preferred over Leaguepedia)."""
    job_id = next_job_id("lp")
    create_job(job_id, "lolpros", params={"server": server})
    background.add_task(_sync_lolpros_job, job_id, server)
    return {"job_id": job_id, "status": "started", "server": server}


@router.post("/sync-leaguepedia")
def sync_leaguepedia(background: BackgroundTasks):
    """Quick sync (~75 s): wikitext infobox + Cargo backfill + bulk EMEA Cargo.
    Skips the slow per-pro Lolpros profile crawl. Use this for routine
    refresh after a SoloQ ingest. For the full deep enrichment, use
    /admin/sync-leaguepedia-full."""
    job_id = next_job_id("lp")
    create_job(job_id, "leaguepedia", params={"with_lolpros_bulk": False})
    background.add_task(_sync_leaguepedia_job, job_id, False)
    return {"job_id": job_id, "status": "started"}


@router.post("/sync-leaguepedia-full")
def sync_leaguepedia_full(background: BackgroundTasks):
    """Full sync (~6 min): everything in /sync-leaguepedia + a bulk crawl
    of every active EMEA pro's Lolpros profile (~5000 fetches at
    concurrency 8). This unlocks perfect puuid-based pro matching and
    pulls Lolpros team / slug / accounts / social. Run this once a week."""
    job_id = next_job_id("lpf")
    create_job(job_id, "leaguepedia_full", params={"with_lolpros_bulk": True})
    background.add_task(_sync_leaguepedia_job, job_id, True)
    return {"job_id": job_id, "status": "started"}


async def _resolve_unknown_names_async(max_resolve: int, progress_cb=None) -> dict:
    """Reusable: call Riot account-v1 on every stub player and persist real Riot IDs.

    `progress_cb(attempted, resolved)` is called every 25 lookups so the
    caller can update its job dict.
    """
    from ..models import Player as _Player
    from ..services.riot_client import RiotClient

    db = SessionLocal()
    resolved = 0
    attempted = 0
    try:
        stubs = (
            db.query(_Player)
            .filter(
                (_Player.summoner_name == "(unknown)")
                | (_Player.summoner_name == "")
                | (~_Player.summoner_name.like("%#%"))
            )
            .limit(max_resolve)
            .all()
        )
        if progress_cb:
            progress_cb(0, 0, total=len(stubs))
        async with RiotClient() as client:
            for p in stubs:
                attempted += 1
                try:
                    acct = await client.account_by_puuid(p.puuid)
                    if acct and acct.get("gameName"):
                        tl = acct.get("tagLine") or ""
                        p.summoner_name = f"{acct['gameName']}#{tl}" if tl else acct["gameName"]
                        resolved += 1
                except Exception as exc:
                    logger.warning("resolve %s failed: %s", p.puuid[:8], exc)
                if attempted % 25 == 0:
                    db.commit()
                    if progress_cb:
                        progress_cb(attempted, resolved)
            db.commit()
    finally:
        db.close()
    return {"attempted": attempted, "resolved": resolved}


def _resolve_unknown_names_job(job_id: str, max_resolve: int):
    """Standalone background job — used by /admin/resolve-names."""
    import asyncio

    def _on_progress(attempted, resolved, total=None):
        progress: dict = {"attempted": attempted, "resolved": resolved}
        if total is not None:
            progress["total"] = total
        update_job(job_id, progress=progress)

    update_job(job_id, status="running", step="resolving")
    try:
        stats = asyncio.run(_resolve_unknown_names_async(max_resolve, _on_progress))
        update_job(job_id, status="done", step="done", extras_merge={"stats": stats})
    except Exception as exc:
        logger.exception("resolve-names failed")
        update_job(job_id, status="error", error=str(exc))


@router.post("/resolve-names")
def resolve_unknown_names(background: BackgroundTasks, max_resolve: int = Query(default=200)):
    """Walk every '(unknown)' / stub player and resolve their Riot ID via account-v1."""
    job_id = next_job_id("rn")
    create_job(job_id, "resolve_names", params={"max_resolve": max_resolve})
    background.add_task(_resolve_unknown_names_job, job_id, max_resolve)
    return {"job_id": job_id, "status": "started", "max_resolve": max_resolve}


@router.post("/cleanup-demo")
def cleanup_demo(db: Session = Depends(get_db)):
    """Delete all synthetic demo data (puuids prefixed with 'demo-' and matches DEMO_*)."""
    from ..models import (
        ChampionPool, Match, MatchParticipant, Player, PlayerAggregate, RankSnapshot,
    )
    n_parts = db.query(MatchParticipant).filter(MatchParticipant.match_id.like("DEMO_%")).delete(synchronize_session=False)
    n_matches = db.query(Match).filter(Match.match_id.like("DEMO_%")).delete(synchronize_session=False)
    n_aggs = db.query(PlayerAggregate).filter(PlayerAggregate.puuid.like("demo-%")).delete(synchronize_session=False)
    n_pool = db.query(ChampionPool).filter(ChampionPool.puuid.like("demo-%")).delete(synchronize_session=False)
    n_ranks = db.query(RankSnapshot).filter(RankSnapshot.puuid.like("demo-%")).delete(synchronize_session=False)
    n_players = db.query(Player).filter(Player.puuid.like("demo-%")).delete(synchronize_session=False)
    db.commit()
    return {
        "deleted": {
            "players": n_players, "rank_snapshots": n_ranks,
            "matches": n_matches, "participations": n_parts,
            "aggregates": n_aggs, "champion_pool": n_pool,
        }
    }


# ============================================================
# /admin/add-player — manually register a player by Riot ID
# ============================================================

@router.post("/add-player")
def add_player(
    background: BackgroundTasks,
    riot_id: str = Query(..., description='Riot ID like "Caps#EUW"'),
    platform: str = Query(default="euw1", description="euw1 / kr / na1 / eun1 / br1 / jp1 / oc1 / la1 / la2 / tr1 / ru"),
    match_count: int = Query(default=30, ge=1, le=100, description="How many recent ranked matches to ingest"),
    auto_watch: bool = Query(default=True, description="Add to current user's watchlist after ingest"),
):
    """Manually register a player by Riot ID — any region, any rank.

    Designed for tracking specific players the analyst already knows
    (former pros, French scene amateurs, friends-of-staff, etc.) without
    waiting for them to surface in a Challenger ladder ingest.

    Pipeline:
      1. account-v1 by-riot-id   → puuid
      2. summoner-v4             → account_level + summoner_id
      3. league-v4 by-puuid      → current tier + LP (any tier, even Iron-Diamond)
      4. match-v5                → ingest last N ranked SoloQ games + timelines
      5. aggregate + CSS for this single puuid
      6. (optional) add to first-admin's watchlist
    """
    from fastapi import HTTPException
    if "#" not in riot_id:
        raise HTTPException(400, "riot_id must contain '#' (gameName#tagLine)")
    game_name, tag_line = riot_id.split("#", 1)
    game_name, tag_line = game_name.strip(), tag_line.strip()
    if not (game_name and tag_line):
        raise HTTPException(400, "empty gameName or tagLine")

    job_id = next_job_id("add")
    create_job(job_id, "add_player", params={
        "riot_id": riot_id, "platform": platform,
        "match_count": match_count, "auto_watch": auto_watch,
    })
    background.add_task(_run_add_player_job, job_id, game_name, tag_line,
                        platform, match_count, auto_watch)
    return {"job_id": job_id, "status": "started", "riot_id": riot_id, "platform": platform}


def _run_add_player_job(job_id: str, game_name: str, tag_line: str,
                        platform: str, match_count: int, auto_watch: bool):
    """Background worker for add-player."""
    from datetime import datetime, timezone
    from ..models import Player, RankSnapshot, User, WatchlistEntry, PlayerAggregate
    from ..services.aggregation import aggregate_player
    from ..services.ingestion import _resolve_keys, ingest_player_matches
    from ..services.riot_client import PLATFORM_TO_REGION, RateLimiter, RiotClient
    from ..services.scoring import compute_css_for_aggregate

    update_job(job_id, status="running", step="resolve_riot_id")

    super_region = PLATFORM_TO_REGION.get(platform.lower())
    if not super_region:
        update_job(job_id, status="error", error=f"unknown platform {platform!r}")
        return

    # Pick the first configured key. _resolve_keys() returns RIOT_API_KEYS
    # (multi-key) when set, falling back to RIOT_API_KEY (single). Without
    # this, the job inherits whatever ENV is in `settings.riot_api_key` —
    # which may be an expired Personal Key, while the multi-key list has
    # fresh ones.
    keys = _resolve_keys()
    api_key = keys[0] if keys else None
    if not api_key:
        update_job(job_id, status="error", error="no Riot API key configured")
        return

    async def _ingest():
        limiter = RateLimiter()
        async with RiotClient(api_key=api_key, platform=platform.lower(),
                              region=super_region, limiter=limiter) as client:
            try:
                acct = await client.account_by_riot_id(game_name, tag_line)
            except Exception as exc:
                raise RuntimeError(f"Riot ID lookup failed: {exc}") from exc
            if not acct or not acct.get("puuid"):
                raise RuntimeError(f"Riot ID {game_name}#{tag_line} not found on {platform}")
            puuid = acct["puuid"]

            update_job(job_id, step="summoner_lookup",
                       extras_merge={"puuid": puuid,
                                     "game_name": acct.get("gameName"),
                                     "tag_line": acct.get("tagLine")})

            try:
                summ = await client.summoner_by_puuid(puuid)
            except Exception as exc:
                logger.warning("summoner lookup failed for %s: %s", puuid[:12], exc)
                summ = {}

            update_job(job_id, step="rank_lookup")
            try:
                entries = await client.league_entries_by_puuid(puuid)
            except Exception as exc:
                logger.warning("league entries lookup failed for %s: %s", puuid[:12], exc)
                entries = []
            soloq_entry = next(
                (e for e in entries if e.get("queueType") == "RANKED_SOLO_5x5"),
                None,
            )

            db = SessionLocal()
            try:
                now = datetime.now(timezone.utc)
                p = db.get(Player, puuid)
                if not p:
                    p = Player(puuid=puuid)
                    db.add(p)
                p.summoner_id = (summ.get("id") if summ else None) or p.summoner_id
                p.summoner_name = f"{acct.get('gameName')}#{acct.get('tagLine')}"
                p.region = platform.lower()
                p.account_level = (summ.get("summonerLevel", 0)
                                    if summ else (p.account_level or 0))
                p.last_updated = now

                if soloq_entry:
                    db.add(RankSnapshot(
                        puuid=puuid,
                        tier=(soloq_entry.get("tier") or "").upper() or None,
                        rank=soloq_entry.get("rank", "I"),
                        lp=soloq_entry.get("leaguePoints", 0),
                        wins=soloq_entry.get("wins", 0),
                        losses=soloq_entry.get("losses", 0),
                        snapshot_date=now,
                    ))
                db.commit()

                update_job(job_id, step=f"ingesting_matches_{match_count}")
                added = 0
                try:
                    added = await ingest_player_matches(client, db, puuid, count=match_count)
                except Exception as exc:
                    logger.exception("match ingest failed for %s: %s", puuid[:12], exc)

                update_job(job_id, step="aggregate")
                try:
                    aggregate_player(db, puuid)
                except Exception as exc:
                    logger.exception("aggregate failed for %s: %s", puuid[:12], exc)

                update_job(job_id, step="score")
                try:
                    for agg in db.query(PlayerAggregate).filter_by(puuid=puuid).all():
                        css, raw, _ = compute_css_for_aggregate(db, agg)
                        agg.css_score = css
                        agg.css_raw = raw
                    db.commit()
                except Exception as exc:
                    logger.exception("score failed for %s: %s", puuid[:12], exc)

                if auto_watch:
                    update_job(job_id, step="watchlist")
                    admin_user = db.query(User).filter(User.role == "admin").first()
                    if admin_user:
                        existing = db.query(WatchlistEntry).filter_by(
                            user_id=admin_user.id, puuid=puuid).first()
                        if not existing:
                            db.add(WatchlistEntry(
                                user_id=admin_user.id, puuid=puuid,
                                tag="manually-added",
                                added_at=now,
                                stage="watch",
                                stage_changed_at=now,
                            ))
                            db.commit()

                stats = {
                    "puuid": puuid,
                    "riot_id": f"{acct.get('gameName')}#{acct.get('tagLine')}",
                    "region": platform.lower(),
                    "tier": (soloq_entry.get("tier") if soloq_entry else None),
                    "rank": (soloq_entry.get("rank") if soloq_entry else None),
                    "lp": (soloq_entry.get("leaguePoints") if soloq_entry else None),
                    "wins": (soloq_entry.get("wins") if soloq_entry else None),
                    "losses": (soloq_entry.get("losses") if soloq_entry else None),
                    "account_level": p.account_level,
                    "matches_added": added,
                }
                update_job(job_id, status="done", step="done",
                           extras_merge={"stats": stats})
                logger.info("add-player done: %s", stats)
            finally:
                db.close()

    try:
        asyncio.run(_ingest())
    except Exception as exc:
        logger.exception("add-player failed: %s", exc)
        update_job(job_id, status="error", error=str(exc))
