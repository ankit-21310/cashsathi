"""Backfill billing records for existing manually activated Founder Plans.

Safe to run repeatedly. It never creates revenue ledger entries because every
legacy plan already points at its authoritative ledger record.
"""

import argparse
from datetime import UTC, datetime

from cashsathi_api.config import get_settings
from cashsathi_api.domain import (
    BillingOrder,
    BillingOrderStatus,
    BillingProvider,
    BillingTransaction,
    BillingTransactionStatus,
)
from cashsathi_api.repository import FirestoreRepository


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Write the backfill records.")
    args = parser.parse_args()
    repository = FirestoreRepository(get_settings())
    cursor: str | None = None
    created = 0
    while True:
        plans, cursor = repository.list_founder_plans(100, cursor)
        for plan in plans:
            if repository.list_billing_transactions(plan.business_id):
                continue
            if not args.apply:
                created += 1
                continue
            now = datetime.now(UTC)
            order = repository.save_billing_order(
                BillingOrder(
                    id=f"billord_manual_{plan.business_id}",
                    business_id=plan.business_id,
                    provider=BillingProvider.MANUAL,
                    provider_order_id=None,
                    receipt=plan.ledger_entry_id[:40],
                    idempotency_key=plan.idempotency_key,
                    status=BillingOrderStatus.PAID,
                    created_by=plan.activated_by,
                    created_at=plan.activated_at,
                    updated_at=plan.activated_at,
                )
            )
            payment_id = f"manual_{plan.ledger_entry_id.removeprefix('ledger_')[:40]}"
            payment = BillingTransaction(
                id=payment_id,
                business_id=plan.business_id,
                billing_order_id=order.id,
                provider=BillingProvider.MANUAL,
                provider_payment_id=payment_id,
                provider_order_id=None,
                amount_minor=plan.price_minor,
                currency=plan.currency,
                status=BillingTransactionStatus.CAPTURED,
                captured_at=plan.activated_at,
                created_at=plan.activated_at,
                updated_at=now,
            )
            repository.record_billing_transaction(order, payment, None, None)
            created += 1
        if cursor is None:
            break
    action = "Backfilled" if args.apply else "Would backfill"
    print(f"{action} {created} manual billing transaction(s).")


if __name__ == "__main__":
    main()
