# CVLN Wallet — Sécurité des Agents

CVLN Wallet est un produit financier : toute action capable de signer/transférer/retirer/modifier des permissions = **HIGH RISK**. Aucune IA n'a de capacité financière illimitée par défaut.

## Modèle de permissions (least-privilege)
Scopes hiérarchiques, accordés explicitement par l'admin par agent :
- **read** — balance, portfolio, history, addresses, assets
- **request** — préparer transaction/swap/paiement (aucun fonds déplacé)
- **sign** — demander une signature
- **execute** — exécuter une transaction
- **admin** — modifier politiques/permissions/agents

Vérifications appliquées à chaque intent (`has_scopes`) :
- Skill inconnue → **DENIED** (400)
- Scope manquant → **DENIED** (403)
- `Payments.Send` sans `execute` → **DENIED** à l'exécution
- Montant > `spending_limit_cc` → **DENIED** (403)
- Session agent expirée → **DENIED** (401)
- Transaction HIGH/MED risk → **confirmation utilisateur obligatoire** avant execute

## Protections
- **Least privilege** + **explicit permissions** par agent.
- **Transaction preview** générée avant toute exécution.
- **Spending limits** par agent (CC).
- **Confirmation humaine** (propriétaire uniquement) pour risque élevé.
- **Audit logging** de toutes les actions (`audit_logs`): created/prepared/confirmed/executed/denied/revoked/expired.
- **Agent identity** (token dédié), **session expiry** (`session_ttl_hours`), **revocation** (`/revoke`).

## Séparation Admin / Utilisateur
- Pages/API **admin uniquement** : back-office `/admin`, API entités `/developers` + `/api/entities*`, gestion agents `/api/admin/agents*`, audit, réglages monétaires, validation retraits.
- Un utilisateur standard **ne voit ni** les clés API des entités, **ni** le back-office, **ni** la gestion d'agents (nav masquée + garde backend `require_admin`).

## Principe final
INTELLIGENCE → INTENTION → PERMISSION → SIGNATURE → EXÉCUTION, avec contrôle à chaque étape.
