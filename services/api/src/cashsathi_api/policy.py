from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from cashsathi_api.domain import (
    Action,
    ActionState,
    AgentDecision,
    Invoice,
    InvoiceState,
    ModelDecision,
    PolicyDefaults,
    PolicyOutcome,
    PolicyResult,
)

KOLKATA = ZoneInfo("Asia/Kolkata")


def _fallback_check(invoice: Invoice, now: datetime) -> datetime:
    if invoice.due_date:
        due_check = datetime.combine(invoice.due_date, time(hour=9), KOLKATA).astimezone(UTC)
        if due_check > now:
            return due_check
    return now + timedelta(hours=24)


def evaluate_policy(
    *,
    invoice: Invoice,
    invoice_state: InvoiceState,
    proposal: ModelDecision,
    settings: PolicyDefaults,
    actions: list[Action],
    policy_version: str,
    now: datetime | None = None,
) -> PolicyResult:
    current = now or datetime.now(UTC)
    matched: list[str] = []

    if invoice_state == InvoiceState.PAID:
        return PolicyResult(
            outcome=PolicyOutcome.ALLOW,
            final_decision=AgentDecision.CLOSE_AS_PAID,
            matched_rules=["verified_payment"],
            requires_approval=False,
            next_check_at=None,
            policy_version=policy_version,
        )
    if invoice_state == InvoiceState.DISPUTED:
        return PolicyResult(
            outcome=PolicyOutcome.BLOCK,
            final_decision=AgentDecision.REQUEST_HUMAN_REVIEW,
            matched_rules=["dispute_hard_stop"],
            requires_approval=True,
            next_check_at=None,
            policy_version=policy_version,
        )
    if invoice_state == InvoiceState.HUMAN_REVIEW:
        return PolicyResult(
            outcome=PolicyOutcome.BLOCK,
            final_decision=AgentDecision.REQUEST_HUMAN_REVIEW,
            matched_rules=["missing_or_unresolved_data"],
            requires_approval=True,
            next_check_at=None,
            policy_version=policy_version,
        )

    risk_flags = {flag.upper() for flag in proposal.risk_flags}
    if "LEGAL_LANGUAGE" in risk_flags and not settings.legal_language_allowed:
        return PolicyResult(
            outcome=PolicyOutcome.BLOCK,
            final_decision=AgentDecision.REQUEST_HUMAN_REVIEW,
            matched_rules=["legal_language_hard_stop"],
            requires_approval=True,
            next_check_at=None,
            policy_version=policy_version,
        )
    if proposal.decision == AgentDecision.CLOSE_AS_PAID:
        return PolicyResult(
            outcome=PolicyOutcome.BLOCK,
            final_decision=AgentDecision.REQUEST_HUMAN_REVIEW,
            matched_rules=["payment_confirmation_required"],
            requires_approval=True,
            next_check_at=None,
            policy_version=policy_version,
        )
    if proposal.decision == AgentDecision.SEND_REMINDER:
        if invoice.customer.email is None:
            return PolicyResult(
                outcome=PolicyOutcome.BLOCK,
                final_decision=AgentDecision.REQUEST_HUMAN_REVIEW,
                matched_rules=["customer_email_missing"],
                requires_approval=True,
                next_check_at=None,
                policy_version=policy_version,
            )
        prior_deliveries = [
            action
            for action in actions
            if action.state in {ActionState.SUCCEEDED, ActionState.UNKNOWN}
        ]
        if prior_deliveries:
            latest = max(action.created_at for action in prior_deliveries)
            cooldown_end = latest + timedelta(hours=settings.reminder_cooldown_hours)
            if cooldown_end > current:
                return PolicyResult(
                    outcome=PolicyOutcome.BLOCK,
                    final_decision=AgentDecision.SCHEDULE_RECHECK,
                    matched_rules=["reminder_cooldown"],
                    requires_approval=False,
                    next_check_at=cooldown_end,
                    policy_version=policy_version,
                )
        if invoice.customer.manual_only:
            matched.append("manual_only_customer")
        if invoice.currency != settings.high_value_currency:
            matched.append("non_inr_requires_approval")
        elif invoice.amount_minor >= settings.high_value_threshold_minor:
            matched.append("high_value_requires_approval")
        if proposal.requires_human_approval:
            matched.append("model_requested_approval")
        requires_approval = bool(matched)
        return PolicyResult(
            outcome=(PolicyOutcome.REQUIRE_APPROVAL if requires_approval else PolicyOutcome.ALLOW),
            final_decision=AgentDecision.SEND_REMINDER,
            matched_rules=matched,
            requires_approval=requires_approval,
            next_check_at=proposal.next_check_at,
            policy_version=policy_version,
        )

    next_check = proposal.next_check_at
    if proposal.decision in {
        AgentDecision.WAIT,
        AgentDecision.SCHEDULE_RECHECK,
    } and (next_check is None or next_check <= current):
        next_check = _fallback_check(invoice, current)
        matched.append("deterministic_next_check")
    return PolicyResult(
        outcome=PolicyOutcome.ALLOW,
        final_decision=proposal.decision,
        matched_rules=matched,
        requires_approval=proposal.decision == AgentDecision.REQUEST_HUMAN_REVIEW,
        next_check_at=next_check,
        policy_version=policy_version,
    )
