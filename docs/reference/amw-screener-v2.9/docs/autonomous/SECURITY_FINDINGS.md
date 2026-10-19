# Security findings (this session)

Scope: code review of API authorization, storage queries, background jobs, alert delivery and secret handling, plus
regression tests on local fixtures. No third-party systems were tested. This is not a complete security audit.

| ID | Severity | Finding | Evidence | Remediation | Regression test | Status |
|---|---|---|---|---|---|---|
| SEC-001 | High | Scan/lab jobs not owner-scoped in the API (list/read/cancel any job, read its signals) | src/api/main.py `/api/v1/jobs*` | `Job.owner`; `JobManager.get/list/cancel(owner, admin)`; API passes caller | tests/test_platform.py::test_api_jobs_and_signals_are_owner_scoped | **Fixed** |
| SEC-002 | High | `signals` had no owner; `/api/v1/signals` returned every user's signals incl. private lab strategies; lifecycle updated other users' signals | src/storage/db.py | `user_id` + `canonical_key` columns (schema v4, additive migration), stored key scoped per owner, all signal reads/writes owner-filtered | test_signals_and_lifecycle_are_owner_scoped, test_schema_migration_adds_owner_columns_to_old_database | **Fixed** |
| SEC-003 | High | Alert rules of all users evaluated against any user's scan | src/services/alerts.py | `process_alerts(store, rows, user_id)` evaluates only the owner's rules | test_alerts_only_use_the_scan_owners_rules | **Fixed** |
| SEC-004 | High | Telegram alerts of all users sent to one operator chat | src/services/alerts.py | per-user linking (official bot via `/start` code, or user-owned bot with encrypted token) | tests/test_notify.py | **Fixed** |
| SEC-005 | Medium | Email alerts of all users sent to one operator address | src/services/alerts.py | per-user alert email setting | test_telegram_not_linked_is_skipped (+ code review) | **Fixed** |

Residual risks: legacy signal rows created before schema v4 have no owner (visible only to the system view);
in-memory jobs are lost on restart; Streamlit desktop mode is single-user by design.

## Continuation audit (2026-10-19)
No new security vulnerability found in the reviewed areas (storage, jobs, backtest, analysis). A-002 (concurrent insert
rollback) is a data-integrity issue, fixed; see AUDIT_FINDINGS.md. Tenant scoping of the new v2.5 tables
(`job_runs`, `forward_tests`) is covered by tests (owner-scoped job lookup, forward API isolation).

## v2.6.0
| ID | Severity | Finding | Remediation | Test | Status |
|---|---|---|---|---|---|
| S-001 | Medium | No security headers on API responses; CI did not fail on vulnerable dependencies | headers middleware; strict `pip-audit` (no known vulnerabilities in current requirements at the time of the change) | test_security_headers_on_api_responses | Fixed |
New v2.6 data (scanners, notifications, research reports) is owner-scoped; cross-user access tests: scanner API (404),
replay/duplicate (KeyError), notifications and daily report (empty for other users), forward API.
Residual: Streamlit UI has no per-user login (desktop/local admin by design); CSRF not applicable to the Bearer API.
