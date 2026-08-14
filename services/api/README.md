# CashSathi API

FastAPI service for Firebase authentication, tenant-derived authorization, transient Gemini PDF extraction, constrained collection decisions, controlled Gmail execution, scheduled rechecks, payments, metrics, and server-owned Firestore access.

## Phase 2–5 API

- `GET|POST /api/consents/product-processing`
- `POST /api/invoices/extract` (`multipart/form-data`, PDF only)
- `POST|GET /api/invoices`
- `GET /api/invoices/{invoice_id}`
- `POST /api/invoices/{invoice_id}/evaluate`
- `GET /api/agent-runs`
- `GET /api/actions` and `POST /api/actions/{id}/approve|cancel|retry|resolve`
- `GET|POST /api/integrations/gmail/*`
- `POST /api/invoices/{invoice_id}/payments`
- `GET /api/invoices/{invoice_id}/timeline`
- `GET /api/metrics`
- `POST /api/jobs/recheck` (Google OIDC only)
- `GET|POST /api/admin/*` (configured Firebase administrators only)

The PDF exists only for the extraction request. OAuth refresh tokens are KMS-encrypted. Evidence events and logs never contain PDF bytes, tokens, raw model/provider responses, or reminder bodies; approval-visible copy remains inside the tenant action record.

When both Firebase emulator hosts point to localhost and the environment is not production, deterministic local adapters support the complete browser journey without external AI, OAuth, KMS, or email calls. Production configuration rejects emulator hosts.
