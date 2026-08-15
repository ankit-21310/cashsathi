# Production runbook

This runbook covers the Phase 6–8 repository release. Cloud deployment, genuine customer validation, public assets, and external submission remain operator responsibilities.

## Release order

1. Authenticate `gcloud`, Firebase, and application-default credentials; verify the intended project.
2. Run `infra/gcp/bootstrap.ps1`. For an existing database, run the Phase 4, Phase 6–7, and Phase 9 backfills first without `--apply`, inspect the counts, then repeat with `--apply`.
3. Deploy Firestore rules and indexes. Confirm TTL is enabled for `_rate_limits.expires_at`; application logic still treats expired windows as expired because TTL deletion is asynchronous.
4. Run `infra/gcp/configure-observability.ps1` **before** smoke events. User-defined log metrics do not backfill.
5. Deploy with strict readiness enabled and exact HTTPS web/API/OAuth origins, Gmail/KMS secrets, scheduler identity/audience, and at least one administrator UID.
6. Provision the judge account with a password supplied only through the operator environment. Run the clean-browser production smoke script.
7. Run `infra/gcp/emit-alert-test-events.ps1 -ProjectId <id> -ConfirmNotifications` and confirm four notifications. Verify the two-hour heartbeat-absence policy in a separately scheduled window. Synthetic events contain no customer payloads.
8. Generate a fresh admin evidence ZIP, replace every Phase 8 package placeholder, commit the intended release, and run `npm run verify:phase8 -- --evidence-zip <path>`. Preserve the untracked checksum manifest with the controlled release evidence.

## Alert response

- API 5xx (3 in 5 minutes): correlate request IDs, check the current Cloud Run revision and dependency health, and roll back if the failures began with the release.
- Model/schema (2 in 15 minutes): verify Gemini availability, model configuration, schema compatibility, and sanitized failure categories. Extraction and decisioning make at most two total attempts.
- Gmail failure (any): inspect the action attempt and provider result. Never replay an ambiguous delivery automatically.
- Scheduler failure (any) or success absent for 2 hours: verify OIDC issuer, dedicated service-account identity, exact audience, job target, and API revision. Trigger one authenticated run only after correcting the cause; idempotent action keys prevent duplicate proposals.
- Budget 50/90/100%: inspect Cloud Billing by service. At 90%, pause nonessential validation workloads; at 100%, disable automation and scheduler execution until the owner approves further spend.

## Ambiguous Gmail delivery

Keep the action in `UNKNOWN`. Check the dedicated sender mailbox and recipient evidence outside the application. An owner or administrator may resolve it as confirmed delivered, confirmed not delivered, or manually sent. Do not call Gmail send again for the same action unless the existing workflow creates a new explicit action.

## Account deletion

The owner must type the exact business name and explicitly confirm. The service disables automation, attempts Google token revocation, purges tenant operational data, unlinks retained financial ledger records, and deletes the Firebase user last. If Google revocation fails, local deletion continues; tell the owner to remove access under Google Account > Security > Third-party connections and investigate the sanitized `privacy_deletion_failure` alert. If Firebase-user deletion fails after local purge, use the request ID to complete Auth deletion manually; never reconstruct purged tenant data.

Only global anonymous counters may remain when `ANONYMIZED_METRICS` was active. Retained financial entries contain amount/date/category only, no business link, and a digest in place of the receipt reference.

## Evidence handling

Generate evidence only through the admin export. Verify `manifest.json` reports schema version 2 and `complete: true`, inspect collection counts, reconcile `pnl_by_month.csv`, review `customer_breakdown.csv`, and store the ZIP in an access-controlled location. It contains pseudonyms and only currently consented testimonial/identity material. On withdrawal, stop all future use immediately and remove the material from controlled submissions, decks, folders, and publication artifacts. Never merge DEMO, RELATED, PREEXISTING, UNCLASSIFIED, and ARMS_LENGTH results, and never convert post-action timing into a causation claim.

## Rollback

Route traffic to the previous known-good Cloud Run revision. Do not reverse Firestore migrations: new fields are additive and readers accept missing pseudonyms/defaults. Keep the scheduler paused while validating the rollback, then execute one authenticated recovery run and confirm the success heartbeat.

## Known limitations

- Payments and outreach are manual; no payment processor or message campaign is included.
- Gmail delivery can be ambiguous and is deliberately not automatically retried.
- Firestore TTL deletion is delayed; correctness does not depend on immediate cleanup.
- Evidence pseudonyms protect direct tenant IDs but are not a substitute for access control.
- Phase 7 is not complete from code alone. The exit gate requires genuine outreach, 3–5 design partners, and at least five independently verifiable arms-length paying businesses with receipts, feedback, outcomes, and separate consents.
