# Phase 0/1 risk register

| Risk | Control | Owner/status |
| --- | --- | --- |
| Public name conflicts with existing finance products | Internal codename and configurable neutral display name; formal clearance before launch | Founder, open |
| Cross-business data access | Server-derived tenant context, nested membership verification, deny-all client rules, isolation tests | Engineering, implemented |
| Emulator tokens accepted in production | Startup validation rejects emulator variables in production | Engineering, implemented |
| Secret committed to source | Ignore patterns, environment examples, Secret Manager, CI secret scanning | Engineering, active |
| Budget overrun | USD 10-equivalent budget alerts, min instances zero, max instances three | Cloud owner, pending deployment |
| Fake or overstated traction | Separate demo, related-party, and arms-length records; conservative metric definitions | Founder, active |
| Invoice privacy exposure | Transient document processing and sanitized logs | Engineering, Phase 2 enforcement pending |
| Aggressive collection action | Cooldown, approval threshold, disputes and legal language hard stops | Engineering, enforced in decision and execution workflows |
| Delivery ambiguity causes duplicate reminder | Deterministic action keys and `UNKNOWN` state | Engineering, Phase 4 pending |
| Deadline leaves no deployed Gemini loop | Phase 0/1 explicitly marked insufficient; Phase 2–4 remain critical path | Founder, open |
