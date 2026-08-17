from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import json
import secrets
from collections import defaultdict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from typing import Annotated
from uuid import uuid4

import structlog
from fastapi import Depends, FastAPI, File, Header, Query, Request, Response, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

from cashsathi_api.auth import (
    AccountAuthManager,
    AuthVerifier,
    FirebaseAccountAuthManager,
    FirebaseAuthVerifier,
    authenticated_user,
)
from cashsathi_api.billing import (
    PaymentGateway,
    ProviderPayment,
    RazorpayGateway,
    provider_payment_from_payload,
)
from cashsathi_api.config import Settings, get_settings
from cashsathi_api.decisioning import (
    DecisionAdapter,
    DecisionUnavailableError,
    GeminiDecisionAdapter,
)
from cashsathi_api.domain import (
    AccountDelete,
    AccountDeleteResult,
    AccountingIntegrationPage,
    AccountingIntegrationStatus,
    AccountingProvider,
    Action,
    ActionCancel,
    ActionPage,
    ActionResolve,
    ActionState,
    AdminImpactResponse,
    AgentRunPage,
    AuthenticatedUser,
    AutomationUpdate,
    BillingAccessMode,
    BillingCheckoutOrder,
    BillingConfirm,
    BillingConfirmation,
    BillingCurrent,
    BillingOrder,
    BillingOrderCreate,
    BillingOrderStatus,
    BillingProvider,
    BillingTransaction,
    BillingTransactionStatus,
    Business,
    BusinessClassificationUpdate,
    BusinessCreate,
    BusinessPage,
    BusinessRelationship,
    CashForecast,
    ConsentAccept,
    ConsentEventAction,
    ConsentStatus,
    CustomerPolicyUpdate,
    CustomerSnapshot,
    DataClassification,
    DisputeCreate,
    DisputeResolve,
    ErrorEnvelope,
    EvaluationResult,
    EvidenceEventType,
    EvidenceLedgerCreate,
    EvidenceLedgerEntry,
    EvidenceLedgerPage,
    ExtractionResult,
    FounderPlanActivate,
    FounderPlanEnrollment,
    FounderPlanPage,
    FounderPlanStatus,
    GmailConnection,
    GmailConnectResponse,
    GmailOAuthState,
    GmailStatus,
    HealthResponse,
    Interview,
    InterviewCreate,
    InterviewPage,
    InvitationRevoke,
    Invoice,
    InvoiceConfirm,
    InvoiceDetail,
    InvoicePage,
    InvoiceSummary,
    InvoiceTimeline,
    InvoiceWorkflowStatus,
    Membership,
    MembershipPage,
    MembershipRevoke,
    MembershipRole,
    MembershipRoleUpdate,
    MeResponse,
    MetricsResponse,
    OptionalConsentEvent,
    OptionalConsentGrant,
    OptionalConsentResponse,
    OptionalConsentType,
    OptionalConsentWithdraw,
    Payment,
    PaymentCreate,
    PolicyDefaults,
    PolicyTemplateApply,
    PolicyTemplateKind,
    PolicyTemplatePage,
    Prospect,
    ProspectCreate,
    ProspectPage,
    ProspectStatus,
    ProspectUpdate,
    RecheckResult,
    RestrictivePolicyUpdate,
    RevenueCustomer,
    RevenueCustomerPage,
    RevenuePaymentDetail,
    RevenuePaymentPage,
    RevenueSummary,
    TeamInvitation,
    TeamInvitationCreate,
    TeamInvitationPage,
    TenantContext,
)
from cashsathi_api.emulator_adapters import (
    EmulatorDecisionAdapter,
    EmulatorGmailAdapter,
    EmulatorInvoiceExtractor,
    EmulatorTokenCipher,
)
from cashsathi_api.errors import (
    ApiError,
    api_error_handler,
    unhandled_error_handler,
    validation_error_handler,
)
from cashsathi_api.evidence import admin_impact, build_timeline, calculate_metrics
from cashsathi_api.gmail import (
    AesGcmTokenCipher,
    GmailAdapter,
    GmailDefiniteFailure,
    GmailUnavailableError,
    GoogleGmailAdapter,
    GoogleKmsTokenCipher,
    TokenCipher,
)
from cashsathi_api.invoice_processing import (
    ExtractionUnavailableError,
    GeminiInvoiceExtractor,
    InvoiceExtractor,
    validate_pdf,
)
from cashsathi_api.money import decimal_to_minor
from cashsathi_api.observability import RequestContextMiddleware, configure_logging
from cashsathi_api.phase9 import (
    accounting_integration_statuses,
    build_cash_forecast,
    build_finance_readiness_zip,
    restrictive_policy_templates,
)
from cashsathi_api.phase67 import (
    OPTIONAL_CONSENTS,
    active_optional_consents,
    build_account_export,
    build_evidence_zip,
    optional_consent_response,
    statement_hash,
)
from cashsathi_api.repository import FirestoreRepository, Repository, billing_order_id
from cashsathi_api.request_guards import RequestGuardsMiddleware
from cashsathi_api.scheduler_auth import (
    GoogleSchedulerVerifier,
    SchedulerVerifier,
    VercelCronVerifier,
)
from cashsathi_api.state_engine import calculate_invoice_state
from cashsathi_api.workflow import CollectionWorkflow, initial_next_check

PRODUCT_CONSENT_VERSION = "2026-08-14.v1"
PRODUCT_CONSENT_STATEMENT = (
    "I authorize the service to process invoices and related receivables information that I am "
    "permitted to provide, and to use my configured mailbox for actions I explicitly approve or "
    "allow under my policies. I understand that I remain responsible for invoice accuracy, "
    "customer relationships, disputes, and payment confirmation."
)
PRODUCT_CONSENT_HASH = hashlib.sha256(PRODUCT_CONSENT_STATEMENT.encode()).hexdigest()
FOUNDER_PLAN_AMOUNT_MINOR = 29_900
FOUNDER_PLAN_CURRENCY = "INR"


def _gateway_from(request: Request) -> PaymentGateway:
    gateway: PaymentGateway | None = request.app.state.payment_gateway
    if gateway is None:
        raise ApiError(503, "billing_not_configured", "Payment checkout is not configured.")
    return gateway


def _transaction_from_provider(
    order: BillingOrder, payment: ProviderPayment, now: datetime
) -> BillingTransaction:
    status_map = {
        "authorized": BillingTransactionStatus.AUTHORIZED,
        "captured": BillingTransactionStatus.CAPTURED,
        "failed": BillingTransactionStatus.FAILED,
    }
    status = status_map.get(payment.status.casefold(), BillingTransactionStatus.AUTHORIZED)
    if payment.amount_refunded >= payment.amount and payment.amount > 0:
        status = BillingTransactionStatus.REFUNDED
    elif payment.amount_refunded > 0:
        status = BillingTransactionStatus.PARTIALLY_REFUNDED
    provider_created = (
        datetime.fromtimestamp(payment.created_at, UTC) if payment.created_at > 0 else now
    )
    return BillingTransaction(
        id=payment.id,
        business_id=order.business_id,
        billing_order_id=order.id,
        provider=BillingProvider.RAZORPAY,
        provider_payment_id=payment.id,
        provider_order_id=payment.order_id,
        amount_minor=payment.amount,
        currency=payment.currency,
        amount_refunded_minor=payment.amount_refunded,
        provider_fee_minor=payment.fee,
        provider_tax_minor=payment.tax,
        payment_method=payment.method,
        payer_email=payment.email,
        status=status,
        failure_code=payment.error_code,
        failure_description=payment.error_description,
        captured_at=now if status == BillingTransactionStatus.CAPTURED else None,
        refunded_at=(
            now
            if status
            in {BillingTransactionStatus.PARTIALLY_REFUNDED, BillingTransactionStatus.REFUNDED}
            else None
        ),
        created_at=provider_created,
        updated_at=now,
    )


def _validate_provider_payment(order: BillingOrder, payment: ProviderPayment) -> None:
    if payment.order_id != order.provider_order_id:
        structlog.get_logger("billing").warning(
            "payment_order_mismatch", category="billing_verification_failure"
        )
        raise ApiError(422, "payment_order_mismatch", "The payment does not match this order.")
    if payment.amount != order.amount_minor or payment.currency != order.currency:
        structlog.get_logger("billing").warning(
            "payment_amount_mismatch", category="billing_verification_failure"
        )
        raise ApiError(422, "payment_amount_mismatch", "The payment amount or currency is invalid.")


def _record_provider_payment(
    repo: Repository, order: BillingOrder, payment: ProviderPayment
) -> tuple[BillingTransaction, FounderPlanEnrollment | None]:
    _validate_provider_payment(order, payment)
    now = datetime.now(UTC)
    transaction = _transaction_from_provider(order, payment, now)
    existing = repo.get_billing_transaction(payment.id)
    if existing:
        transaction = transaction.model_copy(
            update={
                "created_at": existing.created_at,
                "captured_at": existing.captured_at or transaction.captured_at,
                "payer_email": transaction.payer_email or existing.payer_email,
            }
        )
    enrollment: FounderPlanEnrollment | None = None
    ledger: EvidenceLedgerEntry | None = None
    if transaction.status == BillingTransactionStatus.CAPTURED:
        enrollment = FounderPlanEnrollment(
            id=f"plan_{order.business_id}",
            business_id=order.business_id,
            status=FounderPlanStatus.ACTIVE,
            invoices_used=0,
            ledger_entry_id=f"ledger_billing_{hashlib.sha256(payment.id.encode()).hexdigest()[:24]}",
            receipt_reference=payment.id,
            idempotency_key=order.idempotency_key,
            activated_by=order.created_by,
            activated_at=now,
            source=BillingProvider.RAZORPAY,
            provider_order_id=order.provider_order_id,
            provider_payment_id=payment.id,
        )
        ledger = EvidenceLedgerEntry(
            id=enrollment.ledger_entry_id,
            kind="PRODUCT_REVENUE",
            amount_minor=FOUNDER_PLAN_AMOUNT_MINOR,
            currency=FOUNDER_PLAN_CURRENCY,
            occurred_on=now.date(),
            category="Founder Recovery Plan",
            reference=payment.id,
            business_id=order.business_id,
            marketing=False,
            reversal_of=None,
            created_by=order.created_by,
            created_at=now,
        )
    if transaction.status in {
        BillingTransactionStatus.PARTIALLY_REFUNDED,
        BillingTransactionStatus.REFUNDED,
    }:
        previous_refund = existing.amount_refunded_minor if existing else 0
        refund_delta = max(transaction.amount_refunded_minor - previous_refund, 0)
        plan = repo.get_founder_plan(order.business_id)
        if transaction.status == BillingTransactionStatus.REFUNDED and plan:
            enrollment = plan.model_copy(
                update={"status": FounderPlanStatus.REFUNDED, "refunded_at": now}
            )
        if refund_delta:
            ledger = EvidenceLedgerEntry(
                id=(
                    "ledger_refund_"
                    + hashlib.sha256(
                        f"{payment.id}:{transaction.amount_refunded_minor}".encode()
                    ).hexdigest()[:24]
                ),
                kind="PRODUCT_REFUND",
                amount_minor=refund_delta,
                currency=transaction.currency,
                occurred_on=now.date(),
                category="Founder Recovery Plan refund",
                reference=payment.id,
                business_id=order.business_id,
                marketing=False,
                reversal_of=None,
                created_by="razorpay_webhook",
                created_at=now,
            )
        result = repo.record_billing_refund(transaction, enrollment, ledger)
        structlog.get_logger("billing").info(
            "billing_refund_recorded",
            category="billing_refund",
            payment_id=transaction.provider_payment_id,
            status=transaction.status.value,
        )
        return result
    result = repo.record_billing_transaction(order, transaction, enrollment, ledger)
    structlog.get_logger("billing").info(
        "billing_payment_recorded",
        category=(
            "billing_capture"
            if transaction.status == BillingTransactionStatus.CAPTURED
            else "billing_attempt"
        ),
        payment_id=transaction.provider_payment_id,
        status=transaction.status.value,
    )
    return result


def _filter_revenue_transactions(
    repo: Repository,
    *,
    from_date: date | None,
    to_date: date | None,
    status: BillingTransactionStatus | None,
    payment_method: str | None,
    data_classification: DataClassification | None,
    relationship: BusinessRelationship | None,
    query: str | None,
) -> list[BillingTransaction]:
    businesses = {business.id: business for business in repo.list_businesses()}
    normalized_query = query.strip().casefold() if query else None
    selected: list[BillingTransaction] = []
    for transaction in repo.list_billing_transactions():
        business = businesses.get(transaction.business_id or "")
        occurred_at = transaction.captured_at or transaction.created_at
        if from_date and occurred_at.date() < from_date:
            continue
        if to_date and occurred_at.date() > to_date:
            continue
        if status and transaction.status != status:
            continue
        if payment_method and transaction.payment_method != payment_method:
            continue
        if data_classification and (
            business is None or business.data_classification != data_classification
        ):
            continue
        if relationship and (business is None or business.relationship != relationship):
            continue
        if normalized_query and normalized_query not in " ".join(
            [
                business.name.casefold() if business else "",
                str(transaction.payer_email or "").casefold(),
                transaction.provider_payment_id.casefold(),
            ]
        ):
            continue
        selected.append(transaction)
    return selected


def _all_founder_plans(repo: Repository) -> list[FounderPlanEnrollment]:
    plans: list[FounderPlanEnrollment] = []
    cursor: str | None = None
    while True:
        page, cursor = repo.list_founder_plans(100, cursor)
        plans.extend(page)
        if cursor is None:
            return plans


def repository_from(request: Request) -> Repository:
    return request.app.state.repository  # type: ignore[no-any-return]


RepositoryDependency = Annotated[Repository, Depends(repository_from)]
UserDependency = Annotated[AuthenticatedUser, Depends(authenticated_user)]


def workflow_from(request: Request) -> CollectionWorkflow:
    adapter: DecisionAdapter | None = request.app.state.decision_adapter
    if adapter is None:
        raise ApiError(503, "gemini_not_configured", "Agent decisioning is not configured.")
    return CollectionWorkflow(
        repository=request.app.state.repository,
        decision_adapter=adapter,
        settings=request.app.state.settings,
        gmail_adapter=request.app.state.gmail_adapter,
        token_cipher=request.app.state.token_cipher,
    )


def require_admin(user: AuthenticatedUser, settings: Settings) -> None:
    if user.uid not in settings.admin_uids:
        raise ApiError(403, "admin_required", "Platform administrator access is required.")


def require_membership_role(tenant: TenantContext, *allowed: MembershipRole) -> None:
    if tenant.role not in allowed:
        raise ApiError(
            403,
            "role_forbidden",
            "Your business role does not allow this operation.",
        )


def action_visible_to_role(action: Action, tenant: TenantContext) -> Action:
    if tenant.role != MembershipRole.ADVISOR:
        return action
    return action.model_copy(update={"recipient_email": None, "subject": None, "body": None})


PROSPECT_TRANSITIONS: dict[ProspectStatus, set[ProspectStatus]] = {
    ProspectStatus.NOT_CONTACTED: {
        ProspectStatus.CONTACTED,
        ProspectStatus.INTERVIEWED,
        ProspectStatus.DECLINED,
        ProspectStatus.DO_NOT_CONTACT,
    },
    ProspectStatus.CONTACTED: {
        ProspectStatus.INTERVIEW_SCHEDULED,
        ProspectStatus.INTERVIEWED,
        ProspectStatus.DECLINED,
        ProspectStatus.DO_NOT_CONTACT,
    },
    ProspectStatus.INTERVIEW_SCHEDULED: {
        ProspectStatus.INTERVIEWED,
        ProspectStatus.DECLINED,
        ProspectStatus.DO_NOT_CONTACT,
    },
    ProspectStatus.INTERVIEWED: {
        ProspectStatus.INTERVIEW_SCHEDULED,
        ProspectStatus.DESIGN_PARTNER,
        ProspectStatus.CONVERTED,
        ProspectStatus.DECLINED,
        ProspectStatus.DO_NOT_CONTACT,
    },
    ProspectStatus.DESIGN_PARTNER: {
        ProspectStatus.CONVERTED,
        ProspectStatus.DECLINED,
        ProspectStatus.DO_NOT_CONTACT,
    },
    ProspectStatus.CONVERTED: set(),
    ProspectStatus.DECLINED: set(),
    ProspectStatus.DO_NOT_CONTACT: set(),
}


def validate_prospect_transition(current: ProspectStatus, target: ProspectStatus) -> None:
    if target != current and target not in PROSPECT_TRANSITIONS[current]:
        raise ApiError(
            409,
            "invalid_prospect_transition",
            f"A prospect cannot move from {current.value} to {target.value}.",
        )


def _invoice_id(extraction_id: str) -> str:
    return f"inv_{hashlib.sha256(extraction_id.encode()).hexdigest()[:24]}"


def _customer_id(email: str | None, extraction_id: str) -> str:
    identity = email.casefold().strip() if email else extraction_id
    return f"cust_{hashlib.sha256(identity.encode()).hexdigest()[:24]}"


def _confirmation_hash(payload: InvoiceConfirm, amount_minor: int) -> str:
    canonical = payload.model_dump(mode="json", exclude={"confirmed"})
    canonical["amount_minor"] = amount_minor
    canonical.pop("amount_decimal", None)
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def _invoice_summary(
    invoice: Invoice, actions: list[Action], cooldown_hours: int
) -> InvoiceSummary:
    return InvoiceSummary(
        id=invoice.id,
        invoice_number=invoice.invoice_number,
        customer_name=invoice.customer.name,
        amount_minor=invoice.amount_minor,
        currency=invoice.currency,
        due_date=invoice.due_date,
        current_state=calculate_invoice_state(invoice, actions, cooldown_hours=cooldown_hours),
        created_at=invoice.created_at,
    )


def enforce_rate_limit(
    repo: Repository,
    subject: str,
    operation: str,
    limit: int,
    window_seconds: int,
) -> None:
    repo.consume_rate_limit(
        subject=subject,
        operation=operation,
        limit=limit,
        window_seconds=window_seconds,
        now=datetime.now(UTC),
    )


async def read_upload_bounded(file: UploadFile, maximum_bytes: int) -> bytes:
    chunks: list[bytes] = []
    streamed_bytes = 0
    while True:
        chunk = await file.read(min(64 * 1024, maximum_bytes + 1 - streamed_bytes))
        if not chunk:
            return b"".join(chunks)
        streamed_bytes += len(chunk)
        if streamed_bytes > maximum_bytes:
            raise ApiError(413, "pdf_too_large", "The PDF exceeds the size limit.")
        chunks.append(chunk)


def create_app(
    *,
    settings: Settings | None = None,
    repository: Repository | None = None,
    auth_verifier: AuthVerifier | None = None,
    invoice_extractor: InvoiceExtractor | None = None,
    decision_adapter: DecisionAdapter | None = None,
    gmail_adapter: GmailAdapter | None = None,
    token_cipher: TokenCipher | None = None,
    scheduler_verifier: SchedulerVerifier | None = None,
    account_auth_manager: AccountAuthManager | None = None,
    payment_gateway: PaymentGateway | None = None,
) -> FastAPI:
    runtime_settings = settings or get_settings()
    configure_logging(runtime_settings)

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        application.state.repository = repository or FirestoreRepository(runtime_settings)
        application.state.auth_verifier = auth_verifier or FirebaseAuthVerifier(runtime_settings)
        application.state.account_auth_manager = (
            account_auth_manager or FirebaseAccountAuthManager()
        )
        if payment_gateway is not None:
            application.state.payment_gateway = payment_gateway
        elif runtime_settings.razorpay_key_id and runtime_settings.razorpay_key_secret:
            application.state.payment_gateway = RazorpayGateway(runtime_settings)
        else:
            application.state.payment_gateway = None
        if invoice_extractor is not None:
            application.state.invoice_extractor = invoice_extractor
        elif runtime_settings.local_emulators_enabled:
            application.state.invoice_extractor = EmulatorInvoiceExtractor()
        else:
            try:
                application.state.invoice_extractor = GeminiInvoiceExtractor(runtime_settings)
            except ExtractionUnavailableError:
                application.state.invoice_extractor = None
        if decision_adapter is not None:
            application.state.decision_adapter = decision_adapter
        elif runtime_settings.local_emulators_enabled:
            application.state.decision_adapter = EmulatorDecisionAdapter()
        else:
            try:
                application.state.decision_adapter = GeminiDecisionAdapter(runtime_settings)
            except DecisionUnavailableError:
                application.state.decision_adapter = None
        if gmail_adapter is not None:
            application.state.gmail_adapter = gmail_adapter
        elif runtime_settings.local_emulators_enabled:
            application.state.gmail_adapter = EmulatorGmailAdapter(runtime_settings)
        else:
            try:
                application.state.gmail_adapter = GoogleGmailAdapter(runtime_settings)
            except GmailUnavailableError:
                application.state.gmail_adapter = None
        if token_cipher is not None:
            application.state.token_cipher = token_cipher
        elif runtime_settings.local_emulators_enabled:
            application.state.token_cipher = EmulatorTokenCipher()
        else:
            try:
                if runtime_settings.runtime_platform == "vercel":
                    application.state.token_cipher = AesGcmTokenCipher(runtime_settings)
                else:
                    application.state.token_cipher = GoogleKmsTokenCipher(runtime_settings)
            except GmailUnavailableError:
                application.state.token_cipher = None
        if scheduler_verifier is not None:
            application.state.scheduler_verifier = scheduler_verifier
        elif runtime_settings.runtime_platform == "vercel":
            application.state.scheduler_verifier = VercelCronVerifier(runtime_settings)
        else:
            application.state.scheduler_verifier = GoogleSchedulerVerifier(runtime_settings)
        yield

    application = FastAPI(
        title="CashSathi API",
        version="0.3.0",
        docs_url="/docs" if runtime_settings.app_env != "production" else None,
        redoc_url=None,
        lifespan=lifespan,
    )
    application.state.settings = runtime_settings
    application.add_middleware(
        CORSMiddleware,
        allow_origins=runtime_settings.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
        expose_headers=["X-Request-ID", "Content-Disposition", "Retry-After"],
    )
    application.add_middleware(RequestGuardsMiddleware, settings=runtime_settings)
    application.add_middleware(RequestContextMiddleware)
    application.add_exception_handler(ApiError, api_error_handler)  # type: ignore[arg-type]
    application.add_exception_handler(RequestValidationError, validation_error_handler)  # type: ignore[arg-type]
    application.add_exception_handler(Exception, unhandled_error_handler)

    @application.get("/healthz", response_model=HealthResponse, tags=["health"])
    def health() -> HealthResponse:
        return HealthResponse(status="ok", environment=runtime_settings.app_env)

    @application.get(
        "/readyz",
        response_model=HealthResponse,
        responses={503: {"model": ErrorEnvelope}},
        tags=["health"],
    )
    def ready(repo: RepositoryDependency) -> HealthResponse:
        try:
            is_ready = repo.ready()
        except Exception:
            structlog.get_logger("readiness").exception("firestore_readiness_failed")
            raise ApiError(503, "service_not_ready", "The data service is not ready.") from None
        if not is_ready:
            raise ApiError(503, "service_not_ready", "The data service is not ready.")
        return HealthResponse(status="ready", environment=runtime_settings.app_env)

    @application.get("/api/me", response_model=MeResponse, tags=["account"])
    def me(user: UserDependency, repo: RepositoryDependency) -> MeResponse:
        business, membership = repo.get_account(user)
        return MeResponse(
            uid=user.uid,
            email=user.email,
            display_name=user.display_name,
            business=business,
            membership=membership,
            is_platform_admin=user.uid in runtime_settings.admin_uids,
        )

    @application.post("/api/team/invitations", response_model=TeamInvitation, tags=["team"])
    def create_team_invitation(
        payload: TeamInvitationCreate,
        user: UserDependency,
        repo: RepositoryDependency,
    ) -> TeamInvitation:
        tenant = repo.require_tenant(user)
        require_membership_role(tenant, MembershipRole.OWNER, MembershipRole.ADMIN)
        if tenant.role == MembershipRole.ADMIN and payload.role == MembershipRole.ADMIN:
            raise ApiError(403, "role_forbidden", "Only the owner can invite an administrator.")
        now = datetime.now(UTC)
        invitation = TeamInvitation(
            id=f"invite_{uuid4().hex}",
            business_id=tenant.business_id,
            email=payload.email,
            role=payload.role,
            status="PENDING",
            invited_by=tenant.user_id,
            created_at=now,
            expires_at=now + timedelta(days=7),
        )
        return repo.create_team_invitation(tenant, invitation)

    @application.get("/api/team/invitations", response_model=TeamInvitationPage, tags=["team"])
    def list_team_invitations(
        user: UserDependency, repo: RepositoryDependency
    ) -> TeamInvitationPage:
        tenant = repo.require_tenant(user)
        require_membership_role(tenant, MembershipRole.OWNER, MembershipRole.ADMIN)
        return TeamInvitationPage(items=repo.list_team_invitations(tenant))

    @application.post(
        "/api/team/invitations/{invitation_id}/accept",
        response_model=Membership,
        tags=["team"],
    )
    def accept_team_invitation(
        invitation_id: str, user: UserDependency, repo: RepositoryDependency
    ) -> Membership:
        return repo.accept_team_invitation(user, invitation_id)

    @application.post(
        "/api/team/invitations/{invitation_id}/revoke",
        response_model=TeamInvitation,
        tags=["team"],
    )
    def revoke_team_invitation(
        invitation_id: str,
        _payload: InvitationRevoke,
        user: UserDependency,
        repo: RepositoryDependency,
    ) -> TeamInvitation:
        tenant = repo.require_tenant(user)
        require_membership_role(tenant, MembershipRole.OWNER, MembershipRole.ADMIN)
        invitation = next(
            (item for item in repo.list_team_invitations(tenant) if item.id == invitation_id),
            None,
        )
        if invitation is None:
            raise ApiError(404, "invitation_not_found", "The invitation was not found.")
        if tenant.role == MembershipRole.ADMIN and invitation.role == MembershipRole.ADMIN:
            raise ApiError(
                403, "role_forbidden", "Only the owner can manage an administrator invitation."
            )
        return repo.revoke_team_invitation(tenant, invitation_id)

    @application.get("/api/team/members", response_model=MembershipPage, tags=["team"])
    def list_team_members(user: UserDependency, repo: RepositoryDependency) -> MembershipPage:
        tenant = repo.require_tenant(user)
        require_membership_role(tenant, MembershipRole.OWNER, MembershipRole.ADMIN)
        return MembershipPage(items=repo.list_members(tenant))

    @application.patch(
        "/api/team/members/{member_user_id}", response_model=Membership, tags=["team"]
    )
    def update_team_member(
        member_user_id: str,
        payload: MembershipRoleUpdate,
        user: UserDependency,
        repo: RepositoryDependency,
    ) -> Membership:
        tenant = repo.require_tenant(user)
        require_membership_role(tenant, MembershipRole.OWNER, MembershipRole.ADMIN)
        target = next(
            (item for item in repo.list_members(tenant) if item.user_id == member_user_id),
            None,
        )
        if target is None:
            raise ApiError(404, "member_not_found", "The team member was not found.")
        if tenant.role == MembershipRole.ADMIN and (
            target.role == MembershipRole.ADMIN or payload.role == MembershipRole.ADMIN
        ):
            raise ApiError(403, "role_forbidden", "Only the owner can manage administrators.")
        return repo.update_member_role(tenant, member_user_id, payload.role)

    @application.post(
        "/api/team/members/{member_user_id}/revoke",
        response_model=Membership,
        tags=["team"],
    )
    def revoke_team_member(
        member_user_id: str,
        _payload: MembershipRevoke,
        user: UserDependency,
        repo: RepositoryDependency,
    ) -> Membership:
        tenant = repo.require_tenant(user)
        require_membership_role(tenant, MembershipRole.OWNER, MembershipRole.ADMIN)
        if member_user_id == tenant.user_id:
            raise ApiError(409, "self_revocation_forbidden", "You cannot revoke yourself.")
        target = next(
            (item for item in repo.list_members(tenant) if item.user_id == member_user_id),
            None,
        )
        if target is None:
            raise ApiError(404, "member_not_found", "The team member was not found.")
        if tenant.role == MembershipRole.ADMIN and target.role == MembershipRole.ADMIN:
            raise ApiError(403, "role_forbidden", "Only the owner can revoke an administrator.")
        return repo.revoke_member(tenant, member_user_id)

    @application.post("/api/businesses", response_model=Business, tags=["businesses"])
    def create_business(
        payload: BusinessCreate, user: UserDependency, repo: RepositoryDependency
    ) -> Business:
        business, _membership = repo.get_or_create_business(
            user,
            payload.name,
            (
                BillingAccessMode.REQUIRED
                if runtime_settings.billing_enforcement_enabled
                else BillingAccessMode.LEGACY
            ),
        )
        return business

    @application.get("/api/businesses/current", response_model=Business, tags=["businesses"])
    def current_business(user: UserDependency, repo: RepositoryDependency) -> Business:
        tenant = repo.require_tenant(user)
        business, _membership = repo.get_account(user)
        if business is None or business.id != tenant.business_id:
            raise ApiError(
                403, "tenant_mismatch", "The authenticated tenant could not be resolved."
            )
        return business

    @application.get(
        "/api/consents/product-processing",
        response_model=ConsentStatus,
        tags=["privacy"],
    )
    def product_consent_status(user: UserDependency, repo: RepositoryDependency) -> ConsentStatus:
        tenant = repo.require_tenant(user)
        record = repo.get_consent(tenant, PRODUCT_CONSENT_VERSION)
        return ConsentStatus(
            version=PRODUCT_CONSENT_VERSION,
            statement=PRODUCT_CONSENT_STATEMENT,
            granted=record is not None,
            granted_at=record.granted_at if record else None,
        )

    @application.post(
        "/api/consents/product-processing",
        response_model=ConsentStatus,
        tags=["privacy"],
    )
    def grant_product_consent(
        payload: ConsentAccept, user: UserDependency, repo: RepositoryDependency
    ) -> ConsentStatus:
        tenant = repo.require_tenant(user)
        require_membership_role(tenant, MembershipRole.OWNER, MembershipRole.ADMIN)
        if payload.version != PRODUCT_CONSENT_VERSION:
            raise ApiError(
                409,
                "consent_version_changed",
                "The consent statement changed. Review the current version before accepting.",
            )
        record = repo.grant_consent(tenant, PRODUCT_CONSENT_VERSION, PRODUCT_CONSENT_HASH)
        return ConsentStatus(
            version=PRODUCT_CONSENT_VERSION,
            statement=PRODUCT_CONSENT_STATEMENT,
            granted=True,
            granted_at=record.granted_at,
        )

    @application.get(
        "/api/privacy/consents", response_model=OptionalConsentResponse, tags=["privacy"]
    )
    def optional_consents(
        user: UserDependency, repo: RepositoryDependency
    ) -> OptionalConsentResponse:
        return optional_consent_response(repo.list_optional_consents(repo.require_tenant(user)))

    @application.post(
        "/api/privacy/consents/{consent_type}/grant",
        response_model=OptionalConsentResponse,
        tags=["privacy"],
    )
    def grant_optional_consent(
        consent_type: OptionalConsentType,
        payload: OptionalConsentGrant,
        user: UserDependency,
        repo: RepositoryDependency,
    ) -> OptionalConsentResponse:
        tenant = repo.require_tenant(user)
        require_membership_role(tenant, MembershipRole.OWNER)
        version, _statement = OPTIONAL_CONSENTS[consent_type]
        if payload.version != version:
            raise ApiError(
                409,
                "consent_version_changed",
                "The consent statement changed. Review the current version before accepting.",
            )
        if consent_type == OptionalConsentType.ANONYMIZED_METRICS:
            if payload.approved_text or payload.channels:
                raise ApiError(
                    422,
                    "invalid_consent_scope",
                    "Anonymized metrics consent does not accept text or channels.",
                )
        elif not payload.approved_text or not payload.approved_text.strip() or not payload.channels:
            raise ApiError(
                422,
                "consent_scope_required",
                "Approved text and at least one publication channel are required.",
            )
        events = repo.list_optional_consents(tenant)
        if consent_type in active_optional_consents(events):
            raise ApiError(409, "consent_already_active", "This consent is already active.")
        repo.append_optional_consent(
            tenant,
            OptionalConsentEvent(
                id=f"consent_evt_{uuid4().hex}",
                consent_type=consent_type,
                action=ConsentEventAction.GRANTED,
                version=version,
                business_id=tenant.business_id,
                user_id=tenant.user_id,
                statement_sha256=statement_hash(consent_type),
                approved_text=payload.approved_text.strip() if payload.approved_text else None,
                channels=payload.channels,
                occurred_at=datetime.now(UTC),
            ),
        )
        return optional_consent_response(repo.list_optional_consents(tenant))

    @application.post(
        "/api/privacy/consents/{grant_id}/withdraw",
        response_model=OptionalConsentResponse,
        tags=["privacy"],
    )
    def withdraw_optional_consent(
        grant_id: str,
        _payload: OptionalConsentWithdraw,
        user: UserDependency,
        repo: RepositoryDependency,
    ) -> OptionalConsentResponse:
        tenant = repo.require_tenant(user)
        require_membership_role(tenant, MembershipRole.OWNER)
        events = repo.list_optional_consents(tenant)
        grant = next(
            (
                event
                for event in events
                if event.id == grant_id and event.action == ConsentEventAction.GRANTED
            ),
            None,
        )
        if grant is None:
            raise ApiError(404, "consent_grant_not_found", "The consent grant was not found.")
        if grant.id not in {active.id for active in active_optional_consents(events).values()}:
            raise ApiError(409, "consent_not_active", "The consent grant is not active.")
        repo.append_optional_consent(
            tenant,
            OptionalConsentEvent(
                id=f"consent_evt_{uuid4().hex}",
                consent_type=grant.consent_type,
                action=ConsentEventAction.WITHDRAWN,
                version=grant.version,
                business_id=tenant.business_id,
                user_id=tenant.user_id,
                statement_sha256=grant.statement_sha256,
                withdraws_grant_id=grant.id,
                occurred_at=datetime.now(UTC),
            ),
        )
        return optional_consent_response(repo.list_optional_consents(tenant))

    @application.get("/api/account/export", tags=["privacy"])
    def account_export(user: UserDependency, repo: RepositoryDependency) -> Response:
        tenant = repo.require_tenant(user)
        require_membership_role(tenant, MembershipRole.OWNER, MembershipRole.ADMIN)
        enforce_rate_limit(repo, tenant.business_id, "account_export", 3, 3600)
        export = build_account_export(repo, tenant, PRODUCT_CONSENT_VERSION)
        return Response(
            content=export.model_dump_json(indent=2),
            media_type="application/json",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="cashsathi-account-{tenant.business_id}.json"'
                )
            },
        )

    @application.post("/api/account/delete", response_model=AccountDeleteResult, tags=["privacy"])
    async def delete_account(
        payload: AccountDelete,
        request: Request,
        user: UserDependency,
        repo: RepositoryDependency,
    ) -> AccountDeleteResult:
        tenant = repo.require_tenant(user)
        require_membership_role(tenant, MembershipRole.OWNER)
        enforce_rate_limit(repo, tenant.user_id, "account_delete", 3, 86400)
        business = repo.get_business_by_id(tenant.business_id)
        if payload.business_name.strip().casefold() != business.name.strip().casefold():
            raise ApiError(
                422, "business_name_mismatch", "Enter the exact business name to confirm deletion."
            )
        events = repo.list_optional_consents(tenant)
        retain_metrics = OptionalConsentType.ANONYMIZED_METRICS in active_optional_consents(events)
        repo.update_automation(tenant, False)
        revocation_succeeded = True
        connection = repo.get_gmail_connection(tenant.business_id)
        if connection and connection.encrypted_refresh_token:
            adapter: GmailAdapter | None = request.app.state.gmail_adapter
            cipher: TokenCipher | None = request.app.state.token_cipher
            try:
                if adapter is None or cipher is None:
                    raise GmailUnavailableError("Gmail revocation is not configured")
                refresh_token = await run_in_threadpool(
                    cipher.decrypt, connection.encrypted_refresh_token
                )
                await run_in_threadpool(adapter.revoke, refresh_token)
            except GmailUnavailableError:
                revocation_succeeded = False
                structlog.get_logger("privacy").error(
                    "gmail_revocation_failed", category="privacy_deletion_failure"
                )
        try:
            repo.delete_account(tenant, retain_metrics)
            manager: AccountAuthManager = request.app.state.account_auth_manager
            await run_in_threadpool(manager.delete_user, user.uid)
        except Exception:
            structlog.get_logger("privacy").exception(
                "account_deletion_failed", category="privacy_deletion_failure"
            )
            raise ApiError(
                503,
                "account_deletion_incomplete",
                "Account deletion could not be completed. Contact support with the request ID.",
            ) from None
        return AccountDeleteResult(
            gmail_revocation_succeeded=revocation_succeeded,
            google_revocation_instructions_required=not revocation_succeeded,
        )

    @application.get(
        "/api/plans/current",
        response_model=FounderPlanEnrollment | None,
        tags=["plans"],
    )
    def current_plan(
        user: UserDependency, repo: RepositoryDependency
    ) -> FounderPlanEnrollment | None:
        tenant = repo.require_tenant(user)
        return repo.get_founder_plan(tenant.business_id)

    @application.post("/api/billing/orders", response_model=BillingCheckoutOrder, tags=["billing"])
    def create_billing_order(
        payload: BillingOrderCreate,
        request: Request,
        user: UserDependency,
        repo: RepositoryDependency,
    ) -> BillingCheckoutOrder:
        tenant = repo.require_tenant(user)
        require_membership_role(tenant, MembershipRole.OWNER)
        business, _membership = repo.get_account(user)
        if business is None:
            raise ApiError(404, "business_not_found", "Complete business onboarding first.")
        if repo.get_founder_plan(tenant.business_id) is not None:
            raise ApiError(409, "plan_already_owned", "This workspace already has a Founder Plan.")
        existing = repo.get_billing_order_by_idempotency(
            tenant.business_id, payload.idempotency_key
        )
        if existing is None:
            existing = next(
                (
                    item
                    for item in repo.list_billing_orders(tenant.business_id)
                    if item.provider == BillingProvider.RAZORPAY
                    and item.status in {BillingOrderStatus.CREATED, BillingOrderStatus.ATTEMPTED}
                ),
                None,
            )
        gateway = _gateway_from(request)
        if existing is None:
            internal_id = billing_order_id(tenant.business_id, payload.idempotency_key)
            provider_order = gateway.create_or_find_order(
                amount_minor=FOUNDER_PLAN_AMOUNT_MINOR,
                currency=FOUNDER_PLAN_CURRENCY,
                receipt=internal_id,
                billing_order_id=internal_id,
            )
            now = datetime.now(UTC)
            provider_status = {
                "paid": BillingOrderStatus.PAID,
                "attempted": BillingOrderStatus.ATTEMPTED,
            }.get(provider_order.status.casefold(), BillingOrderStatus.CREATED)
            existing = repo.save_billing_order(
                BillingOrder(
                    id=internal_id,
                    business_id=tenant.business_id,
                    provider_order_id=provider_order.id,
                    receipt=internal_id,
                    idempotency_key=payload.idempotency_key,
                    status=provider_status,
                    created_by=user.uid,
                    created_at=now,
                    updated_at=now,
                )
            )
        return BillingCheckoutOrder(
            order=existing,
            public_key_id=gateway.public_key_id,
            business_name=business.name,
            customer_email=user.email,
        )

    @application.post("/api/billing/confirm", response_model=BillingConfirmation, tags=["billing"])
    def confirm_billing_payment(
        payload: BillingConfirm,
        request: Request,
        user: UserDependency,
        repo: RepositoryDependency,
    ) -> BillingConfirmation:
        tenant = repo.require_tenant(user)
        require_membership_role(tenant, MembershipRole.OWNER)
        order = repo.get_billing_order_by_provider_id(payload.provider_order_id)
        if order.business_id != tenant.business_id:
            raise ApiError(404, "billing_order_not_found", "The billing order was not found.")
        if order.provider_order_id is None or order.provider != BillingProvider.RAZORPAY:
            raise ApiError(
                422, "invalid_billing_order", "The billing order cannot be confirmed online."
            )
        gateway = _gateway_from(request)
        if not gateway.verify_checkout_signature(
            order_id=order.provider_order_id,
            payment_id=payload.provider_payment_id,
            signature=payload.signature,
        ):
            raise ApiError(422, "invalid_payment_signature", "Payment verification failed.")
        payment = gateway.fetch_payment(payload.provider_payment_id)
        transaction, plan = _record_provider_payment(repo, order, payment)
        if transaction.status == BillingTransactionStatus.FAILED:
            raise ApiError(409, "payment_failed", "The payment was not captured.")
        return BillingConfirmation(
            status=(
                "CAPTURED"
                if transaction.status == BillingTransactionStatus.CAPTURED
                else "PROCESSING"
            ),
            transaction=transaction,
            plan=plan,
        )

    @application.get("/api/billing/current", response_model=BillingCurrent, tags=["billing"])
    def current_billing(user: UserDependency, repo: RepositoryDependency) -> BillingCurrent:
        tenant = repo.require_tenant(user)
        require_membership_role(tenant, MembershipRole.OWNER)
        business, _membership = repo.get_account(user)
        if business is None:
            raise ApiError(404, "business_not_found", "Complete business onboarding first.")
        plan = repo.get_founder_plan(tenant.business_id)
        orders = repo.list_billing_orders(tenant.business_id)
        transactions = repo.list_billing_transactions(tenant.business_id)
        required = (
            business.billing_access_mode == BillingAccessMode.REQUIRED
            and business.data_classification != DataClassification.DEMO
        )
        return BillingCurrent(
            billing_access_mode=business.billing_access_mode,
            payment_required=required
            and (plan is None or plan.status == FounderPlanStatus.REFUNDED),
            plan=plan,
            latest_order=orders[0] if orders else None,
            transactions=transactions[:20],
        )

    @application.post("/api/webhooks/razorpay", tags=["billing"])
    async def razorpay_webhook(
        request: Request,
        repo: RepositoryDependency,
        signature: Annotated[str | None, Header(alias="X-Razorpay-Signature")] = None,
        event_id: Annotated[str | None, Header(alias="X-Razorpay-Event-Id")] = None,
    ) -> dict[str, bool]:
        if not signature or not event_id:
            raise ApiError(401, "webhook_signature_required", "Webhook authentication failed.")
        body = await request.body()
        gateway = _gateway_from(request)
        if not gateway.verify_webhook_signature(body, signature):
            structlog.get_logger("billing").warning("invalid_webhook_signature")
            raise ApiError(401, "invalid_webhook_signature", "Webhook authentication failed.")
        try:
            payload = json.loads(body)
        except (TypeError, ValueError):
            raise ApiError(
                422, "invalid_webhook_payload", "The webhook payload is invalid."
            ) from None
        event_type = str(payload.get("event", ""))
        if event_type not in {"payment.captured", "payment.failed", "refund.processed"}:
            created = repo.record_billing_webhook_event(event_id, event_type, None, "IGNORED")
            if not created:
                structlog.get_logger("billing").info(
                    "duplicate_webhook_event", category="billing_duplicate_event"
                )
            return {"accepted": True}
        payment_entity = payload.get("payload", {}).get("payment", {}).get("entity")
        if isinstance(payment_entity, dict):
            payment = provider_payment_from_payload(payment_entity)
        elif event_type == "refund.processed":
            refund_entity = payload.get("payload", {}).get("refund", {}).get("entity")
            payment_id = (
                refund_entity.get("payment_id") if isinstance(refund_entity, dict) else None
            )
            if not isinstance(payment_id, str):
                raise ApiError(422, "payment_entity_missing", "The webhook payment is missing.")
            payment = gateway.fetch_payment(payment_id)
        else:
            raise ApiError(422, "payment_entity_missing", "The webhook payment is missing.")
        order = repo.get_billing_order_by_provider_id(payment.order_id)
        _record_provider_payment(repo, order, payment)
        created = repo.record_billing_webhook_event(event_id, event_type, payment.id, "PROCESSED")
        if not created:
            structlog.get_logger("billing").info(
                "duplicate_webhook_event", category="billing_duplicate_event"
            )
        return {"accepted": True}

    @application.post("/api/invoices/extract", response_model=ExtractionResult, tags=["invoices"])
    async def extract_invoice(
        request: Request,
        user: UserDependency,
        repo: RepositoryDependency,
        file: Annotated[UploadFile, File()],
    ) -> ExtractionResult:
        tenant = repo.require_tenant(user)
        require_membership_role(
            tenant, MembershipRole.OWNER, MembershipRole.ADMIN, MembershipRole.OPERATOR
        )
        enforce_rate_limit(repo, tenant.business_id, "invoice_extract", 5, 600)
        if repo.get_consent(tenant, PRODUCT_CONSENT_VERSION) is None:
            raise ApiError(
                403,
                "consent_required",
                "Accept the current product-processing consent before uploading an invoice.",
            )
        try:
            data = await read_upload_bounded(file, runtime_settings.max_pdf_bytes)
        finally:
            await file.close()
        pdf = validate_pdf(
            filename=file.filename,
            content_type=file.content_type,
            data=data,
            settings=runtime_settings,
        )
        extractor: InvoiceExtractor | None = request.app.state.invoice_extractor
        if extractor is None:
            raise ApiError(503, "gemini_not_configured", "Invoice extraction is not configured.")
        try:
            output = await run_in_threadpool(extractor.extract, pdf)
        except ExtractionUnavailableError:
            structlog.get_logger("gemini").warning(
                "invoice_extraction_failed", category="schema_or_model_failure"
            )
            raise ApiError(
                503, "extraction_unavailable", "Invoice extraction is temporarily unavailable."
            ) from None
        extraction_id = repo.record_extraction(
            tenant,
            {
                "model_id": extractor.model_id,
                "prompt_version": extractor.prompt_version,
                "latency_ms": output.latency_ms,
                "input_tokens": output.input_tokens,
                "output_tokens": output.output_tokens,
                "page_count": pdf.page_count,
                "byte_count": pdf.byte_count,
                "validation_status": "VALID",
                "warning_codes": [warning.code for warning in output.draft.warnings],
            },
        )
        return ExtractionResult(
            extraction_id=extraction_id,
            draft=output.draft,
            model_id=extractor.model_id,
            prompt_version=extractor.prompt_version,
            latency_ms=output.latency_ms,
        )

    @application.post("/api/invoices", response_model=Invoice, tags=["invoices"])
    def confirm_invoice(
        payload: InvoiceConfirm, user: UserDependency, repo: RepositoryDependency
    ) -> Invoice:
        tenant = repo.require_tenant(user)
        require_membership_role(
            tenant, MembershipRole.OWNER, MembershipRole.ADMIN, MembershipRole.OPERATOR
        )
        if repo.get_consent(tenant, PRODUCT_CONSENT_VERSION) is None:
            raise ApiError(403, "consent_required", "Product-processing consent is required.")
        amount_minor = decimal_to_minor(payload.amount_decimal, payload.currency)
        email = str(payload.customer_email).casefold() if payload.customer_email else None
        now = datetime.now(UTC)
        invoice_id = _invoice_id(payload.extraction_id)
        invoice = Invoice(
            id=invoice_id,
            business_id=tenant.business_id,
            extraction_id=payload.extraction_id,
            invoice_number=payload.invoice_number,
            customer=CustomerSnapshot(
                id=_customer_id(email, payload.extraction_id),
                name=payload.customer_name,
                email=email,
                manual_only=payload.customer_manual_only,
            ),
            amount_minor=amount_minor,
            currency=payload.currency,
            issue_date=payload.issue_date,
            due_date=payload.due_date,
            payment_terms=payload.payment_terms,
            review_required=payload.due_date is None,
            review_reason="missing_due_date" if payload.due_date is None else None,
            confirmation_hash=_confirmation_hash(payload, amount_minor),
            created_at=now,
            updated_at=now,
        )
        invoice = invoice.model_copy(
            update={
                "next_check_at": initial_next_check(invoice, now),
                "workflow_status": (
                    InvoiceWorkflowStatus.PAUSED
                    if invoice.review_required
                    else InvoiceWorkflowStatus.OPEN
                ),
            }
        )
        return repo.save_invoice(tenant, invoice)

    @application.get("/api/invoices", response_model=InvoicePage, tags=["invoices"])
    def list_invoices(
        user: UserDependency,
        repo: RepositoryDependency,
        limit: Annotated[int, Query(ge=1, le=100)] = 25,
        cursor: Annotated[str | None, Query(max_length=300)] = None,
    ) -> InvoicePage:
        tenant = repo.require_tenant(user)
        invoices, next_cursor = repo.list_invoices(tenant, limit, cursor)
        settings = repo.get_policy_settings(tenant)
        actions_by_invoice: dict[str, list[Action]] = defaultdict(list)
        for action in repo.list_all_action_records(tenant):
            actions_by_invoice[action.invoice_id].append(action)
        return InvoicePage(
            items=[
                _invoice_summary(
                    invoice,
                    actions_by_invoice.get(invoice.id, []),
                    settings.reminder_cooldown_hours,
                )
                for invoice in invoices
            ],
            next_cursor=next_cursor,
        )

    @application.get("/api/invoices/{invoice_id}", response_model=InvoiceDetail, tags=["invoices"])
    def invoice_detail(
        invoice_id: str, user: UserDependency, repo: RepositoryDependency
    ) -> InvoiceDetail:
        tenant = repo.require_tenant(user)
        invoice = repo.get_invoice(tenant, invoice_id)
        actions = repo.list_actions(tenant, invoice.id)
        runs, _cursor = repo.list_agent_runs(tenant, invoice.id, 1, None)
        settings = repo.get_policy_settings(tenant)
        return InvoiceDetail(
            invoice=invoice,
            current_state=calculate_invoice_state(
                invoice, actions, cooldown_hours=settings.reminder_cooldown_hours
            ),
            latest_agent_run=runs[0] if runs else None,
            latest_action=action_visible_to_role(actions[0], tenant) if actions else None,
        )

    @application.patch(
        "/api/customers/{customer_id}", response_model=CustomerSnapshot, tags=["customers"]
    )
    def update_customer_policy(
        customer_id: str,
        payload: CustomerPolicyUpdate,
        user: UserDependency,
        repo: RepositoryDependency,
    ) -> CustomerSnapshot:
        tenant = repo.require_tenant(user)
        require_membership_role(tenant, MembershipRole.OWNER, MembershipRole.ADMIN)
        return repo.update_customer_policy(
            tenant,
            customer_id,
            payload.manual_only,
            payload.locale,
        )

    @application.post(
        "/api/invoices/{invoice_id}/disputes", response_model=Invoice, tags=["invoices"]
    )
    def open_dispute(
        invoice_id: str,
        payload: DisputeCreate,
        user: UserDependency,
        repo: RepositoryDependency,
    ) -> Invoice:
        tenant = repo.require_tenant(user)
        require_membership_role(
            tenant, MembershipRole.OWNER, MembershipRole.ADMIN, MembershipRole.OPERATOR
        )
        invoice = repo.get_invoice(tenant, invoice_id)
        if invoice.dispute_active:
            raise ApiError(409, "dispute_already_active", "The invoice is already disputed.")
        if invoice.workflow_status == InvoiceWorkflowStatus.CLOSED:
            raise ApiError(409, "invoice_closed", "A closed invoice cannot be disputed.")
        now = datetime.now(UTC)
        cancelled_action_id: str | None = None
        if invoice.active_action_id:
            action = repo.get_action(tenant, invoice.active_action_id)
            if action.state in {
                ActionState.PROPOSED,
                ActionState.AWAITING_APPROVAL,
                ActionState.FAILED,
            }:
                cancelled = action.model_copy(
                    update={
                        "state": ActionState.CANCELLED,
                        "cancelled_by": tenant.user_id,
                        "cancelled_at": now,
                        "cancel_reason": "Invoice dispute opened",
                    }
                )
                repo.update_action(
                    tenant,
                    cancelled,
                    None,
                    EvidenceEventType.ACTION_CANCELLED,
                    "USER",
                )
                cancelled_action_id = action.id
        disputed = invoice.model_copy(
            update={
                "dispute_active": True,
                "dispute_reason": payload.reason,
                "dispute_note": payload.note,
                "dispute_opened_at": now,
                "dispute_opened_by": tenant.user_id,
                "dispute_resolution_note": None,
                "dispute_resolved_at": None,
                "dispute_resolved_by": None,
                "workflow_status": InvoiceWorkflowStatus.PAUSED,
                "next_check_at": None,
                "evaluation_lease_until": None,
                "active_action_id": None if cancelled_action_id else invoice.active_action_id,
                "updated_at": now,
            }
        )
        return repo.update_invoice_with_event(
            tenant,
            disputed,
            EvidenceEventType.DISPUTE_OPENED,
            {
                "reason": payload.reason.value,
                "cancelled_action_id": cancelled_action_id,
                "note_present": payload.note is not None,
            },
        )

    @application.post(
        "/api/invoices/{invoice_id}/disputes/resolve",
        response_model=Invoice,
        tags=["invoices"],
    )
    def resolve_dispute(
        invoice_id: str,
        payload: DisputeResolve,
        user: UserDependency,
        repo: RepositoryDependency,
    ) -> Invoice:
        tenant = repo.require_tenant(user)
        require_membership_role(
            tenant, MembershipRole.OWNER, MembershipRole.ADMIN, MembershipRole.OPERATOR
        )
        invoice = repo.get_invoice(tenant, invoice_id)
        if not invoice.dispute_active:
            raise ApiError(409, "dispute_not_active", "The invoice has no active dispute.")
        now = datetime.now(UTC)
        if payload.next_check_at <= now:
            raise ApiError(422, "next_check_in_past", "The next check must be in the future.")
        reopened = invoice.model_copy(
            update={
                "dispute_active": False,
                "dispute_resolution_note": payload.resolution_note,
                "dispute_resolved_at": now,
                "dispute_resolved_by": tenant.user_id,
                "workflow_status": InvoiceWorkflowStatus.OPEN,
                "next_check_at": payload.next_check_at,
                "evaluation_lease_until": None,
                "updated_at": now,
            }
        )
        return repo.update_invoice_with_event(
            tenant,
            reopened,
            EvidenceEventType.DISPUTE_RESOLVED,
            {
                "next_check_at": payload.next_check_at.isoformat(),
                "resolution_note_present": payload.resolution_note is not None,
            },
        )

    @application.post(
        "/api/invoices/{invoice_id}/evaluate",
        response_model=EvaluationResult,
        tags=["agent"],
    )
    async def evaluate_invoice(
        invoice_id: str,
        request: Request,
        user: UserDependency,
        repo: RepositoryDependency,
    ) -> EvaluationResult:
        tenant = repo.require_tenant(user)
        require_membership_role(
            tenant, MembershipRole.OWNER, MembershipRole.ADMIN, MembershipRole.OPERATOR
        )
        enforce_rate_limit(repo, tenant.business_id, "invoice_evaluate", 20, 600)
        return await workflow_from(request).evaluate(tenant, invoice_id)

    @application.get("/api/agent-runs", response_model=AgentRunPage, tags=["agent"])
    def list_agent_runs(
        user: UserDependency,
        repo: RepositoryDependency,
        invoice_id: Annotated[str | None, Query(max_length=100)] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 25,
        cursor: Annotated[str | None, Query(max_length=300)] = None,
    ) -> AgentRunPage:
        tenant = repo.require_tenant(user)
        runs, next_cursor = repo.list_agent_runs(tenant, invoice_id, limit, cursor)
        return AgentRunPage(items=runs, next_cursor=next_cursor)

    @application.get("/api/actions", response_model=ActionPage, tags=["actions"])
    def list_actions(
        user: UserDependency,
        repo: RepositoryDependency,
        state: Annotated[ActionState | None, Query()] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 25,
        cursor: Annotated[str | None, Query(max_length=300)] = None,
    ) -> ActionPage:
        tenant = repo.require_tenant(user)
        actions, next_cursor = repo.list_all_actions(tenant, state, limit, cursor)
        return ActionPage(
            items=[action_visible_to_role(action, tenant) for action in actions],
            next_cursor=next_cursor,
        )

    @application.post("/api/actions/{action_id}/approve", response_model=Action, tags=["actions"])
    async def approve_action(
        action_id: str, request: Request, user: UserDependency, repo: RepositoryDependency
    ) -> Action:
        tenant = repo.require_tenant(user)
        require_membership_role(tenant, MembershipRole.OWNER, MembershipRole.ADMIN)
        enforce_rate_limit(repo, tenant.business_id, "action_mutation", 20, 600)
        return await workflow_from(request).approve(tenant, action_id)

    @application.post("/api/actions/{action_id}/cancel", response_model=Action, tags=["actions"])
    def cancel_action(
        action_id: str,
        payload: ActionCancel,
        request: Request,
        user: UserDependency,
        repo: RepositoryDependency,
    ) -> Action:
        tenant = repo.require_tenant(user)
        require_membership_role(
            tenant, MembershipRole.OWNER, MembershipRole.ADMIN, MembershipRole.OPERATOR
        )
        enforce_rate_limit(repo, tenant.business_id, "action_mutation", 20, 600)
        return workflow_from(request).cancel(tenant, action_id, payload)

    @application.post("/api/actions/{action_id}/retry", response_model=Action, tags=["actions"])
    async def retry_action(
        action_id: str, request: Request, user: UserDependency, repo: RepositoryDependency
    ) -> Action:
        tenant = repo.require_tenant(user)
        require_membership_role(tenant, MembershipRole.OWNER, MembershipRole.ADMIN)
        enforce_rate_limit(repo, tenant.business_id, "action_mutation", 20, 600)
        return await workflow_from(request).retry(tenant, action_id)

    @application.post("/api/actions/{action_id}/resolve", response_model=Action, tags=["actions"])
    def resolve_action(
        action_id: str,
        payload: ActionResolve,
        request: Request,
        user: UserDependency,
        repo: RepositoryDependency,
    ) -> Action:
        tenant = repo.require_tenant(user)
        require_membership_role(
            tenant, MembershipRole.OWNER, MembershipRole.ADMIN, MembershipRole.OPERATOR
        )
        enforce_rate_limit(repo, tenant.business_id, "action_mutation", 20, 600)
        return workflow_from(request).resolve(tenant, action_id, payload)

    @application.post(
        "/api/invoices/{invoice_id}/payments", response_model=Payment, tags=["payments"]
    )
    def record_payment(
        invoice_id: str,
        payload: PaymentCreate,
        user: UserDependency,
        repo: RepositoryDependency,
    ) -> Payment:
        tenant = repo.require_tenant(user)
        require_membership_role(
            tenant, MembershipRole.OWNER, MembershipRole.ADMIN, MembershipRole.OPERATOR
        )
        invoice = repo.get_invoice(tenant, invoice_id)
        if payload.currency != invoice.currency:
            raise ApiError(
                422, "payment_currency_mismatch", "Payment currency must match the invoice."
            )
        now = datetime.now(UTC)
        if payload.paid_at > now + timedelta(minutes=5):
            raise ApiError(422, "payment_date_in_future", "Payment time cannot be in the future.")
        amount_minor = decimal_to_minor(payload.amount_decimal, payload.currency)
        raw_id = f"{tenant.business_id}:{invoice_id}:{payload.idempotency_key}"
        payment = Payment(
            id=f"pay_{hashlib.sha256(raw_id.encode()).hexdigest()[:28]}",
            invoice_id=invoice_id,
            business_id=tenant.business_id,
            amount_minor=amount_minor,
            currency=payload.currency,
            paid_at=payload.paid_at,
            reference=payload.reference.strip(),
            idempotency_key=payload.idempotency_key,
            confirmed_by=tenant.user_id,
            created_at=now,
        )
        stored, _invoice = repo.record_payment(tenant, payment)
        return stored

    @application.get(
        "/api/invoices/{invoice_id}/timeline", response_model=InvoiceTimeline, tags=["evidence"]
    )
    def invoice_timeline(
        invoice_id: str, user: UserDependency, repo: RepositoryDependency
    ) -> InvoiceTimeline:
        return build_timeline(repo, repo.require_tenant(user), invoice_id)

    @application.get("/api/metrics", response_model=MetricsResponse, tags=["metrics"])
    def metrics(user: UserDependency, repo: RepositoryDependency) -> MetricsResponse:
        return calculate_metrics(repo, repo.require_tenant(user))

    @application.get("/api/forecast", response_model=CashForecast, tags=["finance"])
    def cash_forecast(user: UserDependency, repo: RepositoryDependency) -> CashForecast:
        return build_cash_forecast(repo, repo.require_tenant(user))

    @application.get("/api/finance-pack", tags=["finance"])
    def finance_readiness_pack(user: UserDependency, repo: RepositoryDependency) -> Response:
        tenant = repo.require_tenant(user)
        require_membership_role(
            tenant, MembershipRole.OWNER, MembershipRole.ADMIN, MembershipRole.ADVISOR
        )
        payload = build_finance_readiness_zip(repo, tenant)
        return Response(
            content=payload,
            media_type="application/zip",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="cashsathi-finance-pack-{tenant.business_id}.zip"'
                )
            },
        )

    @application.get(
        "/api/integrations/accounting",
        response_model=AccountingIntegrationPage,
        tags=["integrations"],
    )
    def accounting_integrations(
        user: UserDependency, repo: RepositoryDependency
    ) -> AccountingIntegrationPage:
        tenant = repo.require_tenant(user)
        require_membership_role(tenant, MembershipRole.OWNER, MembershipRole.ADMIN)
        return accounting_integration_statuses()

    @application.get(
        "/api/integrations/accounting/{provider}/sync-status",
        response_model=AccountingIntegrationStatus,
        tags=["integrations"],
    )
    def accounting_sync_status(
        provider: AccountingProvider,
        user: UserDependency,
        repo: RepositoryDependency,
    ) -> AccountingIntegrationStatus:
        tenant = repo.require_tenant(user)
        require_membership_role(tenant, MembershipRole.OWNER, MembershipRole.ADMIN)
        return next(
            item for item in accounting_integration_statuses().items if item.provider == provider
        )

    @application.post(
        "/api/integrations/accounting/{provider}/sync",
        response_model=AccountingIntegrationStatus,
        tags=["integrations"],
    )
    def sync_accounting_provider(
        provider: AccountingProvider,
        user: UserDependency,
        repo: RepositoryDependency,
    ) -> AccountingIntegrationStatus:
        tenant = repo.require_tenant(user)
        require_membership_role(tenant, MembershipRole.OWNER, MembershipRole.ADMIN)
        raise ApiError(
            409,
            "accounting_integration_not_configured",
            f"{provider.value} credentials and organization approval are not configured.",
        )

    @application.get("/api/settings/automation", response_model=PolicyDefaults, tags=["settings"])
    def automation_settings(user: UserDependency, repo: RepositoryDependency) -> PolicyDefaults:
        return repo.get_policy_settings(repo.require_tenant(user))

    @application.post("/api/settings/automation", response_model=PolicyDefaults, tags=["settings"])
    def update_automation(
        payload: AutomationUpdate, user: UserDependency, repo: RepositoryDependency
    ) -> PolicyDefaults:
        tenant = repo.require_tenant(user)
        require_membership_role(tenant, MembershipRole.OWNER, MembershipRole.ADMIN)
        return repo.update_automation(tenant, payload.enabled)

    @application.patch("/api/settings/policy", response_model=PolicyDefaults, tags=["settings"])
    def update_restrictive_policy(
        payload: RestrictivePolicyUpdate,
        user: UserDependency,
        repo: RepositoryDependency,
    ) -> PolicyDefaults:
        tenant = repo.require_tenant(user)
        require_membership_role(tenant, MembershipRole.OWNER, MembershipRole.ADMIN)
        current = repo.get_policy_settings(tenant)
        if (
            payload.reminder_cooldown_hours is not None
            and payload.reminder_cooldown_hours < current.reminder_cooldown_hours
        ):
            raise ApiError(422, "policy_less_restrictive", "Reminder cooldown cannot decrease.")
        if (
            payload.high_value_threshold_minor is not None
            and payload.high_value_threshold_minor > current.high_value_threshold_minor
        ):
            raise ApiError(422, "policy_less_restrictive", "High-value threshold cannot increase.")
        updated = current.model_copy(
            update={
                "reminder_cooldown_hours": (
                    payload.reminder_cooldown_hours
                    if payload.reminder_cooldown_hours is not None
                    else current.reminder_cooldown_hours
                ),
                "high_value_threshold_minor": (
                    payload.high_value_threshold_minor
                    if payload.high_value_threshold_minor is not None
                    else current.high_value_threshold_minor
                ),
            }
        )
        return repo.update_policy_settings(tenant, updated)

    @application.get(
        "/api/settings/policy/templates",
        response_model=PolicyTemplatePage,
        tags=["settings"],
    )
    def policy_templates(user: UserDependency, repo: RepositoryDependency) -> PolicyTemplatePage:
        tenant = repo.require_tenant(user)
        return restrictive_policy_templates(repo.get_policy_settings(tenant))

    @application.post(
        "/api/settings/policy/templates/{template}/apply",
        response_model=PolicyDefaults,
        tags=["settings"],
    )
    def apply_policy_template(
        template: PolicyTemplateKind,
        _payload: PolicyTemplateApply,
        user: UserDependency,
        repo: RepositoryDependency,
    ) -> PolicyDefaults:
        tenant = repo.require_tenant(user)
        require_membership_role(tenant, MembershipRole.OWNER, MembershipRole.ADMIN)
        selected = next(
            item
            for item in restrictive_policy_templates(repo.get_policy_settings(tenant)).items
            if item.template == template
        )
        current = repo.get_policy_settings(tenant)
        updated = current.model_copy(
            update={
                "reminder_cooldown_hours": selected.reminder_cooldown_hours,
                "high_value_threshold_minor": selected.high_value_threshold_minor,
            }
        )
        return repo.update_policy_settings(tenant, updated)

    @application.get(
        "/api/integrations/gmail/status", response_model=GmailStatus, tags=["integrations"]
    )
    def gmail_status(user: UserDependency, repo: RepositoryDependency) -> GmailStatus:
        tenant = repo.require_tenant(user)
        connection = repo.get_gmail_connection(tenant.business_id)
        settings = repo.get_policy_settings(tenant)
        connected = bool(
            connection and connection.disconnected_at is None and connection.encrypted_refresh_token
        )
        return GmailStatus(
            connected=connected,
            connected_at=connection.connected_at if connected and connection else None,
            last_error_code=connection.last_error_code if connection else None,
            automation_enabled=settings.automation_enabled,
        )

    @application.post(
        "/api/integrations/gmail/connect",
        response_model=GmailConnectResponse,
        tags=["integrations"],
    )
    def connect_gmail(
        request: Request, user: UserDependency, repo: RepositoryDependency
    ) -> GmailConnectResponse:
        tenant = repo.require_tenant(user)
        require_membership_role(tenant, MembershipRole.OWNER, MembershipRole.ADMIN)
        enforce_rate_limit(repo, tenant.user_id, "gmail_connect", 5, 3600)
        adapter: GmailAdapter | None = request.app.state.gmail_adapter
        if adapter is None or request.app.state.token_cipher is None:
            raise ApiError(503, "gmail_not_configured", "Gmail integration is not configured.")
        state_value = secrets.token_urlsafe(32)
        verifier = adapter.new_pkce_verifier()
        now = datetime.now(UTC)
        repo.create_oauth_state(
            tenant,
            GmailOAuthState(
                state=state_value,
                business_id=tenant.business_id,
                user_id=tenant.user_id,
                code_verifier=verifier,
                expires_at=now + timedelta(minutes=10),
                created_at=now,
            ),
        )
        return GmailConnectResponse(
            authorization_url=adapter.authorization_url(state_value, verifier)
        )

    @application.get("/api/integrations/gmail/callback", tags=["integrations"])
    async def gmail_callback(
        request: Request,
        state: Annotated[str, Query(min_length=20, max_length=200)],
        code: Annotated[str | None, Query(max_length=4096)] = None,
        error: Annotated[str | None, Query(max_length=200)] = None,
    ) -> RedirectResponse:
        callback_repo: Repository = request.app.state.repository
        oauth_state = callback_repo.consume_oauth_state(state)
        target = runtime_settings.web_base_url.rstrip("/") + "/integrations/gmail"
        if error or not code:
            return RedirectResponse(f"{target}?status=cancelled", status_code=303)
        # Resolve and validate the tenant before exchanging the code so a stale/mismatched
        # OAuth state never results in a live, unrevoked Google-side grant.
        tenant = callback_repo.require_tenant(AuthenticatedUser(oauth_state.user_id, None, None))
        if tenant.business_id != oauth_state.business_id:
            raise ApiError(400, "oauth_tenant_mismatch", "The Gmail connection tenant changed.")
        adapter: GmailAdapter | None = request.app.state.gmail_adapter
        cipher: TokenCipher | None = request.app.state.token_cipher
        if adapter is None or cipher is None:
            return RedirectResponse(f"{target}?status=not-configured", status_code=303)
        try:
            refresh_token = await run_in_threadpool(
                adapter.exchange_code, code, oauth_state.code_verifier
            )
            encrypted = await run_in_threadpool(cipher.encrypt, refresh_token)
        except (GmailDefiniteFailure, GmailUnavailableError):
            return RedirectResponse(f"{target}?status=failed", status_code=303)
        now = datetime.now(UTC)
        callback_repo.save_gmail_connection(
            tenant,
            GmailConnection(
                business_id=tenant.business_id,
                encrypted_refresh_token=encrypted,
                kms_key_name=cipher.key_name,
                connected_at=now,
                updated_at=now,
            ),
        )
        return RedirectResponse(f"{target}?status=connected", status_code=303)

    @application.post("/api/integrations/gmail/disconnect", tags=["integrations"])
    def disconnect_gmail(user: UserDependency, repo: RepositoryDependency) -> GmailStatus:
        tenant = repo.require_tenant(user)
        require_membership_role(tenant, MembershipRole.OWNER, MembershipRole.ADMIN)
        repo.disconnect_gmail(tenant)
        repo.update_automation(tenant, False)
        return GmailStatus(connected=False, automation_enabled=False)

    @application.get("/api/jobs/recheck", response_model=RecheckResult, tags=["jobs"])
    @application.post("/api/jobs/recheck", response_model=RecheckResult, tags=["jobs"])
    async def recheck_due_invoices(
        request: Request,
        repo: RepositoryDependency,
        authorization: Annotated[str | None, Header()] = None,
    ) -> RecheckResult:
        verifier: SchedulerVerifier = request.app.state.scheduler_verifier
        verifier.verify(authorization)
        workflow = workflow_from(request)
        workflow.reconcile_stale_actions()
        claimed = repo.claim_due_invoices(
            datetime.now(UTC), runtime_settings.scheduler_batch_size, 10
        )
        semaphore = asyncio.Semaphore(runtime_settings.scheduler_concurrency)

        async def run_one(tenant: TenantContext, invoice: Invoice) -> bool:
            async with semaphore:
                try:
                    await workflow.evaluate(tenant, invoice.id)
                    return True
                except Exception:
                    structlog.get_logger("scheduler").exception(
                        "scheduled_evaluation_failed",
                        invoice_id=invoice.id,
                        category="scheduler_failure",
                    )
                    return False

        results = await asyncio.gather(*(run_one(tenant, invoice) for tenant, invoice in claimed))
        succeeded = sum(1 for result in results if result)
        structlog.get_logger("scheduler").info(
            "scheduler_run_completed",
            category="scheduler_success",
            claimed=len(claimed),
            succeeded=succeeded,
            failed=len(results) - succeeded,
        )
        return RecheckResult(
            claimed=len(claimed),
            evaluated=len(results),
            succeeded=succeeded,
            failed=len(results) - succeeded,
        )

    @application.get("/api/admin/businesses", response_model=BusinessPage, tags=["admin"])
    def admin_businesses(
        user: UserDependency,
        repo: RepositoryDependency,
        limit: Annotated[int, Query(ge=1, le=100)] = 25,
        cursor: Annotated[str | None, Query(max_length=300)] = None,
    ) -> BusinessPage:
        require_admin(user, runtime_settings)
        items, next_cursor = repo.list_businesses_page(limit, cursor)
        return BusinessPage(items=items, next_cursor=next_cursor)

    @application.post(
        "/api/admin/businesses/{business_id}/classification",
        response_model=Business,
        tags=["admin"],
    )
    def classify_business(
        business_id: str,
        payload: BusinessClassificationUpdate,
        user: UserDependency,
        repo: RepositoryDependency,
    ) -> Business:
        require_admin(user, runtime_settings)
        return repo.classify_business(
            business_id, payload.data_classification, payload.relationship
        )

    @application.get(
        "/api/admin/evidence-ledger", response_model=EvidenceLedgerPage, tags=["admin"]
    )
    def evidence_ledger(
        user: UserDependency,
        repo: RepositoryDependency,
        limit: Annotated[int, Query(ge=1, le=100)] = 25,
        cursor: Annotated[str | None, Query(max_length=300)] = None,
    ) -> EvidenceLedgerPage:
        require_admin(user, runtime_settings)
        items, next_cursor = repo.list_ledger_entries_page(limit, cursor)
        return EvidenceLedgerPage(items=items, next_cursor=next_cursor)

    @application.post(
        "/api/admin/evidence-ledger", response_model=EvidenceLedgerEntry, tags=["admin"]
    )
    def create_evidence_ledger_entry(
        payload: EvidenceLedgerCreate,
        user: UserDependency,
        repo: RepositoryDependency,
    ) -> EvidenceLedgerEntry:
        require_admin(user, runtime_settings)
        if payload.kind.value == "PRODUCT_REVENUE" and not payload.business_id:
            raise ApiError(422, "revenue_business_required", "Revenue must link to a business.")
        if payload.business_id:
            repo.get_business_by_id(payload.business_id)
        amount_minor = decimal_to_minor(payload.amount_decimal, payload.currency)
        if payload.reversal_of:
            original = next(
                (entry for entry in repo.list_ledger_entries() if entry.id == payload.reversal_of),
                None,
            )
            if original is None:
                raise ApiError(
                    422, "reversal_not_found", "The original ledger entry was not found."
                )
            if any(entry.reversal_of == original.id for entry in repo.list_ledger_entries()):
                raise ApiError(409, "already_reversed", "The ledger entry is already reversed.")
            if (
                original.kind != payload.kind
                or original.currency != payload.currency
                or original.amount_minor != amount_minor
            ):
                raise ApiError(
                    422, "invalid_reversal", "A reversal must exactly match the original."
                )
        entry = EvidenceLedgerEntry(
            id=f"ledger_{uuid4().hex}",
            kind=payload.kind,
            amount_minor=amount_minor,
            currency=payload.currency,
            occurred_on=payload.occurred_on,
            category=payload.category,
            reference=payload.reference,
            business_id=payload.business_id,
            marketing=payload.marketing,
            reversal_of=payload.reversal_of,
            created_by=user.uid,
            created_at=datetime.now(UTC),
        )
        return repo.create_ledger_entry(entry)

    @application.get("/api/admin/validation/prospects", response_model=ProspectPage, tags=["admin"])
    def list_validation_prospects(
        user: UserDependency,
        repo: RepositoryDependency,
        limit: Annotated[int, Query(ge=1, le=100)] = 25,
        cursor: Annotated[str | None, Query(max_length=300)] = None,
    ) -> ProspectPage:
        require_admin(user, runtime_settings)
        items, next_cursor = repo.list_prospects(limit, cursor)
        return ProspectPage(items=items, next_cursor=next_cursor)

    @application.post("/api/admin/validation/prospects", response_model=Prospect, tags=["admin"])
    def create_validation_prospect(
        payload: ProspectCreate,
        user: UserDependency,
        repo: RepositoryDependency,
    ) -> Prospect:
        require_admin(user, runtime_settings)
        if payload.linked_business_id:
            repo.get_business_by_id(payload.linked_business_id)
        now = datetime.now(UTC)
        prospect = Prospect(
            id=f"prospect_{uuid4().hex}",
            **payload.model_dump(),
            created_by=user.uid,
            created_at=now,
            updated_at=now,
        )
        return repo.create_prospect(prospect)

    @application.patch(
        "/api/admin/validation/prospects/{prospect_id}",
        response_model=Prospect,
        tags=["admin"],
    )
    def update_validation_prospect(
        prospect_id: str,
        payload: ProspectUpdate,
        user: UserDependency,
        repo: RepositoryDependency,
    ) -> Prospect:
        require_admin(user, runtime_settings)
        changes = payload.model_dump(exclude_unset=True)
        current = repo.get_prospect(prospect_id)
        if changes.get("status") is not None:
            validate_prospect_transition(current.status, ProspectStatus(changes["status"]))
        if changes.get("linked_business_id"):
            repo.get_business_by_id(str(changes["linked_business_id"]))
        if not changes:
            return current
        return repo.update_prospect(prospect_id, changes)

    @application.get(
        "/api/admin/validation/prospects/{prospect_id}/interviews",
        response_model=InterviewPage,
        tags=["admin"],
    )
    def list_validation_interviews(
        prospect_id: str,
        user: UserDependency,
        repo: RepositoryDependency,
        limit: Annotated[int, Query(ge=1, le=100)] = 25,
        cursor: Annotated[str | None, Query(max_length=300)] = None,
    ) -> InterviewPage:
        require_admin(user, runtime_settings)
        items, next_cursor = repo.list_interviews(prospect_id, limit, cursor)
        return InterviewPage(items=items, next_cursor=next_cursor)

    @application.post(
        "/api/admin/validation/prospects/{prospect_id}/interviews",
        response_model=Interview,
        tags=["admin"],
    )
    def create_validation_interview(
        prospect_id: str,
        payload: InterviewCreate,
        user: UserDependency,
        repo: RepositoryDependency,
    ) -> Interview:
        require_admin(user, runtime_settings)
        prospect = repo.get_prospect(prospect_id)
        validate_prospect_transition(prospect.status, ProspectStatus.INTERVIEWED)
        return repo.create_interview(
            Interview(
                id=f"interview_{uuid4().hex}",
                prospect_id=prospect_id,
                **payload.model_dump(),
                recorded_by=user.uid,
                created_at=datetime.now(UTC),
            )
        )

    @application.get("/api/admin/founder-plans", response_model=FounderPlanPage, tags=["admin"])
    def admin_founder_plans(
        user: UserDependency,
        repo: RepositoryDependency,
        limit: Annotated[int, Query(ge=1, le=100)] = 25,
        cursor: Annotated[str | None, Query(max_length=300)] = None,
    ) -> FounderPlanPage:
        require_admin(user, runtime_settings)
        items, next_cursor = repo.list_founder_plans(limit, cursor)
        return FounderPlanPage(items=items, next_cursor=next_cursor)

    @application.post(
        "/api/admin/founder-plans/activate",
        response_model=FounderPlanEnrollment,
        tags=["admin"],
    )
    def activate_founder_plan(
        payload: FounderPlanActivate,
        user: UserDependency,
        repo: RepositoryDependency,
    ) -> FounderPlanEnrollment:
        require_admin(user, runtime_settings)
        business = repo.get_business_by_id(payload.business_id)
        if (
            business.data_classification.value != "REAL"
            or business.relationship.value == "UNCLASSIFIED"
        ):
            raise ApiError(
                422,
                "business_classification_required",
                "Classify the business as real and set its relationship before activation.",
            )
        now = datetime.now(UTC)
        ledger_digest = hashlib.sha256(payload.idempotency_key.encode()).hexdigest()[:24]
        ledger_id = f"ledger_plan_{ledger_digest}"
        enrollment = FounderPlanEnrollment(
            id=f"plan_{business.id}",
            business_id=business.id,
            status=FounderPlanStatus.ACTIVE,
            invoices_used=0,
            ledger_entry_id=ledger_id,
            receipt_reference=payload.receipt_reference.strip(),
            idempotency_key=payload.idempotency_key,
            activated_by=user.uid,
            activated_at=now,
        )
        ledger = EvidenceLedgerEntry(
            id=ledger_id,
            kind="PRODUCT_REVENUE",
            amount_minor=29_900,
            currency="INR",
            occurred_on=payload.paid_on,
            category="Founder Recovery Plan",
            reference=payload.receipt_reference.strip(),
            business_id=business.id,
            marketing=False,
            reversal_of=None,
            created_by=user.uid,
            created_at=now,
        )
        activated = repo.activate_founder_plan(enrollment, ledger)
        manual_order = repo.save_billing_order(
            BillingOrder(
                id=f"billord_manual_{business.id}",
                business_id=business.id,
                provider=BillingProvider.MANUAL,
                provider_order_id=None,
                receipt=ledger_id[:40],
                idempotency_key=payload.idempotency_key,
                status=BillingOrderStatus.PAID,
                created_by=user.uid,
                created_at=now,
                updated_at=now,
            )
        )
        manual_transaction = BillingTransaction(
            id=f"manual_{ledger_digest}",
            business_id=business.id,
            billing_order_id=manual_order.id,
            provider=BillingProvider.MANUAL,
            provider_payment_id=f"manual_{ledger_digest}",
            provider_order_id=None,
            amount_minor=FOUNDER_PLAN_AMOUNT_MINOR,
            currency=FOUNDER_PLAN_CURRENCY,
            status=BillingTransactionStatus.CAPTURED,
            captured_at=now,
            created_at=now,
            updated_at=now,
        )
        repo.record_billing_transaction(manual_order, manual_transaction, None, None)
        return activated

    @application.get("/api/admin/evidence-export", tags=["admin"])
    def admin_evidence_export(user: UserDependency, repo: RepositoryDependency) -> Response:
        require_admin(user, runtime_settings)
        enforce_rate_limit(repo, "platform", "evidence_export", 3, 3600)
        archive = build_evidence_zip(repo, runtime_settings.export_record_limit)
        return Response(
            content=archive,
            media_type="application/zip",
            headers={"Content-Disposition": 'attachment; filename="cashsathi-evidence.zip"'},
        )

    @application.get("/api/admin/revenue/summary", response_model=RevenueSummary, tags=["admin"])
    def revenue_summary(
        user: UserDependency,
        repo: RepositoryDependency,
        from_date: date | None = None,
        to_date: date | None = None,
        status: BillingTransactionStatus | None = None,
        payment_method: Annotated[str | None, Query(max_length=50)] = None,
        data_classification: DataClassification | None = None,
        relationship: BusinessRelationship | None = None,
        q: Annotated[str | None, Query(max_length=100)] = None,
    ) -> RevenueSummary:
        require_admin(user, runtime_settings)
        transactions = _filter_revenue_transactions(
            repo,
            from_date=from_date,
            to_date=to_date,
            status=status,
            payment_method=payment_method,
            data_classification=data_classification,
            relationship=relationship,
            query=q,
        )
        captured_states = {
            BillingTransactionStatus.CAPTURED,
            BillingTransactionStatus.PARTIALLY_REFUNDED,
            BillingTransactionStatus.REFUNDED,
        }
        captured = [item for item in transactions if item.status in captured_states]
        gross = sum(item.amount_minor for item in captured)
        refunded = sum(item.amount_refunded_minor for item in captured)
        plan_business_ids = {item.business_id for item in transactions if item.business_id}
        plans = [
            plan
            for plan in _all_founder_plans(repo)
            if not plan_business_ids or plan.business_id in plan_business_ids
        ]
        return RevenueSummary(
            gross_captured_minor=gross,
            refunded_minor=refunded,
            net_captured_minor=gross - refunded,
            provider_fees_minor=sum(item.provider_fee_minor for item in captured),
            provider_tax_minor=sum(item.provider_tax_minor for item in captured),
            paying_businesses=len(
                {
                    item.business_id
                    for item in captured
                    if item.business_id and item.amount_minor > item.amount_refunded_minor
                }
            ),
            capture_rate=(len(captured) / len(transactions) if transactions else 0),
            active_plans=sum(1 for plan in plans if plan.status == FounderPlanStatus.ACTIVE),
            exhausted_plans=sum(1 for plan in plans if plan.status == FounderPlanStatus.EXHAUSTED),
            refunded_plans=sum(1 for plan in plans if plan.status == FounderPlanStatus.REFUNDED),
        )

    @application.get(
        "/api/admin/revenue/customers", response_model=RevenueCustomerPage, tags=["admin"]
    )
    def revenue_customers(
        user: UserDependency,
        repo: RepositoryDependency,
        from_date: date | None = None,
        to_date: date | None = None,
        status: BillingTransactionStatus | None = None,
        payment_method: Annotated[str | None, Query(max_length=50)] = None,
        data_classification: DataClassification | None = None,
        relationship: BusinessRelationship | None = None,
        q: Annotated[str | None, Query(max_length=100)] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        cursor: Annotated[str | None, Query(max_length=20)] = None,
    ) -> RevenueCustomerPage:
        require_admin(user, runtime_settings)
        transactions = _filter_revenue_transactions(
            repo,
            from_date=from_date,
            to_date=to_date,
            status=status,
            payment_method=payment_method,
            data_classification=data_classification,
            relationship=relationship,
            query=q,
        )
        by_business: dict[str, list[BillingTransaction]] = defaultdict(list)
        for transaction in transactions:
            if transaction.business_id:
                by_business[transaction.business_id].append(transaction)
        plans = _all_founder_plans(repo)
        plans_by_business = {plan.business_id: plan for plan in plans}
        normalized_query = q.strip().casefold() if q else None
        items: list[RevenueCustomer] = []
        filters_transactions = any(
            [from_date, to_date, status, payment_method, data_classification, relationship]
        )
        for business in repo.list_businesses():
            business_transactions = by_business.get(business.id, [])
            if filters_transactions and not business_transactions:
                continue
            latest = business_transactions[0] if business_transactions else None
            payer_email = next(
                (item.payer_email for item in business_transactions if item.payer_email), None
            )
            if (
                normalized_query
                and normalized_query
                not in " ".join(
                    [
                        business.name,
                        str(payer_email or ""),
                        *(item.provider_payment_id for item in business_transactions),
                    ]
                ).casefold()
            ):
                continue
            captured = [
                item
                for item in business_transactions
                if item.status
                in {
                    BillingTransactionStatus.CAPTURED,
                    BillingTransactionStatus.PARTIALLY_REFUNDED,
                    BillingTransactionStatus.REFUNDED,
                }
            ]
            plan = plans_by_business.get(business.id)
            items.append(
                RevenueCustomer(
                    business_id=business.id,
                    business_name=business.name,
                    payer_email=payer_email,
                    data_classification=business.data_classification,
                    relationship=business.relationship,
                    payment_status=latest.status if latest else None,
                    payment_method=latest.payment_method if latest else None,
                    captured_at=latest.captured_at if latest else None,
                    gross_captured_minor=sum(item.amount_minor for item in captured),
                    refunded_minor=sum(item.amount_refunded_minor for item in captured),
                    plan_status=plan.status if plan else None,
                    invoices_used=plan.invoices_used if plan else 0,
                    invoice_limit=plan.invoice_limit if plan else 10,
                )
            )
        items.sort(
            key=lambda item: item.captured_at or datetime.min.replace(tzinfo=UTC), reverse=True
        )
        try:
            start = int(cursor or "0")
        except ValueError:
            raise ApiError(422, "invalid_cursor", "The pagination cursor is invalid.") from None
        selected = items[start : start + limit]
        next_cursor = str(start + limit) if start + limit < len(items) else None
        return RevenueCustomerPage(items=selected, next_cursor=next_cursor)

    @application.get(
        "/api/admin/revenue/payments", response_model=RevenuePaymentPage, tags=["admin"]
    )
    def revenue_payments(
        user: UserDependency,
        repo: RepositoryDependency,
        from_date: date | None = None,
        to_date: date | None = None,
        status: BillingTransactionStatus | None = None,
        payment_method: Annotated[str | None, Query(max_length=50)] = None,
        data_classification: DataClassification | None = None,
        relationship: BusinessRelationship | None = None,
        q: Annotated[str | None, Query(max_length=100)] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        cursor: Annotated[str | None, Query(max_length=20)] = None,
    ) -> RevenuePaymentPage:
        require_admin(user, runtime_settings)
        payments = _filter_revenue_transactions(
            repo,
            from_date=from_date,
            to_date=to_date,
            status=status,
            payment_method=payment_method,
            data_classification=data_classification,
            relationship=relationship,
            query=q,
        )
        try:
            start = int(cursor or "0")
        except ValueError:
            raise ApiError(422, "invalid_cursor", "The pagination cursor is invalid.") from None
        selected = payments[start : start + limit]
        return RevenuePaymentPage(
            items=selected,
            next_cursor=str(start + limit) if start + limit < len(payments) else None,
        )

    @application.get(
        "/api/admin/revenue/payments/{payment_id}",
        response_model=RevenuePaymentDetail,
        tags=["admin"],
    )
    def revenue_payment_detail(
        payment_id: str, user: UserDependency, repo: RepositoryDependency
    ) -> RevenuePaymentDetail:
        require_admin(user, runtime_settings)
        payment = repo.get_billing_transaction(payment_id)
        if payment is None:
            raise ApiError(404, "billing_payment_not_found", "The billing payment was not found.")
        order = next(
            (
                item
                for item in repo.list_billing_orders(payment.business_id)
                if item.id == payment.billing_order_id
            ),
            None,
        )
        business = repo.get_business_by_id(payment.business_id) if payment.business_id else None
        plan = repo.get_founder_plan(payment.business_id) if payment.business_id else None
        return RevenuePaymentDetail(payment=payment, order=order, business=business, plan=plan)

    @application.get("/api/admin/revenue/export.csv", tags=["admin"])
    def revenue_export(
        user: UserDependency,
        repo: RepositoryDependency,
        from_date: date | None = None,
        to_date: date | None = None,
        status: BillingTransactionStatus | None = None,
        payment_method: Annotated[str | None, Query(max_length=50)] = None,
        data_classification: DataClassification | None = None,
        relationship: BusinessRelationship | None = None,
        q: Annotated[str | None, Query(max_length=100)] = None,
    ) -> Response:
        require_admin(user, runtime_settings)
        transactions = _filter_revenue_transactions(
            repo,
            from_date=from_date,
            to_date=to_date,
            status=status,
            payment_method=payment_method,
            data_classification=data_classification,
            relationship=relationship,
            query=q,
        )
        businesses = {business.id: business for business in repo.list_businesses()}
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(
            [
                "payment_id",
                "business",
                "payer_email",
                "status",
                "method",
                "amount_minor",
                "refunded_minor",
                "fee_minor",
                "tax_minor",
                "currency",
                "captured_at",
            ]
        )
        for transaction in transactions:
            business = businesses.get(transaction.business_id or "")
            writer.writerow(
                [
                    transaction.provider_payment_id,
                    business.name if business else "Deleted account",
                    str(transaction.payer_email or ""),
                    transaction.status.value,
                    transaction.payment_method or "",
                    transaction.amount_minor,
                    transaction.amount_refunded_minor,
                    transaction.provider_fee_minor,
                    transaction.provider_tax_minor,
                    transaction.currency,
                    transaction.captured_at.isoformat() if transaction.captured_at else "",
                ]
            )
        return Response(
            content=output.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": 'attachment; filename="cashsathi-revenue.csv"'},
        )

    @application.get("/api/admin/impact", response_model=AdminImpactResponse, tags=["admin"])
    def platform_impact(user: UserDependency, repo: RepositoryDependency) -> AdminImpactResponse:
        require_admin(user, runtime_settings)
        return admin_impact(repo)

    return application


app = create_app()
