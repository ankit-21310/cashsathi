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

Exports must include UTC timestamps, event IDs, business pseudonyms, model ID, prompt/policy version, decision, policy result, tool status, and outcome references. Customer names, invoice content, email addresses, and testimonials require the relevant consent before disclosure.
