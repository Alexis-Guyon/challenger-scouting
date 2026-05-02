from fastapi import APIRouter, Depends, Query
from sqlalchemy import desc
from sqlalchemy.orm import Session

from ..auth import get_current_user
from ..db import get_db
from ..models import Player, PlayerAggregate, PlayerMeta, RankSnapshot

router = APIRouter(prefix="/compare", tags=["compare"], dependencies=[Depends(get_current_user)])


@router.get("")
def compare_players(
    puuids: list[str] = Query(..., alias="puuid"),
    role: str | None = None,
    db: Session = Depends(get_db),
):
    out = []
    for puuid in puuids[:5]:
        p = db.get(Player, puuid)
        if not p:
            continue
        q = db.query(PlayerAggregate).filter_by(puuid=puuid)
        if role:
            q = q.filter_by(role=role.upper())
        agg = q.order_by(PlayerAggregate.games_played.desc()).first()
        if not agg:
            continue

        rank = (
            db.query(RankSnapshot)
            .filter_by(puuid=puuid)
            .order_by(desc(RankSnapshot.snapshot_date))
            .first()
        )
        meta = db.get(PlayerMeta, puuid)

        out.append({
            "puuid": puuid,
            "summoner_name": p.summoner_name,
            "role": agg.role,
            "patch": agg.patch,
            "games_played": agg.games_played,
            "winrate": round((agg.wins / agg.games_played * 100) if agg.games_played else 0, 1),
            "css_score": round(agg.css_score, 1),
            "percentile_rank": agg.percentile_rank,
            "pepite_score": round(agg.pepite_score, 1) if agg.pepite_score is not None else None,
            "is_rising_star": bool(agg.is_rising_star),
            "tier": rank.tier if rank else None,
            "lp": rank.lp if rank else None,
            "age": meta.age if meta else None,
            "current_team": meta.current_team if meta else None,
            "current_team_tag": meta.current_team_tag if meta else None,
            "is_pro": bool(meta.is_pro) if meta else False,
            "stats": {
                "gd15": round(agg.avg_gd15, 1),
                "xpd15": round(agg.avg_xpd15, 1),
                "csd15": round(agg.avg_csd15, 1),
                "cspm": round(agg.avg_cspm, 2),
                "dmg_share": round(agg.avg_dmg_share, 3),
                "dpm": round(agg.avg_dpm, 1),
                "kp": round(agg.avg_kp, 3),
                "kda": round(agg.avg_kda, 2),
                "vspm": round(agg.avg_vspm, 2),
                "wpm": round(agg.avg_wpm, 2),
                "solo_kills": round(agg.avg_solo_kills, 2),
                "champion_pool_size": agg.champion_pool_size,
            },
        })
    return out
