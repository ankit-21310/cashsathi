from __future__ import annotations

import argparse
import csv
from datetime import UTC, datetime
from pathlib import Path

import firebase_admin
from firebase_admin import firestore


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Idempotently import public-business prospects; never sends outreach."
    )
    parser.add_argument("--project", required=True)
    parser.add_argument("--csv", default="docs/validation/prospects.csv")
    parser.add_argument("--admin-uid", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    source = Path(args.csv).resolve()
    firebase_admin.initialize_app(options={"projectId": args.project})
    client = firestore.client()
    created = skipped = 0
    with source.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            prospect_id = f"prospect_{row['prospect_id'].strip().casefold()}"
            reference = client.collection("validation_prospects").document(prospect_id)
            if reference.get().exists:
                skipped += 1
                continue
            created += 1
            if args.apply:
                now = datetime.now(UTC)
                reference.create(
                    {
                        "id": prospect_id,
                        "company": row["company"].strip(),
                        "city": row.get("city", "").strip() or None,
                        "segment": row["segment"].strip(),
                        "public_website": row.get("public_website", "").strip() or None,
                        "public_contact_channel": row["public_contact_channel"].strip(),
                        "status": "NOT_CONTACTED",
                        "notes": row.get("notes", "").strip() or None,
                        "next_follow_up_on": None,
                        "linked_business_id": None,
                        "created_by": args.admin_uid,
                        "created_at": now,
                        "updated_at": now,
                    }
                )
    print(f"Would create: {created}; already present: {skipped}")
    if not args.apply:
        print("Dry run only. Re-run with --apply to import records.")


if __name__ == "__main__":
    main()
