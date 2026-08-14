"""Dry-run-first operational backfill for Phase 4 fields; never sends email."""

from __future__ import annotations

import argparse
import hashlib
from datetime import UTC, datetime

from google.cloud import firestore

from cashsathi_api.domain import (
    Action,
    BusinessRelationship,
    DataClassification,
    Invoice,
    InvoiceWorkflowStatus,
)
from cashsathi_api.reminders import render_reminder
from cashsathi_api.workflow import initial_next_check


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--database", default="(default)")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    client = firestore.Client(project=args.project, database=args.database)
    changes = 0

    for business_snapshot in client.collection("businesses").stream():
        business_data = business_snapshot.to_dict() or {}
        business_updates: dict[str, object] = {}
        if "data_classification" not in business_data:
            business_updates["data_classification"] = DataClassification.UNCLASSIFIED.value
        if "relationship" not in business_data:
            business_updates["relationship"] = BusinessRelationship.UNCLASSIFIED.value
        if business_updates:
            changes += 1
            if args.apply:
                business_snapshot.reference.update(business_updates)

        for invoice_snapshot in business_snapshot.reference.collection("invoices").stream():
            raw = invoice_snapshot.to_dict() or {}
            invoice = Invoice.model_validate(raw)
            invoice_updates: dict[str, object] = {}
            if "workflow_status" not in raw:
                invoice_updates["workflow_status"] = (
                    InvoiceWorkflowStatus.PAUSED.value
                    if invoice.review_required
                    else InvoiceWorkflowStatus.OPEN.value
                )
            if "next_check_at" not in raw:
                next_check = initial_next_check(invoice)
                invoice_updates["next_check_at"] = next_check.isoformat() if next_check else None
            if "reminder_sequence" not in raw:
                invoice_updates["reminder_sequence"] = 0
            if "active_action_id" not in raw:
                invoice_updates["active_action_id"] = None

            action_snapshots = list(
                business_snapshot.reference.collection("actions")
                .where("invoice_id", "==", invoice.id)
                .stream()
            )
            sequence = 0
            active_action_id: str | None = None
            for action_snapshot in sorted(
                action_snapshots, key=lambda item: (item.to_dict() or {}).get("created_at", "")
            ):
                sequence += 1
                action_raw = action_snapshot.to_dict() or {}
                action = Action.model_validate(action_raw)
                action_updates: dict[str, object] = {}
                if not action.action_key:
                    raw_key = f"{invoice.business_id}:{invoice.id}:SEND_REMINDER:{sequence}"
                    action_key = hashlib.sha256(raw_key.encode()).hexdigest()
                    message = render_reminder(invoice, tone=None, intent=None)
                    action_updates.update(
                        {
                            "action_key": action_key,
                            "reminder_sequence": sequence,
                            "recipient_email": invoice.customer.email,
                            "subject": message.subject,
                            "body": message.body,
                            "automatic": False,
                            "attempt_count": action.attempt_count,
                        }
                    )
                if action.state.value in {
                    "PROPOSED",
                    "AWAITING_APPROVAL",
                    "EXECUTING",
                    "FAILED",
                    "UNKNOWN",
                }:
                    active_action_id = action.id
                if action_updates:
                    changes += 1
                    if args.apply:
                        action_snapshot.reference.update(action_updates)
            if sequence:
                invoice_updates["reminder_sequence"] = sequence
            if active_action_id:
                invoice_updates.update(
                    {
                        "active_action_id": active_action_id,
                        "workflow_status": InvoiceWorkflowStatus.PAUSED.value,
                        "next_check_at": None,
                    }
                )
            if invoice_updates:
                invoice_updates["updated_at"] = datetime.now(UTC).isoformat()
                invoice_updates["migration_version"] = "phase-4-v1"
                changes += 1
                if args.apply:
                    invoice_snapshot.reference.update(invoice_updates)

    mode = "applied" if args.apply else "would apply"
    print(f"Phase 4 backfill {mode} {changes} document update(s). No email was sent.")


if __name__ == "__main__":
    main()
