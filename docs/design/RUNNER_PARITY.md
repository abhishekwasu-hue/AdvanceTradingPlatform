# RUNNER_PARITY - S8: PAPER parity between ATP's worker and the user's runner

Test: `backend/tests/test_runner_s8_parity.py` (CI, fails on any mismatch). Cases and bars: `backend/tests/runner_parity_cases.py`.
Runner half: `backend/tests/runner_parity_job.py` (its own process: runner mode, its SQLite file, no Redis).

How it runs
- The SAME recorded bar series, the SAME price at each step and the SAME cycle times go through:
  1. ATP's PAPER worker path: `TradingWorker.run_cycle` (its frames call, freshness check, closed-bar cut, strategy, the
     entry pipeline, position monitor, square-off, expiry settlement), market data = the case's bars / prices;
  2. the runner loop: `Runner.run_cycle` on its local store, a PAPER-only fake feed with the same bars / prices.
- Owner-style deployments, each with its own settings only: one saved DSL strategy (CLOSE crosses above SMA(5), his own
  ATR stop 1.0 x ATR(5)), `max_lots` 1, his square-off time 15:15; synthetic index contracts (lot 75, two expiries).
- No look-ahead: the last bar of each series is still forming at the entry cycle; both sides take the signal on the
  closed bar before it (the compared `signal_bar`). The expiry-day close comes from the Daily bar of that day only
  (server: the licensed Daily store; runner: his broker's Daily bar, once closed); the next day's forming bar is not read.
- Compared per trade: symbol, side, quantity, leg role, the cycle of the entry and of the exit, entry / exit price, exit
  reason, settlement time, P&L (charges included), signal bar; plus the orders in record order and the PAPER venue's own
  order sequence (the hedge leg first). A trade row's own entry / exit stamp is the run's wall clock on both sides, so the
  cycle is compared instead (a settlement's stamp is data - the expiry close - and is compared as is).

## Result (all match)

| Case | Server worker | Runner loop | Match |
|---|---|---|---|
| Equity DSL single leg, his ATR stop | LONG 1 @ 90.00 (step 1) -> Stop Loss @ 88.20 (step 2), P&L -2.00 | same | match |
| Equity INTRADAY, square-off at his 15:15 | LONG 1 @ 90.00 -> Intraday square-off @ 92.00 (15:16 cycle), P&L 1.97 | same | match |
| Index option single leg (ATM CE), his 30% premium stop | LONG 75 @ 100.00 -> Premium floor @ 60.00, P&L -3012.02 | same | match |
| Bull put spread, two legs, hedge first (his smart-execution ON) | placed BUY 24400 PE, then SELL 24500 PE; both Intraday square-off: SHORT 120 -> 90 (P&L 2229.68), LONG 80 -> 60 (P&L -1511.34) | same order, same fills | match |
| SWING index CE (OTM 2) past expiry | LONG 75 @ 50.00 -> Expired - settled @ 100.00 (24700 - 24600) at the expiry close 15:30 IST, P&L 3733.91 | same | match |

## Differences found and how they stand

| # | Difference | Root cause | Status |
|---|---|---|---|
| D1 | "One direction per symbol" (his organisation's `opposite_positions` = deny) was checked by the worker only | the check was a worker method | FIXED: moved unchanged to `execution/order_defaults.opposite_position`; the worker and the runner loop call it (test) |
| D2 | A deployment with OI gates: the worker checks them against ATP's option-chain store; the runner has no such data | data the runner does not hold | FIXED fail-closed: the runner skips it in plain words ("Your OI gates need ATP's option-chain data ...") - never trades past an unchecked gate |
| D3 | Index strategies: the server replaces an index's volume with its near-month future's (`strategy_bars._future_volume`); the runner reads its broker's index bars as they are | the runner feed has no future-volume step | LISTED (RUN-S8-1): matters only for a strategy that reads volume on an index; default: add it to the runner feed (bind the near future on his broker) in a later step |
| D4 | VIX gate: the worker captures India VIX readings for an organisation with `vix_gate` on; the runner captures none | capture is a worker job | LISTED (RUN-S8-2): only with his own VIX setting; then the gate's fail-safe pauses bullish index entries (never more entries). Default: capture on the runner from his broker |
| D5 | `market_store_reads`: the server may read candles store-first; the runner reads his broker | by design (his broker's data on his machine) | by design |
| D6 | Strategy templates, approval-only entries, "Complete the rest" | listed in S2 / S4b / S4c | not on the runner yet (skipped in plain words) |

Not compared here (out of PAPER scope): LIVE fills (the pilot), broker-side order types, Upstox / Zerodha OAuth.
