# CVLN Virtual Card — Sécurité

## PCI / données sensibles
- **PAN et CVV ne sont jamais générés ni stockés** (émission MOCK). Seul `last4` masqué est conservé.
- Affichage UI toujours masqué : `•••• •••• •••• 1234`.
- Aucune donnée sensible carte dans les logs.
- Au passage à un issuer réel : utiliser un token processor, jamais le PAN en base.

## Risk Engine (réutilisé de la couche Agent)
Considère montant, marchand (préparé), agent, skill, carte, plafonds, solde → LOW/MED/HIGH/DENIED.
Politique effective = `Card Policy ∩ Agent Policy ∩ Wallet Policy` (la plus restrictive gagne).

## Audit
Événements : `Card.Frozen`, `Card.Unfrozen`, `Card.LimitChanged`, `Card.PaymentCaptured`, `MobileWallet.EligibilityChecked`, `MobileWallet.ProvisioningFailed`. Associés à user_id / agent_id / intent_id.

## Freeze cohérence
Carte `frozen` → tout paiement (agent ou direct) est refusé au niveau intent (prepare) ET execute. En production avec issuer, le token provisionné devrait aussi être suspendu.

## Séparation admin/user
`Card.SetLimits` (agent) = scope `admin`. Les contrôles carte utilisateur restent sur le compte du propriétaire. Le back-office reste admin-only.
