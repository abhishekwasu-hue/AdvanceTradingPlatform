# The Pine Script subset (handover 11c-1, AMW-20 first slice)

Status: built behind the flag `strategy_lab_pine` (OFF by default). Code: `backend/app/lab/pine/`, panel
`frontend/src/lab/pine/`. Owner order: `docs/owner-orders/ATP_HANDOVER_PACK.md` section 11 (c-1, d, i). Design:
`docs/design/AMW_PORT.md` section 3.3 (Pine-subset mode), ADR-0027 (host modes), `docs/design/STRATEGY_LAB_THREAT_MODEL.md`.

## 1. What it does

A trader pastes a TradingView Pine v5 script. ATP:

1. parses it with a hand-written tokenizer and parser (`tokens.py`, `parser.py`);
2. translates every **signal condition** the script states into one ScreenQL condition (`translate.py`), built from a
   fixed table of supported names;
3. validates each condition with the same `compile_screen` a user's own screen goes through (types, units, windows,
   look-ahead, cost cap);
4. returns line-numbered diagnostics: what is supported, what is ignored (and why), what is not translated (with the
   closest supported alternative);
5. shows each condition in plain words, and can run one as a screen through the Screener's own run path.

**The Pine text is never executed.** There is no `eval`, `exec`, interpreter or runner anywhere in `app/lab/pine`: the
parser builds a small syntax tree and the translator maps it to ScreenQL nodes. User text never becomes ScreenQL text:
names come from the table, a variable is inlined as the nodes it was built from, an input becomes a `$parameter`
(a plain identifier), every number is a parsed float, and titles / messages / strings are never copied into a
condition. This is a host mode (ADR-0027): no runner, and the G-LAB checklist does not gate it.

## 2. Statements

| Pine | Subset |
|---|---|
| `//@version=5` | required (v6 is read as the v5 subset; v4 and older are refused: convert in TradingView first) |
| `indicator(...)`, `strategy(...)` | metadata only (the title). Sizing / capital / cost / pyramiding arguments are reported and ignored |
| `name = expr`, `float name = expr`, `series int name = ...` | a declaration; the value is inlined where it is used |
| `[a, b, c] = ta.macd(...)` | a tuple from `ta.macd`, `ta.bb`, `ta.supertrend`, `ta.dmi`; `_` skips a value |
| `x = input.int(14, "Len", minval=1, maxval=200)` | a parameter `$x` with its default, bounds and title |
| `input.float`, `input.bool`, `input(...)` | parameters (float / bool / by the default's type) |
| `input.source(close)` | fixed at its default (reported) |
| `if cond` + indented body | the body may hold only `strategy.entry`, `strategy.close`, `strategy.close_all`, `alert` and nested `if` blocks; nested conditions are joined with AND |
| line wrapping | open `(` / `[`, or a next line indented by a number of spaces that is not a multiple of 4 |

## 3. Signal conditions

| Pine | Signal |
|---|---|
| `if cond` + `strategy.entry(id, strategy.long)` / `strategy.short` | long-side / short-side condition |
| `strategy.entry(..., when=cond)` | the same, condition from `when` |
| `strategy.close(id)` / `strategy.close_all()` inside `if` or with `when=` | exit condition |
| `alertcondition(cond, title, message)` | alert condition (the title is the label) |
| `plotshape(cond, ...)`, `plotchar(cond, ...)` with a true/false series | marker condition |
| `alert(...)` inside `if cond` | alert condition |

Order sizing and order prices are **reported and ignored**: `qty`, `qty_percent`, `default_qty_*`, `initial_capital`,
`pyramiding`, `commission_*`, `slippage`, `limit`, `stop`, `strategy.exit(...)`, `strategy.risk.*`. Lots, legs, stops,
targets, exits and the number of trades are the user's own deployment settings (CLAUDE.md section 3). A signal with no
condition (it would fire on every bar) is refused. At most 20 signals per script.

## 4. Expressions

Operators, Pine's precedence (lowest first): `?:`, `or`, `and`, `== !=`, `< > <= >=`, `+ -`, `* /`, unary `- + not`,
then `x[n]` and calls. Literal arithmetic is folded (`2 * 3` -> `6`).

| Pine | ScreenQL |
|---|---|
| `open high low close volume` | the fields |
| `hl2`, `hlc3`, `ohlc4`, `hlcc4` | `(high + low) / 2`, ... |
| `x[n]` (n a whole number 0..500) | the offset added to every field and call inside `x` |
| `x[len]` (len an `input.int`, x a number) | `Lag(x, $len)` |
| `and`, `or`, `not` | `AND`, `OR`, `NOT` |
| `c ? a : b` with a and b conditions | `(c AND a) OR (NOT c AND b)` |
| `barstate.isconfirmed` | `TRUE` (ATP reads closed bars only) |
| `ta.ema(src, n)`, `ta.sma(src, n)` | `EMA(src, n)`, `SMA(src, n)` |
| `ta.rsi(close, n)` | `RSI(n)` (`ta.rsi(close[k], n)` -> `RSI(n)[k]`); another source is refused |
| `ta.atr(n)` | `ATR(n)` |
| `ta.highest(src, n)`, `ta.highest(n)` | `Max(src, n)`, `Max(high, n)` |
| `ta.lowest(src, n)`, `ta.lowest(n)` | `Min(src, n)`, `Min(low, n)` |
| `ta.change(x, n)`, `ta.mom(x, n)` | `x - x[n]` (`x - Lag(x, $n)` for an input length) |
| `ta.crossover(a, b)`, `ta.crossunder(a, b)`, `ta.cross(a, b)` | `CrossAbove`, `CrossBelow`, either |
| `[m, s, h] = ta.macd(src, f, sl, g)` | `EMA(src, f) - EMA(src, sl)`, `EMA(that, g)`, their difference |
| `ta.stoch(src, high, low, n)` | `100 * (src - Min(low, n)) / (Max(high, n) - Min(low, n))` |
| `[mid, up, lo] = ta.bb(close, n, k)` | `BBMid(n, k)`, `BBUpper(n, k)`, `BBLower(n, k)` (source close only) |
| `[line, dir] = ta.supertrend(k, n)` | `Supertrend(n, k)`; `dir < 0` -> `close > Supertrend(n, k)`, `dir > 0` -> `close < ...`; `ta.change(dir) < 0` / `> 0` -> the turn bar; `dir != dir[1]` |
| `[p, m, a] = ta.dmi(n, n)` | `PlusDI(n)`, `MinusDI(n)`, `ADX(n)` (one length for both) |
| `ta.stdev(src, n)` | `StDev(src, n)` (population, as Pine's default; needs `screenql_amw_functions`) |
| `ta.roc(src, n)` | `PctChange(src, n)` |
| `ta.rising(src, n)`, `ta.falling(src, n)` | `src > Max(src, n)[1]`, `src < Min(src, n)[1]` |
| `math.max(a, b, ...)`, `math.min(...)` | `Greatest(...)`, `Least(...)` |

A length must be a whole number 1..500 or an `input.int`; a Supertrend factor / Bollinger multiplier a positive number
or an input (one value for the whole series, Z10-a). Units are checked by the ScreenQL validator
(`close > ta.rsi(close, 14)` is refused: a price against an oscillator).

Drawing is ignored (reported as such): `plot`, `hline`, `fill`, `bgcolor`, `barcolor`, `plotcandle`, `plotbar`,
`label.*`, `line.*`, `box.*`, `table.*`, `color.*`, `input.color`.

## 5. Not translated (each with a line-numbered diagnostic and the closest alternative)

| Construct | Alternative offered |
|---|---|
| `ta.wma`, `ta.hma`, `ta.vwma`, `ta.alma`, `ta.swma`, `ta.linreg` | `ta.ema` / `ta.sma` |
| `ta.rma` | `ta.ema(src, 2 * len - 1)` (the same smoothing) |
| `ta.pivothigh` / `ta.pivotlow` | `SwingHigh()` / `SwingLow()` in a ScreenQL screen |
| `ta.valuewhen`, `ta.barssince` | direct comparisons, `Count` / `CountStreak` |
| `ta.cci`, `ta.mfi`, `ta.cmo`, `ta.wpr`, `ta.kc`, `ta.sar`, `ta.vwap`, `ta.obv`, `ta.tr`, ... | the nearest supported function (table in `translate.py`) |
| `request.*` (without look-ahead) | the chart's own timeframe; `@1w` in a ScreenQL screen |
| `var`, `varip`, `:=`, `+=` ... | history references or `ta.highest` / `ta.lowest` (no state carried from bar to bar) |
| `for`, `while`, `switch`, user functions (`=>`), `else`, `import`, `type`, `method` | one expression per condition, functions inlined |
| `na`, `nz`, `na()`, `fixnan` | not needed: ATP never matches a bar where a value is missing |
| `%`, `math.abs`, `math.round` ..., numeric `?:` | written-out comparisons |
| `bar_index`, `time`, `hour`, `dayofweek`, `syminfo.*`, `strategy.position_size` ... | per name (the subset has no clock, calendar or position rules) |
| `input.string` / `input.timeframe` / `input.symbol` used in a condition | `input.int` / `input.float` / `input.bool` |

A signal that uses a value from a line that did not translate is listed as not translated, naming that line; the
other signals still translate.

## 6. Look-ahead

Refused for the **whole script** (no signal is offered, and "Run as screen" answers 422), wherever it appears, even in
a plot or a line the subset does not translate:

- `barmerge.lookahead_on`, and `request.security(..., lookahead=...)` with anything but `barmerge.lookahead_off`;
- a negative history offset `x[-1]`, `x[-n]`;
- a negative `offset=` (a value drawn on a bar before it was known);
- a history reference by an input whose default or `minval` is negative.

The ScreenQL validator then checks the translated condition again (no negative offsets, closed bars only).

## 7. Limits (fuzz-tested, `backend/tests/test_lab_pine_fuzz.py`)

| Limit | Value |
|---|---|
| Script size | 50,000 characters, 2,000 lines, 20,000 tokens |
| Nesting in one expression | 40 levels (brackets, calls, `?:`, unary chains) |
| Operators in one statement | 200 (a long flat chain is a deep tree too) |
| One translated value | 1,500 ScreenQL nodes, nested at most 40 deep (stops exponential inlining) |
| History reference | 500 bars back |
| Signals per script | 20 |
| The ScreenQL condition | the screen limits: 4,000 characters, the organisation's cost cap |

The scanner is single-pass and linear (no backtracking regular expressions); every limit is a diagnostic, never an
exception (any exception is a bug: the fuzz tests feed random tokens, random characters, mutated templates and deep
inputs, each under a time budget).

## 8. Values

Values follow ATP's own indicator code, so they match ATP's charts, screens and backtests; they converge to, but are
not promised to equal, TradingView's (EMA seeding; ATP's Bollinger bands use the sample standard deviation, Pine's the
population one). The page says so. On a bar where the close equals the Supertrend line exactly, `dir` is read from the
comparison with the line.

## 9. API (flag `strategy_lab_pine`; 404 while off)

| Route | Class | What |
|---|---|---|
| `GET /api/lab/pine/templates` | member | five templates: EMA cross, RSI pullback, Supertrend flip, MACD cross, Bollinger re-entry (signal conditions only, no performance claim) |
| `POST /api/lab/pine/translate` | self (computes, stores nothing; 30 per minute per user) | `{source, base_tf}` -> signals, params, diagnostics, constructs |
| `POST /api/lab/pine/run` | trader | `{source, signal?, params?, symbols, exchange, base_tf, preview?}` -> the Screener run (needs `screener_v2`; the same validation, gates and per-user run limit; the run stored under the caller's organisation) |

## 10. Later (handover 11d)

The same translated condition reaches the screen's backtest, the paper forward test, PAPER Autopilot and alerts in the
next slices, through the paths screens already use. Lab storage (drafts, immutable versions, run records, the
`lab-envelope/1` with sides / stop / target) is AMW-19. The AI "convert a TradingView Pine script" assistant (11e) can
call `translate` and show the diagnostics as "what changed".
