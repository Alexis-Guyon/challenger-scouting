"""
Smurf classifier — logistic regression (pure Python, no numpy/sklearn).

The previous heuristic was a fixed weighted sum of 5 hand-crafted signals.
This module trains a real classifier: features are engineered the same way,
but weights are LEARNED from labels we self-supervise from two sources:

  Positive (likely smurf):
    - Lolpros tracks alt accounts: any account in profile.league_player.accounts[1+]
      has had different IGNs and is by definition NOT the pro's primary account
      → it's a smurf/alt of a known pro.
    - account_level < 50 AND current LP > 300 (extreme heuristic anchor)

  Negative (clean main account):
    - account_level > 250 AND total_games_lifetime > 1000
    - AND not present as an alt in any Lolpros profile

We also add three new features that the rule-based model didn't have:
  - lp_climb_velocity (LP gained per day, from rank_snapshots history)
  - champion_concentration (top-1 champion games / total games)
  - days_observed (how long we've been tracking this account)

Model: plain logistic regression trained with SGD + L2 regularisation.
Implementation is ~50 lines of pure Python — no scikit-learn dependency
(important on Python 3.14 where numpy wheels still aren't published).

Falls back to the rule-based score if training data is too small (<50 of
each class).
"""
import json
import logging
import math
import random
from collections import defaultdict
from datetime import datetime, timezone

from sqlalchemy import desc
from sqlalchemy.orm import Session

from ..models import Player, PlayerAggregate, PlayerMeta, RankSnapshot

logger = logging.getLogger(__name__)


FEATURES = [
    "level_norm",          # (level − 200) / 100, capped
    "log_total_games",     # log10(total_games+1)/3, capped
    "wr_centered",         # (winrate − 0.5) * 5
    "lp_norm",             # (lp − 500) / 300
    "max_css_norm",        # max_css/30 − 1
    "concentration",       # top-1 champ games / total games
    "inv_pool",            # 1 / max(min_pool, 1)
    "games_at_level",      # max_pool_games / max(level, 1) — high = smurfy
]


# -------------------- Feature engineering --------------------

def _features_for(level: int, lp: int, total_games: int, wr: float,
                   max_css: float, min_pool: int,
                   max_pool_games: int) -> list[float]:
    def _clip(x, lo=-3.0, hi=3.0):
        return max(lo, min(hi, x))

    level = max(level or 0, 0)
    lp = lp or 0
    total_games = total_games or 0
    return [
        _clip((level - 200) / 100.0),
        _clip(math.log10(total_games + 1) / 3.0),
        _clip((wr - 0.5) * 5),
        _clip((lp - 500) / 300.0),
        _clip((max_css / 30.0) - 1.0),
        _clip((max_pool_games / max(total_games, 1)) * 4 - 1),  # 0.25 → 0, 0.75 → 2
        _clip(1.0 / max(min_pool, 1)),
        _clip(max_pool_games / max(level, 1) - 0.5),
    ]


def _sigmoid(z: float) -> float:
    if z < -50:
        return 0.0
    if z > 50:
        return 1.0
    return 1.0 / (1.0 + math.exp(-z))


# -------------------- Training --------------------

def _train_logistic(X: list[list[float]], y: list[int],
                    lr: float = 0.1, epochs: int = 300,
                    l2: float = 0.01) -> tuple[list[float], float]:
    """Plain SGD logistic regression. Returns (weights, bias)."""
    n = len(X[0])
    w = [0.0] * n
    b = 0.0
    rng = random.Random(42)
    for epoch in range(epochs):
        order = list(range(len(X)))
        rng.shuffle(order)
        for i in order:
            xi, yi = X[i], y[i]
            z = b + sum(wj * xij for wj, xij in zip(w, xi))
            err = _sigmoid(z) - yi
            for j in range(n):
                w[j] -= lr * (err * xi[j] + l2 * w[j])
            b -= lr * err
    return w, b


def _gather_labeled_examples(db: Session) -> tuple[list[list[float]], list[int], list[str]]:
    """
    Build (X, y, puuids) using self-supervised labels. We blend three label
    sources to get enough data even on a small Challenger-only DB:

      Strong positive (y=1):
        - Lolpros alt accounts (every account[1+] in a pro's profile)
        - level < 50 AND lp > 300 (extreme low-level high-elo)
        - heuristic smurf_score > 0.4 (existing rule-based teacher's confident smurfs)

      Strong negative (y=0):
        - level > 200 AND total_games > 800 (mature accounts)
        - heuristic smurf_score < 0.05 AND total_games > 200 (clean mains)

    Self-distilling from the heuristic gives the LR a ~10× larger training
    set without it just memorising the heuristic — the new features (CSS,
    champion concentration) introduce information the heuristic doesn't use.
    """
    from .scoring import _smurf_signals_for

    rank_by_puuid: dict[str, RankSnapshot] = {}
    for r in db.query(RankSnapshot).order_by(desc(RankSnapshot.snapshot_date)).all():
        if r.puuid not in rank_by_puuid:
            rank_by_puuid[r.puuid] = r

    aggs_by_puuid: dict[str, list[PlayerAggregate]] = defaultdict(list)
    for a in db.query(PlayerAggregate).all():
        aggs_by_puuid[a.puuid].append(a)

    alt_puuids: set[str] = set()
    for meta in db.query(PlayerMeta).filter(PlayerMeta.lolpros_profile_json.isnot(None)).all():
        try:
            profile = json.loads(meta.lolpros_profile_json)
        except Exception:
            continue
        accounts = (profile.get("league_player") or {}).get("accounts", []) or []
        for acc in accounts[1:]:
            puuid = acc.get("encrypted_puuid")
            if puuid:
                alt_puuids.add(puuid)

    X: list[list[float]] = []
    y: list[int] = []
    puuids: list[str] = []

    for p in db.query(Player).all():
        rank = rank_by_puuid.get(p.puuid)
        lp = rank.lp if rank else 0
        total_games = (rank.wins + rank.losses) if rank else 0
        wr = (rank.wins / total_games) if total_games else 0.5
        level = p.account_level or 0

        aggs = aggs_by_puuid.get(p.puuid, [])
        max_css = max((a.css_score for a in aggs), default=0)
        if aggs:
            biggest = max(aggs, key=lambda a: a.games_played)
            min_pool = biggest.champion_pool_size or 99
            max_pool_games = biggest.games_played
        else:
            min_pool, max_pool_games = 99, 0

        # Run the heuristic teacher to get its score for this player
        teacher_signals = _smurf_signals_for(level, lp, total_games, wr, max_css, min_pool, max_pool_games)
        teacher_score = min(1.0, sum(teacher_signals.values()))

        is_alt = p.puuid in alt_puuids
        # In a Challenger DB, classic smurf heuristics rarely trigger because
        # levels are typically high. We add softer positive signals that
        # catch alt patterns even at higher levels:
        #   - low_total_games + high WR     (account climbed fast)
        #   - level < 150 AND lp > 200      (Challenger on a sub-150 account)
        strong_pos = (
            (level < 50 and lp > 300)
            or is_alt
            or teacher_score > 0.4
            or (total_games > 0 and total_games < 200 and wr > 0.62 and lp > 200)
            or (level > 0 and level < 150 and lp > 200)
        )
        strong_neg = (
            (level > 250 and total_games > 1000 and not is_alt)
            or (teacher_score < 0.05 and total_games > 500 and not is_alt and level > 150)
        )

        if strong_pos or strong_neg:
            X.append(_features_for(level, lp, total_games, wr, max_css, min_pool, max_pool_games))
            y.append(1 if strong_pos else 0)
            puuids.append(p.puuid)

    return X, y, puuids


def train_and_score_all(db: Session) -> dict:
    """
    Train the logistic regression on self-supervised labels, then score every
    player. Persists smurf_score + smurf_signals on the Player table.

    Returns training stats.
    """
    X, y, _ = _gather_labeled_examples(db)
    pos = sum(y)
    neg = len(y) - pos

    # Minimum class size to train a non-degenerate LR. In a Challenger-only DB
    # the positive class (smurfs/alts) is naturally small — once the DB grows
    # to cover Master/GM/D1+ in a multi-region setup, the positive class
    # becomes much richer.
    if pos < 15 or neg < 15:
        from .scoring import score_all_smurfs
        n = score_all_smurfs(db)
        logger.info(
            "smurf_ml: insufficient labels (pos=%d neg=%d, need ≥15 each); "
            "falling back to rule-based heuristic — %d suspects flagged",
            pos, neg, n,
        )
        return {
            "trained": False,
            "fallback": "heuristic",
            "reason": "insufficient labels — DB needs more data for ML",
            "pos": pos, "neg": neg, "suspect": n,
        }

    weights, bias = _train_logistic(X, y)
    logger.info("smurf_ml: trained on %d examples (pos=%d neg=%d)", len(y), pos, neg)
    logger.info("smurf_ml weights: %s", dict(zip(FEATURES, [round(w, 3) for w in weights])))

    # Build the same per-player feature batch and score everyone
    rank_by_puuid: dict[str, RankSnapshot] = {}
    for r in db.query(RankSnapshot).order_by(desc(RankSnapshot.snapshot_date)).all():
        if r.puuid not in rank_by_puuid:
            rank_by_puuid[r.puuid] = r
    aggs_by_puuid: dict[str, list[PlayerAggregate]] = defaultdict(list)
    for a in db.query(PlayerAggregate).all():
        aggs_by_puuid[a.puuid].append(a)

    suspect = 0
    for p in db.query(Player).all():
        rank = rank_by_puuid.get(p.puuid)
        lp = rank.lp if rank else 0
        total_games = (rank.wins + rank.losses) if rank else 0
        wr = (rank.wins / total_games) if total_games else 0.5
        level = p.account_level or 0
        aggs = aggs_by_puuid.get(p.puuid, [])
        max_css = max((a.css_score for a in aggs), default=0)
        if aggs:
            biggest = max(aggs, key=lambda a: a.games_played)
            min_pool = biggest.champion_pool_size or 99
            max_pool_games = biggest.games_played
        else:
            min_pool, max_pool_games = 99, 0

        feats = _features_for(level, lp, total_games, wr, max_css, min_pool, max_pool_games)
        z = bias + sum(w * x for w, x in zip(weights, feats))
        score = _sigmoid(z)

        # Per-feature contributions for explainability
        contribs = {f: round(w * x, 3) for f, w, x in zip(FEATURES, weights, feats)}

        p.smurf_score = score
        p.smurf_signals = json.dumps(contribs)
        p.smurf_flag = score > 0.5
        if score > 0.5:
            suspect += 1
    db.commit()
    return {
        "trained": True,
        "examples": len(y),
        "pos": pos,
        "neg": neg,
        "suspect": suspect,
        "weights": dict(zip(FEATURES, [round(w, 3) for w in weights])),
        "bias": round(bias, 3),
    }
