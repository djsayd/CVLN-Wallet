# CVLN Financial State Machines (P0.1-B2)

> As-built documentation. **CURRENT / IMPLEMENTED** is separated from **TARGET / FUTURE**.
> A planned transition is never described as REAL.

## Purpose
Financial operations must not mutate their own status freely. State changes are centralised and
enforced **atomically at the DB layer** so that concurrency, retries and adverse transitions can
never create, reserve or spend more value than the Financial Core authorises.

## Pipeline
```
Request → Idempotency → Validation → Policy/Risk → State Machine → Authorization(Hold)
        → Execution → Ledger → Settlement → Reconciliation → Audit/Event
```

## CURRENT / IMPLEMENTED — Hold state machine
The first real state machine in the Core governs **BalanceHold** (authorization/capture).

| From | To | Allowed | Conditions | Side effects |
|---|---|---|---|---|
| (none) | ACTIVE | ✅ | atomic reservation succeeded (available ≥ amount) | `held_cc += amount`, history, `Financial.HoldCreated` |
| ACTIVE | PARTIALLY_CAPTURED | ✅ | `0 < amt < remaining` | `held_cc -= amt`, ledger debit, `Financial.HoldPartiallyCaptured` |
| ACTIVE / PARTIALLY_CAPTURED | CAPTURED | ✅ | `amt == remaining` | `held_cc -= amt`, ledger debit, `Financial.HoldCaptured` |
| ACTIVE / PARTIALLY_CAPTURED | RELEASED | ✅ | manual/system | `held_cc -= remaining`, `Financial.HoldReleased` |
| ACTIVE / PARTIALLY_CAPTURED | EXPIRED | ✅ | `expires_at <= now` (lazy) | `held_cc -= remaining`, `Financial.HoldExpired` |
| CAPTURED / RELEASED / EXPIRED | * | ❌ DENIED | terminal | none (HTTP 409 `CAPTURE_INVALID` / `INVALID_STATE_TRANSITION`) |

Terminal states (`CAPTURED`, `RELEASED`, `EXPIRED`) never leave.

## Central transition mechanism
There is **no read-then-write**. The `status` field itself is the lock:
- `_terminate_hold(hold_id, terminal, ...)` uses `find_one_and_update` filtered on
  `status ∈ {ACTIVE, PARTIALLY_CAPTURED}` → **exactly one** caller wins the flip; the loser gets `None`
  (idempotent no-op). Only the winner adjusts `held_cc`.
- Capture uses `find_one_and_update` with `$expr: amount - captured >= amt` and `$inc: captured += amt` →
  over-capture and double-capture are impossible.
Every transition writes an **append-only** row to `financial_state_history`
(`previous_state, new_state, actor, reason, correlation_id, created_at`). History is never rewritten.

## Concurrency
Because the terminal transition is a single conditional document update, `ACTIVE → CAPTURED` (or
`→ RELEASED` / `→ EXPIRED`) can only be won once. Verified by concurrent tests (see below).

## Idempotency vs State Machine
`Idempotency-Key` protects against **repeating the same logical request** (one reservation, one ledger
posting). The state machine protects against **invalid business transitions**. They are independent and
both enforced on `/api/holds`.

## Kill switches
`withdrawals`, `card`, `agents` are enforced at their operation endpoints. A kill switch may block the
creation of new operations (and thus new holds for that op), but **release and expiry are always allowed**
so funds can be restituted.

## Failure & recovery
Standalone MongoDB has **no multi-document transactions**. Cross-document consistency
(`users.held_cc` ↔ `balance_holds`) uses a deterministic compensation strategy: the atomic status flip
runs first, then `held_cc` is adjusted. A crash in the window can only **under-release** (funds stay
locked), never over-release / double-spend. The Integrity Engine detects drift; `POST /api/admin/holds/rebuild`
repairs it in one direction only (Holds → held_cc).

## Invariants
- No illegal transition succeeds.
- No terminal state is replayed.
- History is append-only.
- No value movement happens outside the Financial Core / double-entry ledger.

## Current coverage
| Capability | Status | Tests |
|---|---|---|
| Hold state machine (ACTIVE→CAPTURED/PARTIAL/RELEASED/EXPIRED) | REAL | curl + testing_agent |
| Append-only history | REAL | curl |
| Concurrent-transition safety | REAL | 10× concurrent holds, capture/expiry race |

## TARGET / FUTURE (NOT REAL)
Refund, Reversal, external Settlement, Invest orders, Crypto withdrawals, FX conversions,
disputes/chargebacks — all remain PLANNED and are not governed by a real state machine yet.

## Status
`state_machines = REAL` (hold lifecycle). See `/api/system/status`.
