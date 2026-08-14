from __future__ import annotations

import argparse
import secrets

import firebase_admin
from firebase_admin import firestore
from google.cloud.firestore_v1 import DocumentReference


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill stable evidence pseudonyms for Phase 6-7."
    )
    parser.add_argument("--project", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    firebase_admin.initialize_app(options={"projectId": args.project})
    client = firestore.client()
    changes: list[tuple[DocumentReference, dict[str, str]]] = []
    for snapshot in client.collection("businesses").stream():
        data = snapshot.to_dict() or {}
        update: dict[str, str] = {}
        if not data.get("evidence_pseudonym"):
            update["evidence_pseudonym"] = f"evid_{secrets.token_urlsafe(12)}"
        if not data.get("data_classification"):
            update["data_classification"] = "UNCLASSIFIED"
        if not data.get("relationship"):
            update["relationship"] = "UNCLASSIFIED"
        if update:
            changes.append((snapshot.reference, update))
    print(f"Businesses requiring additive Phase 6-7 defaults: {len(changes)}")
    if not args.apply:
        print("Dry run only. Re-run with --apply to write changes.")
        return
    for reference, update in changes:
        reference.update(update)
    print(f"Updated businesses: {len(changes)}")


if __name__ == "__main__":
    main()
