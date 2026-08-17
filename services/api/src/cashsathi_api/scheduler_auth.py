from __future__ import annotations

import hmac
from typing import Protocol

from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2 import id_token

from cashsathi_api.config import Settings
from cashsathi_api.errors import ApiError


class SchedulerVerifier(Protocol):
    def verify(self, authorization: str | None) -> str: ...


class VercelCronVerifier:
    def __init__(self, settings: Settings) -> None:
        self._secret = (
            settings.cron_secret.get_secret_value() if settings.cron_secret is not None else None
        )

    def verify(self, authorization: str | None) -> str:
        if not self._secret:
            raise ApiError(
                503, "scheduler_not_configured", "Cron authentication is not configured."
            )
        scheme, _, token = (authorization or "").partition(" ")
        if scheme.casefold() != "bearer" or not token:
            raise ApiError(401, "scheduler_auth_required", "Cron authentication is required.")
        if not hmac.compare_digest(token, self._secret):
            raise ApiError(401, "invalid_scheduler_token", "Cron authentication failed.")
        return "vercel-cron"


class GoogleSchedulerVerifier:
    def __init__(self, settings: Settings) -> None:
        self._audience = settings.scheduler_audience
        self._email = settings.scheduler_service_account_email

    def verify(self, authorization: str | None) -> str:
        if not self._audience or not self._email:
            raise ApiError(
                503, "scheduler_not_configured", "Scheduler authentication is not configured."
            )
        if not authorization or not authorization.startswith("Bearer "):
            raise ApiError(401, "scheduler_auth_required", "Scheduler authentication is required.")
        token = authorization.removeprefix("Bearer ").strip()
        try:
            claims = id_token.verify_oauth2_token(  # type: ignore[no-untyped-call]
                token,
                GoogleAuthRequest(),
                audience=self._audience,
            )
        except Exception:
            raise ApiError(
                401, "invalid_scheduler_token", "Scheduler authentication failed."
            ) from None
        if claims.get("email") != self._email or claims.get("email_verified") is not True:
            raise ApiError(
                403, "scheduler_identity_forbidden", "Scheduler identity is not allowed."
            )
        return self._email
