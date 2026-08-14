from __future__ import annotations

import base64
import hashlib
import os
from datetime import UTC, datetime
from typing import Any, Protocol, cast

from firebase_admin import firestore
from google.auth.credentials import AnonymousCredentials
from google.cloud import firestore as google_firestore

from cashsathi_api.auth import initialize_firebase
from cashsathi_api.config import Settings
from cashsathi_api.domain import (
    Action,
    AgentRun,
    AuthenticatedUser,
    Business,
    ConsentRecord,
    EvidenceEventType,
    Invoice,
    Membership,
    MembershipRole,
    MembershipStatus,
    PolicyDefaults,
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
    ) -> None: ...

    def list_agent_runs(
        self, tenant: TenantContext, invoice_id: str | None, limit: int, cursor: str | None
    ) -> tuple[list[AgentRun], str | None]: ...


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
        transaction = self._client.transaction()

        @google_firestore.transactional
        def create_in_transaction(txn: Any) -> None:
            snapshot = user_ref.get(transaction=txn)
            if snapshot.exists and (snapshot.to_dict() or {}).get("business_id"):
                return
            now = datetime.now(UTC)
            txn.set(
                business_ref,
                {"name": name.strip(), "owner_user_id": user.uid, "created_at": now},
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

    def save_evaluation(self, tenant: TenantContext, run: AgentRun, action: Action | None) -> None:
        business_ref = self._business_ref(tenant)
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
        if action:
            batch.create(
                business_ref.collection("actions").document(action.id),
                action.model_dump(mode="json"),
            )
            batch.create(
                self._client.collection("evidence_events").document(f"evt_action_{action.id}"),
                _event(
                    event_type=EvidenceEventType.ACTION_PROPOSED,
                    tenant=tenant,
                    subject_type="action",
                    subject_id=action.id,
                    actor_type="AGENT",
                    properties={
                        "invoice_id": action.invoice_id,
                        "agent_run_id": action.agent_run_id,
                        "state": action.state.value,
                    },
                ),
            )
        batch.commit()

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

    def _business_ref(self, tenant: TenantContext) -> Any:
        return self._client.collection("businesses").document(tenant.business_id)

    @staticmethod
    def _parse_business(business_id: str, data: dict[str, Any]) -> Business:
        return Business(
            id=business_id,
            name=str(data["name"]),
            owner_user_id=str(data["owner_user_id"]),
            created_at=data["created_at"],
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
        self.extractions: dict[str, dict[str, Any]] = {}
        self.invoices: dict[str, Invoice] = {}
        self.actions: dict[str, Action] = {}
        self.agent_runs: dict[str, AgentRun] = {}
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
            id=business_id, name=name.strip(), owner_user_id=user.uid, created_at=now
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

    def save_evaluation(self, tenant: TenantContext, run: AgentRun, action: Action | None) -> None:
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
            self.actions[action.id] = action
            self.evidence_events[f"evt_action_{action.id}"] = _event(
                event_type=EvidenceEventType.ACTION_PROPOSED,
                tenant=tenant,
                subject_type="action",
                subject_id=action.id,
                properties={"invoice_id": action.invoice_id, "state": action.state.value},
                actor_type="AGENT",
            )

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
