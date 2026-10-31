# M64 RM-1 - every check that can refuse or resize a new entry (before / after)

Owner order M64 + amendment (30 Oct): every risk check is a switch on Settings › Risk limits, OFF by default. With all
off, an entry is the lots the trader typed (default 1) and only the broker / exchange checks and the kill switch apply.
Exits are never checked (ADR-0004): no row below is asked on an exit, square-off or "Exit now".

"Before" = main `43f0e1f`. Line numbers are of that commit. "Store" = where the threshold comes from.
Store keys: **Org row** = `risk_settings` (one per organisation; the "My limits" card saved here); **Ticket JSON** =
`paper_accounts.risk_settings_json` (flag `risk_by_default`); **Prefs** = `user_prefs.my_limits`; **Hierarchy** =
`risk_limits` rows (admin); **Ceilings** = platform settings (super admin); **Events** = `market_events` rows.

## A. The trader's own limits (now Settings › Risk limits, one switch each, OFF by default)

| # | Check | file:line (before) | Trigger | Store (before) | Message before | User-editable before | After |
|---|---|---|---|---|---|---|---|
| 1 | Max lots per order | - (did not exist) | typed lots > limit | - | - | - | NEW switch `max_lots_per_order` (OFF). Typed size above it: refused with "Max lots per order is 5 - this order is 8 lots. Place 5 lots?" + "Use 5 lots" button; a risk-sized order is lowered to it with a note |
| 2 | Max open positions | risk_engine/risk_manager.py:71 | open >= limit | Org row | "Max open positions reached (3)" | My limits / Risk page | switch `max_open_positions` (OFF unless saved). A multi-leg structure counts as ONE (trading/persistence.py counts distinct leg groups). Message: "You hold 3 open positions; your limit is 3. Exit one before a new entry." |
| 3 | Daily loss limit | risk_manager.py:76-78 | today's P&L <= -limit | Org row (% only) | "Daily loss limit breached (P&L -5000.00)" | My limits / Risk page | switch `daily_loss` (OFF unless saved): % of account basis and/or NEW ₹ amount (smaller applies). "Today's loss ₹2,000 has reached your daily loss limit of ₹1,000. No new entries today; exits still work." |
| 4 | Max new entries per day | risk_manager.py:69 | entries today >= limit | Org row | "Max trades per day reached (20)" | My limits / Risk page | switch `max_entries_per_day` (OFF unless saved); a structure counts as one entry |
| 5 | Consecutive-loss cool-down | risk_manager.py:73 + risk_engine/loss_streak.py | N losses in a row | Org row (N, pause mode) | "Max consecutive losses reached (4) - cool down" | My limits / Risk page | switch `loss_cooldown` (OFF unless saved): N losses -> NEW M minutes (`loss_cooldown_minutes`), else next session / until resume as before. Backtests use the same rule (parity) |
| 6 | Minimum reward:risk | risk_manager.py:64 | R:R < minimum | Org row (0 = off, TT12-4) | "Risk/Reward 1.10 below configured minimum 1.20" | Risk page | switch `min_reward_risk` (OFF unless saved): "This trade's reward:risk is 1.20; your minimum is 1.50." |
| 7 | Margin safety | execution/contract_execution.py:34 (`MARGIN_SAFETY` 0.8) used at :158, prechecks.py:154, multileg.py:508, :525 | LIVE: lots capped to 80% of available margin | code constant | "Insufficient margin ..." / silent cap with a note | no | switch `margin_safety` (OFF = the whole available margin; ON = his %). The broker's own margin check stays |
| 8 | Risk-based sizing | risk_manager.py:83-114 | risk per trade set | Org row (%) | short with no stop REFUSED (:93); "Computed position size is below one lot" (:113) | My limits / Risk page | switch `risk_sizing` (OFF unless saved): % and/or NEW ₹. A short with no stop: the typed lots with a WARNING ("No stop-loss is set, so risk-based sizing cannot size this position ..."), never a refusal. Below one lot: "Your risk per trade of ₹500 is less than one lot's risk of ₹2,700 at this stop-loss ..." |
| 9 | Risk across open positions (R4) | risk_engine/guardian.py:255-269 | open risk + new > cap | Org row | "Portfolio risk: ... (rule R4)" | Risk page | "More checks" switch `portfolio_risk` (OFF unless saved) |
| 10 | Cool-down after a stop-out (R10) | guardian.py:205-213 | stop-out in the underlying < N min ago | Org row | "Cool-down: ... (rule R10)" | Risk page | "More checks" switch `stop_cooldown` (OFF unless saved) |
| 11 | Drawdown steps (P2 halve / P3 pause) | guardian.py:215-225 | equity below peak | Org row | "Drawdown ladder: ... (rule P3)" | Risk page | "More checks" switch `drawdown` (OFF unless saved) |
| 12 | Market event days (M8 block / size cut) | guardian.py:228-238 | an event row today | Events + Org row cut % | "Event blackout: ... (rule M8)" | Risk page (events) | "More checks" switch `event_rules` - OFF by default (also for operator-listed events) |
| 13 | A sold option / future needs a stop | paper/fno_ticket.py:224-227 | no stop on a sold option / future | Prefs | "Your setting asks for a stop-loss ..." | My limits switch | unchanged store; shown as a switch on Settings › Risk limits (OFF by default, as before) |
| 14 | Experience levels | paper/experience.py:287-301 | futures / naked short below level | Prefs + flag `experience_levels` | level text | My limits switch | unchanged; switch on Settings › Risk limits (OFF by default, as before) |
| 15 | Paper ticket limits (risk cap, daily loss lockout, entries a day, min R:R) | risk_engine/by_default.py:556-575, paper/ticket.py:180-202 | as named | Ticket JSON (`_set` = saved, TT-12) | "PAPER entries per day ..." / "Daily loss limit reached ..." | Setup risk step / Risk page | behind flag `risk_by_default` (OFF by default); already OFF unless saved (TT-12). Settings › Risk limits writes the same values there (one value seen; a looser one still waits for the next day) |
| 16 | Portfolio caps (sector, bucket risk, open positions) | portfolio/caps.py:186-212 | as named | Ticket JSON + flag `portfolio_risk_caps` | caps text | Setup / Risk page | unchanged (flag OFF by default, each cap OFF unless saved) |
| 17 | India VIX gate | risk_engine/vix_gate.py:119, :134 | VIX spike / level | flag `vix_gate` (admin) | "India VIX gate: ..." | no (admin flag) | unchanged: admin flag, OFF by default |

## B. Organisation / admin ceilings (code kept, OFF by default for every organisation)

| # | Check | file:line | Store | Before | After |
|---|---|---|---|---|---|
| 18 | Risk hierarchy (order value, loss per trade, daily loss, open positions, trades a day, exposure ...) | risk_engine/hierarchy.py:198-210 | Hierarchy rows (none unless an admin adds one) | "MAX_ORDER_VALUE (tenant): order value 25,000 exceeds limit 20,000" | none by default; message in plain words + "Ask your admin" ("Your organisation's limit on the value of one order: ... Ask your admin.") |
| 19 | Platform ceilings | platform/controls.py:739-766 | Ceilings | clamp a value the trader set above the ceiling | unchanged: applies only to a switched-on value; the page caps the field and says "your admin caps ... Ask your admin" |

## C. Broker / exchange checks and the kill switch (stay with every switch off)

| # | Check | file:line | After |
|---|---|---|---|
| 20 | Kill switch (strategy / user / global) | execution/signal_execution.py:46 | unchanged |
| 21 | Maintenance, disabled broker, user trading disabled, organisation suspended, plan, algo id, broker uncertain | signal_execution.py:47-75 | unchanged (platform / exchange rules) |
| 22 | F&O ban period | signal_execution.py:184, compliance/fo_limits.py | unchanged (exchange rule) |
| 23 | Instrument tradable today / market open | signal_execution.py:278 (`validate_instrument`) | unchanged |
| 24 | Margin available (LIVE) | prechecks.py:120-160, contract_execution.py:145-163, multileg.py:497-527 | unchanged as a broker check; M64: uses the whole available margin unless Margin safety is on; a structure's margin is the broker's basket margin (hedge benefit) when the broker reports one, else the short legs' sum |
| 25 | Freeze quantity | execution/smart_execution.py:81 (`slice_units`) | splits, never refuses |
| 26 | Lot multiple / tick size / order fields | paper/fno_ticket.py:200-223 | unchanged (`MAX_LOTS` 500 at :64 stays an input bound of the ticket, not a risk limit) |
| 27 | Approval-only mode | execution/approval.py | unchanged (an organisation mode, not a limit) |
| 28 | The user's own PAPER pause | signal_execution.py:50-54 | unchanged |

## D. Entry paths (all reach the same checks)

| Path | Goes through | Notes |
|---|---|---|
| Chain order ticket | paper/fno_ticket.py `assess` / `place` -> `execute_signal_for_user` | block messages come back as `risk.blocks` (sentence, link, "Use N lots") |
| F&O basket | paper/fno_basket.py (each leg through the ticket; `_room` for positions / entries) | each leg is still stored as its own position (follow-up: one group per basket) |
| Spread builder / structures / deployments (multi-leg) | execution/multileg.py `execute_structure` | the structure is ONE position, sized on its max loss; a defined-risk credit spread never reads as a naked short |
| Trading bot / deployments (single leg), worker | execution/signal_execution.py `execute_signal_for_user` | same checks; a block is logged to `risk_events` |
| TradingView webhooks | webhooks/routes.py:155 -> `execute_signal_for_user` | same |
| Public API | public_api/routes.py:196 -> `execute_signal_for_user` | same |
| Telegram | telegram_inbound/service.py:573 (reads the organisation's limits) -> the same pipeline | same |

## Count

28 checks found (17 the trader's own, 2 organisation / admin, 9 broker / exchange / kill switch). After M64: all 17 of the
trader's own are OFF by default (12 on the new switches, 5 already OFF behind their own flag or preference); the 2 admin
ones are OFF by default for every organisation; the 9 broker / exchange checks and the kill switch stay.

Every block of a Risk limits check is written to `risk_events` (rule type `RISK_LIMIT:<check>`, the user id, the numbers,
the smaller size offered), which the journal reads.
