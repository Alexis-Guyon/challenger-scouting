"""
Tiny ad-hoc migration script for SQLite. Adds new columns to existing tables
that have evolved since previous deployments. Idempotent.

Run after model changes:
    python migrate.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from sqlalchemy import inspect, text

from app.db import Base, engine


# (table, column_name, sqlite_type, postgres_type)
# We send only the type string after ADD COLUMN; this works on both backends.
NEW_COLUMNS = [
    ("player_meta", "lolesports_id", "VARCHAR"),
    ("matches", "avg_lobby_lp", "INTEGER DEFAULT 0"),
]


def main():
    insp = inspect(engine)
    # Make sure newly-defined tables exist
    Base.metadata.create_all(bind=engine)

    with engine.begin() as conn:
        for table, col, ddl in NEW_COLUMNS:
            if not insp.has_table(table):
                print(f"  skip {table}.{col} — table not present")
                continue
            existing = {c["name"] for c in insp.get_columns(table)}
            if col in existing:
                print(f"  ok   {table}.{col} already exists")
                continue
            print(f"  add  {table}.{col} {ddl}")
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}"))
    print("Done.")


if __name__ == "__main__":
    main()
