# CVLN Wallet — Roadmap deux pistes (BUILD × ACTIVATION)

Objectif : passer de ~30% à 90–100% production-ready. La production réelle naît de la **rencontre** des deux pistes. On n'active plus tout d'un coup ; chaque capacité a un statut honnête.

## Pipeline cible (end-to-end)
`FREK-ID/User → KYC → Account → Money → Card/Invest/Crypto → Authorization → Risk → Provider réel → Execution → Double-entry Ledger → Settlement → Reconciliation → FREKCORE/Proof → Audit → Reporting`

| Étape | Statut | Piste |
|---|---|---|
| FREK-ID / User | REAL | BUILD ✓ |
| KYC/AML | PLANNED | **ACTIVATION** (provider + légal) |
| Account | REAL (cash + coffres, ledger) | BUILD ✓ |
| Money (JCC/EUR) | REAL / dépôt SANDBOX | BUILD ✓ / ACTIVATION (banking) |
| Card | ledger REAL, issuing MOCK | BUILD ✓ / ACTIVATION (issuer) |
| Invest / Crypto / FX / RWA | PLANNED | BUILD (code) + ACTIVATION (broker/CASP) |
| Authorization + Risk | REAL (agent/card), à généraliser | BUILD |
| Provider réel | Stripe SANDBOX; autres PLANNED | ACTIVATION |
| Execution | REAL | BUILD ✓ |
| Double-entry Ledger | **REAL** | BUILD ✓ |
| Settlement | PARTIAL | BUILD + ACTIVATION |
| Reconciliation | PARTIAL (intégrité ledger) | BUILD |
| FREKCORE / Proof | PLANNED (interfaces) | BUILD + ACTIVATION |
| Audit | REAL | BUILD ✓ |
| Reporting | PARTIAL (stats admin) | BUILD |

## BUILD TRACK (ce que je code, un module à la fois, adossé au Financial Core)
1. **Idempotency API** (header Idempotency-Key) — durcir le Core.
2. **Account layer + State machines** (états transaction/card/withdrawal formalisés).
3. **Invest** (stocks/ETF sandbox via MarketDataProvider) — ledger multi-asset.
4. **Crypto** (BTC/ETH/stablecoins, custody provider abstraction) — ledger multi-asset.
5. **FX engine** (quotes/spread/fees).
6. **Business Wallet** (org/subaccounts/roles/budgets).
7. **RWA / tokenized assets**.
8. **Reconciliation & Reporting** (jobs + exports).
9. **Provider abstraction** (PaymentProvider, CardProvider, BrokerProvider, KYCProvider, …).

## ACTIVATION TRACK (hors code — dépendances externes, ne jamais marquer REAL sans validation)
- **Structure juridique** (EMI / agent / partenariats).
- **KYC/AML** provider + sanctions screening.
- **Issuer** + **Card processor** (→ carte réelle, 3DS, TPE).
- **Apple/Google** provisioning (network tokenization).
- **Broker** (titres) + **CASP/Custodian** (crypto).
- **Banking / Open-banking** providers (IBAN, payouts, agrégation).
- **Certifications** : PCI DSS, GDPR, PSD2/PSD3, SCA.

## Ops & résilience (à instrumenter progressivement)
Monitoring/observabilité (logs structurés, metrics, tracing, health) · Sauvegardes + Disaster Recovery · Tests de charge · Pentest · Gestion d'incident (runbooks) · Rotation des secrets · Permissions/RBAC · Fraude/Risk rules · Support utilisateur · **Procédures de réconciliation** (ledger ↔ providers).

## Statuts machine-lisibles
Voir `GET /api/system/status` (feature flags + capabilities REAL/SANDBOX/MOCK/PLANNED) et `GET /api/me/kyc`.
