"""
Create the initial admin user.
Usage:
    python seed_admin.py <username> <password> [role=admin] [org=default]
"""
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from app.auth import hash_password
from app.db import Base, SessionLocal, engine
from app.models import User

Base.metadata.create_all(bind=engine)


def main():
    if len(sys.argv) < 3:
        print("Usage: python seed_admin.py <username> <password> [role] [org]")
        sys.exit(1)
    username = sys.argv[1]
    password = sys.argv[2]
    role = sys.argv[3] if len(sys.argv) > 3 else "admin"
    org = sys.argv[4] if len(sys.argv) > 4 else "default"

    db = SessionLocal()
    try:
        existing = db.query(User).filter_by(username=username).first()
        if existing:
            existing.password_hash = hash_password(password)
            existing.role = role
            existing.org = org
            existing.is_active = True
            db.commit()
            print(f"Updated user '{username}' (role={role}, org={org})")
        else:
            u = User(
                username=username,
                password_hash=hash_password(password),
                role=role, org=org,
                created_at=datetime.now(timezone.utc),
                is_active=True,
            )
            db.add(u); db.commit()
            print(f"Created user '{username}' (role={role}, org={org})")
    finally:
        db.close()


if __name__ == "__main__":
    main()
