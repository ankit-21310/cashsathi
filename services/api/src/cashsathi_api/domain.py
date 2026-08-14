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
    ACTION_CANCELLED = "action.cancelled"
    ACTION_RESOLVED = "action.resolved"
    GMAIL_CONNECTED = "gmail.connected"
    GMAIL_DISCONNECTED = "gmail.disconnected"
    AUTOMATION_CHANGED = "automation.changed"
    EVIDENCE_LEDGER_RECORDED = "evidence.ledger_recorded"
    OPTIONAL_CONSENT_GRANTED = "privacy.optional_consent_granted"
    OPTIONAL_CONSENT_WITHDRAWN = "privacy.optional_consent_withdrawn"
    PLAN_ACTIVATED = "plan.activated"
    PROSPECT_UPDATED = "validation.prospect_updated"
    INTERVIEW_RECORDED = "validation.interview_recorded"
    ACCOUNT_DELETED = "privacy.account_deleted"


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


class DataClassification(StrEnum):
    UNCLASSIFIED = "UNCLASSIFIED"
    DEMO = "DEMO"
    REAL = "REAL"


class BusinessRelationship(StrEnum):
    UNCLASSIFIED = "UNCLASSIFIED"
    ARMS_LENGTH = "ARMS_LENGTH"
    RELATED = "RELATED"
    PREEXISTING = "PREEXISTING"


class InvoiceWorkflowStatus(StrEnum):
    OPEN = "OPEN"
    PAUSED = "PAUSED"
    CLOSED = "CLOSED"


class ReminderTone(StrEnum):
    WARM = "WARM"
    NEUTRAL = "NEUTRAL"


class ReminderIntent(StrEnum):
    DUE_SOON = "DUE_SOON"
    DUE_TODAY = "DUE_TODAY"
    OVERDUE_FOLLOWUP = "OVERDUE_FOLLOWUP"


class ActionResolution(StrEnum):
    CONFIRMED_DELIVERED = "CONFIRMED_DELIVERED"
    CONFIRMED_NOT_DELIVERED = "CONFIRMED_NOT_DELIVERED"
    MANUALLY_SENT = "MANUALLY_SENT"


class LedgerKind(StrEnum):
    PRODUCT_REVENUE = "PRODUCT_REVENUE"
    EXPENSE = "EXPENSE"


class OptionalConsentType(StrEnum):
    ANONYMIZED_METRICS = "ANONYMIZED_METRICS"
    TESTIMONIAL = "TESTIMONIAL"
    IDENTITY_DISCLOSURE = "IDENTITY_DISCLOSURE"


class ConsentEventAction(StrEnum):
    GRANTED = "GRANTED"
    WITHDRAWN = "WITHDRAWN"


class ProspectStatus(StrEnum):
    NOT_CONTACTED = "NOT_CONTACTED"
    CONTACTED = "CONTACTED"
    INTERVIEW_SCHEDULED = "INTERVIEW_SCHEDULED"
    INTERVIEWED = "INTERVIEWED"
    DESIGN_PARTNER = "DESIGN_PARTNER"
    CONVERTED = "CONVERTED"
    DECLINED = "DECLINED"
    DO_NOT_CONTACT = "DO_NOT_CONTACT"


class WillingnessToPay(StrEnum):
    NONE = "NONE"
    MAYBE = "MAYBE"
    YES = "YES"


class FounderPlanStatus(StrEnum):
    ACTIVE = "ACTIVE"
    EXHAUSTED = "EXHAUSTED"


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
    automation_enabled: bool = False


class BusinessCreate(BaseModel):
    name: str = Field(min_length=2, max_length=100)


class Business(BaseModel):
    id: str
    name: str
    owner_user_id: str
    created_at: datetime
    data_classification: DataClassification = DataClassification.UNCLASSIFIED
    relationship: BusinessRelationship = BusinessRelationship.UNCLASSIFIED
    evidence_pseudonym: str = ""


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
    is_platform_admin: bool = False


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


class OptionalConsentGrant(BaseModel):
    version: str = Field(min_length=1, max_length=50)
    accepted: Literal[True]
    approved_text: str | None = Field(default=None, max_length=1000)
    channels: list[str] = Field(default_factory=list, max_length=10)

    @field_validator("channels")
    @classmethod
    def normalize_channels(cls, value: list[str]) -> list[str]:
        normalized = [channel.strip().upper() for channel in value if channel.strip()]
        if len(normalized) != len(set(normalized)):
            raise ValueError("Consent channels must be unique")
        return normalized


class OptionalConsentWithdraw(BaseModel):
    confirmed: Literal[True]


class OptionalConsentEvent(BaseModel):
    id: str
    consent_type: OptionalConsentType
    action: ConsentEventAction
    version: str
    business_id: str
    user_id: str
    statement_sha256: str
    approved_text: str | None = None
    channels: list[str] = Field(default_factory=list)
    withdraws_grant_id: str | None = None
    occurred_at: datetime


class OptionalConsentDefinition(BaseModel):
    consent_type: OptionalConsentType
    version: str
    statement: str
    active_grant: OptionalConsentEvent | None
    history: list[OptionalConsentEvent]


class OptionalConsentResponse(BaseModel):
    items: list[OptionalConsentDefinition]


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
    next_check_at: datetime | None = None
    workflow_status: InvoiceWorkflowStatus = InvoiceWorkflowStatus.OPEN
    evaluation_lease_until: datetime | None = None
    active_action_id: str | None = None
    reminder_sequence: int = Field(default=0, ge=0)


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
    reminder_tone: ReminderTone | None = None
    reminder_intent: ReminderIntent | None = None

    @field_validator("next_check_at")
    @classmethod
    def next_check_must_be_utc_aware(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("next_check_at must include a timezone")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def reminder_brief_matches_decision(self) -> "ModelDecision":
        if self.decision == AgentDecision.SEND_REMINDER:
            if self.reminder_tone is None:
                self.reminder_tone = ReminderTone.WARM
            if self.reminder_intent is None:
                self.reminder_intent = ReminderIntent.OVERDUE_FOLLOWUP
        elif self.reminder_tone is not None or self.reminder_intent is not None:
            raise ValueError("reminder fields are only valid for SEND_REMINDER")
        return self


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
    action_key: str = ""
    recipient_email: str | None = None
    subject: str | None = None
    body: str | None = None
    automatic: bool = False
    approved_by: str | None = None
    approved_at: datetime | None = None
    cancelled_by: str | None = None
    cancelled_at: datetime | None = None
    cancel_reason: str | None = None
    execution_started_at: datetime | None = None
    execution_completed_at: datetime | None = None
    provider_message_id: str | None = None
    failure_code: str | None = None
    failure_message: str | None = None
    delivery_possible: bool | None = None
    resolution: ActionResolution | None = None
    resolved_by: str | None = None
    resolved_at: datetime | None = None
    attempt_count: int = Field(default=0, ge=0)
    reminder_sequence: int = Field(default=0, ge=0)


class ActionPage(BaseModel):
    items: list[Action]
    next_cursor: str | None


class ActionCancel(BaseModel):
    reason: str = Field(min_length=2, max_length=240)


class ActionResolve(BaseModel):
    resolution: ActionResolution
    confirmed: Literal[True]


class ActionAttempt(BaseModel):
    id: str
    action_id: str
    attempt_number: int = Field(ge=1)
    state: ActionState
    automatic: bool
    provider_message_id: str | None = None
    failure_code: str | None = None
    delivery_possible: bool | None = None
    started_at: datetime
    completed_at: datetime | None = None


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
    action_id: str | None = None


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


class PaymentCreate(BaseModel):
    amount_decimal: str = Field(min_length=1, max_length=40)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    paid_at: datetime
    reference: str = Field(min_length=1, max_length=160)
    idempotency_key: str = Field(min_length=8, max_length=100)
    confirmed: Literal[True]

    @field_validator("currency", mode="before")
    @classmethod
    def normalize_payment_currency(cls, value: object) -> object:
        return value.strip().upper() if isinstance(value, str) else value

    @field_validator("paid_at")
    @classmethod
    def paid_at_must_be_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("paid_at must include a timezone")
        return value.astimezone(UTC)


class Payment(BaseModel):
    id: str
    invoice_id: str
    business_id: str
    amount_minor: int = Field(gt=0)
    currency: str
    paid_at: datetime
    reference: str
    idempotency_key: str
    confirmed_by: str
    created_at: datetime


class TimelineEntry(BaseModel):
    id: str
    entry_type: str
    title: str
    detail: str
    occurred_at: datetime
    status: str | None = None


class InvoiceTimeline(BaseModel):
    items: list[TimelineEntry]


class GmailStatus(BaseModel):
    connected: bool
    connected_at: datetime | None = None
    last_error_code: str | None = None
    automation_enabled: bool = False


class GmailConnectResponse(BaseModel):
    authorization_url: str


class GmailOAuthState(BaseModel):
    state: str
    business_id: str
    user_id: str
    code_verifier: str
    expires_at: datetime
    created_at: datetime


class GmailConnection(BaseModel):
    business_id: str
    encrypted_refresh_token: str
    kms_key_name: str
    connected_at: datetime
    updated_at: datetime
    disconnected_at: datetime | None = None
    last_error_code: str | None = None


class AutomationUpdate(BaseModel):
    enabled: bool
    confirmed: Literal[True]


class CurrencyMetrics(BaseModel):
    currency: str
    monitored_minor: int
    outstanding_minor: int
    overdue_minor: int
    verified_paid_minor: int
    post_action_paid_minor: int


class MetricsResponse(BaseModel):
    currencies: list[CurrencyMetrics]
    invoice_count: int
    overdue_count: int
    human_review_count: int
    ai_decisions: int
    successful_actions: int
    automatic_successful_actions: int
    automation_rate: float
    average_days_overdue: float
    pending_approvals: int


class ProspectCreate(BaseModel):
    company: str = Field(min_length=2, max_length=160)
    city: str | None = Field(default=None, max_length=100)
    segment: str = Field(min_length=2, max_length=100)
    public_website: str | None = Field(default=None, max_length=300)
    public_contact_channel: str = Field(min_length=2, max_length=160)
    status: ProspectStatus = ProspectStatus.NOT_CONTACTED
    notes: str | None = Field(default=None, max_length=1000)
    next_follow_up_on: date | None = None
    linked_business_id: str | None = Field(default=None, max_length=100)


class ProspectUpdate(BaseModel):
    status: ProspectStatus | None = None
    notes: str | None = Field(default=None, max_length=1000)
    next_follow_up_on: date | None = None
    linked_business_id: str | None = Field(default=None, max_length=100)


class Prospect(BaseModel):
    id: str
    company: str
    city: str | None
    segment: str
    public_website: str | None
    public_contact_channel: str
    status: ProspectStatus
    notes: str | None
    next_follow_up_on: date | None
    linked_business_id: str | None
    created_by: str
    created_at: datetime
    updated_at: datetime


class ProspectPage(BaseModel):
    items: list[Prospect]
    next_cursor: str | None


class InterviewCreate(BaseModel):
    occurred_on: date
    current_workflow: str = Field(min_length=2, max_length=1000)
    top_pain: str = Field(min_length=2, max_length=1000)
    trust_boundary: str = Field(min_length=2, max_length=1000)
    weekly_receivables_minutes: int | None = Field(default=None, ge=0, le=10080)
    active_invoice_range: str | None = Field(default=None, max_length=80)
    automation_comfort: bool | None = None
    required_approval_cases: list[str] = Field(default_factory=list, max_length=20)
    willingness_to_pay: WillingnessToPay
    feedback: str = Field(min_length=2, max_length=2000)
    follow_up_on: date | None = None


class Interview(BaseModel):
    id: str
    prospect_id: str
    occurred_on: date
    current_workflow: str
    top_pain: str
    trust_boundary: str
    weekly_receivables_minutes: int | None
    active_invoice_range: str | None
    automation_comfort: bool | None
    required_approval_cases: list[str]
    willingness_to_pay: WillingnessToPay
    feedback: str
    follow_up_on: date | None
    recorded_by: str
    created_at: datetime


class InterviewPage(BaseModel):
    items: list[Interview]
    next_cursor: str | None


class FounderPlanActivate(BaseModel):
    business_id: str = Field(min_length=5, max_length=100)
    paid_on: date
    receipt_reference: str = Field(min_length=2, max_length=160)
    idempotency_key: str = Field(min_length=8, max_length=100)
    confirmed: Literal[True]


class FounderPlanEnrollment(BaseModel):
    id: str
    business_id: str
    plan_version: Literal["FOUNDER_RECOVERY_2026_V1"] = "FOUNDER_RECOVERY_2026_V1"
    status: FounderPlanStatus
    price_minor: Literal[29900] = 29900
    currency: Literal["INR"] = "INR"
    invoice_limit: Literal[10] = 10
    invoices_used: int = Field(ge=0, le=10)
    ledger_entry_id: str
    receipt_reference: str
    idempotency_key: str
    activated_by: str
    activated_at: datetime


class FounderPlanPage(BaseModel):
    items: list[FounderPlanEnrollment]
    next_cursor: str | None


class AccountExport(BaseModel):
    schema_version: Literal[1] = 1
    generated_at: datetime
    business: Business
    settings: PolicyDefaults
    product_processing_consent: ConsentRecord | None
    optional_consents: list[OptionalConsentEvent]
    invoices: list[Invoice]
    agent_runs: list[AgentRun]
    actions: list[Action]
    payments: list[Payment]
    founder_plan: FounderPlanEnrollment | None
    gmail_connected: bool


class AccountDelete(BaseModel):
    business_name: str = Field(min_length=2, max_length=100)
    confirmed: Literal[True]


class AccountDeleteResult(BaseModel):
    deleted: Literal[True] = True
    gmail_revocation_succeeded: bool
    google_revocation_instructions_required: bool


class BusinessPage(BaseModel):
    items: list[Business]
    next_cursor: str | None


class BusinessClassificationUpdate(BaseModel):
    data_classification: DataClassification
    relationship: BusinessRelationship


class EvidenceLedgerCreate(BaseModel):
    kind: LedgerKind
    amount_decimal: str = Field(min_length=1, max_length=40)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    occurred_on: date
    category: str = Field(min_length=1, max_length=100)
    reference: str = Field(min_length=1, max_length=160)
    business_id: str | None = Field(default=None, max_length=100)
    marketing: bool = False
    reversal_of: str | None = Field(default=None, max_length=100)

    @field_validator("currency", mode="before")
    @classmethod
    def normalize_ledger_currency(cls, value: object) -> object:
        return value.strip().upper() if isinstance(value, str) else value


class EvidenceLedgerEntry(BaseModel):
    id: str
    kind: LedgerKind
    amount_minor: int
    currency: str
    occurred_on: date
    category: str
    reference: str
    business_id: str | None
    marketing: bool
    reversal_of: str | None
    created_by: str
    created_at: datetime


class EvidenceLedgerPage(BaseModel):
    items: list[EvidenceLedgerEntry]
    next_cursor: str | None


class AdminImpactResponse(BaseModel):
    paying_businesses: int
    related_paying_businesses: int
    preexisting_paying_businesses: int
    real_businesses: int
    demo_businesses: int
    unclassified_businesses: int
    revenue_by_currency: dict[str, int]
    related_revenue_by_currency: dict[str, int]
    preexisting_revenue_by_currency: dict[str, int]
    expenses_by_currency: dict[str, int]
    marketing_spend_by_currency: dict[str, int]
    cac_by_currency: dict[str, int]
    prospects: int = 0
    interviews: int = 0
    design_partners: int = 0
    converted_prospects: int = 0
    active_founder_plans: int = 0
    exhausted_founder_plans: int = 0
    consented_testimonials: int = 0
    operational: MetricsResponse


class RecheckResult(BaseModel):
    claimed: int
    evaluated: int
    succeeded: int
    failed: int


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
