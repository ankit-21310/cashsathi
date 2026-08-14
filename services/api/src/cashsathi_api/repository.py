from __future__ import annotations

import base64
import hashlib
import os
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol, cast

from firebase_admin import firestore
from google.auth.credentials import AnonymousCredentials
from google.cloud import firestore as google_firestore

from cashsathi_api.auth import initialize_firebase
from cashsathi_api.config import Settings
from cashsathi_api.domain import (
    Action,
    ActionAttempt,
    ActionState,
    AgentRun,
    AuthenticatedUser,
    Business,
    BusinessRelationship,
    ConsentEventAction,
    ConsentRecord,
    DataClassification,
    EvidenceEventType,
    EvidenceLedgerEntry,
    FounderPlanEnrollment,
    FounderPlanStatus,
    GmailConnection,
    GmailOAuthState,
    Interview,
    Invoice,
    InvoiceWorkflowStatus,
    Membership,
    MembershipRole,
    MembershipStatus,
    OptionalConsentEvent,
    Payment,
    PolicyDefaults,
    Prospect,
    ProspectStatus,
    TenantContext,
)
from cashsathi_api.errors import ApiError


class Repository(Protocol):
    def ready(self) -> bool: ...

    def get_account(self, user: AuthenticatedUser) -> tuple[Business | None, Membership | None]: ...

    def get_or_create_business(
        self, user: AuthenticatedUser, name: str
    ) -> tuple[Business, Membership]: ...

    def require_tenant(self, user: AuthenticatedUser) -> TenantContext: ...

    def get_consent(self, tenant: TenantContext, version: str) -> ConsentRecord | None: ...

    def grant_consent(
        self, tenant: TenantContext, version: str, statement_sha256: str
    ) -> ConsentRecord: ...

    def list_optional_consents(self, tenant: TenantContext) -> list[OptionalConsentEvent]: ...

    def append_optional_consent(
        self, tenant: TenantContext, event: OptionalConsentEvent
    ) -> OptionalConsentEvent: ...

    def consume_rate_limit(
        self,
        subject: str,
        operation: str,
        limit: int,
        window_seconds: int,
        now: datetime,
    ) -> None: ...

    def record_extraction(self, tenant: TenantContext, properties: dict[str, Any]) -> str: ...

    def save_invoice(self, tenant: TenantContext, invoice: Invoice) -> Invoice: ...

    def get_invoice(self, tenant: TenantContext, invoice_id: str) -> Invoice: ...

    def list_invoices(
        self, tenant: TenantContext, limit: int, cursor: str | None
    ) -> tuple[list[Invoice], str | None]: ...

    def get_policy_settings(self, tenant: TenantContext) -> PolicyDefaults: ...

    def list_actions(self, tenant: TenantContext, invoice_id: str) -> list[Action]: ...

    def save_evaluation(
        self, tenant: TenantContext, run: AgentRun, action: Action | None
    ) -> Action | None: ...

    def list_agent_runs(
        self, tenant: TenantContext, invoice_id: str | None, limit: int, cursor: str | None
    ) -> tuple[list[AgentRun], str | None]: ...

    def list_all_actions(
        self, tenant: TenantContext, state: ActionState | None, limit: int, cursor: str | None
    ) -> tuple[list[Action], str | None]: ...

    def get_action(self, tenant: TenantContext, action_id: str) -> Action: ...

    def update_action(
        self,
        tenant: TenantContext,
        action: Action,
        attempt: ActionAttempt | None,
        event_type: EvidenceEventType,
        actor_type: str,
    ) -> Action: ...

    def reserve_action_execution(
        self,
        tenant: TenantContext,
        action: Action,
        expected_state: ActionState,
        actor_type: str,
    ) -> Action: ...

    def update_invoice(self, tenant: TenantContext, invoice: Invoice) -> Invoice: ...

    def record_payment(
        self, tenant: TenantContext, payment: Payment
    ) -> tuple[Payment, Invoice]: ...

    def list_payments(
        self, tenant: TenantContext, invoice_id: str | None = None
    ) -> list[Payment]: ...

    def update_automation(self, tenant: TenantContext, enabled: bool) -> PolicyDefaults: ...

    def create_oauth_state(self, tenant: TenantContext, state: GmailOAuthState) -> None: ...

    def consume_oauth_state(self, state: str) -> GmailOAuthState: ...

    def get_gmail_connection(self, business_id: str) -> GmailConnection | None: ...

    def save_gmail_connection(
        self, tenant: TenantContext, connection: GmailConnection
    ) -> GmailConnection: ...

    def disconnect_gmail(self, tenant: TenantContext) -> None: ...

    def claim_due_invoices(
        self, now: datetime, limit: int, lease_minutes: int
    ) -> list[tuple[TenantContext, Invoice]]: ...

    def list_all_invoices(self, tenant: TenantContext | None = None) -> list[Invoice]: ...

    def list_all_agent_runs(self, tenant: TenantContext | None = None) -> list[AgentRun]: ...

    def list_all_action_records(self, tenant: TenantContext | None = None) -> list[Action]: ...

    def list_businesses(self) -> list[Business]: ...

    def list_businesses_page(
        self, limit: int, cursor: str | None
    ) -> tuple[list[Business], str | None]: ...

    def get_business_by_id(self, business_id: str) -> Business: ...

    def classify_business(
        self,
        business_id: str,
        classification: DataClassification,
        relationship: BusinessRelationship,
    ) -> Business: ...

    def create_ledger_entry(self, entry: EvidenceLedgerEntry) -> EvidenceLedgerEntry: ...

    def list_ledger_entries(self) -> list[EvidenceLedgerEntry]: ...

    def list_ledger_entries_page(
        self, limit: int, cursor: str | None
    ) -> tuple[list[EvidenceLedgerEntry], str | None]: ...

    def create_prospect(self, prospect: Prospect) -> Prospect: ...

    def update_prospect(self, prospect_id: str, changes: dict[str, Any]) -> Prospect: ...

    def list_prospects(
        self, limit: int, cursor: str | None
    ) -> tuple[list[Prospect], str | None]: ...

    def get_prospect(self, prospect_id: str) -> Prospect: ...

    def create_interview(self, interview: Interview) -> Interview: ...

    def list_interviews(
        self, prospect_id: str, limit: int, cursor: str | None
    ) -> tuple[list[Interview], str | None]: ...

    def activate_founder_plan(
        self, enrollment: FounderPlanEnrollment, ledger: EvidenceLedgerEntry
    ) -> FounderPlanEnrollment: ...

    def get_founder_plan(self, business_id: str) -> FounderPlanEnrollment | None: ...

    def list_founder_plans(
        self, limit: int, cursor: str | None
    ) -> tuple[list[FounderPlanEnrollment], str | None]: ...

    def delete_account(self, tenant: TenantContext, retain_anonymous_metrics: bool) -> None: ...


def _business_id(uid: str) -> str:
    digest = hashlib.sha256(uid.encode("utf-8")).hexdigest()[:20]
    return f"biz_{digest}"


def _consent_id(tenant: TenantContext, version: str) -> str:
    raw = f"{tenant.business_id}:{tenant.user_id}:product_processing:{version}"
    return f"consent_{hashlib.sha256(raw.encode()).hexdigest()[:28]}"


def _cursor_encode(document_id: str) -> str:
    return base64.urlsafe_b64encode(document_id.encode()).decode().rstrip("=")


def _cursor_decode(cursor: str) -> str:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        decoded = base64.urlsafe_b64decode(padded.encode()).decode()
    except Exception:
        raise ApiError(422, "invalid_cursor", "The pagination cursor is invalid.") from None
    if not decoded or "/" in decoded:
        raise ApiError(422, "invalid_cursor", "The pagination cursor is invalid.")
    return decoded


def _event(
    *,
    event_type: EvidenceEventType,
    tenant: TenantContext,
    subject_type: str,
    subject_id: str,
    properties: dict[str, Any],
    actor_type: str = "USER",
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "event_type": event_type.value,
        "business_id": tenant.business_id,
        "actor_type": actor_type,
        "actor_id": tenant.user_id,
        "subject_type": subject_type,
        "subject_id": subject_id,
        "occurred_at": datetime.now(UTC).isoformat(),
        "source": "api",
        "properties": properties,
    }


class FirestoreRepository:
    def __init__(self, settings: Settings) -> None:
        initialize_firebase(settings)
        if settings.firestore_emulator_host:
            os.environ.setdefault("FIRESTORE_EMULATOR_HOST", settings.firestore_emulator_host)
            self._client = google_firestore.Client(
                project=settings.gcp_project_id,
                database=settings.firestore_database_id,
                credentials=AnonymousCredentials(),  # type: ignore[no-untyped-call]
            )
        else:
            self._client = firestore.client(database_id=settings.firestore_database_id)

    def ready(self) -> bool:
        list(self._client.collection("_readiness").limit(1).stream())
        return True

    def get_account(self, user: AuthenticatedUser) -> tuple[Business | None, Membership | None]:
        user_snapshot = self._client.collection("users").document(user.uid).get()
        if not user_snapshot.exists:
            return None, None
        user_data = user_snapshot.to_dict() or {}
        business_id = user_data.get("business_id")
        if not isinstance(business_id, str):
            return None, None
        business_snapshot = self._client.collection("businesses").document(business_id).get()
        member_snapshot = (
            self._client.collection("businesses")
            .document(business_id)
            .collection("members")
            .document(user.uid)
            .get()
        )
        if not business_snapshot.exists or not member_snapshot.exists:
            raise ApiError(403, "membership_invalid", "The business membership is incomplete.")
        return self._parse_business(
            business_id, business_snapshot.to_dict() or {}
        ), self._parse_membership(business_id, user.uid, member_snapshot.to_dict() or {})

    def get_or_create_business(
        self, user: AuthenticatedUser, name: str
    ) -> tuple[Business, Membership]:
        existing = self.get_account(user)
        if existing[0] and existing[1]:
            return cast(tuple[Business, Membership], existing)
        business_id = _business_id(user.uid)
        business_ref = self._client.collection("businesses").document(business_id)
        user_ref = self._client.collection("users").document(user.uid)
        member_ref = business_ref.collection("members").document(user.uid)
        settings_ref = self._client.collection("settings").document(business_id)
        event_ref = self._client.collection("evidence_events").document(
            f"evt_business_created_{business_id}"
        )
        evidence_pseudonym = f"evid_{secrets.token_urlsafe(12)}"
        transaction = self._client.transaction()

        @google_firestore.transactional
        def create_in_transaction(txn: Any) -> None:
            snapshot = user_ref.get(transaction=txn)
            if snapshot.exists and (snapshot.to_dict() or {}).get("business_id"):
                return
            now = datetime.now(UTC)
            txn.set(
                business_ref,
                {
                    "name": name.strip(),
                    "owner_user_id": user.uid,
                    "created_at": now,
                    "data_classification": DataClassification.UNCLASSIFIED.value,
                    "relationship": BusinessRelationship.UNCLASSIFIED.value,
                    "evidence_pseudonym": evidence_pseudonym,
                },
            )
            txn.set(
                user_ref,
                {
                    "email": user.email,
                    "display_name": user.display_name,
                    "business_id": business_id,
                    "created_at": now,
                },
            )
            txn.set(
                member_ref,
                {
                    "role": MembershipRole.OWNER.value,
                    "status": MembershipStatus.ACTIVE.value,
                    "created_at": now,
                },
            )
            txn.set(settings_ref, PolicyDefaults().model_dump(mode="json"))
            tenant = TenantContext(user.uid, business_id, MembershipRole.OWNER)
            txn.set(
                event_ref,
                _event(
                    event_type=EvidenceEventType.BUSINESS_CREATED,
                    tenant=tenant,
                    subject_type="business",
                    subject_id=business_id,
                    properties={},
                ),
            )

        create_in_transaction(transaction)
        created = self.get_account(user)
        if not created[0] or not created[1]:
            raise ApiError(500, "business_creation_failed", "The business could not be created.")
        return created[0], created[1]

    def require_tenant(self, user: AuthenticatedUser) -> TenantContext:
        business, membership = self.get_account(user)
        if not business or not membership:
            raise ApiError(404, "business_not_found", "Complete business onboarding first.")
        if membership.status != MembershipStatus.ACTIVE:
            raise ApiError(403, "membership_inactive", "The business membership is not active.")
        return TenantContext(user_id=user.uid, business_id=business.id, role=membership.role)

    def get_consent(self, tenant: TenantContext, version: str) -> ConsentRecord | None:
        snapshot = (
            self._business_ref(tenant)
            .collection("consents")
            .document(_consent_id(tenant, version))
            .get()
        )
        return ConsentRecord.model_validate(snapshot.to_dict()) if snapshot.exists else None

    def grant_consent(
        self, tenant: TenantContext, version: str, statement_sha256: str
    ) -> ConsentRecord:
        consent_id = _consent_id(tenant, version)
        consent_ref = self._business_ref(tenant).collection("consents").document(consent_id)
        existing = consent_ref.get()
        if existing.exists:
            return ConsentRecord.model_validate(existing.to_dict())
        record = ConsentRecord(
            version=version,
            granted_at=datetime.now(UTC),
            user_id=tenant.user_id,
            business_id=tenant.business_id,
            statement_sha256=statement_sha256,
        )
        batch = self._client.batch()
        batch.create(consent_ref, record.model_dump(mode="json"))
        batch.create(
            self._client.collection("evidence_events").document(f"evt_{consent_id}"),
            _event(
                event_type=EvidenceEventType.CONSENT_GRANTED,
                tenant=tenant,
                subject_type="business",
                subject_id=tenant.business_id,
                properties={"consent_type": record.consent_type, "version": version},
            ),
        )
        batch.commit()
        return record

    def list_optional_consents(self, tenant: TenantContext) -> list[OptionalConsentEvent]:
        snapshots = (
            self._business_ref(tenant)
            .collection("optional_consents")
            .order_by("occurred_at", direction=google_firestore.Query.DESCENDING)
            .stream()
        )
        return [OptionalConsentEvent.model_validate(snapshot.to_dict()) for snapshot in snapshots]

    def append_optional_consent(
        self, tenant: TenantContext, event: OptionalConsentEvent
    ) -> OptionalConsentEvent:
        if event.business_id != tenant.business_id or event.user_id != tenant.user_id:
            raise ApiError(403, "consent_tenant_mismatch", "Consent tenant mismatch.")
        ref = self._business_ref(tenant).collection("optional_consents").document(event.id)
        evidence_ref = self._client.collection("evidence_events").document(f"evt_{event.id}")
        batch = self._client.batch()
        batch.create(ref, event.model_dump(mode="json"))
        batch.create(
            evidence_ref,
            _event(
                event_type=(
                    EvidenceEventType.OPTIONAL_CONSENT_GRANTED
                    if event.action == ConsentEventAction.GRANTED
                    else EvidenceEventType.OPTIONAL_CONSENT_WITHDRAWN
                ),
                tenant=tenant,
                subject_type="consent",
                subject_id=event.id,
                properties={
                    "consent_type": event.consent_type.value,
                    "action": event.action.value,
                    "version": event.version,
                    "withdraws_grant_id": event.withdraws_grant_id,
                },
            ),
        )
        batch.commit()
        return event

    def consume_rate_limit(
        self,
        subject: str,
        operation: str,
        limit: int,
        window_seconds: int,
        now: datetime,
    ) -> None:
        window = int(now.timestamp()) // window_seconds
        digest = hashlib.sha256(f"{subject}:{operation}:{window}".encode()).hexdigest()
        expires_at = datetime.fromtimestamp((window + 1) * window_seconds, UTC)
        ref = self._client.collection("_rate_limits").document(digest)
        transaction = self._client.transaction(max_attempts=5)

        @google_firestore.transactional
        def consume(txn: Any) -> None:
            snapshot = ref.get(transaction=txn)
            data = snapshot.to_dict() or {}
            count = int(data.get("count", 0))
            stored_expiry = data.get("expires_at")
            if isinstance(stored_expiry, datetime) and stored_expiry <= now:
                count = 0
            if count >= limit:
                retry_after = window_seconds - (int(now.timestamp()) % window_seconds)
                raise ApiError(
                    429,
                    "rate_limit_exceeded",
                    "Too many requests. Try again after the rate-limit window resets.",
                    {"operation": operation, "retry_after_seconds": retry_after},
                    {"Retry-After": str(retry_after)},
                )
            txn.set(
                ref,
                {
                    "count": count + 1,
                    "operation": operation,
                    "expires_at": expires_at,
                },
            )

        consume(transaction)

    def record_extraction(self, tenant: TenantContext, properties: dict[str, Any]) -> str:
        extraction_id = f"ext_{self._client.collection('_ids').document().id}"
        self._client.collection("evidence_events").document(extraction_id).create(
            _event(
                event_type=EvidenceEventType.EXTRACTION_COMPLETED,
                tenant=tenant,
                subject_type="invoice",
                subject_id=extraction_id,
                properties=properties,
            )
        )
        return extraction_id

    def save_invoice(self, tenant: TenantContext, invoice: Invoice) -> Invoice:
        extraction_ref = self._client.collection("evidence_events").document(invoice.extraction_id)
        invoice_ref = self._business_ref(tenant).collection("invoices").document(invoice.id)
        customer_ref = (
            self._business_ref(tenant).collection("customers").document(invoice.customer.id)
        )
        event_ref = self._client.collection("evidence_events").document(
            f"evt_invoice_confirmed_{invoice.id}"
        )
        plan_ref = self._client.collection("founder_plans").document(f"plan_{tenant.business_id}")
        transaction = self._client.transaction()

        @google_firestore.transactional
        def confirm(txn: Any) -> dict[str, Any] | None:
            extraction = extraction_ref.get(transaction=txn)
            if not extraction.exists:
                raise ApiError(404, "extraction_not_found", "The extraction could not be found.")
            extraction_data = extraction.to_dict() or {}
            if (
                extraction_data.get("business_id") != tenant.business_id
                or extraction_data.get("event_type") != EvidenceEventType.EXTRACTION_COMPLETED.value
            ):
                raise ApiError(404, "extraction_not_found", "The extraction could not be found.")
            existing = invoice_ref.get(transaction=txn)
            if existing.exists:
                return existing.to_dict() or {}
            plan_snapshot = plan_ref.get(transaction=txn)
            if plan_snapshot.exists:
                plan = FounderPlanEnrollment.model_validate(plan_snapshot.to_dict())
                if plan.invoices_used >= plan.invoice_limit:
                    raise ApiError(
                        409,
                        "plan_invoice_limit_reached",
                        "The Founder Recovery Plan ten-invoice allowance is exhausted.",
                    )
                invoices_used = plan.invoices_used + 1
                txn.update(
                    plan_ref,
                    {
                        "invoices_used": invoices_used,
                        "status": (
                            FounderPlanStatus.EXHAUSTED.value
                            if invoices_used == plan.invoice_limit
                            else FounderPlanStatus.ACTIVE.value
                        ),
                    },
                )
            txn.set(
                customer_ref,
                {
                    **invoice.customer.model_dump(mode="json"),
                    "updated_at": invoice.updated_at.isoformat(),
                },
                merge=True,
            )
            txn.create(invoice_ref, invoice.model_dump(mode="json"))
            txn.create(
                event_ref,
                _event(
                    event_type=EvidenceEventType.INVOICE_CONFIRMED,
                    tenant=tenant,
                    subject_type="invoice",
                    subject_id=invoice.id,
                    properties={
                        "currency": invoice.currency,
                        "amount_minor": invoice.amount_minor,
                        "review_required": invoice.review_required,
                        "extraction_id": invoice.extraction_id,
                    },
                ),
            )
            return None

        existing_data = confirm(transaction)
        if existing_data is not None:
            existing_invoice = Invoice.model_validate(existing_data)
            if existing_invoice.confirmation_hash != invoice.confirmation_hash:
                raise ApiError(
                    409,
                    "invoice_confirmation_conflict",
                    "This extraction was already confirmed with different values.",
                )
            return existing_invoice
        return invoice

    def get_invoice(self, tenant: TenantContext, invoice_id: str) -> Invoice:
        snapshot = self._business_ref(tenant).collection("invoices").document(invoice_id).get()
        if not snapshot.exists:
            raise ApiError(404, "invoice_not_found", "The invoice could not be found.")
        return Invoice.model_validate(snapshot.to_dict())

    def list_invoices(
        self, tenant: TenantContext, limit: int, cursor: str | None
    ) -> tuple[list[Invoice], str | None]:
        collection = self._business_ref(tenant).collection("invoices")
        query = collection.order_by("created_at", direction=google_firestore.Query.DESCENDING)
        if cursor:
            cursor_snapshot = collection.document(_cursor_decode(cursor)).get()
            if not cursor_snapshot.exists:
                raise ApiError(422, "invalid_cursor", "The pagination cursor is invalid.")
            query = query.start_after(cursor_snapshot)
        snapshots = list(query.limit(limit + 1).stream())
        has_more = len(snapshots) > limit
        selected = snapshots[:limit]
        invoices = [Invoice.model_validate(snapshot.to_dict()) for snapshot in selected]
        next_cursor = _cursor_encode(selected[-1].id) if has_more and selected else None
        return invoices, next_cursor

    def get_policy_settings(self, tenant: TenantContext) -> PolicyDefaults:
        snapshot = self._client.collection("settings").document(tenant.business_id).get()
        return PolicyDefaults.model_validate(snapshot.to_dict() or {})

    def list_actions(self, tenant: TenantContext, invoice_id: str) -> list[Action]:
        snapshots = (
            self._business_ref(tenant)
            .collection("actions")
            .where("invoice_id", "==", invoice_id)
            .order_by("created_at", direction=google_firestore.Query.DESCENDING)
            .limit(100)
            .stream()
        )
        return [Action.model_validate(snapshot.to_dict()) for snapshot in snapshots]

    def save_evaluation(
        self, tenant: TenantContext, run: AgentRun, action: Action | None
    ) -> Action | None:
        business_ref = self._business_ref(tenant)
        if action is not None:
            invoice_ref = business_ref.collection("invoices").document(run.invoice_id)
            transaction = self._client.transaction()

            @google_firestore.transactional
            def reserve(txn: Any) -> dict[str, Any]:
                invoice_snapshot = invoice_ref.get(transaction=txn)
                if not invoice_snapshot.exists:
                    raise ApiError(404, "invoice_not_found", "The invoice could not be found.")
                invoice = Invoice.model_validate(invoice_snapshot.to_dict())
                reserved = action
                created = True
                if invoice.active_action_id:
                    active_ref = business_ref.collection("actions").document(
                        invoice.active_action_id
                    )
                    active_snapshot = active_ref.get(transaction=txn)
                    if active_snapshot.exists:
                        active = Action.model_validate(active_snapshot.to_dict())
                        if active.state in {
                            ActionState.PROPOSED,
                            ActionState.AWAITING_APPROVAL,
                            ActionState.EXECUTING,
                            ActionState.FAILED,
                            ActionState.UNKNOWN,
                        }:
                            reserved = active
                            created = False
                if created:
                    sequence = invoice.reminder_sequence + 1
                    raw_key = f"{tenant.business_id}:{invoice.id}:SEND_REMINDER:{sequence}"
                    action_key = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
                    reserved = action.model_copy(
                        update={
                            "id": f"act_{action_key[:28]}",
                            "action_key": action_key,
                            "reminder_sequence": sequence,
                        }
                    )
                    txn.create(
                        business_ref.collection("actions").document(reserved.id),
                        reserved.model_dump(mode="json"),
                    )
                    txn.update(
                        invoice_ref,
                        {
                            "active_action_id": reserved.id,
                            "reminder_sequence": sequence,
                            "workflow_status": InvoiceWorkflowStatus.PAUSED.value,
                            "next_check_at": None,
                            "evaluation_lease_until": None,
                        },
                    )
                stored_run = run.model_copy(update={"action_id": reserved.id})
                txn.create(
                    business_ref.collection("agent_runs").document(run.id),
                    stored_run.model_dump(mode="json"),
                )
                if run.model_proposal is not None:
                    txn.create(
                        self._client.collection("evidence_events").document(
                            f"evt_decision_{run.id}"
                        ),
                        _event(
                            event_type=EvidenceEventType.AGENT_DECISION_CREATED,
                            tenant=tenant,
                            subject_type="invoice",
                            subject_id=run.invoice_id,
                            actor_type="AGENT",
                            properties={
                                "agent_run_id": run.id,
                                "model_id": run.model_id,
                                "prompt_version": run.prompt_version,
                                "status": run.status.value,
                            },
                        ),
                    )
                if run.policy_result:
                    txn.create(
                        self._client.collection("evidence_events").document(f"evt_policy_{run.id}"),
                        _event(
                            event_type=EvidenceEventType.POLICY_CHECKED,
                            tenant=tenant,
                            subject_type="invoice",
                            subject_id=run.invoice_id,
                            actor_type="SYSTEM",
                            properties={
                                "agent_run_id": run.id,
                                "outcome": run.policy_result.outcome.value,
                                "final_decision": run.policy_result.final_decision.value,
                                "matched_rules": run.policy_result.matched_rules,
                                "policy_version": run.policy_result.policy_version,
                            },
                        ),
                    )
                if created:
                    txn.create(
                        self._client.collection("evidence_events").document(
                            f"evt_action_{reserved.id}"
                        ),
                        _event(
                            event_type=EvidenceEventType.ACTION_PROPOSED,
                            tenant=tenant,
                            subject_type="action",
                            subject_id=reserved.id,
                            actor_type="AGENT",
                            properties={
                                "invoice_id": reserved.invoice_id,
                                "agent_run_id": reserved.agent_run_id,
                                "state": reserved.state.value,
                            },
                        ),
                    )
                return reserved.model_dump(mode="json")

            return Action.model_validate(reserve(transaction))

        batch = self._client.batch()
        batch.create(
            business_ref.collection("agent_runs").document(run.id), run.model_dump(mode="json")
        )
        if run.model_proposal is not None:
            batch.create(
                self._client.collection("evidence_events").document(f"evt_decision_{run.id}"),
                _event(
                    event_type=EvidenceEventType.AGENT_DECISION_CREATED,
                    tenant=tenant,
                    subject_type="invoice",
                    subject_id=run.invoice_id,
                    actor_type="AGENT",
                    properties={
                        "agent_run_id": run.id,
                        "model_id": run.model_id,
                        "prompt_version": run.prompt_version,
                        "status": run.status.value,
                    },
                ),
            )
        if run.policy_result:
            batch.create(
                self._client.collection("evidence_events").document(f"evt_policy_{run.id}"),
                _event(
                    event_type=EvidenceEventType.POLICY_CHECKED,
                    tenant=tenant,
                    subject_type="invoice",
                    subject_id=run.invoice_id,
                    actor_type="SYSTEM",
                    properties={
                        "agent_run_id": run.id,
                        "outcome": run.policy_result.outcome.value,
                        "final_decision": run.policy_result.final_decision.value,
                        "matched_rules": run.policy_result.matched_rules,
                        "policy_version": run.policy_result.policy_version,
                    },
                ),
            )
        batch.commit()
        return None

    def list_agent_runs(
        self, tenant: TenantContext, invoice_id: str | None, limit: int, cursor: str | None
    ) -> tuple[list[AgentRun], str | None]:
        collection = self._business_ref(tenant).collection("agent_runs")
        query = collection.order_by("created_at", direction=google_firestore.Query.DESCENDING)
        if invoice_id:
            query = query.where("invoice_id", "==", invoice_id)
        if cursor:
            cursor_snapshot = collection.document(_cursor_decode(cursor)).get()
            if not cursor_snapshot.exists:
                raise ApiError(422, "invalid_cursor", "The pagination cursor is invalid.")
            query = query.start_after(cursor_snapshot)
        snapshots = list(query.limit(limit + 1).stream())
        has_more = len(snapshots) > limit
        selected = snapshots[:limit]
        runs = [AgentRun.model_validate(snapshot.to_dict()) for snapshot in selected]
        next_cursor = _cursor_encode(selected[-1].id) if has_more and selected else None
        return runs, next_cursor

    def list_all_actions(
        self, tenant: TenantContext, state: ActionState | None, limit: int, cursor: str | None
    ) -> tuple[list[Action], str | None]:
        collection = self._business_ref(tenant).collection("actions")
        query = collection.order_by("created_at", direction=google_firestore.Query.DESCENDING)
        if state is not None:
            query = query.where("state", "==", state.value)
        if cursor:
            snapshot = collection.document(_cursor_decode(cursor)).get()
            if not snapshot.exists:
                raise ApiError(422, "invalid_cursor", "The pagination cursor is invalid.")
            query = query.start_after(snapshot)
        snapshots = list(query.limit(limit + 1).stream())
        selected = snapshots[:limit]
        return (
            [Action.model_validate(snapshot.to_dict()) for snapshot in selected],
            _cursor_encode(selected[-1].id) if len(snapshots) > limit and selected else None,
        )

    def get_action(self, tenant: TenantContext, action_id: str) -> Action:
        snapshot = self._business_ref(tenant).collection("actions").document(action_id).get()
        if not snapshot.exists:
            raise ApiError(404, "action_not_found", "The action could not be found.")
        action = Action.model_validate(snapshot.to_dict())
        self.get_invoice(tenant, action.invoice_id)
        return action

    def update_action(
        self,
        tenant: TenantContext,
        action: Action,
        attempt: ActionAttempt | None,
        event_type: EvidenceEventType,
        actor_type: str,
    ) -> Action:
        current = self.get_action(tenant, action.id)
        if current.invoice_id != action.invoice_id:
            raise ApiError(409, "action_conflict", "The action no longer matches its invoice.")
        batch = self._client.batch()
        action_ref = self._business_ref(tenant).collection("actions").document(action.id)
        batch.set(action_ref, action.model_dump(mode="json"))
        if attempt is not None:
            batch.create(
                action_ref.collection("attempts").document(attempt.id),
                attempt.model_dump(mode="json"),
            )
        event_id = (
            f"evt_{event_type.value.replace('.', '_')}_{action.id}_"
            f"{action.state.value.lower()}_{action.attempt_count}"
        )
        batch.create(
            self._client.collection("evidence_events").document(event_id),
            _event(
                event_type=event_type,
                tenant=tenant,
                subject_type="action",
                subject_id=action.id,
                actor_type=actor_type,
                properties={
                    "invoice_id": action.invoice_id,
                    "state": action.state.value,
                    "automatic": action.automatic,
                    "failure_code": action.failure_code,
                    "provider_message_id": action.provider_message_id,
                },
            ),
        )
        batch.commit()
        return action

    def reserve_action_execution(
        self,
        tenant: TenantContext,
        action: Action,
        expected_state: ActionState,
        actor_type: str,
    ) -> Action:
        action_ref = self._business_ref(tenant).collection("actions").document(action.id)
        transaction = self._client.transaction()

        @google_firestore.transactional
        def reserve(txn: Any) -> dict[str, Any]:
            snapshot = action_ref.get(transaction=txn)
            if not snapshot.exists:
                raise ApiError(404, "action_not_found", "The action could not be found.")
            current = Action.model_validate(snapshot.to_dict())
            if current.state != expected_state:
                raise ApiError(409, "action_not_executable", "The action state changed.")
            now = datetime.now(UTC)
            executing = action.model_copy(
                update={
                    "state": ActionState.EXECUTING,
                    "attempt_count": current.attempt_count + 1,
                    "execution_started_at": now,
                    "execution_completed_at": None,
                    "failure_code": None,
                    "failure_message": None,
                    "delivery_possible": None,
                }
            )
            txn.set(action_ref, executing.model_dump(mode="json"))
            if expected_state in {ActionState.AWAITING_APPROVAL, ActionState.FAILED}:
                txn.create(
                    self._client.collection("evidence_events").document(
                        f"evt_action_approved_{action.id}_{executing.attempt_count}"
                    ),
                    _event(
                        event_type=EvidenceEventType.ACTION_APPROVED,
                        tenant=tenant,
                        subject_type="action",
                        subject_id=action.id,
                        actor_type=actor_type,
                        properties={"invoice_id": action.invoice_id},
                    ),
                )
            txn.create(
                self._client.collection("evidence_events").document(
                    f"evt_action_executing_{action.id}_{executing.attempt_count}"
                ),
                _event(
                    event_type=EvidenceEventType.ACTION_EXECUTED,
                    tenant=tenant,
                    subject_type="action",
                    subject_id=action.id,
                    actor_type=actor_type,
                    properties={
                        "invoice_id": action.invoice_id,
                        "state": ActionState.EXECUTING.value,
                        "automatic": action.automatic,
                    },
                ),
            )
            return executing.model_dump(mode="json")

        return Action.model_validate(reserve(transaction))

    def update_invoice(self, tenant: TenantContext, invoice: Invoice) -> Invoice:
        current = self.get_invoice(tenant, invoice.id)
        if current.business_id != invoice.business_id:
            raise ApiError(409, "invoice_conflict", "The invoice tenant changed unexpectedly.")
        self._business_ref(tenant).collection("invoices").document(invoice.id).set(
            invoice.model_dump(mode="json")
        )
        return invoice

    def record_payment(self, tenant: TenantContext, payment: Payment) -> tuple[Payment, Invoice]:
        business_ref = self._business_ref(tenant)
        invoice_ref = business_ref.collection("invoices").document(payment.invoice_id)
        payment_ref = business_ref.collection("payments").document(payment.id)
        event_ref = self._client.collection("evidence_events").document(
            f"evt_payment_recorded_{payment.id}"
        )
        close_event_ref = self._client.collection("evidence_events").document(
            f"evt_invoice_closed_{payment.invoice_id}"
        )
        transaction = self._client.transaction()

        @google_firestore.transactional
        def record(txn: Any) -> tuple[dict[str, Any], dict[str, Any]]:
            invoice_snapshot = invoice_ref.get(transaction=txn)
            if not invoice_snapshot.exists:
                raise ApiError(404, "invoice_not_found", "The invoice could not be found.")
            invoice = Invoice.model_validate(invoice_snapshot.to_dict())
            existing = payment_ref.get(transaction=txn)
            if existing.exists:
                existing_payment = Payment.model_validate(existing.to_dict())
                if existing_payment.model_dump(exclude={"created_at"}) != payment.model_dump(
                    exclude={"created_at"}
                ):
                    raise ApiError(
                        409, "payment_idempotency_conflict", "The payment key is already in use."
                    )
                return existing_payment.model_dump(mode="json"), invoice.model_dump(mode="json")
            paid_total = invoice.verified_paid_minor + payment.amount_minor
            if paid_total > invoice.amount_minor:
                raise ApiError(409, "payment_exceeds_balance", "Payment exceeds the open balance.")
            cancelled_action: Action | None = None
            active_action_ref = None
            if paid_total == invoice.amount_minor and invoice.active_action_id:
                active_action_ref = business_ref.collection("actions").document(
                    invoice.active_action_id
                )
                active_snapshot = active_action_ref.get(transaction=txn)
                if active_snapshot.exists:
                    active = Action.model_validate(active_snapshot.to_dict())
                    if active.state in {
                        ActionState.AWAITING_APPROVAL,
                        ActionState.PROPOSED,
                        ActionState.FAILED,
                    }:
                        cancelled_action = active.model_copy(
                            update={
                                "state": ActionState.CANCELLED,
                                "cancelled_by": "payment-workflow",
                                "cancelled_at": payment.created_at,
                                "cancel_reason": "Invoice balance was fully paid.",
                            }
                        )
            updated = invoice.model_copy(
                update={
                    "verified_paid_minor": paid_total,
                    "updated_at": payment.created_at,
                    "workflow_status": (
                        InvoiceWorkflowStatus.CLOSED
                        if paid_total == invoice.amount_minor
                        else invoice.workflow_status
                    ),
                    "next_check_at": None
                    if paid_total == invoice.amount_minor
                    else invoice.next_check_at,
                    "active_action_id": None
                    if paid_total == invoice.amount_minor
                    else invoice.active_action_id,
                }
            )
            txn.create(payment_ref, payment.model_dump(mode="json"))
            txn.set(invoice_ref, updated.model_dump(mode="json"))
            if cancelled_action is not None and active_action_ref is not None:
                txn.set(active_action_ref, cancelled_action.model_dump(mode="json"))
                txn.create(
                    self._client.collection("evidence_events").document(
                        f"evt_action_cancelled_{cancelled_action.id}_payment"
                    ),
                    _event(
                        event_type=EvidenceEventType.ACTION_CANCELLED,
                        tenant=tenant,
                        subject_type="action",
                        subject_id=cancelled_action.id,
                        actor_type="SYSTEM",
                        properties={
                            "invoice_id": invoice.id,
                            "reason": "invoice_paid",
                        },
                    ),
                )
            txn.create(
                event_ref,
                _event(
                    event_type=EvidenceEventType.PAYMENT_RECORDED,
                    tenant=tenant,
                    subject_type="payment",
                    subject_id=payment.id,
                    properties={
                        "invoice_id": payment.invoice_id,
                        "amount_minor": payment.amount_minor,
                        "currency": payment.currency,
                        "invoice_closed": paid_total == invoice.amount_minor,
                    },
                ),
            )
            if paid_total == invoice.amount_minor:
                txn.create(
                    close_event_ref,
                    _event(
                        event_type=EvidenceEventType.INVOICE_CLOSED,
                        tenant=tenant,
                        subject_type="invoice",
                        subject_id=invoice.id,
                        properties={"payment_id": payment.id},
                    ),
                )
            return payment.model_dump(mode="json"), updated.model_dump(mode="json")

        payment_data, invoice_data = record(transaction)
        return Payment.model_validate(payment_data), Invoice.model_validate(invoice_data)

    def list_payments(self, tenant: TenantContext, invoice_id: str | None = None) -> list[Payment]:
        query = (
            self._business_ref(tenant)
            .collection("payments")
            .order_by("created_at", direction=google_firestore.Query.DESCENDING)
        )
        if invoice_id:
            self.get_invoice(tenant, invoice_id)
            query = query.where("invoice_id", "==", invoice_id)
        return [Payment.model_validate(snapshot.to_dict()) for snapshot in query.stream()]

    def update_automation(self, tenant: TenantContext, enabled: bool) -> PolicyDefaults:
        ref = self._client.collection("settings").document(tenant.business_id)
        ref.set({"automation_enabled": enabled}, merge=True)
        now = datetime.now(UTC)
        self._client.collection("evidence_events").document(
            f"evt_automation_{tenant.business_id}_{int(now.timestamp() * 1000)}"
        ).create(
            _event(
                event_type=EvidenceEventType.AUTOMATION_CHANGED,
                tenant=tenant,
                subject_type="business",
                subject_id=tenant.business_id,
                properties={"enabled": enabled},
            )
        )
        return self.get_policy_settings(tenant)

    def create_oauth_state(self, tenant: TenantContext, state: GmailOAuthState) -> None:
        if state.business_id != tenant.business_id or state.user_id != tenant.user_id:
            raise ApiError(403, "oauth_state_tenant_mismatch", "OAuth state tenant mismatch.")
        self._client.collection("oauth_states").document(state.state).create(
            state.model_dump(mode="json")
        )

    def consume_oauth_state(self, state: str) -> GmailOAuthState:
        ref = self._client.collection("oauth_states").document(state)
        transaction = self._client.transaction()

        @google_firestore.transactional
        def consume(txn: Any) -> dict[str, Any]:
            snapshot = ref.get(transaction=txn)
            if not snapshot.exists:
                raise ApiError(
                    400, "invalid_oauth_state", "The Gmail connection request is invalid."
                )
            data = snapshot.to_dict() or {}
            parsed = GmailOAuthState.model_validate(data)
            txn.delete(ref)
            return parsed.model_dump(mode="json")

        result = GmailOAuthState.model_validate(consume(transaction))
        if result.expires_at <= datetime.now(UTC):
            raise ApiError(400, "expired_oauth_state", "The Gmail connection request expired.")
        return result

    def get_gmail_connection(self, business_id: str) -> GmailConnection | None:
        snapshot = (
            self._client.collection("businesses")
            .document(business_id)
            .collection("integrations")
            .document("gmail")
            .get()
        )
        return GmailConnection.model_validate(snapshot.to_dict()) if snapshot.exists else None

    def save_gmail_connection(
        self, tenant: TenantContext, connection: GmailConnection
    ) -> GmailConnection:
        if connection.business_id != tenant.business_id:
            raise ApiError(403, "gmail_tenant_mismatch", "Gmail connection tenant mismatch.")
        ref = self._business_ref(tenant).collection("integrations").document("gmail")
        ref.set(connection.model_dump(mode="json"))
        self._client.collection("evidence_events").document(
            f"evt_gmail_connected_{tenant.business_id}_{int(connection.connected_at.timestamp())}"
        ).create(
            _event(
                event_type=EvidenceEventType.GMAIL_CONNECTED,
                tenant=tenant,
                subject_type="business",
                subject_id=tenant.business_id,
                properties={"connected": True},
            )
        )
        return connection

    def disconnect_gmail(self, tenant: TenantContext) -> None:
        connection = self.get_gmail_connection(tenant.business_id)
        if connection is None:
            return
        now = datetime.now(UTC)
        updated = connection.model_copy(
            update={"encrypted_refresh_token": "", "disconnected_at": now, "updated_at": now}
        )
        self._business_ref(tenant).collection("integrations").document("gmail").set(
            updated.model_dump(mode="json")
        )
        self._client.collection("evidence_events").document(
            f"evt_gmail_disconnected_{tenant.business_id}_{int(now.timestamp())}"
        ).create(
            _event(
                event_type=EvidenceEventType.GMAIL_DISCONNECTED,
                tenant=tenant,
                subject_type="business",
                subject_id=tenant.business_id,
                properties={"connected": False},
            )
        )

    def claim_due_invoices(
        self, now: datetime, limit: int, lease_minutes: int
    ) -> list[tuple[TenantContext, Invoice]]:
        snapshots = list(
            self._client.collection_group("invoices")
            .where("workflow_status", "==", InvoiceWorkflowStatus.OPEN.value)
            .where("next_check_at", "<=", now.isoformat())
            .order_by("next_check_at")
            .limit(limit * 2)
            .stream()
        )
        claimed: list[tuple[TenantContext, Invoice]] = []
        lease_until = now + timedelta(minutes=lease_minutes)
        for snapshot in snapshots:
            if len(claimed) >= limit:
                break
            candidate_ref = snapshot.reference
            transaction = self._client.transaction()

            @google_firestore.transactional
            def claim(txn: Any, ref: Any = candidate_ref) -> dict[str, Any] | None:
                current_snapshot = ref.get(transaction=txn)
                if not current_snapshot.exists:
                    return None
                current = Invoice.model_validate(current_snapshot.to_dict())
                if (
                    current.workflow_status != InvoiceWorkflowStatus.OPEN
                    or current.next_check_at is None
                    or current.next_check_at > now
                    or (
                        current.evaluation_lease_until is not None
                        and current.evaluation_lease_until > now
                    )
                ):
                    return None
                claimed_value = current.model_copy(update={"evaluation_lease_until": lease_until})
                txn.update(
                    ref,
                    {"evaluation_lease_until": lease_until.isoformat()},
                )
                return claimed_value.model_dump(mode="json")

            claimed_data = claim(transaction)
            if claimed_data is None:
                continue
            claimed_invoice = Invoice.model_validate(claimed_data)
            claimed.append(
                (
                    TenantContext(
                        user_id="cloud-scheduler",
                        business_id=claimed_invoice.business_id,
                        role=MembershipRole.OWNER,
                    ),
                    claimed_invoice,
                )
            )
        return claimed

    def list_all_invoices(self, tenant: TenantContext | None = None) -> list[Invoice]:
        if tenant:
            snapshots = self._business_ref(tenant).collection("invoices").stream()
        else:
            snapshots = self._client.collection_group("invoices").stream()
        return [Invoice.model_validate(snapshot.to_dict()) for snapshot in snapshots]

    def list_all_agent_runs(self, tenant: TenantContext | None = None) -> list[AgentRun]:
        if tenant:
            snapshots = self._business_ref(tenant).collection("agent_runs").stream()
        else:
            snapshots = self._client.collection_group("agent_runs").stream()
        return [AgentRun.model_validate(snapshot.to_dict()) for snapshot in snapshots]

    def list_all_action_records(self, tenant: TenantContext | None = None) -> list[Action]:
        if tenant:
            return [
                Action.model_validate(snapshot.to_dict())
                for snapshot in self._business_ref(tenant).collection("actions").stream()
            ]
        return [
            Action.model_validate(snapshot.to_dict())
            for snapshot in self._client.collection_group("actions").stream()
        ]

    def list_businesses(self) -> list[Business]:
        return [
            self._parse_business(snapshot.id, snapshot.to_dict() or {})
            for snapshot in self._client.collection("businesses").stream()
        ]

    def list_businesses_page(
        self, limit: int, cursor: str | None
    ) -> tuple[list[Business], str | None]:
        collection = self._client.collection("businesses")
        query = collection.order_by("created_at", direction=google_firestore.Query.DESCENDING)
        if cursor:
            snapshot = collection.document(_cursor_decode(cursor)).get()
            if not snapshot.exists:
                raise ApiError(422, "invalid_cursor", "The pagination cursor is invalid.")
            query = query.start_after(snapshot)
        snapshots = list(query.limit(limit + 1).stream())
        selected = snapshots[:limit]
        return (
            [self._parse_business(snapshot.id, snapshot.to_dict() or {}) for snapshot in selected],
            _cursor_encode(selected[-1].id) if len(snapshots) > limit and selected else None,
        )

    def get_business_by_id(self, business_id: str) -> Business:
        snapshot = self._client.collection("businesses").document(business_id).get()
        if not snapshot.exists:
            raise ApiError(404, "business_not_found", "The business could not be found.")
        return self._parse_business(business_id, snapshot.to_dict() or {})

    def classify_business(
        self,
        business_id: str,
        classification: DataClassification,
        relationship: BusinessRelationship,
    ) -> Business:
        ref = self._client.collection("businesses").document(business_id)
        snapshot = ref.get()
        if not snapshot.exists:
            raise ApiError(404, "business_not_found", "The business could not be found.")
        ref.update(
            {"data_classification": classification.value, "relationship": relationship.value}
        )
        data = snapshot.to_dict() or {}
        data.update(
            {"data_classification": classification.value, "relationship": relationship.value}
        )
        return self._parse_business(business_id, data)

    def create_ledger_entry(self, entry: EvidenceLedgerEntry) -> EvidenceLedgerEntry:
        ref = self._client.collection("evidence_ledger").document(entry.id)
        event_ref = self._client.collection("evidence_events").document(
            f"evt_evidence_ledger_{entry.id}"
        )
        batch = self._client.batch()
        try:
            batch.create(ref, entry.model_dump(mode="json"))
            batch.create(
                event_ref,
                {
                    "schema_version": 1,
                    "event_type": EvidenceEventType.EVIDENCE_LEDGER_RECORDED.value,
                    "business_id": entry.business_id or "platform",
                    "actor_type": "USER",
                    "actor_id": entry.created_by,
                    "subject_type": "evidence_ledger",
                    "subject_id": entry.id,
                    "occurred_at": entry.created_at.isoformat(),
                    "source": "api",
                    "properties": {
                        "kind": entry.kind.value,
                        "amount_minor": entry.amount_minor,
                        "currency": entry.currency,
                        "reversal_of": entry.reversal_of,
                    },
                },
            )
            batch.commit()
        except Exception as exc:
            if ref.get().exists:
                raise ApiError(
                    409, "ledger_entry_exists", "The ledger entry already exists."
                ) from exc
            raise
        return entry

    def list_ledger_entries(self) -> list[EvidenceLedgerEntry]:
        snapshots = (
            self._client.collection("evidence_ledger")
            .order_by("created_at", direction=google_firestore.Query.DESCENDING)
            .stream()
        )
        return [EvidenceLedgerEntry.model_validate(snapshot.to_dict()) for snapshot in snapshots]

    def list_ledger_entries_page(
        self, limit: int, cursor: str | None
    ) -> tuple[list[EvidenceLedgerEntry], str | None]:
        collection = self._client.collection("evidence_ledger")
        query = collection.order_by("created_at", direction=google_firestore.Query.DESCENDING)
        if cursor:
            snapshot = collection.document(_cursor_decode(cursor)).get()
            if not snapshot.exists:
                raise ApiError(422, "invalid_cursor", "The pagination cursor is invalid.")
            query = query.start_after(snapshot)
        snapshots = list(query.limit(limit + 1).stream())
        selected = snapshots[:limit]
        return (
            [EvidenceLedgerEntry.model_validate(snapshot.to_dict()) for snapshot in selected],
            _cursor_encode(selected[-1].id) if len(snapshots) > limit and selected else None,
        )

    def create_prospect(self, prospect: Prospect) -> Prospect:
        self._client.collection("validation_prospects").document(prospect.id).create(
            prospect.model_dump(mode="json")
        )
        return prospect

    def get_prospect(self, prospect_id: str) -> Prospect:
        snapshot = self._client.collection("validation_prospects").document(prospect_id).get()
        if not snapshot.exists:
            raise ApiError(404, "prospect_not_found", "The prospect could not be found.")
        return Prospect.model_validate(snapshot.to_dict())

    def update_prospect(self, prospect_id: str, changes: dict[str, Any]) -> Prospect:
        ref = self._client.collection("validation_prospects").document(prospect_id)
        if not ref.get().exists:
            raise ApiError(404, "prospect_not_found", "The prospect could not be found.")
        changes = {**changes, "updated_at": datetime.now(UTC)}
        ref.update(changes)
        return self.get_prospect(prospect_id)

    def list_prospects(self, limit: int, cursor: str | None) -> tuple[list[Prospect], str | None]:
        collection = self._client.collection("validation_prospects")
        query = collection.order_by("updated_at", direction=google_firestore.Query.DESCENDING)
        if cursor:
            snapshot = collection.document(_cursor_decode(cursor)).get()
            if not snapshot.exists:
                raise ApiError(422, "invalid_cursor", "The pagination cursor is invalid.")
            query = query.start_after(snapshot)
        snapshots = list(query.limit(limit + 1).stream())
        selected = snapshots[:limit]
        return (
            [Prospect.model_validate(snapshot.to_dict()) for snapshot in selected],
            _cursor_encode(selected[-1].id) if len(snapshots) > limit and selected else None,
        )

    def create_interview(self, interview: Interview) -> Interview:
        self.get_prospect(interview.prospect_id)
        self._client.collection("validation_interviews").document(interview.id).create(
            interview.model_dump(mode="json")
        )
        self.update_prospect(
            interview.prospect_id,
            {
                "status": ProspectStatus.INTERVIEWED.value,
                "next_follow_up_on": interview.follow_up_on,
            },
        )
        return interview

    def list_interviews(
        self, prospect_id: str, limit: int, cursor: str | None
    ) -> tuple[list[Interview], str | None]:
        self.get_prospect(prospect_id)
        collection = self._client.collection("validation_interviews")
        query = collection.where("prospect_id", "==", prospect_id).order_by(
            "created_at", direction=google_firestore.Query.DESCENDING
        )
        if cursor:
            snapshot = collection.document(_cursor_decode(cursor)).get()
            if not snapshot.exists or (snapshot.to_dict() or {}).get("prospect_id") != prospect_id:
                raise ApiError(422, "invalid_cursor", "The pagination cursor is invalid.")
            query = query.start_after(snapshot)
        snapshots = list(query.limit(limit + 1).stream())
        selected = snapshots[:limit]
        return (
            [Interview.model_validate(snapshot.to_dict()) for snapshot in selected],
            _cursor_encode(selected[-1].id) if len(snapshots) > limit and selected else None,
        )

    def activate_founder_plan(
        self, enrollment: FounderPlanEnrollment, ledger: EvidenceLedgerEntry
    ) -> FounderPlanEnrollment:
        invoices_query = (
            self._client.collection("businesses")
            .document(enrollment.business_id)
            .collection("invoices")
            .limit(enrollment.invoice_limit)
        )
        plan_ref = self._client.collection("founder_plans").document(enrollment.id)
        ledger_ref = self._client.collection("evidence_ledger").document(ledger.id)
        event_ref = self._client.collection("evidence_events").document(
            f"evt_plan_activated_{enrollment.id}"
        )
        transaction = self._client.transaction(max_attempts=5)

        @google_firestore.transactional
        def activate(txn: Any) -> tuple[dict[str, Any], bool]:
            existing = plan_ref.get(transaction=txn)
            if existing.exists:
                return existing.to_dict() or {}, False
            invoices = list(invoices_query.get(transaction=txn))
            activated = enrollment.model_copy(
                update={
                    "invoices_used": len(invoices),
                    "status": (
                        FounderPlanStatus.EXHAUSTED
                        if len(invoices) == enrollment.invoice_limit
                        else FounderPlanStatus.ACTIVE
                    ),
                }
            )
            txn.create(plan_ref, activated.model_dump(mode="json"))
            txn.create(ledger_ref, ledger.model_dump(mode="json"))
            txn.create(
                event_ref,
                {
                    "schema_version": 1,
                    "event_type": EvidenceEventType.PLAN_ACTIVATED.value,
                    "business_id": activated.business_id,
                    "actor_type": "USER",
                    "actor_id": enrollment.activated_by,
                    "subject_type": "founder_plan",
                    "subject_id": activated.id,
                    "occurred_at": activated.activated_at.isoformat(),
                    "source": "api",
                    "properties": {
                        "plan_version": activated.plan_version,
                        "price_minor": activated.price_minor,
                        "currency": activated.currency,
                        "invoice_limit": activated.invoice_limit,
                    },
                },
            )
            return activated.model_dump(mode="json"), True

        plan_data, created = activate(transaction)
        result = FounderPlanEnrollment.model_validate(plan_data)
        if not created and result.idempotency_key != enrollment.idempotency_key:
            raise ApiError(409, "founder_plan_exists", "A founder plan already exists.")
        return result

    def get_founder_plan(self, business_id: str) -> FounderPlanEnrollment | None:
        snapshot = self._client.collection("founder_plans").document(f"plan_{business_id}").get()
        return FounderPlanEnrollment.model_validate(snapshot.to_dict()) if snapshot.exists else None

    def list_founder_plans(
        self, limit: int, cursor: str | None
    ) -> tuple[list[FounderPlanEnrollment], str | None]:
        collection = self._client.collection("founder_plans")
        query = collection.order_by("activated_at", direction=google_firestore.Query.DESCENDING)
        if cursor:
            snapshot = collection.document(_cursor_decode(cursor)).get()
            if not snapshot.exists:
                raise ApiError(422, "invalid_cursor", "The pagination cursor is invalid.")
            query = query.start_after(snapshot)
        snapshots = list(query.limit(limit + 1).stream())
        selected = snapshots[:limit]
        return (
            [FounderPlanEnrollment.model_validate(snapshot.to_dict()) for snapshot in selected],
            _cursor_encode(selected[-1].id) if len(snapshots) > limit and selected else None,
        )

    def delete_account(self, tenant: TenantContext, retain_anonymous_metrics: bool) -> None:
        invoices = self.list_all_invoices(tenant)
        runs = self.list_all_agent_runs(tenant)
        actions = self.list_all_action_records(tenant)
        if retain_anonymous_metrics:
            aggregate_ref = self._client.collection("platform_aggregates").document(
                "deleted_accounts"
            )
            aggregate_ref.set(
                {
                    "businesses": google_firestore.Increment(1),
                    "invoices": google_firestore.Increment(len(invoices)),
                    "agent_runs": google_firestore.Increment(len(runs)),
                    "successful_actions": google_firestore.Increment(
                        sum(1 for action in actions if action.state == ActionState.SUCCEEDED)
                    ),
                    "updated_at": datetime.now(UTC),
                },
                merge=True,
            )
        for snapshot in (
            self._client.collection("evidence_ledger")
            .where("business_id", "==", tenant.business_id)
            .stream()
        ):
            data = snapshot.to_dict() or {}
            reference = str(data.get("reference", ""))
            snapshot.reference.update(
                {
                    "business_id": None,
                    "created_by": "deleted_account",
                    "reference": f"sha256:{hashlib.sha256(reference.encode()).hexdigest()}",
                }
            )
        for collection_name in ("oauth_states", "evidence_events"):
            for snapshot in (
                self._client.collection(collection_name)
                .where("business_id", "==", tenant.business_id)
                .stream()
            ):
                snapshot.reference.delete()
        for snapshot in (
            self._client.collection("validation_prospects")
            .where("linked_business_id", "==", tenant.business_id)
            .stream()
        ):
            snapshot.reference.update({"linked_business_id": None, "updated_at": datetime.now(UTC)})
        self._client.collection("founder_plans").document(f"plan_{tenant.business_id}").delete()
        self._client.collection("settings").document(tenant.business_id).delete()
        self._client.recursive_delete(self._business_ref(tenant))
        self._client.collection("users").document(tenant.user_id).delete()

    def _business_ref(self, tenant: TenantContext) -> Any:
        return self._client.collection("businesses").document(tenant.business_id)

    @staticmethod
    def _parse_business(business_id: str, data: dict[str, Any]) -> Business:
        return Business(
            id=business_id,
            name=str(data["name"]),
            owner_user_id=str(data["owner_user_id"]),
            created_at=data["created_at"],
            data_classification=data.get("data_classification", DataClassification.UNCLASSIFIED),
            relationship=data.get("relationship", BusinessRelationship.UNCLASSIFIED),
            evidence_pseudonym=str(data.get("evidence_pseudonym", "")),
        )

    @staticmethod
    def _parse_membership(business_id: str, user_id: str, data: dict[str, Any]) -> Membership:
        return Membership(
            business_id=business_id,
            user_id=user_id,
            role=MembershipRole(str(data["role"])),
            status=MembershipStatus(str(data["status"])),
            created_at=data["created_at"],
        )


class InMemoryRepository:
    """Deterministic test repository with the same tenant boundary as Firestore."""

    def __init__(self) -> None:
        self.businesses: dict[str, Business] = {}
        self.memberships: dict[str, Membership] = {}
        self.consents: dict[str, ConsentRecord] = {}
        self.optional_consents: dict[str, OptionalConsentEvent] = {}
        self.rate_limits: dict[str, tuple[int, datetime]] = {}
        self.extractions: dict[str, dict[str, Any]] = {}
        self.invoices: dict[str, Invoice] = {}
        self.actions: dict[str, Action] = {}
        self.agent_runs: dict[str, AgentRun] = {}
        self.payments: dict[str, Payment] = {}
        self.action_attempts: dict[str, ActionAttempt] = {}
        self.oauth_states: dict[str, GmailOAuthState] = {}
        self.gmail_connections: dict[str, GmailConnection] = {}
        self.ledger_entries: dict[str, EvidenceLedgerEntry] = {}
        self.prospects: dict[str, Prospect] = {}
        self.interviews: dict[str, Interview] = {}
        self.founder_plans: dict[str, FounderPlanEnrollment] = {}
        self.evidence_events: dict[str, dict[str, Any]] = {}
        self.policy_settings: dict[str, PolicyDefaults] = {}
        self._id_counter = 0

    def _next_id(self, prefix: str) -> str:
        self._id_counter += 1
        return f"{prefix}_{self._id_counter:08d}"

    def ready(self) -> bool:
        return True

    def get_account(self, user: AuthenticatedUser) -> tuple[Business | None, Membership | None]:
        membership = self.memberships.get(user.uid)
        if membership is None:
            return None, None
        return self.businesses.get(membership.business_id), membership

    def get_or_create_business(
        self, user: AuthenticatedUser, name: str
    ) -> tuple[Business, Membership]:
        existing = self.get_account(user)
        if existing[0] and existing[1]:
            return cast(tuple[Business, Membership], existing)
        business_id = _business_id(user.uid)
        now = datetime.now(UTC)
        business = Business(
            id=business_id,
            name=name.strip(),
            owner_user_id=user.uid,
            created_at=now,
            evidence_pseudonym=f"evid_{secrets.token_urlsafe(12)}",
        )
        membership = Membership(
            business_id=business_id,
            user_id=user.uid,
            role=MembershipRole.OWNER,
            status=MembershipStatus.ACTIVE,
            created_at=now,
        )
        self.businesses[business_id] = business
        self.memberships[user.uid] = membership
        self.policy_settings[business_id] = PolicyDefaults()
        return business, membership

    def require_tenant(self, user: AuthenticatedUser) -> TenantContext:
        business, membership = self.get_account(user)
        if not business or not membership:
            raise ApiError(404, "business_not_found", "Complete business onboarding first.")
        return TenantContext(user_id=user.uid, business_id=business.id, role=membership.role)

    def get_consent(self, tenant: TenantContext, version: str) -> ConsentRecord | None:
        return self.consents.get(_consent_id(tenant, version))

    def grant_consent(
        self, tenant: TenantContext, version: str, statement_sha256: str
    ) -> ConsentRecord:
        consent_id = _consent_id(tenant, version)
        existing = self.consents.get(consent_id)
        if existing:
            return existing
        record = ConsentRecord(
            version=version,
            granted_at=datetime.now(UTC),
            user_id=tenant.user_id,
            business_id=tenant.business_id,
            statement_sha256=statement_sha256,
        )
        self.consents[consent_id] = record
        self.evidence_events[f"evt_{consent_id}"] = _event(
            event_type=EvidenceEventType.CONSENT_GRANTED,
            tenant=tenant,
            subject_type="business",
            subject_id=tenant.business_id,
            properties={"version": version},
        )
        return record

    def list_optional_consents(self, tenant: TenantContext) -> list[OptionalConsentEvent]:
        return sorted(
            (
                event
                for event in self.optional_consents.values()
                if event.business_id == tenant.business_id
            ),
            key=lambda event: event.occurred_at,
            reverse=True,
        )

    def append_optional_consent(
        self, tenant: TenantContext, event: OptionalConsentEvent
    ) -> OptionalConsentEvent:
        if event.business_id != tenant.business_id or event.user_id != tenant.user_id:
            raise ApiError(403, "consent_tenant_mismatch", "Consent tenant mismatch.")
        if event.id in self.optional_consents:
            raise ApiError(409, "consent_event_exists", "The consent event already exists.")
        self.optional_consents[event.id] = event
        self.evidence_events[f"evt_{event.id}"] = _event(
            event_type=(
                EvidenceEventType.OPTIONAL_CONSENT_GRANTED
                if event.action == ConsentEventAction.GRANTED
                else EvidenceEventType.OPTIONAL_CONSENT_WITHDRAWN
            ),
            tenant=tenant,
            subject_type="consent",
            subject_id=event.id,
            properties={
                "consent_type": event.consent_type.value,
                "action": event.action.value,
                "version": event.version,
            },
        )
        return event

    def consume_rate_limit(
        self,
        subject: str,
        operation: str,
        limit: int,
        window_seconds: int,
        now: datetime,
    ) -> None:
        window = int(now.timestamp()) // window_seconds
        digest = hashlib.sha256(f"{subject}:{operation}:{window}".encode()).hexdigest()
        expires_at = datetime.fromtimestamp((window + 1) * window_seconds, UTC)
        count, stored_expiry = self.rate_limits.get(digest, (0, expires_at))
        if stored_expiry <= now:
            count = 0
        if count >= limit:
            retry_after = window_seconds - (int(now.timestamp()) % window_seconds)
            raise ApiError(
                429,
                "rate_limit_exceeded",
                "Too many requests. Try again after the rate-limit window resets.",
                {"operation": operation, "retry_after_seconds": retry_after},
                {"Retry-After": str(retry_after)},
            )
        self.rate_limits[digest] = (count + 1, expires_at)

    def record_extraction(self, tenant: TenantContext, properties: dict[str, Any]) -> str:
        extraction_id = self._next_id("ext")
        self.extractions[extraction_id] = {
            "business_id": tenant.business_id,
            "event_type": EvidenceEventType.EXTRACTION_COMPLETED.value,
            "properties": properties,
        }
        self.evidence_events[extraction_id] = self.extractions[extraction_id]
        return extraction_id

    def save_invoice(self, tenant: TenantContext, invoice: Invoice) -> Invoice:
        extraction = self.extractions.get(invoice.extraction_id)
        if not extraction or extraction["business_id"] != tenant.business_id:
            raise ApiError(404, "extraction_not_found", "The extraction could not be found.")
        existing = self.invoices.get(invoice.id)
        if existing:
            if existing.business_id != tenant.business_id:
                raise ApiError(404, "invoice_not_found", "The invoice could not be found.")
            if existing.confirmation_hash != invoice.confirmation_hash:
                raise ApiError(
                    409,
                    "invoice_confirmation_conflict",
                    "This extraction was already confirmed with different values.",
                )
            return existing
        plan = self.founder_plans.get(tenant.business_id)
        if plan:
            if plan.invoices_used >= plan.invoice_limit:
                raise ApiError(
                    409,
                    "plan_invoice_limit_reached",
                    "The Founder Recovery Plan ten-invoice allowance is exhausted.",
                )
            used = plan.invoices_used + 1
            self.founder_plans[tenant.business_id] = plan.model_copy(
                update={
                    "invoices_used": used,
                    "status": (
                        FounderPlanStatus.EXHAUSTED
                        if used == plan.invoice_limit
                        else FounderPlanStatus.ACTIVE
                    ),
                }
            )
        self.invoices[invoice.id] = invoice
        self.evidence_events[f"evt_invoice_confirmed_{invoice.id}"] = _event(
            event_type=EvidenceEventType.INVOICE_CONFIRMED,
            tenant=tenant,
            subject_type="invoice",
            subject_id=invoice.id,
            properties={
                "currency": invoice.currency,
                "amount_minor": invoice.amount_minor,
                "review_required": invoice.review_required,
                "extraction_id": invoice.extraction_id,
            },
        )
        return invoice

    def get_invoice(self, tenant: TenantContext, invoice_id: str) -> Invoice:
        invoice = self.invoices.get(invoice_id)
        if not invoice or invoice.business_id != tenant.business_id:
            raise ApiError(404, "invoice_not_found", "The invoice could not be found.")
        return invoice

    def list_invoices(
        self, tenant: TenantContext, limit: int, cursor: str | None
    ) -> tuple[list[Invoice], str | None]:
        invoices = sorted(
            (
                invoice
                for invoice in self.invoices.values()
                if invoice.business_id == tenant.business_id
            ),
            key=lambda invoice: (invoice.created_at, invoice.id),
            reverse=True,
        )
        start = 0
        if cursor:
            cursor_id = _cursor_decode(cursor)
            try:
                start = next(i for i, invoice in enumerate(invoices) if invoice.id == cursor_id) + 1
            except StopIteration:
                raise ApiError(422, "invalid_cursor", "The pagination cursor is invalid.") from None
        selected = invoices[start : start + limit]
        has_more = start + limit < len(invoices)
        return selected, _cursor_encode(selected[-1].id) if has_more and selected else None

    def get_policy_settings(self, tenant: TenantContext) -> PolicyDefaults:
        return self.policy_settings.get(tenant.business_id, PolicyDefaults())

    def list_actions(self, tenant: TenantContext, invoice_id: str) -> list[Action]:
        self.get_invoice(tenant, invoice_id)
        return sorted(
            (action for action in self.actions.values() if action.invoice_id == invoice_id),
            key=lambda action: action.created_at,
            reverse=True,
        )

    def save_evaluation(
        self, tenant: TenantContext, run: AgentRun, action: Action | None
    ) -> Action | None:
        self.get_invoice(tenant, run.invoice_id)
        self.agent_runs[run.id] = run
        if run.model_proposal is not None:
            self.evidence_events[f"evt_decision_{run.id}"] = _event(
                event_type=EvidenceEventType.AGENT_DECISION_CREATED,
                tenant=tenant,
                subject_type="invoice",
                subject_id=run.invoice_id,
                properties={"model_id": run.model_id, "prompt_version": run.prompt_version},
                actor_type="AGENT",
            )
        if run.policy_result is not None:
            self.evidence_events[f"evt_policy_{run.id}"] = _event(
                event_type=EvidenceEventType.POLICY_CHECKED,
                tenant=tenant,
                subject_type="invoice",
                subject_id=run.invoice_id,
                properties={
                    "outcome": run.policy_result.outcome.value,
                    "final_decision": run.policy_result.final_decision.value,
                    "matched_rules": run.policy_result.matched_rules,
                    "policy_version": run.policy_result.policy_version,
                },
                actor_type="SYSTEM",
            )
        if action:
            existing = next(
                (
                    candidate
                    for candidate in self.actions.values()
                    if action.action_key and candidate.action_key == action.action_key
                ),
                None,
            )
            if existing:
                return existing
            self.actions[action.id] = action
            invoice = self.invoices[run.invoice_id]
            self.invoices[run.invoice_id] = invoice.model_copy(
                update={
                    "active_action_id": action.id,
                    "reminder_sequence": action.reminder_sequence,
                    "workflow_status": InvoiceWorkflowStatus.PAUSED,
                    "next_check_at": None,
                    "evaluation_lease_until": None,
                }
            )
            self.evidence_events[f"evt_action_{action.id}"] = _event(
                event_type=EvidenceEventType.ACTION_PROPOSED,
                tenant=tenant,
                subject_type="action",
                subject_id=action.id,
                properties={"invoice_id": action.invoice_id, "state": action.state.value},
                actor_type="AGENT",
            )
        return action

    def list_agent_runs(
        self, tenant: TenantContext, invoice_id: str | None, limit: int, cursor: str | None
    ) -> tuple[list[AgentRun], str | None]:
        if invoice_id:
            self.get_invoice(tenant, invoice_id)
        runs = sorted(
            (
                run
                for run in self.agent_runs.values()
                if run.business_id == tenant.business_id
                and (invoice_id is None or run.invoice_id == invoice_id)
            ),
            key=lambda run: (run.created_at, run.id),
            reverse=True,
        )
        start = 0
        if cursor:
            cursor_id = _cursor_decode(cursor)
            try:
                start = next(i for i, run in enumerate(runs) if run.id == cursor_id) + 1
            except StopIteration:
                raise ApiError(422, "invalid_cursor", "The pagination cursor is invalid.") from None
        selected = runs[start : start + limit]
        has_more = start + limit < len(runs)
        return selected, _cursor_encode(selected[-1].id) if has_more and selected else None

    def list_all_actions(
        self, tenant: TenantContext, state: ActionState | None, limit: int, cursor: str | None
    ) -> tuple[list[Action], str | None]:
        actions = sorted(
            (
                action
                for action in self.actions.values()
                if self.invoices.get(action.invoice_id)
                and self.invoices[action.invoice_id].business_id == tenant.business_id
                and (state is None or action.state == state)
            ),
            key=lambda item: (item.created_at, item.id),
            reverse=True,
        )
        start = 0
        if cursor:
            cursor_id = _cursor_decode(cursor)
            try:
                start = next(i for i, item in enumerate(actions) if item.id == cursor_id) + 1
            except StopIteration:
                raise ApiError(422, "invalid_cursor", "The pagination cursor is invalid.") from None
        selected = actions[start : start + limit]
        return (
            selected,
            _cursor_encode(selected[-1].id) if start + limit < len(actions) and selected else None,
        )

    def get_action(self, tenant: TenantContext, action_id: str) -> Action:
        action = self.actions.get(action_id)
        if action is None:
            raise ApiError(404, "action_not_found", "The action could not be found.")
        self.get_invoice(tenant, action.invoice_id)
        return action

    def update_action(
        self,
        tenant: TenantContext,
        action: Action,
        attempt: ActionAttempt | None,
        event_type: EvidenceEventType,
        actor_type: str,
    ) -> Action:
        self.get_action(tenant, action.id)
        self.actions[action.id] = action
        if attempt:
            self.action_attempts[attempt.id] = attempt
        event_id = (
            f"evt_{event_type.value.replace('.', '_')}_{action.id}_"
            f"{action.state.value.lower()}_{action.attempt_count}"
        )
        self.evidence_events[event_id] = _event(
            event_type=event_type,
            tenant=tenant,
            subject_type="action",
            subject_id=action.id,
            actor_type=actor_type,
            properties={
                "invoice_id": action.invoice_id,
                "state": action.state.value,
                "automatic": action.automatic,
                "failure_code": action.failure_code,
                "provider_message_id": action.provider_message_id,
            },
        )
        return action

    def reserve_action_execution(
        self,
        tenant: TenantContext,
        action: Action,
        expected_state: ActionState,
        actor_type: str,
    ) -> Action:
        current = self.get_action(tenant, action.id)
        if current.state != expected_state:
            raise ApiError(409, "action_not_executable", "The action state changed.")
        now = datetime.now(UTC)
        executing = action.model_copy(
            update={
                "state": ActionState.EXECUTING,
                "attempt_count": current.attempt_count + 1,
                "execution_started_at": now,
                "execution_completed_at": None,
                "failure_code": None,
                "failure_message": None,
                "delivery_possible": None,
            }
        )
        self.actions[action.id] = executing
        if expected_state in {ActionState.AWAITING_APPROVAL, ActionState.FAILED}:
            self.evidence_events[f"evt_action_approved_{action.id}_{executing.attempt_count}"] = (
                _event(
                    event_type=EvidenceEventType.ACTION_APPROVED,
                    tenant=tenant,
                    subject_type="action",
                    subject_id=action.id,
                    actor_type=actor_type,
                    properties={"invoice_id": action.invoice_id},
                )
            )
        self.evidence_events[f"evt_action_executing_{action.id}_{executing.attempt_count}"] = (
            _event(
                event_type=EvidenceEventType.ACTION_EXECUTED,
                tenant=tenant,
                subject_type="action",
                subject_id=action.id,
                actor_type=actor_type,
                properties={
                    "invoice_id": action.invoice_id,
                    "state": ActionState.EXECUTING.value,
                    "automatic": action.automatic,
                },
            )
        )
        return executing

    def update_invoice(self, tenant: TenantContext, invoice: Invoice) -> Invoice:
        self.get_invoice(tenant, invoice.id)
        self.invoices[invoice.id] = invoice
        return invoice

    def record_payment(self, tenant: TenantContext, payment: Payment) -> tuple[Payment, Invoice]:
        invoice = self.get_invoice(tenant, payment.invoice_id)
        existing = self.payments.get(payment.id)
        if existing:
            if existing.model_dump(exclude={"created_at"}) != payment.model_dump(
                exclude={"created_at"}
            ):
                raise ApiError(
                    409, "payment_idempotency_conflict", "The payment key is already in use."
                )
            return existing, invoice
        paid_total = invoice.verified_paid_minor + payment.amount_minor
        if paid_total > invoice.amount_minor:
            raise ApiError(409, "payment_exceeds_balance", "Payment exceeds the open balance.")
        updated = invoice.model_copy(
            update={
                "verified_paid_minor": paid_total,
                "updated_at": payment.created_at,
                "workflow_status": (
                    InvoiceWorkflowStatus.CLOSED
                    if paid_total == invoice.amount_minor
                    else invoice.workflow_status
                ),
                "next_check_at": None
                if paid_total == invoice.amount_minor
                else invoice.next_check_at,
                "active_action_id": None
                if paid_total == invoice.amount_minor
                else invoice.active_action_id,
            }
        )
        self.payments[payment.id] = payment
        self.invoices[invoice.id] = updated
        if paid_total == invoice.amount_minor and invoice.active_action_id:
            active = self.actions.get(invoice.active_action_id)
            if active and active.state in {
                ActionState.PROPOSED,
                ActionState.AWAITING_APPROVAL,
                ActionState.FAILED,
            }:
                cancelled = active.model_copy(
                    update={
                        "state": ActionState.CANCELLED,
                        "cancel_reason": "Invoice balance was fully paid.",
                        "cancelled_at": payment.created_at,
                        "updated_at": payment.created_at,
                    }
                )
                self.actions[active.id] = cancelled
                self.evidence_events[f"evt_action_cancelled_{active.id}_payment"] = _event(
                    event_type=EvidenceEventType.ACTION_CANCELLED,
                    tenant=tenant,
                    subject_type="action",
                    subject_id=active.id,
                    actor_type="SYSTEM",
                    properties={
                        "invoice_id": invoice.id,
                        "reason": "invoice_fully_paid",
                    },
                )
        self.evidence_events[f"evt_payment_recorded_{payment.id}"] = _event(
            event_type=EvidenceEventType.PAYMENT_RECORDED,
            tenant=tenant,
            subject_type="payment",
            subject_id=payment.id,
            properties={
                "invoice_id": payment.invoice_id,
                "amount_minor": payment.amount_minor,
                "currency": payment.currency,
                "invoice_closed": paid_total == invoice.amount_minor,
            },
        )
        if paid_total == invoice.amount_minor:
            self.evidence_events[f"evt_invoice_closed_{invoice.id}"] = _event(
                event_type=EvidenceEventType.INVOICE_CLOSED,
                tenant=tenant,
                subject_type="invoice",
                subject_id=invoice.id,
                properties={"payment_id": payment.id},
            )
        return payment, updated

    def list_payments(self, tenant: TenantContext, invoice_id: str | None = None) -> list[Payment]:
        if invoice_id:
            self.get_invoice(tenant, invoice_id)
        return sorted(
            (
                payment
                for payment in self.payments.values()
                if payment.business_id == tenant.business_id
                and (invoice_id is None or payment.invoice_id == invoice_id)
            ),
            key=lambda payment: payment.created_at,
            reverse=True,
        )

    def update_automation(self, tenant: TenantContext, enabled: bool) -> PolicyDefaults:
        current = self.get_policy_settings(tenant)
        updated = current.model_copy(update={"automation_enabled": enabled})
        self.policy_settings[tenant.business_id] = updated
        self.evidence_events[f"evt_automation_{tenant.business_id}_{len(self.evidence_events)}"] = (
            _event(
                event_type=EvidenceEventType.AUTOMATION_CHANGED,
                tenant=tenant,
                subject_type="business",
                subject_id=tenant.business_id,
                properties={"enabled": enabled},
            )
        )
        return updated

    def create_oauth_state(self, tenant: TenantContext, state: GmailOAuthState) -> None:
        if state.business_id != tenant.business_id or state.user_id != tenant.user_id:
            raise ApiError(403, "oauth_state_tenant_mismatch", "OAuth state tenant mismatch.")
        if state.state in self.oauth_states:
            raise ApiError(409, "oauth_state_conflict", "OAuth state already exists.")
        self.oauth_states[state.state] = state

    def consume_oauth_state(self, state: str) -> GmailOAuthState:
        result = self.oauth_states.pop(state, None)
        if result is None:
            raise ApiError(400, "invalid_oauth_state", "The Gmail connection request is invalid.")
        if result.expires_at <= datetime.now(UTC):
            raise ApiError(400, "expired_oauth_state", "The Gmail connection request expired.")
        return result

    def get_gmail_connection(self, business_id: str) -> GmailConnection | None:
        return self.gmail_connections.get(business_id)

    def save_gmail_connection(
        self, tenant: TenantContext, connection: GmailConnection
    ) -> GmailConnection:
        if connection.business_id != tenant.business_id:
            raise ApiError(403, "gmail_tenant_mismatch", "Gmail connection tenant mismatch.")
        self.gmail_connections[tenant.business_id] = connection
        return connection

    def disconnect_gmail(self, tenant: TenantContext) -> None:
        connection = self.gmail_connections.get(tenant.business_id)
        if connection:
            now = datetime.now(UTC)
            self.gmail_connections[tenant.business_id] = connection.model_copy(
                update={"encrypted_refresh_token": "", "disconnected_at": now, "updated_at": now}
            )

    def claim_due_invoices(
        self, now: datetime, limit: int, lease_minutes: int
    ) -> list[tuple[TenantContext, Invoice]]:
        due = sorted(
            (
                invoice
                for invoice in self.invoices.values()
                if invoice.workflow_status == InvoiceWorkflowStatus.OPEN
                and invoice.next_check_at is not None
                and invoice.next_check_at <= now
                and (
                    invoice.evaluation_lease_until is None or invoice.evaluation_lease_until <= now
                )
            ),
            key=lambda invoice: invoice.next_check_at or now,
        )[:limit]
        result: list[tuple[TenantContext, Invoice]] = []
        for invoice in due:
            claimed = invoice.model_copy(
                update={"evaluation_lease_until": now + timedelta(minutes=lease_minutes)}
            )
            self.invoices[invoice.id] = claimed
            result.append(
                (
                    TenantContext("cloud-scheduler", invoice.business_id, MembershipRole.OWNER),
                    claimed,
                )
            )
        return result

    def list_all_invoices(self, tenant: TenantContext | None = None) -> list[Invoice]:
        return [
            invoice
            for invoice in self.invoices.values()
            if tenant is None or invoice.business_id == tenant.business_id
        ]

    def list_all_agent_runs(self, tenant: TenantContext | None = None) -> list[AgentRun]:
        return [
            run
            for run in self.agent_runs.values()
            if tenant is None or run.business_id == tenant.business_id
        ]

    def list_all_action_records(self, tenant: TenantContext | None = None) -> list[Action]:
        if tenant is None:
            return list(self.actions.values())
        return [
            action
            for action in self.actions.values()
            if self.invoices.get(action.invoice_id)
            and self.invoices[action.invoice_id].business_id == tenant.business_id
        ]

    def list_businesses(self) -> list[Business]:
        return list(self.businesses.values())

    def list_businesses_page(
        self, limit: int, cursor: str | None
    ) -> tuple[list[Business], str | None]:
        businesses = sorted(
            self.businesses.values(), key=lambda item: (item.created_at, item.id), reverse=True
        )
        return self._page(businesses, limit, cursor)

    def get_business_by_id(self, business_id: str) -> Business:
        business = self.businesses.get(business_id)
        if business is None:
            raise ApiError(404, "business_not_found", "The business could not be found.")
        return business

    def classify_business(
        self,
        business_id: str,
        classification: DataClassification,
        relationship: BusinessRelationship,
    ) -> Business:
        business = self.businesses.get(business_id)
        if business is None:
            raise ApiError(404, "business_not_found", "The business could not be found.")
        updated = business.model_copy(
            update={"data_classification": classification, "relationship": relationship}
        )
        self.businesses[business_id] = updated
        return updated

    def create_ledger_entry(self, entry: EvidenceLedgerEntry) -> EvidenceLedgerEntry:
        if entry.id in self.ledger_entries:
            raise ApiError(409, "ledger_entry_exists", "The ledger entry already exists.")
        self.ledger_entries[entry.id] = entry
        self.evidence_events[f"evt_evidence_ledger_{entry.id}"] = {
            "schema_version": 1,
            "event_type": EvidenceEventType.EVIDENCE_LEDGER_RECORDED.value,
            "business_id": entry.business_id or "platform",
            "actor_type": "USER",
            "actor_id": entry.created_by,
            "subject_type": "evidence_ledger",
            "subject_id": entry.id,
            "occurred_at": entry.created_at.isoformat(),
            "source": "api",
            "properties": {
                "kind": entry.kind.value,
                "amount_minor": entry.amount_minor,
                "currency": entry.currency,
                "reversal_of": entry.reversal_of,
            },
        }
        return entry

    def list_ledger_entries(self) -> list[EvidenceLedgerEntry]:
        return sorted(
            self.ledger_entries.values(), key=lambda entry: entry.created_at, reverse=True
        )

    def list_ledger_entries_page(
        self, limit: int, cursor: str | None
    ) -> tuple[list[EvidenceLedgerEntry], str | None]:
        return self._page(self.list_ledger_entries(), limit, cursor)

    def create_prospect(self, prospect: Prospect) -> Prospect:
        if prospect.id in self.prospects:
            existing = self.prospects[prospect.id]
            if (
                existing.company == prospect.company
                and existing.public_website == prospect.public_website
            ):
                return existing
            raise ApiError(409, "prospect_exists", "The prospect already exists.")
        self.prospects[prospect.id] = prospect
        return prospect

    def get_prospect(self, prospect_id: str) -> Prospect:
        prospect = self.prospects.get(prospect_id)
        if not prospect:
            raise ApiError(404, "prospect_not_found", "The prospect could not be found.")
        return prospect

    def update_prospect(self, prospect_id: str, changes: dict[str, Any]) -> Prospect:
        prospect = self.get_prospect(prospect_id)
        updated = prospect.model_copy(update={**changes, "updated_at": datetime.now(UTC)})
        self.prospects[prospect_id] = updated
        return updated

    def list_prospects(self, limit: int, cursor: str | None) -> tuple[list[Prospect], str | None]:
        prospects = sorted(
            self.prospects.values(), key=lambda item: (item.updated_at, item.id), reverse=True
        )
        return self._page(prospects, limit, cursor)

    def create_interview(self, interview: Interview) -> Interview:
        self.get_prospect(interview.prospect_id)
        if interview.id in self.interviews:
            return self.interviews[interview.id]
        self.interviews[interview.id] = interview
        self.update_prospect(
            interview.prospect_id,
            {
                "status": ProspectStatus.INTERVIEWED,
                "next_follow_up_on": interview.follow_up_on,
            },
        )
        return interview

    def list_interviews(
        self, prospect_id: str, limit: int, cursor: str | None
    ) -> tuple[list[Interview], str | None]:
        self.get_prospect(prospect_id)
        interviews = sorted(
            (item for item in self.interviews.values() if item.prospect_id == prospect_id),
            key=lambda item: (item.created_at, item.id),
            reverse=True,
        )
        return self._page(interviews, limit, cursor)

    def activate_founder_plan(
        self, enrollment: FounderPlanEnrollment, ledger: EvidenceLedgerEntry
    ) -> FounderPlanEnrollment:
        existing = self.founder_plans.get(enrollment.business_id)
        if existing:
            if existing.idempotency_key != enrollment.idempotency_key:
                raise ApiError(409, "founder_plan_exists", "A founder plan already exists.")
            return existing
        used = min(
            enrollment.invoice_limit,
            sum(
                1
                for invoice in self.invoices.values()
                if invoice.business_id == enrollment.business_id
            ),
        )
        enrollment = enrollment.model_copy(
            update={
                "invoices_used": used,
                "status": (
                    FounderPlanStatus.EXHAUSTED
                    if used == enrollment.invoice_limit
                    else FounderPlanStatus.ACTIVE
                ),
            }
        )
        self.founder_plans[enrollment.business_id] = enrollment
        self.ledger_entries[ledger.id] = ledger
        return enrollment

    def get_founder_plan(self, business_id: str) -> FounderPlanEnrollment | None:
        return self.founder_plans.get(business_id)

    def list_founder_plans(
        self, limit: int, cursor: str | None
    ) -> tuple[list[FounderPlanEnrollment], str | None]:
        plans = sorted(
            self.founder_plans.values(),
            key=lambda item: (item.activated_at, item.id),
            reverse=True,
        )
        return self._page(plans, limit, cursor)

    def delete_account(self, tenant: TenantContext, retain_anonymous_metrics: bool) -> None:
        invoice_ids = {
            invoice.id
            for invoice in self.invoices.values()
            if invoice.business_id == tenant.business_id
        }
        if retain_anonymous_metrics:
            self.evidence_events["aggregate_deleted_accounts"] = {
                "businesses": int(
                    self.evidence_events.get("aggregate_deleted_accounts", {}).get("businesses", 0)
                )
                + 1
            }
        for entry_id, entry in list(self.ledger_entries.items()):
            if entry.business_id == tenant.business_id:
                self.ledger_entries[entry_id] = entry.model_copy(
                    update={
                        "business_id": None,
                        "created_by": "deleted_account",
                        "reference": (
                            f"sha256:{hashlib.sha256(entry.reference.encode()).hexdigest()}"
                        ),
                    }
                )
        for prospect_id, prospect in list(self.prospects.items()):
            if prospect.linked_business_id == tenant.business_id:
                self.prospects[prospect_id] = prospect.model_copy(
                    update={"linked_business_id": None, "updated_at": datetime.now(UTC)}
                )
        for action_id, action in list(self.actions.items()):
            if action.invoice_id in invoice_ids:
                self.actions.pop(action_id, None)
        for attempt_id, attempt in list(self.action_attempts.items()):
            if attempt.action_id not in self.actions:
                self.action_attempts.pop(attempt_id, None)
        for extraction_id, extraction in list(self.extractions.items()):
            if extraction.get("business_id") == tenant.business_id:
                self.extractions.pop(extraction_id, None)
        for state_id, state in list(self.oauth_states.items()):
            if state.business_id == tenant.business_id:
                self.oauth_states.pop(state_id, None)
        for mapping in (
            self.invoices,
            self.agent_runs,
            self.payments,
            self.gmail_connections,
            self.policy_settings,
            self.founder_plans,
            self.businesses,
        ):
            for key, value in list(mapping.items()):
                business_id = getattr(value, "business_id", key)
                if business_id == tenant.business_id:
                    mapping.pop(key, None)
        for consent_id, consent in list(self.consents.items()):
            if consent.business_id == tenant.business_id:
                self.consents.pop(consent_id, None)
        for event_id, event in list(self.optional_consents.items()):
            if event.business_id == tenant.business_id:
                self.optional_consents.pop(event_id, None)
        for evidence_id, evidence in list(self.evidence_events.items()):
            if evidence.get("business_id") == tenant.business_id:
                self.evidence_events.pop(evidence_id, None)
        self.memberships.pop(tenant.user_id, None)

    def _page(
        self, items: list[Any], limit: int, cursor: str | None
    ) -> tuple[list[Any], str | None]:
        start = 0
        if cursor:
            cursor_id = _cursor_decode(cursor)
            try:
                start = next(index for index, item in enumerate(items) if item.id == cursor_id) + 1
            except StopIteration:
                raise ApiError(422, "invalid_cursor", "The pagination cursor is invalid.") from None
        selected = items[start : start + limit]
        has_more = start + limit < len(items)
        return selected, _cursor_encode(selected[-1].id) if has_more and selected else None
