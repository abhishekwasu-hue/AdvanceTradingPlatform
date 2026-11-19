"""SFC (Strategy-first chart, owner APPROVED 19 Nov; docs/plans/strategy-first-chart-PLAN.md): the `atp.strategy/2`
strategy and the ONE evaluator the chart, the backtest, PAPER and LIVE read.

* schema    - the saved strategy (blocks, AND / OR groups, timeframe per condition, entry, exits, options); DEF-0
* compile   - every condition -> validated ScreenQL (the registry's own functions and checks)
* evaluate  - one pure function of closed bars: per-condition truth and values, the trees, the signal bars, warm-up
* adapter   - the BaseStrategy the backtest / worker / runner call (it reads the same evaluation; it never orders)
* translate - a saved v1 no-code strategy as v2 (exact or not at all)

Signals only (CLAUDE.md section 3): nothing here places, blocks or changes an order.
"""
