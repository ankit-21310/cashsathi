from __future__ import annotations

import argparse
import hashlib
import os
import secrets
from datetime import UTC, date, datetime, timedelta

import firebase_admin
from firebase_admin import auth, firestore

from cashsathi_api.domain import PolicyDefaults


def main() -> None:
    parser = argparse.ArgumentParser(description="Provision an isolated synthetic judge workspace.")
    parser.add_argument("--project", required=True)
    parser.add_argument("--email", required=True)
    args = parser.parse_args()
    password = os.environ.get("JUDGE_PASSWORD", "")
    if len(password) < 12:
        raise SystemExit(
            "Set JUDGE_PASSWORD to a non-committed password of at least 12 characters."
        )
    firebase_admin.initialize_app(options={"projectId": args.project})
    try:
        user = auth.get_user_by_email(args.email)
        auth.update_user(user.uid, password=password, email_verified=True, disabled=False)
    except auth.UserNotFoundError:
        user = auth.create_user(
            email=args.email, password=password, email_verified=True, display_name="Judge"
        )
    client = firestore.client()
    business_id = f"biz_{hashlib.sha256(user.uid.encode()).hexdigest()[:20]}"
    business_ref = client.collection("businesses").document(business_id)
    now = datetime.now(UTC)
    batch = client.batch()
    batch.set(
        business_ref,
        {
            "name": "Synthetic Judge Workspace",
            "owner_user_id": user.uid,
            "created_at": now,
            "data_classification": "DEMO",
            "relationship": "UNCLASSIFIED",
            "evidence_pseudonym": f"evid_{secrets.token_urlsafe(12)}",
        },
        merge=True,
    )
    batch.set(
        client.collection("users").document(user.uid),
        {
            "email": args.email,
            "display_name": "Judge",
            "business_id": business_id,
            "created_at": now,
        },
        merge=True,
    )
    batch.set(
        business_ref.collection("members").document(user.uid),
        {"role": "OWNER", "status": "ACTIVE", "created_at": now},
        merge=True,
    )
    batch.set(
        client.collection("settings").document(business_id),
        PolicyDefaults(automation_enabled=False).model_dump(mode="json"),
        merge=True,
    )
    invoice_id = "inv_judge_synthetic_001"
    due_date = date.today() - timedelta(days=7)
    batch.set(
        business_ref.collection("invoices").document(invoice_id),
        {
            "id": invoice_id,
            "business_id": business_id,
            "extraction_id": "ext_judge_synthetic_001",
            "invoice_number": "DEMO-INV-001",
            "customer": {
                "id": "cust_judge_synthetic",
                "name": "Synthetic Customer",
                "email": None,
                "manual_only": True,
            },
            "amount_minor": 2_000_000,
            "currency": "INR",
            "issue_date": due_date - timedelta(days=30),
            "due_date": due_date,
            "payment_terms": "Synthetic demo only",
            "review_required": False,
            "review_reason": None,
            "dispute_active": False,
            "verified_paid_minor": 0,
            "confirmation_hash": hashlib.sha256(invoice_id.encode()).hexdigest(),
            "created_at": now,
            "updated_at": now,
            "next_check_at": None,
            "workflow_status": "PAUSED",
            "evaluation_lease_until": None,
            "active_action_id": None,
            "reminder_sequence": 0,
        },
        merge=True,
    )
    batch.commit()
    print(f"Judge account provisioned as DEMO: {args.email}; password was not printed.")


if __name__ == "__main__":
    main()
