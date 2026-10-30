# ATP_ORDER_M64 - Order-time risk checks: one settings page, prevalent defaults, no silent blocks (owner order, 30 Oct, verbatim)

M64 · ATP · Order-time risk checks: one settings page, prevalent defaults, no silent blocks (entries only; exits never checked — ADR-0004)
Finding (code main 43f0e1f): risk_engine/risk_manager.py:39-116 refuses an entry when (a) min R:R is set and not met, (b) max trades/day, (c) max open positions, (d) consecutive losses, (e) daily loss, and then SIZES the order from risk-per-trade ÷ |entry − stop| — a short leg with no stop is refused (:93) and any size below one lot is refused (:113). For option premiums this sizing rarely reaches one lot, so the owner meets blocks at every ticket. Further checks live in by_default.py (My limits), loss_streak.py, guardian.py, hierarchy.py, portfolio caps, vix_gate.py, kill switch, contract_execution.py:34 (margin safety 0.8), paper/fno_ticket.py:64 (MAX_LOTS 500) — several screens, several defaults, messages that name internal words. Research R32: prevalent F&O practice = size in LOTS, money/percent daily loss limit, max open positions, max lots per order within the exchange freeze limit, optional streak cool-down, no R:R filter unless the user sets one.
RM-1 Inventory first: post a table of EVERY check that can refuse or resize an entry (file:line, trigger, current threshold source, message shown, user-editable? where). Include the ticket, basket, spread builder, deployments, webhooks and Telegram paths.
RM-2 One settings page "Risk limits" (Settings › Risk limits; also reachable from every block message) with these user settings and defaults (defaults = prevalent practice; the user can change each; 0/off = no check):
 • Position size mode: LOTS (default) | risk-based. Default lots per order 1.
 • Max lots per order: default = the instrument's exchange freeze quantity (auto-split above it; never refuse).
 • Max open positions: default 3 (strategy legs of one spread count as ONE position).
 • Daily loss limit: default 2 % of the account basis (and an optional ₹ amount); when hit: no new entries today, exits unaffected, one CRITICAL message.
 • Max new entries per day: default 10.
 • Consecutive-loss cool-down: default 3 losses → pause new entries for N minutes (setting, default 30); off = 0.
 • Minimum reward:risk: default OFF (0). Never applied unless the user sets it.
 • Margin safety: default 80 % of available margin (setting), message shows the ₹ numbers.
 • Risk-based sizing (optional mode): risk per trade % or ₹ ÷ stop distance; for an undefined-risk short with no stop the size falls back to "lots per order" with a WARNING chip, never a refusal.
 Org/admin ceilings (hierarchy) stay above the user's settings; the message says which one applied.
RM-3 Block behaviour: never silent, never a raw reason string. One sentence in plain words + the numbers + a "Change limit" link to the exact setting (or "Ask your admin" when an org ceiling applies) + the option to proceed with a smaller size when the limit is size-based (e.g. "Max lots per order is 5 — place 5?"). Log every block (who/what/which limit) for the journal.
RM-4 Spreads/baskets: the risk checks evaluate the STRUCTURE (max loss of the spread, margin of the structure), not each leg as a naked short; a credit spread with a hedge leg is defined-risk and must not hit the "short with no stop" rule.
RM-5 Remove duplicate screens: one source of truth for limits (by_default + hierarchy), delete or redirect the other limit UIs; defaults migrate existing users' values unchanged.
RM-6 Tests: every check has a unit test for allow / block / resize with the message text; defined-risk spread passes; freeze-split; daily-loss hit stops entries but not exits. Proof: screenshots of the settings page (phone + desktop) and one block message with its link; the RM-1 table before/after.
