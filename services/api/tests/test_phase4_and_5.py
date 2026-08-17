from __future__ import annotations

from datetime import UTC, datetime, timedelta
from io import BytesIO
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient
from pypdf import PdfWriter

from cashsathi_api.domain import Action, ActionState, AuthenticatedUser, GmailConnection
from cashsathi_api.repository import InMemoryRepository


def auth(token: str = "alice-token") -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def pdf_bytes() -> bytes:
    stream = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.write(stream)
    return stream.getvalue()


def setup_invoice(client: TestClient, suffix: str = "") -> dict[str, object]:
    client.post("/api/businesses", headers=auth(), json={"name": "Alice Studio"})
    status = client.get("/api/consents/product-processing", headers=auth()).json()
    client.post(
        "/api/consents/product-processing",
        headers=auth(),
        json={"version": status["version"], "accepted": True},
    )
    extraction = client.post(
        "/api/invoices/extract",
        headers=auth(),
        files={"file": (f"invoice{suffix}.pdf", pdf_bytes(), "application/pdf")},
    ).json()
    response = client.post(
        "/api/invoices",
        headers=auth(),
        json={
            "extraction_id": extraction["extraction_id"],
            "invoice_number": f"INV-100{suffix}",
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


def connect_test_gmail(repository: InMemoryRepository) -> None:
    tenant = repository.require_tenant(AuthenticatedUser("alice", None, None))
    now = datetime.now(UTC)
    repository.save_gmail_connection(
        tenant,
        GmailConnection(
            business_id=tenant.business_id,
            encrypted_refresh_token="encrypted:refresh-token",
            kms_key_name="test-key",
            connected_at=now,
            updated_at=now,
        ),
    )


def test_approval_send_metrics_and_payment_are_end_to_end(
    client: TestClient, repository: InMemoryRepository
) -> None:
    invoice = setup_invoice(client)
    connect_test_gmail(repository)
    evaluated = client.post(f"/api/invoices/{invoice['id']}/evaluate", headers=auth())
    action = evaluated.json()["action"]
    assert action["state"] == "AWAITING_APPROVAL"
    assert "legal" not in action["body"].lower()

    approved = client.post(f"/api/actions/{action['id']}/approve", headers=auth())
    assert approved.status_code == 200
    assert approved.json()["state"] == "SUCCEEDED"
    assert approved.json()["provider_message_id"] == "gmail-1"
    assert client.post(f"/api/actions/{action['id']}/approve", headers=auth()).status_code == 409

    payment_payload = {
        "amount_decimal": "25000.00",
        "currency": "INR",
        "paid_at": "2026-08-14T08:00:00Z",
        "reference": "UPI-1",
        "idempotency_key": "payment-key-1",
        "confirmed": True,
    }
    first = client.post(
        f"/api/invoices/{invoice['id']}/payments", headers=auth(), json=payment_payload
    )
    assert first.status_code == 200
    replay = client.post(
        f"/api/invoices/{invoice['id']}/payments", headers=auth(), json=payment_payload
    )
    assert replay.status_code == 200
    assert replay.json()["id"] == first.json()["id"]

    payment_payload.update({"reference": "UPI-2", "idempotency_key": "payment-key-2"})
    assert (
        client.post(
            f"/api/invoices/{invoice['id']}/payments", headers=auth(), json=payment_payload
        ).status_code
        == 200
    )
    detail = client.get(f"/api/invoices/{invoice['id']}", headers=auth()).json()
    assert detail["current_state"] == "PAID"
    metrics = client.get("/api/metrics", headers=auth()).json()
    assert metrics["successful_actions"] == 1
    assert metrics["currencies"][0]["verified_paid_minor"] == 5_000_000


def test_oauth_state_is_single_use_and_tokens_are_not_exposed(
    client: TestClient, repository: InMemoryRepository
) -> None:
    setup_invoice(client, "-oauth")
    connect = client.post("/api/integrations/gmail/connect", headers=auth())
    assert connect.status_code == 200
    state = parse_qs(urlparse(connect.json()["authorization_url"]).query)["state"][0]
    callback = client.get(
        f"/api/integrations/gmail/callback?state={state}&code=test-code",
        follow_redirects=False,
    )
    assert callback.status_code == 303
    assert callback.headers["location"].endswith("status=connected")
    assert "refresh" not in str(client.get("/api/integrations/gmail/status", headers=auth()).json())
    replay = client.get(
        f"/api/integrations/gmail/callback?state={state}&code=test-code",
        follow_redirects=False,
    )
    assert replay.status_code == 400
    assert repository.get_gmail_connection(next(iter(repository.businesses))) is not None


def test_full_payment_cancels_an_unsent_reminder(client: TestClient) -> None:
    invoice = setup_invoice(client, "-paid-before-send")
    evaluated = client.post(f"/api/invoices/{invoice['id']}/evaluate", headers=auth())
    assert evaluated.status_code == 200
    action = evaluated.json()["action"]
    assert action["state"] == "AWAITING_APPROVAL"

    paid = client.post(
        f"/api/invoices/{invoice['id']}/payments",
        headers=auth(),
        json={
            "amount_decimal": "50000.00",
            "currency": "INR",
            "paid_at": "2026-08-14T08:00:00Z",
            "reference": "BANK-FULL-1",
            "idempotency_key": "paid-before-send-key",
            "confirmed": True,
        },
    )
    assert paid.status_code == 200
    actions = client.get("/api/actions", headers=auth()).json()["items"]
    cancelled = next(item for item in actions if item["id"] == action["id"])
    assert cancelled["state"] == "CANCELLED"
    assert client.post(f"/api/actions/{action['id']}/approve", headers=auth()).status_code == 409
    detail = client.get(f"/api/invoices/{invoice['id']}", headers=auth()).json()
    assert detail["current_state"] == "PAID"


def test_admin_evidence_is_separate_and_tenant_protected(
    client: TestClient,
) -> None:
    setup_invoice(client, "-admin")
    business = client.get("/api/me", headers=auth()).json()["business"]
    classified = client.post(
        f"/api/admin/businesses/{business['id']}/classification",
        headers=auth(),
        json={"data_classification": "REAL", "relationship": "ARMS_LENGTH"},
    )
    assert classified.status_code == 200
    ledger = client.post(
        "/api/admin/evidence-ledger",
        headers=auth(),
        json={
            "kind": "PRODUCT_REVENUE",
            "amount_decimal": "299.00",
            "currency": "INR",
            "occurred_on": "2026-08-14",
            "category": "Founder plan",
            "reference": "receipt-1",
            "business_id": business["id"],
            "marketing": False,
        },
    )
    assert ledger.status_code == 200
    impact = client.get("/api/admin/impact", headers=auth()).json()
    assert impact["paying_businesses"] == 1
    assert impact["revenue_by_currency"]["INR"] == 29_900
    assert client.get("/api/admin/impact", headers=auth("bob-token")).status_code == 403


def test_scheduler_is_oidc_gated_and_repeated_runs_do_not_duplicate_actions(
    client: TestClient, repository: InMemoryRepository
) -> None:
    setup_invoice(client, "-scheduler")
    assert client.post("/api/jobs/recheck").status_code == 401
    first = client.post("/api/jobs/recheck", headers={"Authorization": "Bearer scheduler-token"})
    assert first.status_code == 200
    assert first.json()["claimed"] == 1
    second = client.post("/api/jobs/recheck", headers={"Authorization": "Bearer scheduler-token"})
    assert second.status_code == 200
    assert second.json()["claimed"] == 0
    assert len(repository.actions) == 1


def test_stale_executing_actions_are_reconciled_via_bounded_query(
    client: TestClient, repository: InMemoryRepository
) -> None:
    invoice = setup_invoice(client, "-stale")
    stale_started = datetime.now(UTC) - timedelta(hours=2)
    repository.actions["act_stale"] = Action(
        id="act_stale",
        invoice_id=invoice["id"],
        business_id=invoice["business_id"],
        agent_run_id="run_stale",
        state=ActionState.EXECUTING,
        created_at=stale_started,
        execution_started_at=stale_started,
    )

    stale = repository.list_stale_executing_actions(datetime.now(UTC))
    assert [action.id for action in stale] == ["act_stale"]
    assert stale[0].business_id == invoice["business_id"]

    response = client.post("/api/jobs/recheck", headers={"Authorization": "Bearer scheduler-token"})
    assert response.status_code == 200

    reconciled = repository.actions["act_stale"]
    assert reconciled.state == ActionState.UNKNOWN
    assert reconciled.failure_code == "stale_execution"
