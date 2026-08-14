from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator


class MembershipRole(StrEnum):
    OWNER = "OWNER"


class MembershipStatus(StrEnum):
    ACTIVE = "ACTIVE"


class EvidenceEventType(StrEnum):
    BUSINESS_CREATED = "business.created"
    CONSENT_GRANTED = "consent.granted"
    EXTRACTION_COMPLETED = "invoice.extraction_completed"
    INVOICE_CONFIRMED = "invoice.confirmed"
    AGENT_DECISION_CREATED = "agent.decision_created"
    POLICY_CHECKED = "policy.checked"
    ACTION_PROPOSED = "action.proposed"
    ACTION_APPROVED = "action.approved"
    ACTION_EXECUTED = "action.executed"
    PAYMENT_RECORDED = "payment.recorded"
    INVOICE_CLOSED = "invoice.closed"


class InvoiceState(StrEnum):
    UPCOMING = "UPCOMING"
    DUE = "DUE"
    OVERDUE = "OVERDUE"
    WAITING_FOR_REPLY = "WAITING_FOR_REPLY"
    DISPUTED = "DISPUTED"
    HUMAN_REVIEW = "HUMAN_REVIEW"
    PAID = "PAID"


class AgentDecision(StrEnum):
    WAIT = "WAIT"
    SEND_REMINDER = "SEND_REMINDER"
    SCHEDULE_RECHECK = "SCHEDULE_RECHECK"
    REQUEST_HUMAN_REVIEW = "REQUEST_HUMAN_REVIEW"
    CLOSE_AS_PAID = "CLOSE_AS_PAID"


class PolicyOutcome(StrEnum):
    ALLOW = "ALLOW"
    REQUIRE_APPROVAL = "REQUIRE_APPROVAL"
    BLOCK = "BLOCK"


class ActionState(StrEnum):
    PROPOSED = "PROPOSED"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    EXECUTING = "EXECUTING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"
    CANCELLED = "CANCELLED"


class Confidence(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


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


class ConsentAccept(BaseModel):
    version: str = Field(min_length=1, max_length=50)
    accepted: Literal[True]


class ConsentRecord(BaseModel):
    consent_type: Literal["product_processing"] = "product_processing"
    version: str
    granted_at: datetime
    user_id: str
    business_id: str
    source: Literal["web.invoice_upload"] = "web.invoice_upload"
    statement_sha256: str


class ConsentStatus(BaseModel):
    consent_type: Literal["product_processing"] = "product_processing"
    version: str
    statement: str
    granted: bool
    granted_at: datetime | None = None


class ExtractionWarning(BaseModel):
    code: str = Field(min_length=1, max_length=80)
    field: str | None = Field(default=None, max_length=80)
    message: str = Field(min_length=1, max_length=240)


class ExtractedInvoiceDraft(BaseModel):
    invoice_number: str | None = Field(default=None, max_length=100)
    customer_name: str | None = Field(default=None, max_length=160)
    customer_email: EmailStr | None = None
    amount_decimal: str | None = Field(default=None, max_length=40)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    issue_date: date | None = None
    due_date: date | None = None
    payment_terms: str | None = Field(default=None, max_length=500)
    confidence: dict[str, Confidence] = Field(default_factory=dict)
    warnings: list[ExtractionWarning] = Field(default_factory=list, max_length=30)

    @field_validator("invoice_number", "customer_name", "payment_terms", mode="before")
    @classmethod
    def strip_optional_text(cls, value: object) -> object:
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        return value

    @field_validator("currency", mode="before")
    @classmethod
    def uppercase_currency(cls, value: object) -> object:
        return value.strip().upper() if isinstance(value, str) else value


class ExtractionResult(BaseModel):
    extraction_id: str
    draft: ExtractedInvoiceDraft
    model_id: str
    prompt_version: str
    latency_ms: int = Field(ge=0)


class InvoiceConfirm(BaseModel):
    extraction_id: str = Field(min_length=8, max_length=100)
    invoice_number: str = Field(min_length=1, max_length=100)
    customer_name: str = Field(min_length=1, max_length=160)
    customer_email: EmailStr | None = None
    customer_manual_only: bool = False
    amount_decimal: str = Field(min_length=1, max_length=40)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    issue_date: date | None = None
    due_date: date | None = None
    payment_terms: str | None = Field(default=None, max_length=500)
    confirmed: Literal[True]

    @field_validator("invoice_number", "customer_name", "amount_decimal")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("customer_email", mode="before")
    @classmethod
    def empty_email_is_none(cls, value: object) -> object:
        return None if value == "" else value

    @field_validator("currency", mode="before")
    @classmethod
    def normalize_currency(cls, value: object) -> object:
        return value.strip().upper() if isinstance(value, str) else value

    @field_validator("payment_terms", mode="before")
    @classmethod
    def empty_terms_are_none(cls, value: object) -> object:
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        return value

    @model_validator(mode="after")
    def dates_are_consistent(self) -> "InvoiceConfirm":
        if self.issue_date and self.due_date and self.due_date < self.issue_date:
            raise ValueError("Due date cannot be earlier than issue date")
        return self


class CustomerSnapshot(BaseModel):
    id: str
    name: str
    email: str | None
    manual_only: bool


class Invoice(BaseModel):
    id: str
    business_id: str
    extraction_id: str
    invoice_number: str
    customer: CustomerSnapshot
    amount_minor: int = Field(gt=0)
    currency: str
    issue_date: date | None
    due_date: date | None
    payment_terms: str | None
    review_required: bool
    review_reason: str | None
    dispute_active: bool = False
    verified_paid_minor: int = 0
    confirmation_hash: str
    created_at: datetime
    updated_at: datetime


class InvoiceSummary(BaseModel):
    id: str
    invoice_number: str
    customer_name: str
    amount_minor: int
    currency: str
    due_date: date | None
    current_state: InvoiceState
    created_at: datetime


class InvoicePage(BaseModel):
    items: list[InvoiceSummary]
    next_cursor: str | None


class ModelDecision(BaseModel):
    decision: AgentDecision
    rationale: str = Field(min_length=1, max_length=500)
    risk_flags: list[str] = Field(default_factory=list, max_length=20)
    requires_human_approval: bool = False
    next_check_at: datetime | None = None

    @field_validator("next_check_at")
    @classmethod
    def next_check_must_be_utc_aware(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("next_check_at must include a timezone")
        return value.astimezone(UTC)


class PolicyResult(BaseModel):
    outcome: PolicyOutcome
    final_decision: AgentDecision
    matched_rules: list[str]
    requires_approval: bool
    next_check_at: datetime | None
    policy_version: str


class Action(BaseModel):
    id: str
    invoice_id: str
    agent_run_id: str
    action_type: Literal["SEND_REMINDER"] = "SEND_REMINDER"
    state: ActionState
    created_at: datetime


class AgentRunStatus(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    HUMAN_REVIEW = "HUMAN_REVIEW"


class AgentRun(BaseModel):
    id: str
    invoice_id: str
    business_id: str
    status: AgentRunStatus
    invoice_state: InvoiceState
    model_proposal: ModelDecision | None
    policy_result: PolicyResult | None
    model_id: str
    prompt_version: str
    attempt_count: int = Field(ge=0, le=2)
    latency_ms: int = Field(ge=0)
    input_tokens: int | None = None
    output_tokens: int | None = None
    failure_code: str | None = None
    created_at: datetime


class AgentRunPage(BaseModel):
    items: list[AgentRun]
    next_cursor: str | None


class EvaluationResult(BaseModel):
    agent_run: AgentRun
    action: Action | None


class InvoiceDetail(BaseModel):
    invoice: Invoice
    current_state: InvoiceState
    latest_agent_run: AgentRun | None
    latest_action: Action | None


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
