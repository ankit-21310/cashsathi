# syntax=docker/dockerfile:1.7
FROM ghcr.io/astral-sh/uv:0.11.29 AS uv
FROM python:3.13-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app
COPY --from=uv /uv /uvx /bin/
COPY services/api/pyproject.toml services/api/uv.lock services/api/README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY services/api/src ./src
RUN uv sync --frozen --no-dev

RUN useradd --system --uid 10001 appuser && chown -R appuser:appuser /app
USER appuser

ENV PORT=8080
EXPOSE 8080
CMD ["uvicorn", "cashsathi_api.main:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers", "--forwarded-allow-ips=*"]
