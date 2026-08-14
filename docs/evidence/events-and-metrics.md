# Evidence events and metrics

## Append-oriented event envelope

Every material workflow transition emits one event with:

```json
{
  "event_id": "provider-generated stable identifier",
  "schema_version": 1,
  "event_type": "action.executed",
  "business_id": "biz_...",
  "actor_type": "USER|AGENT|SYSTEM",
  "actor_id": "non-secret identifier",
  "subject_type": "invoice|action|business|payment",
  "subject_id": "record identifier",
  "occurred_at": "UTC timestamp",
  "source": "api|scheduler|tool",
  "properties": {}
}
```

Required event types:

- `business.created`
- `consent.granted`
- `invoice.extraction_completed`
- `invoice.confirmed`
- `agent.decision_created`
- `policy.checked`
- `action.proposed`
- `action.approved`
- `action.executed`
- `action.cancelled`
- `action.resolved`
- `payment.recorded`
- `invoice.closed`
- `gmail.connected`
- `gmail.disconnected`
- `automation.changed`
- `evidence.ledger_recorded`
- `consent.optional_granted`
- `consent.optional_withdrawn`
- `validation.prospect_recorded`
- `validation.interview_recorded`
- `founder_plan.activated`
- `account.deleted`

Properties contain sanitized operational facts, never bearer tokens, OAuth tokens, raw PDFs, full model prompts, or email bodies. Events are immutable through public APIs.

## Metric definitions

| Metric | Definition |
| --- | --- |
| Monitored value | Sum of confirmed invoice face value currently or previously managed. |
| Outstanding value | Confirmed invoice value without a verified payment/closure. |
| Overdue value | Outstanding value whose deterministic due date has passed. |
| Verified payments | Payment records explicitly confirmed by the owner with amount and date. |
| Post-action payments | Verified payments occurring after a logged action; correlation only. |
| AI decisions | Validated production `agent.decision_created` events. |
| Successful actions | Executed actions with a provider-confirmed success result. |
| Automation rate | Successful actions executed without human approval divided by all executed actions. |
| Paying businesses | Distinct arms-length businesses with verified product revenue. |
| Product revenue | Money paid to the product, reported separately from invoice recoveries. |
| CAC | Marketing and acquisition spend divided by new paying arms-length businesses. |

Demo, related-party, and arms-length records must be separately labelled. “Recovered because of CashSathi” may only be used when evidence supports causation; the default claim is timing correlation after a logged action.

## Evidence exports

The schema-v2 admin ZIP includes a completeness manifest, scoreboard, pseudonymous businesses, agent runs, actions, owner-confirmed payments, founder-plan records, a sanitized financial ledger, and only currently consented testimonials/identity fields. It also includes a May–August 2026 monthly P&L, an aggregated customer/validation breakdown, versioned submission metrics, and an evidence README. It pages internally and aborts above the configured safety ceiling instead of truncating. Customer names, prospect names, interview text, invoice content, email addresses, provider secrets, tenant IDs, and raw receipt references are excluded.

The P&L reports integer minor units per currency and applies valid reversals. Revenue remains split into arms-length, related-party, pre-existing, and unclassified categories; expenses and marketing spend remain explicit. The submission metrics use the stated report period, while `scoreboard.json` remains the operational all-time view for backward compatibility.
