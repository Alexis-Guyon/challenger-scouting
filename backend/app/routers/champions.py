"""
Champion-specific scouting endpoints.

  GET /champions                — list every champion that has at least one
                                  ChampionPool row in the DB, with simple
                                  meta-stats (total games, distinct mains, etc.)
  GET /champions/{champion_id}  — leaderboard of best players ON that champion
                                  (ordered by champion_css desc), with the
                                  same role/patch/min_games filters as /players.

These let scouts answer "give me the top 10 ADCs on Kaisa right now" instead
of just "top 10 ADCs in general".
"""
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, func
from sqlalchemy.orm import Session

from ..auth import get_current_user
from ..db import get_db
from ..models import ChampionPool, Player, PlayerAggregate, PlayerMeta, RankSnapshot

router = APIRouter(prefix="/champions", tags=["champions"], dependencies=[Depends(get_current_user)])


@router.get("")
def list_champions(
    role: str | None = Query(default=None, description="Filter by role (TOP/JGL/MID/ADC/SUP)"),
    patch: str | None = Query(default=None, description="Patch filter, e.g. '16.9'"),
    min_total_games: int = Query(default=20),
    db: Session = Depends(get_db),
):
    """
    Return every champion played in our DB, with aggregate stats. One row per
    (champion_id, role, patch).
    """
    q = db.query(ChampionPool)
    if role:
        q = q.filter(ChampionPool.role == role.upper())
    if patch:
        q = q.filter(ChampionPool.patch == patch)
    rows = q.all()

    by_key: dict[tuple, list[ChampionPool]] = defaultdict(list)
    for cp in rows:
        if not cp.champion_name:
            continue
        by_key[(cp.champion_id, cp.champion_name, cp.role, cp.patch)].append(cp)

    out = []
    for (cid, name, r, p), items in by_key.items():
        total_games = sum(it.games for it in items)
        if total_games < min_total_games:
            continue
        total_wins = sum(it.wins for it in items)
        avg_kda = sum(it.avg_kda * it.games for it in items) / max(total_games, 1)
        n_baselined = sum(1 for it in items if getattr(it, "has_champion_baseline", False))
        n_with_score = sum(1 for it in items if getattr(it, "champion_css", 0))
        if n_with_score:
            avg_champ_css = sum(getattr(it, "champion_css", 0) for it in items) / n_with_score
        else:
            avg_champ_css = 0
        out.append({
            "champion_id": cid,
            "champion_name": name,
            "role": r,
            "patch": p,
            "total_mains": len(items),
            "total_games": total_games,
            "winrate": round(total_wins / total_games * 100, 1) if total_games else 0,
            "avg_kda": round(avg_kda, 2),
            "baselined": n_baselined > 0,
            "avg_champ_css": round(avg_champ_css, 1),
        })

    out.sort(key=lambda x: (-x["total_games"], x["champion_name"]))
    return out


@router.get("/{champion_id}")
def champion_leaderboard(
    champion_id: int,
    role: str | None = Query(default=None),
    patch: str | None = Query(default=None),
    min_games: int = Query(default=3, ge=1),
    limit: int = Query(default=50, ge=1, le=200),
    pro_only: bool = Query(default=False),
    db: Session = Depends(get_db),
):
    """
    Top players on a specific champion, ordered by champion-CSS desc.
    Joins ChampionPool with Player + latest rank + Lolpros meta so the row
    can render the same way as the main leaderboard.
    """
    q = db.query(ChampionPool, Player).join(Player, ChampionPool.puuid == Player.puuid)
    q = q.filter(ChampionPool.champion_id == champion_id)
    q = q.filter(ChampionPool.games >= min_games)
    if role:
        q = q.filter(ChampionPool.role == role.upper())
    if patch:
        q = q.filter(ChampionPool.patch == patch)
    if pro_only:
        q = q.outerjoin(PlayerMeta, PlayerMeta.puuid == Player.puuid)
        q = q.filter(PlayerMeta.is_pro == True)  # noqa: E712

    # Order by champion_css desc when available, else by games_played desc.
    q = q.order_by(desc(ChampionPool.champion_css), desc(ChampionPool.games))
    rows = q.limit(limit * 2).all()

    if not rows:
        return {"champion_id": champion_id, "items": []}

    champion_name = rows[0][0].champion_name

    # Build player meta lookup for the rows we kept
    puuids = [p.puuid for _, p in rows]
    metas = {
        m.puuid: m
        for m in db.query(PlayerMeta).filter(PlayerMeta.puuid.in_(puuids)).all()
    }
    ranks: dict[str, RankSnapshot] = {}
    for r in db.query(RankSnapshot).filter(RankSnapshot.puuid.in_(puuids)).order_by(desc(RankSnapshot.snapshot_date)).all():
        ranks.setdefault(r.puuid, r)

    items = []
    for cp, p in rows[:limit]:
        meta = metas.get(p.puuid)
        rank = ranks.get(p.puuid)
        meta_payload = None
        if meta and meta.is_pro:
            meta_payload = {
                "current_team": meta.current_team,
                "current_team_tag": meta.current_team_tag,
                "current_team_logo_url": (meta.current_team_logo_url or "").replace("http://", "https://") or None,
                "is_fa": (meta.current_team or "") == "" and not meta.is_retired,
                "country": meta.country,
                "age": meta.age,
            }
        items.append({
            "puuid": p.puuid,
            "summoner_name": p.summoner_name,
            "tier": rank.tier if rank else None,
            "lp": rank.lp if rank else None,
            "role": cp.role,
            "patch": cp.patch,
            "games": cp.games,
            "wins": cp.wins,
            "winrate": round(cp.wins / cp.games * 100, 1) if cp.games else 0,
            "avg_kda": round(cp.avg_kda, 2),
            "avg_dmg_share": round(cp.avg_dmg_share, 3),
            "champion_css": round(getattr(cp, "champion_css", 0) or 0, 1),
            "has_champion_baseline": bool(getattr(cp, "has_champion_baseline", False)),
            "meta": meta_payload,
        })
    return {
        "champion_id": champion_id,
        "champion_name": champion_name,
        "total": len(rows),
        "items": items,
    }
