from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

import structlog
from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware

from cashsathi_api.auth import AuthVerifier, FirebaseAuthVerifier, authenticated_user
from cashsathi_api.config import Settings, get_settings
from cashsathi_api.domain import (
    AuthenticatedUser,
    Business,
    BusinessCreate,
    ErrorEnvelope,
    HealthResponse,
    MeResponse,
)
from cashsathi_api.errors import (
    ApiError,
    api_error_handler,
    unhandled_error_handler,
    validation_error_handler,
)
from cashsathi_api.observability import RequestContextMiddleware, configure_logging
from cashsathi_api.repository import FirestoreRepository, Repository


def repository_from(request: Request) -> Repository:
    return request.app.state.repository  # type: ignore[no-any-return]


RepositoryDependency = Annotated[Repository, Depends(repository_from)]
UserDependency = Annotated[AuthenticatedUser, Depends(authenticated_user)]


def create_app(
    *,
    settings: Settings | None = None,
    repository: Repository | None = None,
    auth_verifier: AuthVerifier | None = None,
) -> FastAPI:
    runtime_settings = settings or get_settings()
    configure_logging(runtime_settings)

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        application.state.repository = repository or FirestoreRepository(runtime_settings)
        application.state.auth_verifier = auth_verifier or FirebaseAuthVerifier(runtime_settings)
        yield

    application = FastAPI(
        title="CashSathi API",
        version="0.1.0",
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

    @application.get(
        "/api/me",
        response_model=MeResponse,
        responses={401: {"model": ErrorEnvelope}},
        tags=["account"],
    )
    def me(
        user: UserDependency,
        repo: RepositoryDependency,
    ) -> MeResponse:
        business, membership = repo.get_account(user)
        return MeResponse(
            uid=user.uid,
            email=user.email,
            display_name=user.display_name,
            business=business,
            membership=membership,
        )

    @application.post(
        "/api/businesses",
        response_model=Business,
        responses={401: {"model": ErrorEnvelope}},
        tags=["businesses"],
    )
    def create_business(
        payload: BusinessCreate,
        user: UserDependency,
        repo: RepositoryDependency,
    ) -> Business:
        business, _membership = repo.get_or_create_business(user, payload.name)
        return business

    @application.get(
        "/api/businesses/current",
        response_model=Business,
        responses={401: {"model": ErrorEnvelope}, 404: {"model": ErrorEnvelope}},
        tags=["businesses"],
    )
    def current_business(
        user: UserDependency,
        repo: RepositoryDependency,
    ) -> Business:
        tenant = repo.require_tenant(user)
        business, _membership = repo.get_account(user)
        if business is None or business.id != tenant.business_id:
            raise ApiError(
                403, "tenant_mismatch", "The authenticated tenant could not be resolved."
            )
        return business

    return application


app = create_app()
