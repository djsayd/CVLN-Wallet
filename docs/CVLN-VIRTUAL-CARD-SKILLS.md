# CVLN Virtual Card — Agent Skills

Skills ajoutées au registre (`SKILLS`) découvrables par CVLN Agent Factory via `GET /api/agent/skills` :

| Skill | Capability | Scopes | Risk | Confirmation |
|---|---|---|---|---|
| Card.View | card_view | read | LOW | non |
| Card.Freeze | card_freeze | request | MED | oui |
| Card.SetLimits | card_limits | admin | HIGH | oui |
| Card.Pay | card_pay | request+sign+execute | HIGH | oui |

## Card.Pay — paramètres
`{ "amount_cc": number, "merchant": string, "payment_type": "online"|"merchant"|"tpe" }`

## Contrôles appliqués (create intent + execute)
- Carte inexistante → 400
- Carte `frozen` → 403 (paiement refusé)
- `agent_enabled=false` sur la carte → 403
- montant > `agent.spending_limit_cc` → 403
- montant > `card.per_tx_limit_cc` → 403
- montant > solde → 400
- HIGH risk → confirmation humaine obligatoire avant execute
- `execute` refusé sans le scope `execute`

## Ce qu'un agent NE peut PAS faire (jamais)
Récupérer PAN/CVV, désactiver la sécurité, supprimer les plafonds, contourner la confirmation, retirer du cash, changer le propriétaire. L'agent manipule des **capacités**, pas des secrets de carte.

## Endpoints utilisateur (contrôles directs)
`GET /api/card`, `POST /api/card/freeze`, `POST /api/card/unfreeze`, `PUT /api/card/limits`, `GET /api/card/transactions`.

## Mobile wallet (Agent Skills — non ajoutées comme actives)
`Card.MobileWalletStatus`, `Card.AppleWalletEligibility`, `Card.GoogleWalletEligibility` = **PLANNED**. `Card.AddToAppleWallet/AddToGoogleWallet` traitées comme sensibles → nécessiteraient confirmation propriétaire + issuer. Non activées.
