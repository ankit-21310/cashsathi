from datetime import UTC, date, datetime, timedelta

from cashsathi_api.domain import (
    Action,
    ActionState,
    AgentDecision,
    CustomerSnapshot,
    Invoice,
    InvoiceState,
    ModelDecision,
    PolicyDefaults,
    PolicyOutcome,
)
from cashsathi_api.policy import evaluate_policy
from cashsathi_api.state_engine import calculate_invoice_state

NOW = datetime(2026, 8, 14, 8, tzinfo=UTC)


def invoice(**overrides: object) -> Invoice:
    data: dict[str, object] = {
        "id": "inv_1",
        "business_id": "biz_1",
        "extraction_id": "ext_1",
        "invoice_number": "INV-1",
        "customer": CustomerSnapshot(
            id="cust_1", name="Client", email="billing@example.com", manual_only=False
        ),
        "amount_minor": 100_000,
        "currency": "INR",
        "issue_date": date(2026, 7, 1),
        "due_date": date(2026, 8, 10),
        "payment_terms": "Net 30",
        "review_required": False,
        "review_reason": None,
        "confirmation_hash": "hash",
        "created_at": NOW,
        "updated_at": NOW,
    }
    data.update(overrides)
    return Invoice.model_validate(data)


def proposal(decision: AgentDecision = AgentDecision.SEND_REMINDER) -> ModelDecision:
    return ModelDecision(decision=decision, rationale="Safe internal rationale.")


def policy(
    target: Invoice, state: InvoiceState, model: ModelDecision, actions: list[Action] | None = None
):  # type: ignore[no-untyped-def]
    return evaluate_policy(
        invoice=target,
        invoice_state=state,
        proposal=model,
        settings=PolicyDefaults(),
        actions=actions or [],
        policy_version="test-policy-v1",
        now=NOW,
    )


def test_state_precedence_is_deterministic() -> None:
    assert (
        calculate_invoice_state(invoice(verified_paid_minor=100_000), now=NOW) == InvoiceState.PAID
    )
    assert calculate_invoice_state(invoice(dispute_active=True), now=NOW) == InvoiceState.DISPUTED
    assert (
        calculate_invoice_state(invoice(due_date=None, review_required=True), now=NOW)
        == InvoiceState.HUMAN_REVIEW
    )
    assert (
        calculate_invoice_state(invoice(due_date=date(2026, 8, 15)), now=NOW)
        == InvoiceState.UPCOMING
    )
    assert calculate_invoice_state(invoice(due_date=date(2026, 8, 14)), now=NOW) == InvoiceState.DUE
    assert calculate_invoice_state(invoice(), now=NOW) == InvoiceState.OVERDUE


def test_waiting_for_reply_respects_the_configured_cooldown() -> None:
    delivered = Action(
        id="act_1",
        invoice_id="inv_1",
        agent_run_id="run_1",
        state=ActionState.SUCCEEDED,
        created_at=NOW - timedelta(hours=100),
        execution_completed_at=NOW - timedelta(hours=100),
    )
    # 100 hours since delivery: past the default 72h cooldown, but still inside a
    # business-configured 168h (weekly) cooldown, so the two must disagree.
    assert calculate_invoice_state(invoice(), [delivered], now=NOW) == InvoiceState.OVERDUE
    assert (
        calculate_invoice_state(invoice(), [delivered], now=NOW, cooldown_hours=168)
        == InvoiceState.WAITING_FOR_REPLY
    )


def test_paid_disputed_and_legal_risks_are_hard_stops() -> None:
    assert (
        policy(invoice(verified_paid_minor=100_000), InvoiceState.PAID, proposal()).final_decision
        == AgentDecision.CLOSE_AS_PAID
    )
    disputed = policy(invoice(dispute_active=True), InvoiceState.DISPUTED, proposal())
    assert disputed.outcome == PolicyOutcome.BLOCK
    legal = policy(
        invoice(),
        InvoiceState.OVERDUE,
        ModelDecision(
            decision=AgentDecision.SEND_REMINDER,
            rationale="Internal.",
            risk_flags=["LEGAL_LANGUAGE"],
        ),
    )
    assert legal.final_decision == AgentDecision.REQUEST_HUMAN_REVIEW


def test_reminder_approval_and_cooldown_rules() -> None:
    high_value = policy(invoice(amount_minor=5_000_000), InvoiceState.OVERDUE, proposal())
    assert high_value.outcome == PolicyOutcome.REQUIRE_APPROVAL
    non_inr = policy(invoice(currency="USD"), InvoiceState.OVERDUE, proposal())
    assert "non_inr_requires_approval" in non_inr.matched_rules
    manual = policy(
        invoice(
            customer=CustomerSnapshot(
                id="cust_1", name="Client", email="billing@example.com", manual_only=True
            )
        ),
        InvoiceState.OVERDUE,
        proposal(),
    )
    assert manual.requires_approval

    prior = Action(
        id="act_1",
        invoice_id="inv_1",
        agent_run_id="run_1",
        state=ActionState.UNKNOWN,
        created_at=NOW - timedelta(hours=1),
    )
    cooldown = policy(invoice(), InvoiceState.OVERDUE, proposal(), [prior])
    assert cooldown.final_decision == AgentDecision.SCHEDULE_RECHECK
    assert cooldown.next_check_at == prior.created_at + timedelta(hours=72)


def test_missing_email_and_unverified_payment_never_create_reminders_or_close() -> None:
    no_email = invoice(
        customer=CustomerSnapshot(id="cust_1", name="Client", email=None, manual_only=False)
    )
    blocked = policy(no_email, InvoiceState.OVERDUE, proposal())
    assert blocked.final_decision == AgentDecision.REQUEST_HUMAN_REVIEW
    close = policy(invoice(), InvoiceState.OVERDUE, proposal(AgentDecision.CLOSE_AS_PAID))
    assert close.outcome == PolicyOutcome.BLOCK
