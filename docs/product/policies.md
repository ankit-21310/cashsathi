# Collection policy baseline

These defaults are enforced by application code after any model decision. Owners may make a policy more restrictive; loosening dispute, legal-language, or payment-confirmation safeguards is outside the MVP.

| Policy | Default | Enforcement intent |
| --- | --- | --- |
| Reminder cooldown | 72 hours | No automatic reminder inside the cooldown window. |
| High-value threshold | ₹50,000 / 5,000,000 minor units | Require human approval at or above the threshold. |
| Manual-only customer | Off per customer | When enabled, every action requires a human. |
| Dispute handling | Human required | Disagreement or dispute language stops automation. |
| Legal language | Prohibited | No threats, legal-right claims, or filing promises. |
| Payment closure | Explicit confirmation required | A model cannot independently mark an invoice paid. |
| Invalid model output | One bounded retry | A second failure routes to human review. |
| Ambiguous tool delivery | Mark `UNKNOWN` | Do not resend automatically when delivery is uncertain. |

Money is always stored as integer minor units plus an ISO 4217 currency code. Dates are stored as typed timestamps or ISO dates; user-facing presentation defaults to Asia/Kolkata.

Policy outcomes and the rule version must be persisted before tool execution. Actions and agent runs are append-oriented; corrections create new records rather than rewriting production evidence.
