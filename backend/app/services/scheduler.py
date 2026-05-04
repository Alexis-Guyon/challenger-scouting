"""
Daily 4am ladder ingest scheduler.

Self-contained asyncio loop — no apscheduler / cron / systemd timer needed.
The loop sleeps until the next configured trigger (HH:MM server local time),
fires the multi-key ingest job, then sleeps again.

Activated by setting `DAILY_INGEST_ENABLED=true` in .env. The scope
(regions, tiers, players-per-tier, games-per-player, key-partition strategy)
is fully driven by env vars — no code change to retune.

Job runs are logged in the same in-memory tracker as the manual /admin
ingest jobs, so progress shows up at GET /admin/jobs/{id} and in the
Admin tab.
"""
import asyncio
import logging
from datetime import datetime, timedelta

from ..config import settings
from .jobs import create_job, next_job_id, update_job

logger = logging.getLogger(__name__)

# Module-level singleton task — set on startup, read on shutdown.
_scheduler_task: asyncio.Task | None = None
# Soft lock: a job in flight when the next tick fires is skipped (no overlap).
_job_in_flight: bool = False


def _seconds_until_next_trigger(hour: int, minute: int) -> float:
    """Return seconds from now until the next HH:MM (server local time).
    If the trigger time has already passed today, schedule for tomorrow."""
    now = datetime.now()
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target = target + timedelta(days=1)
    return (target - now).total_seconds()


async def _run_one_pass() -> None:
    """Execute one daily ingest pass. Logs into the job tracker."""
    global _job_in_flight
    if _job_in_flight:
        logger.warning("daily ingest skipped — previous run still in flight")
        return

    from .ingestion import _resolve_keys, run_multi_key_ingestion

    keys = _resolve_keys()
    tiers = [t.strip() for t in settings.daily_ingest_tiers.split(",") if t.strip()]
    regions = [r.strip() for r in settings.daily_ingest_regions.split(",") if r.strip()]
    if not (tiers and regions and keys):
        logger.error("daily ingest config invalid (tiers=%s regions=%s keys=%d)",
                     tiers, regions, len(keys))
        return

    job_id = next_job_id("daily")
    create_job(job_id, "daily_ingest", params={
        "tiers": tiers, "regions": regions,
        "keys_count": len(keys),
        "partition": settings.daily_ingest_partition,
        "players_per_tier": settings.daily_ingest_players_per_tier,
        "games_per_player": settings.daily_ingest_games_per_player,
        "started_at": datetime.now().isoformat(timespec="seconds"),
    })

    _job_in_flight = True
    started = datetime.now()
    update_job(job_id, status="running", step="ingesting")
    try:
        # Per-(tier×region) progress callback → live update on /admin/jobs
        def _progress(tier, region, i, n, name, matches_added):
            pct = round(i / max(n, 1) * 100, 1)
            update_job(job_id, step=f"{tier.upper()}/{region.upper()} {i}/{n} ({pct}%) — {name} +{matches_added}")

        summary = await run_multi_key_ingestion(
            tiers=tiers,
            regions=regions,
            keys=keys,
            partition=settings.daily_ingest_partition,
            player_limit=settings.daily_ingest_players_per_tier,
            matches_per_player=settings.daily_ingest_games_per_player,
            progress_cb=_progress,
        )
        elapsed = (datetime.now() - started).total_seconds()
        summary["elapsed_sec"] = round(elapsed, 1)
        update_job(job_id, status="done", step="done", extras_merge={"stats": summary})
        logger.info("daily ingest finished in %.0fs: %s", elapsed, summary)

        # Run aggregation + scoring after the ingest so the new matches
        # show up in the leaderboard. Reuse the existing recompute pipeline
        # (same one the /admin/recompute endpoint triggers). Spawned as a
        # separate job so its progress is independently visible.
        try:
            update_job(job_id, step="spawning_recompute")
            from ..routers.admin import _run_recompute_job
            recompute_job_id = next_job_id("rc")
            create_job(recompute_job_id, "post_ingest_recompute",
                       params={"trigger": "daily_ingest", "parent_job": job_id})
            await asyncio.to_thread(_run_recompute_job, recompute_job_id,
                                    settings.min_games)
            update_job(job_id, step="done",
                       extras_merge={"recompute_job_id": recompute_job_id})
            logger.info("daily ingest: post-recompute done (%s)", recompute_job_id)
        except Exception as exc:
            logger.exception("daily ingest: post-recompute failed: %s", exc)
            update_job(job_id, step="recompute_failed", error=str(exc))
    except Exception as exc:
        logger.exception("daily ingest crashed: %s", exc)
        update_job(job_id, status="error", error=str(exc))
    finally:
        _job_in_flight = False


async def _scheduler_loop() -> None:
    """Sleep → fire → sleep loop. Runs forever until cancelled."""
    while True:
        try:
            wait = _seconds_until_next_trigger(
                settings.daily_ingest_hour, settings.daily_ingest_minute
            )
            wake = datetime.now() + timedelta(seconds=wait)
            logger.info("daily ingest scheduler: next run at %s (in %.0f min)",
                        wake.strftime("%Y-%m-%d %H:%M"), wait / 60)
            await asyncio.sleep(wait)
            await _run_one_pass()
        except asyncio.CancelledError:
            logger.info("daily ingest scheduler: cancelled")
            raise
        except Exception as exc:
            # Don't let an exception in the loop kill the scheduler.
            # Wait a minute and retry the loop.
            logger.exception("daily ingest scheduler: loop error: %s", exc)
            await asyncio.sleep(60)


def start_scheduler() -> None:
    """Launch the daily ingest loop on the running asyncio event loop.
    Idempotent — calling twice is a no-op."""
    global _scheduler_task
    if not settings.daily_ingest_enabled:
        logger.info("daily ingest disabled (DAILY_INGEST_ENABLED=false)")
        return
    if _scheduler_task and not _scheduler_task.done():
        return
    loop = asyncio.get_event_loop()
    _scheduler_task = loop.create_task(_scheduler_loop())
    logger.info("daily ingest scheduler started (trigger %02d:%02d local)",
                settings.daily_ingest_hour, settings.daily_ingest_minute)


def stop_scheduler() -> None:
    """Cancel the scheduler task (graceful shutdown)."""
    global _scheduler_task
    if _scheduler_task and not _scheduler_task.done():
        _scheduler_task.cancel()
        _scheduler_task = None


async def trigger_now() -> dict:
    """Manual trigger — runs the same pass the scheduler would. Useful
    for the Admin tab "Run daily ingest now" button without waiting for 4am."""
    if _job_in_flight:
        return {"ok": False, "error": "another run is already in flight"}
    await _run_one_pass()
    return {"ok": True}
