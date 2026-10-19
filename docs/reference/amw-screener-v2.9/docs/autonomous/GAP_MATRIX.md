# Feature audit and gap matrix (baseline b63af78)

Status vocabulary: IMPLEMENTED_AND_VERIFIED · IMPLEMENTED_BUT_UNDERTESTED · PARTIALLY_IMPLEMENTED · MOCK_OR_PLACEHOLDER ·
MISSING · BLOCKED_BY_EXTERNAL_DEPENDENCY · SECURITY_CONCERN · CORRECTNESS_CONCERN. Evidence = file / test.

| ID | Feature | Baseline status | Evidence | Gap | Priority |
|---|---|---|---|---|---|
| F-01 | OHLCV validation, forming-candle exclusion, staleness, holidays | IMPLEMENTED_AND_VERIFIED | src/data/validation.py; tests/test_data.py | provider failover/reconciliation absent (single provider) | P1 |
| F-02 | Market structure (swings, BOS/CHoCH), non-repainting | IMPLEMENTED_AND_VERIFIED | src/analysis/structure.py; tests/test_analysis.py | — | — |
| F-03 | Six strategy engines + scoring + mandatory gates | IMPLEMENTED_AND_VERIFIED | src/strategies/*; tests/test_strategies.py | — | — |
| F-04 | Scan jobs (API) | **SECURITY_CONCERN** | src/api/main.py `/jobs*`; src/services/jobs.py | jobs not owner-scoped: any user can list, read signals of and cancel another user's job (SEC-001) | **P0** |
| F-05 | Signal store / lifecycle | **SECURITY_CONCERN** | src/storage/db.py `signals` (no owner column) | private lab-strategy and watchlist signals visible to all users via `/signals`; lifecycle updates cross tenants (SEC-002) | **P0** |
| F-06 | Alerts | **SECURITY_CONCERN** | src/services/alerts.py `process_alerts` | every user's alert rules evaluated against every user's scan signals (SEC-003); Telegram/email go to ONE operator chat/address for all users (SEC-004) | **P0** |
| F-07 | Canonical signal record | PARTIALLY_IMPLEMENTED | src/strategies/base.py `Signal` | no owner, provider, data-snapshot hash; reports/messages not derived from stored record | P1 |
| F-08 | Reports (PDF/XLSX/PNG/JSON) | MISSING (CSV only) | src/screener/export.py | report generation, permissions, artifacts | P2 |
| F-09 | High-resolution chart renderer | MISSING | Plotly interactive only (no kaleido) | static PNG renderer for reports/Telegram | P2 |
| F-10 | Telegram (official + user-owned bot), quiet hours, rate limit | PARTIALLY_IMPLEMENTED | src/services/alerts.py | per-user linking, encrypted user bot token, quiet hours, rate limit, delivery log | P2 |
| F-11 | Scheduled scans / reports | MISSING | — | scheduler with injected clock, owner-scoped schedules | P2 |
| F-12 | SaaS plan entitlements | MISSING (plan column only) | users.plan | config-driven limits enforced in backend | P2 |
| F-13 | Backtest metrics sample-size guard | CORRECTNESS_CONCERN | src/backtest/engine.py `metrics` | win rate etc. shown for 1–5 trades without warning → mark INSUFFICIENT EVIDENCE | P1 |
| F-14 | BYOK AI gateway | IMPLEMENTED_AND_VERIFIED (mocked) | src/ai/*; tests/test_ai_byok.py | live provider verification needs user keys | P2 (blocked) |
| F-15 | Strategy Lab (8 modes) | IMPLEMENTED_AND_VERIFIED | src/lab/*; tests/test_lab*.py | — | — |
| F-16 | Paper forward testing tied to signals | PARTIALLY_IMPLEMENTED | src/services/paper.py (ledger only) | persistence + signal linkage | P3 |
| F-17 | Real-time / licensed market data | BLOCKED_BY_EXTERNAL_DEPENDENCY | yfinance only | provider + licence decision (Q-001) | P1 (blocked) |
| F-18 | Elliott/harmonic patterns | MISSING (deliberately) | — | ambiguous; only as candidates with uncertainty | P3 |
