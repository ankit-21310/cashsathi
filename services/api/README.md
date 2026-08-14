# CashSathi API

The Phase 6–7 API adds streamed JSON/PDF size enforcement, route deadlines, Firestore fixed-window limits, strict production readiness, optional consent history, account export/deletion, admin validation records, Founder Recovery Plan enforcement, and sanitized evidence ZIP generation.

All list endpoints accept at most 100 records per page. Evidence export pages internally and fails with `export_limit_exceeded` above `EXPORT_RECORD_LIMIT`; it never silently truncates. Rate-limit subjects are SHA-256 digests and `_rate_limits.expires_at` must have Firestore TTL enabled.

Operator utilities default to dry-run:

```text
uv run python scripts/backfill_phase_6_7.py --project <id>
uv run python scripts/import_validation_prospects.py --project <id> --admin-uid <uid>
```

Add `--apply` only after reviewing counts. Neither utility sends messages or changes invoice outcomes.

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
