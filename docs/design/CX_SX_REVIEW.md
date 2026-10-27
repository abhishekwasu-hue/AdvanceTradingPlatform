# CX / SX independent review against the code (P1-6c, P1-6d)

Review of docs/owner-orders/ATP_ORDER_CX_SX.md against `main` at 0254913f (alembic head `7e2a4c6b8d10`), before any
build (CLAUDE.md §6). Binding: ADR-0004 (exits never blocked), ADR-0006 (AI never places / blocks / changes orders),
T-D2 (spot default for spreads), TT-7 (touch / close + TF), TT-8 (manual basket = one group), TM2-4 (restart reconcile).
Paths are under `backend/app/` unless they start with `frontend/`.

## 1. Reuse map

| Item | What exists (file:line) | What is missing |
|---|---|---|
| CX1 select | Manual basket group: `paper/fno_groups.py:55` `tag` (`group_meta.basket_group`, `leg_role`), `:99` `group_of` (open legs of one basket, pre-TT-8 baskets via order keys `:79`). Algo structures: `TradeRecord.leg_group_id` (`db/models.py:399`). | No "set of positions" object; no "all open positions" scope; selection must be validated as one holder, one tenant, all open. |
| CX2 trigger | Spot + premium trigger of ONE position: `execution/resting_book.py:50` `Trigger`, `:171` `arm_exit`. Structure value / P&L-per-unit math: `trading/position_monitor.py:1016` `group_exit`, `:1030` `structure_exit_reason` (net credit, debit, short-strike breach). | ₹ MTM of a set of legs; net premium of an arbitrary set; % of net premium; a group-level row in the book; trailing for groups. |
| CX3 check mode | `trading/trigger_rules.py:31` `touched`, `:72` `closed_candles` (no forming bar), `:105` `close_trigger(armed_at=)`; engine `execution/trigger_engine.py:135` `_reached`. | Close mode only works on ONE symbol's candles. MTM / net-premium close needs a synthetic candle (sum of the legs' 1-min closes per bucket) - new pure function next to `closed_candles`. |
| CX4 fire | Shorts first, wings held while a LIVE short is open: `position_monitor.py:1108` `close_group`; `execution/smart_execution.py:73` `exit_sequence`, `:148` `run(exiting=True)`; LIVE partial-fill resume inside `close_position` (`position_monitor.py:899`, `resume_only`, `_partial_aware` `:308`); protected limit where a venue forbids MARKET: `execution/marketable_limit.py:68` `to_venue_order` (used by `brokers/kotak_neo.py:605`, `brokers/groww.py:573`, `execution/paper_adapter.py:209`), `:90` `reprice_until_filled`. Notify -> Telegram: `notifications/service.py:10` `notify` (queues Telegram by severity). | A group FIRING state that retries the legs still open on every pass until all are closed; one summary notification (today each leg also sends its own EXIT notification). |
| CX5 safety | Engine runs before every entry gate, no kill-switch / limit / approval read: `workers/trading_worker.py:1287-1295`; claim exactly once `trigger_engine.py:157` `_claim`; restart `:586` `reconcile_on_start`, ack timeout `:555` `_overdue` + `core/config.py:374` `TRIGGER_ACK_TIMEOUT_SECONDS`; Phase 0 lock refuses ENTRY orders only (`brokers/rate_budget.py:290`); proofs `tests/test_tm2_owner_proofs.py:144,193,217,281,348,363`. | The same guarantees for a group row (claim, take-over, reconcile per leg); the AI-import guard test extended to the new module. |
| CX6 UI | `frontend/src/trading/PositionsV2.tsx` (622 lines) with `GroupExitSheetV2` (`fno/GroupExitsV2.tsx`, `fno/groupExits.ts`); status bar only on unmerged `claude/terminal-t1-workspace` (`frontend/src/terminal/StatusBarV2.tsx`, `backend/app/terminal/status.py`). | "Set combined stop-loss" sheet, preview words, chip with distance, status-bar entry. |
| SX1 ticket | Basket ticket `frontend/src/fno/BasketPanelV2.tsx`, `fno/basketModel.ts`; backend `paper/fno_basket.py:206` `preview`, `:394` `execute` (+ `:308` `execute_smart`), group tagged after fill (`:388` `_group`). Prefs: `me/prefs.py:254` `TradingDefaultsPrefs` (namespace `trading_defaults`, frontend pre-fill only). No dedicated spread builder on main. | "Exits for this strategy" section; the exit armed in the same request as the basket (idempotent on `basket_ref`); new `trading_defaults` fields. |
| SX2 trailing | Single-leg trail / break-even, stop only tightens: `trading/exit_rules.py:30` `ExitRules`, `:85` `apply_exit_rules`. `resting_book.MAX_TRAIL` is the order-trail log length, not trailing. | Group trailing: trail-SL, lock, lock-and-trail, to-break-even on MTM / premium / spot, monotonic. |
| SX3 OCO | One-position OCO: `resting_book.py:254` `cancel_for_trade` after a fill (`trigger_engine.py:317`). | SL + target of one strategy as ONE row (OCO by construction), plus cancel of the legs' own armed exits. |
| SX4 leg vs strategy | Leg stops: `TradeRecord.stop_loss` judged by the monitor (`position_monitor.py:1227` `monitor_open_positions`); armed leg exits in the resting book. | A rule for who cancels whom (section 4). Today the ticket REQUIRES a stop on every sold option (`paper/fno_ticket.py:213-214`, `fno/basketModel.ts:131-133`): conflicts with "leg-level SL stays optional". |
| SX5 review | `fno_basket.py:79` `hedge_margin`, `:261` `smart_view` (margin with hedge benefit). | Exits sentence on the review. |
| SX6 / SX7 | As CX5. No re-entry code path exists in the basket or the engine (good). | Chip on the group; a test that nothing re-enters. |

## 2. Data model (migration: YES, additive, `ROLLBACK_SAFE = True`)

New table `group_exits` (one row = one combined exit; CX and SX are the same row, `origin` CX | SX):
- `id, tenant_id, user_id, client_ref` (unique per tenant + user, as `uq_resting_order_client_ref`), `mode` PAPER | LIVE,
  `origin`, `basket_ref` (SX / spread group, nullable), `scope` SELECTED | ALL_OPEN | GROUP;
- `legs_json`: the trade ids it covers at the current version (frozen; see 4.2), with each leg's side, qty, entry price;
- `measure` MTM_INR | NET_PREMIUM | NET_PREMIUM_PCT | SPOT; `spot_symbol`, `spot_exchange`, `spot_side` (SPOT only);
- `sl_value`, `target_value` (both nullable, at least one set), `basis_premium` (net premium at entry / at set time,
  for % and the preview);
- `trail_kind` NONE | TRAIL | LOCK | LOCK_TRAIL | BREAK_EVEN, `trail_json` {start/A, lock/B, step/X, by/Y};
  state `best_value`, `current_sl`, `locked_at` (lock fires once), never moved against the trade (pure function + test);
- `check_mode` TOUCH | CLOSE, `timeframe_minutes`;
- `status` ARMED -> FIRING -> DONE | PARTIAL_STUCK; or CANCELLED | SUPERSEDED; `fired_kind` SL | TARGET;
  `fired_at`, `fired_value`, `version` (int, +1 per edit; edits are `UPDATE ... WHERE version = :v AND status = ARMED`),
  `trail_log` (JSON list {at, status, note}, as `resting_book.add_trail`), `created_at`, `updated_at`.
- Index (`tenant_id`, `status`). No change to `paper_resting_orders` or `trades`; legs' exits keep their own rows.
Downgrade drops the table; previous code never reads it (code-only rollback ok). One migration, after 15:30 IST.

## 3. Engine

3.1 Where: a new module `execution/group_exits.py` (store / validate / list / cancel, like `resting_book`) and a pass
`run_group_exits` inside `execution/trigger_engine.py`, called in `run_exits` FIRST (stops before targets, groups before
single-leg rows, so a strategy SL is not pre-empted by a leg TARGET in the same pass). Worker call sites unchanged
(`trading_worker.py:1290`, `tick_pass` `:674`).

3.2 Prices (one reading per symbol per pass, `_Prices` `trigger_engine.py:116`): legs' LTP from the cycle's
`market_data.get_ltp` or the hub ticks (`_tick_prices` `:661`, max age 5 s); spot from the same feed via
`position_monitor.underlying_exchange`. MTM = Σ sign × (ltp − entry) × qty, gross of charges. Net premium = Σ short
ltp − Σ long ltp (per unit, ratio-weighted as `group_exit` `:1016`). **Any leg without a price -> no decision this pass**
(as `_monitor_group` `:1087`); never a partial value. PAPER and LIVE use the same function and the same feed.

3.3 Candle close: SPOT reuses `close_trigger` on the underlying's minutes (`market_data.get_today_minutes`). MTM /
premium: new pure `combined_closes(bars_by_leg, weights, tf, now)` building each closed bucket from every leg's 1-min
close; a bucket with any leg missing is skipped; `armed_at` respected (no look-ahead; parity tests with the backtest).

3.4 Fire exactly once: `ARMED -> FIRING` by a conditional UPDATE (copy of `_claim` `:157`). Then the legs close through
`position_monitor.close_group` (shorts first; LIVE wings held while a short is open; LIVE partial fills resumed by
`close_position`). Each pass while FIRING: legs already closed are skipped, open legs retried shorts-first; DONE when all
legs are closed; after N passes with a stuck short -> PARTIAL_STUCK + CRITICAL notification (close_group already
alerts). Duplicate safety per leg is `close_position`'s own claim (PAPER, review M3) and exit ledger / tag (LIVE).

3.5 Restart (TM2-4): `reconcile_on_start` also takes every FIRING group (exits first) and runs the same "retry open
legs" step: a leg already closed before the crash is adopted (PAPER: `trade.exit_time`; LIVE: the broker order by tag via
`close_position`), an open leg is sent once. Ack timeout reuses `_overdue`.

3.6 What could block today (checked): kill switch - only entries (`execution/signal_execution.py:48`,
`paper/fno_ticket.py:497`); Phase 0 lock - entries only (`brokers/rate_budget.py:290`); approval-only - targets of
approval-only positions become "Exit now?" proposals only in the monitor (`approval.py:471`), the engine never reads it;
limits / risk - not on the exit path. **Must NOT be used by the engine:** `approval.exit_now` (`approval.py:602`, refuses
LIVE) and therefore `fno_basket.close_filled` (`:333`); and `smart_execution.run` stops at the first failed leg with no
retry. Real gaps found:
- F1 `close_group` holds wings only for LIVE (`position_monitor.py:1136-1137`): in PAPER, a short with no price stays open
  while its wings close (PAPER != LIVE, a naked short in paper). Fix: hold wings in both modes.
- F2 `tick_pass` is PAPER-only (`trigger_engine.py:674`): a PAPER group fires on ticks, a LIVE one only each cycle -
  different timing. Fix (LIVE switch day): run LIVE evaluation on ticks and hand the send to the cycle, or document.
- F3 `fno_groups.exit_group` overwrites `leg_role` on the rows (`fno_groups.py:216`) - harmless today, but the CX
  engine must take roles via `role_of`, not mutate rows.
- F4 Each leg close raises its own EXIT notification (`close_position`) - a 4-leg exit sends 5 messages.
- F5 LIVE exits go as MARKET where the venue allows it (Upstox); "marketable limit with protection" (CX4) happens only
  where MARKET is forbidden. Needs a decision (CXQ-6).

## 4. Conflicts and risks

4.1 Leg vs strategy (SX4). When the group fires: cancel every RESTING exit of its legs (`resting_book.cancel_for_trade`)
and close the legs under the group's reason; a leg stop on `TradeRecord.stop_loss` that fires in the same cycle wins
for that leg only (`close_position` claims once; the group skips it). When a LEG exit fires first: proposed default
keeps the group exit armed on the remaining legs (new version, notice) - literal "rest cancel" would leave the other
legs unprotected (CXQ-1).
4.2 Positions change after setting: a leg closed by hand -> new version without it (MTM of closed legs: CXQ-2); lots added
to the same contract (a new trade row) are NOT covered unless the user edits (CXQ-3); "all open positions" is a frozen
list at set time, not dynamic (CXQ-4). A partly filled basket (SE-7 INCOMPLETE, `incomplete_structures.py:104`) arms
on the filled legs and adds legs filled by "Complete the rest" (same `basket_ref`).
4.3 Mixed underlyings: SPOT and NET_PREMIUM only when all legs share one underlying; ₹ MTM works on any set.
4.4 PAPER = LIVE: same row, same function, same price source; differences F1, F2 to close. LIVE exits still need the
owner's LIVE switch for any send (`close_position` with a broker session only from the worker).
4.5 No re-entry (SX7): DONE / CANCELLED are final; no code path creates an entry from a group exit (test).
4.6 Wording (SEBI §3): numbers come only from the user; texts: "Exit all 3 legs if combined loss reaches ₹X". The
words "stop-loss / target" are the user's own exit settings on his own positions (allowed as order settings, not as
signal text); never on signals or education pages. No suggested values, no "recommended" SL.
4.7 Prefill (SX1): `trading_defaults` today has no group-exit or trigger-source fields; `resting_book.py:7` says a
trigger source is "never pre-filled" while T-D2 asks for a user default in Settings. Proposed: new optional fields
`group_sl_measure`, `group_sl_value`, `group_target_value`, `group_trail_*`, `spread_trigger_source` (default SPOT, T-D2),
frontend pre-fill only, marked "from your defaults"; the server never reads them to trade.
4.8 AI (ADR-0006): extend `test_point4_no_ai_module_reaches...` to `group_exits`; no route under `/api/ai` or agent tool.
4.9 Edits during FIRING are refused with plain words ("already exiting"); cancel likewise.

## 5. Open questions for the owner (provisional default in brackets)

- CXQ-1 A leg's own stop fires first: [strategy exit stays on the other legs, shown as a new version + notice].
- CXQ-2 Combined MTM after a leg closed: [realised P&L of the group's closed legs + open MTM of the rest, i.e. the whole
  strategy since entry; for an ad-hoc CX set: open legs only, from the moment it was set].
- CXQ-3 Lots added later to a covered contract: [not covered until the user edits; the chip says "2 legs not covered"].
- CXQ-4 "All open positions": [frozen list at set time, not new positions].
- CXQ-5 Sold-option leg stop on the basket ticket: [still required unless a strategy SL is set on the same ticket; then
  optional (SX4)] - a behaviour change, needs ✅.
- CXQ-6 LIVE exit order type: [protected marketable limit (user's band) + reprice until filled on every venue, MARKET
  never; PAPER fills the same way].
- CXQ-7 Approval-only org and a combined TARGET: [fires automatically - the user armed it himself; same as TM-2].
- CXQ-8 Notifications: [one summary per group exit (screen + Telegram), leg notifications suppressed for that exit].
- CXQ-9 MTM includes charges: [no, gross MTM, labelled "before charges"].
- CXQ-10 A stuck short in a group exit: [retry every pass, wings held, CRITICAL alert once, chip "exit stuck"].

## 6. PR plan (one `claude/<name>` branch + draft PR each; tests map to CX7 / SX8)

1. `claude/cx-engine-core` (MIGRATION yes, additive): `group_exits` table + model + pure measure / trailing /
   synthetic-close functions + `run_group_exits` + reconcile + F1 fix. Tests: fires once (claim race, two passes);
   shorts-first order; exits pass with kill switch ON + approval-only + Phase 0 lock; restart mid-FIRING no double exit
   (PAPER + fake LIVE broker); no price on one leg -> no decision; close mode no look-ahead; AI guard.
2. `claude/cx-api` (no migration): create / edit (versioned) / cancel / list routes under `/api/paper/fno/group-exits`,
   owner-only, idempotent `client_ref`, preview words; one summary notification (F4). Tests: tenancy route table,
   edit during FIRING refused, leg closed by hand -> new version.
3. `claude/cx-ui` (ui_v2): "Set combined stop-loss" sheet + chip + distance in PositionsV2 (status bar after T1
   merges). Tests: preview words, chip; phone + desktop before / after on ONE comparison page (CX7).
4. `claude/sx-trailing` (no migration): trail / lock / lock-and-trail / break-even with persistence of `current_sl`.
   Tests: moves only in favour (all 3 measures), lock fires once, OCO cancels the other (SX8).
5. `claude/sx-ticket` (ui_v2, no migration): "Exits for this strategy" on the basket ticket, armed with the basket
   (same `basket_ref`), review sentence + hedge margin, `trading_defaults` new fields. Tests: prefill marked, empty
   allowed, INCOMPLETE basket arms on filled legs, no re-entry; ticket + chip screenshots (SX8).
6. (after owner ✅ CXQ-5/6) `claude/cx-live-parity`: LIVE order type, LIVE tick evaluation (F2). Tests: parity scan =
   backtest = PAPER = LIVE adapter fake.
Deploy after PR 1 needs the migration (atp.sh, after 15:30 IST); the others are code-only.
