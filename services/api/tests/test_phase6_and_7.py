from __future__ import annotations

import json
import zipfile
from datetime import UTC, date, datetime
from io import BytesIO

import pytest
from fastapi.testclient import TestClient

from cashsathi_api.domain import AuthenticatedUser, CustomerSnapshot, FounderPlanStatus, Invoice
from cashsathi_api.errors import ApiError
from cashsathi_api.repository import InMemoryRepository


def auth(token: str = "alice-token") -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def onboard(client: TestClient, name: str = "Alice Studio") -> dict[str, object]:
    response = client.post("/api/businesses", headers=auth(), json={"name": name})
    assert response.status_code == 200
    return response.json()


def test_security_headers_json_body_limit_and_admin_marker(client: TestClient) -> None:
    response = client.get("/api/me", headers=auth())
    assert response.status_code == 200
    assert response.json()["is_platform_admin"] is True
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "camera=()" in response.headers["permissions-policy"]

    too_large = client.post(
        "/api/businesses",
        headers=auth(),
        json={"name": "A" * (256 * 1024)},
    )
    assert too_large.status_code == 413
    assert too_large.json()["error"]["code"] == "request_too_large"
    streamed = client.post(
        "/api/businesses",
        headers={**auth(), "Content-Type": "application/json"},
        content=iter([b'{"name":"', b"A" * (256 * 1024), b'"}']),
    )
    assert streamed.status_code == 413


def test_optional_consent_is_append_only_withdrawable_and_exported(client: TestClient) -> None:
    onboard(client)
    definitions = client.get("/api/privacy/consents", headers=auth()).json()["items"]
    testimonial = next(item for item in definitions if item["consent_type"] == "TESTIMONIAL")

    missing_scope = client.post(
        "/api/privacy/consents/TESTIMONIAL/grant",
        headers=auth(),
        json={"version": testimonial["version"], "accepted": True, "channels": []},
    )
    assert missing_scope.status_code == 422
    granted = client.post(
        "/api/privacy/consents/TESTIMONIAL/grant",
        headers=auth(),
        json={
            "version": testimonial["version"],
            "accepted": True,
            "approved_text": "CashSathi saved me time.",
            "channels": ["XPRIZE_SUBMISSION"],
        },
    )
    assert granted.status_code == 200
    active = next(
        item for item in granted.json()["items"] if item["consent_type"] == "TESTIMONIAL"
    )["active_grant"]
    withdrawn = client.post(
        f"/api/privacy/consents/{active['id']}/withdraw",
        headers=auth(),
        json={"confirmed": True},
    )
    item = next(
        value for value in withdrawn.json()["items"] if value["consent_type"] == "TESTIMONIAL"
    )
    assert item["active_grant"] is None
    assert sorted(event["action"] for event in item["history"]) == ["GRANTED", "WITHDRAWN"]

    account_export = client.get("/api/account/export", headers=auth())
    assert account_export.status_code == 200
    exported = account_export.json()
    assert len(exported["optional_consents"]) == 2
    assert "encrypted_refresh_token" not in account_export.text
    assert "oauth" not in account_export.text.lower()


def test_validation_workspace_is_admin_only_and_paginated(client: TestClient) -> None:
    forbidden = client.get("/api/admin/validation/prospects", headers=auth("bob-token"))
    assert forbidden.status_code == 403
    for company in ("Acme Foods", "Banyan Design"):
        created = client.post(
            "/api/admin/validation/prospects",
            headers=auth(),
            json={
                "company": company,
                "segment": "Services",
                "public_website": "https://example.test",
                "public_contact_channel": "Public website contact form",
            },
        )
        assert created.status_code == 200
    first = client.get("/api/admin/validation/prospects?limit=1", headers=auth()).json()
    assert len(first["items"]) == 1
    assert first["next_cursor"]
    second = client.get(
        "/api/admin/validation/prospects",
        headers=auth(),
        params={"limit": 1, "cursor": first["next_cursor"]},
    ).json()
    assert second["items"][0]["id"] != first["items"][0]["id"]

    prospect_id = first["items"][0]["id"]
    interview = client.post(
        f"/api/admin/validation/prospects/{prospect_id}/interviews",
        headers=auth(),
        json={
            "occurred_on": date.today().isoformat(),
            "current_workflow": "Spreadsheet and manual reminders",
            "top_pain": "Following up consistently",
            "trust_boundary": "Approve every external message",
            "weekly_receivables_minutes": 180,
            "active_invoice_range": "10-25",
            "automation_comfort": False,
            "required_approval_cases": ["First reminder"],
            "willingness_to_pay": "YES",
            "feedback": "Would trial with approval controls.",
        },
    )
    assert interview.status_code == 200
    updated = client.get("/api/admin/validation/prospects?limit=100", headers=auth()).json()
    assert next(item for item in updated["items"] if item["id"] == prospect_id)["status"] == (
        "INTERVIEWED"
    )
    stopped = client.patch(
        f"/api/admin/validation/prospects/{prospect_id}",
        headers=auth(),
        json={"status": "DO_NOT_CONTACT"},
    )
    assert stopped.status_code == 200
    invalid = client.patch(
        f"/api/admin/validation/prospects/{prospect_id}",
        headers=auth(),
        json={"status": "CONTACTED"},
    )
    assert invalid.status_code == 409
    assert invalid.json()["error"]["code"] == "invalid_prospect_transition"


def test_founder_plan_activation_is_exact_and_idempotent(
    client: TestClient, repository: InMemoryRepository
) -> None:
    business = onboard(client)
    classified = client.post(
        f"/api/admin/businesses/{business['id']}/classification",
        headers=auth(),
        json={"data_classification": "REAL", "relationship": "ARMS_LENGTH"},
    )
    assert classified.status_code == 200
    tenant = repository.require_tenant(AuthenticatedUser("alice", None, None))
    now = datetime.now(UTC)
    existing_extraction = repository.record_extraction(tenant, {"test": True})
    repository.save_invoice(
        tenant,
        Invoice(
            id="inv_plan_0",
            business_id=tenant.business_id,
            extraction_id=existing_extraction,
            invoice_number="PLAN-0",
            customer=CustomerSnapshot(
                id="customer_plan", name="Synthetic customer", email=None, manual_only=True
            ),
            amount_minor=10_000,
            currency="INR",
            issue_date=None,
            due_date=None,
            payment_terms=None,
            review_required=True,
            review_reason="test",
            confirmation_hash="hash-0",
            created_at=now,
            updated_at=now,
        ),
    )
    payload = {
        "business_id": business["id"],
        "paid_on": "2026-08-14",
        "receipt_reference": "MANUAL-UPI-299",
        "idempotency_key": "manual-payment-0001",
        "confirmed": True,
    }
    first = client.post("/api/admin/founder-plans/activate", headers=auth(), json=payload)
    replay = client.post("/api/admin/founder-plans/activate", headers=auth(), json=payload)
    assert first.status_code == 200
    assert replay.status_code == 200
    assert first.json() == replay.json()
    assert first.json()["price_minor"] == 29_900
    assert first.json()["invoice_limit"] == 10
    assert first.json()["invoices_used"] == 1
    entries = repository.list_ledger_entries()
    assert len(entries) == 1
    assert entries[0].amount_minor == 29_900
    assert entries[0].kind.value == "PRODUCT_REVENUE"

    for index in range(1, 10):
        extraction_id = repository.record_extraction(tenant, {"test": True})
        repository.save_invoice(
            tenant,
            Invoice(
                id=f"inv_plan_{index}",
                business_id=tenant.business_id,
                extraction_id=extraction_id,
                invoice_number=f"PLAN-{index}",
                customer=CustomerSnapshot(
                    id="customer_plan", name="Synthetic customer", email=None, manual_only=True
                ),
                amount_minor=10_000,
                currency="INR",
                issue_date=None,
                due_date=None,
                payment_terms=None,
                review_required=True,
                review_reason="test",
                confirmation_hash=f"hash-{index}",
                created_at=now,
                updated_at=now,
            ),
        )
    exhausted = repository.get_founder_plan(str(business["id"]))
    assert exhausted is not None
    assert exhausted.invoices_used == 10
    assert exhausted.status == FounderPlanStatus.EXHAUSTED
    extra_extraction = repository.record_extraction(tenant, {"test": True})
    with pytest.raises(ApiError, match="allowance is exhausted") as exc_info:
        repository.save_invoice(
            tenant,
            Invoice(
                id="inv_plan_11",
                business_id=tenant.business_id,
                extraction_id=extra_extraction,
                invoice_number="PLAN-11",
                customer=CustomerSnapshot(
                    id="customer_plan", name="Synthetic customer", email=None, manual_only=True
                ),
                amount_minor=10_000,
                currency="INR",
                issue_date=None,
                due_date=None,
                payment_terms=None,
                review_required=True,
                review_reason="test",
                confirmation_hash="hash-11",
                created_at=now,
                updated_at=now,
            ),
        )
    assert exc_info.value.code == "plan_invoice_limit_reached"


def test_sanitized_evidence_zip_has_completeness_and_no_tenant_ids(client: TestClient) -> None:
    business = onboard(client)
    response = client.get("/api/admin/evidence-export", headers=auth())
    assert response.status_code == 200
    with zipfile.ZipFile(BytesIO(response.content)) as archive:
        assert "scoreboard.json" in archive.namelist()
        manifest = json.loads(archive.read("manifest.json"))
        businesses = archive.read("businesses.csv").decode()
        assert manifest["complete"] is True
        assert "collection_counts" in manifest
        assert business["id"] not in businesses
        assert business["evidence_pseudonym"] in businesses


def test_export_rate_limit_returns_retry_after(
    client: TestClient, repository: InMemoryRepository
) -> None:
    onboard(client)
    for _ in range(3):
        assert client.get("/api/account/export", headers=auth()).status_code == 200
    blocked = client.get("/api/account/export", headers=auth())
    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "rate_limit_exceeded"
    assert int(blocked.headers["retry-after"]) > 0
    assert all("alice" not in digest for digest in repository.rate_limits)


def test_account_deletion_requires_exact_name_and_purges_owner(
    client: TestClient, repository: InMemoryRepository
) -> None:
    business = onboard(client)
    ledger = client.post(
        "/api/admin/evidence-ledger",
        headers=auth(),
        json={
            "kind": "PRODUCT_REVENUE",
            "amount_decimal": "299.00",
            "currency": "INR",
            "occurred_on": "2026-08-14",
            "category": "Founder plan",
            "reference": "private-receipt-reference",
            "business_id": business["id"],
            "marketing": False,
        },
    )
    assert ledger.status_code == 200
    metrics_definition = next(
        item
        for item in client.get("/api/privacy/consents", headers=auth()).json()["items"]
        if item["consent_type"] == "ANONYMIZED_METRICS"
    )
    assert (
        client.post(
            "/api/privacy/consents/ANONYMIZED_METRICS/grant",
            headers=auth(),
            json={"version": metrics_definition["version"], "accepted": True},
        ).status_code
        == 200
    )
    mismatch = client.post(
        "/api/account/delete",
        headers=auth(),
        json={"business_name": "Wrong", "confirmed": True},
    )
    assert mismatch.status_code == 422
    deleted = client.post(
        "/api/account/delete",
        headers=auth(),
        json={"business_name": "Alice Studio", "confirmed": True},
    )
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] is True
    assert client.get("/api/businesses/current", headers=auth()).status_code == 404
    retained = repository.list_ledger_entries()[0]
    assert retained.business_id is None
    assert retained.reference.startswith("sha256:")
    assert "private-receipt-reference" not in retained.reference
    assert repository.evidence_events["aggregate_deleted_accounts"]["businesses"] == 1
