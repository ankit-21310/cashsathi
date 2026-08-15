from __future__ import annotations

import zipfile
from datetime import UTC, date, datetime, timedelta
from io import BytesIO
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter

from cashsathi_api.decisioning import (
    DecisionSchemaFailure,
    GeminiDecisionAdapter,
)
from cashsathi_api.domain import (
    AgentDecision,
    CustomerSnapshot,
    Invoice,
    InvoiceState,
    InvoiceWorkflowStatus,
    PolicyDefaults,
)
from cashsathi_api.repository import InMemoryRepository


def auth(token: str = "alice-token") -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def pdf_bytes() -> bytes:
    stream = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.write(stream)
    return stream.getvalue()


def create_invoice(client: TestClient, token: str = "alice-token") -> dict[str, Any]:
    client.post("/api/businesses", headers=auth(token), json={"name": "Test Studio"})
    consent = client.get("/api/consents/product-processing", headers=auth(token)).json()
    client.post(
        "/api/consents/product-processing",
        headers=auth(token),
        json={"version": consent["version"], "accepted": True},
    )
    extraction = client.post(
        "/api/invoices/extract",
        headers=auth(token),
        files={"file": ("invoice.pdf", pdf_bytes(), "application/pdf")},
    ).json()
    response = client.post(
        "/api/invoices",
        headers=auth(token),
        json={
            "extraction_id": extraction["extraction_id"],
            "invoice_number": "INV-SAFETY-1",
            "customer_name": "Northstar Client",
            "customer_email": "billing@northstar.example",
            "customer_manual_only": False,
            "amount_decimal": "50000.00",
            "currency": "INR",
            "issue_date": "2026-07-01",
            "due_date": "2026-07-31",
            "payment_terms": "Net 30",
            "confirmed": True,
        },
    )
    assert response.status_code == 200
    return response.json()


def test_restrictive_policy_customer_and_dispute_lifecycle(
    client: TestClient, repository: InMemoryRepository
) -> None:
    invoice = create_invoice(client)

    policy = client.patch(
        "/api/settings/policy",
        headers=auth(),
        json={
            "reminder_cooldown_hours": 96,
            "high_value_threshold_minor": 4_000_000,
            "confirmed": True,
        },
    )
    assert policy.status_code == 200
    assert policy.json()["reminder_cooldown_hours"] == 96
    assert policy.json()["dispute_requires_human"] is True

    loosen = client.patch(
        "/api/settings/policy",
        headers=auth(),
        json={"reminder_cooldown_hours": 72, "confirmed": True},
    )
    assert loosen.status_code == 422
    immutable = client.patch(
        "/api/settings/policy",
        headers=auth(),
        json={"legal_language_allowed": True, "confirmed": True},
    )
    assert immutable.status_code == 422

    templates = client.get("/api/settings/policy/templates", headers=auth())
    assert templates.status_code == 200
    assert {item["template"] for item in templates.json()["items"]} == {
        "AGENCY",
        "CONSULTANT",
        "MANUFACTURER",
    }
    consultant = client.post(
        "/api/settings/policy/templates/CONSULTANT/apply",
        headers=auth(),
        json={"confirmed": True},
    )
    assert consultant.status_code == 200
    assert consultant.json()["reminder_cooldown_hours"] == 120
    assert consultant.json()["high_value_threshold_minor"] == 2_500_000
    agency_after_consultant = client.post(
        "/api/settings/policy/templates/AGENCY/apply",
        headers=auth(),
        json={"confirmed": True},
    )
    assert agency_after_consultant.json()["reminder_cooldown_hours"] == 120
    assert agency_after_consultant.json()["high_value_threshold_minor"] == 2_500_000

    customer = client.patch(
        f"/api/customers/{invoice['customer']['id']}",
        headers=auth(),
        json={"manual_only": True, "locale": "hi-IN", "confirmed": True},
    )
    assert customer.status_code == 200
    assert customer.json()["manual_only"] is True
    assert customer.json()["locale"] == "hi-IN"

    evaluated = client.post(f"/api/invoices/{invoice['id']}/evaluate", headers=auth())
    assert evaluated.status_code == 200
    result = evaluated.json()
    assert result["agent_run"]["proposed_function"] == "send_payment_reminder"
    assert result["agent_run"]["function_call_id"]
    assert result["action"]["locale"] == "hi-IN"
    assert result["action"]["template_version"] == "reminder-hi-in-v1"
    assert "नमस्ते" in result["action"]["body"]

    private_note = "Private dispute note: account 998877"
    disputed = client.post(
        f"/api/invoices/{invoice['id']}/disputes",
        headers=auth(),
        json={"reason": "PAYMENT_CLAIM", "note": private_note, "confirmed": True},
    )
    assert disputed.status_code == 200
    assert disputed.json()["dispute_active"] is True
    assert disputed.json()["workflow_status"] == "PAUSED"
    assert repository.actions[result["action"]["id"]].state.value == "CANCELLED"
    assert private_note not in str(repository.evidence_events)

    blocked = client.post(f"/api/invoices/{invoice['id']}/evaluate", headers=auth())
    assert blocked.status_code == 200
    assert blocked.json()["agent_run"]["policy_result"]["matched_rules"] == ["dispute_hard_stop"]
    assert blocked.json()["action"] is None

    next_check = datetime.now(UTC) + timedelta(days=1)
    resolved = client.post(
        f"/api/invoices/{invoice['id']}/disputes/resolve",
        headers=auth(),
        json={
            "resolution_note": "Handled privately",
            "next_check_at": next_check.isoformat(),
            "confirmed": True,
        },
    )
    assert resolved.status_code == 200
    assert resolved.json()["dispute_active"] is False
    assert resolved.json()["workflow_status"] == "OPEN"
    assert "Handled privately" not in str(repository.evidence_events)

    client.post("/api/businesses", headers=auth("bob-token"), json={"name": "Other Studio"})
    hidden = client.patch(
        f"/api/customers/{invoice['customer']['id']}",
        headers=auth("bob-token"),
        json={"manual_only": False, "confirmed": True},
    )
    assert hidden.status_code == 404


def sample_invoice() -> Invoice:
    now = datetime.now(UTC)
    return Invoice(
        id="inv_contract",
        business_id="biz_contract",
        extraction_id="ext_contract",
        invoice_number="INV-1",
        customer=CustomerSnapshot(
            id="cust_contract",
            name="Contract Customer",
            email="billing@example.test",
            manual_only=False,
        ),
        amount_minor=100_000,
        currency="INR",
        issue_date=date(2026, 7, 1),
        due_date=date(2026, 7, 31),
        payment_terms="Net 30",
        review_required=False,
        review_reason=None,
        confirmation_hash="hash",
        created_at=now,
        updated_at=now,
        workflow_status=InvoiceWorkflowStatus.OPEN,
    )


def function_response(name: str, arguments: dict[str, Any], call_id: str = "call-1") -> Any:
    call = SimpleNamespace(name=name, args=arguments, id=call_id)
    return SimpleNamespace(
        candidates=[
            SimpleNamespace(content=SimpleNamespace(parts=[SimpleNamespace(function_call=call)]))
        ],
        usage_metadata=SimpleNamespace(prompt_token_count=12, candidates_token_count=4),
    )


class FakeModels:
    def __init__(self, responses: list[Any]) -> None:
        self.responses = responses
        self.configs: list[Any] = []

    def generate_content(self, **kwargs: Any) -> Any:
        self.configs.append(kwargs["config"])
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def adapter_with(*responses: Any) -> tuple[GeminiDecisionAdapter, FakeModels]:
    adapter = object.__new__(GeminiDecisionAdapter)
    adapter.model_id = "gemini-contract"
    adapter.prompt_version = "contract-v1"
    models = FakeModels(list(responses))
    adapter._client = SimpleNamespace(models=models)
    return adapter, models


@pytest.mark.parametrize(
    ("function_name", "arguments", "decision"),
    [
        (
            "wait",
            {
                "invoice_id": "inv_contract",
                "reason_code": "SAFE_RECHECK",
                "next_check_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
            },
            AgentDecision.WAIT,
        ),
        (
            "send_payment_reminder",
            {
                "invoice_id": "inv_contract",
                "tone": "WARM",
                "intent": "OVERDUE_FOLLOWUP",
                "risk_flags": [],
            },
            AgentDecision.SEND_REMINDER,
        ),
        (
            "schedule_recheck",
            {
                "invoice_id": "inv_contract",
                "reason_code": "COOLDOWN_ACTIVE",
                "next_check_at": (datetime.now(UTC) + timedelta(days=2)).isoformat(),
            },
            AgentDecision.SCHEDULE_RECHECK,
        ),
        (
            "request_human_review",
            {
                "invoice_id": "inv_contract",
                "reason_code": "DISPUTE_REPORTED",
                "risk_flags": ["CUSTOMER_DISPUTE"],
            },
            AgentDecision.REQUEST_HUMAN_REVIEW,
        ),
        (
            "close_as_paid",
            {
                "invoice_id": "inv_contract",
                "reason_code": "OWNER_CONFIRMED_PAYMENT",
            },
            AgentDecision.CLOSE_AS_PAID,
        ),
    ],
)
def test_gemini_function_contracts(
    function_name: str, arguments: dict[str, Any], decision: AgentDecision
) -> None:
    adapter, models = adapter_with(function_response(function_name, arguments))

    output = adapter.decide(sample_invoice(), InvoiceState.OVERDUE, [], PolicyDefaults())

    assert output.proposal.decision == decision
    assert output.proposed_function == function_name
    assert output.function_arguments["invoice_id"] == arguments["invoice_id"]
    for key, value in arguments.items():
        if key != "next_check_at":
            assert output.function_arguments[key] == value
    if "next_check_at" in arguments:
        expected = datetime.fromisoformat(str(arguments["next_check_at"]))
        actual = datetime.fromisoformat(
            str(output.function_arguments["next_check_at"]).replace("Z", "+00:00")
        )
        assert actual == expected
    config = models.configs[0]
    assert config.automatic_function_calling.disable is True
    assert config.tool_config.function_calling_config.mode.value == "ANY"
    assert set(config.tool_config.function_calling_config.allowed_function_names) == {
        "wait",
        "send_payment_reminder",
        "schedule_recheck",
        "request_human_review",
        "close_as_paid",
    }


def test_invalid_or_multiple_calls_retry_once_then_fail() -> None:
    mismatched = function_response(
        "send_payment_reminder",
        {
            "invoice_id": "wrong_invoice",
            "tone": "WARM",
            "intent": "OVERDUE_FOLLOWUP",
            "risk_flags": [],
        },
    )
    multiple = function_response(
        "send_payment_reminder",
        {
            "invoice_id": "inv_contract",
            "tone": "WARM",
            "intent": "OVERDUE_FOLLOWUP",
            "risk_flags": [],
        },
    )
    multiple.candidates[0].content.parts.append(
        SimpleNamespace(
            function_call=SimpleNamespace(
                name="wait",
                args={
                    "invoice_id": "inv_contract",
                    "reason_code": "SAFE_RECHECK",
                    "next_check_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
                },
                id="call-2",
            )
        )
    )
    adapter, models = adapter_with(multiple, mismatched)

    with pytest.raises(DecisionSchemaFailure) as raised:
        adapter.decide(sample_invoice(), InvoiceState.OVERDUE, [], PolicyDefaults())

    assert raised.value.attempt_count == 2
    assert len(models.configs) == 2


def test_team_invitation_roles_redaction_and_revocation(
    client: TestClient, repository: InMemoryRepository
) -> None:
    invoice = create_invoice(client)
    evaluated = client.post(f"/api/invoices/{invoice['id']}/evaluate", headers=auth())
    assert evaluated.status_code == 200

    invited = client.post(
        "/api/team/invitations",
        headers=auth(),
        json={"email": "bob@example.com", "role": "OPERATOR"},
    )
    assert invited.status_code == 200
    invitation_id = invited.json()["id"]
    assert "bob@example.com" not in str(repository.evidence_events)

    accepted = client.post(
        f"/api/team/invitations/{invitation_id}/accept", headers=auth("bob-token")
    )
    assert accepted.status_code == 200
    assert accepted.json()["role"] == "OPERATOR"

    operator_policy = client.patch(
        "/api/settings/policy",
        headers=auth("bob-token"),
        json={"reminder_cooldown_hours": 96, "confirmed": True},
    )
    assert operator_policy.status_code == 403

    changed = client.patch(
        "/api/team/members/bob",
        headers=auth(),
        json={"role": "ADVISOR", "confirmed": True},
    )
    assert changed.status_code == 200
    assert changed.json()["role"] == "ADVISOR"

    advisor_actions = client.get("/api/actions", headers=auth("bob-token"))
    assert advisor_actions.status_code == 200
    assert advisor_actions.json()["items"][0]["recipient_email"] is None
    assert advisor_actions.json()["items"][0]["subject"] is None
    assert advisor_actions.json()["items"][0]["body"] is None

    advisor_payment = client.post(
        f"/api/invoices/{invoice['id']}/payments",
        headers=auth("bob-token"),
        json={
            "amount_decimal": "1.00",
            "currency": "INR",
            "paid_at": datetime.now(UTC).isoformat(),
            "reference": "bank-ref",
            "idempotency_key": "advisor-payment-key",
            "confirmed": True,
        },
    )
    assert advisor_payment.status_code == 403

    revoked = client.post(
        "/api/team/members/bob/revoke",
        headers=auth(),
        json={"confirmed": True},
    )
    assert revoked.status_code == 200
    assert revoked.json()["status"] == "REVOKED"
    inactive = client.get("/api/metrics", headers=auth("bob-token"))
    assert inactive.status_code == 403

    pending = client.post(
        "/api/team/invitations",
        headers=auth(),
        json={"email": "charlie@example.com", "role": "ADVISOR"},
    ).json()
    cancelled = client.post(
        f"/api/team/invitations/{pending['id']}/revoke",
        headers=auth(),
        json={"confirmed": True},
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "REVOKED"


def test_forecast_finance_pack_and_accounting_contracts(client: TestClient) -> None:
    invoice = create_invoice(client)
    evaluated = client.post(f"/api/invoices/{invoice['id']}/evaluate", headers=auth())
    assert evaluated.status_code == 200
    private_note = "Never export this dispute note 441122"
    opened = client.post(
        f"/api/invoices/{invoice['id']}/disputes",
        headers=auth(),
        json={"reason": "OTHER", "note": private_note, "confirmed": True},
    )
    assert opened.status_code == 200

    forecast = client.get("/api/forecast", headers=auth())
    assert forecast.status_code == 200
    body = forecast.json()
    assert body["methodology_version"] == "deterministic-due-date-delay-p50-v1"
    assert [horizon["weeks"] for horizon in body["horizons"]] == [4, 8, 12]
    assert body["horizons"][0]["expected_inflow_by_currency"]["INR"] == 5_000_000

    finance_pack = client.get("/api/finance-pack", headers=auth())
    assert finance_pack.status_code == 200
    with zipfile.ZipFile(BytesIO(finance_pack.content)) as archive:
        assert set(archive.namelist()) == {
            "manifest.json",
            "methodology.json",
            "aging.json",
            "verified_payments.json",
            "policy_action_history.json",
            "forecast.json",
        }
        exported = "\n".join(archive.read(name).decode("utf-8") for name in archive.namelist())
    assert private_note not in exported
    assert "Hello Northstar Client" not in exported
    assert "billing@northstar.example" not in exported

    integrations = client.get("/api/integrations/accounting", headers=auth())
    assert integrations.status_code == 200
    assert integrations.json()["items"] == [
        {
            "provider": "ZOHO_BOOKS",
            "state": "NOT_CONFIGURED",
            "organization_external_id": None,
            "last_sync_at": None,
            "last_cursor": None,
            "imported_customers": 0,
            "imported_invoices": 0,
            "imported_payments": 0,
            "last_error_code": None,
            "credentials_encrypted": False,
        },
        {
            "provider": "TALLY_PRIME",
            "state": "NOT_CONFIGURED",
            "organization_external_id": None,
            "last_sync_at": None,
            "last_cursor": None,
            "imported_customers": 0,
            "imported_invoices": 0,
            "imported_payments": 0,
            "last_error_code": None,
            "credentials_encrypted": False,
        },
    ]
    sync = client.post("/api/integrations/accounting/ZOHO_BOOKS/sync", headers=auth())
    assert sync.status_code == 409
    assert sync.json()["error"]["code"] == "accounting_integration_not_configured"
