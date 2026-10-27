# UI-H Part 1 - UI/UX audit (24 Oct 2026)

Roadmap item **UI-H** (owner order, INBOX 24 Oct; standard H1-H10 in DECISIONS 24 Oct). Part 1 only: audit, no app
code changed. Part 3 (fix waves) starts after the owner approves this audit.

## How it was done
- Built `main` (`341de6f8`) and served it on port 4186; the Terminal from the open T1 branch
  (`claude/terminal-t1-workspace`, not on main yet). Flags: `ui_v2`, `colour_v2`, `terminal` and every page flag ON.
  Data: the e2e mocks only (made-up data) - `e2e/mockApi.ts`, `dashboardMocks`, `tradingMocks`, `chartsMocks`,
  `settingsMocks`, `adminMocks`, `fixtures/option_chain_v2.json`, `option_chain_charts.json`, the chart-analyze fixtures.
- 238 captures: about 60 page / tab states x 390 + 1440 px x light + dark, plus a "market closed" state (Saturday 24 Oct 13:39
  IST, sessions closed) for Today, Charts and the Option chain. A DOM pass on each capture measured: page overflow,
  truncated text, sideways scrollers, tab rows (tabs off-screen, active styling), inline method text, number formats,
  status / as-of notes, disclaimers, developer words. About 10 contact sheets were viewed by eye.
- **Mock gaps:** Signals, Paper bot, Sectors & indices, Find setups, Learn, Ideas, Strategies, Performance results,
  Journal, Copilot, News, Company research, Contract specs, Team and Plan an options trade call APIs the capture mocks
  do not answer. Their own committed v2 baselines (`frontend/e2e/__screenshots__`, e.g. `signals-v2-390-light.png`) were
  used for the look; the captured error / "Coming soon" states are scored only for H8.
- Screenshots and the gallery are outside git (owner's token rule). File names: `<page>-<tab>-<width>-<theme>.jpg`;
  below, `@390` / `@1440` means "that width, both themes" unless a theme is named.

Severity: **H** = a trader is misled or cannot see / reach something; **M** = clearly against the standard, often
seen; **L** = polish.

## Findings per page

| Page / tab | Issues (checklist ID - severity - what) | Screenshot |
|---|---|---|
| Today | H7-H: on a closed Saturday the header still reads "Saturday, 24 October · 13:39" (a live clock) · H1-M: "Get started" card sits above the P&L and the market strip on phone, so the summary is not first · H10-L: no disclaimer line · H4-L: index values full precision (24,812.35), fine, but P&L cards mix "+₹660 booked · +₹3,720 open" in one line | today-closed-390-light.jpg, today-main-390-dark.jpg |
| Charts | H6-H: blank canvas for an option contract when closed, no empty-state message (bug C1, from code; the mocks cannot reproduce it) · H2-H: timeframe row at 390 shows 1 min-30 min; 1 H, D, W, M sit off-screen (584 px row in 340 px) with no scroll cue · H7-M: "Live" badge + "Market open/Closed" chip + price tag: three status marks · H1-L: no page title / no summary (chart-first is right for this page) | charts-main-390-dark.jpg, charts-closed-390-light.jpg |
| Signals | H2-M: Waiting / Today / History is a white-pill control unlike other pages · H8-M: read failure says "could not be read. Try again" with no reason · H10-L: "Nothing is placed until you approve it" is good; no information-only line | signals-main-390-light.jpg, baseline signals-v2-390-light |
| My trades - Positions | H3-M: inline "Worked out from each option's last price and the index or stock price now." · H4-M: rupee figures always full (₹1,61,990); no compact + full-on-tap · H9-L: top bar truncates "My positions" box at 390 | mytrades-positions-390-light.jpg |
| My trades - Orders | H5-H: blotter is an 822 px table in a 322 px box at 390: only Time / Action / Symbol visible · H4-H: times shown as `10/19/2026, 5:10:00 AM` (browser locale, not en-IN / IST) · H1-M: legacy page - uppercase headers, "broker-style order blotter" jargon, own heading under the tab bar | mytrades-orders-390-light.jpg |
| My trades - Money | H1-L: heading "My portfolio" under the tab "Money" · H4-M: full rupee values | mytrades-money-390-light.jpg |
| More sheet | H9-L: tile "Sectors & ind..." truncated · fine otherwise | more-sheet-390-light.jpg |
| Option chain - Chain | H5-H: Puts OI column cut at the right edge on phone (owner's report) · H9-M: "Days to exp..." card label truncated at 1440 · H4-M: OI shown "6 L" but OI change "−15,000" and Volume "59,000" in full; Analyses header "expiry 2026-10-27" (ISO) beside "3 Nov 2026" · H7-H: closed market shows "Market closed - showing the last session's chain" AND "Data as of 10:00 IST" (two notes, and the as-of is the request time - bug B2) · H1-L: title twice (top bar + h1) | option-chain-chain-1440-dark.jpg, option-chain-closed-390-light.jpg |
| Option chain - Analyses (6 tabs) | H3-H: every tab ends with an inline "How it is worked out: ... OI x max(0, K - strike) ..." paragraph · H2-H: at 390 only 4 of 6 tabs fit (Move / IV skew or S/R / Build-up off-screen, 491 px in 332 px), short labels differ from desktop, the active tab is only bold on a slightly lighter grey (weak contrast) · H6-H: Max pain / S-R bars start at 0, no minimum marked, y labels squeezed in a fixed 44 px gutter (clip with crore-size values) · H5-M: 2-3 tables always open under each chart; "Through the session" tables 459-715 px wide at 390 · B1/B3/B4 below | option-chain-analyses-maxpain-390-light.jpg, option-chain-analyses-pcr-390-dark.jpg |
| Option chain - Charts | H2-M: "Strikes shown (up to 3)" control is 1406 px wide (scrolls) · H7-M: "Data as of 10:30 IST · 8 stored readings" while the Analyses below says "Data as of 10:00 IST" · H3-M: method text under the charts ("divided by call OI over the 21 strikes ...") · good: one chart at a time with a pager, spot line, legend | option-chain-charts-390-dark.jpg |
| Plan an options trade | H1-L: heading "Options Builder" vs menu "Plan an options trade" · H8-M: generic "not available right now" toast | option-chain-plan-390-light.jpg |
| Analyze a chart | H8-H: with the engine missing the page says "Coming soon - not open for your account yet" (wrong reason; A1) · H9-M: top bar "Analyze a ch..." at 390 · H3-L: long privacy / provider text inline | chart-analyze-engine-off-390-light.jpg, chart-analyze-main-390-light.jpg |
| Terminal (T1 branch) | H2-M: timeframe row cut at 390 (382 px in 226 px) and at 1440 · H4-M: "Funds ₹9,93,300" full · H7-M: "Live" badge on the chart while the page footer also shows mode / feed · H1-L: no h1; six panel buttons styled as a second button grid, not a segmented control | terminal-main-390-light.jpg, terminal-main-1440-dark.jpg |
| Sectors & indices | H9-M: "Sectors & ind..." truncated in the top bar · H2-M: two stacked segmented controls (Sectors/Indices, Day/Week/Month) with equal weight · H8-L: a chip "I don't know" for a sector with no data | markets-main-390-light.jpg, baseline markets-v2-390-dark |
| Find setups / Custom scan | H1-M: custom scan headed "Market Scanner" (old name) · H8-M: when the scanner API is off the page says "Coming soon" | find-setups-custom-390-light.jpg, baseline screener-idea-v2-390-light |
| Paper bot | H3-L: "Pick a strategy and a symbol..." help box inline (has Hide) · H8-M: "This is not available right now. Try again later." toast with no reason | paper-bot-main-390-light.jpg, baseline paper-bot-v2-390-light |
| Performance - Results / Journal / Coach | H1-M: old headings "Analytics", "Journal & discipline", "Coach & Guide" under the tabs · H2-H: Coach adds an underline tab row (Trade coach / Guide) and chip filters - a third tab style · H9-M: top bar "How am I doi..." · H4-L: uppercase card labels (TRADES, WIN RATE) | performance-coach-390-light.jpg |
| Strategies - Library / Ideas / Build / Test | H1-M: old headings "Strategy Library", "Strategy Builder", "Backtesting" · H8-M: "Coming soon" / "not available" when an API answers 404 | strategies-*-390-light.jpg, baseline idea-library-v2-390-light |
| AI Copilot | H9-L: "AI Copilot" label truncated · disclaimer present (good) | copilot-main-390-light.jpg |
| News & events | H8-H: developer text on the page: "News trust: 1.00 (0 verdicts, null)" · H9-L: "on · fetch every 15 min" truncated | news-events-main-390-light.jpg |
| Learn | H2-M: Lessons / Words explained uses a blue-tint control (another style); topic chips cut at 390 · H10 good ("Education only...") | baseline learn-v2-390-light |
| Company research / Contract specs / Factor Lab / Activity log / Team / Notifications | H1-M: old headings ("Fundamental Analysis", "Instruments", "System Logs") differ from the menu names · H8-M: Activity log explains "CSV or JSON ... SHA-256 ... manifest ... UTC" to a trader · H10: Factor Lab repeats a long legal line | company-research-main-390-light.jpg, activity-log-main-390-light.jpg |
| Risk limits | H3-M: "how it is worked out" lines inline on each card · H4-M: ₹5,00,000 / ₹5,06,000 in full · H9-L: title box truncation at 390 | risk-limits-main-390-light.jpg |
| Broker | Good (status first, plain words). H1-L: no page title on phone | broker-main-390-light.jpg |
| Settings index + Account, Setup, Trading defaults, AI, Alerts, Appearance, Help, Advanced | Index is the best page today (status badges per row). Account: H4-L uppercase section titles ("TWO-FACTOR AUTHENTICATION (TOTP)", "RFC 6238") - H8-M jargon · Trading defaults: H9-M top bar "My trading defaults" truncated at 390 · Setup / Trading defaults: H8-M "We could not load ... We will try again" with no reason | settings-index-390-dark.jpg, settings-account-390-light.jpg |
| Admin console (System, Controls, Users, Testing, Support) | H5-H: Users page overflows the phone by 277 px (two wide tables) · H2-M: section tabs: "Testing tools" and "Support" off-screen at 390 (566 px row) · H5-M: worker / chain tables 341-368 px in 332 px · technical words are allowed here (admin) | admin-users-390-light.jpg, admin-controls-390-dark.jpg |

Cross-page: no page uses the H1 order "title -> status badge -> summary cards -> visual -> details" consistently
(Option chain and My trades come closest); there are **six different tab / sub-tab styles** (white pill, filled grey,
blue tint, underline, button grid, chips); the "(?)" icon exists on summary cards but method text is still printed
inline; no shared number formatter for compact Indian units; the one-line disclaimer (H10) appears on 6 of ~30 pages
with five different wordings. Text sizes: no visible text below 14 px was found in the DOM pass.

## The 10 worst issues (ranked)
1. **C1 / H6** Charts: an option contract on a closed market shows an empty canvas with no message (owner's report; the mocks always return bars, so it is proven from the code below, not a capture).
2. **H3** Option chain Analyses: formula paragraphs ("How it is worked out: ... max(0, K - strike) ...") printed inline under every tab. option-chain-analyses-maxpain-390-light.jpg
3. **H2** Sub-tabs hidden on phone: Analyses (2 of 6 off-screen), Charts timeframes (1 H / D / W / M off-screen), Terminal timeframes, Admin sections; the active sub-tab has weak contrast. charts-main-390-dark.jpg
4. **H7 / B2** Closed market looks live: "Data as of 13:39 IST" on Saturday, Today's live clock, "Live" badges, and two contradicting notes on the chain. option-chain-closed-390-light.jpg
5. **H5** Option chain Puts column cut on phone; Orders blotter shows 3 of 7 columns; Admin Users overflows the page by 277 px. mytrades-orders-390-light.jpg
6. **H6** Max pain / S-R charts: axis from 0, minimum not marked, y-labels squeezed (fixed 44 px gutter). option-chain-analyses-maxpain-1440-dark.jpg
7. **H8 / A1** Analyze a chart with the engine missing says "Coming soon - not open for your account" (wrong reason); several pages show "Coming soon" when an API merely failed. chart-analyze-engine-off-390-light.jpg
8. **H4** Orders times in US format and browser time zone (`10/19/2026, 5:10:00 AM`); full rupee values everywhere; ISO expiry dates beside "3 Nov 2026". mytrades-orders-390-light.jpg
9. **H2** Six different tab styles across the app (Signals, My trades, Coach, Learn, Markets, Admin, Terminal). performance-coach-390-light.jpg
10. **H8** Developer text to traders: "News trust: 1.00 (0 verdicts, null)", "CSV or JSON ... SHA-256 ... manifest", "RFC 6238 app". news-events-main-390-light.jpg

## Root causes of the owner's functional bugs (read-only, from the code)
- **C1 blank chart (option contract, closed):** the server answers `POST /market-data/candles` with `bars: []` and no
  error when the broker returns nothing (`backend/app/market_data/candles_routes.py` pass 1; `market_data/service.py`
  `_fetch`: history window ends `now - 1 day`, today's part comes from the Upstox intraday endpoint, both empty for the
  contract on a closed day). The frontend `CandleFeed.tick()` (`frontend/src/chart/useChartCandles.ts`) then calls
  `replace([])` and clears the error, so the canvas is empty with no message. Proof still needed on the real feed
  (one logged request for the contract at 30 min): which of the two calls is empty.
- **A1 engine missing:** the engine is the private package `atp_private.chart_analyze`; `deploy/atp.sh` does not
  install it. The page maps `engine.installed === false` to the generic `<ComingSoon>`
  (`frontend/src/chartAnalyze/ChartAnalyzePageV2.tsx:311-314`), hence the wrong reason.
- **B1 Analyses disabled with own-broker data:** `backend/app/option_chain/analyses_routes.py:109-111` drops the
  session history when `byob` is on (`history = [] if byob`, `history_off=byob`), because snapshots come only from the
  shared OI collector (`option_chain/snapshots.py`, tenant collector session); the OI routes refuse the same way
  (`option_chain/routes.py` `_no_byob`). Fix: store the user's own chain reads as his snapshots. No hard constraint seen.
- **B2a "Data as of 13:39" on Saturday:** `analyses_routes.py:112` sets `data_as_of = now` (the request time), not the
  chain's capture time / last session close.
- **B2b Spot = ATM strike (22,600.00):** not proven from code. Spot is `chain.underlying_ltp` from Upstox
  `underlying_spot_price` (`brokers/upstox.py:461`), else `_last_price` (`option_chain/routes.py:307`); no code sets spot
  to a strike. Most likely the broker sends the strike-rounded value after hours, or a stored lake chain
  (`market_lake/chain_store.py`) carries it. Needs one captured response on a closed day.
- **B3 days to expiry:** `backend/app/option_chain/chain_view.py:304` `(expiry - today).days` - calendar days; the
  market calendar (`market_data/calendar.py`) is not used. Expected-move text uses the same count.
- **B4 PCR zones:** `backend/app/option_chain/dish.py:31` `PCR_ZONES` constants (0.70 / 0.90 / 1.00 / 1.30); not in any
  settings namespace.
- **Max pain chart:** `frontend/src/optionChain/analyses.ts:183` forces bar charts to include 0; `CHART.padLeft = 44`
  (`analyses.ts:174`) is a fixed y-label gutter; the server sends only a spot marker (`option_chain/analyses.py:515`
  `_spot_marker`), so the minimum is never marked. **Puts column cut:** `frontend/src/optionChain/ChainTableV2.tsx` phone
  column set does not fit 390 px (last OI cell clipped).
- **Orders time format (found here):** `frontend/src/pages/OrdersPage.tsx:79` `new Date(o.time).toLocaleString()`.

## Proposed wave order
**Wave 0 - shared components (ui_v2, colour_v2 tokens, no page-local styling):**
1. `PageHeader` - short title that wraps, never truncates (H1, H9).
2. `StatusBadge` - one per page: live / closed "as of <last session close>" / paper / delayed (H7).
3. `SummaryCard` - one big number + one plain line + optional (?) (H1, H4).
4. `SubTabs` segmented control - active = filled accent + bold (WCAG AA), inactive muted; wraps or scrolls the active
   one into view on phone; replaces the six styles (H2).
5. `HelpPopover` / (?) sheet - bottom sheet on phone, popover on desktop; takes all method / formula text (H3).
6. `IndianNumber` - Cr / L / K compact, full value on tap / hover, fixed decimals, +/− and meaning colours; plus one
   en-IN / IST date-time formatter (H4).
7. `ResponsiveTable` - sticky first column + scroll or stacked rows; "Show table" collapse when a visual exists (H5).
8. `ChartFrame` with empty state - zoomed axis, label gutter sized to the labels, spot / now marker, legend, plain
   "no bars for this contract / timeframe" state (H6, C1).
9. `Disclaimer` - one line at the bottom, one wording (H10); and a shared `UnavailableState` (user reason vs admin
   wording, H8).

**Wave 1 (most used):** Option chain + Analyses + Charts tab (with B1-B4, max pain, Puts column), Charts (C1,
timeframe row), Today. **Wave 2:** My trades (Positions / Orders / Money), Signals, Terminal. **Wave 3:** Paper bot,
Find setups, Sectors & indices, Analyze a chart (A1 state), Strategies (4 tabs), Performance (3 tabs). **Wave 4:**
Settings and sub-pages, Learn, News, Copilot, Risk limits, Broker, the remaining More pages. **Wave 5:** Admin console.
One before / after comparison page (phone + desktop) per wave.
