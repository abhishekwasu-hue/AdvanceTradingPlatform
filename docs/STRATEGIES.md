# Inbuilt Auto-Executable Scalping Strategies

Seven strategies ship inbuilt in the strategy registry (`app/strategy_engine/registry.py`) and
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

# backtest any of the seven over historical bars
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
