import base64
import binascii
import json
import os
from typing import Any, Protocol

import firebase_admin
from fastapi import Request
from firebase_admin import auth, credentials

from cashsathi_api.config import Settings
from cashsathi_api.domain import AuthenticatedUser
from cashsathi_api.errors import ApiError


class AuthVerifier(Protocol):
    def verify(self, token: str) -> AuthenticatedUser: ...


class AccountAuthManager(Protocol):
    def delete_user(self, uid: str) -> None: ...


class FirebaseAccountAuthManager:
    def delete_user(self, uid: str) -> None:
        auth.delete_user(uid)


def decode_firebase_service_account(settings: Settings) -> dict[str, Any]:
    encoded = settings.firebase_service_account_json_b64
    if encoded is None:
        raise ValueError("Firebase service account is not configured")
    try:
        raw = base64.b64decode(encoded.get_secret_value(), validate=True)
        payload = json.loads(raw.decode("utf-8"))
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Firebase service account is malformed") from exc
    if not isinstance(payload, dict) or payload.get("type") != "service_account":
        raise ValueError("Firebase service account is malformed")
    if payload.get("project_id") != settings.gcp_project_id:
        raise ValueError("Firebase service account project does not match GCP_PROJECT_ID")
    return payload


def initialize_firebase(settings: Settings) -> None:
    if settings.firebase_auth_emulator_host:
        os.environ.setdefault("FIREBASE_AUTH_EMULATOR_HOST", settings.firebase_auth_emulator_host)
    os.environ.setdefault("GCLOUD_PROJECT", settings.gcp_project_id)
    try:
        firebase_admin.get_app()
    except ValueError:
        credential = None
        if settings.firebase_service_account_json_b64 is not None:
            credential = credentials.Certificate(decode_firebase_service_account(settings))
        firebase_admin.initialize_app(
            credential=credential,
            options={"projectId": settings.gcp_project_id},
        )


class FirebaseAuthVerifier:
    def __init__(self, settings: Settings) -> None:
        initialize_firebase(settings)

    def verify(self, token: str) -> AuthenticatedUser:
        try:
            decoded: dict[str, Any] = auth.verify_id_token(token, check_revoked=True)
        except (auth.InvalidIdTokenError, auth.ExpiredIdTokenError, auth.RevokedIdTokenError):
            raise ApiError(
                401, "invalid_token", "The authentication token is invalid or expired."
            ) from None
        except Exception:
            raise ApiError(
                401, "authentication_failed", "Authentication could not be verified."
            ) from None

        uid = decoded.get("uid") or decoded.get("sub")
        if not isinstance(uid, str) or not uid:
            raise ApiError(401, "invalid_token", "The authentication token has no user identifier.")
        return AuthenticatedUser(
            uid=uid,
            email=decoded.get("email") if isinstance(decoded.get("email"), str) else None,
            display_name=decoded.get("name") if isinstance(decoded.get("name"), str) else None,
        )


def authenticated_user(request: Request) -> AuthenticatedUser:
    authorization = request.headers.get("Authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise ApiError(401, "authentication_required", "A Firebase bearer token is required.")
    verifier: AuthVerifier = request.app.state.auth_verifier
    return verifier.verify(token)
