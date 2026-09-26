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
