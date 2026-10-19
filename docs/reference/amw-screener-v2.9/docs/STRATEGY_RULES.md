# Strategy and structure rules

All rules use **completed bars** only. Every threshold below lives in `config/settings.yaml` and is versioned.

## Market structure
* **Swing high/low**: bar *i* whose high (low) is strictly above (below) the *k* bars to the left and ≥ (≤) the *k*
  bars to the right (`swing_k`, default 3). It is **known only at bar i+k** (`confirmed_at`); nothing uses it earlier.
* **HH/HL/LH/LL**: each confirmed swing compared with the previous swing of the same kind.
* **BOS**: a bar **closes** beyond the latest confirmed swing in the direction of the current structure trend (or the
  first break). **CHoCH / MSS**: a close beyond the latest confirmed swing *against* the current structure trend.
  A wick beyond the level with the close back inside is a liquidity **sweep**, not a break.
* **Zones**: 1–3 base candles (body ≤ 50 % of range) followed by an impulsive candle (range ≥ 1.3 ATR, body ≥ 60 %)
  closing beyond the base → RBR/DBR demand or RBD/DBD supply. Status: fresh → tested (touch) → invalidated (close beyond).
* **S/R**: clusters of ≥ 2 confirmed swings within 0.5 ATR. **Liquidity pools** (BSL/SSL, equal highs/lows) are
  hypotheses derived from untaken swings — OHLCV cannot reveal real orders.

## Regime (`src/analysis/regime.py`)
Trend votes = EMA alignment, EMA50 10-bar slope (±0.5 ATR), structure trend. Trending if ≥ 2 votes agree and ADX ≥ 20;
range-bound if ADX < 20 and flat EMA50; otherwise transition. Volatility state from ATR14/ATR50 and realised-volatility
percentile.

## Scoring
`score = 100 × Σ(wᵢ·sᵢ) / Σ(all weights)` over components *trend_mtf, structure, volume, level_quality, momentum,
relative_strength, risk_reward*. Missing evidence contributes 0 and lowers `score_coverage`; STALE data −5.
A signal qualifies only if **every mandatory condition passed and data is usable** — the score can never override a
failed mandatory rule. Labels: ≥ 85 A1 Candidate, ≥ 70 Strong, ≥ 55 Watchlist, else Low Confluence (configurable;
not calibrated probabilities).

## Strategies (mandatory conditions marked M)
* **Breakout** — levels: last confirmed swing, consolidation range (≤ 4 ATR), pattern boundary, 3-swing trendline.
  M: completed close beyond level within `fresh_bars`; still holding (else *failed breakout / trap*); close beyond by
  ≥ 0.1 ATR; RVOL ≥ `min_rvol` (if required); retest (if required); R:R ≥ `min_rr`. Gap breakouts are flagged.
* **Pullback** — M: trend score ≥ 0.5 with matching structure; touched EMA20/EMA50, prior BOS level, S/R, zone or
  Fib 38.2–61.8 %; not extended (≤ 1 ATR); rejection candle or EMA20 reclaim (if required); R:R. Optional: depth
  23.6–78.6 %, volume contraction.
* **Reversal** — M: prior opposite trend or RSI extreme; ≥ 2 independent triggers (sweep, S/R/zone rejection, failed
  breakdown/breakout, confirmed double/triple/H&S, RSI divergence, reversal candle); R:R. Without CHoCH it is an
  **early candidate**, never a confirmed reversal.
* **Trend continuation** — M: close > EMA20 > EMA50 (> EMA200), structure bias ≥ 0.5, ADX ≥ 20 with DI agreement, R:R.
* **Intraday** (5m/15m/30m) — Opening-range breakout, VWAP pullback, VWAP-reclaim reversal; session window filter;
  VWAP only when session volume is complete; signals expire at session end.
* **Swing** — daily Breakout/Pullback/Reversal + weekly alignment gate, stop ≤ 8 % of price, holding-period
  assumption, overnight-gap warning.

## Signal lifecycle
`candidate → awaiting_confirmation → qualified → active → completed`, plus `invalidated`, `expired`, `rejected`,
`insufficient_data`. Illegal transitions raise errors; each transition is stored with reason and timestamp.
Signals are de-duplicated by `ticker|strategy|setup|timeframe|direction|signal bar`.

## Analysis modules v2 (v2.6.0) — explicit definitions, no predictive value implied

**Supertrend** (`indicators.supertrend`): ATR(10) Wilder, multiplier 3, standard band ratchet; `st_dir` +1/-1;
NaN during ATR warm-up. Value at bar t uses bars <= t only.

**Fibonacci extensions** (`indicators.fib_extensions`): 127.2 %, 161.8 %, 200 % of the impulse beyond its end.

**Candlestick definitions v2** (`candles.CANDLE_TOL`, configurable):
| Pattern | Definition |
|---|---|
| Pin Bar | one wick >= 2/3 of the range, body <= 1/3, body in the opposite third (bullish = long lower wick) |
| Strong Body | body >= 70 % of the range and range >= 1 x ATR(14) |
| Rejection | wick >= 50 % of the range, close in the opposite half, not already a Pin Bar |
| Gap Reversal | open gaps >= 0.5 x ATR beyond the prior close and the bar closes back beyond the prior close |
Existing patterns (Hammer, Shooting Star, Engulfing, Doji, Inside/Outside Bar, Morning/Evening Star) are unchanged.
Every pattern stays a *candidate* until the next bar: confirmed (close beyond the pattern high/low), failed or
unconfirmed. Built-in strategies use only their own named pattern lists, so v2 patterns do not change them.

**Gaps** (`structure.gaps`): opening gap >= 0.5 x the prior bar's ATR within the last 60 bars; `filled` when a later
bar traded back to the prior close.

**Support/resistance v2** (`structure.sr_levels(..., df)`): strength = touches (>= 2 swing points within 0.5 ATR);
`origin` = majority swing kind; `role_reversal` = origin differs from the current side of price; `crosses` = closes
crossing the level since it formed; `bars_since_touch` = freshness.

**Backtest higher-timeframe context** (A-007): 1D backtests get COMPLETED weeks; intraday backtests get COMPLETED
daily sessions (the current session is dropped until its last bar closes). Finer intermediate HTFs (e.g. 1H for a
15m strategy) are not rebuilt in backtests.

## Signal record immutability (A-005)
The stored signal (fields, payload, scores, versions, timestamps, data snapshot) is written once and never changed
(database trigger). Later qualification, activation, completion, invalidation or expiry are appended to
`signal_events`; `state` / `state_reason` on the signal row are only the current-state projection of those events.
A signal that later qualifies keeps its original record (e.g. original status `candidate`) plus a
`awaiting_confirmation -> qualified` event; no second record is created. Notifications, reports and the API label the
original record and the lifecycle events separately.

