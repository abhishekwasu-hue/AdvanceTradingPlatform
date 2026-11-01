# PAPER-NR audit (read-only) - "paper trading var restrictions nkot" / "Remove all restrictions"

Audited: origin/main @ 401b501f (merge of #827). Paths are under backend/app/. Nothing was edited, pushed or commented.

Rule applied: in PAPER, no entry is refused, reduced or held for a user restriction or for capital / margin; the would-be
refusal becomes an info note on the order trail / ticket. LIVE is unchanged. Exits are never blocked (they do not pass
through any gate below; ADR-0004). Platform safety stays in PAPER: kill switch, Phase-0 lock / live_trading, admin controls,
data and auth checks.

Legend - Type: U = user restriction, C = capital / margin, P = platform safety (incl. data / auth / missing setting).
Shared = the same code runs for PAPER and LIVE (S) / PAPER-only (PO) / LIVE-only (LO).
Change: SKIP = in PAPER, do not refuse / reduce; add an info note. KEEP = unchanged.

## 1. Every place a PAPER entry can be refused, reduced or held

### 1a. Single-leg pipeline: execution/signal_execution.py (execute_signal_for_user, entry_refusals)

| # | file:line | check | type | shared | change |
|---|---|---|---|---|---|
| 1 | signal_execution.py:49 | GLOBAL / TENANT / STRATEGY kill switch (kill_switch/checks.py:68) | P | S | KEEP |
| 2 | signal_execution.py:50 -> platform/controls.py entry_blocks | maintenance mode; Phase-0 lock (LIVE only); broker disabled by operator (LIVE only); user.trading_disabled_reason (admin) | P | S | KEEP |
| 3 | signal_execution.py:54 | user's own PAPER pause (Telegram /pause, web) - paper/service.paused_reason | U (his own stop button) | PO | KEEP by default (see Q-C) |
| 4 | signal_execution.py:62 -> risk_engine/by_default.py:504-523 | daily loss lockout (by_default.lockout :461) and entries-per-day slot (reserve_entry :537); flag risk_by_default | U | PO | SKIP (do not take a slot; note) |
| 5 | signal_execution.py:63 | organisation suspended (tenant_is_active) | P | S | KEEP |
| 6 | signal_execution.py:65-78 | plan has no LIVE; SEBI algo id; broker-uncertain | P | LO | KEEP |
| 7 | signal_execution.py:178 | approval-only gate (execution/approval.py:138 gate_refusal) - holds the entry as a proposal | P (org workflow / admin) | S | KEEP (see Q-C) |
| 8 | signal_execution.py:185, :201 | F&O ban period on the underlying (fo_limits.entry_refusal) | P (exchange rule) | S | KEEP by default (see Q-C) |
| 9 | signal_execution.py:193, :198 -> paper/experience.py:184 | experience-level gates for futures / naked short options (user's own "Limit me by my experience level", me/prefs.py MyLimitsPrefs) | U | S | SKIP |
| 10 | signal_execution.py:215-219 | holding / order type / price protection not set (order_defaults.needs_setting) | P (missing setting) | S | KEEP |
| 11 | signal_execution.py:234 | plan_error: contract not executable (no premium quote etc.) | P (data) | S | KEEP |
| 12 | signal_execution.py:251 | LIVE without broker adapter | P | LO | KEEP |
| 13 | signal_execution.py:268 -> platform/controls.py:782 clamp_config | admin risk ceilings clamp the user's risk settings | P (admin) - clamps U values | S | KEEP (moot in PAPER once #15-#30 are skipped) |
| 14 | signal_execution.py:274 -> by_default.apply_user_cap | user's own PAPER risk cap lowers risk per trade (REDUCE) | U | PO | SKIP (see Q-A) |
| 15 | signal_execution.py:282 -> risk_engine/guardian.py:211-219 | R10 cool-down after a stop-out (refuse) | U | S | SKIP |
| 16 | guardian.py:222-226 | P3 drawdown pause (refuse) | U | S | SKIP |
| 17 | guardian.py:227-230 | P2 drawdown step halves risk per trade (REDUCE) | U | S | SKIP |
| 18 | guardian.py:233-246 | M8 event day block / size cut (refuse / REDUCE); cfg.event_rules | U | S | SKIP |
| 19 | guardian.py:248-252 -> risk_engine/vix_gate.py check | India VIX gate: spike pauses bullish index entries, level cuts size (flag vix_gate) | U | S | SKIP |
| 20 | signal_execution.py:304 -> execution/prechecks.py:63 validate_instrument | expired contract (both modes); index / master checks (LIVE only) | P (data) | S | KEEP |
| 21 | signal_execution.py:332 -> prechecks.py:118 live_margin_cap | broker margin covers >= 1 unit; caps quantity | C | LO | KEEP (already LIVE only) |
| 22 | signal_execution.py:353 -> contract_execution.py:150 written_lot_cap | written option margin | C | LO | KEEP |
| 23 | signal_execution.py:393 -> risk_engine/hierarchy.py:193-257 | org risk-limit hierarchy (GLOBAL..INSTRUMENT: daily loss, strategy loss, loss/trade, order value, qty, open positions, trades/day, capital %, gross exposure, concentration); "limits unavailable" fails closed (:200); a BLOCK on MAX_STRATEGY_LOSS / MAX_DAILY_LOSS ENGAGES a STRATEGY / TENANT kill switch (:249, :253) | U (GLOBAL scope = admin, see Q-C) | S | SKIP in PAPER for non-GLOBAL scopes; a PAPER order must never engage a kill switch |
| 24 | signal_execution.py:395 -> guardian.py:261 portfolio_risk_block | R4 open risk at the stops + this trade vs cap | U | S | SKIP |
| 25 | risk_engine/risk_manager.py:100 | minimum reward:risk | U | S | SKIP |
| 26 | risk_manager.py:102 | max entries per day | U | S | SKIP |
| 27 | risk_manager.py:104 | max open positions | U | S | SKIP |
| 28 | risk_manager.py:106-107 | losses-in-a-row pause (loss streak) | U | S | SKIP |
| 29 | risk_manager.py:109-111 | daily loss limit | U | S | SKIP |
| 30 | risk_manager.py:85-92 | not tradeable / no entry or stop / non-finite level | P (data) | S | KEEP |
| 31 | risk_manager.py:134 | entry == stop under risk-based sizing | U-derived (sizing) | S | SKIP -> his lots / 1 lot + note (Q-A) |
| 32 | risk_manager.py:148 | risk per trade below one lot | U | S | SKIP -> his lots / 1 lot + note (Q-A) |
| 33 | risk_manager.py:155-162 | max lots per order: refuse a typed size / REDUCE a computed size | U | S | SKIP |
| 34 | execution/router.py:189-195 | size above max_quantity is capped; < 1 lot refused (max_quantity = his deployment max_lots / ticket lots in PAPER; margin cap in LIVE) | his own lots (sizing) / C in LIVE | S | KEEP (it is his lots, not a limit) |
| 35 | router.py:196-200 | pre_place_check (= #23 + #24) | U | S | SKIP via #23/#24 |
| 36 | router.py:221-235 | product / order style / price band not set | P (missing setting) | S | KEEP |
| 37 | router.py:242 | broker circuit breaker (paper venue too) | P | S | KEEP |
| 38 | execution/paper_adapter.py:200-219 | qty <= 0, no price, waiting limit | P (data) | PO | KEEP |

Paper venue capital: paper_adapter.py:339-341 get_margins reports funds, but nothing on main refuses a PAPER fill for cash. Paper capital is only the basis for % limits (by_default.capital_basis). #840 touches paper_broker.py / router.py / paper/service.py / new paper_settings.py - re-check after it merges.

### 1b. Multi-leg pipeline: execution/multileg.py (execute_structure)

| # | file:line | check | type | shared | change |
|---|---|---|---|---|---|
| 39 | multileg.py:139 | entry_refusals (= #1-#6, incl. #3/#4) | P + U | S | as #1-#6 |
| 40 | multileg.py:143 | approval-only gate | P | S | KEEP |
| 41 | multileg.py:148 | F&O ban | P | S | KEEP (Q-C) |
| 42 | multileg.py:153 | experience gates (structure legs) | U | S | SKIP |
| 43 | multileg.py:155-159 | non-finite levels; holding not set | P | S | KEEP |
| 44 | multileg.py:174-182 | leg quotes / economics fail | P (data) | S | KEEP |
| 45 | multileg.py:198 | admin ceilings clamp | P | S | KEEP (moot) |
| 46 | multileg.py:205 | user's PAPER cap (REDUCE) | U | PO | SKIP (Q-A) |
| 47 | multileg.py:207-215 | guardian (= #15-#19) | U | S | SKIP |
| 48 | multileg.py:232-238 | RiskManager (= #25-#33) | U | S | SKIP |
| 49 | multileg.py:241-245 | one lot risks more than risk per trade | U | S | SKIP -> his lots / 1 lot (Q-A) |
| 50 | multileg.py:246-247 | lots <= rules.max_lots (his own lots) | sizing | S | KEEP |
| 51 | multileg.py:248-257 -> :588 _live_lot_cap | broker margin / debit funds | C | LO | KEEP |
| 52 | multileg.py:259-268 -> :565 _hold_margin_refusal | SWING spread: estimated margin vs practice balance minus held margin | C | PO | SKIP (note: needed / free) |
| 53 | multileg.py:278-283 | R4 portfolio risk | U | S | SKIP |
| 54 | multileg.py:287-298 | hierarchy (= #23, incl. kill-switch engage) | U | S | SKIP (non-GLOBAL), no engage |

### 1c. Pre-pipeline gates (worker, approval, dispatcher, trigger engine)

| # | file:line | check | type | shared | change |
|---|---|---|---|---|---|
| 55 | workers/trading_worker.py:1613-1617 | venue closed | P | S | KEEP |
| 56 | trading_worker.py:1617-1640 | LIVE: Phase-0 lock, plan, IP check | P | LO | KEEP |
| 57 | trading_worker.py:1629, :1641 | replica lock lost / degraded | P | S | KEEP |
| 58 | trading_worker.py:1648-1649, :2088 | venue no-new-entries cut-off (market_data/calendar.py:196 intraday_cutoffs) | P (operational, venue session) | S | KEEP |
| 59 | trading_worker.py:2069-2076 | holding not set for the bot | P | S | KEEP |
| 60 | trading_worker.py:2077-2086 | swing SHORT on cash equity (cannot be held short overnight) | P (market rule) | S | KEEP |
| 61 | trading_worker.py:2093 | bot already holds its position (one position per bot) | bot structure | S | KEEP |
| 62 | trading_worker.py:2098-2104 -> :2588 _opposite_position | opposite positions "deny" (org risk_settings) | U | S (filters same mode) | SKIP in PAPER |
| 63 | trading_worker.py:2105-2117 | OI gates (deployment opt-in, option_chain/oi_gates) | his bot's entry condition | S | KEEP by default (Q-C) |
| 64 | trading_worker.py:2120 -> approval.py:233-248 propose_entry | PAUSE (#3); by_default.proposal_refusal (:593, = #4); portfolio caps (portfolio/caps.py:186: max open positions, correlated risk %, sector %; fails closed on read errors) -> EntriesPaused | U | PO | SKIP #4 and caps; KEEP pause |
| 65 | approval.py:529-534 execute_entry | portfolio caps again at Approve | U | PO | SKIP |
| 66 | approval.py:411-416, :495-509, :525-527 | bot gone / changed, deployment status, open position, re-quote drift / stale | P (data) | PO | KEEP |
| 67 | trading_worker.py:2152-2156 / 2370 | LIVE broker account unavailable | P | LO | KEEP |
| 68 | execution/core/dispatcher.py:188-197 | owner may not trade (role / scope) KEEP; pause KEEP; proposal_refusal SKIP; caps SKIP | P + U | PO | as stated |
| 69 | core/policy.py:47-63 | AUTO_LIVE refused; Auto PAPER off / approval-only -> proposal | P | S | KEEP |
| 70 | core/policy.py:66-84 level_problems | stop missing / wrong side | P (data) | S | KEEP |
| 71 | execution/trigger_engine.py:431-436 | LIVE resting entry refused (Phase-0 lock) | P | LO | KEEP |
| 72 | trigger_engine.py:462 | PAPER resting entry -> fno_ticket.place (= #77-#82) | as #77-#82 | PO | via #77-#82 |

### 1d. PAPER tickets, basket, fan-out (previews that decide can_place, then the pipeline)

| # | file:line | check | type | shared | change |
|---|---|---|---|---|---|
| 73 | paper/ticket.py:136-151 | setup / direction / level errors / time stop / cash underlying only | P (data) | PO | KEEP |
| 74 | ticket.py:161 | guardian refusals (= #15-#19) | U | PO | SKIP -> warnings |
| 75 | ticket.py:186-205 | min R:R; one lot over cap; size above cap needs override; override ceiling; override reason length | U | PO | SKIP -> warnings (override flow becomes unnecessary in PAPER) |
| 76 | ticket.py:208 | entry_refusals (= #1-#6) | P + U | PO | as #1-#6 |
| 77 | paper/fno_ticket.py:449 + :227-228 | "a future needs a stop-loss" (his own My limits switch stop_required_on_sold) | U | PO | SKIP -> warning (Q-C lists it) |
| 78 | fno_ticket.py:203-226, :214 | ticket errors: side, type, product, lots 1..MAX_LOTS(500, :65), tick, SL limit/trigger | P (input / data) | PO | KEEP |
| 79 | fno_ticket.py:~484-496 | entry_refusals, approval gate, experience gates, F&O ban | P + U | PO | as #1-#9 |
| 80 | fno_ticket.py:500-501 (_effective_config :378) | guardian refusals | U | PO | SKIP |
| 81 | fno_ticket.py:504-521 | RiskManager refusals; risk-sizing "Place N lots?" block | U | PO | SKIP |
| 82 | fno_ticket.py:466-476 | no live price, expiry passed | P (data) | PO | KEEP |
| 83 | paper/fno_basket.py:200-210 (_room), :223-225 | basket opens more positions than max open positions / trades per day leave | U | PO | SKIP |
| 84 | accounts/paper_fanout.py:150-159 | sub-account capital below estimated margin -> account SKIPPED | C | PO | SKIP -> place, note |
| 85 | paper/fno_position_actions.py:250, :309 | stop_required_on_sold on reverse / modify | U | PO | SKIP -> warning |

### Count (gate rows above, 85)
- User restriction to SKIP in PAPER: 36 rows (#4, 9, 14-19, 23-29, 31-33, 35, 42, 46-49, 53, 54, 62, 64, 65, 68-part, 74, 75, 77, 80, 81, 83, 85), of which REDUCE (size cut, not refusal): #14, 17, 18, 19, 33, 46.
- Capital / margin: 6 rows - 2 PAPER (#52 hold margin, #84 fan-out capital) -> SKIP; 4 LIVE-only (#21, 22, 34-LIVE part, 51) -> KEEP.
- Platform safety (incl. data, auth, missing setting, admin, exchange / venue rules): 43 rows -> KEEP.
- Grey rows kept by default, for the owner: #3 own pause, #7/#40 approval-only, #8/#41 F&O ban, #23 GLOBAL scope, #63 OI gates.
- Already mode-split today: only #4, #14, #46, #52, #64, #65, #83, #84 are PAPER-only; everything else in U is shared PAPER/LIVE code, so the change must branch on mode and leave the LIVE branch byte-identical.

### Side findings (facts, not part of PAPER-NR)
- hierarchy._measure (hierarchy.py:116-150) and trading/persistence.build_trading_day_state (:111) do not filter by mode: PAPER trades count in the LIVE limits' day loss / trades / open positions, and a PAPER order's hierarchy BLOCK can engage the TENANT kill switch, which then blocks LIVE entries too. PAPER-NR removes the second effect (no engage from PAPER); the first is LIVE behaviour and stays unless the owner orders otherwise.

## 2. Open PRs touching the same spots (get_files)

| PR | title (short) | overlapping files |
|---|---|---|
| #836 M68 PR-5 Risk limits | no pre-chosen ceiling / pause / opposite rule / VIX numbers | core/models.py, contract_execution.py, multileg.py, prechecks.py, signal_execution.py, paper/experience.py, fno_basket.py, fno_insights.py, fno_ticket.py, ticket.py, platform/controls.py (risk_ceilings(session, tenant_id)), portfolio/caps.py, by_default.py, guardian.py (dd_cut_pct, owner_id), guardian_routes.py, hierarchy.py, NEW risk_engine/own_risk.py, risk_limits.py, risk_engine/routes.py, vix_gate.py, trading_worker.py (_opposite_position: only a saved "deny") - LARGEST overlap |
| #839 M68 PR-2 Position size | no pre-chosen size | paper_fanout.py, core/models.py, multileg.py, NEW execution/position_size.py, signal_execution.py, fno_basket.py, ticket.py, risk_manager.py, risk_engine/routes.py |
| #840 M68 PR-7 Paper account | no pre-chosen starting capital / brokerage / slippage | incomplete_structures.py, india_costs.py, multileg.py, paper_broker.py, NEW paper_charges.py, router.py, signal_execution.py, trigger_engine.py, options_builder/routes.py, experience.py, fno_basket.py, fno_insights.py, fno_ticket.py, NEW paper/paper_settings.py, paper/service.py, ticket.py, portfolio/caps.py, by_default.py |
| #837 M68 PR-3 Exits | no ATP square-off / cut-off / timer / ceilings | approval.py, core/dispatcher.py, NEW exit_settings.py, group_exits.py, incomplete_structures.py, signal_execution.py, smart_execution.py, strategy_exits.py, me/prefs.py, fno_groups.py, fno_position_actions.py, fno_routes.py, ticket.py, trading_worker.py (may change #58 cut-off) |
| #838 M68 PR-6 Automation | no pre-chosen entry mode / limits | approval.py, NEW execution/automation.py, core/dispatcher.py, multileg.py, signal_execution.py, me/prefs.py, fno_ticket.py, ticket.py, trading_worker.py |
| #843 M68 PR-4 Options | no pre-chosen expiry / strike / side | smart_execution.py, me/prefs.py, options_builder/routes.py, fno_basket.py, fno_routes.py, fno_ticket.py, settlement_watch.py, trading_worker.py |
| #831 M68 PR-8 Suggestions | | options_builder/routes.py, paper/manual.py, paper/service.py |
| #854 M68 PR-8b interview symbol | | none of the audited files (ai/ and others only) |

Build order: PAPER-NR after #836 -> #839 -> #840 -> #837 -> #838 -> #843 -> #831 (#854 independent). Re-run this audit's
line numbers on main after they merge (#836 renames DRAWDOWN_CUT_MULTIPLIER, adds own_risk, changes the opposite rule and
risk_ceilings' signature; #839 adds position_size.py, the sizing chokepoint; #840 may add a paper cash check in paper_broker).

## 3. Minimal design: one mode check, applied at the shared producers

One new module `app/risk_engine/paper_nr.py` (no other new concept):
- `def advisory(mode: str) -> bool` - True only for mode PAPER (and, if the owner wants a flag per R-rules, while flag
  `paper_no_restrictions` is on; LIVE is never advisory).
- `def as_info(reasons) -> list[str]` - "Practice: no limit applied - <reason>" for the order trail / ticket warnings.
- `def unrestricted(cfg: RiskConfig) -> RiskConfig` - `risk_limits.apply_switches(cfg, enabled=set())`, i.e. every
  Risk-limits check reads "not set". DEF-0 already makes every check in RiskManager, the guardian (R10, P2/P3, M8) and
  R4 a no-op when not set, so those need no `if` of their own.

Where the check goes (the producers, not the ~20 call sites):
1. The effective risk config: today built 4 times (signal_execution.py:241-275, multileg.py:189-206,
   fno_ticket._effective_config :378, ticket.preview :151-158). Fold into one `entry_risk_config(session, user, mode, ...)`
   (or #839's position_size.py if it already does this) that returns `(cfg_used, info)`: in PAPER it evaluates the real
   config once read-only (RiskManager + guardian + R4) to collect info notes, then returns `unrestricted(cfg)`.
   Covers #14-#19 (except VIX), #24-#29, #31-#33, #46-#49, #53, #74, #75, #80, #81.
2. `guard_entry` (guardian.py:199): `if advisory(mode)`: reasons -> notes, size_multiplier = 1 (covers the VIX gate #19,
   which is flag-driven, not config-driven).
3. `hierarchy.evaluate` (hierarchy.py:193): `if advisory(ctx.mode)` and scope != GLOBAL: BLOCK -> note, and no kill
   switch engaged (#23, #54).
4. `signal_execution.entry_refusals` (:43): the PAPER block at :51-62 - by_default lockout / entries-per-day become info
   (no slot reserved); experience gates (#9, #42, #79) move behind the same split. Kill switch, platform controls,
   tenant and LIVE checks unchanged.
5. PAPER-only producers get the same one-line split: by_default.proposal_refusal (:593), portfolio/caps.entry_refusals
   (:186), trading_worker._opposite_position (:2588, PAPER deployments only), multileg._hold_margin_refusal (:565),
   fno_basket._room, paper_fanout capital skip (:151), fno_ticket stop_required_on_sold (:449, fno_position_actions :250/:309).
Result: one predicate, ~9 producer functions; the order pipelines, the router and every exit path are not touched.
Sizing in PAPER: his own lots (deployment max_lots / ticket lots / requested_quantity), else one lot (Q-A).

Tests (new file tests/test_paper_nr.py plus the existing suites):
- PAPER refusal -> info (parametrised, one case per SKIP row): daily loss (RiskManager + by_default + hierarchy), entries
  per day, max open positions, loss streak, min R:R, max lots per order, below one lot, stop cool-down, drawdown pause and
  halve, event block / cut, VIX spike / level, R4, every hierarchy limit type (and assert no kill switch row engaged),
  opposite position (worker), experience gate, portfolio caps (propose, approve, dispatcher), SWING hold margin,
  fan-out capital, ticket / fno_ticket / basket previews: can_place True and the reason in warnings; the order is FILLED
  and reasons_json carries the info line.
- LIVE refusals unchanged: the same parameter set in mode LIVE (fake broker, Phase-0 lock off in the test) -> REJECTED with
  the same reasons as on main; existing suites unchanged for LIVE: test_m64_risk_limits.py, test_phase_v1_guardian.py,
  test_risk_hierarchy.py, test_n1_f2_portfolio_caps.py, test_z5_p1d_risk_by_default.py (their PAPER assertions change to
  "info" only where PAPER-NR applies; with the flag off they pass unchanged).
- Platform safety still applies in PAPER: GLOBAL / TENANT / STRATEGY kill switch, maintenance, user trading disabled,
  organisation suspended, approval-only gate, expired contract, circuit breaker, missing holding / order type -> still
  REJECTED in PAPER; test_z10c_phase0_lock.py and test_phase0_deployment_defaults.py unchanged.
- Exits: test_def0_exits_and_gates.py, test_m55_s2_exits.py, test_exit_logic.py, test_worker_exits_without_deployment.py,
  test_kill_switch_api.py, test_cx_group_exits.py run unchanged; plus one call-graph test (as test_n1_f2 does) that no exit
  path imports paper_nr, and one test: kill switch engaged + PAPER position -> exit still closes it.

## 4. Ambiguous points for the owner (each with a default)

- Q-A Risk-based sizing (risk per trade %) in PAPER - a size setting or a restriction? Default: if he set it, it still
  computes the lots (it is his own lots setting, CLAUDE.md s.3); its refusals (below one lot, entry = stop, max lots per
  order) become his typed lots / one lot + info; no reduction by his PAPER cap, drawdown step, event day or VIX.
- Q-B Paper capital without a capital check: the balance can go negative. Default: allowed; the PAPER account shows the
  balance in red with "below zero - practice only", and each entry's info line shows estimated margin vs free balance;
  % settings keep reading the capital basis (starting capital + closed P&L) and, being info only, cannot block.
- Q-C Grey items kept by default (say if any should become info too): his own Pause button (it is his stop, like a kill
  switch); approval-only mode (an org workflow set by the admin); F&O ban period (an exchange rule); OI gates (his bot's
  own entry condition); GLOBAL-scope risk limits and the risk ceilings (admin controls). Also "a future needs a stop-loss"
  (his own My limits switch) - default: info in PAPER.
- Q-D (log only) PAPER trades count in the LIVE limits (no mode filter in hierarchy._measure / build_trading_day_state).
  Default: unchanged (LIVE keeps every check as it is).
