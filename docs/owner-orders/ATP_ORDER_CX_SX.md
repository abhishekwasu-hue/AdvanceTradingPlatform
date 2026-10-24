# Owner orders CX and SX (24 Oct 2026, verbatim)

Roadmap: P1-6c (CX) and P1-6d (SX), under P1-6. One engine for both (TM-2 resting book + trigger engine). Independent
review against the code first (CLAUDE.md §6): docs/design/CX_SX_REVIEW.md.

## CX — Combined stop-loss / target on positions

OWNER ORDER — Combined stop-loss / target on positions (Terminal + My trades → Positions). Roadmap row (ID) under P1-6. ≤20-line report.
CX1 Select: all open positions, or tick any set of positions, or a spread group.
CX2 Trigger (pick one per combined exit): combined MTM loss in ₹ (with optional ₹ target); underlying spot level (default for spreads, as T-D2); or the group's net premium. Optional trailing (lock in profit after a set gain).
CX3 Check mode: on every tick, or only on candle close of a chosen timeframe (setting), to avoid opening spikes.
CX4 When it fires: exit the selected legs at once as one group — short legs first, then the bought hedges (avoids a margin shortfall); marketable limit with protection; partial fills retried; result notified (screen + Telegram).
CX5 Safety: exits are never blocked by limits, approval mode or the kill switch; AI never sets or changes it; the user can edit or cancel any time; it survives restarts (resting book + immediate reconcile, TM-2); PAPER and LIVE behave identically (LIVE only when the owner's switch is on).
CX6 User-friendly: one button "Set combined stop-loss" → a sheet with tick boxes, a ₹ / spot / premium input, and a plain preview ("If combined loss reaches ₹X, these 3 legs exit — sells first"); the active combined exit shows as a chip on the positions panel and the status bar with distance to trigger.
CX7 Proof: tests (fires once; shorts-first order; exits pass with kill switch ON; restart mid-trigger no double exit) + phone and desktop screenshots.

## SX — Strategy-level SL / target / trailing attached at entry

OWNER ORDER — Strategy-level SL / target / trailing attached at entry (spread + basket tickets), same engine as Combined Exit (CX). ≤20-line report.
SX1 On the spread builder and basket ticket, before "Review": an "Exits for this strategy" section: Overall SL and Overall Target, each as combined ₹ MTM, % of net premium, or underlying spot level (spot default for spreads, T-D2). Prefilled from the user's dashboard defaults; editable; can be left empty.
SX2 Trailing (choose one): Trail SL (every X favourable move in MTM/premium/spot moves the SL by Y); Lock profit (once profit reaches A, SL moves to lock B); Lock and trail (lock B at A, then trail by X/Y); Trail to break-even. The SL only ever moves in the trade's favour.
SX3 OCO: when SL or target fires, the other is cancelled; all legs exit as one group — shorts first, then hedges; partial fills retried; notification.
SX4 Leg-level SL stays optional; strategy-level and leg-level never fight (the first to fire exits; the rest cancel).
SX5 Review screen shows the exits in plain words ("Exit all 2 legs if loss reaches ₹X or profit ₹Y; trailing: …") and the margin with hedge benefit.
SX6 After entry the exits appear on the position group (chip + distance to trigger) and can be edited or removed; same safety as CX5 (never blocked, AI never touches, survives restart, PAPER = LIVE behaviour).
SX7 No automatic re-entry (not part of the owner's method).
SX8 Proof: tests (trail moves only in favour; lock fires once; OCO cancels the other; shorts-first exit; restart no double exit) + screenshots of the ticket and the position chip.
