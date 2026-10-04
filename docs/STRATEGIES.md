# Inbuilt Auto-Executable Scalping Strategies

Thirteen strategies ship inbuilt in the strategy registry (seven scalpers, from Phase AO four
indicator combinations and from Phase AS two daily swing strategies - see the end of this page) (`app/strategy_engine/registry.py`) and
are auto-executable: each produces a `Signal` with entry/stop-loss/targets/risk-reward/score
that flows straight through the Risk Engine into the Paper (and eventually Live) execution
router with no manual chart-reading step required. They're aimed at intraday NIFTY 50 /
index cash & F&O scalping on 1–60 minute bars.

Every strategy scores its own signal 0–100 (see `grade_from_score` in `app/core/enums.py`):
`90+` = A1, `80-89` = High Quality, `70-79` = Valid, `60-69` = Weak, `<60` = No Trade — and a
signal below the strategy's `min_rr` (default 1.2) is also demoted to `NO_TRADE`, so nothing
forces a trade that doesn't qualify.

## Multi-timeframe combos (`MTFTrendPullbackScalper`)

One parametrized strategy, registered four times as the requested timeframe pairs:

| id | Lower timeframe (entry) | Higher timeframe (trend filter) |
|---|---|---|
| `mtf_1m_5m_trend_pullback`  | 1 min | 5 min |
| `mtf_1m_15m_trend_pullback` | 1 min | 15 min |
| `mtf_5m_30m_trend_pullback` | 5 min | 30 min |
| `mtf_5m_60m_trend_pullback` | 5 min | 60 min |

**Logic (LONG; SHORT is the mirror image):**
1. HTF close is above a *rising* HTF EMA(20) → higher-timeframe trend is bullish.
2. LTF EMA(9) is above LTF EMA(21) → local structure agrees with the HTF trend.
3. Price has pulled back within `0.5 × ATR(14)` of the LTF EMA(9) → a tradeable entry zone,
   not a breakout chase.
4. RSI(14) crosses back up through 45 → the pullback is reversing, not continuing.
5. ADX(14) ≥ 18 → the market is actually trending, filtering out chop.

All five must align; otherwise the strategy returns `NO_TRADE` with the specific reason(s).
Stop loss is `close − ATR(14) × 1.2` (long) / `close + ATR × 1.2` (short); targets default to
1.5R and 2.5R.

This is the "pullback + support/resistance + price-action reversal" style the brief asks for,
rather than a breakout-only entry, applied across the requested timeframe pairs.

## Indicator-based single-timeframe scalpers

### EMA + RSI Scalper — `ema_rsi_scalper_1m`
EMA(9)/EMA(21) crossover for entry timing, RSI(14) vs its 50 midline as a momentum-direction
filter. Bullish EMA cross + RSI > 50 → LONG; bearish cross + RSI < 50 → SHORT. Stop loss is
1× ATR(14) beyond entry; default target RR is 1.5R/2R. Defaults to 1-minute bars for HFT-style
scalping but `tf` is a parameter.

### Supertrend + ADX Scalper — `supertrend_adx_scalper_1m`
Supertrend(10, 3) trend flip as the entry/exit trigger, ADX(14) ≥ 20 as a strength filter so
flips inside a flat/choppy market are ignored. Stop loss sits just past the current Supertrend
line (`line ∓ 0.3 × ATR`), which trails naturally as the line itself trails price. Default
1-minute bars.

### RSI + ADX Momentum Scalper — `rsi_adx_momentum_5m`
RSI(14) crossing its 50 midline, confirmed by directional dominance (+DI > −DI for longs,
−DI > +DI for shorts) and ADX(14) ≥ 20. Stop loss is 1× ATR(14) beyond entry; default target RR
1.5R/2R. Defaults to 5-minute bars (a slightly slower momentum confirmation layer to pair with
the faster 1-minute strategies above).

## Using them

```bash
# list every inbuilt strategy and its default params
curl -s localhost:8000/api/strategies | jq

# generate a signal from OHLCV candles you already have (per timeframe)
curl -s -X POST localhost:8000/api/strategies/ema_rsi_scalper_1m/signal \
  -H 'content-type: application/json' \
  -d '{"symbol": "RELIANCE", "candles": {"1min": [...]}}'

# same, but auto-route through risk management + paper execution if tradeable
curl -s -X POST localhost:8000/api/strategies/mtf_1m_5m_trend_pullback/paper-execute \
  -H 'content-type: application/json' \
  -d '{"symbol": "NIFTY", "candles": {"1min": [...], "5min": [...]}}'

# backtest any of the thirteen over historical bars
curl -s -X POST localhost:8000/api/backtest \
  -H 'content-type: application/json' \
  -d '{"strategy_id": "supertrend_adx_scalper_1m", "symbol": "BANKNIFTY", "base_timeframe": "1min", "candles": [...]}'
```

`paper-execute` is safe to call freely — `LIVE` mode is intentionally blocked
(`LiveTradingNotConfigured`) until a real, authenticated broker adapter is wired in, per the
platform's safety rules: nothing here can place a real order yet.

## Tuning

Every strategy's parameters (EMA/RSI/ADX/ATR periods, thresholds, target R:R, `min_rr`) are
constructor kwargs with sensible scalping defaults — override per-call via `strategy_params` on
`/api/backtest`, or by constructing the strategy directly in Python for programmatic tuning /
walk-forward testing.


## Indicator combinations (Phase AO, `app/strategy_engine/combo_strategies.py`)

Four widely used intraday setups, each pairing a trigger with a filter. Single timeframe, 5-minute
by default (`tf` parameter); stops are ATR- or structure-based and targets come from the shared
`build_signal` (`target_rr`, `min_rr`).

| id | Trigger | Filter / confirmation | Stop |
|---|---|---|---|
| `macd_ema_trend_5m` | MACD(12,26,9) crosses its signal line | close on the same side of EMA(200) | 1.2 x ATR(14) |
| `bb_rsi_reversion_5m` | close back inside the Bollinger band (20, 2) | previous bar outside the band with RSI(14) <= 30 / >= 70 | beyond the last 3 bars' extreme + 0.2 ATR |
| `vwap_supertrend_5m` | Supertrend(10,3) flip, or a VWAP reclaim/loss | Supertrend direction and VWAP side agree | Supertrend line +/- 0.2 ATR |
| `orb_15m_5m` | first close beyond the 09:15-09:30 IST range | volume >= 1.5 x its 20-bar average (skipped for indices, which carry no volume); entries until 14:30 | middle of the range |

Indices have no volume, so the VWAP used by `vwap_supertrend_5m` falls back to the session's running
mean of the typical price (`session_vwap_or_mean`).

### On the chart

`POST /api/strategies/{id}/chart-run` walks a strategy over the candles a chart is showing with the
backtest engine (nothing is recorded or metered) and returns its trades, win rate, P&L and the
signal on the last bar. The Pro Chart's **Strategies** panel switches each strategy on the chart
(entries, exits and the last signal's entry / stop / target lines, plus its indicators) and, on a
broker-symbol chart, **Deploy** creates or resumes a PAPER deployment on that symbol (off = pause).
A strategy runs on its own timeframes, so the chart must be at that timeframe or a finer one that
divides it; the panel offers the switch when it is not.

## Swing strategies (Phase AS, `app/strategy_engine/swing_strategies.py`)

Daily-candle setups held overnight, for a trader who cannot watch the screen. They decide on
**closed** daily bars (the market-data service serves completed days for the `day` interval) and are
deployed with `holding: "SWING"` and `timeframe: "day"`.

| id | Trigger | Filter / confirmation | Stop | Targets |
|---|---|---|---|---|
| `swing_ema_pullback_d` | a pullback that touches EMA(20) and closes back above it on a bullish day (mirror for shorts) | EMA(20) above EMA(50) and close above EMA(50); RSI(14) >= 40 | below the 2-day pullback low - 0.25 ATR, at least 1 ATR | 2R / 3R |
| `swing_breakout_d` | a close beyond the previous 20 sessions' high (low) | ADX(14) >= 20; volume >= 1.5 x the 20-day average (skipped when the symbol has no volume) | back inside the old range by 1 ATR, between 1 and 3 ATR | 2R / 3.5R |

What a swing deployment changes:

* **Product**: CNC (delivery) for cash equity, NRML for futures and bought options - entry, exit and
  the broker-side protective stop alike (`app/execution/products.py`). Exits of a swing position use
  the same product as its entry.
* **No square-off**: the worker's end-of-day square-off skips SWING trades; the stop guard re-arms a
  day-validity stop each morning if the broker expired it.
* **Long only in cash**: delivery shares cannot be held short overnight, so a SHORT signal on an
  UNDERLYING swing deployment is skipped with a reason; take swing shorts on futures or options.
* **Not allowed**: multi-leg structures and written options overnight.
* **Data**: daily candles fetch at least 400 days (EMA 200 and 52-week levels need it); the
  staleness gate allows a long weekend plus a holiday on daily bars.
* The strategy interview sizes swing risk a quarter smaller than intraday (an overnight gap can open
  beyond the stop), uses monthly expiries for options and never adds a 15:10 time exit.

