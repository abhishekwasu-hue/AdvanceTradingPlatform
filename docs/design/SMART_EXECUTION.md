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
