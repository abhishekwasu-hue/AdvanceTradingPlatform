# Discipline engine (Z6 / P1-e)

Spec: `docs/specs/ATP_PROFITABILITY_MANUAL_TRADING_OSB_SPEC.md` §1.3 and §1.7. Decision record: ADR-0026.
Flag: `discipline_engine`, **OFF by default**. While it is off:
- nothing is captured;
- `/api/journal/*` answers 503 with `X-Feature-Disabled: discipline_engine`.

The engine works with numbers only. It never recommends a trade, a security or an action. It never places, changes or
blocks an order.

## What it does

1. **Auto-journal for every trade.** This covers manual trades, strategy (deployment) trades, webhook and API trades,
   and Z3's PAPER approvals. Each trade gets one `journal_entries` row with:
   - **plan**: the signal's entry, the initial stop, the targets, the planned R:R, the setup and the exit rules;
   - **fills**: entry and exit, with side, price, quantity, time, order id, slippage and latency;
   - **costs**: the charges and where they came from (estimated, or from the contract note);
   - **price range**: the highest and lowest price seen while the trade was open (MAE / MFE, in price and in R);
   - **holding time**;
   - **stop history**;
   - **chart references** at entry and exit;
   - **for options**: IV and Greeks at entry.
2. **Behaviour tags.** These are automatic where a behaviour can be detected, and the trader decides on them. Each tag
   starts SUGGESTED, and the trader can CONFIRM or DISMISS it. The trader can also add a tag by hand. A re-run never
   overrides the trader's decision.
3. **Discipline score.** Each IST week (Monday to Monday) gets a score from 0 to 100, with every component shown.

### Chart snapshots
A chart snapshot is stored as a reference, not an image: `{symbol, exchange, instrument_key, underlying_symbol, at,
redraw: "market_lake_as_of"}`. The chart engine redraws it from the market data lake "as of" that instant. ADR-0013
keeps every bar's ingest time, so the redraw shows what was known at the time, without storing images.

### Greeks / IV at entry
These are solved from the entry premium (`app/option_chain/leg_greeks.py`) and the underlying's price at the signal.
The underlying's price travels in the contract meta as `underlying_entry`. Only the journal reads it, and it is not a
trade column. When that price is missing, the entry reads `{"unavailable": "<why>"}`.

IV at exit is a column, but nothing fills it yet: the exit path has no underlying quote (see open question Z6-3).

## Where capture hooks in (and the Z3 integration point)

| Hook | Called from | Captures |
|------|-------------|----------|
| `capture.on_entry` | `trading/persistence.persist_trade`, after the trade row commits | plan, entry fill, entry chart reference, Greeks |
| `capture.on_marks` | `position_monitor.monitor_open_positions`, after the sweep's marks commit | the highest and lowest price seen while open |
| `capture.on_stop_change` | `position_monitor._apply_trade_exit_rules`, after a stop change commits | the stop history |
| `capture.on_exit` | `position_monitor.close_position`, after the exit commits | exit fill, costs, exit chart reference |

Every hook follows the same rules:
- it opens its **own database session**;
- it does nothing unless the flag is on for the tenant;
- it logs and swallows its own errors.

So a journal failure can never fail, roll back or delay a trade or an order (a test checks this). Anything a hook
missed is rebuilt from the trade row (`capture.ensure_entries`) the next time the week is read. This covers a trade
opened while the flag was off, or one opened before this feature existed.

**Z3 (PAPER approval).** Z3 fills go through `execute_signal_for_user`, then `persist_trade`, so they are journaled with
no change to Z3. To label them as approvals rather than MANUAL / STRATEGY, Z3 has two options:
- pass `journal_source="PROPOSAL"` to `persist_trade`; or
- after the fill, call `await app.journal.capture.on_entry(session, trade_record, source="PROPOSAL")`. This is
  idempotent and only updates the source.

A future scan "Then" template does the same with `"SCAN"`. A future stop-edit endpoint calls
`capture.on_stop_change(..., source="hand")`.

## Scope: personal (per user)

The journal, its tags and its settings belong to one trader. Every query filters on both `user_id` and `tenant_id`.
A teammate in the same tenant, the tenant owner and another tenant all get a 404 for someone else's trade or tag, the
same answer as for a trade that does not exist (tests cover a same-tenant user and an other-tenant user).

The risk counters stay tenant-wide (risk is an organisation budget); the journal does not.

## Behaviour tags

| Tag | Rule (settings in `code`) |
|-----|---------------------------|
| `revenge_trade` | Entry within `revenge_window_minutes` of a losing exit, sized above that loser x `revenge_size_multiple`. Size is the risk (stop distance x quantity), or the notional when either trade has no stop. |
| `fomo_entry` | Filled beyond the plan's (signal's) entry, in the chasing direction, by more than `fomo_chase_r` x the planned risk. |
| `averaging_loser` | A new entry in the same symbol and direction while an earlier position is still open and under water (the new fill is worse than the earlier fill). |
| `moved_stop_against` | A stop change that moved the stop farther from the entry. Read from the stop history, or from the trade row (final stop vs initial stop) when no history was captured. |
| `no_stop` | No stop at entry: missing, zero, or on the wrong side of the entry. A multi-leg leg that is covered by its group stop does not count. |
| `overtrading` | The trades of a day beyond the daily norm. The norm is `overtrading_daily_norm` when set; otherwise the trader's own median trades per active day over `overtrading_lookback_days` x `overtrading_multiple`, and never below `overtrading_min_norm`. |
| `early_exit` | Closed between the stop and the first target, for a reason none of the platform's exit rules gave (`SYSTEMATIC_EXIT_MARKERS`: stop, target, time rule, square-off, structure, kill switch, an approved AI action, ...). In other words, closed by hand with no invalidation. |
| `held_through_event` | Open across the start of an events-calendar event of a kind in `event_kinds`. The event must apply to the trade's underlying, using the Risk Guardian's scope rule: none or `*` = every symbol, `INDEX` = index underlyings, else that one underlying. The start is the event's start time, or the session open (09:15 IST) when it has none. |

The legs of one multi-leg structure count as **one decision**:
- the structure counts once against the daily norm;
- legs are never compared with each other;
- in the score, a structure is flagged once.

## Discipline score

Each component is the share of the week's decisions that carry none of that component's tags. The score is the
weighted mean of those shares, rounded half up. A week with no trades has **no score** (null), never a made-up 100.

| Component | Tags | Default weight |
|-----------|------|----------------|
| stop_discipline | no_stop, moved_stop_against | 25 |
| plan_adherence | fomo_entry, early_exit | 20 |
| emotional_control | revenge_trade, averaging_loser | 25 |
| frequency_control | overtrading | 15 |
| event_awareness | held_through_event | 15 |

Which tags count:
- DISMISSED tags never count;
- SUGGESTED tags count while `count_unconfirmed` is on;
- CONFIRMED tags and tags the trader added always count.

## Defaults and research note

Every number below is a **starting point chosen by reasoning, not fitted** to anyone's results. Each one can be
edited per trader (`PUT /api/journal/settings`). A stored value outside its range is ignored and the default is used
instead.

| Setting | Default | Range | Why this default |
|---------|---------|-------|------------------|
| `revenge_window_minutes` | 15 | 1-1440 | The same window the existing trade coach uses (`app/ai/coach.py`, `REVENGE_MINUTES`), so both views agree. Research on prior outcomes and risk-taking (Thaler & Johnson 1990, "break-even effect") supports "right after a loss" as the window that matters. No study gives a minute count. |
| `revenge_size_multiple` | 1.0 | 0.5-10 | The spec says "larger size", so any increase over the losing trade counts. A trader who scales in on purpose can raise it. |
| `fomo_chase_r` | 0.25 | 0.05-5 | Paying a quarter of the planned risk before the trade starts turns a planned 2:1 into about 1.4:1 ((2 - 0.25) / (1 + 0.25)). That is a material change to the plan, and well above the PAPER slippage model, so ordinary fills do not trip it. |
| `overtrading_daily_norm` | 0 (= use own median) | 0-500 | The spec says "above the daily norm". The norm is the trader's own baseline, not a platform number. Barber & Odean (2000) tie underperformance to trading activity *relative* to the trader. |
| `overtrading_lookback_days` | 20 (calendar) | 5-365 | About three trading weeks: recent enough to follow a change in style, long enough for a median. |
| `overtrading_multiple` | 1.5 | 1-10 | "Half again as many as a usual day". |
| `overtrading_min_norm` | 3 | 1-500 | So that a trader who usually takes one trade is not tagged on their second. |
| `event_kinds` | all but EXPIRY | subset of the calendar's kinds | Weekly index expiry is the normal life of an options position, not a news-like jump. A trader can add it. |
| `count_unconfirmed` | true | - | So the score means something without daily clicking. Dismissing a tag removes it from the score. |
| `weights` | 25/20/25/15/15 | 0-100 each, sum > 0 | The behaviours the mission lists first (no stop, oversizing and revenge) weigh most. Only the proportions matter. |

Revisit these once the PAPER cohort has enough weeks of data. Any change should go through a reviewed PR that updates
this table, not a tuning run (open question Z6-1).

## Weekly review (Z6-b)

`GET /api/journal/review?week=&history=4&narrate=false` (`app/journal/review.py`, pure) builds the review from the
same journal, tags and score. Every number comes from the trader's own trades:
- **totals**: trades, decisions, closed and open, wins and losses, win rate, net and gross P&L, charges, average R,
  expectancy, profit factor, largest win and loss, average holding time;
- **setups**: the same per setup, with what worked (net > 0) and what lost (net < 0);
- **behaviours**: per counted tag, the trades and the net P&L and average R of the closed tagged trades;
- **costliest behaviour**: the tag whose trades lost the most. This answers the spec's "one habit to fix next week" as
  a fact; the trader decides what to do with it;
- **discipline score** with its components;
- **comparison with the trader's own previous weeks only**: no leaderboard, nobody else's numbers.

Every review also carries `lines`: the same facts as short sentences that describe and never instruct. A test checks
them against the Copilot's banned-word list, the advice filter and instruction words ("should", "must", ...).

With `narrate=true`, the Copilot (the organisation's provider, `copilot.narrate`) explains the lines in plain language.
It is held to the same checks as every Copilot answer:
- every number must come from the facts;
- no advice or guarantee words;
- one retry, after which the rule lines are the explanation.

It needs the AI Copilot acknowledgement and the `ai_copilot` flag. Without them the review is still complete; only the
explanation comes from the rules.

UI: **Journal & Discipline** (Portfolio group in the sidebar) has three tabs:
- **Week**: the score card with its components, and each trade's plan vs execution, fills, MAE / MFE, costs, stop
  history, Greeks and chart reference, with its tags (confirm / dismiss);
- **Weekly review**;
- **Settings**: thresholds, event kinds, weights, and "back to defaults".

While the flag is off, the page says the feature is not enabled.

## API (all behind the flag; the logged-in trader's own data only)

| Method | Path | What it does |
|--------|------|--------------|
| GET | `/api/journal/week?week=2026-W41` | The week's journal entries with their tags, and the score with its components (default: this week). The detectors re-run on every read, so new trades and changed settings show at once. |
| GET | `/api/journal/trades/{id}` | One trade's journal. |
| POST | `/api/journal/trades/{id}/tags` | `{tag, note?}`: the trader's own tag, stored as CONFIRMED. |
| PATCH | `/api/journal/tags/{id}` | `{status: CONFIRMED / DISMISSED / SUGGESTED}`. |
| GET, PUT | `/api/journal/settings` | Thresholds and weights, the defaults, and each number's range. |
| GET | `/api/journal/review?week=&history=&narrate=` | The weekly review (Z6-b). |

## Tests

`backend/tests/test_z6_discipline_engine.py` covers:
- each detector on fixture journals, positive and negative, including setting changes;
- detection order-independence;
- score determinism (shuffled inputs), a known score value, weights, dismissed and unconfirmed tags, and structures
  counted once;
- settings validation;
- IST week bounds;
- the flag being off by default (503);
- capture hooks with the flag on and off, and a capture failure never reaching the trade path;
- Greeks at entry;
- per-user isolation (same tenant and other tenant);
- events-calendar integration;
- the migration chain.

`backend/tests/test_z6b_weekly_review.py` covers:
- the review numbers against the seeded journal (net, charges, win rate, average R, behaviour cost, score, own
  previous weeks);
- a dismissed tag leaving the review;
- teammate isolation;
- the wording checks;
- the Copilot explanation: rules without a provider, a grounded model text accepted, advice and invented numbers
  rejected.

`frontend/src/journal/format.test.ts` covers the week arithmetic and formats, and checks every string in the page
against the banned-word list.
