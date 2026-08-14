from __future__ import annotations

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
    AuthenticatedUser,
    Business,
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


def _business_id(uid: str) -> str:
    digest = hashlib.sha256(uid.encode("utf-8")).hexdigest()[:20]
    return f"biz_{digest}"


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
            now = google_firestore.SERVER_TIMESTAMP
            txn.set(
                business_ref,
                {
                    "name": name.strip(),
                    "owner_user_id": user.uid,
                    "created_at": now,
                    "updated_at": now,
                },
            )
            txn.set(
                user_ref,
                {
                    "email": user.email,
                    "display_name": user.display_name,
                    "business_id": business_id,
                    "created_at": now,
                    "updated_at": now,
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
            txn.set(
                settings_ref,
                {**PolicyDefaults().model_dump(), "created_at": now, "updated_at": now},
            )
            txn.set(
                event_ref,
                {
                    "schema_version": 1,
                    "event_type": "business.created",
                    "business_id": business_id,
                    "actor_type": "USER",
                    "actor_id": user.uid,
                    "subject_type": "business",
                    "subject_id": business_id,
                    "occurred_at": now,
                    "source": "api",
                    "properties": {},
                },
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
        return business, membership

    def require_tenant(self, user: AuthenticatedUser) -> TenantContext:
        business, membership = self.get_account(user)
        if not business or not membership:
            raise ApiError(404, "business_not_found", "Complete business onboarding first.")
        return TenantContext(user_id=user.uid, business_id=business.id, role=membership.role)
