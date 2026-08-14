from __future__ import annotations

import csv
import json
import zipfile
from io import BytesIO, StringIO

from fastapi.testclient import TestClient


def auth(token: str = "alice-token") -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def create_business(client: TestClient, token: str, name: str) -> dict[str, object]:
    response = client.post("/api/businesses", headers=auth(token), json={"name": name})
    assert response.status_code == 200
    return response.json()


def add_ledger(
    client: TestClient,
    *,
    kind: str,
    amount: str,
    occurred_on: str,
    reference: str,
    business_id: str | None = None,
    marketing: bool = False,
    reversal_of: str | None = None,
) -> dict[str, object]:
    response = client.post(
        "/api/admin/evidence-ledger",
        headers=auth(),
        json={
            "kind": kind,
            "amount_decimal": amount,
            "currency": "INR",
            "occurred_on": occurred_on,
            "category": "Phase 8 test",
            "reference": reference,
            "business_id": business_id,
            "marketing": marketing,
            "reversal_of": reversal_of,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def csv_rows(archive: zipfile.ZipFile, filename: str) -> list[dict[str, str]]:
    return list(csv.DictReader(StringIO(archive.read(filename).decode())))


def test_phase8_export_has_monthly_pnl_customer_breakdown_and_safe_metrics(
    client: TestClient,
) -> None:
    arms = create_business(client, "alice-token", "Private Arms Studio")
    related = create_business(client, "bob-token", "Private Related Studio")
    unclassified = create_business(client, "charlie-token", "Private Unclassified Studio")
    for business, relationship in ((arms, "ARMS_LENGTH"), (related, "RELATED")):
        response = client.post(
            f"/api/admin/businesses/{business['id']}/classification",
            headers=auth(),
            json={"data_classification": "REAL", "relationship": relationship},
        )
        assert response.status_code == 200

    original = add_ledger(
        client,
        kind="PRODUCT_REVENUE",
        amount="100.00",
        occurred_on="2026-05-20",
        reference="private-arms-receipt",
        business_id=str(arms["id"]),
    )
    add_ledger(
        client,
        kind="PRODUCT_REVENUE",
        amount="100.00",
        occurred_on="2026-05-20",
        reference="private-arms-reversal",
        business_id=str(arms["id"]),
        reversal_of=str(original["id"]),
    )
    add_ledger(
        client,
        kind="PRODUCT_REVENUE",
        amount="299.00",
        occurred_on="2026-06-10",
        reference="private-related-receipt",
        business_id=str(related["id"]),
    )
    add_ledger(
        client,
        kind="PRODUCT_REVENUE",
        amount="75.00",
        occurred_on="2026-07-10",
        reference="private-unclassified-receipt",
        business_id=str(unclassified["id"]),
    )
    add_ledger(
        client,
        kind="EXPENSE",
        amount="50.00",
        occurred_on="2026-08-10",
        reference="private-marketing-receipt",
        marketing=True,
    )
    add_ledger(
        client,
        kind="EXPENSE",
        amount="25.00",
        occurred_on="2026-04-10",
        reference="outside-report-period",
    )

    prospect_response = client.post(
        "/api/admin/validation/prospects",
        headers=auth(),
        json={
            "company": "Private Prospect Name",
            "segment": "Agency",
            "public_contact_channel": "Public website",
            "linked_business_id": arms["id"],
        },
    )
    assert prospect_response.status_code == 200
    prospect = prospect_response.json()
    interview = client.post(
        f"/api/admin/validation/prospects/{prospect['id']}/interviews",
        headers=auth(),
        json={
            "occurred_on": "2026-08-14",
            "current_workflow": "Manual spreadsheet",
            "top_pain": "Follow-up consistency",
            "trust_boundary": "Approval for sensitive messages",
            "willingness_to_pay": "YES",
            "feedback": "Private interview feedback",
        },
    )
    assert interview.status_code == 200
    converted = client.patch(
        f"/api/admin/validation/prospects/{prospect['id']}",
        headers=auth(),
        json={"status": "CONVERTED"},
    )
    assert converted.status_code == 200

    response = client.get("/api/admin/evidence-export", headers=auth())
    assert response.status_code == 200
    with zipfile.ZipFile(BytesIO(response.content)) as archive:
        expected_files = {
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
        }
        assert expected_files <= set(archive.namelist())
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["schema_version"] == 2
        assert manifest["complete"] is True
        assert manifest["report_period"] == {
            "starts_on": "2026-05-01",
            "ends_on": "2026-08-31",
        }
        assert manifest["excluded_ledger_entries_outside_report_period"] == 1
        assert manifest["collection_counts"]["prospects"] == 1
        assert manifest["collection_counts"]["interviews"] == 1

        pnl = {
            (row["month"], row["currency"]): row for row in csv_rows(archive, "pnl_by_month.csv")
        }
        assert pnl[("2026-05", "INR")]["arms_length_revenue_minor"] == "0"
        assert pnl[("2026-06", "INR")]["related_party_revenue_minor"] == "29900"
        assert pnl[("2026-07", "INR")]["unclassified_revenue_minor"] == "7500"
        assert pnl[("2026-08", "INR")]["expenses_minor"] == "5000"
        assert pnl[("2026-08", "INR")]["marketing_spend_minor"] == "5000"
        assert pnl[("2026-08", "INR")]["net_minor"] == "-5000"

        breakdown = {row["segment"]: row for row in csv_rows(archive, "customer_breakdown.csv")}
        assert breakdown["Agency"]["prospects"] == "1"
        assert breakdown["Agency"]["interviews"] == "1"
        assert breakdown["Agency"]["converted_prospects"] == "1"
        assert breakdown["Agency"]["arms_length_businesses"] == "1"
        assert breakdown["Agency"]["arms_length_paying_businesses"] == "0"
        assert breakdown["Unspecified"]["related_businesses"] == "1"

        metrics = json.loads(archive.read("submission_metrics.json"))
        assert metrics["business_viability"]["related_revenue_by_currency"] == {"INR": 29900}
        assert metrics["customer_validation"]["interviews"] == 1
        assert "causation" in " ".join(metrics["claim_boundaries"]).lower()

        sanitized = b"\n".join(archive.read(name) for name in expected_files).decode()
        for private_value in (
            str(arms["id"]),
            str(related["id"]),
            str(unclassified["id"]),
            "Private Arms Studio",
            "Private Related Studio",
            "Private Unclassified Studio",
            "Private Prospect Name",
            "private-arms-receipt",
            "private-related-receipt",
            "Private interview feedback",
        ):
            assert private_value not in sanitized


def test_phase8_export_emits_zero_inr_pnl_for_empty_evidence(client: TestClient) -> None:
    response = client.get("/api/admin/evidence-export", headers=auth())
    assert response.status_code == 200
    with zipfile.ZipFile(BytesIO(response.content)) as archive:
        rows = csv_rows(archive, "pnl_by_month.csv")
        assert [row["month"] for row in rows] == [
            "2026-05",
            "2026-06",
            "2026-07",
            "2026-08",
        ]
        assert {row["currency"] for row in rows} == {"INR"}
        assert all(row["total_product_revenue_minor"] == "0" for row in rows)
