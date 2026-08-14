from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class MembershipRole(StrEnum):
    OWNER = "OWNER"


class MembershipStatus(StrEnum):
    ACTIVE = "ACTIVE"


class EvidenceEventType(StrEnum):
    BUSINESS_CREATED = "business.created"
    EXTRACTION_COMPLETED = "invoice.extraction_completed"
    INVOICE_CONFIRMED = "invoice.confirmed"
    AGENT_DECISION_CREATED = "agent.decision_created"
    POLICY_CHECKED = "policy.checked"
    ACTION_PROPOSED = "action.proposed"
    ACTION_APPROVED = "action.approved"
    ACTION_EXECUTED = "action.executed"
    PAYMENT_RECORDED = "payment.recorded"
    INVOICE_CLOSED = "invoice.closed"


@dataclass(frozen=True, slots=True)
class AuthenticatedUser:
    uid: str
    email: str | None
    display_name: str | None


@dataclass(frozen=True, slots=True)
class TenantContext:
    user_id: str
    business_id: str
    role: MembershipRole


class PolicyDefaults(BaseModel):
    model_config = ConfigDict(frozen=True)

    reminder_cooldown_hours: int = Field(default=72, ge=1)
    high_value_threshold_minor: int = Field(default=5_000_000, ge=0)
    high_value_currency: str = Field(default="INR", pattern=r"^[A-Z]{3}$")
    dispute_requires_human: bool = True
    legal_language_allowed: bool = False
    payment_confirmation_required: bool = True


class BusinessCreate(BaseModel):
    name: str = Field(min_length=2, max_length=100)


class Business(BaseModel):
    id: str
    name: str
    owner_user_id: str
    created_at: datetime


class Membership(BaseModel):
    business_id: str
    user_id: str
    role: MembershipRole
    status: MembershipStatus
    created_at: datetime


class MeResponse(BaseModel):
    uid: str
    email: str | None
    display_name: str | None
    business: Business | None
    membership: Membership | None


class ErrorDetail(BaseModel):
    code: str
    message: str
    request_id: str
    details: dict[str, Any] | list[Any] | None = None


class ErrorEnvelope(BaseModel):
    error: ErrorDetail


class HealthResponse(BaseModel):
    status: str
    service: str = "cashsathi-api"
    environment: str
