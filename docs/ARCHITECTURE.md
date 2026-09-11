# Architecture — Phase 1

This repository is being built in phases toward the full platform described in the
project brief (broker-agnostic algo trading for NIFTY 50 cash + F&O, price action,
support/resistance, option chain, risk-managed paper/live execution, backtesting,
strategy builder, dashboard). This first slice implements the **Strategy & Execution
core** end-to-end, vertically, so every layer (indicators → strategy → risk → paper
execution → backtest → API) is real and tested rather than stubbed.

## What exists today

```
backend/
  app/
    core/            # enums, pydantic domain models (Signal, Trade, RiskConfig, ...)
    indicators/       # EMA, SMA, RSI, ATR, ADX/+DI/-DI, Supertrend (pandas/numpy, no TA-Lib dep)
    strategy_engine/   # BaseStrategy, inbuilt multi-timeframe + indicator-based strategies, registry
    risk_engine/       # position sizing, daily loss / trade-count / consecutive-loss gates
    brokers/            # BrokerInterface, domain models, Zerodha/Upstox adapters, stubs, registry
    price_action/       # swing detection, market structure (HH/HL/LH/LL, BOS/CHoCH), candlestick patterns
    support_resistance/ # zone engine: swing clusters, prev day/week, opening range, VWAP, pivots, Fibonacci
    option_chain/       # PCR, Max Pain, ATM/ITM/OTM, OI buildup/unwinding, bias classification
    execution/         # PaperBroker (simulated fills + costs), OrderRouter (paper/live gate)
    backtest/          # event-driven backtest engine with HTF resampling
    main.py            # FastAPI app exposing strategies/signals/paper-execute/backtest/brokers/price-action
  tests/               # pytest coverage for every layer above
docs/
  ARCHITECTURE.md      # this file
  STRATEGIES.md        # the inbuilt auto-executable scalping strategies
```

## Data flow (current slice)

```
OHLCV bars (per timeframe)
        │
        ▼
 Indicator layer (EMA/RSI/ATR/ADX/Supertrend)
        │
        ▼
 Strategy.analyze() -> Signal (direction, entry, SL, target1/2, RR, score, reasons)
        │
        ▼
 RiskManager.validate_and_size() -> RiskDecision (approve/reject + position size)
        │
        ▼
 OrderRouter (PAPER: PaperBroker simulated fill / LIVE: BrokerInterface.place_order()
              via a Zerodha/Upstox/... adapter, or blocked if none is wired in — never
              a silent no-op)
        │
        ▼
 Trade (paper) or a real BrokerOrderResponse (live), or BacktestResult (backtest engine)
```

A strategy can **never** bypass the risk engine: `OrderRouter.execute()` always calls
`RiskManager.validate_and_size()` first, and `ExecutionMode.LIVE` raises
`LiveTradingNotConfigured` unless a concrete, authenticated `BrokerInterface` instance is
passed in — per the platform's non-negotiable safety rules (no live order without risk
validation, no live trading while disabled).

## Broker Abstraction Layer

`app/brokers/base.py` defines `BrokerInterface`, an async ABC with the 14 methods the brief
specifies (`authenticate`, `get_profile`, `get_instruments`, `get_ltp`, `get_quote`,
`get_historical_data`, `get_option_chain`, `place_order`, `modify_order`, `cancel_order`,
`get_order_book`, `get_trade_book`, `get_positions`, `get_holdings`, `get_margins`). Nothing
upstream (strategy engine, risk engine, order router) ever imports a broker-specific class —
only this interface — so adding a broker means writing one new adapter file.

- **`app/brokers/zerodha.py`** and **`app/brokers/upstox.py`** are full reference
  implementations against Kite Connect v3 and Upstox v2 respectively, using an
  injected `httpx.AsyncClient` (so tests mock transport instead of hitting real endpoints —
  see `tests/test_brokers.py`). Each documents which `BrokerCredentials` fields it needs.
- **`app/brokers/stubs.py`** provides `AngelOneBroker`, `FyersBroker`, `DhanBroker` — they
  satisfy `BrokerInterface` today (registrable, instantiable, type-safe) but every I/O method
  raises `NotImplementedError` pointing at that broker's docs, rather than shipping
  under-verified endpoint guesses as if they were tested.
- **`app/brokers/registry.py`** exposes `get_broker_adapter(name, credentials)` and
  `available_brokers()` — the latter is surfaced read-only at `GET /api/broker/available`.
- **No credential-accepting endpoint exists yet.** Broker credentials are never accepted over
  HTTP without an encrypted secrets store behind it (per the brief's "no secrets in
  plaintext" rule) — that lands with the auth/database phase. Until then, adapters are
  constructed directly in Python with a `BrokerCredentials` object.
- **`OrderRouter`** now takes an optional `broker: BrokerInterface` — `ExecutionMode.LIVE`
  builds a `BrokerOrderRequest` from the approved, risk-sized signal and calls
  `broker.place_order()`; a `REJECTED`/`CANCELLED` broker response surfaces as
  `ExecutionResult(executed=False, ...)` rather than being swallowed. `OrderRouter.execute()`
  is now `async` throughout (paper and live) since live calls are real network I/O.

## Price Action + Support/Resistance Engines

Two standalone analysis engines, not yet wired into strategy signal scoring (kept separate to
avoid destabilizing the already-tested strategies — see "What's next"), but usable today via
API for charting overlays and manual/AI-assistant "why this level" queries.

- **`app/price_action/swings.py`** — fractal swing-high/low detection (`find_swings`, a
  configurable-window local-extreme scan) plus `alternate_swings`, which collapses consecutive
  same-kind swings (including plateaus/ties) down to one point so the sequence strictly
  alternates HIGH/LOW the way real market structure requires.
- **`app/price_action/market_structure.py`** — labels each swing HH/HL/LH/LL against the prior
  swing of the same kind, classifies the overall trend (`UPTREND`/`DOWNTREND`/`RANGE`) from the
  last two labels, and walks the bars chronologically to emit `BOS` (break of structure, price
  breaks a level in the direction of the prevailing trend) or `CHoCH` (change of character, it
  breaks against it) events — each level fires once per break, not once per bar.
- **`app/price_action/candlestick_patterns.py`** — eleven detectors (Doji, Hammer, Shooting
  Star, Bullish/Bearish Engulfing, Morning/Evening Star, Pin Bar, Inside/Outside Bar, Strong
  Rejection Candle), each returning a 0-100 confidence rather than a bare yes/no, per the
  brief's "confidence scores, not every pattern is a signal" requirement.
- **`app/support_resistance/`** — `SupportResistanceEngine.build_zones()` combines seven zone
  sources into `SRZone` objects (a price *range*, never a single exact price): clustered swing
  points (touches/volume-confirmation/rejection-count driven strength score), previous
  day/week high-low, the opening range, session VWAP, standard pivot points (PP/R1-3/S1-3),
  and Fibonacci retracement of the most recent swing leg. Each zone keeps a `source` tag rather
  than merging across sources — spotting true confluence means comparing overlapping zones,
  which is a natural next step once this feeds the Signal Engine.

## Option Chain Intelligence Engine

`app/option_chain/analysis.py` turns a raw `OptionChain` (the same model `BrokerInterface.
get_option_chain()` returns) into the derived analytics the brief asks for:

- **PCR** (total put OI / total call OI), **Max Pain** (the strike minimizing option writers'
  aggregate payout across all strikes, `compute_max_pain`), and **ATM strike** (closest strike
  to the underlying LTP), with per-strike **ITM/ATM/OTM** classification for both legs.
- **OI activity** per strike per side, from the sign of `change_oi` alone (no previous-price
  data needed): rising call OI is tagged `CALL_WRITING` (bearish - resistance building),
  falling is `CALL_UNWINDING`; rising put OI is `PUT_WRITING` (bullish - support building),
  falling is `PUT_UNWINDING`.
- **Call resistance / put support strikes** — the top-N strikes by call OI and put OI
  respectively, the option-chain equivalent of the support/resistance engine's zones.
- **Bias** (`BULLISH`/`BEARISH`/`NEUTRAL`/`CONFLICTING`) that deliberately never comes from PCR
  alone, per the brief's explicit warning against that: it only reports `BULLISH`/`BEARISH`
  when the PCR reading *and* the aggregate OI-change reading agree, `CONFLICTING` when they
  point opposite ways, and `NEUTRAL` whenever either signal is inconclusive or the broker
  didn't supply OI-change data at all.

Exposed at `POST /api/option-chain/analyze`. Like the price-action/S&R engines, this is
standalone today — the strategy engine's option-chain confirmation layer (brief section 11)
is a follow-up once these three analysis engines feed into Signal Engine scoring together.

## API surface (current slice)

- `GET  /api/strategies` — list every inbuilt strategy (id, name, category, timeframes, params)
- `GET  /api/strategies/{id}` — one strategy's metadata
- `POST /api/strategies/{id}/signal` — run a strategy against supplied OHLCV candles, get a `Signal`
- `POST /api/strategies/{id}/paper-execute` — generate a signal and auto-route it through the
  risk engine + paper broker if it's tradeable
- `POST /api/backtest` — run a strategy over historical OHLCV bars, get a `BacktestResult`
  (trades, win rate, profit factor, drawdown, equity curve, ...)
- `GET  /api/broker/available` — broker ids the abstraction layer can adapt to
- `POST /api/price-action/structure` — swings, HH/HL/LH/LL labels, trend, BOS/CHoCH events
- `POST /api/price-action/patterns` — every candlestick pattern match with its confidence score
- `POST /api/support-resistance/zones` — the combined support/resistance zone list
- `POST /api/option-chain/analyze` — PCR, Max Pain, ATM/ITM/OTM, OI activity, bias
- `GET  /api/system/health` — liveness

## Design decisions worth flagging

- **No TA-Lib dependency.** Indicators are implemented directly on pandas/numpy with Wilder
  smoothing where the standard defines it (RSI, ATR, ADX). This keeps the service installable
  anywhere without a compiled C dependency, and every indicator has unit tests validating its
  math rather than trusting an opaque library.
- **Strategies are parametrized, not copy-pasted.** The four multi-timeframe combos
  (1m/5m, 1m/15m, 5m/30m, 5m/60m) are one `MTFTrendPullbackScalper` class instantiated four
  times with different timeframe pairs and registered under distinct ids — see
  `app/strategy_engine/mtf_strategy.py`.
- **Signals are never forced.** Every `analyze()` returns a `Signal`; when no setup qualifies
  it comes back as `direction=NO_TRADE` with `reasons` explaining why (matches the platform's
  "the engine must be able to explicitly return NO TRADE" requirement).
- **Backtest resampling is a documented simplification.** Higher-timeframe bars are built by
  resampling the base timeframe and used as soon as their window closes at-or-before the
  current bar — safe for completed HTF bars, but doesn't model intrabar ticks. Noted in
  `run_backtest`'s docstring so it isn't mistaken for tick-accurate simulation.

## What's next (not yet built)

Per the original 40-section brief, still outstanding: wiring price-action, support-resistance
*and* option-chain confirmation into the Signal Engine's scoring (all three engines exist and
are API-reachable, but the seven inbuilt strategies don't consult them yet), the visual
no-code strategy builder, TradingView-style charting UI, the React/Next.js dashboard and
remaining tabs, PostgreSQL/Redis persistence, auth + encrypted secret storage, and Docker/CI
deployment. Angel One/Fyers/Dhan adapters are structurally registered but still need their real
endpoints wired in (see `app/brokers/stubs.py`). This slice is the foundation those layers plug
into: strategies are already timeframe- and instrument-agnostic (`symbol` is just a string), so
once an authenticated broker adapter is constructed and instrument-master lookups are wired to
a persistence layer, the same `Signal`/`Trade`/`BrokerOrderRequest` models carry straight
through to real equity/futures/options trading.
