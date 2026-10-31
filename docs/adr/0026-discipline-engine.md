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
2. **Capture hooks after the commit, off the trade path, flag-gated, never raising.** The hooks sit at the existing
   single points (entry, monitor marks, stop change, exit). The flag is read on the caller's own connection (cached),
   and the work is queued to one background task with its own session and a timeout, so a journal fault can never
   fail, roll back, delay or block a trade or an exit, and nothing in the order path waits on it or on a second pool
   connection. Whatever a hook missed is rebuilt from the trade row on read.
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

## Addendum (M62 JN-1 / JN-2, 30 Oct)
- The trader's own layer is added beside the auto-captured data (`app/journal/entries.py`): his rule checklist and his own
  tags on `journal_entries` (two nullable columns), manual trades done outside ATP in `journal_manual_trades` (journal data
  only: no order, position, broker, risk or P&L path reads it), images in `journal_attachments` (files in the private
  store of `app/chart_analyze/storage.py`). Notes of an ATP trade stay on `trades.notes`.
- Chart snapshots stay references (decision 7): the PNG is drawn on request by `app/ai/chart_png.py` from the stored bars
  ending at the instant, with entry / exit / stop marked; nothing is stored.
- `deploy/atp.sh journal-status` (read-only) and `journal-backfill [--tenant ID]` (`app/journal/ops.py`) report and fill
  the rows of closed trades. The flag stays OFF by default; the owner turns it on per organisation.
