# CVLN Wallet — PRD

## Problem
Premium fintech wallet (Revolut/Qonto style) for CVLN Group cultural ecosystem. React + FastAPI + MongoDB. Currency Jeton CC (1 JCC=1.50 EUR). FREK-ID identity, FREK Score.

## Implemented (2026-06)
- Google auth (Emergent-managed), per-user FREK-ID, session cookies
- Dashboard, Wallet (send/buy CC), Transactions (filter/search), Coffres (create/deposit/withdraw/delete), Convertir (EUR<->JCC), Marketplace (buy), Ecosysteme, FREK-ID profile, Parametres
- Developer API: 12 entity wallets each with API key (X-API-Key auth). Endpoints /api/v1/entity/{me,balance,transactions,transfer,charge}, /api/v1/frek/{id}. Owner view /api/entities + rotate-key. All tested via curl.

## Backlog
- P1: QR scan, Pay flow, real Stripe top-up for Acheter CC
- P1: webhooks for entity API, rate limiting on API keys
- P2: charts, notifications persistence

## Update 2026-06 — Money integrity + Stripe
- New accounts start at 0 CC (no fake money); min deposit enforced.
- EUR->CC via Stripe Checkout (multi-currency); CC credited only after payment confirmed (poll + webhook /api/stripe/webhook).
- Withdrawals CC->bank = payout requests (pending) validated by admin; refund on reject. Real IBAN payouts need Stripe Connect at go-live.
- Admin back-office (ADMIN_EMAIL=djsayd972@gmail.com): settings (rate, min deposit, reserve), stats, withdrawal approve/reject.
- Stripe sandbox provisioned (Flow A). Onboarding pending KYC.

## Update 2026-06 — Agent Skills layer (P0)
- Séparation admin/user: API entités + back-office + gestion agents = admin only (nav masquée + require_admin backend).
- Registre de skills (Wallet.Balance, Assets.Portfolio, Payments.Request/Send, FREK.Identity, KORA.StreamIncome).
- Scopes read/request/sign/execute/admin; agents avec token, spending_limit_cc, session_ttl, revoke.
- Flow PREPARE(/agent/intent) -> CONFIRM(owner) -> EXECUTE; risk engine + preview + audit_logs.
- Docs: docs/AGENTSKILL-WALLET-MAPPING.md, CVLN-WALLET-AGENTS.md, CVLN-WALLET-SECURITY.md.
- Multi-chain BTC/ETH/SOL = préparé non activé (P2). Frontend Agents UI = P1.

## Update 2026-06 — Agent Factory UI (P0 closed)
- Écran Admin /agents: onglets Agents/Skills/Intents/Audit; création agent (scopes least-privilege, plafond, TTL), révoke, simulate intent.
- Modal de confirmation transaction (preview: agent, action, montant, destination, risque) — Confirmer/Refuser.
- Endpoints ajoutés: GET /admin/skills, GET /admin/intents, POST /agent/intent/{id}/decline.
- E2E navigateur OK: create->simulate->preview->confirm->execute; solde 1000->950; audit 4 events.
- Nav admin-only: API, Agents, Admin (masqué pour users + require_admin backend).
