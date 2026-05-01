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

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


settings = Settings()
