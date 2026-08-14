from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, model_validator
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
    gemini_api_key: SecretStr | None = None
    gemini_model: str = "gemini-3.6-flash"
    extraction_prompt_version: str = "invoice-extraction-v1"
    decision_prompt_version: str = "collection-decision-v1"
    policy_version: str = "collection-policy-v1"
    max_pdf_bytes: int = Field(default=10 * 1024 * 1024, ge=1024)
    max_pdf_pages: int = Field(default=25, ge=1, le=1000)
    gemini_timeout_seconds: int = Field(default=45, ge=5, le=110)
    web_base_url: str = "http://localhost:3000"
    gmail_oauth_client_id: SecretStr | None = None
    gmail_oauth_client_secret: SecretStr | None = None
    gmail_oauth_redirect_uri: str = "http://localhost:8000/api/integrations/gmail/callback"
    gmail_kms_key_name: str | None = None
    gmail_timeout_seconds: int = Field(default=20, ge=5, le=60)
    scheduler_service_account_email: str | None = None
    scheduler_audience: str | None = None
    scheduler_batch_size: int = Field(default=20, ge=1, le=100)
    scheduler_concurrency: int = Field(default=3, ge=1, le=10)
    action_execution_timeout_minutes: int = Field(default=10, ge=1, le=60)
    platform_admin_uids: str = ""

    @property
    def cors_origins(self) -> list[str]:
        return [
            origin.strip().rstrip("/")
            for origin in self.cors_allowed_origins.split(",")
            if origin.strip()
        ]

    @property
    def admin_uids(self) -> set[str]:
        return {uid.strip() for uid in self.platform_admin_uids.split(",") if uid.strip()}

    @property
    def local_emulators_enabled(self) -> bool:
        local_prefixes = ("127.0.0.1:", "localhost:")
        auth_host = self.firebase_auth_emulator_host
        firestore_host = self.firestore_emulator_host
        return (
            self.app_env != "production"
            and auth_host is not None
            and firestore_host is not None
            and auth_host.startswith(local_prefixes)
            and firestore_host.startswith(local_prefixes)
        )

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
        if self.app_env == "production" and not self.web_base_url.startswith("https://"):
            raise ValueError("Production web base URL must use HTTPS")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
