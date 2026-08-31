# CVLN Financial Recovery (P0.1-B5)

> As-built. Status: **recovery_engine = REAL**. Recovery describes OPERATIONAL state; the ledger
> remains the accounting truth (no second ledger).

## Principle
Correctness must not depend on any background worker. Recovery is a maintenance/observability layer that
detects and safely repairs operational drift. Nothing financial is ever blindly replayed.

## Recovery scan — `POST /api/admin/recovery/scan`
Detects and classifies:
| Finding | Classification |
|---|---|
| `stale_idempotency` (PROCESSING > 15 min) | AUTO_RECOVERABLE |
| `expired_active_holds` (past `expires_at`) | AUTO_RECOVERABLE |
| `expired_approvals` (PENDING past expiry) | AUTO_RECOVERABLE |
| `stuck_settlements` (non-terminal > 24 h) | MANUAL_REVIEW |
| `unprocessed_inbox` | MANUAL_REVIEW |
| `undelivered_outbox` (count) | (info) |
| `dead_letter_outbox` | CRITICAL |
Each scan is logged to `recovery_journal`.

## Auto-heal — `POST /api/admin/recovery/auto-heal`
Only AUTO_RECOVERABLE, idempotent and safe:
- clears stale PROCESSING idempotency records (a retry with the same key then works);
- lazy-expires overdue holds via `_terminate_hold` (single-winner, no double `held_cc` decrement);
- flips stale PENDING approvals to EXPIRED.
MANUAL_REVIEW / CRITICAL findings are **not** auto-corrected.

## Recovery journal — `GET /api/admin/recovery/journal`
Append-only operational log: scans, auto-heals, precision-migration reports, and failure entries
(`approval_execution_failed`, `withdrawal_reject_refund_failed`) classified `MANUAL_REVIEW` / `CRITICAL`.

## No blind replay
Outbox replay relies on consumer idempotency (`outbox_consumed`). Hold expiry uses the atomic
single-winner terminal transition. Idempotency-Key operations dedup by key. Terminal states cannot be
re-entered.

## Verified crash-window behaviour
- stale PROCESSING idempotency → flagged, auto-healed, retry with same key succeeds;
- ACTIVE hold past expiry → excluded from available balance immediately (lazy-expiry) even before heal;
- rejected-withdrawal refund failure → status reverted to pending + CRITICAL journal entry, so the refund
  is never silently lost and is retryable.

## TARGET / FUTURE
A startup-time recovery pass and provider-observation retry (query provider for uncertain settlements)
once real provider adapters exist.
