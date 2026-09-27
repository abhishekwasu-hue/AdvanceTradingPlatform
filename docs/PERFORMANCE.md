# Performance baseline (section 54)

Phase O4. Measured with `backend/scripts/loadtest.py`, which drives the read paths a dashboard
polls (`/api/auth/me`, `/api/deployments`, `/api/trades`, `/api/notifications`,
`/api/system/status`, `/api/portfolio/exposure`) with N concurrent clients and reports p50/p95/p99
per route plus the 5xx rate, exiting non-zero when SLO-1 (p95 <= 500 ms, 5xx <= 0.5%) is missed.

```
python scripts/loadtest.py --base http://localhost:8000 --users 20 --seconds 30 [--json]
```

## Baseline run (2026-09-27, single uvicorn worker, Postgres 16 + Redis on the same 2-vCPU sandbox)

| Route | requests | p50 ms | p95 ms | p99 ms | 5xx |
|---|---|---|---|---|---|
| /api/auth/me | 1038 | 44.0 | 131.0 | 853.2 | 0 |
| /api/deployments | 1037 | 52.9 | 126.8 | 230.9 | 0 |
| /api/trades | 1036 | 48.1 | 119.9 | 218.3 | 0 |
| /api/notifications | 1033 | 52.2 | 129.1 | 209.4 | 0 |
| /api/system/status | 1023 | 58.1 | 150.4 | 244.6 | 0 |
| /api/portfolio/exposure | 1020 | 58.2 | 134.2 | 203.3 | 0 |

20 users for 21 s: 6187 requests, 294 requests/s, 0.00% errors. **SLO-1: PASS** (worst p95 150 ms).
The `/api/auth/me` p99 outlier is the first-request tenant-key unwrap and session lookup after
registration; steady-state p99 for that route is in line with the others.

## What this tells us

- A single API process comfortably serves a few hundred dashboard requests per second; the
  worker is a separate process, so API load never delays a trading cycle (SLO-3 is measured by
  `atp_worker_cycle_duration_seconds`, not here).
- Every route above is one or two indexed queries; the shared cost is JWT verification plus the
  login-session liveness check. If p95 ever approaches the 500 ms budget, the first lever is
  `uvicorn --workers N` behind the nginx front end, the second is caching `/api/system/status`
  (unauthenticated, identical for everyone) for a few seconds in Redis.
- Entry latency (SLO-7, signal to fill) depends on the broker's API round trip and is exported as
  `atp_order_entry_latency_seconds`; it cannot be load-tested without a broker sandbox.

## Re-running

Run the probe against staging after every release that touches the request path (auth,
middleware, ORM models) and paste the table here with the date; a regression of more than 2x on
any p95 is a release blocker. `--json` output is suitable for a CI artefact. The probe registers a
throwaway account, so it needs the `self_signup` flag on or `--token <existing access token>`.
