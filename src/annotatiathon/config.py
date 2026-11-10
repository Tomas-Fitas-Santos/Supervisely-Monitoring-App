"""Strict settings. Event data and credentials are never embedded in source code."""
from functools import lru_cache

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "local"
    database_url: str
    supervisely_api_token: SecretStr | None = None
    supervisely_allowed_hosts: str = "app.supervisely.com"
    local_data_dir: str = ".nightjar-data"
    max_dataset_bytes: int = 140_000_000_000
    max_dataset_assets: int = 250_000

    @field_validator("database_url")
    @classmethod
    def require_postgres(cls, value: str) -> str:
        if not value.startswith("postgresql+psycopg://"):
            raise ValueError("DATABASE_URL must use PostgreSQL with the psycopg driver.")
        return value

    @field_validator("app_env")
    @classmethod
    def valid_environment(cls, value: str) -> str:
        if value not in {"local", "production"}:
            raise ValueError("APP_ENV must be local or production.")
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
