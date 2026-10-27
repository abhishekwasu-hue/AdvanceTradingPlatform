# Trading Terminal order: independent review against the code (CLAUDE.md section 6)

Order: `docs/owner-orders/ATP_SESSION_TRADING_TERMINAL.md` (README row 21; DECISIONS T-D1..T-D3, 24 Oct). It supersedes
Part 6 of `ATP_SESSION_TERMINAL_MASTER.md`. Review of 24 Oct 2026 against `origin/main` at `66cf80dc`. Docs only:
nothing is built here, no flag and no migration. Questions TT-1..TT-30 are in `docs/OPEN_QUESTIONS.md` with a
provisional default each. Model for format: `docs/design/TERMINAL_MASTER_REVIEW.md` (TM-1..TM-32).

Checked against: CLAUDE.md (sections 2-8), `docs/owner-orders/DECISIONS.md` (the owner principle of 23 Oct, the
answered TM rows, FNO-4-10, SE-7, T-D1..T-D3), ADR-0004 / 0006 / 0010 / 0014, the TM review, `docs/design/DATA_HUB.md`
(TMH-1..8), `docs/design/SMART_EXECUTION.md` (SE-1..8, SE7-1..6), open FNO-1-3, FNO-1-4, FNO-2-1, and the order of
the chart work (`ATP_SESSION_CHART_AND_COLOUR.md`, gesture rules). Everything below was read in the code; anything that
could not be verified from the repository is marked **unverified**.

## 0. Summary

Most of the Terminal's parts exist as separate pages: the one F&O ticket, the chain trading sheet, the basket with
smart execution (hedge first, INCOMPLETE state), the PAPER execution adapter, the data hub with live ticks to the
browser, server-side per-user prefs, saved chart layouts, the group / spot exits of deployment structures, journal
auto-capture, the loss lockout with "loosen next day" friction, the kill switch and the page tours. T1 (workspace,
layouts, status bar) can be built now with **no migration**. The later parts collide with the code or a standing
rule in ten places; each needs a decision before the part that depends on it:

1. **A LIVE manual entry has no lawful path today** (TT-1). z10c rule 2 (`tests/test_z10c_order_paths.py`) fails the
   build if any API route reaches a broker ENTRY. LIVE entries leave only from the trading worker, whose cycle is
   `WORKER_CYCLE_SECONDS` = 60 (`core/config.py:370`). "LIVE unchanged" plus "click to adapter under 150 ms" (T8) cannot
   both hold without a new, approved order sender.
2. **Approval-only mode refuses every manual F&O order** (TT-2). `fno_ticket.assess` calls `approval.gate_refusal`
   with `ORIGIN_AUTOMATIC` (`paper/fno_ticket.py:401`), and the trader pack turns `approval_only_entries` on
   (`platform/trader_pack.py:58`). With the pack on, every Terminal order would be refused. FNO-1-4 (the same
   question) is still open.
3. **The browser cannot see a tick in under 300 ms** (TT-3). TM-12 (accepted) caps the browser at one update per
   symbol per second (`hub.py:52`), the hub writes one Redis snapshot per second, and the SSE loop polls every second
   (`live/routes.py:43`): up to about two seconds end to end.
4. **No resting PAPER order and no trigger engine yet** (TT-5, TT-6). TM-2 (resting book + one trigger engine) is
   approved but not built. A paper order that would rest is refused (`fno_ticket.paper_fill`,
   `paper_adapter.py:15`). All exits are judged once per worker cycle on one quote (`position_monitor`). T2's chart
   limit / SL orders, the re-priced limit exit and all of T3 depend on TM-2.
5. **Manual basket legs are not a group** (TT-8). The basket places each leg as its own trade; Positions groups them
   only by the idempotency-key prefix (`fno_contracts.position_groups`). Group exits (T3.2) need a real group. If a
   real group is made, "Exit now" on one leg closes the whole structure (`execution/approval.py:600`). That clashes
   with exiting one leg and with partial exits.
6. **Discipline limits exist but run the other way** (TT-12). `risk_by_default` already has a loss lockout, a
   trades-per-day limit, a min reward:risk, risk per trade and the "loosen only from tomorrow" friction. But its values
   are platform defaults (2 % daily loss, 10 trades, R:R 1.5, 1 % risk), they apply to PAPER only, and the ticket
   refuses lots above the risk-per-trade cap. T6 asks for limits that are OFF until the user sets them, in rupees, and
   for LIVE too.
7. **"Exit all" and the kill switch are organisation-wide** (TT-13, TT-14, TT-15). `POST /api/kill-switch/emergency-exit`
   closes every open position of the tenant (all members), at prices the browser sends (`kill_switch/routes.py:204-283`).
   `/api/positions` and `/api/orders` return every member's rows (`trading/routes.py:102, 503`). The Terminal needs
   reads and Exit all scoped to the user, priced on the server.
8. **Chart gestures conflict** (TT-11). The chart order fixed long-press = crosshair and drag = pan. T2 wants
   long-press = order menu and drag = modify.
9. **Spread from the chart would suggest strikes unless the rules change** (TT-20, TT-21). Strike rules are relative to
   spot (ATM / ITM / OTM + offset), not to a level the user picks. `NEAREST` includes today's expiry. The basket fills
   missing settings with a "neutral start" (2 strikes, 1 lot, 50 % stop; `fno_basket.py:42-44, 161`). T4 says a
   missing setting must be asked for, never filled.
10. **Parity is partial** (TT-7). No engine supports "close of an N-minute candle". The options backtest has no
    trailing. Neither backtest has lock-and-trail, a rupee MTM stop or an account-level exit (section 5).

## 1. Per-section status

| Section | Status | Exists to reuse (file: what it does) | Gaps (new) |
|---|---|---|---|
| T1 Workspace | partial | `frontend/src/fno/CockpitDeskV2.tsx` + `fno/desk.ts` (a read-only desk: chart of his underlying with his strikes, chain part, basket; phone quick switch); `chart/EmbeddedChartV2.tsx`, `chart/MultiChartV2.tsx` + `charting/workspace.ts` (1-8 charts, link groups, saved layouts in `chart_layouts`, `charts/layouts.py`); `backend/app/me/prefs.py` (per-user JSON namespaces, 16 KB, compare-and-set version); `live/liveTicks.ts`, `live/LivePricesBanner.tsx` (hub state: live / reconnecting / renew); `paper/fno_basket.py:252` practice balance; `kill_switch/routes.py:106` tenant engage; `shell/navModel.ts`, `shell/pageFlags.ts`, `tours/content/*` | the `/terminal` page; a dockable grid (no grid library in `package.json`, TT-17); layouts in a `terminal` prefs namespace (TT-16); presets; the status bar (MTM today, open MTM, funds, margin used, feed state, mode chip, kill switch, privacy mask - mask not built anywhere, TM-19); phone tabs; an Orders panel (the Orders page reads trades, not `/api/orders` + `/events`); user-scoped reads (TT-15) |
| T2 Fast manual trading | partial | `fno/OrderTicketV2.tsx` (`TicketBody`, `Review` exported; B / S keys; lots with lot size; LTP / bid / ask helpers), `fno/ticketModel.ts`; `paper/fno_ticket.py` assess / place (one PAPER order through `execute_signal_for_user`, idempotency `fno:<user>:<client_ref>` `:480`, `phase0_lock.assert_paper`); `execution/paper_adapter.py` (TM-1); `fno/ChainTradingV2.tsx` + `ChainActionSheetV2.tsx` (tap -> sheet -> ticket / basket); `fno/BasketPanelV2.tsx` + `paper/fno_basket.py` (`execute_smart`); `fno/SmartExecutionV2.tsx`, `execution/smart_execution.py`; freeze slicing `instruments/freeze_limits.py`; margin view (TM-10); order trail `GET /api/orders/{id}/events`; chart lines: `chartCore.setLevels` (static price lines), drawing drag engine `charting/tools.ts`; crosshair link `charting/crosshairLink.ts`; `useTradesLayer.ts` (his trades, read only) | resting book (TM-2, TT-5); a modify path (FNO-4-10 approved for PAPER only, not built, TT-10); cancel; order lines + drag handles; split view of option / spread premium; partial exit (TT-9) and reverse; per-fill lines already exist (one trade row per order); lots per underlying (`TicketDefaults.lots` is one number); exit type default and re-price timer; editable shortcuts; confirms-off settings (TT-22); a LIVE manual path (TT-1); the approval-only rule (TT-2) |
| T3 Exits for sellers | partial | `trading/position_monitor.py:1016` `group_exit` / `structure_exit_reason` (spread value stop / target, P&L per unit) / `underlying_exit_reason` (breakevens, short-strike breach on spot) - deployment structures only; `trading/exit_rules.py` (trail %, break-even R, time; shared with `backtest/engine.py:126`); `backtest/options_engine.py:396-411` (same structure rules, underlying on bar high / low); `execution/incomplete_structures.py` (rules wait while INCOMPLETE); worker heartbeat `worker_heartbeats` + `observability/ops_alerts.py` (to the operator) | the trigger engine (TM-2) on ticks (TT-6); a trigger source per order (premium / spot, T-D2) with close / touch and timeframe (TT-7); group exits for manual baskets (TT-8); rupee MTM stop / target, lock profit, lock-and-trail; account-level day exits (TT-27); trailing by points / steps; heartbeat to the user (TT-26); words + rupees on the group |
| T4 Seller tools | partial | `decision_tools/whatif.py` (T+0, later day, IV shift) and `routes.py`; `options_builder/payoff.py`, `optionsBuilder/PayoffCanvas.tsx`; basket evaluate (payoff, breakevens, margin estimate); `option_chain/charts.py` (ATM straddle, ATM IV, smile); India VIX in the context panel; `chart_analyze/strikes.py` ("strikes from your settings", never picks - the pattern for spread-from-chart); `instruments/spreads.py:364` `plan_structure`; `paper/fno_contracts.py:143` roll target; `paper/settlement_watch.py` (delivery warning) | 9.1 sliders / 9.2 stress (not built); premium-decay and MTM-vs-spot charts (TM-20, not built); IV + VIX overlay; level-relative strike rules and "skip today's expiry" (TT-20); roll what-if; expiry-today list |
| T5 Algo panel | partial | `deployments/routes.py:711/729/754` pause / resume / stop (PAUSED keeps exits); `kill_switch/routes.py:128` strategy switch; `signals/SignalsV2.tsx` + `SignalPartsV2.tsx` (Approve / Skip, countdown, snooze, his levels); origin derivable from `TradeRecord.strategy_id` (`manual_fno`) / `deployment_id`; journal source MANUAL / STRATEGY (`journal/capture.py:78`); Copilot read tools `ai/tools/copilot*.py` | Reduce lots (needs the split, TT-9); Exit all per bot (TT-14); the bot MTM line; a compact Signals list inside a panel; origin filter; the signal band on the chart (structure words; his own signals only) |
| T6 Discipline | partial | `risk_engine/by_default.py` (daily loss lockout, trades per day, open positions, min R:R, loosen-from-tomorrow `plan_change`); `risk_engine/loss_streak.py` (pause after N losses: next session / until resume); `risk_lockouts` rows; `RiskManager` refusals; journal auto-capture with a chart reference | rupee daily loss, timed cool-off, lots per order / underlying, all OFF by default (TT-12); LIVE coverage (owner principle); "most this order can lose" exists on the ticket (`max_loss`); the kill switch and mask on the status bar; the journal's chart at exit already a reference (no image) |
| T7 Practice | not started | `charting/ReplayBar.tsx` + `replay.ts` only in the legacy `pages/ChartWindow.tsx` (step / play, causal price-action layer; `text-xs`, TM-30) | master Part 10 (speed, replay sandbox orders TM-22, options replay) - not built; T7 waits for it (TT-24) |
| T8 Speed | partial | hub streams, reconnect / renew states (`market_data/hub.py`), SSE `ticks` (`live/routes.py`), `execution/ops_throttle.py`, broker rate budgets (`brokers/rate_budget.py`, per-day buckets TM-27), INCOMPLETE state | the latency targets cannot be met on the TM-12 design (TT-3); per-tab cap 50 symbols (TT-4); positions are polled every 30 s (`PositionsV2.tsx:54`); a visible order queue state; measurement scripts |
| T9 Compliance | ok, with care | advice-word filters, `signal_language`; `chart_analyze/strikes.py` (source "your_settings"); `compliance/static_ip.py`, `tagging.py`, `in_sebi.json` (generated `docs/COMPLIANCE_IN.md`) | the lawyer's line (TT-30; A6.3's single list does not exist yet); keep the neutral start out of T4 (TT-20) |
| T10 Tests | partial | `test_z10c_order_paths.py`, `test_tm1_paper_adapter.py`, `test_tm7_smart_execution.py`, `test_se7_live_multileg.py`, `test_tm1_data_hub*.py`, `test_exit_rules_and_backtest_depth.py`, `test_phase_w_option_backtest.py`; e2e `chain-ticket-v2`, `basket-v2`, `fno-workspace-v2`, `chart-axes-v2` | the "same order object from every entry point" test; trigger parity suite; shortcut / layout tests; the comparison page |

## 2. The modules the order names: verified

| Named in the order | Where (verified) | Notes |
|---|---|---|
| OrderTicketV2 | `frontend/src/fno/OrderTicketV2.tsx` (370 lines) | a modal sheet / side panel; `TicketBody` and `Review` are exported, so it can be embedded in a panel |
| ChainTradingV2 | `frontend/src/fno/ChainTradingV2.tsx` (`useChainTrading`, `ChainBottomStripV2`) | on only with `paper_account`; re-reads "P" markers every 30 s |
| ChainActionSheetV2 | `frontend/src/fno/ChainActionSheetV2.tsx` | Buy / Sell / Add to basket / chart / watchlist / alert / Exit now |
| BasketPanelV2 | `frontend/src/fno/BasketPanelV2.tsx` (`BasketBody`, `ConfirmView`, `ResultView` exported); basket kept in the browser (`fno/basket.ts`) | saved baskets in prefs `fno_baskets` |
| SmartExecutionV2 | `frontend/src/fno/SmartExecutionV2.tsx`; backend `execution/smart_execution.py`, `multileg_smart.py`, `incomplete_structures.py`, `incomplete_routes.py` | flag `smart_execution`; without it the old basket / multi-leg paths still run (`fno_basket.py:389-401`, `multileg.py:270-297`) |
| CockpitDeskV2 / desk.ts | `frontend/src/fno/CockpitDeskV2.tsx`, `fno/desk.ts` | read only; "nothing here trades" |
| PositionsV2 | `frontend/src/trading/PositionsV2.tsx` (default export page, `PositionRowV2`) | polls `/api/positions` (tenant-wide) every 30 s |
| chartCore | `frontend/src/chart/chartCore.ts` (525 lines) | `setLevels` draws static lines; no order-line drag, no click menu |
| PAPER adapter (TM-1) | `backend/app/execution/paper_adapter.py` | in-memory books per flow (SE-1); fills only what the price reaches now |
| ExitRules | `backend/app/trading/exit_rules.py` | trail %, break-even R, time; no ladder (TM-17 not built) |
| Data hub | `backend/app/market_data/hub.py` (flag `data_hub`), `live/routes.py` `PUT /api/live/ticks`; frontend `src/live/liveTicks.ts`, `LivePricesBanner.tsx`, `charting/ltpHub.ts` | in the trader pack |
| Smart execution | see above | SE-7 merged (`test_se7_live_multileg.py`) |
| Trigger engine / resting book (TM-2) | **not built** (no `resting` / trigger module; no branch on origin) | approved 23 Oct |
| Market Replay (Part 10) | **not built** (legacy ReplayBar only) | |
| Kill switch | `backend/app/kill_switch/` (GLOBAL / TENANT / STRATEGY; no USER scope, `core/enums.py:114`) | no user-facing button in the v2 UI (only the admin's global switch) |
| Risk limits | `risk_engine/by_default.py` (flag `risk_by_default`), `risk_manager.py`, `loss_streak.py`, `guardian.py` | settings JSON on `paper_accounts.risk_settings_json` |
| Journal | `backend/app/journal/capture.py` (flag `discipline_engine`) | a chart snapshot is a reference redrawn later; F&O drafts `paper/fno_journal.py` |
| Prefs store | `backend/app/me/prefs.py` (`GET /api/me/prefs`, `PUT /api/me/prefs/{namespace}`) | allow-list `NAMESPACES` `:323` |
| Page tours | `frontend/src/tours/` (`content/*.ts`, `pages.ts`, prefs `tours`) | |
| Flag catalog | `backend/app/platform/flag_catalog.py` + `controls.FEATURE_FLAGS` (`controls.py:89`), test `tests/test_ux1_flag_catalog.py` | no `terminal` flag yet |
| Trader pack | `backend/app/platform/trader_pack.py` (`PACK`) | includes `approval_only_entries`, `risk_by_default`, `data_hub`, `smart_execution` |

## 3. Conflicts and risks against the code and the rules

### ADR-0004: exits are never blocked
- Holds today: every exit goes through `close_position` / `close_group` (shorts first; naked-short guard), and none of
  the refusal paths (kill switch, lockout, phase-0 lock, approval-only) touch them. T6 must add limits only to
  `entry_refusals` / `RiskManager.validate_and_size` (entry-only by construction).
- Risks: (a) an "exit line can never be dragged away" - a drag that removes the last stop needs a confirm, but the
  confirm must not delay a stop that is firing; (b) a re-priced limit exit (T2) must fall back to market / marketable
  limit per his setting (TM-3), never wait forever; (c) "Close the filled legs" and group exits pause while INCOMPLETE
  (`rules_paused`) - legs must stay closable (they are); (d) Exit all must not depend on browser prices
  (`emergency_exit` skips a position whose price the browser did not send - an exit silently not done, TT-14).

### ADR-0006: AI never acts on orders
- Holds: the Copilot tools are read only; T5 adds only an explain / risk-question panel. Nothing in the Terminal may
  pass Copilot output into an order object. Test: the Terminal's order builder imports nothing from `src/copilot`.

### CLAUDE.md section 3: signals, not advice
- Spread from the chart (T4) must reuse the `chart_analyze/strikes.py` stance: every value from his settings, flagged
  as his. The basket's neutral start (`NEUTRAL_WIDTH_STEPS`, `NEUTRAL_LOTS`, `NEUTRAL_STOP_PCT`) is a platform default
  and must not be used there (TT-20). The level-to-strike mapping itself is a rule and must be his setting.
- T-D2's "spot" default for spreads chooses the source, not the level: no level may be pre-filled (TT-21).
- Pre-existing, flagged only: `risk_by_default` starts with a min reward:risk of 1.5 (`config.py:839`) on the equity
  plan ticket - a reward:risk filter the user did not state unless he keeps it. Part of TT-12.
- The signal band on the chart (T5) is allowed on his own signals only (symbol + area as a range); tours and
  screenshots for the comparison page use fixtures older than three months, no names.

### The one order path
- PAPER entries all end in ONE pipeline, `execute_signal_for_user` (`execution/signal_execution.py`). The front doors
  are several: the F&O ticket (`fno_ticket.place`), the basket (per leg through the ticket), the equity plan ticket
  (`paper/ticket.py:303`), the console (`main.py:649`), approvals (`approval.py:370`), webhooks, the public API, the
  intent dispatcher, the worker (deployments) and `execute_structure` (multi-leg).
- A real second path exists today behind a flag: with `smart_execution` off, the basket uses the old sequencing
  (`fno_basket.py:389-401`) and the multi-leg executor the old wings-first / auto-unwind path (`multileg.py:297` vs
  `:279`). The Terminal should require `smart_execution` (TT-28).
- The Terminal adds no new pipeline: every entry point builds one `OrderAsk` (or a basket of them) and calls the ticket
  / basket routes. The T10 test "same order object from every entry point" belongs in the frontend (one
  `buildOrder()` used by chart, chain, panel, keyboard, basket) plus a backend test that the ticket and basket give
  identical rows for the same ask.

### Idempotency
- `orders.idempotency_key` is unique per tenant (`db/models.py:695`), but the ticket sets a key only when the client
  sends `client_ref` (`fno_ticket.py:480`). A keyboard Enter or a chart click without one could place twice (TT-29).
- The basket's `basket_ref` is required (`fno_routes.py:182`). Freeze slices carry their own keys (smart execution).

### The z10c order-path guard
- Rule 2: no API route may reach a broker ENTRY. Every PAPER Terminal order is broker-less (GATED callees) and
  passes. A LIVE manual entry from a route would fail the build by design (TT-1).
- Rule 3: a new route that can reach an EXIT / PROTECTIVE call site (Exit all, a LIVE stop modify) must be added to
  `ROUTES_REACHING_BROKER_ORDERS` with its reason (TT-10, TT-14).
- Rule 4: a dedicated LIVE order sender would be a new process entry point in `PROCESS_ENTRY_POINTS` (TT-1).

### Tenancy
- `/api/positions`, `/api/trades`, `/api/orders` return the whole tenant (`trading/routes.py:90-127, 503-516`); the
  emergency exit and the TENANT kill switch act on the whole tenant. A self-signup creates its own tenant
  (`auth/routes.py:227`), so solo users are unaffected, but team members would see / stop / close each other's
  positions from the Terminal. Default: Terminal reads and Exit all filter on `user_id` too (TT-15); the kill switch
  question is TT-13. Every new route goes into `tests/tenancy_route_table.py`.
- Layout prefs are already per user and tenant (`user_prefs` unique on tenant, user, namespace).

### The owner principle: no paper / live split
- Pieces that are PAPER-only in code today: the ticket and basket (MODE "PAPER"), `risk_by_default` (PAPER entries
  only), Exit now (LIVE refused, `approval.py:583`), FNO-4-10 (approved "PAPER only" before the principle). Each is
  lawful in Phase 0; the Terminal must be built so the SAME object reaches the LIVE path when Abhi turns LIVE on.
  TT-1 / TT-10 / TT-12 settle how.

### Phase-0 PAPER lock
- Untouched: `phase0_lock.assert_paper` in `fno_ticket.place`, the chokepoint in `brokers/rate_budget.py`. The
  Terminal shows the mode chip from the server (live_trading / phase0 state) and never writes either flag.

### Parity (backtest = paper = live)
See section 5: today's parity covers ExitRules (trail %, break-even R, time) in `backtest/engine.py` and the monitor,
and the structure / underlying exits in `options_engine.py` and the monitor. Everything T3 adds is new in all three
places.

## 4. Migrations

| Need | Where it lives | Migration? | Additive? |
|---|---|---|---|
| Layouts per user (T1) | a new prefs namespace `terminal` (`user_prefs` row; 10 layouts + shortcuts + confirms fit in 16 KB, size test) | **no** | - |
| Resting PAPER orders + trigger store (T2 / T3, TM-2) | one table for resting orders and armed triggers (owner, source premium / spot, level, close / touch, timeframe, state, fired_at, idempotency key unique) | **yes** | yes, new table only; deployed after 15:30 IST |
| Group exits for manual baskets (T3.2) | `trades.leg_group_id` + `group_meta` (existing columns) | no | - |
| Partial exit / reduce (T2, T5) | split the trade row (TM-17's method): a new row, no new column | no | - |
| Discipline limits (T6) | new keys in `paper_accounts.risk_settings_json`; cool-off derived on read from the last closed trades (no "until" column needed) | no | - |
| Kill switch per member (only if TT-13 says so) | `kill_switches.user_id` nullable + a partial unique index | yes, only then | yes |
| Account-level day exits (T3.2) | the user's own values in prefs / risk JSON; fired state in the TM-2 table | covered by TM-2 | - |
| Replay orders (T7) | a sandbox in memory (TM-22) | no | - |

## 5. What the engines support today (parity)

| Rule | Live / paper monitor | `backtest/engine.py` | `backtest/options_engine.py` |
|---|---|---|---|
| Premium stop / target on one leg | yes (once per cycle, on a quote) | n/a (spot) | single option: premium floor / ceiling on the bar close |
| Spot (underlying) level | deployment structures: breakevens, short strikes on touch of the quote | spot stop / targets on bar high / low | underlying levels on bar high / low (`:396`), filled at the level |
| Close of an N-minute candle | **no** | no | no |
| Trailing stop % | yes (`apply_exit_rules`, quote as high = low) | yes (bar range) | **no** (only time rules pass, `:252`) |
| Trail by points / steps, start after X | no | no | no |
| Spread value stop / target | yes (`structure_exit_reason`) | n/a | yes, on the bar close (`:411`) |
| P&L per unit stop / target | yes (`pnl_stop`) | n/a | yes |
| Rupee MTM stop / target on a group | no | no | no |
| Lock profit / lock-and-trail | no | no | no |
| Account-level day stop | no (the lockout only refuses entries) | no | no |
| Evaluation cadence | worker cycle, 60 s default | every bar | every bar |

Parity default (TT-7): ONE pure function per rule (next to `exit_rules.py`) called by the monitor / trigger engine and
by both backtest engines; "close" reads only closed candles of his timeframe; "touch" reads ticks live and bar high /
low in a backtest (a backtest cannot see inside a bar - the order of stop vs target inside one bar is resolved the
pessimistic way, as `options_engine.py:383` already does; the test suite and the UI say so).

## 6. Recommended PR for T1 (the first build part)

Branch `claude/terminal-t1-workspace`, draft PR, **no migration**, behind `ui_v2` + new flag `terminal` (OFF).

Backend
- `app/platform/controls.py` + `app/platform/flag_catalog.py`: flag `terminal` ("The Terminal: chart, chain, order
  panel, positions and orders on one screen", area trading, depends `ui_v2`, needs "the data hub for live prices; smart
  execution for multi-leg orders", status `unfinished`). NOT added to `trader_pack.PACK` until Abhi's ✅ (TT-25), with a
  test.
- `app/me/prefs.py`: namespace `terminal` - `layouts` (at most 10: name <= 40 chars, preset base, panels with id from
  an allow-list, x / y / w / h on a 12-column grid, open / closed, chart count 1-4, phone tab order), `active`,
  `confirms` (per order type, Exit all / reverse not storable), `shortcuts` (map, on / off). Validators: no overlap,
  inside the grid, unique names. Presets are NOT stored (frontend code), so "reset to preset" needs no server.
- `app/terminal/status.py` + `routes.py`: `GET /api/terminal/status` (flag-gated, `get_current_user`, filters on
  tenant AND user): today's realised P&L, open MTM from the hub snapshot (else the last mark with its time), practice
  balance (`fno_basket.practice_balance`), margin used as ATP's labelled estimate (TT-19), the hub state, the mode
  (from `live_trading` / `phase0_paper_only`, read only), the kill switch state. Read only; no order path.
- `tests/tenancy_route_table.py`: the new route. z10c tests must stay green unchanged (proves no new order path).

Frontend (`src/terminal/`)
- `TerminalPageV2.tsx` (lazy chunk), `layoutModel.ts` (pure: presets "Option seller" / "Quick trade" / "Multi-chart",
  move / resize / collide / clamp, save / rename / switch / reset, version conflict), `grid/` (own 12-column CSS grid
  with drag handles and keyboard move / resize; no new dependency, TT-17), `panels.ts` (registry wrapping the existing
  components: `EmbeddedChartV2` x1-4, a compact chain ladder from `useChainTrading`, `TicketBody` / `Review` from
  OrderTicketV2, `PositionsV2` rows, an Orders panel on `/api/orders` + `/events` (user filter), `BasketBody`, Bots &
  signals and Payoff as "coming in a later part" plain-word placeholders), `StatusBarV2.tsx` (MTM, funds, margin, feed
  state from `liveTicks`, mode chip, kill switch with a confirm - TENANT engage per TT-13, privacy mask per device per
  TM-19), `PhoneTabsV2.tsx` (Chart · Chain · Order · Positions · Orders · Bots; status bar = MTM + funds + mode; 44 px
  targets).
- Navigation: `ShellKey` "terminal" in `shell/navModel.ts` (Trading group, flag `terminal`), `pagesV2.ts`,
  `pageFlags.ts`, More sheet / command bar via the model; the nav test becomes 13 entries with the flag off, 14 with it
  on (TT-18). Tour `tours/content/terminal.ts` (T-D1; no names or prices in the tour text).
- Text sizes from tokens (no `text-xs` below 14 px), colours from tokens (`check:v2-colours`), the Terminal chunk
  measured against `check:bundle`.

Tests
- pytest: prefs `terminal` validation (11th layout refused, overlap refused, 10 full layouts fit in 16 KB), per-user
  isolation (another member's layout and status never read), status route flag-off 404, status excludes another
  member's positions, trader pack excludes `terminal`, flag catalog parity.
- vitest: `layoutModel` (presets valid, save / restore round-trip, reset, conflict 409 path), status bar (feed states,
  mask hides every amount, phone shrink), phone tabs, empty / loading texts (no ids, no developer text), nav visibility
  with the flag on / off.
- e2e (Playwright, mocked API): `terminal-v2.e2e.ts` desktop 1440 and phone 360 / 390 - open, switch preset, move /
  resize a panel, save, reload, the layout is back; screenshots light / dark into the one comparison page.
- Measurement in the PR (T8, honest numbers only): tick-to-screen on the current SSE path, chart pan fps with 5,000
  bars, layout shift (CLS) on panel updates.

The rest, in the order's sequence (one draft PR each): TM-2 resting book + trigger engine (migration, after 15:30
IST) -> T2 (needs TT-1, TT-2, TT-9, TT-10, TT-11, TT-29 answered) -> T3 (TT-6, TT-7, TT-8, TT-21) -> T5 + T6 (TT-12,
TT-13, TT-14) -> T4 (TT-20, needs 9.1 / 9.2 / TM-20) -> T7 (after Part 10).
