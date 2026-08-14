from __future__ import annotations

import re
from dataclasses import dataclass

from cashsathi_api.domain import Invoice, ReminderIntent, ReminderTone


@dataclass(frozen=True, slots=True)
class ReminderMessage:
    subject: str
    body: str


def _inline(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def render_reminder(
    invoice: Invoice,
    *,
    tone: ReminderTone | None,
    intent: ReminderIntent | None,
) -> ReminderMessage:
    """Render policy-owned copy; model output only selects bounded variants."""
    invoice_number = _inline(invoice.invoice_number)
    customer_name = _inline(invoice.customer.name)
    selected_tone = tone or ReminderTone.WARM
    selected_intent = intent or ReminderIntent.OVERDUE_FOLLOWUP

    subject_prefix = (
        "Friendly reminder" if selected_tone == ReminderTone.WARM else "Invoice reminder"
    )
    subject = f"{subject_prefix}: invoice {invoice_number}"

    opening = f"Hello {customer_name},"
    if selected_intent == ReminderIntent.DUE_SOON:
        detail = f"This is a friendly reminder that invoice {invoice_number} is due soon."
    elif selected_intent == ReminderIntent.DUE_TODAY:
        detail = f"This is a reminder that invoice {invoice_number} is due today."
    else:
        detail = f"Our records show that invoice {invoice_number} is currently overdue."
    request = (
        "Please let us know if payment has already been arranged or if there is any issue "
        "with the invoice that we should review."
    )
    closing = "Thank you for your time."
    return ReminderMessage(
        subject=subject[:160], body="\n\n".join((opening, detail, request, closing))
    )
