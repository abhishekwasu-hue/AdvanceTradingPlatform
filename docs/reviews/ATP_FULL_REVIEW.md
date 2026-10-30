# ATP full review — functionality, UI, phone layout, speed

Method: (1) code audit of every page in the sidebar (frontend v2 page + the backend endpoints it calls); every finding carries
file:line proof; (2) production check on aialgodesk.tech while signed in as the owner: every page opened, API calls timed,
error banners read, horizontal overflow measured at 375 px phone width; (3) the key findings re-read in code before listing.
Severity: High = wrong trade / money / safety or a broken function; Medium = wrong information shown or a CLAUDE.md rule
broken; Low = UI / performance. "Needs live check" items are NOT proven yet.

## 0. Production facts (measured)
| Item | Result |
|---|---|
| Worker | running, healthy, 60 s cycle, last cycle 707 ms |
| Page load (41 pages) | every page settled in 2-4 s; API calls mostly < 300 ms |
| Slow calls | Charts: /markets/overview 2.9 s, /market/strip 2.8 s, /option-chain/banners 2.8 s (on page open); /ai/chat/home 0.9 s; /strategy-templates 0.95 s; /me/prefs 0.8-0.9 s on My trading defaults |
| Errors | no error banner on any page; /pwa/vitals answered 422 once (cause not proven) |
| Phone 375 px | only Settings overflows: the header avatar ("AW") is cut at the right edge and the page scrolls sideways by 10 px; all other 40 pages fit |
| Instrument master | POST /deployments answered "No NIFTY options in the instrument master - sync it (Admin Console)": no NIFTY option bot can start |
| Signals | 0 rows in 30 days across all 5 sources (the first paper bot was created on the review day) |
| Flags | approval_only_entries ON (so "You approve every order" is true today); byob_data_tier ON; why_no_trade OFF; paper_report_card OFF |

## 1. Paper bot (/deployments) and Signals (/signals)
| Sev | Finding | Proof |
|---|---|---|
| High | Every 409 from POST /deployments is shown as "this strategy already runs on paper"; production returned "No NIFTY options in the instrument master" and the page showed the duplicate sentence | paperbot/PaperBotV2.tsx:471-472 `e.status === 409 ? "Nothing new was started: this strategy already runs..."`; be deployments/routes.py 409 for no broker, no contracts, duplicate |
| High | A stopped bot blocks a new bot on the same strategy + symbol forever: the unique key ignores status and the v2 page offers no delete | db/models.py:543-544 `UniqueConstraint("tenant_id","strategy_id","symbol","mode","instrument_kind")`; PaperBotV2.tsx BotRow shows no action for a stopped bot; DeploymentsPage.tsx:348 list hidden when `paperOnly` |
| High | An option seller pack (F1/F2) on an index from the quick form starts as a SINGLE BUY ATM option when the user has no defaults; a swing (daily) bot never gets a structure even with defaults | paperbot/model.ts:75 `option_position: "BUY"`; settings/tradingDefaults.ts:252 `!ctx.swing && isStructure(...)`; strategy_templates/packs.py:37 STRUCTURE_NOTE |
| Medium (owner decision) | A directional seller cannot run both sides on one strategy + symbol: one deployment = one structure (bull put on SHORT = no trade) and a second deployment is refused by the unique key | instruments/spreads.py:282 `structure_direction_ok`; models.py:543 |
| Medium | Pause reason shows "Paused: paused by user: paused from the Paper bot page"; the test uses a string the server never stores | deployments/routes.py:753 `f"Paused by user: {request.reason}"`; paperbot/model.ts:142 regex `^paused from`; PaperBotV2.test.tsx:112 |
| Medium | Signals on a phone (compact): no PAPER/LIVE chip and the button says only "Approve" | signals/SignalPartsV2.tsx:383 `compact ? "Approve"`, :397 `{!compact && (` (mode chip) |
| Low | Signals source filter has no "Signal generator"; those rows appear only under All sources | signals/feed.ts:93-98 SOURCES |


## 2. Markets, trading and terminal pages (code audit, group A)

### Today "/" (dashboard/DashboardV2.tsx)
| Sev | Finding | Proof |
|---|---|---|
| Low (perf) | The page polls 11 endpoints while it is open: 9 of them every 30 s, OI every 60 s, and the strip every 15 s while the market is open. The 30-day analytics summary, which changes only when a trade closes, is re-read every 30 s. | FE dashboard/useDashboardData.ts:121-131 `usePolled(winRateSummary, every(REFRESH_MS), enabled)`, `REFRESH_MS = 30_000` (:22) |

### Charts "/charts" (chart/ChartsPageV2.tsx, chart/useChartCandles.ts)
| Sev | Finding | Proof |
|---|---|---|
| Medium | Any error the server reports for a symbol is shown as "No prices for X … Check the symbol, or pick another one." That includes a broker exception, an end-of-day-only refusal and an entitlement reason. Only "budget used" gets its own text, so a broker failure on a valid symbol tells the user the symbol is wrong. | FE chart/useChartCandles.ts:150 `throw new Error(entry?.error === "budget used" ? "budget used" : "no bars")`; :81 `"no bars") return \`No prices for ${symbol} from your broker. Check the symbol…\``; BE market_data/candles_routes.py:483 `"error": f"{type(exc).__name__}: {exc}"`, :471 `"error": ent.reason` |

### Watchlist (watchlist/WatchlistPageV2.tsx)
| Sev | Finding | Proof |
|---|---|---|
| Low (perf) | The ready-made list groups are read twice on every visit: once by the page and once by the panel inside it. | FE watchlist/WatchlistPageV2.tsx:67 `systemListsApi.groups().then(...)`; chart/WatchlistPanel.tsx:110 `systemListsApi.groups().then(...)` |

### Option chain "/option-chain" (optionChain/*)
| Sev | Finding | Proof |
|---|---|---|
| Medium | With byob on, every 409 carries `X-Data-Tier: C`, including the case where the user has no usable session (for example a token that expired). The FE maps every 409 that has a tier to "not available live on your plan". A user whose session lapsed is therefore told it is a plan limit, and the server's actual reason is dropped. | FE optionChain/useOptionChain.ts:58-61 `return e.dataTier ? "plan" : "no-session"`; model.ts:43 `plan: "This symbol is not available live on your plan."`; BE market_data/entitlement.py:200-205 (no session → `DataTier.EOD, reason=_no_session_reason`); candles_routes.py:681 `raise _tier_c(ents)`; :151 `headers={"X-Data-Tier": ...}` |
| Medium | Once a chain is on screen, any later failure (no session, plan refusal, failed read) keeps the old numbers. Below them it shows only the generic "We could not load… We will try again in a moment." For no-session or plan, retrying will not fix it, and the old numbers are not marked as old. | FE optionChain/ChainLadderV2.tsx:68 `{problem && <p …>{TEXT.loadFailed}</p>}` (the specific texts are used only when `!live`, :40) |

### Sectors & indices "/markets" (markets/MarketsPageV2.tsx)
| Sev | Finding | Proof |
|---|---|---|
| Medium | The error text promises "We will try again in a moment", but the page never retries on its own. `useLoad` reads once and reads again only on the Try again button (nonce). The page also has no refresh at all while it stays open. | FE markets/MarketsPageV2.tsx:43-49 `useEffect(… load() …, [key, nonce])`; :425 `"We could not load the market view. We will try again in a moment."`; :280 the same for a list |

### Find setups "/screener" (pages/ScreenerPage.tsx)
No proven defect in the parts traced (load, validate, preview, run, batches, auto-refresh, error mapping).

### Custom scan "/screener/custom" (pages/ScannerPage.tsx, legacy page, not in PAGE_COMPONENTS_V2)
| Sev | Finding | Proof |
|---|---|---|
| Medium | Raw server exception text reaches the user, both as data warnings and as the run error. The FE adds the per-symbol `error` to warnings unchanged, and the BE fills it with `TypeError: …` style text. With no candles, the thrown error joins those texts, adds "empty response", and is shown via `errorSentence`, which passes plain Error messages through. | FE components/DataSource.tsx:146 `` warnings.push(`${symbol}: ${entry.error}`) ``, :149 `` throw new Error(`No candles from ${res.source.broker}: ${warnings.join("; ") \|\| "empty response"}`) ``, :171 `` `${symbol} chain: ${entry.error}` ``; ScannerPage.tsx:142 `setError(errorSentence(e))`, :152 `dataWarnings.map(…)`; api/errors.ts:129 `if (e instanceof Error) return e.message;`; BE candles_routes.py:483/:717 `f"{type(exc).__name__}: {exc}"` |
| Medium | Developer text is shown to users: the AI provider and model ids, and the prompt version. | ScannerPage.tsx:167 `· {aiPlan.provider}{aiPlan.model ? \`/${aiPlan.model}\` : ""} · {aiPlan.prompt_version}`; :336 `· {aiRead.provider}{aiRead.model …}` |
| Medium | The structure filters run with a fixed swing window of 3 that the user cannot see or change. This is a number in reading logic that is not in the user's settings (CLAUDE.md §5). | ScannerPage.tsx:135 `swing_window: 3,` |
| Medium | The page says "Not a ranking", but it shows a 0-100 score per symbol coloured green or red, from an object named `ranked`. That is a performance-style rating against §3. | ScannerPage.tsx:331 `Not a ranking or advice`; :339 `` r.score >= 70 ? "text-up" : r.score >= 50 ? "text-fg" : "text-down" `` |
| Low | The page opens pre-filled with a fixed platform symbol list and an unknown-regime placeholder "?". The title and description use jargon ("Market Scanner … every configured filter"), and the nav calls the page "Custom scan". | ScannerPage.tsx:61 `useState("NIFTY, BANKNIFTY, RELIANCE, TCS, HDFCBANK, INFY")`; :339 `regime {r.regime ?? "?"}`; :150 `title="Market Scanner"`; shell/navModel.ts:116 `"Custom scan"` |

### Terminal (terminal/TerminalPageV2.tsx, PanelsV2.tsx)
| Sev | Finding | Proof |
|---|---|---|
| High | P&L on the Terminal position cards is wrong whenever another member of the same organisation holds the same symbol. The positions are the caller's own (`mine=true`), but the exposure is organisation-wide. The price is worked out as the organisation's notional for the symbol divided by the caller's quantity only, so both the price and the P&L come out inflated. | FE terminal/api.ts:55 `positions: () => fetcher<TradeRecord[]>("/positions?mine=true")`; TerminalPageV2.tsx:156 `api.portfolioExposure(true)`; PanelsV2.tsx:289 `heldRows(list, exposure)`; dashboard/model.ts:235-236 `const qty = positions.filter((p) => p.symbol === row.symbol)…; out[row.symbol] = row.notional / qty`; BE portfolio/routes.py:39 `trades = await engine.open_trades(session, user.tenant_id)`; portfolio/engine.py:55 `return abs(float(trade.quantity) * price)`, :93 `row.notional += notional` |
| Low | Cancelled orders are listed under the "Rejected" group. | FE terminal/api.ts:74-79 (`orderGroup` returns "rejected" for any status not open or executed, including CANCELLED); PanelsV2.tsx:370 `{ key: "rejected", label: "Rejected" }` |

### My trades "/positions" (trading/PositionsV2.tsx)
| Sev | Finding | Proof |
|---|---|---|
| Medium | A failed journal note or tag save does nothing visible. The error is swallowed, and the user's typed note is lost without any message. | trading/PositionsV2.tsx:674 `void api.updateTradeJournal(…).then(onSaved, () => undefined);` |
| Low (perf) | The page polls the whole trade history every 60 s, and the endpoint has no limit. The same unbounded read is also polled by Orders (every 60 s) and Money (every 60 s). | FE PositionsV2.tsx:104 `usePolled(api.listTrades, every(REFRESH_MS * 2), …)`; OrdersV2.tsx:31; PortfolioV2.tsx:33; BE trading/routes.py:111-112 `select(TradeRecord).where(TradeRecord.tenant_id == …).order_by(…)` (no `.limit`) |
| Low | The page title does not match the nav. The nav says "My trades" and "Money", but the pages are titled "My positions" and "My portfolio". | PositionsV2.tsx:189,230 `<PageTitleV2 title="My positions"`; PortfolioV2.tsx:51 `title="My portfolio"`; shell/navModel.ts:58,123 |

### Orders "/positions/orders" (trading/OrdersV2.tsx)
| Sev | Finding | Proof |
|---|---|---|
| Low | The page is named "Orders", but it is built only from filled trades (`/trades`). Refused and failed orders, which the BE keeps in `/orders`, never appear. | OrdersV2.tsx:31 `usePolled(api.listTrades, …)`; :24-26 comment "Read-only: the same /trades answer"; BE trading/routes.py:551-553 `/orders` "every paper/live execution attempt, rejected or filled" |

### Money "/positions/money" (trading/PortfolioV2.tsx)
Only the unbounded `/trades` poll listed under My trades, and the page title mismatch.

### Plan an options trade "/option-chain/plan" (pages/OptionsBuilderPage.tsx, legacy page)
| Sev | Finding | Proof |
|---|---|---|
| Medium | Lots are hard-coded to 1 for templates and new legs. The user's own lots setting (Settings → My trading defaults has "Lots") is never read. This breaks §3: "Lots … come only from the user's own settings". | OptionsBuilderPage.tsx:161 `lots: 1,`; :179 `lots: 1, lot_size: lotSize`; settings/TradingDefaultsV2.tsx:241 `label="Lots"` |
| Medium | With the width field blank, the template strike width defaults to 2 strike steps. That is a platform rule for strikes. The file's own header says "nothing is defaulted". | OptionsBuilderPage.tsx:95 `const width = positive(widthText) ?? (step ? 2 * step : null);`; :220 `hint: "default: 2 strike steps"` |
| Medium | "Place as basket", the page's primary button, can never place anything. The BE always sends `placeable: False`, so the dialog's button is permanently disabled, and it has no onClick in any case. | optionsBuilder/BasketDialog.tsx:35 `<Button variant="primary" disabled={!basket?.placeable} …>Place {n} legs in PAPER</Button>`; BE options_builder/routes.py:290 `"placeable": False` |
| Low | Jargon and unfinished features are shown to users: "from the instrument master", and an "Adjustments and compare / Coming later" section. The title "Options Builder" does not match the nav "Plan an options trade". | OptionsBuilderPage.tsx:218-219 `hint: "from the instrument master"`; :282 `Coming later`; :209 `title="Options Builder"` |

### Analyze a chart "/chart-analyze" (chartAnalyze/*)
| Sev | Finding | Proof |
|---|---|---|
| Low | The history time uses the device's time zone, not IST, unlike the shared `formatDateTimeIST`. | chartAnalyze/ChartAnalyzePageV2.tsx:71 `d.toLocaleString("en-IN", { day: "numeric", … })` (no `timeZone`); used at :476 |
| Low | The no-fit message sends the user to the "Deployments page". In the nav, that page is called "Paper bot". | BE chart_analyze/strikes.py `NONE_FIT = "… Strike settings live on the Deployments page."`; FE shell/navModel.ts:57 `"Paper bot"` |

### Needs live check
1. Whether flag `byob_data_tier` is ON for any tenant. If it is, the Option chain "plan" message above is live. Check: platform flags table, `byob_data_tier`.
2. Whether any tenant has more than one user with open positions in the same symbol. If so, the Terminal P&L is wrong for them today. Check: `SELECT tenant_id, symbol, COUNT(DISTINCT user_id) FROM trade_records WHERE exit_time IS NULL GROUP BY 1,2 HAVING COUNT(DISTINCT user_id) > 1`.
3. The size of `/api/trades` per tenant (rows and response KB), to size the 60 s unbounded poll on My trades, Orders and Money.
4. Custom scan AI read: whether `next_step`, `thesis` or `risks` text ever contains buy, sell, target or stop words. Those fields are rendered unguarded at ScannerPage.tsx:340-342, unlike Analyze a chart, which runs `guardResult`.

## 3. Insights, strategies, settings and account pages (code audit, group B)

### Risk limits (/risk-management) — trading/RiskV2.tsx

| Sev | Finding | Proof |
|---|---|---|
| High | "Not set" limits (null since DEF-0) cannot be kept: drafts become the text "null", every such field fails validation ("Enter a number."), so Save is refused until EVERY one of the 13 fields holds a number. | fe/trading/RiskV2.tsx:47 `String(c[f.key])`; fe/trading/model.ts:518-520 `if (text === "") return "Enter a number."` / `if (!Number.isFinite(v)) return "Enter a number."`; RiskV2.tsx:123 `if (ALL_FIELDS.some((f) => problemOf(f) !== null))`; types.ts:443-456 fields are `number \| null` |
| Medium | Forced to type a number, a trader who types 0 to mean "no limit" blocks every new entry: the save sends 0 and the engine treats 0 as a real limit (`0 >= 0`). The field help never says 0 blocks. | RiskV2.tsx:128 `= Number(drafts[f.key])`; be/risk_engine/routes.py:110-112 stores as sent; be/risk_engine/risk_manager.py:69 `if cfg.max_trades_per_day is not None and state.trades_today >= cfg.max_trades_per_day` (same :71 open positions, :73 losses in a row) |
| Medium | The guardian sentence states a rule that is not set: with dd levels null it reads "The size is halved at –% … pause at –%". | RiskV2.tsx:216 `` `The size is halved at ${formatNumber(guardian.settings.dd_level_1_pct, 1)}% below your equity peak…` `` |

### Performance (/analytics) — pages/AnalyticsPage.tsx

| Sev | Finding | Proof |
|---|---|---|
| Medium | Performance tiles, By strategy / By symbol and "Live vs backtest" read the whole organisation's trades (every team member), while the page says "your" history and the Tax card on the same page and the Coach read only the user's own trades: numbers disagree for a team member. | be/trading/routes.py:451 `select(TradeRecord).where(TradeRecord.tenant_id == user.tenant_id)`; be/trading/degradation.py:66 same; vs be/tax/report.py:271 `TradeRecord.user_id == user_id`, be/ai/routes.py:748 `TradeRecord.user_id == user.id`; AnalyticsPage.tsx:130 `"Aggregated from your full persisted trade history"` |
| Medium | "Live vs backtest" counts PAPER trades as "Live trades / Live win rate" (no mode filter). In Phase 0 every row is PAPER. | degradation.py:66 (no `mode` filter); AnalyticsPage.tsx:133 `<th …>Live trades</th><th …>Live win rate</th>` |
| Medium | Raw strategy ids shown (e.g. `custom:12`, `plan_…`) in "By Strategy" (column "Key") and "Live vs backtest". | be/trading/analytics.py:73 `by_strategy=_group_stats(closed, lambda r: r.strategy_id)`; AnalyticsPage.tsx:75 `<th …>Key</th>`, :84 `{r.key}`, :136 `{r.strategy_id}`; id format be/custom_strategies/routes.py:60 `f"{CUSTOM_PREFIX}{record.id}"`, be/paper/ticket.py:294 `f"plan_{plan.setup.lower()}"` |
| Medium | Tax card opens on LIVE trades; in Phase 0 (PAPER only) it shows 0 trades until the user switches. | AnalyticsPage.tsx:14 `useState<string>("LIVE")` |
| Low | Developer wording in the header ("persisted", "Positions/Trade Journal"); title "Analytics" while nav says "Performance". | AnalyticsPage.tsx:130; shell/navModel.ts:92 `"Performance"` |
| Low | 8-column tax table and 8-column degradation table have no horizontal-scroll wrapper inside the Card: they overflow the card at 375 px. | AnalyticsPage.tsx:43 `<table className="w-full text-xs">` (8 `<th>` at :44), :133 (8 `<th>`); no `overflow-x-auto` in file |
| Low | Degradation endpoint: one DB query per strategy (N+1) and every closed trade loaded on each page open. | degradation.py:66 loads all, :71-72 `for strategy_id … await session.scalar(select(BacktestRunRecord)…)` |

### Journal (/analytics/journal) — pages/JournalPage.tsx

| Sev | Finding | Proof |
|---|---|---|
| Low | A non-503 load error leaves a skeleton forever (only a toast). Same in Weekly review and Settings tabs. | JournalPage.tsx:334-335 `.catch((e) => { if (…503) setOff(true); else toast.error(…) })` then :365 `!data ? <Skeleton …/>`; :186/:192 `if (!review) return <Skeleton …/>`; :252-253 |
| Medium | Developer reference shown to users: a design note they cannot open. | JournalPage.tsx:308 `"The defaults and the reasoning behind each one are in the discipline engine's design note."` |

### Coach (/analytics/coach) — CoachGuidePage + components/TradeCoach.tsx

| Sev | Finding | Proof |
|---|---|---|
| Medium | "By strategy" bars label rows with the raw strategy id. | components/TradeCoach.tsx:164 `labelOf={(g) => g.key}`; be/ai/coach.py:55 `"strategy": _get(t, "strategy_id")`, :194 groups by it |
| Low | Selected and unselected Period/Mode buttons use the same text and border colour (only a faint background differs). | TradeCoach.tsx:110 `days === d ? "border-border bg-surface-2 text-fg-muted" : "border-border text-fg-muted"` (same :112) |

### Strategies (/strategies) — pages/StrategiesPage.tsx

| Sev | Finding | Proof |
|---|---|---|
| Medium | Raw JSON of parameters shown to every user, and it exposes platform stop/target defaults (`atr_mult_sl`, `target_rr`, `min_rr`) of the ready-made strategies (§3: SL/targets only from the user's own settings). | StrategiesPage.tsx:49 `{JSON.stringify(s.default_params, null, 2)}`; be/strategy_engine/combo_strategies.py:68 `"atr_mult_sl": 1.2, "target_rr": (1.5, 2.5), "min_rr": 1.2`, :96 used in `build_signal(…, p["target_rr"])`; indicator_strategies.py:31 |
| Low | No loading, empty or retry state: an empty grid while loading or when the list is empty. | StrategiesPage.tsx:14 `useState<StrategyInfo[]>([])`, :29-54 renders only the map |

### Build a strategy (/strategies/build) — pages/StrategyBuilderPage.tsx

| Sev | Finding | Proof |
|---|---|---|
| Medium | Delete has no error handling and no confirm: a failed delete is an unhandled rejection with no message. | StrategyBuilderPage.tsx:217-220 `async function handleDelete(id) { await api.deleteCustomStrategy(id); refresh(); }`; :341 one-click `Delete` |
| Medium | Developer text: internal id and parser internals. | :339 `` `(${s.config.timeframe}, id ${s.strategy_id})` ``; :274-275 `"A deterministic, rule-based parser - not a call to an external AI (no AI-provider credentials are configured)."` (also contradicts the `<Disclaimer kind="ai" />` at :270) |
| Low | Clearing a number box sends 0, not blank (period 0, ATR period 0, fixed value 0). | :76 `value: Number(e.target.value)`, :95 `period: Number(…)`, :403 `atr_period: Number(e.target.value)` |
| Low | Raw indicator codes in the picker (`PLUS_DI`, `OR_HIGH`, `PDH`, `DAY_OPEN`, `VOLUME_SMA`). | :19-20 `INDICATORS`, :86 `<option …>{ind}</option>` |

### Test a strategy (/strategies/test) — pages/BacktestPage.tsx

| Sev | Finding | Proof |
|---|---|---|
| Medium | Platform exit rule ON by default: "intraday" is pre-ticked, so every option test adds a 15:00 entry cut-off and a 15:15 square-off the user never set (§3 exits only from his settings). | BacktestPage.tsx:117 `useState(true)`; :509 `"intraday (no entries after 15:00, square-off 15:15 IST)"`; be/backtest/options_engine.py:397 `if reason is None and config.intraday … >= SQUARE_OFF_AT`, :450 |
| Medium | Strikes / legs / width pre-filled by the platform (Bull put spread, ATM, width 2, custom legs ATM short + OTM+2 long) — DEF-0 applied in Build but not here. | :95 `"BULL_PUT_SPREAD"`, :98 `"ATM"`, :100 `useState(2)`, :106-109 prefilled `customLegs` |
| Medium | The page claims the test uses "the same … exits and lot sizing a deployment uses", but it never sends the user's risk settings; the server runs with an empty RiskConfig. | :377 text; fe/api/client.ts:442 body has no `risk_config`; be/main.py:749 `risk_config = request.risk_config or _default_risk_config`, :493 `_default_risk_config = RiskConfig()` |
| Medium | Developer text in results: engine versions, run ids, raw param JSON, raw metric keys, raw strategy ids, worker internals, JSON grid input. | :578 `Options engine v{…engine_version}`; :646 `saved as run #${result.run_id}`; :655 options `net_pnl/expectancy/…`; :675 `{JSON.stringify(r.params)}`; :778 `{r.strategy_id}`; :780 `engine v{r.engine_version}`; :522 `"the worker records the chains of ACTIVE option deployments"`; :653/:77 JSON grid `'{"ema_fast": [9, 12], …}'` |
| Medium | Strategy list load has no catch: a failure leaves an empty picker and a disabled Run with no message (unhandled rejection). | :194-197 `api.listStrategies().then((list) => {…});` (no `.catch`) |
| Low | "Optimise" does nothing when a template was tested (uses `selected`, undefined for a template) though the button is enabled. | :84 `if (!selected \|\| chartCandles.length === 0) return;`; :657 `disabled={optBusy \|\| !result}` |
| Low | Literal text "undefined" in Max loss, "-%" for a missing win rate, "undefined to undefined" for coverage without dates. | :601 `: "undefined"`; :784 `{r.win_rate ?? "-"}%`; :523 `` `${c.from?.slice(0, 10)} to ${c.to?.slice(0, 10)}` `` with types.ts:306 `from: string \| null` |

### Ideas (/ideas) — ideas/IdeaLibraryV2.tsx
No defect proven (loading / failed / off states present; no buy-sell or price text in the page; idea settings carry no stop/target/lot keys — checked be/idea_library/ideas.json).

### AI Copilot (/copilot) — copilot/chat/CopilotChatV2.tsx
No defect proven (home error shown, per-turn error shown, strip polled with server delay and paused when hidden, no order action).

### News & events (/news-events) — pages/NewsEventsPage.tsx

| Sev | Finding | Proof |
|---|---|---|
| Medium | Feature-flag name shown to every user. | NewsEventsPage.tsx:45 `"off (feature flag news_feed)"` |
| Medium | Delete has no error handling (unhandled rejection, no message). | :145-148 `await api.deleteNewsEvent(id); refresh();` |
| Low | List-load and vote errors are written into the "Add a cited event" card (and not shown at all to a signed-out viewer); the feed-sources card hides its own load error (returns null before rendering it). | :122/:114 `setError(…)` shown only at :330 inside the form card (rendered only when `user`); :33-35 `.catch((e) => setError(…)); … if (!status) return null;` |
| Low | Unbounded list, re-fetched on every keystroke of the Symbol box; server loads every row then filters in Python. | :126-129 effect on `symbolFilter`; be/news_events/persistence.py:63-73 no `limit`, `records = [r for r in records if target in …]` |
| Low | `newsFeedStatus` requested twice per page open. | :33 and :98 |

### Company research (/fundamentals) — pages/FundamentalsPage.tsx

| Sev | Finding | Proof |
|---|---|---|
| Medium | Made-up sample inputs pre-filled and used for results: market price 300, DCF base revenue 1000 / net debt 300 / 50 shares, scores 60/60/60, technical score 70 LONG. "Compute Valuation" / DCF / Score run on them as is. | FundamentalsPage.tsx:93 `useState(300)`; :96-99 `base_revenue: 1000 … net_debt: 300, shares_outstanding: 50`; :102-107 `useState(60)` ×3, `useState(70)`, `"LONG"`; used at :232, :240, :249, :261 |
| Medium | Header says nothing is fetched from a live feed ("no SEBI/NSE/BSE credentials are wired in yet"), but the page offers "Add from NSE … Fetch" that pulls from NSE; also developer text. | :389 description; :416-419 `"Add from NSE (symbols)"`, title `"Creates the profile from NSE and pulls shareholding + announcements"` |
| Low | Company list and sector list loads have no catch (unhandled rejection; page shows "No companies yet"). | :137-142 `fundamentalsApi.listCompanies().then(…)` (no catch); :291 `fundamentalsApi.sectors().then(setSectors)` |
| Low | "-%" shown for a missing promoter holding. | :470 `{selectedCompany.promoter_holding_pct ?? "-"}%` |

### Settings index / AI / Appearance / Help / Advanced — settings/SettingsIndexV2.tsx, SettingsSectionV2.tsx

| Sev | Finding | Proof |
|---|---|---|
| Medium | Settings → Alerts ("More ways to get alerts", not Advanced) shows developer text and SMTP / DLT internals to retail users. | SettingsSectionV2.tsx:122-124 `<AlertChannelsCard ownBotOnly part="channels" />`; components/AlertChannelsCard.tsx:239-242 `"The trading worker raises CRITICAL alerts (… deployment auto-paused …)"`; :356-359 `"DLT entity id"`, `{dlt_template_id} {dlt_entity_id} {sender_id}` |
No other defect proven on the index, Appearance or Help.

### My trading defaults (/settings/trading-defaults) — settings/TradingDefaultsV2.tsx

| Sev | Finding | Proof |
|---|---|---|
| Low | Defaults and liquidity limits are saved one after the other; if the second save fails after the first succeeded, the note says "could not be saved" though the defaults were saved. | TradingDefaultsV2.tsx:63-66 `saveDefaults(…)` then `saveLimits(…)`; :76 catch → `"Your defaults could not be saved just now."` |

### Setup (/settings/setup) — setup/StepsV2.tsx, setup/model.ts

| Sev | Finding | Proof |
|---|---|---|
| Medium | One tap "Start a paper bot" creates a deployment whose symbol, option side (BUY), expiry (NEAREST), strike (ATM, offset 0) and holding (INTRADAY) are chosen by the platform, and the built-in strategy's own stop/target params apply (§3). Card text uses "Buys …" wording. | setup/model.ts:301-308 `{ instrument_kind: "OPTION", option_position: "BUY", expiry_rule: "NEAREST", strike_rule: "ATM", strike_offset: 0 }`, `holding: … "INTRADAY"`; :291-297 SUGGESTIONS `"Buys RELIANCE on a pullback…"`, `"Buys a NIFTY 50 option…"`; StepsV2.tsx:303 `api.createDeployment(paperDeployment(s))` |

### Account (/settings/account) — pages/AccountPage.tsx

| Sev | Finding | Proof |
|---|---|---|
| Low | Sessions have no loading or error state: "Active sessions (0) · No session data." while loading and on failure. | AccountPage.tsx:35 `useState<SessionInfo[]>([])`, :47 `.catch(() => setSessions([]))`, :168-169 |
| Low | Support-access times use the device time zone, not IST like the rest of the page. | :219 `toLocaleString("en-IN", { dateStyle: "medium", timeStyle: "short" })` (no `timeZone`) vs :177 `formatDateTimeIST` |
| Low | A long e-mail has no wrap/truncate in the header row. | :94-96 `<div><div …>{user.email}</div></div>` (no `min-w-0`, `truncate` or `break-all`) |

### Broker (/broker) — broker/BrokerPage.tsx
No defect proven (loading, failed + retry, empty, confirm on remove, phone layout handled at :58-59).

### Notifications (/notifications) — pages/NotificationsPage.tsx

| Sev | Finding | Proof |
|---|---|---|
| Medium | Raw event codes shown on every row. | NotificationsPage.tsx:100 `<span className="… uppercase …">{n.event_type}</span>` |
| Low | No loading state: "No notifications yet." shows while the list loads. | :29 `useState<NotificationEntry[]>([])`, :85-86 |
| Low | Every live event re-fetches all three lists (feed + two Vision report reads). | :39-44 `refresh()` does 3 requests; :47 `useLiveEvents(["notifications"], refresh)` |

### Team (/team) — pages/TeamPage.tsx

| Sev | Finding | Proof |
|---|---|---|
| Medium | LIVE wording shown in Phase 0 (PAPER only): the page never uses `useLiveHidden`, unlike Settings (B3/SR-9). | TeamPage.tsx:262 `"for LIVE deployments, broker credentials…"`, :278 `"LIVE deployments without their own policy use this."`, :324 `"Paper trading only on this plan - LIVE deployments need Pro or Business."`; no `useLiveHidden` import (:1-9) |
| Medium | Permission codes shown in monospace to an organisation owner (a USER-group person), contrary to the Account page's own rule "permission codes are for platform administrators only". | :237 `<span className="font-mono text-fg">{c.scope}</span>`; AccountPage.tsx:131 comment |
| Low | Remove member is one click with no confirm. | :209 `onClick={() => act(…, () => api.removeMember(m.id))}` |
| Low | Members (5 columns + action buttons) and invites tables have no phone layout / scroll wrapper. | :182, :164 `<table className="w-full text-xs …">`; no `overflow-x-auto` in file |

### Needs live check (cannot be proven from code)
1. Risk limits: open /risk-management on the live site with any limit "Not set" and press Save limits — expect "Fix the values marked below". Also check the saved `risk_settings` row for 0 values in max_trades_per_day / max_open_positions / max_consecutive_losses (they block every entry).
2. Analytics scope: does Abhi's organisation have more than one member? (Only then do Performance vs Coach/Tax numbers differ.)
3. Which strategy ids exist in his trade history (raw `custom:N` / `plan_…` visibility on Performance and Coach).
4. Flag `ai_one_key`: if OFF, Settings → AI shows components/AiProviderCard.tsx with operator/token-price text (:78, :88-89, :118) — developer text outside Advanced.
5. News feed table size (unbounded list render on /news-events).
6. Flags `discipline_engine`, `idea_library`, `strategy_templates`, `backtest_optimizer` states (decide whether Journal / Ideas / template-test / Optimise paths are reachable for users).

## 4. Built-in (default) strategies - backend/app/strategy_engine (13 strategies; ready-made on the Paper bot form: orb_15m_5m, vwap_supertrend_5m, swing_ema_pullback_d)
Checked: entry logic of all 13, closed-bar feed (worker uses `closed_frames`, AUD-V1 - OK), swing SHORT on cash shares (skipped, trading_worker.py:1777 - OK), indicator formulas (EMA, Wilder RSI / ATR / ADX, Supertrend - standard).
| Sev | Finding | Proof |
|---|---|---|
| Medium | "Why this signal" is empty or nearly empty for built-in signals: the feed keeps only reason lines without digits (signal_language_v2 ON), and the built-in reasons carry indicator periods and prices. Run on the real reason formats: Swing EMA pullback -> no line; ORB -> only "no volume ... skipped"; VWAP + Supertrend -> "Supertrend flipped bullish"; MTF -> "Price pulled back into fast EMA zone". | signals_feed/service.py:268-273 `shown_lines(..., "signal_own")`; reasons e.g. swing_strategies.py:74-75 `f"Daily uptrend: EMA{p['ema_fast']} {f:.2f} ..."`, combo_strategies.py:224 `f"First close above the opening range high {r_high:.2f}"` |
| Medium (rule §3 / §5) | Every built-in strategy fixes its own stop and targets (`atr_mult_sl`, `target_rr`, `atr_buffer`, ORB stop at the range middle) and its indicator periods; a deployment has no field to change them, so the user cannot set them (§5: "Generic templates may use indicator parameters as user settings"). Same item as REV-1 decision D2. | e.g. mtf_strategy.py:37-40 `"atr_mult_sl": 1.2 ... "target_rr": (1.5, 2.5), "min_rr": 1.2`; base.py:69 `target_rr ... = (1.5, 2.5)` default; deployments/routes.py DeploymentCreateRequest has no params field; custom_strategies/resolver.py `registry.get(strategy_id)` |
| Medium | The `min_rr` "filter" can never act: it compares `rr1` = the strategy's own fixed first target multiple with a fixed `min_rr`, and every built-in has target_rr[0] >= min_rr (1.5 vs 1.2; 1.2 vs 1.2; 2.0 vs 1.5). It is shown to users as a reward:risk filter on the Strategies page. | base.py:93-96 `if min_rr ... and rr1 < float(min_rr)`; values in each default_params |
| Medium | VWAP + Supertrend on an index (the ready-made pick is shown with NIFTY 50) uses a running mean of the typical price, not a VWAP (indices have no volume), but its reasons and name say "VWAP". | combo_strategies.py:38-48 `vwap.fillna(running_mean)`; :176 `f"Close {close:.2f} above VWAP {vw:.2f}"`; paperbot/model.ts:23 READY `vwap_supertrend_5m` with `"NIFTY 50"` |
| Low | The ready-made Swing EMA Pullback line says only "Waits for a pullback inside an uptrend", but the strategy also signals the downtrend mirror (taken on an option / future; skipped on shares). | paperbot/model.ts:24; swing_strategies.py:79-84 |
| Low | RSI returns 100 when price did not move at all over the period (no gains and no losses), so a flat stretch reads as overbought. | indicators/momentum.py:18 `result.where(avg_loss != 0, 100.0)` |
| Info | None of the 13 uses the owner's method (trend -> buyer/seller area -> corrective pullback -> commitment candle; RSI divergence only): they are EMA / RSI level / ADX / MACD / Bollinger / Supertrend / opening-range / 20-day-breakout rules. The owner's method lives only in templates F (seller packs over D1/D3/A2/E3) and G (waves). | strategy_engine/*.py |

## 5. Strategy test (owner order: "test all predefined strategy on multiple index and stocks")
Method: ATP's own `run_backtest` + `TemplateStrategy` (no strategy logic re-written), default settings, one lot, the paper
broker's 0.02 % slippage. Live NSE rules added by the test harness because the underlying backtest does not have them: no
new entries from 15:00 IST and square-off at 15:15 IST. Data (private trade-data repo): 1-minute index prices BANKNIFTY
23 Jul-19 Oct 2026 and NIFTY 1 Jul-8 Oct 2026 (2 bad ticks removed); BANKNIFTY daily 2016-2026 for the daily strategies.
Not tested: stocks and other indices - no data reachable from the review session (the live site's own backtest with the
owner's broker is the way to run them). Charts, full results and scripts: trade-data review/atp_strategy_tests/2026-10-30.
Result unit: R = the strategy's own stop distance; "gross" = before entry slippage.

| Group | Result |
|---|---|
| 11 intraday built-ins | All 11 lose after slippage on both indices except MTF 5m/60m on NIFTY (+0.01R, 6 trades). Before slippage they sit between -0.16R and +0.18R per trade (leaving out the two 1-trade MTF 5m runs on BANKNIFTY): no edge that survives costs. EMA + RSI 1m: 887 / 1056 trades in about 3 months, -0.34R / -0.38R per trade (gross -0.01R / +0.04R) - the cost decides it. |
| Ready-made on the Paper bot form | Opening range 5m: -0.22R (65 tr) / -0.18R (86 tr). VWAP + Supertrend 5m: -0.11R (82) / -0.06R (97). Swing EMA pullback (daily BANKNIFTY 10 y): +0.12R, 83 trades, PF 1.18, worst run -11.4R. |
| 2 daily built-ins | Swing EMA pullback +0.12R (83 tr, PF 1.18); Swing breakout +0.15R (103 tr, PF 1.23) - the only positive results with a sample above 80 trades. |
| 43 templates, 15 min | No template is positive on both indices with 30+ trades each. Positive on both but small samples: B3 Channel breakout (+0.06R / 11 tr, +0.03R / 13 tr). F1 Directional seller pack = D1 Supertrend flip (by design): -0.01R / -0.05R. |
| Zero trades | All 11 wave templates (G1-G11) on both indices; B9 Volume breakout and E5 Session VWAP (an index has no volume); C3, C4; B11 on daily BANKNIFTY. Whether the G templates need more higher-timeframe (daily) history than 3 months is NOT proven - needs a check. |

Engine facts found by the test:
| Sev | Finding | Proof |
|---|---|---|
| Medium | Backtest != live for intraday: the underlying backtest has no 15:00 no-new-entry rule and no 15:15 square-off (only a user time exit), so an intraday strategy's test can hold overnight, which live never does. | backtest/engine.py run_backtest (no holding / square-off); backtest/routes.py:176 rules only from the request; workers/trading_worker.py:103 `SQUARE_OFF_AT = dtime(15, 15)` |
| Low | On the bar where a time exit fires, the time exit is taken at the bar close before the stop is checked, so a stop hit inside that bar can exit beyond the stop. | backtest/engine.py: `update.time_exit_reason` -> outcome before `fill.exit(... stop ...)` |
