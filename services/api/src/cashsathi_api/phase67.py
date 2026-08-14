from __future__ import annotations

import csv
import hashlib
import io
import json
import zipfile
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any

from cashsathi_api.domain import (
    AccountExport,
    Business,
    BusinessRelationship,
    ConsentEventAction,
    DataClassification,
    EvidenceLedgerEntry,
    FounderPlanEnrollment,
    Interview,
    LedgerKind,
    MembershipRole,
    OptionalConsentDefinition,
    OptionalConsentEvent,
    OptionalConsentResponse,
    OptionalConsentType,
    Prospect,
    ProspectStatus,
    TenantContext,
)
from cashsathi_api.errors import ApiError
from cashsathi_api.repository import Repository

OPTIONAL_CONSENTS: dict[OptionalConsentType, tuple[str, str]] = {
    OptionalConsentType.ANONYMIZED_METRICS: (
        "2026-08-14.v1",
        "I permit de-identified usage and outcome metrics to be aggregated for product "
        "evaluation and competition reporting. No invoice, customer, or business identity "
        "is disclosed through this permission.",
    ),
    OptionalConsentType.TESTIMONIAL: (
        "2026-08-14.v1",
        "I permit the exact feedback text I approve to be used only in the selected channels.",
    ),
    OptionalConsentType.IDENTITY_DISCLOSURE: (
        "2026-08-14.v1",
        "I permit the approved business identity details to accompany customer evidence only "
        "in the selected channels.",
    ),
}

SUBMISSION_REPORT_MONTHS = tuple(date(2026, month, 1) for month in range(5, 9))
EVIDENCE_README = """CashSathi sanitized evidence export - schema version 2

Money is stored as integer minor units and currencies are never converted. The monthly P&L
covers May through August 2026. Revenue is separated into arms-length, related-party,
pre-existing, and unclassified categories; unclassified revenue must not be claimed as
arms-length demand. Reversals are applied only when their original ledger entry exists.

Operational metrics come from confirmed invoices, validated agent runs, recorded action
results, and owner-confirmed payments. A payment recorded after an action establishes timing,
not causation. Testimonials and identity fields appear only while the corresponding consent
grant is active. Business and invoice references are pseudonymous or hashed, and this archive
does not include business names, customer identities, reminder bodies, OAuth data, receipt
references, or raw model/provider responses.

Source collections: businesses, prospects, interviews, founder plans, evidence ledger,
agent runs, actions, payments, and active optional-consent events.
"""


def statement_hash(consent_type: OptionalConsentType) -> str:
    return hashlib.sha256(OPTIONAL_CONSENTS[consent_type][1].encode()).hexdigest()


def active_optional_consents(
    events: list[OptionalConsentEvent],
) -> dict[OptionalConsentType, OptionalConsentEvent]:
    withdrawn = {
        event.withdraws_grant_id
        for event in events
        if event.action == ConsentEventAction.WITHDRAWN and event.withdraws_grant_id
    }
    active: dict[OptionalConsentType, OptionalConsentEvent] = {}
    for event in sorted(events, key=lambda item: item.occurred_at, reverse=True):
        if (
            event.action == ConsentEventAction.GRANTED
            and event.id not in withdrawn
            and event.consent_type not in active
        ):
            active[event.consent_type] = event
    return active


def optional_consent_response(events: list[OptionalConsentEvent]) -> OptionalConsentResponse:
    active = active_optional_consents(events)
    return OptionalConsentResponse(
        items=[
            OptionalConsentDefinition(
                consent_type=consent_type,
                version=definition[0],
                statement=definition[1],
                active_grant=active.get(consent_type),
                history=[event for event in events if event.consent_type == consent_type][:100],
            )
            for consent_type, definition in OPTIONAL_CONSENTS.items()
        ]
    )


def build_account_export(
    repo: Repository, tenant: TenantContext, product_consent_version: str
) -> AccountExport:
    business = repo.get_business_by_id(tenant.business_id)
    connection = repo.get_gmail_connection(tenant.business_id)
    return AccountExport(
        generated_at=datetime.now(UTC),
        business=business,
        settings=repo.get_policy_settings(tenant),
        product_processing_consent=repo.get_consent(tenant, product_consent_version),
        optional_consents=repo.list_optional_consents(tenant),
        invoices=repo.list_all_invoices(tenant),
        agent_runs=repo.list_all_agent_runs(tenant),
        actions=repo.list_all_action_records(tenant),
        payments=repo.list_payments(tenant),
        founder_plan=repo.get_founder_plan(tenant.business_id),
        gmail_connected=bool(
            connection and connection.disconnected_at is None and connection.encrypted_refresh_token
        ),
    )


def _csv_bytes(rows: list[dict[str, Any]]) -> bytes:
    stream = io.StringIO(newline="")
    if not rows:
        return b""
    writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8")


def _collect_pages[T](
    fetch: Callable[[int, str | None], tuple[list[T], str | None]], record_limit: int
) -> list[T]:
    items: list[T] = []
    cursor: str | None = None
    while True:
        remaining = record_limit - len(items)
        if remaining <= 0:
            raise ApiError(413, "export_limit_exceeded", "The evidence export exceeds its limit.")
        page, cursor = fetch(min(100, remaining), cursor)
        items.extend(page)
        if cursor is None:
            return items


def _entry_amount(entry: EvidenceLedgerEntry, entries_by_id: dict[str, EvidenceLedgerEntry]) -> int:
    if entry.reversal_of and entry.reversal_of not in entries_by_id:
        return 0
    return -entry.amount_minor if entry.reversal_of else entry.amount_minor


def _revenue_bucket(entry: EvidenceLedgerEntry, businesses: dict[str, Business]) -> str:
    business = businesses.get(entry.business_id or "")
    if business is None:
        return "unclassified_revenue_minor"
    if (
        business.data_classification == DataClassification.REAL
        and business.relationship == BusinessRelationship.ARMS_LENGTH
    ):
        return "arms_length_revenue_minor"
    if business.relationship == BusinessRelationship.RELATED:
        return "related_party_revenue_minor"
    if business.relationship == BusinessRelationship.PREEXISTING:
        return "preexisting_revenue_minor"
    return "unclassified_revenue_minor"


def _monthly_pnl_rows(
    ledger: list[EvidenceLedgerEntry], businesses: list[Business]
) -> list[dict[str, Any]]:
    business_by_id = {business.id: business for business in businesses}
    entries_by_id = {entry.id: entry for entry in ledger}
    currencies = sorted({entry.currency for entry in ledger} or {"INR"})
    rows: dict[tuple[str, str], dict[str, int]] = {
        (month.isoformat()[:7], currency): {
            "arms_length_revenue_minor": 0,
            "related_party_revenue_minor": 0,
            "preexisting_revenue_minor": 0,
            "unclassified_revenue_minor": 0,
            "total_product_revenue_minor": 0,
            "expenses_minor": 0,
            "marketing_spend_minor": 0,
            "net_minor": 0,
        }
        for month in SUBMISSION_REPORT_MONTHS
        for currency in currencies
    }
    for entry in ledger:
        month = entry.occurred_on.isoformat()[:7]
        row = rows.get((month, entry.currency))
        if row is None:
            continue
        amount = _entry_amount(entry, entries_by_id)
        if entry.kind == LedgerKind.EXPENSE:
            row["expenses_minor"] += amount
            if entry.marketing:
                row["marketing_spend_minor"] += amount
            continue
        row[_revenue_bucket(entry, business_by_id)] += amount

    for row in rows.values():
        row["total_product_revenue_minor"] = sum(
            row[key]
            for key in (
                "arms_length_revenue_minor",
                "related_party_revenue_minor",
                "preexisting_revenue_minor",
                "unclassified_revenue_minor",
            )
        )
        row["net_minor"] = row["total_product_revenue_minor"] - row["expenses_minor"]
    return [
        {"month": month, "currency": currency, **values}
        for (month, currency), values in rows.items()
    ]


def _net_revenue_by_business(
    ledger: list[EvidenceLedgerEntry], businesses: list[Business]
) -> dict[str, int]:
    business_by_id = {business.id: business for business in businesses}
    entries_by_id = {entry.id: entry for entry in ledger}
    totals: dict[str, int] = defaultdict(int)
    for entry in ledger:
        if (
            entry.kind != LedgerKind.PRODUCT_REVENUE
            or entry.business_id not in business_by_id
            or entry.occurred_on < SUBMISSION_REPORT_MONTHS[0]
            or entry.occurred_on >= date(2026, 9, 1)
        ):
            continue
        totals[entry.business_id] += _entry_amount(entry, entries_by_id)
    return totals


def _customer_breakdown_rows(
    businesses: list[Business],
    prospects: list[Prospect],
    interview_counts: dict[str, int],
    ledger: list[EvidenceLedgerEntry],
) -> list[dict[str, Any]]:
    segments_by_business: dict[str, set[str]] = defaultdict(set)
    for prospect in prospects:
        if prospect.linked_business_id:
            segments_by_business[prospect.linked_business_id].add(prospect.segment.strip())

    def business_segment(business_id: str) -> str:
        segments = segments_by_business.get(business_id, set())
        if not segments:
            return "Unspecified"
        if len(segments) > 1:
            return "Multiple"
        return next(iter(segments))

    rows: dict[str, dict[str, Any]] = {}

    def row_for(segment: str) -> dict[str, Any]:
        return rows.setdefault(
            segment,
            {
                "segment": segment,
                "prospects": 0,
                "interviews": 0,
                "design_partners": 0,
                "converted_prospects": 0,
                "linked_real_businesses": 0,
                "arms_length_businesses": 0,
                "related_businesses": 0,
                "preexisting_businesses": 0,
                "unclassified_or_demo_businesses": 0,
                "arms_length_paying_businesses": 0,
            },
        )

    for prospect in prospects:
        row = row_for(prospect.segment.strip())
        row["prospects"] += 1
        row["interviews"] += interview_counts.get(prospect.id, 0)
        if prospect.status == ProspectStatus.DESIGN_PARTNER:
            row["design_partners"] += 1
        if prospect.status == ProspectStatus.CONVERTED:
            row["converted_prospects"] += 1

    net_revenue = _net_revenue_by_business(ledger, businesses)
    for business in businesses:
        row = row_for(business_segment(business.id))
        if business.data_classification == DataClassification.REAL:
            row["linked_real_businesses"] += 1
        if (
            business.data_classification == DataClassification.REAL
            and business.relationship == BusinessRelationship.ARMS_LENGTH
        ):
            row["arms_length_businesses"] += 1
            if net_revenue.get(business.id, 0) > 0:
                row["arms_length_paying_businesses"] += 1
        elif business.relationship == BusinessRelationship.RELATED:
            row["related_businesses"] += 1
        elif business.relationship == BusinessRelationship.PREEXISTING:
            row["preexisting_businesses"] += 1
        else:
            row["unclassified_or_demo_businesses"] += 1
    return [rows[segment] for segment in sorted(rows, key=str.casefold)]


def build_evidence_zip(repo: Repository, record_limit: int) -> bytes:
    businesses = _collect_pages(repo.list_businesses_page, record_limit)
    plans = _collect_pages(repo.list_founder_plans, record_limit)
    ledger = _collect_pages(repo.list_ledger_entries_page, record_limit)
    prospects = _collect_pages(repo.list_prospects, record_limit)
    interview_counts: dict[str, int] = {}
    interviews_total = 0
    for prospect in prospects:

        def fetch_interviews(
            limit: int, cursor: str | None, prospect_id: str = prospect.id
        ) -> tuple[list[Interview], str | None]:
            return repo.list_interviews(prospect_id, limit, cursor)

        interviews = _collect_pages(
            fetch_interviews,
            record_limit,
        )
        interview_counts[prospect.id] = len(interviews)
        interviews_total += len(interviews)
    business_rows: list[dict[str, Any]] = []
    run_rows: list[dict[str, Any]] = []
    action_rows: list[dict[str, Any]] = []
    payment_rows: list[dict[str, Any]] = []
    testimonial_rows: list[dict[str, Any]] = []
    identity_rows: list[dict[str, Any]] = []
    total = len(businesses) + len(plans) + len(ledger) + len(prospects) + interviews_total
    if total > record_limit:
        raise ApiError(413, "export_limit_exceeded", "The evidence export exceeds its limit.")
    pseudonym_by_business = {
        business.id: business.evidence_pseudonym
        or f"legacy_{hashlib.sha256(business.id.encode()).hexdigest()[:16]}"
        for business in businesses
    }
    for business in businesses:
        tenant = TenantContext("evidence-export", business.id, MembershipRole.OWNER)
        runs = repo.list_all_agent_runs(tenant)
        actions = repo.list_all_action_records(tenant)
        payments = repo.list_payments(tenant)
        total += len(runs) + len(actions) + len(payments)
        if total > record_limit:
            raise ApiError(413, "export_limit_exceeded", "The evidence export exceeds its limit.")
        pseudonym = pseudonym_by_business[business.id]
        consent_events = repo.list_optional_consents(tenant)
        active = active_optional_consents(consent_events)
        business_rows.append(
            {
                "business_pseudonym": pseudonym,
                "data_classification": business.data_classification.value,
                "relationship": business.relationship.value,
                "identity_disclosed": OptionalConsentType.IDENTITY_DISCLOSURE in active,
            }
        )
        for run in runs:
            run_rows.append(
                {
                    "business_pseudonym": pseudonym,
                    "run_id": run.id,
                    "invoice_reference": hashlib.sha256(run.invoice_id.encode()).hexdigest()[:16],
                    "status": run.status.value,
                    "decision": run.policy_result.final_decision.value if run.policy_result else "",
                    "policy_result": run.policy_result.outcome.value if run.policy_result else "",
                    "model_id": run.model_id,
                    "prompt_version": run.prompt_version,
                    "occurred_at": run.created_at.isoformat(),
                }
            )
        for action in actions:
            action_rows.append(
                {
                    "business_pseudonym": pseudonym,
                    "action_id": action.id,
                    "state": action.state.value,
                    "automatic": action.automatic,
                    "provider_confirmed": bool(action.provider_message_id),
                    "occurred_at": (action.execution_completed_at or action.created_at).isoformat(),
                }
            )
        for payment in payments:
            payment_rows.append(
                {
                    "business_pseudonym": pseudonym,
                    "payment_id": payment.id,
                    "amount_minor": payment.amount_minor,
                    "currency": payment.currency,
                    "paid_at": payment.paid_at.isoformat(),
                }
            )
        testimonial = active.get(OptionalConsentType.TESTIMONIAL)
        identity = active.get(OptionalConsentType.IDENTITY_DISCLOSURE)
        if identity:
            total += 1
            identity_rows.append(
                {
                    "business_pseudonym": pseudonym,
                    "approved_identity_fields": identity.approved_text or "",
                    "channels": "|".join(identity.channels),
                }
            )
        if testimonial:
            total += 1
            testimonial_rows.append(
                {
                    "business_pseudonym": pseudonym,
                    "approved_identity_fields": identity.approved_text if identity else "",
                    "approved_text": testimonial.approved_text or "",
                    "channels": "|".join(testimonial.channels),
                }
            )
        if total > record_limit:
            raise ApiError(413, "export_limit_exceeded", "The evidence export exceeds its limit.")

    plan_rows = [
        {
            "business_pseudonym": pseudonym_by_business.get(plan.business_id, "deleted_or_unknown"),
            "plan_version": plan.plan_version,
            "status": plan.status.value,
            "price_minor": plan.price_minor,
            "currency": plan.currency,
            "invoices_used": plan.invoices_used,
            "activated_at": plan.activated_at.isoformat(),
        }
        for plan in plans
    ]
    ledger_rows = [
        {
            "kind": entry.kind.value,
            "amount_minor": entry.amount_minor,
            "currency": entry.currency,
            "occurred_on": entry.occurred_on.isoformat(),
            "category": entry.category,
            "marketing": entry.marketing,
            "reversal_of": entry.reversal_of or "",
        }
        for entry in ledger
    ]
    pnl_rows = _monthly_pnl_rows(ledger, businesses)
    customer_breakdown_rows = _customer_breakdown_rows(
        businesses, prospects, interview_counts, ledger
    )
    excluded_ledger_entries = sum(
        1
        for entry in ledger
        if entry.occurred_on < SUBMISSION_REPORT_MONTHS[0] or entry.occurred_on >= date(2026, 9, 1)
    )
    export_filenames = [
        "manifest.json",
        "scoreboard.json",
        "submission_metrics.json",
        "README.txt",
        "businesses.csv",
        "agent_runs.csv",
        "actions.csv",
        "payments.csv",
        "founder_plans.csv",
        "ledger.csv",
        "pnl_by_month.csv",
        "customer_breakdown.csv",
        "testimonials.csv",
        "identities.csv",
    ]
    manifest = {
        "schema_version": 2,
        "generated_at": datetime.now(UTC).isoformat(),
        "complete": True,
        "record_limit": record_limit,
        "records": total,
        "report_period": {"starts_on": "2026-05-01", "ends_on": "2026-08-31"},
        "files": export_filenames,
        "excluded_ledger_entries_outside_report_period": excluded_ledger_entries,
        "privacy": "Pseudonymous operational evidence; consent-gated testimonials and identity.",
        "collection_counts": {
            "businesses": len(business_rows),
            "agent_runs": len(run_rows),
            "actions": len(action_rows),
            "payments": len(payment_rows),
            "founder_plans": len(plan_rows),
            "ledger": len(ledger_rows),
            "prospects": len(prospects),
            "interviews": interviews_total,
            "testimonials": len(testimonial_rows),
            "identities": len(identity_rows),
        },
    }
    from cashsathi_api.evidence import admin_impact

    scoreboard = admin_impact(repo).model_dump(mode="json")
    pnl_totals: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for row in pnl_rows:
        currency = str(row["currency"])
        for field in (
            "arms_length_revenue_minor",
            "related_party_revenue_minor",
            "preexisting_revenue_minor",
            "unclassified_revenue_minor",
            "expenses_minor",
            "marketing_spend_minor",
        ):
            pnl_totals[field][currency] += int(row[field])
    period_net_revenue = _net_revenue_by_business(ledger, businesses)
    arms_length_paying = {
        business.id
        for business in businesses
        if business.data_classification == DataClassification.REAL
        and business.relationship == BusinessRelationship.ARMS_LENGTH
        and period_net_revenue.get(business.id, 0) > 0
    }
    related_paying = {
        business.id
        for business in businesses
        if business.relationship == BusinessRelationship.RELATED
        and period_net_revenue.get(business.id, 0) > 0
    }
    preexisting_paying = {
        business.id
        for business in businesses
        if business.relationship == BusinessRelationship.PREEXISTING
        and period_net_revenue.get(business.id, 0) > 0
    }
    submission_metrics = {
        "schema_version": 1,
        "generated_at": manifest["generated_at"],
        "report_period": manifest["report_period"],
        "business_viability": {
            "arms_length_paying_businesses": len(arms_length_paying),
            "related_paying_businesses": len(related_paying),
            "preexisting_paying_businesses": len(preexisting_paying),
            "arms_length_revenue_by_currency": dict(pnl_totals["arms_length_revenue_minor"]),
            "related_revenue_by_currency": dict(pnl_totals["related_party_revenue_minor"]),
            "preexisting_revenue_by_currency": dict(pnl_totals["preexisting_revenue_minor"]),
            "unclassified_revenue_by_currency": dict(pnl_totals["unclassified_revenue_minor"]),
            "expenses_by_currency": dict(pnl_totals["expenses_minor"]),
            "marketing_spend_by_currency": dict(pnl_totals["marketing_spend_minor"]),
        },
        "customer_validation": {
            "prospects": scoreboard["prospects"],
            "interviews": scoreboard["interviews"],
            "design_partners": scoreboard["design_partners"],
            "converted_prospects": scoreboard["converted_prospects"],
            "consented_testimonials": scoreboard["consented_testimonials"],
        },
        "operational": scoreboard["operational"],
        "claim_boundaries": [
            "Demo, related-party, pre-existing, and arms-length results remain separate.",
            "Post-action payments show timing after a logged action, not causation.",
            "Only active, channel-scoped testimonial and identity consents are exported.",
        ],
    }
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest, indent=2))
        archive.writestr("scoreboard.json", json.dumps(scoreboard, indent=2))
        archive.writestr("submission_metrics.json", json.dumps(submission_metrics, indent=2))
        archive.writestr("README.txt", EVIDENCE_README)
        for filename, rows in (
            ("businesses.csv", business_rows),
            ("agent_runs.csv", run_rows),
            ("actions.csv", action_rows),
            ("payments.csv", payment_rows),
            ("founder_plans.csv", plan_rows),
            ("ledger.csv", ledger_rows),
            ("pnl_by_month.csv", pnl_rows),
            ("customer_breakdown.csv", customer_breakdown_rows),
            ("testimonials.csv", testimonial_rows),
            ("identities.csv", identity_rows),
        ):
            archive.writestr(filename, _csv_bytes(rows))
    return output.getvalue()


def founder_plan_for_business(repo: Repository, business_id: str) -> FounderPlanEnrollment | None:
    return repo.get_founder_plan(business_id)
