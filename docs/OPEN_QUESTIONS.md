# Open questions

Questions met while working without the owner. Each has a provisional decision (taken so work continues) and where it
applies. When answered: mark **answered**, note the answer, and fix the code if it changes anything.

| # | Question | Provisional decision + reason | Applies to | Status |
|---|----------|-------------------------------|------------|--------|
| A-1 | With every LIVE switch off, should an exit still try to cancel a stop the broker REJECTED? Main does (and the cancel always fails, blocking the exit). | No: a REJECTED stop is never cancelled, switches or not. A rejected order cannot be cancelled anywhere, so skipping the cancel cannot leave a live stop next to the exit; trying it blocked every exit once the stop guard stopped re-arming (ADR-0004). This is the one flags-off behaviour change in PR #82. | `position_monitor._square_off_live`, PR #82 | open |
| A-2 | When does an "accept then reject" streak end? | When the guard's own re-armed stop is seen standing at the start of a cycle (it survived a whole cycle - accepted). A stop cancelled by hand also ends it. The gave-up CRITICAL is sent once per position. | `stop_guard.verify_protective_stops`, PR #82 | open |
| A-3 | Which tick when the venue is not NSE/BSE/NFO/BFO and the registry has no tick (e.g. a low-priced crypto pair)? | Leave the trigger unrounded (as before PR #82): a guessed 0.05 put a sell stop above a 0.0015 price. | `brokers.base.round_stop_trigger`, PR #82 | open |
