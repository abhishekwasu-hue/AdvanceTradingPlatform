# FN2 gap audit: Indicator screener (Chartink-class)

This is a read-only audit. The scope is docs/ROADMAP.md:117. Paths are relative to /home/user/AdvanceTradingPlatform.

## How flags are reported
- Every screener flag is **OFF by code default**. `DEFAULT_ON_FLAGS` in backend/app/platform/controls.py:636-649 holds no screener flag.
- The trader pack turns these flags on: backend/app/platform/trader_pack.py:63-69 (`screener_v2`, `screener_builder_u2`, `screener_intrabar`, `screener_history`, `screener_backtest`, `screener_rankings`, `evidence_charts`, `scan_then` and others).
- The pack leaves these out: `screener_fundamentals`, `screener_ownership`, `screener_waves` and `screener_legends_lg3` (trader_pack.py:90-97).
- **The production value of each flag is not known** (OWNER_RULES A.2; FN1_CHECKLIST "a flag's code default is not its production value").
- FN1 rows 15-21 are all unticked (docs/FN1_CHECKLIST.md:30-36). FN2 is done only when they are ticked on production.
- "Built behind flag X" below means the code is built, X is off by default, and X is in the trader pack.

## Chartink reference (cited)
- **Timeframes.** Chartink offers 1-minute (premium) and 5-minute (free) intraday candles, plus daily, weekly, monthly, quarterly and yearly. It also has "n candles ago" and intraday `[=1]` / `[=-1]` offsets. Sources: https://chartink.com/articles/scanner/ and https://chartink.com/articles/scanner/scanner-user-guide/
- **Indicators.** SMA, EMA, WMA, TMA, RSI, MACD, ADX, ATR, Bollinger Bands, PSAR, VWAP, Supertrend, Ichimoku, CCI, MFI and Heikin-Ashi. It also has user-made custom indicators (https://chartink.com/indicator). Source: scanner-user-guide above.
- **Math.** `+ - * /`, Min/Max over a period, Abs, Ceil, Floor, Round, square and square root. Source: https://chartink.com/articles/scanner/
- **AND / OR.** Sub-filters with "any" (https://chartink.com/articles/scanner/).
- **Universes.** Segments are cash, F&O stocks (on cash data), indices and a watchlist (watchlist is premium). Source: https://chartink.com/articles/scanner/
- **F&O OI.** Public scans use futures OI, for example https://chartink.com/screener/increase-oi-with-increase-price-confirms-uptrend and https://chartink.com/screener/oi-gainers.
  - The user guide's support answers say only cash and indices are supported, and that the "futures" segment uses cash prices.
  - I found no PCR field.
  - **What Chartink's current OI clause looks like could not be confirmed** (the scan page text was not readable).
- **Alerts.** Alerts are sent by SMS, e-mail, desktop / mobile and webhook.
  - Frequency is 1 / 2 / 3 / 5 / 10 / 15 / 20 / 30 / 60 / 120 / 240 minutes, at market open or at market close.
  - There are settings for minimum matches, unique vs duplicate alerts, and pausing after the first trigger.
  - Sources: https://chartink.com/articles/alerts/alerts-guide/, https://chartink.com/articles/alerts/alerts-faq/ and https://chartink.com/articles/alerts/webhook-support-for-alerts/
- **History.** The alert dashboard lists the triggered stocks with time stamps. A backtest shows the matches per candle, with daily history going back about 9 years. Sources: https://chartink.com/articles/backtest/bactesting-faq/ and https://chartink.com/articles/uncategorized/backtesting-now-available/
- **Library.** Users can browse public scans and "top loved" scans, search them, use "copy scan" to their own dashboard, and keep private scans (premium). Sources: https://chartink.com/articles/scanner/stock-screener-faq/ and https://chartink.com/screeners/search?search_term=nr7+scans
- **Result actions.** Auto-refresh every minute (premium) and a copy-widget for dashboards (https://chartink.com/articles/atlas/feature-copy-widget/).
  - Support answers from 2018-2020 said there was no CSV / Excel export of results and no "results to watchlist". **I could not confirm the current state of either.**

## Table

| # | Capability | Chartink | ATP status, with evidence | Next PR if not done |
|---|---|---|---|---|
| 1 | Indicators | Broad set (see above) plus custom indicators | **Partial: built behind `screener_v2` (+ `screenql_amw_functions`).** The registry has 148 entries. Indicators include SMA, EMA, WMA, HMA, RSI, ADX, ATR, VWAP, MACD, Stoch, PSAR, Supertrend, BB, +/-DI, StDev and ZScore (backend/app/screener/registry.py:279-340). Price-action, breakout and zone functions are also built. **Missing:** CCI, MFI, Williams %R, OBV, Ichimoku, Aroon, Keltner, Stoch RSI, TMA and Heikin-Ashi. backend/app/indicators/*.py has no implementation for any of them. Named user-made custom indicators are also missing; the formula block covers part of this (docs/design/SCREENER.md:846). | FN2-a: add the missing indicators to app/indicators, the registry, the runtime and the causal harness. Size L, no migration, no owner decision. Split into 2 PRs. |
| 2 | F&O OI / PCR | Futures OI in scans; no PCR found | **Partial.** The `oi` field exists (registry.py:89) and is resampled (runtime.py:139). Only the NFO, BFO and MCX venues carry it (backend/app/screener/venues.py:46-48). **The run refuses PCR, ChainBias and MaxPainDistancePct:** `needs=("option_chain",)` is not in `RUN_PROVIDES` (registry.py:428-431; backend/app/screener/routes.py:83, 959-966, 1013-1017). The builder hides them (SCREENER.md:807-810). A stock screen cannot read its own futures' OI. | FN2-e: load the chain snapshot and the near-month future's OI on the run and alert paths. Size L, no migration (snapshots exist). Owner decision: which expiry, and the source (the own broker or stored snapshots). |
| 3 | Timeframes 1m to monthly | 1m-240m, D, W, M, Q, Y | **Built behind `screener_v2`.** The engine has 1m, 3m, 5m, 15m, 30m, 1h, 1d, 1w and 1M (backend/app/screener/nodes.py:13, parser.py:38). The builder has the same list (frontend/src/screener/model.ts:32-37). **Gap:** there are no 2m, 10m, 2h or 4h, quarterly or yearly timeframes. That is beyond the FN2 wording, which says "1m-monthly". | None for FN2. Optional, size S: 2h, 4h and quarterly. Owner decision. |
| 4 | Operators | `> < >= <=`, equals | **Built behind `screener_v2`.** The engine has `> < >= <= == !=`, BETWEEN, IN and NOT (parser.py:31-39; SCREENER.md:203-208). | Done. |
| 5 | Crosses | Crossed above / below | **Built behind `screener_v2`.** CrossAbove and CrossBelow, with unit checks (registry.py:291-292). | Done. |
| 6 | "n bars ago" | "n candles ago" | **Built behind `screener_v2`; the builder needs `screener_builder_u2`.** Text uses `x[n]@tf` and `Lag(x,n)` (registry.py:295; SCREENER.md:205), with offsets capped at 500 and negative offsets refused. In the builder, an offset can only be entered in the formula block (frontend/src/screener/StageRow.tsx:7; SCREENER.md:866-867 "no visual editor for arithmetic and offsets"). | FN2-b: an "n bars ago" control on the indicator block. Size S, no migration, no owner decision. |
| 7 | AND / OR | Sub-filter "any" | **Built behind `screener_v2`.** ALL / ANY / NOT in the AST and text (nodes.py:105, 248). The builder has ANY and NOT groups, which do not nest (SCREENER.md:816-819, 839). | Done. Nested groups are optional. |
| 8 | Multi-timeframe | Each clause can have its own timeframe | **Built behind `screener_v2`.** `@tf` is accepted on every operand. A coarser frame is aligned by its close time, with no look-ahead (SCREENER.md:241-247). The validator refuses a timeframe finer than the screen's base. | Done. |
| 9 | Math | `+ - * /`, Min/Max, Abs, Ceil, Floor, Round, sqrt | **Partial: built behind `screener_v2`.** Built: `+ - * /`, unary minus, Max/Min over n bars, Greatest/Least, PctChange and ZScore (registry.py:285-295). **Missing:** Abs, Ceil, Floor, Round, Sqrt and Pow, which are not in the registry list. Arithmetic is only reachable through the formula block (`screener_builder_u2`). | FN2-c: add Abs, Round, Floor, Ceil, Sqrt and Pow to the registry and runtime, with causal cases. Size S, no migration, no owner decision. |
| 10 | Universes | Cash, F&O, indices, watchlist | **Partial: built behind `screener_v2`.** Index, sector and F&O lists come from system watchlists, with NIFTY 50 as the default (backend/app/screener/universes.py:1-20). A list is cut at `MAX_UNIVERSE = 500` (universes.py:42) and runs in batches of `MAX_SYMBOLS = 50` (routes.py:84). Own watchlists are built behind `watchlists` (frontend/src/pages/ScreenerPage.tsx:966). **Gaps:** there is no "all NSE stocks" universe, because a list over 500 is cut. Sector, IndexMember and IsFnO cannot be used as filters because `needs=("reference",)` is refused (registry.py:432-436; routes.py:1013-1017). | FN2-d: put the reference data on the run path (Sector / Industry / IndexMember / IsFnO from the U1 tables). Size M, no migration (the tables exist), no owner decision. Raising the 500 cap (store-first) is a separate size M PR and needs an owner decision on the API budget. |
| 11 | Schedule | 1-240 min, market open / close | **Partial: built behind `screener_v2`.** A rule runs on every closed bar of its timeframe, or intrabar with `screener_intrabar` (backend/app/alerts/barclose.py:1-30). There is a digest hourly or at end of day (backend/app/alerts/rules_routes.py:46-47), plus cooldown and quiet hours. **Gaps:** weekly and monthly rules are accepted (rules_routes.py:44) but never run (`SUPPORTED = INTRADAY ∪ {1d}`, barclose.py:68). Such a rule shows "no closed 1w bar found" (barclose.py:297), which is open item SC-10. There is no "run at market open / close" choice and no scheduled run that is stored without an alert. | FN2-f: weekly and monthly bar-close in `expected_bar`. Until then, refuse 1w / 1M at rule creation with a plain reason. Size S, no migration, no owner decision. A frequency choice independent of the timeframe would be size M and needs an owner decision. |
| 12 | Results actions | Auto-refresh, copy widget, chart links | **Built behind `screener_v2` (+ others).** CSV export (frontend/src/screener/ResultBoard.tsx:30-46, 86-88). Show on chart, behind `evidence_charts` (ResultBoard.tsx:129). Auto-refresh every minute (ScreenerPage.tsx:435-448). Heatmap and chart grid, behind `screener_board_views` (ResultViews.tsx:31, 83). Telegram copy (backend/app/screener/evidence_routes.py:92). Watch-then-trigger, behind `scan_then` / `screener_tx2` (library_routes.py:285). "Tell me when it matches", behind `screener_copilot` (ScanAlertPanel.tsx:1-7; ScreenerPage.tsx:595). **Missing:** "save matches as a watchlist". | FN2-g: "save matches as a watchlist" on the board. Size S, no migration (the watchlists API exists), no owner decision. A smaller fix: the scan-alert panel should not need `screener_copilot`. That is a flag decision for the owner. |
| 13 | Library | Public scans, search, copy scan, private scans | **Partial: built behind `screener_gallery`, `screener_legends`, `screener_legends_lg2`, `screener_breakouts*` and `screener_tx2`.** Saved screens use create / update / archive (routes.py:351-420). Versions and import (version_routes.py:40-64). Gallery and taxonomy (routes.py:188; frontend/src/screener/ScreenGallery.tsx). Legends cards (routes.py:220-230). Mirrors and confluence (library_routes.py:82-184). **Not built:** sharing or copying between users. This is a public or community library, which is the marketplace and is OFF by owner rule. | None for FN2 beyond the FN1 proof. A community library needs an owner decision and belongs to the marketplace phase. |
| 14 | Alerts | SMS, e-mail, desktop / mobile, webhook; min matches; unique | **Built behind `screener_v2` (+ `screener_intrabar`).** Screen rules and instrument rules (rules_routes.py:37-51, 211-247). Rule events (rules_routes.py:307). Dedupe, throttle and digest (SCREENER.md:302). A webhook in Chartink shape (SCREENER.md:331). Telegram and e-mail links (links_routes.py:46-102). Dead letters and retry (rules_routes.py:351-367). **Gaps:** a screen rule takes 1-50 symbols (rules_routes.py:49, 222). There is no "minimum matches" setting. Phone push is behind `mobile_push`, which is unfinished. | FN2-h: let a screen rule run on a universe key instead of 50 typed symbols, batched through the shared API budget. Size M, migration yes (a universe-key column on the alert rule), no owner decision. "Minimum matches" would add a trading-adjacent setting; it should not be built unless the owner asks (A.5). |
| 15 | Scan history | Alert trigger list; backtest per candle | **Partial.** The backend is built behind `screener_history` and `screener_backtest`: who matched on a date, fitness, and the rolling backtest (backend/app/screener/history_routes.py:258, 297, 424; SCREENER.md:1502, 1560). Rankings are built behind `screener_rankings` (ranking_routes.py:113-187). Alert rule events have a UI in frontend/src/api/client.ts:1199 and frontend/src/components/AlertRulesPanel.tsx. **Missing:** none of `/screener/history/*`, `/screener/rankings` or a list of past runs is called anywhere in frontend/src (a grep finds nothing). Runs are stored (`screen_runs`, backend/app/db/models.py:3597), but only `GET /runs/{id}` exists (routes.py:1233); there is no list endpoint. FN1 row 17 has no spec. | FN2-i: a "Past runs" list (`GET /screener/runs?screen_id=`) and its page panel. Size S, no migration. FN2-j: "When did this screen hit" and backtest panels on the History and Backtest APIs. Size M, no migration. FN2-k: the rankings page. Size M, no migration. None needs an owner decision. |

## Counts
- **Built (code built; the flag is off by default and in the trader pack; production value not known): 7.** These are rows 3, 4, 5, 6, 7, 8 and 12.
  - Row 6 is built only through the text and formula block.
  - Row 12 lacks only "save as watchlist".
  - Row 14, alerts, is built but limited to 50 symbols. It is counted as partial below.
- **Partial: 7.** These are rows 1, 2, 9, 10, 11, 14 and 15.
- **Missing outright: 0.** Library sharing (row 13) is out of scope by owner rule, and row 13 is otherwise built.
- "Built and on" cannot be stated for any row, because no flag is ON by default and production values are not known.

## Ordered PR list (smallest first)
1. FN2-f (S): weekly and monthly bar-close for alert rules. Until then, refuse 1w / 1M at rule creation. This also fixes a silent gap: barclose.py:68 accepts these rules but never evaluates them.
2. FN2-c (S): the math functions Abs, Round, Floor, Ceil, Sqrt and Pow.
3. FN2-b (S): an "n bars ago" control on the indicator block.
4. FN2-g (S): save matches as a watchlist. Optionally decouple the scan-alert panel from `screener_copilot` (owner flag decision).
5. FN2-i (S): the past-runs list endpoint and panel.
6. FN2-d (M): reference data on the run path, so Sector, Industry, IndexMember and IsFnO become filters.
7. FN2-j (M): the screen-history and backtest panels on the existing APIs.
8. FN2-k (M): the rankings page.
9. FN2-h (M, migration yes): an alert rule on a universe key, past 50 symbols.
10. FN2-a (L, 2 PRs): the missing indicators CCI, MFI, Williams %R, OBV, Ichimoku, Aroon, Keltner, Stoch RSI, TMA and Heikin-Ashi.
11. FN2-e (L, owner decision on expiry and source): option-chain PCR and futures OI on the run and alert paths.
12. FN7 proof: tick FN1 rows 15-21 on production. This is the FN2 definition of done.

## Owner decisions surfaced (defaults)
- **FN2-e: the OI source.** Default: the near-month future of each stock, from the own broker. The chain comes from stored snapshots.
- **Flag gating of "Tell me when it matches".** Default: move it to `screener_v2`.
- **The 500-symbol universe cap and "all NSE".** Default: keep 500 until store-first coverage is proven.
