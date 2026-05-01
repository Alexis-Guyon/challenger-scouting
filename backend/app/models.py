from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Index,
)
from sqlalchemy.orm import relationship

from .db import Base


class Player(Base):
    __tablename__ = "players"
    puuid = Column(String, primary_key=True)
    summoner_id = Column(String, index=True)
    summoner_name = Column(String, index=True)
    region = Column(String(8), index=True)
    main_role = Column(String(8), index=True, nullable=True)
    account_level = Column(Integer, default=0)
    total_games_lifetime = Column(Integer, default=0)
    smurf_flag = Column(Boolean, default=False)
    last_updated = Column(DateTime, nullable=True)

    ranks = relationship("RankSnapshot", back_populates="player", cascade="all, delete-orphan")
    participations = relationship("MatchParticipant", back_populates="player")
    aggregates = relationship("PlayerAggregate", back_populates="player", cascade="all, delete-orphan")


class RankSnapshot(Base):
    __tablename__ = "rank_snapshots"
    id = Column(Integer, primary_key=True, autoincrement=True)
    puuid = Column(String, ForeignKey("players.puuid"), index=True)
    tier = Column(String, index=True)
    rank = Column(String)
    lp = Column(Integer)
    wins = Column(Integer)
    losses = Column(Integer)
    snapshot_date = Column(DateTime)

    player = relationship("Player", back_populates="ranks")


class Match(Base):
    __tablename__ = "matches"
    match_id = Column(String, primary_key=True)
    region = Column(String, index=True)
    patch = Column(String, index=True)
    game_creation = Column(DateTime, index=True)
    game_duration_sec = Column(Integer)
    queue_id = Column(Integer, index=True)
    blue_win = Column(Boolean)
    avg_lobby_lp = Column(Integer, default=0)

    participants = relationship("MatchParticipant", back_populates="match", cascade="all, delete-orphan")


class MatchParticipant(Base):
    __tablename__ = "match_participants"
    id = Column(Integer, primary_key=True, autoincrement=True)
    match_id = Column(String, ForeignKey("matches.match_id"), index=True)
    puuid = Column(String, ForeignKey("players.puuid"), index=True)
    team_id = Column(Integer)
    role = Column(String, index=True)  # TOP / JGL / MID / ADC / SUP
    champion_id = Column(Integer)
    champion_name = Column(String)
    win = Column(Boolean)

    # Raw stats
    kills = Column(Integer, default=0)
    deaths = Column(Integer, default=0)
    assists = Column(Integer, default=0)
    cs_total = Column(Integer, default=0)
    gold_earned = Column(Integer, default=0)
    damage_to_champs = Column(Integer, default=0)
    damage_taken = Column(Integer, default=0)
    vision_score = Column(Integer, default=0)
    wards_placed = Column(Integer, default=0)
    wards_killed = Column(Integer, default=0)
    control_wards = Column(Integer, default=0)
    solo_kills = Column(Integer, default=0)
    objective_dmg = Column(Integer, default=0)
    dragon_kills = Column(Integer, default=0)
    baron_kills = Column(Integer, default=0)
    turret_kills = Column(Integer, default=0)

    # Derived from timeline
    gd_at_15 = Column(Integer, default=0)
    xpd_at_15 = Column(Integer, default=0)
    csd_at_15 = Column(Integer, default=0)
    cs_at_10 = Column(Integer, default=0)
    cs_at_15 = Column(Integer, default=0)
    early_deaths = Column(Integer, default=0)  # deaths before 14:00

    # Computed shares
    damage_share = Column(Float, default=0.0)
    kill_participation = Column(Float, default=0.0)
    kda = Column(Float, default=0.0)

    match = relationship("Match", back_populates="participants")
    player = relationship("Player", back_populates="participations")

    __table_args__ = (
        UniqueConstraint("match_id", "puuid", name="uq_match_player"),
        Index("ix_role_patch", "role"),
    )


class PlayerAggregate(Base):
    __tablename__ = "player_aggregates"
    id = Column(Integer, primary_key=True, autoincrement=True)
    puuid = Column(String, ForeignKey("players.puuid"), index=True)
    patch = Column(String, index=True)
    role = Column(String, index=True)
    games_played = Column(Integer, default=0)
    wins = Column(Integer, default=0)

    avg_gd15 = Column(Float, default=0)
    avg_xpd15 = Column(Float, default=0)
    avg_csd15 = Column(Float, default=0)
    avg_cspm = Column(Float, default=0)
    avg_dmg_share = Column(Float, default=0)
    avg_dpm = Column(Float, default=0)
    avg_kp = Column(Float, default=0)
    avg_kda = Column(Float, default=0)
    avg_vspm = Column(Float, default=0)
    avg_wpm = Column(Float, default=0)
    avg_wcpm = Column(Float, default=0)
    avg_solo_kills = Column(Float, default=0)
    avg_objective_dmg = Column(Float, default=0)
    avg_early_deaths = Column(Float, default=0)
    avg_deaths = Column(Float, default=0)

    # Variance for consistency
    std_gd15 = Column(Float, default=0)
    std_dmg_share = Column(Float, default=0)
    std_kp = Column(Float, default=0)

    # Champion pool
    champion_pool_size = Column(Integer, default=0)

    # Score
    css_score = Column(Float, default=0)
    css_raw = Column(Float, default=0)
    percentile_rank = Column(Float, default=0)

    player = relationship("Player", back_populates="aggregates")

    __table_args__ = (
        UniqueConstraint("puuid", "patch", "role", name="uq_player_patch_role"),
    )


class ChampionPool(Base):
    __tablename__ = "champion_pool"
    id = Column(Integer, primary_key=True, autoincrement=True)
    puuid = Column(String, ForeignKey("players.puuid"), index=True)
    patch = Column(String, index=True)
    champion_id = Column(Integer)
    champion_name = Column(String)
    games = Column(Integer, default=0)
    wins = Column(Integer, default=0)
    avg_kda = Column(Float, default=0)
    avg_dmg_share = Column(Float, default=0)


class Tournament(Base):
    __tablename__ = "tournaments"
    id = Column(String, primary_key=True)         # lolesports tournament/event id
    league_id = Column(String, index=True)
    league_slug = Column(String, index=True)      # "lec", "lfl", "prime_league", ...
    league_name = Column(String)
    name = Column(String)                         # split / season name
    region = Column(String, index=True)
    start_date = Column(DateTime)
    end_date = Column(DateTime)
    last_synced = Column(DateTime)


class ProTeam(Base):
    __tablename__ = "pro_teams"
    id = Column(String, primary_key=True)
    code = Column(String, index=True)             # G2, FNC, MAD, ...
    name = Column(String)
    league_slug = Column(String, index=True)
    image_url = Column(String)


class OfficialMatch(Base):
    __tablename__ = "official_matches"
    id = Column(String, primary_key=True)         # lolesports gameId
    event_id = Column(String, index=True)         # parent match (Bo3/Bo5)
    tournament_id = Column(String, ForeignKey("tournaments.id"), index=True)
    block_name = Column(String)                   # "Week 1", "Playoffs - Round 1", …
    blue_team_id = Column(String, index=True)
    red_team_id = Column(String, index=True)
    blue_win = Column(Boolean)
    patch = Column(String, index=True)
    duration_sec = Column(Integer)
    game_date = Column(DateTime, index=True)
    state = Column(String)                        # completed / inProgress / unstarted


class OfficialMatchParticipant(Base):
    __tablename__ = "official_match_participants"
    id = Column(Integer, primary_key=True, autoincrement=True)
    match_id = Column(String, ForeignKey("official_matches.id"), index=True)
    team_id = Column(String, index=True)
    side = Column(String)                          # blue / red
    role = Column(String, index=True)              # top / jungle / mid / bottom / support
    pro_player_id = Column(String, index=True)     # lolesports playerId
    player_name = Column(String, index=True)
    summoner_name = Column(String)                 # in-game name during the match
    champion = Column(String)
    win = Column(Boolean)
    kills = Column(Integer, default=0)
    deaths = Column(Integer, default=0)
    assists = Column(Integer, default=0)
    cs = Column(Integer, default=0)
    gold = Column(Integer, default=0)
    level = Column(Integer, default=0)
    # 10-second-frame derivatives (computed from feed.lolesports.com window)
    gd_at_15 = Column(Integer, default=0)
    xpd_at_15 = Column(Integer, default=0)
    csd_at_15 = Column(Integer, default=0)
    gold_at_15 = Column(Integer, default=0)
    cs_at_15 = Column(Integer, default=0)
    # Computed
    kda = Column(Float, default=0.0)
    kill_participation = Column(Float, default=0.0)


class CurrentLECRoster(Base):
    """Latest known roster per (team, role) for the active LEC tournament."""
    __tablename__ = "current_lec_roster"
    id = Column(Integer, primary_key=True, autoincrement=True)
    team_id = Column(String, index=True)
    team_code = Column(String, index=True)
    role = Column(String, index=True)
    pro_player_id = Column(String, index=True)
    player_name = Column(String, index=True)
    last_seen = Column(DateTime)

    __table_args__ = (
        UniqueConstraint("team_id", "role", "pro_player_id", name="uq_lec_roster"),
    )


class PlayerMeta(Base):
    """
    Leaguepedia-sourced player metadata.
    One row per puuid. Populated by /admin/sync-leaguepedia.
    """
    __tablename__ = "player_meta"
    puuid = Column(String, ForeignKey("players.puuid"), primary_key=True)
    leaguepedia_id = Column(String, index=True)        # canonical "Player" field on the wiki
    leaguepedia_url = Column(String)
    country = Column(String, index=True)               # e.g. "France", "Germany"
    nationality_primary = Column(String, index=True)   # short code used by LP
    residency = Column(String, index=True)             # "Europe", "Korea", "North America", ...
    birthdate = Column(String)                         # ISO date "YYYY-MM-DD" if available
    age = Column(Integer)                              # cached, recomputed on sync
    role = Column(String)                              # LP role (Top/Jungle/Mid/Bot/Support)
    current_team = Column(String, index=True)         # empty string => Free Agent
    is_retired = Column(Boolean, default=False)
    contract_end = Column(String)                      # ISO date if known (rarely populated)
    is_pro = Column(Boolean, default=False)            # has a LP entry at all
    lolesports_id = Column(String, index=True)         # cross-ref to OfficialMatchParticipant.pro_player_id
    last_synced = Column(DateTime)


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String, unique=True, index=True, nullable=False)
    password_hash = Column(String, nullable=False)
    role = Column(String, default="analyst")  # admin / analyst
    org = Column(String, default="default")
    created_at = Column(DateTime)
    is_active = Column(Boolean, default=True)


class WatchlistEntry(Base):
    __tablename__ = "watchlist"
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True)
    puuid = Column(String, ForeignKey("players.puuid"), index=True)
    tag = Column(String, default="")  # free text: "ADC FA target", "EU U21", ...
    added_at = Column(DateTime)

    __table_args__ = (
        UniqueConstraint("user_id", "puuid", name="uq_watch_user_puuid"),
    )


class ScoutNote(Base):
    __tablename__ = "scout_notes"
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True)
    puuid = Column(String, ForeignKey("players.puuid"), index=True)
    content = Column(Text, nullable=False)
    created_at = Column(DateTime)


class RoleDistribution(Base):
    """Stores μ and σ per metric, per role, per patch — for z-score scoring."""

    __tablename__ = "role_distributions"
    id = Column(Integer, primary_key=True, autoincrement=True)
    patch = Column(String, index=True)
    role = Column(String, index=True)
    metric = Column(String, index=True)
    mean = Column(Float)
    std = Column(Float)
    n_samples = Column(Integer)

    __table_args__ = (
        UniqueConstraint("patch", "role", "metric", name="uq_dist_patch_role_metric"),
    )
