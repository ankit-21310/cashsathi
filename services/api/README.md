# CashSathi API

FastAPI service for Firebase authentication, tenant-derived authorization, consent, transient Gemini PDF extraction, confirmed invoices, deterministic state calculation, constrained collection decisions, and server-owned Firestore access.

## Phase 2–3 API

- `GET|POST /api/consents/product-processing`
- `POST /api/invoices/extract` (`multipart/form-data`, PDF only)
- `POST|GET /api/invoices`
- `GET /api/invoices/{invoice_id}`
- `POST /api/invoices/{invoice_id}/evaluate`
- `GET /api/agent-runs`

The PDF exists only for the extraction request. Firestore receives user-confirmed fields and sanitized evidence metadata, never PDF bytes, prompts, raw model responses, or reminder bodies.
