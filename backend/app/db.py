from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from .config import settings


def _normalize_url(url: str) -> str:
    """Railway/Heroku give postgres://; SQLAlchemy 2.x needs postgresql+psycopg://."""
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+psycopg://", 1)
    elif url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


_url = _normalize_url(settings.database_url)
_is_sqlite = _url.startswith("sqlite")

_engine_kwargs: dict = {}
if _is_sqlite:
    # SQLite: single-writer/multi-reader. We use a small thread pool plus
    # `check_same_thread=False` so background ingestion jobs don't starve
    # incoming HTTP requests for connections (the symptom we hit during
    # large ingests was the API hanging on /api/health while a job ran).
    _engine_kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
    _engine_kwargs.update(pool_size=10, max_overflow=20, pool_pre_ping=True)
else:
    _engine_kwargs.update(pool_pre_ping=True, pool_size=20, max_overflow=10)

engine = create_engine(_url, **_engine_kwargs)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
