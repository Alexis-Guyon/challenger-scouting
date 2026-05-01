"""
Challenger Scouting Score (CSS) engine.

Z-score-based scoring against the Challenger pool's distribution per role/patch.
Weights configured per role.
"""
import logging
from collections import defaultdict

from sqlalchemy.orm import Session

from ..config import settings
from ..models import PlayerAggregate, RoleDistribution

logger = logging.getLogger(__name__)


# Per-role weights for the 8 categories.
# Each category groups multiple z-scored metrics with internal weights.
ROLE_WEIGHTS = {
    "TOP": {
        "lane": 0.25, "damage": 0.15, "vision": 0.05, "objective": 0.10,
        "mapplay": 0.10, "survival": 0.10, "champpool": 0.10, "consistency": 0.15,
    },
    "JGL": {
        "lane": 0.15, "damage": 0.10, "vision": 0.10, "objective": 0.25,
        "mapplay": 0.20, "survival": 0.05, "champpool": 0.10, "consistency": 0.05,
    },
    "MID": {
        "lane": 0.25, "damage": 0.25, "vision": 0.05, "objective": 0.10,
        "mapplay": 0.15, "survival": 0.05, "champpool": 0.10, "consistency": 0.05,
    },
    "ADC": {
        "lane": 0.25, "damage": 0.30, "vision": 0.05, "objective": 0.10,
        "mapplay": 0.05, "survival": 0.10, "champpool": 0.10, "consistency": 0.05,
    },
    "SUP": {
        "lane": 0.10, "damage": 0.05, "vision": 0.30, "objective": 0.15,
        "mapplay": 0.15, "survival": 0.10, "champpool": 0.10, "consistency": 0.05,
    },
}

# Each category = list of (metric, internal_weight).
# These are stable across roles; the role weight on the category does the differentiation.
CATEGORY_METRICS = {
    "lane":      [("gd15", 0.4), ("xpd15", 0.3), ("csd15", 0.2), ("cspm", 0.1)],
    "damage":    [("dmg_share", 0.6), ("dpm", 0.4)],
    "vision":    [("vspm", 0.5), ("wpm", 0.25), ("wcpm", 0.25)],
    "objective": [("objective_dmg", 1.0)],
    "mapplay":   [("kp", 0.5), ("solo_kills", 0.5)],
    "survival":  [("kda", 0.6), ("early_deaths", -0.4)],  # negative = lower is better
    # champpool & consistency handled separately
}


def z_to_score(z: float) -> float:
    """Convert z-score to 0-100 (50 + 15*z, clipped)."""
    return max(0.0, min(100.0, 50.0 + 15.0 * z))


def load_distributions(db: Session, patch: str, role: str) -> dict[str, tuple[float, float]]:
    rows = db.query(RoleDistribution).filter_by(patch=patch, role=role).all()
    return {r.metric: (r.mean, r.std) for r in rows}


def compute_css_for_aggregate(db: Session, agg: PlayerAggregate) -> tuple[float, float, dict]:
    """Returns (css_raw, css_final, breakdown_dict)."""
    dists = load_distributions(db, agg.patch, agg.role)
    if not dists:
        return 0.0, 0.0, {"error": "no distribution available"}

    weights = ROLE_WEIGHTS.get(agg.role, ROLE_WEIGHTS["MID"])

    metric_attr = {
        "gd15": "avg_gd15", "xpd15": "avg_xpd15", "csd15": "avg_csd15",
        "cspm": "avg_cspm", "dmg_share": "avg_dmg_share", "dpm": "avg_dpm",
        "kp": "avg_kp", "kda": "avg_kda", "vspm": "avg_vspm",
        "wpm": "avg_wpm", "wcpm": "avg_wcpm",
        "solo_kills": "avg_solo_kills", "objective_dmg": "avg_objective_dmg",
        "early_deaths": "avg_early_deaths",
    }

    metric_scores: dict[str, float] = {}
    for metric, attr in metric_attr.items():
        if metric not in dists:
            continue
        mu, sd = dists[metric]
        sd = sd or 1e-6
        val = getattr(agg, attr)
        z = (val - mu) / sd
        metric_scores[metric] = z_to_score(z)

    # Category aggregation
    category_scores: dict[str, float] = {}
    for cat, members in CATEGORY_METRICS.items():
        num, denom = 0.0, 0.0
        for metric, w in members:
            if metric not in metric_scores:
                continue
            score = metric_scores[metric]
            if w < 0:
                score = 100 - score  # invert (lower is better)
                w = -w
            num += score * w
            denom += w
        category_scores[cat] = num / denom if denom > 0 else 50.0

    # champpool: scaled by pool size
    pool = agg.champion_pool_size or 0
    if pool <= 1:
        champpool = 30.0
    elif pool == 2:
        champpool = 45.0
    elif pool == 3:
        champpool = 60.0
    elif pool == 4:
        champpool = 75.0
    else:
        champpool = 85.0
    category_scores["champpool"] = champpool

    # consistency: lower std_gd15 vs role distribution → higher score
    sd_gd15 = dists.get("gd15", (0, 1))[1] or 1
    consistency_ratio = (agg.std_gd15 or sd_gd15) / sd_gd15
    consistency = max(0.0, min(100.0, 100 - 50 * (consistency_ratio - 1)))
    category_scores["consistency"] = consistency

    # Weighted CSS
    css_raw = sum(category_scores[c] * w for c, w in weights.items())

    # Adjustments
    games = agg.games_played
    sample_factor = 0.5 + 0.5 * min(1.0, games / settings.min_games)  # 0.5..1.0

    player = agg.player
    smurf_factor = 1.0
    if player:
        if player.account_level and player.account_level < 60:
            smurf_factor = 0.7
        elif player.smurf_flag:
            smurf_factor = 0.7

    # Lobby-LP weighting: average lobby LP for this player's games on this patch+role.
    # >900 LP avg → uplift up to ×1.10; <500 LP avg → discount down to ×0.90.
    lobby_factor = _lobby_factor_for(db, agg)

    css_final = css_raw * sample_factor * smurf_factor * lobby_factor

    breakdown = {
        "metrics": metric_scores,
        "categories": category_scores,
        "weights": weights,
        "sample_factor": sample_factor,
        "smurf_factor": smurf_factor,
        "lobby_factor": lobby_factor,
        "css_raw": css_raw,
        "css_final": css_final,
        "games_played": games,
    }
    return css_raw, css_final, breakdown


def _lobby_factor_for(db: Session, agg: PlayerAggregate) -> float:
    """
    Compute mean avg_lobby_lp over the matches that contributed to this aggregate.
    Map to a factor in [0.90, 1.10] anchored at 700 LP (Challenger median).
    """
    from ..models import Match, MatchParticipant
    rows = (
        db.query(Match.avg_lobby_lp)
        .join(MatchParticipant, MatchParticipant.match_id == Match.match_id)
        .filter(MatchParticipant.puuid == agg.puuid, MatchParticipant.role == agg.role,
                Match.patch == agg.patch, Match.avg_lobby_lp != None)  # noqa: E711
        .all()
    )
    if not rows:
        return 1.0
    avg_lp = sum(r[0] for r in rows) / len(rows)
    # ±0.10 around 700 LP, clipped
    delta = (avg_lp - 700) / 2000.0  # 200 LP delta = ~0.1 factor change
    return max(0.90, min(1.10, 1.0 + delta))


def score_all(db: Session, min_games: int) -> int:
    """Compute CSS for every PlayerAggregate with enough games. Returns count."""
    aggs = db.query(PlayerAggregate).filter(PlayerAggregate.games_played >= min_games).all()

    # Group by (patch, role) for percentile rank
    by_pr: dict[tuple[str, str], list[tuple[PlayerAggregate, float]]] = defaultdict(list)
    for a in aggs:
        _, css_final, _ = compute_css_for_aggregate(db, a)
        a.css_raw = css_final / max(0.5, 1)  # placeholder; raw computed inside
        a.css_score = css_final
        by_pr[(a.patch, a.role)].append((a, css_final))

    # Percentile
    for (patch, role), items in by_pr.items():
        items.sort(key=lambda x: x[1])
        total = len(items)
        if total <= 1:
            for a, _ in items:
                a.percentile_rank = 50.0
        else:
            for rank, (a, _) in enumerate(items):
                a.percentile_rank = round(100 * rank / (total - 1), 1)

    db.commit()
    logger.info("scored %d player aggregates", len(aggs))
    return len(aggs)
