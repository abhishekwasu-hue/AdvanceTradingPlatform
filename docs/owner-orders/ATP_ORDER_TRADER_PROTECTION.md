# Owner order TP: trader-protection features F1-F8 (24 Oct 2026, verbatim)

Roadmap: Phase 3 rows TP-1..TP-8, in this order; TP-1 starts only after Phase 1 is complete.

OWNER ORDER — Trader-protection features F1–F8 (research basis: SEBI study — 91% of individual F&O traders lost money in FY24-25; common failures: no stop, oversizing, revenge trading, overtrading, moving stops away, no journal/review). Add each as a roadmap row (Phase 3, "TP-1…TP-8") in this order; start TP-1 only after P1 is complete; one PR per item; max 3 items in progress; report ≤25 lines by ID with screenshots (phone + desktop). Everything is information only (no buy/sell calls — SEBI RA rules), AI never places/blocks/changes orders, exits are never blocked, PAPER and LIVE behave the same.

TP-1 (F1) Pre-trade risk card on every order review (single, spread, basket): max loss, breakeven(s), margin with hedge benefit, % of capital, distance to the nearest zone/line, today's events (expiry, results, policy, holiday).

TP-2 (F7) Naked-short warning: a short option without a hedge is flagged at review and on positions (warning only, never a block).

TP-3 (F3) Charges & slippage meter: per trade and per month — brokerage, STT, exchange, GST, stamp, and slippage vs the price at order time.

TP-4 (F2) Personal rules guard, set by the user, all OFF by default: stops may only move in the trade's favour; cooldown after N losses or a ₹ loss; daily trade count. Shown clearly when active; never imposed silently; exits always allowed.

TP-5 (F5) Alerts: price enters a zone, false break, confirmed break, combined-SL distance — screen + Telegram/WhatsApp, with one-tap open/exit that always shows a confirm step.

TP-6 (F4) Auto journal: chart snapshots at entry and exit, reader context (zone, state, entry model), mistake tags (moved stop, revenge, overtrade, late entry); weekly review report (best/worst trades, one fixable pattern, results by time of day).

TP-7 (F6) Option-seller tools: what-if / stress test (spot move, IV change, days passing), expiry-day roll helper to the next weekly using the user's own settings, adjustment information when spot nears the short strike (information, not advice).

TP-8 (F8) Copilot post-trade review from the journal (what went well / what to fix), using CP-V chart reading; never suggests buy/sell levels.
