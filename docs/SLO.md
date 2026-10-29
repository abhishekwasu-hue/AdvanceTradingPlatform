# Service Level Objectives

Phase G2. Each objective names the metric that measures it (all exported at `backend:8000/metrics`
and `worker:9102/metrics`, Phase E1) and the alert in `scripts/monitoring/prometheus-alerts.yml`
that fires when the objective is at risk. Targets are what the platform is built to meet; they are
not yet measured against production traffic, and the first month of real data should revise them.

The objectives are ordered by consequence: a stalled engine during market hours is money, a slow
dashboard is annoyance.

| # | Objective | Target | Measured by | Alert |
|---|-----------|--------|-------------|-------|
| SLO-1 | API availability and latency | 99.5% of requests non-5xx over 30 days; p95 latency under 500 ms for reads | `atp_http_requests_total{status="5xx"}` over `atp_http_requests_total`; `atp_http_request_duration_seconds_bucket` | `ApiErrorBudgetBurn`, `ApiLatencyHigh` |
| SLO-2 | Engine liveness on trading days | Heartbeat never older than 3 cycles (180 s) while the market is open, 99.9% of market minutes | `atp_worker_heartbeat_age_seconds` | `TradingWorkerStalled`, `TradingWorkerNeverSeen` |
| SLO-3 | Cycle time | 99% of cycles complete within one base candle (60 s) | `atp_worker_cycle_duration_seconds_bucket` | `WorkerCycleOverBudget` |
| SLO-4 | Market-data freshness | Fewer than 5 decisions per 10 minutes refused for stale data; zero signals evaluated on candles more than `MARKET_DATA_MAX_STALE_BARS` behind (enforced, not just measured) | `atp_market_data_stale_total{kind}` | `MarketDataStale` |
| SLO-5 | Broker health | No circuit breaker open for more than 1 minute; zero LIVE orders FAILED per day | `atp_broker_circuit_state`, `atp_broker_calls_total{outcome}`, `atp_orders_total{mode="LIVE",status="FAILED"}` | `BrokerCircuitOpen`, `LiveOrdersFailing` |
| SLO-6 | Reconciliation | A broker-uncertain tenant is either cleared or has a CRITICAL notification with the mismatch within one cycle; no tenant blocked longer than 30 minutes without operator action | `atp_broker_uncertain_tenants`, `atp_reconciliations_total{source,outcome}` | `TenantsBlockedPendingReconciliation` |
| SLO-7 | Entry latency | p95 signal-to-fill under 2 s for LIVE entries (measured from the signal reaching the router to the fill being read back) | `atp_order_entry_latency_seconds_bucket{mode}` | `SlowEntries` |
| SLO-8 | Alert delivery | Out-of-app alerts (Telegram/email) leave within 60 s; outbox never above 20 for 10 minutes | `atp_alert_outbox_pending`, `atp_alert_deliveries_total{status}` | `AlertOutboxBacklog`, `AlertDeliveriesFailing` |
| SLO-9 | Recovery | RTO 30 min / RPO 5 min for the database (WAL archiving + weekly base backup, docs/OPERATIONS.md 1.2a); nightly logical backup verified by restore | CI backup rehearsal; `scripts/backup/verify_backup.sh` exit status | (CI, not Prometheus) |

## SLO-1 in the 500-user load test (LD-9)

`backend/scripts/loadtest.py` judges a load run with two verdicts, and both must pass (owner, LD-9, 20 Oct: login is
measured apart from the 0.5 s API target):

| Verdict | Scenarios | Pass rule |
|---|---|---|
| API | every scenario but login: the dashboard reads, proposals, positions, the PAPER account, the chart, the screener run | p95 at or under 500 ms per scenario (the chart's documented limit 1,000 ms, the screener run's 2,000 ms); server errors at most 0.5% of the API requests |
| Login | `POST /api/auth/login` | p95 at or under 2,000 ms at 500 concurrent sessions, and no failed login at all (a time-out, a 5xx, a 429 or a refusal each fails it) |

A slower CI machine may scale the p95 limits (`--slo-factor`), never the error limits. The staging-stack job `load-500`
passes or fails on the same rule (its primary profile's exit status).

## How the safety gates relate to the SLOs

* **Staleness gate (Phase G1)** enforces SLO-4 rather than measuring it: a stale feed produces a
  skipped evaluation with the reason on the deployment, and a counter increment, never a trade.
* **Broker-uncertain flag (Phase G1)** is the SLO-6 mechanism: a FAILED LIVE order blocks new LIVE
  entries for that organisation until reconciliation passes; the worker reconciles every cycle
  while flagged, and on start-up before its first cycle.
* **Circuit breaker (Phase G2)** protects SLO-5: it pauses new LIVE entries to a failing broker
  for every tenant. It is not the kill switch (a person's decision); the two are independent and
  either alone stops entries.
* **Exits are never gated** by the breaker or the uncertain flag. A stale *quote* does stop an
  exit decision for that cycle (acting on a wrong price is worse than waiting one cycle); LIVE
  positions still have their broker-side protective stop.

## Error budgets

SLO-1's 99.5% over 30 days is about 3.6 hours of 5xx-equivalent downtime. Spend it on deploys
outside market hours. Any budget burn during 09:15-15:30 IST is an incident, whatever the monthly
figure says.

## Review

Revisit the targets after the first full month of live trading, with the histograms exported from
Prometheus. Loosen a target only with a written reason in this file; tighten freely.

## Published targets (AUD-T8)

The public status page (`/status`, flag `status_page`) shows SLO-1 to SLO-4, SLO-8 and SLO-9 in plain words, labelled as
targets, next to the measured uptime per market session. `backend/app/observability/slo.py` mirrors the objective and
target cells of the table above; `backend/tests/test_aud_t8_status_page.py` fails when they drift, so change both
together. SLO-5 to SLO-7 (LIVE broker objectives) are published when LIVE trading starts.
