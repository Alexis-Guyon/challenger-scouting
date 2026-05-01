from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, HTTPException, status
from sqlalchemy.orm import Session

from ..auth import get_current_user, hash_password, issue_token, require_admin, verify_password
from ..db import get_db
from ..models import User

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login")
def login(
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    user = db.query(User).filter_by(username=username).first()
    if not user or not user.is_active or not verify_password(password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid credentials")
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
