# CVLN Refund, Reversal & Fees (P0.1-B3)

> As-built. **CURRENT / IMPLEMENTED** vs **TARGET / FUTURE** kept explicit.
> Every value movement goes through the double-entry ledger (source of truth).

## Fees engine (REAL)
- Configurable policy in `settings.fee_policy`: `{ operation: { pct, flat } }`.
  Valid operations: `withdrawal, capture, marketplace, conversion, transfer, deposit`.
  `PUT /api/admin/fees` (admin) validates operation whitelist + `0 <= pct <= 1`, `flat >= 0`.
  `GET /api/admin/fees` reads it. `POST /api/fees/quote {operation, amount}` returns `{fee, net}` (no charge).
- `apply_fee(user, operation, base, enforce)` posts a `Frais` ledger entry (user cash → revenue) and a
  transaction row, emits `Financial.FeeApplied`. `enforce=True` debits atomically only if available covers it.
- **Wired**: `POST /api/withdrawals` charges the `withdrawal` fee. The withdrawal debit itself is now an
  **atomic** conditional `find_one_and_update` on `users` (`$expr balance_cc - held_cc >= amount + fee`),
  so it honours B2 holds and is race-safe; compensation restores the debit if post-debit work fails.
- Default policy is empty ⇒ 0 fees ⇒ no behaviour change unless an admin configures it.

## Refund engine (REAL)
`POST /api/refunds {original_tx_id, amount?, reason}` (admin, idempotent via `Idempotency-Key`):
- Only an **outflow** tx (`amount < 0`) is refundable (`400 ONLY_OUTFLOW_REFUNDABLE`); `404` only if the tx
  does not exist; ledger entry must be 2-legged (`400 LEDGER_ENTRY_UNSUPPORTED`).
- **Atomic cumulative guard** on the ORIGINAL transaction:
  `find_one_and_update({reversed != true, $expr: refunded_cc + amt <= principal}, $inc refunded_cc)`.
  ⇒ partial refunds allowed up to principal; over-refund ⇒ `409`. Compensation decrements `refunded_cc`
  if the subsequent ledger post fails.
- Credits the user (reverse of the original counterparty leg), records a `Remboursement` tx + a `refunds`
  record (`COMPLETED`, `fully_refunded`), state history, `Financial.RefundCompleted` / `RefundRejected`.
- **Verified concurrency**: 20–24 concurrent partial refunds summing beyond principal ⇒ never over-refund.

## Reversal engine (REAL)
`POST /api/reversals {original_tx_id, reason}` (admin, idempotent):
- **Atomic single-winner guard**: `find_one_and_update({reversed != true, refunded_cc == 0}, $set reversed=true)`.
  ⇒ reverse exactly once (`409 ALREADY_REVERSED_OR_REFUNDED`); a reversed tx cannot be refunded and a
  refunded tx cannot be reversed (mutual exclusion). Compensation clears the flag if posting fails.
- Posts the **exact inverse** of every original posting (sums to 0 ⇒ ledger stays balanced), updates the
  user cash cache, records an `Extourne` tx + a `reversals` record, `Financial.ReversalCompleted`.
- **Verified concurrency**: 16 concurrent reversals of one tx ⇒ exactly one winner.

## Admin withdrawal decisions (atomic)
`POST /api/admin/withdrawals/{id}/approve|reject` flip `pending → processed|rejected` via an atomic
`find_one_and_update` **first** — the status transition is the lock, so concurrent/retried decisions
cannot double-process. `reject` refunds **principal AND the withdrawal fee** via the ledger.

## Integrity
`GET /api/admin/financial-health` reports `refunds`/`reversals` counts and stays `ledger_balanced` +
`jcc_supply_reconciled` after every refund/reversal/fee (all are balanced 2-leg postings).

## Events
`Financial.FeeApplied`, `Financial.FeePolicyUpdated`, `Financial.RefundCompleted`, `Financial.RefundRejected`,
`Financial.ReversalCompleted`.

## Status
`fees_engine = REAL`, `refund_engine = REAL`, `reversal_engine = REAL` (see `/api/system/status`).
`settlement_engine = PARTIAL`, `outbox_events = PLANNED`. Stripe SANDBOX, card_issuing MOCK unchanged.

## TARGET / FUTURE (NOT REAL)
- Refund of multi-leg ledger entries (currently rejected as UNSUPPORTED).
- Idempotency-Key on admin decision endpoints (atomic status flip already prevents double-credit).
- Fee wiring for capture/marketplace/conversion/transfer (helper is ready; only withdrawal is wired).
- Refund/reversal of external-settlement transactions (needs P0.1-B4 settlement + outbox).
