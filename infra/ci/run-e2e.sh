#!/usr/bin/env bash
set -euo pipefail

api_port="${CASH_SATHI_E2E_API_PORT:-8000}"
web_port="${CASH_SATHI_E2E_WEB_PORT:-3000}"

export APP_ENV=development
export GCP_PROJECT_ID=cashsathi-local
export FIREBASE_AUTH_EMULATOR_HOST=127.0.0.1:9099
export FIRESTORE_EMULATOR_HOST=127.0.0.1:8080
export CORS_ALLOWED_ORIGINS="http://127.0.0.1:${web_port}"
export WEB_BASE_URL="http://127.0.0.1:${web_port}"
export GMAIL_OAUTH_REDIRECT_URI="http://127.0.0.1:${api_port}/api/integrations/gmail/callback"
export PLATFORM_ADMIN_UIDS=demo-owner-a
export NEXT_PUBLIC_API_BASE_URL="http://127.0.0.1:${api_port}"
export NEXT_DIST_DIR=.next-e2e

(cd services/api && .venv/bin/python -m pytest -m firestore)
services/api/.venv/bin/uvicorn cashsathi_api.main:app --host 127.0.0.1 --port "${api_port}" >/tmp/cashsathi-api.log 2>&1 &
api_pid=$!
trap 'kill ${api_pid} ${web_pid:-} 2>/dev/null || true' EXIT

npm run seed
npm run build:web
(cd apps/web && exec node node_modules/next/dist/bin/next start --hostname 127.0.0.1 --port "${web_port}") >/tmp/cashsathi-web.log 2>&1 &
web_pid=$!

api_ready=0
for _ in $(seq 1 40); do
  if curl --fail --silent "http://127.0.0.1:${api_port}/readyz" >/dev/null; then api_ready=1; break; fi
  sleep 1
done
if [[ ${api_ready} -ne 1 ]]; then
  cat /tmp/cashsathi-api.log || true
  echo "API did not become ready" >&2
  exit 1
fi
web_ready=0
for _ in $(seq 1 40); do
  if curl --fail --silent "http://127.0.0.1:${web_port}" >/dev/null; then web_ready=1; break; fi
  sleep 1
done
if [[ ${web_ready} -ne 1 ]]; then
  cat /tmp/cashsathi-web.log || true
  echo "Web app did not become ready" >&2
  exit 1
fi

if ! E2E_FIREBASE=1 E2E_BASE_URL="http://127.0.0.1:${web_port}" npm run test:e2e --workspace=web; then
  cat /tmp/cashsathi-api.log || true
  cat /tmp/cashsathi-web.log || true
  exit 1
fi
