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
    brokers/            # BrokerInterface, adapters, registry, routes, daily token lifecycle + Upstox OAuth
    auth/                # register/login (JWT), password hashing, get_current_user dependency
    db/                  # SQLAlchemy async engine/session, User/BrokerCredential/TradeRecord/AuditLog models
    secrets_store/        # Fernet encryption for broker credentials at rest
    trading/              # trade persistence, exit logic, position monitor (one close path, paper + live)
    price_action/       # swing detection, market structure (HH/HL/LH/LL, BOS/CHoCH), candlestick patterns
    support_resistance/ # zone engine: swing clusters, prev day/week, opening range, VWAP, pivots, Fibonacci
    option_chain/       # PCR, Max Pain, ATM/ITM/OTM, OI buildup/unwinding, bias classification
    signal_scoring/     # weighted composite score combining every analysis engine (the "why this trade" layer)
    execution/         # PaperBroker (simulated fills + costs), OrderRouter (paper + live fills, protective SL-M)
    market_data/       # broker candles (Redis-cached, resampled) + LTP, NSE session/holiday calendar
    workers/           # the autonomous trading worker (separate process) + worker heartbeat endpoint
    deployments/       # /api/deployments - what each tenant runs autonomously, in which mode
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

## Visual Design System

A full visual redesign pass, requested explicitly ("basic, not production grade") rather than a
functional one - every backend engine and API call underneath is unchanged. The console now
reads as one deliberate "modern trading terminal" system instead of a set of ad-hoc pages:

- **Fonts**: Inter (UI text) and JetBrains Mono (numbers) via Google Fonts, replacing the system
  font stack. Every price/quantity/count in `StatTile`, the Instruments table, and the
  `CandleChart` axes uses the mono face with tabular figures (`.font-tabular` in
  `src/styles/index.css`) so digits never jitter horizontally as values update - the same reason
  a real trading terminal never lets its price ticker's digits shift width.
- **A brand color distinct from bullish/bearish semantics**: `accent` (green) and `danger` (red)
  used to double as both "this is a positive/negative P&L or bullish/bearish signal" *and* "click
  this button" - the same color meant two different things depending on context. `tailwind.config.js`
  now has a separate `brand` (blue) token for every interactive primary action (buttons, active
  nav/tab state, links, focus rings), while `accent`/`danger` stay reserved exclusively for
  bullish/bearish and positive/negative P&L indicators across every page (`DirectionBadge`,
  `GradeBadge`, `StatTile` tone, P&L table cells, sentiment badges) - a real fintech UI
  convention (a buy button and a "this is bullish" tag are not the same kind of thing).
- **A proper icon set**: `lucide-react` replaces the unicode symbols (▦ ⚙ ✎ 🔍) the sidebar and
  pages used before - `Sidebar.tsx`'s `NAV_GROUPS` now pairs every destination with a real,
  semantically-named icon (`Zap` for Signals, `Radar` for the Scanner, `ShieldAlert` for Risk
  Management, etc.).
- **A logo mark** (`src/components/Logo.tsx`, inline SVG - a geometric ascending-bars motif in
  the brand gradient) used consistently in the sidebar header and the login/register screen,
  replacing plain text in both places.
- **Sidebar restructure**: flat, ungrouped nav → five labeled sections (Overview, Trade, Research,
  Portfolio, System) so the ~18 destinations read as an organized system rather than a long list;
  active state moved from a green bar (bullish color) to a blue one (brand color) for the same
  reason described above; the footer account chip now shows a persistent "PAPER MODE" badge.
- **A top bar** (`App.tsx`): a slim header showing the current page's title plus a notification
  bell and account icon - both present throughout the whole app now, not just reachable via the
  sidebar footer.
- **Shared UI atoms** (`src/components/ui.tsx`): `Card`/`StatTile` gained a subtle shadow token
  (`shadow-card` in `tailwind.config.js`) and rounder corners; `DemoDataBanner` gained an icon.
  Since nearly every page is built from these same atoms, this one file's redesign cascades
  visual consistency across the whole console without needing to hand-restyle each page.
- **Login/register screen** (`AccountPage.tsx`): now a centered, branded card (logo mark, icon-
  prefixed inputs, focus rings in the brand color) instead of a plain form - the same shell/sidebar
  architecture is kept (logging in is optional, not a gate - anonymous use of Signals/Backtesting
  still works, per the note above), only the card's own presentation changed.
- Verified live end-to-end with Playwright: screenshotted the Dashboard, login screen, Signals
  (including a generated signal's candlestick chart), Market Scanner, Instruments, and Strategy
  Builder pages to confirm the new fonts/icons/colors render correctly and consistently, and that
  no existing functionality (login, signal generation, filters) regressed.

Full backend suite unaffected (377 passing, frontend-only change); frontend `npm run build`
(TypeScript + Vite production bundle) passes clean.

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

`docker-compose.yml` at the repo root wires three services: `postgres` (17-alpine, a named
volume, a `pg_isready` healthcheck), `backend` (built from `backend/Dockerfile` — Python 3.13
slim, non-root user, a container healthcheck against `/api/system/health`, waits for Postgres
to be healthy before starting, and its `CMD` now runs `alembic upgrade head` before starting
`uvicorn` so the container always boots against the current tracked schema rather than relying
on `create_all`), and `frontend` (built from `frontend/Dockerfile` — a Node 24 build stage producing
the Vite production bundle, served by an `nginx:1.27-alpine` stage whose `nginx.conf`
reverse-proxies `/api/*` to the `backend` service by its compose network name and falls back to
`index.html` for client-side routes). `requirements.txt`'s `numpy`/`cryptography` upper bounds
are deliberately wide (`numpy<3.0`, `cryptography<46.0`) rather than pinned to whatever was
current when this was written, so a fresh `pip install` doesn't get needlessly downgraded
against newer packages already on your system. Copy `.env.example` to `.env` at the repo root
first (Postgres credentials, `JWT_SECRET_KEY`, `SECRETS_ENCRYPTION_KEY`), then:

```bash
docker compose up --build
# backend:  http://localhost:8000
# frontend: http://localhost:8080
```

**Verification history:** the development sandbox this was originally written in has a network
egress policy that explicitly blocks Docker Hub's CDN, so only `docker compose config` (service
graph, env interpolation, healthcheck syntax) could be validated there - actually pulling images
and running `docker compose up` was untested. It has since been run for real, end to end, on a
real machine (Windows 11 + Docker Desktop, WSL2 backend, Docker 29.8.0 / Compose v5.5.1): all
four images pulled and built cleanly, Postgres initialized, all Alembic migrations up to the
current head applied automatically before the backend started serving, the container healthcheck
against `/api/system/health` passed, and the frontend was reachable at `localhost:8080` with the
backend proxied through nginx at `/api/*` - a full, unmodified `docker compose up --build` with
no code changes needed.

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

Genuinely still outstanding: Angel One/Fyers/Dhan/CoinDCX adapters are structurally registered but
still need their real endpoints wired in (see `app/brokers/stubs.py`) - deliberately deprioritized
once Zerodha/Upstox/Shoonya existed. `docker compose up --build` has since been run and verified
end to end on a real machine (see "Docker Deployment" above).

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
  with mocked HTTP responses, but its live network behavior is still unverified: the development
  sandbox's network policy blocks `nseindia.com` specifically (a separate, narrower block than
  the since-resolved Docker Hub one - see Docker Deployment above), so this one still needs a
  real smoke test.
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

## Market Scanner

A watchlist screener that runs configurable filters across many symbols at once and returns
only the ones that clear every filter - `app/scanner/engine.py::run_scanner`. It is a pure,
stateless function (no DB model, no Alembic migration): given a `ScannerRequest` it returns a
`ScannerResult` and nothing is persisted.

- **Indicator filters reuse the Strategy Builder's own building blocks.** Rather than invent a
  second condition DSL, `ScannerRequest.indicator_conditions` is a list of the exact same
  `Condition`/`Operand` types `app/strategy_engine/declarative.py` already defines for the
  no-code Strategy Builder - the same engine (`Condition.evaluate`) checks them here, and the
  frontend reuses the same `ConditionListEditor`/`ConditionEditor`/`OperandEditor` components
  (exported from `StrategyBuilderPage.tsx`) to edit them.
- **Structure filters** (`StructureFilter`, `StructureFilterType`) test price-action state built
  from each symbol's own candles: `TREND_UPTREND`/`DOWNTREND`/`RANGE` against
  `analyze_market_structure().trend`; `BOS_BULLISH`/`BEARISH` and `CHOCH_BULLISH`/`BEARISH`
  against the most recent `StructureEvent`; `PATTERN_BULLISH`/`BEARISH` against
  `detect_patterns_at()` on the latest bar; `NEAR_SUPPORT`/`NEAR_RESISTANCE` against the closest
  `SupportResistanceEngine` zone, within `tolerance_pct` of the current close.
- **Option filters** (`OptionFilter`, `OptionFilterType`) test `analyze_option_chain()` output
  for a symbol's *optionally* supplied `OptionChain`: `PCR` against an operator/value threshold,
  `BIAS_BULLISH`/`BEARISH` against the chain's classified bias, `NEAR_MAX_PAIN` against distance
  from `max_pain` within `tolerance_pct`. Consistent with the platform's "never fabricate data"
  rule: a symbol with no option chain supplied simply never matches an option filter, rather
  than skipping the filter or defaulting to a pass.
- All three filter groups are AND-combined, both within a group and across groups; each check
  short-circuits on first failure. A `ScannerMatch` records which specific filters matched
  (`matched_indicator_labels`/`matched_structure_labels`/`matched_option_labels`, each using the
  same human-readable `.label()` pattern the Strategy Builder uses) so the UI can show *why* a
  symbol matched, not just that it did.
- `POST /api/scanner/run` (`ScannerRequest` in, `ScannerResult` out) needs no authentication - it
  is a pure function of its input, same as `/backtest`.
- Frontend: `frontend/src/pages/ScannerPage.tsx` - a comma-separated watchlist input, the reused
  indicator-condition editor, and add/remove list editors for structure and option filters.
  "Run Scanner" generates deterministic sample candles per symbol (seeded from the symbol name
  itself via `generateSampleCandles`, so every watchlist entry gets a different but reproducible
  price path) plus, only when at least one option filter is configured, a sample option chain
  per symbol (`generateSampleOptionChain`, tilt varied per symbol) - the same no-live-broker
  sample-data convention every other page already follows. Results render as a card per matched
  symbol with its matched-filter labels as badges.
- Verified live end-to-end with Playwright against a running backend + Vite dev server: loaded
  the Scanner page, added a structure filter and an option filter, ran a scan against a 6-symbol
  watchlist, and confirmed the backend correctly filtered it down to matching symbols with
  correct per-symbol matched-label badges.

Full backend suite: 352 passing (up from 340), including new `tests/test_scanner.py` (indicator
filter matching and AND-combination, every structure filter type, PCR/bias/max-pain option
filters, an option filter correctly failing when no chain is supplied, and scanned/matched count
reporting) and `tests/test_scanner_api.py` (the `/api/scanner/run` endpoint end-to-end).

## News & Event Engine

Structured, cited entries for macro/market news that has no live feed wired in - RBI monetary
policy decisions, Union Budget announcements, government/regulatory policy changes, broad
corporate news, global macro events, and sector-wide developments -
`app/news_events/{models,persistence,routes}.py`.

- **Reuses the fundamentals module's citation convention rather than inventing a new one.**
  `NewsEvent.source` is a `SourceCitation` (`app/fundamentals/models.py` - `source`, `source_url`,
  `publication_date`, `retrieved_date`, `confidence`), the exact same model every fundamentals
  input already carries. Unlike most fundamentals inputs, where `source` is optional, it's
  **mandatory** here (`source: SourceCitation`, no default) - the entire point of this table is a
  sourced claim, not a raw number a human might reasonably supply without one.
- **Complements, not duplicates, the existing Event Impact Score engine** (task #81,
  `app/fundamentals/engines/event_impact.py`), which scores per-company `CorporateAction` entries
  (always tied to one `company_id`). `NewsEvent` is the broader, market-wide counterpart: category
  is `RBI_POLICY`/`UNION_BUDGET`/`GOVT_POLICY`/`CORPORATE`/`GLOBAL_MACRO`/`SECTOR`/`OTHER`, and
  `affected_symbols` is a plain list (empty means market-wide, e.g. an RBI repo rate decision) -
  there's no per-company FK, since most of these events aren't about one company.
- **Shared reference data, not tenant-private** - same pattern as the fundamentals module: reads
  (`GET /api/news-events`, with optional `category`/`symbol`/`since` filters, and `GET
  /api/news-events/{id}`) are open to everyone, writes (`POST /api/news-events`) require auth so
  every entry is attributed (`created_by`). A real RBI policy decision is a fact for every tenant,
  not a per-tenant judgement call, so it isn't scoped by `tenant_id` like trades/orders/strategies
  are. `DELETE /api/news-events/{id}` is restricted to the user who created the entry (403 for
  anyone else) - the platform's one narrow correction mechanism if an entry was a mistake; there
  is no update endpoint (delete and re-add instead, keeping the CRUD surface small).
- `affected_symbols` and `source` are stored as JSON text columns (`affected_symbols_json`,
  `source_json`) - the same "Pydantic model round-tripped through a JSON text column" pattern
  `CorporateActionRecord`/`EarningsCalendarEventRecord` already use for `source_json`, converted
  by `app/news_events/persistence.py` so the ORM never leaks into the routes layer.
- Frontend: `frontend/src/pages/NewsEventsPage.tsx` - a category/symbol filter, a card per event
  showing its category and sentiment badges, headline, description, affected symbols, event date,
  and its cited source (a clickable link when `source_url` is supplied), and - for logged-in users
  - an "Add a cited event" form requiring at minimum a headline and a source name before it can be
  submitted. A "Delete" control appears only on entries the logged-in user created.
- Verified live end-to-end with Playwright against a running backend + Vite dev server: registered
  a user, opened the News & Events page, submitted a cited RBI policy entry through the actual
  form, and confirmed it rendered with its category/sentiment badges and clickable source link.
  This surfaced one real bug, fixed during verification: the frontend's default citation object
  explicitly sent `retrieved_date: null`, but the backend's `SourceCitation.retrieved_date` is a
  non-optional field with a server-side `default_factory` (today's date) - an explicit `null`
  failed Pydantic validation (422) where simply omitting the field would have let the default
  apply. Fixed by making `retrieved_date` optional on the frontend's `SourceCitation` type and
  removing it from `defaultNewsEvent()`'s initial value, so it's never sent on create.

Full backend suite: 359 passing (up from 352), including new `tests/test_news_events_api.py`
(auth required for writes but not reads, a citation being mandatory on create, full
create/list/get/delete, delete restricted to the creator, category/symbol/since-date filtering,
and `affected_symbols` defaulting to an empty market-wide list).

## Multi-Asset-Class Support (MCX & Crypto)

Extends position sizing and instrument metadata beyond plain NSE/BSE equity & index options,
which already worked (every broker adapter's `exchange` field was always a free-form string, and
`OrderRouter` already took an arbitrary `exchange` to route on). The actual gap was the risk
engine: it sized every order in whole "lots" of one tenant-wide `RiskConfig.lot_size`, which is
meaningless once one symbol's lot might be 100 barrels of crude oil and another's is a fraction
of one Bitcoin - there is no such thing as "one lot" of a spot crypto pair.

- **`app/instruments/models.py` + `registry.py`**: a `ContractSpec` (symbol, exchange,
  `AssetClass`, `lot_size`, `tick_size`, `fractional`) and a small static registry of MCX
  commodity contracts (GOLD, GOLDM, SILVER, SILVERM, CRUDEOIL, NATURALGAS, COPPER) and crypto
  pairs (BTCINR, ETHINR, USDTINR_CRYPTO). These are publicly known, standard contract
  specifications (exchange lot sizes), not live prices and not fabricated - but exchanges revise
  them periodically, so treat this as an overridable reference default, not a live feed. Plain
  equity/index-option symbols are deliberately **not** in this registry - they never needed to be,
  since they already size correctly off the tenant's own risk settings; `get_contract_spec()`
  returns `None` for them, which every caller treats as "size the default equity way", never as
  an error.
- **`AssetClass` enum** (`app/core/enums.py`): EQUITY, INDEX_OPTION, COMMODITY, CRYPTO.
- **`RiskManager.validate_and_size` is now contract-spec-aware** (`contract_spec: Optional[
  ContractSpec] = None`, backward compatible - every existing caller that never passes one gets
  the exact same behavior as before). When a spec is supplied: a non-fractional instrument (MCX)
  floors to whole multiples of *its own* lot size instead of the tenant's; a fractional one
  (crypto) floors to whole multiples of its own smallest quantity increment instead, without ever
  rounding to a whole "lot" - a $1,000 risk budget against a ₹50,00,000 BTC stop distance sizes to
  0.01 BTC, not 0.
- **Quantity is `float` everywhere now, not `int`** - `RiskDecision.quantity`, `Trade.quantity`,
  `TradeRecord.quantity`/`OrderRecord.quantity` (DB), `BrokerOrderRequest`/`BrokerOrderStatus`/
  `BrokerTradeEntry`/`BrokerPosition`/`BrokerHolding.quantity`, every broker adapter's
  `modify_order(quantity=...)`. This was the one real blocker to fractional crypto sizing ever
  working: the old `int` type would have silently floored 0.01 BTC to 0. A pure widening for
  every existing equity/index-option/MCX quantity, which always was and still is a whole number.
- **`OrderRouter.execute` and the backtest engine both look up the traded symbol's contract spec
  automatically** (`get_contract_spec(signal.symbol)`) and pass it into `validate_and_size` - a
  caller never has to know or care whether a symbol needs special sizing.
- **`GET /api/instruments`** (list) and **`GET /api/instruments/{symbol}`** (lookup, 404 if
  unregistered) expose the registry - public, no auth, since it's static reference metadata like
  `/api/strategies`. Frontend: `frontend/src/pages/InstrumentsPage.tsx` lists every registered
  MCX and crypto contract with its lot size, tick size, and whether it sizes fractionally.
- **A `coindcx` broker stub** (`app/brokers/stubs.py::CoinDCXBroker`) was added alongside the
  existing Angel One/Fyers/Dhan stubs, registered in `BROKER_ADAPTERS` - structurally wired in and
  satisfies `BrokerInterface`, but every I/O method raises `NotImplementedError` until a real
  adapter is written and tested against CoinDCX's (or another exchange's) actual API. No crypto
  exchange adapter has real, tested I/O today - none of the platform's fully-implemented brokers
  (Zerodha, Upstox, Shoonya) are crypto exchanges, and building one requires exchange-specific
  credentials this environment has no way to test against, the same honesty constraint that kept
  the NSE fundamentals provider "real but unverified in this sandbox" rather than faked.
- Migration `alembic/versions/b7d4f9a3c821_widen_quantity_to_float.py` (chained after
  `a1c9d3e7f204`) widens `trades.quantity` and `orders.quantity` from Integer to Float - verified
  against a real Postgres instance with seeded pre-existing whole-number rows: applies, preserves
  existing data exactly, downgrades, re-applies cleanly, zero `alembic check` drift.
- Verified live end-to-end with Playwright: the Instruments page renders all registered MCX and
  crypto contracts with correct lot/tick sizes, and a full equity paper-execute round trip through
  Signals still works unchanged after the quantity-type and risk-engine changes.

Full backend suite: 367 passing (up from 359), including new `tests/test_instruments_api.py`
(registry lookups, case-insensitivity, asset-class coverage, the list/get endpoints, 404 for an
unregistered symbol) and two new sizing tests in `tests/test_risk_and_execution.py` (MCX lot-size
flooring differing from the tenant default, and crypto sizing to a sub-1 fractional quantity) -
plus a `CoinDCXBroker` case added to the existing parametrized stub-adapter test.

## Conversational (Rule-Based) Strategy Builder

Lets a user describe a strategy in plain English and get a pre-filled Strategy Builder form to
review and edit, instead of composing every condition by hand in the dropdown editors from
scratch. This is a **deterministic, rule-based parser**
(`app/strategy_engine/nlu_parser.py::parse_strategy_description`) - not a call to an external AI
provider. No AI-provider credentials exist in this deployment, and calling a real LLM on a
trading-strategy description sight-unseen would be exactly the kind of "present a guess as a
fact" behavior the platform refuses to do everywhere else (see the NSE provider, the fundamentals
module's mandatory citations, and the News & Event engine above). Instead it recognizes a fixed,
documented set of phrasings and turns them into the exact same `Condition`/`Operand`/
`CustomStrategyConfig` building blocks the no-code Strategy Builder already produces.

- **Recognizes**: entry direction ("buy"/"go long"/"long when" vs. "sell"/"go short"/"short
  when", with "and"-joined clauses); indicator conditions with a period as `RSI(14)`, `RSI 14`,
  or `14 RSI`/`14-period RSI` (omitted, it defaults the same way `Operand` itself does);
  comparisons (`crosses above`/`crosses over`, `crosses below`/`crosses under`, `>`/`above`/
  `greater than`/`over`, `<`/`below`/`less than`/`under`, `>=`, `<=`); and risk parameters as
  their own sentences anywhere in the text (`<N>x ATR stop loss`, `ATR period <N>`, `target risk
  reward of <N>` with an optional `to <M>`, `minimum risk reward of <N>`, `<N>min`/`<N> minute`
  timeframe).
- **Never fabricates a condition it isn't confident about.** Any clause or sentence that doesn't
  match a recognized pattern is reported back verbatim as a warning (`Could not understand
  condition: "volume spikes"`) rather than silently dropped or guessed at - the same "tell the
  user exactly what was and wasn't understood" honesty this platform applies to data citations.
  `ParsedStrategyPreview` mirrors `CustomStrategyConfig` field-for-field but skips its "at least
  one condition" validation, since an all-warnings parse is still a valid (empty) preview to show
  the user, not a server error.
- **`POST /api/custom-strategies/parse`** (no auth needed - a pure function of its input, same as
  `/backtest`) takes `{text, name}` and returns `{config, interpreted, warnings}` -
  `interpreted` is a human-readable line per thing it understood (reusing `Condition.label()`,
  the same formatting the condition editors already display), so the user sees precisely what
  each piece of their sentence became before it touches anything. Nothing is persisted here;
  saving still goes through the existing `POST /api/custom-strategies` once the user is happy
  with the (fully editable) result.
- Frontend: a new "Describe your strategy in plain English" panel at the top of
  `StrategyBuilderPage.tsx` - a textarea, a Parse button, an "Understood as" list and a "Not
  understood" list, and a "Load into builder below" button that populates the exact same
  condition-editor components (`ConditionListEditor`/`ConditionEditor`/`OperandEditor`, reused
  from the Market Scanner work) already used to build a strategy by hand - so a parsed condition
  is not a read-only preview, it's a fully editable starting point.
- Verified live end-to-end with Playwright: parsed a multi-sentence description covering both
  entry directions, all four risk parameters, and one deliberately unparseable clause; confirmed
  the "understood"/"not understood" split rendered correctly; loaded it into the real condition
  editors (confirmed the dropdowns were populated, not just displayed as text); and saved it
  through the unmodified save path (`201 Created`).
- One real bug found and fixed during development, before any test was written against it: the
  initial sentence splitter split on every `.`, which silently corrupted decimal numbers (e.g.
  `"1.5x ATR"` split into `"1"` and `"5x ATR"`, misparsing the multiplier as `5.0`). Fixed by only
  splitting on a period not immediately followed by a digit - a decimal point's next character is
  always a digit, while a sentence-ending period is always followed by whitespace or the end of
  the text.

Full backend suite: 377 passing (up from 367), including new `tests/test_nlu_parser.py` (explicit
and default indicator periods, both period-before and period-after syntax, all four risk
parameters across separate sentences, the decimal-number sentence-splitting edge case, an
unrecognized clause producing a warning instead of a fabricated condition, and the `/parse`
endpoint itself - including that it never persists anything and never errors on fully
unparseable input).

## Phase 1 Production Hardening (master-prompt Sections 47-55)

The platform's own master specification lays out a phased roadmap (its Section 62) that
explicitly forbids starting a later phase before the previous phase's Backtest→Paper→Sandbox→Live
pipeline is fully validated and production-hardened for one asset class - "no phase skips that
gate just because the underlying engine is 'the same code.'" MCX/crypto support (`##
Multi-Asset...` above) and the conversational strategy builder were both built ahead of that gate
being closed. This section is the work to close it: go back and harden Phase 1 (NSE F&O core)
against the spec's own Sections 47-55 (compliance, security, reliability/observability, testing,
CI/CD, disaster recovery, data governance, performance, documentation) before resuming later
phases.

### Backtest-vs-live exit-logic parity (Section 50/13)

Found while writing the parity test Section 50 explicitly calls out as missing ("the test that
actually enforces Section 13's 'same DSL everywhere' requirement; without it, that requirement
silently rots"): `app/backtest/engine.py`'s per-bar exit check and `app/trading/exit_logic.py`'s
`check_exit` (the live/paper path) were two independently written copies of the same stop-loss/
target2/target1 priority rule, linked only by a comment claiming they matched - nothing enforced
that claim, so a future change to either could have silently drifted from the other without any
test catching it.

Fixed by extracting the priority rule into one function, `determine_exit_price(direction,
stop_loss, target1, target2, low, high)` in `app/trading/exit_logic.py`, and making both callers
pure delegations to it:
- `check_exit(trade, current_price)` calls it with `low == high == current_price` (a live/paper
  feed only ever has one price at a time).
- `run_backtest`'s per-bar loop calls it with the bar's actual `low`/`high` range (a backtest
  knows the full range a bar traded through).

`tests/test_exit_logic.py` (new, 21 tests) is the parity suite: direct coverage of
`determine_exit_price` for every direction/priority combination (including the same-bar-crosses-
both-targets case, where target2 must win), tests proving `check_exit` returns exactly what
`determine_exit_price` returns for the same inputs, and tests driving the real `run_backtest`
through engineered OHLCV bars and asserting its trade outcome equals `determine_exit_price`
called directly on the same bar - i.e. the backtest engine is proven to have no exit-priority
logic of its own left to drift.

Full backend suite: 398 passing (up from 377), refactor is behavior-preserving (the pre-existing
`tests/test_backtest.py` suite, including the target2-priority regression test, passes unchanged).

### Tamper-evident hash-chained audit logs (Section 48)

`AuditLogRecord` (register/login, broker credentials stored/deleted, broker authenticated/
failed, kill-switch engaged/disengaged, emergency exit, position-reconciliation mismatches, ...)
previously had no integrity guarantee beyond normal row-level DB access control - anyone with
write access to the table (a compromised app server, a rogue DB admin, a bad migration) could
edit or delete a row with nothing to detect it. Master prompt Section 48 calls for a
tamper-evident hash chain specifically so that this class of tampering is *detectable* after the
fact, independent of trusting whoever currently holds DB access.

Added `app/audit/log.py`: every row's `hash` is a SHA-256 of its own fields plus the previous
row's `hash` (or the literal `GENESIS` for the very first row ever written), forming one chain
across the whole table in `id` order. `write_audit_log()` is now the *only* sanctioned way to
create an `AuditLogRecord` - every previous direct `session.add(AuditLogRecord(...))` call site
(auth, brokers, kill-switch, reconciliation routes) was migrated to it, so there is no way left in
the codebase to write an audit row outside the chain. `verify_audit_chain()` recomputes every
row's hash from its stored fields and reports the first row where it (or its `prev_hash` link)
disagrees - altering, deleting, or splicing in a row anywhere in the table's history breaks every
hash from that point forward. `GET /api/audit-logs/verify` (SUPER_ADMIN-gated, since the chain
spans every tenant) exposes this as a platform operator's tamper check.

Migration `c81f4e2a9d36` adds the two columns and backfills a real hash chain for whatever rows
already existed before it ran (verified directly against a real Postgres instance seeded with
genuine pre-existing rows from earlier dev/testing sessions: the backfilled chain reads back as
intact via `verify_audit_chain`, a live write after the migration correctly extends it, and a
downgrade/upgrade round trip plus `alembic check` both come back clean).

One real bug found and fixed while writing the tests, not by inspection: the first version of
`verify_audit_chain` intermittently reported an intact two-row chain as broken. Root cause -
SQLite's `DateTime(timezone=True)` does not reliably round-trip tzinfo; reading a just-inserted
row back within the same still-open transaction (exactly what `write_audit_log`'s "fetch the last
row" query does for every write after the first) came back tz-naive, while the in-memory object
used to *compute* that same row's hash was tz-aware - two different `.isoformat()` strings for the
same instant, so the recomputed hash never matched the stored one. Fixed by normalizing every
timestamp to UTC-naive (`_normalize_timestamp` in `app/audit/log.py`) before it ever goes into a
hash, so the hash is stable regardless of which way a given read happens to come back. The
existing v1 limitation of computing the "previous row" without a DB-level lock (a real concern
only under genuinely concurrent writers on Postgres, never hit by this single-threaded test suite)
is called out directly in that module's docstring rather than silently assumed safe.

Full backend suite: 403 passing (up from 398), including new `tests/test_audit_log_chain.py` (a
valid chain, a row correctly chaining off whatever the actual previous hash is, the SUPER_ADMIN
gate on the verify endpoint, the endpoint reporting an intact chain, and - run deliberately last in
the file, since it permanently corrupts the shared test database every other test in the file and
in `tests/test_audit_logs_api.py` reads from - that tampering with a row's `detail` after the fact
is actually detected, at the exact row that was changed).

### Per-IP rate limiting on auth endpoints (Section 48)

`/api/auth/register` and `/api/auth/login` previously accepted unlimited attempts from a single
caller - exactly the two endpoints a registration-spam or credential-stuffing/brute-force attack
actually targets. Added `app/core/rate_limit.py::rate_limit(name, limit, window_seconds)`, a
dependency factory wrapping a per-`(name, caller IP)` sliding-window counter, applied as
`register_rate_limit`/`login_rate_limit` (10 requests/60s each, independent counters) in
`app/auth/routes.py`.

Documented v1 limitations rather than hidden: it's in-process (a real multi-instance deployment
needs a shared store - Redis is already optional infra here, see `app/cache/client.py` - to hold a
limit across instances), and it trusts `request.client.host` directly rather than an
`X-Forwarded-For` header (trusting a client-settable header without validating it came from a
known reverse proxy would make the limiter trivially bypassable - a real deployment behind a
proxy should configure `request.client.host` to already be correct, e.g. Uvicorn's
`--proxy-headers`).

Both limiter dependencies are module-level names specifically so tests can target them via
`app.dependency_overrides` - every request in the shared test suite's `TestClient` comes from the
same fake IP, so leaving the real limiter active would rate-limit the test suite itself long
before any individual test's request count. `tests/test_auth_api.py` disables both by default for
the whole suite; new `tests/test_rate_limiting.py` is the one place that re-enables them (against
separate `TestClient` instances with their own distinct fake IPs, so it can't be starved by every
other test's shared-IP traffic) to prove the 429 behavior itself: the 11th request in a window is
rejected, login and register are limited independently, and two different IPs are never
cross-blocked.

Full backend suite: 406 passing (up from 403).

### Structured, correlated logging on the Signal -> Risk -> Order pipeline (Section 49)

No part of the codebase used Python's `logging` module before this - the only trace of a
pipeline run was the DB rows themselves (`OrderRecord`/`OrderEventRecord`), fine for after-the-
fact auditing but useless for an operator grepping/alerting on live log output, and with no way to
tell which log line belongs to which tenant/strategy/order without re-deriving it from the DB.

Added `app/core/logging_config.py`: a `contextvars`-based correlation context
(`bind_log_context(tenant_id=..., strategy_id=..., signal_ref=...)` as a context manager,
`update_log_context(order_id=...)` to add fields to whichever context is currently active), a
`logging.Filter` that copies the active context onto every `LogRecord`, and a `JsonFormatter` so
every log line is one structured JSON object instead of free text. `configure_logging()` wires
this into the root logger at app startup (`app/main.py`'s lifespan).

There is no dedicated `signal_id` anywhere in this codebase - `Signal` is a transient, in-memory
Pydantic value with no DB identity on the paper/live execution path - so `signal_ref`
(`"{symbol}@{timestamp}"`) is used instead as the best available stand-in, documented as such in
the module's own docstring rather than silently treated as equivalent to a real id.

`app/execution/signal_execution.py::execute_signal_for_user` (the actual Signal -> Risk -> Order
pipeline every paper/live execution goes through) now wraps its whole body in
`bind_log_context(tenant_id=user.tenant_id, strategy_id=strategy_id, signal_ref=...)`, adds
`order_id` via `update_log_context` the moment the order exists, and logs at every meaningful
transition (order created, kill-switch rejection, risk-engine rejection, filled, position opened).
`app/execution/order_persistence.py::transition_order` logs every state-machine transition at
DEBUG - since it reads the same contextvar, every transition it logs automatically inherits
whatever tenant/strategy/order context the caller already bound, with no need to pass those ids
into every individual log call by hand.

Full backend suite: 412 passing (up from 406), including new `tests/test_logging_config.py`:
direct unit tests of context binding/nesting/restoration and the JSON formatter, plus one
end-to-end test that runs a real paper-execute call through the API, attaches a collecting log
handler with the correlation filter to the actual execution/persistence loggers, and asserts every
captured record carries that specific request's own `tenant_id`/`strategy_id`/`order_id` - not
just that some logging call happened somewhere.

### CI dependency and container scanning (Section 48/51)

CI (`.github/workflows/ci.yml`) previously only ran the test suite, an Alembic migration apply,
and a schema-drift check - no step ever checked whether a pinned dependency or a built container
image actually had a known vulnerability. Running `pip-audit` against `backend/requirements.txt`
for the first time surfaced *real, currently-known* CVEs, not hypothetical ones: 13-15 advisories
across `cryptography` (multiple, fixed only as of 50.0.0) and `pytest` (fixed at 9.0.3) that the
existing version caps (`cryptography<46.0`, `pytest<9.0`) were still exposed to. Fixed by widening
`requirements.txt` to `cryptography>=50.0.0,<51.0` and `pytest>=8.0,<10.0` (and `cffi>=1.16,<3.0`,
since `cryptography>=50` requires `cffi>=2.0` - the old `<2.0` cap made the upgrade
un-installable) - re-running `pip-audit` afterward reports zero known vulnerabilities, and the
full 412-test suite passes unchanged against the upgraded versions (installed and run directly,
not just resolved on paper).

Added to CI:
- **`pip-audit -r requirements.txt`** (backend job) - blocking, since it's now proven to catch
  real issues, not just theoretical ones.
- **`npm audit --omit=dev`** (frontend job) - blocking on production dependencies, which audit
  clean today. A separate `npm audit` (including dev dependencies) runs as report-only
  (`|| true`): it currently flags a moderate/high advisory in `esbuild`/`vite`'s dev server only
  (never shipped to production), whose only fix is a breaking Vite major-version upgrade - a
  framework-migration decision deliberately left for its own discussion rather than forced
  silently by a CI dependency bump.
- **`container-scan` job** - builds both `backend/Dockerfile` and `frontend/Dockerfile` and scans
  each image with Trivy (`aquasecurity/trivy-action`, failing on CRITICAL/HIGH findings that have
  a known fix, `ignore-unfixed: true` so an unfixable base-image issue can't block every build).

Honest limitation: the `container-scan` job's build-and-scan steps could not be exercised in this
sandbox - its network egress policy blocks Docker Hub/CDN image pulls entirely (`docker build`
fails immediately trying to pull `python:3.13-slim`, independent of the pre-configured HTTPS
proxy), so this specific job needs to be watched on its first real run in GitHub Actions (which
has normal internet access) rather than being claimed as verified here. The YAML itself was
validated to parse correctly, and the `pip-audit`/`npm audit` steps were run for real against the
project's actual dependency files with the results described above.

### Idempotency race, DSL/webhook fuzzing, and disaster-simulation tests (Section 50)

Three real bugs found and fixed while building out the test categories Section 50 explicitly
calls out as missing, each confirmed directly (not just reasoned about) before being fixed:

- **Idempotency race under real concurrency.** The existing idempotency-key check (`main.py::
  paper_execute`, `app/webhooks/routes.py::tradingview_webhook`) reads "does an order already
  exist for this key?" before either request commits - a classic TOCTOU race. Running two
  concurrent `execute_signal_for_user` calls sharing one idempotency key with `asyncio.gather`
  reliably crashed with an unhandled `sqlalchemy.exc.IntegrityError` on the `(tenant_id,
  idempotency_key)` unique constraint. Fixed in `create_order` (`app/execution/
  order_persistence.py`): it now catches that IntegrityError and returns the row that actually
  won the race (`(order, was_newly_created=False)`) instead of raising, and
  `execute_signal_for_user` replays that order's already-decided outcome
  (`execution_result_from_order`) rather than re-running the settled order through the pipeline a
  second time. Verified under genuine 8-way concurrent load against a real Postgres instance:
  exactly one order created, zero crashes (versus reliably crashing before the fix). Fixing this
  surfaced a *second* bug in the fix's own first draft: reading `user.tenant_id` after
  `session.rollback()` (rollback expires every loaded object) raised `MissingGreenlet` - fixed by
  capturing `tenant_id` into a local variable before any DB operation runs. Also fixed in the same
  pass: the kill-switch-rejection branch never set `order.reasons_json`, so a race-detected replay
  of a kill-switch-rejected order would have replayed with an empty reasons list instead of the
  real rejection reason.
- **DSL parser fuzzing.** New `tests/test_nlu_parser_fuzz.py` (Hypothesis, ~750 generated
  examples across arbitrary text, high-Unicode/emoji input, and the exact decimal-vs-sentence-
  boundary character classes the parser's own sentence-splitter was already fixed for once) -
  `parse_strategy_description` never raised on any generated input. No bug found here; the
  parser's existing "never fabricate, always warn on the unrecognized" design held up under fuzz
  pressure, not just the hand-picked examples in `tests/test_nlu_parser.py`.
- **Webhook schema fuzzing.** New `tests/test_webhook_fuzz.py` (Hypothesis, ~250 generated
  examples: every field replaced with an arbitrary JSON scalar/collection, and the whole body
  replaced with something that isn't even a dict) posted against the real `/api/webhooks/
  tradingview/{token}` endpoint - every response was a clean 200 or 422, never a 500. No bug found
  here either; `TradingViewAlertPayload`'s Pydantic validation already rejects malformed input
  cleanly, and the existing `entry != stop_loss` guard on the risk_reward division already
  prevents a divide-by-zero.
- **Disaster simulation: kill broker mid-fill.** `OrderRouter.execute`'s live-order path
  (`await self.broker.place_order(...)`) had no exception handling at all - a broker call that
  errors mid-flight (network failure, timeout, the broker's own outage) would propagate all the
  way up as an unhandled exception, leaving the order stuck at `RISK_CHECK` forever: never
  REJECTED, never FAILED, invisible to any "list my pending orders" query, and the caller's
  request ending in an unhandled 500. Fixed by catching the exception and returning a new
  `ExecutionResult.system_failure=True` flag, distinguishing "the broker explicitly declined this
  order" (REJECTED, a business decision) from "the broker call itself failed" (FAILED, a system
  failure notified as one via the existing `SYSTEM_FAILURE` notification type). `RISK_CHECK ->
  FAILED` was added to the order state machine's allowed transitions (`app/execution/
  order_state_machine.py`) specifically to make this reachable - it was not legal before this fix,
  which is exactly why the order used to get stuck rather than ever reaching FAILED on its own.
  New `tests/test_disaster_simulation.py` covers a broker exception, a broker timeout specifically,
  confirms an ordinary broker rejection is *not* misclassified as a system failure, and confirms
  the new state transition is narrowly scoped (RISK_CHECK still can't jump straight to
  POSITION_OPEN). At the time of this fix LIVE mode was not yet wired into the shared
  `execute_signal_for_user` pipeline; it is now (Phase A4 below), and this behaviour carries over
  unchanged: a broker exception mid-placement still ends as FAILED + SYSTEM_FAILURE.

`hypothesis>=6.100,<7.0` added to `requirements.txt` for the two fuzz test files above - `pip-audit`
re-checked clean with it installed.

Full backend suite: 429 passing (up from 412).

### Disaster recovery & data governance runbooks (Section 52/53)

New `docs/OPERATIONS.md`: RPO/RTO targets, backup/restore-verification procedure, a crash-recovery
runbook for open positions (what already makes this safe by construction - durable DB state,
reconciliation, the order state machine's non-terminal statuses making a stuck order visible -
versus the one real gap: no broker-side GTT stop-loss failsafe yet for LIVE mode), a "platform down
during market hours" runbook, and a data-governance section (classification, the already-real
immutable/hash-chained audit trail, retention/deletion targets under the DPDP Act and SEBI's 5-year
rule, and a data-lineage requirement for the future real AI strategy builder). Written honestly as
a mix of what's already true in code today versus target procedures for whoever stands up the
first real production deployment - this environment has no real infrastructure to exercise these
against, so nothing here claims to have been drilled for real.

## Phase A: Autonomous Trading Core

Until this phase the platform was a console: every signal, paper fill and position check
happened because someone clicked a button in a browser, on client-supplied candles, and LIVE mode
was reachable only by constructing `OrderRouter` by hand in a test. A business-grade platform has
to trade *on its own*, on *real* market data, for *every* tenant, while every browser is closed -
and it has to stay safe when a token expires at 03:30, a broker call times out mid-fill, or the
process dies with a live position open. Phase A is that core, built in nine committed parts:

| Part | What landed | Where |
|------|-------------|-------|
| A1 | Schema: `strategy_deployments`, `worker_heartbeats`, `market_holidays` (seeded with NSE circular CMTR71775 for 2026), token lifecycle columns on `broker_credentials`, `broker_order_id`/`sl_order_id`/`deployment_id` on `trades` | `alembic/versions/d2a7c9e4f581_*`, `app/db/models.py` |
| A2 | Broker-backed market data (recent history + today's intraday, 60s Redis cache shared across tenants, resampled to every strategy timeframe anchored at 09:15) and the IST session calendar | `app/market_data/service.py`, `app/market_data/calendar.py`, `BrokerInterface.get_intraday_candles/get_ltp_for_symbol`, Upstox overrides |
| A3 | Daily token lifecycle (VALID/EXPIRED/UNKNOWN with expiry at each broker's cut-off), `verify_token` against the broker, Upstox OAuth start/callback, `GET /api/broker/token-status`, `place_stop_loss_order` (SL-M) | `app/brokers/token_lifecycle.py`, `app/brokers/routes.py`, `app/brokers/base.py` |
| A4 | LIVE through the shared pipeline: real fill price from the order book, protective SL-M placed on every live fill, ids persisted on the trade, LIVE-without-broker REJECTED on the order trail | `app/execution/router.py`, `app/execution/signal_execution.py`, `app/trading/persistence.py` |
| A5 | One close path for paper and live (`close_position`), broker square-off that never double-exits a stop that already fired, tenant-wide open-position sweep; mark-price and emergency-exit now route through it | `app/trading/position_monitor.py` |
| A6 | The worker process: session gate, token gate, exits before entries, 15:00 entry cut-off, 15:15 square-off, one entry per signal bar, auto-pause on repeated failures, Redis replica lock, heartbeat | `app/workers/trading_worker.py`, `app/workers/routes.py`, `docker-compose.yml` (`worker`) |
| A7 | Deployments API (strict creation, pause/resume/stop/delete, audited, SUPPORT read-only) and market-holidays admin API | `app/deployments/routes.py`, `app/market_data/routes.py` |
| A8 | Autopilot tab, broker session banner with one-click Upstox login, LIVE confirmation, Settings session-health card, Dashboard heartbeat | `frontend/src/pages/DeploymentsPage.tsx`, `components/BrokerTokenBanner.tsx` |

### One worker cycle (every `WORKER_CYCLE_SECONDS`, default 60)

```
acquire Redis lock (fail-open)                     app/cache/client.py::cache_acquire_lock
market_session_status(now)                         app/market_data/calendar.py
  closed -> heartbeat only
for each tenant with ACTIVE/PAUSED deployments:
  acting user = deployment creator (or first tenant user)
  for each broker the tenant needs: verify_token   app/brokers/token_lifecycle.py  (TOKEN_EXPIRED alert on rejection)
  no usable broker -> record last_error on every deployment, skip tenant (auto-resumes after re-login)
  MarketDataService(broker)
  >= 15:15 IST ? square off every open position   app/trading/position_monitor.py::close_position
              : monitor_open_positions (LTP -> check_exit -> close_position)
  for each ACTIVE deployment:
    resolve_strategy -> get_frames (cached candles, resampled) -> has_enough_history?
    strategy.analyze -> NO_TRADE? done
    same signal bar as last time? / open position for this deployment? / after 15:00? -> skip
    LIVE with no usable token -> skip with reason
    execute_signal_for_user(mode, broker, deployment_id, idempotency_key="deployment:<id>:<bar ts>")
    failure -> consecutive_failures++, PAUSED + SYSTEM_FAILURE after 5
heartbeat row (cycle_count, last_cycle_ms, last_error)  -> GET /api/system/worker-status
release lock
```

Everything a deployment does goes through the *same* `execute_signal_for_user` the console's
paper-execute and the TradingView webhook use: kill switches, the tenant's saved risk limits,
the order state machine, idempotency, notifications. There is no second execution path for the
robot.

### Design decisions worth flagging

* **Worker is its own process, never a thread in the API.** The previous single-user bot this
  platform replaces ran its engine inside the Streamlit UI process; a browser tab closing stopped
  trading. Here the API can restart, scale, or be down entirely and the worker keeps trading;
  `worker_heartbeats` is how anyone (dashboard, ops alerting) knows it is alive.
* **Tokens are a first-class state, not a string.** Indian retail brokers issue a daily-expiring
  token and no refresh token. `token_status`/`token_expires_at` plus `verify_token` make "is the
  session good right now" a fact the LIVE gate checks, and a rejection raises one CRITICAL
  `TOKEN_EXPIRED` notification (not one per cycle). Upstox's login is driven end-to-end through
  OAuth (`/oauth/start` -> Upstox dialog -> `/oauth/callback`, bound to the tenant by a signed
  10-minute state) so the daily routine is one click, not copying a code out of an address bar.
* **Paper still needs a broker.** Paper deployments consume real broker candles, so a tenant with
  no stored broker cannot deploy even in PAPER - by design: paper trading on synthetic candles
  proves nothing about the strategy.
* **Broker-side stop with every live fill.** `place_stop_loss_order` (SL-M, opposite side, at the
  signal's stop) closes the gap `docs/OPERATIONS.md` 1.3.4 used to flag. A failed stop never
  undoes the real fill; it raises CRITICAL and the software stop still applies. On exit the
  monitor checks whether that stop already fired before placing anything, because a second exit
  order against an already-flat position would open a reverse one.
* **Intraday discipline is enforced, not advised.** No entries after 15:00 IST, everything
  flattened at 15:15 IST (before brokers' own forced MIS square-off), weekends and the
  `market_holidays` table are closed days. Muhurat/special sessions are deliberately not traded.
* **Exact fills where the broker tells us, honest fallbacks where it doesn't.** Entry and exit
  prices come from the broker order book's `average_price` when available (short retry while a
  market order is pending) and fall back to the signal level otherwise; reconciliation is where
  the fallback gets corrected. Live charges are the same NSE-intraday estimate paper uses until
  the contract note is ingested.
* **One replica unless Redis is present.** The cycle lock fails open when Redis is unreachable so
  a Redis outage never stops the one worker that is running; two replicas without Redis *would*
  double-trade, which `docs/OPERATIONS.md` states plainly.
* **Timestamps persisted in UTC.** SQLite (the test DB) drops tzinfo; storing an IST-aware
  `last_signal_at` there read back as a UTC wall time 5.5h in the future and suppressed the next
  entry. Found by the worker tests, fixed by normalising before every write.

### What is and is not verified

* All of the above is covered by tests against the in-memory DB with fake brokers
  (`tests/test_market_calendar.py`, `test_market_data_service.py`, `test_token_lifecycle.py`,
  `test_live_execution.py`, `test_position_monitor.py`, `test_trading_worker.py`,
  `test_deployments_api.py`, plus Upstox adapter tests in `test_brokers.py`); the A1 migration
  was round-tripped (upgrade, `alembic check`, downgrade, upgrade) on a real Postgres; one real
  worker cycle ran against Postgres and wrote its heartbeat; the frontend was type-checked, built,
  and screenshot-verified against the running API. Full suite: 521 passing.
* **Not yet verified against a real Upstox account.** The Upstox intraday/LTP/order-book/OAuth
  calls follow the public v2 API documentation and are exercised only through mocked HTTP. The
  first real run must be a PAPER deployment with real Upstox credentials entered in Settings
  (never in chat or `.env`), watched for a full session, before any LIVE deployment - see
  `docs/OPERATIONS.md` 1.5.
* Not built in this phase (tracked for later phases): tenant plans/seat limits, email/SMS
  delivery for the CRITICAL alerts the worker raises, contract-note ingestion for exact live
  charges, options-specific deployments (strike selection), and a Zerodha login flow equivalent
  to the Upstox OAuth one (Kite's redirect flow is structurally the same and can reuse the state
  helper).

## Phase B: Business Layer

### B0: Out-of-app alert delivery (Telegram + email)

The autonomous worker raises CRITICAL notifications - `TOKEN_EXPIRED` at 03:31, a live fill whose
protective stop failed, a deployment auto-paused, the daily loss limit hit - precisely when no
browser is open to show them. `app/alerts/` gets them to a phone or inbox:

* **Channels** (`alert_channels`, one per tenant per type): a Telegram bot + chat id, or an SMTP
  mailbox. Config is Fernet-encrypted like broker credentials; the API returns a masked summary
  (`bot_token_hint`, `password_set`) and an update that leaves a secret blank keeps the stored one.
  `min_severity` (default WARNING) is the floor - routine ENTRY/EXIT stays in-app.
* **Outbox** (`alert_deliveries`): `notify()` now also writes one PENDING row per matching channel
  in the same transaction. It never touches the network, so a dead SMTP server cannot slow or
  fail the pipeline that raised the alert.
* **Dispatcher** (`app/alerts/dispatcher.py::dispatch_pending`): the trading worker drains due
  rows every cycle, market open or not. Failures retry with exponential backoff (30s, 60s, 120s,
  ...) up to 5 attempts, then the row is FAILED with the error kept; each row commits on its own
  so one broken channel never delays another. Telegram goes through `sendMessage` with HTML
  (escaped); email through stdlib `smtplib` in a thread (STARTTLS + optional login).
* **API**: `GET/PUT/DELETE /api/alert-channels/{telegram|email}`, `POST .../test` (sends a probe
  right now and returns the exact failure text), `GET /api/alert-channels/deliveries` (the outbox
  - "was that CRITICAL actually sent?"). Writes are audited; SUPPORT is read-only.
* **UI**: Settings -> "Alert delivery" card (both channels, floor, enable, Save / Send test /
  Remove, recent deliveries with status and error).

Verified by `tests/test_alerts.py` (enqueue by severity floor, masked secrets, secret retention on
update, Telegram HTML payload via mocked HTTP, SMTP via a stubbed sender, backoff then FAILED,
test-send error surfacing, worker cycle draining the outbox) and a Postgres round-trip of the
`e3b8d1c7a942` migration. Not verified against a real Telegram bot or SMTP server from this
environment (outbound blocked); the "Send test" button is there for exactly that first check.

### B1: Multi-user tenants (team, roles, invitations)

Until now one signup was one tenant with one user. A business account needs an owner, several
traders, someone who only builds strategies, and someone from finance who must see everything and
touch nothing. `app/team/` and the auth additions provide that:

* **Roles** (`UserRole`): `OWNER` (registration creates one; manages the team and everything a
  trader can), `USER` (trader), `STRATEGY_CREATOR`, `VIEWER` (tenant read-only), `SUPPORT`
  (platform support staff, read-only), `SUPER_ADMIN` (platform-wide). Two shared gates in
  `app/auth/dependencies.py` - `require_trader` (OWNER/USER/STRATEGY_CREATOR) on every endpoint
  that places, configures or stops trading or touches broker/alert credentials, and
  `require_owner` on team management. Reads stay on `get_current_user`, so a VIEWER's console is
  fully populated and every write button returns 403.
* **Invitations** (`tenant_invites`): an owner creates one for an email + role and gets a link
  (`FRONTEND_URL?invite=<token>`) to share; only a SHA-256 of the token is stored, links expire
  after 48h, are single-use, and re-inviting an email revokes the previous link. The invitee
  lands on the Account tab, sees who invited them and as what, chooses a password, and joins
  *that* tenant (`POST /api/auth/invite/{token}/accept`, rate-limited like register). OWNER is
  never an invitable role - a leaked link cannot mint an owner; owners are promoted from existing
  members.
* **Membership** (`/api/team/members`): role changes and removal are owner-only and audited.
  Removal deactivates (`users.is_active = false`) rather than deletes, so trades, orders and audit
  rows stay attributed; the removed user's existing JWT stops working on the next request and
  login is refused. A tenant always keeps at least one active owner (the last owner cannot be
  demoted or removed, and cannot remove themselves).
* **Per-user notification read state** (`notification_reads`): one teammate reading a CRITICAL
  alert no longer clears it for the others; `read-all` marks only the caller's copy.
* **Acting user for headless paths**: TradingView webhooks and the worker attribute orders to the
  deployment's creator when active, else the tenant's earliest active OWNER.
* **Migration `f4c2a9e1b753`** adds the tables and `users.is_active`, promotes each existing
  tenant's first USER to OWNER (every pre-existing tenant is single-user, so this is exactly its
  owner) and carries old `notifications.read_at` marks over as that user's read rows.
* **UI**: Team tab (System group) with invite creation + copyable link, pending invites, member
  list with role select / remove / reactivate, organisation rename; role badge in the sidebar;
  Account tab handles `?invite=`.

Verified by `tests/test_team_api.py` (owner on registration, invite -> accept into the same
tenant with the invited role, hashed single-use expiring tokens, re-invite replacing, revoke,
owner-only management, last-owner protection, removal killing tokens and logins immediately,
VIEWER refused on eight write endpoints, per-user read state, webhook attribution) and a Postgres
round-trip of the migration. Invite links are shared by the owner (copy button) - emailing them
automatically is deliberately left for when the platform has its own transactional email sender
(the per-tenant alert SMTP channel is the tenant's mailbox, not the platform's).

### B2: Plans, limits and tenant status

`Tenant.plan` and `Tenant.status` existed since the multi-tenancy foundation but nothing read
them. Now they mean something:

* **Plan catalogue in code** (`app/plans/registry.py`): `free` (2 active deployments, paper only,
  3 custom strategies, 1 member, 1 alert channel), `pro` (10 / LIVE / 25 / 5 / 2), `business`
  (50 / LIVE / 200 / 25 / 2). Plans are code, not rows, because their limits are business rules
  that change with releases and deserve review; an unknown plan id resolves to `free`, so a DB
  typo can only restrict, never unlock live trading.
* **Enforcement where the limit would be exceeded** (`app/plans/limits.py`, HTTP 402 with a
  message naming the limit, the usage and what to do): deployment create (slot + LIVE
  entitlement) and resume (LIVE entitlement only - a PAUSED row already holds its slot), custom
  strategy create, invite create (active members + open invites), alert channel create (updates
  are always allowed). STOPPED deployments free their slot.
* **Suspension** (`status = suspended`): `require_trader`/`require_owner` now also refuse a
  suspended organisation (403), so every trading and configuration write stops while reads keep
  working - its people can still see positions and history. Belt and braces, the execution
  pipeline REJECTs any order for a suspended tenant (webhooks included), and the worker keeps
  monitoring exits but takes no entries, recording why on each deployment.
* **Entitlement re-checked at fire time**: a LIVE deployment on a tenant whose plan was later
  downgraded is skipped by the worker (and refused by the pipeline) - a plan change takes effect
  on the next cycle, not the next deployment.
* `GET /api/team/tenant` returns plan, limits and usage; the Team tab shows usage-vs-limit bars
  and whether live trading is included. Changing plan/status is a SUPER_ADMIN action (B3).

Verified by `tests/test_plans.py` (fallback, usage, deployment cap with pause/stop semantics, LIVE
refused on free and allowed on pro, downgrade blocking resume, strategy/member/channel caps,
suspended tenant read-only at the API, rejected in the pipeline, skipped by the worker).

### B3: Platform admin console (SUPER_ADMIN)

* **Bootstrap** (`app/admin/bootstrap.py`): `SUPER_ADMIN_EMAILS` (comma-separated) in the
  environment. Those users are promoted at startup and on registration; nothing is ever demoted
  automatically and no tenant-facing UI or API can grant the role.
* **API** (`/api/admin/*`, `require_role()` = SUPER_ADMIN only): `overview` (tenants by
  status/plan, users, active/LIVE deployments, open/LIVE positions, global kill switch, worker
  heartbeat), `tenants` (search by name or member email; owners, members, deployments, open
  positions per tenant), `tenants/{id}` (usage vs limits, users, brokers' token status,
  deployments, tenant kill switch), `PATCH tenants/{id}` (plan and/or status with a reason -
  written to the *tenant's* audit trail naming the admin, and delivered to the tenant as a
  notification, CRITICAL on suspension), `audit-logs` (platform-wide, filterable by tenant and
  event), `deployments` (the ops view of everything the worker is running).
* **UI**: an "Admin Console" entry in a Platform nav group that only a SUPER_ADMIN sees:
  overview tiles, global kill switch engage/disengage, searchable tenant table with inline plan
  and status selects (suspension asks for a reason), a tenant detail panel, and the platform
  audit trail.

Verified by `tests/test_admin_api.py` (role gate on every endpoint, env bootstrap at register
and at startup, cross-tenant listing/search, plan/status change audited on the tenant's trail and
notified, validation of plan/status values, detail view contents).

### B4: Per-tenant broker rate budgets and cycle fairness

One worker serves every tenant, and each tenant trades on its own broker API key with its own
published rate limits (Upstox ~25 req/s and 250/min; Kite 3 req/s on quotes/historical). Two
things keep one busy tenant from hurting itself or anyone else:

* **`RateLimitedBroker`** (`app/brokers/rate_budget.py`): every adapter the worker hands to the
  market-data service and the execution pipeline is wrapped so each call first draws a token from
  that tenant's `RateBudget` - a per-second bucket (with a small burst) *and* a per-minute
  bucket, set to roughly a third of the broker's allowance (`BROKER_RATE_LIMITS`) to leave room
  for the tenant's own manual use of the same key. Order placement and cancellation are
  throttled too, deliberately: an exit order that provokes a 429 is worse than one delayed by
  200ms. Budgets are keyed by (tenant, broker) and never shared.
* **Cycle-time fairness**: a tenant may spend at most half a cycle (`MAX_TENANT_SHARE_OF_CYCLE`)
  evaluating entries; deployments that did not get their turn go first next cycle (a per-tenant
  round-robin cursor). A tenant with forty deployments on a slow broker is slowed, never starved,
  and never starves the tenants after it. Exits (the position sweep) run before this budget and
  are never cut short.

Verified by `tests/test_rate_budget.py` (burst then throttle at the per-second rate, per-minute
cap, refill, per-broker limits, full delegation through the wrapper including the Upstox-specific
conveniences, one budget per tenant in a real worker cycle, round-robin across cycles).

## Phase C: Auth Hardening

### C1: Login sessions and rotating refresh tokens

Before this, a login produced one 24-hour JWT that nothing could revoke: a removed teammate, a
stolen laptop or a changed password all had to wait for the token to expire. Now:

* **Every login is a session** (`user_sessions`): the access JWT (15 min by default,
  `ACCESS_TOKEN_MINUTES`) carries the session id, and `get_current_user` checks that session is
  alive on every request. Logout, "log out everywhere", an owner logging a member out, member
  removal (and, from C2, a password change) revoke sessions and take effect on the next request.
  A token without a session id is refused outright.
* **Refresh tokens rotate** (`app/auth/sessions.py`): the client holds an opaque 48-byte token
  whose SHA-256 is the only thing stored; `POST /api/auth/refresh` returns a new pair and keeps
  the previous hash for exactly one step. Presenting an already-rotated token means two parties
  hold the same credential, so the session is revoked rather than guessing which is the real user
  (the legitimate client simply logs in again). Refresh extends the session up to
  `REFRESH_TOKEN_DAYS` (30) of inactivity; refresh is rate-limited per IP.
* **Session management**: `GET /api/auth/sessions` (device, IP, user agent, last use, "this
  device"), `DELETE /api/auth/sessions/{id}`, `POST /api/auth/logout`, `POST /api/auth/logout-all`,
  and for owners `POST /api/team/members/{id}/logout-all`. The Account tab lists sessions with
  revoke buttons and a "Log out everywhere" action; the Team tab has the owner's "log out".
* **Frontend**: the API client stores both tokens, and on a 401 performs one single-flight
  refresh and retries the request, so users never notice the 15-minute access token.

Verified by `tests/test_sessions.py` (both tokens on register/login/invite-accept, session-less
JWT refused, rotation, reuse detection revoking the session, logout invalidating immediately,
logout-all, single revoke and cross-user 404, expiry, owner logout-all and removal, IP/agent
capture, hashed storage) and a Postgres round-trip of migration `a7d3e5f1c208`.

### C2: Password policy, reset and change

* **Policy** (`app/auth/passwords.py`): at least 10 characters, at least two character classes,
  not on a built-in list of the passwords that appear in every breach corpus (with the common
  "+123"/"@123" suffix tricks stripped before comparing), not built from the user's own email.
  Applied on registration, invite acceptance, reset and change, with a 400 that says which rule.
  No forced rotation and no mandatory-symbol theatre: length and a blocklist are what stop
  credential stuffing.
* **Forgot / reset** (`password_resets`): `POST /api/auth/password/forgot` always answers 202
  with the same text (no account enumeration) and is rate-limited per IP. When the user's
  organisation has an email alert channel, the one-hour, single-use link is sent through it to
  the user's own address; otherwise the owner issues one from the Team tab
  (`POST /api/team/members/{id}/reset-link`, shown once). Only the token's SHA-256 is stored.
  Completing a reset sets the password, burns the link, ends every session and logs the user in.
* **Change** (`POST /api/auth/password/change`): requires the current password, applies the
  policy, ends every *other* session and rotates the current one.
* **UI**: "Forgot password?" on the login card, `?reset=` landing on the Account tab, and a
  Change password card when logged in.

Verified by `tests/test_passwords.py` (policy rules, register/invite enforcement, forgot with
identical responses for known/unknown emails, emailed link through the tenant channel with a
mocked SMTP, hint masking, single use, expiry, owner-issued link, change with wrong/same/weak
passwords and other-session invalidation) and migration `b9e4c2d7a316` round-tripped.

### C3: TOTP two-factor authentication

* **Enrolment** (`app/auth/mfa.py`, pyotp / RFC 6238): `POST /api/auth/mfa/enrol` returns a
  secret and `otpauth://` URI (the Account tab renders a QR and the manual key);
  `POST /api/auth/mfa/confirm` turns MFA on only once a code from the app verifies, and returns
  eight backup codes exactly once. The secret is Fernet-encrypted at rest; backup codes are stored
  as SHA-256 hashes and consumed on use; both can be regenerated with a current code.
* **Two-step login**: with MFA on, `/login` answers `mfa_required` plus a five-minute,
  purpose-bound challenge token instead of a session; `POST /api/auth/mfa/verify` with a TOTP or
  backup code starts the session, marked `mfa_verified_at`. Failed codes are audited and
  rate-limited.
* **Step-up**: `POST /api/auth/mfa/step-up` verifies the *current* session (for one that logged in
  before MFA was enabled). The backend refuses step-up-protected actions with a 403 carrying
  `X-Step-Up: mfa`; the UI opens a code prompt and retries.
* **Where it is required**: always for the admin console and the global kill switch (platform
  administrators cannot disable MFA); and, when the owner turns on the tenant policy
  `require_mfa_for_live` (Team tab - the owner must have MFA themselves first), for creating or
  resuming LIVE deployments, storing broker credentials and starting the broker OAuth login. PAPER
  is never gated.
* **Disable** needs the password *and* a current code, so neither a stolen session nor a stolen
  password alone can switch the second factor off.

Verified by `tests/test_mfa.py` (enrol/confirm, two-step login incl. wrong codes and a forged
challenge, backup codes single-use and regeneration, the LIVE/broker step-up under the tenant
policy with the `X-Step-Up` signal, members without MFA told to enable it, admin console and global
kill switch gated, disable rules, secret encrypted at rest) and migration `c5f1a8d3e927`.

### C4: Login protection

* **Every attempt is recorded** (`login_events`: email, user when known, success, reason, IP,
  user agent). Unknown emails are recorded too so per-IP counting sees them; only the user's own
  history is shown to them (System Logs tab), and platform admins get a platform-wide view
  (`/api/admin/login-events`) for spotting a credential-stuffing run.
* **Lockout** (`app/auth/lockout.py`): ten failures for an email in fifteen minutes lock that
  account (423, from any IP, even with the right password) and fifty failures from one IP lock
  that IP; both lift when the failures age out of the window. Failed MFA codes count. Computed from
  the table, so it holds across instances and restarts, unlike the in-process request limiter.
* **New-device alert**: a successful login from an IP + user agent the user has never logged in
  from before raises a WARNING `SECURITY` notification (delivered through the tenant's alert
  channels if the floor allows) telling them to log out everywhere and change the password if it
  was not them. Registration itself is not counted as a device.

Verified by `tests/test_login_protection.py` (recording, per-email lock across IPs with expiry,
per-IP lock, MFA failures counting, new-device alert once per device, admin view) and migration
`d6a2b9f4e158`.


## Phase D: Compliance

### D1: SEBI algo-order tagging

SEBI's retail algorithmic-trading framework (circular of February 2025, in force from August
2025) requires every order an algorithm generates to carry the algo identifier the exchange issued
when the broker registered that algo. Brokers surface this through the free-text order `tag`
(Zerodha, Upstox) or `remarks` (Shoonya) field.

* **Tenant configuration**: `tenants.algo_id` (migration `e7b3c5d1a409`), set by an OWNER on the
  Team tab (`PATCH /api/team/tenant {algo_id}`; 1-32 letters/digits/`-`/`_`, empty string clears,
  every change audited as `tenant_algo_id_changed`). The platform does not register algos - the
  broker does that with the exchange - it only makes sure the issued id reaches every order.
* **One tag builder for every leg**: `app/execution/tagging.py::build_order_tag` composes
  `<algo id>-<strategy>-<leg>` (legs `ENT`, `SL`, `EXIT`) inside the broker's tag limit
  (`BrokerInterface.max_tag_length`, default 20 - Zerodha's documented alphanumeric limit),
  stripping characters brokers reject and shortening only the strategy part: the algo id and the
  leg are what the exchange and reconciliation key on. Entry and protective-stop orders go through
  `OrderRouter` (which now takes `algo_id`), exit orders through the position monitor's
  `_square_off_live`, so no broker order leaves the platform untagged.
* **Stored on the order trail**: `orders.algo_tag` holds the exact tag sent (PAPER orders carry the
  tag they would have had, so the trail is identical in both modes), exposed on `GET /api/orders`.
* **Optional gate**: `ALGO_ID_REQUIRED_FOR_LIVE=true` rejects LIVE orders for tenants without an
  algo id before any broker call (reason on the order trail, no broker interaction) - off by
  default so PAPER-only and pre-registration deployments keep working.

Verified by `tests/test_algo_tagging.py` (tag shaping and limits, router entry/SL tags, tenant
API validation and audit, PAPER and LIVE order rows, the LIVE gate, exit-order tagging).

### D2: Compliance exports

Auditors and regulators ask for records as files for a date range, and want to be able to
show later that the file is the one the platform produced. `app/exports/service.py` builds CSV or
JSON exports of four datasets - `audit-logs` (with each row's `prev_hash`/`hash`), `orders`
(every attempt with status, broker id and algo tag), `trades` and `login-events` - for an
inclusive UTC date range, capped at 50,000 rows (the manifest says when it was truncated).

* **Scopes**: `GET /api/exports/{dataset}` is the organisation's own trail and is OWNER-only
  (not the caller's rows but the tenant's, which is what the responsible person is asked for);
  `GET /api/admin/exports/{dataset}` is platform-wide for SUPER_ADMIN with a verified MFA
  session, narrowed with `tenant_id` when needed. Tenant scoping is applied server-side from the
  caller's tenant, as everywhere else.
* **Evidence properties**: the response carries `X-Content-SHA256` of the exact bytes,
  `X-Export-Rows`, and for the audit dataset `X-Audit-Chain-Intact` (the platform re-verifies its
  own chain at export time); the same facts sit in a manifest (JSON: `manifest` object; CSV:
  trailing `# key=value` comment lines, so the file stays a plain CSV) together with the hash
  recipe, so the chain can be re-verified offline. Every export writes an `export_generated`
  audit row (dataset, range, rows, SHA-256): who pulled what is on the same chain.
* **UI**: an export card on System Logs (owners) and on the Admin Console (platform or the
  selected tenant) downloads the file through the authenticated API client and shows the
  filename, row count, chain verdict and SHA-256 of what was just downloaded.

Verified by `tests/test_exports.py` (CSV/JSON shape and manifest, SHA-256 header, chain verdict,
export audit row, date range and validation, owner-only and tenant isolation, admin platform vs
tenant scope, algo tag column).

### D3: Data retention and personal-data erasure

The written policy (docs/OPERATIONS.md section 2.3) is now code in `app/retention/policy.py`:
two regimes, checked by tests.

* **Never deleted** (`NEVER_DELETED`): `audit_logs`, `orders`, `order_events`, `trades`,
  `signal_history`, `custom_strategies`, `strategy_versions`, `users`, `tenants`,
  `market_holidays` - the regulatory trading record (SEBI five-year rule) plus the identity rows
  it is attributed to. `tests/test_retention.py` asserts the rule set never names one of them.
* **Bounded** (env-configurable, floor 7 days): login attempts (365), delivered/failed alert
  rows (90), in-app notifications (180; read markers and delivery rows cascade), sessions 30 days
  after expiry or revocation, spent password resets (7) and invites (30). Predicates only ever
  match *finished* rows (a live session or pending delivery is never eligible).
* **Runner** (`app/retention/service.py::run_retention`): one bounded batch per table per run
  (`RETENTION_BATCH_SIZE`, default 5000) so a first run over a backlog never holds a long
  transaction; a per-table failure is reported and does not stop the others; a run that deleted
  anything writes one `retention_run` audit row with the counts. `preview_retention` is the same
  query as a dry run. The trading worker calls it once per IST day when the market is closed
  (`CycleReport.retention`), so it never competes with order flow; SUPER_ADMIN can inspect
  policy, eligible counts and the last run at `GET /api/admin/retention` and trigger a batch with
  `POST /api/admin/retention/run` (both audited).
* **Erasure** (`erase_user`, `POST /api/team/members/{id}/erase`, owner-only, member must already
  be removed): the DPDP-style right to erasure without breaking attribution. The `users` row
  stays as an anonymous id (`erased-<id>@erased.invalid`, an impossible password hash, MFA and
  backup codes gone, every session revoked, pending resets deleted) and the email on that user's
  login attempts is rewritten; an audit row records the user id, not the email. Earlier audit
  rows that quote the email remain - they are hash-chained and cannot be edited - which the
  policy documents as the accepted trade-off between erasure and an immutable trail.

Verified by `tests/test_retention.py` (policy floor and parsing, rule/never-deleted disjointness,
old-vs-fresh deletion per table with regulatory tables untouched, audit row and idempotency,
disabled policy, batch bound, worker once-a-day scheduling, admin endpoints, erasure semantics).

### D4: Contract-note ingestion (actual charges)

`trades.charges` and `pnl` at close time come from the platform's NSE cost model
(`PaperBroker.estimate_round_trip_costs`) - an estimate. The broker's contract note is the truth,
and the books an auditor compares against. Phase D4 lets a trader or owner upload the broker's
contract note / tradebook CSV and replaces the estimate with the broker's own numbers.

* **Parser** (`app/contract_notes/parser.py`): broker-agnostic. Each field has a set of accepted
  header aliases (symbol/tradingsymbol/scrip, side/trade_type/buy-sell, qty, price/rate,
  order_id/order no, date in several formats) and charges come either from a total column or from
  any subset of components (brokerage, STT, exchange transaction charges, GST, SEBI fee, stamp
  duty, clearing), summed per leg and kept as a breakdown. Delimiters are sniffed; a header that
  lacks symbol/side/quantity/price is rejected with the header seen and the accepted names, so
  a wrong export fails loudly instead of matching nothing.
* **Matching** (`service.match_legs`): by broker order id first - exact, against the trade's
  `broker_order_id`, `sl_order_id` and the new `exit_order_id` (the position monitor now records
  the closing order's id) - then, for legs without an id or PAPER-era trades, by symbol + side +
  quantity + IST session date, one leg per side per trade. A leg is used at most once; unmatched
  legs are reported, never guessed.
* **Applying**: matched legs' charges become the trade's `charges`, P&L is recomputed as gross
  minus actual charges, `charges_source` flips from `ESTIMATED` to `CONTRACT_NOTE` and the trade
  points at the note. The upload itself is stored (`contract_notes`: uploader, filename, SHA-256,
  note date, counts) with every parsed leg and its match (`contract_note_lines`), and audited as
  `contract_note_ingested`. The same file (same SHA-256) is refused with 409; a corrected file for
  the same day simply re-matches and overwrites. `apply=false` is a dry run with the same shape.
  Migration `f8c4d6e2b510`.
* **API/UI**: `POST /api/contract-notes` (multipart, traders and owners), `GET /api/contract-notes`
  and `/{id}` (tenant-scoped). The Positions tab has a "Contract notes" card (preview → apply,
  per-trade estimate → actual table, unmatched legs, upload history) and the trade table shows
  each trade's charges with an `est.`/`actual` marker.

Verified by `tests/test_contract_notes.py` (component vs total charges, aliases, delimiters and
date formats, header rejection, order-id-first matching with fill fallback, dry run vs apply,
trade fields and audit row, duplicate refusal, unmatched legs leaving trades untouched, bad-file
errors, tenant isolation). Not verified: a real broker's export - the alias table is built from
the column names Zerodha/Upstox tradebook exports are known to use, and the parser's error names
the header it saw so a new broker's format can be added from one failed upload.


## Phase E: Operations

### E1: Metrics and structured health

`app/observability/metrics.py` holds one Prometheus registry per process. Series come in two kinds:

* **Incremented where things happen** (process-local): `atp_http_requests_total` and
  `atp_http_request_duration_seconds` by method, *route template* and status class (templates,
  never raw paths, so ids do not explode the label space); `atp_orders_total{mode,status}` at
  every terminal point of `execute_signal_for_user`; `atp_login_attempts_total{success,reason}`;
  `atp_alert_deliveries_total{channel,status}`; and on the worker `atp_worker_cycles_total`,
  `atp_worker_cycle_duration_seconds`, `atp_worker_signals_executed_total`,
  `atp_worker_positions_closed_total`, `atp_worker_errors_total`,
  `atp_worker_last_cycle_timestamp_seconds`, `atp_retention_rows_deleted_total{table}`.
* **Refreshed from the database at scrape time** (platform state, the ones to alert on):
  `atp_active_deployments{mode}`, `atp_open_positions{mode}`, `atp_alert_outbox_pending`,
  `atp_worker_heartbeat_age_seconds` (-1 = never), `atp_login_failures_15m`.

Exposure: `GET /metrics` on the API - outside `/api`, so the nginx front never proxies it to
browsers; `METRICS_TOKEN` makes it bearer-protected. The worker serves its own registry with
`prometheus_client.start_http_server` on `WORKER_METRICS_PORT` (9102, `expose`d in compose, not
published). A `prometheus.yml` therefore has two targets: `backend:8000/metrics` (with the token)
and `worker:9102/metrics`.

Health (`app/observability/routes.py`): `GET /api/system/health` stays the trivial liveness
probe; `GET /api/system/ready` is readiness (database round-trip, 503 otherwise) for an
orchestrator; `GET /api/system/health/deep` is the operator view - database latency, Redis
(optional: `disabled`/`unreachable` degrade, never fail), migrations (`alembic_version` vs the
shipped scripts' head; `unknown` when the table or scripts are absent), worker heartbeat age
against three cycles with the market calendar (`stale_market_open` is the "engine is down"
signal; `stale` overnight is normal). Overall `ok` / `degraded` (200) / `down` (503, database).

### E2: Request correlation and API versioning

`app/observability/middleware.py::ObservabilityMiddleware` is a pure-ASGI middleware (no
`BaseHTTPMiddleware`, so streaming exports are untouched) doing three things per request:

* **Request id**: honours an inbound `X-Request-ID` (sanitised to `[A-Za-z0-9._:-]`, 64 chars)
  or mints a UUID4, binds it into the structured log context for the whole request (every log
  line the request produces carries `request_id=`) and echoes it in the response, so a
  user-reported failure is one grep away.
* **`/api/v1` alias**: `/api/v1/...` is the canonical public path and is rewritten to the
  routers' `/api/...` before routing, so every route exists under both without duplicating
  routers. API responses carry `X-API-Version: 1`; a request on the unversioned alias also gets
  `Deprecation: true` and `Link: </api/v1/...>; rel="successor-version"` (RFC 8594 style), so
  integrators can find the canonical path. The alias is not scheduled for removal - the
  TradingView webhook URLs and the Upstox OAuth callback registered at brokers use it and must
  keep working. `/api/v2` is a 404, not a silent alias. The frontend client now uses `/api/v1`.
* **HTTP metrics** (E1) from the route template after the app has routed.

Verified by `tests/test_observability.py` (metric families and labels, template-not-path
cardinality, token gate, orders counter on a LIVE fill, deep health components and worker
freshness, readiness, request-id echo/mint/sanitise, v1 alias parity for GET/POST/query strings
with version and deprecation headers, v2 404, non-API paths without version headers).

### E3: Backups you can restore

`scripts/backup/` (POSIX sh, so it runs in the `postgres:*-alpine` image with nothing installed):

* `backup.sh` - `pg_dump --format=custom --compress=6` of the configured database (`DATABASE_URL`
  in the app's own SQLAlchemy form is accepted and normalised, or libpq `PG*` variables), optional
  AES-256 encryption with `openssl enc -pbkdf2` when `BACKUP_ENCRYPTION_PASSPHRASE` is set,
  SHA-256 sidecar, `latest` symlink, `LAST_BACKUP_OK` marker (untouched on failure, so its age is
  the alert), then retention (older than `BACKUP_RETENTION_DAYS`, never below `BACKUP_KEEP_MIN`).
* `restore.sh <file|latest> [target-url]` - verifies the sidecar, decrypts when needed, asks for
  the database name (or `RESTORE_CONFIRM=yes`), `pg_restore --clean --if-exists --no-owner`.
* `verify_backup.sh [file|latest]` - the rehearsal: creates `<db>_verify_<ts>`, restores into it,
  compares `alembic_version`, checks `tenants`/`orders`/`trades`/`audit_logs` counts are not above
  the source's, runs `python -m app.audit.verify_chain` (new CLI over `verify_audit_chain`) against
  the copy, drops the scratch database, prints one JSON line and exits non-zero unless `status`
  is `ok`. The first failure names the status (`restore_failed`, `schema_mismatch`,
  `table_missing`, `count_mismatch`, `audit_chain_broken`); later checks only add notes.
* `run_scheduled.sh` - the compose `backup` service entrypoint: a loop, not cron, so a crash
  restarts with the container.

Design choices: logical dumps rather than `pg_basebackup`/WAL because they are provider-independent,
restorable into any Postgres of the same or newer major, and small enough to copy off-host daily;
the doc says plainly that point-in-time recovery needs WAL archiving or a managed offering on top.
The compose service pins the same Postgres major as the database so `pg_dump`/`pg_restore` match.

Verified by `tests/test_backup_scripts.py` against a real Postgres (skipped on the SQLite-only
run, executed in CI after the migrations step): plain round-trip with an intact chain, encrypted
round-trip and a wrong passphrase yielding `restore_failed`, and a tampered file refused on the
SHA-256 check. Also rehearsed by hand in this environment against the migrated local database.


## Phase F: F&O Autopilot

The autonomous engine so far ran a strategy on a symbol and traded that same symbol, which is
right for cash equity and impossible for an index. Phase F lets a deployment analyse an
underlying (index or stock) and trade a derived contract - an option (bought or written) or a
future - chosen by rules at signal time, sized in lots, and exited on the strategy's own
underlying levels with a premium safety net.

### F1: Instrument master

`instruments` table (migration `a1c9e7d3b520`), platform-wide: one row per contract a broker
knows - `broker`, `exchange` (NSE/BSE/NFO/BFO as brokers name them), `instrument_key` (the
broker's own id, e.g. `NSE_FO|56789`), `tradingsymbol`, `underlying` (NIFTY, BANKNIFTY,
RELIANCE), `instrument_type` (EQ/INDEX/FUT/CE/PE), `expiry`, `strike`, `lot_size`, `tick_size`,
`weekly`, `synced_at`; indexed for the two lookups routing needs (by underlying/type/expiry/
strike, and by tradingsymbol).

* **Sources** (`app/instruments/master.py`): Upstox's public per-exchange gzip JSON - no token
  needed, so the platform has a master before any tenant logs in; `parse_upstox_master` maps
  segments to exchanges (`NSE_FO` -> `NFO`, `BSE_FO` -> `BFO`), derives the underlying from
  `underlying_symbol`/`name`, and normalises expiry from epoch milliseconds. Kite-style adapter
  dumps go through `parse_broker_instruments`. `replace_master` swaps a broker's rows for the
  given exchanges in one transaction, so readers never see a half-synced table.
* **Bug fixed on the way**: the Upstox adapter stored `expiry` as the raw JSON value; the real
  master sends epoch milliseconds, which the `Instrument` model (ISO string) rejects. It now
  goes through `normalise_expiry`, and `name` prefers `underlying_symbol`.
* **Underlying naming**: strategies take index candles under the index symbol (`NIFTY 50`,
  `NIFTY BANK`), the F&O master names the underlying `NIFTY`/`BANKNIFTY`; `underlying_of` and
  `INDEX_SYMBOLS` map both ways, `derivatives_exchange` picks NFO or BFO (SENSEX/BANKEX).
* **Daily sync**: the worker downloads `INSTRUMENT_SYNC_EXCHANGES` (default `NSE`) once per IST
  day from `INSTRUMENT_SYNC_HOUR_IST` (08:00) so expiries and lot sizes are current before the
  open; a failed download is reported on the cycle and retried the next day, never every minute.
  SUPER_ADMIN can force it with `POST /api/instrument-master/sync` (audited; a download failure
  is a 502 with the reason).
* **API**: `GET /api/instrument-master/status|search|expiries|strikes` for the console and the
  contract resolver (F2).

Verified by `tests/test_instrument_master.py` against a synthetic Upstox-shaped master
(`tests/master_fixture.py`: NIFTY weekly + monthly options and future, BANKNIFTY, RELIANCE
equity and options, indices): expiry formats, segment/type/underlying mapping, atomic replace,
lookups, API and permissions, mocked sync, worker once-a-day scheduling and failure handling.
Not verified: the real Upstox master download (egress is blocked in this environment); the
parser's field names follow Upstox's published JSON and the adapter's existing mapping.

### F2: Contract rules on deployments

A deployment now says *what to trade* when its strategy signals on `symbol` (migration
`b2d8f6a4c731`): `instrument_kind` UNDERLYING (the original behaviour, cash equity), OPTION or
FUTURE, with rules resolved at signal time rather than a contract fixed at creation - a
deployment created on Monday trades Thursday's at-the-money strike on Thursday.

* **Rules** (`ContractRulesRequest.normalised` fills defaults and rejects nonsense):
  `option_position` BUY (LONG -> buy CE, SHORT -> buy PE; loss capped at the premium) or WRITE
  (LONG -> sell PE, SHORT -> sell CE; premium received, margin blocked, open-ended risk until the
  underlying stop or the premium ceiling); `expiry_rule` NEAREST / NEXT / MONTHLY; `strike_rule`
  ATM / ITM / OTM with `strike_offset` listed steps (ITM for a CE is below spot, for a PE above);
  `premium_stop_pct` - for a bought option the premium floor below entry (default 30%), for a
  written one the ceiling above entry (default 50%) - the safety net under the strategy's
  underlying-level exits (F4); `max_lots` caps risk-based sizing (F3). Futures take only an
  expiry rule. Uniqueness is now (tenant, strategy, symbol, mode, kind), so the same strategy can
  run an option and a future deployment on one underlying.
* **Guards**: an index (`NIFTY 50`, `NIFTY BANK`, ...) cannot be deployed as UNDERLYING (400 with
  the fix); a derived-contract deployment needs the underlying's contracts in the instrument
  master (409 pointing at the sync). Plan checks run first, so a free tenant still sees 402.
* **Resolver** (`app/instruments/contracts.py`): `select_expiry` (expiry day counts as available;
  MONTHLY = last expiry of the nearest month with one), `select_strike` (nearest listed strike,
  ties to the lower; ITM/OTM stepped along the listed strikes and clamped), `option_right`, and
  `resolve_contract(session, symbol, rules, direction, spot, today)` returning a
  `ResolvedContract` (tradingsymbol, exchange NFO/BFO, broker instrument key, lot size, expiry,
  strike, right, the entry order side and the trade direction used for P&L). Every failure is a
  `ContractResolutionError` with the reason, which the worker records on the deployment.
* **Preview**: `POST /api/deployments/preview-contract` resolves both directions for the given
  rules using a supplied spot or the tenant's broker LTP, and says why when it cannot. The
  Autopilot form has the rule controls (position, expiry, strike/offset, premium stop, max lots),
  the preview card, switches to OPTION when an index symbol is typed, and the deployments table
  shows each deployment's rule summary; the LIVE confirmation names written options' risk.
* **Safety in this build**: until F3 wires execution, the worker records "not enabled" on an
  OPTION/FUTURE deployment and takes no trade - it never falls through to trading the index.

Verified by `tests/test_contract_rules.py` (right by position, expiry and strike rules incl.
ties/clamping, resolution of bought/written options and futures for index and stock underlyings,
explicit errors, request validation and defaults, uniqueness across kinds, master-presence guard,
preview endpoint, the worker guard).

### F3: Executing on the derived contract

`app/execution/contract_execution.py` turns "LONG NIFTY 50 at 24512, stop 24460" plus the
resolved contract into an *order signal* on the contract, so the risk engine, paper broker, live
router and order trail keep working on one `Signal` shape:

* **Bought option**: entry = current premium (`contract_ltp`, by broker instrument key first,
  tradingsymbol second), stop = premium floor (`premium_stop_pct` below), direction LONG. Risk
  per unit is the premium at risk, so the risk engine's `risk_amount / risk_per_unit` sizes lots
  off it exactly as it sizes shares off a stop distance, in whole lots of the master's lot size
  (a `ContractSpec` built from the resolved contract overrides the registry lookup). A small
  account gets the existing "below one lot" rejection with its reason on the order trail.
* **Written option**: entry = premium received, stop = premium ceiling above, direction SHORT
  (P&L falls as the premium rises). Capped by `max_lots` (one lot when unset) and, LIVE, by
  `written_lot_cap`: the broker's own margin requirement for one lot (`get_order_margin`, new on
  `BrokerInterface`, implemented for Upstox `/charges/margin` and Kite `/margins/orders`)
  against 80% of available margin. An unknown requirement or insufficient margin is a REJECTED
  order with the reason - never a guess.
* **Future**: entry = the future's price; the underlying's stop and target distances are
  transplanted onto it; direction as signalled. Exits then work exactly as for cash (F4).
* **Same pipeline**: `execute_signal_for_user(..., contract, rules, quote_broker)` builds the plan
  before the order row is created (so the trail's symbol is the contract from its first event),
  rejects on a missing quote, applies the size cap in `OrderRouter.execute(max_quantity=...)`, and
  the existing LIVE path places the entry on NFO/BFO and an SL-M on the opposite side at the
  premium floor/ceiling (or the transplanted future stop). PAPER fills at the contract's own
  price with the usual slippage model, not at the underlying's.
* **Trade record** (migration `c3e9a7b5d842`): `symbol` is the contract; `stop_loss` is on the
  contract; `target1` is now nullable (an option has no target on its own price); the strategy's
  levels are kept as `underlying_symbol`/`underlying_direction`/`underlying_stop_loss`/
  `underlying_target1`/`underlying_target2` for the monitor (F4); plus `instrument_kind`,
  `exchange`, `instrument_key`, `lot_size`, `expiry`, `option_position`, `premium_stop_pct`.
  `exchange_for_trade` gives the monitor and square-off the right exchange for the quote and
  the exit order.
* **From the new master prompt** (V4.14 execution quality, safety rule 17): every trade records
  `expected_price` (the signal's price), `slippage` (signed against the trade) and
  `entry_latency_ms`; a LIVE entry that fills partially records the position and sizes the
  protective stop to the *filled* quantity, with the partial fill spelled out on the trail (the
  order book is read once for both price and quantity).
* **Worker**: an OPTION/FUTURE deployment resolves its contract at signal time off the latest
  underlying close; a rule that cannot be satisfied is recorded on the deployment and no trade
  is taken. The F2 guard is gone.

Verified by `tests/test_contract_execution.py` (order plans for buy/write/future, quote
fallbacks and failure, margin cap arithmetic and refusals, PAPER buy sizing and stored fields,
below-one-lot rejection, max-lots cap, missing-quote rejection on the trail, LIVE buy entry +
floor SL-M, LIVE write sell + ceiling SL-M capped by margin, LIVE write refused on unknown/
insufficient margin, partial fill, PAPER future with transplanted levels, worker end-to-end
and resolution failure). Not verified: real broker margin API responses (parsed defensively).

### F4: Exits for derived contracts

The strategy decided the trade on the underlying, so the underlying decides the exit; the
contract only prices it. `app/trading/exit_logic.py::check_contract_exit`:

* **OPTION**: `underlying_exit` applies the strategy's stop / target 2 / target 1 (same priority
  as cash, tolerant of a strategy with no targets) to the *underlying's* price; a hit exits at
  the contract's current price with the reason suffixed "(underlying)". Independently, the
  premium safety net: a bought option whose premium fell to the floor (`trade.stop_loss`), or a
  written option whose premium rose to the ceiling, exits at the contract price - and this check
  still runs when the underlying quote is unavailable, so a dead index feed never leaves an
  option unprotected.
* **FUTURE / UNDERLYING**: the levels are on the contract's own price - plain `check_exit`.
* **Monitor** (`monitor_open_positions`): for option trades it quotes both the contract (on its
  own exchange, `exchange_for_trade`) and the underlying (`underlying_exchange`: NSE, or BSE for
  SENSEX/BANKEX); LIVE exits go through the existing single close path, so the floor/ceiling
  SL-M is cancelled (or recognised as already filled) before the market exit on NFO/BFO.
  Square-off at 15:15 IST is unchanged and covers expiry day.
* **Charges**: `PaperBroker.estimate_round_trip_costs` now has per-kind profiles (equity, option
  premium turnover with sell-side STT, futures notional with sell-side STT) - still an estimate a
  contract note replaces (D4).
* **Manual check**: `POST /api/positions/{id}/mark-price` takes `underlying_price` alongside the
  contract's `current_price` for option positions; the Positions tab shows the underlying
  levels and the premium floor/ceiling on each derived position.

Verified by `tests/test_contract_exits.py` (level priority and missing targets, bought and
written option exits on underlying levels and on the premium net, futures/cash unchanged, cost
profiles, underlying exchange mapping, the monitor's two quotes with P&L on the premium, the
premium-only fallback when the underlying feed fails, LIVE exit on NFO after cancelling the stop,
the mark-price endpoint).

## Phase G: Safety and reliability closure

The revised master prompt's consolidated safety rules 7, 8 and 18 and sections 17 and 49, plus the
V4.9 health spellings, V3.14 rule 2 and the section 47 disclaimers. Small modules, each closing
one gap `docs/MASTER_PROMPT_GAP_ANALYSIS.md` named; `docs/SLO.md` states what they protect.

### G1: Staleness gate, broker-uncertain flag, reconciliation on start

**Staleness gate** (`app/market_data/freshness.py`). Two checks, both returning a human-readable
reason or None:

* `candle_staleness(last_bar_ts, timeframe, now)`: the newest bar of the deployment's base
  timeframe may be at most `(MARKET_DATA_MAX_STALE_BARS + 1) * timeframe` old (default 3 missed
  bars: a 1-minute feed more than 4 minutes behind the clock). The worker runs it in
  `_evaluate_deployment` after the history check and *before* `strategy.analyze`; a stale feed
  sets `last_error = "Skipped: market data stale: newest 1min bar is N min old (...)"`, counts on
  `atp_market_data_stale_total{kind="candles"}` and `CycleReport.stale_skips`, and evaluates
  nothing. The next fresh cycle trades normally.
* `quote_is_stale(quote_ts, now)`: `MarketDataService.get_ltp` now asks the broker for a full
  quote first (`BrokerInterface.get_quote_for_symbol`, implemented for Upstox and Kite with their
  `last_trade_time`/`timestamp` parsed by `app/brokers/timestamps.py`) and raises
  `StaleMarketDataError` when the exchange timestamp is older than `QUOTE_MAX_STALE_SECONDS`
  (120). The position monitor already treats any price failure as "no decision this cycle", so a
  stale quote leaves the position as it is, with the reason in the outcome's warnings. Brokers
  whose LTP endpoint carries no timestamp are accepted as real-time (nothing to judge by).

**Broker-uncertain flag** (`app/reconciliation/service.py`, `tenants.broker_uncertain_since /
broker_uncertain_reason / last_reconciled_at`, migration `d4f0b8c6e953`). When a LIVE order ends
FAILED - the broker call raised or timed out, so nobody knows whether the broker holds the
position - `execute_signal_for_user` calls `mark_broker_uncertain`. From then on:

* every new LIVE entry for that organisation is refused - inside `execute_signal_for_user`
  (REJECTED, reason "Broker state uncertain since ... - LIVE entries blocked until position
  reconciliation passes"), so the console, webhooks and the worker all hit the same wall; the
  worker additionally skips one step earlier and writes the reason on the deployment;
* exits are untouched (open risk is still real);
* the worker runs `run_reconciliation` for the tenant *every cycle* while flagged. Zero
  mismatches clears the flag (`broker_uncertain_cleared` audit row) and the same cycle may trade;
  mismatches keep it, with one CRITICAL notification naming them.

`run_reconciliation` is the one implementation behind the on-demand `POST
/api/reconciliation/{broker}`, the worker's per-cycle run and the start-up run. It compares LIVE
trades only (PAPER positions never exist at the broker), writes the same audit rows as before,
stamps `last_reconciled_at`, and updates the flag. `GET /api/reconciliation/status` exposes the
state; the Autopilot page shows a red banner with a "Reconcile" button while flagged.

**Reconciliation on start** (`TradingWorker.reconcile_on_start`, safety rule 18). Before the
first cycle, every tenant holding an open LIVE trade is reconciled against the broker(s) its
deployments trade through (falling back to every stored broker for console-entered trades). A
mismatch flags the tenant and raises the CRITICAL notification; a clean run clears a stale flag.
A failure here never stops the worker from starting - the flag is the protection, not the
process exit.

### G2: Circuit breaker and SLOs

`app/brokers/circuit_breaker.py`: one `CircuitBreaker` per broker name per process. Every call
through `RateLimitedBroker` (what the worker uses) reports its outcome via `observe_call`;
`OrderRouter` reports its own `place_order` when handed an unwrapped adapter. Only health failures
count - timeouts, connection errors, 5xx, 429, malformed payloads; a business 4xx (margin,
invalid instrument, expired token) is the broker working and moves nothing. More than
`BROKER_CIRCUIT_FAILURE_RATIO` (0.5) of the last `BROKER_CIRCUIT_WINDOW_SECONDS` (60) of calls
failing, after at least `BROKER_CIRCUIT_MIN_CALLS` (5), opens the breaker for
`BROKER_CIRCUIT_OPEN_SECONDS` (120); then HALF_OPEN admits one probe entry, whose outcome closes
or re-opens it.

`OrderRouter.execute` checks `breaker.allow_submission()` before a LIVE entry and returns a
REJECTED result with the breaker's reason while it is open - platform-wide, every tenant, because
the broker is the shared dependency. Exits, cancels and protective stops are never refused. The
kill switch is a person's decision and stays independent; either alone stops entries. Metrics:
`atp_broker_calls_total{broker,method,outcome}`, `atp_broker_circuit_state{broker}` (0/1/2),
`atp_broker_circuit_rejections_total`, plus `atp_order_entry_latency_seconds{mode}` for SLO-7.

`docs/SLO.md` lists nine objectives with the metric that measures each and the alert that guards
it; `scripts/monitoring/prometheus-alerts.yml` holds those alerts as Prometheus rules.

### G3: Health aliases, broker disconnect, disclaimers

* `GET /api/system/health/live | ready | dependencies` (V4.9). `dependencies` is `health/deep`
  plus every breaker's snapshot and the count of broker-uncertain tenants; an open breaker makes
  it `degraded`.
* `BrokerInterface.get_balance()` (alias of `get_margins`) and `disconnect()` (default: forget
  the token; Upstox `DELETE /logout`, Kite `DELETE /session/token`). `POST
  /api/broker/{name}/disconnect` revokes today's session on purpose: broker logout, access token
  removed from the encrypted payload, status EXPIRED, audit row `broker_disconnected`. Key and
  secret stay, so the next login needs no re-entry.
* `Disclaimer` component (`frontend/src/components/ui.tsx`) on the Backtest, Signals, Scanner,
  Fundamentals and Strategy Builder pages, each naming what that page's numbers are not.

## Phase H: Options depth

Master prompt sections 23-25 and V2.1-2.6: the strike-selection pipeline and the V1 option
structures (bull put spread, bear call spread, iron condor), on top of Phase F's single leg.

### H1: Strike-selection pipeline

`app/instruments/strike_selection.py`. A deployment may carry `strike_filters` (JSON):
`min_oi`, `min_volume`, `max_spread_pct` (bid/ask over mid), `min_iv_pct`/`max_iv_pct`,
`target_delta` with `delta_tolerance`, `min_premium`/`max_premium`, `search_steps`. With filters
active, `resolve_contract` no longer trusts the rule strike: it fetches the live option chain
through the tenant's broker (`chain_provider`, built by the worker from the market-data adapter),
judges every listed strike within `search_steps` of the rule strike (`candidate_for` computes
spread %, IV from the chain or solved from the premium, delta from the broker or Black-Scholes),
and picks the passing strike nearest the target delta (or nearest the rule strike). The verdicts
travel on `ResolvedContract.selection` / `selection_notes`, so the preview shows why 24450 beat
24500 and the order's reasons carry the same sentence. No chain, an empty chain, or nothing
passing is a `ContractResolutionError` recorded on the deployment - a configured filter is
never silently skipped. Upstox chain parsing now keeps bid/ask, IV, delta, OI change and spot.

### H2: Multi-leg structures

`app/instruments/spreads.py` resolves a structure from the same rules: the short leg at the rule
strike (ATM/OTM n) for the sold right, the wing `spread_width` listed steps further out; the
condor does both sides with the shorts `strike_offset` steps OTM. Direction discipline: a bull
put only on LONG, a bear call only on SHORT, the condor on either; a mismatch is a recorded
"not entered on a SHORT signal", never the mirror structure. `structure_metrics` gives net
credit, max profit, max loss (width - credit), breakevens and the group exit levels
(`target_credit_pct` of the credit captured, `stop_credit_pct` of the credit lost).

`app/execution/multileg.py::execute_structure` is the group counterpart of the single-leg
pipeline: the same entry refusals (`entry_refusals`, now shared), one OrderRecord per leg under
`<key>:L<i>`, quotes for every leg, the risk engine's day checks, lots sized off **max loss**
(risk per trade / max loss per lot, capped by `max_lots` and LIVE by the broker's margin for the
short legs), then paper fills at the quoted premiums or LIVE placement *wings first, shorts
second*. A failed leg after another filled is unwound with market orders, every order ends
FAILED, the tenant is flagged broker-uncertain (Phase G1) and a CRITICAL notification names the
leg. Trades share `leg_group_id`, carry `leg_role`, `option_strategy` and the group's metrics in
`group_meta`.

Exits (`position_monitor._monitor_group`): the legs are judged together on the spread's value
(cost to close = shorts' premiums minus wings'): value <= target, value >= stop, or the
underlying through a short strike; a missing leg quote means no decision this cycle. Closing
buys the shorts back first, then sells the wings; each leg books its own P&L. `GET
/api/positions/greeks` computes per-leg and per-group Greeks from live premiums (IV solved
from the last price) and the underlying's spot; the Positions page shows them on demand.

The Autopilot form gains a Structure selector, wing width, target/stop credit %, a strike-filter
panel, and previews legs with credit, max loss and breakeven when a broker session can quote
them. Migration `e5a1c9d7f064`.

## Phase I: Risk hierarchy and broker accounts

### I1: Risk hierarchy

Master prompt V3.4 / V4.5 and the section 17 check list. `risk_limits` rows carry one
`limit_type` at one scope - GLOBAL (platform, SUPER_ADMIN), TENANT, USER, ACCOUNT, STRATEGY or
INSTRUMENT (`scope_id` = user id / account id / strategy id / symbol) - and
`app/risk_engine/hierarchy.py::evaluate` checks an order against every limit that applies to
it, keeping the smallest of each type ("strictest wins"). Eight types: MAX_DAILY_LOSS and
MAX_STRATEGY_LOSS (realised today, currency), MAX_LOSS_PER_TRADE (at the stop; for a structure
its max loss), MAX_ORDER_VALUE, MAX_POSITION_QUANTITY, MAX_OPEN_POSITIONS, MAX_TRADES_PER_DAY,
MAX_CAPITAL_ALLOCATION_PCT.

The evaluator runs after sizing and before any fill or broker call: `OrderRouter.execute`
awaits `pre_place_check(quantity)` (the closure `execute_signal_for_user` builds), and
`execute_structure` calls it with the structure's max loss. Every check is a `risk_events` row
(PASS, WARN at 80% of the limit, BLOCK) carrying the measured value, the limit, the scope and
the order id, so the log answers both "why was this refused" and "how close are we". Breaches
also act: MAX_STRATEGY_LOSS engages the strategy kill switch, a TENANT/GLOBAL MAX_DAILY_LOSS
engages the tenant kill switch - idempotent, audited, with a CRITICAL notification - so the
next signal is refused at the door. A failure to read or measure a limit blocks the order
(fail safe). API under `/api/risk/limits|events|evaluate`; the Risk page shows limits and the
event log. `risk_events` is in the never-deleted set.

### I2: Broker accounts

V3.14 rule 3 and V3.1-3.5 routing. `broker_credentials.account_label` (default `primary`,
unique per tenant/broker/label) lets one broker hold several accounts as several credential
rows; `broker_accounts` is the account behind each credential: broker identifier, display
name, ACTIVE/DISABLED, default flag, and the last synced balance, used margin, realised and
unrealised P&L (`app/accounts/service.py::sync_account`, from `get_balance`, `get_profile`
and `get_positions`). Accounts are created when credentials are stored (and lazily for
credentials that predate the table). API: `GET /api/accounts`, `POST /api/accounts/{id}/sync`,
`.../enable|disable|default`, `PATCH /api/accounts/{id}`; credential endpoints take an
`account_label` query parameter.

Routing: a deployment may name `broker_account_id`; otherwise the broker's default account
applies. The worker builds one adapter per (broker, label) and, per deployment, resolves the
route (`_live_broker_for`): a DISABLED account, a missing session or a broker-uncertain tenant
each record their reason and skip the entry. The account id is passed into execution so
ACCOUNT-scope risk limits apply. Settings shows the accounts card; the Autopilot form offers
the broker's accounts for LIVE deployments.

## Phase J: Exits and backtesting depth

### J1: Dynamic exit rules

Master prompt section 29. `app/trading/exit_rules.py::ExitRules` (JSON on the deployment and
on each trade it opens) carries `trailing_stop_pct`, `break_even_at_r`, `time_exit_minutes`
and `time_exit_at` (IST). `apply_exit_rules` is one pure function of the trade's state and the
latest bar or quote: it returns a stop that only ever tightens (break-even once the trade is
`R` multiples in profit, a trailing stop off the best price once in profit), the new best
price, and a time-exit reason when one fired. The backtest engine applies it per bar (judging
the bar against the stop tightened on the *previous* bar) and the position monitor applies it
per cycle on the current price, persisting `best_price` / `stop_loss` (the original stop stays
in `initial_stop_loss`) and, LIVE, moving the broker-side SL-M trigger with `modify_order`
(a failed modify keeps the software stop and is logged). One rule implementation for backtest,
paper and live - section 13's "same logic everywhere" for exits.

### J2: Backtest analytics, robustness and run records

Sections 31-32, V2.10, V4.8. `app/backtest/analytics.py::build_analytics` derives the report
views from the closed trades and the equity curve: monthly P&L, day-of-week and hour-of-day
performance, exit-reason breakdown, direction split, holding-time stats, slippage summary
(entry vs the signal's expected price), cost share, streaks, ratio metrics (CAGR, Sharpe,
Sortino, Calmar where the sample allows) and the drawdown curve; `BacktestResult.analytics`
carries it. `app/backtest/robustness.py` adds **Monte Carlo** (resample the trade P&Ls with
replacement, report percentiles of final P&L and max drawdown, probability of loss, probability
the drawdown exceeds the original) and **walk-forward** (the same parameters on consecutive
windows, per-window metrics and a consistency score; no per-window re-optimisation, which would
be a new strategy version). Endpoints `POST /api/backtest/monte-carlo` and
`/api/backtest/walk-forward`.

Every backtest a logged-in user runs is recorded (`backtest_runs`: strategy, params, exit
rules, data source label, bar count and span, engine version, headline metrics and analytics)
and listed at `GET /api/backtests` / `GET /api/backtests/{id}`, so a number on a screen is
traceable to what produced it. The Backtest page gains exit-rule inputs, the analytics tables,
Monte Carlo and walk-forward cards and the run history; the Autopilot form gains the same
exit-rule inputs. Migration `a7c3e1f9b286`.

## Phase K: Commercial SaaS layer

### K1: Plans with prices, subscriptions, billing lifecycle, metering

Master prompt V3.6-3.8, V3.14 rule 5. `app/plans/registry.py::Plan` now carries prices
(monthly/yearly, INR), the commercial limits (LIVE strategies, backtests per month, public API
calls per day, accounts, brokers), feature flags (`option_features`, `ai_features`,
`marketplace_access`, `public_api`), support level and trial days; `plans/limits.py::
feature_allowed` / `require_feature` gate features on plan **and** tenant status (a suspended
organisation loses paid features). `app/billing/service.py` holds the lifecycle behind a
`BillingProvider` abstraction whose first implementation is **manual** (bank transfer / UPI,
operator records the payment; a gateway provider plugs in at the same seam):
`subscribe` (a plan with trial days starts TRIALING and entitles at once; an invoice is raised
payable after the trial), `record_payment` (operator/webhook: invoices PAID, ACTIVE, period
extended), `cancel` (at period end or immediately), and the worker's daily `sweep` (trial or
period ended → PAST_DUE with a 7-day grace and a renewal invoice; grace ended → CANCELLED,
plan back to Free with `tenants.status_reason` recording why; cancellations falling due).
Tables `subscriptions`, `billing_transactions` (INVOICE/PAYMENT/REFUND, never edited except
OPEN→PAID), `usage_records` (daily buckets per metric). `meter()` is called for every order
attempt (`create_order`), every logged-in backtest, every TradingView webhook event and every
public API call. Endpoints `/api/billing/plans|""|subscribe|cancel|transactions|usage`,
operator `POST /api/admin/billing/{tenant_id}/payment`. No card data ever touches the app.

#### K1b: Razorpay gateway

`app/billing/razorpay.py::RazorpayProvider` implements the seam against Razorpay Subscriptions:
our (plan, cycle) is mirrored once as a Razorpay Plan (`billing_gateway_plans`, re-created when
a price changes since gateway plans are immutable); `create_subscription` creates the gateway
subscription with `start_at` at the trial end and returns the hosted checkout link, stored on
`subscriptions.checkout_url` and shown on the Billing card; `change_plan` PATCHes the gateway
subscription (`schedule_change_at: now`); `cancel` passes `cancel_at_cycle_end`. Webhooks land at
`POST /api/billing/webhooks/razorpay`: HMAC-SHA256 over the raw body with the webhook secret,
de-duplicated on `X-Razorpay-Event-Id` via `billing_webhook_events`, and applied through the same
service calls the operator uses (`subscription.charged` → `record_payment`, itself idempotent on
the payment id; `payment.failed` → FAILED_PAYMENT + notice; `subscription.halted` → PAST_DUE with
grace; `subscription.cancelled|completed` → cancelled, Free). The provider is chosen from
`BILLING_PROVIDER` at first use (`set_provider` swaps it in tests); missing keys fall back to
manual with a warning. Gateway credentials are platform secrets in the environment, never tenant
data. Migration `d0f6b4c2e5a9`.

### K2: Strategy marketplace

V3.9-3.10, V3.14 rule 8. `app/marketplace/service.py`: a creator lists one **frozen version**
of their custom strategy (`marketplace_listings.config_json`; later edits never reach
subscribers) with a title, description, methodology and a **saved backtest run as documented
performance** (required to submit - the listing shows the run's symbol, timeframe, bar span,
data source and engine version alongside the metrics, with a fixed disclaimer). Flow
DRAFT → PENDING_REVIEW → PUBLISHED / REJECTED (with the reviewer's note back to the creator) →
UNLISTED, reviewed by the SUPER_ADMIN (`/api/admin/marketplace/pending|{id}/publish|reject`).
Subscribing copies the frozen config into the subscriber's own `custom_strategies` (a new
version with source `marketplace:<id>`), so it goes through the subscriber's backtest → paper →
live pipeline like anything they wrote; unsubscribing keeps the copy. Marketplace access is
plan-gated (`require_feature(tenant, "marketplace_access")`), listings never expose the
creator's internals. Frontend: the Marketplace page (discover, subscribe, publish, review queue).

### K3: Public API and developer portal

V3.11-3.12, V3.14 rules 9-10. `app/public_api/keys.py`: keys `atp_<prefix>_<secret>` shown
once, stored hashed, with scopes, a per-key per-minute limiter, optional expiry, revocation,
plan gate (`public_api`) and daily allowance (`max_api_calls_per_day`); `api_key_auth(scope)`
is the dependency every `/api/public/v1/*` route uses, and it meters one `api_call`.
`app/public_api/routes.py`: read endpoints (account, instruments, strategies, signals, orders,
positions, trades, backtests, risk limits/events) and `POST /signals` which runs
`execute_signal_for_user` in PAPER with an idempotency key (LIVE through a key is refused - it
is bound to a broker account through deployments). `GET /api/public/v1/docs` is the
machine-readable developer reference; `docs/PUBLIC_API.md` the human one. Keys are managed
under Settings (OWNER only).

### K4: Webhook alert channel

V3.13 / section 42. `alerts/channels.py::WebhookConfig` (HTTPS URL, shared secret 16+ chars,
optional event-type filter) joins Telegram and email; `dispatcher.send_webhook` POSTs the
notification as JSON with `X-ATP-Event`, `X-ATP-Timestamp` and
`X-ATP-Signature: sha256=HMAC-SHA256(secret, timestamp + "." + body)`, through the same outbox
with the same retries and delivery records. Paid plans allow three channels so all of
Telegram, email and webhook can be on. SMS/push remain out of scope (no provider decision).

Migration `b8d4f2a0c397`.

## Phase L: AI layer

Master prompt section 56, V4.1-4.3, V4.7, safety rules 15-16. Three principles run through
every part: the model **drafts, never decides**; every AI output goes through the same
pipeline as a human's (backtest → paper → live, risk hierarchy, kill switches); the AI never
holds a broker credential (a provider gets prompt text in and text out).

### L1: Provider seam and tenant keys

`app/ai/providers.py::LLMProvider` is one method, `complete(system, user) -> str`, with
Anthropic (the official `anthropic` SDK: `claude-opus-5` by default, adaptive thinking, effort
`medium`, `fallbacks="default"` so a safety-classifier decline is re-run on Anthropic's
recommended substitute; a surviving refusal is a ProviderError, never an empty draft), OpenAI
(Chat Completions over httpx) and a **rule-based** implementation that
wraps the existing NLU parser - no key, no network - so every AI feature has an explainable
fallback. The tenant's choice lives in `ai_provider_configs`: provider, model, and the API key
Fernet-encrypted with `SECRETS_ENCRYPTION_KEY`, entered on the Settings page only (OWNER),
never returned once stored, never read from the environment or a chat. Free-plan tenants and
tenants without a configured provider always get the rule-based provider
(`ai_settings.provider_for`). Provider errors (401/429/unreachable) are mapped to plain
messages and recorded on the config row.

### L2: Strategy generator behind the review gate

`app/ai/generator.py` asks the provider for one JSON object in the `CustomStrategyConfig`
contract (the no-code builder's schema: indicators, operators, ATR stop, R targets) plus an
explanation and the model's own caveats. The answer is validated by the same pydantic model
that guards `POST /api/custom-strategies`; an invalid answer is retried once with the error
quoted back, then recorded as FAILED. The raw response is kept on the `ai_strategy_drafts` row
for lineage (provider, model, prompt, time). State machine: DRAFT → BACKTESTED
(`POST /api/ai/drafts/{id}/backtest` runs the draft through the ordinary engine and records a
`backtest_runs` row with `strategy_id = ai_draft_<id>`) → APPROVED (`POST .../approve`, a human
call that is refused until a backtest is attached) or REJECTED. Approval is the only way a
draft becomes a `custom_strategies` row, stamped `origin="ai:<draft>"` and `ai_approved_by`,
with a strategy version whose source is `ai:<draft>`; from there it is an ordinary strategy.

### L3: Market regime engine

`app/ai/regime.py::classify_regime` is deterministic: ADX(14) for trend strength, EMA20/EMA50
relation and slope for direction, ATR as a share of price against its rolling median for
volatility → TRENDING_UP / TRENDING_DOWN / RANGING / VOLATILE / QUIET (or UNKNOWN under 60
bars), each with a confidence and the numbers behind the call. A deployment's optional
`regime_filter` (a set of allowed regimes) is judged on the base frame right before the
strategy runs; a blocked entry writes the regime, its confidence and the leading reasons into
`last_error`, so the Autopilot card says exactly why it sat out. `POST /api/ai/regime`
classifies uploaded candles; the Autopilot form offers the filter as toggles.

### L4: Monitoring agent and the action-state machine

`app/ai/monitor.py` observes each tenant's deployments at the end of every worker cycle from
persisted records only (never a model call in the decision path): LOSING_STREAK (last 3 trades
today lost), DAY_DRAWDOWN (a deployment's realised P&L today below 2% of capital),
ERROR_STREAK (3 consecutive evaluation failures - below the worker's own auto-pause at 5, so
the human hears first), STALE_POSITION (open > 120 min with the regime turned adverse),
WIN_RATE_DRIFT (rolling win rate ≥ 25 points under the latest backtest). Each firing is one
`ai_actions` row: PROPOSED → APPROVED → EXECUTED / FAILED, or REJECTED, or EXPIRED after 24
hours; duplicates per (deployment, rule) are suppressed while one is open or was decided today.
A proposal raises an `AI_PROPOSAL` notification (WARNING, so Telegram/email/webhook carry it).
`POST /api/ai/actions/{id}/approve` is the human step and executes at once through the
ordinary services (pause = the deployment's status change with an audited reason; exit = the
position monitor's `close_position` off a usable broker session's LTP; review/reduce-risk =
acknowledged, no automatic change). The AI Copilot page lists open proposals with their
evidence, the decided history, the generator and the regime read; the Settings page holds
the provider card. Migration `c9e5a3b1d4a8`.

Not built (needs a product decision): AI-written scanners (V4.2) beyond the rule-based scanner,
per-tenant model fine-tuning, and any auto-approval class - every action stays human-gated.

## Phase M: Closure of the remaining master-prompt gaps

### M1: Platform controls (V4.13)

`app/platform/controls.py` holds operator switches in `platform_controls` (key -> JSON):
**maintenance mode** (no new entries anywhere, paper or live; exits, monitoring and the API keep
running; every user sees the message via the unauthenticated `GET /api/system/status` the top bar
polls) and **disabled brokers** (no new LIVE entries through a named broker; exits still go). Both
are checked in `entry_refusals` alongside the kill switches, so single-leg, multi-leg, TradingView
and public-API entries all obey them; the worker also refuses at `_live_broker_for` and says why
on the deployment. **Per-user trading disable** is `users.trading_disabled_reason`, set by the
tenant OWNER (`POST /api/team/members/{id}/trading-disable|enable`) or a platform admin, and
checked in the same place - the member keeps reading, exiting and reporting. Admin endpoints:
`GET /api/admin/controls`, `PUT .../maintenance`, `PUT .../brokers` (SUPER_ADMIN + MFA).

### M2: Portfolio engine and two more risk scopes (V4.4-4.5)

`app/portfolio/engine.py::snapshot` aggregates a tenant's open trades into gross/net/long/short
notional, per-symbol and per-strategy exposure, concentration, unrealised P&L at the given prices
(LTP when a broker session is usable, else entry - the response says which), realised P&L today
and loss-if-every-stop-hits, with plain warnings (leverage, concentration over 40%, stop risk over
5% of capital). `GET /api/portfolio/exposure`; the Portfolio page shows it. The risk hierarchy
gains **PORTFOLIO** (whole book, no scope id) and **DEPLOYMENT** (one Autopilot instance) scopes -
eight levels: GLOBAL, TENANT, USER, ACCOUNT, PORTFOLIO, STRATEGY, DEPLOYMENT, INSTRUMENT - and two
limit types, `MAX_GROSS_EXPOSURE` (open notional after the order) and
`MAX_SYMBOL_CONCENTRATION_PCT` (one symbol's share of capital after the order). Open-position and
trades-per-day counts honour the DEPLOYMENT scope; `RiskContext.deployment_id` is set by both
executors.

### M3: Trade journal (V4.14), EMERGENCY severity (V4.11), degradation baselines (V4.15)

Trades carry `regime_at_entry` (stamped by the worker from the regime it classified on the base
frame), free `notes` and comma-separated `tags` (`PATCH /api/trades/{id}/journal`; Positions page).
`NotificationSeverity.EMERGENCY` ranks above CRITICAL for channel floors and is used for the
emergency exit. `app/trading/degradation.py` compares each strategy's live results (all and the
last 20 trades) with its latest saved backtest - win rate, expectancy, profit factor - into
OK / WATCH / DEGRADED / NO_BASELINE / INSUFFICIENT_DATA with the reasons (`GET
/api/analytics/degradation`; Analytics page).

### M4: Incidents (V4.10), broker interface completion (section 8), optimisation (V4.6)

`incidents` records severity, title, status (OPEN → MITIGATED → RESOLVED), source, the audit-log
id range they span, root cause, actions and measured data-loss / downtime minutes (downtime is
computed at resolution when not given); engaging the global kill switch opens one automatically
(`app/incidents/`, `/api/admin/incidents`). `BrokerInterface.exit_position` squares off at market
by default (adapters may override with a native square-off) and `subscribe_market_data` raises
`NotImplementedError` until an adapter streams - callers poll. `app/backtest/optimizer.py` runs a
bounded grid (60 combinations) on the in-sample part of the data, re-runs each untouched on the
held-out part and ranks by the **out-of-sample** metric with the in-sample figure and an
`overfit_gap` beside it (`POST /api/backtest/optimize`; Backtest page). Migration `e1a7c5d3f6b0`.

Still open after Phase M (all need a product/provider decision, not code alone): SMS/push
channels, fine-grained API scopes replacing roles (V4.12), per-tenant envelope encryption and a
secret manager (§48), multi-currency and FIU/TDS (§57-61), a websocket market-data feed, and the
V1 exit gate - a real Upstox run with the operator's credentials.

## Phase N: Security and platform hardening

### N1: Per-tenant envelope encryption (section 48, ADR 0008)

`tenant_keys` holds one random Fernet data key per tenant, wrapped by the master key from
`SECRETS_ENCRYPTION_KEY`. `encrypt_text(plaintext, tenant_id)` writes `t1:<tenant_id>:<token>`
under the tenant key; `decrypt_text` reads both that and the legacy master-key format, so nothing
had to be migrated on deploy. Keys are unwrapped into an in-process ring by
`envelope.ensure_tenant_key` at every entry point that may decrypt: `get_current_user`, the
public-API key auth, the TradingView webhook, the worker per tenant, the alert dispatcher per
channel, registration, and `warm_all` at API start-up. A cold ring never fails a write (it falls
back to the master format with a warning); a cold decrypt raises an actionable error.
`scripts/reencrypt_secrets.py status|reencrypt|rotate-master` moves legacy rows to tenant keys
and re-wraps all keys after a master change (idempotent; O(tenants), no credential row touched).
`GET /api/system/encryption` (admin) reports the migration state.

### N2: Fine-grained scopes (V4.12, ADR 0009)

`app/auth/scopes.py`: a catalogue of scopes, a role -> default-scopes matrix, and per-member
overrides in `users.scope_overrides` (`{"deny": [...], "grant": [...]}`) set by the tenant OWNER
through `PUT /api/team/members/{id}/scopes` (grants capped at the owner's own scopes; unknown
scopes and `admin:platform` rejected; an owner cannot drop their own `team:manage`).
`require_role(..., scope=...)` makes `require_trader` check `trading:write` and `require_owner`
check `team:manage`; LIVE creation/resume checks `trading:live`; credential storage checks
`brokers:write`. Denials return 403 with `X-Missing-Scope`. `/api/auth/me` returns the effective
scopes; `/api/auth/scopes` returns the catalogue. Exits are never scope-gated.

### N3: Email verification

`users.email_verified_at` + `email_verifications` (24-hour single-use tokens, SHA-256 stored).
Registration issues a link through the platform mailer (`app/notifications/mailer.py`,
`PLATFORM_SMTP_*`; when unconfigured the link is logged and the resend endpoint says so). Invite
acceptance counts as verified (the invite reached that inbox); a platform admin can stamp a user
(`POST /api/team/members/{id}/verify-email`). With `EMAIL_VERIFICATION_REQUIRED=true`, LIVE
deployments and broker credential storage return 403 with `X-Step-Up: email` until verified;
PAPER and exits are untouched. Users created before Phase N were backfilled as verified. The
password-reset mailer now falls back to the platform mailer when the tenant has no EMAIL channel.

### N4: Feature flags and the migration-hour guard (section 51)

Feature flags live in `platform_controls` under `feature_flags` with kill-flag semantics: a
feature is on unless turned off, optionally keeping an allow-list of tenant ids (staged rollout).
Flags: `ai_copilot`, `marketplace`, `public_api`, `backtest_optimizer`, `live_trading`,
`self_signup`. `require_flag` returns 503 with `X-Feature-Disabled`; `GET /api/system/features`
tells the UI what is on for the caller's tenant; admins manage them at
`GET/PUT /api/admin/controls/flags[/{name}]`. `scripts/migrate_guard.py` wraps
`alembic upgrade head` in the container entrypoint: nothing pending -> start; pending and NSE
session closed -> migrate; pending and session open -> refuse (exit 3) unless `--force` /
`MIGRATION_FORCE=1`.

### N5: ADRs and the DSL reference (section 55)

`docs/adr/` records the ten decisions that shape the platform; `docs/STRATEGY_DSL.md` is the
versioned reference for `CustomStrategyConfig`.

## Phase O: Reliability, delivery and data-protection closure

### O1: Least-privilege database roles (section 48)

`scripts/db_roles.sql` (driven by `scripts/init_db_roles.py`) creates `atp_migrator`, which owns
the schema and is the only role that may run DDL, and `atp_app`, which holds SELECT/INSERT/UPDATE/
DELETE on every table and sequence (including future ones via default privileges) and nothing
else. The API and worker connect as `atp_app` (`DATABASE_URL`); alembic and the migration guard
connect as `atp_migrator` (`MIGRATION_DATABASE_URL`, falling back to `DATABASE_URL` so a
single-role development database keeps working). A compromised app process can therefore not
drop or alter tables, create roles or read other databases. The script is idempotent and is
verified in CI against a scratch database (`tests/test_phase_o_hardening.py`).

### O2: Per-exchange sessions (sections 57-61)

`app/market_data/calendar.py` now carries one `ExchangeSession` per venue family: NSE (09:15-15:30,
no new entries after 15:00, square-off 15:15; BSE/NFO/BFO share its clock and holidays), MCX
(09:00-23:30, cut-offs 23:00/23:15, its own holiday calendar) and CRYPTO (24x7, no cut-offs).
`session_family()` maps any exchange code to its family; `session_status(..., exchange=)` and
`all_session_statuses()` give one status per family. The worker considers NSE plus every family
that has an active deployment, runs whenever any of them is open, skips deployments whose venue
is closed, applies each deployment's own entry cut-off, squares off only the venues whose
square-off time has passed and monitors only positions on venues still trading. The retention
job still keys off the NSE session. `GET /api/system/status` exposes all three sessions.

### O3: Browser push and SMS channels (V3.13)

`app/alerts/webpush.py` implements RFC 8291 (`aes128gcm` payload encryption: ephemeral P-256
ECDH, HKDF with the subscription's auth secret, AES-128-GCM) and RFC 8292 (VAPID ES256 JWT) on
top of `cryptography`, so no native dependency is needed. The platform's VAPID key is
configuration (`VAPID_PRIVATE_KEY`, generate with `python -m app.alerts.webpush`). A PUSH channel
holds every device of a tenant; the Settings card registers `public/sw.js`, subscribes and posts
the subscription; the dispatcher prunes endpoints the push service reports gone (404/410).
SMS is a request template for any HTTPS gateway (`SmsConfig`: URL, write-only headers, body
template with `{to}`/`{text}`/`{title}`/`{severity}`, recipients); presets for MSG91 and Twilio
are in the UI. Paid plans allow five channels. `docs/OPERATIONS.md 1.6e` covers both.

### O4: Chaos tests, load baseline, AI and billing metrics (sections 49, 50, 54, V4.11)

`tests/test_phase_o_hardening.py` injects the failures the runbooks talk about: Redis down (the
single worker keeps trading; the lock fails open), a broker socket error mid-order (order FAILED,
tenant flagged broker-uncertain, no retry without reconciliation), a candle API timeout (recorded
on the deployment, cycle continues) and an alert-outbox failure (trading unaffected).
`scripts/loadtest.py` measures the API against SLO-1 and `docs/PERFORMANCE.md` records the
baseline. New Prometheus series: `atp_ai_provider_calls_total{provider,outcome}`,
`atp_ai_proposals_total{action}`, `atp_ai_decisions_total{decision}`,
`atp_billing_payments_total{source}`, `atp_billing_transitions_total{transition}`.

### O5: Point-in-time recovery (section 52)

The compose `postgres` service runs with `archive_mode=on` and copies every WAL segment (at most
five minutes old, `archive_timeout=300`) into the `wal_archive` volume. `scripts/backup/base_backup.sh`
takes a weekly physical base backup (`pg_basebackup`, tar+gzip, SHA-256, retention, WAL pruning);
`scripts/backup/pitr_restore.sh <base|latest> "<time>" <new data dir>` unpacks a base backup and
writes the recovery settings so a fresh Postgres replays the archive to that instant and promotes.
Nightly logical dumps stay as the provider-independent copy. RPO is now the archive interval
(minutes) instead of a day; the per-data-class table is in OPERATIONS 1.1.

## Phase P: Safety backstop, tax, currency, drift and staging

### P1: Protective-stop guard (section 52)

`app/trading/stop_guard.py::verify_protective_stops` reads the broker order book once per tenant
and, for every open LIVE single-leg trade, checks that its SL-M is standing (OPEN / TRIGGER
PENDING). A stop that is missing (placement failed at entry), cancelled or rejected is re-placed
on the opposite side at the trade's stop, the trade's `sl_order_id` is updated, an audit row
`protective_stop_rearmed` is written and the user gets a WARNING; a stop the exchange already
filled is left to the position monitor to book. Placement failures raise one CRITICAL per trade
per 30 minutes and never touch the position. It runs at worker start-up after reconciliation and
once per cycle per tenant with a LIVE broker (`CycleReport.stops_rearmed`). Multi-leg structures
are skipped: their legs are protected as a group by the monitor.

### P2: Financial-year tax report (sections 57-61)

`app/tax/report.py` aggregates closed trades of an Indian financial year (1 April to 31 March,
IST) into the three income heads the Income Tax Act uses: equity intraday (speculative business),
F&O including MCX (non-speculative business) and crypto/VDA (section 115BBH: 30% on gains, no loss
set-off, 1% TDS under 194S). Turnover follows the ICAI guidance note (absolute profit/loss, plus
premium on options sold). STT/CTT/TDS are independent estimates at the rates in `RATES` for
reconciliation with the broker's annual statement; the per-trade `charges` already recorded are
reported alongside. `GET /api/tax/years|report|report.csv` (LIVE by default, PAPER or ALL on
request); the Analytics page has the card and the CSV download. FIU-IND reporting is an
exchange-side obligation and is documented as such, not implemented.

### P3: Base currency and FX (section 57)

`tenants.base_currency` (INR default, owner-editable from Team) and `ContractSpec.quote_currency`
(INR for every current instrument) are the two ends of `app/fx/service.py::convert`, which uses
a direct rate, its inverse or the INR pivot from the operator-maintained `fx_rates` table
(`PUT /api/admin/fx-rates`, `GET /api/fx/rates`). Portfolio exposure converts each symbol's price
into the tenant's base currency before aggregating and reports `base_currency`, `fx_rates_used`
and `fx_missing`. With INR-only instruments and tenants nothing changes; a USDT-quoted pair or a
USD-reporting desk now has a place to plug in.

### P4: Drift gate (section 50)

The monitoring agent gained a `DEGRADATION` rule: when the degradation engine (win rate,
expectancy and profit factor of the live record versus the strategy's latest saved backtest)
returns DEGRADED, the agent proposes `PAUSE_DEPLOYMENT` with the reasons as evidence. As with
every proposal, a human approves or rejects; nothing pauses on its own (ADR 0006).

### P5: Staging and rolling deploy (section 51)

`docker-compose.staging.yml` overlays the production compose file with separate ports, database,
volumes and a staging banner; `scripts/deploy.sh staging|production [ref]` builds, runs the
migration guard in a one-off container, restarts the API and waits for the deep health check,
then restarts worker and frontend, printing the running versions (rolling on one host, not
blue-green: one database, one worker replica by design). `.github/workflows/deploy-staging.yml`
runs it over SSH on pushes to main when the `STAGING_ENABLED` variable and SSH secrets exist.
Cloud infrastructure-as-code stays out of the repository until a target cloud is chosen.

## Phase Q: Order pre-checks (section 17)

Section 17 lists what a new entry must prove before placement: data fresh (G1), broker healthy
(G2), risk approved (risk engine + I1 hierarchy), **margin available** and **instrument/expiry
valid**. `app/execution/prechecks.py` closes the last two, between the kill-switch refusals and
the risk engine in `execute_signal_for_user`:

* `validate_instrument` - a resolved contract whose expiry has passed is refused in every mode
  (and one expiring today is noted). LIVE only: an index spot symbol (`NIFTY 50`, `NIFTY BANK`,
  `SENSEX`...) is refused with the instruction to set option/future contract rules, and a plain
  symbol is looked up in the broker's instrument master when that master has been synced -
  unknown symbol, INDEX row or past expiry refuse; a master that was never synced for the broker
  only notes that the symbol was not verified. MCX/crypto registry instruments skip the master.
* `live_margin_cap` - generalises F3's `written_lot_cap` to every LIVE entry. It asks the
  broker's margin calculator for one lot (or one share) - Zerodha basket margin, Upstox
  `/charges/margin` - and caps the size at `MARGIN_SAFETY` (80%) of available margin, refusing
  when even one unit is not covered or when the funds endpoint cannot be read (safety rule 7).
  A bought option without a calculator answer uses premium x lot size, which is the exact cash
  the buy consumes. Equity or a future without a calculator answer is *not* guessed (intraday
  leverage differs per broker and per stock): the trail notes "margin not verifiable" and the
  broker enforces it at placement, exactly as before. Written options keep F3's strict rule.

Both are business decisions, so the order trail records REJECTED (never FAILED) with the reason,
the user gets a WARNING notification, and the verification notes ("verified against upstox's
instrument master", "Margin 500/share, 10,000 available -> at most 16") are prepended to the
order's reasons. The router now reports `partial_fill` and `requested_quantity`, and a fill
smaller than requested passes through `PARTIAL_FILL` on the order trail before `FILLED`
(safety rule 17 on the order, not only on the trade).

## Phase R: Option structures depth (sections 23-25, V2.1-2.6)

`app/instruments/spreads.py` grew from three structures to nine, all built at signal time from
the deployment's rules and executed by the same `execute_structure` group pipeline:

| Structure | Legs | Signal | Economics | Sizing basis |
| --- | --- | --- | --- | --- |
| Bull put / bear call spread (H2) | short + wing | LONG / SHORT | credit, max loss = width - credit | max loss |
| Iron condor (H2) | 2 shorts OTM + 2 wings | either | credit, defined | max loss |
| **Iron butterfly** | 2 shorts ATM + 2 wings | either | credit, defined | max loss |
| **Short straddle** | 2 shorts ATM, no wings | either | credit, **undefined** | the stop (`stop_credit_pct` of the credit) |
| **Short strangle** | 2 shorts OTM, no wings | either | credit, undefined | the stop |
| **Long straddle** | 2 longs ATM | either | **debit** = max loss, profit open | the debit |
| **Long strangle** | 2 longs OTM | either | debit | the debit |
| **Calendar spread** | short rule expiry ATM + long next expiry, same strike (PE on LONG, CE on SHORT) | either | debit | the debit |

`StructureMetrics` carries `debit`, `defined_risk`, `risk_per_unit` (what the sizer divides
risk-per-trade by), `max_loss` (None when undefined) and `max_profit` (None when open-ended),
so the executor, the risk hierarchy and the preview all reason from one number. A debit
structure quoting as a net credit, or a credit one as a net debit, is refused as inconsistent
quotes, never entered.

Group exits (`position_monitor.group_exit`) gained two rules. A debit structure is judged on its
*worth* (longs minus shorts): close at worth >= debit x (1 + target%) or <= debit x (1 - stop%),
with stop% capped at 100 (the whole debit; the deployment API rejects more). A structure whose
shorts sit at the money (short straddle, iron butterfly) cannot use "underlying through the
short strike" - it would fire on entry - so `underlying_exits` holds its breakevens and the
group closes beyond either one; spreads, condors and strangles keep the short-strike breach.

LIVE margin: the short legs' broker number as before; a long-only structure (long straddle or
strangle) blocks exactly its debit, so `_live_lot_cap` uses debit x lot size (Phase Q's premium
rule). Placement order is unchanged: long legs first, shorts second, so a calendar buys the far
expiry before selling the near one and no short is ever naked. The deployment API sets
`option_position` BUY for debit structures and WRITE for credit ones, `spread_width` applies to
winged structures only, and `describe_structure` names each structure and its risk character
on the deployment card. The Autopilot form lists all nine with the target/stop inputs labelled
"% of credit" or "% of debit" and an undefined-risk warning where it applies.

Still not built: ratio spreads, long butterflies and a free-form leg builder (an explicit legs
list on the deployment instead of rules), and historical option-chain backtests.

## Phase S: Streaming quotes (sections 8 and 10)

Every price the worker used before this phase was a REST poll: one call per symbol per cycle,
through the rate budget, at most once a minute. `app/market_data/stream.py` adds the broker
websocket feeds without changing any decision rule:

* **`TickCache`** is the hand-off. Streams write the newest tick per (broker, exchange, symbol);
  `MarketDataService.get_ltp` reads it first and uses it when it is younger than
  `TICK_MAX_AGE_SECONDS` (default: the Phase G1 quote limit), otherwise falls back to the REST
  quote exactly as before. Process-local with a Redis mirror (short TTL) so the API process can
  show the same price; Redis absent means REST, never a stale value.
* **`TickStream`** owns one websocket: connect, subscribe, decode, reconnect with backoff
  (1-2-5-10-30 s), resend the whole subscription on every new socket, apply subscription changes
  on the live socket. The connection object is injectable, so the loop is tested against a
  scripted fake; the decoders are pure functions on bytes.
* **`UpstoxTickStream`**: Market Data Feed V3 - the authorised wss URL from
  `GET /v3/feed/market-data-feed/authorize`, a JSON `sub` frame in `ltpc` mode keyed by
  instrument key, protobuf `FeedResponse` frames. A minimal protobuf wire-format reader extracts
  LTPC (ltp, last trade time, close) from `ltpc`, `fullFeed.marketFF/indexFF.ltpc` and
  `firstLevelWithGreeks.ltpc`, so there is no generated `_pb2` module and no `protobuf`
  dependency; `encode_upstox_v3` is the test-side inverse.
* **`KiteTickStream`**: Kite Connect's ticker at `wss://ws.kite.trade?api_key=..&access_token=..`,
  JSON `subscribe`/`mode full` frames by instrument token, binary quote frames (big-endian,
  paise, 1e7 for currency derivatives, exchange timestamp at offset 60 of the full packet).
* **`StreamManager`** in the worker keeps one stream task per `<tenant>:<broker session>`,
  resubscribed every cycle to each deployment's symbol and every open position's contract and
  underlying (`_stream_quotes`). `CycleReport.streams_connected` counts connected sockets;
  `ticks_received_total` and `stream_reconnects_total` are exported per broker.

Off by default (`STREAMING_QUOTES_ENABLED=false`): the worker polls REST as before. Turned on, a
symbol the stream cannot resolve, a broker with no stream (Shoonya and the stubs), or a socket
that has gone quiet all degrade to the REST poll, and the staleness gate still refuses a tick
whose exchange time is older than the limit. `BrokerInterface.subscribe_market_data` now points
at `stream_for(adapter)`; the worker, not the adapter, owns the connection.

Verified here against hand-built frames and scripted sockets only; the first live run against
Upstox should confirm the authorise response key and the LTPC field numbers before the flag is
left on in production (OPERATIONS 1.6h).

## Phase T: Broker-selection rules (V3.1-3.5)

Phase I2 gave a tenant several broker accounts and let a deployment name one; the account was
fixed when the deployment was created. `app/accounts/routing.py` makes the choice at signal
time from what the accounts look like now:

* **Policies** (`RoutingPolicy`): `EXPLICIT` (unchanged default - the deployment's account, else
  the broker's default), `MOST_MARGIN` (largest synced available balance), `LEAST_UTILISED`
  (smallest `used / (used + available)`), `FEWEST_POSITIONS` (fewest open LIVE positions, ties
  to the larger balance). A deployment carries its own policy; the tenant's
  `default_routing_policy` (Team page) applies when it has none. Candidates are the ACTIVE
  accounts at the deployment's broker, or at every broker the tenant has a session for when
  `route_across_brokers` is set. PAPER deployments cannot carry a policy (400).
* **Fresh numbers only**: `choose_account` is a pure function; a capital policy trusts a balance
  synced within `ROUTING_MAX_SYNC_AGE_SECONDS` (15 min) and otherwise falls back to the default
  account with a note that says so - a stale figure never decides where money goes. The worker
  refreshes every ACTIVE account through its own session once per `ACCOUNT_REFRESH_SECONDS`
  (5 min) *before* routing (`_refresh_accounts`), so the decision is made on this cycle's
  balances; a failed pull is recorded on the account row (`last_sync_error`).
* **The decision is recorded**: `strategy_deployments.last_route` ("account #7 (upstox/second)
  by MOST_MARGIN: 3,00,000 available") is shown on the Autopilot card; every trade stores the
  account it was placed in (`trades.broker_account_id`).
* **Exits follow the trade's account.** Before this phase the worker closed, squared off and
  stop-guarded every LIVE position through the first LIVE session it held - wrong the moment a
  tenant had two accounts. Now the worker builds one adapter per ACTIVE account with a usable
  token (not only the accounts a deployment names), and with more than one account it passes no
  broker to the monitor and the square-off: `position_monitor.broker_for_trade` resolves the
  trade's own account (then the deployment's broker, then the single credential), building each
  adapter once per cycle (`session.info["trade_brokers"]`). `verify_protective_stops` takes an
  `account_id` scope and is run per account against that account's order book, trades recorded
  before accounts were tracked going with the broker's default account. Reconciliation is still
  one session per tenant (the first LIVE one); a multi-account reconciliation is the remaining
  gap and is noted in the gap analysis.

Migration `b4d6f8a1c3e5` adds `routing_policy`, `route_across_brokers`, `last_route` on
deployments, `default_routing_policy` on tenants and the indexed `broker_account_id` on trades.

## Phase U: Ratio spreads, long butterfly and the free-form leg builder (sections 23-25)

Phases H2 and R gave each structure a hand-written rule for its economics. The three items the
gap analysis still listed under sections 23-25 cannot be priced that way: a ratio spread's max
loss depends on how many more it sells than buys, a butterfly's on its wings and debit, and a
free-form leg set on whatever the user put in it. `app/instruments/payoff.py` prices them from
the expiry payoff instead:

* **`analyse(legs)`** takes `PayoffLeg(right, role, strike, premium, ratio)` for a same-expiry
  set and evaluates the piecewise-linear P&L per unit at S = 0 and at every strike. The slope
  beyond the highest strike (the CE legs' signed ratios) says whether the upside is bounded,
  the slope below the lowest strike (the PE legs') whether the downside is; the roots are the
  breakevens. `max_loss` is None when either side is unbounded, `max_profit` when the upside
  is; `peak` is the best finite outcome.
* **Structures** (`OptionStrategy`): `CALL_RATIO_SPREAD` (buy 1 CE at the rule strike, sell 2
  `spread_width` steps higher; LONG signals only; undefined risk above), `PUT_RATIO_SPREAD`
  (the mirror on SHORT signals), `LONG_BUTTERFLY` (buy wing / sell 2 ATM / buy wing, CE on a
  LONG lean, PE on a SHORT one; a debit with defined risk) and `CUSTOM`, whose legs come from
  the deployment's `custom_legs` (2-6 of `{right, role, strike_rule, strike_offset, ratio}`,
  all on the deployment's expiry rule; strike filters do not apply; two legs resolving to one
  contract are refused rather than netted).
* **Leg ratios.** `ResolvedLeg.ratio` (1:2, 1:2:1) multiplies the lot quantity per leg through
  the executor: one order per leg with its own quantity, LIVE margin probed at the leg's
  quantity, the unwind and the trade records at the same. `group_meta.quantity` stays the 1x
  leg's and the position monitor weights each leg by `quantity / that`, so the older
  structures (all ratios 1) are unchanged.
* **Exits as P&L per unit.** A ratio spread can be entered for almost nothing, where "50% of
  the credit" means nothing; these structures carry `pnl_target` / `pnl_stop` in their metrics
  and `group_exit` judges `net_credit - value` against them. Target and stop are percentages
  of the *risk basis*: the max loss when defined (stop default 50%, capped at 100%), else the
  peak profit (stop default 100%: risk what it can make). An unprotected side also gets an
  underlying exit at that side's breakeven (`underlying_exits`). Sizing divides risk per trade
  by the max loss when defined, else by the loss the stop accepts, as Phase R did for the
  short straddle.

A leg set that cannot profit at expiry at the quoted premiums, or a defined-risk set that shows
no loss (inconsistent quotes), is refused before any order. Migration `c5e7a9b2d4f6` adds
`strategy_deployments.custom_legs`. The Autopilot form gains the three structures and a leg
builder (buy/sell, ratio, ATM/ITM/OTM + steps, CE/PE per leg); the preview shows each leg's
ratio, the computed max loss/profit and the P&L exit levels.

## Phase V1: Risk Guardian engine rules

The AI strategy builder's "Pro Trader Risk Guardian" spec lists the rules every strategy must
obey and says the hard ones must live in the execution engine, not only in the prompt. Most
already did: a stop before every entry, size derived from risk (never from premium or margin),
strictest-wins limits, a stop that only tightens (`exit_rules.py`), defined-risk structures
with undefined-risk ones sized off the stop. `app/risk_engine/guardian.py` adds the rest, run
on every entry - single leg and multi-leg, PAPER and LIVE, whatever built the strategy:

* **R10 cool-down.** No re-entry in an underlying for `stop_cooldown_minutes` (default 30)
  after a position in it closed on a stop: the stop level, a premium floor/ceiling, a structure
  stop, a breached short strike. Target, time and square-off exits start no cool-down, and a
  LIVE stop-out does not cool PAPER down (modes are separate).
* **P2/P3 drawdown ladder.** Equity per mode = capital + realised P&L of every closed trade;
  the peak is the high-water mark. `dd_level_1_pct` (5%) below it the risk per trade is
  halved; `dd_level_2_pct` (10%) below it new entries are paused until equity recovers or the
  level is raised after a review. The multiplier is never above 1, so a hot streak never
  raises size (P4).
* **M8 event blackout.** `market_events` rows - a tenant's own, or global ones the operator
  keeps - name a date, an optional IST time window, an underlying (or `INDEX` for the whole
  index bucket, or every symbol) and an action: BLOCK refuses entries, SIZE_CUT scales the risk
  per trade by `1 - size_cut_pct/100` (default the tenant's `event_size_cut_pct`, 50%).
* **R4 portfolio risk with correlated buckets.** After sizing, the loss if every open stop hits
  plus this trade's max loss must stay within `max_portfolio_risk_pct` (6%) of capital.
  NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY, SENSEX and BANKEX form one `INDEX` bucket, so index
  short-vol positions never count as diversified; a multi-leg group counts once at its
  structure's risk per unit. The refusal names the bucket the trade would join.
* **Ceilings.** `platform.controls.risk_ceilings` (risk per trade 2%, daily loss 5%, portfolio
  risk 10%, pause level 25%, minimum cool-down 0) are SUPER_ADMIN settings under
  `/api/admin/controls/risk-ceilings`. The risk-settings API refuses values above them and the
  engine clamps saved settings at runtime, noting the clamp on the order.

Where it sits: `execute_signal_for_user` and `execute_structure` clamp the tenant's
`RiskConfig`, run `guard_entry` (refusal = REJECTED order with a RISK_REJECTION notification;
size cut = a smaller `risk_per_trade_pct` for the sizer), then check `portfolio_risk_block`
next to the Phase I1 hierarchy before placement. `GET /api/risk-guardian/status` reports the
drawdown, state and multiplier per mode, open risk by bucket, active cool-downs, today's
events and the ceilings - the Risk page shows it, and Phase V3 feeds it to the AI as runtime
context. R6 (never add to a loser) holds by construction: one open position per deployment and
no add-to-position path. Migration `d6f8b1c3e5a7`.

## Phase V2: Compliance validator on AI drafts

The spec's section 3: "do not trust the AI alone". `app/ai/compliance.py` re-checks every draft
the generator produces, before it can be backtested or approved:

* **Draft-level rules** are checked on the `CustomStrategyConfig`: R1 (a stop before entry), M2
  (the stop at least `MIN_STOP_ATR_MULT` = 1 x ATR, outside normal noise), M1 (targets ordered
  and at or above the minimum R:R); a minimum R:R below 1:1 is a warning.
* **Engine-enforced rules** (R3/R9 size from risk, R4 portfolio cap, R5 daily kill switch, R6,
  R7, R10 cool-down, M8 events, P2-P4 drawdown ladder) are reported PASS with the tenant's
  effective values, so the checklist is complete; R8 (defined risk), M4/M5 (break-even,
  trailing) and M9 (regime filter) are N/A with a pointer to where they are set.
* **One AI auto-fix round, then deterministic fixes.** `generator.generate` runs the checklist
  after parsing. A failure on the first attempt goes back to the model with the request ("it
  violated the risk rules - M2: ..."); on the last attempt the fixes are applied to the config
  (stop raised to the minimum, targets re-ordered/raised) and listed in `compliance.fixes`,
  so what is saved is compliant either way and the correction is on the record.
* **"User must accept."** The report carries the maximum loss per trade in currency and % of
  capital and the worst case (a gap through the stop at `GAP_MULTIPLE` = 3 x the planned loss,
  the daily loss limit, the drawdown ladder). `approve` refuses without `accept_risk: true`
  and quotes the statement; the AI Copilot page shows the checklist and requires the tick.
* **Evidence, plainly.** When a backtest is attached the metrics are judged (`assess_evidence`:
  fewer than 30 trades, a losing result, a thin profit factor, a drawdown over 15% of capital)
  as E1 warnings that stay on the draft - the AI never flatters a strategy. A draft with any
  FAIL cannot be approved.

Stored in `ai_strategy_drafts.compliance_json` (migration `e7a9c2d4f6b8`) and returned as
`compliance` on every draft.

## Phase V3: The guardian system prompt, runtime context and schema mapping

`app/ai/prompt.py` replaces the Phase L2 one-paragraph prompt with the spec's "AMW Strategy
Architect" template, versioned (`PROMPT_VERSION`) and filled on every request:

* **Runtime context** (`build_runtime_context`): capital and currency, the risk profile derived
  from the risk per trade, the allowed instruments (the requested symbol first), open risk and
  drawdown from the guardian status (LIVE when the tenant has a live book, else PAPER), a
  one-line summary of the last ten closed trades (wins/losses, net, stop-outs, a losing
  streak), the market events of the next seven days, the regime the page read and the user's
  language. Stored on the draft (`context_json`) with the prompt version, so a later review sees
  what the model was told. `GET /api/ai/context` shows it to the user first.
* **The prompt** states what the engine enforces (size from risk, the caps, the cool-down, the
  drawdown ladder, event days, the 1 x ATR stop floor) as facts the model describes rather
  than promises it makes, and the non-negotiables for what it may propose: a stop before entry,
  targets at or above the minimum R:R, defined-risk option selling unless the user explicitly
  accepts unlimited risk, "no trade" when the regime is wrong, and slowing down on tilt. It
  answers in the user's language; JSON keys and enums stay English.
* **Schema mapping.** The spec's STRATEGY_SCHEMA is wider than what the platform deploys, so the
  answer is `config` (the platform's `CustomStrategyConfig`, unchanged) plus `deployment` - a
  `DeploymentSuggestion` of Autopilot settings: instrument kind, option structure (any
  `OptionStrategy`), position, expiry and strike rules, spread width, credit target/stop, exit
  rules (break-even, trailing, time), regime filter and `next_step`. Parsing is tolerant: an
  unknown enum, an out-of-range value or an unknown regime is dropped with a warning, never
  guessed. Sizing, portfolio caps and the ladder are never asked of the model.
* **Compliance reads the suggestion** (Phase V2 extended): R8 PASS for a bought option or a
  defined-risk structure, WARN for a short straddle/strangle, ratio spread or naked write; M4/M5
  PASS when break-even/trailing are proposed; M7 checks a credit structure's capture (30-80%)
  and stop (<= 200% of the credit) against the spec's 50-70% / 2-3x premium; M9 PASS with a
  regime filter. The AI Copilot page shows the suggestion as "Suggested Autopilot settings" to
  copy into the deployment form.

Migration `f8b1d3e5a7c9` (prompt_version, context_json, deployment_json on drafts). The
rule-based fallback provider answers under the same prompt without a deployment block.

## Phase W: Historical option-chain backtests (sections 31-32, V2.1-2.6)

Until now a backtest traded the underlying only; an option deployment's strikes, expiries,
credits, group exits and lot sizing were exercised live and on paper but never on history. Phase
W runs the same structures on historical bars.

* **One planner for live and backtest.** `app/instruments/spreads.py` gains `structure_sides`,
  `plan_side` and `plan_structure`: the strike math of every structure (primary strike per right,
  wings, the 1:2 ratio short, the butterfly's 1:2:1, the calendar's far leg, custom legs) as a
  pure function of the spot and a strike ladder. `resolve_structure` now calls it and only adds
  the instrument-master lookups and chain filters, so a backtest and a deployment cannot pick
  different strikes for the same rules (`tests/test_phase_w_option_backtest.py` asserts parity).
  Likewise `position_monitor.structure_exit_reason` / `underlying_exit_reason` are the group exit
  rule on its own; `group_exit` computes the group's value and delegates.
* **Pricing** (`app/backtest/options.py`). `OptionPricer.price(right, strike, expiry, spot, at)`
  with two sources. `SnapshotPricer`: real quotes (recorded or uploaded rows), the latest at or
  before the bar within `max_age_minutes`; a missing quote refuses the entry unless a fallback is
  given. `SyntheticPricer`: Black-Scholes (the Greeks module) off the bar's underlying level, the
  time to the expiry's 15:30 IST close and a `VolatilityModel` - a fixed IV the user asks for or
  the annualised realised volatility of the trailing closes (floored 8%, capped 150%); at the
  close of expiry day the premium is the intrinsic value; premiums round to the 0.05 tick. The
  result carries the model's name and a disclaimer: synthetic premiums have no smile, no bid/ask
  and no liquidity, so they show structure mechanics, not an edge.
* **Conventions** are the exchange's current ones and overridable per run, because they changed:
  lot sizes (NIFTY 75, BANKNIFTY 35, FINNIFTY 65, MIDCPNIFTY 140, SENSEX 20, BANKEX 30), strike
  steps (50/100/50/25/100/100; stocks by price band), weekly expiries only for NIFTY (Tuesday)
  and SENSEX (Thursday), everything else monthly on the last Tuesday (BSE: Thursday).
  `ExpiryCalendar` lists them and moves a holiday expiry to the previous trading day; the API
  loads the platform's holiday table into it. A NIFTY backtest across 2024 passes
  `expiry_weekday=3` (Thursday) to match that year's listings.
* **Engine** (`app/backtest/options_engine.py::run_option_backtest`). The strategy signals on the
  underlying as before. On a signal: expiry from the calendar and the rule, strikes from a ladder
  around spot, `plan_structure`, every leg priced, `structure_metrics` (unchanged) for the credit,
  max loss, breakevens and target/stop levels, then lots exactly as `execute_structure` sizes
  them - risk per trade over the risk per unit through the risk engine, capped by `max_lots`
  (a written single option without a cap trades one lot, as live). Fills take the paper broker's
  slippage. Every later bar: expiry settlement at intrinsic value on expiry day's last bars (a
  calendar's far leg stays priced), time exits from the shared `ExitRules`, the intraday
  square-off at 15:15 IST and no entries after 15:00 (the worker's cut-offs; `intraday=False`
  holds to exit or expiry), the underlying levels (short strikes, breakevens) checked on the bar's
  low/high and filled at the level, then the structure's own value at the close through
  `structure_exit_reason`. A SINGLE option mirrors Phase F4: premium floor/ceiling on the option,
  stop/targets on the underlying. One `Trade` per structure (entry = credit or debit per unit,
  exit = cost to close, P&L net of per-leg option charges) keeps analytics, Monte Carlo and
  walk-forward unchanged; `BacktestResult.options` carries the pricing model, conventions, the
  skipped-signal tally (sizer refusals, no quote, quotes inconsistent) and every structure's legs.
* **Recorded chains** (`app/backtest/chain_recorder.py`, table `option_chain_snapshots`,
  migration `a9c1e3f5b7d9`). The worker samples the chains of the underlyings its ACTIVE option
  deployments trade: one fetch per underlying per `CHAIN_SNAPSHOT_INTERVAL_MINUTES` (default 5;
  0 disables), `CHAIN_SNAPSHOT_ATM_SPAN` strikes either side of the money (default 12), only
  while the venue is open, only rows with a real LTP. Platform-wide reference data (a NIFTY quote
  is the same fact for every tenant), deduplicated across tenants by interval, trimmed by
  retention after `RETENTION_CHAIN_SNAPSHOTS_DAYS` (default 400). Tenants can also upload rows
  (`POST /api/backtest/option-chain/snapshots`); coverage and raw rows are readable.
* **API.** `BacktestBody.options` (an `OptionBacktestBody`: the deployment's option fields plus
  `pricing` synthetic|snapshots|uploaded, IV, conventions, `intraday`) on `POST /api/backtest`,
  `/monte-carlo` and `/walk-forward`; one `BacktestRunner` dispatches, so a run record stores
  `engine_version` `3-options`, the option config under `params._options` and the summary in
  `metrics.options`. `pricing=snapshots` loads the recorded rows for the candle span and falls
  back to synthetic when allowed (counted in the result), else refuses with a 400.
* **UI.** The Backtest page gets "Trade as: Options" - structure, position, expiry/strike rules,
  width, target/stop or premium floor/ceiling, max lots, the custom-leg builder, pricing source
  (with a CSV upload that can be stored as platform history), IV, conventions, intraday - and an
  "Option structures" card: pricing model, lot/step, calendar, skipped signals and a per-structure
  table with legs, credit/debit, max loss, exit reason and P&L.

## Phase X: Marketplace revenue share and creator payouts (V3.9-3.10)

Phase K2 made the marketplace free: a listing is a frozen strategy version, subscribing copies it,
unsubscribing keeps the copy. Phase X lets a creator charge for that copy and be paid for it,
without the platform ever holding money.

* **A one-time price fits the copy semantics.** `marketplace_listings.price` (INR, 0 keeps the
  listing free) with `platform_fee_pct` frozen at publish from the operator's terms, so a later
  change of terms never re-prices a live listing. The price can be changed only while the
  listing is not live (DRAFT/REJECTED/UNLISTED). Terms (`platform_fee_pct` 20, `min_payout` 500,
  `max_listing_price` 50000) are a platform control (`/api/admin/controls/marketplace-terms`,
  SUPER_ADMIN) and readable by creators (`GET /api/marketplace/terms`).
* **Charges** (`marketplace_charges`, `app/marketplace/billing.py`). Buying a paid listing opens
  one charge (gross, fee, creator net, provider, reference) and a `PENDING_PAYMENT` subscription;
  `POST /{id}/subscribe` answers 202 with the charge and, under a gateway, the hosted checkout
  link. The copy is made only when the charge is PAID - by the operator confirming an
  out-of-band transfer (`POST /api/admin/marketplace/charges/{id}/paid` with the reference,
  manual provider) or by the gateway's webhook. A repeated purchase returns the same open
  charge; settling is idempotent; an OPEN charge can be voided (the pending subscription is
  cancelled, a new purchase opens a fresh charge). Both sides are notified on settlement.
* **Gateway seam.** `BillingProvider.create_payment_link(amount, currency, description,
  reference_id, notes, customer_email)`: the manual provider returns no URL; `RazorpayProvider`
  creates a Razorpay Payment Link (`/v1/payment_links`, `reference_id = mpc-<charge id>`). The
  existing signed, idempotent webhook (`/api/billing/webhooks/razorpay`) routes `payment_link.*`
  events to `handle_payment_link_event`, which matches the charge by our reference (or the
  notes we attached), checks the link id and the amount paid, and settles. Underpaid, unknown
  or mismatched events are noted, never booked.
* **Earnings and payouts.** A PAID charge is the creator's earning row (`creator_net`). `GET
  /api/marketplace/earnings` sums gross, fees, net, available (not yet in a payout), pending and
  paid out, and lists sales (buyer as a tenant number only) and payouts. An OWNER requests a
  payout (`POST /api/marketplace/payouts`) of everything available once it reaches the minimum;
  one request at a time. The destination the creator types (UPI id or IFSC/account) is stored
  encrypted under the tenant's key (`secrets_store.encrypt_text`) with only a `…1234` hint in
  clear; the operator reads it through an audited endpoint when making the transfer, then marks
  the payout PAID with the transfer reference (required) or REJECTED with a note, which releases
  the earnings. `GET /api/admin/marketplace/revenue` totals the platform's side.
* **UI.** Marketplace page: price on every card and a "Buy for ₹…" button that shows the checkout
  link or the manual-payment instruction; "Your purchases" with open charges and pay links; a
  price field on the publish form with the creator's share; "Creator earnings" with the payout
  request; for the operator, revenue totals, editable terms, open charges to confirm/void and
  the payout queue (show destination, mark paid, reject).

Migration `b1d3f5a7c9e2` (price/currency/fee on listings, wider subscription status,
`marketplace_charges`, `marketplace_payouts`). Tests: `tests/test_phase_x_marketplace_revenue.py`.

## Phase Y: The AI scanner (V4.2)

The Market Scanner stays what it was: a deterministic function of candles, chain and filters
(`app/scanner/engine.py`). Phase Y puts the tenant's LLM on either side of it and nowhere else -
the master prompt's "scanner is not an order" and "AI is not an order" both hold by construction,
because neither call touches the execution path.

* **Plan** (`app/scanner/ai.py::plan_scan`, `POST /api/scanner/ai/plan`). Plain language becomes a
  `ScanPlan`: indicator conditions in the Strategy Builder's `Condition` DSL, structure filters and
  option filters, plus the timeframe and any symbols the request names. The system prompt lists the
  real enums (indicators, operators, structure and option filter types, timeframes) so the model
  cannot invent a filter; `parse_plan` validates each filter on its own and drops a bad one with a
  warning rather than guessing (volume, fundamentals and news land in the warnings as "cannot be
  screened"). The page loads the plan into its editors; the user reviews and presses Run on the
  ordinary `/api/scanner/run`. Without an external provider (none configured, or the plan lacks AI
  features) `rule_based_plan` does the same job deterministically: the NLU parser for indicator
  conditions, keyword rules for structure ("uptrend", "near support", "bullish break") and option
  ("PCR above 1.2", "put writing", "max pain") filters, index words and uppercase tokens for symbols.
  A model that answers unusably falls back to that parse with a warning naming it.
* **Read** (`read_scan`, `POST /api/scanner/ai/read`). The scan's request and result go back; for
  each match the engine's own labels, the close and the regime `classify_regime` reads on that
  symbol's candles (kind, confidence, the numbers behind it) are handed to the model, never the
  candles themselves. The model ranks and explains (thesis, what invalidates it, next step);
  `parse_read` drops symbols the scanner did not match, caps an UNKNOWN-regime symbol at 60, notes
  matches the model skipped, and the fixed disclaimer rides every answer. `rule_based_read` gives a
  transparent score when there is no model: 10 per filter category matched, +15 when the regime
  agrees with the labels' direction, -20 when it conflicts, -10 in a VOLATILE regime, capped at 60
  when the regime is unknown.
* **Envelope.** Both endpoints need a trader login, sit behind the `ai_copilot` operator flag,
  meter `ai_scanner` usage, write audit rows (`ai_scan_planned`, `ai_scan_read`) with the provider
  and model, count provider calls in `ai_provider_calls`, and record provider errors on the tenant's
  AI settings like the generator does. Prompts are versioned (`scanner-v1.0`). At most 40 matches
  are read per call.
* **UI.** Scanner page: a "Describe the scan (AI)" box that fills the filter editors and shows the
  plan's explanation, provider and warnings; an "AI read of these matches" button on the results
  with the ranked list, regime per symbol, risks, next step and disclaimer.

No schema change. Tests: `tests/test_phase_y_ai_scanner.py`.
