# Advance Trading Platform

An algorithmic trading platform for Indian markets (NSE cash, F&O, indices), built in phases.
So far this delivers the **strategy, risk, execution, broker-abstraction, price-action,
option-chain, signal-scoring, and database/auth core**: indicators, inbuilt auto-executable
multi-timeframe and indicator-based intraday scalping strategies, a risk engine, a paper
execution router, a broker-agnostic `BrokerInterface` with real Zerodha, Upstox, and Shoonya
adapters (Angel One/Fyers/Dhan registered as pluggable stubs), a market-structure +
candlestick-pattern engine, a support/resistance zone engine (swing clusters, prev day/week,
opening range, VWAP, pivots, Fibonacci), an option-chain intelligence engine (PCR, Max Pain,
ATM/ITM/OTM, OI buildup/unwinding, bias), a weighted-composite signal scoring engine that ties
all three analysis engines together into one "why this trade" score, PostgreSQL persistence
with JWT auth and Fernet-encrypted broker credential storage (unlocking a real
`POST /api/broker/{name}/authenticate` login flow), a backtest engine, and a FastAPI service
exposing all of it.

A Vite + React + TypeScript + Tailwind frontend console (`frontend/`) now sits on top of that
API — Dashboard, Strategy Library, Signals (a real TradingView `lightweight-charts` candlestick
chart with entry/SL/target lines and support/resistance zones, plus the full "why this trade"
score breakdown), Backtesting (equity curve + trade log), Option Chain, Positions, and Account
(login/register) pages, all wired to real backend computation over a clearly-labeled sample
dataset (no live broker is connected yet). Signing in is optional everywhere except Positions -
it additionally persists your paper-execute fills to PostgreSQL so they show up on the
Positions page across sessions. See [`frontend/README.md`](frontend/README.md).

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the system design and what's still to
be built, and [`docs/STRATEGIES.md`](docs/STRATEGIES.md) for the seven inbuilt scalping
strategies and how to call them.

## Quick start

### Option A: Docker Compose (one command)

```bash
cp .env.example .env   # adjust JWT_SECRET_KEY / SECRETS_ENCRYPTION_KEY for anything beyond local dev
docker compose up --build
# backend:  http://localhost:8000/docs
# frontend: http://localhost:8080
```

Written and its config validated (`docker compose config`), but not build-and-run verified in
this development sandbox — its network policy blocks Docker Hub's CDN. See "Docker Deployment"
in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the full note; please confirm it builds
cleanly wherever you run it before relying on it.

### Option B: run backend and frontend directly

```bash
# database (one-time local setup - adjust to your Postgres install)
sudo -u postgres psql -c "CREATE USER atp_user WITH PASSWORD 'atp_dev_password';"
sudo -u postgres psql -c "CREATE DATABASE advance_trading_platform OWNER atp_user;"

# backend
cd backend
cp .env.example .env   # then fill in JWT_SECRET_KEY / SECRETS_ENCRYPTION_KEY for anything beyond local dev
pip install -r requirements.txt
uvicorn app.main:app --reload
# API docs at http://localhost:8000/docs - tables are created automatically on startup

# frontend (separate terminal)
cd frontend
npm install
npm run dev
# console at http://localhost:5173
```

## Run the tests

```bash
cd backend
pytest -q       # 114 passing - runs against an in-memory SQLite DB, no Postgres needed

cd frontend
npm run build   # type-checks + production build
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
`BrokerInterface` instance (e.g. `ZerodhaBroker`, `UpstoxBroker`, `ShoonyaBroker`) is explicitly
passed to `OrderRouter`; with none wired in it stays safely blocked
(`LiveTradingNotConfigured`) rather than silently doing nothing. Broker credentials are now
accepted over the API (`POST /api/broker/{name}/credentials`), but only behind a JWT-authenticated
user and encrypted at rest (Fernet) — never in plaintext, never logged.
