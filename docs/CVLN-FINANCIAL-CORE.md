# CVLN Financial Core — Double-Entry Ledger

## Règle d'architecture (obligatoire)
**Aucun module ne gère ses propres balances.** Card, Invest, Crypto, FX, Rewards, JCC, Coffres et tout futur compte DOIVENT router chaque mouvement de valeur via `ledger_post()`. Les balances sont **dérivées du ledger** ; `users.balance_cc` et `coffres.amount_cc` ne sont que des **caches dénormalisés** vérifiés par le contrôle d'intégrité.

## Modèle
- `ledger_entries` : écriture = { entry_id, idempotency_key, description, category, asset, ref, postings[], created_at }.
- Chaque écriture est **équilibrée** : `Σ postings.amount == 0` (par asset). Rejet 500 sinon.
- Comptes : `acct_cash_{user_id}`, `acct_coffre_{coffre_id}`, et comptes système (`acct_sys_issuance|stripe|external|clearing|fx|revenue`).
- Balance d'un compte = `Σ postings.amount` (agrégation) — recalculable à tout moment.

## Conventions de contrepartie (par catégorie)
| Catégorie | Contrepartie système |
|---|---|
| Dépôt (Stripe) | acct_sys_stripe |
| Retrait | acct_sys_external |
| Conversion | acct_sys_fx |
| Reward | acct_sys_issuance |
| Marketplace / Card | acct_sys_revenue |
| Transfert / Agent / défaut | acct_sys_clearing |

## API
- `ledger_post(description, category, postings, asset, ref, idempotency_key)` — **point d'entrée unique** (idempotent par clé).
- `ledger_balance(account_id)` — balance dérivée.
- `GET /api/ledger/accounts` — comptes de l'utilisateur (balance dérivée + cache).
- `GET /api/ledger/entries` — journal de l'utilisateur.
- `GET /api/admin/ledger/integrity` — invariant global (Σ=0 par asset), nombre d'écritures, divergences cache↔dérivé, balances système.

## Idempotency
`ledger_post` accepte une `idempotency_key` : une même clé ne crée jamais deux écritures. `add_transaction(..., idempotency_key=...)` la propage. (Idempotency au niveau API = P1.)

## Intégrité (vérifiée en test)
`balanced=True`, `per_asset_sum={JCC:0.0}`, `cache_mismatches=[]`. Toutes les balances utilisateur sont reconstructibles depuis le ledger.

## Flows déjà routés via le Core
Dépôts Stripe, retraits, transferts, conversions, achats Marketplace, paiements Carte, paiements Agent, mouvements de Coffres, fermeture de Coffre — tous passent par `ledger_post` (via `add_transaction` ou appel direct).
