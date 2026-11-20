"""TERM-CON: the Terminal's Conclusion panel (docs/plans/terminal-conclusion-PLAN.md).

One read for the Terminal's symbol + expiry that lines up what ATP already computes - the chart reading, the OI banner,
the caller's own positions, today's orders and the broker state - each line with its source and time.

Read only. Nothing here places, changes or blocks an order, no order path imports this package, and the AI is never
called from it (ADR-0004, ADR-0006). Signal lines pass the signal language (`app/signals/language.check`, surface
`signal_own`); records of what happened (positions, orders, the broker) are facts as the Positions / Orders pages show
them. PAPER and LIVE rows are read by the same code; the mode is a field on each row.
"""
FLAG = "terminal_conclusion"
FLAGS = ("ui_v2", "terminal", FLAG)
