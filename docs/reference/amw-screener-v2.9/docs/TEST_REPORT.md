# Test and verification report — v2.9.0 (2026-10-19)

All results below were produced by commands actually executed for this release. Environment: Linux x86_64,
Python 3.11, Node 22 / TypeScript 6.0.2, R 4.3.3, Lua 5.4 (lupa 2.8), SQLite 3.45, PostgreSQL 16, Docker 29.

## Automated tests
| Command | Result |
|---|---|
| **New** virtualenv in `/opt`: `pip install -r requirements-dev.txt` on a clean copy of the tree | OK (incl. `websockets` 17.2) |
| `ruff check .` | All checks passed |
| `AMW_TEST_PG_URL=… python -m pytest -q` (new venv, clean copy, run as root so the sandbox is active, local PostgreSQL 16) | **293 passed** (incl. the 2 PostgreSQL tests); the database was upgraded in place step by step v7 → v9 → v11 → v13 across releases |
| Same suite inside the production Docker image (all runtimes installed, `CAP_SYS_ADMIN`, no PostgreSQL URL) | **291 passed, 2 skipped** (the skipped tests need `AMW_TEST_PG_URL`) |
| v2.4.0 for reference | 220 passed (venv + PostgreSQL); Docker 218 passed, 2 skipped |
| `test_feed.py` repeated 5× (threads and timing) | 5 × 25 passed (no flakiness observed) |
| v2.3.0 for reference | 191 passed, 1 skipped; a PostgreSQL database created at schema v3 was upgraded in place to v5 |

| Test file | Tests | Covers |
|---|---|---|
| test_data.py | 13 | validation (NaN, duplicates, OHLC, negative volume, ordering), forming candles incl. session end and weekly bars, holidays/staleness, missing bars, corporate-action flag, Yahoo limits, universe fallback/local CSV, provider retries/dedup/cache, settings validation |
| test_analysis.py | 11 | EMA/SMA/RSI/ATR/MACD/Bollinger reference values, ADX, RVOL (prior bars), VWAP, RS, Fibonacci, no artificial zeros, candles in context, swing confirmation + non-repainting, BOS/CHoCH close-only, double top, regime, RSI divergence |
| test_strategies.py | 14 | each built-in strategy, touch ≠ breakout, failed breakout, stale/insufficient never qualify, reproducible ranking, mandatory gates, enable/disable, MTF conflict, bearish condition-text regression |
| test_platform.py | 20 | scan isolation/filters/CSV, cancel/resume, dedup + lifecycle transitions, alert dedup/cooldown/failures, risk sizing, paper ledger reconciliation, backtest sequencing/costs, tenant isolation/roles/constraints, billing idempotency, AI grounding/outage, jobs, API auth/roles/rate limit/webhook, no order/position/funds routes and FYERS client limited to login + history URLs, PostgreSQL contract; **tenant-isolation regressions SEC-001..003** (jobs, signals, lifecycle, alerts), quiet hours + hourly cap, additive migration of an old database, INSUFFICIENT EVIDENCE on small backtest samples |
| test_lab_release.py | 31 | all eight modes released; operator restriction (`AMW_LAB_LANGUAGES`) in validate/execute/API; Pine templates, inputs → stop/target, indicator parity, crossover semantics, diagnostics; Python templates, helper parity, user errors, logs; **Windows local trusted mode for every code runtime** (Python, SQL, Lua, JavaScript, TypeScript, R); **end-to-end create → validate → run → publish → scan → backtest for all eight modes**; API tenancy |
| test_lab.py | 34 | multi-language architecture: contract/schema, visual IR, Pine subset, SQL allow-list, all runtimes, **cross-language consistency** (8 modes, 4 fixtures), visual → Python export equivalence, sandbox isolation, JS realm-escape attempts, Lua/R/SQL restrictions, timeout/memory/output limits, cancellation, per-user quota, look-ahead prevention, immutable versions, lab scan statuses, shared backtest and cost sensitivity, R chunking of large batches |
| test_ai_byok.py | 30 | **BYOK AI** (mocked OpenAI/Anthropic HTTP, no paid calls): test-before-save, encryption at rest, ciphertext bound to user+provider, wrong master key fails closed, each request uses the caller's own key, env/company key never used (also a static code guard), no-key → no call, key deletion, session-only keys, redaction in logs/errors/API responses, API tenant isolation, selection modes (default/manual/task routing), fallback only per policy, Dual Review needs both keys + confirmation, error classification (401/403/404/429/503/400, insufficient credit, network), bounded retries, calls-per-task + authorization, cost thresholds with provider-token vs estimate labelling, daily threshold, cancellation, bounded agent repair loop (drafts only), deterministic analysis without a key, master-key file permissions |
| test_notify.py | 5 | Telegram (mocked Bot API): official-bot linking by code, user-owned bot token encrypted and never exposed, bounded retries + classification + redaction, unlink, message rendered from the stored canonical record |
| test_reports.py | 5 | all five formats generated and parsed back (PDF/XLSX/CSV/JSON/PNG), owner-only generation and download, snapshot-mismatch reporting, chart markers on the computed candle/price, invalid format / unknown signal / safe names |
| test_saas_sched.py | 8 | plan limits enforced in backend, concurrent-job limit, daily/interval schedule timing incl. weekends/holidays/no double run, schedule validation, scheduled scan runs once for the owner, scheduled PDF reports delivered to the owner's Telegram, API for reports/schedules/notify/plan incl. cross-user denial |
| test_feed.py | 27 | **FYERS live data** (mocked FYERS HTTP, no account): bar validation/completion, aggregator without forward-fill, session-aligned resampling, symbol mapping, `appIdHash`, login URL, code exchange with redacted failures, history error mapping, rate limiter + breach guard, encrypted credentials, one-time OAuth state bound to the user, completed-bar polling (forming bar excluded), owner-only broadcast, revisions and catch-up, token expiry, rate-limit guard, poll schedule (session/weekend/holiday/symbol cap), hub backpressure, scans on FYERS daily and intraday data, data-source fallback status, scan aborts on an expired session, REST + WebSocket isolation; **two processes on one database**: one collector, shared per-minute budget, shared status, remote Stop, login in the other process resumes the feed, shared rate guard, auto-resume after a crash; lease/status/stream queries on **PostgreSQL** |
| test_analysis_v2.py | 7 | Supertrend (trend, flip, no look-ahead), Fib extensions, Pin Bar / Strong Body / Rejection / Gap Reversal positive+negative cases, gaps + fill, S/R role reversal + freshness |
| test_scanner_predicates.py | 5 | new IR predicates incl. RSI divergence parity with the analysis module, UNKNOWN for missing HTF, argument validation |
| test_scanner_layer.py | 9 | envelope validation, 19 templates valid, NL builder (master-prompt example, unsupported reporting), save/version/duplicate/isolation, replay without future leakage, version comparison, API |
| test_reports_notify_v2.py | 5 | research reports parse back, notifications dedup/quiet hours/cap/status, invalidation + forward + daily summary hooks |
| test_v25.py | 10 | durable job records visible from another process, persisted errors, restart marks orphans FAILED (healthy jobs untouched), forward-test entry/exit rules (next-bar open, stop-first ties, gaps, expiry, shorts), scan enrollment + marking + evidence label, forward API owner scoping, report retention and storage quota, Signal Intelligence → chart/report hand-over |
| test_e2e.py | 3 | Workflow A (scan → canonical record → chart → report → Telegram identical values), Workflow B (NL strategy → mocked AI draft → publish → deterministic backtest; stats from trades), Workflow E (backtest determinism, entry after signal, costs reduce returns) |
| test_ui.py | 20 | every page (Home + 13) renders without exceptions on simulated data; Live Data page: connect, masked App ID, Secret never re-rendered, login link; screener scan flow; Strategy Lab create → validate → publish (Pine) and Visual builder flow (template, delete condition, readable logic, validate, publish); AI setup page shows the billing notice, official links and masked key inputs; AI analyst without a key stays deterministic |

Sandbox self-test on this machine: uid 65534, no network, no secrets in environment, no writes, no access to `/root`
or the app `data/` directory, no host-visible file escape → **verified**.

## Manual / integration verification
| Check | Result |
|---|---|
| `streamlit run Home.py` health endpoint | `ok` (v2.4.0: HTTP 200) |
| v2.4.0 API smoke (`python api_server.py`, token auth, SQLite): `/health/ready` (schema 6), `GET /data-source`, FYERS connect (App ID masked), login URL, feed start → `MARKET_CLOSED` before 09:15 IST with no FYERS call, feed stop; the FYERS secret appears neither in the API log nor in the database file | all OK |
| v2.4.0 WebSocket across real processes: `uvicorn` API server + `websockets` client, bars written by a third process | bars streamed in order; a bar corrected before the stream tail arrived as one `revision` with the corrected values; bad token → `unauthorized` |
| `python api_server.py`: `/health/live`, `/health/ready`, `/openapi.json` | 200 / ready (SQLite) / 200 |
| Docker image build + container sandbox self-test with `CAP_SYS_ADMIN` | verified |
| Same container without `CAP_SYS_ADMIN` | not verified → execution disabled (fail-closed) |
| `docker compose up db api` (PostgreSQL 16): ready, migrations v1–v2, admin token, Pine publish, Python validate in sandbox | all OK |
| Live Yahoo data, 5 NSE stocks: Pine *EMA trend* and Python *Breakout* — run, scan, backtest (RELIANCE 1D) | all COMPLETED |
| Live Yahoo data, same 5 stocks: Visual, SQL, JavaScript, TypeScript, Lua, R — run, scan, backtest | all COMPLETED; the equivalent EMA-stack strategy gave the same 3 backtest trades in Visual, JS, TS, Lua and R |
| Production image capability check (`CAP_SYS_ADMIN`) | sandbox verified; native execution True for Python, Visual, SQL, JavaScript, TypeScript, Lua, R; Pine translated |

## Performance (single machine, live Yahoo data, all 6 built-in strategies, 6 timeframes, no inter-batch pause)
| Universe | Cache | Wall time | Provider requests | Data OK |
|---|---|---|---|---|
| 10 symbols | cold | 10.4 s | 12 | 60/60 |
| 10 symbols | warm | 4.6 s | 0 | 60/60 |
| 48 symbols | cold | 39.0 s | 18 | 288/288 |
| 48 symbols | warm | 21.8 s | 0 | 288/288 |

Warm-cache time is analysis cost; it does not mean fresher data. A full 500-symbol scan was **not** measured; Yahoo
throttling is the expected limit. No concurrent-user load test was run, so no capacity claim is made.

## Not verified / limitations
* Windows: not executed on a Windows machine in this environment. The Windows code path (no OS sandbox, local trusted
  mode) is exercised for every runtime by tests that simulate it on Linux; the CI workflow includes a `windows-latest` job.
  Node.js/TypeScript and R are optional installs on Windows and were not tested there.
* GitHub Actions workflow written but not run (no repository access from this environment).
* Docker build inside this environment needed the host CA certificate for PyPI/npm/apt (proxy); the shipped
  Dockerfile is unchanged and is expected to build on a standard network (not verified on one).
* Production image size: 1.5 GB (R and Node included).
* **FYERS was not called live** (no FYERS account in this environment). The integration follows FYERS' documented API v3
  and is verified against a mock; do one live login and one full trading day before production (QUESTION_LOG Q-007).
* Telegram/email/billing providers and **OpenAI/Anthropic were not called live** (no keys in this environment). BYOK behaviour
  is verified with mocked responses that follow the providers' documented API formats; verify once with your own key.
* AMW does not read provider invoices; cost figures are local estimates from user-entered prices.
