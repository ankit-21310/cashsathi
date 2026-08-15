from __future__ import annotations

import io
import json
import statistics
import zipfile
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from cashsathi_api.domain import (
    AccountingConnectionState,
    AccountingIntegrationPage,
    AccountingIntegrationStatus,
    AccountingProvider,
    CashForecast,
    ForecastBucket,
    ForecastHorizon,
    InvoiceWorkflowStatus,
    PolicyDefaults,
    PolicyTemplateKind,
    PolicyTemplatePage,
    PolicyTemplateSummary,
    TenantContext,
)
from cashsathi_api.repository import Repository

KOLKATA = ZoneInfo("Asia/Kolkata")
FORECAST_METHOD_VERSION = "deterministic-due-date-delay-p50-v1"
FINANCE_PACK_SCHEMA_VERSION = 1

POLICY_TEMPLATES: dict[PolicyTemplateKind, tuple[str, int, int]] = {
    PolicyTemplateKind.AGENCY: ("Agency", 96, 4_000_000),
    PolicyTemplateKind.CONSULTANT: ("Consultant", 120, 2_500_000),
    PolicyTemplateKind.MANUFACTURER: ("Manufacturer", 168, 5_000_000),
}


def restrictive_policy_templates(current: PolicyDefaults) -> PolicyTemplatePage:
    return PolicyTemplatePage(
        items=[
            PolicyTemplateSummary(
                template=template,
                label=label,
                reminder_cooldown_hours=max(current.reminder_cooldown_hours, cooldown),
                high_value_threshold_minor=min(current.high_value_threshold_minor, threshold_minor),
            )
            for template, (label, cooldown, threshold_minor) in POLICY_TEMPLATES.items()
        ]
    )


def _observed_delay_days(repo: Repository, tenant: TenantContext) -> int:
    invoices = {invoice.id: invoice for invoice in repo.list_all_invoices(tenant)}
    last_payment_dates: dict[str, date] = {}
    for payment in repo.list_payments(tenant):
        local_date = payment.paid_at.astimezone(KOLKATA).date()
        prior = last_payment_dates.get(payment.invoice_id)
        if prior is None or local_date > prior:
            last_payment_dates[payment.invoice_id] = local_date
    delays = []
    for invoice_id, paid_on in last_payment_dates.items():
        invoice = invoices.get(invoice_id)
        if (
            invoice is not None
            and invoice.due_date is not None
            and invoice.verified_paid_minor >= invoice.amount_minor
        ):
            delays.append(max((paid_on - invoice.due_date).days, 0))
    return int(statistics.median_low(delays)) if delays else 0


def build_cash_forecast(
    repo: Repository, tenant: TenantContext, now: datetime | None = None
) -> CashForecast:
    generated_at = now or datetime.now(UTC)
    starts_on = generated_at.astimezone(KOLKATA).date()
    delay_days = _observed_delay_days(repo, tenant)
    inflows: list[dict[str, int]] = [defaultdict(int) for _ in range(12)]
    counts = [0 for _ in range(12)]
    for invoice in repo.list_all_invoices(tenant):
        outstanding = max(invoice.amount_minor - invoice.verified_paid_minor, 0)
        if (
            outstanding == 0
            or invoice.workflow_status == InvoiceWorkflowStatus.CLOSED
            or invoice.due_date is None
        ):
            continue
        expected_on = invoice.due_date + timedelta(days=delay_days)
        bucket_index = max((expected_on - starts_on).days // 7, 0)
        if bucket_index >= 12:
            continue
        inflows[bucket_index][invoice.currency] += outstanding
        counts[bucket_index] += 1
    all_buckets = [
        ForecastBucket(
            starts_on=starts_on + timedelta(days=index * 7),
            ends_on=starts_on + timedelta(days=index * 7 + 6),
            expected_inflow_by_currency=dict(sorted(inflows[index].items())),
            invoice_count=counts[index],
        )
        for index in range(12)
    ]
    horizons: list[ForecastHorizon] = []
    for weeks in (4, 8, 12):
        totals: dict[str, int] = defaultdict(int)
        for bucket in all_buckets[:weeks]:
            for currency, amount in bucket.expected_inflow_by_currency.items():
                totals[currency] += amount
        horizons.append(
            ForecastHorizon(
                weeks=weeks,
                expected_inflow_by_currency=dict(sorted(totals.items())),
                buckets=all_buckets[:weeks],
            )
        )
    return CashForecast(
        generated_at=generated_at,
        methodology_version=FORECAST_METHOD_VERSION,
        observed_payment_delay_days=delay_days,
        horizons=horizons,
    )


def accounting_integration_statuses() -> AccountingIntegrationPage:
    return AccountingIntegrationPage(
        items=[
            AccountingIntegrationStatus(
                provider=provider,
                state=AccountingConnectionState.NOT_CONFIGURED,
            )
            for provider in (AccountingProvider.ZOHO_BOOKS, AccountingProvider.TALLY_PRIME)
        ]
    )


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, indent=2, ensure_ascii=False).encode("utf-8")


def build_finance_readiness_zip(repo: Repository, tenant: TenantContext) -> bytes:
    generated_at = datetime.now(UTC)
    invoices = repo.list_all_invoices(tenant)
    payments = repo.list_payments(tenant)
    actions = repo.list_all_action_records(tenant)
    runs = repo.list_all_agent_runs(tenant)
    forecast = build_cash_forecast(repo, tenant, generated_at)
    aging_rows = [
        {
            "invoice_id": invoice.id,
            "invoice_number": invoice.invoice_number,
            "customer_id": invoice.customer.id,
            "customer_name": invoice.customer.name,
            "currency": invoice.currency,
            "amount_minor": invoice.amount_minor,
            "verified_paid_minor": invoice.verified_paid_minor,
            "outstanding_minor": max(invoice.amount_minor - invoice.verified_paid_minor, 0),
            "due_date": invoice.due_date.isoformat() if invoice.due_date else None,
            "workflow_status": invoice.workflow_status.value,
            "dispute_active": invoice.dispute_active,
        }
        for invoice in invoices
    ]
    payment_rows = [
        {
            "payment_id": payment.id,
            "invoice_id": payment.invoice_id,
            "amount_minor": payment.amount_minor,
            "currency": payment.currency,
            "paid_at": payment.paid_at.isoformat(),
            "recorded_at": payment.created_at.isoformat(),
            "reference": payment.reference,
        }
        for payment in payments
    ]
    action_rows = [
        {
            "action_id": action.id,
            "invoice_id": action.invoice_id,
            "state": action.state.value,
            "automatic": action.automatic,
            "provider_confirmed": bool(action.provider_message_id),
            "locale": action.locale.value,
            "template_version": action.template_version,
            "created_at": action.created_at.isoformat(),
        }
        for action in actions
    ]
    policy_rows = [
        {
            "agent_run_id": run.id,
            "invoice_id": run.invoice_id,
            "proposed_function": run.proposed_function,
            "policy_outcome": run.policy_result.outcome.value if run.policy_result else None,
            "final_decision": (
                run.policy_result.final_decision.value if run.policy_result else None
            ),
            "matched_rules": run.policy_result.matched_rules if run.policy_result else [],
            "created_at": run.created_at.isoformat(),
        }
        for run in runs
    ]
    methodology = {
        "schema_version": FINANCE_PACK_SCHEMA_VERSION,
        "generated_at": generated_at.isoformat(),
        "payment_definition": "Only owner-confirmed payment records are counted.",
        "forecast": (
            "Expected dates equal confirmed invoice due dates plus the observed median "
            "non-negative payment delay. No model can alter forecast numbers."
        ),
        "privacy": (
            "Reminder subjects/bodies, recipient emails, dispute notes, credentials, tokens, "
            "and provider payloads are excluded."
        ),
    }
    manifest = {
        "schema_version": FINANCE_PACK_SCHEMA_VERSION,
        "generated_at": generated_at.isoformat(),
        "files": [
            "methodology.json",
            "aging.json",
            "verified_payments.json",
            "policy_action_history.json",
            "forecast.json",
        ],
        "counts": {
            "invoices": len(aging_rows),
            "payments": len(payment_rows),
            "actions": len(action_rows),
            "agent_runs": len(policy_rows),
        },
    }
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", _json_bytes(manifest))
        archive.writestr("methodology.json", _json_bytes(methodology))
        archive.writestr("aging.json", _json_bytes(aging_rows))
        archive.writestr("verified_payments.json", _json_bytes(payment_rows))
        archive.writestr(
            "policy_action_history.json",
            _json_bytes({"policy_runs": policy_rows, "actions": action_rows}),
        )
        archive.writestr("forecast.json", _json_bytes(forecast.model_dump(mode="json")))
    return output.getvalue()
