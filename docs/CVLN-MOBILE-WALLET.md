# CVLN Mobile Wallet — Apple Wallet / Google Wallet

## État de support (honnête)
| Fonctionnalité | Statut |
|---|---|
| Apple Wallet | **PLANNED / NOT ACTIVE** |
| Apple Pay | **NOT SUPPORTED** (pas d'issuer/processor) |
| Google Wallet | **PLANNED / NOT ACTIVE** |
| Google Pay | **NOT SUPPORTED** |
| In-app / Push provisioning | **NOT IMPLEMENTED** |
| Network tokenization / device token | **NOT IMPLEMENTED** |

## Pourquoi bloqué
Le provisioning Apple/Google exige un **issuer/processor certifié** (network tokenization, certificats/entitlements Apple, push provisioning Google), un partenariat émetteur et l'éligibilité carte. CVLN n'a pas encore d'issuer connecté → **aucune activation réelle**. Le bouton frontend affiche honnêtement le statut PLANNED et n'ajoute rien.

## Abstraction prête (à brancher au go-live issuer)
Endpoints : `GET /api/card/wallet-eligibility` (retourne `PLANNED`, `eligible:false`, `reason`), `POST /api/card/wallet/{platform}/provision` (retourne 501 tant qu'aucun issuer). Interface conceptuelle cible : `getAppleWalletEligibility / getGoogleWalletEligibility / createProvisioningSession / completeProvisioning / getProvisioningStatus / removeDeviceToken`.

## Device trust (à implémenter avec provisioning)
Modèle cible `Device{device_id, platform, app_instance, trusted, created_at, last_seen, revoked}` + Trust/Revoke/Remove-from-device. Audit : `MobileWallet.EligibilityChecked` déjà émis ; `ProvisioningStarted/Completed/Failed/DeviceAdded/DeviceRevoked/CardRemoved` à ajouter.

## Smart button (frontend)
Détecte la plateforme (iOS→Apple, Android→Google, autre→indisponible). N'affiche jamais Apple sur Android ni l'inverse. Éligibilité fournie par le backend avec raison lisible.

## Production readiness
NON prêt. Ne jamais marquer REAL avant validation avec l'infrastructure officielle issuer/processor.
