import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from sqlalchemy.orm import Session

from ..auth import get_current_user, hash_password, issue_token, require_admin, verify_password
from ..db import get_db
from ..models import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login")
def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    # Log every login attempt with IP — helps detect brute-force / probing
    # from unauthorized IPs (the tool is internal-only so any unfamiliar
    # IP hitting /auth/login is worth investigating).
    client_ip = request.client.host if request.client else "?"
    user = db.query(User).filter_by(username=username).first()
    if not user or not user.is_active or not verify_password(password, user.password_hash):
        logger.warning("auth: FAILED login attempt for username=%r from ip=%s", username, client_ip)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid credentials")
    logger.info("auth: successful login user=%s from ip=%s", user.username, client_ip)
    token = issue_token(user)
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {
            "id": user.id, "username": user.username,
            "role": user.role, "org": user.org,
        },
    }


@router.get("/me")
def me(user: User = Depends(get_current_user)):
    return {"id": user.id, "username": user.username, "role": user.role, "org": user.org}


@router.post("/users")
def create_user(
    username: str = Form(...),
    password: str = Form(...),
    role: str = Form("analyst"),
    org: str = Form("default"),
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    if db.query(User).filter_by(username=username).first():
        raise HTTPException(status_code=400, detail="username already exists")
    if role not in ("admin", "analyst"):
        raise HTTPException(status_code=400, detail="role must be admin or analyst")
    u = User(
        username=username, password_hash=hash_password(password),
        role=role, org=org, created_at=datetime.now(timezone.utc),
        is_active=True,
    )
    db.add(u); db.commit(); db.refresh(u)
    return {"id": u.id, "username": u.username, "role": u.role, "org": u.org}
