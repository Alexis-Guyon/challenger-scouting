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
    min_current_css: float = 55.0,
) -> list[dict]:
    """
    A "rising star" is a (puuid, role) where the most recent `min_consecutive`
    snapshots form a monotonically increasing CSS sequence with each step ≥
    `min_per_step_gain` and total gain ≥ `min_total_gain`. Current CSS must
    also be ≥ `min_current_css` so we don't surface low-tier uptrends.
    """
    # Group snapshots by (puuid, role), most recent first
    rows = db.query(CSSSnapshot).order_by(desc(CSSSnapshot.snapshot_at)).all()
    by_key: dict[tuple, list[CSSSnapshot]] = defaultdict(list)
    for s in rows:
        by_key[(s.puuid, s.role)].append(s)

    out: list[dict] = []

    for (puuid, role), seq in by_key.items():
        if len(seq) < min_consecutive:
            continue

        # Take the latest N (already most-recent-first)
        latest = seq[:min_consecutive]
        # Reverse to oldest → newest for monotonic check
        chrono = list(reversed(latest))
        css_seq = [s.css_score for s in chrono]

        # Skip if current CSS too low
        if css_seq[-1] < min_current_css:
            continue

        # Check monotonic increase with min step gain
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
    logger.info("rising stars: %d players match (min_consecutive=%d, min_total_gain=%.1f)",
                len(out), min_consecutive, min_total_gain)
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
