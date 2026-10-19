# Audit findings (continuation audit, 2026-10-19, on top of v2.5.0)

Method: code review of analysis/strategy/backtest/storage/job code, profiling with the deterministic fixture
provider, the full test suite, and targeted reproductions. Every "Fixed" item has a regression test that was shown to
FAIL without the fix and PASS with it. No live market data or third-party API was used for these checks.

## Feature inventory (verified state)

| Area | State | Evidence |
|---|---|---|
| Scanner, 6 strategy engines, scoring, data validation | Implemented + tested | tests/test_strategies.py, test_data.py, test_analysis.py |
| Market structure (swings, HH/HL/LH/LL, BOS/CHoCH close-only, sweeps), S/R, zones, candles | Implemented + tested; no future-bar access found (grep for `shift(-`, `i+1` reviewed: candle confirmation only uses bars inside the evaluated window) | src/analysis/*.py |
| Backtest, walk-forward, costs | Implemented + tested; **A-001 fixed** (weekly context) | src/backtest/engine.py |
| Strategy Lab (8 modes), sandbox | Implemented + tested | test_lab*.py |
| Reports, Telegram, scheduler, plans, forward tests, durable jobs | Implemented + tested (Telegram/e-mail with mocks only) | test_reports.py, test_notify.py, test_saas_sched.py, test_v25.py |
| AI analyst / drafting (BYOK) | Implemented; tested with **mocked** providers only (no live key) | test_ai_byok.py |
| FYERS live 1-minute feed | Implemented; tested with a **mocked** FYERS API only | test_feed.py |
| `BrokerProviderTemplate` | Template only, intentionally not connected | src/data/provider.py |
| UI controls without backend | None found (pages render and call services; TODO/NotImplemented hits are abstract base methods) | grep, tests/test_ui.py |

## Findings

| ID | Priority | Finding | Impact | Root cause | Fix | Regression test | Status |
|---|---|---|---|---|---|---|---|
| A-001 | P1 (accuracy) | Swing-strategy backtest on 1D built weekly bars from the truncated daily window and kept the **current partial week as a completed weekly bar** | Backtest used a weekly context the live scanner never sees (live excludes forming weekly candles) → backtest results not representative of live signals | `_weekly()` resampled with `W-FRI` and kept the last, unfinished week | Drop the last weekly bar while its Friday label is after the last daily bar | tests/test_strategies.py::test_backtest_weekly_context_excludes_forming_week | **Fixed** |
| A-002 | P0 (data integrity) | `record_signals` checked existing keys, then inserted. A concurrent scan of the same user (scheduled + manual, or dashboard + API) inserting the same signal in between raised IntegrityError and **rolled back the whole batch** | All other new signals of that batch were lost (and alerts/forward tests for them skipped) | check-then-insert race on the primary key | `INSERT … ON CONFLICT DO NOTHING … RETURNING key` (SQLite + PostgreSQL); a skipped row is simply not new. The first version used `rowcount`, which the PostgreSQL contract test showed is unreliable there (new signals were counted as existing) — corrected before release | tests/test_platform.py::test_concurrent_duplicate_signal_does_not_lose_the_batch | **Fixed** |
| A-003 | P1 (reliability) | Job final status became visible before its database record was written | Another process (or a status poll) could see `failed` with an empty error | status assigned before persisting in `JobManager.run` | Persist first, publish status after | tests/test_v25.py::test_failed_job_error_is_persisted (was intermittently failing in the full suite; 0 failures in later runs) | **Fixed** |
| A-004 | P2 (performance) | Data snapshot fingerprint recomputed for every signal of the same symbol/timeframe; true range computed 3× per frame; ~25 separate column joins per frame | Scan analysis cost | redundant work | per-analysis fingerprint cache, single true range, one concat; output proven identical | equivalence check vs previous implementation (all columns, 5 timeframes, atol 1e-12); scan fingerprint identical | **Fixed** (see PERFORMANCE_RESULTS.md) |
| A-005 | P3 (consistency, low) | When a stored signal later transitions (e.g. awaiting_confirmation → qualified) its canonical payload keeps the original status/conditions; the lifecycle `state` holds the current state | Reports show the record as first stored plus the current state | By design (immutable canonical record, D-007) | None — documented; reports already show `state` | — | Accepted (by design) |
| A-006 | P2 | Data validation costs ~12 ms per frame spread over many small pandas operations (no single hotspot) | Minor vs network latency of live providers | pandas overhead | OHLC consistency check moved to NumPy; further work only if live profiling shows it matters | — | Partly improved |

## v2.6.0 batch (remaining-work session)

| ID | Priority | Finding | Fix | Regression test | Status |
|---|---|---|---|---|---|
| S-001 | P0 security | API responses carried no hardening headers; CI ignored dependency-audit failures (`|| true`) | nosniff, X-Frame-Options DENY, CSP (strict for API, relaxed for /docs), Referrer-Policy, Permissions-Policy, COOP, no-store on /api, HSTS behind HTTPS; CI `pip-audit` now fails the build (current requirements: no known vulnerabilities) | tests/test_platform.py::test_security_headers_on_api_responses | **Fixed** |
| A-007 | P1 correctness | Backtests evaluated strategies with only the primary timeframe (swing excepted), so higher-timeframe score components were "NA" in backtests but present live | point-in-time HTF frames: 1D → completed weeks, intraday → completed daily sessions; cached per completed HTF bar | tests/test_strategies.py::test_backtest_htf_context_is_point_in_time | **Fixed** (intermediate HTFs such as 1H for 15m not rebuilt — documented) |
| P-001 | P2 performance | `/api/v1/signals` loaded the JSON payload of every row and sorted without a (user_id, first_seen) index (14 ms query) | composite index (schema v9, additive migration) + list endpoint skips the payload (1.9 ms query) | migration test asserts the index on upgraded DBs | **Fixed** — but did not change endpoint throughput (see P-002) |
| P-002 | P2 performance | Under 20 concurrent clients the signal list fell from 63 to 15 req/s; root cause measured as GIL contention between 40 worker threads (1 thread: 72 req/s) | `AMW_API_THREADS` knob; default unchanged because fewer threads let one slow request (backtest/report/AI call) block others | measurements in PERFORMANCE_RESULTS.md | **Open — owner decision Q-014** |
| T-001 | test | `test_local_trusted_mode_other_runtimes[javascript]` failed once under heavy CPU load, passed 5/5 alone and in later full runs | none (timing-sensitive under load) | — | Monitoring |

## v2.7.0
| ID | Priority | Finding | Fix | Test | Status |
|---|---|---|---|---|---|
| A-008 | P0 data integrity | Two processes (dashboard + API) each run the scheduler; both could start the same due slot | atomic `schedule_claim` compare-and-set on `last_run` | test_two_processes_never_run_the_same_scheduled_slot (fails with the old behaviour patched in, passes with the fix) | **Fixed** |
| A-007b | P1 correctness | Backtests rebuilt only 1W/1D context; 15m/5m strategies also read 1H/15m in live scans | completed, 09:15-aligned intraday HTF bars | test_backtest_intermediate_htf_frames_are_completed_and_aligned | **Fixed** |

## v2.8.0
* P-002 → mitigated: multi-process API (`AMW_API_WORKERS`) measured 14x faster for the signal list; default stays 1
  until the owner chooses (Q-014). Per-process rate limiting means the effective per-key limit scales with workers.
* Slow synchronous endpoints have background variants (`?async_job=true`) with persisted results.

## v2.9.0
| ID | Priority | Finding | Fix | Test | Status |
|---|---|---|---|---|---|
| A-005 | P1 | Original signal fields were not protected against future edits; transitions read-then-write (possible duplicate events across processes); reports did not separate original vs lifecycle | DB trigger (v12), compare-and-set transitions, lifecycle sections in reports, qualification notifications | tests/test_signal_immutability.py (5), test_reports.py mismatch test now simulates provider revision | **Fixed** (verified on SQLite and PostgreSQL) |
| A-009 | P0 data integrity | Same signal/format/mode reports shared one filename: new generations overwrote old files; retention could delete a file still referenced by COMPLETED rows | report id in the filename | test_each_report_generation_has_its_own_file | **Fixed** |

