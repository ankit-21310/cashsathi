from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import uuid4

import structlog
from fastapi import Depends, FastAPI, File, Header, Query, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

from cashsathi_api.auth import AuthVerifier, FirebaseAuthVerifier, authenticated_user
from cashsathi_api.config import Settings, get_settings
from cashsathi_api.decisioning import (
    DecisionAdapter,
    DecisionUnavailableError,
    GeminiDecisionAdapter,
)
from cashsathi_api.domain import (
    Action,
    ActionCancel,
    ActionPage,
    ActionResolve,
    ActionState,
    AdminImpactResponse,
    AgentRunPage,
    AuthenticatedUser,
    AutomationUpdate,
    Business,
    BusinessClassificationUpdate,
    BusinessCreate,
    ConsentAccept,
    ConsentStatus,
    CustomerSnapshot,
    ErrorEnvelope,
    EvaluationResult,
    EvidenceLedgerCreate,
    EvidenceLedgerEntry,
    ExtractionResult,
    GmailConnection,
    GmailConnectResponse,
    GmailOAuthState,
    GmailStatus,
    HealthResponse,
    Invoice,
    InvoiceConfirm,
    InvoiceDetail,
    InvoicePage,
    InvoiceSummary,
    InvoiceTimeline,
    InvoiceWorkflowStatus,
    MeResponse,
    MetricsResponse,
    Payment,
    PaymentCreate,
    PolicyDefaults,
    RecheckResult,
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
from cashsathi_api.repository import FirestoreRepository, Repository
from cashsathi_api.scheduler_auth import GoogleSchedulerVerifier, SchedulerVerifier
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


def _invoice_summary(repo: Repository, tenant: TenantContext, invoice: Invoice) -> InvoiceSummary:
    # TenantContext is intentionally carried by repository APIs and never supplied by the client.
    actions = repo.list_actions(tenant, invoice.id)
    return InvoiceSummary(
        id=invoice.id,
        invoice_number=invoice.invoice_number,
        customer_name=invoice.customer.name,
        amount_minor=invoice.amount_minor,
        currency=invoice.currency,
        due_date=invoice.due_date,
        current_state=calculate_invoice_state(invoice, actions),
        created_at=invoice.created_at,
    )


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
) -> FastAPI:
    runtime_settings = settings or get_settings()
    configure_logging(runtime_settings)

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        application.state.repository = repository or FirestoreRepository(runtime_settings)
        application.state.auth_verifier = auth_verifier or FirebaseAuthVerifier(runtime_settings)
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
                application.state.token_cipher = GoogleKmsTokenCipher(runtime_settings)
            except GmailUnavailableError:
                application.state.token_cipher = None
        application.state.scheduler_verifier = scheduler_verifier or GoogleSchedulerVerifier(
            runtime_settings
        )
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
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
        expose_headers=["X-Request-ID"],
    )
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
        )

    @application.post("/api/businesses", response_model=Business, tags=["businesses"])
    def create_business(
        payload: BusinessCreate, user: UserDependency, repo: RepositoryDependency
    ) -> Business:
        business, _membership = repo.get_or_create_business(user, payload.name)
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

    @application.post("/api/invoices/extract", response_model=ExtractionResult, tags=["invoices"])
    async def extract_invoice(
        request: Request,
        user: UserDependency,
        repo: RepositoryDependency,
        file: Annotated[UploadFile, File()],
    ) -> ExtractionResult:
        tenant = repo.require_tenant(user)
        if repo.get_consent(tenant, PRODUCT_CONSENT_VERSION) is None:
            raise ApiError(
                403,
                "consent_required",
                "Accept the current product-processing consent before uploading an invoice.",
            )
        try:
            data = await file.read(runtime_settings.max_pdf_bytes + 1)
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
        return InvoicePage(
            items=[_invoice_summary(repo, tenant, invoice) for invoice in invoices],
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
        return InvoiceDetail(
            invoice=invoice,
            current_state=calculate_invoice_state(invoice, actions),
            latest_agent_run=runs[0] if runs else None,
            latest_action=actions[0] if actions else None,
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
        return ActionPage(items=actions, next_cursor=next_cursor)

    @application.post("/api/actions/{action_id}/approve", response_model=Action, tags=["actions"])
    async def approve_action(
        action_id: str, request: Request, user: UserDependency, repo: RepositoryDependency
    ) -> Action:
        return await workflow_from(request).approve(repo.require_tenant(user), action_id)

    @application.post("/api/actions/{action_id}/cancel", response_model=Action, tags=["actions"])
    def cancel_action(
        action_id: str,
        payload: ActionCancel,
        request: Request,
        user: UserDependency,
        repo: RepositoryDependency,
    ) -> Action:
        return workflow_from(request).cancel(repo.require_tenant(user), action_id, payload)

    @application.post("/api/actions/{action_id}/retry", response_model=Action, tags=["actions"])
    async def retry_action(
        action_id: str, request: Request, user: UserDependency, repo: RepositoryDependency
    ) -> Action:
        return await workflow_from(request).retry(repo.require_tenant(user), action_id)

    @application.post("/api/actions/{action_id}/resolve", response_model=Action, tags=["actions"])
    def resolve_action(
        action_id: str,
        payload: ActionResolve,
        request: Request,
        user: UserDependency,
        repo: RepositoryDependency,
    ) -> Action:
        return workflow_from(request).resolve(repo.require_tenant(user), action_id, payload)

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

    @application.get("/api/settings/automation", response_model=PolicyDefaults, tags=["settings"])
    def automation_settings(user: UserDependency, repo: RepositoryDependency) -> PolicyDefaults:
        return repo.get_policy_settings(repo.require_tenant(user))

    @application.post("/api/settings/automation", response_model=PolicyDefaults, tags=["settings"])
    def update_automation(
        payload: AutomationUpdate, user: UserDependency, repo: RepositoryDependency
    ) -> PolicyDefaults:
        return repo.update_automation(repo.require_tenant(user), payload.enabled)

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
        tenant = callback_repo.require_tenant(AuthenticatedUser(oauth_state.user_id, None, None))
        if tenant.business_id != oauth_state.business_id:
            raise ApiError(400, "oauth_tenant_mismatch", "The Gmail connection tenant changed.")
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
        repo.disconnect_gmail(tenant)
        repo.update_automation(tenant, False)
        return GmailStatus(connected=False, automation_enabled=False)

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
                        "scheduled_evaluation_failed", invoice_id=invoice.id
                    )
                    return False

        results = await asyncio.gather(*(run_one(tenant, invoice) for tenant, invoice in claimed))
        succeeded = sum(1 for result in results if result)
        return RecheckResult(
            claimed=len(claimed),
            evaluated=len(results),
            succeeded=succeeded,
            failed=len(results) - succeeded,
        )

    @application.get("/api/admin/businesses", response_model=list[Business], tags=["admin"])
    def admin_businesses(user: UserDependency, repo: RepositoryDependency) -> list[Business]:
        require_admin(user, runtime_settings)
        return repo.list_businesses()

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
        "/api/admin/evidence-ledger", response_model=list[EvidenceLedgerEntry], tags=["admin"]
    )
    def evidence_ledger(
        user: UserDependency, repo: RepositoryDependency
    ) -> list[EvidenceLedgerEntry]:
        require_admin(user, runtime_settings)
        return repo.list_ledger_entries()

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

    @application.get("/api/admin/impact", response_model=AdminImpactResponse, tags=["admin"])
    def platform_impact(user: UserDependency, repo: RepositoryDependency) -> AdminImpactResponse:
        require_admin(user, runtime_settings)
        return admin_impact(repo)

    return application


app = create_app()
