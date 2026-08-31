# CVLN Virtual Card — AUDIT

## État initial (avant cette mission)
- **Aucun modèle Card en base**, aucune collection `cards`.
- La "carte virtuelle" était **UI-ONLY** : un simple visuel dégradé dans `Wallet.js` affichant le solde/FREK-ID. Aucune donnée, aucun état, aucune logique.
- **Aucun issuer / processor** connecté. Pas de PAN, pas de CVV, pas de tokenisation, pas de 3DS, pas de webhook carte, pas de settlement.
- **TPE / NFC / Apple Pay / Google Pay** : inexistants.

## État après cette mission
| Élément | Statut |
|---|---|
| Modèle Card (DB) | **REAL** (collection `cards`, seed auto par utilisateur) |
| PAN / CVV | **NON générés / NON stockés** (seul `last4` masqué) |
| Émission (issuing) | **MOCK** (`issuing_status: MOCK`) — pas d'issuer certifié |
| Freeze / Unfreeze | **REAL** (statut `active`/`frozen`) |
| Limites (daily, per-tx, online/tpe/agent) | **REAL** (appliquées au flow de paiement) |
| Paiement carte | **REAL sur ledger CC** via flow Agent Intent (débit CC), **MOCK côté réseau carte** |
| Refund / chargeback | **PLANNED** |
| Online 3DS / capture / webhook | **NOT IMPLEMENTED** (pas de processor carte) |
| TPE / NFC / contactless | **NOT IMPLEMENTED** |
| Apple Wallet / Google Wallet | **PLANNED** (abstraction + eligibility, jamais activé) |

## Flow de paiement carte (réel, via moteur d'intents existant)
`Agent → Card.Pay intent → permission(scopes) → policy(carte active + agent_enabled) → risk(plafond agent ∩ plafond carte ∩ solde) → preview → confirmation utilisateur → execute → débit ledger CC (category "Card") → audit (Card.PaymentCaptured)`

La politique la plus restrictive gagne : `min(agent.spending_limit, card.per_tx_limit, balance)`.
