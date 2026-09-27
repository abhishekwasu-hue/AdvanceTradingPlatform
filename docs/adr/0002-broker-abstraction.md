# ADR 0002: One `BrokerInterface`, adapters per broker, credentials never leave the tenant

**Date:** 2025-12 · **Status:** accepted

## Context
Indian retail brokers (Upstox, Zerodha, Shoonya, Angel One, Fyers, Dhan) expose different REST
APIs, token lifecycles (daily expiry, OAuth vs TOTP login) and order semantics. The trading worker,
execution router and reconciliation must not know which broker is behind a deployment.

## Decision
`app/brokers/base.py::BrokerInterface` is the only contract the rest of the platform uses:
authenticate, quotes/candles, place/modify/cancel orders, place stop-loss, positions, balance,
`exit_position`, `subscribe_market_data`, disconnect. One adapter class per broker. Credentials are
entered only through the Settings page, encrypted at rest, decrypted in memory on demand, and
never returned by any API or logged. The token lifecycle (expiry, refresh, "log in again" banner)
is broker-agnostic code over adapter hooks.

## Consequences
- Adding a broker is one adapter plus an entry in the registry; nothing else changes.
- Adapters that cannot support a call raise `NotSupported`, and the platform degrades (for example
  `subscribe_market_data` raises until an adapter streams; the worker polls candles instead).
- Rate budgets and the circuit breaker (Phase G2) sit between the worker and the adapter, per
  broker, so a degraded broker API blocks new LIVE entries platform-wide without touching exits.
