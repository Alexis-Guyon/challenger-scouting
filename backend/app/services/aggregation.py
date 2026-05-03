"""
Aggregate per-match participations into player_aggregates per (puuid, patch, role).
Compute role-wide distributions (mu, sigma) for z-score scoring.
"""
import logging
from collections import defaultdict
from statistics import mean, pstdev

from sqlalchemy.orm import Session

from ..models import (
    ChampionDistribution,
    ChampionPool,
    MatchParticipant,
    Match,
    Player,
    PlayerAggregate,
    RoleDistribution,
)

logger = logging.getLogger(__name__)


# Metrics aggregated per player
METRICS = [
    "gd15", "xpd15", "csd15", "cspm", "dmg_share", "dpm",
    "kp", "kda", "vspm", "wpm", "wcpm",
    "solo_kills", "objective_dmg", "early_deaths", "deaths",
]


def _safe_mean(xs: list[float]) -> float:
    return mean(xs) if xs else 0.0


def _safe_std(xs: list[float]) -> float:
    return pstdev(xs) if len(xs) > 1 else 0.0


def aggregate_player(db: Session, puuid: str, patch: str | None = None,
                     current_patch_only: bool = False) -> list[PlayerAggregate]:
    """
    Aggregate one player's participations into PlayerAggregate rows (one per role/patch).

    - patch: if provided, restrict to that patch.
    - current_patch_only: only consider games on the most-played patch in the DB
      (proxy for "current meta"). Useful for hardening CSS to current meta.
    """
    q = (
        db.query(MatchParticipant, Match)
        .join(Match, MatchParticipant.match_id == Match.match_id)
        .filter(MatchParticipant.puuid == puuid)
    )
    if patch:
        q = q.filter(Match.patch == patch)
    elif current_patch_only:
        from sqlalchemy import func as _func
        latest_patch = (
            db.query(Match.patch, _func.count(Match.match_id).label("n"))
            .group_by(Match.patch)
            .order_by(_func.count(Match.match_id).desc())
            .first()
        )
        if latest_patch:
            q = q.filter(Match.patch == latest_patch[0])
    rows = q.all()

    if not rows:
        return []

    by_role: dict[tuple[str, str], list[tuple[MatchParticipant, Match]]] = defaultdict(list)
    for mp, m in rows:
        if not mp.role or mp.role == "":
            continue
        by_role[(m.patch, mp.role)].append((mp, m))

    aggregates: list[PlayerAggregate] = []
    for (p_patch, role), items in by_role.items():
        gd15 = [mp.gd_at_15 for mp, _ in items]
        xpd15 = [mp.xpd_at_15 for mp, _ in items]
        csd15 = [mp.csd_at_15 for mp, _ in items]
        dmg_share = [mp.damage_share for mp, _ in items]
        kp = [mp.kill_participation for mp, _ in items]
        kda = [mp.kda for mp, _ in items]
        deaths = [mp.deaths for mp, _ in items]
        early_deaths = [mp.early_deaths for mp, _ in items]
        solo_kills = [mp.solo_kills for mp, _ in items]
        obj_dmg = [mp.objective_dmg for mp, _ in items]

        # per-minute (need duration)
        cspm, dpm, vspm, wpm, wcpm = [], [], [], [], []
        for mp, m in items:
            mins = max(m.game_duration_sec / 60.0, 1.0)
            cspm.append(mp.cs_total / mins)
            dpm.append(mp.damage_to_champs / mins)
            vspm.append(mp.vision_score / mins)
            wpm.append(mp.wards_placed / mins)
            wcpm.append(mp.wards_killed / mins)

        wins = sum(1 for mp, _ in items if mp.win)

        # Champion pool (≥3 games threshold)
        champ_counts: dict[int, list[MatchParticipant]] = defaultdict(list)
        for mp, _ in items:
            champ_counts[mp.champion_id].append(mp)
        pool_size = sum(1 for c, ps in champ_counts.items() if len(ps) >= 3)

        agg = (
            db.query(PlayerAggregate)
            .filter_by(puuid=puuid, patch=p_patch, role=role)
            .one_or_none()
        )
        if not agg:
            agg = PlayerAggregate(puuid=puuid, patch=p_patch, role=role)
            db.add(agg)

        agg.games_played = len(items)
        agg.wins = wins
        agg.avg_gd15 = _safe_mean(gd15)
        agg.avg_xpd15 = _safe_mean(xpd15)
        agg.avg_csd15 = _safe_mean(csd15)
        agg.avg_cspm = _safe_mean(cspm)
        agg.avg_dmg_share = _safe_mean(dmg_share)
        agg.avg_dpm = _safe_mean(dpm)
        agg.avg_kp = _safe_mean(kp)
        agg.avg_kda = _safe_mean(kda)
        agg.avg_vspm = _safe_mean(vspm)
        agg.avg_wpm = _safe_mean(wpm)
        agg.avg_wcpm = _safe_mean(wcpm)
        agg.avg_solo_kills = _safe_mean(solo_kills)
        agg.avg_objective_dmg = _safe_mean(obj_dmg)
        agg.avg_early_deaths = _safe_mean(early_deaths)
        agg.avg_deaths = _safe_mean(deaths)
        agg.std_gd15 = _safe_std(gd15)
        agg.std_dmg_share = _safe_std(dmg_share)
        agg.std_kp = _safe_std(kp)
        agg.champion_pool_size = pool_size

        aggregates.append(agg)

        # Champion pool detail (richer stats for per-champion CSS)
        # Build O(1) match lookup once instead of scanning items per champion
        match_by_part_id = {mp.id: m for mp, m in items}
        db.query(ChampionPool).filter_by(puuid=puuid, patch=p_patch).delete()
        for cid, ps in champ_counts.items():
            cs_dur_sec = []
            for x in ps:
                m = match_by_part_id.get(x.id)
                if m and m.game_duration_sec:
                    cs_dur_sec.append(m.game_duration_sec)
            avg_dur_min = (_safe_mean(cs_dur_sec) / 60.0) if cs_dur_sec else 30.0
            avg_dpm = _safe_mean([x.damage_to_champs for x in ps]) / max(avg_dur_min, 1)
            # Role this player most often plays this champion in
            role_counts: dict[str, int] = defaultdict(int)
            for x in ps:
                role_counts[x.role] += 1
            top_role = max(role_counts, key=role_counts.get) if role_counts else role
            cp = ChampionPool(
                puuid=puuid,
                patch=p_patch,
                role=top_role,
                champion_id=cid,
                champion_name=ps[0].champion_name,
                games=len(ps),
                wins=sum(1 for x in ps if x.win),
                avg_kda=_safe_mean([x.kda for x in ps]),
                avg_dmg_share=_safe_mean([x.damage_share for x in ps]),
                avg_kp=_safe_mean([x.kill_participation for x in ps]),
                avg_gd15=_safe_mean([x.gd_at_15 for x in ps]),
                avg_csd15=_safe_mean([x.csd_at_15 for x in ps]),
                avg_dpm=avg_dpm,
            )
            db.add(cp)

        # Set main_role on Player if best
        player = db.get(Player, puuid)
        if player:
            current_top = (
                db.query(PlayerAggregate)
                .filter_by(puuid=puuid)
                .order_by(PlayerAggregate.games_played.desc())
                .first()
            )
            if current_top:
                player.main_role = current_top.role

    db.commit()
    return aggregates


def aggregate_all_players(db: Session) -> int:
    puuids = [p.puuid for p in db.query(Player).all()]
    n = 0
    for puuid in puuids:
        aggs = aggregate_player(db, puuid)
        if aggs:
            n += 1
    return n


def compute_lobby_lp(db: Session) -> int:
    """
    For each match, compute avg_lobby_lp = mean LP of the 9 *other* participants
    (using their latest RankSnapshot). Neutralizes "soft lobby" perfs.
    """
    from collections import defaultdict
    from sqlalchemy import desc as sql_desc

    # Latest LP per puuid
    lp_by_puuid: dict[str, int] = {}
    from ..models import RankSnapshot
    for puuid in {row[0] for row in db.query(MatchParticipant.puuid).distinct().all()}:
        rank = (
            db.query(RankSnapshot)
            .filter_by(puuid=puuid)
            .order_by(sql_desc(RankSnapshot.snapshot_date))
            .first()
        )
        if rank and rank.lp is not None:
            lp_by_puuid[puuid] = rank.lp

    # For each match, average the LP of all participants we have data for
    parts_by_match: dict[str, list[str]] = defaultdict(list)
    for mp in db.query(MatchParticipant).all():
        parts_by_match[mp.match_id].append(mp.puuid)

    n_updated = 0
    for match_id, puuids in parts_by_match.items():
        lps = [lp_by_puuid[p] for p in puuids if p in lp_by_puuid]
        if not lps:
            continue
        avg = int(sum(lps) / len(lps))
        m = db.get(Match, match_id)
        if m:
            m.avg_lobby_lp = avg
            n_updated += 1
    db.commit()
    return n_updated



def compute_role_distributions(db: Session, min_games: int) -> dict:
    """For each (patch, role, metric), compute mean & std across all players passing min_games."""
    aggs = (
        db.query(PlayerAggregate)
        .filter(PlayerAggregate.games_played >= min_games)
        .all()
    )
    by_pr: dict[tuple[str, str], list[PlayerAggregate]] = defaultdict(list)
    for a in aggs:
        by_pr[(a.patch, a.role)].append(a)

    metric_attr = {
        "gd15": "avg_gd15", "xpd15": "avg_xpd15", "csd15": "avg_csd15",
        "cspm": "avg_cspm", "dmg_share": "avg_dmg_share", "dpm": "avg_dpm",
        "kp": "avg_kp", "kda": "avg_kda", "vspm": "avg_vspm",
        "wpm": "avg_wpm", "wcpm": "avg_wcpm",
        "solo_kills": "avg_solo_kills", "objective_dmg": "avg_objective_dmg",
        "early_deaths": "avg_early_deaths", "deaths": "avg_deaths",
    }

    db.query(RoleDistribution).delete()
    out = {}
    for (patch, role), pool in by_pr.items():
        for metric, attr in metric_attr.items():
            xs = [getattr(a, attr) for a in pool]
            mu = _safe_mean(xs)
            sd = _safe_std(xs) or 1e-6
            db.add(RoleDistribution(
                patch=patch, role=role, metric=metric,
                mean=mu, std=sd, n_samples=len(xs),
            ))
            out[(patch, role, metric)] = (mu, sd, len(xs))
    db.commit()
    logger.info("computed %d role distributions", len(out))
    return out


def compute_champion_distributions(db: Session, min_match_count: int = 5,
                                    min_players_per_champion: int = 5,
                                    min_games_per_player: int = 3) -> dict:
    """
    Build per-champion baselines for z-score scoring. Two passes:

    Pass A (player-aggregate based, statistically tight):
      built from ChampionPool entries when at least `min_players_per_champion`
      distinct Challenger players each have ≥ `min_games_per_player` games on
      that (champ, role, patch). Uses player averages → smooth distributions.

    Pass B (match-level fallback, covers niche/off-meta):
      built from raw MatchParticipant rows for any (champ, role, patch) that
      didn't qualify under Pass A. Threshold: `min_match_count` total games
      seen anywhere in the DB. Game-level variance is wider than player-avg
      variance, so z-scores from this baseline are slightly compressed (top
      performers look slightly less elite). We accept that as the cost of
      covering ~all champions instead of just the meta.
    """
    metric_attr = {
        "kda": "avg_kda",
        "dmg_share": "avg_dmg_share",
        "kp": "avg_kp",
        "gd15": "avg_gd15",
        "csd15": "avg_csd15",
        "dpm": "avg_dpm",
    }

    # ----- Pass A: player-aggregate level -----
    cps = db.query(ChampionPool).filter(ChampionPool.games >= min_games_per_player).all()
    by_prc_a: dict[tuple[str, str, int], list[ChampionPool]] = defaultdict(list)
    for cp in cps:
        if not cp.role:
            continue
        by_prc_a[(cp.patch, cp.role, cp.champion_id)].append(cp)

    db.query(ChampionDistribution).delete()
    out: dict[tuple, tuple] = {}
    qualified_a: set[tuple[str, str, int]] = set()

    for (patch, role, cid), pool in by_prc_a.items():
        if len(pool) < min_players_per_champion:
            continue
        qualified_a.add((patch, role, cid))
        for metric, attr in metric_attr.items():
            xs = [getattr(cp, attr) for cp in pool]
            mu = _safe_mean(xs)
            sd = _safe_std(xs) or 1e-6
            db.add(ChampionDistribution(
                patch=patch, role=role, champion_id=cid, metric=metric,
                mean=mu, std=sd, n_samples=len(xs),
            ))
            out[(patch, role, cid, metric)] = (mu, sd, len(xs))

    # ----- Pass B: match-level fallback for everything else -----
    by_prc_b: dict[tuple[str, str, int], list[dict]] = defaultdict(list)
    rows = (
        db.query(MatchParticipant, Match)
        .join(Match, MatchParticipant.match_id == Match.match_id)
        .all()
    )
    for mp, m in rows:
        if not mp.role or not mp.champion_id or not m.patch:
            continue
        key = (m.patch, mp.role, mp.champion_id)
        if key in qualified_a:
            continue  # Pass A already covered this combo with tighter stats
        dur_min = max(m.game_duration_sec / 60.0, 1.0) if m.game_duration_sec else 30.0
        by_prc_b[key].append({
            "kda": mp.kda,
            "dmg_share": mp.damage_share,
            "kp": mp.kill_participation,
            "gd15": mp.gd_at_15,
            "csd15": mp.csd_at_15,
            "dpm": mp.damage_to_champs / dur_min,
        })

    pass_b_count = 0
    for (patch, role, cid), samples in by_prc_b.items():
        if len(samples) < min_match_count:
            continue
        for metric in metric_attr.keys():
            xs = [s[metric] for s in samples]
            mu = _safe_mean(xs)
            sd = _safe_std(xs) or 1e-6
            db.add(ChampionDistribution(
                patch=patch, role=role, champion_id=cid, metric=metric,
                mean=mu, std=sd, n_samples=len(samples),
            ))
            out[(patch, role, cid, metric)] = (mu, sd, len(samples))
        pass_b_count += 1

    db.commit()
    logger.info(
        "champion distributions: %d total (Pass A: %d player-level, Pass B: %d match-level fallback)",
        len(out) // len(metric_attr), len(qualified_a), pass_b_count,
    )
    return out
