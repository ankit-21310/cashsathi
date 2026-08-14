# CashSathi (internal codename)

CashSathi is the internal codename for a constrained AI accounts-receivable operator for Indian micro and small businesses. Public builds default to **Receivables Operator Preview** until the product name receives formal clearance.

This repository implements Phases 0–5: product/safety foundations, tenant-isolated invoice extraction and decisioning, controlled Gmail execution, hourly rechecks, explicit payment confirmation, auditable timelines, operator dashboards, and a separately authorized competition-evidence view.

## Repository

- `apps/web`: Next.js 16 web application with Firebase Authentication.
- `services/api`: FastAPI service that verifies Firebase ID tokens and owns all Firestore access.
- `docs`: product, safety, evidence, validation, and operational records.
- `infra`: Firebase, Cloud Build, and Google Cloud deployment configuration.

## Local setup

Prerequisites: Node.js 24, Python 3.13 managed by `uv`, and Java 21+ for Firebase emulators.

1. Copy `apps/web/.env.example` to `apps/web/.env.local`.
2. Copy `services/api/.env.example` to `services/api/.env`.
3. Add a Gemini API key to `services/api/.env` to use extraction and decisioning; the rest of the application and automated tests run without it.
4. With both Firebase emulators configured, local extraction, decisioning, OAuth, encryption, and delivery use deterministic emulator adapters and never call Gemini, Gmail, or KMS. To test the real Gmail provider, use a non-emulator development configuration with a Google OAuth web client, exact callback URL, Cloud KMS key, and application-default credentials.
5. Run `npm ci` and `uv sync --directory services/api --all-groups`.
6. Start Firebase emulators with `npm run emulators`.
7. In another terminal, seed two isolated demo tenants with `npm run seed`.
8. Start the applications with `npm run dev`.

Web: <http://localhost:3000>  
API: <http://localhost:8000>  
Emulator UI: <http://localhost:4000>

## Quality gates

```text
npm run lint
npm run typecheck
npm test
npm run build
```

Production deployment instructions are in `docs/operations/cloud-setup.md`. Never commit Firebase Admin credentials or `.env` files.
