# CVLN Wallet — Agents (intégration CVLN Agent Factory)

Réf. Agent Factory: https://github.com/frekcore/CVLNAgentfactory (non réimplémenté — CVLN Wallet expose des capacités découvrables).

## Découverte des skills
Un agent s'authentifie via header `X-Agent-Token` puis appelle `GET /api/agent/skills`.
Réponse: liste des skills avec `name, capability, scopes, risk, confirm, supported_networks, authorized` (selon les scopes accordés à cet agent).

## Format Skill (déclaration)
```
name, description, version, capabilities, tools, inputs, outputs,
permissions (scopes), risk_level, required_confirmation, supported_networks
```
Exemple `Payment.Send` → capability `send_asset`, permissions `wallet.read + wallet.sign + wallet.execute`, risk `HIGH`, confirmation `REQUIRED`.

## Flow transactionnel (séparation stricte)
```
Agent → Intent → Skill → Permission Check → Risk Engine →
Transaction Preview → User Confirmation → Signing → Execute → Status → Audit Log
```
- `POST /api/agent/intent` = **PREPARE** (permission + risk + preview, aucun fonds déplacé).
- `POST /api/agent/intent/{id}/confirm` = **CONFIRMATION humaine** (propriétaire du wallet uniquement).
- `POST /api/agent/intent/{id}/execute` = **EXECUTE** (refusé si confirmation requise non obtenue).

PREPARE, SIGN et EXECUTE sont des étapes distinctes: un agent ne passe jamais d'`intention → transaction` en une fois.

## Administration (admin uniquement)
- `POST /api/admin/agents` créer un agent + scopes + `spending_limit_cc` + `session_ttl_hours`.
- `GET /api/admin/agents` / `POST /api/admin/agents/{id}/revoke`.
- `GET /api/admin/audit` journal complet.

## Interfaces préparées (P1)
FREK.Identity/Attestation/Proof/Anchor/Certificate/EventPassport/Timestamp et
KORA.CreatorWallet/StreamIncome/SplitEngine/RightsLedger/ValueEngine : déclarées comme capacités, à brancher sans réimplémenter FREKCORE/KORA.
