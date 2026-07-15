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
    # Riot summoner-v4 profileIconId → account portrait in ladder + profile
    ("players", "profile_icon_id", "INTEGER"),
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
    # Pépite composite score (0..100) + breakdown JSON for UI tooltips
    ("player_aggregates", "pepite_score", "FLOAT DEFAULT 0"),
    ("player_aggregates", "pepite_breakdown_json", "TEXT"),
    # Tournament @10-min splits (added alongside existing @15)
    ("official_match_participants", "gd_at_10", "INTEGER DEFAULT 0"),
    ("official_match_participants", "xpd_at_10", "INTEGER DEFAULT 0"),
    ("official_match_participants", "csd_at_10", "INTEGER DEFAULT 0"),
    ("official_match_participants", "gold_at_10", "INTEGER DEFAULT 0"),
    ("official_match_participants", "cs_at_10", "INTEGER DEFAULT 0"),
    # Leaguepedia-enriched player fields (real name + socials + fav champs)
    ("player_meta", "real_name", "VARCHAR"),
    ("player_meta", "alt_names", "VARCHAR"),
    ("player_meta", "fav_champions", "TEXT"),
    ("player_meta", "twitter_handle", "VARCHAR"),
    ("player_meta", "twitch_url", "VARCHAR"),
    ("player_meta", "instagram_handle", "VARCHAR"),
    ("player_meta", "youtube_url", "VARCHAR"),
    ("player_meta", "tiktok_handle", "VARCHAR"),
    # Recruitment kanban — pipeline stage on each watchlist entry
    ("watchlist", "stage", "VARCHAR DEFAULT 'watch'"),
    ("watchlist", "stage_changed_at", "DATETIME"),
    # Track tournament games where lolesports livestats had no frame data
    ("official_matches", "data_complete", "BOOLEAN DEFAULT 1"),
]


# Composite / covering indexes that make the leaderboard (/players) and
# /champions endpoints fast. Single-column indexes on these tables already
# exist via the models; these cover the specific multi-column access patterns
# those two hot endpoints hit. Idempotent (CREATE INDEX IF NOT EXISTS).
PERF_INDEXES = [
    # /players: the per-puuid "primary aggregate" subquery filters on
    # (games_played, role, patch) then groups by puuid picking max(games,id).
    ("ix_pa_games", "player_aggregates", "(games_played)"),
    ("ix_pa_role_patch_games", "player_aggregates", "(role, patch, games_played)"),
    ("ix_pa_puuid_games_id", "player_aggregates", "(puuid, games_played, id)"),
    # /players: correlated EXISTS on a ranked tier — (puuid, tier) lets it be
    # an index-only probe instead of a per-row snapshot scan.
    ("ix_rs_puuid_tier", "rank_snapshots", "(puuid, tier)"),
    # /champions: GROUP BY champion_id, champion_name, role with
    # COUNT(DISTINCT puuid) + SUM(...). This covering index (ordered by the
    # GROUP BY key, carrying every summed column) lets SQLite stream the
    # aggregation index-only — cuts the endpoint from ~14s to sub-second.
    ("ix_cp_cover", "champion_pool",
     "(champion_id, champion_name, role, puuid, games, wins, avg_kda, "
     "champion_css, has_champion_baseline, patch)"),
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

        # Performance indexes (idempotent)
        for name, table, cols in PERF_INDEXES:
            if not insp.has_table(table):
                print(f"  skip index {name} — {table} not present")
                continue
            print(f"  idx  {name} ON {table} {cols}")
            conn.execute(text(f"CREATE INDEX IF NOT EXISTS {name} ON {table} {cols}"))

    # Refresh planner statistics so SQLite picks good join orders for the
    # multi-subquery leaderboard query. Cheap and safe to re-run.
    with engine.begin() as conn:
        print("  analyze …")
        conn.execute(text("ANALYZE"))
    print("Done.")


if __name__ == "__main__":
    main()
