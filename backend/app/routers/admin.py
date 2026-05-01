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
from ..services.scoring import score_all, score_all_champions, score_all_smurfs
from ..services.tournament_ingestion import DEFAULT_LEAGUE_SLUGS, run_tournament_sync_sync

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])
logger = logging.getLogger(__name__)


_jobs: dict[str, dict] = {}


def _run_pipeline_job(job_id: str, player_limit: int, matches_per_player: int):
    _jobs[job_id] = {"status": "running", "step": "ingest"}
    try:
        asyncio.run(run_ingestion(player_limit=player_limit, matches_per_player=matches_per_player))

        _jobs[job_id]["step"] = "aggregate"
        db = SessionLocal()
        try:
            compute_lobby_lp(db)
            aggregate_all_players(db)
            _jobs[job_id]["step"] = "distributions"
            compute_role_distributions(db, min_games=1)
            compute_champion_distributions(db, min_games_per_champion=10)
            _jobs[job_id]["step"] = "smurf_scoring"
            score_all_smurfs(db)
            _jobs[job_id]["step"] = "scoring"
            score_all(db, min_games=1)
            _jobs[job_id]["step"] = "champion_scoring"
            score_all_champions(db)
        finally:
            db.close()

        _jobs[job_id] = {"status": "done", "step": "done"}
    except Exception as exc:
        logger.exception("pipeline failed")
        _jobs[job_id] = {"status": "error", "error": str(exc)}


@router.post("/ingest")
def start_ingest(
    background: BackgroundTasks,
    player_limit: int = Query(default=20),
    matches_per_player: int = Query(default=20),
):
    job_id = f"job-{len(_jobs)+1}"
    _jobs[job_id] = {"status": "queued", "step": "queued"}
    background.add_task(_run_pipeline_job, job_id, player_limit, matches_per_player)
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
    compute_champion_distributions(db, min_games_per_champion=10)
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


@router.post("/sync-leaguepedia")
def sync_leaguepedia(background: BackgroundTasks):
    job_id = f"lp-{len(_jobs)+1}"
    _jobs[job_id] = {"status": "queued", "step": "queued"}
    background.add_task(_sync_leaguepedia_job, job_id)
    return {"job_id": job_id, "status": "started"}


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
