from __future__ import annotations

import base64
import hashlib
import secrets
from email.message import EmailMessage
from typing import Protocol, cast

from google.api_core import exceptions as google_exceptions
from google.auth.exceptions import RefreshError
from google.cloud import kms
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from cashsathi_api.config import Settings

GMAIL_SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"
TOKEN_URI = "https://oauth2.googleapis.com/token"


class GmailUnavailableError(Exception):
    pass


class GmailDefiniteFailure(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class GmailAmbiguousFailure(Exception):
    pass


class TokenCipher(Protocol):
    key_name: str

    def encrypt(self, plaintext: str) -> str: ...

    def decrypt(self, ciphertext: str) -> str: ...


class GoogleKmsTokenCipher:
    def __init__(self, settings: Settings) -> None:
        if not settings.gmail_kms_key_name:
            raise GmailUnavailableError("Gmail KMS key is not configured")
        self.key_name = settings.gmail_kms_key_name
        self._client = kms.KeyManagementServiceClient()

    def encrypt(self, plaintext: str) -> str:
        try:
            response = self._client.encrypt(
                request={"name": self.key_name, "plaintext": plaintext.encode("utf-8")}
            )
        except google_exceptions.GoogleAPICallError as exc:
            raise GmailUnavailableError("Token encryption failed") from exc
        return base64.b64encode(response.ciphertext).decode("ascii")

    def decrypt(self, ciphertext: str) -> str:
        try:
            response = self._client.decrypt(
                request={
                    "name": self.key_name,
                    "ciphertext": base64.b64decode(ciphertext.encode("ascii"), validate=True),
                }
            )
        except (ValueError, google_exceptions.GoogleAPICallError) as exc:
            raise GmailUnavailableError("Token decryption failed") from exc
        return response.plaintext.decode("utf-8")


class GmailAdapter(Protocol):
    def new_pkce_verifier(self) -> str: ...

    def authorization_url(self, state: str, code_verifier: str) -> str: ...

    def exchange_code(self, code: str, code_verifier: str) -> str: ...

    def send(self, refresh_token: str, recipient: str, subject: str, body: str) -> str: ...


class GoogleGmailAdapter:
    def __init__(self, settings: Settings) -> None:
        if settings.gmail_oauth_client_id is None or settings.gmail_oauth_client_secret is None:
            raise GmailUnavailableError("Gmail OAuth is not configured")
        self._client_id = settings.gmail_oauth_client_id.get_secret_value()
        self._client_secret = settings.gmail_oauth_client_secret.get_secret_value()
        self._redirect_uri = settings.gmail_oauth_redirect_uri

    def _flow(self, code_verifier: str) -> Flow:
        flow = Flow.from_client_config(
            {
                "web": {
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": TOKEN_URI,
                    "redirect_uris": [self._redirect_uri],
                }
            },
            scopes=[GMAIL_SEND_SCOPE],
            code_verifier=code_verifier,
            autogenerate_code_verifier=False,
        )
        flow.redirect_uri = self._redirect_uri
        return flow

    def new_pkce_verifier(self) -> str:
        return secrets.token_urlsafe(64)

    def authorization_url(self, state: str, code_verifier: str) -> str:
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode("ascii")).digest())
            .decode("ascii")
            .rstrip("=")
        )
        flow = self._flow(code_verifier)
        url, _ = flow.authorization_url(
            state=state,
            access_type="offline",
            prompt="consent",
            include_granted_scopes="false",
            code_challenge=challenge,
            code_challenge_method="S256",
        )
        return cast(str, url)

    def exchange_code(self, code: str, code_verifier: str) -> str:
        try:
            flow = self._flow(code_verifier)
            flow.fetch_token(code=code)
            refresh_token = flow.credentials.refresh_token
        except Exception as exc:
            raise GmailDefiniteFailure(
                "oauth_exchange_failed", "Gmail authorization failed."
            ) from exc
        if not refresh_token:
            raise GmailDefiniteFailure(
                "refresh_token_missing", "Google did not return an offline refresh token."
            )
        return cast(str, refresh_token)

    def send(self, refresh_token: str, recipient: str, subject: str, body: str) -> str:
        credentials = Credentials(  # type: ignore[no-untyped-call]
            token=None,
            refresh_token=refresh_token,
            token_uri=TOKEN_URI,
            client_id=self._client_id,
            client_secret=self._client_secret,
            scopes=[GMAIL_SEND_SCOPE],
        )
        message = EmailMessage()
        message["To"] = recipient
        message["Subject"] = subject
        message.set_content(body)
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")
        try:
            service = build("gmail", "v1", credentials=credentials, cache_discovery=False)
            result = service.users().messages().send(userId="me", body={"raw": raw}).execute()
        except RefreshError as exc:
            raise GmailDefiniteFailure(
                "gmail_reconnect_required", "Gmail authorization must be renewed."
            ) from exc
        except HttpError as exc:
            status = getattr(exc.resp, "status", 500)
            if status < 500 and status != 429:
                code = "gmail_reconnect_required" if status in {401, 403} else "gmail_rejected"
                raise GmailDefiniteFailure(code, "Gmail rejected the reminder.") from exc
            raise GmailAmbiguousFailure("Gmail delivery could not be confirmed.") from exc
        except Exception as exc:
            raise GmailAmbiguousFailure("Gmail delivery could not be confirmed.") from exc
        message_id = result.get("id")
        if not isinstance(message_id, str) or not message_id:
            raise GmailAmbiguousFailure("Gmail returned no delivery identifier.")
        return message_id
