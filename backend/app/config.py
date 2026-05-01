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

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


settings = Settings()
