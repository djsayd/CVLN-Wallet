# CVLN Monetary Precision & Minor Units (P0.1-B5)

> As-built. Honest status: **monetary_precision = PARTIAL**.

## Asset Registry (REAL)
`ASSET_REGISTRY` defines each asset explicitly (no hardcoded historical precision):
`asset_code, decimals, minor_unit, rounding, enabled, type`.
- `JCC`: decimals 2, minor_unit 100, HALF_UP, internal_token.
- `EUR`: decimals 2, minor_unit 100, HALF_UP, fiat.
`GET /api/assets` exposes it. Unknown/disabled asset → `400 ASSET_NOT_SUPPORTED`.

## Money helpers (REAL, centralised rounding)
- `to_minor(amount, asset)` → integer minor units via `Decimal` + the asset's rounding policy.
- `from_minor(minor, asset)` → float display value.
- `money_round(amount, asset)` → the single, centralised rounding entry point (no opportunistic `round()`
  in business code).
- `is_minor_exact(amount, asset)` → validates an amount is representable without precision loss.
`Decimal` is used for all conversions; binary float is never the source of a rounding decision.

## Storage representation vs API display
- Storage: legacy amounts remain float caches (`balance_cc`, ledger posting `amount`) — the ledger stays
  the accounting source of truth.
- API: new financial objects also expose integer minor units (e.g. settlement `amount_minor`).
- The migration endpoint validates that every stored amount is **minor-exact**, so the float cache and
  the canonical minor-unit value agree bit-for-bit within the asset granularity.

## Migration (dry-run, non-destructive)
`POST /api/admin/precision/migrate?dry_run=true` scans all ledger postings and user balances, reports
`ledger_postings_checked`, `balances_checked`, `representable`, and lists any non-representable amounts.
It is **idempotent and non-destructive** and logs a `recovery_journal` entry.
`dry_run=false` → `501 DESTRUCTIVE_MIGRATION_NOT_IMPLEMENTED` (honest: full integer-storage rewrite of
the historical ledger is deferred; that is why the capability is PARTIAL, not REAL).

## Why PARTIAL (not REAL)
The precision *infrastructure* (registry + Money + centralised rounding + validation + minor-unit
exposure) is live and used, and all current data is validated minor-exact. But storage is still float, so
until the ledger is migrated to integer minor-unit storage the capability is honestly **PARTIAL**.

## Verified
`0.1 + 0.2` style amounts are minor-exact; `to_minor`/`from_minor` round-trip; dry-run reports
`representable=true` on clean data; economic values unchanged.

## Rounding policy scope
Fees (`_compute_fee` / `money_round`), captures, refunds and future FX all round through the same
centralised policy defined in the Asset Registry.
