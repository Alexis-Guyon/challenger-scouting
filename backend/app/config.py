from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    riot_api_key: str = "RGAPI-DEMO"
    platform: str = "euw1"
    region: str = "europe"
    min_games: int = 20
    match_history_count: int = 30
    database_url: str = "sqlite:///./data/scouting.db"
    current_patch: str = "14.9"
    jwt_secret: str = "dev-secret-change-me"
    jwt_expiry_hours: int = 72

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

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


settings = Settings()
