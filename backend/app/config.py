from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


# Make the .env path absolute so it's found regardless of which directory
# uvicorn was launched from. Without this, Pydantic resolves "./env"
# against the cwd — running uvicorn from the repo root (instead of from
# backend/) silently makes every secret default to its empty string.
_BACKEND_DIR = Path(__file__).resolve().parent.parent  # …/scouting/backend
_ENV_FILE = _BACKEND_DIR / ".env"


class Settings(BaseSettings):
    riot_api_key: str = "RGAPI-DEMO"
    # Multi-key support: comma-separated list of API keys. When set, the
    # daily ingest partitions work across keys (each key gets its own
    # RateLimiter, so 3 keys = 3× sustained throughput). Falls back to
    # `riot_api_key` when empty (single-key back-compat). Example:
    #   RIOT_API_KEYS=RGAPI-aaa,RGAPI-bbb,RGAPI-ccc
    riot_api_keys: str = ""
    platform: str = "euw1"
    region: str = "europe"
    min_games: int = 20
    match_history_count: int = 30
    database_url: str = "sqlite:///./data/scouting.db"
    current_patch: str = "14.9"
    jwt_secret: str = "dev-secret-change-me"
    jwt_expiry_hours: int = 72

    # ------------------------------------------------------------------
    # Daily scheduled ladder ingest (KR / EUW / NA at 4am).
    # Disabled by default. Set DAILY_INGEST_ENABLED=true to turn on.
    # ------------------------------------------------------------------
    daily_ingest_enabled: bool = False
    daily_ingest_hour: int = 4   # server local time, 24h
    daily_ingest_minute: int = 0
    daily_ingest_regions: str = "euw1,kr,na1"
    daily_ingest_tiers: str = "challenger,grandmaster,master"
    daily_ingest_players_per_tier: int = 500
    daily_ingest_games_per_player: int = 30
    # Key-to-work assignment strategy. One of:
    #   "tier"        — 1 key per tier (3 keys for chall/gm/master)
    #   "region"      — 1 key per region
    #   "tier_region" — 1 key per (tier × region) cell (9 keys ideal)
    #   "round_robin" — distribute work units evenly across all keys
    daily_ingest_partition: str = "tier"

    # lol.fandom.com bot credentials (Leaguepedia/Cargo). Anonymous use is rate-
    # limited to ~1 req/min — basically unusable. Get a bot password at
    # https://lol.fandom.com/wiki/Special:BotPasswords
    fandom_username: str = ""
    fandom_password: str = ""
    # Legacy aliases — still accepted for back-compat. If both are set,
    # the FANDOM_* names win.
    lp_username: str = ""
    lp_password: str = ""

    # Webhook alerts (Discord + Slack). Either or both can be set.
    # The Discord webhook URL: server settings → integrations → webhooks → new.
    # The Slack webhook URL: api.slack.com/apps → incoming webhooks.
    discord_webhook_url: str = ""
    slack_webhook_url: str = ""

    # Public origin used in alert links (the frontend URL — Vercel, Fly, etc.)
    public_app_url: str = ""

    # Alert thresholds — see services/alerts.py for exact semantics
    alert_css_min: float = 70.0          # only surface players with CSS ≥ this
    alert_css_delta: float = 4.0         # raise an alert when CSS jumps by ≥ this
    alert_winrate_streak_min: int = 6    # min consecutive wins to alert

    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
