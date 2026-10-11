# Risk by default and the plan-before-trade PAPER ticket (Z5 / P1-d, Phase 0)

P1 spec 1.1 (risk by default), 1.2 (plan before trade) and 2.2 (order ticket), in the minimal form the Phase 0 freeze
asks for: a plan-before-trade ticket for PAPER, risk-by-default caps and lockouts (sizing, daily loss lockout with exits
allowed, cost line). Also two Trade repo ports folded into Z5: the strike breach model and the India VIX gate.

Two flags, both **off by default**: `risk_by_default` (ticket, caps, lockout, cost line, proposal card risk check and
breach block) and `vix_gate`. With both off nothing changes. PAPER only: nothing here can reach a broker's order API.

## What a user sees

- **Positions page, "Plan before trade (PAPER)" card.** A strip with the cap per trade, today's P&L against the daily
  limit, entries today, overrides, and the India VIX gate. Under a lockout it says why and until when, and that exits
  stay available.
- **The plan.** Symbol, direction, setup (pullback at a level, S/R flip, breakout retest, range edge, OI / IV, other),
  entry, stop, target, an optional time stop (HH:MM IST), the invalidation note, the checklist (six user ticks plus two
  the server fills: reward:risk and size within the cap), an optional quantity, and an override with a reason.
- **"Check the plan".** The server answers with:
  - the size the cap allows (whole lots) and the risk at the stop;
  - the risk gauge (fraction of the cap; the server says ok / override / over);
  - the reward:risk before and after costs;
  - the cost line: brokerage, STT, exchange, SEBI, stamp, GST, a slippage estimate, the totals at the target and at
    the stop, and the break-even move;
  - errors, refusals and warnings.
  The confirm button says exactly what happens ("Place PAPER: BUY 5000 RELIANCE").
- **Proposal card.** A waiting entry proposal shows the same size, risk, cost line and refusals for the person looking
  at it. A proposal that carries option legs also shows each short strike's breach chance.

## Rules (all settings in `backend/app/core/config.py`; the user's own values in `paper_accounts.risk_settings_json`)

| Rule | Setting (default) | Notes |
|---|---|---|
| Cap per trade | `RISK_DEFAULT_RISK_PER_TRADE_PCT` (1 % of the capital basis), `RISK_DEFAULT_RISK_PER_TRADE_AMOUNT` (0 = none) | Stricter of the two. Capital basis = PAPER starting capital + realised net P&L before today. The organisation's own risk per trade (after the platform ceilings) also applies, strictest wins (ADR-0003); then the guardian's cuts (drawdown ladder, event day, VIX gate). |
| Override | `RISK_OVERRIDE_MAX_MULTIPLE` (2x), `RISK_OVERRIDE_MIN_REASON_CHARS` (10) | Logged (`risk_cap_override` audit event), stored on the plan, counted (placed only). Never past the organisation's limit. |
| Daily loss lockout | `RISK_DEFAULT_DAILY_LOSS_PCT` (2 %) | Worst point of the day's PAPER net P&L (closes in order, plus today's open marks). Recorded once (`risk_lockouts`, a notification) and kept for the IST day. New PAPER entries and entry proposals of that user only. |
| Entries per day | `RISK_DEFAULT_MAX_TRADES_PER_DAY` (10; 0 = none) | The user's own new PAPER entries (a structure counts once). |
| Minimum reward:risk | `RISK_DEFAULT_MIN_RR` (1.5) | A plan below it is refused. |
| Cost line | `TICKET_BROKERAGE_PER_ORDER` (20), `TICKET_SLIPPAGE_PCT` (0.02 % per side) | ATP's dated India cost model (`india_costs`), the paper broker's own numbers. |
| VIX spike pause | `VIX_GATE_SPIKE_PCT` (5 %, `>=`), `VIX_GATE_UNDERLYINGS` (NIFTY, BANKNIFTY, SENSEX) | Trade parity: bullish entries only, PAPER and LIVE, for the rest of the day. |
| VIX level cut | `VIX_GATE_HIGH_LEVEL` (20), `VIX_GATE_SIZE_CUT_PCT` (50), `VIX_GATE_MAX_AGE_MINUTES` (45) | ATP addition: the latest fresh reading at or above the level cuts the risk per trade. |

## Where it runs

- `entry_refusals` (both order pipelines): the lockout and the entries-per-day limit for a user's PAPER entry.
- `execute_signal_for_user` and `execute_structure`: the user's cap (or the ticket's checked cap) before the guardian.
  So an approved proposal, a console entry and a ticket are sized the same way.
- `guardian.guard_entry` (both pipelines and the ticket preview): the VIX gate, with the entry's direction.
- `approval.propose_entry`: no proposal is raised for a locked-out user (`EntriesPaused`, as the Z3-b pause).
- **Exits never pass through any of these.** Stops, targets, time stops, square-off, the kill switch, "Exit now" and
  "Exit now?" are untouched. Tests close a locked-out user's position by "Exit now" and by its stop (with a VIX spike
  active too). "Exit now" itself is Z3's (its flags, the user's own live price, PAPER only).

## The ticket's placement

The plan is checked again on the server and stored (`trade_plans`). The entry is re-priced at the user's own live
price (their BT1 entitlement, never a teammate's session); outside the re-quote limit (`APPROVAL_REQUOTE_MAX_DRIFT_*`)
nothing is placed and the plan must be updated. Under approval-only mode no entry bypasses the gate (Z3-a review): the
ticket raises an ENTRY proposal (source "ticket") carrying the plan's limits in `evidence.ticket`, the plan becomes
PROPOSED, and Approve places the PAPER order within those limits. Otherwise one PAPER order goes through the ordinary
pipeline, with:

- the plan's quantity as a ceiling (fewer units when the live price is farther from the stop, so the risk stays inside
  the cap);
- the checked cap as `risk_cap_amount`;
- the time stop as the position's `time_exit_at`.

The plan row ends PLACED (with its order and position), PROPOSED (with its proposal, whose outcome the plan shows) or
REFUSED (with the reasons).

## Strike breach model (Trade `strike_breach_model.py`)

`backend/app/options_builder/strike_breach.py` ports the inference only:

- the features (sigma20, ATR14, ADX14, the DI direction), with no look-ahead;
- the frozen NIFTY coefficients of Trade's full model, for both outcomes (close and touch);
- the probability, and its inversion (the distance needed for 5 / 10 / 20 % risk);
- a per-leg report.

The fitting and report code stays in Trade (research). The builder's `POST /evaluate` returns it when the request
carries the underlying's daily bars. The proposal card shows it from `evidence.strike_breach`.

Golden parity: `backend/tests/fixtures/strike_breach/make_golden.py` runs Trade's own functions.

## Not in this PR (later P1 items)

- Weekly limit, max open positions per user, exposure per underlying, consecutive-loss cool-off timer.
- Expiry-day and event-day guardrails beyond the guardian's event blackout.
- Naked-short restrictions and the overnight warning.
- Drawdown curve and recovery ladder per user.
- The "cost of overtrading" meter (P1-e journal).
- Resting limit entries, brackets, baskets from the builder and the keyboard flows (P1-g).
