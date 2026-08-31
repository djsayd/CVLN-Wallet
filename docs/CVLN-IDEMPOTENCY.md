# CVLN Idempotency Engine

## Objectif
Garantir qu'une même opération logique ne soit jamais comptabilisée deux fois (invariant I6), même en cas de double-click, retry, timeout, crash après commit, ou duplicate webhook.

## Deux niveaux
1. **API-level** (`idem_begin` / `idem_finish`) : header `Idempotency-Key`. Collection `idempotency_records`, index **unique** sur `idem_id = scope:user_id:key`.
2. **Ledger-level** (`ledger_post(idempotency_key=...)`) : index unique `ledger_entries.idempotency_key` → aucun double posting.

## Comportement (API)
| Cas | Résultat |
|---|---|
| Même clé + même payload, opération terminée | **replay** de la réponse stockée |
| Même clé + payload différent | **409 IDEMPOTENCY_CONFLICT** |
| Même clé pendant traitement | **409 IDEMPOTENCY_IN_PROGRESS** |
| Pas de clé | opération normale (clé optionnelle) |

## États IdempotencyRecord
`PROCESSING → COMPLETED`. (FAILED_RETRYABLE / FAILED_FINAL / EXPIRED = PLANNED.)

## Concurrence
Insert atomique avec index unique → `DuplicateKeyError` détecte la course. Pas de lock global.

## Couverture actuelle (honnête)
- **REAL** : `POST /api/actions/send`, `POST /api/withdrawals`.
- **Déjà idempotent autrement** : dépôts Stripe (`/payments/checkout` → `payment_transactions` par `session_id`; crediting protégé par update conditionnel `payment_status != paid`).
- **PLANNED (à câbler)** : coffre move/delete, marketplace buy, agent execute, refunds/reversals, futurs providers/webhooks. Le helper est réutilisable — câblage = 3 lignes par endpoint.
