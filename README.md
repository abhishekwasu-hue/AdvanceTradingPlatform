# Advance Trading Platform

An algorithmic trading platform for Indian markets (NSE cash, F&O, indices), built in phases.
This delivers the **strategy, risk, execution, broker-abstraction, price-action, option-chain,
signal-scoring, database/auth, and platform-operations core**: indicators, inbuilt
auto-executable multi-timeframe and indicator-based intraday scalping strategies, a **no-code
Strategy Builder** for user-defined rule-based strategies, a risk engine with **per-user
persisted risk settings**, a paper execution router with **manual position exit-tracking**
(stop loss/target checks against a supplied price), a broker-agnostic `BrokerInterface` with
real Zerodha, Upstox, and Shoonya adapters (Angel One/Fyers/Dhan registered as pluggable stubs),
a market-structure + candlestick-pattern engine, a support/resistance zone engine (swing
clusters, prev day/week, opening range, VWAP, pivots, Fibonacci), an option-chain intelligence
engine (PCR, Max Pain, ATM/ITM/OTM, OI buildup/unwinding, bias), a weighted-composite signal
scoring engine with **persisted signal history**, PostgreSQL persistence (with **Alembic
migrations**) with JWT auth and Fernet-encrypted broker credential storage, an **analytics
engine** (win rate/P&L by strategy and symbol), an **audit log**, optional **Redis caching**, a
backtest engine, **CI** (GitHub Actions: pytest + migration drift check + frontend build), a
**Fundamental Analysis & Company Intelligence Engine** (business quality, earnings quality,
valuation, DCF, red flags, SWOT, scenario projection, an earnings calendar, peer/competitor
comparison, pre- and post-earnings analysis, sector-specific fundamentals for banking/IT/auto/
pharma/oil & gas/cement, an Event Impact Score, a Fundamental Alert Engine, a Final Company
Report, and a composite Fundamental Score fused with the technical signal score - see
[`docs/FUNDAMENTALS.md`](docs/FUNDAMENTALS.md)), and a FastAPI service exposing all of it -
**production-hardened** in a full correctness/security review (see "Production Hardening Pass"
in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)): a fail-fast startup check against insecure
default secrets, configurable CORS, timing-safe login, and several real cross-tenant/exit-logic/
broker-parsing bugs fixed with regression tests. The platform is now genuine **multi-tenant
SaaS** (see "Multi-Tenancy + RBAC Foundation" in `docs/ARCHITECTURE.md`): a `Tenant` isolation
boundary, `tenant_id` scoping on every shared resource (broker credentials, trades, signal
history, custom strategies, risk settings), and RBAC roles (`SUPER_ADMIN`/`USER`/
`STRATEGY_CREATOR`/`SUPPORT`) on every user. Every paper-execute call also now runs through a
**formal order state machine** (see "Order Idempotency + Formal Order State Machine" in
`docs/ARCHITECTURE.md`): an auditable `orders`/`order_events` ledger for every execution attempt
(filled or rejected), enforced CREATED→...→POSITION_OPEN/REJECTED/FAILED/CANCELLED transitions,
and an `idempotency_key` so a retried submission replays its original outcome instead of
double-executing. **Kill switches** (global/tenant/strategy - see "Kill Switches + Emergency
Exit" in `docs/ARCHITECTURE.md`) can block new orders at any of those three scopes, and a single
**emergency exit** call engages the tenant switch, cancels pending orders, and closes every
open position a current price is supplied for. Custom strategies now have **immutable version
control** (see "Strategy Version Control" in `docs/ARCHITECTURE.md`): every edit appends a new
version rather than overwriting one, and a rollback appends a new version too rather than
resurrecting an old one - full history via `GET /api/custom-strategies/{id}/versions`. The
Option Chain engine now includes a **Black-Scholes Greeks engine** (see "Greeks Engine" in
`docs/ARCHITECTURE.md`): Delta/Gamma/Theta/Vega per strike inside the existing chain analysis
(solved from a real quoted price when no IV is supplied), plus a standalone per-leg/per-strategy
calculator (`POST /api/option-chain/greeks`) that nets Greeks across a multi-leg position. A new
**position reconciliation engine** (`POST /api/reconciliation/{broker_name}` - see "Position
Reconciliation Engine" in `docs/ARCHITECTURE.md`) compares what the platform believes it holds
against what a real broker reports, flagging quantity mismatches and positions missing or
untracked on either side, with every mismatch logged to the audit trail. An **in-app notification
engine** (a new Notifications tab in the console - see "Notification Engine" in
`docs/ARCHITECTURE.md`) now surfaces entries, exits, rejections, broker disconnects/token
expiry, risk and daily-loss-limit rejections, emergency exits, and system failures as they
happen, each with a severity level, with read/unread state. **TradingView webhook ingestion**
(`POST /api/webhooks/tradingview/{token}` - see "TradingView Webhook Ingestion" in
`docs/ARCHITECTURE.md`) lets a TradingView alert fire a real, risk-checked, kill-switch-aware
paper trade through the same engine as a manual paper execute, authenticated by a per-tenant
webhook URL shown (and rotatable) on the Settings page. A **Market Scanner** (see "Market
Scanner" in `docs/ARCHITECTURE.md`) screens a whole watchlist at once against indicator
conditions (the same building blocks as the Strategy Builder), price-action/structure filters
(trend, break of structure, candlestick patterns, proximity to support/resistance), and
option-chain filters (PCR, bias, proximity to max pain), returning only the symbols that clear
every filter with a label for each one that matched. A **News & Event engine** (see "News & Event
Engine" in `docs/ARCHITECTURE.md`) holds structured, cited entries for RBI policy decisions, the
Union Budget, government policy, corporate news, and other market-moving events - shared
reference data like the fundamentals module, always user-entered and cited since there's no live
news feed wired in, with a mandatory source citation on every entry.

A Vite + React + TypeScript + Tailwind frontend console (`frontend/`) sits on top of that API —
Dashboard, Strategy Library, **Strategy Builder** (no-code rule composer), Signals (a real
TradingView `lightweight-charts` candlestick chart with entry/SL/target lines and
support/resistance zones, the full "why this trade" score breakdown, and signal history),
Backtesting (a candlestick chart with entry/exit trade markers, equity curve, and trade log),
Option Chain, Positions (with a manual "check price" exit control), **Portfolio**, **Orders**,
**Analytics**, **Risk Management**, **Fundamental Analysis** (company profile, financials,
valuation & DCF, SWOT, red flags, Fundamental Score, fusion with the technical signal, an
earnings calendar with pre- and post-earnings analysis, peer/competitor comparison,
sector-specific metrics, and a final report with a fundamental alert feed),
**Settings** (broker credentials), **System Logs** (audit trail), **Notifications** (in-app
entry/exit/rejection/broker/risk/emergency-exit/system-failure feed), and Account (login/register)
pages, all wired to real backend computation over a
clearly-labeled sample dataset (no live broker is connected yet). Signing in is optional
everywhere except the account-scoped tabs (Positions, Portfolio, Orders, Analytics, Risk
Management, Settings, System Logs, Notifications) - it persists your paper-execute fills, signal
history, risk settings, custom strategies, and broker credentials to PostgreSQL so they show up
across sessions. See [`frontend/README.md`](frontend/README.md).

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

Wires four services: `postgres`, `redis` (optional caching - the API works fine without it),
`backend` (runs `alembic upgrade head` before serving), `frontend`. Written and its config
validated (`docker compose config`), but not build-and-run verified in this development sandbox
— its network policy blocks Docker Hub's CDN. See "Docker Deployment" in
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the full note; please confirm it builds
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
alembic upgrade head   # applies the tracked schema migrations (see docs/ARCHITECTURE.md)
uvicorn app.main:app --reload
# API docs at http://localhost:8000/docs

# frontend (separate terminal)
cd frontend
npm install
npm run dev
# console at http://localhost:5173
```

## Run the tests

```bash
cd backend
pytest -q       # 359 passing - runs against an in-memory SQLite DB, no Postgres/Redis needed

cd frontend
npm run build   # type-checks + production build
```

CI (`.github/workflows/ci.yml`) runs both on every push/PR, plus applies Alembic migrations
against a real Postgres service container and checks for model/migration drift.

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

Beyond the seven inbuilt strategies, a logged-in user can compose their own from the **Strategy
Builder** page: AND-combined long/short entry conditions comparing an indicator (EMA, SMA, RSI,
ADX, ±DI, ATR, Supertrend, or plain price) against a fixed value or another indicator, with plain
comparisons or crossover detection. A saved strategy gets a `custom:<id>` id and runs through the
exact same `/signal`, `/signal/enrich`, `/paper-execute`, and `/backtest` pipeline as the inbuilt
ones - it shows up in every strategy dropdown once you're signed in.
