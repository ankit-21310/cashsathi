from __future__ import annotations

import re
from dataclasses import dataclass

from cashsathi_api.domain import Invoice, ReminderIntent, ReminderLocale, ReminderTone


@dataclass(frozen=True, slots=True)
class ReminderMessage:
    subject: str
    body: str
    locale: ReminderLocale
    template_version: str


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

    if invoice.customer.locale == ReminderLocale.HI_IN:
        subject_prefix = (
            "भुगतान का विनम्र अनुस्मारक" if selected_tone == ReminderTone.WARM else "भुगतान अनुस्मारक"
        )
        subject = f"{subject_prefix}: चालान {invoice_number}"
        opening = f"नमस्ते {customer_name},"
        if selected_intent == ReminderIntent.DUE_SOON:
            detail = f"यह एक विनम्र अनुस्मारक है कि चालान {invoice_number} की देय तिथि निकट है।"
        elif selected_intent == ReminderIntent.DUE_TODAY:
            detail = f"यह एक अनुस्मारक है कि चालान {invoice_number} आज देय है।"
        else:
            detail = f"हमारे रिकॉर्ड के अनुसार चालान {invoice_number} की देय तिथि बीत चुकी है।"
        request = (
            "यदि भुगतान की व्यवस्था पहले ही हो चुकी है, या चालान से संबंधित किसी समस्या "
            "की हमें समीक्षा करनी चाहिए, तो कृपया हमें बताएं।"
        )
        closing = "आपके समय के लिए धन्यवाद।"
        return ReminderMessage(
            subject=subject[:160],
            body="\n\n".join((opening, detail, request, closing)),
            locale=ReminderLocale.HI_IN,
            template_version="reminder-hi-in-v1",
        )

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
        subject=subject[:160],
        body="\n\n".join((opening, detail, request, closing)),
        locale=ReminderLocale.EN_IN,
        template_version="reminder-en-in-v1",
    )
