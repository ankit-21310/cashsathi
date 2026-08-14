# Receivables Operator web

Next.js 16 application for Firebase owner authentication, business onboarding, consent-gated invoice extraction/review, invoice monitoring, and constrained agent decision review.

Copy `.env.example` to `.env.local`, run the Firebase emulators and API from the repository root, then start this workspace with `npm run dev --workspace=web`.

All `NEXT_PUBLIC_*` Firebase values are public web-app configuration. No Firebase Admin key or service-account JSON belongs in this workspace.
