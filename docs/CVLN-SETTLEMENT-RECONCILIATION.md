# CVLN Settlement & Reconciliation (P0.1-B4)

> As-built. **CURRENT / IMPLEMENTED** vs **TARGET / FUTURE** explicit.
> Settlement is a TRACKING layer over provider interactions for already-captured ledger
> movements. It does **not** re-post value (no second ledger).

## Separation of concerns
`AUTHORIZATION (hold)` ≠ `CAPTURE (ledger posting)` ≠ `SETTLEMENT (provider)` ≠ `RECONCILIATION`.
A transaction can be captured in the ledger yet not settled at the provider.

## Settlement model (`settlements`)
`settlement_id, transaction_id, user_id, provider, provider_reference, asset, amount, amount_minor,
direction, internal_status, external_status, reconciliation_status, correlation_id, idempotency_key
(unique: stl:<tx>), failure_code, failure_reason, retry_count, created_at, updated_at, settled_at`.
No provider secrets are stored.

## State machine (implemented)
```
PENDING ─▶ SUBMITTED ─▶ PROCESSING ─▶ SETTLED
   └────────┴────────────┴─▶ FAILED / CANCELLED / REQUIRES_REVIEW
REQUIRES_REVIEW ─▶ SETTLED / FAILED / CANCELLED
```
Terminal: `SETTLED, FAILED, CANCELLED` (never leave).
Transitions are **atomic**: `settlement_transition` uses `find_one_and_update` filtered on the set of
valid predecessors → the status field is the lock, so a concurrent terminal transition wins exactly once.
Every transition writes append-only `financial_state_history` and emits an outbox event.

## Provider adapter boundary
`MockProviderAdapter` (status **MOCK**). A provider only reports external state; it **never** writes
balances, ledger, holds or settlement state directly — the Financial Core owns all value mutations.
Real issuers/banks/brokers/custodians plug in behind this boundary later.

## Webhook inbox (dedup + out-of-order + scope)
`POST /api/webhooks/{provider}` — `webhook_inbox` unique on `(provider, provider_event_id)`.
- Same event N times → exactly one business effect (`duplicate_ignored`).
- Same `event_id`, different body → `duplicate_conflict` + `Financial.ProviderWebhookConflict`.
- **Provider-scoped**: settlement lookup requires `provider_reference` AND the URL `{provider}`. A webhook
  for provider A can never drive provider B's settlement; a reference belonging to another provider →
  `Financial.ProviderScopeViolation` + inbox `SCOPE_VIOLATION`.
- Out-of-order: a webhook that cannot apply on a non-terminal settlement → `REQUIRES_REVIEW` (never blind
  override); on a terminal settlement → `ignored_terminal`.
- **Known activation gap**: no HMAC/signature verification (providers are MOCK). Required before production.

## Reconciliation engine
`POST /api/admin/reconciliation/run` compares internal vs external and opens auditable
`ReconciliationCase`s (`missing_provider_reference`, `status_mismatch`, `amount_mismatch`). **No silent
correction** — every discrepancy is a case. `resolve` is atomic (`OPEN/INVESTIGATING → RESOLVED /
ACCEPTED_DIFFERENCE / ESCALATED`), audited.

## Events
`Financial.SettlementCreated/Submitted/Processing/Settled/Failed/Cancelled/RequiresReview`,
`Financial.ProviderWebhookReceived/Duplicate/Conflict`, `Financial.ProviderScopeViolation`,
`Financial.ProviderStateConflict`, `Financial.ReconciliationMismatch/Resolved`.

## Integrity (financial-health)
`settlements{total,terminal,requires_review,stuck}`, `reconciliation_open_cases`,
`reconciliation_high_severity`. Severity HIGH if review/high-severity cases exist.

## Status
`settlement_engine = PARTIAL` (state machine + reconciliation REAL, provider MOCK),
`provider_adapters = MOCK`, `reconciliation = REAL`.

## TARGET / FUTURE
Real provider adapters (issuer/bank/broker/custodian) with signed webhooks + stable submit idempotency
keys; suspense/clearing accounts already representable via the existing ledger.
