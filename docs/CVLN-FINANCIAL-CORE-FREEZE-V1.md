# CVLN WALLET FINANCIAL CORE v1 — BASELINE FROZEN

Date: 2026-06. Environment: Preview (standalone MongoDB, no multi-doc transactions).
Freeze = a stable, governed financial baseline onto which Invest/Crypto/FX/Card-production and
the ACTIVATION track can plug WITHOUT reinventing accounting. Not "immutable".

## Architecture
```
Request → Idempotency → Validation → Policy/Risk → State Machine → Authorization(Hold)
       → Capture(Ledger) → Settlement(Provider) → Reconciliation → Outbox/Events → Audit
```
Single double-entry ledger is the source of truth. `balance_cc` / `held_cc` / entity & coffre balances
are reconstructible caches. Amounts are validated minor-unit-exact via the Asset Registry.

## Invariants (enforced + tested)
- Σ(postings per asset) == 0 (ledger always balanced).
- JCC supply reconciled (ledger vs circulation).
- held_cc ≥ 0, held_cc ≤ balance_cc, available = balance − held ≥ 0.
- No module mutates value outside the ledger; every spend path uses `atomic_spend` (hold-aware, race-safe).
- Refund cumulative ≤ refundable principal; refund/reversal mutually exclusive; reversal terminal once.
- Settlement terminal transition wins once; same provider webhook ⇒ 1 effect; provider-scoped.
- Same Idempotency-Key ⇒ 1 economic effect; maker ≠ checker; approval executes once.
- No silent monetary precision loss; no silent reconciliation correction.

## Capability matrix (honest)
| Capability | Status | Evidence |
|---|---|---|
| Double-entry ledger | REAL | integrity + supply reconciliation, 197 tests |
| Idempotency API | REAL | replay/conflict tests (iter 1-2) |
| Kill-switches | REAL | iter 3 |
| Holds / available balance / state machine | REAL | iter 4, 32 holds tests, concurrency |
| Fees engine | REAL | iter 5-6 |
| Refund engine | REAL | atomic cumulative guard, concurrency |
| Reversal engine | REAL | single-winner, mutual exclusion |
| Reconciliation | REAL | cases + resolve, no silent correction |
| Asset registry | REAL | GET /api/assets, minor-exact validation |
| Maker-checker | REAL | maker≠checker backend, single-execution 20x concurrent |
| Recovery engine | REAL | scan/auto-heal/journal, crash-window tests |
| Settlement engine | PARTIAL | state machine + reconciliation REAL, provider MOCK |
| Outbox / at-least-once | PARTIAL | idempotent events + recovery; no multi-doc atomicity |
| Monetary precision | PARTIAL | registry+Money+validation live; storage still float |
| Provider adapters | MOCK | MockProviderAdapter only |
| Stripe deposits | SANDBOX | not production-claimed |
| Card issuing | MOCK | no issuer/processor |
| Apple/Google Pay | PLANNED / NOT_SUPPORTED | no entitlements |
| Invest / Crypto / FX / RWA / Business | PLANNED | not started |
| KYC/AML | PLANNED | no real provider |

## Tests
197 automated tests green (pytest, serial). Independent testing_agent iterations 4-8. Real concurrency,
crash-window and chaos scenarios. curl-verified for every B4/B5 endpoint.

## Bugs found & fixed during this mission
- HIGH: withdrawal bypassed holds (non-atomic) → atomic hold-aware debit.
- CRITICAL: withdrawal approve/reject TOCTOU double-credit → atomic status-flip lock.
- CRITICAL: webhook not provider-scoped (cross-provider drive) → provider-scoped lookup + scope-violation signal.
- HIGH (systemic, found in final audit): send / coffre-move / marketplace / entity-charge all bypassed
  holds via stale read-then-write → all routed through `atomic_spend`.
- Several MEDIUM/MINOR: refund 404→400, fee-on-reject refund, guard compensation, recovery classification,
  precision dry_run=false honesty (501), payload-conflict detection.

## Known non-blocking gaps (activation dependencies / tech-debt)
- Provider adapters are MOCK; real issuer/bank/broker/custodian + signed webhooks (HMAC) + stable submit
  idempotency keys required before settlement production. Submit-retry currently overwrites provider_reference
  (safe with MOCK; must use provider idempotency key when real).
- Outbox is PARTIAL: no strict transactional atomicity (needs replica-set tx or event-sourced write);
  `outbox_events`/`outbox_consumed` have no TTL/archival (ISO-string timestamps) → add purge before scale.
- Monetary precision PARTIAL: infrastructure + validation live, but historical ledger still stored as
  float (validated minor-exact); destructive integer-storage migration deferred (returns 501).
- Entity API (`entities.balance_cc`) balances are a parallel counter not yet ledger-backed; user-fund side
  IS ledgered and hold-aware. Full ledger integration is future work (account_registry = PARTIAL).
- Stripe SANDBOX; no production payout/Connect.

## FREEZE DECISION
**CVLN WALLET FINANCIAL CORE v1 — BASELINE FROZEN.**
The software-side accounting core is coherent under concurrency, retry, duplicate, crash, timeout,
out-of-order webhooks, uncertain provider, human error, replay, partial failure, rounding, migration and
recovery. Real providers and the BUILD/ACTIVATION domains can now be attached on top of this baseline.
