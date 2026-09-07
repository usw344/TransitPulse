from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="TRANSITPULSE_",
        extra="ignore",
    )

    database_url: str = (
        "postgresql+psycopg://transitpulse:transitpulse@localhost:5432/transitpulse"
    )


settings = Settings()
