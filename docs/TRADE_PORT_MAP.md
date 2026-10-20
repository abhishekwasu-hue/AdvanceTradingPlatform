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
- **N-list rows** (section "N-list ports: status on 2026-10-13") were read on `main` at ed0d44e (#221) and, for the
  slices not merged yet, on their branches at the heads named in that section (Trade `origin/main` at 1e6c9ac).
- **Takeover rows** (section "3a. Trade takeover") were read at Trade `origin/main` **acecd23** (#335, 2026-10-20) and
  ATP `main` at 880fa8c (#278). The owner decided that ATP takes over everything from Trade and that Trade will be shut
  down; the plan is `docs/design/TRADE_TAKEOVER.md`. On that date Trade has no `docs/reader/STABLE.md` and no
  `reader-layer*` tags, so no reader layer is stable yet.
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
| waiting (no tag) | Group G takeover row (section 3a): ATP ports it only after Trade tags it stable (`docs/reader/STABLE.md`, tag `reader-layer<N>-stable-vX`) and exports its parity fixtures. Nothing is ported before that. |
| not needed | Not ported on purpose. The reason is given: a Streamlit page, a Trade DB layer, an adapter ATP already has, a research or ops script, or ATP's own equivalent. |

## Summary

One row per top-level `.py` file (140) and per folder (35: 25 code packages and 10 others such as `tests/`,
`research/`, `data/`), except five folders that are split because their parts have different statuses: `paper/`
(9 rows), `price_action/` (8 rows), `elliott/` (2 rows: the ported part and the rest), `bots_common/` (3 rows, the
level-bot gate; it was one planned row until Trade #292 added it) and `decision3/` (2 rows: the `method.py` functions
the level gate calls, and the rest). N1 (2026-10-12) adds `level_bots_replay.py` (Trade #295) as a top-level file:
141 + 31 + 9 + 8 + 2 + 3 + 2 = **196 rows**. Other Trade
additions after eafc872 (for example `reader2/`, #296) are not mapped as file rows yet; the takeover rows in section 3a
name the ones the reader and the trading flow use (`facts/`, `engine3_live.py`, `bots_common/one_bot.py`,
`bots_common/plain_text.py`).

Section 3a adds **14 takeover rows** (one per reader layer or takeover item, not per file). They are counted on their
own and are not part of the 196: all 14 are "waiting (no tag)".

| Status | Rows |
|---|---|
| ported | 17 |
| partial | 18 |
| partial (unverified) | 9 |
| in progress (Z7a) | 4 |
| missing | 48 (25 of them are G rows that wait for the owner's "stable" mark) |
| not needed | 100 |
| **Total** | **196** |

Counts recounted from the group tables on 2026-10-13: the two CH3b rows (#218, `legs.py`, `trendlines.py`) had not been counted, and the two N1 L1 rows (#210, `level_reaction.py`, `decision3/method.py`) move to ported here.

| Group | Rows | ported | partial | partial (unverified) | in progress (Z7a) | missing | not needed |
|---|---|---|---|---|---|---|---|
| 1. Phase 0 (fold into Z3 / Z5 / Z10) | 21 | 2 | 11 | 4 | 0 | 2 | 2 |
| 2. Right after Phase 0 (N-list) | 32 | 6 | 3 | 3 | 4 | 16 | 0 |
| 3. Only when the owner marks it stable (G) | 25 | 0 | 0 | 0 | 0 | 25 (waiting) | 0 |
| 4. Already ported or covered, outside the groups | 11 | 7 | 4 | 0 | 0 | 0 | 0 |
| 5. Unscheduled (not in the owner's lists) | 9 | 2 | 0 | 2 | 0 | 5 | 0 |
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
| Level-bot confirmation gate | `bots_common/level_reaction.py` (+ `level_gate_bot.py`, `level_visibility.py`), replay `level_bots_replay.py` (Trade 1e6c9ac) | `backend/app/price_action/level_gate/`, `backend/app/price_action/daily_trend/` (pure libraries) | #210, #213 | - | moved to the N-list: L1 and L2 ported (section "N-list ports" below); the bot layer is X1 (PR #214, open) | `test_n1_l1_level_gate.py`, `test_n1_l2_daily_trend.py` |
| Charges table sources (TX-1) | `charges.py`, `elliott/costs.py` | `backend/app/execution/india_costs.py` (`SOURCES`: URL, circular / notification, date per row; `unverified()`), tax report `rate_sources` | this branch (`claude/sources-verify`) | - | partial: every row now cites its source; 1 Apr 2026 STT confirmed by NSE/FATAX/73524; five rows still "verify" (docs/OPEN_QUESTIONS.md TX-1); `charges.py` per-broker brokerage still not compared | `test_trade_port_contracts_costs.py` (sources, both sides of each circular), `test_tax_report_charges.py` |

## N-list ports: status on 2026-10-13

N1 / N4 / N3 work that ports a Trade rule or reuses one, at Trade `origin/main` **1e6c9ac**. The "State" column is
the slice's git state: merged (on `main` at ed0d44e), PR open, or on a branch. Every flag named is off by default.

| Item | Trade source (Trade@1e6c9ac) | ATP | State | Flag | Port status | Parity / tests |
|---|---|---|---|---|---|---|
| N1 E1 execution core | none: ATP's own (ADR-0014). The Trade rule it carries is "one entry per confirmation" (`mcx_confirm_used`), which becomes the intent's idempotency key in X1 | `backend/app/execution/core/` (`policy.py` routing table, `intents.py` submit / claim / expire / requeue, `dispatcher.py`); the worker drains it every cycle; AUTO_LIVE always refused | merged #206 | `execution_core` | not a port (ATP's own); done | `backend/tests/test_n1_e1_execution_core.py` (`test_policy_table`, `test_no_mode_and_no_input_ever_routes_live`, `test_submit_is_idempotent_and_only_for_an_active_member_of_the_organisation`) |
| N1 L1 level gate | `bots_common/level_reaction.py` (`evaluate`, `safe_evaluate`, `describe`, `session_start_index`, `closed_bars`, `median_ranges`, `_impulsive_approach`, `_target`, `context_from_state`, `side_allowed`), `decision3/method.py` (`zone_study`, `zone_pattern`, `commitment`, `reversal_form`, `power_shift`, `contraction`, `candle_read`), `elliott/breaks.py` (`break_from`, `time_accepted`) | `backend/app/price_action/level_gate/` (`gate.py`, `method.py`, `settings.py`), `backend/app/price_action/breaks.py`; pure library, nothing on `main` calls it | merged #210 | none | ported | `backend/tests/test_n1_l1_level_gate.py` (golden `tests/fixtures/level_gate/golden.json.gz` from Trade's own functions; `drift_check.py`) |
| N1 L2 Daily trend fold | `decision3/daily.py` (`fold`, `state_at`), `decision3/daily_swings.py` (`prepare`), `decision3/daily_legs.py`, the `legs2` / `patterns2` parts the fold reads, `level_reaction.trend_context` | `backend/app/price_action/daily_trend/` (`fold.py`, `legs.py`, `rules.py`, `context.py`, `settings.py`); pure library at a pinned revision (OPEN_QUESTIONS N1-2) | merged #213 | none | ported | `backend/tests/test_n1_l2_daily_trend.py` (golden `tests/fixtures/daily_trend/golden.json.gz`; `drift_check.py`) |
| N1 X1 scan Then (Notify / One-click) | `bots_common/level_gate_bot.py` (confirm TF, gate settings, `SKIPPED_GATE_*` codes, one entry per confirmation), `bots_common/level_visibility.py` | `backend/app/screener/then/` (`templates.py`, `areas.py`, `trend.py`, `runner.py`, `routes.py`), `backend/app/price_action/level_gate/status.py`: scan rule -> Z7a areas -> L1 / L2 gate on closed bars -> E1 intent | PR #214 open (draft, `claude/n1-x1` c5d086c); X1b on `claude/n1-x1b` | `scan_then` (+ `execution_core`) | partial: touch mode and the dashboard tab are not ported (ATP has no touch-mode bots); the visibility line goes to the EOD summary | `backend/tests/test_n1_x1_scan_then.py` (`test_status_codes_follow_trade`, `test_daily_context_from_the_lake_matches_the_ported_fold_and_is_computed_once_per_day`); the gate itself is L1's golden |
| N1 X2 Auto PAPER | `bots_common/level_gate_bot.py` `entry_mode` (confirmation entries in PAPER) | `backend/app/screener/then/auto_paper.py`, the E1 dispatcher's PAPER route (no broker; approval-only and the P1-d caps first) | on branch `claude/n1-x2` (8668795), no PR | `scan_auto_paper` | partial: PAPER only; AUTO_LIVE is refused in every case | `backend/tests/test_n1_x2_auto_paper.py` (`test_the_flag_is_off_by_default_and_auto_live_is_refused_in_every_case`, `test_pause_lockout_and_caps_refuse_and_place_nothing`) |
| N1 F portfolio risk (F1-F3) | Trade's portfolio-wide open-risk cap and limits (`cloud_db.py` `PORTFOLIO_RISK_CAP_*`, correlated group = the VIX-halt symbols, `max_open_positions`): rules only, Trade's DB layer is never ported | `backend/app/portfolio/` (`risk.py`, `risk_data.py`, `caps.py`, `risk_text.py`, `routes.py`): per-user PAPER book (gross / net, sector buckets, beta to NIFTY, correlated open risk at the stops), entry-only caps in `guardian.guard_entry` (exits never consult them), F3 cards / Telegram lines | PR #219 open (F1 + F2; `claude/n1-f` 0fedb26 also carries F3) | `portfolio_risk`, `portfolio_risk_caps` | partial: ATP's own model generalises Trade's cap; no golden comparison | `backend/tests/test_n1_f1_portfolio_risk.py` (`test_the_one_sided_warning_follows_trade_s_rule`), `test_n1_f2_portfolio_caps.py`, `test_n1_f3_portfolio_cards.py` |
| N4 CH3a price-action layer | `elliott/swings.py`, `elliott/breaks.py` (ported earlier at Trade@0df3e09 as `causal_swings.py` / `breaks.py`) | `backend/app/charts/price_action_layer.py`: causal swings per degree, legs, real breaks, swing levels and Dow trend as of a time; replay; `GET /api/charts/layers/price-action` | merged #212 | `chart_price_action` | reuses the ported engines (no new Trade logic) | engine parity: `backend/tests/test_trade_port_price_action.py`; layer: `test_ch3_price_action_layer.py` (`test_golden_files_are_current_and_byte_identical`, `test_no_lookahead_bars_after_at_never_change_the_layer`, `test_truncation_invariance_of_swings_legs_and_breaks`) |
| N4 CH3b price-action concepts | `opportunity_engine/structure.py` (BOS / CHoCH / sweeps / states), `price_action/legs.py`, `price_action/trendlines.py` (+ `opportunity_engine/detectors/patterns.find_trendline`, `measures.ref_range`), `decision3/liquidity.py` (pools, sweeps, marks) | `backend/app/price_action/structure_tracker.py`, `legs.py`, `trendlines.py`, `liquidity.py`; chart reference only (no signal, no gate) | merged #218 | `chart_price_action` | ported (those functions); not ported: trapped zones, the Elliott Tier-A check, the touch audit, the 30m -> 60m resampler, Trade's plotting | `backend/tests/test_ch3b_trade_parity.py` (`test_ports_equal_trade_golden`, `test_golden_matches_trade_live`, `test_ported_engines_are_causal`; golden `ch3b_golden.py`, Trade's own output) |
| N3 BK4 trendline / channel breakouts | `price_action/trendlines.py` + `find_trendline` + `ref_range` (the CH3b port, reused, not re-ported) | `backend/app/screener/breakouts/lines.py` (B5 trendline / channel, B6 flag / pennant / triangle: the CH3b trendline rule evaluated as of every bar on the bars before it), `retest.py` (B9) | on branch `claude/n3-bk4` (f8f74cd, on top of `claude/n3-bk3`), no PR | `screener_breakouts_bk4` (needs `screener_breakouts`) | reuse of a ported rule | `backend/tests/test_z7_bk4_breakouts.py` (`test_the_line_is_the_trendline_ports_choice_on_every_prefix`, `test_the_ref_range_and_swings_are_the_trendline_ports`) |

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
| `trading_engine.py` | Trade lifecycle: open and close LIVE and PAPER, SL / target / EOD / OI-reversal / trailing exits, broker reconciliation, VIX halt gate, max-loss caps | `backend/app/execution/router.py`, `backend/app/execution/multileg.py`, `backend/app/trading/position_monitor.py`, `backend/app/reconciliation/engine.py` | partial (unverified): ATP has its own lifecycle; the exit rules and the VIX gate are the parts to port (rows above and below). N1 F (read at a6f0800): `check_portfolio_risk_cap` (open max loss of a correlated bucket + the new trade <= cap % of capital; refuses when capital is unknown) and `max_open_positions` are mirrored as rules, not code, in F2 (`app/portfolio/caps.py`); the index bucket is F1's `INDEX` bucket | `tests/test_n1_f1_portfolio_risk.py`, F2 tests (hand-built cases) |
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
| `bots_common/level_reaction.py` (level-bot confirmation gate; Trade #292 / #293, `docs/LEVEL_BOTS_CONFIRMATION.md`) | One shared gate for the level bots: Daily trend first (`decision3.daily.fold`) -> the stored level is only a candidate area -> arrival from the trade side arms it -> handover study inside the area (trend-side vs counter-side candle size and volume; absorption blocks; wedge / channel / double-top patterns as evidence; impulsive approach blocks) -> dominant commitment candle (body >= 1.5 x window average) on higher volume (NA => grade <= B) -> real break disarms -> SL beyond the far edge, target = next opposite area, R:R >= 3 -> Vision -> Telegram approval | `backend/app/price_action/level_gate/` (`gate.py` state machine, `method.py`, `settings.py`), `backend/app/price_action/breaks.py` (`break_from`, `time_accepted`); the Daily trend context in `backend/app/price_action/daily_trend/` (N1 L2) | ported (N1 L1 #210 at Trade@1e6c9ac, pure library; the trend fold by N1 L2 #213). Not wired on `main`: the first caller is X1 (PR #214, open) | `backend/tests/test_n1_l1_level_gate.py` (golden `tests/fixtures/level_gate/golden.json.gz` from Trade's own functions; `drift_check.py` for newer revisions) |
| `bots_common/level_gate_bot.py` | Thin layer for the bots: `entry_mode` (confirmation for PAPER, touch for LIVE), gate settings from the dashboard, one cycle gate per symbol (trend + closed bars + all areas), signal-log codes `SKIPPED_GATE_*`, confirmation TF per bot (5m / 15m) | none on `main` (`backend/app/screener/then/` + `backend/app/price_action/level_gate/status.py` on `claude/n1-x1`) | missing on `main`. Built: N1 X1 scan Then (PR #214, open; `claude/n1-x1`, X1b on `claude/n1-x1b`): `confirm_tf`, gate settings, Trade's `SKIPPED_GATE_*` codes on the intent, one entry per confirmation; touch mode not ported (ATP has no touch-mode level bots). Auto PAPER (Trade's confirmation `entry_mode` for PAPER) is X2 (`claude/n1-x2`) | on the branches: `backend/tests/test_n1_x1_scan_then.py` (`test_status_codes_follow_trade`), `test_n1_x2_auto_paper.py` (Trade has `tests/test_level_bots_5m.py`, `test_level_bots_classic_srv2.py`, `test_level_bots_mcx.py`) |
| `bots_common/level_visibility.py` | L5 visibility: today's armed / confirmed / ignored areas per bot (dashboard tab) and the gate section of the 15:45 summary | none | missing on `main`. Built with X1 (PR #214, open): the intents list and the rule's `last_problem`; the summary line goes to the EOD summary | none yet (Trade has `tests/test_level_bots_visibility.py`) |
| `decision3/method.py` (the functions the level gate calls) | `zone_study`, `zone_pattern`, `commitment`, `reversal_form`, `power_shift`, `contraction`, `candle_read` and their helpers. The rest of `method.py` (risk, plans, conviction) stays with the `decision3/` G row | `backend/app/price_action/level_gate/method.py` | ported (N1 L1 #210 at Trade@1e6c9ac; every check mirrored for both directions) | `backend/tests/test_n1_l1_level_gate.py` |
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

Since the owner's takeover decision (2026-10-20) these file rows are ported through the per-layer rows of section 3a:
a row here closes when every layer that uses it is ported at a stable tag. Trade's `docs/reports/v22/METHOD_MAP.md`
says which of them are in the reading path: `decision3/` (`engine`, `levels`, `flip`, `method`, `daily`, `daily_legs`,
`trendline`, `liquidity`, `telegram_view`), `swings2/`, `legs2/` (volume, features), `patterns2/` (shapes inside
`daily_legs`) and `rsi2/` (`Divergence`). It lists `chart_reader/`, `opportunity_engine/` (except
`opportunity_engine/volume.py`, the FUT-volume data path), `elliott/`, `market_state/`, `decision2/`, `pivots/` (v1),
`zones2/` and the `trendlines2/` layer as outside the reading path; those rows are expected to become "not needed"
when the last layer is stable, unless `STABLE.md` names them.

| Trade module | Purpose | Note |
|---|---|---|
| `decision3/` (rest) | v2.2 method-first engine (Abhi's six steps): daily legs / swings, levels, liquidity, trendline, advisory, vision audit, Telegram view | owner's G list. The level gate's Daily trend (`daily.fold` with `daily_legs`, `legs2`, `patterns2`) is ported at a pinned revision in N1 L2 unless the owner says otherwise (OPEN_QUESTIONS N1-2); `method.py`'s gate functions are their own row in group 2. N4 CH3b ported `liquidity.py` pools, sweeps and marks (not the trapped zones) to `backend/app/price_action/liquidity.py` for the chart, with parity |
| `swings2/` | Layer 1 v2.1: nested directional-change swing engine + market structure (BOS / CHoCH / sweep) | owner's G list |
| `legs2/` | Layer 2: role and character of each leg -> labels I and K | owner's G list |
| `patterns2/` | Layer 3: correction pattern inside I and K (hypotheses, rules, scores) | owner's G list |
| `zones2/` | Zone layer of the v2 stack | owner's G list |
| `trendlines2/` | Trendline layer of the v2 stack | owner's G list |
| `rsi2/` | RSI layer of the v2 stack | owner's G list |
| `elliott/` (rest) | Elliott pullback credit spread bot: counts, patterns, setups, trigger, confirm, invalidation, exits, strikes, pricing, backtest, golden, bhavcopy, contract master, own_tf | owner's G list; the ported files are the `elliott/` row in group 4 |
| `mtf/` | Layers 1-7 on any timeframe's own candles (shadow) | owner's G list |
| `simple_core/` | Simple core: entry area + commitment candle -> entry signal; execution in settings | owner's G list |
| `opportunity_engine/` | MTF price-action opportunity engine: structure state machine, zones, level quality, journal, scoring, backtest, visual audit | owner's G list. N4 CH3b ported `structure.py` (the structure state machine: BOS / CHoCH / sweeps) to `backend/app/price_action/structure_tracker.py` for the chart, with parity (`tests/test_ch3b_trade_parity.py`) |
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

## 3a. Trade takeover (G, waits for stable)

The owner's plan: `docs/design/TRADE_TAKEOVER.md`. Trade finishes the Chart Reader layer by layer (the six layers of
Trade's `docs/knowledge/CHART_READER_METHOD.md` section 7). Each layer the owner approves gets an entry in Trade's
`docs/reader/STABLE.md`, a tag `reader-layer<N>-stable-vX` and parity fixtures in the private trade-data repo
(`parity/<layer>/<tag>/`). ATP ports each layer as soon as it is tagged, into one package, `atp_private/reader/` in the
private package (owner decision PR-1: a private repository, never ATP's public tree; `docs/design/PRIVATE_PACKAGE.md`),
with one owner module per concept (Trade's structure-reset table, `docs/design/CHART_READER_AGENT.md` section 3, as
refined by `docs/reports/v22/METHOD_MAP.md`; after the KB move out of Trade's public tree, the KB and the method docs
are read from the private package). A row moves out of "waiting (no tag)" when its tag appears; a new tag for the same
layer (a bug fix) means a re-port and a new parity run, and the row records the newest tag.

Trade sources are at acecd23. They are the owners named today; the `STABLE.md` entry for the tag is what ATP ports, and
it wins where it differs. ATP targets are proposed paths in the private package (`atp_private/...`, `knowledge/`,
`tests/`; nothing there exists yet); only the boundary (provider interface and loader) and the generic parts live in
ATP's public tree. Parity fixtures never enter ATP's git: they live in the private fixtures store on the host
(`<fixtures store>/parity/<layer>/<tag>/`, see `docs/design/TRADE_TAKEOVER.md` section 3), and the test reads them from
there. "Trade state" is Trade's own progress on 2026-10-20, from Trade's `docs/WORK_LOG.md` and `METHOD_MAP.md`.

| # | Layer / item | Trade source (Trade@acecd23) | Stable tag | ATP target | Parity fixtures | Status |
|---|---|---|---|---|---|---|
| T1 | Reader layer 1: swings and structure (swing, degree, impulse vs correction, equal highs / lows, weekly and daily trend) | `swings2/engine.py` (`build`, `known`; `pivots/` is its pivot engine), `swings2/structure.py` (`all_structure`, `trend_of`), `decision3/daily.py` (`fold`, `state_at`), `decision3/daily_swings.py`, `decision3/daily_legs.py` (with its `legs2/` features and `patterns2/` shapes) | - | `atp_private/reader/swings.py`, `reader/legs.py`, `reader/trend.py`; the private CI check `tests/test_reader_no_numbers.py` starts here | `parity/layer1/<tag>/` | waiting (no tag). Trade state: being rebuilt (logic-only swings under the owner's review). ATP's `price_action/daily_trend/` (N1 L2, Trade@1e6c9ac) stays for the level gate; the reader gets its own module at the tag |
| T2 | Reader layer 2: areas and lifecycle (one base per area, demand / supply / flipped / origin of a sharp move, sweep, broken, flipped, dead, every valid area ahead armed) | `decision3/levels.py` (`_born`, `_band_swing`, `_step_far_swing`, `active`, `snapshot`, `display_bands`), `decision3/flip.py` (`far_swing`, `structural`, `zone_read`, ...), `decision3/liquidity.py` (`pools`) | - | `atp_private/reader/areas.py`, `reader/liquidity.py` | `parity/layer2/<tag>/` | waiting (no tag). Trade state: layer built (`break_rule = structure`, `liquidity_rule = equal`, no cap on armed areas), not tagged. Not the same engine as ATP's Z7a market zones (`price_action/zones/`), which stay for screens and the "Market zones" layer |
| T3 | Reader layer 3: zone study, commitment and lower-timeframe change (visit, slow and fast handover, absorption, commitment form and volume, LTF change of character, raw FUT volume) | `decision3/engine.py` (`visit`, `_visit_structure`, `_at_area`, `_failed_commit_outside`, `vol_view`), `decision3/method.py` (`zone_study`, `handover_episode`, `sharp_reversal`, `commitment`, `reversal_form`, `candle_read`, `zone_pattern`, `ltf_frame`, `ltf_choch`), `decision3/flip.py` (`approach_context`, `legs_arrays`), `decision3/liquidity.py` (`sweeps_and_traps`), `legs2/volume.py` (`bar_volume`), `opportunity_engine/volume.py` (`attach_futures_volume`, `near_month_rows`) | - | `atp_private/reader/visit.py`, `reader/zone_study.py`, `reader/candles.py`, `reader/volume.py`, `reader/ltf.py` | `parity/layer3/<tag>/` | waiting (no tag). Trade state: 3a and 3b built; the owner's layer-3 notes (approach leg across gaps and sessions) merged in #335; not tagged. ATP's `price_action/level_gate/method.py` (N1 L1) holds older reads of the same functions, pinned for the level gate; it is not edited by this port |
| T4 | Reader layer 4: lines and confluence (two reactions, third touch as the trade location, lines on the structure's own swings, band + line confluence, correction-line trigger) | `facts/validity.py` (`check_line`, `check_band`, `confluence`, `select_lines`, `select_bands`), `decision3/trendline.py` (`k_line`, `swing_line`, `confluence_areas`), `trendlines2/two_touch.py` (`display_lines`) | - | `atp_private/reader/lines.py`, `reader/confluence.py` | `parity/layer4/<tag>/` | waiting (no tag). Trade state: not started (the line finder still has numbers). ATP's CH3b `price_action/trendlines.py` is an older rule and stays a chart reference |
| T5 | Reader layer 5: RSI divergence (consecutive real swings of one degree, equal RSI counts, evidence only) | `rsi2/engine.py` (`Divergence`; `RSI`), `rsi2/settings.py` | - | `atp_private/reader/divergence.py` | `parity/layer5/<tag>/` | waiting (no tag). Trade state: not started (`div_degrees` follows layer 1) |
| T6 | Reader layer 6: reader output (decision enter / wait / no trade, A / B label without scores, story, evidence list present / absent, every price from the data; context lines as context only) | `decision3/engine.py` (`decide`, `V22`), `decision3/telegram_view.py`, `decision3/setup_chart.py`, `engine3_live.py` (`row_from_v22`), `bots_common/plain_text.py`, `facts/dossier.py` (`build_dossier`, `NOT_FACTS`), `vision/context.py`, `vision/gap_context.py` | - | `atp_private/reader/decide.py`, `reader/output.py` (the output contract: METHOD section 5), `reader/dossier.py`, `reader/chart.py` (overlay data) | `parity/layer6/<tag>/` | waiting (no tag). Trade state: not started (evidence weights still give A / B / weak). The "Read this chart" panel and the "Reader zones & lines" overlay read this output |
| T7 | KB and Method Pack | `docs/knowledge/KNOWLEDGE_BASE.md`, `docs/knowledge/CHART_READER_METHOD.md`, `docs/knowledge/ABHI_METHOD_NOTES.md`, `docs/knowledge/method_pack.md` (built by `scripts/build_method_pack.py` with `vision/method_pack.py`; stale-pack test `tests/test_method_pack.py`) | - (the version that matches each layer tag) | the private package: `knowledge/` (copied once from Trade, then edited only there; each tag records the private commit), `atp_private/method_pack/` (pack text and version hash), a stale-pack test; never ATP's public tree | none (text; the pack version hash is recorded per tag) | waiting (no tag). After the switch-over ATP owns them with the same change control |
| T8 | Vision audit (audit-only checklist 1-7 with the Method Pack as the cached system prefix; never blocks or changes a decision) | `vision/v4.py`, `vision/checklist.py` (`DECISION3_MAP`), `vision/method_pack.py`, `decision3/vision_audit.py`, `vision/signal_audit.py` (cost, usage) | - | `backend/app/ai/vision_audit.py` (exists: ATP's own audit-only Vision, BYOK and budget) plus the checklist prompt from the private package (`atp_private/method_pack/`), loaded at run time | `parity/vision/<tag>/` (the checklist cases, if Trade exports them) | waiting (no tag). ATP's audit exists; the V4 checklist waits for the KB / pack at a tag and for layer 6 |
| T9 | The one-bot PAPER executor with approvals: the 15M SRv2 engine-signal flow (signal, Telegram ✅ / ✏️ entry only / ❌ reason, PAPER, SL / target / lots / exits from the dashboard, exits never blocked) | `bots_common/one_bot.py`, `srv2_momentum_reversal_strategy.py` (`15m_dynamic_sr`, `signal_source = engine`), `engine3_live.py`, `engine_signal.py`, `scripts/engine_signal_run.py`, `paper/bot_hooks.py`, `paper/engine_entry.py`, `vision/approval.py`, `vision/telegram_bot.py`, `paper/watch.py`, `paper/journal.py` | - (after T6) | a deployment type in `backend/app/deployments/` (a `strategy_deployments` row whose strategy is the reader flow), entries through the E1 core (`backend/app/execution/core/`) and `backend/app/execution/approval.py`, Telegram in `backend/app/telegram_inbound/service.py`, PAPER fills in `backend/app/execution/paper_broker.py`, exits in `backend/app/trading/position_monitor.py` | the shadow comparison (section 11 of the design) | waiting (no tag). Needs layer 6. Overlaps the group 1 `paper/` rows (journal, watch, engine entry) |
| T10 | The MCX bot | `mcx_futures_trader.py`, `mcx_filters.py`, `mcx_contract_specs.py`, `mcx_quantity_check.py`, `refresh_market_zones_mcx.py` (MCX exits: `position_stream_monitor_mcx.service`) | - | an MCX deployment type on the same core; MCX specs in `backend/app/instruments/registry.py`, quantity rule in `backend/app/brokers/upstox.py` | Trade's MCX tests as golden cases | waiting (no tag). In Trade its entries go through the level-bot gate and are off in one-bot mode; see the open question in the design |
| T11 | The level bots (5M instant, classic S/R reversal, 15M SRv2 touch / confirmation, MCX) | today `bots_common/level_reaction.py`, `bots_common/level_gate_bot.py`, `bots_common/level_visibility.py`; the owner decided they are rebuilt on the method after the reader's last layer, and not in Trade | - | rebuilt after T6 is stable: the method rules in the private package on `atp_private/reader/`, the generic bot and deployment code in ATP (ATP's N1 X1 scan Then and the L1 gate are the current base) | ATP's own tests (nothing to compare in Trade) | waiting (no tag): starts only after the last reader layer is stable |
| T12 | Data: bars (index spot 1-minute to daily, near-month FUT 5-minute volume and OI, MCX bars, NSE F&O bhavcopy history) | trade-data folders of bars and bhavcopy, Trade's `data/*.parquet`, listed in Trade's `EXPORT_MANIFEST.md` | - (the manifest's own version) | daily bars: the shared lake `md_candles` (sources `nse_bhav:*` already cover NSE daily); intraday and FUT bars: the owner's private store `md_tenant_candles` (HB0 / HB2 rules), loaded by `atp.sh import-trade` | checksums in the manifest | waiting: `EXPORT_MANIFEST.md` does not exist yet |
| T13 | Data: golden fixtures, parity fixtures and the owner's approved charts | trade-data golden case files, `parity/<layer>/<tag>/` (once it exists), the owner's marked and approved charts | - | the private fixtures store on the host (outside git and outside the Method Pack), read by the parity tests | the store itself | waiting: no parity folder and no manifest yet |
| T14 | Data: Trade's records and review artefacts (PAPER journal, engine signals, Vision log, review and audit charts and reports) | Trade's database (`cloud_db.py` tables), trade-data review, report and audit folders | - | a private, read-only archive on the host (kept for the shadow comparison and the owner's reference; never served to another tenant) | none | waiting: depends on what `EXPORT_MANIFEST.md` lists |

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
| `price_action/legs.py` | Leg detection and classes (impulse vs pullback, CHoCH in a pullback) | `backend/app/price_action/legs.py` (Trade@1e6c9ac; chart reference only - the N4 CH3b leg classes, no signal, no gate) | ported (CH3b; the Elliott Tier-A check and Trade's plotting are not) | `backend/tests/test_ch3b_trade_parity.py` (golden from Trade's own output) |
| `price_action/level_validation.py` | Level validation (Osler method) | none | missing (as above) | none (Trade has `tests/test_level_validation.py`) |
| `price_action/levels_v2.py` | Levels v2 | `backend/app/support_resistance/levels.py` (ATP's own levels) | partial (unverified) | none (Trade has `tests/test_levels_v2.py`) |
| `price_action/major_levels.py` | Major levels | `backend/app/support_resistance/engine.py` (ATP's own) | partial (unverified); may overlap Z7a | none (Trade has `tests/test_major_levels.py`) |
| `price_action/trendlines.py` | Trendline rules (falling resistance, rising support) | `backend/app/price_action/trendlines.py` with `opportunity_engine/detectors/patterns.find_trendline` and `measures.ref_range` (Trade@1e6c9ac; chart only, CH3b); the channel line is ATP's | ported (CH3b; the touch audit and the 30m -> 60m resampler are not) | `backend/tests/test_ch3b_trade_parity.py` |
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
| `database.py` | SQLite schema, order / trade logging, queries | `backend/app/db/` | Trade DB layer. N1 F1 mirrors one rule from it: `compute_portfolio_risk_summary`'s one-sided warning (two or more directional positions, all on one side) in `app/portfolio/risk.py`, tested with hand-built cases |
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
8. **Two owner tables in Trade disagree on two concepts.** The structure-reset table (`docs/design/CHART_READER_AGENT.md`
   section 3, 13 Oct) names `reader2` as the owner of legs (impulse vs correction) and plans to merge decision3's Daily
   Dow into `swings2`. The later `docs/reports/v22/METHOD_MAP.md` (19 Oct, governed by `CHART_READER_METHOD.md`) names
   `decision3/daily_legs.py` and `decision3/daily.py` as those owners, and `reader2` stays research-only behind its
   gallery gate. Rows T1-T6 follow `METHOD_MAP.md`; the `STABLE.md` entry of each tag decides.
9. **The 13 Oct design said ATP would port the reader only after Trade's shadow had proved it (N6).** The owner's
   takeover decision (2026-10-20) replaces that: ATP ports each layer as soon as it is tagged stable, runs its own
   shadow, and Trade's last work is the reader.
