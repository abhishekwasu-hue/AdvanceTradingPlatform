# Backtesting methodology

One trade simulator (`src/backtest/engine.py`) is used by built-in strategies and Strategy Lab versions.

* **Point-in-time signals**: at bar *j* the strategy sees only bars ≤ *j* (re-analysed from a truncated window; swings
  require *k* right bars). Only signals whose signal bar is *j* can open a trade. Lab strategies receive host-built
  contexts for every *j* in one sandboxed batch; the runtime cannot set timestamps.
* **Execution**: entry at the next bar's open ± per-side costs (brokerage + exchange/taxes + slippage, configurable).
  If that open is already beyond the stop the trade is skipped and counted (`skipped_gap_entries`).
* **Exits**: gap through the stop/target at a later open fills at the open; stop and target inside the same bar → stop
  assumed first (conservative); otherwise time exit after `max_hold_bars`. One position at a time.
* **Metrics**: trades, win rate, average R, expectancy (R), profit factor, average trade %, equity return
  (fixed-fraction risk on normalised equity — no account balance assumed), max drawdown, exposure; in-sample vs
  out-of-sample split by time; walk-forward folds; parameter sensitivity; buy-and-hold benchmark (symbol and index).
* **Limitations**: survivorship bias (current constituents only), unadjusted Yahoo prices, delayed data, no intraday
  path within a bar, weekly context for swing resampled from daily, small samples. SQL screens only support
  per-bar screening. Results are historical and never a promise of future performance; do not tune parameters and
  report performance on the same sample.

## Forward tests (paper, from live signals)

Every qualified signal stored by a scan is also forward-tested (`src/services/forward.py`): entry at the open of the
first completed bar after the signal bar; exit when a later bar touches the stop or target (if one bar touches both,
the stop is assumed; a gap beyond a level exits at that bar's open) or after a fixed number of bars per timeframe
(5m 48, 15m 32, 30m 26, 1H 30, 1D 20, 1W 12) at the close. Results are R multiples per strategy version, labelled
INSUFFICIENT EVIDENCE below 30 closed tests. No costs or slippage are applied; these are simulations, not fills.
