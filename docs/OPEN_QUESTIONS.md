# Open questions

Questions met while working without the owner. Each has a provisional decision (taken so work continues) and where it
applies. When answered: mark **answered**, note the answer, and fix the code if it changes anything.

| # | Question | Provisional decision + reason | Applies to | Status |
|---|----------|-------------------------------|------------|--------|
| 1 | Backtest HTF bins are anchored at midnight (pandas default), so 30/60-minute bars start at 09:00/10:00, not 09:15 like broker charts (the live service anchors at 09:15). Should the backtest re-anchor to 09:15? | Kept midnight: changing the grid changes every MTF backtest a second time in the same PR; realism 1 only fixes *when* a bar is seen. A 09:15 grid is a one-line `origin=` change, to do with an engine version bump once decided. | `app/backtest/engine.py`, `options_engine.py` (`resample_ohlc` calls), PR "backtest realism" | open |
| 2 | Part A (PR #82) and this part both touch backtest/expiry code; this branch starts from main without PR #82. | Kept independent branches as asked (each from main); whichever merges second gets a merge from main before its merge. | PR #82, backtest realism PR | open |
