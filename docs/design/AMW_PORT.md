# AMW port - feature gap map and port plan

Source compared: **AMW AI Stock Screener v2.9.0** (Streamlit UI `Home.py` + `pages/1..15`, FastAPI `src/api/main.py`,
engines under `src/`, docs under `docs/`). Target: **ATP `main` at 045da0c** (backend `backend/app`, frontend
`frontend/src`, design docs `docs/design/*.md`).

How this map was made:
- Every ATP equivalent below was checked by reading the code on `main`, not by matching names. A row says what the
  ATP code does where it differs.
- AMW is a reference source only. None of its code was run. Its tests are used as behaviour specifications: each port
  slice re-writes the cases in ATP style on ATP fixtures (`tests/sample_market.py`, `tests/master_fixture.py`).
- The AMW archive carries no licence file. Code is re-implemented from the behaviour described here and in the AMW
  docs, not copied (open question AMW-Q1).

Standing rules that shape every decision below:
- PAPER only, LIVE off; the AI never places orders (ADR-0006); exits are never blocked (ADR-0004).
- Every new feature sits behind a flag in `DEFAULT_OFF_FLAGS` (`backend/app/platform/controls.py`); the UI hides it
  through `GET /api/system/features`.
- Every new `/api` route is classified in `backend/tests/tenancy_route_table.py`.
- Every migration declares `ROLLBACK_SAFE`, and `main` keeps a single alembic head.
- English only (the strategy interview is the one exception).
- AI text shown to a user is rules-generated or grounded (H-C1 output filter + grounding; HC4-5 retired free AI text
  in the multi-view analysis).
- BYOK keys are per user, saved through Settings, encrypted (envelope encryption, ADR-0008). Broker keys enter only
  through Settings.
- A new ScreenQL function needs Z10-a no-look-ahead cases (`backend/app/quality/causal_cases.py`).
- The frontend never imports from outside `frontend/`.

---

## 1. Feature matrix

Status: **Have** (ATP does it, equal or better), **Partial** (ATP covers part of it, or does it differently with a
gap), **Missing** (nothing equivalent on `main`).
Decision: **Skip** (ATP's is equal or better, or the AMW behaviour conflicts with an ATP rule and is not wanted),
**Extend ATP** (add to an existing ATP module), **New slice** (new module or page). Slice ids refer to section 4.

### 1.1 Pages (Home and pages 1-15)

| # | AMW feature | AMW source | ATP equivalent | Status | What is different | Decision |
|---|---|---|---|---|---|---|
| P-0 | Home: Market Overview (benchmark regime with evidence, breadth and sectors of the last scan, data freshness, provider health, recent alerts) | `Home.py`, `src/screener/scanner.py::market_overview`, `src/analysis/regime.py` | `frontend/src/pages/DashboardPage.tsx` (MarketPulseCard, OiBannersCard), `backend/app/ai/regime.py`, `backend/app/decision_tools/market.py`, `frontend/src/screener/FreshnessPill.tsx`, Copilot Market Pulse tab | Partial | ATP's dashboard is about the user's deployments, positions and P&L, plus market pulse and OI banners. There is no card for "breadth and sectors of my last screen run" and no single data-freshness / source-health tile. | Extend ATP (AMW-16) |
| P-1 | Advanced Screener: universe (Nifty lists, custom, watchlists), six strategies, background scan with cancel/resume, filters, presets, CSV, MTF matrix, data quality | `pages/1_Advanced_Screener.py`, `src/screener/scanner.py`, `src/services/jobs.py`, `src/screener/export.py` | `frontend/src/pages/ScreenerPage.tsx`, `backend/app/screener/{parser,validator,registry,runtime,routes}.py`, `backend/app/universe/*`, saved screens, gallery, evidence packs | Partial | ATP's screener (ScreenQL) is far richer: funnel, ANY/NOT groups, 80+ registry functions, as-of universe, evidence charts. Gaps: no named watchlists as a universe, no CSV export of a run, a run is synchronous and capped at 50 symbols (`MAX_SYMBOLS`). | Extend ATP (AMW-2 watchlists; CSV in AMW-14). Background large-universe run: Skip (ATP's vectorised screens and S6a lake runs cover it). |
| P-2 | Strategy Workbench: edit built-in strategy parameters, preview old vs new rules, versioned configs | `pages/2_Strategy_Workbench.py`, `src/strategies/base.py`, `config/settings.yaml` | `frontend/src/pages/StrategiesPage.tsx`, deployment parameters, `backend/app/custom_strategies/versioning.py` (immutable versions + rollback), `backend/app/backtest/*` | Partial | ATP's built-ins are code with per-deployment parameters; user-authored strategies have immutable versions and rollback. There is no "old vs new" side-by-side preview, but a backtest of each version answers the same question. | Skip |
| P-3 | Price Action Workbench: swings, BOS/CHoCH, zones, S/R, patterns, signal levels, MTF matrix, evidence, CSV | `pages/3_Price_Action_Workbench.py`, `src/analysis/*` | `backend/app/price_action/*`, `backend/app/support_resistance/*`, `backend/app/charts/{price_action_layer,zones_layer,studies}.py`, `frontend/src/charting/{PriceActionLayer,MarketZonesLayer,ReplayBar}.tsx`, chart workspace (CH5) | Have | ATP is stronger: causal swings per degree, legs, liquidity, trendlines, zone lifecycle and memory, replay as of any bar. Gaps are named chart patterns (row PA-5) and candle confirmation status (row PA-6). | Skip (gaps in AMW-3, AMW-4) |
| P-4 | AI Chart Analyst: evidence-grounded report, optional LLM text with number check, Dual Review, AI strategy drafting | `pages/4_AI_Chart_Analyst.py`, `src/services/analyst.py`, `src/ai/agent.py` | `backend/app/ai/multiview.py` (rules-only technical/options/news/macro views), `backend/app/ai/vision_audit.py` (BYOK Vision verdicts), `backend/app/ai/grounding.py`, `backend/app/ai/output_filter.py`, `backend/app/ai/generator.py` | Partial | ATP shows only rules-generated text (HC4-5) or structured Vision verdicts with the model's digits dropped. There is no on-demand per-symbol "chart analyst" report that lists setups, rejected setups, conflicts and invalidation from engine evidence. | New slice (AMW-11, AMW-13) |
| P-5 | Signal Intelligence: ranked setups, passed/failed conditions, score breakdown, lifecycle history | `pages/5_Signal_Intelligence.py`, `src/services/lifecycle.py` | `frontend/src/pages/SignalsPage.tsx`, `POST /api/strategies/{id}/signal/enrich` (`backend/app/main.py`), `backend/app/signal_scoring/engine.py`, `signal_history` table | Partial | ATP enriches one signal with a 7-component score and grade and keeps a history row. There is no canonical immutable record, no condition-by-condition pass/fail list, no lifecycle (qualified -> active -> completed / invalidated / expired), no ranked list across a run. | New slice (AMW-8, AMW-9) |
| P-6 | Backtesting & Analytics: equity/drawdown, IS/OOS, walk-forward, sensitivity, costs | `pages/6_Backtesting.py`, `src/backtest/engine.py` | `frontend/src/pages/BacktestPage.tsx`, `backend/app/backtest/{engine,robustness,validation,optimizer,windows,analytics,data_policy,repro}.py`, C2 execution models | Have | ATP is stronger: Monte Carlo, walk-forward, deflated Sharpe, PBO, sealed holdout, execution models, reproducibility fingerprint, rolling screen backtest (S6b). | Skip |
| P-7 | Watchlist & Alerts: watchlists, alert rules, cooldowns, delivery history, Telegram (official or own bot), email, quiet hours, schedules | `pages/7_Watchlist_Alerts.py`, `src/services/alerts.py`, `src/services/scheduler.py` | Alert rules and events (`backend/app/alerts/*`), outbox and dead letters, `notification_links`, `frontend/src/pages/NotificationsPage.tsx`, `AlertChannelsCard` | Partial | Alerts, delivery, quiet hours, caps and Telegram linking are Have (ATP stronger). Missing: persistent named watchlists, and scheduled report deliveries. | New slice (AMW-2, AMW-15) |
| P-8 | Paper Trading & Journal: simulated trades, risk sizing from configured equity, rule adherence, automatic forward tests of qualified signals | `pages/8_Paper_Trading_Journal.py`, `src/services/paper.py`, `src/services/forward.py`, `src/risk.py` | `backend/app/paper/{service,ticket,manual}.py`, `backend/app/journal/*`, `frontend/src/pages/JournalPage.tsx`, risk-by-default ticket | Partial | PAPER account, sizing and discipline journal are Have (ATP stronger). Forward tests of every qualified signal (R multiples per strategy version) are missing. | New slice (AMW-10) |
| P-9 | Settings & System Health: risk/costs/labels, data/session/holidays, DB/provider/cache/jobs health, audit log | `pages/9_Settings_System_Health.py` | `frontend/src/pages/{SettingsPage,RiskManagementPage,StatusPage,SystemLogsPage}.tsx`, `backend/app/market_data/routes.py` (holidays), `backend/app/workers/routes.py` (worker status), `backend/app/observability/status_page.py`, hash-chained audit log | Have | ATP splits these across Settings, Risk Management, Status and System Logs; all are present. | Skip |
| P-10 | Strategy Lab: eight authoring modes, validate, run, publish immutable versions, scan, backtest | `pages/10_Strategy_Lab.py`, `src/lab/*` | Copilot "Strategy Lab" tab (`frontend/src/copilot/tabs/StrategyLabTab.tsx`: AI drafts in the DSL + research panel), Strategy Builder (`StrategyBuilderPage.tsx`, `backend/app/strategy_engine/declarative.py`), ScreenQL | Partial | ATP has one declarative DSL (ADR-0005: "no arbitrary code, so no sandbox is needed") and ScreenQL. No Pine subset, no code modes, no sandbox. The name "Strategy Lab" is already used by a Copilot tab (AMW-Q2). | New slice group (AMW-18..AMW-25) |
| P-11 | AI Provider Setup & Usage: keys (test, replace, delete), provider selection and fallback, spending safeguards, usage dashboard | `pages/11_AI_Provider_Setup.py`, `src/ai/{gateway,providers,crypto,redact}.py` | `frontend/src/components/VisionByokCard.tsx`, `backend/app/ai/{byok,byok_routes,metering,pricing}.py`, `user_ai_keys`, `user_vision_prefs`, `llm_calls.billed_to_user_key` | Partial | ATP stores per-user keys (Anthropic, OpenAI, Gemini), tests them, applies daily and monthly caps - but only for Vision, with one provider. Missing: task routing, fallback policy, per-task safeguards, a usage dashboard with token counts. | Extend ATP (AMW-12) |
| P-12 | Report Center: PDF/XLSX/CSV/JSON/PNG per signal, research reports, plan limits | `pages/12_Report_Center.py`, `src/reports/*` | `backend/app/exports/service.py` (compliance CSV/JSON with SHA-256), `backend/app/screener/evidence_png.py`, `backend/app/ai/chart_png.py`, tax report | Missing | ATP has compliance exports and evidence images, but no user report centre, no per-signal report, no PDF or XLSX. | New slice (AMW-14) |
| P-13 | Live Data (FYERS): connect own app, daily login, background 1-minute feed, live status, stored bars, chart, FYERS as scan source | `pages/13_Live_Data_FYERS.py`, `src/feed/*` | `backend/app/brokers/fyers.py` (REST adapter, daily auth code via Settings), HB2 `backend/app/market_lake/intraday_history.py` (owner-only after-close capture), BT1 `byob_data_tier`, `backend/app/market_data/stream.py` (Upstox ticks), `backend/app/live/routes.py` (SSE) | Partial | ATP can read Fyers history and quotes through the user's Settings credential but has no per-user live 1-minute collector, lease or bar stream. `broker_data_terms.json` marks Fyers **pending** ("written consent needed before charts/screeners run on Fyers data"). | New slice (AMW-17), gated on consent |
| P-14 | Scanner Builder: templates by category, natural-language builder (no key), JSON editing, save/version/duplicate, run or replay as of a past moment, compare two versions | `pages/14_Scanner_Builder.py`, `src/scanner/*` | ScreenQL builder and text editor (U5 D1/D2), gallery and taxonomy (TX1), Legends, starters, S6a `POST /api/screener/history/asof`, S6b backtest, TX4 forking | Partial | Building, gallery and as-of replay are Have (ATP's replay is point-in-time on the lake, stronger). Missing: deterministic NL to ScreenQL, immutable screen versions (a screen is updated in place), version comparison. | Extend ATP (AMW-5, AMW-6, AMW-7) |
| P-15 | Data Health & Admin: active source and fallback, provider and feed status, data quality of last scan, users and plans, audit log | `pages/15_Data_Health_Admin.py` | `frontend/src/pages/AdminPage.tsx`, `backend/app/admin/routes.py` (tenants, plans, audit logs, readiness, screener audit, retention), `backend/app/market_lake/quality.py` (`data_quality_events`), status page | Partial | Users, plans and audit are Have. No data-health view that summarises quality events, source freshness and broker-session health. | Extend ATP (AMW-16) |

### 1.2 Screener and scanner

| # | AMW feature | AMW source | ATP equivalent | Status | What is different | Decision |
|---|---|---|---|---|---|---|
| S-1 | 19 scanner templates (Price Action, Trend, Volume, Reversal, Research), each with timeframes, data needs, limitations, "NOT VALIDATED" | `src/scanner/templates.py` | `backend/app/screener/{taxonomy,library,legends}.py`, `backend/app/screener/data/taxonomy.json`, `frontend/src/screener/{ScreenGallery.tsx,starters.json}`, breakout templates, TX2 watch lists | Partial | The gallery mechanism is Have. About half of AMW's templates have no ATP counterpart (RSI divergence, swing-label sequences, pullback to EMA in trend, reversal confluence, VWAP reclaim). | Extend ATP (AMW-5) |
| S-2 | Deterministic natural-language builder: clause -> registry, interpretations and assumptions listed, UNSUPPORTED clauses reported, finest timeframe is primary | `src/scanner/nl.py` | `backend/app/scanner/ai.py::plan_scan` (LLM or rule fallback, emits the legacy scanner DSL), `backend/app/strategy_engine/nlu_parser.py`; S9 Copilot screen builder (branch, in review) | Partial | ATP's no-key path targets the legacy scanner, not ScreenQL, and does not list assumptions per clause. | Extend ATP (AMW-6) |
| S-3 | Immutable scanner versions (SHA-256 of rules + spec), drafts never execute, duplicate | `src/scanner/spec.py`, `lab_versions` | `screens` table (`ScreenRecord`, updated in place by `PUT /api/screener/screens/{id}`), `screen_runs.ast_sha256` | Partial | A run records the AST hash, so results are reproducible, but there is no version history to pin, compare or roll back. | New slice (AMW-7) |
| S-4 | Point-in-time replay as of a past moment (history cut, forming bar dropped, quality re-assessed) | `src/scanner/replay.py` | `backend/app/screener/history.py`, `POST /api/screener/history/asof` (S6a: as-of index membership, prices as known at d, causal fast path), evidence replay (EV3, PR) | Have | ATP is stronger (survivorship-free universe, corporate actions as known). AMW replays intraday on provider history; ATP's S6a is end-of-day. | Skip |
| S-5 | Version comparison on the same symbols, window, data and costs; no winner declared | `src/scanner/replay.py::compare` | S6a fitness, S6b rolling screen backtest (`POST /api/screener/history/backtest`) per screen | Missing | Each screen can be measured, but there is no "version A vs version B on identical inputs" call or view. | New slice (AMW-7) |
| S-6 | Scanner envelope: universe, HTF, frequency, session, incomplete-candle policy, data-quality requirement, signal expiry, alert prefs | `src/scanner/spec.py::Envelope` | `ScreenRecord` (base timeframe, params), `alert_rules` (universe, timeframe, cooldown, fire_on, expiry), closed bars only by design | Have | ATP spreads the envelope over screens and alert rules; the completed-bar rule is built in. | Skip |
| S-7 | Scheduled scanner runs, one run per slot across processes | `src/services/scheduler.py` (kind `scanner`) | Bar-close alert engine `backend/app/alerts/barclose.py` (idempotent per rule, symbol and bar), single worker (ADR-0010) | Have | ATP evaluates rules on every closed bar instead of at fixed times. | Skip |
| S-8 | Share a scanner copy with another user | `POST /api/v1/scanners/{id}/share` | Screens are organisation-scoped; TX4 forking (branch) | Partial | Within an organisation sharing is implicit; across organisations it is not wanted (tenancy). | Skip |
| S-9 | Background scan jobs: progress, cancel, resume, durable job records, orphan recovery | `src/services/jobs.py`, `job_runs` | `backend/app/ai/research_jobs.py` pattern (queue, heartbeat, interrupted), screener runs synchronous | Partial | The job pattern exists for research studies; screener runs do not need it at ATP's caps. Lab runs reuse it (AMW-19). | Skip |

### 1.3 Strategy engines and scoring

| # | AMW feature | AMW source | ATP equivalent | Status | What is different | Decision |
|---|---|---|---|---|---|---|
| E-1 | Breakout engine (swing, range, pattern, trendline levels; hold, RVOL, retest, trap) | `src/strategies/breakout.py` | `backend/app/screener/breakouts/*` (BK1-BK4: range, swing, NR7, 52-week, ATR, volume, VCP, Donchian, squeeze, trendline, pattern, gap, retest, quality grade), `SwingBreakout`, `OpeningRangeBreakout` | Have | ATP is much broader and has quality grading. | Skip |
| E-2 | Pullback engine (trend + touch of EMA/BOS level/S-R/zone/Fib; not extended; rejection; R:R) | `src/strategies/pullback.py` | `MTFTrendPullbackScalper` (`mtf_strategy.py`), `SwingEmaPullback` (`swing_strategies.py`), level gate (`price_action/level_gate/*`), Scan Then | Have | Fib 38.2-61.8 % touch is not an ATP option; the level gate covers zones and swing levels. | Skip |
| E-3 | Reversal engine (prior trend or RSI extreme; at least 2 independent triggers; "early candidate" without CHoCH) | `src/strategies/reversal.py` | `backend/app/price_action/reversal.py` (`ReversalAt`, composite rejection candle), `BollingerRsiReversion`, `StructureEvent` | Partial | ATP has a strong single-rule reversal at a level. Missing: RSI divergence as a trigger and the "two independent triggers, early until CHoCH" template. | Extend ATP (AMW-3, AMW-5) |
| E-4 | Trend continuation (close > EMA20 > EMA50 > EMA200, structure bias, ADX with DI agreement) | `src/strategies/trend.py` | `MacdEmaTrend` (`combo_strategies.py`), daily trend fold (L2, `price_action/daily_trend/*`), `Trend`, `DowTrend`, `ADX`, `PlusDI`, `MinusDI` in ScreenQL | Have | Expressible as one ScreenQL screen; added as a template. | Skip (template in AMW-5) |
| E-5 | Intraday (opening-range breakout, VWAP pullback, VWAP reclaim; session window; expiry at session end) | `src/strategies/intraday.py` | `OpeningRangeBreakout`, `VwapSupertrend`, BK2 intraday breakouts, `SessionVWAP`, `RelVolumeTOD`, `ORHigh`/`ORLow` | Have | VWAP pullback / reclaim exist only as expressions; added as templates. A session-window predicate is missing (AMW-3 `TimeOfDay`). | Skip (template in AMW-5) |
| E-6 | Swing (daily setups + weekly alignment gate, stop <= 8 % of price, overnight-gap warning) | `src/strategies/swing.py` | `SwingEmaPullback`, `SwingBreakout`, `Trend@1w` in ScreenQL | Partial | No weekly-alignment gate or stop-width cap on the built-in swing strategies. Expressible as a screen with `@1w`. | Extend ATP (template in AMW-5) |
| E-7 | Confluence scoring: weighted components, mandatory gate (score never overrides a failed mandatory rule), coverage %, STALE penalty, labels | `src/strategies/base.py`, `docs/STRATEGY_RULES.md` | `backend/app/signal_scoring/engine.py` (trend 20, structure 15, S/R 20, price action 20, volume 10, option chain 10, R:R 5; grade), TX2 confluence view (PR) | Partial | ATP has no coverage figure (missing evidence is scored, not reported as missing) and no explicit mandatory gate on the grade. | Extend ATP (AMW-8) |
| E-8 | Signal lifecycle (candidate -> awaiting confirmation -> qualified -> active -> completed; invalidated, expired, rejected, insufficient data) with illegal-transition errors | `src/services/lifecycle.py` | None (`signal_history` rows have no state) | Missing | - | New slice (AMW-9) |

### 1.4 Price action

| # | AMW feature | AMW source | ATP equivalent | Status | What is different | Decision |
|---|---|---|---|---|---|---|
| PA-1 | Confirmed swings (known at i+k), HH/HL/LH/LL, BOS/CHoCH on close, sweeps | `src/analysis/structure.py` | `backend/app/price_action/{causal_swings,market_structure,breaks,legs}.py`, CH3b layer | Have | ATP has swing degrees and legs; equal or better. A swing-label ScreenQL function is missing (AMW-3). | Skip |
| PA-2 | Supply/demand zones (RBR/DBR/RBD/DBD), fresh -> tested -> invalidated | `src/analysis/structure.py` | `backend/app/price_action/zones/*` (Z7a: lifecycle fresh -> tested -> flipped -> invalidated, level memory, nightly store), `NearZone`/`ZoneRole`/`ZoneState`/`ZoneStrength` | Have | ATP is stronger. | Skip |
| PA-3 | S/R clusters, strength, role reversal, crosses, freshness | `src/analysis/structure.py::sr_levels` | `backend/app/support_resistance/*`, `backend/app/price_action/level_strength.py`, `ZoneRole` | Have | - | Skip |
| PA-4 | Liquidity pools (BSL/SSL, equal highs/lows) as hypotheses | `src/analysis/structure.py` | `backend/app/price_action/liquidity.py` (pools, sweeps, pool marks), chart layer | Have | - | Skip |
| PA-5 | Chart patterns: double/triple tops and bottoms, head and shoulders (and inverse), triangles, wedges, broadening, rectangle; status and target; cup-and-handle reported as not implemented | `src/analysis/patterns.py` | BK4 pattern breakouts (flag, triangle: `screener/breakouts/templates.py`), level gate `zone_pattern` evidence labels (`price_action/level_gate/method.py`) | Partial | ATP has no named-pattern detector with a confirmation status, chart layer or ScreenQL function. | New slice (AMW-4) |
| PA-6 | Candlestick patterns incl. v2 (Pin Bar, Strong Body, Rejection, Gap Reversal) with next-bar confirmation status | `src/analysis/candles.py` | `backend/app/price_action/candlestick_patterns.py` (11 detectors incl. pin bar and strong rejection; 14 vectorised masks with bullish / bearish variants), `Pattern`/`PatternBullish`/`PatternBearish` | Partial | Missing: Strong Body, Gap Reversal, and a confirmed / failed / unconfirmed status on the next bar. | Extend ATP (AMW-3) |
| PA-7 | Gaps (>= 0.5 ATR, filled flag) | `src/analysis/structure.py::gaps` | `backend/app/price_action/gap_context.py`, `GapType`, `GapBreakout` | Have | - | Skip |
| PA-8 | Supertrend, Fibonacci extensions | `src/analysis/indicators.py` | `Supertrend` (indicators + ScreenQL), Fibonacci drawing tool (`backend/app/charts/drawings.py`) | Have | No computed Fibonacci-extension series; the drawing tool covers the use. | Skip |
| PA-9 | Market regime (EMA votes, ADX, volatility state) | `src/analysis/regime.py` | `backend/app/ai/regime.py`, regime catalogue, TX3 regime greying | Have | - | Skip |
| PA-10 | Multi-timeframe matrix | `src/analysis/context.py`, page 3 | `Trend@tf` in ScreenQL, EV3 W/D/1H trend strip (PR #240) | Have | - | Skip |
| PA-11 | Optional subjective modules registry (Elliott, harmonics), empty | `src/analysis/optional.py` | None | Missing | Empty in AMW; nothing to port. | Skip |

### 1.5 Signals, AI, backtest, paper

| # | AMW feature | AMW source | ATP equivalent | Status | What is different | Decision |
|---|---|---|---|---|---|---|
| SG-1 | Canonical signal record `amw.signal.v1`: strategy + version, conditions with actual values and thresholds, score components, levels, expiry, provider, data snapshot SHA-256, evaluation time | `src/screener/scanner.py::stamp_canonical`, `signals` table | `signal_history` (score, grade, reasons), `screen_evidence` (evidence/1 per screen match: the engine's numbers, drawn-as declarations) | Partial | ATP's evidence pack is the closest match, for screen matches only. There is no record for strategy signals with conditions and a data fingerprint. | New slice (AMW-9) |
| SG-2 | Record immutability (database trigger) and append-only lifecycle events | `src/storage/db.py::_immutability_ddl`, `signal_events` | None (append-only by convention on `screen_runs`, `research_trials`) | Missing | - | New slice (AMW-9) |
| AI-1 | Deterministic evidence report (no LLM): MTF structure, alignment, setups, rejected setups, "no-trade is valid" | `src/services/analyst.py::deterministic_report` | `backend/app/ai/multiview.py` technical view (`lines`, `rules_summary`) | Partial | ATP's technical view is regime + candles; it does not list setups, failed conditions or invalidation levels. | New slice (AMW-11) |
| AI-2 | Optional LLM explanation, rejected if it cites numbers not in the evidence | `src/services/analyst.py::validate_llm_text` | `backend/app/ai/grounding.py` (numbers-check, ticker-check), `backend/app/ai/output_filter.py` | Partial | ATP's checks are stronger, and HC4-5 retired free AI text in analysis views. Free LLM prose conflicts with the rules-only rule (section 2). | Skip (free text); AI may only order rule sentence ids |
| AI-3 | Dual Review: same evidence to OpenAI and Anthropic on the user's two keys, confirmed every time | `src/ai/gateway.py`, `POST /api/v1/ai/dual-review` | `backend/app/ai/vision_audit.py` (one provider, structured verdicts) | Missing | - | New slice (AMW-13), structured verdicts only |
| AI-4 | AI strategy drafting: bounded agent (1 generation + repairs), code validated, offered as a draft only | `src/ai/agent.py` | `backend/app/ai/generator.py` (drafts in the DSL), research loop (H-C3), Copilot Strategy Lab tab | Partial | ATP drafts only the declarative DSL and backtests drafts in-sample. AI-written code for sandboxed languages is not wanted. | Skip (code drafting); later AI drafts may target ScreenQL / Pine subset |
| AI-5 | BYOK keys: AES-GCM bound to user + provider, model-list key test, masked display, delete, session-only option | `src/ai/{crypto,providers}.py` | `backend/app/ai/byok.py` (`user_ai_keys`, envelope encryption bound to tenant + user + provider, key test, hint, delete) | Have | ATP has no session-only mode (keys are stored via Settings, encrypted, by rule). | Skip |
| AI-6 | Provider selection: default, manual, task routing, fallback none / ask / auto (own other key only) | `src/ai/gateway.py` | `user_vision_prefs.provider` (one provider for Vision) | Missing | - | Extend ATP (AMW-12) |
| AI-7 | Spending safeguards: calls per task, retries, output tokens, cost per task, daily/monthly thresholds, confirmations | `src/ai/gateway.py` | `byok_allowed` (daily and monthly USD caps, hard stop), `backend/app/ai/rate_limit.py`, `backend/app/ai/pricing.py` | Partial | ATP caps spend per day and month; no per-task limits or confirmation step. | Extend ATP (AMW-12) |
| AI-8 | Usage dashboard: every attempt with provider-returned tokens, estimated cost, daily/monthly totals | `GET /api/v1/ai/usage`, page 11 | `llm_calls` with `billed_to_user_key`, `byok_spend` | Partial | Data is recorded; there is no per-user usage view. | Extend ATP (AMW-12) |
| AI-9 | Key redaction in logs and errors | `src/ai/redact.py` | `backend/app/ai/pii.py`, sanitised `last_test_error`, `bot_token` redaction | Have | - | Skip |
| B-1 | Trade simulator: next-bar open entry, costs, stop-first on same-bar touch, gap skips, one position | `src/backtest/engine.py` | `backend/app/backtest/engine.py` + C2 models (fills, slippage, latency, margin), `windows.py` (HTF closed bars) | Have | ATP is stronger. | Skip |
| B-2 | IS/OOS, walk-forward, sensitivity, buy-and-hold benchmark | `src/backtest/engine.py` | `backend/app/backtest/{robustness,validation,optimizer}.py`, holdout (`data_policy.py`) | Have | ATP adds deflated Sharpe, PBO, Reality Check. | Skip |
| B-3 | Backtest of a Strategy Lab version (host-built contexts for every bar, one sandbox batch) | `src/lab/service.py::lab_backtest` | None | Missing | - | New slice (AMW-20, AMW-22) |
| PT-1 | Paper journal: risk sizing from configured equity, costs, rule adherence | `src/services/paper.py`, `src/risk.py` | `backend/app/paper/*`, `backend/app/journal/*` (discipline score, behaviour tags), risk-by-default ticket | Have | ATP is stronger. | Skip |
| PT-2 | Forward test of every qualified signal: entry at next open, stop-first, fixed max bars per timeframe, R per strategy version, INSUFFICIENT EVIDENCE under 30 | `src/services/forward.py`, `forward_tests` | S6a screen fitness (forward returns of matches vs the universe, aggregate), `backend/app/decision_tools/own_stats.py` (own closed trades) | Partial | ATP measures screens in aggregate, not each signal with its stop and target. | New slice (AMW-10) |

### 1.6 Watchlists, alerts, notifications, reports

| # | AMW feature | AMW source | ATP equivalent | Status | What is different | Decision |
|---|---|---|---|---|---|---|
| W-1 | Named watchlists (CRUD), usable as a scan universe | `watchlists` table, `/api/v1/watchlists` | Free-text symbol box on `ScannerPage.tsx`; TX2 "watch lists" are built-in screens | Missing | - | New slice (AMW-2) |
| W-2 | Alert rules on signals (strategies, min score, directions, timeframes, symbols, cooldown, channels) | `src/services/alerts.py`, `alert_rules` | `backend/app/alerts/{engine,barclose,rules_routes}.py`: rules on screens and instruments, cooldown, priority, digest, intrabar option | Have | AMW rules filter signals; ATP rules are screens. Once AMW-9 lands, a screen can express the same filter. | Skip |
| W-3 | Delivery history, duplicate prevention per rule/signal/channel, quiet hours, hourly cap, bounded retries | `src/services/alerts.py` | Outbox `alert_deliveries`, idempotency keys, dead letters and replay, quiet hours, caps, stale-data guard | Have | ATP is stronger. | Skip |
| W-4 | Telegram via the official bot, linked with a one-time code | `src/notify/telegram.py`, `src/services/alerts.py` | `notification_links` (S3b-2), `backend/app/alerts/links_routes.py` | Have | AMW polls `getUpdates`; ATP uses its webhook and link codes. | Skip |
| W-5 | User-owned Telegram bot, token encrypted and masked | `src/services/alerts.py::set_own_bot` | Organisation alert channel with an encrypted bot token (`backend/app/alerts/channels.py`, `AlertChannelsCard`) | Have | Owned by the organisation; in the beta each sign-up is its own organisation. | Skip |
| W-6 | Email to each user's own address | `src/services/alerts.py::send_email` | `backend/app/notifications/mailer.py`, signed unsubscribe | Have | - | Skip |
| W-7 | System notifications per kind (signal qualified / invalidated / completed / expired, forward closed, daily summary, feed failure, backtest done) with per-kind channel choice | `src/services/notify_events.py` | `backend/app/notifications/service.py`, push categories (`backend/app/alerts/deeplink.py`) | Partial | The delivery path exists; the AMW kinds depend on records ATP does not have yet (AMW-9, AMW-10). | Extend ATP (AMW-15) |
| W-8 | Schedules: `scan`, `scan_report` (PDFs of top-N to Telegram), `scanner`; trading days; one run per slot | `src/services/scheduler.py` | Bar-close alert engine (scans); no scheduled reports | Partial | - | New slice (AMW-15) |
| R-1 | Per-signal reports: PDF (quick/standard/detailed), XLSX, CSV, JSON, PNG; chart cut at the signal's data bar; snapshot match stated; owner-only; logged | `src/reports/builder.py` | `backend/app/exports/service.py` (compliance only), `backend/app/screener/evidence_png.py` | Missing | - | New slice (AMW-14) |
| R-2 | Static annotated chart renderer (swings, BOS/CHoCH, zones, levels), annotations checked against candles | `src/reports/chart_png.py` | `backend/app/ai/chart_png.py` (deterministic PNG, stdlib only), `backend/app/screener/evidence_png.py` | Have | ATP's renderer has no extra dependency; reuse it. | Skip |
| R-3 | Research reports: backtest, forward-test and daily scanner reports (PDF + CSV) | `src/reports/research.py` | None | Missing | - | New slice (AMW-14) |
| R-4 | Report retention and storage quota per plan | `src/saas/entitlements.py`, `src/services/jobs.py` | `backend/app/retention` (platform retention jobs) | Partial | No report objects to retain yet. | Extend ATP (AMW-14) |

### 1.7 Live data, admin, plans

| # | AMW feature | AMW source | ATP equivalent | Status | What is different | Decision |
|---|---|---|---|---|---|---|
| F-1 | FYERS connection: App ID + secret encrypted, OAuth login link with one-time hashed state, callback | `src/feed/fyers.py`, `PUT /api/v1/broker/fyers`, `/login-url`, `/callback` | `backend/app/brokers/fyers.py` (auth-code exchange, `appIdHash`), Settings broker card, broker OAuth callback class in the tenancy table | Partial | ATP takes the auth code through Settings; a redirect-based daily login for Fyers is not wired. | Extend ATP (AMW-17) |
| F-2 | Background 1-minute collector per user: completed bars only, validation, revisions counted, no forward fill, catch-up, 30-day purge | `src/feed/{collector,bars}.py`, `minute_bars` | HB2 owner intraday capture into `md_tenant_candles` (owner only, after the close, versions, forming bar dropped) | Partial | HB2 serves one pinned owner after the close; AMW serves every user during the session. | New slice (AMW-17) |
| F-3 | One collector per account across processes: database lease, heartbeat, remote stop, auto-resume | `src/feed/collector.py` | Single trading worker with a Redis lock (ADR-0010) | Partial | ATP runs one worker; a per-user lease row is still needed for restarts and status. | New slice (AMW-17) |
| F-4 | WebSocket stream of the caller's own completed bars (token in the first message) | `WS /api/v1/stream/bars`, `src/feed/hub.py` | `GET /api/live/stream` (SSE, bearer header, PW4); internal Upstox tick stream | Partial | ATP's live channel is SSE with the token in a header. | New slice (AMW-17), SSE |
| F-5 | Rate budget per user and a daily 429 breach guard | `src/feed/fyers.py` | `backend/app/brokers/rate_budget.py`, `backend/app/market_data/api_budget.py`, circuit breaker | Have | - | Skip |
| F-6 | Data source for scans: default or own FYERS; signal stamps the provider actually used | `GET/PUT /api/v1/data-source` | `DataSourceBar` / `useCandleSource`, BT1 data tiers, `screen_runs.data_source` | Have | - | Skip |
| A-1 | Data-source health and failover status, provider and feed status | page 15, `src/data/failover.py` | Status page components, broker session health card, freshness | Partial | No single data-health view. | Extend ATP (AMW-16) |
| A-2 | Data quality of the last scan (typed statuses) | page 15, `src/data/validation.py` | `data_quality_events` (B3), `GET /api/admin/screener/audit` | Partial | Recorded, not summarised for users or operators. | Extend ATP (AMW-16) |
| A-3 | Users and plans administration | page 15, `src/api/manage.py` | `backend/app/admin/routes.py` (tenants, plans), team page | Have | - | Skip |
| A-4 | Audit log | `audit_log` | Hash-chained audit log (`backend/app/audit/*`), `GET /api/admin/audit-logs` | Have | ATP is stronger. | Skip |
| SA-1 | Plans and entitlements: symbols per scan, concurrent jobs, reports per day, Lab strategies, alert rules, schedules, minimum schedule interval, report retention and storage; 403 `plan_limit` | `src/saas/entitlements.py` | `backend/app/plans/{registry,limits}.py` (deployments, custom strategies, members, alert channels, backtests per month, AI budget) | Partial | Different limit set; the AMW limits for new features do not exist. | Extend ATP (AMW-1) |
| SA-2 | Billing webhook, HMAC-verified, idempotent | `POST /api/v1/billing/webhook` | `backend/app/billing/{razorpay,webhooks}.py` | Have | - | Skip |

### 1.8 Strategy Lab

| # | AMW feature | AMW source | ATP equivalent | Status | What is different | Decision |
|---|---|---|---|---|---|---|
| L-1 | Drafts, immutable published versions (SHA-256), run records with status and config snapshot | `src/lab/service.py`, `lab_strategies`, `lab_versions`, `lab_runs` | `custom_strategies` + `strategy_versions` (immutable, rollback), Copilot drafts | Partial | ATP versions only the declarative DSL. | New slice (AMW-19) |
| L-2 | Language-independent IR evaluated by the host (rule tree, predicates, stop/target) | `src/lab/ir.py` | ScreenQL AST `screenql/1` + runtime (`backend/app/screener/{nodes,validator,runtime}.py`) | Partial | ATP's AST is the better IR (typed, unit-checked, Z10-a causal). It has no strategy envelope (sides, stop, target). | New slice (AMW-20): compile to ScreenQL |
| L-3 | Visual no-code builder (nested AND/OR, depth <= 6, <= 300 nodes, predicates, stop ATR/swing, target R, readable logic, warm-up) | `src/lab/ir.py`, page 10 | Screener builder (U5 D2: ALL/ANY/NOT groups, live counts), Strategy Builder (AND-only DSL) | Partial | ATP's screener builder already edits nested groups; a strategy envelope is missing. | Extend ATP (AMW-20) |
| L-4 | Pine Script v5/v6 subset translated to IR, never executed; line-numbered diagnostics | `src/lab/pine.py` | None | Missing | - | New slice (AMW-20) |
| L-5 | Python mode (`evaluate(ctx)`, math/statistics only, helpers, restricted builtins, audit hook) | `src/lab/workers/py_worker.py` | None | Missing | - | New slice (AMW-22) |
| L-6 | SQL mode (one SELECT over approved views, sqlglot allow-list, SQLite authorizer, step and row limits) | `src/lab/sqlcheck.py`, `py_worker.py` | None | Missing | - | New slice (AMW-23) |
| L-7 | Lua mode (lupa, removed libraries, memory and instruction limits) | `py_worker.py` | None | Missing | - | New slice (AMW-23) |
| L-8 | JavaScript and TypeScript modes (Node `vm` null-prototype realm, code generation off, `--permission`; TS type-checked against an SDK `.d.ts`) | `src/lab/workers/{js_worker.js,ts_compile.js,amw-sdk.d.ts}` | None | Missing | - | New slice (AMW-24) |
| L-9 | R mode (allow-listed stats/utils, forbidden constructs) | `src/lab/workers/r_worker.R`, `src/lab/engine.py::static_check` | None | Missing | - | New slice (AMW-25) |
| L-10 | OS sandbox: network and mount namespaces, uid 65534, rlimits, empty env, timeout, self-test | `src/lab/sandbox.py` | None (ADR-0005: no sandbox needed) | Missing | AMW's design has gaps (section 3.4). | New slice (AMW-21), redesigned |
| L-11 | Capability matrix detected per machine (`GET /api/v1/lab/capabilities`) | `src/lab/contract.py::capabilities` | None | Missing | - | New slice (AMW-21) |
| L-12 | Scan a universe and backtest with a published version | `src/lab/service.py` | None | Missing | - | New slice (AMW-20, AMW-22) |
| L-13 | Templates per mode (Pine 6, visual 8, Python 6, SQL 4, JS/TS, Lua, R) | `src/lab/templates.py`, `pine.py`, `ir.py`, `sqlcheck.py` | Strategy templates in the Copilot interview | Missing | - | New slice (with each mode) |
| L-14 | Export a visual IR to Python (expression subset, equivalence tested) | `src/lab/ir.py::export_python` | None | Missing | Exporting to an executable language adds no capability once visual compiles to ScreenQL. | Skip |

### 1.9 AMW API areas (`src/api/main.py`, `/api/v1`)

| # | AMW API area | AMW routes | ATP equivalent | Status | What is different | Decision |
|---|---|---|---|---|---|---|
| API-1 | Auth: bearer tokens (hash stored), roles admin/analyst/viewer, `AMW_AUTH_MODE=local`, rate limit, request ids, CORS | middleware in `src/api/main.py`, `src/api/manage.py` | `backend/app/auth/*` (JWT, sessions, MFA), scopes (ADR-0009), `backend/app/core/rate_limit.py`, tenancy route table | Have | AMW's local mode has no login. Not ported (section 2). | Skip |
| API-2 | Health and meta: `/health/live`, `/health/ready`, `/meta` | same | `/api/system/health`, status page, readiness | Have | - | Skip |
| API-3 | Market data: `/universes/{name}`, `/ohlcv/{ticker}`, `/features/{ticker}`, `/instruments`, `/instruments/resolve` | same | `backend/app/universe/*`, `GET /api/market-data/history` (B5), candles routes, `backend/app/instruments/*` | Have | ATP reads the lake and the user's broker session, never Yahoo. | Skip |
| API-4 | Scans and jobs: `/scans`, `/jobs`, `/jobs/{id}`, `/cancel`, `/jobs/{id}/signals`, `/evaluate/{ticker}` | same | `POST /api/screener/run`, `GET /api/screener/runs/{id}`, `POST /api/strategies/{id}/signal` | Partial | No job list or cancel for screener runs (not needed at ATP caps). | Skip |
| API-5 | Signals: `/signals`, `/signals/{key}` | same | `signal_history` reads on the Signals page | Partial | - | New slice (AMW-9) |
| API-6 | Analyst and AI: `/analyst`, `/ai/providers`, `/ai/keys/{provider}` (PUT/DELETE/model), `/ai/policy`, `/ai/usage`, `/ai/draft-strategy`, `/ai/dual-review` | same | `/api/ai/byok/{status,key,test,prefs,reports}`, Copilot routes | Partial | Policy, usage and dual review are missing; drafting targets the DSL. | Extend ATP (AMW-11..13) |
| API-7 | Watchlists: `/watchlists` (GET, POST, DELETE) | same | None | Missing | - | New slice (AMW-2) |
| API-8 | Alerts and journal: `/alerts/rules`, `/alerts/events`, `/journal` | same | Alert rules / events routes, journal and PAPER routes | Have | - | Skip |
| API-9 | Backtests: `/backtests` (sync or job) | same | Backtest routes (`backend/app/backtest/routes.py`) | Have | - | Skip |
| API-10 | Lab: `/lab/capabilities`, `/lab/templates`, `/lab/strategies` (CRUD), `/publish`, `/lab/validate`, `/lab/render`, `/lab/export/python`, `/scans`, `/backtests`, `/lab/runs` | same | None | Missing | - | New slice group (AMW-19..25) |
| API-11 | Reports and schedules: `/reports`, `/reports/{id}/download`, `/schedules` | same | None | Missing | - | New slice (AMW-14, AMW-15) |
| API-12 | Notification settings: `/notify`, `/notify/telegram/official/{start,verify}`, `/notify/telegram/own`, `DELETE /notify/telegram` | same | Alert channels, `notification_links` routes, unsubscribe | Have | - | Skip |
| API-13 | Broker and feed: `/broker/fyers` (PUT/DELETE), `/login-url`, `/callback`, `/feed/{start,stop,status,bars}`, `WS /stream/bars`, `/data-source` | same | Broker credential routes, `/api/live/stream` (SSE), data-source selection in the UI | Partial | - | New slice (AMW-17) |
| API-14 | Scanner layer: `/scanner/templates`, `/scanner/nl`, `/scanners` (save), `/duplicate`, `/share`, `/replay`, `/compare` | same | `/api/screener/{registry,taxonomy,legends,validate,screens,run,runs}`, `/api/screener/history/{asof,fitness,backtest}` | Partial | NL, versions and compare are missing. | Extend ATP (AMW-6, AMW-7) |
| API-15 | Forward tests: `/forward`, `/forward/mark` | same | None | Missing | - | New slice (AMW-10) |
| API-16 | Plan and admin: `/plan`, `/admin/health`, `/admin/audit`, `/billing/webhook` | same | `/api/billing/{plans,usage}`, `/api/admin/*`, Razorpay webhook | Have | - | Skip |

### 1.10 AMW engines and services

| # | AMW engine / service | AMW source | ATP equivalent | Status | What is different | Decision |
|---|---|---|---|---|---|---|
| X-1 | Market-data provider: Yahoo Finance (delayed), disk cache with `pickle`, broker template | `src/data/provider.py` | Broker adapters + lake (`backend/app/market_lake/*`), B5 history API | Have | Yahoo is never a signal input in ATP; `pickle` on cached files is unsafe. | Skip |
| X-2 | Controlled failover between providers (no merging, discrepancy refusal, circuit breaker) | `src/data/failover.py` | Provider seams (ADR-0016), broker circuit breaker (`backend/app/brokers/circuit_breaker.py`) | Partial | ATP does not fail over between data vendors; one source per read is by design. | Skip |
| X-3 | OHLCV validation, completed-candle rule, holiday-aware staleness, corporate-action flag | `src/data/validation.py` | B3 quality detectors, B4 adjusted view, closed-bars-only reads, `backend/app/market_data/freshness.py`, holiday calendar | Have | ATP is stronger. | Skip |
| X-4 | Universe loader (Nifty 50/200/500, NSE list, labelled FALLBACK) | `src/data/universe.py` | `backend/app/universe/*` (as-of membership, sectors, F&O lots) | Have | ATP is survivorship-free; no fallback list. | Skip |
| X-5 | Instrument master from official NSE lists | `src/data/instruments.py` | `backend/app/instruments/*`, `md_isin_symbols` | Have | - | Skip |
| X-6 | Fixture provider (SIMULATED data for tests) | `src/data/fixture_provider.py` | `backend/tests/sample_market.py`, `master_fixture.py`, golden fixtures | Have | - | Skip |
| X-7 | Indicators (EMA, SMA, RSI, ATR, MACD, ADX/DI, RVOL, VWAP, Bollinger, Supertrend) | `src/analysis/indicators.py` | `backend/app/indicators/*` (prefix cache, Z10-a golden) | Have | - | Skip |
| X-8 | Per-timeframe analysis bundle (indicators, swings, events, zones, S/R, liquidity, patterns, candles) | `src/analysis/context.py` | ScreenQL `SymbolData`, evidence packs, chart layers | Have | - | Skip |
| X-9 | Risk helpers: position size from configured equity, cost per side | `src/risk.py` | `backend/app/risk_engine/*`, `backend/app/decision_tools/calculators.py`, India cost model | Have | - | Skip |
| X-10 | Job manager: thread pool, progress, cancel, resume, durable `job_runs`, orphan recovery | `src/services/jobs.py` | `backend/app/ai/research_jobs.py` + `backend/app/workers/research_worker.py` (queue, heartbeat, interrupted) | Partial | Same pattern, one queue; Lab runs reuse it. | Extend ATP (AMW-19) |
| X-11 | Signal lifecycle service | `src/services/lifecycle.py` | None | Missing | - | New slice (AMW-9) |
| X-12 | Shared service layer for UI and API | `src/services/app_services.py` | FastAPI routes over modules | Have | - | Skip |
| X-13 | Storage: SQLAlchemy Core, SQLite or PostgreSQL, additive `create_all` migrations, `user_id` on private tables | `src/storage/db.py` | Alembic (single head, `ROLLBACK_SAFE`), row-level multi-tenancy (ADR-0001), tenancy sweep | Have | ATP's model is stricter (section 2). | Skip |
| X-14 | Plotly chart rendering | `src/ui/charts.py` | lightweight-charts v5 engine (`frontend/src/charting/*`) | Have | - | Skip |
| X-15 | Telegram client (Bot API, bounded retries, redaction) | `src/notify/telegram.py` | `backend/app/alerts/dispatcher.py`, `backend/app/telegram_inbound/service.py` | Have | - | Skip |
| X-16 | In-process scheduler thread | `src/services/scheduler.py` | Trading worker loop (ADR-0010) | Partial | ATP schedules in the worker, never in the API process. | New slice (AMW-15) in the worker |
| X-17 | Key encryption (AES-256-GCM, master key from env or a 0600 file) | `src/ai/crypto.py` | `backend/app/secrets_store/{encryption,envelope}.py` (per-tenant data keys, ADR-0008) | Have | ATP is stronger. | Skip |
| X-18 | Versioned thresholds in `config/settings.yaml` | `src/config.py` | `backend/app/price_action/pa_settings.py`, per-module settings, env | Have | - | Skip |
| X-19 | Research report builder (reportlab PDFs) | `src/reports/research.py` | None | Missing | - | New slice (AMW-14) |
| X-20 | CSV export (UTF-8 BOM, ISO timestamps) | `src/screener/export.py` | Backtest CSV in `BacktestPage.tsx`, compliance CSV | Partial | No screen-run or signal CSV. | Extend ATP (AMW-14) |

### 1.11 Totals

| Status | Rows |
|---|---|
| Have | 55 |
| Partial | 51 |
| Missing | 28 |
| **Total** | **134** |

| Decision | Rows |
|---|---|
| Skip | 64 |
| Extend ATP | 25 |
| New slice | 45 |

The Partial and Missing rows that are not skipped map to the 25 slices in section 4.

---

## 2. ATP rule conflicts and how the port resolves them

| # | AMW behaviour | ATP rule it conflicts with | Resolution in the port |
|---|---|---|---|
| C-1 | AI Chart Analyst returns free LLM prose (checked only for unknown numbers), in a language the user picks | AI output is rules-generated or grounded; HC4-5 retired free AI text; English only | The analyst report is built from rule sentences over engine evidence (AMW-11). An AI step may only select and order known sentence ids, validated server-side (the HC4 follow-up shape). No free text is shown. English only; the `lang` argument is not ported. |
| C-2 | Dual Review shows two providers' prose side by side | Same as C-1 | Dual Review returns two sets of **structured verdicts** (the Vision audit format: per-aspect pass / caution / fail with numbers from the engine only, model digits dropped, instructing notes withheld) plus an agreement flag (AMW-13). |
| C-3 | AI strategy drafting writes Python / Pine code | ADR-0006 (AI acts only on approval), ADR-0005 (one DSL), untrusted code | Not ported for code modes. The Copilot keeps drafting the DSL; a later slice may let it draft ScreenQL or Pine subset, which the host compiles and validates. Drafts are never published, scanned or run automatically. |
| C-4 | Own users, roles (admin/analyst/viewer), bearer tokens, `AMW_AUTH_MODE=local` (no login), SQLite store, `create_all` + `ALTER TABLE` migrations | Row-level multi-tenancy (ADR-0001), scopes (ADR-0009), tenancy route table, single alembic head, `ROLLBACK_SAFE` | Nothing of AMW's auth or storage is ported. Every new table carries `tenant_id` and `user_id`; reads of personal rows filter on both (BT1b). Every route uses ATP's dependencies (`get_current_user`, `require_trader`, `require_scope`) and is classified in `tests/tenancy_route_table.py`. Migrations are alembic revisions on the current head with `ROLLBACK_SAFE` declared. |
| C-5 | Yahoo Finance as the default data source; a `pickle` disk cache; a FALLBACK large-cap list when NSE blocks downloads | `docs/DATA_SOURCES.md`: Yahoo is background-only and never a signal input; data comes from the lake or the user's own broker session | All ported features read the shared lake (`md_candles`, bhavcopy EOD) or the user's own session through BT1b. No Yahoo, no pickle, no fallback universe (an unknown universe is refused). |
| C-6 | Strategy Lab runs untrusted code inside the app and API containers, which need root, `CAP_SYS_ADMIN` and `apparmor:unconfined`; Windows runs code with no OS sandbox when `AMW_ALLOW_UNSANDBOXED=1` | ADR-0005 (no arbitrary code), least privilege (backend runs as `appuser`), shared 8 GB host with live trading processes | A new ADR (ADR-0027) amends ADR-0005. Code runs only in a separate runner container with no network and no secrets, inside per-run isolation (section 3). The backend and worker never gain privileges. There is no unsandboxed mode on any platform. Every code mode sits behind its own flag that cannot be turned on before the security review gate. |
| C-7 | A Lab strategy's output becomes a signal that drives alerts and reports | ADR-0006, PAPER only, LIVE off | Code-mode results are research output: shown, backtested, reported. They never create entry intents (`execution_core`), Scan Then actions, auto-PAPER entries or proposals; a test enforces it. Host-evaluated versions compile to ScreenQL and may become screens and alert rules like any screen. |
| C-8 | Long-running scans, Lab runs and the scheduler run in the API or Streamlit process | ADR-0004 (exits never blocked), ADR-0010 (one worker loop) | Lab runs are queued and drained by the research worker (a separate process). Schedules run in the trading worker's existing tick but only enqueue work. Nothing new runs inside the trading cycle's critical path. |
| C-9 | All eight Lab modes "released" by default; operators may restrict them | Every new feature is behind a flag, off by default | Flags `strategy_lab`, `strategy_lab_host`, `strategy_lab_python`, `strategy_lab_sql_lua`, `strategy_lab_js`, `strategy_lab_r`, all in `DEFAULT_OFF_FLAGS` (section 3.5). |
| C-10 | BYOK "session only" mode keeps a key in backend memory | BYOK keys are per user, stored via Settings, encrypted | Not ported. Keys are stored encrypted (envelope encryption) or not at all. |
| C-11 | Live Data page has its own FYERS connection form (App ID, secret) and a "paste the redirected URL" fallback | Broker keys only through Settings | The FYERS credential stays in Settings -> Broker accounts. The feed controls (start, stop, symbols) refer to that credential and never accept a key. |
| C-12 | FYERS data is used for each user's charts and scans | `broker_data_terms.json`: Fyers status **pending**, "written consent needed before charts/screeners run on Fyers data" | AMW-17 is built behind `fyers_minute_feed`; `set_flag` refuses ON while the Fyers terms status is not `allowed` (AMW-Q5). Bars stay per user (`md_tenant_candles` with source `broker:fyers:u<id>`, `holder_visible`). |
| C-13 | WebSocket stream; the token travels in the first message | PW4 live channel is SSE with the bearer token in a header | The bar stream is an SSE endpoint with the same auth as `/api/live/stream`. |
| C-14 | Telegram linking polls `getUpdates` on the official bot | ATP receives Telegram through its webhook (`telegram_inbound`) | Use ATP's `notification_links` and link codes; nothing polls. |
| C-15 | Report files on the local disk under `data/reports/<user_id>/` | Multi-container deployment, R2 backups, retention jobs | Reports are stored as tenant- and user-scoped rows (small files) or in the object store with a tenant/user prefix; retention by `backend/app/retention`. |
| C-16 | New dependencies: Streamlit, Plotly, matplotlib, reportlab, openpyxl, sqlglot, lupa, Node, TypeScript, R | ATP keeps `backend/requirements.txt` minimal (the PNG renderer uses the standard library) | No Streamlit, Plotly or matplotlib. PNG uses the existing renderer. XLSX uses a small standard-library writer; PDF waits for a dependency decision (AMW-Q4). sqlglot, lupa, Node, TypeScript and R live only in the runner images, never in the backend image. |
| C-17 | Scores labelled "A1 Candidate", "Strong" | Wording rules (`backend/app/ai/wording.py`), "matches" vocabulary, no advice terms | Labels are re-worded to ATP's grade vocabulary and pass the wording check; every surface keeps the disclaimer. |
| C-18 | Lab contexts give code the full series; backtests rebuild context per bar | Z10-a no look-ahead | The host builds every context from closed bars cut at the decision bar (`backend/app/backtest/windows.py` rules for higher timeframes). Code never receives a later bar. New ScreenQL functions carry Z10-a cases. |
| C-19 | TypeScript SDK `.d.ts` lives next to the worker scripts | The frontend never imports from outside `frontend/` | The runner owns `lab_runner/sdk/atp-sdk.d.ts`; the frontend keeps its own copy under `frontend/src/lab/` for editor hints. A CI test compares the two by hash. |
| C-20 | Paper forward tests apply no costs | ATP's PAPER results and backtests carry the India cost model | Forward tests report gross R and net R (C2 default model) side by side and are labelled simulations (AMW-Q8). They are not PAPER orders and never touch the PAPER account. |
| C-21 | No licence file in the AMW archive | Provenance of ported code | Re-implement from behaviour; port tests as specifications; record provenance per slice, as `docs/TRADE_PORT_MAP.md` does (AMW-Q1). |

---

## 3. Strategy Lab port design

The owner wants the Strategy Lab included and done properly. The design keeps one idea from AMW - one contract for
every authoring mode - and changes two things: the IR is ATP's ScreenQL AST, and untrusted code runs in a separate,
locked-down runner instead of the application containers.

### 3.1 Shape

```
 author -> draft (lab_strategies) -> validate -> publish -> immutable version (lab_versions, sha256)
                                                              |
        host builds every context (closed bars, indicators, structure, HTF, params) from the lake or the
        user's own broker session (BT1b); the timestamp of a result is always the host's
                                                              |
   visual -----------------> ScreenQL AST + envelope --> ScreenQL runtime (host, no user code)
   pine subset -> translate -^
   python / sql / lua / js / ts / r --> lab_runs queue --> research worker (dispatcher)
                                         --> UDS --> lab-runner container (no network, no secrets)
                                         --> per-run jail --> JSON result --> host contract check
                                                              |
             Lab scan results | Lab backtest (ATP engine via a BaseStrategy adapter) | reports
             never: entry intents, Scan Then, auto PAPER, proposals (code modes)
```

### 3.2 Storage (migration in AMW-19)

One alembic revision on `main`'s head, `ROLLBACK_SAFE = True` (three new tables, nothing existing changed), with
`ROLLBACK_SAFE_REASON` covering the trigger SQL (it touches only the new `lab_versions` table).

| Table | Columns | Rules |
|---|---|---|
| `lab_strategies` (drafts) | `id`, `tenant_id`, `user_id`, `name`, `description`, `mode` (`visual`, `pine`, `python`, `sql`, `lua`, `javascript`, `typescript`, `r`), `draft_source` (<= 100,000 characters), `draft_envelope_json`, `draft_spec_json` (params with bounds, timeframes, history, limits), `archived`, `created_at`, `updated_at`; unique (`tenant_id`, `user_id`, `name`) | Personal rows: every read filters on `tenant_id` and `user_id`. Archived, never hard-deleted while a run points at it. Plan limit `lab_strategies` (AMW-1). |
| `lab_versions` (immutable) | `id`, `tenant_id`, `user_id`, `strategy_id`, `version`, `mode`, `source`, `compiled_json` (host modes: canonical ScreenQL text and AST per side), `envelope_json`, `spec_json`, `source_sha256`, `compiled_sha256`, `runtime_pin` (for example `python-3.13/worker-1` or `node-22.x/ts-x.y.z`), `image_digest`, `note`, `created_at`; unique (`strategy_id`, `version`) | No UPDATE path in code. On PostgreSQL a `BEFORE UPDATE` trigger refuses any change. DELETE is not blocked by the trigger, so tenant cascade and DPDP erasure (`erase_user`) still work. |
| `lab_runs` (queue + audit) | `id`, `tenant_id`, `user_id`, `strategy_id`, `version` (null = draft try-out), `kind` (`validate`, `try`, `scan`, `backtest`), `status` (`queued`, `running`, `completed`, `partial`, `failed`, `cancelled`, `interrupted`), `mode`, `runtime`, `config_json` (universe, timeframe, params, data source, as-of), `stats_json`, `error` (sanitised, <= 2,000 characters), `logs` (<= 16 KB), `cpu_ms`, `wall_ms`, `heartbeat_at`, `created_at`, `started_at`, `finished_at` | Same queue and heartbeat rules as `research_jobs` (stale -> `interrupted`). Index (`status`, `created_at`). |
| `lab_run_results` | `id`, `run_id`, `tenant_id`, `user_id`, `symbol`, `signal_bar`, `qualified`, `bias`, `rules_json` (name, passed true/false/null, detail), `entry`, `stop`, `target`, `error` | Bounded per run (plan `max_screen_symbols`). |

Drafts are never used by scans or backtests. A "try" run on a draft is allowed on at most 5 symbols. Publishing
freezes source, envelope, spec and compiled form and records the hashes; a scan or backtest always names a version.

### 3.3 The IR, and how Pine-subset and visual modes become ScreenQL

ATP's ScreenQL (`screenql/1`, ADR-0021) is already a typed, versioned, serialisable AST with a validator (units,
offsets, timeframes, look-ahead, cost cap) and a runtime that only reads closed bars. It replaces AMW's `ir.py`.
What ScreenQL lacks is the strategy part, so the Lab adds a small envelope, `lab-envelope/1`:

```json
{
  "envelope_version": 1,
  "base_tf": "1d",
  "long":  {"screen": "<canonical ScreenQL text>", "ast": {}},
  "short": null,
  "stop":   {"method": "atr", "mult": 1.5} ,
  "target": {"r": 2.0},
  "params": {"len": {"type": "int", "default": 20, "min": 5, "max": 200}},
  "classification": "trend"
}
```

- Stop methods: `atr` (close -/+ mult x ATR(14)) and `swing` (last confirmed swing low/high -/+ buffer x ATR). Both
  are computed by the host at the decision bar.
- Evaluation: the ScreenQL runtime evaluates each side on the symbol's closed bars. A side qualifies only when its
  screen matches; NaN never matches, which gives AMW's "missing never qualifies" for free. Every rule's value is read
  back from the evidence pack so the result lists each rule with pass / fail / unknown.
- Backtest: a `LabStrategy(BaseStrategy)` adapter turns a version into a `Signal` through `build_signal()`, exactly as
  `DeclarativeStrategy` does, so ATP's backtest engine, models and robustness checks run it unchanged. Every ScreenQL
  function is causal (Z10-a), so the series evaluation equals a per-bar cut; functions marked `LAST_BAR_ONLY` are
  evaluated bar by bar, as S6a does.

**Visual mode.** The editor is the Screener's block builder (U5 D2: ALL / ANY / NOT groups, live counts) with an
envelope panel (sides, stop, target, parameters). It produces builder JSON, which `compile_screen` turns into the
AST; the round-trip parity tests of S1a already cover it. AMW's limits (depth <= 6, <= 300 nodes) are replaced by
ScreenQL's (60 levels, 4,000 characters, cost cap). AMW predicates map to registry functions:

| AMW predicate | ScreenQL (illustrative; the registry is authoritative) |
|---|---|
| `trend_is`, `htf_trend_is` | `Trend` classifier; `Trend@1w` |
| `bos_recent`, `choch_recent` (within N bars) | `SwingBreakType` series with `Count(...)`; `StructureEvent` is `LAST_BAR_ONLY` and cannot sit inside `Count` |
| `close_above_last_swing_high` / `below_low` | `close > SwingHigh(0)` / `close < SwingLow(0)` |
| `near_support`, `near_resistance` | `NearSupport`, `NearResistance` |
| `in_demand_zone`, `in_supply_zone`, `sr_role_reversal` | `NearZone(role="demand")`, `ZoneRole`, `ZoneState` |
| `breakout_retest` | `RetestEntry` |
| `pullback_to_ema` | `Min(low, 3) <= EMA(close, 20) + 0.5 * ATR(14) AND close > EMA(close, 20)` |
| `min_avg_traded_value` | `SMA(close * volume, 20) >= $min_value` |
| `supertrend_dir`, `pct_change`, `gap_recent` | `Supertrend`, `PctChange`, `GapType` / `GapBreakout` |
| `candle_pattern` (with next-bar confirmation) | `Pattern` / `PatternBullish` / `PatternBearish`, confirmation argument added in AMW-3 |
| `rsi_divergence` | `RsiDivergence` (new, AMW-3) |
| `last_swing_label` (HH / HL / LH / LL) | `SwingLabel` (new, AMW-3) |
| `session_between` | `TimeOfDay` (new, AMW-3) |
| `htf_close_above_ema` | `close@1w > EMA(close, 20)@1w` |
| `data_quality_ok` | Not a predicate: a symbol whose data fails the lake quality checks is reported "not evaluated" for the run |

**Pine-subset mode.** `backend/app/lab/pine.py` is a hand-written tokenizer and parser (like ScreenQL's) that
translates the subset to ScreenQL text, then runs the normal validator. Pine is never executed. Line-numbered
diagnostics are kept.

| Pine | ScreenQL |
|---|---|
| `//@version=5` or `6`, `indicator()` / `strategy()` header | required header; ignored otherwise |
| `x = input.int(20)`, `input.float(...)` | `$x` parameter with default and bounds |
| `open high low close volume`, `hl2 hlc3 ohlc4` | fields; `(high + low) / 2`, ... |
| `x[n]` | `x[n]` (n a non-negative integer literal) |
| `ta.ema(src, n)`, `ta.sma` | `EMA(src, n)`, `SMA(src, n)` |
| `ta.rsi(close, n)`, `ta.atr(n)` | `RSI(n)`, `ATR(n)` (a non-close RSI source is refused unless the registry accepts it) |
| `ta.highest(src, n)`, `ta.lowest(src, n)` | `Max(src, n)`, `Min(src, n)` |
| `ta.change(x, n)` | `(x - x[n])` |
| `ta.stdev(x, n)` | `StDev(x, n)` (new, AMW-3) |
| `ta.crossover(a, b)`, `ta.crossunder(a, b)` | `CrossAbove(a, b)`, `CrossBelow(a, b)` |
| `and`, `or`, `not`, comparisons, `+ - * /` | `AND`, `OR`, `NOT`, the same operators |
| `long_signal = ...`, `short_signal = ...`, `alertcondition(...)` | the envelope's `long` / `short` screens |
| `stop_atr = input.float(1.5)`, `target_r = input.float(2.0)` | envelope `stop` and `target` |
| `strategy.*`, `request.*`, `var` / `varip`, `:=`, `if` / `for` / `while`, user functions, `?:`, `na()` / `nz()`, `math.*`, arrays / maps | refused with a line-numbered diagnostic |
| `plot*`, `bgcolor`, `label.*`, `line.*`, `box.*`, `table.*` | ignored with a warning |

Values follow ATP's indicator code, so they match ATP's charts, screens and backtests; they converge to, but are not
promised to equal, TradingView's after warm-up. The page says so.

### 3.4 Code modes: what AMW's sandbox does, its gaps, and the ATP runner

**What `src/lab/sandbox.py` does.** For every code mode it starts a child process through
`unshare -n -m --propagation private sh -c <mount tmpfs over /tmp, /var/tmp, /dev/shm> && setpriv --reuid=65534
--regid=65534 --clear-groups --no-new-privs`. Before exec it sets rlimits: CPU = timeout seconds, address space (not
for Node, which relies on `--max-old-space-size`), file size 0, 1,024 open files, no core. The environment is a fixed
PATH and locale with a temporary HOME. The work directory is a random `mkdtemp` under a 0711 `.sandbox` directory,
made mode 1777, with read-only input files. A wall-clock timeout kills the process group; stdout and stderr are
truncated to a size limit. In-memory semaphores cap global and per-user concurrency. A cached self-test checks uid,
an environment canary, network, file writes, reads of `/root` and `data/`, and escapes to the host `/tmp`. Inside,
each language adds guards: Python restricted builtins, an import allow-list and an audit hook; Node a null-prototype
`vm` realm with code generation off and `--permission`; Lua with libraries removed and memory / instruction limits;
SQLite with an authorizer, progress handler and row cap; R with regex deny-lists. On Windows, or when the self-test
fails, code runs only if `AMW_ALLOW_UNSANDBOXED=1`, with no OS boundary at all.

**Gaps found in it.**
1. Privilege: it needs root and `CAP_SYS_ADMIN` plus `apparmor:unconfined` on the app and API containers, so the whole
   web application runs with a widened kernel surface.
2. No PID namespace: a run sees every host process in `/proc`. All runs share uid 65534, so one run can signal (and,
   depending on the host's Yama setting, trace) another user's concurrent run, and read its `/proc/<pid>/cwd` - for R
   that exposes another user's `strategy.R` and `ctx_data.R`.
3. No cgroup: no `pids.max` and no `RLIMIT_NPROC`, so a fork bomb is possible; memory is bounded only by
   `RLIMIT_AS`, which is not applied to Node.
4. Escaped children survive: a child that calls `setsid` or double-forks leaves the process group, so `killpg` on
   timeout misses it, and it keeps running as uid 65534 next to later runs.
5. No seccomp filter: the full host-kernel syscall surface stays reachable.
6. Filesystem: only the temporary directories are replaced. The rest of the root filesystem is visible to the extent
   that file modes allow (application source, installed packages, `/etc`, any world-readable file); only `data/` and
   `.cache/` are protected (0700).
7. Output is buffered without a bound: `communicate()` reads everything into host memory before truncating, so a run
   that prints gigabytes until its timeout can exhaust the host process's memory.
8. Quotas are per process and in memory: the dashboard and the API each enforce their own counters.
9. The self-test runs once per process (`lru_cache`) and is never repeated after an environment or image change.
10. The unsandboxed mode (Windows, or `AMW_ALLOW_UNSANDBOXED=1`) runs untrusted code with no boundary.
11. The language guards are deny-lists and are bypassable - for example R's `\bsystem2?\s*\(` check does not catch
    `f <- system; f("...")`. They are defence in depth only, not a boundary.
12. The work directory is 1777 and `RLIMIT_FSIZE=0` does not stop creating directories or inodes.

**ATP design.** Three layers, each one sufficient on its own for the next to be defence in depth.

*Layer 1 - a separate runner container (`lab-runner`, compose profile `lab`, off unless started).*
- `network_mode: none`. The only channel is a Unix domain socket on a volume shared with the dispatcher.
- No database credentials, no `.env`, no tenant keys; the environment holds only the socket path.
- `read_only: true` root filesystem; a small tmpfs for `/tmp`; non-root user; `cap_drop: [ALL]`;
  `security_opt: no-new-privileges`; `pids_limit`; `mem_limit` (`MEM_LAB`); `cpus` limit.
- Runtime: gVisor (`runtime: runsc`) for this container when it can be installed on the host (AMW-Q3). Without gVisor,
  a custom seccomp profile for this container only that allows the namespace calls the per-run jail needs - never
  `CAP_SYS_ADMIN`, never `privileged`, and never any change to the backend, worker or frontend containers.
- One image per mode family, so a family that stays off costs nothing at run time and the backend image does not grow
  (section 3.7).

*Layer 2 - a jail per execution, inside the runner (nsjail or bubblewrap, pinned).*
- New user, PID, mount, network, IPC and UTS namespaces; a fresh `/proc` for the PID namespace; the run is PID 1's
  child and dies with it.
- A distinct unprivileged uid per concurrent slot, so two runs never share a uid.
- Root filesystem = read-only bind mounts of exactly the runtime it needs (interpreter, its standard library, the
  worker script, the SDK) plus a private tmpfs work directory (a few MB). No host root filesystem, no `/etc` beyond the
  minimum, `/dev` limited to `null`, `zero`, `urandom`.
- seccomp-bpf allow-list: no `socket` (other than none), no `ptrace`, `mount`, `unshare`, `setns`, `keyctl`, `bpf`,
  `perf_event_open`, `userfaultfd`, `kexec*`, module or clock-setting calls.
- cgroup v2 per run: `memory.max`, `memory.swap.max = 0`, `pids.max` (for example 32), `cpu.max`. On timeout or
  cancel the runner writes `cgroup.kill`, which ends every process of the run, including escaped children.
- rlimits as a second net: CPU seconds, NOFILE 64, CORE 0, FSIZE small.
- Empty environment; deterministic locale; no clock or randomness promises beyond what the worker provides.
- stdout is read in a streaming loop with a hard byte cap; on overflow the run is killed and marked "output limit
  exceeded". Nothing is buffered beyond the cap.

*Layer 3 - language guards (defence in depth, ported from AMW and tightened).*
- Python: restricted builtins, import allow-list (`math`, `statistics`), audit hook, no dunder access, `atp.*`
  helpers.
- SQL: the statement is checked (sqlglot allow-list) inside the runner; SQLite runs in memory with an authorizer that
  allows only the approved views, a progress-handler step limit and a row cap; `load_extension` disabled.
- Lua: lupa with `os`, `io`, `package`, `require`, `debug`, `load*`, `dofile`, `string.dump`, `raw*`, metatable access
  removed; memory and instruction limits.
- JavaScript / TypeScript: a `vm` context from a null-prototype object, `codeGeneration: {strings: false, wasm: false}`,
  `Math.random` and `Date` disabled, data entering as a string parsed inside the context, `node --permission` with no
  file, child-process, worker or WASI grants; TypeScript type-checked against the SDK and transpiled first.
- R: an environment whose parent is a curated function list (not `baseenv()`), contexts passed as JSON parsed inside
  R, the allow-list of `stats` / `utils` functions; the regex checks stay only as early, friendly errors.

*Dispatcher (host side, `backend/app/lab/dispatch.py`), run by the research worker.*
- Claims `lab_runs` rows like `research_jobs` (conditional UPDATE, heartbeat, stale -> `interrupted`).
- Builds contexts on the host: closed bars only, indicators from `backend/app/indicators`, structure from
  `backend/app/price_action`, higher timeframes per `backend/app/backtest/windows.py`; data from the lake or the user's
  own session (BT1b). No account data, no other user's data.
- Sends one batch per call (bounded contexts per batch), receives JSON, validates it with the contract (schema, finite
  numbers, stop on the correct side of the close, host-set timestamps), stores results.
- Global and per-user concurrency and a per-user daily CPU-seconds budget are enforced in the database (or Redis), not
  in process memory.

*Self-test.* The runner runs a probe suite at start, every hour and after any image change, and the dispatcher stores
the latest result (platform control `lab_sandbox`). The probe checks: no network (TCP, UDP, DNS, netlink), uid, empty
environment, no read of `/etc/shadow`, `/proc/1/environ`, application source or another run's work directory, no
write outside the work directory, fork bomb contained by `pids.max`, memory bomb contained, CPU spin and sleep ended at
the timeout, an escaped child killed by `cgroup.kill`, output flood capped, two concurrent runs invisible to each
other. A failed or stale self-test turns every code mode "unavailable" on the next request (fail closed) and raises
one operator notification. `GET /api/lab/capabilities` reports the detected matrix (mode, runtime version, available,
reason); `GET /api/admin/lab/sandbox` shows the full self-test (admin).

### 3.5 Flags

All are added to `FEATURE_FLAGS` and `DEFAULT_OFF_FLAGS`.

| Flag | Covers | Turn-on precondition (enforced in `set_flag`) |
|---|---|---|
| `strategy_lab` | The page, drafts, versions, runs list, templates, capabilities | none beyond the usual |
| `strategy_lab_host` | Visual and Pine-subset modes (host-evaluated, no runner) | `strategy_lab` on |
| `strategy_lab_python` | Python | `strategy_lab` on; latest runner self-test verified and fresh; a recorded security-review approval for the `python` family |
| `strategy_lab_sql_lua` | SQL and Lua (run in the Python runtime image) | as above, family `sql_lua` |
| `strategy_lab_js` | JavaScript and TypeScript | as above, family `node` |
| `strategy_lab_r` | R | as above, family `r` |

The approval record (platform control `lab_security_review`: family, reviewer, date, commit, image digest) is written
by a SUPER_ADMIN with MFA and audit-logged; it is invalidated automatically when the runner image digest, seccomp
profile or worker script changes. The refusal works like the `legal_pages` placeholder guard. Turning any family flag
off stops new runs and cancels queued ones of that family at once.

### 3.6 Security review checklist (gate G-LAB, before any code-mode flag can be turned on)

1. ADR-0027 (Strategy Lab: host-evaluated modes and sandboxed code modes) accepted; it amends ADR-0005's "no sandbox
   is needed" consequence. Threat model reviewed: assets (host, secrets, other tenants' data and code, trading
   availability), attackers (any signed-up user), entry points (source, params, outputs).
2. Runner container: no network, no secrets in environment or filesystem, read-only root, non-root, `cap_drop: ALL`,
   `no-new-privileges`, limits set; no change to backend, worker or frontend privileges (checked from the compose
   files in CI).
3. Per-run jail: namespaces, distinct uid per slot, minimal bind mounts, fresh `/proc`, seccomp allow-list,
   cgroup v2 limits and `cgroup.kill`, streaming output cap - each verified by an automated test.
4. Red-team suite passes in CI against the real runner images and once on the production host:
   - network: TCP, UDP, DNS, abstract Unix sockets, netlink;
   - reads: `/etc/shadow`, `/proc/1/environ`, application source, the socket directory, another run's work directory and
     `/proc/<pid>/cwd`;
   - writes outside the work directory; inode exhaustion;
   - resources: fork bomb, memory bomb, CPU spin, long sleep, escaped child via `setsid` and double fork, output flood,
     one very long line;
   - cross-run: signal or trace another run;
   - per language: Python object-graph escapes (`__subclasses__`, frames, `gc`), JavaScript constructor chains, `Proxy`,
     WebAssembly, `import()`; R aliasing (`f <- system`), backtick calls, `.Internal`, environment walking; Lua
     `string.dump`, `load`, metatables; SQL `ATTACH`, `load_extension`, `PRAGMA`, recursive-CTE blow-ups.
5. Self-test runs at start, hourly and after image changes; fail-closed behaviour tested; operator alert tested.
6. Quotas tested: per-user and global concurrency, daily CPU seconds, source size, contexts per batch, symbols per scan,
   plan limits; a burst of runs does not move the API's p95 or the trading worker's cycle time beyond the agreed
   budget (load test on the 8 GB profile).
7. Data minimisation: contexts carry only the requester's entitled closed bars; logs are capped, owner-only and
   sanitised; no key or token can reach a context (test).
8. Result use: a test proves code-mode results never reach `execution_core`, Scan Then, auto PAPER, proposals or
   alerts.
9. Audit: every validate, try, publish, scan and backtest writes an audit row with the source SHA-256 and the image
   digest.
10. Supply chain: runner base images pinned by digest; Node, TypeScript, R, lupa and sqlglot pinned to releases at
    least two weeks old; image scan gate green; SBOM stored.
11. Resource budget measured: peak RSS per family and image sizes recorded in `docs/BENCHMARKS.md`; `MEM_LAB` set.
12. Kill path rehearsed: family flag off, `docker compose --profile lab stop`; trading, exits and alerts unaffected.
13. User-facing text reviewed: what isolation means, the limits, that results are research only, the terms for
    uploaded code.
14. An independent reviewer (not the author) and the owner sign off; the approval record is written per family. Any
    later change to the runner, seccomp profile, base image or worker scripts needs a re-review.

### 3.7 Docker images and the 8 GB host

The backend image does not change: no Node, TypeScript, R, lupa or sqlglot in it. Each family gets its own runner
image, built from `lab_runner/` (Dockerfiles per family), pulled only when its compose profile runs.

| Image | Contents | Approximate size (to be measured in AMW-21) |
|---|---|---|
| `atp-lab-py` | `python:3.13-slim` + lupa + sqlglot + Python/SQL/Lua workers + jail tool | about 150-200 MB |
| `atp-lab-node` | `node:22-slim` + pinned TypeScript + JS worker and TS compiler shim + jail tool | about 250-300 MB |
| `atp-lab-r` | Debian slim + `r-base-core` (with BLAS/LAPACK and Fortran runtime) + R worker + jail tool | about 350-450 MB |
| gVisor `runsc` (host) | runtime binary | about 50 MB |

Disk: under 1 GB in total, small against the 100 GB NVMe.

Memory is the constraint. Today's limits sum to about 6.6 GB, leaving about 1.4 GB for the OS, Docker and page cache
(`docs/OPERATIONS.md` 1.2b).
- Lab dispatching adds no process: the research worker drains `lab_runs` as well as studies (AMW-Q7).
- One runner container with `MEM_LAB=512m` and concurrency 1 covers the Python, SQL, Lua and Node families; headroom
  drops to about 0.9 GB.
- R needs about 512-768 MB per run (AMW gives it the strategy limit + 768 MB). It is the last family, and on the 8 GB
  host it either runs alone in the runner with concurrency 1 and `MEM_LAB=768m` (headroom about 0.6 GB, below the
  comfortable margin) or waits for a larger host (AMW-Q6).
- Node's heap is bounded by `--max-old-space-size` and, in ATP, also by the per-run cgroup `memory.max`.

---

## 4. Slice plan

Rules for every slice:
- One reviewable PR, flags off, a self-review note, the module's tests plus the new ones, ruff and mypy on the touched
  files.
- Each new route classified in `tests/tenancy_route_table.py`.
- Each migration re-chained onto `main`'s single head when it merges, with `ROLLBACK_SAFE` declared.
- AMW tests are ported as behaviour specifications (re-written on ATP fixtures); AMW code is never run.

Migration slices must merge in this order to keep one alembic head: AMW-2, AMW-7, AMW-9, AMW-10, AMW-12, AMW-14,
AMW-15, AMW-17, AMW-19.

### 4.1 Platform group

**AMW-1 - Plan limits v2** (S)
- Scope: new `Plan` fields: `max_watchlists`, `max_screen_symbols`, `reports_per_day`, `report_retention_days`,
  `report_storage_mb`, `report_schedules`, `min_schedule_interval_min`, `lab_strategies`, `lab_cpu_seconds_per_day`.
  Checks in `limits.py`; 403 with the existing upgrade hint. Files: `backend/app/plans/{registry,limits}.py`.
- Flag: `plan_limits_v2` (enforcement of the new fields; the fields are read by each later slice).
- Migration: no (plans live in code). Routes: none new (`GET /api/billing/usage` gains the counts).
- Frontend: `BillingCard` shows the new limits.
- Tests to adapt: `test_saas_sched.py` (entitlement cases), `test_decisions.py` (plan contract cases).
- Depends on: nothing.

**AMW-2 - Watchlists** (S)
- Scope: personal named symbol lists; usable as a screener universe and on the Scanner page. Files:
  `backend/app/watchlists/{service,routes}.py`, model in `backend/app/db/models.py`, `frontend/src/screener/` universe
  picker.
- Flag: `watchlists`. Migration: yes (`watchlists`: `tenant_id`, `user_id`, `name`, `symbols_json`, timestamps;
  unique (`tenant_id`, `user_id`, `name`); `ROLLBACK_SAFE = True`).
- Routes: `GET/POST /api/watchlists`, `PUT/DELETE /api/watchlists/{id}` - class "self: the caller's own watchlists".
- Frontend: Screener page universe picker "My watchlists"; Scanner page "Load watchlist"; a small manage panel.
- Tests to adapt: `test_platform.py` (watchlist CRUD and tenancy cases).
- Depends on: AMW-1 (limit).

**AMW-3 - ScreenQL functions for AMW predicates** (M)
- Scope: `RsiDivergence(side, lookback)` on confirmed pivots; `StDev(x, n)`; `SwingLabel(kind)` (HH / HL / LH / LL of
  the last confirmed swing); `TimeOfDay` (intraday session window); candle v2 detectors (Strong Body, Gap Reversal)
  plus a `confirm` argument on `Pattern*` (confirmed / failed / unconfirmed on the next bar). Each one: registry entry
  with units and `LAST_BAR_ONLY` / `READS_HISTORY` class, runtime, fixture test, Z10-a cases. Files:
  `backend/app/screener/{registry,runtime}.py`, `backend/app/price_action/candlestick_patterns.py`,
  `backend/app/quality/causal_cases.py`.
- Flag: `screenql_amw_functions`. Migration: no. Routes: none (the registry endpoint lists them when on).
- Frontend: palette labels in `frontend/src/screener/builderTexts.json`.
- Tests to adapt: `test_scanner_predicates.py`, `test_analysis_v2.py` (candle v2 and S/R role reversal cases).
- Depends on: nothing.

**AMW-4 - Chart patterns** (M)
- Scope: `backend/app/price_action/chart_patterns.py` - double / triple tops and bottoms, head and shoulders and
  inverse, triangles, wedges, broadening, rectangle, from confirmed swings; status (forming / confirmed / failed),
  neckline, measured target, invalidation; known only from the confirmation bar. ScreenQL `ChartPattern(name,
  status)`; a chart layer next to `PriceActionLayer`. Cup-and-handle stays unimplemented and is reported as such.
- Flag: `chart_patterns`. Migration: no. Routes: none new (chart layer data through the existing price-action layer
  route).
- Frontend: `frontend/src/charting/PriceActionLayer.tsx` toggle "Chart patterns".
- Tests to adapt: `test_analysis.py` (pattern cases); Z10-a cases.
- Depends on: nothing (parallel to AMW-3).

**AMW-5 - Screen templates** (S)
- Scope: AMW's 19 templates that ScreenQL can express, as gallery entries (taxonomy data + starters), each with
  timeframes, data needs, limitations and "not validated"; reversal confluence, trend continuation, VWAP pullback and
  reclaim, swing with weekly alignment (`Trend@1w`) and stop-width check.
- Flag: `screener_templates_amw` (needs `screener_gallery`). Migration: no. Routes: none (taxonomy endpoint).
- Frontend: `ScreenGallery.tsx` shows them.
- Tests to adapt: `test_scanner_layer.py` (template validation cases), `test_strategies.py` (reversal, trend, swing
  rule cases as screen fixtures).
- Depends on: AMW-3, AMW-4.

**AMW-6 - Deterministic natural language to ScreenQL** (M)
- Scope: `backend/app/screener/nl.py` - clause splitter, phrase -> registry mapping, interpretations and assumptions
  per clause, UNSUPPORTED clauses listed and never replaced, finest timeframe primary and coarser trend conditions as
  `@tf`. Output is ScreenQL text plus a preview; nothing runs until the user presses Run. Coordinates with S9 (the AI
  path emits the same AST; one input box).
- Flag: `screener_nl`. Migration: no.
- Routes: `POST /api/screener/nl` - class "public: parses the text in the request; stores nothing" (`P_PARSE_ONLY`)
  or "self", per the sweep's suggestion.
- Frontend: Screener page "Describe it in words" box with the interpretation list.
- Tests to adapt: `test_scanner_layer.py` (NL cases incl. the master-prompt example).
- Depends on: AMW-3, AMW-5.

**AMW-7 - Immutable screen versions and version comparison** (M)
- Scope: publishing a screen writes an immutable version (canonical text, AST, params, SHA-256, note); `PUT` edits the
  draft; runs and alert rules may pin a version; duplicate. Compare two versions on the same symbols, window, data and
  cost model through S6a fitness and S6b backtest; report both, declare no winner.
- Flag: `screener_versions` (compare also needs `screener_history` / `screener_backtest`).
- Migration: yes (`screen_versions`; nullable `screen_runs.screen_version_id`; `ROLLBACK_SAFE = True`, additive).
- Routes: `POST /api/screener/screens/{id}/publish` (trader), `GET /api/screener/screens/{id}/versions` (member),
  `POST /api/screener/screens/{id}/duplicate` (trader), `POST /api/screener/screens/{id}/compare` (self, rate
  limited).
- Frontend: Screener page version drawer and compare view.
- Tests to adapt: `test_scanner_layer.py` (save / version / duplicate / compare cases).
- Depends on: S6b (#243) merged.

**AMW-8 - Score coverage and mandatory gate** (S)
- Scope: `signal_scoring` reports `coverage` (share of weight with evidence) and marks components with no evidence as
  missing instead of scoring them; a failed mandatory condition caps the grade; STALE data is labelled.
- Flag: `signal_score_coverage`. Migration: no. Routes: none new (`/signal/enrich` response gains fields).
- Frontend: `frontend/src/components/SignalCard.tsx` shows coverage and the gate.
- Tests to adapt: `test_strategies.py` (scoring cases), `test_platform.py` (score never overrides a failed rule).
- Depends on: nothing.

**AMW-9 - Signal ledger: canonical immutable records and lifecycle** (L)
- Scope: `backend/app/signals/{ledger,lifecycle,routes}.py`. A record per signal: producer (strategy / screen / Lab
  version and version id), conditions with values and thresholds, score components and coverage, levels, expiry,
  data source, SHA-256 of the bar window used, evaluation time. Dedup key per (owner, producer, symbol, timeframe,
  direction, signal bar). Lifecycle transitions as append-only events with reason; illegal transitions refused.
  Producers (AMW-Q10): `/signal/enrich`, screen alert matches, Lab scans of published versions.
- Flag: `signal_ledger`.
- Migration: yes (`signal_records`, `signal_events`; PostgreSQL `BEFORE UPDATE` trigger on `signal_records` refusing
  changes to the record columns; `ROLLBACK_SAFE = True` with a reason: new tables only).
- Routes: `GET /api/signals/records`, `GET /api/signals/records/{key}`, `GET /api/signals/records/{key}/events` -
  class "member: the caller's own records".
- Frontend: Signals page tab "Signal records" (ranked list, pass / fail per condition, score breakdown, lifecycle).
- Tests to adapt: `test_signal_immutability.py`, `test_platform.py` (lifecycle cases), `test_e2e.py` (scan -> record).
- Depends on: AMW-8 (coverage fields).

**AMW-10 - Paper forward tests** (M)
- Scope: `backend/app/signals/forward.py`. Each qualified record is enrolled once; the worker marks it on closed bars:
  entry at the next bar's open, stop first when one bar touches both, gap exits at the open, max bars per timeframe,
  R gross and net of the C2 default costs; stats per producer version with "insufficient evidence" below 30. A
  simulation: never a PAPER order, never the PAPER account.
- Flag: `paper_forward_tests`. Migration: yes (`forward_tests`; unique (`user_id`, `record_id`)).
- Routes: `GET /api/forward-tests`, `GET /api/forward-tests/stats` - "member: the caller's own rows".
- Frontend: Journal page tab "Forward tests".
- Tests to adapt: `test_v25.py` (forward-test cases, no look-ahead entry).
- Depends on: AMW-9.

**AMW-11 - Rules-only Chart Analyst** (M)
- Scope: `backend/app/ai/chart_analyst.py`: evidence (regime, structure, zones, patterns, records) -> numbered rule
  sentences (setups, rejected setups and why, timeframe alignment or conflict, invalidation levels, "no setup is a
  valid outcome"). No model call in this slice; every number comes from the engine.
- Flag: `chart_analyst`. Migration: no.
- Routes: `POST /api/ai/chart-analyst` - "self: a read computed for the caller; stores nothing".
- Frontend: Chart workspace side panel "Analysis"; link from Signals.
- Tests to adapt: `test_platform.py` (analyst grounding cases, re-aimed at the rule text).
- Depends on: AMW-3, AMW-4 (soft), AMW-9 (soft).

**AMW-12 - BYOK routing, safeguards and usage** (M)
- Scope: per-user AI policy: default / manual / task routing (`vision`, `dual_review`, `screen_nl` later), fallback
  `none` / `ask` / `auto` (only to the user's own other key), per-task caps (calls, retries, output tokens, estimated
  cost), confirmation thresholds; usage view from `llm_calls` (provider, model, task, tokens, estimated cost, status).
- Flag: `byok_routing`. Migration: yes (`user_ai_policy`, one row per user; additive).
- Routes: `GET/PUT /api/ai/byok/policy`, `GET /api/ai/byok/usage` - "self: the caller's own AI key, prefs and usage"
  (`S_OWN_AI_KEY`).
- Frontend: Settings -> `VisionByokCard` becomes "AI keys and usage" (tabs: keys, routing, safeguards, usage).
- Tests to adapt: `test_ai_byok.py` (routing, fallback, safeguards, no company key, usage).
- Depends on: nothing (BYOK-V1 merged).

**AMW-13 - Dual Review** (M)
- Scope: the same chart PNG and engine context sent to two providers on the user's two keys; each reply parsed into
  the Vision verdict structure; side by side with an agreement flag. Explicit per-request confirmation that both
  providers bill the user. No free text.
- Flag: `byok_dual_review` (needs `vision_byok`, `byok_routing`).
- Migration: no (a new `subject` value on `vision_reports`; if a column is needed, nullable and additive).
- Routes: `POST /api/ai/byok/dual-review` - `S_OWN_AI_KEY`.
- Frontend: "Dual review" button in the Analysis panel.
- Tests to adapt: `test_ai_byok.py` (dual review cases).
- Depends on: AMW-11, AMW-12, BYOK-V3 (#217).

**AMW-14 - Report Center** (L)
- Scope: `backend/app/reports/*`: per-record reports (JSON, CSV, PNG via the existing renderer, XLSX via a small
  standard-library writer; PDF after AMW-Q4), chart cut at the record's data bar, "snapshot match / different" from the
  stored SHA-256; research reports (backtest, forward tests, screen run) from stored results; owner-only; retention and
  quota per plan; CSV export of screen runs.
- Flag: `report_center`. Migration: yes (`reports`: owner, kind, format, subject, status, size, sha256, snapshot
  match, blob or object key, expiry).
- Routes: `POST /api/reports` (self), `GET /api/reports` (member: own), `GET /api/reports/{id}/download` (member: own).
- Frontend: new page "Reports" (`/reports`); "Report" buttons on Signal records, Backtest and Screener run.
- Tests to adapt: `test_reports.py`, `test_reports_notify_v2.py` (report parts).
- Depends on: AMW-9, AMW-1; AMW-10 for the forward-test report.

**AMW-15 - Report schedules and lifecycle notifications** (M)
- Scope: schedules (daily at HH:MM IST or every N minutes in session, trading days only, one run per slot, skipped
  while the previous run is active) that run a screen and send the top-N reports to the user's linked Telegram through
  the outbox; notification kinds `signal_qualified`, `signal_invalidated`, `signal_completed`, `signal_expired`,
  `forward_closed`, `daily_summary`, `backtest_done` with per-kind channel choice. Runs in the worker tick (enqueue
  only).
- Flag: `report_schedules`. Migration: yes (`report_schedules`; notification preferences, additive).
- Routes: `GET/POST /api/report-schedules`, `DELETE /api/report-schedules/{id}` (self); `GET/PUT
  /api/notifications/preferences` (self).
- Frontend: Notifications page "Schedules" and "What to tell me" sections.
- Tests to adapt: `test_saas_sched.py` (scheduler cases with a fake clock), `test_reports_notify_v2.py` (notification
  cases), `test_notify.py` (canonical message content).
- Depends on: AMW-14, AMW-10.

**AMW-16 - Data health and market overview cards** (S)
- Scope: admin data-health summary (quality events by kind and source, freshness per source, broker sessions healthy /
  expired by count, last screen runs with data problems); a member view of the caller's own sessions and feeds; a
  dashboard "Market overview" card (breadth and sectors of the user's last screen run, data freshness).
- Flag: `data_health`. Migration: no.
- Routes: `GET /api/admin/data-health` (admin), `GET /api/data-health` (member: own sessions only).
- Frontend: `AdminPage.tsx` card "Data health"; `DashboardPage.tsx` card "Market overview".
- Tests to adapt: none direct in AMW (UI smoke only); new tenancy and aggregation tests.
- Depends on: nothing.

**AMW-17 - FYERS live 1-minute feed** (L)
- Scope: per-user collector in the worker: about 5 s after each minute closes, today's 1-minute history for up to N
  symbols through the user's own Fyers credential; completed and valid bars only; upsert with versions into
  `md_tenant_candles` (`broker:fyers:u<id>`, `holder_visible`); no forward fill; statuses (waiting, running, degraded,
  market closed, token expired, rate-limit guard, stopped); a lease row per (user, broker) with heartbeat for restarts;
  redirect-based daily login with one-time hashed state, through the Settings broker card; SSE bar stream.
- Flag: `fyers_minute_feed` (needs `byob_data_tier`; `set_flag` refuses while the Fyers terms are not `allowed`).
- Migration: yes (`feed_leases`: user, broker, symbols, status, heartbeat, last error).
- Routes: `POST /api/feed/start`, `POST /api/feed/stop` (self), `GET /api/feed/status`, `GET /api/feed/bars`,
  `GET /api/feed/stream` (member: own), Fyers OAuth callback (`P_OAUTH_CALLBACK`).
- Frontend: Settings -> Broker accounts "Live 1-minute feed" panel; charts read live bars; `DataSourceBar` shows the
  source.
- Tests to adapt: `test_feed.py` (all 27, mocked Fyers).
- Depends on: owner consent (AMW-Q5), BT1, HB2.

### 4.2 Strategy Lab group

**AMW-18 - ADR-0027 and threat model** (S)
- Scope: docs only: ADR-0027, the section 3 design as `docs/design/STRATEGY_LAB.md`, the gate G-LAB checklist.
- Flag: none. Migration: no. Routes: none. Frontend: none. Tests: `test_adr_index.py` updated.
- Depends on: owner approval of this plan.

**AMW-19 - Lab core** (L)
- Scope: `backend/app/lab/{contract,context,service,routes}.py`: tables and rules of section 3.2, the result
  contract, the host context builder, draft / publish / archive, the runs list, templates catalogue, the research
  worker draining `lab_runs` (no evaluator yet).
- Flag: `strategy_lab`. Migration: yes (section 3.2).
- Routes: `GET/POST /api/lab/strategies`, `GET/PUT /api/lab/strategies/{id}`, `POST /api/lab/strategies/{id}/archive`,
  `POST /api/lab/strategies/{id}/publish` (trader, scope `strategies:write`), `GET /api/lab/strategies/{id}/versions`,
  `GET /api/lab/runs`, `GET /api/lab/runs/{id}` (member: own), `GET /api/lab/templates` (member).
- Frontend: new page "Strategy Lab" (`/strategy-lab`, Trade group); the Copilot tab label changes (AMW-Q2).
- Tests to adapt: `test_lab_release.py` (workflow, versions immutable, drafts never execute), `test_lab.py` (contract
  and schema cases).
- Depends on: AMW-18, AMW-1.

**AMW-20 - Host modes: visual and Pine subset compiled to ScreenQL** (L)
- Scope: the envelope, `backend/app/lab/pine.py` translator, visual mode on the Screener builder, evaluation by the
  ScreenQL runtime, `LabStrategy(BaseStrategy)` adapter for backtests, Lab scans (published versions, plan symbol
  cap), Pine (6) and visual (8) templates. A host-mode version can be saved as a screen.
- Flag: `strategy_lab_host`. Migration: no.
- Routes: `POST /api/lab/validate` (self, stores nothing), `POST /api/lab/strategies/{id}/try` (self, <= 5 symbols),
  `POST /api/lab/strategies/{id}/scans`, `POST /api/lab/strategies/{id}/backtests` (self, queued).
- Frontend: Strategy Lab editor (mode picker, Pine text area with diagnostics, visual builder, envelope panel,
  results, backtest view reusing `BacktestPage` components).
- Tests to adapt: `test_lab.py` (Pine and visual cases, cross-mode consistency, look-ahead prevention), new translator
  round-trip and diagnostics tests.
- Depends on: AMW-19, AMW-3.

**AMW-21 - Sandbox runner, self-test and capabilities (no user code)** (L)
- Scope: `lab_runner/` (Dockerfiles per family, jail configs, seccomp policy, the runner's UDS server, worker scripts),
  `backend/app/lab/dispatch.py`, compose profile `lab`, the probe suite and self-test storage, the capability matrix.
  Only the built-in probe executes.
- Flag: none user-facing (the runner is off unless the profile runs; capabilities show "unavailable").
- Migration: no (self-test results in a platform control).
- Routes: `GET /api/lab/capabilities` (member), `GET /api/admin/lab/sandbox` (admin), `POST /api/admin/lab/sandbox/selftest`
  (admin).
- Frontend: capability table on the Strategy Lab page; Admin card "Lab sandbox".
- Tests to adapt: `test_lab.py` (sandbox security cases, re-written for the new runner and extended per 3.6 item 4).
- Depends on: AMW-19, AMW-Q3.

**Gate G-LAB - security review.** The checklist in section 3.6 is completed, the independent and owner sign-offs are
recorded, and the approval record exists per family. No code-mode flag can be turned on before this; `set_flag`
enforces it. Each later family (AMW-23..25) repeats items 3, 4 (its language), 10, 11 and 14 before its own flag.

**AMW-22 - Python mode and the shared code-mode path** (M)
- Scope: Python worker in `atp-lab-py` (`atp.*` helpers, restricted builtins, import allow-list, audit hook); the
  code-mode try, scan and backtest path (batched contexts per bar, one runner call per batch chunk); templates (6).
- Flag: `strategy_lab_python`. Migration: no. Routes: none new (the AMW-20 routes accept the mode).
- Frontend: Python editor (plain text area with line numbers, AMW-Q11), quick reference.
- Tests to adapt: `test_lab.py` (Python cases, escape attempts, cancellation, timeouts).
- Depends on: AMW-21, G-LAB.

**AMW-23 - SQL and Lua modes** (M)
- Scope: SQL (sqlglot check and SQLite authorizer inside the runner, views `bars` and `latest`, `:name` parameters,
  per-symbol evaluation) and Lua (lupa, limits) in `atp-lab-py`; templates.
- Flag: `strategy_lab_sql_lua`. Migration: no. Routes: none new.
- Frontend: editors and references for both.
- Tests to adapt: `test_lab.py` (SQL and Lua cases).
- Depends on: AMW-22, family review.

**AMW-24 - JavaScript and TypeScript modes** (M)
- Scope: `atp-lab-node` image; `vm` realm worker; TypeScript type check against the SDK; frontend copy of the SDK with
  the hash test; templates.
- Flag: `strategy_lab_js`. Migration: no. Routes: none new.
- Frontend: editors; TypeScript errors with line numbers.
- Tests to adapt: `test_lab.py` (JS / TS cases), `test_lab_release.py` (runtime detection).
- Depends on: AMW-22, family review.

**AMW-25 - R mode** (M)
- Scope: `atp-lab-r` image; R worker with a curated environment and JSON contexts; templates (2); concurrency 1.
- Flag: `strategy_lab_r`. Migration: no. Routes: none new.
- Frontend: editor and reference.
- Tests to adapt: `test_lab.py` (R cases, incl. the aliasing bypass).
- Depends on: AMW-22, family review, AMW-Q6.

### 4.3 Summary

| Slice | Title | Flag | Migration | Size | Depends on |
|---|---|---|---|---|---|
| AMW-1 | Plan limits v2 | `plan_limits_v2` | no | S | - |
| AMW-2 | Watchlists | `watchlists` | yes | S | AMW-1 |
| AMW-3 | ScreenQL functions for AMW predicates | `screenql_amw_functions` | no | M | - |
| AMW-4 | Chart patterns | `chart_patterns` | no | M | - |
| AMW-5 | Screen templates | `screener_templates_amw` | no | S | AMW-3, AMW-4 |
| AMW-6 | Deterministic NL to ScreenQL | `screener_nl` | no | M | AMW-3, AMW-5 |
| AMW-7 | Immutable screen versions + compare | `screener_versions` | yes | M | S6b |
| AMW-8 | Score coverage + mandatory gate | `signal_score_coverage` | no | S | - |
| AMW-9 | Signal ledger + lifecycle | `signal_ledger` | yes | L | AMW-8 |
| AMW-10 | Paper forward tests | `paper_forward_tests` | yes | M | AMW-9 |
| AMW-11 | Rules-only Chart Analyst | `chart_analyst` | no | M | AMW-3, AMW-4, AMW-9 (soft) |
| AMW-12 | BYOK routing, safeguards, usage | `byok_routing` | yes | M | - |
| AMW-13 | Dual Review (structured) | `byok_dual_review` | no | M | AMW-11, AMW-12, #217 |
| AMW-14 | Report Center | `report_center` | yes | L | AMW-9, AMW-1 |
| AMW-15 | Report schedules + lifecycle notifications | `report_schedules` | yes | M | AMW-14, AMW-10 |
| AMW-16 | Data health + market overview cards | `data_health` | no | S | - |
| AMW-17 | FYERS live 1-minute feed | `fyers_minute_feed` | yes | L | consent, BT1, HB2 |
| AMW-18 | ADR-0027 + threat model | - | no | S | owner approval |
| AMW-19 | Lab core | `strategy_lab` | yes | L | AMW-18, AMW-1 |
| AMW-20 | Host modes (visual, Pine subset) | `strategy_lab_host` | no | L | AMW-19, AMW-3 |
| AMW-21 | Sandbox runner + self-test | - | no | L | AMW-19 |
| G-LAB | Security review gate | - | - | - | AMW-21 |
| AMW-22 | Python mode + code path | `strategy_lab_python` | no | M | G-LAB |
| AMW-23 | SQL + Lua | `strategy_lab_sql_lua` | no | M | AMW-22 + review |
| AMW-24 | JavaScript + TypeScript | `strategy_lab_js` | no | M | AMW-22 + review |
| AMW-25 | R | `strategy_lab_r` | no | M | AMW-22 + review, AMW-Q6 |

AMW tests not ported: `test_ui.py` (Streamlit smoke; replaced by vitest and Playwright), `test_failover.py` (no
vendor failover in ATP), `test_data.py` and `test_instruments.py` (ATP's lake and instrument tests are stronger).

---

## 5. Dashboard mapping

Navigation is `frontend/src/components/Sidebar.tsx` (`NAV_GROUPS`); pages are registered in `frontend/src/routes.ts`.
A page or card whose flag is off is hidden through `GET /api/system/features`; a route answers 503 with
`X-Feature-Disabled` when its flag is off.

| Feature | Slice | Where it appears | Route | Nav | Page extended or new |
|---|---|---|---|---|---|
| Plan limits | AMW-1 | Settings -> Billing card | `/settings` | System -> Settings | `BillingCard` (extend) |
| Watchlists | AMW-2 | Screener universe picker; Scanner "Load watchlist"; manage panel | `/screener`, `/scanner` | Trade | `ScreenerPage`, `ScannerPage` (extend) |
| New ScreenQL functions | AMW-3 | Builder palette, text autocomplete | `/screener` | Trade -> Screener | `ScreenerPage` (registry-driven) |
| Chart patterns | AMW-4 | Price-action layer toggle; palette | chart windows, `/screener` | - | `PriceActionLayer` (extend) |
| Templates | AMW-5 | Screen gallery rail and chips | `/screener` | Trade -> Screener | `ScreenGallery` (extend) |
| NL builder | AMW-6 | "Describe it in words" box above the funnel | `/screener` | Trade -> Screener | `ScreenerPage` (extend) |
| Screen versions, compare | AMW-7 | Version drawer, compare view | `/screener` | Trade -> Screener | `ScreenerPage` (extend) |
| Score coverage | AMW-8 | Signal card | `/signals` | Trade -> Signals | `SignalCard` (extend) |
| Signal records | AMW-9 | "Signal records" tab | `/signals` | Trade -> Signals | `SignalsPage` (extend) |
| Forward tests | AMW-10 | "Forward tests" tab | `/journal` | Portfolio -> Journal & Discipline | `JournalPage` (extend) |
| Chart Analyst | AMW-11 | "Analysis" side panel in chart windows; link from a signal record | chart windows, `/signals` | - | `ChartWorkspace`, `SignalsPage` (extend) |
| BYOK routing and usage | AMW-12 | "AI keys and usage" card | `/settings` | System -> Settings | `VisionByokCard` (extend) |
| Dual Review | AMW-13 | Button in the Analysis panel | chart windows | - | Analysis panel (extend) |
| Report Center | AMW-14 | New page; "Report" buttons on records, backtests, runs | `/reports` | Research -> Reports (new item) | new `ReportsPage` |
| Schedules, notification kinds | AMW-15 | "Schedules" and "What to tell me" sections | `/notifications` | System -> Notifications | `NotificationsPage` (extend) |
| Data health | AMW-16 | Admin card; member's own sessions on Status | `/admin`, `/status` | Platform -> Admin Console | `AdminPage` (extend) |
| Market overview | AMW-16 | Dashboard card | `/` | Overview -> Dashboard | `DashboardPage` (extend) |
| FYERS live feed | AMW-17 | Broker accounts "Live 1-minute feed" panel; live bars on charts; source badge | `/settings`, chart windows | System -> Settings | `BrokerAccountsCard`, `DataSourceBar` (extend) |
| Strategy Lab | AMW-19..25 | New page: list, editor per mode, try, publish, versions, scans, backtests, capability table | `/strategy-lab` | Trade -> Strategy Lab (new item, after Strategy Builder) | new `StrategyLabPage` |
| Lab sandbox status | AMW-21 | Admin card | `/admin` | Platform -> Admin Console | `AdminPage` (extend) |
| Copilot drafts | AMW-19 | Copilot tab relabelled; "Open in Strategy Lab" link | `/copilot/strategy-lab` | Trade -> AI Copilot | `StrategyLabTab` (label only, AMW-Q2) |

`Page` in `Sidebar.tsx` gains `reports` and `strategy-lab`; `routes.ts` gains their lazy loaders; `routes.test.ts`
covers both paths. Every new English string goes through the existing English-only check.

---

## 6. Open questions (provisional decisions; to be copied to `docs/OPEN_QUESTIONS.md`)

| # | Question | Provisional decision + reason | Applies to |
|---|---|---|---|
| AMW-Q1 | The AMW archive has no licence file. May code be copied verbatim? | No: re-implement from behaviour; port tests as specifications; record provenance per slice. | all |
| AMW-Q2 | "Strategy Lab" is already the label of a Copilot tab. | The new page is "Strategy Lab" (`/strategy-lab`); the Copilot tab becomes "AI Drafts" (URL `/copilot/strategy-lab` kept), with an "Open in Strategy Lab" link. | AMW-19 |
| AMW-Q3 | Sandbox runtime on Hostinger KVM 2: gVisor, or nsjail with a custom seccomp profile? | gVisor (`runsc`) for the runner container if it installs and passes the probe suite on the host; otherwise nsjail with a runner-only seccomp profile. Never `CAP_SYS_ADMIN` or `privileged` on any container. | AMW-21 |
| AMW-Q4 | PDF needs a dependency (reportlab or similar). | Ship JSON, CSV, PNG and a standard-library XLSX first; add PDF after the owner accepts the dependency. | AMW-14 |
| AMW-Q5 | Fyers data terms are "pending" (written consent). | Build behind `fyers_minute_feed`; `set_flag` refuses ON until the terms file says `allowed`. | AMW-17 |
| AMW-Q6 | R on the 8 GB host (about 768 MB per run). | R is the last family; concurrency 1; enabled only after measuring, or on a larger host. | AMW-25 |
| AMW-Q7 | Which process dispatches Lab runs? | The research worker drains `lab_runs` and research studies (one job at a time); no new process, so no new memory line. | AMW-19, AMW-21 |
| AMW-Q8 | Forward tests: costs or not? | Report gross R and net R (C2 default model) side by side; both labelled simulations. | AMW-10 |
| AMW-Q9 | May code-mode results drive alerts? | No, research only, until a separate review. Host-mode versions may become screens and alert rules. | AMW-22..25 |
| AMW-Q10 | Which producers write signal records? | `/signal/enrich`, screen alert matches and Lab scans of published versions. | AMW-9 |
| AMW-Q11 | A code editor (CodeMirror) adds bundle weight. | A plain text area with line numbers first; a lazily loaded editor later within the bundle budget. | AMW-20..25 |
| AMW-Q12 | Deterministic NL and S9 (AI screen builder) overlap. | One input box: the deterministic parser without a key, S9 with one; both emit the same AST and show the interpretation before Run. | AMW-6 |
