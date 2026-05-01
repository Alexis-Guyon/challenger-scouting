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
from ..services.leaguepedia import run_leaguepedia_sync_sync
from ..services.lolpros import run_lolpros_sync_sync
from ..services.scoring import score_all, score_all_champions, score_all_smurfs
from ..services.tournament_ingestion import DEFAULT_LEAGUE_SLUGS, run_tournament_sync_sync

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])
logger = logging.getLogger(__name__)


_jobs: dict[str, dict] = {}


def _run_pipeline_job(
    job_id: str,
    player_limit: int,
    matches_per_player: int,
    auto_resolve_names: bool = True,
    auto_resolve_max: int = 500,
    send_alerts: bool = True,
):
    _jobs[job_id] = {"status": "running", "step": "ingest"}

    def _on_player_done(idx, total, summoner_name, new_matches):
        _jobs[job_id]["progress"] = {
            "phase": "ingest",
            "player_idx": idx, "player_total": total,
            "current_player": summoner_name,
            "new_matches_last": new_matches,
        }

    try:
        asyncio.run(run_ingestion(
            player_limit=player_limit,
            matches_per_player=matches_per_player,
            progress_cb=_on_player_done,
        ))

        # Auto-resolve stub players that just got auto-imported as opponents.
        if auto_resolve_names:
            _jobs[job_id]["step"] = "resolve_names"
            try:
                rn_stats = asyncio.run(_resolve_unknown_names_async(auto_resolve_max))
                _jobs[job_id]["resolve_names"] = rn_stats
            except Exception as exc:
                logger.warning("auto-resolve names failed: %s", exc)
                _jobs[job_id]["resolve_names_error"] = str(exc)

        _jobs[job_id]["step"] = "aggregate"
        db = SessionLocal()
        try:
            compute_lobby_lp(db)
            aggregate_all_players(db)
            _jobs[job_id]["step"] = "distributions"
            compute_role_distributions(db, min_games=1)
            compute_champion_distributions(db)
            _jobs[job_id]["step"] = "smurf_scoring"
            try:
                from ..services.smurf_ml import train_and_score_all
                ml_stats = train_and_score_all(db)
                _jobs[job_id]["smurf_ml"] = ml_stats
            except Exception as exc:
                logger.warning("smurf_ml failed (%s) — falling back to heuristic", exc)
                score_all_smurfs(db)
            _jobs[job_id]["step"] = "scoring"
            score_all(db, min_games=1)
            _jobs[job_id]["step"] = "champion_scoring"
            score_all_champions(db)

            # Run alerts engine: detect deltas vs previous snapshot
            if send_alerts:
                _jobs[job_id]["step"] = "alerts"
                try:
                    from ..services.alerts import run_alerts_check
                    sent = run_alerts_check(db)
                    _jobs[job_id]["alerts_sent"] = sent
                except Exception as exc:
                    logger.warning("alerts check failed: %s", exc)
                    _jobs[job_id]["alerts_error"] = str(exc)
        finally:
            db.close()

        _jobs[job_id]["status"] = "done"
        _jobs[job_id]["step"] = "done"
    except Exception as exc:
        logger.exception("pipeline failed")
        _jobs[job_id] = {"status": "error", "error": str(exc)}


@router.post("/ingest")
def start_ingest(
    background: BackgroundTasks,
    player_limit: int = Query(default=20, ge=1, le=400),
    matches_per_player: int = Query(default=20, ge=1, le=100),
    auto_resolve_names: bool = Query(default=True, description="Run /resolve-names automatically after ingest"),
    auto_alerts: bool = Query(default=True, description="Send Discord/Slack alerts after ingest"),
):
    """
    Pull Challenger players + their match history. Now safer for large batches:
    - player_limit up to 400 (the full Challenger ladder is ~300, GM ~700)
    - matches_per_player up to 100
    - resume: ingest_player_matches already skips matches that are already in DB,
      so re-running an interrupted job naturally picks up where it stopped.
    """
    job_id = f"job-{len(_jobs)+1}"
    _jobs[job_id] = {
        "status": "queued",
        "step": "queued",
        "params": {"player_limit": player_limit, "matches_per_player": matches_per_player},
    }
    background.add_task(
        _run_pipeline_job, job_id, player_limit, matches_per_player,
        auto_resolve_names, 500, auto_alerts,
    )
    return {"job_id": job_id, "status": "started"}


@router.get("/jobs/{job_id}")
def job_status(job_id: str):
    return _jobs.get(job_id, {"status": "unknown"})


@router.post("/recompute")
def recompute(min_games: int = Query(default=None), db: Session = Depends(get_db)):
    """Recompute aggregates, distributions, smurf scores, and CSS (role + champion)."""
    min_games = min_games if min_games is not None else settings.min_games
    n_lobby = compute_lobby_lp(db)
    n_aggs = aggregate_all_players(db)
    compute_role_distributions(db, min_games=max(1, min_games))
    compute_champion_distributions(db)  # uses Pass A (player-level) + Pass B (match-level fallback)
    n_smurf = score_all_smurfs(db)              # must run BEFORE score_all (used in factor)
    n_scored = score_all(db, min_games=max(1, min_games))
    n_champ = score_all_champions(db)
    return {
        "matches_lobby_lp_updated": n_lobby,
        "aggregated_players": n_aggs,
        "scored_aggregates": n_scored,
        "smurf_suspect": n_smurf,
        "champion_baselines": n_champ,
    }


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


def _sync_leaguepedia_job(job_id: str):
    _jobs[job_id] = {"status": "running", "step": "fetching"}
    try:
        db = SessionLocal()
        try:
            stats = run_leaguepedia_sync_sync(db)
        finally:
            db.close()
        _jobs[job_id] = {"status": "done", "step": "done", "stats": stats}
    except Exception as exc:
        logger.exception("leaguepedia sync failed")
        _jobs[job_id] = {"status": "error", "error": str(exc)}


def _sync_tournaments_job(job_id: str, league_slugs: list[str], max_events: int):
    _jobs[job_id] = {"status": "running", "step": "fetching"}
    try:
        stats = run_tournament_sync_sync(league_slugs=league_slugs, max_events_per_league=max_events)
        _jobs[job_id] = {"status": "done", "step": "done", "stats": stats}
    except Exception as exc:
        logger.exception("tournament sync failed")
        _jobs[job_id] = {"status": "error", "error": str(exc)}


@router.post("/sync-tournaments")
def sync_tournaments(
    background: BackgroundTasks,
    leagues: str = Query(default="", description="Comma-separated slugs; empty = defaults"),
    max_events: int = Query(default=200, description="Max events per league"),
):
    slugs = [s.strip() for s in leagues.split(",") if s.strip()] or list(DEFAULT_LEAGUE_SLUGS)
    job_id = f"tn-{len(_jobs)+1}"
    _jobs[job_id] = {"status": "queued", "step": "queued"}
    background.add_task(_sync_tournaments_job, job_id, slugs, max_events)
    return {"job_id": job_id, "status": "started", "leagues": slugs}


def _sync_lolpros_job(job_id: str, server: str):
    _jobs[job_id] = {"status": "running", "step": "fetching"}
    try:
        db = SessionLocal()
        try:
            stats = run_lolpros_sync_sync(db, server=server)
        finally:
            db.close()
        _jobs[job_id] = {"status": "done", "step": "done", "stats": stats}
    except Exception as exc:
        logger.exception("lolpros sync failed")
        _jobs[job_id] = {"status": "error", "error": str(exc)}


@router.post("/sync-lolpros")
def sync_lolpros(background: BackgroundTasks, server: str = Query(default="EUW")):
    """Pull pro player metadata from lolpros.gg (preferred over Leaguepedia)."""
    job_id = f"lp-{len(_jobs)+1}"
    _jobs[job_id] = {"status": "queued", "step": "queued"}
    background.add_task(_sync_lolpros_job, job_id, server)
    return {"job_id": job_id, "status": "started", "server": server}


@router.post("/sync-leaguepedia")
def sync_leaguepedia(background: BackgroundTasks):
    job_id = f"lp-{len(_jobs)+1}"
    _jobs[job_id] = {"status": "queued", "step": "queued"}
    background.add_task(_sync_leaguepedia_job, job_id)
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
        update = {"progress": {"attempted": attempted, "resolved": resolved}}
        if total is not None:
            update["total"] = total
        _jobs[job_id].update(update)

    _jobs[job_id] = {"status": "running", "step": "resolving"}
    try:
        stats = asyncio.run(_resolve_unknown_names_async(max_resolve, _on_progress))
        _jobs[job_id] = {"status": "done", "step": "done", "stats": stats}
    except Exception as exc:
        logger.exception("resolve-names failed")
        _jobs[job_id] = {"status": "error", "error": str(exc)}


@router.post("/resolve-names")
def resolve_unknown_names(background: BackgroundTasks, max_resolve: int = Query(default=200)):
    """Walk every '(unknown)' / stub player and resolve their Riot ID via account-v1."""
    job_id = f"rn-{len(_jobs)+1}"
    _jobs[job_id] = {"status": "queued", "step": "queued"}
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
