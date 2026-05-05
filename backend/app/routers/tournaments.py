"""
Tournament stats endpoints + LEC roster comparison.
"""
import json as _json
import time
from collections import defaultdict
from statistics import mean

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc
from sqlalchemy.orm import Session

from ..auth import get_current_user
from ..db import get_db
from ..models import (
    CurrentLECRoster,
    OfficialMatch,
    OfficialMatchParticipant,
    Player,
    PlayerAggregate,
    PlayerMeta,
    ProTeam,
    Tournament,
    User,
)
router = APIRouter(tags=["tournaments"], dependencies=[Depends(get_current_user)])

# --- separate sub-router for /tournament-matches/* ---
match_router = APIRouter(prefix="/tournament-matches", tags=["tournaments"], dependencies=[Depends(get_current_user)])

# --- /teams/* — pro team scouting page ---
team_router = APIRouter(prefix="/teams", tags=["teams"], dependencies=[Depends(get_current_user)])


# ---------------------------------------------------------------
# Module-level resolution-index cache.
#
# These indexes are expensive to build (31k Player rows + 1.3k JSON-parsed
# Lolpros profiles + name normalization), but they only change when the
# Leaguepedia/Lolpros sync runs. Cache them in process for 5 min — covers
# bursts of modal opens (the user clicking through several matches in a
# row) without making the page lag every click.
# ---------------------------------------------------------------
_RESOLUTION_CACHE_TTL_SEC = 5 * 60
_resolution_cache: dict | None = None
_resolution_cache_at: float = 0.0


def _build_resolution_indexes(db: Session) -> dict:
    """Return {lolpros_name_index, summoner_name_index, pro_puuid_set,
    omp_candidate_index}.

    All values are PLAIN STRINGS (puuids), never ORM instances. Caching
    SQLAlchemy entities here would survive past their session's lifetime
    and trigger DetachedInstanceError on the next access. Consumers that
    need to mutate a PlayerMeta should re-fetch via db.get(PlayerMeta, puuid)
    using the request's own session.
    """
    # name → puuid (the puuid of the PlayerMeta whose Lolpros profile name matched)
    lolpros_name_index: dict[str, str] = {}
    metas_with_profile = db.query(PlayerMeta.puuid, PlayerMeta.lolpros_profile_json).filter(
        PlayerMeta.lolpros_profile_json.isnot(None)
    ).all()
    for puuid, profile_json in metas_with_profile:
        try:
            profile = _json.loads(profile_json)
        except Exception:
            continue
        for cand in _candidates_for_match(profile.get("name") or ""):
            lolpros_name_index.setdefault(cand, puuid)

    # name → [puuid, ...] (one or more Player rows whose summoner_name matched)
    summoner_name_index: dict[str, list[str]] = {}
    for puuid, summoner_name in db.query(Player.puuid, Player.summoner_name).all():
        for cand in _candidates_for_match(summoner_name or ""):
            summoner_name_index.setdefault(cand, []).append(puuid)

    pro_puuid_set: set[str] = {
        row[0] for row in db.query(PlayerMeta.puuid).filter(PlayerMeta.is_pro == True).all()  # noqa: E712
    }

    # OMP candidate index: normalized name → pro_player_id. Used by
    # _resolve_pro_player_id so we don't iterate 34k rows per request.
    omp_candidate_index: dict[str, str] = {}
    omp_rows = (
        db.query(OfficialMatchParticipant.pro_player_id,
                 OfficialMatchParticipant.summoner_name,
                 OfficialMatchParticipant.player_name)
        .filter((OfficialMatchParticipant.summoner_name.isnot(None))
                & (OfficialMatchParticipant.summoner_name != ""))
        .all()
    )
    for ppid, sname, pname in omp_rows:
        if not ppid:
            continue
        for cand in _candidates_for_match(sname or ""):
            omp_candidate_index.setdefault(cand, ppid)
        for cand in _candidates_for_match(pname or ""):
            omp_candidate_index.setdefault(cand, ppid)

    return {
        "lolpros_name_index": lolpros_name_index,
        "summoner_name_index": summoner_name_index,
        "pro_puuid_set": pro_puuid_set,
        "omp_candidate_index": omp_candidate_index,
    }


def _get_resolution_indexes(db: Session) -> dict:
    global _resolution_cache, _resolution_cache_at
    now = time.time()
    if _resolution_cache is None or (now - _resolution_cache_at) > _RESOLUTION_CACHE_TTL_SEC:
        _resolution_cache = _build_resolution_indexes(db)
        _resolution_cache_at = now
    return _resolution_cache


def invalidate_resolution_cache() -> None:
    """Call after any sync that modifies PlayerMeta / Player rows."""
    global _resolution_cache, _resolution_cache_at
    _resolution_cache = None
    _resolution_cache_at = 0.0


# Cross-mapping from LP/Riot role to lolesports role
ROLE_MAP_TO_LOLESPORTS = {
    "TOP": "top",
    "JGL": "jungle",
    "MID": "mid",
    "ADC": "bottom",
    "SUP": "support",
}
ROLE_MAP_FROM_LOLESPORTS = {v: k for k, v in ROLE_MAP_TO_LOLESPORTS.items()}


from ..services.name_matching import name_candidates as _candidates_for_match  # noqa: F401


def _resolve_pro_player_id(db: Session, player: Player, meta: PlayerMeta | None) -> str | None:
    """
    Find lolesports pro_player_id for a Riot player.
    Cross-references in this order:
      1. Cached PlayerMeta.lolesports_id (fast path)
      2. Multiple candidate normalizations of the current Riot ID
      3. Lolpros canonical name (e.g. "Adking") + every historical summoner_name
         the pro has used (e.g. an old "ADKINGEUW#EUW" before the team prefix).
    """
    import json as _json

    if meta and meta.lolesports_id:
        return meta.lolesports_id

    cand_set = set(_candidates_for_match(player.summoner_name))

    # Expand with Lolpros canonical name + historical summoner_names if cached.
    # This is the bridge that lets "KC NEXT ADKING#EUW" match a tournament row
    # whose lolesports player_name is just "Adking".
    if meta and meta.lolpros_profile_json:
        try:
            profile = _json.loads(meta.lolpros_profile_json)
        except Exception:
            profile = None
        if profile:
            if profile.get("name"):
                cand_set.update(_candidates_for_match(profile["name"]))
            league_player = profile.get("league_player") or {}
            for acc in league_player.get("accounts", []) or []:
                for sn in acc.get("summoner_names", []) or []:
                    cand_set.update(_candidates_for_match(sn.get("name", "")))
                # The current account's primary IGN
                if acc.get("summoner_name"):
                    cand_set.update(_candidates_for_match(acc["summoner_name"]))
                if acc.get("gamename"):
                    cand_set.update(_candidates_for_match(acc["gamename"]))

    if not cand_set:
        return None

    # Cached normalized-name → pro_player_id lookup (was 34k-row scan).
    omp_index = _get_resolution_indexes(db)["omp_candidate_index"]
    for cand in cand_set:
        ppid = omp_index.get(cand)
        if ppid:
            if meta:
                meta.lolesports_id = ppid
                db.commit()
            return ppid
    return None


def _aggregate_tournament_stats(rows: list[OfficialMatchParticipant], matches_by_id: dict[str, OfficialMatch]) -> dict:
    if not rows:
        return {"games": 0}
    n = len(rows)
    wins = sum(1 for r in rows if r.win)
    # Per-game CSPM (avg over games). Skip games where the duration estimate
    # fell back to ~100 s (the live-window length) — those would skew CSPM by 10x.
    cspm_per_game = []
    for r in rows:
        m = matches_by_id.get(r.match_id)
        if m and m.duration_sec and m.duration_sec >= 15 * 60:
            cspm_per_game.append(r.cs / (m.duration_sec / 60.0))
    return {
        "games": n,
        "wins": wins,
        "winrate": round(wins / n * 100, 1),
        "kda": round(mean(r.kda for r in rows), 2),
        "kills_pg": round(mean(r.kills for r in rows), 2),
        "deaths_pg": round(mean(r.deaths for r in rows), 2),
        "assists_pg": round(mean(r.assists for r in rows), 2),
        "kp": round(mean(r.kill_participation for r in rows), 3),
        "gd10": round(mean(getattr(r, "gd_at_10", 0) or 0 for r in rows), 1),
        "csd10": round(mean(getattr(r, "csd_at_10", 0) or 0 for r in rows), 1),
        "gold10": round(mean(getattr(r, "gold_at_10", 0) or 0 for r in rows), 1),
        "cs10": round(mean(getattr(r, "cs_at_10", 0) or 0 for r in rows), 1),
        "gd15": round(mean(r.gd_at_15 for r in rows), 1),
        "csd15": round(mean(r.csd_at_15 for r in rows), 1),
        "gold15": round(mean(r.gold_at_15 for r in rows), 1),
        "cs15": round(mean(r.cs_at_15 for r in rows), 1),
        "cspm": round(mean(cspm_per_game), 2) if cspm_per_game else 0,
        "champion_pool_size": len({r.champion for r in rows}),
    }


@router.get("/players/{puuid}/tournaments")
def player_tournament_stats(puuid: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    p = db.get(Player, puuid)
    if not p:
        raise HTTPException(404, "player not found")
    meta = db.get(PlayerMeta, puuid)
    pro_player_id = _resolve_pro_player_id(db, p, meta)
    if not pro_player_id:
        return {"matched": False, "stats_by_league": [], "recent_matches": [], "champion_pool": []}

    parts = (
        db.query(OfficialMatchParticipant)
        .filter_by(pro_player_id=pro_player_id)
        .all()
    )
    if not parts:
        return {"matched": True, "pro_player_id": pro_player_id, "stats_by_league": [], "recent_matches": [], "champion_pool": []}

    match_ids = [r.match_id for r in parts]
    matches = {m.id: m for m in db.query(OfficialMatch).filter(OfficialMatch.id.in_(match_ids)).all()}

    # Group by tournament
    by_tour: dict[str | None, list] = defaultdict(list)
    for r in parts:
        m = matches.get(r.match_id)
        by_tour[m.tournament_id if m else None].append(r)

    tour_objs = {t.id: t for t in db.query(Tournament).filter(Tournament.id.in_([k for k in by_tour if k])).all()}

    stats_by_league = []
    for tid, rows in by_tour.items():
        t = tour_objs.get(tid)
        agg = _aggregate_tournament_stats(rows, matches)
        agg["tournament_id"] = tid
        agg["tournament_name"] = (t.name if t else "(unknown)")
        agg["league_slug"] = (t.league_slug if t else None)
        agg["league_name"] = (t.league_name if t else None)
        stats_by_league.append(agg)

    stats_by_league.sort(key=lambda x: (x.get("league_slug") or "", x.get("tournament_name") or ""))

    # Champion pool
    champ_counts: dict[str, list] = defaultdict(list)
    for r in parts:
        champ_counts[r.champion].append(r)
    champion_pool = sorted([
        {
            "champion": c,
            "games": len(rs),
            "wins": sum(1 for r in rs if r.win),
            "winrate": round(sum(1 for r in rs if r.win) / len(rs) * 100, 1),
            "avg_kda": round(mean(r.kda for r in rs), 2),
        }
        for c, rs in champ_counts.items() if c
    ], key=lambda x: -x["games"])[:15]

    # Recent matches
    parts_sorted = sorted(parts, key=lambda r: matches[r.match_id].game_date if matches.get(r.match_id) and matches[r.match_id].game_date else None, reverse=True)
    recent = []
    for r in parts_sorted[:15]:
        m = matches.get(r.match_id)
        recent.append({
            "match_id": r.match_id,
            "game_date": m.game_date.isoformat() if m and m.game_date else None,
            "patch": m.patch if m else None,
            "block_name": m.block_name if m else None,
            "tournament": tour_objs.get(m.tournament_id).name if m and tour_objs.get(m.tournament_id) else None,
            "league_slug": tour_objs.get(m.tournament_id).league_slug if m and tour_objs.get(m.tournament_id) else None,
            "side": r.side, "role": r.role, "champion": r.champion, "win": r.win,
            "kills": r.kills, "deaths": r.deaths, "assists": r.assists,
            "kda": round(r.kda, 2), "cs": r.cs, "gd15": r.gd_at_15, "csd15": r.csd_at_15,
            "kp": round(r.kill_participation, 3),
            "data_complete": True,
        })

    # Surface placeholder games (data_complete=False) from the same Bo3
    # series the player participated in. We can't link these by puuid
    # (no participant rows exist) but they share an `event_id` with
    # the player's known games. The user sees "this 3rd game existed
    # but stats weren't archived" instead of a silent gap.
    player_event_ids = {
        m.event_id for m in matches.values() if m and m.event_id
    }
    if player_event_ids:
        placeholder_matches = (
            db.query(OfficialMatch)
            .filter(OfficialMatch.event_id.in_(player_event_ids))
            .filter(OfficialMatch.data_complete == False)  # noqa: E712
            .all()
        )
        for pm in placeholder_matches:
            tour = tour_objs.get(pm.tournament_id) if pm.tournament_id else None
            recent.append({
                "match_id": pm.id,
                "game_date": pm.game_date.isoformat() if pm.game_date else None,
                "patch": pm.patch,
                "block_name": pm.block_name,
                "tournament": tour.name if tour else None,
                "league_slug": tour.league_slug if tour else None,
                "side": None, "role": None, "champion": None,
                "win": ((pm.blue_win is True) if pm.blue_win is not None else None),
                "kills": 0, "deaths": 0, "assists": 0, "kda": 0.0,
                "cs": 0, "gd15": 0, "csd15": 0, "kp": 0.0,
                "data_complete": False,
            })
        # Re-sort so placeholders interleave by date
        recent.sort(key=lambda r: r["game_date"] or "", reverse=True)
        recent = recent[:15]

    return {
        "matched": True,
        "pro_player_id": pro_player_id,
        "stats_by_league": stats_by_league,
        "champion_pool": champion_pool,
        "recent_matches": recent,
    }


@router.get("/players/{puuid}/roster-compare")
def roster_compare(
    puuid: str,
    role: str | None = Query(default=None, description="Override role (TOP/JGL/MID/ADC/SUP)"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Compare a Challenger SoloQ player to the current LEC roster at the same role.
    Returns the prospect's SoloQ stats vs each LEC pro's tournament stats AND SoloQ stats (when available).
    """
    p = db.get(Player, puuid)
    if not p:
        raise HTTPException(404, "player not found")

    # Determine role
    prospect_agg = (
        db.query(PlayerAggregate)
        .filter_by(puuid=puuid)
        .order_by(desc(PlayerAggregate.games_played))
        .first()
    )
    if role:
        target_role = role.upper()
    elif prospect_agg:
        target_role = prospect_agg.role
    else:
        raise HTTPException(400, "no role found for prospect; pass ?role=MID")

    lolesports_role = ROLE_MAP_TO_LOLESPORTS.get(target_role, target_role.lower())

    # LEC roster at that role
    roster = (
        db.query(CurrentLECRoster)
        .filter_by(role=lolesports_role)
        .all()
    )
    if not roster:
        return {
            "prospect": _serialize_prospect(p, prospect_agg, db),
            "role": target_role,
            "lec_roster": [],
            "warning": "No LEC roster data — run /admin/sync-tournaments first",
        }

    teams = {t.id: t for t in db.query(ProTeam).filter(ProTeam.id.in_([r.team_id for r in roster])).all()}

    # Pre-built name → PlayerMeta / Player indexes (cached 5 min in module
    # scope to avoid 30k+ rows of normalization on every roster-compare hit).
    _idx = _get_resolution_indexes(db)
    _lolpros_name_index = _idx["lolpros_name_index"]
    _summoner_name_index = _idx["summoner_name_index"]
    _pro_puuid_set = _idx["pro_puuid_set"]

    pro_entries = []
    for r in roster:
        pro_data = _summarize_pro_player(
            db, r.pro_player_id, lolesports_role,
            player_name_hint=r.player_name,
            lolpros_name_index=_lolpros_name_index,
            summoner_name_index=_summoner_name_index,
            pro_puuid_set=_pro_puuid_set,
        )
        pro_data["team_code"] = teams[r.team_id].code if r.team_id in teams else ""
        pro_data["team_name"] = teams[r.team_id].name if r.team_id in teams else ""
        pro_data["player_name"] = r.player_name
        pro_data["pro_player_id"] = r.pro_player_id
        pro_entries.append(pro_data)

    return {
        "prospect": _serialize_prospect(p, prospect_agg, db),
        "role": target_role,
        "lec_roster": pro_entries,
    }


def _serialize_prospect(p: Player, agg: PlayerAggregate | None, db: Session) -> dict:
    out = {
        "puuid": p.puuid,
        "summoner_name": p.summoner_name,
        "tier": None, "lp": None,
        "soloq": None,
    }
    from ..models import RankSnapshot
    rank = db.query(RankSnapshot).filter_by(puuid=p.puuid).order_by(desc(RankSnapshot.snapshot_date)).first()
    if rank:
        out["tier"] = rank.tier
        out["lp"] = rank.lp
    if agg:
        out["soloq"] = {
            "patch": agg.patch, "role": agg.role,
            "games": agg.games_played, "winrate": round((agg.wins / agg.games_played * 100) if agg.games_played else 0, 1),
            "css": round(agg.css_score, 1),
            "percentile": agg.percentile_rank,
            "gd15": round(agg.avg_gd15, 1), "xpd15": round(agg.avg_xpd15, 1), "csd15": round(agg.avg_csd15, 1),
            "cspm": round(agg.avg_cspm, 2), "dmg_share": round(agg.avg_dmg_share, 3),
            "kp": round(agg.avg_kp, 3), "kda": round(agg.avg_kda, 2),
            "vspm": round(agg.avg_vspm, 2),
        }
    return out


def _summarize_pro_player(
    db: Session,
    pro_player_id: str,
    lolesports_role: str,
    *,
    player_name_hint: str | None = None,
    lolpros_name_index: dict | None = None,
    summoner_name_index: dict | None = None,
    pro_puuid_set: set | None = None,
) -> dict:
    """Build a side-by-side-ready summary for a pro: tournament stats + (when
    matched) SoloQ stats.

    Riot-puuid resolution uses 3 strategies (same as the tournament-match
    modal): cached lolesports_id → Lolpros profile name → Riot summoner
    name. The two indexes are built once by the caller and reused per row
    so the whole roster scan stays O(N).
    """
    parts = (
        db.query(OfficialMatchParticipant)
        .filter_by(pro_player_id=pro_player_id, role=lolesports_role)
        .all()
    )
    matches = {}
    if parts:
        match_ids = [r.match_id for r in parts]
        matches = {m.id: m for m in db.query(OfficialMatch).filter(OfficialMatch.id.in_(match_ids)).all()}
    tournament_stats = _aggregate_tournament_stats(parts, matches) if parts else {"games": 0}

    # ---- Resolve riot_puuid via 4 strategies ----
    riot_puuid = None

    # 1. Direct cross-link
    meta = db.query(PlayerMeta).filter_by(lolesports_id=pro_player_id).first()
    if meta:
        riot_puuid = meta.puuid

    # Build candidates from the Lolesports player_name hint (e.g. "Empyros")
    # AND from any in-game summoner_name in the OfficialMatchParticipant rows
    # we just queried (catches cases where lolesports stores "FNC Empyros").
    cand_set: set[str] = set()
    if player_name_hint:
        cand_set.update(_candidates_for_match(player_name_hint))
    for op in parts[:3]:  # only need a couple to seed candidates
        if op.player_name:
            cand_set.update(_candidates_for_match(op.player_name))
        if op.summoner_name:
            cand_set.update(_candidates_for_match(op.summoner_name))

    # 2. Lolpros profile name match → cache the cross-link for next call.
    #    The index stores plain puuids (str) — re-fetch via current
    #    session if we need to update lolesports_id.
    if not riot_puuid and lolpros_name_index:
        for cand in cand_set:
            cached_puuid = lolpros_name_index.get(cand)
            if cached_puuid:
                riot_puuid = cached_puuid
                m = db.get(PlayerMeta, cached_puuid)
                if m and not m.lolesports_id:
                    m.lolesports_id = pro_player_id
                    db.commit()
                break

    # 3. Riot summoner_name match (single-hit only, anti-collision)
    if not riot_puuid and summoner_name_index:
        for cand in cand_set:
            hits_puuids = summoner_name_index.get(cand) or []
            if len(hits_puuids) == 1:
                p_puuid = hits_puuids[0]
                # Persist the cross-link on this player's PlayerMeta
                m = db.get(PlayerMeta, p_puuid)
                if not m:
                    m = PlayerMeta(puuid=p_puuid)
                    db.add(m)
                if not m.lolesports_id:
                    m.lolesports_id = pro_player_id
                    db.commit()
                riot_puuid = p_puuid
                break

    # 4. Multi-hit summoner_name → restrict to is_pro=True. Disambiguates
    #    short pro IGNs (e.g. "Way") that collide with many ladder accounts.
    if not riot_puuid and summoner_name_index and pro_puuid_set:
        for cand in cand_set:
            hits_puuids = summoner_name_index.get(cand) or []
            if len(hits_puuids) <= 1:
                continue
            pro_hits = [puuid for puuid in hits_puuids if puuid in pro_puuid_set]
            if len(pro_hits) == 1:
                p_puuid = pro_hits[0]
                m = db.get(PlayerMeta, p_puuid)
                if not m:
                    m = PlayerMeta(puuid=p_puuid)
                    db.add(m)
                if not m.lolesports_id:
                    m.lolesports_id = pro_player_id
                    db.commit()
                riot_puuid = p_puuid
                break

    soloq = None
    if riot_puuid:
        agg = (
            db.query(PlayerAggregate)
            .filter_by(puuid=riot_puuid)
            .order_by(desc(PlayerAggregate.games_played))
            .first()
        )
        if agg:
            soloq = {
                "patch": agg.patch, "role": agg.role,
                "games": agg.games_played,
                "css": round(agg.css_score, 1),
                "percentile": agg.percentile_rank,
                "gd15": round(agg.avg_gd15, 1),
                "dmg_share": round(agg.avg_dmg_share, 3),
                "kp": round(agg.avg_kp, 3),
                "kda": round(agg.avg_kda, 2),
                "vspm": round(agg.avg_vspm, 2),
            }
    return {"tournament": tournament_stats, "soloq": soloq, "riot_puuid": riot_puuid}


# ============================================================
# /tournament-matches/* — single-match deep-dive
# ============================================================

@match_router.get("/{match_id}")
def tournament_match_detail(match_id: str, db: Session = Depends(get_db)):
    """
    Full roster + per-team stats for a single LEC/ERL match.
    Pulls from our DB only — no extra Riot/lolesports calls. Use the
    /timeline subresource if you want gold curves and events.
    """
    m = db.get(OfficialMatch, match_id)
    if not m:
        raise HTTPException(404, "tournament match not found")

    parts = (
        db.query(OfficialMatchParticipant)
        .filter_by(match_id=match_id)
        .all()
    )

    teams = {t.id: t for t in db.query(ProTeam).filter(ProTeam.id.in_([m.blue_team_id, m.red_team_id])).all()}
    blue = teams.get(m.blue_team_id)
    red = teams.get(m.red_team_id)

    tournament = db.get(Tournament, m.tournament_id) if m.tournament_id else None

    # Group participants by side, compute per-team aggregates
    by_side: dict[str, list[OfficialMatchParticipant]] = {"blue": [], "red": []}
    for p in parts:
        side = (p.side or "").lower()
        if side in by_side:
            by_side[side].append(p)

    def _team_summary(side_parts: list[OfficialMatchParticipant]) -> dict:
        if not side_parts:
            return {}
        return {
            "kills":   sum(p.kills for p in side_parts),
            "deaths":  sum(p.deaths for p in side_parts),
            "assists": sum(p.assists for p in side_parts),
            "gold":    sum(p.gold for p in side_parts),
            "cs":      sum(p.cs for p in side_parts),
            "gd_at_15": sum(p.gd_at_15 for p in side_parts),
            "cs_at_15": sum(p.cs_at_15 for p in side_parts),
        }

    # Pre-built indexes (cached 5 min in module scope). The cache is
    # invalidated by the Leaguepedia/Lolpros/tournament-ingestion endpoints
    # via invalidate_resolution_cache().
    _idx = _get_resolution_indexes(db)
    _lolpros_name_index = _idx["lolpros_name_index"]
    _summoner_name_index = _idx["summoner_name_index"]
    _pro_puuid_set = _idx["pro_puuid_set"]

    # Batch-resolve cached lolesports_id cross-links for ALL participants
    # in one SQL hit (was 10 separate queries — one per participant).
    # NB: bind to a non-`m` variable — `m` already refers to the
    # OfficialMatch loaded at the top of this function and is read again
    # in the response payload below.
    _pro_player_ids = [p.pro_player_id for p in parts if p.pro_player_id]
    _lolesports_id_to_puuid: dict[str, str] = {}
    if _pro_player_ids:
        for _meta in db.query(PlayerMeta).filter(PlayerMeta.lolesports_id.in_(_pro_player_ids)).all():
            _lolesports_id_to_puuid[_meta.lolesports_id] = _meta.puuid

    def _format_participant(p: OfficialMatchParticipant) -> dict:
        # Resolve riot_puuid via 5 progressively looser strategies.
        riot_puuid = None

        # 1. Direct cross-link: PlayerMeta.lolesports_id (set on prior pass).
        if p.pro_player_id:
            riot_puuid = _lolesports_id_to_puuid.get(p.pro_player_id)

        # Build candidate normalizations from BOTH the player_name (e.g.
        # "FNC Razork") AND the in-game summoner_name (e.g. "FNC Razork#xyz").
        # _candidates_for_match strips the team prefix so we get "razork"
        # and other variants — solves the false negatives where the wiki
        # / Lolpros sides only have the bare IGN.
        cand_set: set[str] = set()
        if p.player_name:
            cand_set.update(_candidates_for_match(p.player_name))
        if p.summoner_name:
            cand_set.update(_candidates_for_match(p.summoner_name))

        # 2. Match against cached Lolpros profile names. The Lolpros
        #    profile gives us the cleanest cross-reference (name="Razork").
        #    The cache stores plain puuids (not PlayerMeta) — re-fetch
        #    via current session to read/update lolesports_id.
        if not riot_puuid:
            for cand in cand_set:
                cached_puuid = _lolpros_name_index.get(cand)
                if cached_puuid:
                    riot_puuid = cached_puuid
                    if p.pro_player_id:
                        meta = db.get(PlayerMeta, cached_puuid)
                        if meta and not meta.lolesports_id:
                            meta.lolesports_id = p.pro_player_id
                            db.commit()
                    break

        # 3. Riot summoner_name match (uses pre-built index, fast).
        #    Only accept exactly-one-hit to avoid false positives.
        if not riot_puuid:
            for cand in cand_set:
                hits_puuids = _summoner_name_index.get(cand) or []
                if len(hits_puuids) == 1:
                    riot_puuid = hits_puuids[0]
                    break

        # 4. Multi-hit summoner_name → restrict to known pros. Catches short
        #    pro IGNs like "Way" that collide with many ladder accounts but
        #    where exactly one hit is a tagged pro (is_pro=True via
        #    Leaguepedia/Lolpros sync). Persist the cross-link so subsequent
        #    requests hit strategy 1.
        if not riot_puuid:
            for cand in cand_set:
                hits_puuids = _summoner_name_index.get(cand) or []
                if len(hits_puuids) <= 1:
                    continue
                pro_hits = [puuid for puuid in hits_puuids if puuid in _pro_puuid_set]
                if len(pro_hits) == 1:
                    riot_puuid = pro_hits[0]
                    if p.pro_player_id:
                        m = db.get(PlayerMeta, riot_puuid)
                        if m and not m.lolesports_id:
                            m.lolesports_id = p.pro_player_id
                            db.commit()
                    break

        return {
            "pro_player_id": p.pro_player_id,
            "player_name": p.player_name,
            "summoner_name": p.summoner_name,
            "champion": p.champion,
            "role": p.role,
            "kills": p.kills,
            "deaths": p.deaths,
            "assists": p.assists,
            "kda": round(p.kda, 2),
            "kp": round(p.kill_participation, 3),
            "cs": p.cs,
            "gold": p.gold,
            "level": p.level,
            "gd_at_10": getattr(p, "gd_at_10", 0) or 0,
            "csd_at_10": getattr(p, "csd_at_10", 0) or 0,
            "cs_at_10": getattr(p, "cs_at_10", 0) or 0,
            "gold_at_10": getattr(p, "gold_at_10", 0) or 0,
            "gd_at_15": p.gd_at_15,
            "csd_at_15": p.csd_at_15,
            "cs_at_15": p.cs_at_15,
            "gold_at_15": p.gold_at_15,
            "win": p.win,
            "riot_puuid": riot_puuid,
        }

    return {
        "match_id": match_id,
        "event_id": m.event_id,
        "block_name": m.block_name,
        "patch": m.patch,
        "duration_sec": m.duration_sec,
        "duration_min": (m.duration_sec or 0) // 60,
        "game_date": m.game_date.isoformat() if m.game_date else None,
        "blue_win": m.blue_win,
        "tournament": {
            "id": tournament.id if tournament else None,
            "name": tournament.name if tournament else None,
            "league": tournament.league_slug if tournament else None,
        } if tournament else None,
        "blue_team": {
            "id": m.blue_team_id,
            "code": (blue.code if blue else None),
            "name": (blue.name if blue else None),
            "logo_url": (blue.image_url if blue else None),
            "won": m.blue_win is True,
            "summary": _team_summary(by_side["blue"]),
            "participants": [_format_participant(p) for p in by_side["blue"]],
        },
        "red_team": {
            "id": m.red_team_id,
            "code": (red.code if red else None),
            "name": (red.name if red else None),
            "logo_url": (red.image_url if red else None),
            "won": m.blue_win is False,
            "summary": _team_summary(by_side["red"]),
            "participants": [_format_participant(p) for p in by_side["red"]],
        },
    }


@match_router.get("/{match_id}/timeline")
async def tournament_match_timeline(match_id: str, db: Session = Depends(get_db)):
    """
    Walk the lolesports livestats /window endpoints to reconstruct the gold
    curve + events. Cached 30 min in memory. Returns:
      - gold_curves: per-participant gold over time (10s resolution)
      - team_gold_diff: blue-red team gold delta over time
      - duration_min
    Note: lolesports may rate-limit; if the walk fails partway we return
    whatever we have.
    """
    import time
    from datetime import timedelta as _td
    from ..services.lolesports_client import LolesportsClient, round_to_10s_iso
    from ..services.tournament_ingestion import _parse_iso

    m = db.get(OfficialMatch, match_id)
    if not m:
        raise HTTPException(404, "tournament match not found")
    if not m.game_date:
        raise HTTPException(400, "match has no game_date — cannot fetch timeline")

    cache_key = f"tn:{match_id}"
    if cache_key in _TIMELINE_CACHE:
        payload, expiry = _TIMELINE_CACHE[cache_key]
        if expiry > time.time():
            return payload

    bc_start = m.game_date
    if not bc_start.tzinfo:
        from datetime import timezone as _tz
        bc_start = bc_start.replace(tzinfo=_tz.utc)

    duration = m.duration_sec or 30 * 60
    # Walk windows from broadcast start (+ ~3 min draft buffer) every 10 s of
    # game data — but the API returns 100 s of data per call, so step 100 s.
    step_sec = 100
    blue_total: list[tuple[int, int]] = []
    red_total: list[tuple[int, int]] = []

    async with LolesportsClient() as client:
        cur = bc_start + _td(minutes=3)  # draft+load buffer
        end = bc_start + _td(seconds=duration + 60)
        while cur <= end:
            iso = round_to_10s_iso(cur)
            try:
                window = await client.get_window(match_id, iso)
            except Exception:
                window = None
            if not window or not window.get("frames"):
                cur += _td(seconds=step_sec)
                continue
            for frame in window["frames"]:
                # game time = elapsed from broadcast start in seconds
                ts_iso = frame.get("rfc460Timestamp") or ""
                ts = _parse_iso(ts_iso)
                t_sec = int((ts - bc_start).total_seconds()) if ts else 0
                blue = (frame.get("blueTeam") or {}).get("participants", []) or []
                red  = (frame.get("redTeam")  or {}).get("participants", []) or []
                blue_total.append((t_sec, sum(p.get("totalGold", 0) for p in blue)))
                red_total.append((t_sec, sum(p.get("totalGold", 0) for p in red)))
            cur += _td(seconds=step_sec)

    # Dedupe by t_sec (multiple windows overlap), pick the latest per t
    def _dedupe(rows):
        out = {}
        for t, v in rows:
            out[t] = v
        return sorted(out.items())

    blue_curve = _dedupe(blue_total)
    red_curve = _dedupe(red_total)
    times = sorted(set(t for t, _ in blue_curve) | set(t for t, _ in red_curve))
    blue_dict = dict(blue_curve)
    red_dict = dict(red_curve)
    diff_curve = [(t, blue_dict.get(t, 0) - red_dict.get(t, 0)) for t in times]

    payload = {
        "match_id": match_id,
        "duration_min": duration // 60,
        "minutes": [t // 60 for t in times],
        "blue_gold": [blue_dict.get(t, 0) for t in times],
        "red_gold": [red_dict.get(t, 0) for t in times],
        "gold_diff_blue_minus_red": [d for _, d in diff_curve],
        "samples": len(times),
    }
    _TIMELINE_CACHE[cache_key] = (payload, time.time() + 30 * 60)
    return payload


# Lazy-init shared cache (also used by the SoloQ /matches/{id}/timeline)
_TIMELINE_CACHE: dict = {}


# ============================================================
# /teams/{code} — pro team scouting page
# ============================================================

@team_router.get("/{code}")
def team_detail(code: str, db: Session = Depends(get_db)):
    """Team scouting view: official lolesports record + active roster
    + recent tournament results.

    `code` matches ProTeam.code (e.g. "G2", "FNC", "KC"). Case-insensitive.

    Falls back to a PlayerMeta-only synthetic team when the code isn't
    in our `pro_teams` table — e.g. teams playing in leagues we don't
    sync (Prime League, La Liga of Legends, etc.). The roster still
    renders from PlayerMeta.current_team_tag; recent matches will be
    empty until that league's tournaments are ingested.
    """
    code_norm = code.strip().upper()
    team = (
        db.query(ProTeam)
        .filter(ProTeam.code.ilike(code_norm))
        .order_by(ProTeam.league_slug.asc())  # prefer LEC > sub-leagues if duplicate code
        .first()
    )

    # Fallback: build a synthetic ProTeam-like record from PlayerMeta
    # when no tournament data exists for this team yet.
    if not team:
        sample_meta = (
            db.query(PlayerMeta)
            .filter(PlayerMeta.current_team_tag == code_norm)
            .filter(PlayerMeta.is_pro == True)  # noqa: E712
            .filter(PlayerMeta.is_retired == False)  # noqa: E712
            .first()
        )
        if not sample_meta:
            raise HTTPException(404, f"team not found: {code_norm}")
        # Build a stand-in. ID is None so the recent-matches branch below
        # short-circuits to empty without erroring.
        class _SyntheticTeam:
            id = None
            code = code_norm
            name = sample_meta.current_team or code_norm
            league_slug = None  # unknown — not in our tournament data
            image_url = sample_meta.current_team_logo_url
        team = _SyntheticTeam()

    # --- Recent matches (last 10) ---
    # Skip the SQL when team.id is None (synthetic-team fallback) — there's
    # no point joining OfficialMatch on a non-existent team_id.
    recent_matches = (
        db.query(OfficialMatch)
        .filter((OfficialMatch.blue_team_id == team.id) | (OfficialMatch.red_team_id == team.id))
        .order_by(desc(OfficialMatch.game_date))
        .limit(10)
        .all()
    ) if team.id else []
    other_team_ids = {
        (m.red_team_id if m.blue_team_id == team.id else m.blue_team_id)
        for m in recent_matches
    } - {None}
    other_teams = {
        t.id: t for t in db.query(ProTeam).filter(ProTeam.id.in_(other_team_ids)).all()
    } if other_team_ids else {}
    tournament_ids = {m.tournament_id for m in recent_matches if m.tournament_id}
    tournaments_by_id = {
        t.id: t for t in db.query(Tournament).filter(Tournament.id.in_(tournament_ids)).all()
    } if tournament_ids else {}

    matches_payload = []
    wins = losses = 0
    for m in recent_matches:
        we_blue = m.blue_team_id == team.id
        opp = other_teams.get(m.red_team_id if we_blue else m.blue_team_id)
        won = (we_blue and m.blue_win is True) or (not we_blue and m.blue_win is False)
        if m.blue_win is not None:
            wins += int(won)
            losses += int(not won)
        tour = tournaments_by_id.get(m.tournament_id)
        matches_payload.append({
            "match_id": m.id,
            "game_date": m.game_date.isoformat() if m.game_date else None,
            "league_slug": tour.league_slug if tour else None,
            "tournament_name": tour.name if tour else None,
            "block_name": m.block_name,
            "patch": m.patch,
            "side": "blue" if we_blue else "red",
            "won": won if m.blue_win is not None else None,
            "opponent_code": opp.code if opp else None,
            "opponent_name": opp.name if opp else None,
            "opponent_logo": opp.image_url if opp else None,
        })

    # --- Current roster from PlayerMeta (most reliable: Lolpros-sourced) ---
    # Match by team_tag first (perfect), fall back to team_name. Both
    # name and tag are stored on PlayerMeta.current_team / .current_team_tag.
    roster_q = db.query(PlayerMeta).filter(PlayerMeta.is_pro == True)  # noqa: E712
    if team.code:
        roster_q = roster_q.filter(
            (PlayerMeta.current_team_tag == team.code)
            | (PlayerMeta.current_team == team.name)
        )
    else:
        roster_q = roster_q.filter(PlayerMeta.current_team == team.name)
    metas = roster_q.all()

    roster: list[dict] = []
    if metas:
        # Pull each member's primary aggregate (most-played) for headline CSS
        puuids = [m.puuid for m in metas]
        players_by_puuid = {p.puuid: p for p in db.query(Player).filter(Player.puuid.in_(puuids)).all()}
        agg_by_puuid: dict[str, PlayerAggregate] = {}
        for a in (
            db.query(PlayerAggregate)
            .filter(PlayerAggregate.puuid.in_(puuids))
            .order_by(desc(PlayerAggregate.games_played))
            .all()
        ):
            agg_by_puuid.setdefault(a.puuid, a)

        # Latest rank per member
        latest_ranks: dict[str, "RankSnapshot"] = {}
        from ..models import RankSnapshot
        for r in (
            db.query(RankSnapshot)
            .filter(RankSnapshot.puuid.in_(puuids))
            .order_by(desc(RankSnapshot.snapshot_date))
            .all()
        ):
            latest_ranks.setdefault(r.puuid, r)

        for meta in metas:
            agg = agg_by_puuid.get(meta.puuid)
            rank = latest_ranks.get(meta.puuid)
            p = players_by_puuid.get(meta.puuid)
            roster.append({
                "puuid": meta.puuid,
                "summoner_name": p.summoner_name if p else None,
                "leaguepedia_id": meta.leaguepedia_id,
                "role": meta.role,  # Lolpros-canonical role
                "country": meta.country,
                "age": meta.age,
                "player_image_url": meta.player_image_url,
                "tier": rank.tier if rank else None,
                "lp": rank.lp if rank else None,
                "css": round(agg.css_score, 1) if agg else None,
                "css_role": agg.role if agg else None,
                "games": agg.games_played if agg else 0,
            })
        # Order roster by canonical role: TOP, JGL, MID, ADC, SUP, then unknowns
        ROLE_ORDER = {"Top": 0, "Jungle": 1, "Mid": 2, "Bot": 3, "Support": 4,
                      "TOP": 0, "JGL": 1, "MID": 2, "ADC": 3, "SUP": 4}
        roster.sort(key=lambda r: ROLE_ORDER.get(r.get("role") or "", 99))

    return {
        "team": {
            "id": team.id,
            "code": team.code,
            "name": team.name,
            "league_slug": team.league_slug,
            "logo_url": team.image_url,
        },
        "record_recent": {"wins": wins, "losses": losses, "games": wins + losses},
        "roster": roster,
        "recent_matches": matches_payload,
    }
