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
Engine below. See "Greeks Engine" further down for the Black-Scholes Delta/Gamma/Theta/Vega
this same analysis now attaches per strike.

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
clusters, which is correct data but unreadable rendered directly onto a chart). The
Backtesting page reuses the same `CandleChart` over the sample series the backtest ran
against, with one entry marker (direction-coded arrow) and one exit marker (P&L-coded circle)
per trade in `result.trades` (`directionMarker()` for entries, a small inline builder for
exits) — the equity curve stays a separate inline SVG below it since that series is per-trade,
not time-based, so lightweight-charts' time axis isn't the right fit for it.

`src/auth/AuthContext.tsx` holds login state (backed by the JWT endpoints below, token kept in
`localStorage`); `src/api/client.ts`'s `request()` attaches it as a bearer token automatically
whenever present. The Account page handles register/login/logout; the Sidebar's footer shows
who's signed in. Everything else keeps working anonymously (the "try without an account" flow
from earlier phases is unchanged) — being logged in only adds persistence, per Database + Auth
below.

## Database + Auth

PostgreSQL (async, via SQLAlchemy 2.0 + `asyncpg`) is now real, not deferred: `app/db/models.py`
defines `User`, `BrokerCredentialRecord`, `TradeRecord`, and `AuditLogRecord`; `app/db/session.py`
creates the engine from `DATABASE_URL` and exposes `get_session` as a FastAPI dependency.

Schema changes are now tracked with **Alembic** (`backend/alembic/`, async template): `alembic
upgrade head` applies every tracked migration in order (`alembic/versions/`, starting from
`5ee6c638e477_initial_schema.py`, which was autogenerated against an empty database and matches
`Base.metadata` exactly — verified with `alembic check`/a second autogenerate producing no diff).
`alembic/env.py` reads `DATABASE_URL` from `app.core.config` (the same variable the app itself
uses) rather than a hardcoded URL in `alembic.ini`, and imports `app.db.models` so every table
registers on `Base.metadata` before autogenerate compares against it. Going forward, a model
change means `alembic revision --autogenerate -m "..."` (review the generated script — autogenerate
doesn't catch everything, e.g. plain column renames show up as drop+add) then `alembic upgrade
head`, not hand-editing a live database. The `lifespan` handler's `init_models()` (`create_all`)
still runs on startup as a convenience for a brand-new empty dev database (it's a no-op once
tables exist), but the authoritative schema history — and the only safe way to evolve a database
that already has data — is the migration chain. This closes what was previously an honest gap:
the naive/aware-datetime column bug found earlier this project required manually dropping tables
and restarting because nothing tracked schema versions; that class of problem is what Alembic is
for.

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
- `POST /api/option-chain/analyze` — PCR, Max Pain, ATM/ITM/OTM, OI activity, bias, and
  per-strike Greeks whenever there's enough real data to compute them from
- `POST /api/option-chain/greeks` — Black-Scholes Delta/Gamma/Theta/Vega for one or more option
  legs plus the net Greeks of the combined position - see "Greeks Engine" below
- `POST /api/auth/register` / `POST /api/auth/login` — returns a JWT
- `GET  /api/auth/me` — current user (auth required)
- `GET  /api/trades` — this tenant's full paper trade history (auth required)
- `GET  /api/positions` — this tenant's open (no `exit_time`) trades (auth required)
- `GET  /api/orders` — this tenant's full order ledger, every execution attempt whether it
  filled or not (auth required) - see "Order Idempotency + Formal Order State Machine" below
- `GET  /api/orders/{id}/events` — one order's full append-only state-transition history (auth required)
- `GET  /api/kill-switch/status` — this tenant's global/tenant/strategy kill-switch state (auth required)
- `POST /api/kill-switch/global/{engage,disengage}` — platform-wide stop (SUPER_ADMIN only)
- `POST /api/kill-switch/tenant/{engage,disengage}` — stop new orders for this tenant (auth required)
- `POST /api/kill-switch/strategy/{id}/{engage,disengage}` — stop new orders for one strategy in this tenant (auth required)
- `POST /api/kill-switch/emergency-exit` — engage the tenant switch, cancel pending orders, close
  priced open positions, in one call (auth required) - see "Kill Switches + Emergency Exit" below
- `PUT  /api/custom-strategies/{id}` — edit a saved strategy by appending a new immutable version
  (auth required) - see "Strategy Version Control" below
- `GET  /api/custom-strategies/{id}/versions` — a strategy's full version history (auth required)
- `POST /api/custom-strategies/{id}/versions/{n}/rollback` — make an earlier version live again,
  itself recorded as a new version (auth required)
- `POST /api/reconciliation/{broker_name}` — compare this tenant's internally-tracked open
  positions against the broker's real ones and flag mismatches (auth required) - see "Position
  Reconciliation Engine" below
- `GET  /api/notifications` — this tenant's in-app notification feed, most recent first (auth
  required) - see "Notification Engine" below
- `POST /api/notifications/{id}/read` / `POST /api/notifications/read-all` — mark read (auth required)
- `POST /api/webhooks/tradingview/{webhook_token}` — ingest a TradingView alert as a trade
  signal, authenticated by the URL token alone - see "TradingView Webhook Ingestion" below
- `GET  /api/webhooks/tradingview/token` / `POST /api/webhooks/tradingview/token/rotate` — view
  or rotate this tenant's webhook URL (auth required)
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
to be healthy before starting, and its `CMD` now runs `alembic upgrade head` before starting
`uvicorn` so the container always boots against the current tracked schema rather than relying
on `create_all`), and `frontend` (built from `frontend/Dockerfile` — a Node build stage producing the
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

## Strategy Builder, dashboard tabs, and platform hardening

A full pass since the last section closed most of the previously-open gaps:

- **No-code Strategy Builder** (`app/strategy_engine/declarative.py` + `app/custom_strategies/`):
  a user composes AND-combined long/short entry conditions (indicator vs. a fixed value or
  another indicator, plain comparisons or crossover detection) through a form UI - no drag-and-drop
  canvas, but genuinely code-free. A saved `CustomStrategyConfig` becomes a `DeclarativeStrategy`
  resolved under a `custom:<id>` strategy id by `app/custom_strategies/resolver.py`, and every
  route that accepts a strategy id (`/signal`, `/signal/enrich`, `/paper-execute`, `/backtest`,
  and the `GET /api/strategies` listing itself) resolves it the same way it resolves a built-in
  strategy - a custom strategy is private to its owner (403/404 for anyone else) but otherwise
  indistinguishable from `ema_rsi_scalper_1m` to the rest of the app. Frontend: `Strategy
  Builder` page.
- **Signal history**: every `/signal/enrich` call for a logged-in user is now logged
  (`SignalHistoryRecord`, `GET /api/signal-history`) independent of whether it was ever executed
  - surfaced as a "Recent signal history" table on the Signals page.
- **Manual position exit-tracking**: `POST /api/positions/{id}/mark-price` checks a supplied
  current price against an open position's stop loss/target1/target2 (same SL-then-target2-then-target1
  priority as the backtest engine) and closes it with `PaperBroker`'s real cost model if hit -
  the honest replacement for a live price feed that doesn't exist yet (a "Check price" control on
  the Positions page). Once a real broker quote stream exists, a scheduled job can call the same
  endpoint instead of a person.
- **Per-user Risk Management**: `RiskSettingsRecord` + `GET`/`PUT /api/risk-settings` persist a
  user's own capital/risk-per-trade/daily-loss/trade-count/consecutive-loss/lot-size limits,
  which `/paper-execute` now applies automatically instead of always falling back to the
  hardcoded platform default. Frontend: `Risk Management` tab.
- **Portfolio, Orders, Analytics tabs**: Portfolio aggregates capital deployed and cumulative
  realized P&L from existing trade data; Orders presents every entry/exit fill as a broker-style
  blotter; Analytics (`GET /api/analytics/summary`) breaks win rate/net P&L/profit factor down by
  strategy and by symbol from a user's full persisted trade history.
- **Settings + System Logs tabs**: Settings is the previously-missing UI for the broker
  credential endpoints that already existed (`POST/GET/DELETE /api/broker/{name}/credentials`,
  `POST /api/broker/{name}/authenticate`); System Logs is a viewer for `AuditLogRecord`
  (`GET /api/audit-logs`), which had been written to since the DB/auth phase but had no read path
  until now.
- **Optional Redis caching** (`app/cache/client.py`): a fail-open async wrapper caches
  `POST /api/option-chain/analyze` and `POST /api/support-resistance/zones` (both pure,
  side-effect-free computations) for 5 seconds. Verified for real by stopping the local
  redis-server mid-test-run and confirming the cache-backed endpoints still pass - Redis is
  optional infrastructure here, never a hard dependency. `docker-compose.yml` gets a
  `redis:7-alpine` service.
- **CI** (`.github/workflows/ci.yml`): a `backend` job runs the full pytest suite, then applies
  Alembic migrations against a real Postgres service container and runs `alembic check` to catch
  model/migration drift; a `frontend` job runs `npm run build` (TypeScript type-check + Vite
  production bundle). Both on every push/PR.

Genuinely still outstanding: Angel One/Fyers/Dhan adapters are structurally registered but still
need their real endpoints wired in (see `app/brokers/stubs.py`) - deliberately deprioritized once
Zerodha/Upstox/Shoonya existed. Full `docker compose up --build` execution remains unverified in
this sandbox (its network policy blocks Docker Hub's CDN - see Docker Deployment above); `docker
compose config` validates cleanly and the backend/CI both exercise the same Dockerfile logic
(migrate-then-serve), but an actual build-and-run pass on a machine with normal Docker Hub access
is still worth doing before trusting it in production.

Strategies are already timeframe- and instrument-agnostic (`symbol` is just a string), so once an
authenticated broker adapter is constructed (genuinely possible via `POST
/api/broker/{name}/authenticate`) and instrument-master lookups are wired up, the same
`Signal`/`Trade`/`BrokerOrderRequest` models carry straight through to real equity/futures/options
trading. Signal scoring, price action, support/resistance, and option-chain analysis are already
wired together (see Signal Scoring Engine above) and reachable from the frontend console (see
Frontend Console above).

## Fundamental Analysis & Company Intelligence Engine

A companion to the technical Signal Scoring Engine, covering the "should I even be looking at
this company" question technical signals don't answer: business quality, earnings quality,
valuation, balance sheet/cash flow health, red flags, SWOT, and a composite Fundamental Score
that fuses with the technical score into a final trading bias. Full detail (design principle,
engine-by-engine breakdown, what's deliberately out of scope) lives in
[`docs/FUNDAMENTALS.md`](FUNDAMENTALS.md) - the short version:

- `app/fundamentals/models.py` + DB (`companies`, `financial_periods`,
  `shareholding_snapshots`, `corporate_actions`, `qualitative_factors`): every input carries a
  `SourceCitation` (source, URL, dates, confidence); ratios are always derived live from raw
  supplied figures, never stored redundantly; qualitative judgments (moat factors, management
  quality, SWOT bullets) only ever come from a cited human-entered `QualitativeFactor`, never
  invented by an engine.
- `app/fundamentals/engines/`: Revenue growth/CAGR, Profitability (margins/ROE/ROCE/ROA +
  trend), Earnings Quality (CFO vs PAT), Quarterly QoQ/YoY comparison, Balance Sheet (debt/
  liquidity risk), Cash Flow, relative Valuation + a real DCF calculator (bull/base/bear
  sensitivity), Business Quality scorecard, Red Flag aggregator, SWOT generator, Bull/Base/Bear
  scenario projector, and the Fundamental Score (exact weights from the spec) + a Fusion engine
  combining it with the existing Signal Scoring Engine into A1 LONG/SHORT BIAS, WATCHLIST,
  CAUTION, or NO TRADE.
- `app/fundamentals/providers/nse.py`: a real `NSEProvider` for NSE India's public JSON API,
  following the same `BrokerInterface` adapter pattern as the broker adapters - parsing verified
  with mocked HTTP responses, but its live network behavior is unverified in this sandbox (same
  network-policy block that stopped Docker Hub verification - see Docker Deployment above).
- `app/fundamentals/routes.py`: company/financial/shareholding/corporate-action/qualitative-
  factor CRUD (open reads - shared reference data; auth-required writes so contributions are
  attributed) plus per-engine analysis endpoints, a screener, and a lightweight sector-rotation
  ranking - both limited to whatever companies have actually been entered, not the full NSE/BSE
  universe (no live market-wide feed exists).
- Frontend: a `Fundamental Analysis` page (company profile/financials entry, tabbed analysis,
  valuation & DCF calculator, SWOT/red-flags/qualitative-factor entry, Fundamental Score +
  Fusion, one-page Intelligence Card, screener + sector rotation) - verified end-to-end with
  Playwright against the live backend.

Deliberately out of scope for this pass (per explicit user direction to build the core engines
for real rather than a larger surface that silently does nothing without a live feed): national/
international event impact engines, earnings-call-transcript NLP, and a true market-wide/NIFTY-
level fundamental engine - all three need a live macro/news/analyst-consensus feed this platform
doesn't have credentials for. Angel One/Fyers/Dhan real broker adapters and full `docker compose
up --build` execution remain the same explicitly-deferred items noted above.

## Production Hardening Pass

A full correctness/security review across the codebase, requested explicitly rather than
inferred - the diff was reviewed against the project's very first commit so nothing was
skipped. Real bugs found and fixed, each with a regression test:

- **Cross-tenant paper-trading lockout**: `/paper-execute`'s risk-engine state
  (`TradingDayState`) was one process-global object shared by every user and anonymous caller,
  and nothing ever decremented `open_positions` when a position closed. After
  `max_open_positions` (default 3) successful paper trades from *anyone* since the server
  started, every future call from every user was permanently rejected until a restart. Fixed by
  deriving a logged-in user's state fresh from their own persisted trade history on every
  request (`app/trading/persistence.py::build_trading_day_state`); anonymous calls now always
  start clean instead of inheriting anyone else's counters.
- **Backtest engine ignored target2**: the backtest only ever checked stop-loss/target1, unlike
  the live/paper exit logic (`app/trading/exit_logic.py::check_exit`), which checks target2
  first. A bar crossing both targets closed at target1 in a backtest but would close at target2
  in real paper trading - so backtested performance didn't match the platform's own real exit
  behavior for every inbuilt strategy (they all set a target2). Fixed to match priority: stop
  loss, then target2, then target1.
- **Shoonya option chain returned no OI/LTP data**: `ShoonyaBroker.get_option_chain` built a
  bare list of strikes and never called `/GetQuotes` for oi/ltp/volume, and never merged CE/PE
  legs by strike (unlike the Zerodha adapter's `rows_by_strike` pattern) - so any PCR/Max
  Pain/OI-buildup analysis over a Shoonya chain silently got nothing to work with. Fixed to
  resolve each contract's live quote and merge by strike.
- **Upstox `place_order` sent a plain trading symbol where Upstox's API needs its own
  `instrument_key`** (e.g. `"NSE_EQ|INE002A01018"`) - every other adapter accepts a plain
  trading symbol per `BrokerOrderRequest`'s contract, so a live Upstox order would have carried
  an invalid `instrument_token` and been rejected. Fixed to resolve the symbol via the
  instrument master, the same way `get_historical_data`/`get_option_chain` already did. Both
  Zerodha's and Upstox's instrument-master fetches (a multi-MB file that changes at most daily)
  are now cached per adapter instance instead of re-fetched on every call.
- **Zerodha's CSV strike parsing**: Kite's instrument CSV puts the literal string `"0"` (a
  non-empty, truthy string) in the strike column for every non-option instrument - the old
  `if row.get("strike")` check treated that as present and set `strike=0.0` on every
  equity/future row instead of `None`, making every non-option instrument look like an option
  at strike 0 to anything that branches on `strike is None`. Fixed to parse first, then collapse
  a genuine zero to `None`.
- **Option chain ATM classification never fired for a real price**: `_moneyness()` marked a
  strike ATM only on exact float equality with the raw underlying LTP, which essentially never
  holds for a real (non-integer) price - so the per-strike breakdown never tagged any strike
  ATM even though the top-level `atm_strike` field correctly found the nearest one. Fixed to
  compare against that same `atm_strike`.
- **Declarative strategy (Strategy Builder) accepted a period of 0**: `POST
  /api/custom-strategies`'s own docstring claims constructing a `DeclarativeStrategy` "catches
  e.g. an indicator/period combination that can't run," but construction never actually
  evaluates any indicator - so an indicator period (or ATR period) of 0 saved cleanly and only
  crashed with a `ZeroDivisionError`/`ValueError` on the next `/signal`, `/paper-execute`, or
  `/backtest` call. Fixed with Pydantic field bounds (`gt=0`) so it's a normal 422 at creation
  time instead.
- **Signals page could execute a different signal than the one displayed**: "Paper Execute"
  regenerated sample candles from the *current* bars/seed inputs rather than reusing the exact
  candles that produced the currently-shown signal card, so changing either input after
  generating (without regenerating) could fire a trade the user never actually saw. Fixed by
  pinning the exact data used for the last generated signal and disabling the button until one
  exists.
- **Fundamentals screener swallowed errors silently** (no `try`/`catch`, unlike every other tab
  on that page) - fixed to show the same inline error pattern as the rest of the page.

Security/config hardening also added, all gated behind a new `ENVIRONMENT` variable (default
`development`, so nothing changes for local dev with no `.env` at all):

- `validate_production_config()` (`app/core/config.py`) refuses to start when
  `ENVIRONMENT=production` and `JWT_SECRET_KEY`/`SECRETS_ENCRYPTION_KEY` are still unset/default
  or `ALLOWED_ORIGINS` is still `"*"` - a known-insecure default left over from local dev is a
  common way real deployments get compromised, so failing fast at boot is cheaper than that
  incident.
- CORS origins are now configurable via `ALLOWED_ORIGINS` (comma-separated) instead of a
  hardcoded `"*"`.
- Login now runs a bcrypt comparison against a fixed dummy hash even when the email doesn't
  exist, so response timing doesn't leak whether an email is registered.
- Registration now enforces a minimum (8) and maximum (128) password length.
- The DB engine now sets `pool_pre_ping=True`/`pool_recycle=1800`, so a DB restart or an idle
  connection killed by a load balancer surfaces as a clean retry instead of a mysterious
  mid-request error.

Full backend suite: 238 passing (up from 221 before this pass). See individual commit messages
for the complete list of touched files.

## Multi-Tenancy + RBAC Foundation

The platform moved from single-user accounts to real multi-tenant SaaS, per a newer master spec
whose gap analysis was reconciled with what already existed rather than triggering a rebuild
(kept the existing FastAPI/Postgres/Vite stack). A `Tenant` (`app/db/models.py::Tenant` -
id/name/plan/status) is now the isolation boundary the spec calls for: every tenant-owned table
carries a `tenant_id`, always derived server-side from the authenticated user's own row - never
accepted from the client - so tenants can never read or write each other's data.

- **Registration** (`app/auth/routes.py::register`) creates a brand-new `Tenant` alongside the
  new `User` in the same transaction. There is no invite-onto-an-existing-tenant flow yet, so V1
  is one tenant per signup; a second user can only join an existing tenant via a direct DB insert
  today (see `tests/test_multi_tenancy.py::_add_teammate` for the shape a future invite endpoint
  would produce). Existing pre-migration users each get backfilled into their own new tenant, so
  behavior for pre-existing single-user data is unchanged.
- **`User.role`** (`app/core/enums.py::UserRole` - `SUPER_ADMIN`/`USER`/`STRATEGY_CREATOR`/
  `SUPPORT`) defaults to `USER` on registration. `app/auth/dependencies.py::require_role(...)` is
  the RBAC dependency factory future admin/support/strategy-management routes gate behind
  (`SUPER_ADMIN` always passes); no route uses it yet.
- **Tenant-scoped resources** - visible/shared across every user in the same tenant, isolated
  from every other tenant: broker credentials, trades (paper fills), signal history, custom
  strategies. Each keeps its own `user_id` purely for attribution (who added/created it); every
  route that used to filter by `user_id == user.id` now filters by `tenant_id == user.tenant_id`
  (`app/brokers/routes.py`, `app/trading/routes.py`, `app/custom_strategies/routes.py` +
  `resolver.py`, `app/trading/persistence.py`).
- **Risk settings became tenant-scoped** (`RiskSettingsRecord`, matching the spec's
  `risk_limits.scope=tenant`): one row per tenant instead of per user, with `updated_by`
  (nullable, `SET NULL` on user deletion) replacing the old unique `user_id` for attribution only.
  `app/trading/persistence.py::build_trading_day_state` (the derived-from-history risk-engine
  state introduced in the hardening pass above) is likewise now tenant-scoped: the daily-loss/
  trade-count/consecutive-loss budget is shared across everyone trading under one tenant, which
  is the correct behavior once a tenant can have more than one user.
- **Audit logs** gained a `tenant_id` column (nullable, best-effort) but the `GET /api/audit-logs`
  endpoint deliberately stays filtered by `user_id == user.id` for now - a tenant-wide admin audit
  view is future RBAC-gated work, not implied by adding the column.
- **Fundamentals reference data stayed tenant-agnostic** (companies, financial periods,
  shareholding, corporate actions, earnings calendar, sector metrics, qualitative factors): a
  company's filed financials are the same fact regardless of which tenant is looking, matching
  how an instrument master is shared reference data rather than tenant-owned.
- Migration: `alembic/versions/6f1a2d9b7c31_add_multi_tenancy.py` creates `tenants`, adds
  `role`/`tenant_id` to `users`, adds `tenant_id` to the tenant-scoped tables above, backfills
  every existing user into its own new tenant and every owned row from its owning user's
  `tenant_id`, and migrates `risk_settings` from per-user to per-tenant (including the
  `user_id`→`updated_by` rename and the unique-constraint move). Verified end-to-end against a
  real Postgres instance: applies cleanly from the previous head, backfills pre-existing rows
  correctly, downgrades cleanly, and `alembic check` reports zero drift against the current
  models.
- Deferred, per explicit user direction, for later phases: the Conversational AI Strategy
  Builder (needs a real LLM API key) and MCX Commodities/Cryptocurrency asset classes.

Full backend suite: 244 passing (up from 238 before this pass), including a new
`tests/test_multi_tenancy.py` covering tenant creation on registration, cross-tenant isolation of
custom strategies/risk settings/broker credentials, and same-tenant sharing between two users.

## Order Idempotency + Formal Order State Machine

Every `POST /api/strategies/{id}/paper-execute` call by a logged-in user now creates a formal,
auditable order record - not just a `TradeRecord` the moment something fills, but a full
lifecycle for every execution *attempt*, rejected or not.

- **`OrderRecord`** (`app/db/models.py`) - one row per attempt: tenant/user, strategy, symbol,
  direction, quantity, current `status`, the full `Signal` that produced it (`signal_json`, so a
  replayed idempotent call can return the exact same signal), the rejection/fill `reasons`, and
  (once filled) the `trade_id` it opened.
- **`OrderEventRecord`** - an append-only audit trail, one row per transition, never mutated or
  deleted: `from_status` → `to_status` + a human-readable `detail`. `GET /api/orders/{id}/events`
  exposes the full ordered history for one order.
- **State machine** (`app/execution/order_state_machine.py`) enforces the legal transition graph
  from the spec's order lifecycle: `CREATED → VALIDATING → RISK_CHECK → SUBMITTED → PENDING →
  FILLED → POSITION_OPEN` on the happy path, with `REJECTED`/`FAILED`/`CANCELLED`/`PARTIAL_FILL`
  branching off `RISK_CHECK`/`SUBMITTED`/`PENDING`/`PARTIAL_FILL` respectively.
  `assert_valid_transition` raises `InvalidOrderTransition` on any transition outside that graph
  - a defensive check against a future bug in the calling code, not something a caller's input
  can trigger. `app/execution/order_persistence.py` wraps the DB side: `create_order` opens
  `CREATED` and logs the first event; `transition_order` validates, updates the row, and appends
  the next event, all in one commit, so the ledger is durable even if something crashes
  mid-request.
- **Idempotency**: `PaperExecuteRequest.idempotency_key` (optional) is unique per tenant
  (`OrderRecord`'s `uq_tenant_idempotency_key` constraint - `NULL` is never deduplicated, so
  omitting it behaves exactly as before). A client retrying the same submission - a network
  retry, a duplicated webhook delivery once TradingView ingestion (task #102) lands - gets back
  the first attempt's recorded outcome (`idempotent_replay: true` in the response) instead of
  running risk checks and placing a second order; the unique constraint itself is what makes this
  safe under a concurrent double-send, not just the read-before-write check.
- Anonymous (no-login) paper-execute calls behave exactly as before: no `OrderRecord`, no
  idempotency tracking, matching the console's try-it-without-an-account flow.
- `GET /api/orders` lists a tenant's full order ledger (every attempt, not just fills) - the
  complement to `GET /api/trades`, which only ever gets a row once an order actually fills.
- Migration: `alembic/versions/9a3c6e1b2d47_add_orders_and_order_events.py` - two new tables, no
  backfill needed (there was no prior order-lifecycle data to migrate). Verified against a real
  Postgres instance: applies, downgrades, and re-applies cleanly, with zero `alembic check` drift.

Full backend suite: 262 passing (up from 244 before this pass), including new
`tests/test_order_state_machine.py` (every legal/illegal transition) and `tests/test_orders_api.py`
(full lifecycle on a fill, full lifecycle on a risk rejection, idempotent replay returning the
same order without a second trade, distinct keys each executing independently, and tenant
isolation on both `/api/orders` and `/api/orders/{id}/events`).

## Kill Switches + Emergency Exit

Three widening kill-switch scopes (`app/core/enums.py::KillSwitchScope`), all backed by a single
`KillSwitchRecord` table (`app/db/models.py`) upserted per scope key rather than appended - the
*current* state is what execution checks on every order:

- **GLOBAL** — platform-wide, `tenant_id=None`. Gated behind `require_role()` with no roles
  listed, which (per `app/auth/dependencies.py::require_role`) means only `SUPER_ADMIN` passes -
  reserved for a platform operator, never a tenant's own users.
- **TENANT** — this user's own tenant only; any logged-in user of that tenant can engage/disengage
  it (the "stop everything for my account" panic button).
- **STRATEGY** — one strategy within this tenant only; other strategies keep trading.

`app/kill_switch/checks.py::active_kill_switch_reasons` is checked inside `paper_execute`
(`app/main.py`) right after the order reaches `VALIDATING` and before it ever reaches
`RISK_CHECK` - a GLOBAL, TENANT, or STRATEGY switch engaged for this call's tenant/strategy
rejects the order immediately (`VALIDATING → REJECTED`, a transition added specifically for
pre-risk-check rejections) without ever running risk sizing or touching the paper broker. The
anonymous (no-login) demo path has no tenant to check a TENANT/STRATEGY switch against, so it
only checks GLOBAL (`is_global_kill_switch_engaged`) - a platform-wide incident should still
block the try-it-without-an-account flow.

**Emergency exit** (`POST /api/kill-switch/emergency-exit`) runs the full spec'd sequence in one
call: (1) engage this tenant's kill switch so no new order can enter, (2) cancel every order
still sitting in a non-terminal state (`CANCELLED` is now reachable from every non-terminal
status in the state machine, not just `PENDING`/`PARTIAL_FILL` - a cancel has to be able to
interrupt an order at any live stage), (3) close every open position a current price was
supplied for (`prices: {symbol: price}` in the request body - there is no live broker quote feed
wired in yet, so a position with no supplied price is left open and reported back in
`skipped_symbols` rather than closed at a fabricated price, the same honest limitation the
manual mark-price endpoint already has), (4) leave an `AuditLogRecord` audit trail. A real
notification/alert delivery (email/SMS/push) is task #101 (Notification Engine) - today "alert"
means the audit-log row plus whatever the caller does with the HTTP response.

Full backend suite: 271 passing (up from 262 before this pass), including new
`tests/test_kill_switch_api.py` (RBAC on the global switch, tenant switch blocking one tenant
without affecting another, strategy switch blocking only that strategy, global switch blocking
every tenant and anonymous calls, emergency exit closing priced positions and skipping unpriced
ones) and two new state-machine tests covering the widened `CANCELLED`/pre-risk-check `REJECTED`
transitions.

## Strategy Version Control

Editing a saved Strategy Builder strategy no longer overwrites it in place. A new
`StrategyVersionRecord` table (`app/db/models.py`) holds one immutable row per version, never
updated or deleted once written; `CustomStrategyRecord` gains a `live_version_id` pointer plus
denormalized `name`/`config_json` that always mirror whichever version is currently live, so
every existing reader (the resolver, `/signal`/`/paper-execute`/`/backtest`, the list/get
endpoints) needed zero changes to pick up a version change.

- `app/custom_strategies/versioning.py::create_version` is the single write path every
  create/update/rollback goes through: it archives whichever version was previously `LIVE`
  (flips its `status`, never its `config_json`), inserts a new version row, and repoints the
  parent record's live pointer and denormalized fields at it.
- **Create** (`POST /api/custom-strategies`) makes version 1, `source="created"`.
- **Update** (`PUT /api/custom-strategies/{id}`) re-validates the new config the same way create
  does (constructs a real `DeclarativeStrategy` from it, rejecting an unrunnable rule set as a
  422 rather than saving it), then appends a new version, `source="user_edit"`. A rejected update
  never creates a version - the strategy keeps running on its last good config.
- **History** (`GET /api/custom-strategies/{id}/versions`) lists every version, most recent
  first, each with its `config`, `source`, `status`, and `created_at`.
- **Rollback** (`POST /api/custom-strategies/{id}/versions/{n}/rollback`) makes an earlier
  version live again by *appending* a brand-new version whose content matches the target one
  (`source="rollback"`) - it never resurrects or mutates the old row, so the version history only
  ever grows forward even when the live *config* goes backward.
- All of it is tenant-scoped exactly like the rest of custom-strategy resources: a strategy's
  versions, and the update/rollback endpoints, 404 for a caller outside its owning tenant.
- Migration: `alembic/versions/c47d8f1e2a63_add_strategy_versions.py` backfills a version 1
  (`source='created'`, `status='LIVE'`) for every pre-existing `custom_strategies` row from its
  current `config_json` and points `live_version_id` at it, so nothing pre-migration changes
  behavior. Verified against a real Postgres instance with seeded pre-migration data: applies,
  backfills correctly, downgrades, and re-applies cleanly, with zero `alembic check` drift.

Full backend suite: 278 passing (up from 271 before this pass), including new
`tests/test_strategy_versioning.py` (version 1 on create, update archiving the old version and
creating a new live one, a rejected update creating no version, rollback appending a matching
version without touching the original row's content, rollback to an unknown version returning
404, tenant isolation on versions/update/rollback, and a strategy actually executing signals
against its current live version after an edit).

## Greeks Engine

Pure-Python (no scipy dependency) European Black-Scholes Delta/Gamma/Theta/Vega
(`app/option_chain/greeks.py`), integrated two ways:

- **Per-strike, inside the existing option chain analysis** (`analyze_option_chain` in
  `app/option_chain/analysis.py`, still `POST /api/option-chain/analyze`): for each strike,
  `call_greeks`/`put_greeks` on `StrikeAnalysis` are computed from the chain's own real
  `underlying_ltp`, `expiry`, and either a broker-supplied `call_iv`/`put_iv` or (since none of
  Zerodha/Upstox/Shoonya populate IV directly today, but all three populate `call_ltp`/`put_ltp`
  from a real quote) an implied volatility *solved* from that real quoted price. Never
  fabricated: a strike with no real price/IV to compute from gets `None`, not a guessed number,
  and a quote outside no-arbitrage bounds (below intrinsic value, above the theoretical maximum)
  also gets `None` rather than a nonsensical solved volatility.
- **Per-leg and per-strategy, as a standalone calculator** (`POST /api/option-chain/greeks`,
  `app/option_chain/leg_greeks.py`): takes one or more `OptionLegInput`s (strike, type, signed
  `quantity` - negative for short, already scaled by lot size - underlying LTP, expiry, and
  either a real `option_ltp` or a direct `implied_volatility`), returns each leg's Greeks plus
  position-level Greeks (a short leg's Greeks come back sign-inverted) and the strategy's net
  Delta/Gamma/Theta/Vega across every leg - so a spread/straddle/strangle nets a short leg's
  Greeks against a long leg's rather than reporting each leg in isolation. A 422 means a leg's
  supplied price is outside what's solvable, not a server error.
- `implied_volatility()` solves via bisection rather than Newton-Raphson: Black-Scholes price is
  monotonically increasing in volatility, so bisection is both simple and guaranteed to converge
  within `[0.001, 5.0]` if a solution exists there, without Newton's instability near-zero vega
  (deep ITM/OTM strikes).
- `RISK_FREE_RATE` (`app/core/config.py`, default `0.07`) is a configurable approximation of the
  short-term Indian G-Sec/repo yield used to discount payoffs - not a live rate feed, documented
  as such.

Full backend suite: 303 passing (up from 278 before this pass), including new `tests/
test_greeks.py` (Black-Scholes sanity checks - ATM delta near 0.5, deep ITM/OTM delta bounds,
call/put gamma-vega parity, negative theta for a long option, implied-volatility round-tripping
through the pricer, `None` below intrinsic value and above the theoretical maximum, short-leg
sign inversion, net-zero Greeks for a long+short pair, a bull call spread's bounded positive
delta), `tests/test_greeks_api.py`, and three new cases in `tests/test_option_chain.py` covering
Greeks solved from a real quoted price inside the existing chain analysis, and `None` when
expiry/underlying LTP/price data is missing.

## Position Reconciliation Engine

`app/reconciliation/engine.py::reconcile_positions` compares this tenant's internally-tracked
open positions (`TradeRecord` rows with no `exit_time`, netted by symbol - `LONG` contributes
`+quantity`, `SHORT` contributes `-quantity`, the same signed-net-quantity convention every
broker adapter's own `get_positions()` already uses) against what the broker itself reports, one
row per symbol:

- **MATCHED** - platform and broker agree.
- **QUANTITY_MISMATCH** - both sides have a position in the symbol, but the net quantities differ.
- **MISSING_AT_BROKER** - the platform believes a position is open, but the broker reports none
  (a manual exit at the broker, a missed fill webhook, ...).
- **UNTRACKED_AT_BROKER** - the broker reports a position the platform has no record of at all
  (a manual entry at the broker, an order placed outside the platform, ...).

A broker position with `quantity=0` (a broker's own way of reporting a closed-out position) is
ignored rather than treated as an open position needing reconciliation. Symbols are matched by
exact string equality - the same trading symbol the strategy/order router used going in.

`POST /api/reconciliation/{broker_name}` (auth required) loads this tenant's stored, decrypted
credentials for that broker (the same ones `POST /api/broker/{name}/authenticate` uses), calls
`BrokerInterface.get_positions()` for the real numbers, and compares them against every
internally open `TradeRecord` for this tenant. Every non-`MATCHED` item is written to the audit
trail as it's found (`position_reconciliation_mismatch`), plus one summary row for the run itself
(`position_reconciliation_run`); a broker-side failure (network error, expired session) is caught,
audited (`position_reconciliation_failed`), and surfaced as a clean 502 rather than a raw
exception. Reconciliation works against `TradeRecord` regardless of `mode` (`PAPER` today; ready
for `LIVE` once live order fills are persisted there too), since the comparison itself - what the
platform believes it holds vs. what the broker actually reports - is identical either way.

Full backend suite: 318 passing (up from 303 before this pass), including new
`tests/test_reconciliation.py` (matched long/short positions using the signed-quantity
convention, quantity mismatches, missing-at-broker, untracked-at-broker, zero-quantity broker
rows ignored, multiple open trades for one symbol netted correctly, multiple symbols reported
independently) and `tests/test_reconciliation_api.py` (auth, missing stored credentials, unknown
broker, a full mixed matched/mismatched report with its audit trail, a broker failure surfacing
as a 502 with its own audit entry, and tenant isolation).

## Notification Engine

An in-app (not yet email/SMS/push - that's future work once a delivery channel exists) feed of
the platform's own significant events, all nine categories the spec calls for
(`app/core/enums.py::NotificationType`): `ENTRY`, `EXIT`, `REJECTION`, `BROKER_DISCONNECT`,
`TOKEN_EXPIRED`, `RISK_REJECTION`, `DAILY_LOSS_LIMIT`, `EMERGENCY_EXIT`, `SYSTEM_FAILURE` - each
tagged `INFO`/`WARNING`/`CRITICAL`.

- `app/notifications/service.py::notify` is the single choke point every other engine emits a
  notification through - one `NotificationRecord` row per event, visible to the whole tenant
  like every other tenant-shared resource (`GET /api/notifications`, `unread_only` filter, `POST
  .../{id}/read` and `.../read-all`). `read_at` is a simple per-row marker - an honest v1
  approximation, since a real per-user read-state join table only matters once a tenant can have
  more than its one original user (the same limitation already noted for `GET /api/audit-logs`).
- Wired into every concretely-exercised event source in `paper_execute` (`app/main.py`): a
  successful fill → `ENTRY`; a kill-switch rejection → `REJECTION`; a risk-engine rejection →
  `RISK_REJECTION`, except when the rejection reason specifically names the daily loss limit, in
  which case it's the louder `DAILY_LOSS_LIMIT` (`CRITICAL`, not `WARNING`) - breaching the
  account's daily loss limit is a materially different kind of event than an ordinary R:R
  rejection. `mark_price` (`app/trading/routes.py`) closing a position → `EXIT` (`WARNING` for a
  net loss after costs, `INFO` otherwise). A broker authentication failure
  (`app/brokers/routes.py`) → `TOKEN_EXPIRED` if the error text mentions a token, else the more
  generic `BROKER_DISCONNECT` - a heuristic, not a certainty, since there's no structured error
  code to key off across three different broker APIs. Emergency exit
  (`app/kill_switch/routes.py`) → `EMERGENCY_EXIT`. A reconciliation fetch failure
  (`app/reconciliation/routes.py`) → `SYSTEM_FAILURE`.
- **Frontend**: a new Notifications tab (`frontend/src/pages/NotificationsPage.tsx`) renders the
  feed with a severity badge per entry, an unread count, "mark read" per item, and "mark all
  read." Verified live against a running backend + Vite dev server with Playwright: registering,
  generating a signal, and paper-executing produced a real notification (a `RISK_REJECTION` from
  the sample data's `NO_TRADE` signal) that rendered correctly with proper styling, and marking
  it read updated the UI live with no console errors.
- Migration: `alembic/versions/d84e2f6a1b93_add_notifications.py` - new table only, no backfill
  needed (no prior notification history exists). Verified against a real Postgres instance:
  applies, downgrades, and re-applies cleanly, zero `alembic check` drift.

Full backend suite: 330 passing (up from 318 before this pass), including new
`tests/test_notifications_api.py` covering every wired event source (entry, risk rejection,
daily-loss-limit escalation to CRITICAL, kill-switch rejection, exit with loss-vs-profit
severity, both broker-auth-failure branches, emergency exit, reconciliation failure), read/
read-all state transitions, and tenant isolation.

## TradingView Webhook Ingestion

An inbound TradingView "Webhook URL" alert is treated as a pre-formed trade signal - the
strategy logic already ran in Pine Script on TradingView's side, so there are no OHLCV bars to
re-analyze here. `app/webhooks/routes.py::tradingview_webhook` (`POST
/api/webhooks/tradingview/{webhook_token}`) builds a `Signal` directly from the alert's
`entry`/`stop_loss`/`target1`/`target2`/`direction` and runs it through the exact same
kill-switch → risk-engine → paper-broker → notification pipeline a logged-in
`/paper-execute` call uses.

- **Authenticated**: TradingView's webhook alerts can't carry a JWT/OAuth header - they just
  POST a JSON body to whatever URL you configure. Each tenant gets a unique, randomly-generated
  `webhook_token` (`Tenant.webhook_token`, `secrets.token_urlsafe(24)`, generated at
  registration), embedded in the URL itself; the token *is* the credential. `GET
  /api/webhooks/tradingview/token` (JWT-authenticated) returns the current URL to paste into a
  TradingView alert, and a Settings-page card (`frontend/src/pages/SettingsPage.tsx`) shows it
  with a copy button; `POST .../token/rotate` invalidates the old URL and issues a new one if it
  ever leaks.
- **Schema-validated**: `TradingViewAlertPayload` (Pydantic) requires `strategy_id`, `symbol`,
  `direction`, `entry`, `stop_loss`, `target1`; `direction` explicitly rejects `NO_TRADE` (an
  alert firing is itself a trade signal, never a "no trade" one) as a 422.
- **Duplicate-checked**: an optional `alert_id` field (recommended: TradingView's `{{time}}`
  placeholder, or a UUID from Pine Script) becomes the same `idempotency_key` mechanism
  `/paper-execute` already uses - a retried or duplicated webhook delivery replays the first
  attempt's recorded outcome instead of double-executing. Without an `alert_id`, each delivery
  executes independently (an honest limitation, not a silent risk - TradingView delivery retries
  are rare but not impossible).
- **Through the existing Risk → Order Engine**: refactored the shared post-signal logic (create
  the formal order, check kill switches, run risk sizing, route to the paper broker, persist a
  fill, notify) out of `paper_execute` into `app/execution/signal_execution.py::
  execute_signal_for_user`, so the webhook and the console's own paper-execute path share one
  implementation rather than two independently-maintained copies of the same state machine.
- Since a webhook alert has no logged-in caller to attribute the resulting order to, it's
  attributed to the tenant's earliest-created user (`_get_tenant_owner`) - unambiguous today
  since every V1 tenant has exactly one user (no invite flow yet).
- Migration: `alembic/versions/e5f8a2c74b16_add_tenant_webhook_token.py` backfills a unique
  random token (`md5(random()::text || clock_timestamp()::text || id::text)` - no Postgres
  extension dependency) for every pre-existing tenant. Verified against a real Postgres instance
  with seeded pre-migration tenants: applies, backfills uniquely, downgrades, and re-applies
  cleanly, zero `alembic check` drift.
- Verified live end-to-end with Playwright against a running backend + Vite dev server: the
  Settings page's webhook card renders the URL, rotating it changes the URL and invalidates the
  old one, and a real `POST` to the rotated URL executed a risk-sized paper trade that appeared
  correctly on the Positions page.

Full backend suite: 340 passing (up from 330 before this pass), including new
`tests/test_tradingview_webhook.py` (token auth, NO_TRADE rejection, a full successful
execution with trade/order/notification side effects, risk-engine rejection, tenant kill-switch
rejection, `alert_id` idempotent replay vs. no-dedup-without-one, tenant isolation, and token
rotation invalidating the old URL) - the full existing suite continues passing unchanged after
the `paper_execute` refactor, confirming `execute_signal_for_user` preserves its exact prior
behavior.
