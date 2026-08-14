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
