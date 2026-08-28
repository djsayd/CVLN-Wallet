# AGENTSKILL — WALLET MAPPING (CVLN Wallet)

Source catalogue: https://agentskill.sh/q/Wallet (0 résultat direct → capacités dérivées du catalogue AgentSkill et réimplémentées CVLN-native). **Aucun code externe importé** — les skills servent de référence de capacités uniquement.

| Skill | Source (concept) | Fonction | CVLN Wallet | Agent Factory | Priorité | Action |
|---|---|---|---|---|---|---|
| Wallet.Balance | wallet/portfolio | Lecture solde CC/JCC | `/api/agent/skills` + `/api/wallet` | Capability `read_balance` | **P0** | ✅ Intégré |
| Assets.Portfolio | portfolio | Portefeuille + coffres | réutilise `/coffres`,`/wallet` | `read_portfolio` | **P0** | ✅ Intégré |
| Payments.Request | payments | Préparer une demande (aucun fonds) | intent `Payments.Request` | `request_payment` | **P0** | ✅ Intégré |
| Payments.Send | payments/transaction signing | Envoyer des CC (déplace des fonds) | intent flow + `add_transaction` | `send_asset` (HIGH) | **P0** | ✅ Intégré |
| FREK.Identity | identity | Lecture identité FREK | interface préparée (`/v1/frek`) | `read_identity` | **P1** | Interface prête |
| KORA.StreamIncome | royalties/creator | Revenus créateur | interface préparée | `prepare_income` | **P1** | Interface prête |
| Wallet.Recovery / Passkeys | wallet security | Récupération, passkeys | — | — | **P1** | Backlog (auth Google active) |
| Multi-chain (BTC/ETH/SOL) | web3/multi-chain | Signature on-chain | — | — | **P2** | Non activé (pas dans l'archi actuelle) |
| Swap/DeFi/NFT | defi | Swaps, NFT, .FK | — | — | **P2** | Backlog |
| Treasury bots illimités | treasury | Mouvements auto sans limite | — | — | **REJECT** | Incompatible sécurité (viole least-privilege) |

**Pour chaque skill retenue** (détail dans `docs/skills/*.md`): fonction, permissions (scopes), données manipulées, peut-elle signer/déplacer des fonds, risque, dépendances, statut.

## Réseaux supportés
Actuellement: **JCC (CC interne)** uniquement. BTC/ETH/SOL/stablecoins = architecture préparée (champ `supported_networks`) mais **non activés** — la compatibilité est décidée par l'archi CVLN, pas par une skill externe.
