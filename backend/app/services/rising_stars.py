"""
Rising Star detector.

Looks at the CSSSnapshot history per (puuid, role) and tags players whose
CSS has been monotonically increasing over the last N snapshots (default 3),
with a minimum total gain of `min_total_gain` points.

Used by:
- Leaderboard: highlights "rising star" badge on qualifying rows
- Alerts: complements the per-ingestion delta detection by capturing
  sustained uptrends rather than single-spike jumps
"""
import logging
from collections import defaultdict
from datetime import datetime, timezone

from sqlalchemy import desc
from sqlalchemy.orm import Session

from ..models import CSSSnapshot, Player, PlayerAggregate

logger = logging.getLogger(__name__)


def detect_rising_stars(
    db: Session,
    min_consecutive: int = 3,
    min_total_gain: float = 6.0,
    min_per_step_gain: float = 1.0,
    min_current_css: float = 50.0,
) -> list[dict]:
    """
    A "rising star" is a (puuid, role) whose CSS rose monotonically across the
    last `min_consecutive` distinct PATCHES.

    Mental model: we want patch-over-patch progression (true skill curve), not
    re-syncs of the same patch. So we:
      1. Bucket snapshots by (puuid, role, patch).
      2. Keep only the LATEST snapshot per patch (most recent re-sync wins).
      3. Sort those buckets chronologically (by snapshot_at of the kept row).
      4. Take the most recent `min_consecutive` patches and check the curve.

    Filters:
      - Each step ≥ `min_per_step_gain` (monotonic increase, no plateaus).
      - Total gain ≥ `min_total_gain`.
      - Current CSS ≥ `min_current_css` so we surface real targets, not players
        crawling from 20 to 30.
    """
    # All snapshots, oldest first so "latest per patch" wins via dict update
    rows = (
        db.query(CSSSnapshot)
        .order_by(CSSSnapshot.snapshot_at.asc())
        .all()
    )

    # (puuid, role, patch) -> last snapshot at that patch
    latest_per_patch: dict[tuple, CSSSnapshot] = {}
    for s in rows:
        if not s.role or not s.patch:
            continue
        latest_per_patch[(s.puuid, s.role, s.patch)] = s

    # Group by (puuid, role) for trend detection
    by_pr: dict[tuple, list[CSSSnapshot]] = defaultdict(list)
    for (puuid, role, _), s in latest_per_patch.items():
        by_pr[(puuid, role)].append(s)

    out: list[dict] = []

    for (puuid, role), patch_snaps in by_pr.items():
        # Sort by patch version — every snapshot of a given run has the same
        # snapshot_at, so timestamp sorting was a no-op. Patches like "16.7",
        # "16.8", "16.9", "16.10" need numeric-aware sorting.
        def _patch_key(snap):
            parts = (snap.patch or "0.0").split(".")
            try:
                return tuple(int(x) for x in parts)
            except ValueError:
                return (0, 0)
        patch_snaps.sort(key=_patch_key)
        if len(patch_snaps) < min_consecutive:
            continue

        # Take the LAST N patches (most recent at the end)
        chrono = patch_snaps[-min_consecutive:]
        css_seq = [s.css_score or 0 for s in chrono]

        if css_seq[-1] < min_current_css:
            continue

        steps = [css_seq[i + 1] - css_seq[i] for i in range(len(css_seq) - 1)]
        if not all(step >= min_per_step_gain for step in steps):
            continue

        total_gain = css_seq[-1] - css_seq[0]
        if total_gain < min_total_gain:
            continue

        out.append({
            "puuid": puuid,
            "role": role,
            "total_gain": round(total_gain, 1),
            "steps": [round(s, 1) for s in steps],
            "css_sequence": [round(c, 1) for c in css_seq],
            "patches": [s.patch for s in chrono],
            "current_css": css_seq[-1],
        })

    out.sort(key=lambda x: x["total_gain"], reverse=True)
    logger.info(
        "rising stars: %d (min_consecutive=%d, min_total_gain=%.1f, "
        "min_current_css=%.1f). Eligible (puuid,role) pairs: %d",
        len(out), min_consecutive, min_total_gain, min_current_css,
        sum(1 for v in by_pr.values() if len(v) >= min_consecutive),
    )
    return out


def annotate_rising_stars_in_aggregates(db: Session, **kwargs) -> int:
    """Persist a `is_rising_star` flag on PlayerAggregate. Returns count tagged."""
    detected = detect_rising_stars(db, **kwargs)
    rising_keys = {(d["puuid"], d["role"]) for d in detected}

    aggs = db.query(PlayerAggregate).all()
    n = 0
    for a in aggs:
        flag = (a.puuid, a.role) in rising_keys
        if a.is_rising_star != flag:
            a.is_rising_star = flag
        if flag:
            n += 1
    db.commit()
    return n
