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
