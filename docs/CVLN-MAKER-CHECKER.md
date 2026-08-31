# CVLN Maker-Checker (P0.1-B5)

> As-built. Status: **maker_checker = REAL**.

## Purpose
Sensitive administrative financial operations require a **second, different** approver. Enforced in the
BACKEND, not just the UI.

## Sensitive operations (`SENSITIVE_OPS`)
`manual_ledger_adjustment, fee_policy_change, kill_switch_critical, high_value_refund, settlement_override`.
A non-sensitive operation type → `400 OPERATION_NOT_SENSITIVE`.

## Approval request (`approval_requests`)
`approval_id, operation_type, payload, operation_payload_hash, maker_id, checker_id, status
(PENDING|APPROVED|REJECTED|EXPIRED), reason, correlation_id, execution_status
(NONE|EXECUTING|EXECUTED|FAILED), created_at, expires_at (24h), approved_at, rejected_at`.

## Guarantees (all verified)
- **Maker ≠ Checker**: `POST /api/admin/approvals/{id}/approve` with `maker_id == caller` → `403
  MAKER_CANNOT_BE_CHECKER`. Enforced both in the pre-check and in the atomic filter (`maker_id != caller`).
  No bypass via direct API call.
- **Payload immutability**: the stored `operation_payload_hash` must still match the payload at approval
  time → else `409 PAYLOAD_TAMPERED`. A changed payload requires a new approval.
- **Single execution**: approval is an atomic `find_one_and_update` (`status PENDING → APPROVED`) — the
  status flip is the lock. 20 concurrent checker approvals → exactly ONE executes, rest `409`.
- **Expiry**: an expired PENDING approval → `409 APPROVAL_EXPIRED`; recovery auto-heal flips stale
  PENDING approvals to `EXPIRED` (idempotent).
- **Execution**: `_execute_approved_operation` dispatches by type. `manual_ledger_adjustment` posts a
  balanced 2-leg ledger entry (value changes always go through the ledger). On execution failure the
  approval is marked `FAILED` and a `MANUAL_REVIEW` recovery_journal entry is written.

## Events / audit
`MakerChecker.RequestCreated/Approved/Rejected`, `Financial.MakerCheckerExecuted`. All decisions audited.

## TARGET / FUTURE
Configurable per-operation thresholds (e.g. auto-require maker-checker above a value); currently the
operation *type* is the trigger. High-value refund / settlement override handlers are recorded + audited
(no blind value creation) pending the domains that consume them.
