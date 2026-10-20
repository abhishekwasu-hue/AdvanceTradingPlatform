# ATP web UI audit (before the redesign)

Audit only, captured on 19 Oct 2026 before the redesign (and before #273 made ATP English-only). No product code was changed.

The screenshots this report names directly are in `docs/design/ui-audit/`. The full set (176 screenshots, desktop and
mobile) was shared with the owner in the UI review page; it is not committed, to keep the repository small.

The owner asked for four bugs found here to be fixed ahead of the redesign, each with a test: Authenticate always logging
in the primary account; the Shoonya TOTP secret sent instead of the code; the `angelone` spelling in the Static IP card;
and the Marathi text outside the strategy interview.

## How this was captured

| Item | Value |
|---|---|
| Code | `origin/main` at `f6d6b2c` (AMW-1 plan limits v2), fresh detached worktree `/home/user/wt-ui-audit` |
| Backend | The real FastAPI app on a throwaway SQLite file, started through `backend/scripts/loadtest_app.py`'s `build()`. Bars come from its synthetic, seeded random walk. No broker, no AI key, no worker, no secrets. |
| Frontend | Vite dev server (`vite.audit.config.ts`, an untracked file in the throwaway worktree, proxying `/api` to the local backend). `node_modules` is a symlink to an existing install with the same `package-lock.json`. |
| Browser | One headless Chromium (`/opt/pw-browsers/chromium-1194`), dark theme, service workers blocked. Desktop 1440x900 (DPR 1). Mobile 390x844 (DPR 1.5, touch). |
| Users | `demo@atpdemo.in` is SUPER_ADMIN, so all 28 sidebar items show. Email verified, no MFA, Free plan, PAPER only. Seeded with 3 PAPER deployments (1 paused), 18 closed and 3 open PAPER trades, 4 signal-history rows and 4 notifications. `fresh@atpdemo.in` is a new OWNER with no data, used for the empty states and for the Brokers flow with a stored, obviously fake Zerodha key. Authenticate was never pressed. |
| Flags | Main pass: every flag at its code default. Second pass, **"(flag on)"**: every registered flag turned on in the local throwaway DB, except `phase0_paper_only`. Only the pages whose content changed were kept (file names contain `-flagon-`). Nothing on the VPS was touched. |
| Servers | Both stopped at the end (ports 8765 and 5199 are free again). |

**Files.** `NN-page-desktop.png` / `-mobile.png` is the first screen. `...-full.jpg` is the full page (JPEG q65). `NNe-` is an empty state, `NNb-`/`NNc-` is a variant, and `30`–`34` are error, signed-out and focus states. `_text/` holds each page's visible text and per-page metrics (`metrics.json`, `metrics-flagon.json`), plus an axe-core run (`axe-flagon.json`).

**"Cards"** below is the number of visible bordered, rounded boxes inside `<main>` (cards, tiles and banners, nested ones included). This automated count is used to compare pages.

---

## Global problems (affect every page)

1. **28 top-level nav items in 6 groups**, and the list is taller than a 900 px screen. Settings, Team, System Logs, Notifications and Admin are below the fold on desktop (`00-sidebar-desktop.png`). On mobile the drawer needs two scrolls (`00-nav-drawer-*.png`).
2. **Every page opens with a paragraph of internals** in the page header, often followed by a full-width amber "Data" box and a grey disclaimer box. Content usually starts about 300 px down on desktop and about 2 screens down on mobile.
3. **The amber "Data · Sample | Broker candles" box** appears on Signals, Market Scanner, Backtesting, Option Chain and Factor Lab. Sample data is the normal state, yet it is shown in warning colours with the text *"Deterministic sample OHLCV; the backend computes everything for real on it."*
4. **The same risk disclaimer is repeated** on 7 pages (Signals, Market Scanner, Strategy Builder, Backtesting, Marketplace, Fundamentals, Factor Lab) (*"Trading in equities and derivatives involves substantial risk of loss and is not suitable for every investor. You remain responsible for every order placed from your account."*).
5. **Raw strategy ids are used everywhere** except the Library: `vwap_supertrend_5m`, `rsi_adx_momentum_5m` and so on appear on the Dashboard, Positions, Orders, Analytics, Autopilot, Journal, Coach and Notifications. The Library calls the same strategy "VWAP + Supertrend".
6. **Number, currency and date formats are inconsistent.**
   - Currency: the Dashboard shows `₹78,220`, Portfolio shows `78220.00` (no ₹, no separators), and the Paper account shows `10,00,436.29` (Indian grouping).
   - Dates: Orders, Signals and System Logs show `10/19/2026, 2:35:56 PM` (US). The Journal shows `19/10/26, 8:05 pm`. The Coach shows `2026-10-01 → 2026-10-18`. The Journal week is `2026-W43`.
   - Date inputs are native, render `mm/dd/yyyy`, and are truncated to `mm/dd/yy` on Options Builder.
7. **There is no shared symbol picker.** Symbols are entered as free text on Signals (`NIFTY`), Autopilot (`NIFTY 50`), Backtesting (`NIFTY`), Option Chain (Underlying `NIFTY`), Options Builder (`e.g. NIFTY`) and News (Symbol). Market Scanner and Factor Lab take a comma-separated input, and the Screener a monospace textarea. Fundamentals uses a select plus an "Add from NSE (symbols)" box. The Copilot thesis uses a `NIFTY 50` select.
8. **There is no shared timeframe control.** It is called "Timeframe" on Scanner and Strategy Builder (`5min`), "Base candles" on Autopilot (`1min`), "Bar size" on Factor Lab (`15min`) and "Scan timeframe" on the Screener (`1d`). Daily is `day` in tables. Option Chain has `5 min / 10 min / 15 min` chips, Coach has `7 days / 30 days / 90 days` chips, and Analytics has an `FY 2026-27` select.
9. **Controls look different from page to page.** Native `<select>` styling is used beside custom segmented controls, with three pill styles for PAPER/LIVE (the Dashboard checklist, Autopilot "Mode", the Copilot "Your day") and a select for the same choice (Analytics tax "Trades LIVE"). Links acting as buttons (`journal`, `Mark read`, `revoke`, `+ Add condition`) are 16–17 px tall.
10. **Accessibility** (axe-core, desktop, flags on):
    - About 70 form fields across Strategy Builder, Risk (15), Settings (8 broker credential password inputs), News (6), Factor Lab (9), Autopilot, Backtest and Marketplace have a visible label that is not bound to the input (`label`, `select-name`).
    - The LONG/SHORT pills fail contrast at 4.25:1 (`#ef4444` on `#311b23`, 12 px): 21 on Orders and 12 on Positions.
    - Much text is under 11 px (uppercase 10 px headings, `text-[10px]` table heads, the "ceiling 2" hints). The metrics count 53 text nodes under 11 px on Positions and 26 on the Dashboard.
    - There is no skip link. Focus rings show on inputs (`34-focus-sidebar-desktop.png`).
11. **Mobile:**
    - On 12 pages the tables are wider than the 390 px screen and scroll sideways inside their card: Positions 806 px, Orders 828, Portfolio 819, Autopilot 754, System Logs 679, Analytics 638, Account 576, Signals 569, Instruments 513, Admin 497, Team 413.
    - Many tap targets are under 32 px (metrics `tinyTargets`): Settings 47 (77 with flags on), Positions 32 (60 with flags on), Option Chain 31, Autopilot 23, Admin 22.
    - Pages are very long on a phone: Settings is 7,426 px (10,249 with flags on), Positions with flags on 6,778, the Dashboard 4,581, Admin 3,329 and Risk 2,933.
12. **The bottom nav promotes the Screener**, which is switched off by default, so the second tab of every phone session lands on a raw error (see Screener). It also uses the Radar icon for Screener, while the sidebar uses Radar for **Market Scanner** and a Filter icon for Screener.
13. **Banners stack up in the top bar** with flags on: a "Please read and accept the Terms of use..." strip on every page (`legal_pages`), "Verify your email", "LIVE paused on ..." and "MAINTENANCE".

---

## Per-page findings

### 00 Navigation: `src/components/Sidebar.tsx`, `src/components/BottomNav.tsx`
- **Job:** get to a page.
- **Crowding:** 27 items, plus Admin Console for SUPER_ADMIN, in the groups Overview, Trade (9), Research (6), Portfolio (7), System (4) and Platform.
  - "Coach & Guide" sits under Portfolio.
  - "Journal & Discipline" and "Coach & Guide" are separate items.
  - "Notifications" appears both as a bell in the top bar and as a sidebar item.
- **Wording:** the sidebar footer shows `SUPER ADMIN` and an amber `PAPER MODE` pill. Amber is used for the normal mode.
- **Mobile:** the drawer covers the page and needs about 2 scrolls. The bottom nav has 4 shortcuts plus More, and the Screener shortcut leads to a disabled feature by default.

### 01 Dashboard: `/` · `src/pages/DashboardPage.tsx`
Screenshots: `01-dashboard-desktop-full.jpg`, `01-dashboard-mobile-full.jpg`, `01e-dashboard-empty-*`, `32-error-dashboard-api-500-desktop.png`.
- **Job:** unclear. It tries to be a P&L overview, an automation monitor, a setup checklist and a product brochure at once.
- **Crowding:** 26 cards. The page holds:
  - 3 status pills plus an amber concentration pill;
  - 3 header buttons;
  - 6 KPI tiles;
  - Market Pulse;
  - P&L by strategy;
  - Strategy mix donut;
  - Exposure donut;
  - Running deployments;
  - Open positions;
  - Latest alerts;
  - a **15-row Go-Live checklist**;
  - **8 "Engines behind this console" marketing tiles**.

  That is 2,174 px on desktop and 4,581 px on mobile.
- **Developer wording:**
  - *"Numbers refresh every 30 seconds."*
  - The `Backend online` pill.
  - Checklist rows: *"Start the worker service (docker compose up worker, see docs/OPERATIONS.md 1.7)"*, *"Trading worker running · No heartbeat has ever been recorded"*, *"Computed from the platform's own state; nothing here changes anything. 'todo' blocks the target, 'warn' is allowed but unwise, 'info' is optional."*
  - Engine tiles: *"Eight-level limits, drawdown ladder..."*, *"Seven real adapters behind one interface"*.
  - Broker Funds tile: `- · 0 accounts · 7 adapters`.
  - Alerts list raw event types (`DEPLOYMENT_PAUSED`, `TRADE_CLOSED`, `RISK_ALERT`).
- **Colour:**
  - A red "Autopilot stopped" pill and a red "4 blockers for PAPER" (5 for a new user) on a working PAPER setup.
  - An amber TCS concentration pill and an amber "1 paused".
  - "New device login" alerts on every login are WARNING, with amber dots.
- **Empty state** (`01e`): mostly fine ("Nothing deployed yet", "Deploy the first one"). However, a brand-new PAPER user is greeted by a red "5 blockers for PAPER" and a list of operator tasks (instrument master, worker, holiday calendar), and a pre-filled "Verify your email" pill.
- **Error state** (`32`): every API returns 500, yet the page says *"Good afternoon, trader"*, the Net P&L tile says **"Log in"**, and other tiles show `•••` forever, with a red `Backend offline` pill. A server error is presented as being signed out.
- **Mobile:** 5.4 screens of scrolling. The KPI tiles stack 2-up. `Log in under Settings > Brokers` and `Build your own` are 16 px tall links.

### 02 Signals: `/signals` · `src/pages/SignalsPage.tsx`
- **Job:** run one inbuilt strategy on one symbol and see why it fired.
- **Crowding:** 4 cards. An amber Data box and a disclaimer come before the form.
- **Wording:**
  - *"Deterministic sample OHLCV; the backend computes everything for real on it... Bars and seed apply to sample data only."*
  - Form fields **"Sample bars 356"** and **"Sample data seed 7"** are developer knobs.
  - The history table shows raw strategy ids.
- **Controls:** the symbol is a free-text `NIFTY`, while Autopilot uses `NIFTY 50`. "Paper Execute" is a disabled grey button with low contrast.
- **Mobile:** the history table is 569 px wide.

### 03 Screener: `/screener` · `src/pages/ScreenerPage.tsx`
Screenshots: `03-screener-*`, `03-screener-flagon-*`.
- **Job:** build a multi-condition scan over a universe and list the passing symbols.
- **Default (flag off): error state.** The whole page is one line: *"The 'screener_v2' feature is currently disabled by the platform operator. Reference: a2d39b06562d4d7788d90bd884360837."* It shows the flag name and a request id, with no page header and no way forward, yet the page is in the sidebar and in the phone bottom nav.
- **Flag on:**
  - This is the cleanest page in the app: a title, toolbar, Universe, Conditions with templates, and Results.
  - It is the only page without the standard PageHeader, and it uses a full-width layout (`max-w-none`).
  - It has its own timeframe vocabulary (`1d`) and a monospace symbols textarea.
  - "Browse screens / Library tools / ETFs / Save / Run" are 5 toolbar buttons of equal weight.

### 04 Market Scanner: `/scanner` · `src/pages/ScannerPage.tsx`
- **Job:** filter a watchlist by indicator, structure and option-chain conditions. This is the same job as the Screener.
- **Crowding:** 7 cards: Data box, disclaimer, "Describe the scan (AI)", Watchlist, Indicator filters, Price-action filters, Option-chain filters, then Run.
- **Wording:**
  - *"Without an AI provider under Settings the deterministic parser is used."*
  - *"A sample option chain is generated per symbol only when at least one option filter is configured. A symbol never fabricates a pass when it has no chain data."*
- **Controls:** a comma-separated symbols input and a native `5min` select. "Plan filters with AI" is a disabled dark-blue button.

### 05 Strategy Library: `/strategies` · `src/pages/StrategiesPage.tsx`
- **Job:** browse the 13 inbuilt strategies.
- **Crowding:** 13 equal cards, each with a "Default parameters" disclosure that shows raw JSON.
- **Wording:** *"Every inbuilt auto-executable scalping strategy, straight from the registry."* "Registry" is internal, and "scalping" is wrong for the two daily swing strategies.
- There are no actions on a card (Backtest, Deploy or Chart). The page is a dead end.

### 06 Strategy Builder: `/strategy-builder` · `src/pages/StrategyBuilderPage.tsx`
- **Job:** compose rule-based entry conditions without code.
- **Crowding:** 4 cards plus a disclaimer.
- **Wording:** *"A deterministic, rule-based parser - not a call to an external AI (no AI-provider credentials are configured). It recognizes a fixed set of phrasings..."*
- **Controls:**
  - Each condition row has 6 native selects and inputs (`Indicator | RSI | 14 | same tf | > | Fixed value | 50`).
  - Option values are raw: `PLUS_DI`, `MINUS_DI`, `same tf`.
  - axe: 7 unlabelled inputs and 5 unlabelled selects.
- **Mobile:** the condition selects are 26–27 px tall.

### 07 AI Copilot: `/copilot` · `src/pages/AiCopilotPage.tsx` (tabs in `src/copilot/`)
Screenshots: `07-copilot-*` (first use), `07b-copilot-after-notice-*`, `07-copilot-flagon-*`.
- **Job:** unclear. It is a second dashboard with 7 tabs: Market Pulse, Strategy Lab, Idea Builder, Ask Copilot, Watchtower, News Radar, Coach & Scorecard.
- **First use:** a modal acceptance notice covers the page. This is fine legally. The same notice also blocks **Coach & Guide**, which is not an AI page.
- **Crowding** (Market Pulse tab, after the notice):
  - a hero with a 3D globe;
  - 3 gauges (Today's read, Sentiment, Regime);
  - Regime filters, Pre-flight checklist and Your day;
  - Market thesis (shadow), Today's events and Your deployments;
  - Market memory with Global cues.

  That is about 12 cards and 1,883 px.
- **Wording:**
  - *"No market read yet - press "Read now" in Market memory (needs a broker session), or wait for the worker after 09:15."*
  - *"Worker not seen in the last minutes - deployments will not trade (check the worker container)"*
  - *"The shadow size multiplier is recorded and scored only; it is applied nowhere."*
  - *"The worker reads the market from your broker every 15 minutes"*
  - "Market thesis (shadow)".
- **Colour:** an amber "NO MARKET DATA YET" chip, and red X icons in the Pre-flight checklist for a PAPER user.
- **Non-English:** a "Read in Marathi" disclosure in the notice (server text).

### 08 Autopilot: `/deployments` · `src/pages/DeploymentsPage.tsx`
- **Job:** create and manage strategy deployments.
- **Crowding:** 11 cards.
  - 4 status tiles and a red banner.
  - A deploy form with 5 sub-panels: instrument and option settings, Dynamic exits, Regime filter, OI gates, Mode.
  - Then the deployments table. The form alone is about 750 px on desktop.
- **Wording:**
  - Page description: *"Strategy deployments the background worker trades on its own: live broker candles every minute..."*
  - **"TRADING WORKER Stopped"** in big red, and *"No worker heartbeat yet. Nothing is being evaluated or monitored. Start the `worker` service (see the operations guide)."*
  - *"OI GATES (optional; checked before each new entry from the option chain's OI - missing or stale OI data blocks the entry...)"*
  - Placeholders `risk-based`, `off`, `spot (optional)`.
- **Colour:**
  - Red "Stopped" and a red banner are operator problems shown to the trader.
  - Amber text: *"Store broker credentials in Settings first - even paper trading needs a broker session for live candles."*
  - An amber PAUSED pill and amber pause note.
- **Controls:**
  - Symbol `NIFTY 50` as free text and Exchange `NSE` as free text.
  - "Base candles 1min".
  - Position select text truncated (`Buy (LONG→CE, SI`).
  - The actions are bare 12 px icons (pause and stop) with no labels.
- **Mobile:** the table is 754 px wide, and there are 23 small targets (the "trending up" and similar chips are 27 px).

### 09 Backtesting: `/backtest` · `src/pages/BacktestPage.tsx`
- **Job:** simulate a strategy on history.
- **Crowding:** 5 cards. Reasonable before a run (the result view was not captured: it needs a run).
- **Wording:** the amber Data box; *"DYNAMIC EXITS (SAME RULES THE LIVE MONITOR APPLIES)"*; "Sample bars 600". In the code (after a run): *"Grid as JSON (max 60 combinations)"* and *"No recorded chain quotes yet - the worker records the chains of ACTIVE option deployments every few minutes"*.
- **Controls:** the symbol is free text and "Trade as" is a native select.

### 10 Marketplace: `/marketplace` · `src/pages/MarketplacePage.tsx`
- **Job:** browse and subscribe to strategies, and publish your own.
- **Crowding:** 6 cards. The **admin revenue/terms panel is inline on the user page** (Sales, Gross, Platform fees, Creators' share, Paid out, Open charges tiles), with raw config keys as labels: `platform_fee_pct`, `min_payout`, `max_listing_price`.
- **Wording / bugs:** money tiles show **"Free"** for ₹0 (Gross Free, Platform fees Free, Paid out Free).
- **Colour:** an amber full-width banner *"The strategy marketplace needs the Pro or Business plan (current: Free)."*
- **Empty state:** "Nothing published yet." The publish form is shown even though the plan cannot use it.

### 11 Fundamental Analysis: `/fundamentals` · `src/pages/FundamentalsPage.tsx` (1,188 lines)
- **Job:** keep a cited fundamentals file per company and score it.
- **Empty state:** an empty company select, "+ Add Company", "Add from NSE (symbols) [Fetch]", and *"No companies yet — add one above to get started."* Clean, but two adjacent add flows with no explanation of which to use.
- **Wording:** *"Institutional-grade company intelligence... nothing here is fetched from a live feed (no SEBI/NSE/BSE credentials are wired in yet)."* In the code: *"financial statements are not published as JSON by NSE"*.

### 12 Option Chain: `/option-chain` · `src/pages/OptionChainPage.tsx`
- **Job:** read one underlying's chain (PCR, max pain, OI) and set OI alerts.
- **Crowding:** 7 cards. The chain itself does not appear until "Analyze Chain" is pressed. What shows by default is two empty OI cards, an **OI banner settings** form (6 numeric fields) and an **OI banner alerts** form (8 checkboxes, 5 fields, 4 buttons). Settings dominate the page.
- **Wording:**
  - "Sample OI tilt".
  - *"Collector stale (ops)"*.
  - *"Snapshots to confirm a flip"*.
  - Flag on: *"OI banner unavailable: The OI banner is read from a shared option-chain collector, not from your own broker session, so it is off while the bring-your-own-broker data tier is on."*
- **Controls:** Underlying and LTP are free text; the `5 min / 10 min / 15 min` chips are 22 px tall.
- **Mobile:** 31 targets under 32 px.

### 13 Options Builder: `/options-builder` · `src/pages/OptionsBuilderPage.tsx`
- **Job:** build a multi-leg option position and see its payoff.
- **Crowding:** 15 cards.
  - A 9-field parameter strip (Underlying, Spot, Strike step, Lot size, Template width, Base IV, Near expiry, Next expiry, Brokerage).
  - 5 category tabs and 8 template tiles.
  - The payoff, metrics and actions.
  - "Adjustments and compare: **Coming later**".
- **Wording:** helper text under the fields: *"from the instrument master"*, *"prices template legs"*. *"Saving strategies is not turned on for your organisation yet."*
- **Controls:**
  - Native date inputs truncated to `mm/dd/y`.
  - Template tiles look disabled until 5 fields are filled, with no indication of which ones.
  - "Place as basket" wraps onto 2 lines.

### 14 Instruments: `/instruments` · `src/pages/InstrumentsPage.tsx`
- **Job:** reference list of MCX and crypto contract specs.
- **Crowding:** 2 tables. It is a static reference that does not need a top-level nav slot.
- **Wording:** an 80-word description: *"...beyond plain NSE/BSE equity & index options (which already size correctly off your Risk Management lot size). These are static reference contract specifications... not a live price feed..."*. Symbol `USDTINR_CRYPTO`.
- **Mobile:** the table is 513 px wide.

### 15 News & Events: `/news-events` · `src/pages/NewsEventsPage.tsx`
- **Job:** a cited calendar of market events, plus an optional feed.
- **Crowding:** 4 cards. A large "Add a cited event" form (8 fields) sits on the main reading page.
- **Wording:**
  - **"LIVE FEED SOURCES off (feature flag news_feed)"**.
  - Flag on: *"News trust: 1.00 (0 verdicts, 10 more verdict(s) before the trust applies)"*.
- **Colour:** an amber `unverified feed` chip inside the description.
- **Controls:** a native date input (`10/19/2026`).

### 16 Factor Lab: `/quant` · `src/pages/QuantPage.tsx`
- **Job:** rank a watchlist by factors.
- **Crowding:** 3 cards, with 7 weight sliders in one row.
- **Wording:** the amber Data box; *"Weights are renormalised; a factor a symbol has no data for carries no weight in its composite."*
- **Naming:** "Factor Lab" (sidebar), `/quant` (route) and `QuantPage` are three names for one page.
- **Mobile:** the slider inputs are 16 px tall (10 targets).

### 17 Positions: `/positions` · `src/pages/PositionsPage.tsx`
Screenshots: `17-positions-*`, `17-positions-flagon-*`, `17e-*`, `31-error-positions-api-down-desktop.png`.
- **Job:** see and manage open positions. Today it also hosts the trade history, contract notes and (with flags on) the PAPER account, ladder, experience level, portfolio risk, decision tools and the order ticket.
- **Crowding:**
  - Default: 8 cards, 1,663 px.
  - **Flag on: 24 cards, 4,203 px desktop and 6,778 px mobile.** Paper account, Progression ladder, Experience level, Portfolio risk (duplicating the Portfolio page), Decision tools (Kelly, what-if), Plan-before-trade ticket with a 6-item checklist, 3 KPI tiles, Position chart, Open positions, Trade history (21 rows), Contract notes upload.
- **Wording:**
  - The description is wrong: *"Paper trades executed from the Signals tab while signed in as demo@atpdemo.in."*
  - Per position: *"Live price and P&L are on the chart above when a broker is logged in; or check a price by hand: [Current price] [Check price]"*.
  - Amber: *"Live chart needs a broker with a valid session. Add the API key under Settings > Brokers (stored encrypted, never in chat or the environment)..."*
  - Charges `0.00 EST.`.
  - Flag on: *"Figures from your own PAPER trades priced from platform quotes... Trades priced from your own inputs (console candles) are left out."*
- **Colour:** an amber chart banner on the normal state, and a red "Exit now" outline button on every row (flags on).
- **Error state** (`31`): *"Cannot reach the server..."* in red text, but the tiles still show `0 / 0 / 0` and *"No open positions yet - execute a signal from the Signals tab."*, so the error reads like an empty account.
- **Accessibility:** the LONG/SHORT pill contrast fails (12 nodes). The "journal" link is 16 px tall.
- **Mobile:** the history table is 806 px wide, with 32 small targets (60 with flags on).

### 18 Portfolio: `/portfolio` · `src/pages/PortfolioPage.tsx`
- **Job:** exposure and capital use across open positions.
- **Crowding:** 13 cards: 4 KPI tiles, then 5 more exposure tiles, then the table and a chart.
- **Wording:** "EXPOSURE (ENTRY PRICES - NO BROKER SESSION)"; *"Set portfolio-wide caps ... under Risk Management → limits, scope "Portfolio"."* The chart's y-label reads `-295 – 877`.
- **Formats:** `78220.00`, `436.29` and `78220 (78%)`, with no ₹ and no separators, unlike the Dashboard.
- **Colour:** amber `41.2%` and an amber bullet *"TCS is 41% of capital - concentration risk"*. The same warning is also on the Dashboard.
- **Mobile:** the table is 819 px wide.

### 19 Orders: `/orders` · `src/pages/OrdersPage.tsx`
- **Job:** a fill blotter.
- **Crowding:** low. One table of 39 rows, no filters and no paging.
- **Wording:** raw exit reasons (`TARGET1`, `STOP_LOSS`, `SQUARE_OFF`) beside prose ("Buy to open"). Raw strategy ids. A US date format.
- **Accessibility:** 21 contrast failures (pills).
- **Mobile:** the table is 828 px wide.

### 20 Analytics: `/analytics` · `src/pages/AnalyticsPage.tsx`
- **Job:** performance stats from closed trades.
- **Crowding:** 12 cards. **A tax report** with a 4-bullet legal footnote sits *above* the headline stats.
- **Wording:**
  - The "LIVE VS BACKTEST" table labels PAPER trades as **"LIVE TRADES"** and "LIVE WIN RATE".
  - *"Turnover method: ABS_PNL ... The method is a setting (TAX_FNO_TURNOVER_METHOD)"*.
  - The column header is "KEY".
  - *"Aggregated from your full persisted trade history (Positions/Trade Journal)."*
- **Defaults:** the tax report defaults to **Trades: LIVE** for a PAPER-only user, so it shows a wall of zeros (also the first thing on mobile, `20e-analytics-empty-mobile.png`).
- **Mobile:** the tax table is 638 px wide and its select controls are 23 px tall.

### 21 Journal & Discipline: `/journal` · `src/pages/JournalPage.tsx`
- **Job:** a weekly discipline score and behaviour tags.
- **Default (flag off): dead page.** *"Not enabled. The discipline engine is turned off for this organisation. A platform operator can turn on the discipline_engine feature."* The page is still in the sidebar.
- **Flag on:** clean (score breakdown and trades this week).
  - The week is an ISO label `2026-W43`, and the dates are `19/10/26, 8:05 pm`.
  - Its "Discipline score 100/100" disagrees with the Coach's "Discipline grade A 94/100".

### 22 Coach & Guide: `/coach` · `src/pages/CoachGuidePage.tsx`
- **Job:** patterns in your own trades.
- **Default:** blocked by the AI Copilot first-use notice (including "Read in Marathi"), although the coach is rule-based.
- **Flag on / after the notice:**
  - 15 cards: grade, 6 KPI tiles, Patterns, Coach observations, an equity curve, and By strategy / By entry hour / By symbol.
  - It overlaps Analytics almost exactly: win rate, profit factor, net P&L, by strategy, by symbol.
  - It has its own `Mode All / PAPER / LIVE` and `Period` chips.

### 23 Risk Management: `/risk-management` · `src/pages/RiskManagementPage.tsx`
- **Job:** set sizing and loss limits.
- **Crowding:** 7 cards, 13 numeric inputs, Guardian status, Market events, Risk limits and Risk events.
- **Wording:**
  - Internal rule codes in the help text: *"(R4)"*, *"(R10)"*, *"(P2)"*, *"(P3)"*, *"(M8)"*.
  - *"...the Risk Engine checks before every paper-execute order. Anonymous calls always use the platform default..."*
  - "Scope id".
  - *"Value (₹ realised today; breach stops the organisation)"*.
  - `ceiling 2` hints at 10 px.
- **Accessibility:** 15 unlabelled inputs and 4 unlabelled selects.
- **Controls:** a native date input (`10/19/2026`), and HH:MM typed as text (`from HH:MM`).

### 24 Settings: `/settings` · `src/pages/SettingsPage.tsx`
Screenshots: `24-settings-*`, `24-settings-flagon-*`, `24b-settings-brokers-*`, `24c-settings-brokers-stored-*`.
- **Job:** unclear. It is one long page of 14 unrelated sections (15–16 with flags on):
  - Appearance, Broker session health, Connected brokers, Add/update credentials, Broker accounts;
  - Static IP (SEBI);
  - Plan & billing (and plan limits when the flag is on);
  - Alert delivery (Telegram, Email, Webhook, Push, SMS, and WhatsApp when the flag is on);
  - Public API keys, AI provider and DPDP consent, Vision BYOK, TradingView webhook.
- **Crowding:** 21 cards, 80 controls (119 with flags on), **4,497 px desktop** (6,043 with flags on) and **7,426 px mobile** (10,249 with flags on). There are no tabs or sub-navigation.
- **Wording:**
  - Page description: *"...only ever decrypted in memory when you authenticate - never logged, never returned in plaintext by any API response."*
  - **"This server's egress IP: not configured (SERVER_EGRESS_IP) - not enforced yet (STATIC_IP_REQUIRED_FOR_LIVE off)."**
  - *"A role can change 1 time(s) in 7 days."*
  - *"Every event is POSTed as JSON with X-ATP-Signature: sha256=HMAC-SHA256(secret, timestamp + "." + body)..."*
  - *"Keys reach only /api/public/v1/* (never the console endpoints)... GET /api/public/v1/docs... X-API-Key header"*.
  - AI provider: the model table lists `claude-opus-5-5`, `claude-sonnet-5-5`, `claude-haiku-5-5` with token-count footnotes, and the status chip is `rule_based · nlu-parser-v1`.
  - *"Vision with your own AI key is not enabled for this organisation yet (feature vision_byok)."*
  - The TradingView JSON schema `{ strategy_id, symbol, ... }` is printed inline.
  - The billing pill reads `NONE`.
- **Colour:** an amber DPDP consent box, amber `UNKNOWN` token pills, an amber "Disable", and the amber note *"external providers need the Pro or Business plan"*.
- **Non-English:** "Read in Marathi" on the DPDP consent, and the "AI Copilot languages: Marathi / None" toggle under Appearance.
- **Accessibility:** the 8 broker credential inputs have no bound labels (`<label>` without `htmlFor`). The `Read in Marathi` link fails contrast (4.29:1).
- **Mobile:** 47 targets under 32 px (77 with flags on), including 13 px checkboxes and 23–27 px selects.
- See the dedicated **Settings → Brokers** section below.

### 25 Team: `/team` · `src/pages/TeamPage.tsx`
- **Job:** invite members and set organisation policy.
- **Crowding:** 12 cards, including 4 KPI tiles (Organisation, Your role, Active members, Open invites) that repeat the table, and a plan-usage block repeated from Settings → Billing.
- **Wording:**
  - The organisation name is the owner's email.
  - *"tagged `ALGOID-strategy-leg` at the broker"*.
  - "Default account routing: Explicit".
- **Colour:** amber progress bars and amber text *"Paper trading only on this plan - LIVE deployments need Pro or Business. Contact support to upgrade."* (the seed made deployments 3/2, so the bar is full).
- **Mobile:** the table is 413 px wide. The Reporting currency and Routing selects are 23 px tall.

### 26 System Logs: `/system-logs` · `src/pages/SystemLogsPage.tsx`
- **Job:** your own login and security history.
- **Wording:** raw event names (`user_login`, `email_verified`, `user_registered`), raw results (`ok (password)`, `failed: unknown_email`) and full user-agent strings.
- The name "System Logs" suggests platform logs, but the page is a personal security log, and it largely duplicates Account → sessions.
- **Mobile:** the table is 679 px wide.

### 27 Notifications: `/notifications` · `src/pages/NotificationsPage.tsx`
- **Job:** the in-app alert feed, plus alert rules (tab "Rules & delivery").
- **Wording:**
  - Raw types `SECURITY`, `DEPLOYMENT_PAUSED`, `TRADE_CLOSED`, `RISK_ALERT`, `TRADE_EXECUTED`.
  - A full user agent in "New device login" (*"Mozilla/5.0 (X11; Linux x86_64) ... HeadlessChrome/141..."*).
- **Colour:** every login creates an amber WARNING card.
- **Controls:** the "Mark read" link is 17 px tall. The "Rules & delivery" tab overlaps Settings → Alert delivery.

### 28 Admin Console: `/admin` · `src/pages/AdminPage.tsx`
- **Job:** platform operations (SUPER_ADMIN only).
- **Error state** (captured naturally: the demo admin has no MFA):
  - The 5 KPI tiles show `•••` forever.
  - *"This action requires two-factor authentication - enable it from the Account tab first"* is shown twice in red.
  - Beta rollout says *"Loading the cohort…"* forever, and the Feature flags table is empty.
  - Other sections still render, so the page looks half broken instead of showing a single "set up MFA" gate.
- **Crowding:** 22 cards. Readiness, Holidays, Kill switch, Maintenance, Disabled brokers, Beta, Risk ceilings, Feature flags, Incidents, Tenants, Export, Audit trail.
- **Wording:** acceptable for operators, but the Disabled brokers placeholder `upstox, zerodha` and the "Comma-separated names" field ask for raw ids.

### 29 Account: `/account` · `src/pages/AccountPage.tsx`
- **Job:** sign-in, email, MFA, password and sessions.
- **Wording:**
  - "Your permissions" shows 13 raw scope strings (`admin:platform`, `api_keys:manage`, `trading:live`, …) in monospace.
  - The sessions list shows "Other device" plus a full user agent, with a 16 px `revoke` link.
- **Colour:** an amber "Not enabled" for MFA.
- **Mobile:** the sessions table is 576 px wide.

### Other states captured
- `30-error-not-found-desktop.png`: a clean "This page does not exist" card.
- `33-signed-out-*`: the signed-out Dashboard shows "Log in" inside the Net P&L tile, which is the same visual as the 500 error.

---

## Overlapping pages to merge or rename

| Pair / group | Why they overlap | Suggestion |
|---|---|---|
| **Screener ↔ Market Scanner** | Both filter a symbol list by indicator conditions and list passes. The Scanner adds structure and option-chain filters and an AI "describe the scan" box; the Screener (v2) has templates, live counts, a library and ETFs. The phone bottom nav calls the Screener by the Scanner's icon. | One "Scanner" page (the Screener v2 UI), with option-chain and structure conditions as condition types. |
| **Strategy Builder ↔ Copilot "Strategy Lab" / "Idea Builder" ↔ Market Scanner "Describe the scan (AI)"** | Three ways to turn words into rules (a deterministic parser, an AI interview, an AI scan planner). | One "Build" flow with an optional AI assist. |
| **Strategy Library ↔ Marketplace ↔ Strategy Builder "Your saved strategies"** | Three lists of strategies (inbuilt, others', your own). | One "Strategies" page with tabs: Inbuilt / Mine / Marketplace. |
| **Signals ↔ Backtesting ↔ Strategy Library** | Signals runs one strategy on one symbol and Backtesting runs it over history, with the same form (Strategy, Symbol, Sample bars, Data box). The Library has no actions. | A strategy detail page with Signal now / Backtest / Deploy actions. |
| **Dashboard ↔ AI Copilot "Market Pulse"** | Both are home dashboards: deployments, today's P&L and loss budget, checklist ("Go-Live checklist" vs "Pre-flight checklist"), market status. | One home page. The Copilot becomes an assistant panel, not a second home. |
| **Positions ↔ Portfolio** | Both show open positions, capital deployed and exposure; with flags on, Positions even embeds "Portfolio risk". The concentration warning is on both (and on the Dashboard). | One "Portfolio" page with Open / History / Exposure tabs. |
| **Positions (trade history) ↔ Orders** | Orders is the same trades split into entry and exit rows. | A tab of Portfolio ("Fills"). |
| **Analytics ↔ Coach & Guide ↔ Journal & Discipline ↔ Copilot "Coach & Scorecard"** | Four places compute win rate, profit factor, by-strategy and by-symbol stats and a discipline score, with two different discipline numbers (Journal 100/100, Coach A 94/100). | One "Performance" page (stats + coach notes) and one "Journal" page. Move the tax report to Reports or Account. |
| **News & Events ↔ Copilot "News Radar" ↔ Risk "Market events"** | Event and news lists in three places. Risk's "Market events" are blackout dates, also called events. | Rename Risk's to "Blackout days". Keep one News page. |
| **Notifications page ↔ bell ↔ Notifications "Rules & delivery" ↔ Settings "Alert delivery"** | Alert configuration is split across two pages, and the feed has two entry points. | Feed under the bell only. All alert settings in one place. |
| **System Logs ↔ Account (sessions) ↔ Notifications (new-device logins)** | The same login events in three places. | Fold System Logs into Account → Security, and rename it "Login history". |
| **Team ↔ Settings → Plan & billing** | Plan usage bars appear on both. | Billing only in one place. |
| **Option Chain ↔ Options Builder** | Both start from an underlying and expiry. The Builder asks again for spot, strike step, lot size and IV, which the Chain already knows. | Builder opened from the Chain with the parameters pre-filled. |
| **Instruments ↔ Fundamentals** | Both are reference data on a symbol, and Instruments is a static table. | Instruments becomes a help/reference page, not a nav item. |
| **Names** | "Autopilot" (sidebar) = `/deployments` = "Deployments" in Analytics and the Copilot. "Factor Lab" = `/quant`. "Coach & Guide" vs the Copilot's "Coach & Scorecard". "Journal & Discipline" (sidebar) vs "Journal & discipline" (page). | Pick one name per thing. |

---

## Settings → Brokers: today's flow and fields

**Where:** `frontend/src/pages/SettingsPage.tsx`, `src/components/BrokerTokenBanner.tsx`, `src/components/BrokerAccountsCard.tsx`; backend `backend/app/brokers/registry.py`, `routes.py`, `token_lifecycle.py` and the 7 adapters.

### What the user sees today (`24b-settings-brokers-*.png`, and `24c-settings-brokers-stored-*.png` with a stored fake key)
1. **Broker session health:** a paragraph about expiry times. Once something is stored, there is one row per credential, for example `Zerodha · UNKNOWN (amber) · session not yet verified - authenticate or log in to enable LIVE · [Open zerodha login]`, a "Paste today's request_token (or the whole redirected address)" input and `[Login with code]`. Upstox gets `[Login to Upstox]` (OAuth) and shows the callback URL to register.
2. **Connected brokers (n):** a row `Zerodha / primary updated <date>` with **Authenticate** and **Remove** text links.
3. **Add / update broker credentials:**
   - a `Broker` native select of the 7 raw ids (`zerodha, upstox, shoonya, angel_one, fyers, dhan, coindcx`; CSS capitalises them to "Angel_one");
   - `Account label` (default `primary`);
   - a note: *"Fill only what you are changing - a field left blank keeps its stored value..."*;
   - **the same 8 password fields for every broker**: API Key, API Secret, Access Token, Request Token, Client ID, PIN, TOTP Secret, Redirect URI;
   - `[Store credentials]`.
4. **Broker accounts (n):** a row `☆ Zerodha Primary #1 · zerodha/primary · ACTIVE · token UNKNOWN · [Sync] [Read-only check] [Disable]` with balance and margin.
5. Elsewhere on the same page, **Static IP (SEBI)** has its own broker select (`upstox, zerodha, fyers, dhan, angelone, shoonya`).

### Backend rules that the form does not show
- `POST /api/v1/broker/{name}/credentials?account_label=`:
  - requires the `brokers:write` scope and a **verified email**;
  - requires an **MFA step-up when the user is SUPER_ADMIN or the organisation set `require_mfa_for_live`**;
  - merges blank fields with the stored values;
  - resets the token status to UNKNOWN.
- `POST /{name}/authenticate` decrypts and calls `adapter.authenticate()`, then marks the token VALID until the broker's daily expiry. It **takes no account_label and always uses the `primary` row**, and the UI's Authenticate button does not pass the label either. On a second account ("Authenticate" on `zerodha/backup`), the primary is authenticated instead.
- `GET /{name}/login-url` + `POST /{name}/login-code` is the paste-the-code daily login, for **Zerodha (`request_token`) and Fyers (`auth_code`)** only.
- `GET /upstox/oauth/start` → callback is the browser OAuth flow, for **Upstox** only.
- Daily expiry (IST): Upstox 03:30, Angel One 05:00, Zerodha/Shoonya/Fyers/Dhan 06:00. CoinDCX keys are permanent.

### Per-broker field map (what each adapter actually reads)

| Broker (id) | Required to store and construct | Needed for login / authenticate | Daily login method | Fields from the 8 that are **unused** | Hidden extras the adapter reads (no UI field) |
|---|---|---|---|---|---|
| Zerodha Kite (`zerodha`) | `api_key` | `access_token` **or** (`request_token` + `api_secret`) | Paste-the-code (`request_token`) via "Open zerodha login". Expires 06:00. | client_id, pin, totp_secret, redirect_uri | – |
| Upstox (`upstox`) | `api_key` (= client_id) | `access_token` **or** (`request_token` (OAuth code) + `api_secret` (= client_secret) + `redirect_uri`) | Browser OAuth "Login to Upstox". The redirect URI must equal the shown callback. Expires 03:30. | client_id, pin, totp_secret | – |
| Shoonya / Finvasia (`shoonya`) | `client_id` (uid) + `api_secret` (**the login password**) + `api_key` (vendor API key) | `totp_secret` is sent as **factor2, the current OTP or DOB/PAN, not a TOTP secret** | Authenticate each day with a fresh factor2. Expires 06:00. | access_token, request_token, pin, redirect_uri | `vc` (vendor code, default `<uid>_API`), `imei` (default `abc1111`) |
| Angel One SmartAPI (`angel_one`) | `api_key` (SmartAPI app key) **and** (`access_token` **or** `client_id` + `pin`) | `client_id` + `pin` + `totp_secret` (a base32 secret, or a 6–8 digit code used as-is) | Authenticate (server computes the TOTP). Expires 05:00. | api_secret, request_token, redirect_uri | `local_ip`, `public_ip`, `mac` (headers; default 127.0.0.1 and zeros) |
| Fyers (`fyers`) | `api_key` (App ID) **and** (`access_token` **or** `request_token` + `api_secret`) | `request_token` (= auth_code) + `api_secret`; `redirect_uri` needed to build the login URL | Paste-the-code (`auth_code`) via "Open fyers login". Expires 06:00. | client_id, pin, totp_secret | – |
| Dhan (`dhan`) | `client_id` + `access_token` (generated in the Dhan console) | Authenticate = profile check only | Paste a new access token when it expires. Expires 06:00 per the registry. | api_key, api_secret, request_token, pin, totp_secret, redirect_uri | – |
| CoinDCX (`coindcx`) | `api_key` + `api_secret` (checked when signing) | Authenticate = `users/info` | None (permanent key). | access_token, request_token, client_id, pin, totp_secret, redirect_uri | – |

### Problems in this flow
- **Every broker gets all 8 fields.** For example, Dhan needs 2 of the 8 and CoinDCX needs 2 of the 8. None is marked required.
- **The labels are misleading for 2 brokers.**
  - Shoonya's password goes in "API Secret" and its one-time 2FA code in "TOTP Secret".
  - Upstox's client id goes in "API Key", while the separate "Client ID" field is ignored.
- **The broker id is shown raw** (`angel_one`).
- **Static IP uses `angelone`**, which never matches the backend name `angel_one`.
- **One broker appears in 3 cards** with 3 different action sets: "Open zerodha login / Login with code", "Authenticate / Remove" and "Sync / Read-only check / Disable". It is unclear which one to press.
- **Authenticate ignores the account label** (described above).
- **The form gives no hint** that storing needs a verified email and, for a SUPER_ADMIN, an MFA step-up. The user only learns this from an error after typing secrets.
- **The fields have no bound labels** (axe), so a screen reader reads 8 identical unnamed password boxes.
- **Contradictory copy:** "Fill only what you are changing" is shown even on a first setup, and the health text says Kite tokens *"cannot be refreshed automatically"* next to a button that does exactly that for Upstox.

---

## The 10 worst problems, ranked

1. **Pages are crowded with no hierarchy.**
   - Settings is one 4,500–6,000 px page of 14+ unrelated sections; on mobile that is 7,400–10,200 px and 80–119 controls.
   - Positions with flags on is 24 cards and 4,200 px. The Dashboard is 26 cards and 2,174 px, including a 15-row checklist and 8 marketing tiles.
   - No page has tabs or progressive disclosure for its secondary tools.
2. **System internals and developer wording are on user screens**, for example:
   - *"docker compose up worker, see docs/OPERATIONS.md 1.7"*;
   - "TRADING WORKER Stopped" and *"No worker heartbeat yet"*;
   - *"the backend computes everything for real on it"*;
   - `SERVER_EGRESS_IP`, `STATIC_IP_REQUIRED_FOR_LIVE`, `TAX_FNO_TURNOVER_METHOD`;
   - `(feature flag news_feed)`, `feature vision_byok`, `discipline_engine`, "The 'screener_v2' feature is currently disabled... Reference: a2d39b..." ;
   - rule codes (R4)/(P2)/(M8), `platform_fee_pct`, raw scopes, raw event types, model ids, "straight from the registry".
3. **Warning and error colours are used for normal states.** Sample data shows an amber Data box on 5 pages. A healthy PAPER account shows a red "Autopilot stopped", red "4–5 blockers for PAPER", red pre-flight X's and an amber `PAPER MODE`. Every login creates an amber WARNING. Plan-gated sections get amber banners. A real problem cannot stand out.
4. **The navigation is too big and full of overlaps.** There are 28 items. Screener vs Market Scanner, Positions vs Portfolio vs Orders, Analytics vs Coach vs Journal vs the Copilot scorecard, and the Dashboard vs the Copilot "Market Pulse" do the same jobs. Two discipline scores disagree (100 vs A/94). The list does not fit a desktop screen.
5. **Flag-off pages are dead ends that are still promoted.** The Screener (second item of the phone bottom nav) and Journal & Discipline show only an operator message naming the flag. Coach & Guide is blocked behind the AI Copilot notice.
6. **The broker setup asks for the wrong things.** It uses one generic 8-field form for 7 brokers with very different needs (2–5 real fields each), mislabels Shoonya and Upstox fields, has no "required" marks, shows a broker in 3 cards with 3 action sets, ignores the account label on Authenticate, misspells `angelone` in Static IP, and hides the verified-email and MFA preconditions until after the user types secrets.
7. **Error states look like empty or signed-out states.** A 500 on the Dashboard shows "Good afternoon, trader" and **"Log in"** in the P&L tile, with endless `•••`. A network failure on Positions shows `0/0/0` and "No open positions yet". Admin without MFA is half rendered with `•••` tiles and "Loading the cohort…" forever.
8. **The basic controls are inconsistent**: symbol entry (8 different patterns), timeframe (6 names and formats), dates (US `10/19/2026`, `19/10/26`, ISO weeks, native pickers truncated to `mm/dd/yy`), currency (`₹78,220` vs `78220.00` vs `10,00,436.29`), PAPER/LIVE switches (3 visual styles plus a select), and raw strategy ids vs display names.
9. **Mobile is a stacked desktop.** 12 pages have tables 413–828 px wide on a 390 px screen. There are many targets under 32 px (Settings 47–77, Positions 32–60, Option Chain 31, Autopilot 23; links 16–17 px, checkboxes 13 px). On most pages the first screen is page description, amber Data box and disclaimer, with no content.
10. **Accessibility:**
    - About 70 inputs and selects have visible but unbound labels (including all 8 broker secrets and 15 Risk fields).
    - The LONG/SHORT and SHORT/loss pills fail AA contrast (4.25:1) on Orders and Positions.
    - Many headings and table heads use 10–11 px uppercase text (53 text nodes under 11 px on Positions).
    - There is no skip link past the 28-item nav, and icon-only row actions (Autopilot pause and stop) have no visible label.

---

## Pages that could not be fully rendered, and why
- **Screener and Journal (default):** they render only their "disabled" message, because `screener_v2` and `discipline_engine` are off by default. The full pages were captured with the flags on (`03-*-flagon-*`, `21-*-flagon-*`).
- **Admin Console:** the KPI tiles, readiness, beta and feature-flag table did not load, because the SUPER_ADMIN demo user has no MFA and those calls require it. That error state is what was captured. The MFA was not enrolled.
- **Result views needing an action** (a Backtest result, a Signal result, a Scanner result, a filled Option Chain or Options Builder payoff) were not captured. These audit shots show each page as it opens.
- **Live market widgets** (Market Pulse charts, live prices, OI banner) show their "needs a broker session" state, because no broker or worker is used, by design.
- **Real Upstox OAuth or Zerodha login** was not exercised. The broker forms are shown with nothing stored (`24b`) and with an obviously fake Zerodha key stored for the empty user (`24c`); Authenticate was never pressed.
