# AMW port - status audit (owner order 11a)

**Date:** 2026-10-22 · **Checked against:** `main` at 15f619f (PR #329) · **Plan audited:** `docs/design/AMW_PORT.md`

Scope: every page row (P-0..P-15), every Strategy Lab row (L-1..L-14), every AI row (AI-1..AI-9), every rule-conflict
row (C-1..C-21) and every slice (AMW-1..AMW-25) of the port plan.

How each status was decided:
- The code on `main` was read: `backend/app` and `frontend/src`, the ScreenQL registry
  (`backend/app/screener/registry.py`), the flags in `backend/app/platform/controls.py` (`DEFAULT_OFF_FLAGS` /
  `FEATURE_FLAGS`), the routes, and the merged PR titles. The plan's own Have / Partial / Missing column was not
  trusted: several rows have moved since the plan was written (PRs #258, #260, #266, #267, #268, #272, #300, #306).
- **Ported**: the behaviour (or, for a C- row, the resolution) is on `main`, behind its flag where one is named.
- **Partial**: part of it is on `main`; the "missing" column says what is not.
- **Missing**: nothing equivalent on `main`. Rows the plan decided to skip say so in the "missing" column.
- No flag of AMW-4..AMW-25 exists in `controls.py` today (checked: `chart_patterns`, `screener_templates_amw`,
  `screener_nl`, `screener_versions`, `signal_score_coverage`, `signal_ledger`, `paper_forward_tests`, `chart_analyst`,
  `byok_routing`, `byok_dual_review`, `report_center`, `report_schedules`, `data_health`, `fyers_minute_feed`,
  `strategy_lab`, `strategy_lab_host`, `strategy_lab_python`, `strategy_lab_sql_lua`, `strategy_lab_js`,
  `strategy_lab_r`). There is no `backend/app/lab/`, no `/api/lab/*` route and no `/strategy-lab` page.

## Counts

| Group | Ported | Partial | Missing | Total |
|---|---|---|---|---|
| Slices AMW-1..AMW-25 | 4 | 2 | 19 | 25 |
| Pages P-0..P-15 | 3 | 12 | 1 | 16 |
| Strategy Lab L-1..L-14 | 0 | 3 | 11 | 14 |
| AI AI-1..AI-9 | 3 | 4 | 2 | 9 |
| Rule conflicts C-1..C-21 | 8 | 4 | 9 | 21 |
| **All audited items** | **18** | **25** | **42** | **85** |

## 1. Slices AMW-1..AMW-25

| Item | What it is | Status | Evidence | What is missing |
|---|---|---|---|---|
| AMW-1 | Plan limits v2 (watchlists, screen symbols, reports, schedules, Lab budget) | Ported | PR #266; `backend/app/plans/{registry,limits}.py`; flag `plan_limits_v2`; `frontend/src/components/PlanLimitsV2.tsx` | Only the watchlist limit has a feature to enforce today; the report, schedule and Lab limits wait for their slices. The Screener run cap is still a fixed 50 (`MAX_SYMBOLS`, `screener/routes.py`), not `max_screen_symbols` (AMW2-2). |
| AMW-2 | Named personal watchlists as a Screener / Scanner universe | Ported | PR #267; `backend/app/watchlists/{service,routes}.py`; flag `watchlists`; `frontend/src/screener/WatchlistPicker.tsx` on `ScreenerPage` and `ScannerPage`; tenancy rows in `tests/tenancy_route_table.py` | - |
| AMW-3 | New ScreenQL functions: StDev, RsiDivergence, SwingLabel, TimeOfDay, two candles, next-bar confirmation | Ported | PR #268; `backend/app/screener/{registry,runtime,amw_functions}.py`; Z10-a cases in `backend/app/quality/causal_cases.py`; flag `screenql_amw_functions` | - |
| AMW-4 | Named chart patterns (double top, head and shoulders, triangles, wedges) with status | Missing | No `price_action/chart_patterns.py`, no `ChartPattern` in the registry, no flag `chart_patterns` | All of it. BK4 pattern breakouts (#241) cover flag / triangle breakouts only. |
| AMW-5 | AMW's scanner templates as gallery entries | Missing | No AMW template in `backend/app/screener/data/taxonomy.json` or `frontend/src/screener/starters.json`; no flag `screener_templates_amw` | All of it. AMW-3 (done) already provides the functions most of them need. |
| AMW-6 | Plain words to ScreenQL, with assumptions listed | Partial | PR #272 (S9): `backend/app/screener/nl.py`, `POST /api/screener/copilot/nl`, flag `screener_copilot` | S9 needs an AI provider; with none it refuses. The deterministic no-key parser (`screener_nl`), clause-by-clause interpretations and UNSUPPORTED clauses are missing. |
| AMW-7 | Immutable screen versions, duplicate, compare two versions | Missing | `PUT /api/screener/screens/{id}` still updates in place; no `screen_versions` table, no publish / compare route | All of it. |
| AMW-8 | Score coverage and a mandatory gate on the grade | Missing | `backend/app/signal_scoring/engine.py` has no coverage field; no flag | All of it. |
| AMW-9 | Signal ledger: immutable signal records and lifecycle | Missing | No `signal_records` / `signal_events`; the v2 Signals inbox (PR #306, `GET /api/signals/feed`) reads existing signals only | All of it. |
| AMW-10 | Paper forward test of every qualified signal | Missing | No `forward_tests` table or route | All of it (needs AMW-9). |
| AMW-11 | Rules-only Chart Analyst (setups, rejected setups, invalidation) | Missing | No `backend/app/ai/chart_analyst.py`; the closest is the rules-only multi-view (PR #258, `ai/multiview.py`) | All of it. |
| AMW-12 | BYOK task routing, fallback, per-task caps, usage view | Missing | `backend/app/ai/byok_routes.py` has status / key / test / prefs / reports only; no `user_ai_policy` | All of it. |
| AMW-13 | Dual Review on two of the user's own keys (structured verdicts) | Missing | Only single-provider Vision (`ai/vision_audit.py`, PRs #160, #163, #217) | All of it (needs AMW-11, AMW-12). |
| AMW-14 | Report Center (per-signal and research reports, CSV of a screen run) | Missing | No `backend/app/reports/`, no `/api/reports`, no Reports page | All of it; PDF also waits for AMW-L4. |
| AMW-15 | Report schedules and lifecycle notifications | Missing | Only the `report_schedules` plan field (AMW-1); no schedules table, route or worker job | All of it (needs AMW-14, AMW-10). |
| AMW-16 | Data-health view and a market overview card | Partial | Market context panel (PR #260, `backend/app/context_panel/`: regime, breadth, VIX with status and freshness per tile); dashboard market strip (PR #300, `GET /api/market/strip`) | Admin data-health summary (quality events, freshness per source, broker sessions), member data-health view, and the "breadth and sectors of my last screen run" card. |
| AMW-17 | FYERS live 1-minute feed per user | Missing | `backend/app/brokers/fyers.py` is REST only; `broker_data_terms.json` still marks Fyers `pending` | All of it; blocked on written consent (AMW-L3). |
| AMW-18 | ADR-0027 and the Strategy Lab threat model | Ported | PR #264; `docs/adr/0027-strategy-lab-isolated-runner.md`, `docs/design/STRATEGY_LAB_THREAT_MODEL.md`, LAB-1..LAB-8; owner approval recorded 2026-10-22 (this PR) | - |
| AMW-19 | Lab core: drafts, immutable versions, runs, templates, Lab page | Missing | No `backend/app/lab/`, no `lab_*` tables, no `/api/lab/*`, no `/strategy-lab` page | All of it. |
| AMW-20 | Host modes: visual builder and Pine subset compiled to ScreenQL | Missing | No Pine translator anywhere in `backend/app` | All of it (owner priority 11c.1; needs no runner). |
| AMW-21 | Lab gateway, gVisor family runners, self-test, G-LAB switch gate | Missing | No `lab_runner/`, no gateway service, no sandbox admin routes | All of it; needs gVisor on the host and LAB-6 measurements. |
| AMW-22 | Python mode and the shared code-mode path | Missing | - | All of it; after G-LAB (family `python`) and LAB-6. |
| AMW-23 | SQL and Lua modes | Missing | - | All of it; deferred by owner order 11c.3. |
| AMW-24 | JavaScript and TypeScript modes | Missing | - | All of it; deferred by owner order 11c.3. |
| AMW-25 | R mode | Missing | - | All of it; deferred by owner order 11c.3 (also needs a larger host, AMW-Q6). |

## 2. Pages P-0..P-15

| Item | What it is | Status | Evidence | What is missing |
|---|---|---|---|---|
| P-0 | Home market overview (regime, breadth, sectors, freshness, provider health, alerts) | Partial | `pages/DashboardPage.tsx`, context panel (PR #260), Dashboard v2 "Today" + market strip (PR #300) | Breadth / sectors of the user's last screen run; one data-freshness / source-health tile (AMW-16). |
| P-1 | Advanced Screener (universe, strategies, filters, presets, CSV, MTF, data quality) | Partial | `pages/ScreenerPage.tsx`, `backend/app/screener/*`; watchlists as universe (PR #267); board views (PR #250) | CSV export of a run (AMW-14); the run is capped at 50 symbols (`screener/routes.py` `MAX_SYMBOLS`). |
| P-2 | Strategy Workbench (edit parameters, old vs new, versioned configs) | Partial | `backend/app/custom_strategies/versioning.py`, `pages/StrategiesPage.tsx` | Side-by-side old vs new preview. Plan decision: skip (a backtest per version answers it). |
| P-3 | Price Action Workbench (swings, BOS / CHoCH, zones, S/R, patterns) | Ported | `backend/app/price_action/*`, `charting/PriceActionLayer.tsx`, `charting/MarketZonesLayer.tsx` | Named chart patterns (AMW-4); candle confirmation is ported (PR #268). |
| P-4 | AI Chart Analyst (evidence report, setups, rejected setups, invalidation) | Partial | Rules-only multi-view (PR #258, `ai/multiview.py`), BYOK Vision (`ai/vision_audit.py`) | Per-symbol analyst report from engine evidence (AMW-11); Dual Review (AMW-13). |
| P-5 | Signal Intelligence (ranked setups, pass / fail per condition, lifecycle) | Partial | `pages/SignalsPage.tsx`, `/signal/enrich`, `signal_scoring/engine.py`, v2 inbox (PR #306) | Immutable records, condition-by-condition pass / fail, lifecycle, coverage (AMW-8, AMW-9). |
| P-6 | Backtesting and analytics (IS / OOS, walk-forward, costs) | Ported | `backend/app/backtest/*`, `pages/BacktestPage.tsx`, S6b (PR #243) | - |
| P-7 | Watchlists and alerts (rules, cooldowns, Telegram, email, quiet hours, schedules) | Partial | `backend/app/alerts/*`, `notification_links`, watchlists (PR #267) | Scheduled report deliveries (AMW-15). |
| P-8 | Paper trading and journal (sizing, adherence, forward tests) | Partial | `backend/app/paper/*`, `backend/app/journal/*`, `pages/JournalPage.tsx` | Forward test of every qualified signal (AMW-10). |
| P-9 | Settings and system health | Ported | `pages/{SettingsPage,RiskManagementPage,StatusPage,SystemLogsPage}.tsx`, `observability/status_page.py` | - |
| P-10 | Strategy Lab (eight authoring modes) | Partial | Only the Copilot "Strategy Lab" tab (`copilot/tabs/StrategyLabTab.tsx`, DSL drafts + research panel, PR #142) and the Strategy Builder | The Lab itself: AMW-19..AMW-25. |
| P-11 | AI provider setup and usage (keys, routing, fallback, safeguards, usage) | Partial | `components/VisionByokCard.tsx`, `ai/{byok,byok_routes,metering,pricing}.py` | Task routing, fallback, per-task caps, usage dashboard (AMW-12). |
| P-12 | Report Center (PDF / XLSX / CSV / JSON / PNG) | Missing | Only compliance exports (`exports/service.py`) and evidence PNGs | All of it (AMW-14). |
| P-13 | Live data FYERS (own app, daily login, 1-minute feed, stream) | Partial | `brokers/fyers.py` (REST, auth code via Settings), HB2 owner capture, `/api/live/stream` (SSE) | Per-user 1-minute collector, lease, bar stream, redirect login (AMW-17, consent first). |
| P-14 | Scanner Builder (templates, plain-words builder, versions, replay, compare) | Partial | ScreenQL builder (PRs #122, #261), gallery, as-of replay (S6a), S9 plain-words drafting (PR #272) | No-key plain-words parser (AMW-6), immutable versions and compare (AMW-7), AMW templates (AMW-5). |
| P-15 | Data health and admin | Partial | `pages/AdminPage.tsx`, `admin/routes.py`, `market_lake/quality.py` | One data-health view over quality events, freshness and broker sessions (AMW-16). |

## 3. Strategy Lab L-1..L-14

| Item | What it is | Status | Evidence | What is missing |
|---|---|---|---|---|
| L-1 | Drafts, immutable published versions, run records | Partial | `custom_strategies` + `strategy_versions` (DSL only) | `lab_strategies` / `lab_versions` / `lab_runs` (AMW-19). |
| L-2 | Language-independent IR evaluated by the host | Partial | ScreenQL AST + runtime (`screener/{nodes,validator,runtime}.py`, PRs #122, #123) | The `lab-envelope/1` strategy envelope: sides, stop, target (AMW-20). |
| L-3 | Visual no-code builder with nested AND / OR | Partial | Screener builder with ALL / ANY / NOT groups (U5 D2), Strategy Builder (AND-only DSL) | A Lab visual mode with the strategy envelope (AMW-20). |
| L-4 | Pine Script v5 / v6 subset translated, never executed, line-numbered diagnostics | Missing | No translator in `backend/app` | All of it (AMW-20; owner priority 11c.1). |
| L-5 | Python mode | Missing | - | AMW-22, after G-LAB. |
| L-6 | SQL mode | Missing | - | AMW-23 (deferred). |
| L-7 | Lua mode | Missing | - | AMW-23 (deferred). |
| L-8 | JavaScript and TypeScript modes | Missing | - | AMW-24 (deferred). |
| L-9 | R mode | Missing | - | AMW-25 (deferred). |
| L-10 | OS sandbox for code | Missing | ADR-0027 design only | gVisor runners and gateway (AMW-21). |
| L-11 | Capability matrix per machine | Missing | - | `GET /api/lab/capabilities` (AMW-21). |
| L-12 | Scan a universe and backtest with a published version | Missing | - | AMW-20 (host modes), AMW-22 (code modes). |
| L-13 | Templates per mode | Missing | Strategy templates exist only in the Copilot interview | Pine / visual / Python templates with each mode slice. |
| L-14 | Export a visual IR to Python | Missing | - | Plan decision: skip (no added capability once visual compiles to ScreenQL). |

## 4. AI AI-1..AI-9

| Item | What it is | Status | Evidence | What is missing |
|---|---|---|---|---|
| AI-1 | Deterministic evidence report, no LLM | Partial | Rules-only multi-view (PR #258, `ai/multiview.py`) | Setups, rejected setups, failed conditions and invalidation levels (AMW-11). |
| AI-2 | LLM text rejected when it cites numbers not in the evidence | Ported | `ai/grounding.py` (`check_numbers`, ticker check), `ai/output_filter.py` | - (free LLM prose stays retired by rule HC4-5). |
| AI-3 | Dual Review on two providers | Missing | - | AMW-13. |
| AI-4 | AI drafting, validated, offered as a draft only | Partial | `ai/generator.py` (DSL drafts), S9 ScreenQL drafts (PR #272) | Drafts for the Lab modes. Owner order 11e (2026-10-22) overrides C-3: the AI may write code for every Lab mode, as untrusted code under ADR-0027. |
| AI-5 | BYOK keys encrypted, tested, masked, deletable | Ported | `ai/byok.py`, `ai/byok_routes.py` (PR #160) | - (session-only mode not wanted, C-10). |
| AI-6 | Provider selection, task routing, fallback | Missing | `user_vision_prefs.provider` (one provider, Vision only) | AMW-12. |
| AI-7 | Spending safeguards per task, confirmations | Partial | Daily / monthly caps (`byok_allowed`), `ai/rate_limit.py`, `ai/pricing.py` | Per-task caps and the confirmation step (AMW-12). |
| AI-8 | Usage dashboard with tokens and cost | Partial | `llm_calls.billed_to_user_key`, `byok_spend` recorded | The per-user usage view (AMW-12). |
| AI-9 | Key redaction in logs and errors | Ported | `ai/pii.py`, sanitised `last_test_error` | - |

## 5. Rule conflicts C-1..C-21 (is the resolution in place?)

| Item | What it is | Status | Evidence | What is missing |
|---|---|---|---|---|
| C-1 | Analyst text from rule sentences, no free prose | Partial | HC4-5 and PR #258 keep analysis rules-only | The analyst itself (AMW-11). |
| C-2 | Dual Review as structured verdicts | Missing | - | AMW-13. |
| C-3 | AI drafts DSL / ScreenQL / Pine subset only, never code | Partial | DSL drafts, ScreenQL drafts (PR #272) | **Superseded by owner order 11e (2026-10-22):** the AI may write code for every Lab mode; it is validated and run exactly like user code. To be written into ADR-0027 with AMW-19 / AMW-20. |
| C-4 | No AMW auth or storage; tenant + user on new tables; routes classified | Ported | AMW-1 / AMW-2 / AMW-3 follow it (`watchlists` rows carry tenant and user; `S_OWN_WATCHLISTS` in `tests/tenancy_route_table.py`) | - |
| C-5 | No Yahoo, no pickle, no fallback universe | Ported | The ported slices read the lake or the user's own session; no Yahoo source and no pickle cache in their code | - |
| C-6 | Code only in gVisor runners; ADR-0027 amends ADR-0005 | Partial | ADR-0027 (PR #264), ADR-0005 amendment note | The runners and gateway (AMW-21). |
| C-7 | Code-mode results never trade or alert | Missing | No code modes yet | The enforcing test (G-16) with AMW-22. |
| C-8 | Lab runs queued, gateway handles runner frames | Missing | - | AMW-19, AMW-21. |
| C-9 | One default-off flag per Lab family | Missing | No `strategy_lab*` flag in `controls.py` | Added with AMW-19..AMW-25. |
| C-10 | No session-only BYOK mode | Ported | Keys only stored encrypted (`ai/byok.py`, ADR-0008) | - |
| C-11 | FYERS credential only through Settings | Ported | `brokers/fyers.py` reads the Settings credential; no other key entry | - |
| C-12 | FYERS data only after written consent | Missing | Terms still `pending` in `market_data/broker_data_terms.json`; no `fyers_minute_feed` flag or `set_flag` refusal yet | With AMW-17. |
| C-13 | Bar stream as SSE, token in a header | Missing | The live channel `/api/live/stream` is SSE; no bar stream exists | With AMW-17. |
| C-14 | Telegram through ATP's webhook and link codes, no polling | Ported | `notification_links`, `alerts/links_routes.py`, `telegram_inbound/` | - |
| C-15 | Reports as tenant / user scoped rows or objects | Missing | - | With AMW-14. |
| C-16 | No Streamlit / Plotly / matplotlib / reportlab in the backend | Ported | `backend/requirements*.txt` has none of them | - |
| C-17 | AMW labels re-worded to ATP's grade vocabulary | Ported | No AMW label ("A1 Candidate") in `backend/app`; `ai/wording.py` check | - |
| C-18 | Lab contexts from closed bars only; Z10-a cases per new function | Partial | AMW-3 functions carry Z10-a cases (`quality/causal_cases.py`) | Lab context builder (AMW-19). |
| C-19 | SDK `.d.ts` owned by the runner, frontend copy hash-checked | Missing | - | With AMW-24 (deferred). |
| C-20 | Forward tests show gross and net R | Missing | - | With AMW-10. |
| C-21 | AMW code re-implemented, not copied | Ported | AMW reference copy removed from the tree (PR #297, PR-3); AMW-1..3 written in ATP style; AMW-L1 parked | - |

## 6. Still missing, in order of user value

Most valuable first for a retail or beginner Indian trader. One line each.

1. **AMW-6 no-key plain-words screen builder**: a beginner types "stocks above the 50-day average with rising volume" and gets a screen on any plan, without an AI key.
2. **Screener run size (AMW-1 `max_screen_symbols`)**: a run is capped at 50 symbols, too few for the F&O list or NIFTY 200 / 500 scans most users want.
3. **AMW-5 ready-made AMW templates**: one-tap scans (RSI divergence, pullback in trend, VWAP reclaim, reversal confluence) for users with no idea of their own; feeds the Idea Library (order 11k).
4. **AMW-19 + AMW-20 Strategy Lab with the visual builder and the Pine Script subset**: most Indian expert traders write TradingView Pine; paste it, see what is supported, and backtest / paper it with no runner needed (owner priority 11c.1).
5. **AMW-11 rules-only Chart Analyst**: "what setups are here, which were rejected and why, where the idea fails", in plain words from the engine's own numbers.
6. **AMW-8 + AMW-9 signal records with pass / fail per condition**: a user sees exactly which conditions passed, how much evidence was missing, and what happened to each signal afterwards.
7. **AMW-10 paper forward tests**: every qualified signal is tracked on later bars, so a user sees how a method does before risking money.
8. **AMW-4 named chart patterns**: double tops, head and shoulders and triangles are what retail traders already know and look for.
9. **AMW-7 screen versions and compare**: change a screen safely and compare old and new on the same data, with no winner declared.
10. **AMW-14 Report Center and CSV export**: save or share a signal, a backtest or a screen run as CSV / XLSX / PNG (PDF after AMW-L4).
11. **AMW-15 scheduled reports and lifecycle notifications**: the morning screen results on Telegram, and a message when a signal is invalidated or expires.
12. **AMW-16 rest: data-health view and "my last scan" overview**: tells a user (and the admin) when data is stale before he trusts a scan.
13. **AMW-21 + AMW-22 Python mode**: for expert users, after the gVisor runner and the G-LAB evidence (LAB-6: the separate small server is recommended).
14. **AMW-12 BYOK routing, safeguards and usage view**: control over which own key is used and what it costs; matters mostly to the few users with keys.
15. **AMW-13 Dual Review**: a second opinion from a second own key; niche and costs the user twice.
16. **AMW-17 FYERS live 1-minute feed**: blocked until Fyers gives written consent (AMW-L3).
17. **AMW-23 / AMW-24 / AMW-25 SQL, Lua, JavaScript, TypeScript, R modes**: deferred by owner order 11c.3; built only if users ask.
