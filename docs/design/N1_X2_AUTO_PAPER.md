# N1 X2: Auto PAPER from scans (the core's PAPER route)

Status: **built** on `claude/n1-x2` (plan approved; X2-2 decided: the arming lives in `gate_settings_json`, no migration). X2 follows X1 (#214), which it builds on. It extends
the X2 row of `docs/design/N1_EXECUTION_CORE.md` §4. Open questions are X2-1 to X2-7 in `docs/OPEN_QUESTIONS.md`.

## 0. The decision X2 does not make

N1-3 is still open. The L6 replay shows that the gate cuts trades and drawdown, but its net expectancy stays at or
below zero, so whether Auto PAPER is offered at all is **the owner's decision**. X2 therefore builds the route and
keeps every switch off:

- **Platform flag `scan_auto_paper`.** It is in `DEFAULT_OFF_FLAGS` and only the platform operator can turn it on.
  While it is off, every AUTO_PAPER template stays exactly as in X1: the core turns it into a **proposal** (policy row
  "Auto PAPER is not enabled for this organisation").
- **Approval-only mode** (`approval_only_entries`) still wins. While it is on, Auto PAPER is a proposal (N1-6), even
  with the flag on.
- **The template owner arms it.** A template's mode is AUTO_PAPER, and its owner has explicitly armed it. Arming is a
  separate PUT with a confirmation, audited, and cleared whenever the mode changes. An AUTO_PAPER template that is
  not armed is a proposal.

So until the owner decides N1-3 and the operator turns the flag on, X2 changes nothing anyone can see except the chip
text ("Auto PAPER - as a proposal").

Unchanged: PAPER only, never a broker order. The policy refuses Auto LIVE (`phase0_paper_only` needs no change). The
AI never places an order; scan setups are rule outputs from the ported level gate, not model outputs. Exits are never
blocked (ADR-0004). A position opened by Auto PAPER is an ordinary PAPER trade: the monitor's stops and targets,
Exit now and the kill switch all apply.

## 1. The PAPER route (dispatcher)

`policy.decide` already has the row: AUTO_PAPER with approval-only off and `auto_paper_enabled` gives PAPER. X2 sets
`auto_paper_enabled = flag scan_auto_paper on for the organisation AND the template is armed`. The template comes
from the intent's `source_ref` (`rule:<id>:symbol:<sym>`). Any other source passes False.

`dispatcher._paper(session, row, signal, decision, now)` checks, in order. Every refusal is an intent outcome
(REFUSED with the reason). The proposal fallbacks are DONE with route PROPOSE.

1. `approval.required(session, tenant_id)` is asked again, right before the pipeline. If it is now on, the intent
   falls back to a **proposal**, never a placement. This is the Z3 census entry: `approval.required(` must come
   before `execute_signal_for_user(` in the dispatcher.
2. The owner's `paused_reason` (Z3-b) and `by_default.proposal_refusal` (P1-d lockout, entries per day) refuse.
3. The F2 caps (`caps.entry_refusals`, with the owner's book read once) refuse.
4. **Scan limits:**
   - at most `SCAN_AUTO_PAPER_MAX_PER_DAY` (3) Auto PAPER entries per template per IST day, counted from
     `execution_intents` (route PAPER, DONE), so no new table;
   - a `SCAN_AUTO_PAPER_COOLDOWN_MINUTES` (30) per-symbol cool-down for the same owner after an Auto PAPER entry;
   - over either limit, the intent becomes a proposal (X2-3).
5. **Re-quote:**
   - The intent's entry is the commitment candle's close; a PAPER fill there minutes later would flatter the result.
   - X2 re-quotes through the owner's own price (`worker_bridge.price_lookup_for_user`, as Approve does, BT1b) and
     applies the same `approval.requote_check` (drift in % and ticks, not through the stop).
   - No price, or too much drift, means a proposal at the original levels instead (X2-4).
6. `execute_signal_for_user(owner, mode="PAPER", origin=ORIGIN_AUTOMATIC, idempotency_key=f"intent:{id}",
   journal_source="SCAN_AUTO", ...)` is called **without a broker**. It is a GATED callee, so the call is not an edge to
   a sink. The pipeline still runs its own gates: kill switches, the platform controls, the risk engine and the
   guardian (R4, R10, P2/P3, M8, VIX), and Z3's `gate_refusal`, which passes because approval-only is off.
7. The outcome is recorded on the intent: DONE with the order and trade, or FAILED / REFUSED with the pipeline's
   reasons. It also gets an audit row `scan_auto_paper_entry` and a notification to the owner: "Auto PAPER entry
   placed: LONG X at P (stop S, target T) from scan rule R. Exits run as usual; Exit now is on the Positions page."
   The same message goes to Telegram. It states facts only, never advice.

## 2. Template and API (X1 routes)

- `PUT /api/alerts/rules/{id}/then/arm` takes `{armed: true, confirm: "AUTO_PAPER"}`. Trader only; the template's
  owner only (not the OWNER on someone else's template, because the entries go to the owner's book). It answers 409
  unless the mode is AUTO_PAPER. It is audited. The `DELETE` form disarms. A mode change or a template PUT disarms.
  Both routes are classified in the tenancy table.
- The arming is a key in `gate_settings_json` (`_auto_paper_arm: {at, by}`; X2-2 decided, no migration). A missing,
  malformed or foreign key is "not armed"; the gate's settings and the API's `gate` never include it. Arming needs
  the platform switch on (409 otherwise).
- GET `.../then` adds `auto_paper: {available, armed, why}`. `why` says whichever of these holds: flag off,
  approval-only on, not armed, or limits reached today.
- **X1b chip:**
  - "Auto PAPER - armed" (warning tone) only when available and armed;
  - otherwise "Auto PAPER - as a proposal";
  - the drawer shows `why`, an Arm / Disarm button with the confirmation text, and today's Auto PAPER entries out
    of the limit;
  - every string goes through i18n, and none of them gives advice or a win-rate claim (N1-3).

## 3. Safety tests

- **Z10-c:**
  - The core is still outside `reaching()`.
  - An AST test checks that the dispatcher's `execute_signal_for_user(` call passes no `broker` argument.
  - The census entry asserts that `approval.required(` comes before `execute_signal_for_user(` in the dispatcher.
  - No API route reaches `dispatcher._paper` (rule 2). The trading worker stays its only process entry point
    (rule 4, allow-list unchanged).
- **Policy:**
  - With the flag off, or approval-only on, or the template not armed, AUTO_PAPER is a proposal. One test per
    switch, plus all on giving PAPER.
  - AUTO_LIVE is refused in every combination.
- **Fallbacks:**
  - Approval-only switched on between the claim and the call gives a proposal and no order.
  - Over the daily limit, in the cool-down, no price, or drift each give a proposal.
  - Paused, P1-d lockout or F2 caps each give REFUSED, and no order is placed.
- **Placement:** one PAPER trade for the owner (their `user_id`, mode PAPER), idempotent per intent (a re-dispatched
  intent places nothing new), with the audit row and the notification text checked for advice words.
- **ADR-0004:** the Auto PAPER position closes through the monitor (stop) and through Exit now, with the flag
  switched off in between.
- **BT1b:** the re-quote reads the owner's own session only. Under `byob_data_tier` no tenant session is used, and
  without one the intent falls back to a proposal.
- **Arm route:** trader and owner only, 409 on the wrong mode, disarmed by a mode change, audited, and the tenancy
  sweep passes.

## 4. PR shape

One PR, about 700 lines excluding tests:
- the template's arm state and routes;
- the dispatcher's `_paper` with its fallbacks;
- the limits;
- the X1b chip and drawer additions;
- tests.

The flag `scan_auto_paper` is off by default. Merge order: after #214 (X1), because it uses the template table and
the routes.

## Review of X2 (fixes)

- **H1:** a retried intent links what it already did before it is routed again: its PAPER order first (DONE, route
  PAPER, "placed before an interruption"), else its proposal. A crash after placing, then a disarm, then a retry,
  never raises a proposal for a second entry.
- **M1:**
  - The expiry and the stale-claim sweep link an order already placed (DONE, route PAPER), never EXPIRED or FAILED.
  - The daily limit and the cool-down count the PAPER orders that opened a position (order key `intent:<id>` joined to
    the scan intents of the rule and user), whatever the intents' own status.
- **M2:**
  - Auto PAPER places only while NSE is open (the session calendar, with holidays) and before the last entry time.
  - The last entry time is the earliest of the venue's no-new-entries time (15:00), the 15:15 square-off and
    `SCAN_AUTO_PAPER_LAST_ENTRY`. A malformed setting never widens the window.
  - Outside the window the setup becomes a proposal.
- **L1:**
  - An arming is valid only if made after the operator last changed `scan_auto_paper` (audit `feature_flag_set`) and
    for `SCAN_AUTO_PAPER_ARM_DAYS` (7) days. The reason is shown in `auto_paper.why`.
  - `approval.can_trade(owner)` is asked again in the checks.
- **L2:** the E1 AST guard rejects `**kwargs` and `*args` in the pipeline call.
- **L3:** the drawer note reads "With the platform switch off or approval-only mode on, a setup is a proposal".

## Re-review of X2 (LOWs)

- **L1:**
  - An intent's order is only ever the owner's own PAPER order with key `intent:<id>`. This applies to the linking
    lookup and to the cap and cool-down counts.
  - If the key is held by any other order, the intent is refused ("the order key is used by another order") and the
    other order is never replayed.
  - The console's `paper-execute` rejects caller keys starting with `intent:`. The webhook, public API, ticket,
    approval and deployment keys are already namespaced (`tv:`, `api:`, `ticket:`, `approval:`, `deployment:`).
- **L2:** when the other attempt's order is still in flight (CREATED ... PARTIAL_FILL), this attempt leaves the row to
  it (returns CLAIMED, counted as neither failed nor done) instead of refusing it.
- **L3:**
  - A linked order that filled finishes DONE; a rejected or failed one finishes REFUSED (route PAPER) with that
    status; one still in flight is left.
  - The audit row and the notification are written exactly once (`app/execution/core/announce.py`), also when the
    crash came between the fill and the announcement.
- **L4:**
  - Each intent is dispatched with its own clock: the drain's time plus what has passed since it began.
  - Auto PAPER places NSE / BSE equity and F&O only in Phase 0 (X2-10); other venues get a proposal with the reason.
- **L5:** the arming's audit lookup reads platform rows (`tenant_id IS NULL`, indexed).

