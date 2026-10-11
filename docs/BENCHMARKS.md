# Backtest benchmarks (spec part C3) and reproducibility (C4)

## Speed

`python backend/scripts/bench_backtest.py [--strategies ID ...] [--sessions 5 20] [--repeat N] [--json]`

The data is crafted (a seeded random walk, 375 one-minute bars per session), so absolute numbers only compare runs
on the same machine. `growth` = (time of the longest run / time of the shortest) / (bar ratio): about 1 for an engine
that is linear in the bars.

Measured 2026-10-10 on the CI-class sandbox (2 and 8 sessions = 750 and 3,000 one-minute bars; engine 4):

| Strategy | 750 bars | 3,000 bars | bars/s at 3,000 |
|---|---:|---:|---:|
| mtf_1m_5m_trend_pullback | 0.44 s | 1.82 s | 1,646 |
| mtf_1m_15m_trend_pullback | 0.28 s | 1.73 s | 1,733 |
| mtf_5m_30m_trend_pullback | 0.04 s | 0.30 s | 10,104 |
| mtf_5m_60m_trend_pullback | 0.03 s | 0.24 s | 12,766 |
| ema_rsi_scalper_1m | 0.23 s | 1.10 s | 2,739 |
| supertrend_adx_scalper_1m | 0.23 s | 0.64 s | 4,678 |
| rsi_adx_momentum_5m | 0.05 s | 0.24 s | 12,417 |
| macd_ema_trend_5m | 0.01 s | 0.15 s | 20,661 |
| bb_rsi_reversion_5m | 0.07 s | 0.33 s | 8,987 |
| vwap_supertrend_5m | 0.21 s | 1.25 s | 2,405 |
| orb_15m_5m | 0.05 s | 0.16 s | 18,821 |
| swing_ema_pullback_d (daily) | 104 bars 0.02 s | 416 bars 0.21 s | 1,956 |
| swing_breakout_d (daily) | 104 bars 0.03 s | 416 bars 0.17 s | 2,470 |

Short spans are dominated by the warm-up (no signal before `min_history`), which is why `growth` is noisy there.
Two years of one-minute bars (187,500): `mtf_1m_5m_trend_pullback` 123 s, `ema_rsi_scalper_1m` 72 s (realism 3, PR #83).

What the indicator cache buys, `ema_rsi_scalper_1m` (3 and 12 sessions):

| | 1,125 bars | 4,500 bars |
|---|---:|---:|
| with the cache (every run) | 0.40 s | 1.50 s |
| without it (`run_backtest.__wrapped__`) | 3.25 s | 17.57 s |

## The CI guard

Wall-clock limits flake on shared runners, so CI checks the cause rather than the clock:
`backend/tests/test_realism_benchmark.py` runs every inbuilt strategy on 4 and 10 sessions and requires the indicator
cache's counters (`app.indicators.prefix_cache.run_stats()`: whole-frame computations, prefix answers served, calls it
could not serve) to stay constant in the number of bars. A change that makes any strategy recompute an indicator on
each bar's window fails it. Checked against a simulated regression (the run's frames not registered): 12 of 13
strategies fail; `orb_15m_5m` uses no cached indicator.

## Reproducibility (C4)

Every backtest result carries `reproducibility` (`app/backtest/repro.py`), stored with the run record
(`GET /api/backtests/{id}`):

| Field | What |
|---|---|
| `engine_version` | the engine's version (bumped whenever results can change for the same inputs) |
| `code_version` | `APP_VERSION` of the build |
| `data_version` | SHA-256 over the bars: UTC timestamps + OHLCV as float64 (not a label or file name) |
| `config_hash` | SHA-256 over canonical JSON of strategy id, effective parameters, symbol, timeframe, risk, exit rules, option settings (+ uploaded option quotes) |
| `result_hash` | SHA-256 over canonical JSON of the result (trades, equity, metrics, analytics) |
| `seed` | the RNG seed where a run draws random numbers; `null` for the deterministic engine (Monte Carlo reports its own `seed`) |

Same engine + code + data + config => same `result_hash`, byte for byte. `backend/tests/test_realism_repro.py` checks it
in one process and across fresh processes with different `PYTHONHASHSEED`, and that each hash moves only with what it
names (one tick changes `data_version`; one parameter changes `config_hash`).
