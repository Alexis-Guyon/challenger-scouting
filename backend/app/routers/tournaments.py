"""
Tournament stats endpoints + LEC roster comparison.
"""
import re
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
from ..services.scoring import compute_css_for_aggregate

router = APIRouter(tags=["tournaments"], dependencies=[Depends(get_current_user)])


# Cross-mapping from LP/Riot role to lolesports role
ROLE_MAP_TO_LOLESPORTS = {
    "TOP": "top",
    "JGL": "jungle",
    "MID": "mid",
    "ADC": "bottom",
    "SUP": "support",
}
ROLE_MAP_FROM_LOLESPORTS = {v: k for k, v in ROLE_MAP_TO_LOLESPORTS.items()}


def _normalize_for_match(s: str | None) -> str:
    if not s:
        return ""
    s = s.split("#")[0]
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _candidates_for_match(s: str | None) -> list[str]:
    """Multiple normalized forms — same logic as Leaguepedia matcher (strip team prefix + suffix)."""
    if not s:
        return []
    base = s.split("#")[0].strip()
    out: list[str] = []
    seen: set[str] = set()

    def push(x: str):
        n = re.sub(r"[^a-z0-9]", "", x.lower())
        if n and n not in seen:
            seen.add(n)
            out.append(n)

    push(base)
    push(re.sub(r"^(twtv|trainer|coach|sub)\s+", "", base, flags=re.I).strip())
    m = re.match(r"^([A-Z0-9]{1,5})\s+(.+)$", base)
    if m:
        push(m.group(2))
        push(m.group(2).split(" ")[-1])
    no_suffix = re.sub(r"\s+(NEXT|academy|smurf|alt|main|\d+)$", "", base, flags=re.I).strip()
    if no_suffix != base:
        push(no_suffix)
    if m:
        cleaned = re.sub(r"\s+(NEXT|academy|smurf|alt|main|\d+)$", "", m.group(2), flags=re.I).strip()
        push(cleaned)
        push(cleaned.split(" ")[-1])
    parts = base.split(" ")
    if len(parts) > 1:
        push(parts[-1])
    return out


def _resolve_pro_player_id(db: Session, player: Player, meta: PlayerMeta | None) -> str | None:
    """Find lolesports pro_player_id for a Riot player. Cached on PlayerMeta.lolesports_id."""
    if meta and meta.lolesports_id:
        return meta.lolesports_id

    candidates = _candidates_for_match(player.summoner_name)
    if not candidates:
        return None
    cand_set = set(candidates)

    rows = (
        db.query(OfficialMatchParticipant)
        .filter(
            (OfficialMatchParticipant.summoner_name.isnot(None))
            & (OfficialMatchParticipant.summoner_name != "")
        )
        .all()
    )
    for c in rows:
        sname = _normalize_for_match(c.summoner_name)
        pname = _normalize_for_match(c.player_name)
        if sname in cand_set or pname in cand_set:
            if meta:
                meta.lolesports_id = c.pro_player_id
                db.commit()
            return c.pro_player_id
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
        })

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

    pro_entries = []
    for r in roster:
        pro_data = _summarize_pro_player(db, r.pro_player_id, lolesports_role)
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


def _summarize_pro_player(db: Session, pro_player_id: str, lolesports_role: str) -> dict:
    """Build a side-by-side-ready summary for a pro: tournament stats + (when matched) SoloQ stats."""
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

    # Try to find Riot puuid via PlayerMeta.lolesports_id
    meta = db.query(PlayerMeta).filter_by(lolesports_id=pro_player_id).first()
    soloq = None
    if meta:
        agg = (
            db.query(PlayerAggregate)
            .filter_by(puuid=meta.puuid)
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
    return {"tournament": tournament_stats, "soloq": soloq}
