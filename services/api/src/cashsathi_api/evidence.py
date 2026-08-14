from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from cashsathi_api.domain import (
    Action,
    ActionState,
    AdminImpactResponse,
    BusinessRelationship,
    CurrencyMetrics,
    DataClassification,
    InvoiceTimeline,
    LedgerKind,
    MembershipRole,
    MetricsResponse,
    Payment,
    TenantContext,
    TimelineEntry,
)
from cashsathi_api.repository import Repository
from cashsathi_api.state_engine import calculate_invoice_state


def calculate_metrics(repo: Repository, tenant: TenantContext | None = None) -> MetricsResponse:
    invoices = repo.list_all_invoices(tenant)
    runs = repo.list_all_agent_runs(tenant)
    actions = repo.list_all_action_records(tenant)
    payments = repo.list_payments(tenant) if tenant else []
    if tenant is None:
        payments = []
        for business in repo.list_businesses():
            system_tenant = TenantContext(
                user_id="platform-metrics",
                business_id=business.id,
                role=MembershipRole.OWNER,
            )
            payments.extend(repo.list_payments(system_tenant))

    actions_by_invoice: dict[str, list[Action]] = defaultdict(list)
    for action in actions:
        actions_by_invoice[action.invoice_id].append(action)
    payments_by_invoice: dict[str, list[Payment]] = defaultdict(list)
    for payment in payments:
        payments_by_invoice[payment.invoice_id].append(payment)

    totals: dict[str, dict[str, int]] = defaultdict(
        lambda: {
            "monitored_minor": 0,
            "outstanding_minor": 0,
            "overdue_minor": 0,
            "verified_paid_minor": 0,
            "post_action_paid_minor": 0,
        }
    )
    now = datetime.now(UTC)
    local_today = now.astimezone(ZoneInfo("Asia/Kolkata")).date()
    overdue_days: list[int] = []
    overdue_count = 0
    review_count = 0
    for invoice in invoices:
        amount = totals[invoice.currency]
        balance = max(invoice.amount_minor - invoice.verified_paid_minor, 0)
        amount["monitored_minor"] += invoice.amount_minor
        amount["outstanding_minor"] += balance
        amount["verified_paid_minor"] += invoice.verified_paid_minor
        state = calculate_invoice_state(invoice, actions_by_invoice[invoice.id], now)
        if state.value == "OVERDUE":
            overdue_count += 1
            amount["overdue_minor"] += balance
            if invoice.due_date:
                overdue_days.append((local_today - invoice.due_date).days)
        if state.value == "HUMAN_REVIEW":
            review_count += 1
        delivered_at = [
            action.execution_completed_at or action.created_at
            for action in actions_by_invoice[invoice.id]
            if action.state == ActionState.SUCCEEDED
        ]
        if delivered_at:
            first_delivery = min(delivered_at)
            amount["post_action_paid_minor"] += sum(
                payment.amount_minor
                for payment in payments_by_invoice[invoice.id]
                if payment.paid_at >= first_delivery
            )

    successful = [action for action in actions if action.state == ActionState.SUCCEEDED]
    automatic = [action for action in successful if action.automatic]
    return MetricsResponse(
        currencies=[
            CurrencyMetrics(currency=currency, **values)
            for currency, values in sorted(totals.items())
        ],
        invoice_count=len(invoices),
        overdue_count=overdue_count,
        human_review_count=review_count,
        ai_decisions=sum(1 for run in runs if run.model_proposal is not None),
        successful_actions=len(successful),
        automatic_successful_actions=len(automatic),
        automation_rate=round(len(automatic) / len(successful), 4) if successful else 0,
        average_days_overdue=(
            round(sum(overdue_days) / len(overdue_days), 1) if overdue_days else 0
        ),
        pending_approvals=sum(
            1 for action in actions if action.state == ActionState.AWAITING_APPROVAL
        ),
    )


def build_timeline(repo: Repository, tenant: TenantContext, invoice_id: str) -> InvoiceTimeline:
    invoice = repo.get_invoice(tenant, invoice_id)
    actions = repo.list_actions(tenant, invoice_id)
    runs, _ = repo.list_agent_runs(tenant, invoice_id, 100, None)
    payments = repo.list_payments(tenant, invoice_id)
    items = [
        TimelineEntry(
            id=invoice.extraction_id,
            entry_type="EXTRACTION",
            title="Invoice facts extracted",
            detail="Gemini extraction completed; the original PDF was not retained.",
            occurred_at=invoice.created_at,
            status="CONFIRMED",
        ),
        TimelineEntry(
            id=f"confirmed_{invoice.id}",
            entry_type="INVOICE",
            title="Invoice confirmed",
            detail=f"Invoice {invoice.invoice_number} was confirmed by the owner.",
            occurred_at=invoice.created_at,
            status="CONFIRMED",
        ),
    ]
    for run in runs:
        decision = run.policy_result.final_decision.value if run.policy_result else run.status.value
        items.append(
            TimelineEntry(
                id=run.id,
                entry_type="AGENT_RUN",
                title=f"Agent decision: {decision.replace('_', ' ').title()}",
                detail=(
                    run.model_proposal.rationale
                    if run.model_proposal
                    else "The model request failed before a decision was produced."
                ),
                occurred_at=run.created_at,
                status=run.status.value,
            )
        )
    for action in actions:
        items.append(
            TimelineEntry(
                id=action.id,
                entry_type="ACTION",
                title=f"Reminder {action.state.value.replace('_', ' ').lower()}",
                detail=(
                    action.failure_message
                    or (
                        "Provider delivery ID recorded."
                        if action.provider_message_id
                        else "Action recorded."
                    )
                ),
                occurred_at=action.execution_completed_at or action.created_at,
                status=action.state.value,
            )
        )
    for payment in payments:
        items.append(
            TimelineEntry(
                id=payment.id,
                entry_type="PAYMENT",
                title="Verified payment recorded",
                detail=f"Owner reference: {payment.reference}",
                occurred_at=payment.created_at,
                status="VERIFIED",
            )
        )
    return InvoiceTimeline(items=sorted(items, key=lambda item: item.occurred_at))


def admin_impact(repo: Repository) -> AdminImpactResponse:
    businesses = repo.list_businesses()
    business_map = {business.id: business for business in businesses}
    revenue: dict[str, int] = defaultdict(int)
    related_revenue: dict[str, int] = defaultdict(int)
    expenses: dict[str, int] = defaultdict(int)
    marketing: dict[str, int] = defaultdict(int)
    arms_net: dict[tuple[str, str], int] = defaultdict(int)
    related_net: dict[tuple[str, str], int] = defaultdict(int)
    entries = repo.list_ledger_entries()
    by_id = {entry.id: entry for entry in entries}
    for entry in entries:
        sign = -1 if entry.reversal_of else 1
        if entry.reversal_of and entry.reversal_of not in by_id:
            continue
        amount = entry.amount_minor * sign
        if entry.kind == LedgerKind.EXPENSE:
            expenses[entry.currency] += amount
            if entry.marketing:
                marketing[entry.currency] += amount
            continue
        business = business_map.get(entry.business_id or "")
        if not business:
            continue
        if (
            business.data_classification == DataClassification.REAL
            and business.relationship == BusinessRelationship.ARMS_LENGTH
        ):
            revenue[entry.currency] += amount
            arms_net[(business.id, entry.currency)] += amount
        elif business.relationship == BusinessRelationship.RELATED:
            related_revenue[entry.currency] += amount
            related_net[(business.id, entry.currency)] += amount
    paying = {business_id for (business_id, _currency), amount in arms_net.items() if amount > 0}
    related_paying = {
        business_id for (business_id, _currency), amount in related_net.items() if amount > 0
    }
    return AdminImpactResponse(
        paying_businesses=len(paying),
        related_paying_businesses=len(related_paying),
        real_businesses=sum(
            1 for business in businesses if business.data_classification == DataClassification.REAL
        ),
        demo_businesses=sum(
            1 for business in businesses if business.data_classification == DataClassification.DEMO
        ),
        unclassified_businesses=sum(
            1
            for business in businesses
            if business.data_classification == DataClassification.UNCLASSIFIED
        ),
        revenue_by_currency=dict(revenue),
        related_revenue_by_currency=dict(related_revenue),
        expenses_by_currency=dict(expenses),
        marketing_spend_by_currency=dict(marketing),
        operational=calculate_metrics(repo),
    )
