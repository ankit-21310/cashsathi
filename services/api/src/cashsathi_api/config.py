from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Validated runtime configuration.

    Emulator variables are intentionally rejected in production because Firebase
    emulator tokens are unsigned and must never be trusted by a deployed service.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_env: Literal["development", "test", "production"] = "development"
    gcp_project_id: str = Field(default="cashsathi-local", min_length=3)
    firestore_database_id: str = "(default)"
    cors_allowed_origins: str = "http://localhost:3000"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    firebase_auth_emulator_host: str | None = None
    firestore_emulator_host: str | None = None

    @property
    def cors_origins(self) -> list[str]:
        return [
            origin.strip().rstrip("/")
            for origin in self.cors_allowed_origins.split(",")
            if origin.strip()
        ]

    @model_validator(mode="after")
    def reject_production_emulators(self) -> "Settings":
        if self.app_env == "production" and self.gcp_project_id == "cashsathi-local":
            raise ValueError("Production requires an explicit GCP project ID")
        if self.app_env == "production" and (
            self.firebase_auth_emulator_host or self.firestore_emulator_host
        ):
            raise ValueError("Firebase emulator variables must not be set in production")
        if not self.cors_origins:
            raise ValueError("At least one CORS origin is required")
        if self.app_env == "production" and any(
            origin.startswith("http://") for origin in self.cors_origins
        ):
            raise ValueError("Production CORS origins must use HTTPS")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
