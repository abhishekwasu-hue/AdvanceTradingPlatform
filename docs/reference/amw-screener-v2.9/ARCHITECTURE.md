# Architecture

## 1. Decision summary

| Decision | Choice | Why / trade-off |
|---|---|---|
| UI | **Streamlit** (Home + 15 pages) | Runs on a Windows desktop with one `pip install`; the user base is single-user/desktop first. A Next.js frontend was *not* built: it would add Node tooling to every install without adding capability. The typed REST API is the integration point for a future web frontend. |
| Backend API | **FastAPI** `/api/v1` | Typed request validation (pydantic), OpenAPI docs, token auth, roles, rate limit, request IDs. |
| Quant engine | Python, pandas, NumPy | Independently testable modules; no UI imports. |
| Database | **SQLAlchemy Core**; SQLite default, **PostgreSQL** via `DATABASE_URL` | Zero-setup desktop; same schema verified on PostgreSQL 16. |
| Jobs | In-process `JobManager` (thread pool, progress, cooperative cancel, resume) + durable `job_runs` records and start-up orphan recovery | Single-process deployment; Celery/Redis would add operations cost with no measured benefit yet. Revisit for multi-worker scale (see IMPLEMENTATION_PLAN). |
| Cache | Disk cache keyed by provider/ticker/timeframe/interval/period with TTL | Avoids repeat Yahoo calls when only filters change. |
| Strategy Lab execution | Separate worker processes in an OS sandbox (Linux namespaces + uid drop + rlimits) | Untrusted code never runs in the API/UI process. |
| Deployment | Docker image (Python 3.11 + Node 22/TypeScript 6.0.2 + R 4.x) + Compose (PostgreSQL, app, api) | Reproducible single-server deployment with every Strategy Lab runtime. |
| Live broker data | Per-user FYERS connection; one background collector per user coordinated through a **database lease**; WebSocket stream tails the database | The dashboard and API are separate processes; the database is the only shared state, so no message broker is needed at this scale. Data stays with the account owner (licensing). |

## 2. Layers

```
                 ┌────────────── Streamlit pages (Home.py, pages/*) ──────────────┐   ┌─ FastAPI (src/api) ─┐
                 │  ui_common.py  ·  src/ui/charts.py (Plotly rendering only)     │   │ auth · roles · RL   │
                 └───────────────────────────────┬────────────────────────────────┘   └──────────┬──────────┘
                                                 ▼                                              ▼
                                 src/services/app_services.py  (shared by UI and API)
             ┌──────────────┬──────────────┬───────────────┬───────────────┬──────────────┬─────────────────┐
             ▼              ▼              ▼               ▼               ▼              ▼                 ▼
      src/screener    src/strategies   src/lab          src/backtest    src/services    src/storage       src/risk.py
      scanner, export six engines,     contract, IR,    shared trade    alerts, jobs,   db.py (schema,
                      scoring base     pine, engine,    simulator       lifecycle,      migrations,
                                       sandbox,workers                  paper, analyst  tenancy)
             └──────────────┴───────┬──────┴───────────────┴───────────────┘
                                    ▼
                         src/analysis  (indicators, structure, candles, patterns, regime, context)
                                    ▼
                         src/data      (provider interface, yfinance, fixture, validation, universe)
```

Rules: `src/analysis` and `src/strategies` never import UI code; the UI never computes signals itself;
every signal is produced from validated, completed-bar data.

## 3. Data flow of a scan

1. `universe.load_universe` → official CSV / URL / cache / labelled FALLBACK.
2. `provider.fetch_many` per batch × timeframe (cache, bounded retries, exponential backoff, request dedup).
3. `validation.clean_and_validate` → drop invalid rows (never fill), exclude forming candle, holiday-aware staleness,
   missing-bar count, corporate-action flag → typed `QualityReport`.
4. `context.analyze_tf` → indicators, confirmed swings, BOS/CHoCH, zones, S/R, liquidity, patterns, candles.
5. Strategies (`src/strategies/*` or a published Lab version via `LabStrategy`) → `Signal` with conditions, components,
   score, levels; mandatory-gate + data-quality gate in `Strategy.finalize`.
6. `stamp_canonical` (provider, data snapshot, evaluation time) → `Store.record_signals(rows, owner)` (dedup per owner)
   → `lifecycle.update_lifecycle(owner)` → `alerts.process_alerts(owner)` → reports / Telegram render from the stored record.

## 4. Strategy Lab

```
 author ──► draft (lab_strategies) ──validate──► publish ──► immutable version (lab_versions, sha256)
                                                              │
         contract.build_context (host builds ALL inputs: completed bars, indicators, structure, HTF, params)
                                                              ▼
   python ─┐                                   ┌─ pine ──► pine.translate_pine ──► IR ──► ir.evaluate_ir (host)
   js/ts ──┤ engine.execute ─► sandbox.run ──► │
   lua/sql ┤      (one batch per call)         └─ visual ─► IR ──► ir.evaluate_ir (host)
   r ──────┘                                              │
                         contract.validate_result (schema, finite numbers, stop side) ──► LabResult
                                                              ▼
              LabStrategy (scanner adapter)  ·  lab_backtest (shared backtest engine)  ·  lab_runs (audit)
```

Release gating: `config/settings.yaml → strategy_lab.enabled_languages` (all eight modes released), restrictable per
server with `AMW_LAB_LANGUAGES`. A released mode executes only when its runtime is present and the sandbox (or Windows
local trusted mode) is available; the capability matrix reports this per machine.

## 5. AI layer (Bring Your Own Key)

```
UI / API ──► src/ai/gateway.AIGateway ──► provider selection (user policy) ──► local safeguards ──► user's own key
                                          (decrypted in memory, src/ai/crypto) ──► src/ai/providers (OpenAI | Anthropic)
                                          ──► ai_usage log (provider tokens + AMW estimate)
services/analyst (evidence + number verification) and ai/agent (bounded drafting loop) only talk to the gateway.
```
No company key exists anywhere in the request path. Details: docs/AI_BYOK.md.

## 6. Database (schema version 9)

`users`, `preferences`, `watchlists`, `presets`, `strategy_configs` (versioned built-ins), `signals` + `signal_events`
(lifecycle), `alert_rules` + `alert_events` (unique rule/signal/channel), `journal` (paper trades, `CHECK qty > 0`),
`scan_runs`, `audit_log`, `billing_events` (unique provider event id), `lab_strategies`, `lab_versions`
(unique strategy/version, immutable), `lab_runs`, `ai_credentials` (encrypted, unique user/provider), `ai_usage`, `reports`, `schedules`,
`broker_connections` (v6: encrypted app secret + access token, hashed one-time login state, feed lease/heartbeat/status;
unique user/broker), `minute_bars` (v6: per-user completed 1-minute bars, unique user/symbol/ts, revision count),
`job_runs` (v7: durable job records with holder + heartbeat), `rate_buckets` (v7: per-minute counters shared by processes),
`forward_tests` (v7: paper forward test per qualified signal, unique user/signal), `notification_events` (v8: system
notifications, unique user/kind/dedup key/channel), index `signals(user_id, first_seen)` (v9), `schema_version`.
`signals.user_id` / `scan_runs.user_id` (v4) scope signals and scans to their owner; migrations are additive
(`Store.COLUMN_MIGRATIONS`) and verified on SQLite and PostgreSQL 16.
All private tables carry `user_id`; every Store method takes the caller's `user_id` (no cross-tenant reads).
Timestamps are stored as UTC (naive); market timestamps are exchange-local (Asia/Kolkata) strings.

## 7. Live 1-minute feed (FYERS, per user)

```
 Streamlit process                      API process                         FYERS (user's own app)
 ───────────────────                    ───────────────────                 ──────────────────────
 FeedManager ─┐                         FeedManager ─┐
              │ start: acquire lease                 │ start refused while another holder is alive
              ▼                                      ▼
   FyersCollector thread ── every minute +5 s ──► GET /data/history (today, 1-minute) ──► completed + valid bars
              │ heartbeat 10 s (lease + status)                      │
              ▼                                                      ▼
   broker_connections (lease, status, encrypted token)      minute_bars (per user; new / revised)
              ▲                                                      │
              │ status / Stop (release lease)                        ▼
   any process ◄──────────────────────────────────────── WebSocket /stream/bars tails minute_bars (≤ 2 s)
```
* `src/feed/fyers.py` — client (login, history), rate limiter with a database-backed daily breach guard, symbol mapping,
  `FyersHistoryProvider` for scans. `src/feed/bars.py` — bar validation, completion rule, aggregation, resampling.
  `src/feed/collector.py` — collector loop, lease/heartbeat/recovery, `FeedManager`. `src/feed/hub.py` — in-process
  per-user pub/sub (wake-up and status for local streams).
* The forming minute is never stored or streamed; missing minutes are never filled; revisions are counted.

## 8. Universal scanner layer (v2.6)
`src/scanner/spec.py` (envelope + validation + save/duplicate) · `nl.py` (deterministic NL → IR) · `templates.py` ·
`replay.py` (point-in-time replay, version comparison). Scanners are Strategy Lab strategies (language `visual` or code)
whose spec carries `scanner`; execution goes through `lab.engine.execute` (IR engine or sandbox) like every Lab
strategy. Details: docs/SCANNER_SPEC.md.
