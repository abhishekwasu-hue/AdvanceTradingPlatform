# ADR 0010: One trading worker loop per replica set, Redis lock, market-calendar gated

**Date:** 2026-02 · **Status:** accepted

## Context
Signals must be evaluated and positions monitored without a browser open, every cycle, for every
tenant, without two replicas double-placing orders.

## Decision
`app/workers/trading_worker.py` runs one loop: acquire a Redis lock (TTL twice the cycle), check
the NSE session calendar (holidays from the DB), process tenants in turn (credentials per tenant,
rate budget per broker, entry refusals, signals, exits), drain alert deliveries, run daily jobs
(instrument master, retention, billing sweep), write a heartbeat. A replica that fails the lock
skips the cycle. Start-up reconciles every tenant with open LIVE positions against its broker
before the first cycle.

## Consequences
- Horizontal scaling is by tenant sharding later, not by racing replicas now.
- The heartbeat is the liveness signal the dashboard and alerts use; a stale heartbeat is an
  incident.
- Schema migrations during the session are refused by `scripts/migrate_guard.py` (Phase N4)
  because the worker holds positions through them.
