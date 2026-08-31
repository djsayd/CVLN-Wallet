# CVLN Outbox / Inbox / Delivery (P0.1-B4)

> As-built. Honest status: **outbox_events = PARTIAL** on standalone MongoDB.

## The problem
A financial mutation and its event must not be able to permanently lose one of the two.
Standalone MongoDB has **no multi-document transactions**, so a strict transactional outbox is
**not** achievable. We therefore build the strongest achievable guarantee and label it PARTIAL.

## Strategy (PARTIAL, honest)
- Events are **idempotent**: `outbox_events.event_id` is unique; `emit_event` swallows duplicate inserts.
- Delivery is **at-least-once**: a background `outbox_worker` polls `PENDING/RETRY` events, atomically
  claims one (`status → DELIVERING`, `$inc attempts`), delivers, then marks `DELIVERED`.
- **Consumer idempotency**: `_deliver_event` inserts into `outbox_consumed` (unique `event_id`); a replay
  is delivered again but produces exactly **one** business effect.
- **Backoff + dead-letter**: exponential backoff (cap 60s); after 8 attempts → `DEAD_LETTER`.
- **Recovery**: the recovery scanner detects undelivered / dead-letter events; `POST /api/admin/outbox/{id}/replay`
  safely re-queues (consumer dedup prevents double effect).

Because emit happens *after* the mutation (not atomically with it), a crash in the tiny window between
them can drop an event. This is why the status is **PARTIAL**, not REAL. Mitigation: events are
reconstructible from aggregate state, and the recovery scan surfaces gaps for controlled regeneration.

## Outbox event model (`outbox_events`)
`event_id (unique), event_type, aggregate_type, aggregate_id, correlation_id, causation_id, payload,
status (PENDING|DELIVERING|DELIVERED|RETRY|DEAD_LETTER), attempts, created_at, available_at,
delivered_at, last_error`.

## Webhook inbox (`webhook_inbox`)
Unique `(provider, provider_event_id)`; fields `payload_hash, received_at, processing_status, attempts,
last_error, payload, result`. Same webhook received 2/10/100 times → one mutation. See
CVLN-SETTLEMENT-RECONCILIATION.md for dedup / conflict / out-of-order / provider-scope behaviour.

## Verified
100 duplicate webhooks → 1 effect; replay → `outbox_consumed` stays 1 per event; dead-letter surfaced as
CRITICAL by recovery scan; 197-test regression green.

## Known non-blocking gaps
- No TTL/archival: `outbox_events`/`outbox_consumed` grow unbounded (timestamps are ISO strings, not
  BSON dates, so TTL indexes don't apply). Add a purge job or migrate to date types before scale.
- Strict transactional outbox requires a MongoDB replica set (transactions) or an event-sourced write.
