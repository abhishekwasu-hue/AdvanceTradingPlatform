# Owner order DEF-0 - No platform defaults in order placement / risk (28 Oct 2026, verbatim)

Roadmap: P1 row DEF-0 (extends TT-12; supersedes any earlier default values; merges AP-FIX AP4 / AP7).

```
OWNER ORDER — NO PLATFORM DEFAULTS IN ORDER PLACEMENT / RISK (28 Oct). Roadmap: P1 row "DEF-0" (extends TT-12; supersedes any earlier
default values; merges AP-FIX AP4 / AP7). File in INBOX verbatim / DECISIONS / roadmap / README first.
Owner (Marathi, translated): "Order placement has many default settings such as max loss and risk per trade — those were my personal
numbers and they have come into ATP as defaults. That is not right; each user will set them as he wants."
EVIDENCE (production, PAPER, 28 Oct 10:49, owner screenshots): Sell NIFTY 03 NOV 22700 CE, Market, NRML, 1 lot (65), price 131.50,
stop-loss 150.00, max loss ₹1,202.50 → "Cannot place" because (1) "Max loss ₹1,203 is 1.2% of your capital (₹1,00,000) … more than your
risk per trade of ₹500 (0.5%)" while the PAPER balance shown on the same ticket is ₹10,00,000 — the user never set these; (2) "Naked
short options open at the Advanced experience level; yours is Beginner". Both warning boxes say "Information only: it does not hold
back the order", yet the button is disabled. Margin shows "–" beside "the broker's SPAN and exposure margin comes with the order ticket".

RULE (binding): every limit, threshold, gate and exit below is a USER setting. Unset / blank = OFF (no limit, no gate, no exit).
Nothing is pre-filled from code, config, the owner's numbers or any "sensible default". The ticket and Autopilot only SHOW facts
(max loss, breakeven, credit, % of the user's own capital if he set one) and compare them with the user's OWN settings when set.
Exits are never blocked (ADR-0004); PAPER and LIVE use the same path.

D1 RiskConfig (core/models.py:106-125): capital 100_000, risk_per_trade_pct 0.5, max_daily_loss_pct 3.0, max_trades_per_day 20,
   max_open_positions 3, max_consecutive_losses 4, max_portfolio_risk_pct 6.0, stop_cooldown_minutes 30, dd_level_1_pct 5.0,
   dd_level_2_pct 10.0, event_size_cut_pct 50.0, loss_streak_pause → all Optional, default None = off. Capital for any % shown =
   the user's own capital setting, else the PAPER account balance (₹10,00,000 here), never 100_000.
D2 Other hard-coded capital: ai/interview.py:98, :156, :561 (EVIDENCE_RISK), ai/monitor.py:83 → the user's capital or none.
D3 Hidden exits (AP-FIX AP7): instruments/spreads.py:75-76, 587-588 (50% credit target, 100% credit stop); instruments/contracts.py:28;
   deployments/routes.py:170; execution/multileg.py:350 (30 / 50% premium stop) → blank = no such exit; the review states exactly
   which exits are set.
D4 Experience levels (flag experience_levels; config.py:792, 805-809; paper/experience.py; execution/multileg.py:135;
   execution/signal_execution.py:189): owner default — a user setting "Limit me by my experience level", OFF by default; when the user
   turns it on, the current behaviour applies. No opt-out ritual when it is off.
D5 Risk-cap override "at most 2x the cap" and any other override multiplier → only exists if the user set a cap.
D6 Ticket truth: a warning that says "Information only" must never disable the button. Only a hard rule the USER set (or a broker /
   exchange rejection, or LIVE safety switches) may block, and the button then names that rule in one line with a link to change it.
D7 Stop-loss 150.00 on this ticket: report where it came from (user input / saved default / computed). If computed or platform-set → the
   SL field starts blank unless the user saved his own default.
D8 Margin: "–" next to "comes with the order ticket" contradicts itself. Use the broker's margin API (Upstox margin endpoint, if the
   adapter has it) for an estimate on PAPER too, labelled "estimate"; else one honest line "margin not available".
D9 Settings UI: one "My limits" card (Risk page + Settings → Trading defaults) listing every setting above, each blank = "Not set (no
   limit)", with plain one-line help in ⓘ Details (UX-11). First-run: no forced risk setup; an optional "Set my limits" prompt.
D10 Migration: existing rows that hold the old platform values but were never edited by the user → set to NULL (keep a backup table +
   report count per field); rows the user saved keep their values. Migration after 15:30 IST.
TESTS: a new user with no settings can place a PAPER naked / spread / futures order and Autopilot can enter (no gate, no hidden exit);
with each user setting set, the matching rule applies; exits never blocked; parity PAPER = LIVE path; grep guard in CI: no numeric
literal defaults for these fields in models / config / spreads / contracts.
PROOF: the same ticket (NIFTY 03 NOV 22700 CE sell, 1 lot) before / after on phone; the My limits card blank and set; ONE comparison
page. Self-review every diff; deploy line + migration yes (D10). Report ≤ 15 lines; first report = confirmed / not reproduced per D-item.
```
