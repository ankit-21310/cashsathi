from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Annotated
from uuid import uuid4

import structlog
from fastapi import Depends, FastAPI, File, Query, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware

from cashsathi_api.auth import AuthVerifier, FirebaseAuthVerifier, authenticated_user
from cashsathi_api.config import Settings, get_settings
from cashsathi_api.decisioning import (
    DecisionAdapter,
    DecisionSchemaFailure,
    DecisionTransportFailure,
    DecisionUnavailableError,
    GeminiDecisionAdapter,
)
from cashsathi_api.domain import (
    Action,
    ActionState,
    AgentDecision,
    AgentRun,
    AgentRunPage,
    AgentRunStatus,
    AuthenticatedUser,
    Business,
    BusinessCreate,
    ConsentAccept,
    ConsentStatus,
    CustomerSnapshot,
    ErrorEnvelope,
    EvaluationResult,
    ExtractionResult,
    HealthResponse,
    Invoice,
    InvoiceConfirm,
    InvoiceDetail,
    InvoicePage,
    InvoiceSummary,
    MeResponse,
    ModelDecision,
    PolicyOutcome,
    PolicyResult,
    TenantContext,
)
from cashsathi_api.errors import (
    ApiError,
    api_error_handler,
    unhandled_error_handler,
    validation_error_handler,
)
from cashsathi_api.invoice_processing import (
    ExtractionUnavailableError,
    GeminiInvoiceExtractor,
    InvoiceExtractor,
    validate_pdf,
)
from cashsathi_api.money import decimal_to_minor
from cashsathi_api.observability import RequestContextMiddleware, configure_logging
from cashsathi_api.policy import evaluate_policy
from cashsathi_api.repository import FirestoreRepository, Repository
from cashsathi_api.state_engine import calculate_invoice_state

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
) -> FastAPI:
    runtime_settings = settings or get_settings()
    configure_logging(runtime_settings)

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        application.state.repository = repository or FirestoreRepository(runtime_settings)
        application.state.auth_verifier = auth_verifier or FirebaseAuthVerifier(runtime_settings)
        if invoice_extractor is not None:
            application.state.invoice_extractor = invoice_extractor
        else:
            try:
                application.state.invoice_extractor = GeminiInvoiceExtractor(runtime_settings)
            except ExtractionUnavailableError:
                application.state.invoice_extractor = None
        if decision_adapter is not None:
            application.state.decision_adapter = decision_adapter
        else:
            try:
                application.state.decision_adapter = GeminiDecisionAdapter(runtime_settings)
            except DecisionUnavailableError:
                application.state.decision_adapter = None
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
        invoice = repo.get_invoice(tenant, invoice_id)
        actions = repo.list_actions(tenant, invoice.id)
        state = calculate_invoice_state(invoice, actions)
        adapter: DecisionAdapter | None = request.app.state.decision_adapter
        if adapter is None:
            raise ApiError(503, "gemini_not_configured", "Agent decisioning is not configured.")
        run_id = f"run_{uuid4().hex}"
        created_at = datetime.now(UTC)
        try:
            output = await run_in_threadpool(adapter.decide, invoice, state, actions)
        except DecisionSchemaFailure as failure:
            proposal = ModelDecision(
                decision=AgentDecision.REQUEST_HUMAN_REVIEW,
                rationale="Gemini returned invalid structured output twice.",
                risk_flags=["INVALID_MODEL_OUTPUT"],
                requires_human_approval=True,
            )
            policy_result = PolicyResult(
                outcome=PolicyOutcome.BLOCK,
                final_decision=AgentDecision.REQUEST_HUMAN_REVIEW,
                matched_rules=["invalid_model_output"],
                requires_approval=True,
                next_check_at=None,
                policy_version=runtime_settings.policy_version,
            )
            run = AgentRun(
                id=run_id,
                invoice_id=invoice.id,
                business_id=tenant.business_id,
                status=AgentRunStatus.HUMAN_REVIEW,
                invoice_state=state,
                model_proposal=proposal,
                policy_result=policy_result,
                model_id=adapter.model_id,
                prompt_version=adapter.prompt_version,
                attempt_count=failure.attempt_count,
                latency_ms=failure.latency_ms,
                failure_code="invalid_model_output",
                created_at=created_at,
            )
            repo.save_evaluation(tenant, run, None)
            return EvaluationResult(agent_run=run, action=None)
        except DecisionTransportFailure as failure:
            run = AgentRun(
                id=run_id,
                invoice_id=invoice.id,
                business_id=tenant.business_id,
                status=AgentRunStatus.FAILED,
                invoice_state=state,
                model_proposal=None,
                policy_result=None,
                model_id=adapter.model_id,
                prompt_version=adapter.prompt_version,
                attempt_count=1,
                latency_ms=failure.latency_ms,
                failure_code="model_transport_failure",
                created_at=created_at,
            )
            repo.save_evaluation(tenant, run, None)
            raise ApiError(
                503,
                "decision_unavailable",
                "Agent decisioning is temporarily unavailable. No action was created.",
            ) from None

        policy_result = evaluate_policy(
            invoice=invoice,
            invoice_state=state,
            proposal=output.proposal,
            settings=repo.get_policy_settings(tenant),
            actions=actions,
            policy_version=runtime_settings.policy_version,
        )
        action = None
        if policy_result.final_decision == AgentDecision.SEND_REMINDER:
            action = Action(
                id=f"act_{uuid4().hex}",
                invoice_id=invoice.id,
                agent_run_id=run_id,
                state=(
                    ActionState.AWAITING_APPROVAL
                    if policy_result.requires_approval
                    else ActionState.PROPOSED
                ),
                created_at=created_at,
            )
        run = AgentRun(
            id=run_id,
            invoice_id=invoice.id,
            business_id=tenant.business_id,
            status=AgentRunStatus.SUCCEEDED,
            invoice_state=state,
            model_proposal=output.proposal,
            policy_result=policy_result,
            model_id=adapter.model_id,
            prompt_version=adapter.prompt_version,
            attempt_count=output.attempt_count,
            latency_ms=output.latency_ms,
            input_tokens=output.input_tokens,
            output_tokens=output.output_tokens,
            created_at=created_at,
        )
        repo.save_evaluation(tenant, run, action)
        return EvaluationResult(agent_run=run, action=action)

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

    return application


app = create_app()
