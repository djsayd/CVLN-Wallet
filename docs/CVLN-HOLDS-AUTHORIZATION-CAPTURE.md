# CVLN Holds, Authorization & Capture (P0.1-B2)

> As-built. **CURRENT / IMPLEMENTED** vs **TARGET / FUTURE** kept explicit.

## Purpose
`ledger_balance` is not always equal to spendable funds. Holds reserve value between **authorization**
and **capture/settlement**, so we distinguish total, reserved and available balance.

## Core model
```
ledger_balance_cc   = balance derived from the double-entry ledger (source of truth)
held_cc             = effective reserved amount (denormalised cache on users)
available_balance_cc = ledger_balance_cc - effective_held_cc
```
`held_cc` is a **reconstructible cache**, never an independent source of truth. `available_balance` is
always derivable; it is never persisted as a new source of truth.

## BalanceHold model
`hold_id, user_id, asset, amount, captured, status, reason, correlation_id, created_at, expires_at, released_at`
- `remaining = amount - captured`
- `status ∈ {ACTIVE, PARTIALLY_CAPTURED, CAPTURED, RELEASED, EXPIRED}`

## Lifecycle
```
ACTIVE ──partial──▶ PARTIALLY_CAPTURED ──▶ CAPTURED
  │                        │
  ├────────── RELEASED ◀───┘
  └────────── EXPIRED (expires_at ≤ now)
```

## Atomic reservation (anti-double-spend)
Reservation is a **single conditional document update** on `users` — the check and the increment happen
in the same op, never read-then-write:
```
find_one_and_update(
   { user_id, $expr: { $gte: [ { $subtract: [ balance_cc, {$ifNull:[held_cc,0]} ] }, amount ] } },
   { $inc: { held_cc: amount } } )
```
`None` returned ⇒ `INSUFFICIENT_AVAILABLE_FUNDS`. If the subsequent `balance_holds` insert fails, a
compensation decrements `held_cc` back. Cross-doc atomicity is impossible on standalone MongoDB (no
transactions) — this is documented and handled by compensation + rebuild, never faked.

### Verified anti-double-spend
- Balance 100, 30 already held → 10 concurrent holds of 20 → **exactly 3 accepted**, held=90, available=10.
- 5 concurrent requests with the **same Idempotency-Key** → **one** reservation (held=40, 1 hold row).
- Same key + different payload → `409 IDEMPOTENCY_CONFLICT`.

## Authorization ≠ Capture ≠ Settlement
A hold authorises/reserves funds. It is not a definitive settled movement. Capture converts a reserved
amount into a real ledger spend.

## Capture
- Atomic claim `$inc captured` guarded by `$expr amount - captured >= amt` (no over-capture, no double).
- Full → `CAPTURED`; partial → `PARTIALLY_CAPTURED` (`captured`/`remaining` tracked).
- Each capture: `held_cc -= amt` **and** a real ledger debit via `add_transaction` (category `Hold`).

## Release
- `_terminate_hold(..., RELEASED)` flips status atomically (single winner) and returns `remaining` to
  available. **Double release is impossible**; a repeat returns the existing terminal state (`idempotent:true`).

## Expiry & Lazy-expiry
**Expiry is correctness; cleanup is maintenance.** A hold with `expires_at ≤ now` stops reducing available
balance immediately, even with no worker run. `reconcile_expired_holds(user_id)` is invoked before every
reservation and on balance reads: it atomically flips due holds to `EXPIRED` and decrements `held_cc`.
Idempotent — two concurrent expiries release only once. Capture on an expired/terminal hold → `409`.

## Available balance API — `GET /api/wallet`
```
balance_cc            (ledger / total)
held_cc               (effective reserved, post lazy-expiry)
available_balance_cc  (= balance_cc - held_cc)
```
Backward compatible: `balance_cc` unchanged; new fields added.

## Integrity — `GET /api/admin/holds/integrity` (+ `financial-health.holds_health`)
- `held_cc >= 0`
- `held_cc == Σ remaining of non-expired ACTIVE/PARTIALLY_CAPTURED holds`
- `held_cc <= balance_cc` (available ≥ 0)
- no expired hold still counted; captured/released not counted
Mismatch emits `Financial.HoldIntegrityMismatch`.

## Cache reconstruction — `POST /api/admin/holds/rebuild`
One direction only: **Holds → held_cc**. Never invents or edits holds from the cache.

## Kill-switch interaction
New-operation holds can be blocked; **release and expiry always remain possible** so funds are restituted.

## Events (really emitted)
`Financial.HoldCreated`, `Financial.HoldCaptured`, `Financial.HoldPartiallyCaptured`,
`Financial.HoldReleased`, `Financial.HoldExpired`, `Financial.HoldRejectedInsufficientFunds`,
`Financial.HoldIntegrityMismatch`.

## Tests executed
active hold · concurrent holds (double-spend) · insufficient funds · idempotency+concurrency ·
partial capture · full capture · over-capture reject · double-capture reject · release ·
double-release idempotent · lazy-expiry · capture-after-expiry reject · integrity green.

## Current status
| Component | Status | Evidence |
|---|---|---|
| Atomic reservation | REAL | concurrency curl + testing_agent |
| Capture (full/partial) | REAL | curl + testing_agent |
| Release / double-release | REAL | curl |
| Lazy-expiry | REAL | curl |
| Integrity + rebuild | REAL | curl |

`holds = REAL` — see `/api/system/status`.
