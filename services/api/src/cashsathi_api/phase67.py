from __future__ import annotations

import csv
import hashlib
import io
import json
import zipfile
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from cashsathi_api.domain import (
    AccountExport,
    ConsentEventAction,
    FounderPlanEnrollment,
    MembershipRole,
    OptionalConsentDefinition,
    OptionalConsentEvent,
    OptionalConsentResponse,
    OptionalConsentType,
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


def build_evidence_zip(repo: Repository, record_limit: int) -> bytes:
    businesses = _collect_pages(repo.list_businesses_page, record_limit)
    plans = _collect_pages(repo.list_founder_plans, record_limit)
    ledger = _collect_pages(repo.list_ledger_entries_page, record_limit)
    business_rows: list[dict[str, Any]] = []
    run_rows: list[dict[str, Any]] = []
    action_rows: list[dict[str, Any]] = []
    payment_rows: list[dict[str, Any]] = []
    testimonial_rows: list[dict[str, Any]] = []
    identity_rows: list[dict[str, Any]] = []
    total = len(businesses) + len(plans) + len(ledger)
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
    manifest = {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "complete": True,
        "record_limit": record_limit,
        "records": total,
        "privacy": "Pseudonymous operational evidence; consent-gated testimonials and identity.",
        "collection_counts": {
            "businesses": len(business_rows),
            "agent_runs": len(run_rows),
            "actions": len(action_rows),
            "payments": len(payment_rows),
            "founder_plans": len(plan_rows),
            "ledger": len(ledger_rows),
            "testimonials": len(testimonial_rows),
            "identities": len(identity_rows),
        },
    }
    from cashsathi_api.evidence import admin_impact

    scoreboard = admin_impact(repo).model_dump(mode="json")
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest, indent=2))
        archive.writestr("scoreboard.json", json.dumps(scoreboard, indent=2))
        for filename, rows in (
            ("businesses.csv", business_rows),
            ("agent_runs.csv", run_rows),
            ("actions.csv", action_rows),
            ("payments.csv", payment_rows),
            ("founder_plans.csv", plan_rows),
            ("ledger.csv", ledger_rows),
            ("testimonials.csv", testimonial_rows),
            ("identities.csv", identity_rows),
        ):
            archive.writestr(filename, _csv_bytes(rows))
    return output.getvalue()


def founder_plan_for_business(repo: Repository, business_id: str) -> FounderPlanEnrollment | None:
    return repo.get_founder_plan(business_id)
