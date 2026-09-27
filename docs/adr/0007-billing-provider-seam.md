# ADR 0007: Billing behind a provider seam; the gateway's secrets are the operator's

**Date:** 2026-09 · **Status:** accepted

## Context
Plans, trials, grace periods and usage metering are product logic; collecting money is a gateway
concern (Razorpay today, possibly Stripe or bank transfer). Early customers pay by bank/UPI.

## Decision
`app/billing/service.py` owns the subscription lifecycle and calls a `BillingProvider` for the
gateway steps (create subscription/checkout, change plan, cancel). `ManualProvider` records
operator-entered payments; `RazorpayProvider` uses Razorpay Subscriptions with hosted checkout and
HMAC-verified, idempotent webhooks. Gateway keys are platform configuration (`.env`), never tenant
data, and are never exposed to the frontend.

## Consequences
- Switching gateways touches one module and no plan logic.
- Grace expiry downgrades to the free plan and records the reason, rather than suspending, so
  positions can still be closed (ADR 0004).
