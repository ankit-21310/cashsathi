from __future__ import annotations

import base64
import binascii
import hashlib
import secrets
import urllib.error
import urllib.parse
import urllib.request
from email.message import EmailMessage
from typing import Protocol, cast

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
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


class AesGcmTokenCipher:
    """Versioned authenticated encryption for Vercel-hosted Gmail refresh tokens."""

    _AAD = b"cashsathi:gmail-refresh-token:v1"

    def __init__(self, settings: Settings) -> None:
        configured = settings.gmail_token_encryption_key_b64
        if configured is None:
            raise GmailUnavailableError("Gmail token encryption key is not configured")
        try:
            key = base64.b64decode(configured.get_secret_value(), validate=True)
        except (binascii.Error, ValueError) as exc:
            raise GmailUnavailableError("Gmail token encryption key is invalid") from exc
        if len(key) != 32:
            raise GmailUnavailableError("Gmail token encryption key must be 32 bytes")
        self._cipher = AESGCM(key)
        self.key_name = "vercel-env:aes-256-gcm:v1"

    def encrypt(self, plaintext: str) -> str:
        nonce = secrets.token_bytes(12)
        encrypted = self._cipher.encrypt(nonce, plaintext.encode("utf-8"), self._AAD)
        payload = base64.urlsafe_b64encode(nonce + encrypted).decode("ascii").rstrip("=")
        return f"v1.{payload}"

    def decrypt(self, ciphertext: str) -> str:
        version, separator, payload = ciphertext.partition(".")
        if separator != "." or version != "v1" or not payload:
            raise GmailUnavailableError("Token decryption failed")
        try:
            padded = payload + "=" * (-len(payload) % 4)
            decoded = base64.b64decode(padded, altchars=b"-_", validate=True)
            if len(decoded) < 29:
                raise ValueError("Encrypted payload is too short")
            plaintext = self._cipher.decrypt(decoded[:12], decoded[12:], self._AAD)
            return plaintext.decode("utf-8")
        except (binascii.Error, InvalidTag, UnicodeDecodeError, ValueError) as exc:
            raise GmailUnavailableError("Token decryption failed") from exc


class GmailAdapter(Protocol):
    def new_pkce_verifier(self) -> str: ...

    def authorization_url(self, state: str, code_verifier: str) -> str: ...

    def exchange_code(self, code: str, code_verifier: str) -> str: ...

    def send(self, refresh_token: str, recipient: str, subject: str, body: str) -> str: ...

    def revoke(self, refresh_token: str) -> None: ...


class GoogleGmailAdapter:
    def __init__(self, settings: Settings) -> None:
        if settings.gmail_oauth_client_id is None or settings.gmail_oauth_client_secret is None:
            raise GmailUnavailableError("Gmail OAuth is not configured")
        self._client_id = settings.gmail_oauth_client_id.get_secret_value()
        self._client_secret = settings.gmail_oauth_client_secret.get_secret_value()
        self._redirect_uri = settings.gmail_oauth_redirect_uri
        self._timeout_seconds = settings.gmail_timeout_seconds
        self._recipient_allowlist = settings.allowed_gmail_recipients

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
        if (
            self._recipient_allowlist
            and recipient.strip().casefold() not in self._recipient_allowlist
        ):
            raise GmailDefiniteFailure(
                "recipient_not_allowed", "Gmail delivery is restricted to demo recipients."
            )
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

    def revoke(self, refresh_token: str) -> None:
        request = urllib.request.Request(
            "https://oauth2.googleapis.com/revoke",
            data=urllib.parse.urlencode({"token": refresh_token}).encode("ascii"),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout_seconds) as response:
                if response.status >= 400:
                    raise GmailUnavailableError("Gmail token revocation failed")
        except (OSError, urllib.error.URLError) as exc:
            raise GmailUnavailableError("Gmail token revocation failed") from exc
