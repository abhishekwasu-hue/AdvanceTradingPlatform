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
be built, and [`docs/STRATEGIES.md`](docs/STRATEGIES.md) for the thirteen inbuilt
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

For the Windows PC that hosts the PAPER week, add the local overlay so the database, Redis, API and UI
listen on `127.0.0.1` only and restart with Docker Desktop:
`docker compose -f docker-compose.yml -f docker-compose.local.yml up -d --build` (Marathi runbook with the
daily Fyers login: [`docs/LOCAL_PC_MR.md`](docs/LOCAL_PC_MR.md)).

On Windows, `.gitattributes` keeps the `.sh` scripts LF so the `backup` service's `sh` can run
them. A checkout made before that file existed still has CRLF copies (the `backup` container
restarts with exit code 2); delete them and check them out again once (`del /q scripts\backup\*.sh
scripts\deploy.sh` then `git checkout HEAD -- scripts`; `git ls-files --eol scripts` should show
`w/lf`), then `docker compose restart backup`.

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

## AI Copilot: live market strategist (Phase AW)

The AI Copilot studies the live market and builds today's strategy - it is a strategist, not a chat:

1. **Market study** of the symbol you pick (NIFTY 50, NIFTY BANK, any stock), from your broker's candles: the trend on
   5m / 15m / 60m / daily (EMA 20/50/200, ADX regime, RSI), today's levels (VWAP, the 15-minute opening range, day
   open / high / low, the previous day's high / low / close, floor pivots, support / resistance zones, ATR), a weighted
   bias with confidence, the day's character (trend / range / volatile) and three scenarios with trigger levels.
2. **Strategy synthesis** - setups that fit that character (trend pullback, opening-range breakout, previous-day level
   breakout, VWAP reclaim, Supertrend with a higher-timeframe filter, range reversion), in the bias direction when it is
   clear, written as rules in the platform's own strategy language around today's levels.
3. **Validation** - every parameter set is simulated on the last ~12 sessions (one position at a time, stop first,
   costs, flat by 15:15); parameters are chosen on the earlier 70% of sessions and judged on the later sessions they
   never saw. Each candidate is labelled: held up on unseen data, over-fit risk, too few trades, or no edge. With an AI
   key in Settings, the AI also writes rule sets from the same study, which go through the identical test.
4. **Adopt and deploy** - the best three come with rules, exits, today's triggers and risk per trade for your capital;
   "save as strategy" makes it one of your (versioned, editable) strategies and "deploy in PAPER" starts it on the
   Autopilot. LIVE stays behind the Go-Live checklist.

The strategy language gained the operands this needs: `VWAP`, `DAY_OPEN`, `OR_HIGH` / `OR_LOW` (opening range, period
in minutes), `PDH` / `PDL` / `PDC`, Bollinger `BB_UPPER` / `BB_MID` / `BB_LOWER`, `VOLUME` / `VOLUME_SMA`, and a
higher-timeframe filter on any operand (`EMA(50)` on 15min - completed bars only), all available in the Strategy
Builder too. The Copilot page also keeps today's market briefing, the strategy interview and the draft / agent tools.
API: `POST /api/ai/strategist/study`, `POST /api/ai/strategist/build`, `POST /api/ai/strategist/adopt`.

## Coach & Guide (Phase AV)

A separate page (Portfolio > Coach & Guide):

- **Trade coach** - your closed trades read like a mentor would: win rate, expectancy in rupees and R, profit factor,
  drawdown, a discipline grade, P&L by strategy and by hour, the equity line, and behaviour flags with one fix each -
  revenge trades (an entry within 15 minutes of a loss), overtrading, broken daily loss limits, losses far beyond the
  stop, small winners against big losers, holding losers longer than winners, a losing hour or strategy.
- **Guide** - the bilingual concept library (Phase AT).

Today's market briefing (day type, game plan, your loss budget, why each deployment is or is not trading, the pre-trade
checklist) is the "Today's market" tab of the AI Copilot. API: `GET /api/ai/brief`, `GET /api/ai/coach?days=&mode=`,
`POST /api/ai/copilot` (routes a free-text question to the briefing, the coach, the deployments, the interview or the guide).

## Global cues (Phase AU)

The market memory now also reads the world: S&P 500 and Nasdaq futures and indices, Nikkei, Hang Seng, Brent crude,
gold, the dollar index, USD/INR and the US 10-year yield - from **free public data** (Yahoo Finance, with Stooq as a
fallback; no key, delayed about 15 minutes). The worker reads them from 08:00 IST (before the open, without a broker
session) and every 15 minutes while NSE is open. The **जागतिक संकेत** section of the market-memory card shows each move
coloured by what it usually means for India (crude or the dollar rising is red), an overall mood (positive / mixed /
negative) and plain-language notes ("US futures down: NIFTY often opens lower - let the first 15 minutes settle"). Plans
put the global mood first in their Market background, and the guide answers "crude वाढले तर काय?" from it. GIFT Nifty has
no free reliable source, so the US futures stand in for the overnight mood. The data is background only - never an input
to a signal, an order or a risk check - and the free endpoints are unofficial; a commercial deployment should switch to a
licensed feed. `GLOBAL_CUES_ENABLED=false` turns it off.

## Ask the guide (Phase AT)

The AI Copilot page has a **मार्गदर्शक विचारा / Ask the guide** chat. Ask about any trading concept in Marathi or English -
"RSI म्हणजे काय?", "Stop-loss कुठे ठेवावा?", "Theta म्हणजे काय?", "Position size किती घ्यावा?" - and the answer comes from a
bilingual library of 33 concepts (trend, structure, support / resistance, every indicator the strategies use, stops, sizing,
R:R and expectancy, drawdown, discipline, options and Greeks, IV, VIX, intraday vs swing, gap risk, paper trading,
backtesting, OI / PCR), each explaining how this platform applies it. "आज NIFTY BANK चा कल काय?" is answered from the market
memory. With an AI provider set in Settings, the AI answers instead, grounded on the same notes, the market memory and your
profile; the library is the fallback. Education only - never a buy/sell call on a security. API: `POST /api/ai/ask`,
`GET /api/ai/concepts`, `GET /api/ai/concepts/{id}`.

## Swing trading (Phase AS)

Two daily strategies - **Swing EMA pullback** (buy the dip to EMA 20 in a daily uptrend) and **Swing breakout** (a close
above the 20-day high with ADX and volume) - trade closed daily candles and are **held overnight**: a swing deployment
(`holding: "SWING"`, `timeframe: "day"`) buys delivery (CNC) for shares and NRML for futures / bought options, uses the
same product for its exit and broker-side stop, and is never squared off at the close. Cash shares cannot be held short
overnight, so swing shorts need futures or options; written options and multi-leg structures stay intraday. The Strategy
interview now offers "Swing - days to weeks" (and "मला swing हवे" as feedback): daily candles, a quarter less risk per
trade for gap risk, monthly expiries for options, no 15:10 exit. The Deployments page switches to swing automatically for
a daily strategy.

## Market memory (Phase AR)

The Copilot now knows the market before you ask, the way a shopkeeper knows his stock. While NSE is open the worker
reads, every 15 minutes and through your own broker session, each watched symbol (NIFTY 50, NIFTY BANK, the symbols in
your trader profile and active deployments): trend, regime, structure, support / resistance and bias - plus the market
cues India VIX (the fear gauge) and the index day changes. The AI Copilot page shows it as **Market चा साठा** (with each
symbol's bias over the last sessions and a "read now" button), and every plan gets a **Market background** section: what
VIX says, the last session, the bias trail ("three sessions running" / "changing every session"), and a warning for
beginners when VIX is 20 or higher. API: `GET /api/ai/market-memory`, `POST /api/ai/market-memory/refresh`.

## Options, feedback and memory (Phase AQ)

The interview's plan now comes the way a good shopkeeper shows clothes: **three options** - Safe (less risk, fewer
trades, bigger reward per trade), Balanced (your own answers) and Active (more opportunities, same safety rules) - each
with **how closely it matches you** and, separately, **how well it suits today's market**. "**Not this**" asks why (too
risky, too many trades, reward too small, I don't understand it, calmer bigger moves, no time to watch, not this
strategy); the reasons become preferences and the next three options are rebuilt from them, so the match climbs round by
round. Your answers and what the Copilot learnt are kept in a trader profile, so next time it offers to skip the
questions ("Forget me" deletes it). API: `POST /api/ai/interview/refine`, `POST /api/ai/interview/choose`,
`GET|DELETE /api/ai/profile`.

## Strategy interview (Phase AP)

A beginner who types "give me a strategy" / "ट्रेडिंग स्ट्रॅटेजी सांगा" in the AI Copilot is not handed a list: the
Copilot first asks about them, in Marathi or English, one question at a time with a line on why it matters (experience,
capital, loss per trade and per day, trading style, instrument and symbol, options buy/sell, time, goal, market view;
what the message already said is not asked again). It then reads the market on sample or broker candles - today's move
and VWAP, the higher-timeframe trend, the regime, market structure, nearest support / resistance, volatility - and builds
a plan: the best-fitting inbuilt strategy with evidence from walking it over the same candles, risk management, capital
allocation (a beginner trades 25% of capital), reward:risk and exits, the contract to trade (beginners never sell naked
options or trade index futures) and a PAPER deployment. **Apply risk settings**, **Deploy in PAPER** and **Ask the AI for
a custom rule set** are buttons; nothing is applied on its own. API: `POST /api/ai/interview/start`,
`POST /api/ai/interview/plan` (docs/ARCHITECTURE.md, Phase AP).

## Strategies on the chart (Phase AO)

Every Pro Chart has a **Strategies** button (in full screen and the new-tab chart too). Each of the
eleven inbuilt strategies gets two switches: **Chart** draws its entries and exits on the candles on
screen (the backtest engine, via `POST /api/strategies/{id}/chart-run`), its last signal's entry /
stop / target lines and its indicators, with its win rate and P&L on that data; **Deploy** (on a
broker-symbol chart) creates or resumes a PAPER deployment on that symbol, off pauses it. Four new
indicator-combination strategies join the seven scalpers: MACD + EMA trend, Bollinger + RSI
reversion, VWAP + Supertrend and the opening range breakout (docs/STRATEGIES.md). Market pulse's
"Add symbol" offers a list of NSE indices and F&O stocks.

## Pro Chart (Phase AN)

- Interactive charts on Signals, Backtest, Positions and the Dashboard: indicator overlays (EMA, SMA, Bollinger, VWAP,
  Supertrend), volume / RSI / ADX panes that scroll together, a crosshair legend, timeframe switch, the strategy's own
  indicators on by default, entry / stop / target lines and trade markers, and a live last price that moves the forming
  candle (`GET /api/market-data/ltp`, tick first, then the broker quote with its age).
- Broker charts (the new-tab chart, Signals in broker mode, the position chart) load each timeframe's own history
  automatically - 1m: 5 days, 5m: 10, 15m: 20, 30m / 60m: 30, day: 2 years - and **scrolling back past the oldest bar
  loads the page before it** (`POST /api/market-data/candles` with `before`), until the broker has nothing older. The
  minute refresh merges the newest bars without losing the older pages or your zoom.

## Designer dashboard (Phase AM)

- The Dashboard is a colour-coded one-glance screen: gradient hero with status pills and primary actions, six KPI tiles
  (P&L, win rate, open positions, deployments, broker funds, strategies), P&L-by-strategy bars, strategy-mix and
  long/short exposure rings, running deployments, open positions, latest alerts, the go-live checklist and the engine cards.

## Reconciliation per broker account (Phase AL)

- Each broker account's LIVE positions are compared with that account's own session; the tenant's LIVE block
  settles on the joint result, with mismatch lines tagged by account. Closes the last named gap in
  `docs/MASTER_PROMPT_GAP_ANALYSIS.md`.

## Completion: CoinDCX, real defaults, go-live runbook (Phase AK)

- **CoinDCX spot adapter** replaces the last stub: signed private calls, INR markets, ticker quotes, public
  candles, market/limit/stop-limit orders with step-floored fractional quantities, wallet-derived positions
  and margins. Keys are permanent (no daily login). Every registered broker now has real I/O.
- **`docs/GO_LIVE.md`**: the ordered operator sequence from a fresh deploy to the first LIVE order.

## Read-only broker check (Phase AJ)

- **First live confirmation without an order**: "Read-only check" on a broker account (or
  `POST /api/broker/{name}/smoke-test`) probes profile, funds, instruments, a NIFTY quote, the nearest NIFTY
  option through the worker's symbol translation, positions and the order book, and reports each step with
  timing and the exact error. Audited; never places, modifies or cancels anything.

## Contract symbols per broker (Phase AI)

- **F&O on every broker**: derived contracts keep the Upstox master's spelling platform-wide, and a
  wrapper on every non-Upstox adapter translates them into that broker's own symbol (orders, stops, exits,
  margin probes, quotes) and back (positions, order books) by matching instrument attributes, never by
  guessing a format. Unlisted contracts are refused with a clear error.

## Fundamentals ingestion (Phase AH)

- **Bulk financials import**: paste a CSV of financial periods on the Fundamentals page (or
  `POST /api/fundamentals/companies/{symbol}/financials/import`); periods are created or updated on
  (type, label), bad rows are listed with their reason.
- **Provider refresh**: "Add from NSE" / "Refresh from NSE" pull the company profile, shareholding pattern
  and announcements through the fundamentals provider seam (`FUNDAMENTALS_PROVIDER`, default `nse`);
  `POST /api/fundamentals/refresh` does a whole watchlist and creates missing profiles.

## Factor Lab fundamentals (Phase AG)

- **Value and quality factors filled from stored financials**: `/api/quant/factors` and `/exposure` derive
  PE, PB, ROE, debt/equity and PAT growth from the Fundamentals module's company profiles and financial
  periods for symbols the caller sends without ratios (`use_fundamentals`, on by default); the response says
  which symbols were covered and why a ratio is missing. Factor Lab toggle and notes.

## Dhan adapter (Phase AF)

- **Real Dhan API v2 adapter** replacing the stub: token + client id headers, scrip-master resolution with
  built-in index ids, segment-batched quotes, parallel-array candles, orders with SL-M stops, books,
  positions, holdings, funds and the native option chain with Greeks. Verified against a mocked transport.

## Fyers adapter (Phase AE)

- **Real Fyers API v3 adapter** replacing the stub: auth-code exchange, symbol-master resolution, batched
  quotes, candles, orders with SL-M stops, books, positions, holdings, funds and the native option-chain
  endpoint. Verified against a mocked transport; the first live confirmation is the operator's.

## Holidays page and real data everywhere (Phase AD)

- **Exchange holidays** are managed from the Admin console (paste the NSE circular); **AI Copilot**, the
  **Scanner's option-chain filters** and the **Option Chain** page now take the same Data switch as the
  other research pages, reading the broker's live candles and chains through your own session.

## Angel One adapter (Phase AC)

- **Real SmartAPI adapter** replacing the stub: TOTP login from the stored secret, scrip-master symbol
  resolution, batched quotes, candles, orders with SL-M protective stops, books, positions, holdings,
  margins, an option chain assembled from the master and quotes, logout. Verified against a mocked
  transport; the first live confirmation is the operator's.

## Go-live checklist (Phase AB)

See also `docs/GO_LIVE.md` for the full ordered runbook (Phase AK).

- **Dashboard**: every step still needed before a PAPER run, and the extra ones for LIVE (MFA, verified
  email, SEBI algo id), computed from the platform's state with a jump to the page that fixes it.
- **Admin console**: the operator's list (secrets, SMTP, gateway keys, worker, migrations, Redis, CORS,
  instrument master, holidays, kill switch), naming the environment variable for each gap.

## Broker candles on the research pages (Phase AA)

- **One Data switch** on Signals, Scanner, Backtest and Factor Lab: deterministic sample candles, or
  real candles through your own broker session (`POST /api/market-data/candles`, the worker's cached
  market-data service, resampled from one-minute bars). Broker mode unlocks once a broker is logged in
  under Settings; per-symbol failures are reported beside the symbols that worked.

## Factor Lab (Phase Z)

- **Cross-sectional factor scores**: momentum, reversal, low volatility, trend, liquidity, and value
  and quality from supplied ratios; z-scored across the universe, weighted into a composite, ranked
  into long/short buckets with coverage per symbol.
- **Risk model**: correlations, betas, volatilities, portfolio VaR/CVaR/drawdown, inverse-volatility
  and risk-parity weight suggestions, and the open book's factor tilt. Descriptive only.

## AI scanner (Phase Y)

- **Plain language to filters**: the tenant's LLM (or the deterministic parser) turns a sentence into
  the scanner's indicator, structure and option filters; the user reviews them and runs the scan.
- **AI read of the matches**: ranked, explained, with the platform's regime per symbol and a fixed
  disclaimer. Analysis only; the scanner never places orders.

## Marketplace revenue share (Phase X)

- **Paid listings**: a one-time price per listing, the platform fee frozen at publish; the buyer's
  copy is made only once the charge is paid (Razorpay payment link or operator-confirmed transfer).
- **Creator earnings and payouts**: per-sale ledger, payout requests above a minimum with the
  destination stored encrypted, settled or rejected by the operator with a reference.

## Historical option backtests (Phase W)

- **The same structures a deployment trades**, on history: strikes, expiries, credits, group
  exits and lot sizing come from the live code paths (one planner, one exit rule).
- **Premiums** from recorded option-chain quotes (the worker samples chains while it runs; rows
  can be uploaded) or from Black-Scholes off the underlying bars, clearly labelled as an
  approximation; conventions (lot sizes, strike steps, expiry weekdays) overridable per run.
- **API and UI**: `options` on the backtest endpoints, Monte Carlo and walk-forward included;
  the Backtest page's "Trade as: Options" panel and per-structure results.

## Guardian AI prompt (Phase V3)

- **Versioned system prompt** filled from the account's live state: capital, risk profile,
  open risk, drawdown, recent trades, upcoming events, market regime, language.
- **Schema mapped onto the platform**: the rule set the backtester runs plus suggested
  Autopilot settings (structure, expiry/strike rules, credit target/stop, exits, regime filter),
  parsed tolerantly and re-checked by the compliance validator.

## AI draft compliance (Phase V2)

- **Every AI draft is re-checked** against the Risk Guardian checklist: draft rules fixed
  deterministically after one AI auto-fix round, engine rules reported with live values,
  backtest evidence judged plainly.
- **Explicit acceptance**: approval requires the human to confirm the maximum loss per trade
  and the worst case the report states.

## Risk Guardian rules (Phase V1)

- **Engine-enforced trader discipline**, whatever built the strategy: cool-down after a
  stop-out, a drawdown ladder that halves size and then pauses entries, event-day blackouts and
  size cuts from a market-events calendar, and a portfolio risk cap with index positions in one
  correlated bucket.
- **Platform ceilings** the operator sets; tenant settings cannot exceed them and are clamped
  at runtime if they do.

## Ratio spreads, butterflies and custom legs (Phase U)

- **Payoff-priced structures**: call/put ratio spreads (1:2), long butterfly (1:2:1) and a
  free-form leg builder (2-6 legs with roles, strikes and ratios). Max loss, max profit and
  breakevens are computed from the expiry payoff, never assumed.
- **Per-leg quantities** through the executor and the position monitor; exits on P&L per unit
  with an underlying exit beyond an unprotected side's breakeven.

## Account routing (Phase T)

- **Rule-based broker selection** per deployment or tenant default: most margin, least
  utilised, fewest positions, or explicit; optionally across brokers. The chosen account and
  the reason are shown on the Autopilot card and stored on every trade.
- **Fresh balances only**: the worker refreshes each account's margin before routing; a stale
  number falls back to the default account and says so.
- **Exits follow the trade**: every close, square-off and stop re-arm goes through the account
  the position was opened in.

## Streaming quotes (Phase S)

- **Websocket feeds** for Upstox (Market Data Feed V3, protobuf decoded natively) and Zerodha
  (Kite binary ticker), one stream per broker session, resubscribed each cycle to the symbols
  the deployments and open positions need.
- **Tick cache first, REST second**: `get_ltp` uses a tick younger than the staleness limit and
  polls otherwise; the safety gate is unchanged. Off by default (`STREAMING_QUOTES_ENABLED`).

## Option structures depth (Phase R)

- **Nine structures** on a deployment: bull put, bear call, iron condor, iron butterfly, short
  straddle, short strangle, long straddle, long strangle, calendar spread.
- **Credit, debit and undefined-risk economics** in one metrics object: max loss, max profit,
  breakevens, the sizing basis (max loss, the debit, or the stop) and the group exit levels.
- **Group exits** for debit structures (worth-based) and breakeven exits for at-the-money shorts.

## Order pre-checks (Phase Q)

- **Instrument and expiry validity** before any LIVE order: index spots refused (derive a
  contract), unknown symbols refused against the synced instrument master, expired contracts
  refused in every mode.
- **Margin available for every LIVE entry**: sized to the broker's own margin number (80% of
  available margin), refused when one unit is not covered; bought options fall back to premium x
  lot; nothing is guessed for equity or futures.
- **PARTIAL_FILL** recorded on the order trail when the broker fills less than requested.

## Backstop, tax, currency and staging (Phase P)

- **Protective-stop guard**: every open LIVE position keeps a standing broker-side stop; missing
  or cancelled stops are re-armed each cycle and at start-up.
- **Financial-year tax report** by income head (equity intraday, F&O, crypto) with STT/CTT/TDS
  estimates and CSV download.
- **Reporting currency + FX rates** groundwork; portfolio figures convert to the tenant's currency.
- **Drift gate**: the monitoring agent proposes a pause when live results degrade vs the backtest.
- **Staging overlay and rolling deploy script**, with an optional GitHub deploy workflow.

## Reliability and delivery closure (Phase O)

- **Least-privilege DB roles**: the app runs DML-only; only the migrator owns the schema.
- **Per-exchange sessions**: NSE, MCX (to 23:30 IST) and crypto (24x7) each on their own clock,
  entry cut-offs and square-off.
- **Browser push and SMS** alert channels, with no third-party push service and any SMS gateway.
- **Chaos tests**, a **load-test probe** with the measured baseline (`docs/PERFORMANCE.md`), and
  AI/billing Prometheus metrics.
- **Point-in-time recovery**: WAL archiving, weekly base backups, `pitr_restore.sh`.

## Security and platform hardening (Phase N)

- **Per-tenant envelope encryption**: every tenant's secrets sit under its own data key, wrapped
  by the master key; master rotation re-wraps keys, not credentials (`scripts/reencrypt_secrets.py`).
- **Fine-grained permissions**: owners deny or grant individual scopes per member on top of roles
  (*Team → permissions*); LIVE needs `trading:live`, credentials need `brokers:write`.
- **Email verification** with a platform mailer; optionally required for LIVE and credentials.
- **Feature flags** with tenant allow-lists in the Admin Console; a **migration-hour guard** that
  refuses schema changes while the NSE session is open.
- **ADRs** in `docs/adr/` and the versioned **Strategy DSL reference** in `docs/STRATEGY_DSL.md`.

## Operations and risk closure (Phase M)

- **Platform controls**: maintenance mode with a message, per-broker LIVE disable, per-user trading disable; an
  incidents log opened automatically by the global kill switch.
- **Portfolio engine**: gross/net exposure, concentration and loss-at-stops across the open book; risk limits now span
  eight scopes including the whole portfolio and a single Autopilot deployment.
- **Trade journal** (regime at entry, notes, tags), **live-vs-backtest degradation** per strategy, and **parameter
  optimisation** ranked out-of-sample with an overfit gap.

## AI layer (Phase L)

- **AI Copilot**: describe a strategy, get a draft in the Strategy Builder's rule schema with the model's
  own caveats; it becomes a strategy only after you backtest it and approve it (lineage recorded).
- **Provider seam**: Claude (Anthropic SDK, `claude-opus-5`, recommended), OpenAI, or the built-in rule-based parser;
  the key is entered once on the Settings page and stored encrypted. The model sees prompt text only, never credentials.
- **Regime engine** (trend / range / volatile / quiet) as an optional entry filter per deployment, and a
  **monitoring agent** that proposes pause / exit / review actions with evidence; every action waits for
  your approval and expires unanswered.

## Commercial layer (Phase K)

- **Plans with prices and feature flags**, subscriptions with trial and grace lifecycle, invoices and
  payments behind a billing-provider seam: **Razorpay** (hosted checkout, autopay, signed webhooks) or manual;
  usage metering for orders, backtests, webhook events and API calls; the worker runs the daily billing sweep.
- **Strategy marketplace**: publish one frozen version of a strategy with documented backtest performance,
  operator review, subscribe = a copy in your own strategies (backtest → paper → live as usual).
- **Public API** (`/api/public/v1`, `docs/PUBLIC_API.md`): scoped, hashed, rate-limited keys shown once;
  read endpoints plus idempotent PAPER signal submission; **HMAC-signed webhook** alert channel.

## Exits and backtesting depth (Phase J)

- **Dynamic exits** on deployments and backtests: trailing stop %, break-even at R, time exits;
  one implementation for backtest, paper and live (LIVE moves the broker-side stop).
- **Backtest analytics**: monthly, day-of-week and hour-of-day performance, exit-reason and
  direction breakdowns, holding times, slippage and cost share, streaks, CAGR/Sharpe/Sortino/Calmar,
  drawdown curve; **Monte Carlo** and **walk-forward** robustness checks; every logged-in run recorded.

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
pytest -q       # 780 passing - runs against an in-memory SQLite DB, no Postgres/Redis needed
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
