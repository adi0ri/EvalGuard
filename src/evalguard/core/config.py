"""Environment-driven application configuration."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings shared by the API, CLI, and worker."""

    model_config = SettingsConfigDict(
        env_prefix="EVALGUARD_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "EvalGuard"
    environment: str = "development"
    log_level: str = "INFO"
    database_url: str = "postgresql+psycopg://evalguard:evalguard@localhost:5432/evalguard"
    redis_url: str = "redis://localhost:6379/0"
    queue_name: str = "evalguard"
    test_database_url: str = (
        "postgresql+psycopg://evalguard:evalguard@localhost:5432/evalguard_test"
    )
    test_redis_url: str = "redis://localhost:6379/15"
    test_queue_name: str = "evalguard-tests"


@lru_cache
def get_settings() -> Settings:
    """Return one immutable-by-convention settings object per process."""

    return Settings()
