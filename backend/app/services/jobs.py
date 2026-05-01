"""DB-backed job state helpers.

The admin pipeline previously kept job state in a process-local dict
(`_jobs`), which was wiped on every uvicorn reload — meaning a 90-min
ingest could survive but the `/admin/jobs/<id>` endpoint would lie about
its status. We now persist every state transition in `ingest_jobs`.

Each helper opens its own short-lived `SessionLocal` so background
threads never share a session with the request handler.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from ..db import SessionLocal
from ..models import IngestJob

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _load(s, job_id: str) -> IngestJob | None:
    return s.get(IngestJob, job_id)


def create_job(job_id: str, kind: str, params: dict | None = None) -> None:
    """Insert a fresh `queued` row. Idempotent on `id` collision."""
    s = SessionLocal()
    try:
        existing = _load(s, job_id)
        now = _now()
        if existing:
            existing.kind = kind
            existing.status = "queued"
            existing.step = "queued"
            existing.params_json = json.dumps(params or {})
            existing.progress_json = None
            existing.extras_json = None
            existing.error = None
            existing.updated_at = now
        else:
            s.add(
                IngestJob(
                    id=job_id,
                    kind=kind,
                    status="queued",
                    step="queued",
                    params_json=json.dumps(params or {}),
                    created_at=now,
                    updated_at=now,
                )
            )
        s.commit()
    finally:
        s.close()


def update_job(
    job_id: str,
    *,
    status: str | None = None,
    step: str | None = None,
    progress: dict | None = None,
    error: str | None = None,
    extras_merge: dict | None = None,
) -> None:
    """Patch one or more fields. `extras_merge` is shallow-merged into the
    extras JSON so callers can drop arbitrary stats (alerts_sent,
    resolve_names, smurf_ml, …) without colliding."""
    s = SessionLocal()
    try:
        job = _load(s, job_id)
        if not job:
            logger.warning("update_job called for missing %s — creating row", job_id)
            job = IngestJob(id=job_id, kind="unknown", created_at=_now())
            s.add(job)
        if status is not None:
            job.status = status
        if step is not None:
            job.step = step
        if progress is not None:
            job.progress_json = json.dumps(progress)
        if error is not None:
            job.error = error
        if extras_merge:
            current = {}
            if job.extras_json:
                try:
                    current = json.loads(job.extras_json) or {}
                except Exception:
                    current = {}
            current.update(extras_merge)
            job.extras_json = json.dumps(current)
        job.updated_at = _now()
        s.commit()
    finally:
        s.close()


def get_job(job_id: str) -> dict:
    """Return a dict shaped like the legacy in-memory format for the
    `/admin/jobs/<id>` endpoint."""
    s = SessionLocal()
    try:
        job = _load(s, job_id)
        if not job:
            return {"status": "unknown"}
        out: dict[str, Any] = {
            "status": job.status,
            "step": job.step,
            "kind": job.kind,
            "created_at": job.created_at.isoformat() if job.created_at else None,
            "updated_at": job.updated_at.isoformat() if job.updated_at else None,
        }
        if job.params_json:
            try:
                out["params"] = json.loads(job.params_json)
            except Exception:
                pass
        if job.progress_json:
            try:
                out["progress"] = json.loads(job.progress_json)
            except Exception:
                pass
        if job.extras_json:
            try:
                # Surface extras at the top level for backward-compat with
                # the old endpoint shape (alerts_sent, resolve_names, ...)
                extras = json.loads(job.extras_json) or {}
                out.update(extras)
            except Exception:
                pass
        if job.error:
            out["error"] = job.error
        return out
    finally:
        s.close()


def list_jobs(limit: int = 30) -> list[dict]:
    """Recent jobs, newest first — for the admin history panel."""
    s = SessionLocal()
    try:
        rows = (
            s.query(IngestJob)
            .order_by(IngestJob.updated_at.desc().nullslast(), IngestJob.created_at.desc())
            .limit(limit)
            .all()
        )
        return [get_job_row(r) for r in rows]
    finally:
        s.close()


def get_job_row(job: IngestJob) -> dict:
    out = {
        "id": job.id,
        "kind": job.kind,
        "status": job.status,
        "step": job.step,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "updated_at": job.updated_at.isoformat() if job.updated_at else None,
    }
    if job.error:
        out["error"] = job.error
    return out


def next_job_id(prefix: str) -> str:
    """Generate `prefix-N` where N is the next free integer for that prefix."""
    s = SessionLocal()
    try:
        existing = (
            s.query(IngestJob.id)
            .filter(IngestJob.id.like(f"{prefix}-%"))
            .all()
        )
        max_n = 0
        for (jid,) in existing:
            try:
                n = int(jid.split("-", 1)[1])
                max_n = max(max_n, n)
            except (ValueError, IndexError):
                continue
        return f"{prefix}-{max_n + 1}"
    finally:
        s.close()
