# ADR-0022: Notification Service - grow the existing alert outbox, do not build a second one

**Date:** 2026-10-11 · **Status:** provisional (spec 0.6: the recommended option, until the owner says otherwise) · **Part:** S0

## Context
The platform already sends alerts:
- in-app notifications (`notifications`, read state per user);
- alert channels per organisation (`alert_channels`: Telegram, email, HMAC webhook, web push with VAPID);
- an outbox of deliveries (`alert_deliveries`) drained by the worker, with retries and retention (Phases B0, K, O).

Risk-engine and system alerts go through it.

The Screener spec v2 §4 asks for:
- rule-driven screen and instrument alerts;
- dedupe and cooldown, per-user caps, burst grouping and digests;
- quiet hours by timezone;
- delivery status with reason codes, and a user-visible delivery log;
- per-user Telegram linking, email unsubscribe and bounces;
- versioned webhook payloads with a replay window and a dead-letter queue;
- p95 handoff under 2 s for live alerts.

## Options
1. A new Notification Service with its own tables and workers, and the old alerts migrated later.
2. Extend the existing outbox and dispatcher into the Notification Service, with the same tables and new columns and
   tables where needed.

## Decision (provisional)
**Option 2.**
1. **Model.** Rules produce events, and events produce deliveries:
   - `alert_rules` is new: the screen or instrument condition, schedule, expiry, priority, channels and digest mode.
   - `alert_events` is new: one row per (rule, symbol, bar_time). Its idempotency key (rule, symbol, condition hash,
     bar time) is also held in Redis with a TTL, for the fast path.
   - `alert_deliveries` already exists. It gains priority, group_id, digest_bucket and a reason_code.
2. **Throttle.** Cooldown per (rule, symbol). Per-user, per-channel hourly cap from the plan. Burst grouping: one
   message per rule and bar lists every symbol. Digest buckets are flushed hourly or at EOD. Quiet hours and the
   market-closed option are evaluated in the user's timezone.
3. **Channels stay behind the existing dispatcher seam.**
   - Telegram gains per-user linking: a deep-link code and chat-id verification. Its buttons are limited to Open, Add
     to watchlist, Snooze 1h and Mute symbol.
   - Email gains an unsubscribe token per alert and bounce/complaint handling.
   - Webhooks gain a schema version, a timestamp with a replay window, secret rotation, a dead letter after N attempts,
     the SSRF guard on the URL (exists) and an optional Chartink-shaped body.
   - Web push uses VAPID, as today.
   - Mobile push and SMS/WhatsApp are interface-only seams.
4. **Delivery.** At-least-once with idempotent consumers. The status set is queued / sent / delivered / failed /
   suppressed, with a reason. Every rule has a delivery log.
5. **Content.**
   - Templates are per channel; English is the default and Marathi is optional per user.
   - Every rendered text passes the H-C1 c output filter.
   - Every number carries its data timestamp.
6. **Never an order.** An alert never places or changes an order. A strategy hand-off is a monitor proposal (ADR-0006).
   Stale data suppresses alerts and raises one system alert.

## Consequences
- **Migration.** Existing channels and deliveries keep working, so risk and system alerts need no migration step beyond
  the new columns. The migrations are additive with down-migrations.
- **Delivery path.** The worker's drain loop becomes the dispatcher for every alert kind. Live alerts (p95 under 2 s)
  are dispatched from the stream consumer straight after the outbox write, rather than waiting for the next worker
  cycle.
- **Load.** The load test (1,000 rules × 200 symbols at bar close) and the chaos test (stale feed) become part of CI
  (S3/S4).
