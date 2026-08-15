from io import BytesIO

from fastapi.testclient import TestClient
from pypdf import PdfWriter

from cashsathi_api.decisioning import DecisionSchemaFailure, DecisionTransportFailure
from cashsathi_api.repository import InMemoryRepository


def auth(token: str = "alice-token") -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def pdf_bytes(page_count: int = 1) -> bytes:
    stream = BytesIO()
    writer = PdfWriter()
    for _ in range(page_count):
        writer.add_blank_page(width=200, height=200)
    writer.write(stream)
    return stream.getvalue()


def onboard_and_consent(client: TestClient, token: str = "alice-token") -> None:
    client.post("/api/businesses", headers=auth(token), json={"name": "Test Studio"})
    status = client.get("/api/consents/product-processing", headers=auth(token)).json()
    response = client.post(
        "/api/consents/product-processing",
        headers=auth(token),
        json={"version": status["version"], "accepted": True},
    )
    assert response.status_code == 200


def extract(client: TestClient, token: str = "alice-token") -> dict[str, object]:
    response = client.post(
        "/api/invoices/extract",
        headers=auth(token),
        files={"file": ("invoice.pdf", pdf_bytes(), "application/pdf")},
    )
    assert response.status_code == 200
    return response.json()


def confirmation(extraction_id: str, **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "extraction_id": extraction_id,
        "invoice_number": "INV-100",
        "customer_name": "Northstar Client",
        "customer_email": "billing@northstar.example",
        "customer_manual_only": False,
        "amount_decimal": "50000.00",
        "currency": "INR",
        "issue_date": "2026-07-01",
        "due_date": "2026-07-31",
        "payment_terms": "Net 30",
        "confirmed": True,
    }
    payload.update(overrides)
    return payload


def test_consent_is_versioned_and_required_before_extraction(client: TestClient) -> None:
    client.post("/api/businesses", headers=auth(), json={"name": "Test Studio"})
    status = client.get("/api/consents/product-processing", headers=auth())
    assert status.status_code == 200
    assert status.json()["granted"] is False

    blocked = client.post(
        "/api/invoices/extract",
        headers=auth(),
        files={"file": ("invoice.pdf", pdf_bytes(), "application/pdf")},
    )
    assert blocked.status_code == 403
    assert blocked.json()["error"]["code"] == "consent_required"

    stale = client.post(
        "/api/consents/product-processing",
        headers=auth(),
        json={"version": "old", "accepted": True},
    )
    assert stale.status_code == 409


def test_extract_confirm_list_and_evaluate_are_end_to_end(
    client: TestClient, repository: InMemoryRepository
) -> None:
    onboard_and_consent(client)
    extraction = extract(client)
    extraction_id = str(extraction["extraction_id"])
    assert extraction["draft"]["invoice_number"] == "INV-100"  # type: ignore[index]

    confirmed = client.post("/api/invoices", headers=auth(), json=confirmation(extraction_id))
    assert confirmed.status_code == 200
    invoice = confirmed.json()
    assert invoice["amount_minor"] == 5_000_000

    repeated = client.post("/api/invoices", headers=auth(), json=confirmation(extraction_id))
    assert repeated.status_code == 200
    assert repeated.json()["id"] == invoice["id"]

    listed = client.get("/api/invoices", headers=auth()).json()
    assert listed["items"][0]["current_state"] == "OVERDUE"

    evaluated = client.post(f"/api/invoices/{invoice['id']}/evaluate", headers=auth())
    assert evaluated.status_code == 200
    result = evaluated.json()
    assert result["agent_run"]["policy_result"]["outcome"] == "REQUIRE_APPROVAL"
    assert result["action"]["state"] == "AWAITING_APPROVAL"

    evidence_text = str(repository.evidence_events)
    assert "%PDF" not in evidence_text
    assert "invoice.pdf" not in evidence_text
    assert "billing@northstar.example" not in evidence_text


def test_confirmation_is_conflict_safe_and_tenant_isolated(client: TestClient) -> None:
    onboard_and_consent(client, "alice-token")
    alice_extraction = extract(client, "alice-token")
    extraction_id = str(alice_extraction["extraction_id"])
    created = client.post("/api/invoices", headers=auth(), json=confirmation(extraction_id)).json()

    conflict = client.post(
        "/api/invoices",
        headers=auth(),
        json=confirmation(extraction_id, amount_decimal="1.00"),
    )
    assert conflict.status_code == 409

    onboard_and_consent(client, "bob-token")
    hidden = client.get(f"/api/invoices/{created['id']}", headers=auth("bob-token"))
    assert hidden.status_code == 404


def test_missing_due_date_is_confirmed_for_human_review(client: TestClient) -> None:
    onboard_and_consent(client)
    extraction_id = str(extract(client)["extraction_id"])
    response = client.post(
        "/api/invoices",
        headers=auth(),
        json=confirmation(extraction_id, due_date=None),
    )
    assert response.status_code == 200
    invoice_id = response.json()["id"]
    detail = client.get(f"/api/invoices/{invoice_id}", headers=auth()).json()
    assert detail["current_state"] == "HUMAN_REVIEW"


def test_upload_validation_rejects_wrong_type_and_page_limit(client: TestClient) -> None:
    onboard_and_consent(client)
    wrong_type = client.post(
        "/api/invoices/extract",
        headers=auth(),
        files={"file": ("invoice.pdf", pdf_bytes(), "text/plain")},
    )
    assert wrong_type.status_code == 415
    assert wrong_type.json()["error"]["code"] == "invalid_mime_type"

    too_many = client.post(
        "/api/invoices/extract",
        headers=auth(),
        files={"file": ("invoice.pdf", pdf_bytes(26), "application/pdf")},
    )
    assert too_many.status_code == 422
    assert too_many.json()["error"]["code"] == "too_many_pages"


class InvalidDecisionAdapter:
    model_id = "test-gemini"
    prompt_version = "test-decision-v1"

    def decide(self, _invoice: object, _state: object, _actions: object, _policy: object) -> object:
        raise DecisionSchemaFailure(2, 40)


class TransportFailureAdapter:
    model_id = "test-gemini"
    prompt_version = "test-decision-v1"

    def decide(self, _invoice: object, _state: object, _actions: object, _policy: object) -> object:
        raise DecisionTransportFailure(45)


def test_two_invalid_model_responses_route_to_human_review(client: TestClient) -> None:
    onboard_and_consent(client)
    extraction_id = str(extract(client)["extraction_id"])
    invoice = client.post("/api/invoices", headers=auth(), json=confirmation(extraction_id)).json()
    client.app.state.decision_adapter = InvalidDecisionAdapter()

    response = client.post(f"/api/invoices/{invoice['id']}/evaluate", headers=auth())

    assert response.status_code == 200
    assert response.json()["agent_run"]["status"] == "HUMAN_REVIEW"
    assert response.json()["action"] is None


def test_transport_failure_is_audited_without_an_action(
    client: TestClient, repository: InMemoryRepository
) -> None:
    onboard_and_consent(client)
    extraction_id = str(extract(client)["extraction_id"])
    invoice = client.post("/api/invoices", headers=auth(), json=confirmation(extraction_id)).json()
    client.app.state.decision_adapter = TransportFailureAdapter()

    response = client.post(f"/api/invoices/{invoice['id']}/evaluate", headers=auth())

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "decision_unavailable"
    assert list(repository.agent_runs.values())[-1].status == "FAILED"
    assert repository.actions == {}
