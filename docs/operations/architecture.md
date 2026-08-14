# Phase 1 architecture decision

## Decision

Use a monorepo with a Next.js web service and FastAPI API service deployed independently to Cloud Run. Firebase Authentication runs in the browser; all operational data access runs through the API. Firestore client access is denied by rules, while the API uses a least-privilege service account and derives the business from authenticated membership.

```text
Browser -> Firebase Auth
Browser -> Cloud Run web
Browser -- Firebase ID token --> Cloud Run API -- IAM --> Firestore
Cloud Run API -- API key from Secret Manager --> Gemini API
```

## Tenant invariant

The token supplies only the Firebase user ID. The API loads `users/{uid}.business_id`, verifies `businesses/{businessId}/members/{uid}`, and creates a `TenantContext`. Repository operations accept that context; a request body or URL cannot override its `business_id`.

The Phase 1 one-owner limit is reflected in the API, while nested membership documents allow future team roles.

## Runtime and dependency baseline

- Node.js 24, npm workspaces, Next.js 16.3.1, React 19.2.8, TypeScript 5.9, Tailwind 4, Firebase JS 12.
- Python 3.13, uv, FastAPI 0.141, Pydantic 2.13, Firebase Admin 7.5, Firestore 2.28, structlog 26.
- Vitest/Testing Library, Playwright, pytest, Ruff, and mypy.
- Firebase CLI 15 because its engine supports Node 24.

Exact transitive versions are recorded in `package-lock.json` and `services/api/uv.lock`.

## Data layout

- `users/{uid}`: profile and server-managed current business ID.
- `businesses/{businessId}`: business identity and owner ID.
- `businesses/{businessId}/members/{uid}`: role and active status.
- `settings/{businessId}`: policy defaults in minor units.
- `evidence_events/{eventId}`: append-oriented evidence envelope.
- `businesses/{businessId}/consents/{consentId}`: append-only versioned consent grants.
- `businesses/{businessId}/customers/{customerId}`: customer identity and manual-only policy.
- `businesses/{businessId}/invoices/{invoiceId}`: user-confirmed invoice facts only.
- `businesses/{businessId}/agent_runs/{runId}`: validated proposals and policy outcomes.
- `businesses/{businessId}/actions/{actionId}`: proposed or approval-gated external actions.

## Phase 2–3 operating boundary

The API validates PDF uploads to 10 MiB and 25 pages, sends bytes inline to Gemini, returns an editable draft, and discards the bytes. Confirmation uses the immutable extraction event as provenance and as the idempotency key. Date-derived invoice state is calculated at read/evaluation time in Asia/Kolkata rather than stored as stale state.

Gemini only proposes a structured decision. Deterministic code applies paid, dispute, missing-data, cooldown, high-value, non-INR, manual-only, legal-language, and missing-email safeguards before an append-oriented agent run or action proposal is stored. Phase 3 never executes an external action.

## Consequences

- Browser data access remains simple and cannot bypass API tenant checks.
- The public API service must allow unauthenticated network ingress for browsers, but every `/api/*` route verifies a Firebase bearer token.
- Two services increase deployment steps but provide independent scaling and clearer Google Cloud evidence.
- Public Firebase configuration is build-time data, not a secret; Admin credentials and future API/OAuth secrets never enter the web image.
