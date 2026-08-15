from __future__ import annotations

import os
from datetime import UTC, date, datetime, timedelta
from io import BytesIO
from uuid import uuid4
from zipfile import ZipFile

import pytest

from cashsathi_api.config import Settings
from cashsathi_api.domain import (
    Action,
    ActionState,
    AgentDecision,
    AgentRun,
    AgentRunStatus,
    AuthenticatedUser,
    CustomerSnapshot,
    Invoice,
    InvoiceState,
    InvoiceWorkflowStatus,
    ModelDecision,
    Payment,
    PolicyOutcome,
    PolicyResult,
    ReminderIntent,
    ReminderTone,
    TenantContext,
)
from cashsathi_api.errors import ApiError
from cashsathi_api.phase9 import build_finance_readiness_zip
from cashsathi_api.repository import FirestoreRepository

pytestmark = [
    pytest.mark.firestore,
    pytest.mark.skipif(
        not os.getenv("FIRESTORE_EMULATOR_HOST"),
        reason="Firestore emulator is not running",
    ),
]


def _invoice(repo: FirestoreRepository, tenant: TenantContext, suffix: str) -> Invoice:
    extraction_id = repo.record_extraction(tenant, {"model_id": "contract-test"})
    now = datetime.now(UTC)
    invoice = Invoice(
        id=f"inv_{suffix}",
        business_id=tenant.business_id,
        extraction_id=extraction_id,
        invoice_number=f"INV-{suffix}",
        customer=CustomerSnapshot(
            id=f"cust_{suffix}",
            name=f"Customer {suffix}",
            email=f"{suffix}@example.test",
            manual_only=False,
        ),
        amount_minor=10_000,
        currency="INR",
        issue_date=date.today() - timedelta(days=30),
        due_date=date.today() - timedelta(days=1),
        payment_terms="Net 30",
        review_required=False,
        review_reason=None,
        confirmation_hash=f"hash-{suffix}",
        created_at=now,
        updated_at=now,
        next_check_at=now - timedelta(minutes=1),
        workflow_status=InvoiceWorkflowStatus.OPEN,
    )
    return repo.save_invoice(tenant, invoice)


def test_firestore_repository_contracts() -> None:
    settings = Settings(
        app_env="development",
        gcp_project_id="cashsathi-local",
        firestore_emulator_host=os.environ["FIRESTORE_EMULATOR_HOST"],
    )
    repo = FirestoreRepository(settings)
    nonce = uuid4().hex[:12]
    alice = AuthenticatedUser(f"firestore-alice-{nonce}", f"alice-{nonce}@example.test", "Alice")
    bob = AuthenticatedUser(f"firestore-bob-{nonce}", f"bob-{nonce}@example.test", "Bob")
    _alice_business, _alice_membership = repo.get_or_create_business(alice, "Firestore A")
    _bob_business, _bob_membership = repo.get_or_create_business(bob, "Firestore B")
    alice_tenant = repo.require_tenant(alice)
    bob_tenant = repo.require_tenant(bob)

    invoices = [_invoice(repo, alice_tenant, f"{nonce}-{index}") for index in range(3)]
    with pytest.raises(ApiError) as hidden:
        repo.get_invoice(bob_tenant, invoices[0].id)
    assert hidden.value.status_code == 404

    first_page, cursor = repo.list_invoices(alice_tenant, 2, None)
    assert len(first_page) == 2
    assert cursor is not None
    second_page, final_cursor = repo.list_invoices(alice_tenant, 2, cursor)
    assert len(second_page) == 1
    assert final_cursor is None

    now = datetime.now(UTC)
    proposal = ModelDecision(
        decision=AgentDecision.SEND_REMINDER,
        rationale="Bounded contract proposal.",
        reminder_tone=ReminderTone.WARM,
        reminder_intent=ReminderIntent.OVERDUE_FOLLOWUP,
    )
    proposed_run = AgentRun(
        id=f"run-{nonce}-1",
        invoice_id=invoices[0].id,
        business_id=alice_tenant.business_id,
        status=AgentRunStatus.PROPOSED,
        invoice_state=InvoiceState.OVERDUE,
        model_proposal=proposal,
        policy_result=None,
        model_id="contract-model",
        prompt_version="contract-v1",
        attempt_count=1,
        latency_ms=1,
        created_at=now,
        function_call_id=f"call-{nonce}-1",
        proposed_function="send_payment_reminder",
        function_arguments={
            "invoice_id": invoices[0].id,
            "tone": "WARM",
            "intent": "OVERDUE_FOLLOWUP",
            "risk_flags": [],
        },
    )
    repo.save_agent_proposal(alice_tenant, proposed_run)
    policy = PolicyResult(
        outcome=PolicyOutcome.REQUIRE_APPROVAL,
        final_decision=AgentDecision.SEND_REMINDER,
        matched_rules=["manual_approval"],
        requires_approval=True,
        next_check_at=None,
        policy_version="contract-v1",
    )
    final_run = proposed_run.model_copy(
        update={"status": AgentRunStatus.SUCCEEDED, "policy_result": policy}
    )
    action = Action(
        id=f"action-{nonce}-1",
        invoice_id=invoices[0].id,
        agent_run_id=final_run.id,
        state=ActionState.AWAITING_APPROVAL,
        created_at=now,
        recipient_email="recipient@example.test",
        subject="Private subject",
        body="Private deterministic reminder body",
        reminder_sequence=1,
    )
    reserved = repo.save_evaluation(alice_tenant, final_run, action)
    assert reserved is not None
    assert repo.get_invoice(alice_tenant, invoices[0].id).active_action_id == reserved.id

    second_proposal = proposed_run.model_copy(
        update={
            "id": f"run-{nonce}-2",
            "function_call_id": f"call-{nonce}-2",
            "created_at": now + timedelta(seconds=1),
        }
    )
    repo.save_agent_proposal(alice_tenant, second_proposal)
    duplicate = repo.save_evaluation(
        alice_tenant,
        second_proposal.model_copy(
            update={"status": AgentRunStatus.SUCCEEDED, "policy_result": policy}
        ),
        action.model_copy(update={"id": f"action-{nonce}-2", "agent_run_id": second_proposal.id}),
    )
    assert duplicate is not None
    assert duplicate.id == reserved.id

    payment = Payment(
        id=f"payment-{nonce}",
        invoice_id=invoices[1].id,
        business_id=alice_tenant.business_id,
        amount_minor=1_000,
        currency="INR",
        paid_at=now - timedelta(hours=1),
        reference="bank-reference",
        idempotency_key=f"idempotency-{nonce}",
        confirmed_by=alice.uid,
        created_at=now,
    )
    stored, updated_invoice = repo.record_payment(alice_tenant, payment)
    repeated, repeated_invoice = repo.record_payment(alice_tenant, payment)
    assert repeated.id == stored.id
    assert repeated_invoice.verified_paid_minor == updated_invoice.verified_paid_minor
    with pytest.raises(ApiError) as conflict:
        repo.record_payment(alice_tenant, payment.model_copy(update={"amount_minor": 2_000}))
    assert conflict.value.status_code == 409

    claimed = repo.claim_due_invoices(datetime.now(UTC), 20, 10)
    claimed_ids = {invoice.id for _tenant, invoice in claimed}
    assert invoices[2].id in claimed_ids
    assert repo.get_invoice(alice_tenant, invoices[2].id).evaluation_lease_until is not None

    finance_zip = build_finance_readiness_zip(repo, alice_tenant)
    with ZipFile(BytesIO(finance_zip)) as archive:
        exported = "\n".join(archive.read(name).decode("utf-8") for name in archive.namelist())
    assert "Private deterministic reminder body" not in exported
    assert "recipient@example.test" not in exported

    repo.delete_account(alice_tenant, retain_anonymous_metrics=False)
    business, membership = repo.get_account(alice)
    assert business is None
    assert membership is None
