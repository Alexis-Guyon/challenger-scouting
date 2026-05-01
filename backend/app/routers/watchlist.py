from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, HTTPException
from sqlalchemy import desc
from sqlalchemy.orm import Session

from ..auth import get_current_user
from ..db import get_db
from ..models import Player, PlayerAggregate, RankSnapshot, ScoutNote, User, WatchlistEntry

router = APIRouter(tags=["watchlist"], dependencies=[Depends(get_current_user)])


@router.get("/watchlist")
def list_watchlist(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = (
        db.query(WatchlistEntry, Player)
        .join(Player, WatchlistEntry.puuid == Player.puuid)
        .filter(WatchlistEntry.user_id == user.id)
        .order_by(desc(WatchlistEntry.added_at))
        .all()
    )
    out = []
    for w, p in rows:
        agg = (
            db.query(PlayerAggregate)
            .filter_by(puuid=p.puuid)
            .order_by(desc(PlayerAggregate.games_played))
            .first()
        )
        rank = (
            db.query(RankSnapshot)
            .filter_by(puuid=p.puuid)
            .order_by(desc(RankSnapshot.snapshot_date))
            .first()
        )
        out.append({
            "puuid": p.puuid,
            "summoner_name": p.summoner_name,
            "tag": w.tag,
            "added_at": w.added_at.isoformat() if w.added_at else None,
            "tier": rank.tier if rank else None,
            "lp": rank.lp if rank else None,
            "role": agg.role if agg else None,
            "css_score": round(agg.css_score, 1) if agg else None,
            "percentile_rank": agg.percentile_rank if agg else None,
            "games_played": agg.games_played if agg else 0,
        })
    return out


@router.post("/watchlist")
def add_watchlist(
    puuid: str = Form(...),
    tag: str = Form(""),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not db.get(Player, puuid):
        raise HTTPException(status_code=404, detail="player not found")
    existing = db.query(WatchlistEntry).filter_by(user_id=user.id, puuid=puuid).first()
    if existing:
        existing.tag = tag
    else:
        existing = WatchlistEntry(
            user_id=user.id, puuid=puuid, tag=tag,
            added_at=datetime.now(timezone.utc),
        )
        db.add(existing)
    db.commit()
    return {"ok": True, "puuid": puuid, "tag": tag}


@router.delete("/watchlist/{puuid}")
def remove_watchlist(
    puuid: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    db.query(WatchlistEntry).filter_by(user_id=user.id, puuid=puuid).delete()
    db.commit()
    return {"ok": True}


@router.get("/notes/{puuid}")
def list_notes(
    puuid: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rows = (
        db.query(ScoutNote)
        .filter_by(user_id=user.id, puuid=puuid)
        .order_by(desc(ScoutNote.created_at))
        .all()
    )
    return [
        {
            "id": n.id, "content": n.content,
            "created_at": n.created_at.isoformat() if n.created_at else None,
        }
        for n in rows
    ]


@router.post("/notes/{puuid}")
def add_note(
    puuid: str,
    content: str = Form(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not db.get(Player, puuid):
        raise HTTPException(status_code=404, detail="player not found")
    n = ScoutNote(
        user_id=user.id, puuid=puuid, content=content,
        created_at=datetime.now(timezone.utc),
    )
    db.add(n); db.commit(); db.refresh(n)
    return {"id": n.id, "content": n.content, "created_at": n.created_at.isoformat()}


@router.delete("/notes/{note_id}")
def delete_note(
    note_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    n = db.query(ScoutNote).filter_by(id=note_id, user_id=user.id).first()
    if not n:
        raise HTTPException(status_code=404, detail="note not found")
    db.delete(n); db.commit()
    return {"ok": True}
