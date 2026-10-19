# Implementation plan

## Baseline found (audit)
No pre-existing application code was available: the GitHub repository `abhishekwasu-hue/stockscreener` was created
empty, and no earlier ZIP was supplied. The project was built in this repository from scratch, reusing the earlier
prototype modules (`amw_agent.py`, screener v1) after review. Defects found in that prototype and fixed here:
numpy booleans breaking mandatory-condition checks, swing detection missing equal-high plateaus, intraday session-end
candles treated as forming, FastAPI dropping `_sa*` response keys, a Strategy Lab page selection bug, and template
lists not following the selected language.

## Delivered (v2.0.0 and v2.1.0)
| Milestone | Status | Verification |
|---|---|---|
| Data layer: provider interface, yfinance, fixture provider, validation, holidays, corporate-action flags, cache | Done | tests/test_data.py, live smoke run |
| Analysis: indicators, swings, BOS/CHoCH, zones, S/R, liquidity, candles, patterns, regime | Done | tests/test_analysis.py |
| Six strategy engines, scoring, mandatory gates, lifecycle, dedup | Done | tests/test_strategies.py, tests/test_platform.py |
| Backtesting (shared), paper trading, risk, alerts, AI analyst, jobs | Done | tests/test_platform.py |
| REST API (auth, roles, rate limit, billing webhook), SQLite + PostgreSQL | Done | API tests, PostgreSQL 16 test, Docker Compose run |
| Dashboard: 11 pages | Done | tests/test_ui.py (every page renders; scan + Lab flows) |
| Strategy Lab Phase 1: Python + Pine Script | Released in 2.0.0 | tests/test_lab_release.py, live NSE run |
| Strategy Lab Phase 2: Visual builder, SQL, JavaScript, TypeScript, Lua, R | **Released in 2.1.0** | tests/test_lab.py, tests/test_lab_release.py, tests/test_ui.py, live NSE run, Docker image |
| Docker, Compose, CI (Linux + Windows), docs | Done | image built and run; compose stack verified |
| BYOK AI (OpenAI + Anthropic): encrypted keys, selection/fallback/Dual Review, safeguards, usage dashboard, drafting agent | **Done in 2.2.0** | tests/test_ai_byok.py (mocked providers), UI tests, PostgreSQL round trip |

## Phase 2 — completed in 2.1.0
All eight authoring modes are released. Release criteria met for each: tests pass with the sandbox verified,
cross-language consistency fixture passes, live-data run/scan/backtest completed, runtime included in the Docker image.
Operators can still restrict modes per server with `AMW_LAB_LANGUAGES`.

Open items carried forward (not blockers):
* Visual builder ordering uses ↑/↓ buttons, not drag-and-drop; nested groups are created by "add as OR-group with the
  previous rule" rather than a free tree editor.
* SQL is evaluated per symbol (no cross-sectional ranking across the universe).
* Code editor is ACE (syntax highlighting, themes, find/replace); TypeScript diagnostics are shown after Validate rather
  than inline as you type.
* Independent security review of the JavaScript realm isolation and the R forbidden-call list is recommended before
  accepting code from untrusted public users (see docs/SECURITY.md).
* **Java** is not one of the Lab languages. If it is required, add it as a new runtime (JDK in the image, in-memory
  compilation, `evaluate(Context)` SDK, same contract and consistency fixtures).

## Next platform work (not started)
* BYOK: verify once against the live OpenAI and Anthropic APIs with real user keys; optional integration with an external
  secrets manager (Vault/KMS) for the master key; provider usage/billing APIs if an organisation-level key type is supported.
* Durable job queue (Redis/RQ) if more than one app instance is needed; persistent job recovery after restarts.
* Next.js frontend on top of the existing API, if a public multi-user web product is pursued.
* Authorised broker/market-data adapter (real-time data) via `BrokerProviderTemplate`.
* Load tests on a production-like server (current measurements are single-machine, see docs/TEST_REPORT.md).
* Historical index constituents to reduce survivorship bias in backtests.
