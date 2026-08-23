"""Environment-driven application configuration."""

from decimal import Decimal
from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
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
    real_provider_enabled: bool = False
    openai_api_key: SecretStr | None = None
    openai_model: str = "gpt-5.6-luna"
    openai_reasoning_effort: Literal["none", "low"] = "none"
    openai_max_output_tokens: int = Field(default=1200, ge=256, le=4000)
    openai_max_turns: int = Field(default=6, ge=1, le=8)
    openai_request_timeout_seconds: float = Field(default=45.0, gt=0, le=120)
    openai_max_experiment_cost_usd: Decimal = Field(
        default=Decimal("5.00"),
        gt=0,
        le=Decimal("5.00"),
    )


@lru_cache
def get_settings() -> Settings:
    """Return one immutable-by-convention settings object per process."""

    return Settings()
