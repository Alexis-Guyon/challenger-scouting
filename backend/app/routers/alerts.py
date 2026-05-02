"""Alert rules CRUD + history (per-user Discord webhooks)."""
import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import desc
from sqlalchemy.orm import Session

from ..auth import get_current_user
from ..db import get_db
from ..models import AlertHistory, AlertRule, User

router = APIRouter(prefix="/alerts", tags=["alerts"], dependencies=[Depends(get_current_user)])


class RuleIn(BaseModel):
    name: str
    webhook_url: str
    conditions: dict
    enabled: bool = True


class RuleUpdate(BaseModel):
    name: str | None = None
    webhook_url: str | None = None
    conditions: dict | None = None
    enabled: bool | None = None


def _serialize_rule(rule: AlertRule) -> dict:
    try:
        conditions = json.loads(rule.conditions_json or "{}")
    except Exception:
        conditions = {}
    return {
        "id": rule.id,
        "name": rule.name,
        "webhook_url": rule.webhook_url,
        "conditions": conditions,
        "enabled": bool(rule.enabled),
        "created_at": rule.created_at.isoformat() if rule.created_at else None,
        "last_fired_at": rule.last_fired_at.isoformat() if rule.last_fired_at else None,
    }


@router.get("/rules")
def list_rules(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rules = (
        db.query(AlertRule)
        .filter_by(user_id=user.id)
        .order_by(AlertRule.id.desc())
        .all()
    )
    return {"rules": [_serialize_rule(r) for r in rules]}


@router.post("/rules")
def create_rule(payload: RuleIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rule = AlertRule(
        user_id=user.id,
        name=payload.name,
        webhook_url=payload.webhook_url,
        conditions_json=json.dumps(payload.conditions or {}),
        enabled=payload.enabled,
        created_at=datetime.now(timezone.utc),
    )
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return _serialize_rule(rule)


@router.patch("/rules/{rule_id}")
def update_rule(rule_id: int, payload: RuleUpdate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rule = db.get(AlertRule, rule_id)
    if not rule or rule.user_id != user.id:
        raise HTTPException(404, "rule not found")
    if payload.name is not None:
        rule.name = payload.name
    if payload.webhook_url is not None:
        rule.webhook_url = payload.webhook_url
    if payload.conditions is not None:
        rule.conditions_json = json.dumps(payload.conditions)
    if payload.enabled is not None:
        rule.enabled = payload.enabled
    db.commit()
    return _serialize_rule(rule)


@router.delete("/rules/{rule_id}")
def delete_rule(rule_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rule = db.get(AlertRule, rule_id)
    if not rule or rule.user_id != user.id:
        raise HTTPException(404, "rule not found")
    db.delete(rule)
    db.commit()
    return {"deleted": rule_id}


@router.post("/rules/{rule_id}/test")
def test_rule(rule_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Send a one-off ping to the rule's webhook to verify it's reachable."""
    from ..services.alerts import _post_webhook
    rule = db.get(AlertRule, rule_id)
    if not rule or rule.user_id != user.id:
        raise HTTPException(404, "rule not found")
    ok, err = _post_webhook(
        rule.webhook_url,
        f"✅ **Test alert** — rule **{rule.name}** is wired up.",
    )
    return {"delivered": ok, "error": err}


@router.get("/history")
def history(
    limit: int = 30,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Recent fired alerts for THIS user's rules."""
    rows = (
        db.query(AlertHistory, AlertRule)
        .join(AlertRule, AlertHistory.rule_id == AlertRule.id)
        .filter(AlertRule.user_id == user.id)
        .order_by(desc(AlertHistory.fired_at))
        .limit(limit)
        .all()
    )
    out = []
    for h, rule in rows:
        try:
            payload = json.loads(h.payload_json or "{}")
        except Exception:
            payload = {}
        out.append({
            "id": h.id,
            "rule_id": h.rule_id,
            "rule_name": rule.name,
            "matches": payload.get("matches"),
            "delivered": bool(h.delivered),
            "error": h.error,
            "fired_at": h.fired_at.isoformat() if h.fired_at else None,
        })
    return {"history": out}
