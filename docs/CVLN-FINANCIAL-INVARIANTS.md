# CVLN Financial Invariants (P0.1)

| # | Invariant | Statut |
|---|---|---|
| I1 | Chaque écriture équilibrée par asset (Σ postings=0) | **REAL** (rejet 500 sinon) |
| I2 | Écriture historique jamais éditée/supprimée pour corriger | **REAL** (ledger append-only) |
| I3 | Correction = écriture compensatoire | **REAL** (delete_coffre/refund via nouvelles écritures) |
| I4 | Balances dérivables du ledger | **REAL** (`ledger_balance`) |
| I5 | Les caches ne sont jamais la source de vérité | **REAL** (integrity compare) |
| I6 | Une opération logique jamais comptée deux fois | **REAL** (idempotency API + ledger) |
| I7 | Toute mutation traçable | **REAL** (audit_logs + ledger ref) |
| I8 | Toute transition d'état autorisée | **PARTIAL** (agent/card intents; state engine générique PLANNED) |
| I9 | Opération échouée ne produit pas de mouvement définitif silencieux | **PARTIAL** (exceptions avant ledger_post; holds PLANNED) |
| I10 | Écritures multi-asset équilibrées par asset | **REAL** (ledger_post par asset) |
| I11 | Pas de double-dépense concurrente | **PARTIAL** (idempotency + checks; holds/available-balance PLANNED) |
| I12 | Représentation monétaire sûre | **PARTIAL** (floats + rounding 2 déc.; minor-units/Decimal PLANNED) |

## Ce qui reste PLANNED (documenté, non maquillé)
State-machine engine générique, Holds/available-balance, Refund/Reversal engines dédiés, Fees engine, Settlement externe, Reconciliation cases, Outbox pattern, maker/checker, kill-switches, Decimal minor-units. Architecture prête à les recevoir sur le Financial Core sans refactor comptable.

Voir `GET /api/admin/financial-health` et `GET /api/admin/ledger/integrity` pour la vérification runtime.
