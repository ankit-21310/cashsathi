from __future__ import annotations

import base64
import hashlib
from urllib.parse import urlencode

from cashsathi_api.config import Settings
from cashsathi_api.decisioning import DecisionOutput
from cashsathi_api.domain import (
    AgentDecision,
    ExtractedInvoiceDraft,
    ModelDecision,
    ReminderIntent,
    ReminderTone,
)
from cashsathi_api.invoice_processing import ExtractionOutput, ValidatedPdf


class EmulatorInvoiceExtractor:
    """Deterministic local substitute used only with both Firebase emulators."""

    model_id = "emulator-gemini"
    prompt_version = "emulator-extraction-v1"

    def extract(self, _pdf: ValidatedPdf) -> ExtractionOutput:
        return ExtractionOutput(
            draft=ExtractedInvoiceDraft(
                invoice_number="E2E-INV-100",
                customer_name="Northstar Test Customer",
                customer_email="billing@northstar.example",
                amount_decimal="50000.00",
                currency="INR",
                issue_date="2026-07-01",
                due_date="2026-07-31",
                payment_terms="Net 30",
                confidence={"invoice_number": "HIGH"},
            ),
            latency_ms=1,
            input_tokens=0,
            output_tokens=0,
        )


class EmulatorDecisionAdapter:
    model_id = "emulator-gemini"
    prompt_version = "emulator-decision-v1"

    def decide(self, _invoice: object, _state: object, _actions: object) -> DecisionOutput:
        return DecisionOutput(
            proposal=ModelDecision(
                decision=AgentDecision.SEND_REMINDER,
                rationale="The confirmed invoice is overdue and is eligible for a reminder.",
                reminder_tone=ReminderTone.WARM,
                reminder_intent=ReminderIntent.OVERDUE_FOLLOWUP,
            ),
            attempt_count=1,
            latency_ms=1,
            input_tokens=0,
            output_tokens=0,
        )


class EmulatorTokenCipher:
    key_name = "emulator-key"

    def encrypt(self, plaintext: str) -> str:
        return base64.urlsafe_b64encode(plaintext.encode("utf-8")).decode("ascii")

    def decrypt(self, ciphertext: str) -> str:
        return base64.urlsafe_b64decode(ciphertext.encode("ascii")).decode("utf-8")


class EmulatorGmailAdapter:
    def __init__(self, settings: Settings) -> None:
        self._redirect_uri = settings.gmail_oauth_redirect_uri

    def new_pkce_verifier(self) -> str:
        return "emulator-pkce-verifier-" + "v" * 48

    def authorization_url(self, state: str, code_verifier: str) -> str:
        query = urlencode(
            {
                "state": state,
                "code": f"emulator-{hashlib.sha256(code_verifier.encode()).hexdigest()[:12]}",
            }
        )
        return f"{self._redirect_uri}?{query}"

    def exchange_code(self, code: str, code_verifier: str) -> str:
        digest = hashlib.sha256(f"{code}:{code_verifier}".encode()).hexdigest()
        return f"emulator-refresh-{digest}"

    def send(self, refresh_token: str, recipient: str, subject: str, body: str) -> str:
        digest = hashlib.sha256(
            f"{refresh_token}:{recipient}:{subject}:{body}".encode()
        ).hexdigest()
        return f"emulator-message-{digest[:24]}"

    def revoke(self, _refresh_token: str) -> None:
        return None
