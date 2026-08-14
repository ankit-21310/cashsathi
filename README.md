# CashSathi (internal codename)

CashSathi is the internal codename for a constrained AI accounts-receivable operator for Indian micro and small businesses. Public builds default to **Receivables Operator Preview** until the product name receives formal clearance.

This repository currently implements Phase 0 (product, evidence, safety, and validation foundations) and Phase 1 (authentication, tenant isolation, Firestore access, observability, and deployable web/API shells). Invoice extraction and the deployed Gemini operating loop begin in Phase 2.

## Repository

- `apps/web`: Next.js 16 web application with Firebase Authentication.
- `services/api`: FastAPI service that verifies Firebase ID tokens and owns all Firestore access.
- `docs`: product, safety, evidence, validation, and operational records.
- `infra`: Firebase, Cloud Build, and Google Cloud deployment configuration.

## Local setup

Prerequisites: Node.js 24, Python 3.13 managed by `uv`, and Java 21+ for Firebase emulators.

1. Copy `apps/web/.env.example` to `apps/web/.env.local`.
2. Copy `services/api/.env.example` to `services/api/.env`.
3. Run `npm ci` and `uv sync --directory services/api --all-groups`.
4. Start Firebase emulators with `npm run emulators`.
5. In another terminal, seed two isolated tenants with `npm run seed`.
6. Start the applications with `npm run dev`.

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
