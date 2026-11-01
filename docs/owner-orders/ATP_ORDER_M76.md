# ATP ORDER M76 - Copilot workspace: advanced facilities + mistake-proofing (owner order, 1 Nov, verbatim)

M76 · ATP · COPILOT WORKSPACE — advanced facilities + mistake-proofing (extends M75; separate order). Rule as always: ATP WARNS, never blocks or decides; the user accepts a warning and continues; nothing pre-set (M68); AI never places/changes orders; exits never blocked.
A. Pre-trade check card (shown inside the ticket, same tab, before Place; every line a fact with numbers; each warning has "Accept and continue" / "Edit")
W-1 Structure conflict (from the engine): setup direction vs Daily / 1H trend, vs the area's role and state (e.g. a bullish setup inside a seller area, a short strike inside a buyer area, entry on a FALSE BREAK / BEYOND_NOT_BROKEN object, counter-trend without a confirmed CHoCH), distance to the next opposite area ("room") in points, invalidation level vs the user's SL.
W-2 Fat-finger and consistency: quantity not a lot multiple, quantity far above the user's usual size (his own history), order value and margin vs available, limit price far from LTP (collar %, setting), wrong side (buy where a short spread was planned), duplicate of an open/pending order for the same instrument and side, expiry different from the plan, strike beyond the user's own distance rule, product mismatch (MIS near cut-off), market closed / pre-open / expiry day.
W-3 Risk preview in ₹ and index points: max loss, max profit, break-evens, margin after the trade, portfolio delta/theta/vega after the trade, exposure in the same underlying, margin shortfall if IV rises X % (setting).
W-4 Accepted warnings are written to the journal entry (which warning, time) — the weekly coach shows "warnings you accepted and their outcomes" (numbers only).
B. What-if and scenarios (same panel)
S-1 Overlays on the payoff: spot ±N points, IV ±N %, time +N days, combined; T+0 line; a Greek line; saved scenarios; the same for the whole portfolio.
S-2 "If price reaches area X" button: P&L of open positions and of the planned trade at the engine's next areas; margin at those levels.
S-3 Event awareness: weekly expiry day, RBI/Fed/budget dates and the user's own event list shown on the chart and in the check card (information only).
C. Execution helpers
E-1 Smart ticket defaults FROM THE USER'S OWN SAVED SETTINGS per instrument type; one-tap "mirror this spread" / "reverse" / "roll strike or expiry" / "add hedge" / "close leg" — each only prepares the ticket.
E-2 Spread entry as one basket with leg sequencing (hedge first on entry, short leg first on exit) and partial-fill handling visible per leg; re-price helper for a leg not filled (show new LTP, user confirms).
E-3 Live position cards inside the workspace: P&L, distance of spot to the short strike in points and %, delta, time to expiry, SL/target lines on the chart with drag to modify (confirm dialog).
E-4 One-tap exits from the chart: close all legs of a structure, close half, move SL to break-even — each a user action with a confirm; combined exits (CX/SX) editable here.
D. Attention and automation
D-1 Watch cards (opt-in): price entering a watched area, candle rejection at an area, short strike within N points, IV spike, margin utilisation above the user's %, time-based reminders (e.g. 15 min before cut-off) — in-app + Telegram, each with "Open here" (no navigation away).
D-2 Trigger plans (M75 CW-6) get a dry-run preview: what would have fired on the last N sessions, with the engine's states at those times.
D-3 Session summary at close inside the workspace: trades, accepted warnings, plan adherence, open risk overnight (numbers only).
E. Learning and recall
L-1 "Similar past setups" (setup_recall) beside the current chart: the user's own past trades at the same area/structure with outcomes.
L-2 Teach-on-chart: tap any annotation (area, swing, state) → one-line meaning from the method text (?), no advice.
F. Tests + proof: each warning type with a fixture; accept-and-continue path; journal record; what-if maths; leg sequencing; screenshots (phone + desktop) of the check card, what-if overlay, position cards, watch card.

## Amendment (owner, 1 Nov, verbatim)

M76 amendment (owner, 1 Nov): facilities, not enforcement. (1) No warning compares the trader's choice with ATP's idea of a right strike/size/side — only with the trader's OWN saved rules (if he saved none, that warning does not exist) and with plain facts (lot multiple, market state, duplicate, margin, trend/area conflict from the engine). (2) Every warning type has its own on/off switch in Settings › Trade settings › Warnings; defaults ON only for pure mistakes (lot multiple, duplicate order, wrong expiry vs plan, limit price far from LTP, insufficient margin); structure-conflict and strategy warnings default ON as information cards but never need an extra click to continue unless the user turns "ask me" on; "don't show again" on every card. (3) Wording: one line of facts, no advice, no judgement ("Daily trend: down · your setup: bullish" — not "risky"). (4) Never slow the ticket: checks run in the background; Place is never disabled by a warning.

(5) No warning fatigue: at most ONE compact check card per ticket (all findings inside it, worst first, ≤ 3 lines visible, rest behind "more"); no pop-ups, no modals, no repeated alerts for the same fact in one session; a warning the user accepted once for an instrument/day is not shown again that day; the card never covers Place or the price fields; time budget for all checks ≤ 300 ms, otherwise Place goes ahead and the card fills in after. Measure and report: average cards per ticket and time-to-Place before/after.
