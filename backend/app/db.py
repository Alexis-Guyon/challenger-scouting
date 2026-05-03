from sqlalchemy import create_engine, event
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
    # SQLite: single-writer/multi-reader. WAL mode (set per connection
    # below) makes readers non-blocking on writers and vice-versa; combined
    # with the bumped pool size + 60s busy timeout we no longer hit
    # `database is locked` when /admin/recompute and /api/health collide.
    _engine_kwargs["connect_args"] = {"check_same_thread": False, "timeout": 60}
    _engine_kwargs.update(pool_size=10, max_overflow=20, pool_pre_ping=True)
else:
    _engine_kwargs.update(pool_pre_ping=True, pool_size=20, max_overflow=10)

engine = create_engine(_url, **_engine_kwargs)


# ---- SQLite-specific tuning: WAL + 60s busy_timeout + foreign keys ----
# These pragmas must be set per-connection (SQLite resets them between
# connections), so we hook into SQLAlchemy's `connect` event.
if _is_sqlite:
    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        # WAL: writers don't block readers anymore. Persists across the DB
        # file so this only really needs to run once, but we set it on
        # every connect for safety (cheap PRAGMA, no-op if already WAL).
        cursor.execute("PRAGMA journal_mode=WAL")
        # 60 000 ms busy timeout: any write that hits a lock waits up to
        # 60 s for the holder to release before raising OperationalError.
        # The recompute pipeline is heavy; before WAL we'd hit 30s+ lock
        # waits during /admin/recompute even with this set (because the
        # whole DB was journal-locked). With WAL the wait should be near-
        # zero, but we keep the 60s as a safety net.
        cursor.execute("PRAGMA busy_timeout=60000")
        # Synchronous=NORMAL is safe under WAL and cuts fsync overhead
        # roughly in half compared to FULL — recompute writes ~50k rows.
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
