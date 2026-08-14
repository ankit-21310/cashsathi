from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import structlog
from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from cashsathi_api.config import Settings


class RequestGuardsMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: object, settings: Settings) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self._settings = settings

    def _timeout(self, path: str) -> int:
        if path == "/api/jobs/recheck":
            return self._settings.scheduler_request_timeout_seconds
        if path == "/api/invoices/extract" or path.endswith("/evaluate") or "/actions/" in path:
            return self._settings.ai_request_timeout_seconds
        return self._settings.default_request_timeout_seconds

    @staticmethod
    def _error(request: Request, status: int, code: str, message: str) -> JSONResponse:
        request_id = str(getattr(request.state, "request_id", "unavailable"))
        return JSONResponse(
            status_code=status,
            content={
                "error": {
                    "code": code,
                    "message": message,
                    "request_id": request_id,
                    "details": None,
                }
            },
        )

    def _security_headers(self, response: Response, production: bool) -> None:
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Cache-Control"] = "no-store"
        allowed_origins = " ".join(self._settings.cors_origins)
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; "
            f"connect-src 'self' {allowed_origins}"
        )
        if production:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        content_type = request.headers.get("content-type", "").lower()
        stream_limit: int | None = None
        error_code = "request_too_large"
        error_message = "The request exceeds the size limit."
        if content_type.startswith("application/json"):
            stream_limit = self._settings.max_json_bytes
            error_message = "The JSON request exceeds the size limit."
        elif request.url.path == "/api/invoices/extract" and content_type.startswith(
            "multipart/form-data"
        ):
            stream_limit = self._settings.max_pdf_bytes + 64 * 1024
            error_code = "pdf_too_large"
            error_message = "The PDF upload exceeds the size limit."
        if stream_limit is not None:
            length = request.headers.get("content-length")
            if length and length.isdigit() and int(length) > stream_limit:
                response = self._error(request, 413, error_code, error_message)
                self._security_headers(response, self._settings.app_env == "production")
                return response
            chunks: list[bytes] = []
            streamed_bytes = 0
            async for chunk in request.stream():
                streamed_bytes += len(chunk)
                if streamed_bytes > stream_limit:
                    response = self._error(
                        request,
                        413,
                        error_code,
                        error_message,
                    )
                    self._security_headers(response, self._settings.app_env == "production")
                    return response
                chunks.append(chunk)
            request._body = b"".join(chunks)

        guarded_response: Response
        try:
            guarded_response = await asyncio.wait_for(
                call_next(request), timeout=self._timeout(request.url.path)
            )
        except TimeoutError:
            structlog.get_logger("http").error(
                "request_timeout", path=request.url.path, category="api_timeout"
            )
            guarded_response = self._error(
                request, 504, "request_timeout", "The request exceeded its execution deadline."
            )
        self._security_headers(guarded_response, self._settings.app_env == "production")
        return guarded_response
