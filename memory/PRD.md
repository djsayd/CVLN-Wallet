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
