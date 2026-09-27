# ADR 0005: One declarative strategy DSL for builder, backtest, paper and live

**Date:** 2026-01 · **Status:** accepted

## Context
Users want to compose strategies without Python. Whatever they compose must behave identically in
the backtester, in paper trading and LIVE, or backtest results are meaningless (master prompt
section 12: "one DSL everywhere").

## Decision
`CustomStrategyConfig` (documented in `docs/STRATEGY_DSL.md`) is a JSON document: AND-combined
conditions comparing an indicator operand to a value or another indicator, separately for long and
short, plus ATR stop and R-multiple targets. `DeclarativeStrategy` turns it into a `Signal`
through the same `BaseStrategy.build_signal()` every inbuilt strategy uses, so the backtest
engine, `/signal`, paper execution and the worker are literally the same code path. Strategy
versions are immutable; the AI generator (Phase L) emits this DSL and nothing else, so its drafts
are backtested and reviewed with the same tooling.

## Consequences
- The exit-parity test proves backtest and live monitor apply the same exit rules.
- Expressiveness is bounded on purpose: no arbitrary code, so no sandbox is needed and every
  strategy can be inspected and explained.
- Schema versioning: the DSL reference is versioned (`dsl_version` in the doc); additive fields
  keep older documents valid.
