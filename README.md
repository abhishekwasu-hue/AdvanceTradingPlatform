# Advance Trading Platform

An algorithmic trading platform for Indian markets (NSE cash, F&O, indices), built in phases.
This delivers the **strategy, risk, execution, broker-abstraction, price-action, option-chain,
signal-scoring, database/auth, and platform-operations core**: indicators, inbuilt
auto-executable multi-timeframe and indicator-based intraday scalping strategies, a **no-code
Strategy Builder** for user-defined rule-based strategies, a risk engine with **per-user
persisted risk settings**, a paper execution router with **manual position exit-tracking**
(stop loss/target checks against a supplied price), a broker-agnostic `BrokerInterface` with
real Zerodha, Upstox, and Shoonya adapters (Angel One/Fyers/Dhan/CoinDCX registered as pluggable stubs),
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
news feed wired in, with a mandatory source citation on every entry. **Multi-asset-class support**
(see "Multi-Asset-Class Support (MCX & Crypto)" in `docs/ARCHITECTURE.md`) extends position
sizing beyond plain NSE/BSE equity & index options: a static registry of MCX commodity and crypto
contract specs (`GET /api/instruments`) drives fractional-quantity-aware risk sizing - a
commodity trade floors to whole multiples of its own lot size, and a crypto trade sizes in
fractional units instead of being floored to a meaningless whole "lot". A **Conversational
Strategy Builder** (see "Conversational (Rule-Based) Strategy Builder" in
`docs/ARCHITECTURE.md`) lets you describe a strategy in plain English (e.g. "Buy when RSI(14)
crosses above 60 and price is above EMA 50") and get it pre-filled into the Strategy Builder's
condition editors for review - a deterministic, rule-based parser rather than a call to an
external AI (no AI-provider credentials are configured), which shows exactly what it understood
and flags anything it didn't rather than guessing.

A Vite + React + TypeScript + Tailwind frontend console (`frontend/`) - a "modern trading
terminal" visual design (Inter/JetBrains Mono fonts, a Lucide icon set, a brand color kept
distinct from bullish/bearish P&L colors, a logo, grouped sidebar navigation, a top bar - see
"Visual Design System" in `docs/ARCHITECTURE.md`) - sits on top of that API —
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

Wires five services: `postgres`, `redis` (optional caching; also the trading worker's replica
lock), `backend` (runs `alembic upgrade head` before serving), `worker` (the autonomous trading
engine - same image, `python -m app.workers.trading_worker`), `frontend`. Verified end to end on a real
machine (Windows + Docker Desktop/WSL2): all images pull and build cleanly, migrations apply
automatically, and both services come up healthy. See "Docker Deployment" in
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the full verification note.

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

# trading worker (separate terminal, same .env) - evaluates deployed strategies every minute
python -m app.workers.trading_worker

# frontend (separate terminal)
cd frontend
npm install
npm run dev
# console at http://localhost:5173
```

## Autopilot: trading without a browser open

1. Settings -> store your broker API key/secret (encrypted at rest; never put them in `.env` or
   a chat) and, for Upstox, click **Login to Upstox** each trading morning - tokens expire daily.
2. Autopilot tab -> pick a strategy (inbuilt or one you built), a symbol, PAPER or LIVE ->
   Deploy. LIVE asks you to type `LIVE` and needs a `VALID` broker session.
3. The `worker` service does the rest every minute during NSE hours: live candles, the same risk
   engine and kill switches as a manual execute, a broker-side stop-loss on every live fill,
   continuous exit monitoring, and a full square-off at 15:15 IST. The Dashboard shows its
   heartbeat. Details: `docs/ARCHITECTURE.md` (Phase A) and `docs/OPERATIONS.md` (daily routine).

## Running it as a business (Phase B)

- **Team**: registration creates an organisation with you as Owner. Invite traders, strategy
  creators and read-only viewers from the Team tab (48-hour single-use links). Removing someone
  cuts their access immediately and keeps their history.
- **Plans**: Free is paper-only with 2 deployments and 1 member; Pro and Business unlock LIVE,
  more deployments, members and alert channels (`backend/app/plans/registry.py`). Limits are
  enforced where they would be exceeded, with a message that says which and what to do.
- **Alerts on your phone**: Settings -> Alert delivery (Telegram bot or SMTP). Every CRITICAL the
  worker raises - broker session expired, stop-loss could not be placed, deployment auto-paused,
  daily loss limit - is queued and delivered with retries; the outbox shows what was sent.
- **Platform admin**: put operator emails in `SUPER_ADMIN_EMAILS` to get the Admin Console:
  every tenant, plan/status changes (audited on the tenant's trail and notified to it), the
  platform audit trail, what the worker is running, and the global kill switch.
- **Fair sharing**: each tenant's broker calls run under its own rate budget and each tenant gets
  a bounded share of every worker cycle, so one busy account cannot starve the others.

## Account security (Phase C)

- **Sessions**: short-lived access tokens with a rotating refresh token; see and revoke your
  devices on the Account tab, "Log out everywhere" in one click. Removing a member, changing a
  password or resetting it ends the affected sessions immediately.
- **Passwords**: 10+ characters, no breach-list passwords, no email-derived ones. "Forgot
  password?" emails a one-hour link through your organisation's email channel, or an owner
  issues one from the Team tab.
- **Two-factor authentication** (TOTP: Google Authenticator, Authy, 1Password): QR enrolment,
  backup codes, two-step login. Required for platform administrators and, when the owner turns
  the policy on, for LIVE deployments and broker credentials.
- **Login protection**: every attempt is recorded (System Logs tab), ten failures lock the
  account for fifteen minutes, and a login from a new device raises a security notification.

## Compliance (Phase D)

- **SEBI algo tagging**: the owner enters the exchange-issued algo id on the Team tab; every
  entry, stop-loss and exit order is tagged `<algo id>-<strategy>-<leg>` at the broker and the
  tag is stored on the order. `ALGO_ID_REQUIRED_FOR_LIVE=true` refuses LIVE without one.
- **Exports**: System Logs (owners) and the Admin Console download CSV/JSON of the audit trail
  (with chain hashes and verdict), orders, trades and login attempts for a date range, each with
  its SHA-256; the export itself is on the audit chain.
- **Retention**: login attempts, alert deliveries, notifications, dead sessions and spent tokens
  age out on a configurable schedule run by the worker; orders, trades, signals and audit logs
  are never deleted (five-year rule). Owners can erase a removed teammate's personal data while
  keeping their trading records attributed to an anonymous id.
- **Contract notes**: upload the broker's tradebook CSV on the Positions tab and closed trades
  switch from estimated to the broker's actual charges and P&L, matched by order id.

## Operations (Phase E)

- **Metrics**: Prometheus at `/metrics` on the API (optional `METRICS_TOKEN`) and on the worker
  (`:9102`); health at `/api/system/health` (liveness), `/api/system/ready`, and
  `/api/system/health/deep` (database, Redis, migrations, worker freshness).
- **Request ids and `/api/v1`**: every response carries `X-Request-ID` for log correlation; the
  API is served at `/api/v1` with `/api` kept as an alias that announces its successor.
- **Backups**: the compose `backup` service dumps the database daily (optionally encrypted) with
  retention, and `scripts/backup/verify_backup.sh` rehearses a restore into a scratch database
  and checks schema, counts and the audit chain. CI runs the rehearsal on every push.

## F&O Autopilot (Phase F)

- **Instrument master**: Upstox's public master (equities, indices, futures, options with lot
  sizes, expiries, strikes) synced daily pre-market; search/expiries/strikes API; admin force-sync.
- **Contract rules on a deployment**: trade the underlying, an option (buy or write; nearest /
  next / monthly expiry; ATM / ITM±n / OTM±n; premium stop or ceiling %; max lots) or a future.
  The contract is resolved at signal time from the master and the spot; the Autopilot form
  previews what each direction would trade.
- **Execution**: bought options sized off the premium at risk in whole lots, written options
  capped by the broker's margin requirement (never a guess) and max lots, futures on the
  underlying's stop distance; paper fills at the contract's price; LIVE places the entry on
  NFO/BFO with an SL-M at the premium floor/ceiling; partial fills and execution quality
  (expected vs fill, slippage, latency) are recorded.
- **Exits**: the strategy's underlying levels decide, the premium floor/ceiling is the safety net
  (and still works when the index feed is down); futures exit on their own transplanted levels.

## Risk hierarchy and accounts (Phase I)

- **Risk limits at every scope** (organisation, user, broker account, strategy, instrument; platform-wide for the operator):
  eight limit types checked together on every order with the strictest winning, each check logged as a risk event,
  loss-limit breaches engaging the matching kill switch automatically.
- **Broker accounts**: several accounts per broker (labelled credentials), balance/margin/P&L sync, enable/disable,
  default routing, deployments routed to a named account.

## Options depth (Phase H)

- **Strike-selection pipeline**: liquidity (OI, volume, bid/ask spread), IV band, target delta
  and premium band filters on a deployment, judged against the live option chain at signal
  time; the chosen strike's rationale is shown in the preview and kept on the order.
- **Multi-leg structures**: bull put spread, bear call spread and iron condor deployments,
  sized in lots off max loss (and the broker's margin when LIVE), placed wings first, closed as
  one position on the credit target/stop or a short-strike breach; per-leg and per-structure
  Greeks from live premiums on the Positions page.

## Safety and reliability closure (Phase G)

- **Staleness gate**: no signal on a candle feed more than 3 bars behind the clock, no exit
  decision on a quote older than 2 minutes - the deployment says why it skipped.
- **Broker-uncertain flag**: a FAILED live order blocks new LIVE entries for that organisation
  until position reconciliation against the broker passes; the worker reconciles every cycle
  while blocked and on start-up before its first cycle. Exits keep running.
- **Circuit breaker**: a broker whose calls are failing (timeouts, 5xx, 429) has new LIVE
  entries paused platform-wide for two minutes, then probed; independent of the kill switch.
- **SLOs** (`docs/SLO.md`) with the metrics behind them and Prometheus alert rules
  (`scripts/monitoring/prometheus-alerts.yml`); `/api/system/health/live|ready|dependencies`.
- **Broker disconnect** from Settings-level API (revokes today's token at the broker) and
  disclaimers on every backtest, signal, score and generated-strategy screen.

## Run the tests

```bash
cd backend
pytest -q       # 732 passing - runs against an in-memory SQLite DB, no Postgres/Redis needed
                # (tests/test_backup_scripts.py additionally runs when a migrated Postgres is at DATABASE_URL)

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
