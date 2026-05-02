"""Smurf-detection labeling + retrain endpoints.

The smurf logistic regression in services/smurf_ml.py needs ground
truth to be useful. This router lets a logged-in scout flag suspect
players from the profile UI; labels are stored in the SmurfLabel
table and consumed by the next /admin/smurf/retrain run.
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..auth import get_current_user
from ..db import get_db
from ..models import Player, SmurfLabel, User

router = APIRouter(prefix="/smurf", tags=["smurf"], dependencies=[Depends(get_current_user)])


@router.post("/label/{puuid}")
def upsert_label(
    puuid: str,
    label: bool = True,
    note: str | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Mark a player as a smurf (label=true) or explicitly NOT a smurf
    (label=false). One label per (player, scout)."""
    if not db.get(Player, puuid):
        raise HTTPException(404, "player not found")
    existing = (
        db.query(SmurfLabel)
        .filter_by(puuid=puuid, user_id=user.id)
        .first()
    )
    if existing:
        existing.label = bool(label)
        existing.note = note
    else:
        db.add(SmurfLabel(
            puuid=puuid,
            user_id=user.id,
            label=bool(label),
            note=note,
            created_at=datetime.now(timezone.utc),
        ))
    db.commit()
    return {"puuid": puuid, "label": bool(label)}


@router.delete("/label/{puuid}")
def delete_label(
    puuid: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    n = (
        db.query(SmurfLabel)
        .filter_by(puuid=puuid, user_id=user.id)
        .delete()
    )
    db.commit()
    return {"deleted": n}


@router.get("/label/{puuid}")
def get_label(
    puuid: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return the current scout's label for this player (if any) plus an
    aggregate count across all scouts (cross-team consensus signal)."""
    mine = (
        db.query(SmurfLabel)
        .filter_by(puuid=puuid, user_id=user.id)
        .first()
    )
    yes = db.query(SmurfLabel).filter_by(puuid=puuid, label=True).count()
    no = db.query(SmurfLabel).filter_by(puuid=puuid, label=False).count()
    return {
        "mine": bool(mine.label) if mine else None,
        "mine_note": mine.note if mine else None,
        "votes_yes": yes,
        "votes_no": no,
    }


@router.get("/labels")
def list_my_labels(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """All labels the current scout has set (for a personal label history view)."""
    rows = (
        db.query(SmurfLabel, Player)
        .join(Player, SmurfLabel.puuid == Player.puuid)
        .filter(SmurfLabel.user_id == user.id)
        .order_by(SmurfLabel.created_at.desc())
        .all()
    )
    return {
        "labels": [
            {
                "puuid": l.puuid,
                "summoner_name": p.summoner_name,
                "label": bool(l.label),
                "note": l.note,
                "created_at": l.created_at.isoformat() if l.created_at else None,
            }
            for l, p in rows
        ]
    }
