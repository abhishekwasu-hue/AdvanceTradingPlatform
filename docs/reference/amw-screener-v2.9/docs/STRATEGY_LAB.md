# Strategy Lab

Users build custom screeners in eight authoring modes — **Visual No-Code Builder, Pine Script (subset), Python, SQL,
JavaScript, TypeScript, Lua and R**. All modes are released; operators can restrict them with `AMW_LAB_LANGUAGES`.
Every mode shares one contract
(`src/lab/contract.py`), so timestamps, candle indexing, warm-up and signal semantics are identical.

## Workflow
1. **New strategy** → choose mode → start from a template → *Create draft*.
2. **Validate** — syntax and strategy-API checks. *Validation is not execution.*
3. **Run** — executes the draft on a few symbols with live validated data; shows each rule's result, logs and errors.
4. **Publish version** — freezes the draft as an immutable version (SHA-256 recorded). Drafts are never executed by
   scans or backtests, and imported/saved code is never executed automatically.
5. **Scan** a universe or **Backtest** a published version. Runs are recorded in `lab_runs`
   (`QUEUED/RUNNING/COMPLETED/FAILED/CANCELLED/PARTIALLY_COMPLETED`, configuration snapshot, runtime, counts, timing).

## Input context (same for every language)
* `meta` — symbol, ticker, exchange, timeframe, timezone, provider, data kind, data status, last completed bar.
* `series` — `time, open, high, low, close, volume, ema20, ema50, ema200, sma20, sma50, rsi14, macd, macd_signal,
  macd_hist, atr14, adx, plus_di, minus_di, rvol, vwap, bb_up, bb_low`. Oldest → newest; the **last element is the
  last completed bar**; missing values are `None`/`null` (never 0).
* `structure` — trend label/score, structure trend, **confirmed** swings (with confirmation index), BOS/CHoCH events,
  zones, S/R. `htf` — optional higher-timeframe summaries. `params` — validated parameters.

## Result (validated)
`{status: qualified|rejected, bias: long|short|neutral, classification, rules: [{name, passed: true|false|null, detail}],
entry, invalidation, stop, target, explanation}` — malformed output, non-finite numbers or a stop on the wrong side
of the close are rejected. The signal timestamp is always set by the host.

## Python
`def evaluate(ctx): ...` returning the result dict. Helpers (`amw.*`): `last, prev, highest, lowest, crossover,
crossunder, ema, sma, rsi, atr, isna`. Imports: `math`, `statistics` only. `print()` output appears in the run log.
Templates: Trend, Breakout, Pullback, Reversal, Relative volume, Multi-timeframe confluence.

Execution: separate process; Linux sandbox (no network, uid 65534, private /tmp, read-only work dir, rlimits, empty
environment) plus in-process guards (restricted builtins, import allow-list, audit hook blocking file/socket/subprocess,
`exec`/`compile` of anything but the strategy). Windows: *local trusted mode* only (no OS sandbox) — see README.

## Pine Script (supported subset)
Translated to the Lab's intermediate representation and evaluated by the host engine — Pine is **never executed** and
this is **not** a TradingView-compatible runtime.
* Required: `//@version=5` or `6`; `long_signal = <condition>` and/or `short_signal = <condition>` (or `alertcondition`).
* Supported: `indicator()/strategy()` header, single assignments, `input/input.int/input.float`, series
  `open high low close volume hl2 hlc3 ohlc4`, history `x[n]`, `ta.ema ta.sma ta.rsi ta.atr ta.highest ta.lowest
  ta.change ta.stdev ta.crossover ta.crossunder`, arithmetic, comparisons, `and/or/not`.
* Risk inputs: `stop_atr = input.float(1.5)` (stop = close ∓ stop_atr × ATR14), `target_r = input.float(2.0)`.
* Rejected with a line-numbered diagnostic: `strategy.*` orders, `request.*`, `var/varip`, `:=`, `if/for/while`, user
  functions, ternary `?:`, `na()/nz()`, `math.*`, arrays/maps. `plot*` calls are ignored with a warning.
* Semantics: evaluated on the last **completed** bar (like `barstate.isconfirmed`). A comparison with a missing value is
  *missing* (not qualified). Indicator values match the host engine; they converge to, but are not guaranteed to be
  bit-identical with, TradingView after warm-up.
* Templates: EMA trend, EMA crossover, Donchian breakout + volume, RSI pullback, RSI reversal, Volume spike.

## Visual No-Code Builder
Rule tree with nested AND/OR groups (depth ≤ 6, ≤ 300 nodes), comparisons and crosses over OHLCV fields and indicators
(EMA, SMA, RSI, ATR, highest/lowest, change, stdev, MACD, ADX, RVOL, VWAP, Bollinger) with history offsets, and
predicates for trend/HTF trend, BOS/CHoCH, last confirmed swing break, support/resistance proximity, demand/supply
zones, breakout-and-retest, EMA pullback/rally, session window, minimum traded value and data quality. Stop (ATR or
swing) and target (R). The builder validates the tree, shows the readable logic, computes the required warm-up, and
evaluates the language-independent IR with the host engine (no code is generated and executed). Conditions are ordered
with ↑/↓ buttons (not drag-and-drop). Export to Python is offered only for the expression subset, whose semantic
equivalence is verified by tests; structure predicates and swing stops are not exportable.
Templates: Bullish trend, Bearish trend, Breakout and retest, Trend-continuation pullback, Support reversal,
Resistance reversal, Volume expansion, Multi-timeframe confluence.

## SQL screener
Exactly one read-only `SELECT` (CTEs allowed) over the approved views `bars` (all bars of the symbol) and `latest`
(last completed bar) with columns `symbol, timeframe, bar_index, ts, open, high, low, close, volume, ema20, ema50,
ema200, sma20, sma50, rsi14, macd_hist, atr14, adx, rvol`. Return a `symbol` column (optional `bias`, `note`);
a returned row means *qualified*. Parameters are bound as `:name`. Enforcement: sqlglot allow-list (no DDL/DML/PRAGMA/
ATTACH, single statement, allowed functions only, approved views only) plus a SQLite authorizer, step and row limits
inside the sandbox. Limitations: evaluated per symbol (no cross-sectional ranking), no stateful trade logic; backtests
use per-bar screening only. Templates: Trend filter, Momentum (RSI band), Volume expansion, 20-bar high breakout.

## JavaScript and TypeScript
`function evaluate(ctx) { ... }` returning the result object. Runs in a Node.js `vm` context created from a
null-prototype object (no host-realm objects reachable), code generation from strings and WebAssembly disabled,
`Math.random`/`Date` disabled for determinism, no `require`/`process`/`import`, under `node --permission`.
TypeScript is type-checked against `amw-sdk.d.ts` (`Context`, `Result`, `amw`) with TypeScript 6.0.2 and transpiled
before execution; type errors are reported with line numbers.

## Lua
Lua 5.4 via lupa 2.8. `os, io, package, require, debug, load*, dofile, collectgarbage, coroutine, string.dump,
raw*, set/getmetatable` removed; memory limit and instruction limit (infinite loops stop). Arrays are 1-based;
missing values are NaN (`amw.isna`).

## R
`evaluate <- function(ctx) list(...)`. Base R plus selected `stats`/`utils` functions (`sd, var, median, quantile, cor,
lm, coef, residuals, fitted, predict, t.test, na.omit, complete.cases, head, tail`). `library()` allow-list: `stats`,
`utils`; forbidden: system/shell/pipe/file/url/socket/eval/parse/source/get/assign/environment/`.Internal`/`.Call`/
`:::`/package installation. Missing values are NA. Templates: Trend, return z-score.

## Capability matrix
Shown on the Strategy Lab page and at `GET /api/v1/lab/capabilities`. It is **detected** (tool presence + sandbox
self-test), not hard-coded. On a machine without a verified sandbox, code execution is reported as unavailable.
