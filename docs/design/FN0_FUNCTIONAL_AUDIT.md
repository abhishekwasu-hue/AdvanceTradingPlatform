# FN0 — Functional audit of every feature and flag (P2-F; merged with the P2-1 flag audit)

Order: docs/owner-orders/ATP_ORDER_P2F_FUNCTIONAL.md (owner, 25 Oct). Docs only, no code. Audit of `main` at the time
of writing (after #493, #510, #529).

## Summary for the owner (plain words)

**How this was checked.** The production site cannot be reached from the build container, so nothing below says "works
on production". Every status is judged from the code: is the main journey wired from the screen to the server, does it
use the trader's own broker data, are there stubs, and is there an automated test of the journey. The last column of the
table is the one short check that only you can do on the real site.

**The biggest single gap.** All 78 browser tests (`frontend/e2e/*.e2e.ts`) run against a **mocked server**
(`e2e/mockApi.ts`, `page.route`). The staging stack (`.github/workflows/staging-stack.yml`, job `acceptance`) runs the
real server, but without a real broker session. So **no automated test today proves any journey with real broker
data**. FN1 / FN7 must add that proof (a production check list and a recording per feature).

**Counts (37 feature rows):** works end-to-end in code: **9** · partial: **20** · broken (closed on purpose): **1** · hidden-unfinished: **7**.
"Works in code" still needs your production check before it counts as done (FN1).

**The 10 biggest gaps**
1. No journey is proven on real broker data (all e2e mocked) — FN1 / FN7.
2. Most "ready" features are still OFF until the trader pack is switched on ("Enable trader features", P1-3 waiting) —
   the screener, paper account, watchlists, live prices (`data_hub`), smart execution are invisible to users today.
3. Strategy builder conditions are **AND only** — no OR groups, no custom indicators, no n-bars-ago (FN4).
4. **No custom-indicator library** (named formulas you save and reuse in screens and strategies) — FN3.
5. Screener is missing **Aroon, Ichimoku, abs()**; no **scheduled run** of a saved screen outside alert rules; no
   "**add results to watchlist**" from the results table (FN2).
6. Screens cannot run on the user's **own watchlists** as a universe (only the system lists; the Custom scan page can
   load a watchlist) — FN2.
7. AI call log keeps **calls, tokens and cost but not the inputs and outputs** the order asks for (FN5).
8. The Terminal (going ON by #533) still shows two **"Coming in a later part"** panels (bots, payoff) — R6 says nothing
   half-built stays visible (FN7 / T2).
9. **Static IP per user's broker app**: only one server IP is checked in the go-live checklist; no per-user binding
   (FN6, needed before LIVE).
10. Developer-style pages are visible to traders: **Factor Lab** (takes raw candles), **Contract specs**, **Activity log**
    — candidates for "remove from view" or Advanced/admin (R6, CLAUDE.md §2).

**Your decisions (defaults in brackets):** the 13 `decision` flags stay OFF [yes]; Factor Lab and Contract specs move
to Advanced [yes]; the Terminal's two placeholder panels are hidden until T2 [yes].

## Legend

- **W** works end-to-end in code (screen → server → broker/stored data, no stubs, a journey e2e spec exists; mocked).
- **P** partial (part of the journey missing, or only sample / mocked data, or no journey test).
- **B** broken (a known failing step).
- **H** hidden-unfinished (behind an OFF flag; not visible to users).
- Flag defaults: `ON`/`off` = code default (`backend/app/platform/controls.py` DEFAULT_ON_FLAGS); **prod** = known
  production state (DECISIONS 21 Oct: `ui_v2` ON; `self_signup`, `scan_auto_paper`, `marketplace`, `public_api` OFF);
  **pack** = turned ON by "Enable trader features" (`backend/app/platform/trader_pack.py` PACK) — not yet pressed (P1-3).
- Catalog status (`backend/app/platform/flag_catalog.py`): ready / unfinished / decision / behind_the_scenes (bts).

## The table (every user-facing feature; every flag appears once)

| # | Feature | Page / route | Flag(s) (default; catalog) | Status | Evidence | Visible today | Fix item | Owner production check |
|---|---|---|---|---|---|---|---|---|
| 1 | Redesigned app shell, navigation, phone bar, colour | all pages | `ui_v2` (off; ready; prod ON) · `colour_v2` (ON; ready) | W | shell/navModel.ts, AppShellV2.tsx; e2e shell-v2, colour-v2 | yes | FN1 | open on phone + desktop; every nav entry opens |
| 2 | Today (dashboard), market context | `/` | `context_panel` (off; ready; pack) | W | dashboard/; e2e dashboard-v2, d1-today-v2 | yes (context card only with pack) | FN1 | Today shows your own broker's index prices, not "–" |
| 3 | Daily rhythm (brief, pulse, wrap, weekly) | Today card + Telegram | `daily_rhythm` (off; unfinished; pack) | H | backend/app/rhythm; e2e daily-rhythm-v2 | no | P2-4 → FN7 | after ON: a brief arrives on Telegram before 09:15 |
| 4 | Broker connect + daily renew | `/broker` | `broker_app_links` (off; decision) · `byob_data_tier` (off; decision) | W | brokers/connect.py, token_lifecycle.py; e2e broker | yes | FN1, FN6 | connect Upstox, renew next morning, status turns green |
| 5 | Charts (core, scroll / Auto / Reset / Latest), multi-chart, studies, zones, own trades | `/charts` | `chart_workspace`, `chart_price_action`, `chart_trades_layer`, `chart_studies`, `market_zones`, `decision_tools` (all off; ready; pack) | P | chart/ChartsPageV2.tsx; e2e charts-v2, chart-axes-v2. P1-4 open: not every chart page on the core yet | yes (extras only with pack) | P1-4, FN1 | NIFTY 5m chart loads from your broker and scrolls both axes |
| 6 | Contract charts with OI, futures basis | Charts › contract | `chart_contracts`, `chart_futures_basis` (off; unfinished) | H | need the nightly download (`history_backfill`) | no | DATA-H → FN7 | after the download: an option contract chart shows OI |
| 7 | Live prices everywhere (data hub) | all price cells | `data_hub` (off; unfinished; pack) · `alerts_stale_guard`, `alerts_reliable_outbox` (off; bts; pack) | P | live/liveTicks.ts; e2e live-prices-v2; P1-5 waits for the owner | no | P1-5 | prices tick during market hours; API-call count drops |
| 8 | Watchlists (system + own lists) | Charts › watchlist panel | `watchlists` (off; ready; pack) | P | watchlists/system.py, chart/WatchlistPanel.tsx; no own page or phone-bar entry (P1-8); lists empty until the universe sync runs | no | P1-8 | after sync: NIFTY 50 list filled; add a symbol to your list |
| 9 | Option chain, Analyses, Charts tab, Ask AI | `/option-chain` | `chain_history` (off; bts; pack) | P | optionChain/; e2e option-chain-v2. Spot card read the chain snapshot, not the chart's feed → fixed in #537 (open) | yes | #537, FN1 | spot on chain = spot on chart, same "as of" time |
| 10 | F&O workspace: chain ticket, basket, symbol hub, futures, insights | `/option-chain`, hub tabs | `options_builder` (ON; ready) · `options_builder_saved` (off; ready; pack) · `smart_execution` (off; ready; pack) | W | fno/; e2e chain-ticket-v2, basket-v2, fno-workspace-v2, hub-futures-v2, fno8b-slice2/3/4-v2, tt8-basket-group-v2 | yes (save + smart exec with pack) | P1-7, FN1 | place a paper iron condor; hedge legs fill first |
| 11 | Plan an options trade (payoff) | `/option-chain/plan` | (same `options_builder`) | W | optionsBuilder/; e2e optionsBuilder | yes | FN1 | payoff shows max loss for a spread on today's chain |
| 12 | Terminal | `/terminal` | `terminal` (off; unfinished; #533 makes it ON + ready) | P | terminal/; e2e terminal-v2. Panels "bots" and "payoff" say "Coming in a later part" (PanelsV2.tsx:456) | no (yes after #533) | T2, FN7 | hide or finish the two placeholder panels before ON |
| 13 | Combined exits (CX) on positions | My trades, Terminal | — | P | execution/group_exits.py, fno_routes group-exits; sheet + chip + "Exit short now" button are CX PR 3 (not built) | API only | P1-6c | after PR 3: set a ₹ stop on a spread; it fires in paper |
| 14 | Sectors & indices | `/markets` | `markets_view` (off; unfinished; pack) | H | markets/; e2e markets-v2; needs the daily market data update | no | P2-x → FN7 | after ON: sector heat map shows today's closes |
| 15 | Find setups — core screener (builder, formula blocks, run, venues) | `/screener` | `screener_v2`, `screener_builder_u2`, `screenql_amw_functions`, `screener_intrabar` (off; ready; pack) · `screener_vectorized`, `screener_venues` (off; bts; pack) | P | screener/routes.py (/validate, /run, /screens); e2e d1-find-setups-v2, screener-versions-v2, scan-universe-v2. Gaps in FN2 section | no | FN2 | run "RSI(14) < 30" on NIFTY 50 with your broker; results in < 1 min |
| 16 | Screen library: gallery, legends, breakouts | `/screener` | `screener_gallery`, `screener_legends`, `screener_legends_lg2`, `screener_breakouts`, `screener_breakouts_bk3`, `screener_breakouts_bk4`, `screener_breakouts_fitness` (off; ready; pack) · `screener_legends_lg3` (off; unfinished) | P | screener/library.py, legends.py, breakouts/; e2e screenerGallery | no | FN2 | open a ready screen, run it, see matches |
| 17 | Screen views, ranking, backtest, history, ETF, confluence | `/screener` | `screener_board_views`, `screener_rankings`, `screener_backtest`, `screener_history`, `screener_tx2`, `screener_etf` (off; ready; pack) | P | screener/board.py, ranking.py, backtest.py, history.py, etf.py; no journey e2e for history / backtest | no | FN2 | "when did this screen hit" for last month returns dates |
| 18 | Screen evidence charts (+ Telegram) | screener matches | `evidence_charts`, `evidence_context` (off; ready; pack) | P | screener/evidence*.py; no journey e2e | no | FN2 | a match shows its chart; the Telegram copy arrives |
| 19 | Screens: fundamentals, ownership, waves | screener fields | `screener_fundamentals`, `screener_ownership`, `screener_waves` (off; unfinished) | H | need fundamentals / ownership data | no | keep OFF | — |
| 20 | Scan alerts, watch-then-trigger, execution queue, auto paper | screener + alerts | `scan_then` (off; ready; pack) · `execution_core` (off; bts; pack) · `scan_auto_paper` (off; decision; prod OFF) | P | screener/then/, models AlertRuleRecord (cooldown, digest); no "only when the result changes" rule | no | FN2 | an armed screen alert reaches Telegram once per new match |
| 21 | Screens from the Copilot / plain words | screener + Copilot | `screener_copilot` (off; ready; pack) | P | screener/routes.py /copilot/nl, /copilot/idea; e2e screener-idea-v2 | no | FN5 | type "stocks above 200 EMA", review, save |
| 22 | Custom scan (older scanner) | `/screener/custom` | — | P | scanner/; only screenshot captures (ux-audit-capture); overlaps Find setups | yes | FN2 (merge or remove from view) | decide: keep one scanner |
| 23 | Strategy Lab (Pine subset) | Lab | `strategy_lab_pine` (off; unfinished; pack) | H | lab/pine/ (translator, never executes; MAX_DEPTH 40, MAX_OPERATORS 200) | no | FN3 | — |
| 24 | Signals (approve / reject), structure-only words, Telegram buttons | `/signals` | `signal_language_v2`, `approval_only_entries`, `telegram_inbound` (off; ready; pack) | W | signals/, signals_feed/; e2e signals-v2, signals-day-one-v2, d1-signals-bot-v2 | yes (approval + Telegram with pack) | FN1 | a paper signal arrives; Approve from Telegram |
| 25 | Paper bot, paper account, ladder, risk, VIX gate, portfolio caps | `/deployments`, `/risk-management` | `paper_account`, `risk_by_default`, `vix_gate`, `ladder_evidence`, `experience_levels`, `portfolio_risk`, `portfolio_risk_caps` (off; ready; pack) | W | deployments/, paper/, risk_engine/; e2e paper-bot-v2, risk-v2 | yes (account + caps with pack) | FN1 | start a paper bot on NIFTY; first entry within the session |
| 26 | My trades, orders, money | `/positions`, `/positions/orders`, `/positions/money` | — | W | trading/PositionsV2.tsx; e2e positions-v2, portfolio-v2 | yes | FN1 | P&L on each card matches your paper fills |
| 27 | Performance, journal, coach | `/analytics`, `/analytics/journal`, `/analytics/coach` | `discipline_engine` (off; ready; pack) | P | journal/, analytics; no journey e2e for journal or coach | yes (journal with pack) | FN1 | after 5 paper trades: weekly score shows |
| 28 | Strategies: build, test (backtest), optimizer, cost models, stored history | `/strategies`, `/strategies/build`, `/strategies/test` | `backtest_optimizer` (ON; ready) · `backtest_models` (off; ready; pack) · `backtest_stored_history` (off; decision) | P | strategy_engine/declarative.py (AND only), backtest/engine.py (win rate, drawdown, expectancy); e2e d1-template-backtest-v2. Gaps in FN4 section | yes | FN4 | build "EMA20 crosses EMA50", backtest 1 year with costs |
| 29 | Strategy templates + idea library | `/strategies`, `/ideas` | `strategy_templates`, `idea_library` (off; unfinished; pack) | H | strategy_templates/, idea_library/; e2e strategy-templates-v2, idea-library-v2, d1-adopt-form-v2 | no | P2-3 → FN4 | adopt a template, get a paper signal |
| 30 | AI Copilot (chat, tools, multiview, agent, research, thesis, one-key connect) | `/copilot`, Settings › AI | `ai_copilot` (ON; ready) · `ai_agent`, `ai_multiview`, `ai_one_key` (off; ready; pack) · `ai_research`, `market_thesis` (off; unfinished) · `thesis_stock_targets` (off; decision) | P | ai/; e2e copilot*, connect-ai-v2, key-guides-v2. Gaps in FN5 section | yes | P2-2, FN5 | add a Gemini key, Test, ask "what is NIFTY's trend" |
| 31 | Analyze a chart (vision), own-key vision | `/chart-analyze` | `chart_analyze`, `chart_analyze_strikes`, `chart_analyze_share` (off; unfinished) · `vision_byok` (off; ready; pack) · `vision_byok_v3` (off; unfinished) | H | chart_analyze/; e2e chart-analyze-v2, chart-analyze-engine-v2; engine "not installed" state | no | P2-5 (P5-1 for the reader) | — (stays hidden with an honest status) |
| 32 | News & events | `/news-events` | `news_feed` (off; ready; pack) | P | news_events/ (manual entries + public feed rows); no journey e2e | yes | FN1 | headlines appear, marked unverified |
| 33 | Company research | `/fundamentals` | — | P | fundamentals/providers/nse.py only; no journey e2e | yes | FN1 | open RELIANCE: figures shown with a date |
| 34 | Factor Lab, Contract specs | `/quant`, `/instruments` | — | P | quant/routes.py takes raw candles (developer tool); instruments = reference table | yes | remove from view (Advanced / admin) | — |
| 35 | Settings (AI, alerts, appearance, help, advanced, trading defaults, setup), Notifications, Team, Account, Activity log | `/settings/*`, `/notifications`, `/team`, `/system-logs` | `public_api` (ON; ready; prod OFF) · `help_centre`, `mobile_push`, `alerts_whatsapp_dlt`, `support_bot` (off; unfinished) · `growth_v2` (off; unfinished) · `referrals`, `plan_limits_v2` (off; decision) | W | settings/; e2e settings-index-v2, trading-defaults-v2, setup-v2, text-size-v2, tours-v2, support-desk-v2 | yes | FN1 | rename the organisation; set Telegram; test alert arrives |
| 36 | Public site, sign-up, legal, learn, status page, onboarding e-mails | public pages, `/learn` | `self_signup` (ON; decision; prod OFF) · `beta_cohort_gate`, `legal_pages` (off; decision) · `marketing_site`, `onboarding_emails`, `learn` (off; unfinished) · `public_status_page` (off; ready) | P | site/, legal/, learn/; e2e front-page, d1-before-login-v2, learn-growth-v2 | front page only | Phase 5 | — |
| 37 | Marketplace; LIVE trading | `/marketplace`; LIVE bots | `marketplace` (ON; decision; prod OFF) · `marketplace_ai_listings` (off; decision) · `live_trading` (ON; decision; Phase 0 seeds OFF) · `phase0_paper_only` (off; bts; Phase 0 seeds ON) | B* | *intentionally closed: marketplace is the last phase; LIVE is the owner's switch (FN6 note first) | no | Phase 5, FN6 | — |
| — | Admin console (system, flags, trader pack, users, support desk, view as user, diagnostics) | `/admin` (SUPER_ADMIN) | history jobs: `history_import`, `history_intraday` (off; bts) · `history_backfill` (off; bts; pack) | W | admin/; e2e admin-console-v2, view-as-v2, ops-alerts-v2 | admin only | FN1 | "Enable trader features" once; flag list shows ON |

Row 37 is counted as "broken" only in the sense "closed on purpose"; it is not a defect. Admin is not counted.

## FN2 — Indicator screener: order list vs code

Source: `backend/app/screener/registry.py` (function specs), `nodes.py:14` (timeframes, operators), `parser.py:31`
(keywords), `universes.py`, `routes.py`, `db/models.py` (ScreenRecord, AlertRuleRecord).

| Asked | Exists? | Where / note |
|---|---|---|
| SMA, EMA, WMA (+ HMA, MA) | yes | registry `SMA`, `EMA`, `WMA`, `HMA`, `MA` |
| RSI, MACD (line / signal / hist) | yes | `RSI`, `MACD`, `MACDSignal`, `MACDHist` |
| Bollinger | yes | `BBUpper`, `BBMid`, `BBLower` |
| SuperTrend, ATR, VWAP | yes | `Supertrend`, `SupertrendDir`, `ATR`, `VWAP`, `SessionVWAP` |
| Stochastic, ADX | yes | `StochK`, `StochD`, `ADX`, `PlusDI`, `MinusDI` |
| **Aroon** | **missing** | only named in ai/strategy_builder.py:90 as unsupported |
| **Ichimoku** | **missing** | same |
| Volume | yes | field `volume`, `RelVolume`, `RelVolumeTOD`, `VolumeBreakout` |
| F&O: OI, change in OI, PCR | partial | field `oi` (+ `PctChange`/offset for change), `PCR`, `ChainBias`, `MaxPainDistancePct`; no direct `OIChange` |
| Timeframes 1m → monthly | yes | 1m 3m 5m 15m 30m 1h 1d 1w 1M (`nodes.py:14`) |
| > < ≥ ≤ = | yes | `COMPARE_OPS` incl. `!=`, plus `BETWEEN`, `IN` |
| crosses above / below | yes | `CrossAbove`, `CrossBelow` |
| n bars ago | yes | `x[n]` offsets (no look-ahead), `Lag` |
| AND / OR groups | yes | keywords AND OR NOT with brackets |
| Multi-timeframe in one scan | yes | `expr@1d` on a finer base (`validator.py:83-91`) |
| Math: **abs**, min, max, % | partial | `Max`, `Min`, `Greatest`, `Least`, `PctChange`, `+ - * /`; **no `abs`** |
| Universes: NIFTY 50/100/500, F&O, indices, sectors | yes (after the universe sync) | `universes.py` from `app/watchlists/system.py` |
| Universe: **the user's own watchlists** | **missing in Find setups** | the Custom scan page loads one (AMW-2); `/api/screener/universes` lists system lists only |
| Run now | yes | `POST /api/screener/run` |
| **Run on a schedule** | partial | only as an alert rule (AlertRuleRecord: instant / digest hourly / eod); no scheduled run with a stored result |
| Results table: sort, open chart | yes | screener results + evidence charts |
| Results: **add to watchlist** | **missing** | only from the chain action sheet (fno/ChainActionSheetV2.tsx) |
| Save private / duplicate | yes | `/screens` (tenant-scoped), versions; duplicate via versions UI |
| Predefined library | yes | gallery, legends, breakouts (flags) |
| Alerts: screen, Telegram, webhook | yes | alerts/channels.py (incl. a Chartink-shaped webhook body) |
| Alert **only when the result changes** | **missing** | rules fire per match with cooldown; no "set changed" diff |
| Scan history for a past period | yes | `screener_history` (`history.py`, `history_routes.py`) |

## FN3 — Custom indicators

- **Exists:** ScreenQL formulas (any expression of the registry functions, offsets, timeframes) with plain validation
  errors (`validator.py`, cost cap 200 per run, `MAX_WINDOW` 500); formula blocks in the builder (`screener_builder_u2`);
  screen formulas drawn on the chart (`chart_studies`); the Pine subset translator (`lab/pine/`, never executes,
  MAX_DEPTH 40, MAX_OPERATORS 200). This is already a safe sandbox: no user code runs, only the parsed grammar.
- **Missing:** a **named, saved custom indicator** with parameters (e.g. `MyBand(len)`), a personal library of them, a
  reference to one from another formula, the strategy builder using it, and a chart preview of a single indicator line.
  The strategy builder (`declarative.py`) cannot read ScreenQL at all.

## FN4 — Indicator-based algo strategies

| Asked | Exists? | Where / note |
|---|---|---|
| Builder, entry + exit conditions | partial | `strategy_engine/declarative.py`: **AND-only** conditions, indicator vs value / indicator / crossover, separate long / short |
| OR groups, multi-TF | partial | higher-timeframe filter per operand yes; **OR no** |
| Built-in + custom indicators | partial | EMA, SMA, RSI, ADX, VWAP, session levels, Bollinger, volume; **not** the full screener registry; no custom indicators |
| Index options, strike choice ATM ± n, premium, delta; spreads | yes | `deployments/routes.py:103-110` (strike_rule, offset ≤ 10, spread_width), `instruments/strike_selection.py` (`target_delta`) |
| Size from user settings | yes | trading defaults + risk_by_default |
| SL / target / trailing via SX | **not yet** | SX is P1-6d (review only); CX engine exists (`execution/group_exits.py`) |
| Time windows as user settings | partial | deployment holding INTRADAY / SWING; no general window setting |
| ONE engine backtest = paper = live | yes (parity tests) | tests/test_tpl1_parity.py, test_ch3b_trade_parity.py, test_z10a_no_lookahead.py, test_lab_pine_parity.py |
| Backtest charges + slippage | yes | backtest/engine.py (slippage model), `backtest_models` |
| Metrics: trades, win rate, average, expectancy, max DD | yes | backtest/engine.py:216-224 |
| Equity curve, CSV | yes | backtest result curve; exports/routes.py (csv) |
| Paper forward test on live data | yes | paper bot on the user's broker candles |
| Signal + order log | yes | signals, orders, Activity log |
| Several strategies in parallel | yes | deployments |
| Per-strategy + account kill switch | yes | kill_switch/checks.py, routes.py |
| LIVE only behind the owner's switch | yes | `live_trading` + `phase0_paper_only` (platform/phase0_lock.py) |

## FN5 — AI

- **Providers:** own key for anthropic, openai, gemini (`ai/providers.py:784`); one-key connect adds openrouter, groq,
  mistral, zai, cloudflare (`providers.py:831`, flag `ai_one_key`).
- **Test connection:** yes, `POST /api/ai/byok/test/{provider}` (`ai/byok_routes.py:92`); key-format checks
  (`key_formats.py`); e2e connect-ai-v2, key-guides-v2.
- **Copilot jobs:** built (chat, tools, multiview, agent, research loop); proof per job on production is P2-2.
- **Plain words → builder:** strategies yes (`ai/strategy_builder.py` → a DRAFT the user saves; never runs itself);
  screens yes (`/api/screener/copilot/nl`, behind `screener_copilot`).
- **AI explanation on each signal:** partial (Explain on F&O insights; not on every signal card).
- **Call log + limits:** `ai/metering.py` records calls, tokens, USD per tenant and feature (`UsageRecord`) and a monthly
  budget per plan; **inputs and outputs are not stored** (order asks for them; mind PII — `ai/pii.py`).
- **Errors:** provider errors map to plain sentences (`ProviderError`, vision "cannot read charts"); invalid key / quota
  / provider-down wording needs one check per provider on production.
- **ADR-0006 guard:** AI modules never import the order path — tests/test_z10c_order_paths.py,
  test_copilot_own_key_tools.py, test_bt1b_guard_all_reads.py.

## FN6 — SEBI retail algo framework: facts in the code (the note itself is FN6)

| Requirement | In the code today | Gap |
|---|---|---|
| Static IP bound to each user's broker app | go-live checklist item "Server IP registered with each LIVE broker" (`platform/readiness.py:197-205`, `compliance/static_ip`) | one server IP for all users; no per-user record / proof |
| Daily 2FA session + reminder | broker renew flow + banner (`brokers/token_lifecycle.py`, renew on Broker page / Today) | reminder channel (Telegram before open) to confirm |
| ≤ 10 orders / sec | per-broker token buckets (`brokers/rate_budget.py:36-38`: Upstox 8/s, Zerodha 2/s, Shoonya 3/s) | a platform-wide 10/s per user cap test |
| Price-protected market orders | `execution/order_safety.apply_market_protection`, `compliance/order_policy.py` (`map_to_limit`), `ORDER_MARKET_PROTECTION_PCT` | ON for LIVE by config — confirm the value |
| Algo-ID tagging | tenant `algo_id` (team/routes.py), `execution/tagging.py`, order `tag` sent to Upstox / Zerodha | the exchange-issued id must be obtained (owner) |
| White-box registration, empanelment / hosting | — | owner + lawyer (FN6) |

## Proposed build order (max 3 in progress; one PR per item)

1. **FN1** (S): definition of done in WORKING_RULES + `docs/FN1_CHECKLIST.md` per feature; a production check script
   list for the owner. Parallel: **FN6** note (S, docs).
2. **FN2** in 3 PRs: (a) Aroon, Ichimoku, abs, OIChange (M); (b) own watchlists as a universe + "add to watchlist" from
   results (M); (c) scheduled screen runs + alert only when the result set changes (M, migration).
3. **FN3** (L, 2 PRs): named custom indicators library (migration) + use in screens; then chart preview.
4. **FN4** (L, 3 PRs): OR groups + full registry operands via ScreenQL in the strategy builder; SX exits (P1-6d);
   time windows as settings.
5. **FN5** (M, 2 PRs): AI call log with inputs / outputs (redacted), per-user limits screen; Explain on every signal.
6. **FN7** per item as each lands; first: hide Terminal placeholder panels, move Factor Lab / Contract specs to Advanced.

Migrations expected: FN2c, FN3, FN5 (log columns).
