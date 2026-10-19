# Final report — AMW AI Custom Stock Screener™ (state after v2.9.0)

Scope of this report: the autonomous sessions from v2.2.0 (last commit `b63af78`) to v2.6.0. All changes are in the
working tree, **uncommitted** (decision D-001). Verified results are marked as such; everything else is labelled.

## A. Executive summary
Inspected the whole repository (Python 3.11, Streamlit UI, FastAPI, SQLAlchemy Core on SQLite/PostgreSQL, sandboxed
Strategy Lab, pandas/NumPy analysis). Implemented across sessions: tenant-isolation fixes, canonical signal record,
reports, Telegram/e-mail, scheduler, plans, FYERS live 1-minute feed, durable jobs, forward tests, retention, audit fixes
(A-001..A-007), security headers, analysis v2, a universal scanner layer (spec, NL builder, templates, replay, version
comparison), research reports, system notifications, data-health/admin page and measured performance work.
Final verification (this session): repository suite 263 passed / 2 skipped; clean copy + new venv + PostgreSQL 16 265
passed; Docker 263 passed / 2 skipped. Left incomplete: live verification of FYERS / AI providers / Telegram (no credentials here),
scanner sharing, scheduling scanners, options backtests, Elliott/Harmonic, the API thread decision (Q-014).

## B. Initial audit (v2.2.0 baseline)
Verified then: six strategy engines, structure analysis, backtester, Strategy Lab (8 modes), BYOK AI, API, Docker.
Major gaps found: cross-tenant leaks (SEC-001..005), no canonical signal provenance, no reports/Telegram per user,
no scheduler/plans, no live broker data. Later audits found A-001 (forming week in backtests), A-002 (concurrent insert
lost a batch), A-003 (status before record), A-007 (no HTF context in backtests), S-001 (no security headers). Feature
matrix: /mnt/session/outputs gap analysis and docs/autonomous/AUDIT_FINDINGS.md.

## C. Implementation results (by version; details in IMPLEMENTATION_LOG.md)
| Version | Feature | Verification |
|---|---|---|
| 2.3.0 | SEC-001..005, canonical record, reports (5 formats), Telegram official/own bot, scheduler, plans | regression + e2e tests |
| 2.4.0 | FYERS per-user 1-minute feed, DB lease across processes, WebSocket stream | mocked FYERS + real uvicorn/WebSocket check |
| 2.5.0 | durable jobs + orphan recovery, forward tests, shared FYERS minute budget, retention/quota, signal→chart/report | tests/test_v25.py |
| 2.5.1 | A-001, A-002, A-003 fixes; scan analysis −24 % (identical results) | fix-reverted tests fail, fixed pass |
| 2.6.0 | S-001, A-007, analysis v2, scanner layer, research reports, notifications, data health/admin, P-001 | tests/test_analysis_v2.py, test_scanner_predicates.py, test_scanner_layer.py, test_reports_notify_v2.py, UI tests |

## D. Test results (actual commands; full log in TEST_RESULTS.md)
| Environment | Command | Result |
|---|---|---|
| Dev machine (v2.6.0) | `python -m pytest -q` | 263 passed, 2 skipped (PostgreSQL-only tests) |
| Dev machine | `ruff check .` | clean |
| Clean copy, new venv, PostgreSQL 16 (DB upgraded v7 → v9 in place) | `AMW_TEST_PG_URL=… pytest -q` | **265 passed** |
| Docker image | `pytest -q` (CAP_SYS_ADMIN) | **263 passed, 2 skipped** |
| Built-in scan reproducibility | fixture benchmark fingerprint | unchanged since v2.5.0 (a95857642897f5c5) |

## E. Security findings
SEC-001..005 (High, fixed), S-001 (Medium, fixed). Secrets: AES-256-GCM per user/purpose, never returned, redacted.
No order/position/funds routes (test-enforced). Residual risks: Streamlit desktop UI has no per-user login; Docker
sandbox needs CAP_SYS_ADMIN; Windows local trusted mode is not a sandbox; FYERS per-minute budget per process for
scans was closed in v2.5 (shared counter). Details: SECURITY_FINDINGS.md. This is not a complete security audit.

## F. Data and financial correctness
Completed bars only; missing data never filled; typed data statuses; stale detection; non-repainting swings;
close-confirmed BOS/CHoCH; candle confirmation inside the window only. Backtests: signals from bars ≤ j, next-bar
entry, stop-first on ambiguous bars, gap fills at the open, costs, point-in-time weekly/daily context (A-001, A-007).
Replay cuts history at as_of and drops the forming bar; limitation: provider revisions/adjustments are not
point-in-time. Scores are not probabilities; < 30 trades = INSUFFICIENT EVIDENCE. Options backtests not implemented.

## G. Performance results (PERFORMANCE_RESULTS.md)
Scan analysis 6.52 → 4.96 s on 40 synthetic symbols (v2.5.1, identical results). API (uvicorn, SQLite, 20 concurrent):
health 292 req/s, jobs 233, forward 185, meta 124, signal list 15–20 req/s; signal-list query 14 → 1.9 ms (P-001) but
throughput limited by thread contention (P-002: 72 req/s with 1 thread). No load test with PostgreSQL, multiple
processes or 1,000 users — no capacity claim is made.

## H. Incomplete features
Blocked: FYERS/OpenAI/Anthropic/Telegram live tests, licensed shared data feed, options data, pricing.
Missing/partial (after v2.7.0): workspace sharing and favourites, drag-and-drop builder (structured editing instead),
Elliott/Harmonic, instrument master, failover enabled in production (needs a second same-kind provider), chart
drawing tools, load test of a multi-process API, Q-014 slow endpoints as background jobs.

## I. Owner questions
Q-001 (shared data licence), Q-002 (plans/prices), Q-003 (official bot), Q-005 (retention defaults), Q-006 (live
execution — not planned), Q-007 (FYERS live test), Q-008..Q-013 (assumptions applied in v2.6.0), Q-014 (API threads).

## J. Environment setup (actual commands)
```
python -m venv .venv && . .venv/bin/activate          # Windows: setup_windows.bat
pip install -r requirements-dev.txt
ruff check . && python -m pytest -q                    # AMW_TEST_PG_URL=postgresql+psycopg://… for PostgreSQL tests
streamlit run Home.py                                  # dashboard  (Windows: start_screener.bat)
python api_server.py                                   # API on 127.0.0.1:8000 (Windows: start_api.bat)
docker compose up -d                                   # PostgreSQL + app + api (set .env from .env.example)
```

## K. Next tasks (highest priority first)
1. Commit and push; run CI (Linux + PostgreSQL, Windows).
2. FYERS live trading-day verification (Q-007); AI and Telegram live checks with owner keys.
3. Q-014: move slow synchronous endpoints (backtest, reports, AI) to background jobs, then lower API threads; re-measure.
4. Scheduler lease, then allow multiple API processes.
5. Schedule a saved scanner (scheduler → lab scan) and scanner alerts from the envelope.
6. Scanner sharing within a workspace (Q-012).
7. Rebuild intermediate HTFs in backtests (1H for 15m/5m strategies).
8. Provider failover with discrepancy checks; instrument master.
9. PostgreSQL load test (signals, jobs, reports) with realistic row counts.
10. Independent security review of sandbox workers before public multi-tenant launch.

## L. Final repository status
Branch `main` · starting commit `b63af78` (v2.2.0) · no new commits (D-001) · working tree: all v2.3–v2.6 changes
uncommitted · build: Docker image builds (with host CA in this environment) · known limitations: section H.

## v2.7.0 addendum
Done: A-008 (duplicate scheduled runs across processes), scheduling saved scanners, intermediate HTF in backtests,
failover component, scanner sharing (copy). Next-task list item 4 (scheduler lease) and 5 (schedule a scanner) are
complete; 6 partly (copy sharing); 7 complete; 8 component done, production enablement blocked by Q-001.
Verification: see TEST_RESULTS.md v2.7.0 rows.

## v2.8.0 addendum
Done: multi-process API knob with measured 14x signal-list throughput (P-002), opt-in background endpoints with
persisted results, instrument master, chart drawing tools. Still open: Q-010 (Elliott/Harmonic), Q-012 workspace
sharing, Q-014 production defaults, live verifications, licensed shared feed, options data.

## v2.9.0 addendum (owner decisions)
A-005, Q-014, Q-010, Q-012, Q-002, Q-005 implemented (DECISION_LOG D-032..D-037); A-009 found and fixed while testing
retention. Open business/external items: QUESTION_LOG "Still open".

