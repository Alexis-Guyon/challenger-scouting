"""
Tiny ad-hoc migration script for SQLite + Postgres. Adds new columns to
existing tables that have evolved since previous deployments. Idempotent.

Run after model changes (from the `backend/` directory):
    python scripts/migrate.py
"""
import sys
from pathlib import Path

# This script lives in backend/scripts/ and needs to import from the `app/`
# package one level up.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import inspect, text

from app.db import Base, engine


# (table, column_name, ddl_type)
# Both SQLite and Postgres accept VARCHAR / INTEGER / FLOAT.
NEW_COLUMNS = [
    ("player_meta", "lolesports_id", "VARCHAR"),
    ("matches", "avg_lobby_lp", "INTEGER DEFAULT 0"),
    # Smurf detector ML/rule-based score
    ("players", "smurf_score", "FLOAT DEFAULT 0.0"),
    ("players", "smurf_signals", "TEXT"),
    # Champion-specific CSS
    ("champion_pool", "role", "VARCHAR"),
    ("champion_pool", "avg_kp", "FLOAT DEFAULT 0.0"),
    ("champion_pool", "avg_gd15", "FLOAT DEFAULT 0.0"),
    ("champion_pool", "avg_csd15", "FLOAT DEFAULT 0.0"),
    ("champion_pool", "avg_dpm", "FLOAT DEFAULT 0.0"),
    ("champion_pool", "champion_css", "FLOAT DEFAULT 0.0"),
    ("champion_pool", "has_champion_baseline", "BOOLEAN DEFAULT FALSE"),
    # Team identity (logo for badge rendering)
    ("player_meta", "current_team_tag", "VARCHAR"),
    ("player_meta", "current_team_logo_url", "VARCHAR"),
    # Lolpros full profile cache (social media, previous teams, peak/seasons)
    ("player_meta", "lolpros_slug", "VARCHAR"),
    ("player_meta", "lolpros_profile_json", "TEXT"),
    # Leaguepedia headshot (Special:FilePath URL)
    ("player_meta", "player_image_url", "VARCHAR"),
    # Rising-star tag (sustained CSS uptrend across N snapshots)
    ("player_aggregates", "is_rising_star", "BOOLEAN DEFAULT 0"),
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
