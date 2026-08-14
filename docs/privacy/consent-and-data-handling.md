# Data handling and consent templates

This operational template is not legal advice. Obtain jurisdiction-specific review before a broad public launch.

## Data boundary

- Process invoice PDFs transiently and discard bytes after extraction or failure.
- Persist only user-confirmed invoice fields, operational history, policy settings, and sanitized diagnostics.
- Store raw documents only after a future, explicit owner opt-in.
- Encrypt Gmail refresh tokens with Cloud KMS before persistence in Phase 4.
- Do not place credentials, invoice content, customer email addresses, or reminder bodies in application logs.
- All operational reads and writes pass through the authenticated API and server-derived business membership.
- Support access, correction, export, and deletion requests before production onboarding.

## Product-processing consent

> I authorize the service to process invoices and related receivables information that I am permitted to provide, and to use my configured mailbox for actions I explicitly approve or allow under my policies. I understand that I remain responsible for invoice accuracy, customer relationships, disputes, and payment confirmation.

Required: yes. Record version, timestamp, user ID, business ID, and source.

Implementation: the API returns the canonical statement and current version, records an append-only grant before the first upload, and re-gates processing whenever the version changes. The browser cannot bypass this check because the extraction endpoint verifies consent server-side.

## Anonymized metrics consent

> I separately agree that de-identified usage and outcome metrics may be aggregated for product evaluation and competition reporting. No invoice, customer, or business identity will be disclosed through this permission.

Required: no. Default: not granted.

## Testimonial consent

> I separately permit the quoted feedback I approve to be used in product and competition materials. I will review the exact quote before publication.

Required: no. Default: not granted. Store the approved exact text and permitted channels.

## Identity-disclosure consent

> I separately permit my business name and approved identifying details to appear with the approved testimonial or customer evidence in the stated channels.

Required: no. Default: not granted. This must not be bundled with product access or anonymized metrics.

## Retention defaults

- Transient invoice bytes: current request only.
- Structured operational records: retained while the account is active and then deleted through the documented deletion process.
- Audit/evidence exports: retain only for the disclosed competition or compliance purpose.
- Failed diagnostics: sanitized and time-limited; no raw document payloads.

## Account controls

Authenticated owners can view the append-only history for all optional consents, grant a current version, withdraw an active grant, export structured tenant data, and delete the account. Testimonial grants store the exact approved quote and channels. Identity grants store the exact approved identity fields and channels. A withdrawal excludes that material from every future evidence export immediately.

Account JSON exports include business configuration, settings, invoices, actions, agent runs, owner-confirmed payments, plan enrollment, and consent history. OAuth state, encrypted tokens, rate-limit records, provider secrets, and internal provider payloads are excluded.

Deletion requires the typed business name and an explicit confirmation. Gmail revocation is attempted before the local purge and Firebase Authentication user deletion occurs last. A revocation failure does not preserve local tenant data. The only allowed retention is a global anonymous aggregate when its consent is active and an unlinked financial ledger amount/date/category whose free-text reference has been replaced by a SHA-256 digest.

## Phase 2–3 enforcement

- PDF extension, MIME type, signature, structure, encryption, size, and page count are checked before Gemini processing.
- PDFs are sent inline rather than through a retained file API and are released when the request completes.
- Extraction events contain only model/version, latency, token counts, byte/page counts, validation status, and warning codes.
- Decision prompts omit customer names, email addresses, invoice text, PDFs, and reminder bodies.
- Agent runs retain validated decisions and policy results, never full prompts or raw model responses.
