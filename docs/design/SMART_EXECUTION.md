# Smart execution and the PAPER execution adapter (terminal master order, Part 7 + Part 8)

Owner order: `docs/owner-orders/ATP_SESSION_TERMINAL_MASTER.md` (README row 20), Part 7 "SMART EXECUTION" and Part 8.
Review: `docs/design/TERMINAL_MASTER_REVIEW.md` findings F1, F4, F20-F23. Owner decisions of 23 Oct: TM-1 (approved),
TM-3, TM-10 (approved, supersedes FNO-2-1), TM-11, and the standing rule that the `phase0_paper_only` lock and the
`live_trading` flag are never touched. Two draft PRs: A `claude/terminal-paper-adapter`, B `claude/terminal-smart-execution`.

## 1. PR A - PAPER is a broker adapter (TM-1)

**What.** `backend/app/execution/paper_adapter.py`: `PaperExecutionAdapter(BrokerInterface)` over the existing PAPER fill
and cost model. Registered in `app/brokers/registry.py` as `EXECUTION_ADAPTERS["paper"]` (`get_execution_adapter`), not in
`BROKER_ADAPTERS`: it has no credentials, no login and no connect card, so the connect pages and `available_brokers()`
are unchanged.

| Interface method | PAPER behaviour |
|---|---|
| `place_order` | Fills only what the price reaches now (`fno_ticket.paper_fill`): MARKET at the price, a marketable LIMIT, a triggered SL / SL-M. Fill = `PaperBroker._slip(price)` (the same slippage model); a limit never fills past its price. An order that would rest is REJECTED in plain words (no resting book yet: TM-2 is a later part). No price = REJECTED, never a made-up price. A repeated `client_ref` returns the first order (idempotent). |
| partial fills | Optional `liquidity(order)` hook; the remainder stays OPEN until `cancel_order`, as at a broker. |
| `modify_order` / `cancel_order` | On a working order; a terminal order answers its status. |
| `get_order_book` / `get_trade_book` / `get_positions` | In-memory for the adapter's life (one order flow); net positions with average price. |
| charges | `india_costs.leg_cost` of the fill's IST date, per order (`charges(order_id)`). |
| `get_margins` | The funds it was given (PAPER has no broker margin). |
| reads (`get_ltp`, `get_quote`) | The paper exchange's marks (`mark()`), else the optional `price_lookup` (the user's own session). |

`BrokerOrderRequest.client_ref` (new, optional): the platform's own reference for one order. The existing adapters
ignore it (they build their payloads from explicit fields).

**The first path moved: the F&O ticket's single-leg PAPER order.** `OrderRouter(paper_execution="adapter")` fills a PAPER
entry through the adapter: one MARKET order on the paper exchange at the price the pipeline already checked
(`signal.entry`), the fill read back from the adapter's book, a partial fill handled as LIVE handles it (position = the
filled part, remainder cancelled, `PARTIAL_FILL` on the trail). `execute_signal_for_user(paper_execution=...)` passes it
through; `fno_ticket.PAPER_EXECUTION = "adapter"`. Every other caller keeps the default `"direct"`
(`PaperBroker.open_trade`), unchanged. The kill switches, platform controls, approval gate, risk engine, idempotency key
and order state machine all run before the router, exactly as before; no protective stop order in PAPER (the position
monitor enforces stops, as before); exits are untouched (ADR-0004).

**Parity evidence** (`backend/tests/test_tm1_paper_adapter.py`):
- router level: 60 seeded random signals (LONG / SHORT, prices 5-900, lots 1-75, with and without a quantity cap) through
  `direct` and `adapter`: identical `executed`, reasons, algo tag, day state, and Trade fields (symbol, direction, entry
  price, quantity, stop, targets, expected price); closed at the same exit, identical charges and P&L;
- pipeline level: the ticket placed twice (old path, new path) in two identical organisations, BUY and SELL, a second
  tap each, then Exit now: identical order row (status, quantity, algo tag, reasons), every trail row (from, to, detail),
  the position (price, quantity, stop, kind, exchange, lot, side, holding) and its exit (price, reason, charges, P&L);
  one order each (the second tap placed nothing);
- a tenant kill switch refuses before the adapter is ever called.

Next paths, one at a time with their own parity test: the cash plan ticket (`paper/ticket.py`), the basket legs (through
the ticket already), the worker's PAPER deployments, the approval execute.

## 2. PR B - smart execution for every multi-leg order (flag `smart_execution`, OFF, behind `ui_v2`)

Branched from PR A (`claude/terminal-paper-adapter`, not merged yet). Engine: `backend/app/execution/smart_execution.py`;
the basket moves onto it first (`app/paper/fno_basket.py` `execute_smart`, `smart_view`, `close_filled`); bots and the
trade terminal call the same `run()` when they move (one path at a time). Flag registered in `platform/controls.py`,
`platform/flag_catalog.py` ("ready") and the trader pack (`platform/trader_pack.py`).

| Part | What | Where |
|---|---|---|
| Sequencing | Entry: BUY (hedge) legs first, then SELL legs. Exit: SELL (short) legs closed first, then BUY legs. | `entry_sequence`, `exit_sequence` |
| No naked leg (TM-11) | No SELL sent before every hedge is filled. The first leg that does not fill stops the run: `INCOMPLETE` with `complete_rest` / `close_filled` (entry) or `close_rest` (exit). Nothing is retried or undone by itself. Exiting, a hedge is never closed while its short is open; every leg can still be closed alone with Exit now (ADR-0004). | `run()`; routes `POST /api/paper/fno/basket/execute` (state, actions, message, filled_trade_ids), `POST /api/paper/fno/basket/close-filled` |
| Freeze slicing (TM-9) | Whole lots strictly below the exchange's freeze quantity; the slices add up exactly; unknown limit = one order, the ticket's 500-lot cap stays. Inside ONE pipeline order (one order row, one position, one trades-per-day count): the router's adapter path sends the slices and books the filled total; a slice that does not fill stops the later slices (partial fill on the trail). | `slice_units`, `instruments/freeze_limits.py`, `OrderRouter(freeze_quantity=...)` |
| Freeze source | `instruments/data/freeze_limits.json`: index derivatives from NSE circular NSE/FAOP/76693 (01 Oct 2026, effective 05 Oct 2026). The values came from public coverage of the circular because the exchange site could not be reached from the build machine, so they need checking (SE-4). Stock F&O / BSE: the operator can point `FREEZE_LIMITS_FILE` at a CSV copy of the exchange's published quantity-freeze sheet (symbol + freeze-quantity columns); it overrides the table. No database, no migration, nothing secret. | |
| Market orders (TM-3) | `BrokerCapabilities.market_orders_allowed`; where False, a marketable LIMIT at the protection band past the price. The band is the user's setting (`protection_band_pct`) on the basket path, and the router's existing `market_protection_pct` on the router path. | `order_type_for` |
| Margin (TM-10) | `BrokerInterface.get_basket_margin` (default None). Implemented for Upstox (`POST /charges/margin`, `final_margin` with the hedge benefit, `required_margin` without it) and Kite (`POST /margins/basket`, `final.total` / `initial.total`); the wrappers forward it. Called through the user's OWN credential with a usable token, never a teammate's. When there is no answer, ATP's estimate is shown with the label "ATP's estimate, not your broker's number". Free balance before and after: the practice balance in PAPER (positions already held are not counted, and the page says so). | `margin_view`, `fno_basket.smart_view`, `own_broker` |
| Physical settlement | Stock F&O only (index F&O settles in cash). A future always counts. An option counts when it is in the money, or out of the money but within `settlement_band_pct` of the strike, from `settlement_days_before` days before expiry. Delivery value = quantity x strike (options) or x price (futures). Positions banner `GET /api/paper/fno/settlement`; a WARNING notification once per position per IST day (`SETTLEMENT_WARNING`, delivered to his Telegram by the alert dispatcher) when `settlement_telegram` is on; the trading worker checks every 30 minutes. Information only. | `settlement_flags`, `paper/settlement_watch.py`, `workers/trading_worker.py` |
| Settings | Prefs namespace `smart_execution` (per user, no migration): `settlement_days_before` 4 (0-30), `settlement_band_pct` 2.0 (0-20), `settlement_telegram` true, `protection_band_pct` 0.5 (0.05-5). These are only the starting values; the rules read the user's own values. | `me/prefs.py` |
| UI | Basket confirm: "How the order goes" (order, hedge or sold leg, slices), margin needed with its label, saved by the hedge, practice balance now / free after. Result: the incomplete card with "Complete the rest" (the existing remaining-legs flow) and "Close the filled legs". Ticket: the slicing line. Positions: "Delivery ahead". | `frontend/src/fno/SmartExecutionV2.tsx` |

LIVE: the engine and the router path take any `BrokerInterface`; nothing reads or changes `live_trading` or
`phase0_paper_only`, and every leg still goes through the ordinary pipeline (kill switches, risk engine, chokepoint).
The LIVE multi-leg executor (`execution/multileg.py`) moves onto `run()` in its own PR with a parity test (SE-7).

## 3. SE-7 - the multi-leg executor on the engine, and "if I do not answer" (owner decision 23 Oct)

Branch `claude/terminal-live-multileg` (from PR B). Flag `smart_execution` (OFF): with it off, `execute_structure` runs
the old path unchanged; with it on, the legs of every structure (the bots' spreads, condors, straddles) go through the
engine. No migration (the state is JSON on the legs' existing `group_meta`; the settings are prefs).

| Part | What | Where |
|---|---|---|
| One code path | LIVE through his broker adapter (handed in by the trading worker), PAPER through the PaperExecutionAdapter marked at the quoted premiums. Every leg is an ENTRY order (the Phase 0 chokepoint refuses it while the lock holds); `live_trading` / `phase0_paper_only` are never read or changed. | `execution/multileg_smart.py` `place_legs` |
| TM-11 | Hedges (BUY) first, then the sold legs; a sold leg goes only after every hedge is confirmed filled IN FULL in the broker's book. A leg not filled in full inside the confirmation window is cancelled (a fill landing meanwhile is booked) and stops the run. | `place_legs` -> `smart_execution.run` |
| No auto-unwind | The filled legs (a part fill at its filled quantity) are booked as the group's positions; the legs not filled end FAILED / REJECTED on the trail; state INCOMPLETE with "Complete the rest" / "Close the filled legs" / "Keep them". Broker-uncertain only when something is unknown (no answer to a placement, a cancel not confirmed). | `multileg._smart_not_complete` |
| His rule | Prefs `smart_execution.on_no_answer` (`close_filled` default, or `keep`) and `answer_minutes` (5; 1-120), taken when the order stops (the card shows the time left). At the deadline the worker applies it: close = an EXIT through `close_group` (sold legs first, a hedge held while its short is open) - never blocked by a kill switch, the daily-loss lockout, the Phase 0 lock or the broker-uncertain flag; a leg without a price is retried next cycle. Audited (`structure_incomplete*`), notified (`STRUCTURE_INCOMPLETE`: in the app, Telegram where his channel takes the severity; CRITICAL for LIVE). | `execution/incomplete_structures.py` `run_exits`; worker exits part |
| His taps | `POST /api/trading/incomplete/{group_id}/answer` (`complete_rest` / `close_filled` / `keep`; his own order, or the owner; a second tap does nothing twice). "Complete the rest" is recorded and sent by the worker after the entry gates, through every entry refusal again and the engine (Phase 0: no route reaches an entry order). "Close the filled legs": PAPER at once, LIVE by the worker. | `incomplete_routes.py`, worker `_complete_rests` |
| While incomplete | The structure's own exit rules wait (they are written for the whole structure); every leg stays closable (Exit now, emergency exit, square-off, the taps, the rule). Completed: the rules apply again. | `position_monitor._monitor_group` |
| UI | Positions: the incomplete card (filled, not placed, what stopped it, countdown, three taps). Settings › My trading defaults: minutes to answer, close / keep. | `frontend/src/fno/IncompleteOrdersV2.tsx`, `settings/smartExecutionSettings.tsx` |

**The behaviour change in one sentence:** when a leg of a structure does not fill, ATP no longer sells back the filled
legs at once - it keeps them, says so, and waits for his answer, closing them (sold legs first) only when his own
"if I do not answer" rule says so.

**Parity evidence** (`backend/tests/test_se7_live_multileg.py`, fake brokers only): old (flag off) vs new (flag on) in
two identical organisations - LIVE bull put spread and iron condor: identical broker orders (symbol, side, quantity,
type, product, intent, tag) in the same hedge-first order, identical trades (fill, quantity, stop, expected price, role,
group economics), identical order trail and notes; PAPER: identical fills and trail, identical exit price / charges /
P&L when closed, a replay places nothing. The failure path is the documented change (old: BUY, SELL refused, SELL the
hedge back; new: BUY, SELL refused, the hedge kept, INCOMPLETE). Also: a part-filled hedge sends no sold leg; the rule
closes LIVE legs short-first with the kill switch and the Phase 0 lock on; keep; idempotency; tenancy; the worker.
