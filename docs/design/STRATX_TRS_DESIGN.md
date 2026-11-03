# STRAT-X T-RS - relative strength vs NIFTY: design for the owner's decision

Status: **design only, nothing built** (OPEN_QUESTIONS STRATX-Q1: an engine / data-path change needs the owner's yes).
T-PDHL, T-52WH and T-WDP are built (commit 7a9d0faa3); T-RS is the fourth STRAT-X template.

## Why it is not a plain template today (facts from the code)

- A template is compiled into a recipe and evaluated by `app/strategy_templates/engine.py` `compute(recipe, frames, symbol)`:
  one `SymbolData(symbol, frames)` - the symbol's own bars only.
- The template adapter (`app/strategy_templates/adapter.py`, used by the backtest and the worker) passes
  `{timeframe: df}` of that one symbol; the scan runtime (`app/screener/runtime.py` `evaluate`) reads one `SymbolData`.
- NIFTY appears only as a backtest **benchmark of returns** (`app/screener/backtest.py`, `benchmark_closes`), never as an
  input a rule can read. So "the symbol's close / NIFTY's close" cannot be written as a rule today.

## Options

| | What | Work | Risk |
|---|---|---|---|
| **A (recommended)** | A benchmark series inside the engine: `SymbolData` gets an optional benchmark frame (NIFTY 50, same timeframe, joined on bar start); one new ScreenQL function `rs(n)` = (close / close n bars ago) / (NIFTY close / NIFTY close n bars ago) - 1, i.e. how much the stock moved over n bars relative to NIFTY. Every path that runs templates fetches the benchmark bars too: scan, backtest, options backtest, worker. | 3 PRs: engine + function + tests; the 4 fetch paths; the T-RS template + its card | Medium: data path in 4 places; covered by a parity test (scan = backtest = worker, same signals) and a no-look-ahead test (a benchmark bar counts only once closed; a missing benchmark bar = no signal, never a guess) |
| B | A screener-only "RS vs NIFTY" column (a ranking, not a template) | 1 PR | Low, but it is not the template STRAT-X lists and cannot run as a bot |
| C | Not built | - | - |

Notes for A:
- Bars come from the stores ATP already has (NIFTY 50 index bars: shared index read path M55 T4 / the M71 store); no new
  data source, no paid feed, the owner's beta data decision (M71 option 1) unchanged.
- The rule's numbers (n, the threshold) are the user's own settings; ATP pre-chooses nothing (M68 / owner 31 Oct).
- White box: the template card shows the formula in words ("how much this stock moved over N bars compared with NIFTY"), no
  performance claim.

**Default (until the owner answers): nothing is built** (STRATX-Q1). Recommendation: A.
