from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from cashsathi_api.config import Settings
from cashsathi_api.decisioning import DecisionOutput
from cashsathi_api.domain import (
    AgentDecision,
    AuthenticatedUser,
    ExtractedInvoiceDraft,
    ModelDecision,
)
from cashsathi_api.errors import ApiError
from cashsathi_api.invoice_processing import ExtractionOutput, ValidatedPdf
from cashsathi_api.main import create_app
from cashsathi_api.repository import InMemoryRepository


class TestAuthVerifier:
    __test__ = False

    def verify(self, token: str) -> AuthenticatedUser:
        users = {
            "alice-token": AuthenticatedUser("alice", "alice@example.com", "Alice"),
            "bob-token": AuthenticatedUser("bob", "bob@example.com", "Bob"),
        }
        if token not in users:
            raise ApiError(401, "invalid_token", "The authentication token is invalid or expired.")
        return users[token]


class TestInvoiceExtractor:
    __test__ = False
    model_id = "test-gemini"
    prompt_version = "test-extraction-v1"

    def extract(self, _pdf: ValidatedPdf) -> ExtractionOutput:
        return ExtractionOutput(
            draft=ExtractedInvoiceDraft(
                invoice_number="INV-100",
                customer_name="Northstar Client",
                customer_email="billing@northstar.example",
                amount_decimal="50000.00",
                currency="INR",
                issue_date="2026-07-01",
                due_date="2026-07-31",
                payment_terms="Net 30",
                confidence={"invoice_number": "HIGH"},
            ),
            latency_ms=25,
            input_tokens=100,
            output_tokens=40,
        )


class TestDecisionAdapter:
    __test__ = False
    model_id = "test-gemini"
    prompt_version = "test-decision-v1"

    def decide(self, _invoice: object, _state: object, _actions: object) -> DecisionOutput:
        return DecisionOutput(
            proposal=ModelDecision(
                decision=AgentDecision.SEND_REMINDER,
                rationale="The invoice is overdue and no recent reminder is recorded.",
            ),
            attempt_count=1,
            latency_ms=30,
            input_tokens=80,
            output_tokens=20,
        )


class TestTokenCipher:
    __test__ = False
    key_name = "test-key"

    def encrypt(self, plaintext: str) -> str:
        return f"encrypted:{plaintext}"

    def decrypt(self, ciphertext: str) -> str:
        return ciphertext.removeprefix("encrypted:")


class TestGmailAdapter:
    __test__ = False

    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str]] = []

    def new_pkce_verifier(self) -> str:
        return "v" * 64

    def authorization_url(self, state: str, code_verifier: str) -> str:
        return f"https://accounts.example/authorize?state={state}&challenge={len(code_verifier)}"

    def exchange_code(self, code: str, code_verifier: str) -> str:
        return f"refresh-{code}-{len(code_verifier)}"

    def send(self, refresh_token: str, recipient: str, subject: str, body: str) -> str:
        self.sent.append((recipient, subject, body))
        return f"gmail-{len(self.sent)}"


class TestSchedulerVerifier:
    __test__ = False

    def verify(self, authorization: str | None) -> str:
        if authorization != "Bearer scheduler-token":
            raise ApiError(401, "invalid_scheduler_token", "Scheduler authentication failed.")
        return "scheduler@example.test"


@pytest.fixture
def repository() -> InMemoryRepository:
    return InMemoryRepository()


@pytest.fixture
def client(repository: InMemoryRepository) -> Iterator[TestClient]:
    settings = Settings(
        app_env="test",
        gcp_project_id="cashsathi-test",
        cors_allowed_origins="http://localhost:3000",
        platform_admin_uids="alice",
    )
    app = create_app(
        settings=settings,
        repository=repository,
        auth_verifier=TestAuthVerifier(),
        invoice_extractor=TestInvoiceExtractor(),
        decision_adapter=TestDecisionAdapter(),
        gmail_adapter=TestGmailAdapter(),
        token_cipher=TestTokenCipher(),
        scheduler_verifier=TestSchedulerVerifier(),
    )
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client
