# Advance Trading Platform

An algorithmic trading platform for Indian markets (NSE cash, F&O, indices), built in phases.
So far this delivers the **strategy, risk, execution, broker-abstraction, price-action, and
option-chain core**: indicators, inbuilt auto-executable multi-timeframe and indicator-based
intraday scalping strategies, a risk engine, a paper execution router, a broker-agnostic
`BrokerInterface` with real Zerodha and Upstox adapters (Angel One/Fyers/Dhan registered as
pluggable stubs), a market-structure + candlestick-pattern engine, a support/resistance zone
engine (swing clusters, prev day/week, opening range, VWAP, pivots, Fibonacci), an option-chain
intelligence engine (PCR, Max Pain, ATM/ITM/OTM, OI buildup/unwinding, bias), a backtest
engine, and a FastAPI service exposing all of it.

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the system design and what's still to
be built, and [`docs/STRATEGIES.md`](docs/STRATEGIES.md) for the seven inbuilt scalping
strategies and how to call them.

## Quick start

```bash
cd backend
pip install -r requirements.txt
uvicorn app.main:app --reload
# API docs at http://localhost:8000/docs
```

## Run the tests

```bash
cd backend
pytest -q
```

## Inbuilt strategies at a glance

- **Multi-timeframe pullback scalpers**: 1m/5m, 1m/15m, 5m/30m, 5m/60m — higher-timeframe
  trend filter + lower-timeframe pullback/reversal entry (EMA structure, RSI reversal trigger,
  ADX strength filter).
- **EMA + RSI Scalper** (1m default) — EMA crossover timing confirmed by RSI momentum.
- **Supertrend + ADX Scalper** (1m default) — Supertrend flip confirmed by ADX trend strength.
- **RSI + ADX Momentum Scalper** (5m default) — RSI midline cross confirmed by DI dominance and
  ADX strength.

Every signal carries entry, stop loss, two targets, risk/reward, a 0–100 score/grade, and the
plain-English reasons behind it — and every order, paper or live, passes through the Risk
Engine before execution. `ExecutionMode.LIVE` only places a real order when an authenticated
`BrokerInterface` instance (e.g. `ZerodhaBroker`, `UpstoxBroker`) is explicitly passed to
`OrderRouter`; with none wired in it stays safely blocked (`LiveTradingNotConfigured`) rather
than silently doing nothing. No broker credentials are accepted over the API yet — that lands
with the encrypted secrets-storage phase.
