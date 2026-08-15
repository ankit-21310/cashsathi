# Firebase CLI transitive advisory review

Reviewed: 2026-08-15

`firebase-tools@15.27.0` is the current npm release and is used only as a root
`devDependency` for local/CI emulators, rules tests, seeding, and deployment tooling. It is not
copied into either production container or imported by application runtime code.

`npm audit` reports five moderate findings along this development-only path:

- `firebase-tools -> @google-cloud/pubsub -> @opentelemetry/core@1.30.1`
  (`GHSA-8988-4f7v-96qf`, unbounded W3C baggage parsing).
- `firebase-tools -> gaxios@6.7.1 -> uuid@9.0.1`
  (`GHSA-w5hq-g745-h8pq`, bounds validation for UUID calls that supply a buffer).
- The remaining three records are npm's aggregate entries for `firebase-tools`,
  `@google-cloud/pubsub`, and `gaxios` along those two paths.

No safe upgrade is currently available. `npm audit` proposes `firebase-tools@14.23.0`, which is
a downgrade and still does not represent a supported forward fix. `npm audit fix --force` must
not be used.

Risk controls:

- Production dependencies are clean: `npm audit --omit=dev --audit-level=moderate` reports zero
  vulnerabilities.
- Emulator and deployment commands operate on controlled inputs; untrusted W3C baggage and
  caller-provided UUID buffers are not passed into these CLI dependency paths.
- CI continues to fail on critical Node advisories, scans full Git history with Gitleaks, and
  builds production containers separately.
- Re-check on every `firebase-tools` update and at least monthly. Upgrade as soon as the upstream
  CLI removes both affected transitive versions.
