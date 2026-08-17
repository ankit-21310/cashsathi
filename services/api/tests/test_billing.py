from dataclasses import replace
from datetime import UTC, datetime

import pytest
from conftest import TestPaymentGateway
from fastapi.testclient import TestClient

from cashsathi_api.billing import ProviderPayment
from cashsathi_api.domain import (
    BillingAccessMode,
    CustomerSnapshot,
    Invoice,
    MembershipRole,
    TenantContext,
)
from cashsathi_api.errors import ApiError
from cashsathi_api.repository import InMemoryRepository


def auth(token: str = "alice-token") -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def onboard(client: TestClient) -> dict[str, object]:
    response = client.post("/api/businesses", headers=auth(), json={"name": "Billing Studio"})
    assert response.status_code == 200
    return response.json()


def create_checkout(
    client: TestClient, gateway: TestPaymentGateway, payment_id: str = "pay_test_1"
) -> tuple[dict[str, object], ProviderPayment]:
    order_response = client.post(
        "/api/billing/orders",
        headers=auth(),
        json={"idempotency_key": "billing-checkout-0001"},
    )
    assert order_response.status_code == 200
    order = order_response.json()["order"]
    payment = ProviderPayment(
        id=payment_id,
        order_id=str(order["provider_order_id"]),
        amount=29_900,
        currency="INR",
        status="captured",
        amount_refunded=0,
        fee=600,
        tax=108,
        method="upi",
        email="alice@example.com",
        error_code=None,
        error_description=None,
        created_at=1_787_000_000,
    )
    gateway.payments[payment_id] = payment
    return order, payment


def test_checkout_capture_is_server_priced_idempotent_and_admin_private(
    client: TestClient, repository: InMemoryRepository, payment_gateway: TestPaymentGateway
) -> None:
    onboard(client)
    order, payment = create_checkout(client, payment_gateway)
    replay_order = client.post(
        "/api/billing/orders",
        headers=auth(),
        json={"idempotency_key": "a-different-retry-key"},
    )
    assert replay_order.status_code == 200
    assert replay_order.json()["order"]["id"] == order["id"]

    payload = {
        "provider_order_id": order["provider_order_id"],
        "provider_payment_id": payment.id,
        "signature": "valid-signature-test",
    }
    first = client.post("/api/billing/confirm", headers=auth(), json=payload)
    replay = client.post("/api/billing/confirm", headers=auth(), json=payload)
    assert first.status_code == replay.status_code == 200
    assert first.json()["status"] == "CAPTURED"
    assert first.json()["plan"]["source"] == "RAZORPAY"
    assert len(repository.list_ledger_entries()) == 1
    assert len(repository.list_billing_transactions()) == 1

    summary = client.get("/api/admin/revenue/summary", headers=auth())
    assert summary.status_code == 200
    assert summary.json()["gross_captured_minor"] == 29_900
    assert summary.json()["provider_fees_minor"] == 600
    assert client.get("/api/admin/revenue/summary", headers=auth("bob-token")).status_code == 403


def test_invalid_checkout_signature_fails_closed(
    client: TestClient, payment_gateway: TestPaymentGateway
) -> None:
    onboard(client)
    order, payment = create_checkout(client, payment_gateway)
    response = client.post(
        "/api/billing/confirm",
        headers=auth(),
        json={
            "provider_order_id": order["provider_order_id"],
            "provider_payment_id": payment.id,
            "signature": "tampered-signature",
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_payment_signature"


def test_late_capture_supersedes_a_failed_attempt(
    client: TestClient, payment_gateway: TestPaymentGateway
) -> None:
    onboard(client)
    order, payment = create_checkout(client, payment_gateway)
    payment_gateway.payments[payment.id] = replace(
        payment,
        status="failed",
        error_code="BAD_REQUEST_ERROR",
        error_description="Payment attempt failed",
    )
    callback = {
        "provider_order_id": order["provider_order_id"],
        "provider_payment_id": payment.id,
        "signature": "valid-signature-test",
    }
    failed = client.post("/api/billing/confirm", headers=auth(), json=callback)
    assert failed.status_code == 409

    payment_gateway.payments[payment.id] = payment
    captured = client.post("/api/billing/confirm", headers=auth(), json=callback)
    assert captured.status_code == 200
    assert captured.json()["status"] == "CAPTURED"
    assert captured.json()["plan"]["status"] == "ACTIVE"


def test_refund_webhook_reduces_revenue_and_suspends_plan(
    client: TestClient, payment_gateway: TestPaymentGateway
) -> None:
    onboard(client)
    order, payment = create_checkout(client, payment_gateway)
    captured = client.post(
        "/api/billing/confirm",
        headers=auth(),
        json={
            "provider_order_id": order["provider_order_id"],
            "provider_payment_id": payment.id,
            "signature": "valid-signature-test",
        },
    )
    assert captured.status_code == 200
    payment_gateway.payments[payment.id] = replace(payment, amount_refunded=29_900)
    webhook = {
        "event": "refund.processed",
        "payload": {
            "refund": {
                "entity": {
                    "id": "rfnd_test_1",
                    "payment_id": payment.id,
                    "amount": 29_900,
                    "currency": "INR",
                    "status": "processed",
                }
            }
        },
    }
    response = client.post(
        "/api/webhooks/razorpay",
        headers={
            "X-Razorpay-Signature": "valid-webhook",
            "X-Razorpay-Event-Id": "event-refund-1",
        },
        json=webhook,
    )
    replay = client.post(
        "/api/webhooks/razorpay",
        headers={
            "X-Razorpay-Signature": "valid-webhook",
            "X-Razorpay-Event-Id": "event-refund-1",
        },
        json=webhook,
    )
    assert response.status_code == replay.status_code == 200
    billing = client.get("/api/billing/current", headers=auth()).json()
    assert billing["plan"]["status"] == "REFUNDED"
    summary = client.get("/api/admin/revenue/summary", headers=auth()).json()
    assert summary["net_captured_minor"] == 0
    assert summary["refunded_plans"] == 1


def test_required_workspace_blocks_confirmation_until_plan_exists(
    client: TestClient, repository: InMemoryRepository
) -> None:
    business = onboard(client)
    business_id = str(business["id"])
    repository.businesses[business_id] = repository.businesses[business_id].model_copy(
        update={"billing_access_mode": BillingAccessMode.REQUIRED}
    )
    tenant = TenantContext("alice", business_id, MembershipRole.OWNER)
    extraction_id = repository.record_extraction(tenant, {"test": True})
    now = datetime.now(UTC)
    invoice = Invoice(
        id="invoice-paywall",
        business_id=business_id,
        extraction_id=extraction_id,
        invoice_number="PAYWALL-1",
        customer=CustomerSnapshot(id="customer-1", name="Client", email=None, manual_only=False),
        amount_minor=10_000,
        currency="INR",
        issue_date=None,
        due_date=None,
        payment_terms=None,
        review_required=False,
        review_reason=None,
        confirmation_hash="confirmation-hash",
        created_at=now,
        updated_at=now,
    )
    with pytest.raises(ApiError) as exc_info:
        repository.save_invoice(tenant, invoice)
    assert exc_info.value.status_code == 402
    assert exc_info.value.code == "payment_required"


def test_invalid_webhook_signature_does_not_record_event(client: TestClient) -> None:
    response = client.post(
        "/api/webhooks/razorpay",
        headers={
            "X-Razorpay-Signature": "wrong",
            "X-Razorpay-Event-Id": "event-invalid",
        },
        json={"event": "payment.failed"},
    )
    assert response.status_code == 401
