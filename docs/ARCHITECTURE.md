# Architecture — Phase 1

This repository is being built in phases toward the full platform described in the
project brief (broker-agnostic algo trading for NIFTY 50 cash + F&O, price action,
support/resistance, option chain, risk-managed paper/live execution, backtesting,
strategy builder, dashboard). This first slice implements the **Strategy & Execution
core** end-to-end, vertically, so every layer (indicators → strategy → risk → paper
execution → backtest → API) is real and tested rather than stubbed.

## What exists today

```
docker-compose.yml    # postgres + backend + frontend, one command locally
backend/
  app/
    core/            # enums, pydantic domain models (Signal, Trade, RiskConfig, ...)
    indicators/       # EMA, SMA, RSI, ATR, ADX/+DI/-DI, Supertrend (pandas/numpy, no TA-Lib dep)
    strategy_engine/   # BaseStrategy, inbuilt multi-timeframe + indicator-based strategies, registry
    risk_engine/       # position sizing, daily loss / trade-count / consecutive-loss gates
    brokers/            # BrokerInterface, domain models, Zerodha/Upstox/Shoonya adapters, stubs, registry, routes
    auth/                # register/login (JWT), password hashing, get_current_user dependency
    db/                  # SQLAlchemy async engine/session, User/BrokerCredential/TradeRecord/AuditLog models
    secrets_store/        # Fernet encryption for broker credentials at rest
    trading/              # persists paper-execute fills per user; GET /api/trades, /api/positions
    price_action/       # swing detection, market structure (HH/HL/LH/LL, BOS/CHoCH), candlestick patterns
    support_resistance/ # zone engine: swing clusters, prev day/week, opening range, VWAP, pivots, Fibonacci
    option_chain/       # PCR, Max Pain, ATM/ITM/OTM, OI buildup/unwinding, bias classification
    signal_scoring/     # weighted composite score combining every analysis engine (the "why this trade" layer)
    execution/         # PaperBroker (simulated fills + costs), OrderRouter (paper/live gate)
    backtest/          # event-driven backtest engine with HTF resampling
    main.py            # FastAPI app exposing strategies/signals/paper-execute/backtest/brokers/price-action
  tests/               # pytest coverage for every layer above
frontend/
  src/
    api/client.ts       # typed fetch client; attaches the JWT from localStorage when present
    auth/AuthContext.tsx # login/register/logout state, shared via React context
    utils/sampleData.ts # deterministic sample OHLCV/option-chain generator (no live broker yet)
    components/          # SignalCard, EquityCurveChart, Sidebar, shared UI primitives
    pages/                # Dashboard, Strategies, Signals, Backtest, Option Chain, Positions, Account
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

- **`app/brokers/zerodha.py`**, **`app/brokers/upstox.py`**, and **`app/brokers/shoonya.py`**
  are full reference implementations against Kite Connect v3, Upstox v2, and Shoonya's
  NorenApi (a jData/jKey form-encoded convention several Indian discount brokers share)
  respectively, using an injected `httpx.AsyncClient` (so tests mock transport instead of
  hitting real endpoints — see `tests/test_brokers.py`). Each documents which
  `BrokerCredentials` fields it needs.
- **`app/brokers/stubs.py`** provides `AngelOneBroker`, `FyersBroker`, `DhanBroker` — they
  satisfy `BrokerInterface` today (registrable, instantiable, type-safe) but every I/O method
  raises `NotImplementedError` pointing at that broker's docs, rather than shipping
  under-verified endpoint guesses as if they were tested.
- **`app/brokers/registry.py`** exposes `get_broker_adapter(name, credentials)` and
  `available_brokers()` — the latter is surfaced read-only at `GET /api/broker/available`.
- **Credentials are now accepted over HTTP, but only encrypted at rest and behind auth.**
  `POST /api/broker/{name}/credentials` (see Database + Auth below) is the endpoint that used
  to not exist — it requires a logged-in user and stores ciphertext, never plaintext.
- **`OrderRouter`** now takes an optional `broker: BrokerInterface` — `ExecutionMode.LIVE`
  builds a `BrokerOrderRequest` from the approved, risk-sized signal and calls
  `broker.place_order()`; a `REJECTED`/`CANCELLED` broker response surfaces as
  `ExecutionResult(executed=False, ...)` rather than being swallowed. `OrderRouter.execute()`
  is now `async` throughout (paper and live) since live calls are real network I/O.

## Price Action + Support/Resistance Engines

Two analysis engines, callable standalone via API for charting overlays and manual/AI-assistant
"why this level" queries, and also consumed by the Signal Scoring Engine below.

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

Exposed at `POST /api/option-chain/analyze`, and consumed (optionally) by the Signal Scoring
Engine below.

## Signal Scoring Engine

`app/signal_scoring/engine.py` implements the weighted composite formula from brief section 8
(Trend 20% / Market Structure 15% / Support-Resistance 20% / Price Action 20% / Volume 10% /
Option Chain 10% / Risk-Reward 5%, `WEIGHTS` in that file) as a **non-invasive enrichment
layer**: `enrich_signal(signal, ltf_df, option_chain=None)` takes a `Signal` any of the seven
inbuilt strategies already produced, re-derives market structure, support/resistance zones,
candlestick patterns, and volume fresh from that strategy's own primary-timeframe data (plus
option-chain bias if a chain is supplied), and returns an `EnrichedSignal` with:

- `composite_score` (0-100) and `grade` (A1/High Quality/Valid/Weak/No Trade, same thresholds
  as the base engine) computed from the seven weighted components
- `breakdown`: each component's raw 0-100 reading, its weight, its weighted contribution, and a
  plain-English note (e.g. "Recent BOS confirms bullish structure at 21834.50")
- `confirmations`: those same notes as a flat list - directly answers the dashboard's
  "WHY THIS TRADE?" requirement (brief sections 19 and 25) without the strategy itself needing
  to know about market structure, S/R, or the option chain

No existing strategy class was touched to build this - it sits entirely on top, so all 87
existing + new tests keep passing unmodified. Exposed at
`POST /api/strategies/{id}/signal/enrich` (generates the signal via the normal `/signal` path
and enriches it in one call).

## Frontend Console

`frontend/` (Vite + React + TypeScript + Tailwind, see `frontend/README.md` for the
Next.js-vs-Vite tradeoff) is a control-panel SPA over the API above: Dashboard, Strategy
Library, Signals (the full `EnrichedSignal` "why this trade" card), Backtesting (metrics,
SVG equity curve, trade log), Option Chain, Positions, and Account. `app/main.py` enables
permissive CORS (`CORSMiddleware`, tightened once real deployment domains exist) and the Vite
dev server also proxies `/api` to `localhost:8000`, so either path works.

No broker is authenticated yet, so every page builds candles/option-chain rows from a
deterministic client-side generator (`frontend/src/utils/sampleData.ts`) rather than showing
fabricated "live" data — clearly labeled in the UI. Everything computed *on* that sample data
(scores, backtest metrics, option-chain bias) is the real backend engine, not a mock.

The Signals page now includes a real candlestick chart (`src/components/CandleChart.tsx`,
TradingView's `lightweight-charts` — the library the brief names directly), not just the
signal card: entry/stop-loss/target1/target2 as colored price lines, an up/down marker on the
signal bar, and the strongest few nearby support/resistance zones (`GET
/api/support-resistance/zones`, filtered client-side to zones within 4% of the last close and
capped to the top 5 by `strength_score` — the raw zone list can be dozens of small swing
clusters, which is correct data but unreadable rendered directly onto a chart). Backtesting
still plots its equity curve as inline SVG (a per-trade series, not a price series, so
lightweight-charts' time-based x-axis isn't the right fit there).

`src/auth/AuthContext.tsx` holds login state (backed by the JWT endpoints below, token kept in
`localStorage`); `src/api/client.ts`'s `request()` attaches it as a bearer token automatically
whenever present. The Account page handles register/login/logout; the Sidebar's footer shows
who's signed in. Everything else keeps working anonymously (the "try without an account" flow
from earlier phases is unchanged) — being logged in only adds persistence, per Database + Auth
below.

## Database + Auth

PostgreSQL (async, via SQLAlchemy 2.0 + `asyncpg`) is now real, not deferred: `app/db/models.py`
defines `User`, `BrokerCredentialRecord`, `TradeRecord`, and `AuditLogRecord`; `app/db/session.py`
creates the engine from `DATABASE_URL` and exposes `get_session` as a FastAPI dependency; tables
are created on startup via a `lifespan` handler (`init_models()` — no formal migration tool like
Alembic yet, so schema changes currently mean adjusting the models and re-running against a
fresh or manually-migrated database; that's the honest gap, not a claim of production-grade
migrations).

- **`app/auth/`** — `security.py` hashes passwords with `bcrypt` and issues/verifies JWTs
  (`PyJWT`, `JWT_SECRET_KEY`/`JWT_ALGORITHM`/`JWT_EXPIRE_MINUTES` from `app/core/config.py`);
  `dependencies.py`'s `get_current_user` is the FastAPI dependency every protected route uses;
  `routes.py` exposes `POST /api/auth/register`, `POST /api/auth/login`, `GET /api/auth/me`.
- **`app/secrets_store/encryption.py`** — Fernet symmetric encryption keyed by
  `SECRETS_ENCRYPTION_KEY` (any passphrase is hashed down to a valid key; a fixed dev-only
  fallback keeps local runs working but is explicitly not meant to protect anything real).
- **`app/brokers/routes.py`** — `POST /api/broker/{name}/credentials` encrypts and upserts a
  user's `BrokerCredentials` for one broker (never plaintext, never logged);
  `GET /api/broker/credentials` lists which brokers a user has stored (names/timestamps only);
  `DELETE /api/broker/{name}/credentials` removes one; `POST /api/broker/{name}/authenticate`
  decrypts the stored credentials in memory, constructs the adapter via the existing broker
  registry, and calls its real `authenticate()` — success or failure (broker-side or a raw
  network error) both return a clean HTTP response and write an `AuditLogRecord` row, per the
  platform's audit-trail requirement. This is the endpoint the broker layer's original "no
  credential-accepting endpoint exists yet" note was waiting on.
- **`app/trading/`** — the first thing that actually uses `TradeRecord`. `POST
  /api/strategies/{id}/paper-execute` now takes an *optional* bearer token
  (`get_current_user_optional`, which returns `None` instead of raising when no/an invalid token
  is supplied): anonymous calls behave exactly as before (no persistence, matching the "try it
  without an account" demo flow already used by Signals/Backtest), but a logged-in user's fill
  is written to `TradeRecord` via `persist_paper_trade()`. `app/trading/routes.py` exposes
  `GET /api/trades` (full history) and `GET /api/positions` (rows with no `exit_time`) for the
  current user. Nothing yet closes a paper-execute position automatically (no live price feed
  monitors it — that only happens inside the historical backtest engine's simulation), so every
  persisted trade shows up as "open" until a future exit-tracking pass writes back to the same
  row; that's called out in `TradeRecord`'s docstring rather than left implicit.
- Verified end-to-end against a real local Postgres instance, twice: once for the credential
  flow (register → login → store encrypted Zerodha credentials → confirm the stored value is
  ciphertext in the raw DB row → authenticate, a genuine network call to Zerodha's live API that
  correctly came back 403 with fake credentials and surfaced as a clean 502 → delete, every step
  logged to `audit_logs`), and again for trade persistence (register → force a real strategy
  signal to fire → authenticated paper-execute → `GET /api/trades`/`/api/positions` show the
  exact filled trade, matched in the frontend console by executing from the Signals page and
  seeing it appear on the Positions page for that same session). The automated test suite
  instead runs against an in-memory SQLite database via a dependency override, so `pytest` needs
  no external database.

## API surface (current slice)

- `GET  /api/strategies` — list every inbuilt strategy (id, name, category, timeframes, params)
- `GET  /api/strategies/{id}` — one strategy's metadata
- `POST /api/strategies/{id}/signal` — run a strategy against supplied OHLCV candles, get a `Signal`
- `POST /api/strategies/{id}/signal/enrich` — the same, plus the weighted composite score and
  "why this trade" breakdown (structure, S/R, price action, volume, option chain, RR)
- `POST /api/strategies/{id}/paper-execute` — generate a signal and auto-route it through the
  risk engine + paper broker if it's tradeable
- `POST /api/backtest` — run a strategy over historical OHLCV bars, get a `BacktestResult`
  (trades, win rate, profit factor, drawdown, equity curve, ...)
- `GET  /api/broker/available` — broker ids the abstraction layer can adapt to
- `POST /api/broker/{name}/credentials` — encrypt and store this user's credentials (auth required)
- `GET  /api/broker/credentials` — list which brokers this user has stored (auth required)
- `DELETE /api/broker/{name}/credentials` — remove stored credentials (auth required)
- `POST /api/broker/{name}/authenticate` — decrypt stored credentials and really log in (auth required)
- `POST /api/price-action/structure` — swings, HH/HL/LH/LL labels, trend, BOS/CHoCH events
- `POST /api/price-action/patterns` — every candlestick pattern match with its confidence score
- `POST /api/support-resistance/zones` — the combined support/resistance zone list
- `POST /api/option-chain/analyze` — PCR, Max Pain, ATM/ITM/OTM, OI activity, bias
- `POST /api/auth/register` / `POST /api/auth/login` — returns a JWT
- `GET  /api/auth/me` — current user (auth required)
- `GET  /api/trades` — this user's full paper trade history (auth required)
- `GET  /api/positions` — this user's open (no `exit_time`) trades (auth required)
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

## Docker Deployment

`docker-compose.yml` at the repo root wires three services: `postgres` (16-alpine, a named
volume, a `pg_isready` healthcheck), `backend` (built from `backend/Dockerfile` — Python 3.11
slim, non-root user, a container healthcheck against `/api/system/health`, waits for Postgres
to be healthy before starting so `init_models()` always has a real database to create tables
against), and `frontend` (built from `frontend/Dockerfile` — a Node build stage producing the
Vite production bundle, served by an `nginx:alpine` stage whose `nginx.conf` reverse-proxies
`/api/*` to the `backend` service by its compose network name and falls back to `index.html`
for client-side routes). Copy `.env.example` to `.env` at the repo root first (Postgres
credentials, `JWT_SECRET_KEY`, `SECRETS_ENCRYPTION_KEY`), then:

```bash
docker compose up --build
# backend:  http://localhost:8000
# frontend: http://localhost:8080
```

**Honesty note on verification:** `docker compose config` was run and validates the file
cleanly (service graph, env interpolation, healthcheck syntax all resolve correctly), but this
sandbox's network egress policy explicitly blocks Docker Hub's CDN
(`production.cloudfront.docker.com` — confirmed via a 403 policy denial, not a transient
error), so pulling the `python`/`node`/`postgres`/`nginx` base images and actually running
`docker compose up` could not be exercised here. Please run it on a machine with normal Docker
Hub access before trusting it in production — if anything doesn't build cleanly, that's a real
bug to fix, not a sandbox artifact.

## What's next (not yet built)

Per the original 40-section brief, still outstanding: the visual no-code strategy builder, a
price chart on the Backtesting page itself (candles + trade markers, not just the equity
curve - Signals now has the real candlestick chart, see Frontend Console above), the remaining
dashboard tabs (Orders, Portfolio, Risk Management, Analytics, Settings, System Logs -
Positions/Trade Journal now exist, see Frontend Console and Database + Auth above), Redis (for real-time pub/sub
and caching - Postgres persistence and JWT auth now exist), formal DB migrations (Alembic -
schema changes today mean editing the SQLAlchemy models and re-running against a
fresh/manually-migrated database), and CI (Docker Compose deployment now exists - see Docker
Deployment above - but it's unverified in this sandbox and there's no CI pipeline running the
test suite/build on every push yet). Angel One/Fyers/Dhan adapters are structurally registered
but still need their real endpoints wired in (see `app/brokers/stubs.py`). Persisting signals
and strategy configs to the database (trades are now persisted per user - see `app/trading/` in
Database + Auth above - but signal history and saved/custom strategy configurations aren't yet,
and nothing monitors live prices to auto-close an open paper position) is the natural next step.
This slice is the foundation those layers plug into: strategies are already timeframe- and
instrument-agnostic (`symbol` is just a string), so once an authenticated broker adapter is
constructed (now genuinely possible via `POST /api/broker/{name}/authenticate`) and
instrument-master lookups are wired up, the same `Signal`/`Trade`/`BrokerOrderRequest` models
carry straight through to real
equity/futures/options trading. Signal scoring, price action, support/resistance, and
option-chain analysis are already wired together (see Signal Scoring Engine above) and
reachable from the frontend console (see Frontend Console above).
