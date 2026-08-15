from __future__ import annotations

import argparse
from collections import Counter
from typing import Any

import firebase_admin
from firebase_admin import firestore
from google.cloud.firestore_v1 import DocumentReference


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Dry-run-first additive Phase 9 role, locale, and template backfill."
    )
    parser.add_argument("--project", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    firebase_admin.initialize_app(options={"projectId": args.project})
    client = firestore.client()
    changes: list[tuple[DocumentReference, dict[str, Any], str]] = []

    for snapshot in client.collection_group("members").stream():
        data = snapshot.to_dict() or {}
        update: dict[str, Any] = {}
        if not data.get("role"):
            update["role"] = "OWNER"
        if not data.get("status"):
            update["status"] = "ACTIVE"
        if update:
            changes.append((snapshot.reference, update, "memberships"))

    for snapshot in client.collection_group("invoices").stream():
        customer = (snapshot.to_dict() or {}).get("customer") or {}
        if not customer.get("locale"):
            changes.append((snapshot.reference, {"customer.locale": "en-IN"}, "invoice_customers"))

    for snapshot in client.collection_group("actions").stream():
        data = snapshot.to_dict() or {}
        update = {}
        if not data.get("locale"):
            update["locale"] = "en-IN"
        if not data.get("template_version"):
            update["template_version"] = "reminder-en-in-v1"
        if update:
            changes.append((snapshot.reference, update, "actions"))

    counts = Counter(kind for _reference, _update, kind in changes)
    print(f"Phase 9 additive updates required: {len(changes)}")
    for kind in ("memberships", "invoice_customers", "actions"):
        print(f"  {kind}: {counts[kind]}")
    if not args.apply:
        print("Dry run only. Re-run with --apply after reviewing the counts.")
        return
    for reference, update, _kind in changes:
        reference.update(update)
    print(f"Applied Phase 9 additive updates: {len(changes)}")


if __name__ == "__main__":
    main()
