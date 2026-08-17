# CashSathi API

The Phase 6–8 API adds streamed JSON/PDF size enforcement, route deadlines, Firestore fixed-window limits, strict production readiness, optional consent history, account export/deletion, admin validation records, Founder Recovery Plan enforcement, and sanitized schema-v2 evidence ZIP generation. The Phase 8 archive retains its existing files and adds a May–August monthly P&L, aggregated customer breakdown, versioned submission metrics, and an evidence README.

All list endpoints accept at most 100 records per page. Evidence export pages internally and fails with `export_limit_exceeded` above `EXPORT_RECORD_LIMIT`; it never silently truncates. Rate-limit subjects are SHA-256 digests and `_rate_limits.expires_at` must have Firestore TTL enabled.

Operator utilities default to dry-run:

```text
uv run python scripts/backfill_phase_6_7.py --project <id>
uv run python scripts/backfill_phase_9.py --project <id>
uv run python scripts/import_validation_prospects.py --project <id> --admin-uid <uid>
```

Add `--apply` only after reviewing counts. Neither utility sends messages or changes invoice outcomes.

Phase 9 adds restrictive policy/customer/dispute APIs, explicit Gemini function calls, four
least-privilege roles, English/Hindi deterministic templates, cash forecasts, and a finance pack.
The accounting endpoints remain `NOT_CONFIGURED` until owner-approved Zoho Books or outbound
TallyPrime credentials are provisioned; sync requests fail closed before that point.

Validate the repository-controlled Phase 8 package with `npm run verify:phase8:structure`.
Final verification requires a fresh admin evidence ZIP and must be run from a clean commit;
the verifier never tags, deploys, publishes, or submits.

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
- `GET|POST /api/jobs/recheck` (Vercel cron secret or Google OIDC, by runtime platform)
- `GET|POST /api/admin/*` (configured Firebase administrators only)

The PDF exists only for the extraction request. OAuth refresh tokens use Google KMS on GCP and
versioned AES-256-GCM on Vercel. Evidence events and logs never contain PDF bytes, tokens, raw
model/provider responses, or reminder bodies; approval-visible copy remains inside the tenant
action record.

When both Firebase emulator hosts point to localhost and the environment is not production, deterministic local adapters support the complete browser journey without external AI, OAuth, KMS, or email calls. Production configuration rejects emulator hosts.
