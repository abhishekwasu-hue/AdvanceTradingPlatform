# ADR-0026: The discipline engine - auto-journal, behaviour tags and a weekly score, personal and flag-gated

**Date:** 2026-10 · **Status:** proposed (Z6 / P1-e) · **Phase:** P1

## Context
The P1 spec (§1.3) calls the journal "the heart of the product":
- auto-journal every trade;
- tag the behaviours that lose money (revenge, FOMO, averaging a loser, moving the stop, no stop, overtrading, early
  exit, holding through events);
- score the trader's discipline each week.

The mission adds a hard rule: ATP explains numbers and never advises. The trade pipeline already writes every fill
(PAPER and LIVE) through one function (`persist_trade`) and closes every position through one function
(`close_position`). Z3 (PAPER approval) is being built in parallel on the same pipeline.

## Decision
1. **Separate journal tables.** These are `journal_entries`, `journal_tags` and `journal_settings`, keyed by trade or
   user. No new columns go on `trades`: the trade row stays the execution record, and the journal is derived data that
   can be rebuilt from it.
2. **Capture hooks after the commit, in their own session, flag-gated, never raising.** The hooks sit at the existing
   single points (entry, monitor marks, stop change, exit). A journal fault can never fail or roll back a trade, and
   nothing in the order path waits on it. Whatever a hook missed is rebuilt from the trade row on read.
3. **Pure detectors and score.** No database, no clock and no AI in `detectors.py` and `score.py`. The same inputs
   always give the same tags and score; tests shuffle the inputs to check this.
4. **Every threshold is a per-trader setting** with a documented, reasoned default and a range
   (`docs/design/DISCIPLINE_ENGINE.md`). Nothing is fitted in code.
5. **The trader decides.** Auto tags start SUGGESTED. A re-run never overrides CONFIRMED or DISMISSED. DISMISSED never
   counts in the score.
6. **Personal scope.** The journal is per user (`user_id` and `tenant_id` on every query), not per tenant. Risk
   budgets stay tenant-wide.
7. **Chart snapshots are references**, redrawn from the market data lake "as of" the instant (ADR-0013). No images are
   stored.
8. **Flag `discipline_engine`, OFF by default.**

## Consequences
- Z3's PAPER fills are journaled with no change to Z3. Z3 can label them `PROPOSAL` through `persist_trade`'s
  `journal_source` argument, or through `capture.on_entry(..., source="PROPOSAL")`.
- Reading a week runs the detectors, which costs one query for the week plus context. That is acceptable for a
  personal view; a nightly pre-compute can come later if needed.
- The weekly review (Z6-b) reads the same tables and the same score. The Copilot only narrates those numbers, through
  the existing grounding check and advice filter.
