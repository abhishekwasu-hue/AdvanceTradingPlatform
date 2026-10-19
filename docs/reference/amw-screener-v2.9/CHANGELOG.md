# Changelog

## 2.9.0 — 2026-10-19 (owner decisions A-005, Q-014, Q-010, Q-012, Q-002, Q-005)
### Changed / enforced
- A-005: the original signal record is immutable - a database trigger (SQLite and PostgreSQL, schema v12) rejects
  edits to its fields; lifecycle changes are append-only events; transitions are compare-and-set (retries and
  concurrent processes create no duplicate events or notifications); new `signal_qualified` / `signal_expired`
  notification kinds quote the original record; reports add `original_status`, `first_recorded_utc` and
  `lifecycle_events` (existing fields kept).
- Q-014: production compose runs 4 API workers (`AMW_API_WORKERS`, desktop default 1); background API jobs retry
  transient errors (max 3 attempts) and record attempts; synchronous contracts unchanged. PostgreSQL benchmark added.
- Q-010: Elliott Wave / Harmonic excluded from the core; candidate-only extension point (`src/analysis/optional.py`,
  empty registry, cannot affect scores).
- Q-012: scanner copy kept; ownership/independence/authorization tests.
- Q-002: plans marked provisional in config (`plan_meta`), no prices; `/api/v1/plan` adds `commercial_status`.
- Q-005: retention Free 7 / Pro 90 / Premium 365 / Enterprise 365 days (provisional); legal hold (schema v13,
  admin endpoint); reports younger than 30 minutes, not COMPLETED or on hold are never auto-deleted; failed file
  deletions are retried next run; cleanup runs are audited; report retention is separate from backup retention.
### Fixed
- A-009: repeated generation of the same report overwrote the previous file (now one file per report id).

## 2.8.0 — 2026-10-19
### Added
- Multi-process API: `AMW_API_WORKERS` (default 1). Measured at 20 concurrent clients: signal list 13.9 → 196.6 req/s,
  p50 1355 → 84 ms with 4 workers, 0 errors (P-002). Safe with the atomic scheduler claim, the FYERS feed lease,
  DB-recorded jobs/results and the DB-tailing WebSocket.
- Background variants of slow endpoints (`?async_job=true` on `POST /api/v1/backtests`, `/reports`,
  `/lab/strategies/{id}/backtests`); results stored in `job_runs.result` (schema v10) and readable from any process.
  Default synchronous behaviour unchanged.
- Instrument master (schema v11): tickers with NSE symbol, name, ISIN, series, industry and source lists, filled from
  the official lists the app loads; `GET /api/v1/instruments?q=` and `/instruments/resolve?text=` (ambiguous names
  are not guessed).
- Chart drawing tools in the Price Action Workbench (on-screen only, not saved).
### Changed
- Additive column migrations can skip index creation (large text/JSON columns).

## 2.7.0 — 2026-10-19
### Fixed
- A-008: the dashboard and the API both run the scheduler; a due slot could start twice (duplicate scans/alerts).
  Slots are now claimed with an atomic compare-and-set - exactly one process runs each slot.
### Added
- Schedule a saved scanner (schedule kind `scanner`, latest or pinned version); scanner scans now also create forward
  tests and, when the scanner's alerts are enabled, run the user's alert rules.
- Backtests: intermediate higher timeframes rebuilt point-in-time (15m/30m → 1H; 5m → 15m + 1H), completed bars only.
- Controlled provider failover component (`src/data/failover.py`): no candle merging, per-symbol source attribution in
  the canonical record, discrepancy-based refusal, circuit breaker, mixed data kinds rejected (not enabled by default:
  no second provider of the same data kind is configured - Q-001).
- Share a copy of a scanner's rules with another user (no signals/data; recipient publishes) - API + Scanner Builder.

## 2.6.0 — 2026-10-19 (remaining-work session)
### Added
- **Universal scanner layer** (docs/SCANNER_SPEC.md): validated envelope + shared IR, immutable versions, duplicate,
  point-in-time **replay**, same-data **version comparison**, deterministic **natural-language builder** (no AI key
  needed; unsupported requests reported, never substituted), **19 templates** in 5 categories, Scanner Builder page,
  API `/api/v1/scanner*`.
- New IR predicates: candle_pattern, supertrend_dir, pct_change, rsi_divergence, gap_recent, sr_role_reversal,
  last_swing_label, htf_close_above_ema; predicate argument validation.
- Analysis: Supertrend, Fibonacci extensions, Pin Bar, Strong Body, Rejection, Gap Reversal, gap analysis, S/R role
  reversal / crosses / freshness (definitions in docs/STRATEGY_RULES.md).
- Research reports: backtest, forward-test and daily scanner reports (PDF + CSV).
- System notifications (signal invalidated/completed, forward test closed, daily summary, feed failure, backtest
  finished) with DB dedup, quiet hours, hourly cap, per-kind channel opt-in and an in-app inbox.
- Data Health & Admin page; `AMW_API_THREADS` knob.
### Fixed / changed
- S-001 security headers on all API responses; CI fails on known-vulnerable dependencies.
- A-007 backtests receive point-in-time higher-timeframe context (completed weeks / completed sessions).
- P-001 signal-list query 14 ms → 1.9 ms (index + no payload); throughput limit traced to thread contention (P-002).
- Daily/forward CSV exports always include headers.
- Schema v8 (`notification_events`) and v9 (index on signals(user_id, first_seen)); additive migrations.

## 2.5.1 — 2026-10-19 (continuation audit; docs/autonomous/AUDIT_FINDINGS.md)
### Fixed
- A-001: the swing-strategy backtest treated the current partial week as a completed weekly bar (now dropped, as live).
- A-002: a concurrent duplicate signal insert rolled back the whole batch and lost its other signals (now skipped atomically).
- A-003: a job's final status could be observed before its database record was written.
### Performance
- A-004: scan analysis 24 % faster on the fixture benchmark with identical results (docs/autonomous/PERFORMANCE_RESULTS.md).

## 2.5.0 — 2026-10-19
### Added
- **Durable job records** (`job_runs`): every scan/lab/background job is stored with status, progress, error, holder
  and heartbeat. On start-up, work interrupted by a crash/restart (jobs, lab runs, scheduled runs, reports) is marked
  FAILED with a visible reason instead of disappearing. Jobs of another process (dashboard vs API) are visible read-only.
- **Paper forward tests** of every qualified signal (`forward_tests`): entry at the next bar's open, exit at
  stop/target (stop assumed when both touch one bar; gaps fill at the open) or expiry; R multiple; per strategy-version
  stats with INSUFFICIENT EVIDENCE below 30 closed tests. Marked daily after the close and on demand.
  API `GET /api/v1/forward`, `POST /api/v1/forward/mark`; section on *Paper Trading & Journal*.
- **Shared FYERS minute budget**: the per-minute request budget of an account is a database counter shared by all
  processes (previously only the daily guard was shared).
- **Report retention and storage quota** per plan (`report_retention_days`, `report_storage_mb`; defaults are
  engineering assumptions, Q-005): expired/over-quota files are deleted, rows stay as DELETED with the reason.
- Navigation: *Signal Intelligence* → **Open on chart** (Workbench at the signal's ticker/timeframe with its stored
  stop/target) and **Create report** (Report Center preselects the signal).
### Changed
- Price Action Workbench uses the user's selected data source (default or own FYERS account).
- Daily maintenance (forward marking, report retention) runs in the scheduler after 15:45 exchange time.
- Schema version 7 (`job_runs`, `rate_buckets`, `forward_tests`).

## 2.4.0 — 2026-10-19
### Added
- **Live 1-minute data from the user's own FYERS account** (page *Live Data (FYERS)*, docs/LIVE_DATA_FYERS.md):
  encrypted app credentials, daily OAuth login with a one-time state, background collector (completed and validated
  bars only, revisions, catch-up of missed minutes from FYERS' own candles, no forward-fill, 30-day retention),
  per-user live stream (`/api/v1/stream/bars` WebSocket) and stored bars API, FYERS as an optional scan data source.
- Multi-process safety: a database lease allows one collector per user across the dashboard and API processes, shared
  status (`runner`), Stop from any process, login in any process resumes an expired feed, auto-resume on start-up
  (`AMW_FEED_AUTORESUME`), daily rate-limit guard shared through the database.
- `GET/PUT /api/v1/data-source` report the selected and the actually active scan data source; the Screener warns when
  a FYERS selection falls back to the default provider.
- Endpoints: `PUT/DELETE /api/v1/broker/fyers`, `GET /broker/fyers/login-url`, `POST /broker/fyers/callback`,
  `POST /feed/start`, `POST /feed/stop`, `GET /feed/status`, `GET /feed/bars`, WebSocket `/stream/bars`.
- `websockets` dependency (WebSocket transport for uvicorn).
### Changed
- A scan stops with an explicit error when the data source reports an expired session or the rate-limit guard
  (previously every remaining symbol would have been reported as "no data").
- The "no broker integration" safety test now asserts that no order/position/funds routes exist and that the FYERS
  client calls only the login and history URLs.
- Secrets typed into the dashboard (FYERS secret, AI API key, Telegram bot token) are removed from server-side widget
  state after a successful save.
- Schema version 6 (`broker_connections`, `minute_bars`).
### Fixed
- `start_screener.bat` line endings restored to CRLF; `.gitattributes` keeps `*.bat` as CRLF.

## 2.3.0 — 2026-10-18 (autonomous session; see docs/autonomous/FINAL_REPORT.md)
### Security (fixed, with regression tests)
- SEC-001 jobs are owner-scoped in the API (list/read/cancel/signals).
- SEC-002 signals carry an owner (schema v4, additive migration); all signal reads/writes are owner-filtered.
- SEC-003 alert rules are evaluated only against the rule owner's scans.
- SEC-004/005 Telegram/email go to each user's own chat/address (no shared operator chat/mailbox).
### Added
- Canonical signal record `amw.signal.v1` with provider, data kind, data snapshot fingerprint, evaluation time.
- Reports: PDF, XLSX, CSV, JSON, PNG (annotated static chart renderer), owner-only download, Report Center page.
- Telegram: official AMW bot linking via `/start <code>`, user-owned bot (encrypted token), quiet hours, hourly cap,
  bounded retries, document delivery.
- Scheduler: owner-scoped daily/interval scans and scheduled PDF reports to Telegram (trading days only).
- Plan entitlements (config-driven; enforced in backend) and `GET /api/v1/plan`.
- Backtest metrics carry `evidence` = INSUFFICIENT EVIDENCE below 30 trades.
- End-to-end tests (scanner → record → chart → report → Telegram; NL strategy → backtest; backtest determinism).
### Fixed
- Bearish trend-continuation condition text read `close<EMA20<EMA50>EMA200` (logic was correct; text was wrong).
### Changed
- `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID`/`ALERT_EMAIL_TO` replaced by `AMW_TELEGRAM_BOT_TOKEN` + per-user settings.
- Schema version 5 (`reports`, `schedules`; `signals.user_id`, `signals.canonical_key`, `scan_runs.user_id`).

## 2.2.0 — 2026-10-18
### Added
- **Bring Your Own Key AI** for OpenAI and Anthropic: encrypted per-user keys (AES-256-GCM, bound to user and provider),
  session-only option, test-before-save via the free model-list endpoint, masked display, replace/delete.
- Provider selection: default, manual, task routing, fallback policy (none/ask/auto to the user's other key), Dual Review
  with explicit per-request confirmation.
- Local spending safeguards (calls per task, retries, output tokens, per-task/daily/monthly estimated cost and request
  thresholds, confirmation, agent depth, repair attempts, cancellation) and a per-user AI usage dashboard that separates
  provider-reported tokens from AMW's estimate and points to the provider's billing dashboard.
- AI strategy drafting agent (bounded generate/validate/repair; drafts only).
- API: `/api/v1/ai/providers`, `/ai/keys/{provider}`, `/ai/policy`, `/ai/usage`, `/ai/draft-strategy`, `/ai/dual-review`.
- Page *AI Provider Setup & Usage*; AI Chart Analyst rewritten on the gateway.
### Removed
- The shared `ANTHROPIC_API_KEY` environment-variable client (company-key path).
### Security
- Root logging filter redacts API-key patterns; provider errors are redacted before reaching users.

## 2.1.0 — 2026-10-18
### Added
- Released the remaining Strategy Lab modes: **Visual No-Code Builder, SQL, JavaScript, TypeScript, Lua, R**
  (all eight modes now available). Operators can restrict modes with `AMW_LAB_LANGUAGES`.
- Production Docker image now includes Node.js 22 + TypeScript 6.0.2 and R.
- Windows local trusted mode covers every code runtime; `scripts/check_runtimes.py` reports what can execute.
- End-to-end tests (create → validate → run → publish → scan → backtest) for all eight modes; Visual builder UI test.
### Fixed
- R crashed on large backtest batches: contexts are now evaluated in chunks of 40 with a higher memory limit.
- Worker processes no longer write bytecode (`python -B`): under root with the file-size limit, `-I` ignored
  `PYTHONDONTWRITEBYTECODE` and could leave truncated `.pyc` files.
### Changed
- `AMW_LAB_PHASE2` removed (no longer needed).

## 2.0.0 — 2026-10-18
### Added
- Strategy Lab with a unified strategy contract; **Python** and **Pine Script (subset)** released (Phase 1).
- Phase 2 runtimes implemented behind a release gate: JavaScript, TypeScript, SQL, Lua, R, Visual No-Code Builder.
- Linux process sandbox with empirical self-test; Windows local trusted mode.
- Six strategy engines (Breakout, Pullback, Reversal, Trend Continuation, Intraday, Swing) with mandatory gates and
  transparent scoring; signal lifecycle with stored transitions; de-duplication.
- Market regime engine, weekly timeframe, holiday-aware staleness, missing-bar and corporate-action checks.
- Shared backtest engine (gap handling, costs, IS/OOS, walk-forward, sensitivity); paper trading ledger; risk sizing.
- Alerts (in-app, Telegram, email) with cooldown and duplicate prevention; AI Chart Analyst with number verification.
- REST API `/api/v1` (token auth, roles, rate limit, request ids, billing webhook); PostgreSQL support; Docker/Compose; CI.
- 11-page dashboard; all product text in English.

### Changed
- Entry point is now `Home.py` (was `app.py`). Launchers: `setup_windows.bat`, `start_screener.bat`, `start_api.bat`.

### Dependencies
Pinned in `requirements.txt` (Python 3.11). Upgrade procedure: bump one package, run `python -m pytest` and `ruff check .`,
then the CI pipeline (Linux with sandbox + PostgreSQL, Windows) before merging.
