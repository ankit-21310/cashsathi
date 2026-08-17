from __future__ import annotations

import base64
import os
from types import SimpleNamespace
from typing import Any

import pytest
from google.api_core import exceptions as google_exceptions
from google.auth.exceptions import RefreshError

import cashsathi_api.gmail as gmail_module
import cashsathi_api.scheduler_auth as scheduler_module
from cashsathi_api.config import Settings
from cashsathi_api.domain import ExtractedInvoiceDraft
from cashsathi_api.errors import ApiError
from cashsathi_api.gmail import (
    AesGcmTokenCipher,
    GmailAmbiguousFailure,
    GmailDefiniteFailure,
    GmailUnavailableError,
    GoogleGmailAdapter,
    GoogleKmsTokenCipher,
)
from cashsathi_api.invoice_processing import GeminiInvoiceExtractor, ValidatedPdf
from cashsathi_api.scheduler_auth import GoogleSchedulerVerifier, VercelCronVerifier


class FakeModels:
    def __init__(self, *responses: Any) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def generate_content(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def test_gemini_extraction_adapter_accepts_only_the_structured_contract() -> None:
    draft = ExtractedInvoiceDraft(
        invoice_number="PROVIDER-1",
        customer_name="Provider Contract Customer",
        customer_email="billing@example.com",
        amount_decimal="1500.00",
        currency="INR",
        issue_date="2026-08-01",
        due_date="2026-08-10",
        payment_terms="Net 9",
        confidence={"invoice_number": "HIGH"},
    )
    response = SimpleNamespace(
        parsed=draft,
        text=None,
        usage_metadata=SimpleNamespace(prompt_token_count=21, candidates_token_count=8),
    )
    models = FakeModels(response)
    extractor = object.__new__(GeminiInvoiceExtractor)
    extractor.model_id = "gemini-contract-test"
    extractor.prompt_version = "extract-contract-v1"
    extractor._client = SimpleNamespace(models=models)

    output = extractor.extract(ValidatedPdf(data=b"%PDF-provider", page_count=1, byte_count=13))

    assert output.draft.invoice_number == "PROVIDER-1"
    assert output.input_tokens == 21
    assert output.output_tokens == 8
    assert len(models.calls) == 1
    config = models.calls[0]["config"]
    assert config.response_mime_type == "application/json"
    assert config.response_schema is ExtractedInvoiceDraft
    assert config.temperature == 0


def test_gemini_extraction_adapter_retries_malformed_responses_once() -> None:
    models = FakeModels(
        SimpleNamespace(parsed=None, text="not-json"),
        SimpleNamespace(parsed=None, text="[]"),
    )
    extractor = object.__new__(GeminiInvoiceExtractor)
    extractor.model_id = "gemini-contract-test"
    extractor.prompt_version = "extract-contract-v1"
    extractor._client = SimpleNamespace(models=models)

    with pytest.raises(ApiError) as error:
        extractor.extract(ValidatedPdf(data=b"%PDF-provider", page_count=1, byte_count=13))

    assert error.value.code == "invalid_extraction_response"
    assert len(models.calls) == 2


class FailingKmsClient:
    def encrypt(self, **_kwargs: Any) -> Any:
        raise google_exceptions.GoogleAPICallError("encrypt failed")

    def decrypt(self, **_kwargs: Any) -> Any:
        raise google_exceptions.GoogleAPICallError("decrypt failed")


def test_kms_failures_are_fail_closed_without_exposing_provider_details() -> None:
    cipher = object.__new__(GoogleKmsTokenCipher)
    cipher.key_name = "projects/test/locations/test/keyRings/test/cryptoKeys/test"
    cipher._client = FailingKmsClient()

    with pytest.raises(GmailUnavailableError, match="Token encryption failed") as encrypt:
        cipher.encrypt("private-refresh-token")
    with pytest.raises(GmailUnavailableError, match="Token decryption failed") as decrypt:
        cipher.decrypt("Y2lwaGVydGV4dA==")

    assert "private-refresh-token" not in str(encrypt.value)
    assert "decrypt failed" not in str(decrypt.value)


class FakeHttpError(Exception):
    def __init__(self, status: int) -> None:
        super().__init__(f"HTTP {status}")
        self.resp = SimpleNamespace(status=status)


class FailingSendRequest:
    def __init__(self, error: Exception) -> None:
        self.error = error

    def execute(self) -> Any:
        raise self.error


class FakeGmailService:
    def __init__(self, error: Exception) -> None:
        self.error = error

    def users(self) -> FakeGmailService:
        return self

    def messages(self) -> FakeGmailService:
        return self

    def send(self, **_kwargs: Any) -> FailingSendRequest:
        return FailingSendRequest(self.error)


def gmail_adapter() -> GoogleGmailAdapter:
    adapter = object.__new__(GoogleGmailAdapter)
    adapter._client_id = "test-client"
    adapter._client_secret = "test-secret"
    adapter._redirect_uri = "https://api.example.test/api/integrations/gmail/callback"
    adapter._timeout_seconds = 5
    adapter._recipient_allowlist = set()
    return adapter


def test_aes_gcm_cipher_round_trip_and_fail_closed_validation() -> None:
    settings = Settings(
        app_env="test",
        gmail_token_encryption_key_b64=base64.b64encode(os.urandom(32)).decode("ascii"),
    )
    cipher = AesGcmTokenCipher(settings)
    encrypted = cipher.encrypt("private-refresh-token")

    assert encrypted.startswith("v1.")
    assert "private-refresh-token" not in encrypted
    assert cipher.decrypt(encrypted) == "private-refresh-token"

    payload = encrypted.removeprefix("v1.")
    decoded = bytearray(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    decoded[-1] ^= 1
    tampered = "v1." + base64.urlsafe_b64encode(decoded).decode().rstrip("=")
    with pytest.raises(GmailUnavailableError, match="Token decryption failed"):
        cipher.decrypt(tampered)
    with pytest.raises(GmailUnavailableError, match="Token decryption failed"):
        cipher.decrypt(encrypted.replace("v1.", "v2.", 1))


@pytest.mark.parametrize("encoded", ["not-base64!", base64.b64encode(b"too-short").decode()])
def test_aes_gcm_cipher_rejects_invalid_keys(encoded: str) -> None:
    with pytest.raises(GmailUnavailableError, match="encryption key"):
        AesGcmTokenCipher(Settings(app_env="test", gmail_token_encryption_key_b64=encoded))


def test_gmail_rejects_recipient_outside_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = gmail_adapter()
    adapter._recipient_allowlist = {"allowed@example.test"}
    monkeypatch.setattr(
        gmail_module,
        "build",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("Gmail was called")),
    )

    with pytest.raises(GmailDefiniteFailure) as error:
        adapter.send("refresh-token", "other@example.test", "Reminder", "Body")

    assert error.value.code == "recipient_not_allowed"


def test_vercel_cron_uses_constant_time_bearer_secret() -> None:
    verifier = VercelCronVerifier(Settings(app_env="test", cron_secret="cron-test-secret"))
    assert verifier.verify("Bearer cron-test-secret") == "vercel-cron"

    with pytest.raises(ApiError) as missing:
        verifier.verify(None)
    assert missing.value.status_code == 401

    with pytest.raises(ApiError) as invalid:
        verifier.verify("Bearer wrong-secret")
    assert invalid.value.status_code == 401


@pytest.mark.parametrize(
    ("status", "expected_exception", "expected_code"),
    [
        (400, GmailDefiniteFailure, "gmail_rejected"),
        (401, GmailDefiniteFailure, "gmail_reconnect_required"),
        (429, GmailAmbiguousFailure, None),
        (503, GmailAmbiguousFailure, None),
    ],
)
def test_gmail_distinguishes_definite_and_ambiguous_delivery(
    monkeypatch: pytest.MonkeyPatch,
    status: int,
    expected_exception: type[Exception],
    expected_code: str | None,
) -> None:
    monkeypatch.setattr(gmail_module, "HttpError", FakeHttpError)
    monkeypatch.setattr(
        gmail_module,
        "build",
        lambda *_args, **_kwargs: FakeGmailService(FakeHttpError(status)),
    )

    with pytest.raises(expected_exception) as error:
        gmail_adapter().send(
            "refresh-token", "recipient@example.test", "Reminder", "Deterministic body"
        )

    if expected_code is not None:
        assert isinstance(error.value, GmailDefiniteFailure)
        assert error.value.code == expected_code


def test_gmail_refresh_failure_requires_reconnection(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        gmail_module,
        "build",
        lambda *_args, **_kwargs: FakeGmailService(RefreshError("refresh rejected")),
    )

    with pytest.raises(GmailDefiniteFailure) as error:
        gmail_adapter().send(
            "expired-refresh-token",
            "recipient@example.test",
            "Reminder",
            "Deterministic body",
        )

    assert error.value.code == "gmail_reconnect_required"
    assert "expired-refresh-token" not in str(error.value)


def test_scheduler_verifies_audience_email_and_verified_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        app_env="test",
        scheduler_audience="https://api.example.test/api/jobs/recheck",
        scheduler_service_account_email="scheduler@example.test",
    )
    verifier = GoogleSchedulerVerifier(settings)
    observed: dict[str, str] = {}

    def valid_claims(_token: str, _request: Any, *, audience: str) -> dict[str, Any]:
        observed["audience"] = audience
        return {"email": "scheduler@example.test", "email_verified": True}

    monkeypatch.setattr(scheduler_module.id_token, "verify_oauth2_token", valid_claims)
    assert verifier.verify("Bearer signed-token") == "scheduler@example.test"
    assert observed["audience"] == settings.scheduler_audience

    monkeypatch.setattr(
        scheduler_module.id_token,
        "verify_oauth2_token",
        lambda *_args, **_kwargs: {
            "email": "attacker@example.test",
            "email_verified": True,
        },
    )
    with pytest.raises(ApiError) as forbidden:
        verifier.verify("Bearer signed-token")
    assert forbidden.value.status_code == 403

    monkeypatch.setattr(
        scheduler_module.id_token,
        "verify_oauth2_token",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("bad signature")),
    )
    with pytest.raises(ApiError) as invalid:
        verifier.verify("Bearer invalid-token")
    assert invalid.value.status_code == 401
