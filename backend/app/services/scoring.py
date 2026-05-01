"""
Challenger Scouting Score (CSS) engine.

Z-score-based scoring against the Challenger pool's distribution per role/patch.
Weights configured per role.
"""
import logging
from collections import defaultdict

from sqlalchemy.orm import Session

from ..config import settings
from ..models import (
    ChampionDistribution,
    ChampionPool,
    PlayerAggregate,
    Player,
    RankSnapshot,
    RoleDistribution,
)

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

    # consistency: low intra-player variance in GD@15 → high score.
    # We compare to the MEDIAN intra-player std_gd15 of the role+patch cohort.
    # The previous formula divided by the std of player MEANS (very different
    # quantity, ~5x smaller), pinning everyone to 0. Now: ratio < 1 = more
    # consistent than the median Challenger; ratio > 1 = noisier.
    median_std = _median_within_player_std(db, agg.role, agg.patch)
    if not median_std:
        median_std = 1000  # safe fallback when cohort is too small
    player_std = agg.std_gd15 if agg.std_gd15 is not None else median_std
    consistency_ratio = player_std / median_std
    # 30 pts per unit deviation, anchored at 1.0 = 80
    # ratio 0.5 → 100, ratio 1.0 → 80, ratio 1.5 → 65, ratio 2.0 → 50, ratio 3.0 → 20
    consistency = max(0.0, min(100.0, 80 - 30 * (consistency_ratio - 1)))
    category_scores["consistency"] = consistency

    # Weighted CSS
    css_raw = sum(category_scores[c] * w for c, w in weights.items())

    # Adjustments
    games = agg.games_played
    sample_factor = 0.5 + 0.5 * min(1.0, games / settings.min_games)  # 0.5..1.0

    player = agg.player
    # Smurf factor is now continuous: maps smurf_score [0..1] → factor [1.0..0.6].
    # A clean account (score=0) keeps full CSS; a max-suspicion account drops to 60%.
    smurf_factor = 1.0
    if player and player.smurf_score is not None:
        smurf_factor = max(0.6, 1.0 - 0.4 * player.smurf_score)

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


CHAMPION_METRIC_WEIGHTS = {
    "kda": 0.20,
    "dmg_share": 0.20,
    "kp": 0.15,
    "gd15": 0.20,
    "csd15": 0.10,
    "dpm": 0.15,
}


def compute_champion_css(db: Session, cp: ChampionPool) -> tuple[float, bool]:
    """
    Per-champion CSS, scored against all Challenger players who play this same
    (patch, role, champion). Returns (score_0_100, has_baseline).

    If the champion has no baseline (N<10 in distribution table), returns (0, False).
    """
    dists = (
        db.query(ChampionDistribution)
        .filter_by(patch=cp.patch, role=cp.role, champion_id=cp.champion_id)
        .all()
    )
    if not dists:
        return 0.0, False

    by_metric = {d.metric: (d.mean, d.std) for d in dists}
    val_attr = {
        "kda": "avg_kda", "dmg_share": "avg_dmg_share", "kp": "avg_kp",
        "gd15": "avg_gd15", "csd15": "avg_csd15", "dpm": "avg_dpm",
    }
    num, denom = 0.0, 0.0
    for metric, w in CHAMPION_METRIC_WEIGHTS.items():
        if metric not in by_metric:
            continue
        mu, sd = by_metric[metric]
        sd = sd or 1e-6
        x = getattr(cp, val_attr[metric])
        z = (x - mu) / sd
        score = max(0.0, min(100.0, 50.0 + 15.0 * z))
        num += score * w
        denom += w

    if denom == 0:
        return 0.0, False
    return round(num / denom, 1), True


def score_all_champions(db: Session) -> int:
    """Compute champion-level CSS for every ChampionPool entry. Returns count scored."""
    pool_entries = db.query(ChampionPool).all()
    n_with_baseline = 0
    for cp in pool_entries:
        score, has = compute_champion_css(db, cp)
        cp.champion_css = score
        cp.has_champion_baseline = has
        if has:
            n_with_baseline += 1
    db.commit()
    logger.info("scored champion-level CSS for %d entries (%d with baseline)",
                len(pool_entries), n_with_baseline)
    return n_with_baseline


# ----------------- Smurf detector (multi-signal rule-based) -----------------

def _smurf_signals_for(level: int, lp: int, total_games: int, wr: float,
                        max_css: float, min_pool: int, max_pool_games: int) -> dict[str, float]:
    """Pure function — easy to unit test. Returns dict of triggered signals."""
    signals: dict[str, float] = {}

    # Signal 1 — Account level vs LP (heaviest)
    if level < 50 and lp > 400:
        signals["low_level_high_lp"] = 0.40
    elif level < 80 and lp > 300:
        signals["low_level_high_lp"] = 0.25
    elif level < 120 and lp > 200:
        signals["low_level_high_lp"] = 0.10

    # Signal 2 — Few lifetime ranked games for the rank
    if total_games < 50 and lp > 300:
        signals["low_total_games"] = 0.20
    elif total_games < 200 and lp > 500:
        signals["low_total_games"] = 0.10

    # Signal 3 — Suspiciously high winrate
    if total_games >= 30:
        if wr > 0.65:
            signals["wr_too_high"] = 0.15
        elif wr > 0.60:
            signals["wr_too_high"] = 0.08

    # Signal 4 — One-trick at this level
    if min_pool <= 1 and max_pool_games > 25:
        signals["one_trick_high_games"] = 0.10

    # Signal 5 — Strong CSS combined with low level (cross-check)
    if level < 60 and max_css > 70:
        signals["high_css_low_level"] = 0.15
    elif level < 60 and max_css > 60:
        signals["high_css_low_level"] = 0.07

    return signals


def score_all_smurfs(db: Session) -> int:
    """
    Recompute smurf score for every player using batched queries.
    Returns count of likely smurfs (score > 0.5).
    """
    import json
    from sqlalchemy import func
    from collections import defaultdict

    # Batch 1: latest rank per puuid (single query, group by puuid taking max snapshot_date)
    rank_by_puuid: dict[str, RankSnapshot] = {}
    for r in db.query(RankSnapshot).order_by(RankSnapshot.snapshot_date.desc()).all():
        if r.puuid not in rank_by_puuid:
            rank_by_puuid[r.puuid] = r

    # Batch 2: all aggregates grouped by puuid
    aggs_by_puuid: dict[str, list[PlayerAggregate]] = defaultdict(list)
    for a in db.query(PlayerAggregate).all():
        aggs_by_puuid[a.puuid].append(a)

    suspect = 0
    for p in db.query(Player).all():
        rank = rank_by_puuid.get(p.puuid)
        lp = rank.lp if rank else 0
        total_games = (rank.wins + rank.losses) if rank else 0
        wr = (rank.wins / total_games) if total_games else 0
        level = p.account_level or 0

        aggs = aggs_by_puuid.get(p.puuid, [])
        max_css = max((a.css_score for a in aggs), default=0)
        if aggs:
            biggest = max(aggs, key=lambda a: a.games_played)
            min_pool = biggest.champion_pool_size
            max_pool_games = biggest.games_played
        else:
            min_pool, max_pool_games = 99, 0

        signals = _smurf_signals_for(level, lp, total_games, wr, max_css, min_pool, max_pool_games)
        score = min(1.0, sum(signals.values()))
        p.smurf_score = score
        p.smurf_signals = json.dumps(signals) if signals else None
        p.smurf_flag = score > 0.5
        if score > 0.5:
            suspect += 1
    db.commit()
    logger.info("recomputed smurf scores for all players (%d suspect)", suspect)
    return suspect


# Back-compat: per-player function (used by API request when surfacing live)
def compute_smurf_score(db: Session, player: Player) -> tuple[float, dict]:
    rank = (
        db.query(RankSnapshot)
        .filter_by(puuid=player.puuid)
        .order_by(RankSnapshot.snapshot_date.desc())
        .first()
    )
    lp = rank.lp if rank else 0
    total_games = (rank.wins + rank.losses) if rank else 0
    wr = (rank.wins / total_games) if total_games else 0
    level = player.account_level or 0
    aggs = db.query(PlayerAggregate).filter_by(puuid=player.puuid).all()
    max_css = max((a.css_score for a in aggs), default=0)
    if aggs:
        biggest = max(aggs, key=lambda a: a.games_played)
        min_pool = biggest.champion_pool_size
        max_pool_games = biggest.games_played
    else:
        min_pool, max_pool_games = 99, 0
    signals = _smurf_signals_for(level, lp, total_games, wr, max_css, min_pool, max_pool_games)
    return min(1.0, sum(signals.values())), signals


_MEDIAN_STD_CACHE: dict[tuple, float] = {}


def _median_within_player_std(db: Session, role: str, patch: str) -> float:
    """
    Median intra-player std_gd15 across all PlayerAggregates of the same
    role+patch with games_played > 5. Cached per request — recomputing this
    inside compute_css_for_aggregate for every player would be O(N²).

    The cache is invalidated when score_all() starts a new pass.
    """
    key = (role, patch)
    if key in _MEDIAN_STD_CACHE:
        return _MEDIAN_STD_CACHE[key]
    rows = (
        db.query(PlayerAggregate.std_gd15)
        .filter(
            PlayerAggregate.role == role,
            PlayerAggregate.patch == patch,
            PlayerAggregate.games_played > 5,
            PlayerAggregate.std_gd15.isnot(None),
            PlayerAggregate.std_gd15 > 0,
        )
        .all()
    )
    values = sorted(r[0] for r in rows if r[0] and r[0] > 0)
    if not values:
        return 0.0
    median = values[len(values) // 2]
    _MEDIAN_STD_CACHE[key] = median
    return median


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
    # Reset the per-pass median cache so consistency benchmarks are fresh
    _MEDIAN_STD_CACHE.clear()
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
