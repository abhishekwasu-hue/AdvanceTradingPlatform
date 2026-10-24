# UX audit of every page (Master Order 3, part 2)

Status: audit report only. No page is changed by this document; the fixes follow in separate PRs (one page or a small
group each), in the order of section 1. Base: branch `claude/font-size` (PR #335, the new type scale, 16 px body),
so every picture shows the new text size.

Binding inputs: `docs/owner-orders/ATP_SESSION_UX_AUDIT_MASTER.md` (the standard U1-U12) and
`docs/owner-orders/ATP_HANDOVER_PACK.md` section 8 (overrides). Per section 8a there is **no Simple / Pro switch**:
there are exactly two groups, **USER** and **ADMIN**, and every element below is tagged **User** (stays for traders),
**Admin** (moves to the admin console) or **Remove** (deleted).

How to read the scores: ✓ = passes, ✗ = fails, with a one-phrase reason. "v2" = the redesigned shell behind the
`ui_v2` flag; "old shell" = what every user gets today, because `ui_v2` is OFF by default.

Contents

1. The 10 biggest problems and the order of the fixes
2. Method, evidence and limits
3. Every route, page, sub-page and modal (the inventory)
4. Page-by-page audit (scores, element lists, primary action, mock)
5. Pages to merge or drop, and the proposed sidebar (USER vs ADMIN)
6. The admin console proposal and its server-side enforcement
7. Feature flags: what each one does, and which OFF flags are safe to turn on
8. Banned UI words (input for the future CI guard)
9. The fresh-eyes test (8 tasks)
10. Cross-cutting findings (shell, states, consistency)

---

## 1. The 10 biggest problems and the order of the fixes

| # | Problem | Where (evidence) | Hurts |
|---|---|---|---|
| 1 | **Every user still gets the old shell.** `ui_v2` is OFF by default (it is not in `DEFAULT_ON_FLAGS`), so the clean v2 pages already built (Today, Charts, Option chain, Signals, Positions, Portfolio, Risk, Setup) are invisible. Users see the old 28-item sidebar, "Backend online · Autopilot trading · beat 12s ago", the Signals page that generates a signal on **sample** candles with `NIFTY` pre-filled, and the old option chain. | `frontend/e2e/__screenshots__/front-page-1440-light.png`, `signals-390-light.png`, `option-chain-390-light.png` | everyone, from the first minute |
| 2 | **There is no sign-in screen.** A signed-out visitor at `/` gets the old dashboard with "Backend offline", "Some dashboard data could not be loaded: Not found" and empty number tiles; sign-in hides behind small sidebar text ("Not signed in - click to log in") or the account icon on a phone. The guest sees the full 28-item sidebar. After logging in the user stays on the Account page, which opens with a list of permission scopes (`ai:manage`, `trading:live`, ...). The e-mail and password labels are not tied to their fields. | `ux-audit/sign-in-1440.png`, `sign-in-390.png`, `settings-account-1440.png`; section 9 task 1 | every new user |
| 3 | **"Start paper bot" lands on the hardest page.** The Get started card sends a beginner to Autopilot (`/deployments`): about 40 controls on one screen (13 option structures, OI gates, regime filter, dynamic exits, "instrument master", "the background worker"), "Not found" toasts, and a LIVE / PAPER switch. | `ux-audit/autopilot-1440.png`, `autopilot-390.png` | the core beginner task |
| 4 | **Raw errors and flag names reach the user.** "Not found" (the server's 404 text) shows as a card or toast on 15 pages; "The screener is not turned on for this organisation yet" opens from **Find setups in the main sidebar**; "Vision with your own AI key is not enabled for this organisation yet (feature vision_byok)". Handover 8b says a flag-off page must be hidden, and a direct URL must say "Coming soon". | `ux-audit/screener-1440.png`, `settings-1440.png`, `analytics-1440.png`, `autopilot-390.png` | trust; looks broken |
| 5 | **Developer text everywhere outside "Advanced".** Settings → Alert delivery (HMAC-SHA256 signature recipe, `X-ATP-Signature`, "floor INFO / WARNING / CRITICAL", SMTP STARTTLS, SMS gateway body templates), Public API keys (`X-API-Key`, `/api/public/v1/docs`), the AI provider card (internal model names, per-call cost table, token counts), Strategy Builder (`PLUS_DI`, `OR_HIGH`, "a deterministic, rule-based parser"), Orders (`TARGET_1`, `STOP_LOSS`, `orb_nifty`, times in UTC-shifted US format), Notifications (`RISK_REJECTION`, "Signal is NO_TRADE", user agents such as `Python-urllib/3.11`), Copilot ("shadow ×1.00 (not applied)", "STALE (291 H)"). | section 8 lists every one | everyone |
| 6 | **Sample / demo data on user pages.** Backtest, Custom scan and Factor Lab open on "Data: Sample - Deterministic sample OHLCV; the backend computes everything for real on it"; the old Signals page generates signals from sample candles; Custom scan builds a "sample option chain". U4 says no sample data. | `ux-audit/backtest-1440.png`, `custom-scan-1440.png`, `factor-lab-1440.png` | trust; wrong decisions |
| 7 | **Telegram alerts need a developer.** The only way is Settings → Alert delivery: a bot token "from @BotFather", a numeric chat id, a "floor" and an "enabled" box; the Setup wizard says "Your organisation's Telegram bot is added in Settings". No guided connect. | `ux-audit/setup-1440.png`, `settings-1440.png` | a Get started step nobody finishes |
| 8 | **Signal text breaks the signal language policy (handover 11l).** Signals, the dashboard and positions show single prices as "Entry 24,790.00 · Stop 24,720.00 · Target 24,930.00". The policy allows only structure words and the area range on the user's own signals. | `frontend/e2e/__screenshots__/signals-v2-390-light.png`, `dashboard-v2-1440-light.png` | compliance |
| 9 | **Too many pages, with overlaps and two names each.** 27 entries in the v2 navigation (5 of them under "More") plus 5 search-only pages: three strategy pages (Library / Build / Test), Positions / Orders / Portfolio, Performance / Journal / Coach, a Notifications page plus the bell plus Settings alerts, an Activity log plus Account. Most old pages carry two titles (top bar "Build a strategy", page header "Strategy Builder"; "Autopilot" / "deployments" / "paper bot"). | `ux-audit/strategy-builder-390.png`, `frontend/e2e/__screenshots__/shell-v2-more-390-light.png` | finding things |
| 10 | **Gaps in the core tasks.** "Analyze an uploaded chart" does not exist in this build (drafts #304 / #305); the broker **renewal banner is missing on the v2 dashboard** (`RenewBanner` is used only by the old `DashboardPage`); Fyers still asks for a "Redirect URL", Angel One and Shoonya for a "TOTP secret"; the phone option chain's summary strip (Spot / Futures / ATM) scrolls sideways; the old dashboard shows all-time "Net P&L", not today's. | `frontend/e2e/__screenshots__/option-chain-v2-390-light.png`; `frontend/e2e/fixtures/broker_catalog.json` | the 8 tasks |

### The order of the fixes

Each step is one PR (or a small group), with before / after pictures and the U1-U12 checklist ticked, under
`ui_v2`, PAPER only.

1. **Sign-in and the guest view** (problem 2): a real sign-in / register / forgot-password screen at `/` for a guest
   (no sidebar, no data tiles), labels tied to fields, after sign-in go to Today (or the Broker page for a new
   account). The login bug from 8d is done (#332).
2. **Friendly states everywhere** (problem 4): one error mapper (a server 404 / 5xx never shows its text: "This could
   not be loaded. Try again." plus a retry), a flag-off page is hidden from the navigation and its URL shows "Coming
   soon" (no flag name, no reference id). Find setups leaves the sidebar while `screener_v2` is off.
3. **Turn `ui_v2` on** for the beta (problem 1), after 1 and 2; then retire the old pages that have a v2 version
   (old Dashboard, Signals generator, old Option chain, Positions, Portfolio, Risk).
4. **Paper bot in three taps** (problem 3): "Start paper bot" opens a short flow (pick a ready strategy → pick a
   symbol → Start in PAPER); the full deploy form moves under Advanced on the Paper bot page; no LIVE switch there.
5. **Telegram in one tap** (problem 7): "Connect Telegram" opens the platform bot with a one-time link code; the user
   presses Start in Telegram, the app shows "Connected ✓" and "Send a test". Bot tokens and chat ids go to Admin.
6. **Broker leftovers** (problem 10, 8f): the renewal banner on the v2 dashboard, 1-2 fields per broker (Redirect URL
   and TOTP secret under the broker's Advanced), logos stay initials.
7. **Admin console + Testing tools** (problems 5, 6; section 6): move sample data, test signals, debug panels, flags,
   logs, instrument master, go-live internals, platform readiness into the console; the per-page Diagnostics drawer
   for SUPER_ADMIN; the server-side 403 tests.
8. **Settings clean-up** (problem 5): Alerts (Telegram / app / e-mail as three plain rows), Appearance, Plan, Account,
   Risk limits, then everything technical (webhook, API keys, SMTP, SMS, AI provider, Vision key) under Advanced.
9. **Merge pages and the new sidebar** (problem 9; section 5): My trades (Positions · Orders · Portfolio), Strategies
   (Library · Build · Test), Performance (Results · Journal · Coach); one name per page.
10. **Signal language formatter + the banned-words CI guard** (problems 8, 5; section 8), then re-run the fresh-eyes
    test (section 9) until all 8 tasks pass.

---

## 2. Method, evidence and limits

- **Inventory** from `frontend/src/routes.ts` (30 routed pages), `frontend/src/shell/pagesV2.ts` (8 v2 layouts),
  `frontend/src/shell/navModel.ts` (sidebar, pinned Broker, the search-only extras, bottom bar, flags, roles),
  `frontend/src/shell/aliases.ts` (merged-page addresses), `frontend/src/App.tsx` (stand-alone windows, `/status`,
  `/legal/:slug`), and the components each page renders.
- **Screenshots**: desktop 1440 x 900 and phone 390 x 844, light theme, viewport only, made-up data from the e2e
  fixtures (`frontend/e2e/fixtures/`, `mockApi.ts`, `dashboardMocks.ts`, `tradingMocks.ts`, `chartsMocks.ts`).
  - Pages that already have a committed baseline are **reused** from `frontend/e2e/__screenshots__/` (Broker,
    Today, Charts, Option chain, Signals, My positions, Portfolio, Risk, Setup steps on the phone, the More sheet,
    the old front page and the go-live checklist; and the old-shell phone pictures from `mobile.e2e.ts`).
  - Every other page was captured by the new capture-only spec `frontend/e2e/ux-audit-capture.e2e.ts` into
    `docs/design/ux-audit/` (32 views x 2 widths = 64 PNGs). It uses `page.screenshot({ path })`, never
    `toHaveScreenshot`, and is skipped in CI, so it never becomes a baseline. Re-run it with
    `UX_AUDIT_CAPTURE=1 npx playwright test --project=phone e2e/ux-audit-capture.e2e.ts` against a built preview (without the variable, and always in CI, it is skipped).
  - The Settings → AI section is **masked** (a magenta block) in the Settings pictures, because it lists the AI
    provider's internal model names; the finding is described in words in P24.
- **Visible text**: the capture spec can also write each page's visible text (`AUDIT_TEXT_DIR`, not committed); the
  banned-words list in section 8 was built from that text, so it lists what a user actually sees.
- **Limits**:
  - The mocks answer the endpoints the existing specs need. Pages whose other reads are not mocked show the app's
    real behaviour for a failed read, which is how "Not found" was found on 15 pages; the same text appears in
    production whenever a read fails or a feature's endpoint is off. Some empty tables (Team members, Contract
    specs, Admin tenants) are empty for the same reason.
  - The flags in the captures are the fresh-install defaults (`DEFAULT_ON_FLAGS`) plus `ui_v2` and
    `discipline_engine`; a page behind any other flag is described from its code.
  - Scores are one auditor's reading against U1-U12. U11 (speed) was not measured on a real network; it is scored
    from the loading design (skeletons, progress, blocking calls) only.
  - Dark theme was not re-captured: the committed baselines cover dark for the v2 pages; for the old pages dark is
    scored from the light pictures plus the shared tokens.

---

## 3. Every route, page, sub-page and modal (the inventory)

41 pages / views, 13 modals and sheets, and 7 old pages that a user still reaches = **61 audited items**.

| ID | Page (v2 label) | Address | Shell / component | Nav today (v2) | Proposed group |
|---|---|---|---|---|---|
| P01 | Sign in / Register / Forgot password | `/account` (signed out), `/settings/account` | old `AccountPage` (always the old shell while signed out) | top-bar icon | USER |
| P02 | Broker | `/broker` | `broker/BrokerPage` | pinned, first | USER |
| P03 | Today (Dashboard) | `/` | v2 `dashboard/DashboardV2`; old `DashboardPage` | Markets | USER |
| P04 | Charts (+ Multi-chart tab) | `/charts`, `/charts/:symbol` | v2 `chart/ChartsPageV2` (v2 only) | Markets | USER |
| P05 | Option chain | `/option-chain` | v2 `optionChain/OptionChainPageV2`; old `OptionChainPage` | Markets | USER |
| P06 | Find setups (Screener) | `/screener` | old `ScreenerPage` | Markets | USER (when its flag is on) |
| P07 | Custom scan (Market Scanner) | `/screener/custom` (old `/scanner`) | old `ScannerPage` | search only | USER, tab of P06 |
| P08 | Plan an options trade (Options Builder) | `/option-chain/plan` (old `/options-builder`) | old `OptionsBuilderPage` | search only (flag `options_builder`) | USER, tab of P05 |
| P09 | Signals | `/signals` | v2 `signals/SignalsV2`; old `SignalsPage` | Trading | USER |
| P10 | My positions | `/positions` | v2 `trading/PositionsV2`; old `PositionsPage` | Trading | USER → My trades |
| P11 | Orders | `/orders` | old `OrdersPage` | Trading | USER → tab of My trades |
| P12 | Portfolio | `/portfolio` | v2 `trading/PortfolioV2`; old `PortfolioPage` | Trading | USER → tab of My trades |
| P13 | Risk limits | `/risk-management` | v2 `trading/RiskV2`; old `RiskManagementPage` | Trading | USER → Settings › Risk limits |
| P14 | Strategy library | `/strategies` | old `StrategiesPage` | Strategies | USER → Strategies tab |
| P15 | Build a strategy | `/strategy-builder` | old `StrategyBuilderPage` | Strategies | USER → Strategies tab |
| P16 | Test a strategy (Backtest) | `/backtest` | old `BacktestPage` | Strategies | USER → Strategies tab |
| P17 | AI Copilot (7 tabs) | `/copilot/:tab` (old `/ai-copilot`) | old `AiCopilotPage` + `copilot/tabs/*` | Strategies (flag `ai_copilot`) | USER |
| P18 | Autopilot (Paper bot) | `/deployments` | old `DeploymentsPage` | Strategies | USER, renamed "Paper bot" |
| P19 | Performance (Analytics) | `/analytics` | old `AnalyticsPage` | Insights | USER |
| P20 | Journal | `/journal` | old `JournalPage` | Insights (flag `discipline_engine`) | USER → tab of Performance |
| P21 | Coach and guide | `/journal/coach` (old `/coach`) | old `CoachGuidePage` | search only | USER → tab of Performance |
| P22 | News & events | `/news-events` | old `NewsEventsPage` | Insights | USER |
| P23 | Company research (Fundamentals) | `/fundamentals` | old `FundamentalsPage` | Insights | hide (Admin decides) until a data feed exists |
| P24 | Settings | `/settings` (+ `#alerts`, `#integrations`, `#ai`) | old `SettingsPage` | Account | USER |
| P25 | Settings → Setup (wizard) | `/settings/setup` | v2 `setup/SetupPageV2` | search only | USER |
| P26 | Settings → Advanced (Go-live checklist) | `/settings#advanced`, `#go-live` | `GoLiveChecklistV2` | inside Settings | USER (summary) + ADMIN (internals) |
| P27 | Settings → Account | `/settings/account` (old `/account`) | old `AccountPage` | search only | USER |
| P28 | Team | `/team` | old `TeamPage` | Account | USER (owners) → Settings › Team |
| P29 | Notifications (Feed · Rules & delivery) | `/notifications` | old `NotificationsPage` | Account | USER → the bell's drawer + Settings › Alerts |
| P30 | Factor Lab | `/quant` | old `QuantPage` | More | Remove from user nav (research tool; Admin › Testing tools or a later Pro feature) |
| P31 | Contract specs (Instruments) | `/instruments` | old `InstrumentsPage` | More | ADMIN › System (instrument master) |
| P32 | Marketplace | `/marketplace` | old `MarketplacePage` | More (flag `marketplace`, ON by default) | Remove from nav (marketplace is the last phase, 11l) |
| P33 | Activity log (System Logs) | `/system-logs` | old `SystemLogsPage` | More | USER → Settings › Account › Security; exports → ADMIN |
| P34 | Admin console | `/admin` | old `AdminPage` | More (role SUPER_ADMIN) | ADMIN (rebuilt, section 6) |
| P35 | Not found | any unknown address | `NotFound` | - | USER |
| P36 | Public status page | `/status` | `StatusPage`, outside the shell (flag `public_status_page`) | - | public |
| P37 | Legal pages | `/legal/:slug` | `LegalPage`, outside the shell (flag `legal_pages`) | - | public |
| P38 | Chart in its own tab | `/?chart=<symbol>` | old `ChartWindow` | - | USER |
| P39 | Multi-chart workspace in its own tab | `/?workspace=1` | old `ChartWorkspace` (flag `chart_workspace`) | - | USER |
| P40 | Contract chart window | `/?contract=...` | old `ContractChartWindow` (flag `chart_contracts`) | - | USER (later) |
| P41 | Futures basis window | `/?basis=...` | old `FuturesBasisWindow` (flag `chart_futures_basis`) | - | USER (later) |
| M01 | Command bar (Ctrl K) | any page | `shell/CommandBarV2` | - | USER |
| M02 | More sheet (phone) | any page | `shell/MoreSheetV2` | bottom bar | USER |
| M03 | Broker sign-in prompt ("Connect your broker") | after sign-in | `broker/BrokerPrompt` | - | USER |
| M04 | Broker connect panel | Broker page | `broker/ConnectPanel` | - | USER |
| M05 | Broker → Advanced (static IP, Upstox redirect URL, broker accounts) | Broker page | `broker/BrokerAdvanced` | - | USER (Advanced) |
| M06 | Renewal banner | data pages | `broker/RenewBanner` (old dashboard only) | - | USER |
| M07 | Feedback dialog | top bar | `feedback/*` | - | USER |
| M08 | Help / support bot | top bar (flag `support_bot`) | `support/*` | - | USER |
| M09 | Two-factor step-up dialog | LIVE approvals | `components/StepUpDialog` | - | USER |
| M10 | AI acknowledgement gate | first Copilot use | `components/AiAcknowledgementGate` | - | USER |
| M11 | Signal detail (drawer / pane), Library drawer | Signals | `signals/*` | - | USER |
| M12 | Phone chart sheet (symbol, timeframe, indicators) | Charts | `chart/*` | - | USER |
| M13 | Page intro ("What is this page?") and the "Verify your email" chip | every page | `ui/PageIntro`, top bar | - | USER **Fixed (user-friendly pass, 23 Oct): the chip is gone from both top bars (one Needs attention row with Resend + Settings › Account); the intro is one short line, collapsible to "About this page", remembered per page** |
| O1-O7 | Old pages a user reaches with `ui_v2` OFF (today's default): old Dashboard, old Signals generator, old Option chain, old Positions, old Portfolio, old Risk management, the old sidebar with 28 items | same addresses | `pages/*Page.tsx` | old sidebar | retire after fix 3 |

---

## 4. Page-by-page audit

Element tags: **[User]** stays on a user page, **[Admin]** moves to the admin console (section 6), **[Remove]** is
deleted. Actions: REMOVE, HIDE (under a collapsed "Advanced" on the same page), MOVE (to the named page), MERGE,
MAKE PROMINENT. Picture paths are relative to `docs/design/`; `bl/` means `../../frontend/e2e/__screenshots__/`.

### P01 Sign in / Register / Forgot password - `/account` while signed out

- Purpose: get into the app (or create an account, or reset a password).
- Pictures: `ux-audit/sign-in-1440.png`, `ux-audit/sign-in-390.png` (with `ui_v2` on it is the same picture: flags
  are not read while signed out, so a guest always gets the old shell, `ux-audit/sign-in-old-shell-*.png`).
- Score: U1 ✗ (a guest at `/` sees a dashboard, not this form) · U2 ✓ (Log in is the strongest button) · U3 ✓ ·
  U4 ✗ (the guest's `/` shows "Backend offline" and "Not found") · U5 ✓ · U6 ✓ · U7 ✗ (wrong password message was
  wrong until #332; no loading state on Log in) · U8 ✗ (old shell and old sidebar around a v2 product) · U9 ✗ (the
  phone bottom bar shows Screener / Chain / Positions to a guest) · U10 ✗ (28-item sidebar for a guest; after sign-in
  the user stays on Account) · U11 ✓ · U12 ✗ (the Email and Password labels are not tied to their fields; a screen
  reader reads "edit text").
- Elements:
  - REMOVE [Remove] the sidebar, bottom bar and data tiles for a guest; the guest gets a full-screen sign-in card.
  - REMOVE [Remove] "Log in to see your book; the engines below are live either way", "Backend offline", "Some
    dashboard data could not be loaded: Not found" on the guest's `/`.
  - MAKE PROMINENT [User] one card: Email, Password, "Log in"; under it "Create an account" and "Forgot password?".
  - MOVE [User] after sign-in → Today (a new account → Broker, as #328 does for the prompt).
  - MOVE [User] "Your permissions" scopes list → Settings › Team (owners only), collapsed.
- Primary action: **Log in**.
- Mock:

```
+--------------------------------------------+
|              [logo] AiAlgoDesk             |
|        Trade on paper first. Free.         |
|  +--------------------------------------+  |
|  | Email     [______________________]   |  |
|  | Password  [______________________] o |  |
|  | [            Log in              ]   |  |
|  | Forgot password?   Create an account |  |
|  +--------------------------------------+  |
|   Terms · Privacy · Risk disclosure        |
+--------------------------------------------+
```

### P02 Broker - `/broker` (+ M03 sign-in prompt, M04 connect panel, M05 Advanced)

- Purpose: connect, renew or remove the broker that feeds prices and paper trades.
- Pictures: `bl/broker-1440-light.png`, `bl/broker-390-light.png`, `bl/broker-prompt-1440-light.png`,
  `ux-audit/broker-connect-panel-1440.png`, `ux-audit/broker-connect-panel-390.png`.
- Score: U1 ✓ · U2 ✓ (Renew / Connect) · U3 ✓ (status dot and "Connected ✓") · U4 ✗ (Fyers "Redirect URL", Angel One
  and Shoonya "TOTP secret" in the main panel) · U5 ✓ · U6 ✓ (Advanced collapsed) · U7 ✓ · U8 ✓ · U9 ✓ · U10 ✓
  (first in the nav, with a dot) · U11 ✓ · U12 ✓.
- Elements:
  - HIDE [User] Fyers "Redirect URL", Angel One / Shoonya "TOTP secret" → the broker's own "Advanced" inside the
    panel, with a one-line "Where do I get this?" link (per 8f: 1-2 fields per broker).
  - MAKE PROMINENT [User] "Analytics Token (1 year)" as Upstox's default (done in #328) and the token end date.
  - MAKE PROMINENT [User] the renewal banner on Today (missing on the v2 dashboard, see P03).
  - KEEP [User] initials as logos (8f).
  - MOVE [Admin] the Upstox redirect URL as stored on the server and the static IP registration details → Admin ›
    Support (a user's broker status); the user keeps only "Copy this address into Upstox" in Advanced.
- Primary action: **Connect** (no broker) / **Renew** (needs login).
- Mock: the current page is close to the target; the only change is the panel:

```
Connect Fyers                                   [x]
  App ID      [____________]
  Secret ID   [____________]   Where do I get this?
  > Advanced (Redirect URL)
  [            Connect            ]
```

### P03 Today (Dashboard) - `/` (v2) and the old Dashboard (O1)

- Purpose: what needs me now, how am I doing today, is the market open.
- Pictures: v2 `bl/dashboard-v2-1440-light.png`, `bl/dashboard-v2-390-light.png`; old shell
  `bl/front-page-1440-light.png`, `bl/front-page-390-light.png`.
- Score (v2): U1 ✓ · U2 ✓ (one blue "Review 2 trades") · U3 ✓ (Today's P&L big, market chip, broker chip) · U4 ✗
  (signal rows carry single prices "Entry · Stop · Target"; "PCR 1.12 · Put writing rising" under each index) · U5 ✓ ·
  U6 ✓ · U7 ✗ (no renewal banner when the broker token ends today; it is only on the old page) · U8 ✓ · U9 ✓ ·
  U10 ✓ · U11 ✓ · U12 ✓.
- Score (old): U1 ✗ (six equal tiles) · U2 ✗ (three equal buttons: Deploy a strategy / Positions / AI Copilot) · U3 ✗
  (all-time "Net P&L", not today's) · U4 ✗ ("Backend online", "Autopilot trading · beat 12s ago", "0 accounts · 3
  adapters", "77.0% at stops") · U5 ✗ · U6 ✗ · U7 ✗ · U8 ✗ · U9 ✗ · U10 ✗ · U11 ✓ · U12 ✓.
- Elements (v2):
  - REMOVE [Remove] single prices in the signal rows → structure words + the area range (11l).
  - HIDE [User] "PCR · Put writing rising" → a tap on the index opens it; the strip shows price and change only.
  - MAKE PROMINENT [User] the broker renewal banner ("Upstox needs today's login · Renew") above Needs attention.
  - KEEP [User] Get started (3 steps, 8e), Needs attention, Open positions, Active signals, Today.
- Elements (old page, until it is retired): REMOVE [Admin] the health chips (backend, worker heartbeat) → Admin ›
  System; REMOVE [Remove] the six tiles and the three hero buttons.
- Primary action: the first "Needs attention" item (Review trades / Renew / Connect broker).
- Mock (phone):

```
Monday, 19 October                Market open · 15:30
[!] Upstox needs today's login              [Renew]
+---------------------------------------------------+
| Today's P&L   +₹4,380        ₹660 booked · ₹3,720 open
+---------------------------------------------------+
NIFTY 50 24,812 +0.48%   BANK NIFTY 53,946 -0.39%  VIX 13.4
Needs attention (2)
  2 trades wait for you            [Review 2 trades]
  Autopilot paused "Mean reversion"          [Review]
Open positions (3)                         View all >
Active signals (3)                         View all >
  NIFTY 50 · Long · Seller area 24,780-24,800 reached
[Home] [Charts] [Signals] [My trades] [More]
```

### P04 Charts - `/charts`, `/charts/:symbol` (+ M12 phone sheet, Multi-chart tab)

- Purpose: look at a symbol's price on any timeframe.
- Pictures: `bl/charts-v2-1440-light.png`, `bl/charts-v2-390-light.png`, `bl/charts-v2-390-sheet-light.png`,
  `bl/charts-v2-multi-1440-light.png`, `bl/charts-v2-gate-1440-light.png`.
- Score: U1 ✓ · U2 ✓ (the symbol box) · U3 ✓ (live price tag, "Market open") · U4 ✗ (the broker gate says "You will
  need the keys from your broker's developer console") · U5 ✓ · U6 ✓ · U7 ✓ · U8 ✓ · U9 ✓ (bottom sheet) · U10 ✓ ·
  U11 ✓ · U12 ✗ (drawing tools are icon-only; check each has an aria-label and a tooltip).
- Elements:
  - REMOVE [Remove] "developer console" from the gate → "Connect your broker (2 minutes)".
  - KEEP [User] the timeframe row 1m..M, chart type, Indicators, Multi-chart tab (flag `chart_workspace`).
  - HIDE [User] the volume source label "Volume · NIFTY OCT FUT" → tooltip.
- Primary action: **pick a symbol** (the search box).
- Mock: no layout change; the gate card text only.

### P05 Option chain - `/option-chain` (v2) and old Option chain (O3)

- Purpose: read calls and puts around the money for the nearest expiry.
- Pictures: v2 `bl/option-chain-v2-1440-light.png`, `bl/option-chain-v2-390-light.png`; old
  `bl/option-chain-390-light.png`.
- Score (v2): U1 ✓ · U2 ✓ (automatic symbol and expiry) · U3 ✓ (ATM row highlighted) · U4 ✓ (no sample data) · U5 ✓
  (lakhs) · U6 ✓ (Columns) · U7 ✓ · U8 ✓ · U9 ✗ (the Spot / Futures / ATM / PCR / Max pain strip scrolls sideways on
  a phone) · U10 ✓ · U11 ✓ · U12 ✓.
- Score (old): U4 ✗ (sample chain, "bias that never relies on PCR alone", OI settings for the worker) · U2 ✗ ("Show
  chain" button for what should be automatic) · U9 ✗.
- Elements:
  - MERGE [User] Plan an options trade (P08) → a tab "Plan a trade" on this page (already its address).
  - REMOVE [Remove] the 200-name symbol list as a plain select → a search box with NIFTY / BANKNIFTY / FINNIFTY /
    SENSEX as chips.
  - CHANGE [User] the summary strip → two rows of two on a phone (Spot, ATM / PCR, Max pain), no sideways scroll.
  - MOVE [Admin] the old page's "OI banner settings" thresholds → Admin › Controls (defaults) and Settings ›
    Advanced (per user).
- Primary action: none needed (opens on the nearest expiry); secondary: change symbol.

### P06 Find setups (Screener) - `/screener`

- Purpose: find symbols that match a setup right now.
- Pictures: `ux-audit/screener-1440.png`, `ux-audit/screener-390.png`; old `bl/screener-390-light.png`.
- Score: U1 ✗ (with `screener_v2` off the page is one sentence: "The screener is not turned on for this organisation
  yet.") · U2 ✗ · U3 ✗ · U4 ✗ (flag talk) · U5 ✓ · U6 ✓ · U7 ✗ (flag-off is not designed) · U8 ✓ · U9 ✓ · U10 ✗ (a
  main-nav entry that leads nowhere) · U11 ✓ · U12 ✓.
- Elements:
  - REMOVE [Remove] the nav entry while `screener_v2` is off; a direct URL shows "Coming soon" (8b).
  - MERGE [User] Custom scan (P07) → a tab "Build your own".
  - MAKE PROMINENT [User] the gallery of ready screens (flag `screener_gallery`) as the first view, each with "Run".
  - MOVE [Admin] the venue matrix, vectorised / history / fitness options → Admin › Controls (flags) and the screen's
    own Advanced.
- Primary action: **Run** (on a ready screen).
- Mock:

```
Find setups                         [Ready screens] [Build your own]
Search screens [____________]   Universe: NIFTY 50 v
+--------------------+ +--------------------+ +--------------------+
| Trend pullback     | | Range breakout     | | RSI divergence     |
| 12 matches today   | | 4 matches today    | | 2 matches today    |
| [Run]  Alert me    | | [Run]  Alert me    | | [Run]  Alert me    |
+--------------------+ +--------------------+ +--------------------+
```

### P07 Custom scan (Market Scanner) - `/screener/custom`

- Purpose: build a scan from conditions.
- Pictures: `ux-audit/custom-scan-1440.png`, `ux-audit/custom-scan-390.png`; old `bl/scanner-390-light.png`.
- Score: U1 ✗ (two paragraphs before any control) · U2 ✗ ("Run Scanner" is at the very bottom) · U3 ✗ · U4 ✗ ("Data:
  Sample / Broker candles", "Deterministic sample OHLCV; the backend computes everything for real on it", "a sample
  option chain is generated per symbol", "deterministic parser") · U5 ✗ ("Symbols (comma-separated)") · U6 ✗ (every
  filter group open) · U7 ✗ · U8 ✗ (old components) · U9 ✗ · U10 ✗ (header "Market Scanner", nav "Custom scan") ·
  U11 ✓ · U12 ✓.
- Elements:
  - REMOVE [Admin] the Data switch and every sample path → Admin › Testing tools (the scan always runs on broker /
    stored data; without a broker the BrokerGate card).
  - MAKE PROMINENT [User] "Describe the scan" box (AI or the plain-language parser) as the first control.
  - HIDE [User] indicator, structure and option-chain filter groups → collapsed "Conditions".
  - CHANGE [User] "Symbols (comma-separated)" → a universe picker (NIFTY 50, F&O, my watchlist).
  - REMOVE [Remove] the risk disclaimer paragraph at the top → one line under the results.
- Primary action: **Run scan**.

### P08 Plan an options trade (Options Builder) - `/option-chain/plan`

- Purpose: see the payoff of an options strategy before trading it.
- Pictures: `ux-audit/options-builder-1440.png`, `ux-audit/options-builder-390.png`.
- Score: U1 ✓ · U2 ✗ (no primary; "Place as basket" and "Save strategy" equal) · U3 ✗ · U4 ✗ (manual Spot, Strike
  step, Lot size, Base IV, Near expiry the system can fill; "from the instrument master"; "Saving strategies is not
  turned on for your organisation yet") · U5 ✗ ("Template width", "IV shift") · U6 ✗ · U7 ✗ ("Not found") · U8 ✗ ·
  U9 ✗ · U10 ✗ (two names) · U11 ✓ · U12 ✓.
- Elements:
  - REMOVE [Remove] the manual market inputs → filled from the chain the user came from (symbol, spot, lot, expiry).
  - MAKE PROMINENT [User] the template row (Bull call spread, Iron condor, ...) and the payoff chart.
  - HIDE [User] Base IV, IV shift, Brokerage per order, Template width → Advanced.
  - REMOVE [Remove] "Saving strategies is not turned on..." (flag `options_builder_saved` off → no Save button).
  - REMOVE [Remove] "Adjustments and compare · Coming later".
- Primary action: **Pick a template** → then "Send to paper ticket".

### P09 Signals - `/signals` (v2) and the old Signals generator (O2)

- Purpose: decide on the trades my bots, scans and Copilot suggest.
- Pictures: v2 `bl/signals-v2-1440-light.png`, `bl/signals-v2-390-light.png`; old `bl/signals-390-light.png`.
- Score (v2): U1 ✓ · U2 ✓ (Approve) · U3 ✓ (Waiting count, minutes left) · U4 ✗ (Entry / Stop / Target single prices;
  "Deployment #4") · U5 ✓ · U6 ✓ ("Why this trade") · U7 ✓ · U8 ✓ · U9 ✓ · U10 ✓ · U11 ✓ · U12 ✓.
- Score (old): U1 ✗ ("Generate a signal from any inbuilt strategy") · U4 ✗ (Sample data box, "Deterministic sample
  OHLCV", "Bars and seed") · U7 ✗ ("Not found" toast) · 8c ✗ (`NIFTY` is pre-filled).
- Elements:
  - CONFIRM [User] 8c: v2 has **no pre-applied filter** (`symbol` starts as null in `SignalsV2.tsx`); the "NIFTY 50"
    owners see is the old page's pre-filled symbol and the v2 placeholder "Symbol, e.g. NIFTY 50", which looks like a
    value. Change the placeholder to "Any symbol".
  - REMOVE [Remove] single prices → the structure sentence + "Seller area 24,780-24,800" (11l). The trade numbers the
    approval needs (size, most you can lose) stay in the confirm step, not in the signal text.
  - REMOVE [Remove] "Deployment #4" → the bot's name.
  - REMOVE [Admin] the old generator page (sample candles, strategy picker) → Admin › Testing tools ("Make a test
    signal").
- Primary action: **Approve** (on the first waiting signal).

### P10 My positions - `/positions` (v2) and old Positions (O4)

- Purpose: what I hold now and how it is doing; exit if needed.
- Pictures: `bl/positions-v2-1440-light.png`, `bl/positions-v2-390-light.png`; old `bl/positions-390-light.png`.
- Score: U1 ✓ · U2 ✓ (Exit per row) · U3 ✓ (Open P&L, Booked today, Risk at stops) · U4 ✗ ("Contract notes (actual
  charges)" paragraph about broker order ids and column names; "Premium floor 100.00"; note text "early" shows raw
  joined) · U5 ✓ · U6 ✗ (contract-note import open on the page) · U7 ✓ · U8 ✓ · U9 ✓ · U10 ✓ · U11 ✓ · U12 ✓.
- Elements:
  - MERGE [User] Orders (P11) and Portfolio (P12) → tabs "Open · Closed · Orders · Money" of **My trades**.
  - HIDE [User] contract-note upload → Closed tab › "Use my broker's charges" (one button, one sentence).
  - HIDE [User] Option Greeks → the option row's details.
  - REMOVE [Remove] single "Stop / Target" prices in the row subtitle → the row's details (this is the user's own
    position, a factual record, so it may show numbers; keep them one tap away).
- Primary action: **Exit** on a row; page-level none.

### P11 Orders - `/orders`

- Purpose: every fill, newest first.
- Pictures: `ux-audit/orders-1440.png`, `ux-audit/orders-390.png`; old `bl/orders-390-light.png`.
- Score: U1 ✓ · U2 ✓ (none needed) · U3 ✗ (a flat table) · U4 ✗ (`TARGET_1`, `STOP_LOSS`, `orb_nifty`, `demo_swing`;
  "a broker-style order blotter view") · U5 ✗ (US date format "10/19/2026, 5:10:00 AM" in UTC, not IST; prices
  without ₹ or separators) · U6 ✓ · U7 ✓ · U8 ✗ (old table) · U9 ✗ (8-column table on a phone) · U10 ✓ · U11 ✓ ·
  U12 ✓.
- Elements: MERGE [User] → My trades › Orders; CHANGE [User] reasons to words ("Target 1 hit", "Stop hit"), strategy
  ids to names, times to IST "19 Oct, 10:40"; cards on a phone.
- Primary action: none (a record).

### P12 Portfolio - `/portfolio`

- Purpose: where my money is.
- Pictures: `bl/portfolio-v2-1440-light.png`, `bl/portfolio-v2-390-light.png`.
- Score: all ✓ except U10 ✗ (overlaps My positions; a separate nav item for one card of numbers).
- Elements: MERGE [User] → My trades › Money (the four tiles, "Where your money is", "Booked P&L over time").
- Primary action: none.

### P13 Risk limits - `/risk-management` (v2) and old Risk management (O6)

- Purpose: how much I may lose today and the limits every entry is checked against.
- Pictures: `bl/risk-v2-1440-light.png`, `bl/risk-v2-390-light.png`.
- Score: U1 ✓ · U2 ✓ (Save limits) · U3 ✓ (daily limit, risk per trade, limit in use) · U4 ✗ ("Lot size" typed by
  hand although the instrument master knows it; "Guardian rules", "risk guardian") · U5 ✓ · U6 ✗ (all 13 inputs and
  the market-events editor open) · U7 ✓ · U8 ✓ · U9 ✓ · U10 ✗ (a main-nav item for a set-once page) · U11 ✓ · U12 ✓.
- Elements:
  - MOVE [User] → Settings › Risk limits; Today keeps "Risk in use 39%" with a link.
  - REMOVE [Remove] "Lot size" input (from the instrument master).
  - HIDE [User] Guardian rules and Market events → Advanced (rename "Guardian rules" to "Extra safety rules").
- Primary action: **Save limits**.

### P14 Strategy library - `/strategies`

- Purpose: browse the ready strategies.
- Pictures: `ux-audit/strategies-1440.png`, `ux-audit/strategies-390.png`.
- Score: U1 ✗ ("Every inbuilt auto-executable scalping strategy, straight from the registry.") · U2 ✗ (no action on
  a card) · U3 ✗ · U4 ✗ ("registry", "auto-executable") · U5 ✗ · U6 ✓ · U7 ✗ (a failed read shows "Not found") ·
  U8 ✗ · U9 ✓ · U10 ✗ (header "Strategy Library", nav "Library") · U11 ✓ · U12 ✓.
- Elements: MERGE [User] → Strategies › Library; each card: name, one sentence "how it works", timeframe, **"Use in
  paper bot"**; REMOVE [Remove] "registry"; the Idea Library (11k) lands here later.
- Primary action: **Use in paper bot**.

### P15 Build a strategy - `/strategy-builder`

- Purpose: make my own entry rules without code.
- Pictures: `ux-audit/strategy-builder-1440.png`, `ux-audit/strategy-builder-390.png`.
- Score: U1 ✗ (three paragraphs before the form) · U2 ✗ (Parse and Save strategy compete) · U3 ✗ · U4 ✗ ("A
  deterministic, rule-based parser - not a call to an external AI (no AI-provider credentials are configured)";
  raw indicator ids `PLUS_DI`, `MINUS_DI`, `OR_HIGH`, `PDH`, `VOLUME_SMA`; "Target R:R (1 / 2)") · U5 ✗ · U6 ✗ · U7 ✗
  ("Not found") · U8 ✗ · U9 ✗ · U10 ✗ (two names) · U11 ✓ · U12 ✓.
- Elements:
  - MERGE [User] → Strategies › Build.
  - MAKE PROMINENT [User] "Describe your idea" (one box, plain words) → the rules appear as sentences.
  - CHANGE [User] indicator ids → names ("+DI", "Opening range high", "Previous day high").
  - HIDE [User] the AND-condition grid → "Edit the rules" (Advanced).
  - REMOVE [Remove] the parser / credentials paragraph; the disclaimer → one line by Save.
- Primary action: **Save and test** (goes straight to P16 with this strategy).

### P16 Test a strategy (Backtest) - `/backtest`

- Purpose: see how a strategy would have done on past data.
- Pictures: `ux-audit/backtest-1440.png`, `ux-audit/backtest-390.png`.
- Score: U1 ✗ ("Event-driven simulation with position sizing...") · U2 ✓ ("Run Backtest") · U3 ✗ · U4 ✗ ("Data:
  Sample / Broker candles", "Deterministic sample OHLCV", "Sample bars", "lookback") · U5 ✗ · U6 ✗ (dynamic exits and
  "Trade as" open) · U7 ✗ ("Not found") · U8 ✗ · U9 ✗ · U10 ✗ (header "Backtesting", nav "Test a strategy") · U11 ✗
  (no progress for a long run) · U12 ✓.
- Elements:
  - REMOVE [Admin] the Sample data path → Admin › Testing tools. Without a broker: stored daily / EOD data, said
    plainly ("Tested on end-of-day data").
  - KEEP [User] Strategy, Symbol, Period (last 3 months / 1 year), **Run test**.
  - HIDE [User] dynamic exits, "Trade as options", the optimiser → Advanced.
  - MAKE PROMINENT [User] the result in words ("Won 58 of 100 trades; after costs +₹12,400; worst drop ₹3,100").
- Primary action: **Run test**.

### P17 AI Copilot - `/copilot/:tab` (7 tabs; M10 acknowledgement gate)

- Purpose: ask the AI about the market and my trades; get rules I can read.
- Pictures: `ux-audit/copilot-1440.png`, `ux-audit/copilot-390.png`; old `bl/copilot-390-light.png`.
- Score: U1 ✗ (7 tabs: Market Pulse, Strategy Lab, Idea Builder, Ask Copilot, Watchtower, News Radar, Coach &
  Scorecard) · U2 ✗ (no main action on the first tab) · U3 ✗ (the 3D globe and two gauges are the biggest things) ·
  U4 ✗ ("STALE (36 H OLD)", "Market thesis (shadow)", "Shadow ×1.00 (not applied)", "Structure ↑ ×1.50", "coverage
  59%", "Worker not seen in the last minutes - deployments will not trade (check the worker container)", "log in
  under Settings > Brokers" (an old address), the market memory table) · U5 ✗ · U6 ✗ · U7 ✗ (stale data shown at
  length instead of one line) · U8 ✗ · U9 ✗ · U10 ✗ (Watchtower duplicates Signals; Coach duplicates P21) · U11 ✗
  (the 3D scene) · U12 ✗ (gauges carry meaning by needle position only).
- Elements:
  - MAKE PROMINENT [User] **Ask Copilot** as the first and default tab (a chat box with 3 example questions).
  - MERGE [User] Strategy Lab + Idea Builder → one "Turn an idea into a strategy" (11e) in Strategies › Build.
  - MERGE [User] Watchtower → Signals (it already links there); News Radar → News & events; Coach & Scorecard →
    Performance › Coach.
  - MOVE [Admin] Pre-flight checklist (worker container, broker session) → Admin › System and the per-page
    Diagnostics drawer; the user keeps one chip ("Broker: log in" → Broker page).
  - MOVE [Admin] Market thesis shadow multipliers, "coverage", the market memory table, regime filter internals →
    Diagnostics drawer.
  - REMOVE [Remove] the 3D scene on phones; the gauges become a sentence ("Market today: trending up").
- Primary action: **Ask**.
- Mock:

```
AI Copilot                         Explains · never trades on its own
Market today: trending up · data from 10:45 (live)
+-----------------------------------------------------------+
| Ask anything about the market or your trades              |
| [ Why did my NIFTY bot stop today?                    ] > |
| Try: "How did I do this week?"  "Explain PCR"             |
+-----------------------------------------------------------+
Tabs: [Ask] [Market today] [News]
```

### P18 Autopilot (rename: Paper bot) - `/deployments`

- Purpose: run a strategy automatically on paper and watch it.
- Pictures: `ux-audit/autopilot-1440.png`, `ux-audit/autopilot-390.png`.
- Score: U1 ✗ (a long technical paragraph) · U2 ✗ ("Deploy paper" at the bottom of ~40 controls) · U3 ✗ ("Trading
  worker …" tiles first) · U4 ✗ ("Strategy deployments the background worker trades on its own", "VALID / session
  not yet verified - authenticate or log in to enable LIVE", "the contract is picked from the instrument master", OI
  gates, regime filter, 13 option structures, "Base candles") · U5 ✗ · U6 ✗ · U7 ✗ ("Not found" twice) · U8 ✗ · U9 ✗
  · U10 ✗ (three names: Autopilot / deployments / paper bot) · U11 ✓ · U12 ✓.
- Elements:
  - MAKE PROMINENT [User] **Start a paper bot**: 1 pick a strategy (from Library, with one sentence each), 2 pick a
    symbol (NIFTY 50 default), 3 Start. Lots = 1, PAPER only, the user's risk limits apply.
  - MAKE PROMINENT [User] "Your bots": each a card (name, symbol, today's P&L, Running / Paused, Pause / Stop).
  - HIDE [User] Trade as option / future, structures, strike filters, dynamic exits, regime filter, OI gates →
    Advanced ("More ways to trade").
  - REMOVE [Remove] the LIVE / PAPER switch from this page (LIVE stays behind the go-live gates in Settings ›
    Advanced).
  - MOVE [Admin] Trading worker / Market tiles, broker token "VALID" row → Admin › System + Diagnostics drawer; the
    user gets the BrokerGate card when needed.
- Primary action: **Start paper bot**.
- Mock:

```
Paper bot                                       [Start a paper bot]
Your bots (2)
+--------------------------------------------------------------+
| ORB NIFTY · NIFTY 50       Running    Today +₹660   [Pause]  |
| Mean reversion · BANKNIFTY Paused: 3 losses in a row [Resume]|
+--------------------------------------------------------------+
Start a paper bot
 1 Strategy  [Opening-range breakout v]  "Buys the break of the first 15 min"
 2 Symbol    [NIFTY 50 v]
 [          Start on paper          ]
 > More ways to trade (options, futures, exits, filters)
```

### P19 Performance (Analytics) - `/analytics`

- Purpose: how am I doing over time.
- Pictures: `ux-audit/analytics-1440.png`, `ux-audit/analytics-390.png`.
- Score: U1 ✗ ("Aggregated from your full persisted trade history (Positions/Trade Journal)") · U2 ✓ · U3 ✗ (net P&L
  without ₹: "18250.00") · U4 ✗ ("persisted") · U5 ✗ (no ₹, no separators) · U6 ✗ (the tax report above the results)
  · U7 ✗ ("Not found"; "No closed trades yet" next to 41 closed trades) · U8 ✗ · U9 ✓ · U10 ✗ (header "Analytics",
  nav "Performance") · U11 ✓ · U12 ✓.
- Elements: MERGE [User] Journal (P20) and Coach (P21) as tabs; MAKE PROMINENT [User] Net P&L, Win rate, Worst drop,
  the equity curve; HIDE [User] the tax report → "Download tax report" button; Paper / Live as a switch at the top.
- Primary action: none (read); secondary: Download tax report.

### P20 Journal - `/journal` (flag `discipline_engine`)

- Purpose: my trades with notes and a weekly discipline score.
- Pictures: `ux-audit/discipline-1440.png`, `ux-audit/discipline-390.png`.
- Score: U1 ✗ · U2 ✗ · U3 ✗ · U4 ✗ ("2026-W43" ISO week label) · U5 ✗ · U6 ✓ · U7 ✗ ("Not found" as the whole page) ·
  U8 ✗ · U9 ✓ · U10 ✗ (two names) · U11 ✓ · U12 ✓.
- Elements: MERGE [User] → Performance › Journal; "This week (19-25 Oct)" instead of "2026-W43"; the empty state "No
  trades this week. Your trades are journaled here automatically."
- Primary action: **Add a note** (on a trade).

### P21 Coach and guide - `/journal/coach`

- Purpose: patterns in my own trades and plain explanations.
- Pictures: `ux-audit/coach-1440.png`, `ux-audit/coach-390.png`.
- Score: U1 ✓ · U2 ✗ · U3 ✓ (grade A) · U4 ✗ ("-0.06R", `ema_rsi_scalper_1m`, "Expectancy per trade") · U5 ✗ · U6 ✗ ·
  U7 ✓ · U8 ✗ · U9 ✓ · U10 ✗ (also inside the Copilot) · U11 ✓ · U12 ✓.
- Elements: MERGE [User] → Performance › Coach; strategy ids → names; R values → Advanced.
- Primary action: none (read).

### P22 News & events - `/news-events`

- Purpose: what is happening in the market today.
- Pictures: `ux-audit/news-events-1440.png`, `ux-audit/news-events-390.png`.
- Score: U1 ✗ · U2 ✗ (the "Add a cited event" form is the biggest block) · U3 ✗ · U4 ✗ ("News trust: 1.00 (0 verdicts,
  null)", "LIVE FEED SOURCES on · fetch every 15 min", "Origin: Cited by a person / Unverified feed") · U5 ✗ · U6 ✗ ·
  U7 ✗ ("Not found") · U8 ✗ · U9 ✗ · U10 ✓ · U11 ✓ · U12 ✓.
- Elements: MAKE PROMINENT [User] today's events and headlines as a list; MOVE [Admin] "Add a cited event", feed
  sources, trust score → Admin › Controls (content); REMOVE [Remove] "null".
- Primary action: none (read); a headline opens its source.

### P23 Company research (Fundamentals) - `/fundamentals`

- Purpose: company quality and valuation.
- Pictures: `ux-audit/fundamentals-1440.png`, `ux-audit/fundamentals-390.png`.
- Score: U1 ✗ · U2 ✗ · U3 ✗ · U4 ✗ ("Every input is entered and cited - nothing here is fetched from a live feed (no
  SEBI/NSE/BSE credentials are wired in yet)"; the user must type the financials) · U5 ✗ · U6 ✗ · U7 ✗ ("Not found")
  · U8 ✗ · U9 ✗ · U10 ✗ · U11 ✓ · U12 ✓.
- Elements: REMOVE [Remove] from the user nav until a data source fills it; MOVE [Admin] company data entry → Admin ›
  Testing tools / content.
- Primary action: (later) **Search a company**.

### P24 Settings - `/settings`

- Purpose: set up how the app looks, alerts reach me, my plan and my account.
- Pictures: `ux-audit/settings-1440.png`, `ux-audit/settings-390.png` (AI section masked; see the text).
- Score: U1 ✗ (a 1,000-word page) · U2 ✗ (a Save per channel, many equal buttons) · U3 ✗ · U4 ✗ (Alert delivery:
  "CRITICAL alerts", "floor INFO / WARNING / CRITICAL", HMAC-SHA256 signature recipe, `X-ATP-Signature`, SMTP
  STARTTLS, SMS gateway headers and body template, "DLT"; Integrations: TradingView JSON body with `stop_loss`,
  `target1`, Public API keys `X-API-Key`, `/api/public/v1/docs`; AI provider: provider and model ids, a per-call cost
  table, token counts, "rule_based · nlu-parser-v1", the DPDP consent box; Vision: "(feature vision_byok)") · U5 ✗ ·
  U6 ✗ (everything open) · U7 ✗ ("Not found" under Plan & billing, the webhook and API keys) · U8 ✗ · U9 ✗ · U10 ✓ ·
  U11 ✓ · U12 ✓.
- Elements:
  - KEEP [User] Appearance (Text size, Theme, Colour-blind P&L, Reduce motion), Broker link, Plan & billing (without
    "Not found").
  - CHANGE [User] Alerts → three plain rows: App notifications (Turn on), Telegram (Connect, see P29 / fix 5),
    E-mail (Turn on), each with "Send a test"; what to send as checkboxes in words.
  - MOVE [User] Risk limits (P13), Account (P27), Team (P28) in as sub-pages: Settings › Risk limits / Account /
    Team.
  - HIDE [User] Webhook, SMS gateway, SMTP server, TradingView webhook, Public API keys, AI provider key and the
    DPDP consent, Vision key → Settings › Advanced ("For developers and power users").
  - MOVE [Admin] the model / tier / cost table and token counts → Admin › Users & business (AI cost per user and
    feature); the user sees "AI: on (your key)" / "AI: built-in".
  - REMOVE [Remove] "Prepare for LIVE" from the page header (it is in Advanced, 8e).
- Primary action: none at page level; each row has one button.
- Mock:

```
Settings
  Setup  ·  4 of 4 done                                      >
  Appearance      Text size: Normal · Theme: Device          >
  Broker          Upstox · Connected                         >
  Alerts          App: on · Telegram: Connect · E-mail: off  >
  Risk limits     ₹15,000 a day · 1% a trade                 >
  Plan            Free trial · 12 days left                  >
  Account         demo@example.com · Two-factor: off         >
  Team            3 members                                  >
  > Advanced      Webhooks, API keys, AI key, Go-live checklist
```

### P25 Settings → Setup (wizard) - `/settings/setup`

- Purpose: four short steps to start on paper.
- Pictures: `ux-audit/setup-1440.png`, `ux-audit/setup-390.png`, `bl/setup-v2-step1-390-light.png` ...
  `step4`.
- Score: U1 ✓ · U2 ✓ · U3 ✓ · U4 ✗ ("Your organisation's Telegram bot is added in Settings, under Alert delivery") ·
  U5 ✓ · U6 ✓ · U7 ✓ · U8 ✓ · U9 ✓ · U10 ✗ (a fourth "Prepare for LIVE" link in a paper wizard; Setup and Get started
  overlap) · U11 ✓ · U12 ✓.
- Elements: MERGE [User] Get started (Today) and Setup into one list of the same 3-4 steps; CHANGE [User] the
  Telegram step → "Connect Telegram" (one tap); REMOVE [Remove] "Prepare for LIVE" from the wizard.
- Primary action: the step's button (Connect / Start / Connect Telegram).

### P26 Settings → Advanced (Go-live checklist) - `/settings#advanced`

- Purpose: what is still missing before LIVE.
- Pictures: `ux-audit/settings-advanced-1440.png`, `ux-audit/settings-advanced-390.png`, `bl/go-live-1440-light.png`,
  `bl/go-live-390-light.png`.
- Score: U1 ✓ · U2 ✓ · U3 ✓ · U4 ✗ (the checklist mixes operator items, e.g. worker and readiness checks, with user
  items) · U5 ✓ · U6 ✓ (collapsed, 8e done in #329) · U7 ✗ ("We could not load the checklist. Try again in a moment."
  without a retry button) · U8 ✓ · U9 ✓ · U10 ✓ · U11 ✓ · U12 ✓.
- Elements: KEEP [User] the user's own items (two-factor, e-mail verified, broker, paper record, risk limits); MOVE
  [Admin] the platform items (worker, readiness, encryption, holidays) → Admin › System "Go-live internals"; ADD
  [User] a Try again button.
- Primary action: the first unmet item's button.

### P27 Settings → Account - `/settings/account`

- Purpose: my login, password, two-factor and sessions.
- Pictures: `ux-audit/settings-account-1440.png`, `ux-audit/settings-account-390.png`.
- Score: U1 ✓ · U2 ✗ ("Log out" is the first button) · U3 ✗ · U4 ✗ ("Your permissions: ai:manage, alerts:write,
  ...", "Anonymous use of Signals and Backtesting still works without logging in") · U5 ✗ · U6 ✗ · U7 ✓ · U8 ✗ · U9 ✓
  · U10 ✗ (header-less page: no title, only "D Signed in as") · U11 ✓ · U12 ✓.
- Elements: REMOVE [Remove] the scope chips (→ Team, owners, collapsed) and the anonymous-use sentence; MAKE
  PROMINENT [User] Two-factor (Turn on) and Change password; MERGE [User] Activity log's login history (P33) → "Recent
  sign-ins".
- Primary action: **Turn on two-factor** (when off).

### P28 Team - `/team`

- Purpose: invite teammates and set their roles.
- Pictures: `ux-audit/team-1440.png`, `ux-audit/team-390.png`.
- Score: U1 ✓ · U2 ✓ (Create invite link) · U3 ✗ ("ORGANISATION …" tile) · U4 ✗ ("Not found") · U5 ✓ · U6 ✓ · U7 ✗ ·
  U8 ✗ · U9 ✗ (5-column members table) · U10 ✗ (a main-nav item for owners only) · U11 ✓ · U12 ✓.
- Elements: MOVE [User] → Settings › Team (owners); the member list as cards on a phone.
- Primary action: **Invite**.

### P29 Notifications - `/notifications` (Feed · Rules & delivery)

- Purpose: what happened (fills, risk stops, logins) and alert rules.
- Pictures: `ux-audit/notifications-1440.png`, `ux-audit/notifications-390.png`; old `bl/alerts-390-light.png`.
- Score: U1 ✓ · U2 ✓ (Mark all read) · U3 ✗ (every item is "WARNING") · U4 ✗ (`RISK_REJECTION`, `SECURITY`, "Signal
  is NO_TRADE", IP addresses and raw user agents "Python-urllib/3.11", "curl/8.5.0") · U5 ✗ (US dates) · U6 ✗ · U7 ✓ ·
  U8 ✗ (duplicates the bell and Settings › Alerts) · U9 ✓ · U10 ✗ · U11 ✓ · U12 ✓.
- Elements: MERGE [User] the Feed → the bell's drawer (a full page only as "See all"); MERGE [User] Rules & delivery →
  Settings › Alerts; CHANGE [User] type codes → words and icons ("New sign-in on Chrome, Linux"); MOVE [Admin] IPs and
  raw user agents → Admin › Support.
- Primary action: open an item.

### P30 Factor Lab - `/quant`

- Purpose: rank a list of symbols by factors.
- Pictures: `ux-audit/factor-lab-1440.png`, `ux-audit/factor-lab-390.png`.
- Score: U1 ✗ ("Cross-sectional factor scores and a descriptive risk model") · U2 ✗ · U3 ✗ · U4 ✗ (Sample data,
  "Deterministic sample OHLCV", "Benchmark (for betas)", weights "renormalised") · U5 ✗ · U6 ✗ · U7 ✗ · U8 ✗ · U9 ✗ ·
  U10 ✗ · U11 ✓ · U12 ✓.
- Elements: REMOVE [Remove] from the user nav; the useful part becomes the Rankings screen in Find setups (flag
  `screener_rankings`); the sample path → [Admin] Testing tools.

### P31 Contract specs (Instruments) - `/instruments`

- Purpose: lot sizes and tick sizes of MCX and crypto contracts.
- Pictures: `ux-audit/contract-specs-1440.png`, `ux-audit/contract-specs-390.png`.
- Score: all ✗ except U9 / U11 / U12: an operator reference ("static reference contract specifications", "Not found",
  "None registered").
- Elements: MOVE [Admin] → Admin › System › Instrument master.

### P32 Marketplace - `/marketplace` (flag `marketplace`, ON by default)

- Purpose: strategies other users published.
- Pictures: `ux-audit/marketplace-1440.png`, `ux-audit/marketplace-390.png`.
- Score: U1 ✓ · U2 ✗ · U3 ✗ · U4 ✗ ("Not found", "operator reviews every listing", "freezes one version") · U7 ✗ ("Nothing
  published yet") · U10 ✗ (in "More" although the owner set the marketplace as the last phase, 11l).
- Elements: REMOVE [Remove] from the navigation now: turn the flag `marketplace` OFF for every tenant (it is one of
  the 7 flags ON by default); the BillingCard reference too.

### P33 Activity log (System Logs) - `/system-logs`

- Purpose: my security events and record exports.
- Pictures: `ux-audit/activity-log-1440.png`, `ux-audit/activity-log-390.png`.
- Score: U1 ✗ (header "System Logs", nav "Activity log") · U4 ✗ ("hash-chained; includes prev_hash/hash and the chain
  verdict", SHA-256, UTC, "SEBI algo tag") · U6 ✗ · U7 ✗ ("Not found") · U10 ✗.
- Elements: MERGE [User] login history → Settings › Account › Recent sign-ins; MOVE [Admin] the compliance exports
  (audit trail, orders with algo tag, login attempts) → Admin › Support / System; a user keeps "Download my trades
  (CSV)" in Performance.

### P34 Admin console - `/admin` (SUPER_ADMIN)

- Purpose: run the platform.
- Pictures: `ux-audit/admin-1440.png`, `ux-audit/admin-390.png`; a non-admin at `/admin`:
  `ux-audit/admin-as-user-1440.png` ("Admin Console · Platform administrators only." - should be the plain Not found
  card, so the page's existence is not advertised).
- Score (for an admin): U1 ✗ (one long page, 12 blocks) · U2 ✗ · U3 ✗ · U6 ✗ · U8 ✗ · U9 ✗. It is the right home for the
  operator items listed in this audit; section 6 gives its structure.

### P35 Not found

- Pictures: `ux-audit/not-found-1440.png`, `ux-audit/not-found-390.png`.
- Score: all ✓ ("This page does not exist. The link may be old or mistyped. Go to the dashboard"). Use the same card
  for a flag-off page, worded "Coming soon" (8b).

### P36 Public status page - `/status`; P37 Legal pages - `/legal/:slug`

- Not captured (outside the shell, behind `public_status_page` / `legal_pages`, both OFF). From the code: the status
  page lists API, worker, data feed, alert delivery and incidents; fine for the public. Legal pages: blocked by the
  open `[PLACEHOLDER]` items (the flag refuses to turn on without an override).

### P38-P41 Stand-alone chart windows

- `/?chart=` (`ux-audit/chart-window-old-1440.png`, `-390.png`): the v2 chart alone; ✓ except U10 ✗ (no way back to
  the app and no title on a phone). Add "Open in app".
- `/?workspace=1`, `?contract=`, `?basis=`: behind their flags; same rule.

### Modals and sheets

| ID | Score summary | Changes |
|---|---|---|
| M01 Command bar | ✓ all; U5 ✗ for the section captions | keep [User]; add actions ("Start paper bot", "Connect Telegram") later |
| M02 More sheet | U10 ✗ (about 20 tiles, incl. Factor Lab, Contract specs, Marketplace, Activity log) | shrink with the new sidebar [User] |
| M03 Broker prompt | ✓ all | keep [User] |
| M04 Connect panel | U4 ✗ (Redirect URL / TOTP secret, see P02) | HIDE those under the broker's Advanced [User] |
| M05 Broker Advanced | ✓ (collapsed) | the stored redirect_uri and IP data → [Admin] Support |
| M06 Renewal banner | U7 ✗ (missing on v2 Today) | add to v2 Today and Charts [User] |
| M07 Feedback | ✓ | keep [User] |
| M08 Help bot | flag `support_bot` OFF | turn on after the help pages are reviewed [User] |
| M09 Step-up | ✓ | keep [User] |
| M10 AI acknowledgement | U1 ✗ (a long legal text first) | one sentence + "Read more" [User] |
| M11 Signal detail / Library drawer | U4 ✗ (single prices) | the signal formatter [User] |
| M12 Phone chart sheet | ✓ | keep [User] |
| M13 Page intro + "Verify your email" chip | U3 ✗ (the chip sits on every page above the content) | the chip → Needs attention on Today, once; intros stay [User] - **Fixed 23 Oct (user-friendly pass items 1, 5)** |

---

## 5. Pages to merge or drop, and the proposed sidebar

### Merge

| New page | Made of | Why |
|---|---|---|
| **My trades** (tabs Open · Closed · Orders · Money) | My positions (P10), Orders (P11), Portfolio (P12) | the same trades seen three ways; one place for "what do I hold and what did I do" |
| **Strategies** (tabs Library · Build · Test) | Library (P14), Build a strategy (P15), Test a strategy (P16), the Copilot's Strategy Lab + Idea Builder | one path: pick or describe → test → Use in paper bot (11d) |
| **Performance** (tabs Results · Journal · Coach) | Performance (P19), Journal (P20), Coach and guide (P21), the Copilot's Coach & Scorecard | "how am I doing" in one place |
| **Find setups** (tabs Ready screens · Build your own) | Screener (P06), Custom scan (P07), the useful part of Factor Lab (P30, as Rankings) | one scanner, not three |
| **Option chain** (tab Plan a trade) | Option chain (P05), Plan an options trade (P08) | already the same address family |
| **Signals** | Signals (P09), the Copilot's Watchtower | one inbox for every suggestion |
| **News & events** | News & events (P22), the Copilot's News Radar | one news page |
| **Settings** (sub-pages) | Settings (P24), Setup (P25), Advanced (P26), Account (P27), Team (P28), Risk limits (P13), Notifications' Rules & delivery (P29), Activity log's sign-ins (P33) | set-once pages leave the main nav |
| **Bell drawer** | Notifications feed (P29) | the bell already exists |

### Drop from the user navigation

| Page | Goes to | Tag |
|---|---|---|
| Factor Lab (P30) | Find setups › Rankings (flag `screener_rankings`); the sample path to Testing tools | Remove / Admin |
| Contract specs (P31) | Admin › System › Instrument master | Admin |
| Marketplace (P32) | off until the last phase (11l) | Remove |
| Activity log (P33) | Settings › Account (sign-ins); exports to Admin › Support | User / Admin |
| Company research (P23) | hidden until a data source fills it | Remove (for now) |
| Old Signals generator, old Dashboard, old Option chain, old Positions / Portfolio / Risk | retired once `ui_v2` is on | Remove |

### The proposed sidebar (task order, Broker first)

**USER** (every signed-in trader):

```
Broker  ●                 (pinned: status dot green / amber / red)
Today                     (/)
Charts                    (/charts)
Option chain              (/option-chain, tab Plan a trade)
Find setups               (/screener)            - hidden while screener_v2 is off
Signals             (2)   (/signals)
Paper bot                 (/deployments, renamed)
My trades                 (/positions: Open · Closed · Orders · Money)
Performance               (/analytics: Results · Journal · Coach)
Strategies                (/strategies: Library · Build · Test)
AI Copilot                (/copilot)             - hidden while ai_copilot is off
News & events             (/news-events)
Settings                  (/settings: Setup, Alerts, Risk limits, Appearance, Plan, Account, Team, Advanced)
```

13 entries in one flat list (no section folding needed), down from 27 + 5 search-only. Phone bottom bar: **Today ·
Charts · Signals · My trades · More** (More holds the rest, Broker first with its dot). The bell (notifications),
Help and the account avatar stay in the top bar.

**ADMIN** (SUPER_ADMIN only, after MFA; a separate area, not mixed into the user list):

```
Admin console
  System            health, workers, logs, errors, data feed, instrument master, backups, deploy history,
                    go-live internals, exchange holidays, encryption status
  Controls          feature flags, maintenance mode, kill switches, disabled brokers, risk ceilings
  Users & business  users, organisations, plans, trials, subscriptions, sign-ups / active / revenue,
                    AI cost per user and per feature, beta cohort
  Testing tools     sample data, test signals, debug panels, data-source switch
  Support           one user's errors, broker status, recent sign-ins (never secrets)
  View as user      read-only
```

The admin sees the USER list too (an admin is also a trader) with an "Admin console" entry at the bottom and the
Diagnostics drawer button on each page.

---

## 6. The admin console proposal and its server-side enforcement

### What moves there (from today's user pages)

| From | Element | To |
|---|---|---|
| Backtest, Custom scan, Factor Lab, old Signals | "Data: Sample / Broker candles", "Deterministic sample OHLCV", "Sample bars", seed | Testing tools › Sample data (a per-session switch for the admin only) |
| old Signals | "Generate a signal from any inbuilt strategy" | Testing tools › Make a test signal |
| Custom scan | "a sample option chain is generated per symbol" | Testing tools › Sample option chain |
| old Dashboard | "Backend online", "Autopilot trading · beat 12s ago", "0 accounts · 3 adapters" | System › Health and workers |
| Copilot | Pre-flight checklist (worker container, broker session), market memory table, thesis shadow multipliers, "coverage" | per-page Diagnostics drawer; System › Data feed |
| Autopilot | Trading worker / Market tiles, token "VALID / session not yet verified" | Diagnostics drawer; Support › broker status |
| Settings → AI | provider / tier / model / per-call cost table, token counts, parser version | Users & business › AI cost (per user, per feature) |
| Settings → Vision | "(feature vision_byok)" | Controls › Feature flags (the user sees nothing while it is off) |
| Contract specs | MCX / crypto contract specs | System › Instrument master |
| Options Builder, Autopilot | "from the instrument master" | System › Instrument master (the user sees filled values) |
| Settings → Advanced | platform go-live items (worker, readiness, encryption, holidays) | System › Go-live internals |
| Admin page today | Platform readiness, exchange holidays, kill switch, maintenance, disabled brokers, beta rollout, risk ceilings, feature flags, incidents, tenants, platform exports, audit trail | split into System / Controls / Users & business / Support |
| Activity log | audit-trail / orders / trades / login-attempt exports (hash chain, SHA-256) | Support (per user) and System (platform) |
| Notifications | IP addresses and raw user agents | Support › a user's sign-ins |
| News & events | "Add a cited event", feed sources, trust score | Controls › Content |
| Broker → Advanced | the stored Upstox redirect_uri, static IP registration rows | Support › a user's broker status |

### The per-page Diagnostics drawer

A small "Diagnostics" button in the top bar, rendered **only** when the server says the viewer is SUPER_ADMIN with an
MFA-verified session. It opens a drawer for the current page: the reads the page made (endpoint, status, time), the
data age (market memory, chain, candles), the worker heartbeat, the relevant flags and their state, and the last
errors for this user. Nothing in it is ever part of a user page's normal payload.

### "View as user" (read-only)

The admin picks a user in Support and opens the app as that user sees it, with a fixed banner "Viewing as <e-mail> -
read-only". The server issues a short-lived impersonation token with **no write scopes** (every POST / PUT / DELETE
→ 403), audited on both accounts, never showing secrets (broker keys, API keys, AI keys stay masked).

### Server-side enforcement

What exists today: `/api/admin/*` and `/api/admin/controls/*` routers already carry
`dependencies=[Depends(require_role()), Depends(require_mfa_session)]` (`backend/app/admin/routes.py`,
`backend/app/platform/routes.py`): `require_role()` with no roles means SUPER_ADMIN only, and `require_mfa_session`
demands a TOTP-verified session.

The plan:

1. **One admin prefix.** Every endpoint the console uses lives under `/api/admin/...` with the router-level
   dependencies above. Endpoints that are admin-only today but live elsewhere move or get the same dependency:
   readiness (`/api/readiness` operator items), `/api/system/health` and `/api/system/worker-status` details,
   `/api/system/encryption`, history import / backfill jobs, holidays, platform exports.
2. **No diagnostic fields in user payloads.** User endpoints return only what a user page shows. Diagnostic fields
   (heartbeat seconds, cache ages, row counts, adapter counts, model ids, token counts, raw broker messages, flag
   names) are served only by `/api/admin/diagnostics/<page>` for the Diagnostics drawer. A response model per user
   endpoint (Pydantic `response_model` with an explicit field list) keeps new internal fields from leaking.
3. **Flag-off errors without the flag name.** `require_flag` today raises 503 with "The '<name>' feature is currently
   disabled by the platform operator" and the header `X-Feature-Disabled: <name>`. For a non-admin caller the body
   becomes "This feature is coming soon." and the header is dropped; admins keep the name.
4. **The role and the session check happen on the server.** The client hides the console for non-admins, but the
   server is the gate; the SPA route `/admin` for a non-admin shows the plain Not found card.

### Test plan (backend, pytest)

- **Every admin endpoint refuses a user token.** A test walks `app.routes`, takes every route whose path starts with
  `/api/admin` (plus an explicit list of moved operator endpoints), and calls each method with tokens for OWNER,
  TRADER, STRATEGY_CREATOR, VIEWER: every call must answer **403** (never 200, never a 5xx, never a body with data).
  A new admin route is covered automatically.
- **SUPER_ADMIN without MFA** gets 403 (`MfaRequired`) on the same walk; with a verified session, 2xx.
- **Impersonation token**: every write method on every route answers 403; reads answer as the viewed user.
- **No diagnostic fields**: for each user endpoint the pages read (dashboard, signals feed, positions, option chain,
  charts, settings reads), assert the JSON keys against an allow-list, and assert the absence of a deny-list
  (`heartbeat`, `beat`, `worker`, `cache_age`, `row_count`, `adapters`, `model`, `tokens`, `tenant_id` of other
  tenants, `X-Feature-Disabled` header, flag names in `detail`).
- **Frontend (vitest + e2e)**: with a user `/auth/me`, the Diagnostics button and the Admin console entry are absent;
  `/admin` renders the Not found card; with SUPER_ADMIN they render.

---

## 7. Feature flags

Source: `backend/app/platform/controls.py` (`FEATURE_FLAGS`, `DEFAULT_ON_FLAGS`, `DEFAULT_OFF_FLAGS` computed). 81
flags: **7 ON by default** (`ai_copilot`, `backtest_optimizer`, `live_trading`, `marketplace`, `options_builder`,
`public_api`, `self_signup`) and **74 OFF**. The Phase 0 deployment mode seeds `phase0_paper_only` ON and
`live_trading` OFF where nothing is stored.

How the status was decided: from the flag's own description, whether a user screen exists for it in `frontend/src`,
its dependencies (named in the description or in the code), the go-live runbook (1.8) and the work log. It is an
audit judgement, **not** a staging test: turn each flag on in staging first, one at a time.

Flags UX (8b), to build in fix 2 and fix 7:
- A page or feature whose flag is OFF is hidden from the navigation (the v2 nav already does this for `ai_copilot`,
  `discipline_engine`, `marketplace`, `options_builder`; **not** for Find setups / `screener_v2`, which is the bug in
  P06), and a direct URL shows "Coming soon" - never the flag name or a reference id.
- The admin Feature flags list shows the columns below (plain description, default, what turns on, dependencies,
  status), grouped by area, with a search box; a dependency that is off greys the dependent switch with "Needs
  <plain name> first".

| Flag | Plain description | Default | What turns on | Depends on | Status | Advice |
|---|---|---|---|---|---|---|
| `ai_copilot` | The AI Copilot page and AI strategy drafts | ON | Copilot in the sidebar; AI drafts | an AI key (else the built-in parser) | ready for users | keep ON |
| `marketplace` | Strategies other users publish | ON | Marketplace page | - | owner / compliance decision | turn OFF now (last phase, 11l) |
| `public_api` | API keys for developers | ON | Settings › API keys | - | ready for users | keep ON, under Advanced |
| `backtest_optimizer` | Try many settings of a strategy at once | ON | Optimise button in Backtest | - | ready for users | keep ON, under Advanced |
| `live_trading` | New LIVE (real money) bots | ON | LIVE mode | go-live checklist, two-factor | owner / compliance decision | OFF for Phase 0 |
| `phase0_paper_only` | Paper only: every new real-money entry is refused (exits still work) | OFF | nothing visible | - | ready (behind the scenes) | turn ON for Phase 0 |
| `self_signup` | Anyone can create an account | ON | Register link | - | owner / compliance decision | OFF for the invite-only beta |
| `news_feed` | Live headlines from public sources (marked unverified) | OFF | rows on News & events, News Radar | - | ready for users | safe (runbook 1.8) |
| `telegram_inbound` | Approve / reject paper trades from Telegram buttons | OFF | buttons in Telegram messages | a Telegram bot set up | ready for users | safe for PAPER (runbook 1.8), after Telegram connect works |
| `market_thesis` | A per-symbol market reading with scenarios | OFF | thesis card in the Copilot | market memory | unfinished / needs setup | after the Copilot clean-up (shows internal multipliers today) |
| `context_panel` | Market context: trend, breadth, VIX, events | OFF | context panel | FII / DII source optional | ready for users | safe |
| `options_builder` | Plan an options trade (payoff) | ON | Plan an options trade | instrument master | ready for users | keep ON |
| `options_builder_saved` | Save option plans | OFF | Save button in Plan a trade | options_builder | ready for users | safe |
| `marketplace_ai_listings` | AI-made strategies on the marketplace | OFF | - | marketplace | owner / compliance decision | keep OFF |
| `thesis_stock_targets` | Price targets and confidence for single stocks | OFF | numbers in the thesis | market_thesis | owner / compliance decision | keep OFF (11l: no targets) |
| `screener_v2` | The screener: saved screens and runs on broker data | OFF | Find setups page | a broker or stored data | ready for users | turn ON (Find setups is in the main nav and is empty without it); check on staging first |
| `screener_intrabar` | Alerts on the bar still forming | OFF | an option on alert rules | screener_v2 | ready for users | optional |
| `screener_builder_u2` | Formula blocks in the screen builder | OFF | builder blocks | screener_v2 | ready for users | after screener_v2 |
| `ai_agent` | Copilot reads your positions and news before answering | OFF | better answers | ai_copilot, an AI key | ready for users | owner choice (AI cost) |
| `ai_research` | AI drafts are back-tested and scored automatically | OFF | research loop | ai_copilot, an AI key | unfinished / needs setup | owner choice (AI cost) |
| `beta_cohort_gate` | Sign-up needs an invite code; cap on users | OFF | invite code field | - | owner / compliance decision | owner: ON for the closed beta |
| `vision_byok` | Use your own AI key to read chart images | OFF | Settings › AI › Vision key | a user's AI key | ready for users | owner choice |
| `vision_byok_v3` | Your AI key also reviews scan alerts and a daily chart review | OFF | comments on alerts | vision_byok | unfinished / needs setup | after vision_byok |
| `broker_app_links` | 'Trade in your broker app' links with a pre-filled basket | OFF | a link on proposals | - | owner / compliance decision | keep OFF until compliance review |
| `discipline_engine` | Automatic journal, behaviour tags, weekly discipline score | OFF | Journal page | - | ready for users | safe |
| `byob_data_tier` | Live data only through each user's own broker; data badges | OFF | data badges | broker data terms | owner / compliance decision | owner decision (data licence) |
| `history_import` | Import stored daily / 1-minute history | OFF | nothing visible | R2 store | ready (behind the scenes) | admin job |
| `history_backfill` | Nightly NSE end-of-day download | OFF | nothing visible | - | ready (behind the scenes) | safe (admin job); needed by chart_contracts / basis |
| `history_intraday` | Owner's own 1-minute history | OFF | nothing visible | owner broker session | ready (behind the scenes) | admin job |
| `approval_only_entries` | Every automatic entry waits for your Approve | OFF | Signals waiting for you | paper_account | ready for users | safe (more careful) |
| `paper_account` | Your own paper account and progress ladder | OFF | paper account card, Telegram /status | - | ready for users | safe |
| `risk_by_default` | Plan-before-trade ticket, risk-based size, daily loss lockout | OFF | paper ticket | - | ready for users | safe (PAPER), owner choice |
| `vix_gate` | A VIX spike pauses new bullish index entries | OFF | a note on blocked entries | VIX feed | ready for users | safe |
| `screener_gallery` | Ready-made screens to browse | OFF | gallery on Find setups | screener_v2 | ready for users | after screener_v2 |
| `screener_vectorized` | Faster screens (same results) | OFF | nothing visible | screener_v2 | ready (behind the scenes) | safe |
| `screenql_amw_functions` | Extra screen functions (divergence, swings, candles) | OFF | builder functions | screener_v2 | ready for users | after screener_v2 |
| `alerts_stale_guard` | No alert is sent on old data | OFF | nothing visible | - | ready (behind the scenes) | safe |
| `alerts_reliable_outbox` | Alerts never sent twice, retried if lost | OFF | nothing visible | - | ready (behind the scenes) | safe |
| `support_bot` | How-to help bot | OFF | Help button | help pages | unfinished / needs setup | after the help text review |
| `legal_pages` | Privacy, Terms, Risk disclosure and acceptance | OFF | legal pages, sign-up checkboxes | placeholders filled | owner / compliance decision | blocked: open placeholders |
| `screener_breakouts` | Breakout screens, set 1 | OFF | screens in the gallery | screener_v2 | ready for users | after screener_v2 |
| `screener_breakouts_bk3` | Breakout screens, set 2 | OFF | screens | screener_breakouts | ready for users | after set 1 |
| `screener_breakouts_bk4` | Breakout screens, set 3 | OFF | screens | screener_breakouts | ready for users | after set 1 |
| `screener_breakouts_fitness` | What followed past breakouts | OFF | a history box | screener_breakouts, shared daily data | ready for users | after set 1 |
| `evidence_context` | OI walls, trend strip and replay on evidence charts | OFF | more on match charts | evidence_charts | ready for users | after evidence_charts |
| `evidence_charts` | Each screen match drawn on a chart, also on Telegram | OFF | match charts | screener_v2 | ready for users | after screener_v2 |
| `public_status_page` | A public status page | OFF | /status | - | ready for users | safe |
| `market_zones` | Nightly demand / supply and S/R zones on the chart | OFF | chart layer, zone functions | nightly job | ready for users | safe after one night of data |
| `screener_legends` | Cards on public trading methods with a screen | OFF | Legends cards | screener_v2 | ready for users | after screener_v2; check wording (11k) |
| `screener_board_views` | Heat map and chart grid of matches | OFF | result views | screener_v2 | ready for users | after screener_v2 |
| `scan_auto_paper` | Armed scans place PAPER orders without asking | OFF | paper orders from scans | execution_core, approval_only_entries off | owner / compliance decision | owner choice (no approval step) |
| `screener_legends_lg2` | More Legends cards | OFF | cards | screener_legends | ready for users | after legends |
| `screener_fundamentals` | Reported company figures in screens | OFF | fields | screener_v2, fundamentals data | unfinished / needs setup | needs a fundamentals source |
| `screener_legends_lg3` | Value and growth Legends cards | OFF | cards | screener_fundamentals | unfinished / needs setup | after screener_fundamentals |
| `scan_then` | After a scan match, wait for a level, then notify or propose | OFF | watch-then-trigger | execution_core | ready for users | after execution_core |
| `chart_workspace` | Multi-chart layouts (1-8 charts) | OFF | Multi-chart tab | - | ready for users | safe |
| `execution_core` | One queue routes scan entries to a notification or a paper proposal | OFF | nothing new | - | ready (behind the scenes) | safe (never a broker order) |
| `ladder_evidence` | Your own figures on the paper ladder card | OFF | numbers on the card | paper_account | ready for users | safe |
| `experience_levels` | Futures and naked option selling open with your paper record | OFF | a gate message | paper_account | ready for users | safe |
| `screener_copilot` | Build and run screens from the Copilot | OFF | screen box | screener_v2, ai_copilot | ready for users | after screener_v2 |
| `screener_venues` | Refuse fields a venue does not carry | OFF | clearer errors | screener_v2 | ready (behind the scenes) | after screener_v2 |
| `screener_ownership` | Promoter / FII / DII stakes and events in screens | OFF | fields | screener_v2, ownership data | unfinished / needs setup | needs the data |
| `screener_rankings` | Rank a universe 0-100 | OFF | rankings | screener_v2 | ready for users | after screener_v2 (replaces Factor Lab) |
| `screener_backtest` | Back-test a screen over time | OFF | screen backtest | screener_v2 | ready for users | after screener_v2 |
| `screener_history` | Who matched on a past date, and what followed | OFF | history | screener_v2 | ready for users | after screener_v2 |
| `portfolio_risk` | Risk view of your paper book | OFF | a card on My positions | - | ready for users | safe |
| `portfolio_risk_caps` | Caps on new paper entries by sector and open risk | OFF | blocked-entry messages | portfolio_risk | ready for users | safe after portfolio_risk |
| `chart_studies` | Screen formulas drawn on the chart | OFF | a chart layer | screener_v2 | ready for users | after screener_v2 |
| `chart_price_action` | Swings, legs and trend drawn on the chart | OFF | a chart layer | - | ready for users | safe |
| `ai_multiview` | Technical, options, news and macro views of a symbol | OFF | a Copilot view | ai_copilot | ready for users | owner choice (AI cost) |
| `chart_trades_layer` | Your paper trades on the chart | OFF | a chart layer | - | ready for users | safe |
| `chart_contracts` | Daily option / future charts with OI | OFF | contract chart window | history_backfill data | unfinished / needs setup | after history_backfill has run |
| `chart_futures_basis` | Futures basis and rollover charts | OFF | basis window | history_backfill data | unfinished / needs setup | after history_backfill has run |
| `screener_tx2` | Confluence view, short mirrors, watch-then-trigger lists | OFF | screen tools | screener_v2 | ready for users | after screener_v2 |
| `ui_v2` | The redesigned app (navigation and pages) | OFF | the whole v2 shell | - | ready for users | turn ON after fixes 1-2 (sign-in, friendly errors) |
| `watchlists` | Your own symbol lists | OFF | watchlists on Charts / screens | - | ready for users | safe |
| `decision_tools` | Risk-of-ruin, what-if, correlation, expected move | OFF | tools on the chart | - | ready for users | safe |
| `backtest_models` | More realistic fills in backtests (opt-in per run) | OFF | options in Advanced | - | ready for users | safe |
| `screener_etf` | ETF screen | OFF | a screen | screener_v2, daily history | ready for users | after screener_v2 |
| `alerts_whatsapp_dlt` | WhatsApp and SMS alerts (India DLT) | OFF | channels | DLT registration | unfinished / needs setup | needs DLT registration |
| `plan_limits_v2` | Per-plan limits for watchlists, reports, Lab | OFF | limits | plans | owner / compliance decision | owner decision (pricing) |

### OFF flags that are safe to turn on now

Turn ON for Phase 0 (safety, nothing visible): `phase0_paper_only`.

Safe now (read-only, reliability, or PAPER-only; each has a screen or works behind the scenes):
`news_feed`, `telegram_inbound`, `context_panel`, `options_builder_saved`, `discipline_engine`, `history_backfill`, `approval_only_entries`, `paper_account`, `risk_by_default`, `vix_gate`, `screener_vectorized`, `alerts_stale_guard`, `alerts_reliable_outbox`, `public_status_page`, `market_zones`, `chart_workspace`, `execution_core`, `ladder_evidence`, `experience_levels`, `portfolio_risk`, `portfolio_risk_caps`, `chart_price_action`, `chart_trades_layer`, `watchlists`, `decision_tools`, `backtest_models`.

Turn on after a fix: `ui_v2` (after fixes 1-2), `screener_v2` (check on staging; it makes Find setups work, and 24
research flags depend on it directly or through another screener flag), `market_thesis` (after the Copilot clean-up), `support_bot` (after the help text
review).

Keep OFF (compliance or owner decision): `marketplace_ai_listings`, `thesis_stock_targets`, `broker_app_links`,
`scan_auto_paper`, `byob_data_tier`, `legal_pages` (open placeholders), `plan_limits_v2`, `beta_cohort_gate` (owner:
ON for the closed beta), and among the default-ON ones turn **`marketplace` OFF** now (11l) and keep `live_trading`
OFF in Phase 0.

---

## 8. Banned UI words (input for the future CI guard)

Rule for the guard: these words must not appear in user-visible strings outside an "Advanced" block, the admin
console or the Diagnostics drawer. "Where" lists the pages where the capture saw them on screen (section 2) or the
component that renders them.

| Word / pattern | Kind | Where it appears today (outside Advanced) |
|---|---|---|
| `Sample`, `sample data`, `sample bars`, `seed` | demo data | Backtest, Custom scan, Factor Lab, old Signals (`utils/sampleData`) |
| `Deterministic`, `OHLCV` | developer | Backtest, Custom scan, Factor Lab, old Signals |
| `backend` | developer | Backtest, Custom scan, Factor Lab, old Dashboard ("Backend online / offline") |
| `worker`, `heartbeat`, `beat <n>s ago` | internal | Autopilot, Copilot pre-flight, Settings alert text, old Dashboard |
| `instrument master` | internal | Autopilot, Plan an options trade |
| `tenant`, `organisation ... not turned on`, `(feature <flag>)` | internal / flag names | Find setups, Plan an options trade, Settings (Vision), Admin |
| `Not found` (a raw server detail) | raw error | Activity log, Analytics, Autopilot, Backtest, Contract specs, Fundamentals, Journal, Marketplace, News & events, Plan an options trade, Settings, Strategy library, Strategy builder, Team, old Signals |
| `registry`, `auto-executable`, `persisted` | developer | Strategy library, Analytics |
| `deployment(s)`, `Deployment #<n>` | internal | Autopilot, Copilot, Signals card, Strategy builder, Team |
| `parser`, `rule_based`, `nlu-parser`, `deterministic, rule-based` | developer | Strategy builder, Custom scan, Settings → AI |
| `HMAC`, `X-ATP-Signature`, `X-API-Key`, `/api/`, `endpoint`, `JSON` | developer | Settings (Webhook, Public API keys, TradingView webhook), Activity log |
| `SMTP`, `STARTTLS`, `DLT`, `gateway`, `body template` | developer | Settings → Alert delivery |
| `floor`, `INFO`, `WARNING`, `CRITICAL` (as levels) | internal | Settings → Alert delivery, Notifications, Admin |
| `bot token`, `BotFather`, `chat id` | developer | Settings → Telegram |
| `OAuth`, `redirect`, `Redirect URL`, `request token`, `TOTP secret`, `developer console` | developer (U4 list) | Broker connect panel (Fyers, Angel One, Shoonya), Charts / Option chain broker gate |
| `lookback` | developer (U4 list) | Backtest |
| `TARGET_1`, `STOP_LOSS`, `RISK_REJECTION`, `NO_TRADE`, `SECURITY` (upper-case codes) | enum codes | Orders, Notifications |
| snake-case ids: `orb_nifty`, `demo_swing`, `ema_rsi_scalper_1m` | internal ids | Orders, Coach, Risk limits (strategy names) |
| `PLUS_DI`, `MINUS_DI`, `OR_HIGH`, `OR_LOW`, `PDH`, `PDL`, `PDC`, `VOLUME_SMA`, `BB_UPPER` | internal ids | Strategy builder, Custom scan |
| `shadow`, `multiplier`, `coverage`, `STALE (<n> H)` | internal | Copilot (thesis, market read) |
| `SHA-256`, `prev_hash`, `hash-chained`, `UTC` | developer | Activity log, Admin |
| user agents (`Python-urllib`, `curl/`, `HeadlessChrome`), IP addresses | internal data | Notifications |
| scope ids (`ai:manage`, `trading:live`, ...) | internal | Settings → Account |
| model ids, tier names (`cheap / fast / strong`), token counts | internal | Settings → AI provider |
| `ISO week` labels (`2026-W43`) | developer format | Journal |
| `Entry <price> · Stop <price> · Target <price>` in signal text | policy (11l) | Today, Signals, signal detail |

The guard: a vitest that renders each user page with the e2e fixtures (or scans `.tsx` string literals and the
locale files) and fails on any pattern above unless the string sits inside a component marked `data-advanced`,
`AdminOnly` or `Diagnostics`. Exceptions are listed by file and line in the test, each with a reason.

---

## 9. The fresh-eyes test (8 tasks)

### Method, stated honestly

The brief asked for a separate agent that knows nothing about the codebase. That was **not possible here**: this
session has no tool to start a sub-agent, and a separate cloud session would not reach this container's preview
(and the app needs the e2e mock layer, since there is no backend). So the test was done the second way the brief
allows: **a heuristic walkthrough with Playwright** against the built preview with the e2e mocks, acting as a
first-time user on a **fresh account** (no broker, no strategy, no positions), using only what is visible on screen
(button and link names, page text), at 1440 and 390, with `ui_v2` ON and OFF. A small script drove the clicks and
logged what each screen showed; the judgement about confusion is the auditor's.

Limits: the auditor knows the codebase, so this under-counts confusion; times are script times, not human times;
mocked replies replace the real server (a real broker login cannot be tested). The real fresh-eyes test with an
outside agent or person must still be run after the fixes (Master Order 3, section 3).

### Results

| # | Task | Done? | Steps (v2, desktop) | Where a beginner gets confused |
|---|---|---|---|---|
| 1 | Sign in | **Not without searching** | open `/` → a dashboard with "Backend offline", "Not found", "Log in to see your book" → find the small "Not signed in - click to log in" in the sidebar (desktop) or the account icon (phone; the main area has no sign-in link) → fill Email / Password → Log in → **stays on the Account page** with a list of permission codes | no sign-in screen; errors before sign-in; after sign-in nothing says "you are in"; the fields have no programmatic labels (a screen reader or an automated helper cannot find "Email") |
| 2 | Connect the broker | **Yes (Upstox), partly for others** | Broker (first in the nav, dot) → Upstox tile → one field "Analytics Token" → Connect (the script's own click missed the nav link after sign-in; the steps are confirmed from the Broker page pictures and `e2e/broker.e2e.ts`) | "Where do I get this?" is the only help; Fyers asks for a Redirect URL, Angel One / Shoonya for a TOTP secret; on a phone, Broker is inside More |
| 3 | NIFTY chart on 15m and daily | **Yes, once a broker is connected** | Charts → type NIFTY → 15m → D | before a broker: the gate says "keys from your broker's developer console"; with `ui_v2` OFF there is **no Charts page** at all (`/charts` is "This page does not exist") |
| 4 | Option chain, nearest expiry | **Yes (v2)** / **No (old)** | Option chain → opens on NIFTY, nearest expiry, ATM in the middle | phone: the summary strip scrolls sideways; old page: "Underlying · Show chain", OI settings, PCR jargon, sample chain |
| 5 | Start a paper bot | **No for a beginner** | Today → Get started "Start paper bot" → Autopilot: about 40 controls (structures, OI gates, regime filter, dynamic exits), "Not found" toasts, LIVE / PAPER switch, "Deploy paper" at the bottom | the page assumes a quant: which strategy? what is "Base candles", "Write (LONG→sell PE)", "OI gates"? |
| 6 | Set up Telegram alerts | **No** | Today → "Set up Telegram" → Settings, Alert delivery: "Bot token from @BotFather", "Chat id", "floor", "enabled", Save, Send test; the Setup wizard says the organisation's bot is added in Settings | the user must create a bot and find a numeric chat id; next to it HMAC and SMTP text |
| 7 | Analyze an uploaded chart | **No - the feature does not exist in this build** | searched the command bar for analyze / upload / image / vision / screenshot: nothing; no file input on any page except the contract-note upload | drafts #304 / #305 hold it; a user who heard about it cannot find it |
| 8 | Find today's P&L | **Yes (v2, with a broker)** / **No (old)** | Today → "Today's P&L +₹4,380" at the top | fresh account: hidden until a broker is connected (no "₹0 today" line); old shell: only all-time "Net P&L ₹18,250", today's P&L is not on the front page |

Score: v2 3 of 8 done cleanly (2, 4, 8) plus 1 after a broker (3); old shell 1 of 8 (2). These findings are in
section 1 (problems 2, 3, 7, 10) and the fix order.

---

## 10. Cross-cutting findings

- **Two titles on most old pages**: the v2 top bar shows the nav label, the page shows its own old header (Build a
  strategy / Strategy Builder, Test a strategy / Backtesting, Custom scan / Market Scanner, Performance / Analytics,
  Activity log / System Logs, Contract specs / Instruments, Autopilot / Strategy deployments). Fix: one name per page,
  the page header removed where the top bar carries it.
- **Disclaimers as the second block of a page** (Strategy builder, Backtest, Custom scan, Factor Lab, Fundamentals,
  Marketplace, old Signals): a 4-line risk paragraph before any control. Fix: one line next to the result or the
  action, the full text behind "Risk disclosure".
- **Errors**: raw server text ("Not found") as page content and as toasts on 15 pages; "We could not load ... Try
  again in a moment." without a Try again button (Go-live checklist). Fix: one error component (what happened + one
  action), a mapper that never shows a server `detail` to a user.
- **Numbers**: the old pages show prices without ₹ or separators (Analytics "18250.00"), times in US format and UTC
  (Orders, Notifications). Fix: the v2 formatters (₹, lakhs / crores, IST "19 Oct, 10:40") everywhere.
- **Phone**: wide tables on Orders, Team, Admin, Strategy builder's condition grid, the Copilot market memory; the
  option chain summary strip scrolls sideways.
- **The "Verify your email" chip** sits on every page between the top bar and the content. Fix: one item in Needs
  attention on Today, and a line in Settings › Account. **Fixed 23 Oct (user-friendly pass item 1).**
- **Accessibility**: the sign-in fields' labels are not tied to the inputs (`<label>` without `htmlFor`); the Copilot
  gauges carry meaning by needle position only; chart drawing tools are icon-only (check labels).
- **Colour**: v2 pages use colour for meaning only; old pages use orange for informational boxes (the Sample data box,
  the DPDP box) - amber must mean "needs attention".

## User-friendly pass (owner, 23 Oct) - status

The owner's six screenshot items plus three from the Analyze a chart screenshot, branch `claude/user-friendly-pass-1`
(item 1 also shipped alone as `claude/ufp-verify-email`). All behind `ui_v2` unless noted.

| # | Item | Status |
|---|------|--------|
| 1 | "Verify your email" chip on every page (M13) | Fixed: no chip in either top bar; one dismissible Needs attention row with "Resend link"; Settings › Account card. |
| 2 | "2 paper · 1 live" in the paper phase | Fixed: while `phase0_paper_only` is on, "2 practice positions", "every entry", "Booked so far"; no LIVE notice in the top bar or LIVE row in Needs attention. |
| 3 | "Owner" under the name | Fixed: the sidebar shows the role only when the organisation has more than one member (`GET /api/team/tenant`). |
| 4 | Money with paise (also "Numbers" above) | Fixed: `formatINR` whole rupees with Indian grouping by default; `{ paise: true }` only on tickets, fills, closed-trade P&L and charges; the lakh shorthand is gone from tiles. |
| 5 | "What is this page?" box + repeated subtitle | Fixed: one short line, no box title, never repeating the subtitle (guard test); "Hide" collapses it to "About this page", remembered per page. |
| 6 | "Orb nifty" (row 5 above: `orb_nifty`) | Fixed: `src/ui/strategyName.ts` - the Library name, the built-in registry names, else words ("Opening Range Breakout · NIFTY 50"); guard test against slugs. |
| 7 | "Analysis engine not installed" shown to users | Fixed (server + both shells): without the private engine `/api/system/features` answers `chart_analyze*` off, so the nav hides it and its address shows "Coming soon"; the admin flag list states the reason. |
| 8 | "... 5 MB. paste a screenshot" | Fixed: each drop-zone sentence starts with a capital. |
| 9 | The chip still under the top bar | Same as item 1. |

