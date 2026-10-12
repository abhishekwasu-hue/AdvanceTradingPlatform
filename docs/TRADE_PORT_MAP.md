# Trade -> ATP port map

Every module and folder of the Trade repo (`abhishekwasu-hue/Trade`, read-only reference) and where it stands in
ATP: its purpose, the ATP equivalent (a path that exists on `main`, or "none"), a status, and the parity test.
Spec: `docs/specs/ATP_TRADE_PORT_MAP_SPEC.md` (arrives with PR #165).

- **Trade revision read:** `origin/main` at **eafc872** ("Elliott correction fixture library + acceptance set", #290).
  Earlier ports cite Trade@0df3e09 (price action, contracts, costs, order safety, validation), Trade@73f652c
  (options builder) and the OI banner capture; those files may have moved on in Trade since.
- **N1 level-gate rows** (`bots_common/`, `decision3/method.py`, `level_bots_replay.py`) were read at Trade `origin/main`
  **1e6c9ac** (after #292-#297 and #302), for N1 (`docs/design/N1_EXECUTION_CORE.md`).
- **ATP revision read:** `main` at 2741dec (#163); the Phase 0 rows (section "Phase 0 ports: status on 2026-10-12") and the
  rows they name were re-read on `main` at 399eda3 (#200), and the level-bot gate row on Trade `origin/main` at 1e6c9ac.
- **What a port means here:** the logic, plus golden-parity tests against the Trade functions (same input, same
  output), English names, settings-driven (no hard-coded symbols, strike steps or times), multi-tenant safe.
- **Never ported:** Streamlit pages and widgets, Trade's own DB layers (`cloud_db.py`, `database.py`), broker
  adapters ATP already has, research and diagnostic scripts.
- **Updating this map:** each port PR changes its row (status, ATP path, parity test) and the summary counts.

## Status words

| Status | Meaning |
|---|---|
| ported | The logic is in ATP and a test compares it with Trade (golden cases captured from Trade, or Trade's own tests in English). |
| partial | Some of the logic is in ATP, or ATP has its own version that covers part of it. The row says what is missing. |
| partial (unverified) | ATP has something similar, but nobody has compared it with Trade. Check it before relying on it. |
| in progress (Z7a) | Being ported now as Z7a, the market zones port (`docs/specs/ATP_MARKET_ZONES_PORT.md`, PR #165). |
| missing | Not in ATP. The row names the target. |
| not needed | Not ported on purpose. The reason is given: a Streamlit page, a Trade DB layer, an adapter ATP already has, a research or ops script, or ATP's own equivalent. |

## Summary

One row per top-level `.py` file (140) and per folder (35: 25 code packages and 10 others such as `tests/`,
`research/`, `data/`), except five folders that are split because their parts have different statuses: `paper/`
(9 rows), `price_action/` (8 rows), `elliott/` (2 rows: the ported part and the rest), `bots_common/` (3 rows, the
level-bot gate; it was one planned row until Trade #292 added it) and `decision3/` (2 rows: the `method.py` functions
the level gate calls, and the rest). N1 (2026-10-12) adds `level_bots_replay.py` (Trade #295) as a top-level file:
141 + 31 + 9 + 8 + 2 + 3 + 2 = **196 rows**. Other Trade
additions after eafc872 (for example `reader2/`, #296) are not mapped yet.

| Status | Rows |
|---|---|
| ported | 13 |
| partial | 18 |
| partial (unverified) | 9 |
| in progress (Z7a) | 4 |
| missing | 52 (25 of them are G rows that wait for the owner's "stable" mark) |
| not needed | 100 |
| **Total** | **196** |

| Group | Rows | ported | partial | partial (unverified) | in progress (Z7a) | missing | not needed |
|---|---|---|---|---|---|---|---|
| 1. Phase 0 (fold into Z3 / Z5 / Z10) | 21 | 2 | 11 | 4 | 0 | 2 | 2 |
| 2. Right after Phase 0 (N-list) | 32 | 4 | 3 | 3 | 4 | 18 | 0 |
| 3. Only when the owner marks it stable (G) | 25 | 0 | 0 | 0 | 0 | 25 (waiting) | 0 |
| 4. Already ported or covered, outside the groups | 11 | 7 | 4 | 0 | 0 | 0 | 0 |
| 5. Unscheduled (not in the owner's lists) | 9 | 0 | 0 | 2 | 0 | 7 | 0 |
| 6. Not needed | 98 | 0 | 0 | 0 | 0 | 0 | 98 |

## Phase 0 port list (targets)

| # | Trade source | ATP target | Status now | Phase 0 item |
|---|---|---|---|---|
| 1 | `order_safety.py` (+ `process_lock.py`, `clear_exit_state.py`) | `backend/app/execution/order_safety.py`, `backend/app/execution/multileg.py`, `backend/app/execution/router.py`, `backend/app/execution/prechecks.py` | partial | Z3 (PAPER), Z10 |
| 2 | `charges.py` (golden check) | `backend/app/execution/india_costs.py`, `backend/app/execution/paper_broker.py` | partial | Z3 |
| 3 | `strike_breach_model.py` | `backend/app/options_builder/strike_breach.py` (builder `evaluate` answer), `backend/app/ai/proposal_card.py` (`strike_breach`) | ported (Z5 P1-d; card behind flag `risk_by_default`, off) | Z5 |
| 4 | India VIX regime / spike halt (`check_vix_spike_halt.py`, `trading_engine.check_vix_spike_halt`) | `backend/app/risk_engine/vix_gate.py` (live reading captured by the trading worker from 09:20 IST through the entitled data path; applied in `guardian.guard_entry`: spike = new bullish index entries paused for the day, no valid reading after the grace = the same, high level = size cut; exits never gated) | ported (Z5 P1-d; flag `vix_gate`, off) | Z5 |
| 5 | `paper/` + Telegram commands | `backend/app/execution/paper_broker.py`, `backend/app/trading/`, `backend/app/telegram_inbound/service.py` | partial | Z3 (BT2 / BT3) |
| 6 | `position_stream_monitor.py` + `trade_monitor.py` exit logic (+ `price_stream.py`) | `backend/app/trading/position_monitor.py`, `backend/app/market_data/stream.py`, `backend/app/workers/trading_worker.py` | partial | Z3 (PAPER exit engine) |
| 7 | Expiry rule (`weekly_dual_expiry_option_seller.py`; next-expiry choice from `elliott/contracts.py`) | `backend/app/instruments/expiry_calendar.py` (`ExpiryBook.choose`, already ported) -> wire into `backend/app/options_builder/` and proposals | partial | Z5 |

## Phase 0 ports: status on 2026-10-12

What Phase 0 ported (or built next to a Trade row), as merged on `main` at 399eda3. Every flag named is off by default.

| Item | Trade source | ATP on `main` | PRs | Flag | Status | Parity / tests |
|---|---|---|---|---|---|---|
| Z7a-1 zone detection | `market_zones.py`, `signals.py` (swings, S/R, order blocks), `sr_dynamic.py`, `sr_levels_v3.py` (Trade@eafc872) | `backend/app/price_action/zones/` (`detect.py`, `compute.py`, `levels.py`, `sr_v3.py`, `settings.py`) | #168 | `market_zones` | ported | `backend/tests/test_zones_parity.py` (golden from Trade's functions), `test_zones_detection.py` |
| Z7a-2 lifecycle, level memory, nightly store | `level_memory.py`, `refresh_market_zones.py` | `zones/lifecycle.py`, `zones/memory.py`, `zones/store.py`, `zones/job.py` (worker, once per IST day after the EOD ingest; built from the shared lake bars) | #176 | `market_zones` | ported (memory); partial (nightly job: lake bars instead of a broker fetch; lifecycle is ATP's rule) | `test_zones_memory_parity.py` (golden), `test_zones_lifecycle.py`, `test_zones_store_job.py` |
| Z7a-3 chart layer + ScreenQL zone functions | none in Trade for ScreenQL; `sr_v3_chart.py` is a UI helper (not needed) | `zones/chart.py` (band pick, strength), `zones/read.py` (as-of reads, batched since #200), `GET /api/charts/layers/market-zones`, `backend/app/screener/zones.py` (zone functions, vectorised series since #200) | #196, #200 | `market_zones` | done (ATP-side; no Trade equivalent to compare) | `test_zones_chart_screenql.py` (shared cases `zones_chart_cases.py`, frontend mirror), `test_zones_perf.py` |
| Z7a intraday refresh | `refresh_market_zones_intraday.py`, `refresh_market_zones_mcx.py`, `refresh_dynamic_sr_1m.py`, `refresh_dynamic_sr_5m.py` | `zones/job.run_instrument` is the bar-close entry point; nothing triggers it yet | - | `market_zones` | in progress (Z7a): the bar-close trigger is not built | none |
| U6 BK1 breakout screens | none: ATP-native (U6 spec, `docs/specs/ATP_BREAKOUT_SCREENERS_ADDENDUM.md`); the related Trade rows are `price_action/level_strength.py` (ported earlier) and `opportunity_engine/` (G list) | `backend/app/screener/breakouts/` (`params.py`, `style.py`, `screens.py`, `quality.py`, `templates.py`, `gate.py`); ScreenQL RangeBreakout, SwingBreakout, NRBreakout, NewHighBreakout, RangeExpansion, VolumeBreakout, RelVolume, ATRPercentile, DowTrend | #178 | `screener_breakouts` | not a port (ATP's own); done | `backend/tests/test_z7_bk1_breakouts.py`, `test_z7_bk1_api.py` (fixtures `breakout_fixtures.py`) |
| U6 BK2 intraday breakouts | none: ATP-native (same spec) | `breakouts/intraday.py` (opening range, first hour, previous day, new high / low of day), `breakouts/session.py` (session clock, session VWAP, time-of-day relative volume) | #181 | `screener_breakouts` | not a port (ATP's own); done | `backend/tests/test_z7_bk2_intraday.py` |
| Z5 P1-d VIX gate | `check_vix_spike_halt.py`, `trading_engine.check_vix_spike_halt` | `backend/app/risk_engine/vix_gate.py`, applied in `guardian.guard_entry`; reading by the trading worker from 09:20 IST; per user under `byob_data_tier`, member fallback, kept out of the Copilot's memory (#195) | #179, #195 | `vix_gate` | ported (differences in the group 1 row below) | `test_z5_p1d_risk_by_default.py` (Trade's decision cases in English), `test_z5_p1d_review.py`, `test_z5_p1d_followup.py` |
| Z5 P1-d strike breach | `strike_breach_model.py` | `backend/app/options_builder/strike_breach.py`, builder `POST /evaluate` (`strike_breach`), `backend/app/ai/proposal_card.py` | #179 | `risk_by_default` (the card) | ported: inference and inversion only; fitting stays in Trade | `test_z5_p1d_strike_breach.py` (golden `tests/fixtures/strike_breach/golden.json.gz` from Trade's functions) |
| Level-bot confirmation gate | `bots_common/level_reaction.py` (+ `level_gate_bot.py`, `level_visibility.py`), replay `level_bots_replay.py` (Trade 1e6c9ac) | none | - | - | missing (waiting): the Trade replay is done but does not settle the port - see the group 2 row | none yet |
| Charges table sources (TX-1) | `charges.py`, `elliott/costs.py` | `backend/app/execution/india_costs.py` (`SOURCES`: URL, circular / notification, date per row; `unverified()`), tax report `rate_sources` | this branch (`claude/sources-verify`) | - | partial: every row now cites its source; 1 Apr 2026 STT confirmed by NSE/FATAX/73524; five rows still "verify" (docs/OPEN_QUESTIONS.md TX-1); `charges.py` per-broker brokerage still not compared | `test_trade_port_contracts_costs.py` (sources, both sides of each circular), `test_tax_report_charges.py` |

The six safety checks the spec lists for `order_safety.py`:

| Check | Where it is in Trade | ATP today | Status |
|---|---|---|---|
| Freeze-quantity split | **Not in Trade's `order_safety.py`.** Trade only reads `freeze_quantity` from the MCX instrument master (`resolve_mcx_futures_instruments.py`) and mentions slicing in a comment in `elliott/costs.py`. Nothing splits an order. | none | missing (nothing to port; build it in ATP from the instrument master's freeze limits) |
| Price band / circuit limits | **Not in Trade.** Trade only has Upstox `market_protection` (`order_safety.apply_market_protection`). | Market-protection band only: `backend/app/execution/order_safety.py` and `backend/app/execution/router.py`. No circuit-limit check. | missing (circuit); market protection is ported |
| Max order value | **Not in Trade.** | `backend/app/risk_engine/hierarchy.py` (`MAX_ORDER_VALUE` limit), ATP's own | not needed (ATP equivalent) |
| Duplicate-order guard | `process_lock.py` (one process per bot), `has_open_trade_from_source` checks in the bots, `order_safety.exit_lock` | `backend/app/execution/order_persistence.py` (idempotency key per tenant), `backend/app/workers/redis_guard.py` (worker lock) | partial (unverified); Trade's per-trade exit lock and in-flight exit state are not ported |
| Stale-price guard | `live_price_age` passed into `trading_engine.manage_open_trades` from the WebSocket feed | `backend/app/market_data/freshness.py` (`quote_is_stale`, `candle_staleness`, used by `backend/app/workers/trading_worker.py`) | partial (unverified) |
| Hedge leg first | Leg order from `strategy.py` builders; `hedge_first` setting in `pullback_credit_spread/settings.py` | `backend/app/execution/multileg.py` (wings first; a short waits for a confirmed wing fill; strict full-wing check behind `LIVE_STRICT_WING_FILL`) | ported (`backend/tests/test_trade_port_order_safety.py`) |

## 1. Phase 0 (fold into Z3 / Z5 / Z10)

| Trade module | Purpose | ATP equivalent | Status | Parity test |
|---|---|---|---|---|
| `order_safety.py` | Order-path safety: Upstox market protection, full-failure test, retry once, per-trade exit lock, in-flight exit state, exit re-send plan, failure counts and alerts | `backend/app/execution/order_safety.py` (market protection, full failure, pending ids, exit re-send plan, wing-fill check) | partial: not ported are the exit lock, the in-flight exit state (`mark_inflight` / `save_exit_state` / `merged_exit_prices`), failure counts with a blocked key, `retry_full_failure_once` and the alert texts. See the six-check table above. | `backend/tests/test_trade_port_order_safety.py` (Trade's `tests/test_order_safety.py`, in English) |
| `process_lock.py` | One running process per bot (file lock), part of the duplicate-order guard | `backend/app/workers/redis_guard.py`, `backend/app/execution/order_persistence.py` | partial (unverified): ATP's guard is a worker lock plus order idempotency, not per bot | none |
| `clear_exit_state.py` | Operator escape that clears a stuck partial-exit state after a manual check | none | missing: needed only if ATP adopts Trade's exit-state lock (row above) | none |
| `charges.py` | Real charges per order: brokerage per broker (Upstox calculator, Stocko statutory), STT, exchange, SEBI, stamp, GST; by trade and per period | `backend/app/execution/india_costs.py` (dated statutory rates, ported from `elliott/costs.py`), `backend/app/execution/paper_broker.py` | partial: statutory rates are ported from `elliott/costs.py` and, since TX-1 (2026-10-12), every dated row cites its official source (`india_costs.SOURCES`; the 1 Apr 2026 STT rows by NSE/FATAX/73524; NSE transaction charges re-dated by circular, incl. 1 Mar 2026); `charges.py` itself (per-broker brokerage, order-level turnover) has not been checked against ATP on golden fixtures | `backend/tests/test_trade_port_contracts_costs.py` covers `elliott/costs.py` and the sources only |
| `strike_breach_model.py` | Logistic model: chance that a short strike at distance z is breached before expiry (by DTE, weekday, ADX regime); required distance for a target breach risk | `backend/app/options_builder/strike_breach.py` (frozen NIFTY coefficients, features, `required_z`, per-leg report), builder `POST /evaluate` (`strike_breach`, with the underlying's daily bars), `backend/app/ai/proposal_card.py` | ported: inference and inversion only (the fitting/report code is research and stays in Trade); the daily market structure is an input (neutral unless the caller knows it) because Trade's `StructureTracker` is not ported | `backend/tests/test_z5_p1d_strike_breach.py` (golden cases from Trade's own functions: `tests/fixtures/strike_breach/make_golden.py`; plus Trade's no-look-ahead and inversion tests in English) |
| `check_vix_spike_halt.py` | 09:20 IST job: India VIX % move against the previous close; above the threshold, new bullish index entries halt for the day | `backend/app/risk_engine/vix_gate.py`: `capture` / `capture_for_tenant` (the live LTP plus the close of the last completed session before today, stamped with today's IST date - Trade's `fetch_india_vix` + `fetch_india_vix_prev_close`; through the acting user's own Tier A session with `byob_data_tier` on, else the organisation's usable session), taken by the trading worker (`_vix_readings`) from 09:20 IST every 15 minutes; `spike_decision` / `evaluate` applied in `guardian.guard_entry` for both order pipelines and the PAPER ticket | ported (flag `vix_gate`, off): same threshold (5 %, `>=`), scope (NIFTY, BANKNIFTY, SENSEX; bullish only; PAPER and LIVE), timing (open before 09:20) and fail-safe (no valid reading after 09:20 + a 10-minute grace pauses bullish entries). Differences: any reading of the day counts (not one 09:20 run); an added high-level size cut (`VIX_GATE_HIGH_LEVEL`); no Telegram message per run | `backend/tests/test_z5_p1d_risk_by_default.py` (Trade's `tests/test_check_vix_spike_halt.py` decision cases in English, timing cases) and `backend/tests/test_z5_p1d_review.py` (the worker's capture to the gate, end to end; failed reads; BT1 path) |
| `trading_engine.py` | Trade lifecycle: open and close LIVE and PAPER, SL / target / EOD / OI-reversal / trailing exits, broker reconciliation, VIX halt gate, max-loss caps | `backend/app/execution/router.py`, `backend/app/execution/multileg.py`, `backend/app/trading/position_monitor.py`, `backend/app/reconciliation/engine.py` | partial (unverified): ATP has its own lifecycle; the exit rules and the VIX gate are the parts to port (rows above and below) | none |
| `trade_monitor.py` | Exit loop: runs `manage_open_trades` every few seconds with live prices, a lock, heartbeat and the PAPER watch | `backend/app/trading/position_monitor.py` (`monitor_open_positions`), `backend/app/workers/trading_worker.py` | partial: ATP's monitor covers SL / target / time exits; compare against Trade's rules and cadence for the Z3 PAPER exit engine | none (Trade has `tests/test_trade_monitor.py`) |
| `position_stream_monitor.py` | Runs the exit cycle on WebSocket ticks for open positions (lower slippage than polling) | `backend/app/market_data/stream.py`, `backend/app/trading/position_monitor.py` | partial: ATP streams quotes; a tick-driven exit cycle with throttled updates is the Z3 target | none |
| `price_stream.py` | Upstox Market Data Feed V3 (WebSocket) for open-position prices | `backend/app/market_data/stream.py` | partial (unverified) | none (Trade has `tests/test_price_stream.py`) |
| `weekly_dual_expiry_option_seller.py` | NIFTY + SENSEX weekly seller: Supertrend + structure + OI-rotation confluence, VIX picks the structure; enters 1-2 days before expiry, never on expiry day | `backend/app/instruments/expiry_calendar.py` (`ExpiryBook.choose`: an expiry today -> the next one; `expiry_weekday` per underlying and date) | partial: the expiry choice is ported but not wired into the options builder or proposals. The strategy itself is in group 2. Trade's rule here uses fixed weekdays (NIFTY Tuesday, SENSEX Thursday); ATP must take them from the calendar. | `backend/tests/test_trade_port_contracts_costs.py` (expiry choice) |
| `paper/journal.py` | PAPER journal: entry / exit charges, gross and net P&L, vision verdict, approver; "would-have" rows for signals not taken | `backend/app/execution/paper_broker.py` (charges), `backend/app/trading/routes.py` (journal), `SignalHistoryRecord` in `backend/app/db/models.py` | partial: charges and net P&L exist; no would-have outcome for skipped or rejected signals | none |
| `paper/lots.py` | Lot size from the broker instrument master, with a cache and fallback | `backend/app/instruments/master.py`, `backend/app/instruments/expiry_calendar.py` (`dated_lot_size`) | partial | `backend/tests/test_trade_port_contracts_costs.py` (dated lots) |
| `paper/manual.py` | Manual PAPER trigger from a Telegram text (parse, side / R:R checks, spread build, cutoff, execute when ready) | none | missing: target the Z3 Telegram bot + PAPER flow | none (Trade has `tests/test_paper_manual.py`) |
| `paper/pause.py` | PAPER-only pause flag (who and why) | `backend/app/kill_switch/checks.py` (global / tenant / strategy kill switch) | partial: no PAPER-scope pause | none |
| `paper/commands.py` | Telegram `/status /positions /pause /resume /paper` texts and handlers | `backend/app/telegram_inbound/service.py` (`/positions`, `/risk`, `/brief`, ...) | partial: `/status`, `/pause`, `/resume`, `/paper` are missing | none (Trade has `tests/test_paper_monday.py`) |
| `paper/watch.py` | PAPER updates to Telegram: entry, near-SL / target, exit with P&L | `backend/app/alerts/`, `backend/app/notifications/service.py` | partial (unverified) | none |
| `paper/engine_entry.py` | Opens a PAPER spread from an engine signal (fetch chain, size, open) | `backend/app/execution/signal_execution.py`, `backend/app/instruments/spreads.py` | partial: ATP's own signal -> spread path | none |
| `paper/bot_hooks.py` | Pre-cycle hook for Trade bots (token error, lot size, signal source) | none | not needed: wiring for Trade's bots | none |
| `paper/config.py` | PAPER per-symbol enable flags and lot fallbacks (JSON file) | `backend/app/platform/controls.py` (flags) | not needed: ATP settings per tenant | none |
| `eod_trade_summary.py` | After close: Telegram message with the day's trades, P&L and charges | `backend/app/workers/eod_summary.py` | partial: ATP's EOD summary is PAPER health; add net P&L with charges for BT3 | none (Trade has `tests/test_eod_trade_summary.py`) |

## 2. Right after Phase 0 (N-list)

| Trade module | Purpose | ATP equivalent | Status | Parity test |
|---|---|---|---|---|
| `pdf_reports.py` | PDF reports: market analysis and signal backtest (fonts, charts, tables) | none | missing: target the journal and weekly-review export (N2) | none (Trade has `tests/test_pdf_reports.py`) |
| `pnl_reports.py` | Daily / weekly / monthly P&L with real charges (net P&L) | `backend/app/trading/analytics.py`, `backend/app/tax/report.py` | partial (unverified) | none (Trade has `tests/test_pnl_reports.py`) |
| `market_report.py` | 16:00 report for the next day's intraday and positional option selling (index levels, OI, VIX, ranges) | `backend/app/ai/briefing.py`, `backend/app/ai/global_cues.py` (pre-open) | missing: merge into the briefing / global cues (N2) | none |
| `eod_market_report.py` | Runs `market_report` for NIFTY, BANKNIFTY and SENSEX and sends it | `backend/app/ai/briefing.py` | missing (as above) | none |
| `level_memory.py` | Keeps levels across days: ATR-scaled tolerance, merge, retire after N days without a touch, max distance and count, role by price | `backend/app/price_action/zones/memory.py` (knobs in `ZoneSettings`; remembers dynamic S/R like Trade, classical S/R too when `memory_families` asks; role by price by default), used by `zones/snapshot.py` and the nightly job | ported (Z7a-2) | `backend/tests/test_zones_memory_parity.py` (golden: tolerance, last touch, day-by-day merge walks, remember / nightly apply) |
| `market_zones.py` | Zone store: order blocks, demand / supply, role by price, mitigation, S/R on 1H, dynamic S/R, gaps, 5m/15m confluence | `backend/app/price_action/zones/` (`detect.py`, `compute.py`; lifecycle `lifecycle.py`; store `store.py` + `job.py`). `backend/app/support_resistance/zones.py` is ATP's own swing zones, not a port | ported (Z7a-1 detection, Z7a-2 lifecycle + storage). Deliberate change: Trade's FILLED (first touch) is ATP's fresh -> tested event; a zone dies only on a structural break; lifecycle tracked from confirmation. Chart layer (`zones/chart.py`, `GET /api/charts/layers/market-zones`) and ScreenQL zone functions (`app/screener/zones.py`): Z7a-3 | `backend/tests/test_zones_parity.py` (detection, first touch); lifecycle `backend/tests/test_zones_lifecycle.py` (ATP rule, no Trade equivalent) |
| `sr_dynamic.py` | Python version of the Pine script "Support Resistance - Dynamic v2" (pivot clusters into S/R channels) | `backend/app/price_action/zones/levels.py` (`compute_dynamic_sr`, `find_pivots_indexed`) | ported (Z7a-1); levels get memory and a lifecycle in Z7a-2 | `backend/tests/test_zones_parity.py` (`dynamic_sr`) |
| `sr_levels_v3.py` | Experimental S/R v3: recency, reaction, MTF confluence, PDH / PDL / PWH / PWL, role reversal, gaps, graded | `backend/app/price_action/zones/sr_v3.py` (settings `ZoneSettings.sr_v3`); its break rule (0.10 % buffer, 2 closes) is the default of the ATP zone flip | ported (Z7a-1, level logic); the "S/R levels v3" template follows | `backend/tests/test_zones_parity.py` (`sr_v3`, `sr_v3_gaps`, `sr_v3_flip`) |
| `signals.py` | Price-action signal engine: RSI / Supertrend, market structure, break / pullback / retest, S/R levels, order blocks, trendlines | `backend/app/price_action/zones/levels.py` + `detect.py` + `backend/app/price_action/swings.py` (the four functions below); `backend/app/indicators/`, `backend/app/price_action/market_structure.py` (ATP's own) | partial: `find_swings`, `filter_major_swings`, `find_support_resistance_levels`, `find_order_block` / `is_retesting_order_block` ported (Z7a-1); the rest is missing (group 5) | `backend/tests/test_zones_parity.py` (`swings`, `sr_levels`, `order_block_retest`) |
| `refresh_market_zones.py` | Nightly job: one year of OHLC -> S/R, order blocks, demand / supply, gaps -> stored | `backend/app/price_action/zones/job.py` (worker, once per IST day, flag `market_zones` off by default) + `store.py` (as-of snapshots `market_zone_runs` / `market_zones`, tenant overrides `market_zone_settings`) | partial: built from the shared lake bars instead of a broker fetch, with lifecycle and memory; read API as of a time per tenant: `zones/read.py` + `GET /api/charts/layers/market-zones` (Z7a-3) | function level only (`test_zones_parity.py`, `test_zones_memory_parity.py`); job behaviour `backend/tests/test_zones_store_job.py` |
| `refresh_market_zones_intraday.py` | Intraday refresh of dynamic S/R 15M / 30M / 60M zones | none | in progress (Z7a): Z7a-2 bar-close job | none |
| `refresh_market_zones_mcx.py` | Dynamic S/R 30M / 60M for MCX futures | none | in progress (Z7a): same job; MCX symbols wait for the MCX template | none |
| `refresh_dynamic_sr_1m.py` | Refreshes 1-minute dynamic S/R levels with the 5-minute cron | none | in progress (Z7a): same job | none |
| `refresh_dynamic_sr_5m.py` | Refreshes 5-minute dynamic S/R levels | none | in progress (Z7a): same job | none |
| `backtest_review/` | Day-by-day visual review of backtest trades: charts per trade / day, reason codes, golden gallery, review notes, Telegram review images | none | missing: target CH3 replay + U8 evidence (N4) | none (Trade has `tests/test_backtest_review.py`) |
| `iv_snapshot_collector.py` | Daily ATM +/- 5 strike IV record; feeds the IV-change-from-sideways-baseline gate | `backend/app/option_chain/snapshots.py` (strike IV in `strike_oi_snapshots`), `backend/app/option_chain/oi_gates.py` + `oi_regime.check_iv_change_gate` | partial: the gate is ported; ATP keeps strike IV per slot, not Trade's daily ATM +/- 5 record. Finish in N7. | `backend/tests/test_o1_oi_regime.py` (IV gate) |
| `pullback_credit_spread/` | Template: directional credit spread entered only on a pullback (never a breakout); all settings editable, PAPER default | none | missing: strategy_engine template (N2 / N7) | none (Trade has `tests/test_pcs_core.py`, `test_pcs_signal.py`) |
| `dynamic_sr_instant_trader.py` | Template: 1-min / 5-min instant reversal at dynamic S/R levels | none | missing: template | none (Trade has `tests/test_dynamic_sr_instant_trader.py`) |
| `srv2_momentum_reversal_strategy.py` | Template: 15 / 30 / 60-minute dynamic S/R reversal with a momentum filter | none | missing: template | none (Trade has `tests/test_srv2_momentum_reversal_strategy.py`) |
| `classic_sr_reversal_trader.py` | Template: classic S/R reversal | none | missing: template | none (Trade has `tests/test_classic_sr_reversal_trader.py`) |
| `bots_common/level_reaction.py` (level-bot confirmation gate; Trade #292 / #293, `docs/LEVEL_BOTS_CONFIRMATION.md`) | One shared gate for the level bots: Daily trend first (`decision3.daily.fold`) -> the stored level is only a candidate area -> arrival from the trade side arms it -> handover study inside the area (trend-side vs counter-side candle size and volume; absorption blocks; wedge / channel / double-top patterns as evidence; impulsive approach blocks) -> dominant commitment candle (body >= 1.5 x window average) on higher volume (NA => grade <= B) -> real break disarms -> SL beyond the far edge, target = next opposite area, R:R >= 3 -> Vision -> Telegram approval | none | missing: N1 L1 (state machine, `decision3.method` gate functions, `break_from`) -> `backend/app/price_action/level_gate/` + `backend/app/price_action/breaks.py`; L2a / L2b (the Daily fold, pinned revision, OPEN_QUESTIONS N1-2); wired to scans in X1 through the E1 execution core (`backend/app/execution/core/`). The L6 replay report is in (Trade #295; OPEN_QUESTIONS N1-3) | planned: golden parity from Trade's own functions on fixed bars (`backend/tests/fixtures/level_gate/make_golden.py`, `.json.gz`) |
| `bots_common/level_gate_bot.py` | Thin layer for the bots: `entry_mode` (confirmation for PAPER, touch for LIVE), gate settings from the dashboard, one cycle gate per symbol (trend + closed bars + all areas), signal-log codes `SKIPPED_GATE_*`, confirmation TF per bot (5m / 15m) | none | missing: N1 X1 (the scan Then template: `confirm_tf`, gate settings, reason codes on the intent; touch mode not ported - ATP has no touch-mode level bots) | none yet (Trade has `tests/test_level_bots_5m.py`, `test_level_bots_classic_srv2.py`, `test_level_bots_mcx.py`) |
| `bots_common/level_visibility.py` | L5 visibility: today's armed / confirmed / ignored areas per bot (dashboard tab) and the gate section of the 15:45 summary | none | missing: N1 X1 (the intents list and the rule's `last_problem`; the summary line goes to the EOD summary) | none yet (Trade has `tests/test_level_bots_visibility.py`) |
| `decision3/method.py` (the functions the level gate calls) | `zone_study`, `zone_pattern`, `commitment`, `reversal_form`, `power_shift`, `contraction`, `candle_read` and their helpers. The rest of `method.py` (risk, plans, conviction) stays with the `decision3/` G row | none | missing: N1 L1 -> `backend/app/price_action/level_gate/method.py` (ported because the owner's level-bot rules call them; `method.py` unchanged since #292) | planned: golden parity, every check mirrored for both directions |
| `gap_fill_reversal_strategy.py` | Template: 1H Supertrend trend -> opening gap -> gap fill -> reversal | `backend/app/price_action/gap_context.py` (placeholder only) | missing: template | none |
| `mtf_pullback_strategy.py` | Template: multi-timeframe pullback (with overnight gaps) | `backend/app/strategy_engine/mtf_strategy.py` is ATP's own MTF pullback, not a port | missing: template; `find_overnight_gaps` ported to `backend/app/price_action/zones/detect.py` (Z7a-1, #168) | none (Trade has `tests/test_page_mtf_multi.py`) |
| `oi_greeks_vix_strategy.py` | Template: OI + Greeks + India VIX decision tree | `backend/app/option_chain/oi_regime.py` (the OI part) | missing: template | none |
| `nifty_futures_swing_rsi_st.py` | Template: NIFTY futures swing, RSI(5) + Supertrend(7, 2) at S/R | `backend/app/indicators/` (RSI, Supertrend) | missing: template | none |
| `mcx_futures_trader.py` | Template: MCX futures (CRUDEOIL, NATURALGAS, GOLD, SILVER, COPPER) at S/R touches | none | missing: template, only if MCX data and a broker are available | none (Trade has `tests/test_mcx_futures_trader.py`) |
| `mcx_filters.py` | MCX entry filters (pure; shared by the bot and the replay) | none | missing: goes with the MCX template | none (Trade has `tests/test_mcx_phase1_filters.py`) |
| `mcx_contract_specs.py` | MCX P&L multipliers (GOLD quoted per 10 g, lot 1 kg; SILVER 30 kg) | `backend/app/instruments/registry.py` (MCX contract specs) | partial (unverified): compare the multipliers | none |
| `mcx_quantity_check.py` | LIVE gate: Upstox MCX `quantity` is in lots, not units | `backend/app/brokers/upstox.py` | partial (unverified): needed before any MCX order path | none (Trade has `tests/test_mcx_quantity_check.py`) |

The weekly dual-expiry seller is also a group 2 template; its file has one row, in group 1, which covers both the
expiry rule (Phase 0) and the template (N-list).

## 3. Only when the owner marks it stable (G)

Still changing in Trade (degree-aware Dow, the correction-pattern fixture library). Until the owner marks one stable,
ATP's `price_action` follows their behaviour specs, not their code. All rows: ATP equivalent none, status missing
(waiting), no parity test.

| Trade module | Purpose | Note |
|---|---|---|
| `decision3/` (rest) | v2.2 method-first engine (Abhi's six steps): daily legs / swings, levels, liquidity, trendline, advisory, vision audit, Telegram view | owner's G list. The level gate's Daily trend (`daily.fold` with `daily_legs`, `legs2`, `patterns2`) is ported at a pinned revision in N1 L2 unless the owner says otherwise (OPEN_QUESTIONS N1-2); `method.py`'s gate functions are their own row in group 2 |
| `swings2/` | Layer 1 v2.1: nested directional-change swing engine + market structure (BOS / CHoCH / sweep) | owner's G list |
| `legs2/` | Layer 2: role and character of each leg -> labels I and K | owner's G list |
| `patterns2/` | Layer 3: correction pattern inside I and K (hypotheses, rules, scores) | owner's G list |
| `zones2/` | Zone layer of the v2 stack | owner's G list |
| `trendlines2/` | Trendline layer of the v2 stack | owner's G list |
| `rsi2/` | RSI layer of the v2 stack | owner's G list |
| `elliott/` (rest) | Elliott pullback credit spread bot: counts, patterns, setups, trigger, confirm, invalidation, exits, strikes, pricing, backtest, golden, bhavcopy, contract master, own_tf | owner's G list; the ported files are the `elliott/` row in group 4 |
| `mtf/` | Layers 1-7 on any timeframe's own candles (shadow) | owner's G list |
| `simple_core/` | Simple core: entry area + commitment candle -> entry signal; execution in settings | owner's G list |
| `opportunity_engine/` | MTF price-action opportunity engine: structure state machine, zones, level quality, journal, scoring, backtest, visual audit | owner's G list |
| `vision2/` | Vision veto | owner's G list |
| `vision_led/` | Vision-led entry: candidates, playbook, prompt, situation map, blind test | owner's G list |
| `decision2/` | Earlier common analysis engine (superseded by `decision3`) | not in the owner's list; same family, treated as G (assumption) |
| `engine_signal.py` | `decision2` signal for the bots through one interface (no orders) | treated as G with `decision2` (assumption) |
| `chart_reader/` | "Chart Reader" entry for PAPER bots: facts, areas, narrative, grade, risk | not in the owner's list; still changing, treated as G (assumption) |
| `correction/` | Correction reader (shadow): checklist + annotated charts | treated as G (assumption) |
| `market_state/` | One market-state module shared by `vision_led`, `chart_reader` and the vision facts | treated as G (assumption) |
| `pivots/` | Layer 1 (v1): directional-change pivots, nested degrees | treated as G (assumption); superseded by `swings2/` |
| `review7/` | Abhi-method visual analysis for Telegram review charts | treated as G (assumption) |
| `refresh_market_structure.py` | EOD refresh of the opportunity engine's structure journal | goes with `opportunity_engine/` |
| `refresh_market_structure_intraday.py` | Same, after each closed 15M bar | goes with `opportunity_engine/` |
| `run_opportunity_backtest.py` | Opportunity engine backtest (D1-D3, V1-V3) | goes with `opportunity_engine/` |
| `run_visual_audit.py` | Opportunity engine: EOD vision check of engine levels | goes with `opportunity_engine/` |
| `run_visual_forward_report.py` | Opportunity engine: forward report from the daily visual audit | goes with `opportunity_engine/` |

## 4. Already ported or covered, outside the groups

| Trade module | Purpose | ATP equivalent | Status | Parity test |
|---|---|---|---|---|
| `oi_analysis.py` | OI math: rotation, hysteresis signal, OI-price classes, PCR zones and gate, IV-change gate, max pain, rollover, swing gate, OI wall, DTE, history buckets | `backend/app/option_chain/oi_regime.py`, `backend/app/option_chain/oi_gates.py`, `backend/app/option_chain/snapshots.py` | ported: the pure functions. Trade's DB readers (`get_latest_oi_signal`, `get_latest_pcr`, `fetch_and_save_oi_snapshot`) are replaced by ATP's collector and tables. Deliberate differences (fail closed) are in `docs/design/OI_BANNER.md`. | `backend/tests/test_o1_oi_regime.py` (golden `backend/tests/fixtures/oi_regime/golden.json`, captured by `capture_from_trade.py`) |
| `strategy.py` | Option structure selection: iron condor / butterfly, credit spreads (OTM, fixed, ITM), naked ITM, PoP lookup, position size | `backend/app/options_builder/selectors.py` | ported | `backend/tests/test_p1a_options_builder.py` (golden `backend/tests/fixtures/options_builder/golden.json.gz`, 1,451 cases) |
| `strategy_payoff.py` | Multi-leg payoff, combined Greeks, breakevens, max P/L, ready-made strategies | `backend/app/options_builder/payoff.py`, `backend/app/options_builder/templates.py` | ported | `backend/tests/test_p1a_options_builder.py` |
| `research_stats.py` | Research statistics: Sharpe, deflated Sharpe, PBO / CSCV, shuffle p-value | `backend/app/backtest/validation.py` | ported | `backend/tests/test_trade_port_validation.py` |
| `price_action/level_strength.py` | Sweep, real break, failed breakout, level strength features | `backend/app/price_action/level_strength.py` | ported | `backend/tests/test_trade_port_price_action.py` |
| `elliott/` (ported part) | `settings.py` (candle / break / swing keys), `reversal.py`, `breaks.py`, `swings.py`, `contracts.py`, `costs.py`, `data_policy.py` | `backend/app/price_action/pa_settings.py`, `backend/app/price_action/reversal.py`, `backend/app/price_action/breaks.py`, `backend/app/price_action/causal_swings.py`, `backend/app/instruments/expiry_calendar.py`, `backend/app/execution/india_costs.py`, `backend/app/backtest/data_policy.py` | ported (from Trade@0df3e09); `breaks.py` brought to Trade@1e6c9ac by N1 L1: `time_accepted`, the `break_accept_closes` step (off for the existing callers, 3 in the level gate) and `break_from` | `backend/tests/test_trade_port_price_action.py`, `backend/tests/test_trade_port_contracts_costs.py`, `backend/tests/test_trade_port_validation.py`; `break_from` / time acceptance: `backend/tests/test_n1_l1_level_gate.py` |
| `price_action/candles.py` | Candle rules: 0-100 reversal candle score (#241) | `backend/app/price_action/reversal.py` (mode `score100`), `backend/app/price_action/candlestick_patterns.py` | ported: the score as a mode of `evaluate_reversal` | `backend/tests/test_trade_port_price_action.py` |
| `order_execution.py` | Order-type options under the SEBI retail-algo rules (no plain MARKET via API); not wired to any path in Trade | `backend/app/compliance/order_policy.py` (D3), `backend/app/execution/router.py` (protected price) | partial: ATP's own policy (D3); no parity | none |
| `vision/` | Vision + human eye V0-V2: chart render, signal audit, reduce-only gate, Telegram approve / reject, outcome charts | `backend/app/ai/vision_audit.py`, `backend/app/ai/chart_png.py` (BYOK-V2, Z9) | partial: ATP's own (Vision only comments, never gates); Trade's reduce-only gate is not in ATP | none |
| `oi_snapshot_collector.py` | OI snapshot every 10 minutes into the DB | `backend/app/option_chain/snapshots.py` (O2 collector) | partial: ATP's own collector per tenant-enabled underlying | none |
| `htf_alignment.py` | A higher-timeframe bar is visible only after its `bar_end` | `backend/app/backtest/engine.py` (C1, closed bars only) | partial: same rule, ATP's own code | `backend/tests/test_realism_htf_lookahead.py` (ATP's own, not golden) |

## 5. Unscheduled (not in the owner's lists)

These hold logic that is in none of the spec's groups. The owner decides whether they become templates or are dropped.

| Trade module | Purpose | ATP equivalent | Status | Parity test |
|---|---|---|---|---|
| `strategies/` | Orchestrator strategies: OI / PCR breakout, ICT liquidity sweep + FVG, BB squeeze, VWAP trend, S/R bounce, MTF gap fill, opportunity engine adapter | `backend/app/strategy_engine/combo_strategies.py` (ATP's own VWAP + Supertrend and Bollinger strategies, not ports) | missing | none |
| `entry_engine.py` | "A1 signal engine" extracted: direction -> structure -> break / pullback / retest | none | missing | none |
| `price_action/leg_eval.py` | Leg evaluation (impulse vs correction measures) | none | missing (the earlier AY/AZ/BA price-action port was stopped: the level engine showed no edge over random in Trade's validation, see `docs/WORK_LOG.md`) | none |
| `price_action/legs.py` | Leg detection | none | missing (as above) | none (Trade has `tests/test_price_action_legs.py`) |
| `price_action/level_validation.py` | Level validation (Osler method) | none | missing (as above) | none (Trade has `tests/test_level_validation.py`) |
| `price_action/levels_v2.py` | Levels v2 | `backend/app/support_resistance/levels.py` (ATP's own levels) | partial (unverified) | none (Trade has `tests/test_levels_v2.py`) |
| `price_action/major_levels.py` | Major levels | `backend/app/support_resistance/engine.py` (ATP's own) | partial (unverified); may overlap Z7a | none (Trade has `tests/test_major_levels.py`) |
| `price_action/trendlines.py` | Trendline rules | none | missing | none (Trade has `tests/test_price_action_trendlines.py`) |
| `stocko_api.py` | Stocko (SAS Online) REST wrapper (OAuth2, orders, positions) | none (ATP has no Stocko adapter) | missing: a broker onboarding decision, not a port | none (Trade has `tests/test_stocko_api.py`) |

## 6. Not needed

| Trade module | Purpose | ATP equivalent | Reason |
|---|---|---|---|
| `app.py` | Streamlit entry and navigation | `frontend/` | Streamlit |
| `bot_view.py` | Chart "bot view": active levels of a chosen bot | none | Streamlit |
| `live_chart.py` | Live chart via REST LTP polling | none | Streamlit |
| `live_ticker.py` | Live ticker fragment (no page reload) | none | Streamlit |
| `mini_chart.py` | Mini NIFTY / BANKNIFTY charts | none | Streamlit |
| `tradingview_chart.py` | Lightweight Charts embed for Streamlit | `frontend/` charts | Streamlit |
| `sr_v3_chart.py` | Data and lines for the SR v3 chart view | none | UI helper (logic is in `sr_levels_v3.py`, Z7a) |
| `position_chart.py` | Entry / SL / target lines for a position chart | `frontend/` chart overlays | UI math |
| `safe_widgets.py` | Crash-proof Streamlit input widgets | none | Streamlit |
| `shared_context.py` | Streamlit sidebar settings + option chain fetch | none | Streamlit |
| `ui_headers.py` | Coloured section headings | none | Streamlit |
| `page_backtest_review.py` | Backtest Review page | none | Streamlit page |
| `page_bot_dynamic_sr_algo.py` | Dynamic S/R bots settings page | none | Streamlit page |
| `page_broker_accounts.py` | Broker accounts page | none | Streamlit page |
| `page_dashboard.py` | Dashboard: chart, direction engine, option chain / OI, manual trading, A1 signals, PDF | none | Streamlit page |
| `page_mcx_futures.py` | MCX futures page | none | Streamlit page |
| `page_mtf_pullback.py` | MTF pullback + gap fill page | none | Streamlit page |
| `page_multi_strategy.py` | Multi-strategy orchestrator page | none | Streamlit page |
| `page_opportunity_engine.py` | Opportunity engine page | none | Streamlit page |
| `page_orders.py` | Order book page | none | Streamlit page |
| `page_performance.py` | Performance and backtest page | none | Streamlit page |
| `page_positions.py` | Positions page (MTM) | none | Streamlit page |
| `page_pullback_credit_spread.py` | Pullback credit spread settings + preview | none | Streamlit page (logic in `pullback_credit_spread/`, group 2) |
| `page_sr_levels_v3.py` | SR levels v3 page | none | Streamlit page |
| `page_system_tools.py` | System diagnostics, backups, reconciliation, history export | none | Streamlit page |
| `page_vision_human_eye.py` | Vision and human-eye review page | none | Streamlit page |
| `cloud_db.py` | Supabase / Postgres data layer | `backend/app/db/` | Trade DB layer |
| `database.py` | SQLite schema, order / trade logging, queries | `backend/app/db/` | Trade DB layer |
| `broker_adapter.py` | Broker adapter interface | `backend/app/brokers/base.py` | ATP equivalent |
| `broker_factory.py` | Builds an adapter from a broker account | `backend/app/brokers/registry.py` | ATP equivalent |
| `upstox_api.py` | All Upstox calls (candles, chain, orders, positions, funds, VIX, news) | `backend/app/brokers/upstox.py` | adapter ATP has (the VIX fetch is needed for the group 1 VIX gate) |
| `upstox_broker_adapter.py` | Upstox adapter | `backend/app/brokers/upstox.py` | adapter ATP has |
| `upstox_feed_pb2.py` | Generated protobuf for the Upstox feed | `backend/app/brokers/upstox.py` | generated code |
| `fyers_api.py` | Fyers REST wrapper | `backend/app/brokers/fyers.py` | adapter ATP has |
| `fyers_broker_adapter.py` | Fyers adapter | `backend/app/brokers/fyers.py` | adapter ATP has |
| `shoonya_api.py` | Shoonya (Noren) REST wrapper | `backend/app/brokers/shoonya.py` | adapter ATP has |
| `shoonya_broker_adapter.py` | Shoonya adapter | `backend/app/brokers/shoonya.py` | adapter ATP has |
| `stocko_broker_adapter.py` | Stocko adapter wrapper | none | goes with `stocko_api.py` (group 5) |
| `get_upstox_token_manual.py` | Manual Upstox token (login + OTP) | `backend/app/brokers/routes.py` (OAuth), `backend/app/brokers/login_reminder.py` | ATP equivalent |
| `get_fyers_token_manual.py` | Manual Fyers token | `backend/app/brokers/routes.py` | ATP equivalent |
| `get_shoonya_token_manual.py` | Manual Shoonya token | `backend/app/brokers/routes.py` | ATP equivalent |
| `get_stocko_token_manual.py` | Manual Stocko token | none | goes with `stocko_api.py` |
| `trigger_upstox_token_request.py` | Morning Upstox "access token request" (notifier flow) | `backend/app/brokers/login_reminder.py` | ATP equivalent (daily login + pre-open reminder; Upstox's notifier webhook flow is not in ATP) |
| `upstox_token_webhook.py` | Receives the Upstox notifier webhook token | `backend/app/brokers/token_lifecycle.py` | ATP equivalent (as above) |
| `check_token_freshness.py` | Alert when the Upstox token is stale | `backend/app/brokers/token_lifecycle.py` | ATP equivalent |
| `crypto_utils.py` | Encrypt broker credentials at rest | `backend/app/secrets_store/encryption.py` | ATP equivalent |
| `config.py` | Global constants (DB path, timeframes, IST helpers) | `backend/app/core/config.py` | ATP equivalent |
| `log_setup.py` | Rotating-file logger | `backend/app/core/logging_config.py` | ATP equivalent |
| `read_cache.py` | Short TTL cache for settings / level reads in the exit loop | `backend/app/cache/client.py` | ATP equivalent |
| `notifications.py` | Telegram messages + heartbeat file for unattended scripts | `backend/app/notifications/service.py`, `backend/app/alerts/` | ATP equivalent |
| `diagnostics.py` | Health checks (PDF dependencies, ready to trade) | `backend/app/platform/readiness.py` | ATP equivalent |
| `engine_service.py` | systemd runner for `manage_open_trades` | `backend/app/workers/trading_worker.py` | ATP equivalent (runner) |
| `loader.py` | Builds the orchestrator from `config.yaml` | `backend/app/strategy_engine/registry.py` | ATP equivalent |
| `orchestrator.py` | Runs enabled strategies on a snapshot and ranks their signals | `backend/app/strategy_engine/registry.py`, `backend/app/workers/trading_worker.py` | ATP equivalent |
| `market_data_adapter.py` | Builds a `MarketSnapshot` from Upstox candles and chain | `backend/app/market_data/service.py` | ATP equivalent |
| `instruments.py` | Index instrument list for research scripts | `backend/app/instruments/registry.py` | ATP equivalent |
| `resolve_index_futures_instruments.py` | Front-month NIFTY / BANKNIFTY futures | `backend/app/instruments/master.py`, `backend/app/instruments/contracts.py` | ATP equivalent |
| `resolve_mcx_futures_instruments.py` | Front-month MCX futures (lot size, freeze quantity) | `backend/app/instruments/master.py` | ATP equivalent |
| `mcx_margin.py` | Upstox margin per MCX commodity | `backend/app/execution/prechecks.py` (`live_margin_cap`, broker margin calculator) | ATP equivalent |
| `backtest.py` | Walk-forward, no-lookahead signal backtest (direction accuracy, R:R) | `backend/app/backtest/engine.py` | ATP equivalent |
| `multi_strategy_backtest.py` | Walk-forward backtest for the orchestrator strategies | `backend/app/backtest/engine.py` | ATP equivalent |
| `credit_spread_auto_trader.py` | Unattended credit spread bot (A1 signal + OI) | `backend/app/workers/trading_worker.py` | bot runner; its logic is `strategy.py` + `oi_analysis.py` (ported) |
| `oi_signal_auto_trader.py` | Unattended A1 signal + OI exit bot | `backend/app/workers/trading_worker.py` | bot runner |
| `srv3_instant_shadow.py` | Shadow PAPER of the 5-min bot with SR v3 levels | none | shadow experiment (templates are group 2) |
| `collect_index_futures_volume.py` | Collects front-month futures 5M volume / OI for the opportunity engine | `backend/app/market_lake/ingest.py` | data collector (owner cancelled the Trade-repo collector, 2026-10-11) |
| `daily_nifty_1min_update.py` | Appends NIFTY 1-min candles to Supabase | `backend/app/market_lake/ingest.py` | data collector |
| `migrate_nifty_1min_to_supabase.py` | One-off parquet -> Supabase migration | none | one-off data script (HB0 imports history, PR #164) |
| `real_nifty_data.py` | Loads the stored NIFTY 1-min / daily history | `backend/app/market_lake/asof.py` | data loader |
| `cache_holdout_upstox.py` | Caches the sealed holdout candles | `backend/app/backtest/data_policy.py` | research data |
| `yfinance_source.py` | Yahoo Finance candles without a token | none | data source not used (terms) |
| `rescale_gold_pnl.py` | One-off fix of stored GOLD P&L (x100) | none | one-off data fix |
| `demo_test.py` | Synthetic end-to-end run of the orchestrator | none | demo script |
| `monitor_timing.py` | Records exit-check timing to find slippage delay | `backend/app/observability/metrics.py` | diagnostic |
| `banknifty_positional.py` | H-POS1 report: BANKNIFTY positional short strike, levels vs random | none | research |
| `g2_followups.py` | G2 follow-up report-only tests | none | research |
| `leg_classifier_report.py` | T2.3 / G1 leg classifier report | none | research |
| `leg_level_validation.py` | T3 leg and level validation (Osler) | none | research |
| `oe_t1_hypotheses.py` | T1 diagnostic hypotheses H1-H5 | none | research |
| `run_sr_bot_level_backtest.py` | Old dynamic S/R vs SR v3 levels backtest | none | research |
| `sr_bot_level_backtest.py` | Engine for the above | none | research |
| `run_visual_backfill.py` | One-time vision backfill 2022-2024 | none | research (cost; forward-only by owner decision) |
| `verify_opportunity_data_availability.py` | Opportunity engine PR-0 data check | none | research |
| `mcx_trade_replay.py` | Replays MCX trades with closed bars only | none | research |
| `mcx_trendline_audit.py` | MCX trendline test (no trading) | none | research |
| `mcx_market_readiness_check.py` | Pre-market MCX sanity check | none | ops script |
| `mcx_open_positions_check.py` | Read-only MCX positions check before expiry | none | ops script |
| `verify_mcx_order_quantity_units.py` | Read-only check of MCX quantity units via the margin API | none | ops script (the rule is in `mcx_quantity_check.py`, group 2) |
| `research/` (32 scripts) | Elliott, chart reader, golden gallery, map A/B reports | none | research |
| `research_notes/` | Correction pattern and trendline rule notes | none | notes (source for G behaviour specs) |
| `scripts/` (30 scripts) | Checks, probes, dry runs, vision samples, `engine_signal_run.py` | none | diagnostic (`engine_signal_run.py` goes with `decision2`, group 3) |
| `tests/` (188 files) | Trade's tests and fixtures | - | source of golden cases; the rows above name the Trade test behind each module |
| `data/` | NIFTY parquet history and reports | `backend/app/market_lake/` | data |
| `deploy/` | systemd units (engine, monitors, dashboard, vision, webhook) | `deploy/` | Trade ops |
| `docs/` | Trade specs, prompts, research reports | `docs/specs/` | reference (specs are copied when a port starts) |
| `fonts/` | DejaVu and Noto Devanagari fonts for PDFs | none | asset (needed only with `pdf_reports.py`) |
| `lib/` | `lightweight-charts.js` for Streamlit | `frontend/` | asset |
| `live_chart_component/` | HTML component for the live chart | none | Streamlit |
| `level_bots_replay.py` (+ `scripts/level_bots_replay.py`, Trade #295) | L6 replay: touch vs confirmation on the same days, levels, position rules and costs; writes `docs/reports/level_bots_confirmation.md` | none | research replay: its findings set N1's stance (OPEN_QUESTIONS N1-3); ATP's own backtests come with C2-C6 |

Non-Python files at the top level (`config.yaml`, `PRE_LIVE_CHECKLIST.md`, `requirements*.txt`, `packages.txt`,
`pytest.ini`, `run_*.bat`) are Trade's own setup and are not mapped.

## Findings

1. **`order_safety.py` does not hold most of the checks the spec lists for it.** It has Upstox market protection,
   the full-failure test and retry, the per-trade exit lock, the in-flight exit state, the exit re-send plan and
   alerts. There is no freeze-quantity split, no price band or circuit check and no max-order-value check anywhere in
   Trade. ATP already has a max-order-value limit (`backend/app/risk_engine/hierarchy.py`). The freeze-quantity split
   and the circuit check must be built new in ATP, with ATP tests; there is nothing in Trade to compare against.
2. **The "expiry rule" in `weekly_dual_expiry_option_seller.py` uses fixed weekdays** (NIFTY Tuesday, SENSEX Thursday)
   and enters 1-2 days before expiry. "An expiry today -> take the next one" lives in `elliott/contracts.py`, and is
   already ported as `ExpiryBook.choose` in `backend/app/instruments/expiry_calendar.py`. What is left for Phase 0 is
   wiring it into the options builder and proposals.
3. **The IV gate is already ported** (`oi_regime.check_iv_change_gate`, used by `backend/app/option_chain/oi_gates.py`).
   For `iv_snapshot_collector` only the daily ATM +/- 5 IV record is left.
4. **`charges.py` has not been verified.** The earlier cost port came from `elliott/costs.py` (statutory rates by
   date). The per-broker brokerage in `charges.py` has not been checked on golden fixtures.
5. **The VIX risk gate is ported (Z5 P1-d, flag `vix_gate` off)** with its own live reading (LTP + previous session close, taken from 09:20 IST); the Copilot's market-memory CUE row (built from completed daily bars) stays context only. Before it India VIX appeared only as AI context.
6. **Stocko has no ATP adapter.** It is not "an adapter ATP already has", so it is listed in group 5 for an owner
   decision.
7. **Trade has 140 top-level `.py` files** (the spec says about 140) and 25 code packages. Several packages the
   owner did not list (`chart_reader/`, `correction/`, `decision2/`, `market_state/`, `pivots/`, `review7/`) belong to
   the same still-changing analysis family, so this map treats them as G. That is an assumption for the owner to
   confirm.
