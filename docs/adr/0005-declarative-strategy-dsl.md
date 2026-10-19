# ADR 0005: One declarative strategy DSL for builder, backtest, paper and live

**Date:** 2026-01 · **Status:** accepted · **Amended by:** [ADR-0027](0027-strategy-lab-isolated-runner.md) (Strategy
Lab code modes)

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
  strategy can be inspected and explained. Amended by ADR-0027: the Strategy Lab's code modes run
  user-authored code, but only in an isolated runner container, never in the backend, worker or
  API process. Their results are research output and never trade.
- Schema versioning: the DSL reference is versioned (`dsl_version` in the doc); additive fields
  keep older documents valid.

## Amendment (ADR-0027, 2026-10)
ADR-0027 adds the Strategy Lab. Its visual and Pine-subset modes compile to ScreenQL, and the host
evaluates them, so they stay inside this record. Its code modes (Python, SQL, Lua, JavaScript,
TypeScript, R) run user-authored code, but only in a separate `lab-runner` container. That
container has no network, no secrets and no database access, and each run gets its own jail.
Each code family is behind its own default-off flag, and gate G-LAB must pass before the flag can
be turned on. Everything else here stands: deployable strategies are the DSL only, the AI emits
the DSL and never code, and no user code runs in the backend, worker or API process. ADR-0027
lists what this record still guarantees.
