# Implementation log

## Batch 1 — P0 tenant isolation (SEC-001..005) + canonical signal provenance
Files: src/services/jobs.py, src/api/main.py, src/storage/db.py (schema v4), src/services/lifecycle.py,
src/services/alerts.py (rewritten), src/notify/telegram.py (new), src/services/app_services.py, src/lab/service.py,
src/screener/scanner.py (`stamp_canonical`, `data_snapshot_hash`), pages/3, pages/5, pages/10,
tests/test_platform.py (updated + 5 regression tests), tests/test_notify.py (new).
Verification: `python -m pytest -q` → 172 passed, 1 skipped.

## Batch 2 — Reports + static chart renderer
Files: src/reports/{builder,chart_png}.py (new), src/storage/db.py (schema v5: reports, schedules), pages/12_Report_Center.py,
requirements.txt (+reportlab, matplotlib, openpyxl), tests/test_reports.py. Artifacts PNG and PDF inspected visually.
Defect found by inspecting the PDF: bearish EMA-alignment detail text (fixed in src/strategies/trend.py, regression test added).

## Batch 3 — Telegram/email per user, schedules, entitlements
Files: src/notify/telegram.py, src/services/alerts.py, src/services/scheduler.py, src/saas/entitlements.py,
src/api/main.py (reports, schedules, notify, plan endpoints; 403 plan_limit), pages/7, pages/10, pages/12,
config/settings.yaml (plans), .env.example, start_screener.bat, api_server.py, tests/test_notify.py, tests/test_saas_sched.py.

## Batch 4 — Backtest evidence label + end-to-end workflows
Files: src/backtest/engine.py (`evidence`, MIN_TRADES_FOR_EVIDENCE=30), pages/6, pages/10, tests/test_e2e.py.

## Batch 5 — Verification and documentation
Fresh venv, Docker image, PostgreSQL in-place upgrade v3→v5, servers started; docs updated (README, CHANGELOG,
ARCHITECTURE, SECURITY, TEST_REPORT, REPORTS_NOTIFICATIONS, API.md regenerated: 63 endpoints).

## Continuation audit batch (2026-10-19) — v2.5.1
Files: src/backtest/engine.py (A-001), src/storage/db.py (A-002 `_insert_ignore`), src/services/jobs.py (A-003),
src/screener/scanner.py + src/analysis/indicators.py + src/data/validation.py (A-004 performance),
tests/test_strategies.py, tests/test_platform.py (regression tests), docs/autonomous/AUDIT_FINDINGS.md,
PERFORMANCE_RESULTS.md (new), CHANGELOG.md, pyproject.toml.
Verification: full suite 231 passed, 2 skipped; benchmark median 6.52 s → 4.96 s with identical results.

## v2.6.0 — remaining-work batches (2026-10-19)
* B1 P0: security headers + strict CI audit (S-001); point-in-time HTF context in backtests (A-007).
  Files: src/api/main.py, .github/workflows/ci.yml, src/backtest/engine.py; tests in test_platform.py, test_strategies.py.
* B2 analysis v2: Supertrend, Fib extensions, Pin Bar / Strong Body / Rejection / Gap Reversal, gap analysis, S/R role
  reversal + freshness. Files: src/analysis/indicators.py, candles.py, structure.py, context.py; tests/test_analysis_v2.py.
* B3 scanner foundation: 8 new IR predicates + argument validation; Lab context adds candles/gaps/SR fields; scanner
  spec envelope, save/version/duplicate, point-in-time replay, version comparison, deterministic NL builder, 19
  templates, API endpoints, Scanner Builder page. Files: src/lab/ir.py, src/lab/contract.py, src/scanner/*,
  src/api/main.py, pages/14_Scanner_Builder.py; tests/test_scanner_predicates.py, test_scanner_layer.py, test_ui.py.
* B4 reports + notifications: backtest / forward / daily research reports; system notifications (invalidated,
  completed, forward closed, daily summary, feed failure, backtest done) with dedup, quiet hours, hourly cap; prefs +
  inbox UI. Files: src/reports/research.py, src/services/notify_events.py, src/storage/db.py (v8), hooks in
  app_services, forward, scheduler, collector, Backtesting/Report Center/Watchlist pages; tests/test_reports_notify_v2.py.
* B5 operations: Data Health & Admin page (provider/feed status, data quality, users/plans, audit log); API latency
  benchmark; P-001 index (schema v9); P-002 threads knob. Files: pages/15_Data_Health_Admin.py, api_server.py.
Status: complete for the listed scope; blocked items unchanged (FYERS/AI/Telegram live tests, options data, pricing).

## v2.7.0 — remaining jobs (2026-10-19)
Files: src/storage/db.py (schedule_claim, user_by_username), src/services/scheduler.py (claim + `scanner` kind),
src/lab/service.py (forward + alerts for scanner scans, user's data source), src/backtest/engine.py (intermediate HTF),
src/data/failover.py (new), src/screener/scanner.py (per-symbol source), src/scanner/spec.py (share), src/api/main.py
(schedule kind scanner, share endpoint), pages/7_Watchlist_Alerts.py, pages/14_Scanner_Builder.py; tests in
test_saas_sched.py, test_strategies.py, test_failover.py (new), test_scanner_layer.py.
Verification: full suite 272 passed, 2 skipped.

## v2.8.0 (2026-10-19)
Files: api_server.py (workers), src/services/jobs.py (submit_api, persisted results), src/storage/db.py (v10 result
column, v11 instruments, migration option), src/api/main.py (async_job on backtests/reports/lab backtests, instrument
endpoints), src/data/instruments.py (new), src/services/app_services.py (master sync), pages/3_Price_Action_Workbench.py
(drawing tools); tests: test_platform.py (background jobs), tests/test_instruments.py (new).
Verification: full suite 275 passed, 2 skipped; multi-process benchmark in PERFORMANCE_RESULTS.md.

## v2.9.0 — owner decisions (2026-10-19)
Files: src/storage/db.py (v12 trigger, v13 legal_hold, idempotent transition, report_set_hold), src/services/lifecycle.py,
src/services/notify_events.py, src/reports/builder.py (lifecycle sections, retention safety, A-009), src/services/jobs.py
(retries), src/saas/entitlements.py, src/api/main.py (/plan status, legal-hold endpoint), src/analysis/optional.py (new),
config/settings.yaml, docker-compose.yml, .env.example, pages 7/12/15; tests: test_signal_immutability.py,
test_decisions.py (new), test_reports.py, test_v25.py (retention test aligned with the 30-minute protection).
Verification: full suite 291 passed, 2 skipped; PostgreSQL trigger and idempotency checked directly.

