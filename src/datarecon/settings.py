import os
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache


class Settings(BaseSettings):
    deployment_mode: str = Field(
        default="local-dev",
        description="Deployment mode: local-dev, deploy-aws-local, or deploy-aws"
    )

    db_path: str = Field(
        default="recycling.db",
        description="Path to local SQLite database"
    )

    default_tolerance_pct: float = Field(
        default=5.0,
        description="Default tolerance percentage for reconciliation"
    )

    max_lookback_months: int = Field(
        default=2,
        description="Maximum months back from current month that can be queried"
    )

    log_level: str = Field(
        default="INFO",
        description="Logging level"
    )

    model_config = SettingsConfigDict(
        case_sensitive=False,
        env_file=(".env", ".env.local-dev", ".env.deploy-aws"),
        env_file_encoding="utf-8",
        extra="allow",
    )


@lru_cache()
def get_settings() -> Settings:
    return Settings()
