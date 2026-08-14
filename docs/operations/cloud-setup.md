# Google Cloud and Firebase setup

The repository cannot create cloud resources until the owner supplies the existing project ID and billing account and completes interactive `gcloud auth login` and `firebase login`.

## Immutable decision

Before creating App Engine or location-dependent resources, create the default Firestore database in **Standard, Native mode, `asia-south1` (Mumbai)**. Firestore location cannot be changed later.

## Prerequisites

1. Install the Google Cloud CLI and run `gcloud auth login` plus `gcloud auth application-default login`.
2. Run `npm ci`, then `npx firebase login`.
3. Confirm the selected project is the newly created competition project and billing is attached.
4. Run `infra/gcp/bootstrap.ps1 -ProjectId <id> -BillingAccountId <billing-id>`.

The bootstrap enables Cloud Run, Artifact Registry, Cloud Build, Firestore, Firebase, Secret Manager, Cloud KMS, and Billing Budgets; creates dedicated web/API identities; applies least privilege; creates empty future-secret containers; creates a USD 10 budget with 50/90/100% alerts; and deploys deny-all Firestore client rules.

Empty secret resources are intentional. Add values only when Phase 2/4 code consumes them, then grant the API service account access only to the required secret.

## Firebase console steps

1. Register a Web app and copy its public configuration values.
2. Enable Google and Email/Password under Authentication > Sign-in method.
3. After the web deployment, add its exact `run.app` hostname to Authentication > Settings > Authorized domains.
4. Do not download or commit a service-account JSON file; Cloud Run uses its attached identity.

## Deployment

Run `infra/gcp/deploy.ps1` with the project ID and Firebase public web configuration. The script builds both images remotely, deploys the API, builds/deploys the web app using the API URL, then tightens API CORS to the exact web origin.

Both services use min instances `0`, max instances `3`, one CPU, 512 MiB memory, concurrency `80`, and a 60-second timeout. Browser ingress is public, while `/api/*` authorization is enforced by Firebase ID-token verification.

## Production smoke test

1. `GET <api-url>/healthz` returns `ok` and an `X-Request-ID`.
2. `GET <api-url>/readyz` returns `ready`.
3. Unauthenticated `GET /api/me` returns the safe `authentication_required` envelope.
4. Create and sign in with an email/password owner from a fresh browser.
5. Create the business, reach the dashboard, sign out, and sign in again.
6. Repeat Google sign-in.
7. Confirm Cloud Logging has structured request records without authorization headers or personal invoice content.
8. Confirm another user receives a distinct business ID.

Deployment is not complete until these checks and Firebase authorized-domain configuration pass.
