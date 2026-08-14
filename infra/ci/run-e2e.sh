#!/usr/bin/env bash
set -euo pipefail

export APP_ENV=development
export GCP_PROJECT_ID=cashsathi-local
export FIREBASE_AUTH_EMULATOR_HOST=127.0.0.1:9099
export FIRESTORE_EMULATOR_HOST=127.0.0.1:8080
export CORS_ALLOWED_ORIGINS=http://127.0.0.1:3000
export WEB_BASE_URL=http://127.0.0.1:3000
export GMAIL_OAUTH_REDIRECT_URI=http://127.0.0.1:8000/api/integrations/gmail/callback
export PLATFORM_ADMIN_UIDS=demo-owner-a

services/api/.venv/bin/uvicorn cashsathi_api.main:app --host 127.0.0.1 --port 8000 >/tmp/cashsathi-api.log 2>&1 &
api_pid=$!
trap 'kill ${api_pid} ${web_pid:-} 2>/dev/null || true' EXIT

npm run seed
npm run build:web
(cd apps/web && exec node node_modules/next/dist/bin/next start --hostname 127.0.0.1) >/tmp/cashsathi-web.log 2>&1 &
web_pid=$!

for _ in $(seq 1 40); do
  if curl --fail --silent "http://127.0.0.1:8000/healthz" >/dev/null; then break; fi
  sleep 1
done
for _ in $(seq 1 40); do
  if curl --fail --silent "http://127.0.0.1:3000" >/dev/null; then break; fi
  sleep 1
done

E2E_FIREBASE=1 E2E_BASE_URL=http://127.0.0.1:3000 npm run test:e2e --workspace=web
