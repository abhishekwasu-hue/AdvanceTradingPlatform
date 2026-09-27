# ADR 0003: Eight-scope risk hierarchy, strictest limit wins, append-only risk events

**Date:** 2026-06 · **Status:** accepted

## Context
Limits are set by different people for different reasons: the platform (GLOBAL), the tenant owner
(TENANT, USER, ACCOUNT), the strategist (STRATEGY, DEPLOYMENT), and the portfolio view
(PORTFOLIO, INSTRUMENT). A rule that let a narrower scope *loosen* a wider one would let a trader
undo their owner's cap.

## Decision
`risk_limits` rows carry a scope and a target; the evaluator collects every limit that applies to
a prospective entry and applies the **strictest** value per limit type. It runs between sizing and
placement for single-leg and multi-leg orders alike. Every decision that blocks, shrinks or
triggers an action writes an append-only `risk_events` row; automatic actions are STOP_STRATEGY
and the kill switches, never a silent size change.

## Consequences
- Nobody can widen a limit by adding a narrower one; you can only tighten.
- Risk events are the audit trail for "why did this not trade" and feed the AI monitor.
- Portfolio-level limits (gross exposure, symbol concentration) need the open-position snapshot
  on every entry; the portfolio engine computes it from trades and quotes, no separate ledger.
