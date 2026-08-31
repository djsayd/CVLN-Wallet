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

## Update 2026-06 — Virtual Card + Mobile Wallet abstraction (P1)
- Modèle Card REAL (freeze/limits/online-tpe-agent toggles), PAN/CVV jamais generes (issuing MOCK).
- Card Skills: Card.View/Freeze/SetLimits/Pay dans lAgent Factory; Card.Pay via flow intent (policy carte inter agent inter solde).
- Page /Carte (nav Carte): visuel masque, freeze, plafonds, depenses du jour, historique carte+agent.
- Mobile Wallet Apple/Google = PLANNED (eligibility + provision 501, aucune fausse activation).
- Docs: CVLN-VIRTUAL-CARD-AUDIT/SKILLS/SECURITY.md + CVLN-MOBILE-WALLET.md.
- Tests curl 10/10 OK: seed, eligibility PLANNED, provision 501, plafond carte, confirm+capture 150, freeze bloque, solde 1000->850.

## Update 2026-06 — Financial Core Double-Entry Ledger (P0)
- RULE: aucun module ne gere ses balances; tout passe par ledger_post(). Card/Coffres/Stripe/Retraits/Transferts/Marketplace/Agent routes via le Core.
- ledger_entries equilibrees (Somme postings=0/asset), balances DERIVEES; users.balance_cc & coffres.amount_cc = caches verifies.
- Comptes systeme: issuance/stripe/external/clearing/fx/revenue. Idempotency via idempotency_key.
- Endpoints: GET /ledger/accounts, /ledger/entries, /admin/ledger/integrity. Page admin /ledger (Grand Livre).
- Doc docs/CVLN-FINANCIAL-CORE.md. Test: balanced=True, per_asset JCC=0, 0 divergence cache/derive.

## Update 2026-06 — P0.1 Financial Core Hardening
- Idempotency engine API (Idempotency-Key): idem_begin/idem_finish, collection idempotency_records (unique index). Applique a /actions/send et /withdrawals. Ledger idempotency_key unique.
- Integrity etendu + GET /admin/financial-health (ledger_balanced, jcc_supply_reconciled, circulation, idempotency counts, severity).
- system/status enrichi (idempotency_api REAL; holds/refund/reversal/fees/outbox/state_machines PLANNED; settlement PARTIAL).
- Docs: CVLN-IDEMPOTENCY.md, CVLN-FINANCIAL-INVARIANTS.md.
- Reste PLANNED (non maquille): state-machine engine, holds/available-balance, refund/reversal/fees engines, settlement externe, reconciliation cases, outbox, Decimal minor-units.

## Update 2026-06 — P0.1-B2 State Machine + Holds / Available Balance (REAL)
- **Anti-double-spend ATOMIQUE** (Mongo standalone, pas de tx multi-docs): réservation = 1 seul `find_one_and_update` sur `users` (`$expr balance_cc-held_cc >= amount` + `$inc held_cc`). Jamais de read-then-write. Compensation si l'insert du hold échoue.
- Cache `users.held_cc` (dénormalisé, reconstructible). `available_balance_cc = balance_cc - held_cc`. Exposé dans `GET /api/wallet` (backward-compatible: `balance_cc` conservé).
- Machine à états Hold: ACTIVE→PARTIALLY_CAPTURED→CAPTURED / RELEASED / EXPIRED. Le champ `status` est le verrou atomique (single-winner) → double capture/release impossibles. Historique append-only `financial_state_history` via `record_state`.
- Endpoints: `POST /api/holds` (idempotent via Idempotency-Key), `/api/holds/{id}/capture` (partielle/totale, débit ledger via add_transaction), `/api/holds/{id}/release`, `GET /api/holds`, `GET /api/holds/{id}/history`.
- **Lazy-expiry**: un hold `expires_at<=now` ne bloque plus le disponible immédiatement (sans worker). `reconcile_expired_holds` appelé avant chaque réservation, lecture wallet ET rapport d'intégrité.
- Intégrité: `GET /api/admin/holds/integrity` + `holds_health` dans `/admin/financial-health` (held>=0, held==Σ remaining actifs non-expirés, held<=balance). `POST /api/admin/holds/rebuild` (Holds→held_cc, un seul sens).
- Events: Financial.HoldCreated/Captured/PartiallyCaptured/Released/Expired/RejectedInsufficientFunds/IntegrityMismatch.
- `/api/system/status`: state_machines & holds → REAL. refund/reversal/fees/outbox restent PLANNED, settlement PARTIAL, stripe SANDBOX, card MOCK.
- Docs: CVLN-FINANCIAL-STATE-MACHINES.md, CVLN-HOLDS-AUTHORIZATION-CAPTURE.md.
- Vérifié testing_agent iteration_4: 68/69 + 32 tests holds (concurrence réelle: 10×20/100→max 5, 2×80→1 gagnant, idempotency-key concurrent→1 réservation, capture/release/expiry, race capture-vs-expiry, chaos load). 1 défaut trouvé (faux positif intégrité sur hold expiré non lu) → CORRIGÉ (reconcile avant check) et revérifié (32/32 pass).
- Prochain: P0.1-B3 Refund/Reversal/Fees, puis B4 Settlement/Reconciliation+Outbox, puis B5 Decimal/minor-units + maker/checker.
