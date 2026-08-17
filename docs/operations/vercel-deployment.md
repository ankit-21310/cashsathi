# Vercel Hobby deployment

CashSathi uses two Git-connected Vercel projects from the same repository:

- `cashsathi-api`, rooted at `services/api`
- `cashsathi-web`, rooted at `apps/web`

The API's `vercel.json` exposes the FastAPI application as one Python 3.13 Function, caps
execution at 300 seconds, excludes local/test material, and invokes `GET /api/jobs/recheck`
daily at `02:30 UTC` (`08:00 IST`). The existing GCP adapters remain available when
`RUNTIME_PLATFORM=gcp`.

## Production environment

Configure these values in the API project's Production environment before deploying:

```text
APP_ENV=production
RUNTIME_PLATFORM=vercel
STRICT_PRODUCTION_READINESS=true
GCP_PROJECT_ID=cashsathi
FIRESTORE_DATABASE_ID=(default)
CORS_ALLOWED_ORIGINS=https://cashsathi-web.vercel.app
WEB_BASE_URL=https://cashsathi-web.vercel.app
GEMINI_MODEL=gemini-3.6-flash
GMAIL_OAUTH_REDIRECT_URI=https://cashsathi-api.vercel.app/api/integrations/gmail/callback
PLATFORM_ADMIN_UIDS=<demo-admin-firebase-uid>
```

Retain the timeout, request-size, scheduler batch, scheduler concurrency, and export limit
defaults from `services/api/.env.example` unless the demo needs a smaller bound. Add the
following as sensitive/write-only Production values:

```text
FIREBASE_SERVICE_ACCOUNT_JSON_B64=<base64 service-account JSON>
GEMINI_API_KEY=<dedicated demo key>
GMAIL_OAUTH_CLIENT_ID=<dedicated demo client id>
GMAIL_OAUTH_CLIENT_SECRET=<dedicated demo client secret>
GMAIL_TOKEN_ENCRYPTION_KEY_B64=<base64 of exactly 32 random bytes>
CRON_SECRET=<random bearer secret>
GMAIL_RECIPIENT_ALLOWLIST=<comma-separated dedicated test mailboxes>
```

Do not configure emulator variables, `GMAIL_KMS_KEY_NAME`, Scheduler identity, or Scheduler
audience on Vercel. Never upload `.env` or a credential JSON file. Decode the service-account
key only in memory, and delete the downloaded key immediately after its base64 value is stored
in Vercel.

Configure the web project's Production environment with:

```text
NEXT_PUBLIC_APP_MODE=production
NEXT_PUBLIC_PRODUCT_NAME=<public demo name>
NEXT_PUBLIC_API_BASE_URL=https://cashsathi-api.vercel.app
NEXT_PUBLIC_FIREBASE_API_KEY=<web app key>
NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN=<project auth domain>
NEXT_PUBLIC_FIREBASE_PROJECT_ID=cashsathi
NEXT_PUBLIC_FIREBASE_STORAGE_BUCKET=<project storage bucket>
NEXT_PUBLIC_FIREBASE_MESSAGING_SENDER_ID=<sender id>
NEXT_PUBLIC_FIREBASE_APP_ID=<web app id>
NEXT_PUBLIC_USE_FIREBASE_EMULATOR=false
NEXT_PUBLIC_CSP_FIREBASE_ORIGINS=https://*.googleapis.com https://*.firebaseio.com wss://*.firebaseio.com
```

For Preview, configure only `NEXT_PUBLIC_APP_MODE=preview-disabled` and the product name on
the web project. Do not add API, Firebase, Gemini, Gmail, service-account, cron, or encryption
credentials to Preview. Keep Vercel Authentication enabled for previews only.

## Release order

1. Deploy Firebase's deny-all client rules and composite indexes.
2. Deploy the API and confirm `/healthz`, `/readyz`, cron authentication, and safe unauthenticated
   responses.
3. Set the final API URL as the Gmail OAuth callback.
4. Deploy the web project, then set the web URL in API CORS and `WEB_BASE_URL`.
5. Add the final web hostname to Firebase Authentication's authorized domains.
6. Redeploy both projects and perform the production smoke test with synthetic data and one
   allowlisted mailbox.
