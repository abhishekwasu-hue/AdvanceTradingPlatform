# N1: E execution core -> U4 X1-X2 (scan Notify / One-click / Auto PAPER) -> F portfolio risk

Design note and PR plan for N1. Source: `docs/specs/ATP_PHASE0_FREEZE.md` §3 (N-list) and the N1 row of
`docs/ROADMAP_STATUS.md`. The decision record is `docs/adr/0014-execution-intent-queue.md`.

The Trade repo revision read for the entry rules is `origin/main` at **1e6c9ac** (after #302). The rules come from
these Trade PRs:
- #292: level bots, trend first, entry only after confirmation;
- #293: L4 MCX and L5 visibility;
- #295: the L6 touch-vs-confirmation replay report;
- #294 and #297 changed `decision3/daily*` (the trend fold);
- #296 is Chart Reader v2. It is not part of the gate.

## 0. Rules for every N1 PR

- **PAPER only.** No broker API order can be reached from ATP. All of these must hold, unchanged, after every PR:
  - the `phase0_paper_only` runtime lock;
  - the Z10-c static order-path guard (`tests/test_z10c_order_paths.py`);
  - the Z3 approval-only gate (`approval.gate_refusal`) and its census of pipeline callers.
- **The AI never places an order** (ADR-0006). Vision is audit-only.
- **Exits are never blocked** (ADR-0004). No N1 refusal sits on an exit path.
- **Behind a flag, off by default** (`DEFAULT_OFF_FLAGS`). Nothing in N1 turns a flag on.
- **Per user** (BT1b / BYOK): an intent, a proposal, a PAPER position and a risk read belong to one user of one
  organisation. Nothing is computed for one user from another user's data or key.
- Each PR stays under about 2,500 changed lines, not counting golden fixtures (fixtures over about 1,000 lines are
  stored as `.json.gz`).

## 1. What exists (built on, not rebuilt)

| Piece | Where | Used by N1 as |
|---|---|---|
| Z3 approval-only entries: proposal -> Approve -> re-quote -> one PAPER order; expiry; "Exit now?" | `app/execution/approval.py`, `app/ai/monitor.py` | the PROPOSE route; Approve is the only way an N1 entry becomes a position |
| Z3-b per-user PAPER account, pause, ladder | `app/paper/service.py` | entries are booked to the intent owner's account; `/pause` refuses intents |
| Z5 P1-d risk by default (risk cap, daily loss lockout, entries per day, VIX gate) | `app/risk_engine/by_default.py`, `vix_gate.py` | refusals before a proposal (`EntriesPaused`) and in the pipeline |
| PW3 / PW4 / PW5 push, Telegram buttons, proposal card | `app/notifications`, `app/telegram_inbound`, frontend | delivery of every proposal |
| BYOK-V2 Vision audit on proposals | `app/ai/vision_audit.py` (`enqueue_for_proposal`, called by `propose_entry`) | step 5 of the entry rules (audit only) |
| Z7a market zones (detection, lifecycle, memory, nightly store, read API) | `app/price_action/zones/` | the candidate **areas** for the level gate |
| S3 / S4 alert rules on closed bars | `app/alerts/barclose.py`, `app/alerts/engine.py` | the scan events that X1 turns into intents |
| Z10-c `phase0_paper_only` lock + static order-path guard | `app/platform/phase0_lock.py`, `tests/test_z10c_order_paths.py` | the safety net every N1 PR keeps green |
| R4 portfolio open risk by correlation bucket, portfolio snapshot | `app/risk_engine/guardian.py`, `app/portfolio/engine.py` | the base of F |

## 2. The entry rules (Trade level bots, ported, not rewritten)

The proposal engine for scans uses the same rules as the Trade level bots. In Trade they are
`bots_common/level_reaction.py` and the `decision3` / `elliott` functions it calls (see
`docs/LEVEL_BOTS_CONFIRMATION.md` in Trade). The order:

1. **Trend first.** The Daily trend is known at yesterday's close: `decision3.daily.fold` (degree-aware Dow,
   ABC_FIX). The trend decides which side may trade:
   - UP: bullish entries at demand only;
   - DOWN: bearish entries at supply only;
   - RANGE: bullish in the lower half of the band, bearish in the upper half;
   - NEUTRAL or UNKNOWN, origin broken, origin at risk, or a parent correction: no entries.
2. **Then the area.** A stored level is only a candidate area (low-high), never a signal. Price arrives from the
   trade side (the previous close is above a demand area or below a supply area). That arms the area.
3. **Then the handover study inside the area.** From arming to now, the candle size and volume of the trend-side
   candles are compared with those of the counter-side candles, first half against second half:
   - **Handover** (grade A evidence): the trend side grows (body up is required; range, close location and volume
     also count) and the counter side weakens.
   - **Absorption** (no entry while it lasts): the counter side strengthens.
   - **Patterns** from the swings inside the area are evidence and are named on the ticket. They are never required:
     wedge, channel / flag, double or triple top / bottom.
   - **Also checked:** an impulsive approach (two or more wide candles closing at their extreme) means no entry.
     Contraction and a corrective approach are evidence.
4. **Then a dominant commitment candle on higher volume.** A reversal form is required: engulfing, star, inside
   break, doji confirm, tweezer, pin, or a strong close (one or two candles). The candle must also:
   - close beyond the previous close;
   - close beyond the area's inner edge;
   - have a body of at least 1.5 x the window's average body;
   - have volume above the window's average. Lower volume means keep waiting. Volume NA is allowed, with grade B at
     most.

   A **real break** of the far edge disarms the area (no breakout or reverse trade). **Risk:** the stop is beyond
   the far edge and the armed-period extreme, plus 0.25 x the median range. The target is the next opposite area.
   R:R must be at least 3. With no commitment within 8 bars the area times out.
5. **Then the Vision audit.** BYOK-V2, on the user's own key, audit-only.
6. **Then Telegram / in-app approval.** The Z3 proposal (Approve PAPER / Reject / Snooze).

**What the replay says (Trade #295, `docs/reports/level_bots_confirmation.md`).** The confirmation gate cuts the
trade count and the drawdown by more than an order of magnitude. Its net expectancy stays at or below zero, in points
per trade:

| Bot | Period | Confirmation | Touch |
|---|---|---|---|
| Classic | NIFTY IS | -0.05 | -5.60 |
| 5M Instant | NIFTY IS | -2.95 | -2.91 |
| Classic | NIFTY VAL | -8.09 | -1.67 |

Only the "structural exit" comparison arm (not live in Trade) is positive in some cells. ATP therefore presents the gate as a **filter in front of a human decision**, never as an edge:
- no win-rate or return claims on the card;
- Auto PAPER from the gate stays off until the owner decides (OPEN_QUESTIONS N1-3).

## 3. Architecture

```
scan rule (S4, closed bars) ─┐
                             ├─> [L3 level gate: trend -> area -> handover -> commitment -> risk]  (pure, golden-tested)
Z7a zones (areas) ───────────┘                   │ CONFIRMED (ticket: study line, commitment line, grade, SL/target/R:R)
                                                 v
                          intents.submit (E1: one QUEUED row; a producer never dispatches)
                                                 │  trading worker, every cycle (ADR-0010 lock)
                                                 v
                    dispatcher.drain -> policy.decide (approval-only? phase0 lock? requested mode)
                          │ NOTIFY             │ PROPOSE                         │ PAPER (X2 only)      │ REFUSE
                          v                    v                                 v                      v
                    notification      approval.propose_entry             PAPER pipeline, no        reason on the row
                                      (+ Vision, Telegram, push)         broker (GATED callee)
                                               │ Approve (person)
                                               v
                                      approval.execute_entry -> one PAPER order (existing Z3 path)
```

## 4. PR plan

Order: E1 -> L1 -> L2a -> L2b -> E2 -> X1 -> X2 -> F1 -> F2 -> F3. L1 / L2 are pure libraries and can run in
parallel with E2. X1 needs E1 + L1 + L2.

| PR | What | Flag | PAPER-only / guard | Size (est.) |
|---|---|---|---|---|
| **E1** (this branch, `claude/n1-e1`) | ADR-0014. `execution_intents` table and migration. `app/execution/core`: `policy` (pure routing table + level checks), `intents` (submit / claim / expire / requeue / finish, all conditional), `dispatcher` (NOTIFY -> notification, PROPOSE -> `approval.propose_entry` with key `intent:<id>`, REFUSE; PAPER refused until X2). The worker drains every cycle. Tests: the policy grid, idempotency, claim races, expiry, retry after an interrupted claim, fail-closed per intent, scope by flag, a worker cycle, and the guard below. | `execution_core` | the core calls no pipeline and names no broker (AST test); no `app.execution.core` function reaches an order sink in the Z10-c graph; no new route | ~1,000 |
| **L1** | Level-gate port, part 1: the area state machine. Ported: `level_reaction.evaluate` / `safe_evaluate` / `describe` / `session_start_index` / `closed_bars` / `median_ranges` / `_impulsive_approach` / `_target` / `context_from_state` / `side_allowed`; `decision3.method` `zone_study`, `zone_pattern`, `commitment`, `reversal_form`, `power_shift`, `contraction`, `candle_read` and helpers; `elliott.breaks.break_from` + `time_accepted` added to ATP's existing port (`app/price_action/breaks.py`); the settings subsets. The trend context is an **input** (a state object). Golden parity with Trade's own functions on fixed bars. | none (pure library; nothing calls it yet) | imports nothing from execution, brokers or trading (AST test) | ~1,500 + `.json.gz` goldens |
| **L2a** | Trend fold dependencies: `decision3.daily_legs`, `legs2.measure` / `settings2`, `patterns2.rules` / `rules2` / `settings2`, `decision3.settings` (the parts the fold reads). Golden parity. | none | pure | ~1,200 |
| **L2b** | `decision3.daily.fold` + `daily_swings.prepare` and `state_at`; `level_reaction.trend_context` on top. Golden parity on 10 years of NIFTY / BANKNIFTY Daily bars (`.json.gz`), with a drift check script that reruns the goldens against a newer Trade revision (see N1-2). | none | pure | ~1,500 + goldens |
| **E2** | Hardening: (a) the CLOSING pre-claim of OPEN_QUESTIONS Z3-12. A conditional UPDATE before any broker call; every exit path skips a CLOSING position; a failed leg clears it. It is an exit path, so it may only ever *skip a duplicate* exit, never block the first one. (b) `GET /api/execution/intents` (own rows, user scope; classified in `tenancy_route_table.py`). (c) The venue contract suite `tests/contracts/test_venue_contract.py` on the PAPER broker and fake adapters. | `exit_preclaim` (a), `execution_core` (b) | (a) touches only EXIT call sites already in `ORDER_CALL_SITES` (no new site, no new route reaching one); (b) is read-only and must not appear in `ROUTES_REACHING_BROKER_ORDERS` | ~800 |
| **X1** | U4 "Then" panel for scans, modes **Notify** and **One-click**. A scan rule gets a Then template (mode, confirm TF, gate settings, R:R, risk cap from P1-d). On each alert event: areas from Z7a zones -> L1 / L2 gate on closed confirmation-TF bars -> on CONFIRMED, `intents.submit` with the ticket as evidence and key `scan:<rule>:<symbol>:<area>:<confirm bar>` (one entry per confirmation, as Trade's `mcx_confirm_used`). The proposal card and the Vision context carry the study and commitment lines. Signal-log reasons use Trade's `SKIPPED_GATE_*` codes. Routes: `GET` / `PUT /api/screener/rules/{id}/then` (config only). UI: the U5 mode chip and the Then drawer. | `scan_then` (+ `execution_core`) | the routes only write config rows and intents; the PROPOSE route ends in Z3, whose Approve route is already allowed in `ROUTES_REACHING_BROKER_ORDERS` (ENTRY books PAPER only); rule 2 unchanged | ~2,000 |
| **X2** | **Auto PAPER**: `auto_paper_enabled` from the flag; the dispatcher's PAPER route calls `execute_signal_for_user(mode="PAPER")` **without a broker** (a GATED callee), with `approval.required(...)` asked first. Under approval-only mode it stays a proposal. Caps: entries per scan per day, per-symbol cooldown, the P1-d lockouts. The armed chip is always visible. | `scan_auto_paper` | the dispatcher is added to the Z3 census (proposes first under approval-only); a test asserts no API route reaches the dispatcher's PAPER call and the trading worker stays the only process entry point (rule 4) | ~900 |
| **F1** | Portfolio risk model (pure + read API). The per-user PAPER book (BT1b): gross / net exposure, sector buckets from the U1-b NSE classification, beta to NIFTY from lake Daily bars, correlated open risk at the stops (R4 buckets generalised). `GET /api/portfolio/risk` (own book). | `portfolio_risk` | read-only route, no sink | ~1,200 |
| **F2** | Portfolio caps as **entry refusals only**: max sector %, max correlated open risk, max open positions. They run in `guardian.guard_entry` and before a proposal (`EntriesPaused`). Exits never consult them (ADR-0004 test). | `portfolio_risk_caps` | refusals only; no call site change | ~800 |
| **F3** | The Positions risk card, the Telegram `/risk` lines, and a "portfolio impact" line on the proposal card. | `portfolio_risk` | UI / text | ~900 |

### How each PR keeps the Z10-c guard green

The guard has five rules: (0) GATED callables cannot obtain a broker; (1) every broker call site is classified;
(2) no API route reaches an ENTRY site; (3) the routes reaching EXIT / PROTECTIVE sites are an exact allow-list;
(4) the process entry points reaching a site are an exact allow-list.

- **E1:** no new call site (rule 1 unchanged); no route (rules 2 and 3 unchanged). The worker already reaches order
  sites (rule 4 unchanged). New test `test_no_function_of_the_core_reaches_a_broker_order_method`: the graph's
  `reaching()` holds no `app.execution.core.*` function. So X1's routes can call `intents.submit` and stay clean.
- **L1 / L2:** pure modules; an AST test forbids imports from `app.execution`, `app.brokers` and `app.trading`.
- **E2:** (a) changes the bodies of existing EXIT sites only, so `ORDER_CALL_SITES` is unchanged. A test proves the
  pre-claim never refuses the first exit of a position. (b) A read route; rule 3's allow-list is unchanged.
- **X1:** its routes reach `intents.submit` only. Rule 2 and rule 3 are unchanged. The test in E1 keeps the core out
  of `reaching()`.
- **X2:** the only PR that calls a pipeline from the core, and it does so without a broker: `execute_signal_for_user`
  is GATED on `broker`. Its census entry (`tests/test_z3_approval_entries.py`) asserts `approval.required(` comes
  before `execute_signal_for_user(` in the dispatcher. The E1 graph test then changes from "the core reaches no
  sink" to "the core reaches no sink when called without a broker", which is how `reaching()` already treats GATED
  callees. `phase0_paper_only` needs no change: Auto PAPER is PAPER and Auto LIVE is refused by the policy.
- **F1 - F3:** read routes and entry refusals; no call site or route change.

## 5. Trade gate -> ATP module (L1 / L2 / X1)

Target package: `backend/app/price_action/level_gate/` (next to `zones/`): `gate.py` (state machine), `method.py`
(the decision3 functions), `trend.py` (context + fold), `settings.py`. The real-break rule extends the existing
`backend/app/price_action/breaks.py` (one definition of a real break in ATP, as in Trade).

**G-list note.** `decision3/`, `legs2/` and `patterns2/` are rows of the port map's group 3 ("only when the owner marks
it stable"). The gate *is* the owner's level-bot rule set and calls them, so:
- L1 ports only the `decision3.method` functions the gate calls (`method.py` has not changed since #292);
- L2 (the Daily fold and its `legs2` / `patterns2` dependencies, still changing: #291, #294, #297, #300 in two days)
  is ported at a pinned Trade revision with the drift check below.

The owner is asked to mark `decision3.daily` stable or accept the pinned port (OPEN_QUESTIONS N1-2).

| # | Trade step (Trade@1e6c9ac) | Trade function(s) | ATP module | PR | Parity |
|---|---|---|---|---|---|
| 1 | Trend (Daily, yesterday's close) | `decision3.daily.fold`, `daily_swings.prepare`, `daily.state_at`; `daily_legs`, `legs2.measure`, `patterns2.rules` / `rules2` | `level_gate/trend_fold.py` (+ `legs.py`, `patterns.py`) | L2a / L2b | golden: fold states per day, 10 y NIFTY / BANKNIFTY Daily |
| 2 | Trend -> allowed sides | `level_reaction.context_from_state`, `side_allowed`, `trend_context`, `prepare_daily` | `level_gate/trend.py` | L1 (state in), L2b (fold) | golden: context per state; RANGE halves |
| 3 | Daily-trend fetch, file cache, negative cache, timeout | `daily_trend`, `_fetch_trend`, `call_with_timeout` | not ported: Daily bars come from the shared lake (HB1); the scan job computes the context once per symbol and day; failure = no entry (fail-closed) | X1 | behaviour test |
| 4 | Area | level `zone_low`-`zone_high` (Trade replay: level +/- 0.05 % / 2) | Z7a zones (`zones/read.py`: fresh / tested, not invalidated) | X1 | N1-4 |
| 5 | Arrival and side (IDLE -> ARMED) | `evaluate` (previous close above / below) | `level_gate/gate.py` | L1 | golden: event lists |
| 6 | Impulsive approach | `_impulsive_approach`, `median_ranges` | `gate.py` | L1 | golden |
| 7 | Handover study / absorption | `method.zone_study`, `_cmp`, `_majority`, `_vol`, `_mean` | `level_gate/method.py` | L1 | golden per window, both directions mirrored |
| 8 | Patterns (evidence) | `method.zone_pattern` | `method.py` | L1 | golden |
| 9 | Contraction / corrective approach (evidence) | `method.contraction`, `power_shift`, `candle_read` | `method.py` | L1 | golden |
| 10 | Dominant commitment on higher volume | `method.commitment(win=ARMED)`, `reversal_form`, `_same_window`, `_ohlc` | `method.py` | L1 | golden per bar: form, grade, body / volume multiples, wait reason |
| 11 | Real break of the far edge | `elliott.breaks.break_from`, `time_accepted` (added in Trade after 0df3e09), `first_real_break`, `median_range` | `backend/app/price_action/breaks.py` (already ported from Trade@0df3e09; L1 adds `time_accepted`, the `break_accept_closes` step and `break_from` - the +33 lines since then) | L1 | extends `tests/test_trade_port_price_action.py` goldens |
| 12 | Risk: SL, target, R:R, grade A / B, ticket lines | `evaluate` (plan), `_target`, `form_name`, `lot_multiplier` | `gate.py` | L1 | golden: entry / SL / target / R:R / grade / study / commit lines |
| 13 | Fail-closed wrapper, one-line description | `safe_evaluate`, `describe` | `gate.py` | L1 | golden + error test |
| 14 | Signal-log reason codes | `level_gate_bot.STATUS`, `ARMED_WHY`, `status_of` | `level_gate/status.py` -> the intent's `reason` / the rule's `last_problem` | X1 | table test |
| 15 | Confirmation TF per bot (5m / 15m), closed bars only | `level_gate_bot.CONFIRM_TF`, `closed_bars` | Then template `confirm_tf` (default 5m); S4 closed bars | X1 | behaviour test |
| 16 | Index volume = front futures; NA => grade <= B | `level_gate_bot` volume source | lake / broker futures bars when present, else NA | X1 | N1-5 |
| 17 | One entry per confirmation | `mcx_confirm_used.json` | intent key `scan:<rule>:<symbol>:<area>:<confirm bar>` (unique per organisation) | E1 / X1 | E1 idempotency test |
| 18 | Vision audit | `vision.gate.entry_gate`, `vision.signal_audit` | `app/ai/vision_audit.py` (BYOK-V2, already called by `propose_entry`) + the ticket lines in its context | E1 (wired) / X1 (context) | BYOK-V2 tests |
| 19 | Telegram approval, drift check | `vision/approval.py` | Z3 `approval.propose_entry` / `execute_entry` (re-quote), PW4 / PW5 | E1 | Z3 tests |
| 20 | Legacy touch mode, `paper_would_have` | `level_gate_bot.entry_mode`, touch records | not ported: ATP has no touch-mode level bots. Rejected / expired proposals already give "not taken" rows (Z3-b) | - | - |

### Golden parity method (L1 / L2)

- **Inputs.** Fixed bars:
  - hand-built scenarios (arrival from above / below, absorption then handover, a low-volume commitment, volume NA,
    a false break, a real break with a flip retest, a timeout, an impulsive approach, a gap through the far edge);
  - seeded random walks for breadth;
  - (L2) Daily OHLC history.
- **How the goldens are made.** `backend/tests/fixtures/level_gate/make_golden.py` runs **Trade's own functions** on
  those bars. It runs from a read-only export (`git -C /home/user/trade archive origin/main` into a scratch
  directory; the Trade repo is never written to). It writes `.json.gz` goldens stamped with the Trade revision.
- **The tests.** The ATP port must reproduce every field (states, events, reasons, plan numbers to 1e-9, ticket
  lines in English).
- **Drift check.** The same script against a newer Trade revision reports which goldens changed. A change is ported
  as a reviewed PR, never by regenerating goldens silently.

## 6. Open questions

Recorded in `docs/OPEN_QUESTIONS.md` as N1-1 ... N1-9, each with a provisional decision.
