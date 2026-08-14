from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from cashsathi_api.domain import Action, ActionState, Invoice, InvoiceState

KOLKATA = ZoneInfo("Asia/Kolkata")


def calculate_invoice_state(
    invoice: Invoice, actions: list[Action] | None = None, now: datetime | None = None
) -> InvoiceState:
    current = now or datetime.now(UTC)
    if invoice.verified_paid_minor >= invoice.amount_minor:
        return InvoiceState.PAID
    if invoice.dispute_active:
        return InvoiceState.DISPUTED
    if invoice.review_required or invoice.due_date is None:
        return InvoiceState.HUMAN_REVIEW
    recent = actions or []
    waiting = any(
        action.state in {ActionState.SUCCEEDED, ActionState.UNKNOWN}
        and (current - action.created_at).total_seconds() < 72 * 3600
        for action in recent
    )
    if waiting:
        return InvoiceState.WAITING_FOR_REPLY
    today = current.astimezone(KOLKATA).date()
    if invoice.due_date > today:
        return InvoiceState.UPCOMING
    if invoice.due_date == today:
        return InvoiceState.DUE
    return InvoiceState.OVERDUE
