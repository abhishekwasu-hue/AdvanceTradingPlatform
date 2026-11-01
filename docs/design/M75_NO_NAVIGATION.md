# M75 rule 1 - no click-navigation (inventory and conversion design)

Owner order M75 (docs/owner-orders/ATP_ORDER_M75.md), rule 1, permanent and app-wide: a click NEVER navigates the
user away from the tab he is working in. Every facility opens IN PLACE - side panel, drawer, bottom sheet, modal or
split view - and closes back to the same state. Deep links still work when opened directly. CW-8: a no-navigation test
across the app (every converted button keeps the URL path).

Phase 1 (this PR): this inventory, the shared design, the PR split, and a CI guard
(`frontend/src/ux/noNavigationGuard.test.ts`) whose baseline is today's count, so the count can only go down. No site
is converted in this PR.

## 1. How the inventory was made

The guard's scanner is the inventory: every row below is one site it counts in app source (tests, stories and fixtures
excluded). `M75_NAV_DUMP=<file> npx vitest run src/ux/noNavigationGuard.test.ts` writes the same list as JSON.
It counts:

| Kind | Shape counted | Not counted |
|---|---|---|
| `navigate` | `navigate(...)` from `useNavigate()` | a call with `replace: true` (address clean-up, a canonical address, clearing handed-over state) |
| `Link` | `<Link ...>` | - |
| `a-href` | `<a href=...>` to an app route, also one opened in a new browser tab | `#` anchors, `mailto:`, an external site in a new tab |
| `location.*`, `window.open`, `history.pushState` | `location.assign/replace(...)`, `location.href = ...`, `window.open(...)` | `location.reload()`, a download (`a.href = blobUrl`) |
| `to-prop` | an inline destination handed to a renderer: `more={{ ..., to }}`, `action={{ ..., to }}` | - |
| `to-item` | a toolbar / menu / attention item `{ id: "...", ..., to }` | - |

A renderer (a card's "more" link, the chart toolbar, the attention list, the Ask AI sheet, help articles) is counted
once where it draws the `<Link>`, and each inline destination handed to it is counted where it is written. Some
destinations live in plain model files (`setup/goLive.ts`, `setup/getStarted.ts`, `support/helpCentre.ts`,
`ui/dataPages.ts`, `dashboard/model.ts` attention items, `tours/content/*`): they become in place when their renderer
switches to the shared `<InPlaceLink>` (section 4), because the host resolves any route.

The order's examples, located: Copilot "Open chart" = `copilot/chat/AnswerV2.tsx:154` and
`copilot/tabs/IdeaBuilderTab.tsx:473`; Terminal chart click = `chart/EmbeddedChartV2.tsx:93` ("Open in Chart tab", on
by default in the Terminal chart panel `terminal/PanelsV2.tsx:87`, the Signals detail and the Copilot answer); Ask AI =
`shell/askAiEntry.tsx:99,102` drawn by `shell/AskAiSheetV2.tsx:67`; ticket links = the "Change limit" links
`fno/OrderTicketV2.tsx:493` and `fno/BasketPanelV2.tsx:775`; the bell = `shell/TopBarV2.tsx:254` (classic
`App.tsx:138`). The notifications list itself has no links today.

## 2. Exclusions (not counted), with reasons

| Code | What | Why it stays a navigation | Where (guard lists) |
|---|---|---|---|
| NAV | Sidebar, phone bottom bar, More sheet, logo, account avatar, command bar (Ctrl-K), sub-tab bars (shared `SubTabs`, `PageTabsV2`, the Copilot tab bar, the symbol hub tabs), the Settings tab's own menu and its search, the "page not found" / "coming soon" way home | Choosing a tab is what navigation is for; the order excludes it | `EXCLUDED_FILES`, `EXCLUDED_SITES` |
| AUTH | Sign in / sign out / session expiry, Google sign-in return, the sign-up consent field, admin "view as" start / stop (switches the signed-in identity), the first-sign-in step of a new account | Identity changes reload the app's state on purpose | same |
| EXTERNAL | Broker login pages (OAuth), Telegram, checkout, key consoles, help pages of brokers, news sources | Another site; it opens in a new browser tab and the app tab stays as it was | auto (new-tab rule) + list |
| PUBLIC | The signed-out public website, the legal pages and links between legal documents, the public status page | Not an app tab | `EXCLUDED_FILES` |
| DEEP | A deep link opened from outside (notification tap in the phone app, app links) | This is "opened directly"; the order keeps deep links | `native/deepLinks.ts` |
| REDIRECT | An old address forwarded on load (`ChartWindow`, `ChartWorkspace`) and every `replace: true` clean-up | Not a click | list + auto |
| SAME-TAB | The Chart tab changing its own symbol (`chart/ChartsPageV2.tsx` symbol search / watchlist pick) | The user stays on the Chart tab; the symbol is part of its address | list |
| ANCHOR | `#` links inside a page; downloads | Same page | auto |

## 3. Inventory: 141 sites by area

| Area | Sites | PR |
|---|---|---|
| Copilot | 19 | NAV-2 |
| Terminal / charts | 18 | NAV-3 |
| Option chain (F&O) | 14 | NAV-4 |
| Signals / Positions / Orders | 27 | NAV-5a (signals, bots, ideas, journal, scanner: 17), NAV-5b (positions, orders, portfolio, risk: 10) |
| Today / shell | 36 | NAV-6a (Today cards and attention: 16), NAV-6b (top bar, help, tours, notices, broker gate and prompt, status, legal banner: 20) |
| Settings | 26 | NAV-7 |
| Admin | 1 | NAV-8 |
| Total | 141 | guard BASELINE = 141 |

"(classic UI)" marks a site in the classic shell (`App.tsx`, `src/pages/*`): 10 sites; see question M75N-3.


### Copilot (19)

| Site (file:line) | Source | Target | What the user wants | In-place pattern |
|---|---|---|---|---|
| copilot/chat/AnswerV2.tsx:150 | copilot/chat/AnswerV2 | `answer.action.to` | renderer: the destination comes from data | `<InPlaceLink to>`: the host opens the panel registered for that route |
| copilot/chat/AnswerV2.tsx:154 | copilot/chat/AnswerV2 | `chartTabPath(chart.symbol,` | see the chart of this symbol | Chart side panel: `EmbeddedChartV2` in `Sheet` (right on desktop, bottom on phone) |
| copilot/chat/ChartTurnV2.tsx:53 | copilot/chat/ChartTurnV2 | `"/chart-analyze"` | analyze a chart image | Copilot split view: the Analyze panel beside the chat (same component as /chart-analyze) |
| copilot/chat/CopilotChatV2.tsx:100 | copilot/chat/CopilotChatV2 | `"/copilot/analyze"` | analyze a chart image | Copilot split view: the Analyze panel beside the chat (same component as /chart-analyze) |
| copilot/chat/CopilotChatV2.tsx:173 | copilot/chat/CopilotChatV2 | `home.ai.settings` | connect an AI key | Modal: `ConnectAiPanelV2` in `Dialog` |
| copilot/chat/CopilotChatV2.tsx:190 | copilot/chat/CopilotChatV2 | `home.ai.settings` | connect an AI key | Modal: `ConnectAiPanelV2` in `Dialog` |
| copilot/chat/CopilotChatV2.tsx:267 | copilot/chat/CopilotChatV2 | `link.to` | renderer: the destination comes from data | `<InPlaceLink to>`: the host opens the panel registered for that route |
| copilot/chat/CopilotChatV2.tsx:278 | copilot/chat/CopilotChatV2 | `copilotPath(tab)` | continue in another Copilot tool | Copilot split view: the tool panel beside the chat (state kept) |
| copilot/tabs/AskCopilotTab.tsx:92 | copilot/tabs/AskCopilotTab | `pathFor("coach"` | continue in another Copilot tool | Copilot split view: the tool panel beside the chat (state kept) |
| copilot/tabs/AskCopilotTab.tsx:95 | copilot/tabs/AskCopilotTab | `copilotPath(tabForAction(reply.action.tab` | continue in another Copilot tool | Copilot split view: the tool panel beside the chat (state kept) |
| copilot/tabs/IdeaBuilderTab.tsx:69 | copilot/tabs/IdeaBuilderTab | `"/strategies/build"` | build a strategy from the idea | Copilot split view: the strategy builder beside the chat |
| copilot/tabs/IdeaBuilderTab.tsx:344 | copilot/tabs/IdeaBuilderTab | `copilotPath("strategy-lab"` | continue in another Copilot tool | Copilot split view: the tool panel beside the chat (state kept) |
| copilot/tabs/IdeaBuilderTab.tsx:473 | copilot/tabs/IdeaBuilderTab | `chartWindowUrl(symbol` | see the chart of this symbol | Chart side panel: `EmbeddedChartV2` in `Sheet` (right on desktop, bottom on phone) |
| copilot/tabs/NewsRadarTab.tsx:95 | copilot/tabs/NewsRadarTab | `pathFor("news-events")` | read the news calendar | Drawer: the news list in `Sheet` |
| copilot/tabs/WatchtowerTab.tsx:80 | copilot/tabs/WatchtowerTab | `signalsLinkFor(entries)` | see the signals | Side panel: the signals list in `Sheet` |
| copilot/tabs/lab/DraftsPanel.tsx:73 | copilot/tabs/lab/DraftsPanel | `copilotPath("idea-builder"` | continue in another Copilot tool | Copilot split view: the tool panel beside the chat (state kept) |
| shell/AskAiSheetV2.tsx:67 | shell/AskAiSheetV2 | `o.to` | renderer: the destination comes from data | `<InPlaceLink to>`: the host opens the panel registered for that route |
| shell/askAiEntry.tsx:99 | shell/askAiEntry | `"/chart-analyze" ` | analyze a chart image | Copilot split view: the Analyze panel beside the chat (same component as /chart-analyze) |
| shell/askAiEntry.tsx:102 | shell/askAiEntry | `copilotPath(symbol) ` | continue in another Copilot tool | Copilot split view: the tool panel beside the chat (state kept) |

### Terminal / charts (18)

| Site (file:line) | Source | Target | What the user wants | In-place pattern |
|---|---|---|---|---|
| chart/ChartToolbar.tsx:95 | chart/ChartToolbar | `a.to` | renderer: the destination comes from data | `<InPlaceLink to>`: the host opens the panel registered for that route |
| chart/ChartToolbar.tsx:104 | chart/ChartToolbar | `a.to` | renderer: the destination comes from data | `<InPlaceLink to>`: the host opens the panel registered for that route |
| chart/ChartsPageV2.tsx:213 | chart/ChartsPageV2 | `pathFor("chart-analyze") ` | analyze a chart image | Copilot split view: the Analyze panel beside the chat (same component as /chart-analyze) |
| chart/EmbeddedChartV2.tsx:93 | chart/EmbeddedChartV2 | `chartTabPath(sym` | see the chart of this symbol | Chart side panel: `EmbeddedChartV2` in `Sheet` (right on desktop, bottom on phone) |
| components/ProChart.tsx:570 | components/ProChart | `openUrl` | see the chart of this symbol | Chart side panel: `EmbeddedChartV2` in `Sheet` (right on desktop, bottom on phone) |
| markets/MarketsPageV2.tsx:111 | markets/MarketsPageV2 | `chartTabPath(m.symbol,` | see the chart of this symbol | Chart side panel: `EmbeddedChartV2` in `Sheet` (right on desktop, bottom on phone) |
| markets/MarketsPageV2.tsx:169 | markets/MarketsPageV2 | `to` | drill into a group, or see an index chart | Same tab: the drill-down becomes state; an index opens the chart side panel |
| markets/MarketsPageV2.tsx:193 | markets/MarketsPageV2 | `to` | drill into a group, or see an index chart | Same tab: the drill-down becomes state; an index opens the chart side panel |
| markets/MarketsPageV2.tsx:221 | markets/MarketsPageV2 | `to` | drill into a group, or see an index chart | Same tab: the drill-down becomes state; an index opens the chart side panel |
| markets/MarketsPageV2.tsx:272 | markets/MarketsPageV2 | `chartTabPath(m.symbol,` | see the chart of this symbol | Chart side panel: `EmbeddedChartV2` in `Sheet` (right on desktop, bottom on phone) |
| markets/MarketsPageV2.tsx:317 | markets/MarketsPageV2 | `chartTabPath(d.symbol)` | see the chart of this symbol | Chart side panel: `EmbeddedChartV2` in `Sheet` (right on desktop, bottom on phone) |
| pages/ChartWindow.tsx:96 | pages/ChartWindow (classic UI) | ``${window.location.pathname}?${new URLSearchParams({ workspa` | open the multi-chart workspace | In place: the workspace layout toggles inside the chart window |
| screener/EvidenceChart.tsx:180 | screener/EvidenceChart | `to` | see the chart of this symbol | Chart side panel: `EmbeddedChartV2` in `Sheet` (right on desktop, bottom on phone) |
| screener/EvidenceChart.tsx:181 | screener/EvidenceChart | `to` | see the chart of this symbol | Chart side panel: `EmbeddedChartV2` in `Sheet` (right on desktop, bottom on phone) |
| terminal/PanelsV2.tsx:139 | terminal/PanelsV2 | `BROKER_PATH` | connect / log in to the broker | Broker drawer: `ConnectPanel` in `Sheet` |
| terminal/PanelsV2.tsx:481 | terminal/PanelsV2 | `"/signals"` | see the signals | Side panel: the signals list in `Sheet` |
| terminal/PanelsV2.tsx:482 | terminal/PanelsV2 | `"/deployments"` | start a trading bot | Drawer: the bot start form (`PaperBotV2` form) in `Sheet` |
| watchlist/WatchlistPageV2.tsx:44 | watchlist/WatchlistPageV2 | `chartTabPath(item.symbol` | see the chart of this symbol | Chart side panel: `EmbeddedChartV2` in `Sheet` (right on desktop, bottom on phone) |

### Option chain (14)

| Site (file:line) | Source | Target | What the user wants | In-place pattern |
|---|---|---|---|---|
| fno/BasketPanelV2.tsx:304 | fno/BasketPanelV2 | `"/option-chain/plan"` | open the trade plan for the basket | Split view: the plan beside the basket (same tab) |
| fno/BasketPanelV2.tsx:775 | fno/BasketPanelV2 | `why.link` | change his own risk limits | Side panel: the Risk limits section (`RiskLimitsV2`) in `Sheet` |
| fno/ChainActionSheetV2.tsx:244 | fno/ChainActionSheetV2 | `chartLink(target,` | see the chart of this symbol | Chart side panel: `EmbeddedChartV2` in `Sheet` (right on desktop, bottom on phone) |
| fno/CockpitDeskV2.tsx:118 | fno/CockpitDeskV2 | `chartsPath(focus.spotSymbol) ` | see the chart of this symbol | Chart side panel: `EmbeddedChartV2` in `Sheet` (right on desktop, bottom on phone) |
| fno/CompareV2.tsx:327 | fno/CompareV2 | `adjust` | open the chain / basket for this underlying | Side panel: `ChainTradingV2` / `BasketPanelV2` in `Sheet` |
| fno/FuturesChainV2.tsx:114 | fno/FuturesChainV2 | `chartTabPath(row.tradingsymbol,` | see the chart of this symbol | Chart side panel: `EmbeddedChartV2` in `Sheet` (right on desktop, bottom on phone) |
| fno/FuturesChainV2.tsx:276 | fno/FuturesChainV2 | `basketHref(data.underlying,` | open the chain / basket for this underlying | Side panel: `ChainTradingV2` / `BasketPanelV2` in `Sheet` |
| fno/InsightsV2.tsx:188 | fno/InsightsV2 | `href` | open the chain / basket for this underlying | Side panel: `ChainTradingV2` / `BasketPanelV2` in `Sheet` |
| fno/InsightsV2.tsx:192 | fno/InsightsV2 | `trade` | open the chain / basket for this underlying | Side panel: `ChainTradingV2` / `BasketPanelV2` in `Sheet` |
| fno/OrderTicketV2.tsx:493 | fno/OrderTicketV2 | `why.link` | change his own risk limits | Side panel: the Risk limits section (`RiskLimitsV2`) in `Sheet` |
| optionChain/AskAiV2.tsx:187 | optionChain/AskAiV2 | `"/settings/ai"` | connect an AI key | Modal: `ConnectAiPanelV2` in `Dialog` |
| pages/OptionChainPage.tsx:103 | pages/OptionChainPage (classic UI) | `BROKER_PAGE_PATH` | connect / log in to the broker | Broker drawer: `ConnectPanel` in `Sheet` |
| pages/OptionChainPage.tsx:173 | pages/OptionChainPage (classic UI) | `links.ce` | see the chart of this symbol | Chart side panel: `EmbeddedChartV2` in `Sheet` (right on desktop, bottom on phone) |
| pages/OptionChainPage.tsx:176 | pages/OptionChainPage (classic UI) | `links.pe` | see the chart of this symbol | Chart side panel: `EmbeddedChartV2` in `Sheet` (right on desktop, bottom on phone) |

### Signals / Positions / Orders (27)

| Site (file:line) | Source | Target | What the user wants | In-place pattern |
|---|---|---|---|---|
| ideas/IdeaLibraryV2.tsx:178 | ideas/IdeaLibraryV2 | `screenerPathFor(idea.id,` | find setups / run a scan | Side panel: the scanner in `Sheet` |
| ideas/IdeaLibraryV2.tsx:198 | ideas/IdeaLibraryV2 | `adoptPath` | adopt the idea as a paper strategy | Drawer: the bot start form in `Sheet` |
| ideas/IdeaLibraryV2.tsx:203 | ideas/IdeaLibraryV2 | `testPath` | test the strategy on history | Drawer: the test form in `Sheet` |
| journal/SimilarSetupsV2.tsx:30 | journal/SimilarSetupsV2 | `journalLink(m)` | open the matching journal entry | Bottom sheet: `EntrySheetV2` |
| pages/ScreenerPage.tsx:545 | pages/ScreenerPage (classic UI) | `"/broker"` | connect / log in to the broker | Broker drawer: `ConnectPanel` in `Sheet` |
| paperbot/BotPositionsV2.tsx:62 | paperbot/BotPositionsV2 | `"/positions"` | see his open positions | Side panel: the positions list in `Sheet` |
| paperbot/PaperBotV2.tsx:600 | paperbot/PaperBotV2 | `BROKER_PATH` | connect / log in to the broker | Broker drawer: `ConnectPanel` in `Sheet` |
| paperbot/PaperBotV2.tsx:643 | paperbot/PaperBotV2 | `testPathFor({` | test the strategy on history | Drawer: the test form in `Sheet` |
| paperbot/PaperBotV2.tsx:768 | paperbot/PaperBotV2 | `DEFAULTS_PATH` | change his trading defaults | Side panel: `TradingDefaultsV2` in `Sheet` |
| screener/ScanAlertPanel.tsx:131 | screener/ScanAlertPanel | `"/settings/alerts"` | set up alert delivery | Modal: the alerts section in `Dialog` |
| signals/DayOneV2.tsx:130 | signals/DayOneV2 | `"/deployments"` | start a trading bot | Drawer: the bot start form (`PaperBotV2` form) in `Sheet` |
| signals/DayOneV2.tsx:301 | signals/DayOneV2 | `paperBotPathFor({` | start a trading bot | Drawer: the bot start form (`PaperBotV2` form) in `Sheet` |
| signals/SignalPartsV2.tsx:364 | signals/SignalPartsV2 | `chartTabPath(item.symbol,` | see the chart of this symbol | Chart side panel: `EmbeddedChartV2` in `Sheet` (right on desktop, bottom on phone) |
| signals/SignalsV2.tsx:137 | signals/SignalsV2 | ``/copilot/watchtower?action=${proposal}`` | continue in another Copilot tool | Copilot split view: the tool panel beside the chat (state kept) |
| signals/TryOnSymbolV2.tsx:151 | signals/TryOnSymbolV2 | `chartsPath(signal.symbol)` | see the chart of this symbol | Chart side panel: `EmbeddedChartV2` in `Sheet` (right on desktop, bottom on phone) |
| strategyFlow/StrategyFlowV2.tsx:550 | strategyFlow/StrategyFlowV2 | `paperBotPathFor({ template: template.id` | start a trading bot | Drawer: the bot start form (`PaperBotV2` form) in `Sheet` |
| strategyFlow/StrategyFlowV2.tsx:554 | strategyFlow/StrategyFlowV2 | `PAPER_PATH` | start a trading bot | Drawer: the bot start form (`PaperBotV2` form) in `Sheet` |
| trading/OrdersV2.tsx:73 | trading/OrdersV2 | `"/signals" ` | see the signals | Side panel: the signals list in `Sheet` |
| trading/PortfolioV2.tsx:79 | trading/PortfolioV2 | `"/screener" ` | find setups / run a scan | Side panel: the scanner in `Sheet` |
| trading/PortfolioV2.tsx:105 | trading/PortfolioV2 | `"/risk-management"` | change his own risk limits | Side panel: the Risk limits section (`RiskLimitsV2`) in `Sheet` |
| trading/PositionsV2.tsx:196 | trading/PositionsV2 | `CONNECT_BROKER_PATH` | connect / log in to the broker | Broker drawer: `ConnectPanel` in `Sheet` |
| trading/PositionsV2.tsx:272 | trading/PositionsV2 | `"/screener" ` | find setups / run a scan | Side panel: the scanner in `Sheet` |
| trading/PositionsV2.tsx:598 | trading/PositionsV2 | `adjust` | open the chain / basket for this underlying | Side panel: `ChainTradingV2` / `BasketPanelV2` in `Sheet` |
| trading/PositionsV2.tsx:602 | trading/PositionsV2 | `chartsPath(row.chartSymbol)` | see the chart of this symbol | Chart side panel: `EmbeddedChartV2` in `Sheet` (right on desktop, bottom on phone) |
| trading/PositionsV2.tsx:679 | trading/PositionsV2 | `"/positions/orders" ` | see his orders | Side panel: the orders list in `Sheet` |
| trading/RiskV2.tsx:308 | trading/RiskV2 | `RISK_LIMITS_PATH` | change his own risk limits | Side panel: the Risk limits section (`RiskLimitsV2`) in `Sheet` |
| trading/RiskV2.tsx:340 | trading/RiskV2 | `SETUP_PATH` | change his set-up answers | Side panel: the set-up steps in `Sheet` |

### Today / shell (36)

| Site (file:line) | Source | Target | What the user wants | In-place pattern |
|---|---|---|---|---|
| App.tsx:138 | App (classic UI) | `pathFor("notifications")` | read his notifications | Drawer (right) / bottom sheet: the notifications list |
| broker/BrokerPromptHost.tsx:63 | broker/BrokerPromptHost | `BROKER_PATH` | connect / log in to the broker | Broker drawer: `ConnectPanel` in `Sheet` |
| dashboard/BlocksV2.tsx:25 | dashboard/BlocksV2 | `"/positions" ` | see his open positions | Side panel: the positions list in `Sheet` |
| dashboard/BlocksV2.tsx:28 | dashboard/BlocksV2 | `"/screener" ` | find setups / run a scan | Side panel: the scanner in `Sheet` |
| dashboard/BlocksV2.tsx:66 | dashboard/BlocksV2 | `"/signals" ` | see the signals | Side panel: the signals list in `Sheet` |
| dashboard/BlocksV2.tsx:69 | dashboard/BlocksV2 | `"/signals" ` | see the signals | Side panel: the signals list in `Sheet` |
| dashboard/BlocksV2.tsx:70 | dashboard/BlocksV2 | `"/screener/custom" ` | find setups / run a scan | Side panel: the scanner in `Sheet` |
| dashboard/BlocksV2.tsx:134 | dashboard/BlocksV2 | `"/analytics" ` | see his performance | Side panel: the performance summary in `Sheet` |
| dashboard/CardV2.tsx:42 | dashboard/CardV2 | `more.to` | renderer: the destination comes from data | `<InPlaceLink to>`: the host opens the panel registered for that route |
| dashboard/CardV2.tsx:60 | dashboard/CardV2 | `action.to` | renderer: the destination comes from data | `<InPlaceLink to>`: the host opens the panel registered for that route |
| dashboard/DashboardV2.tsx:132 | dashboard/DashboardV2 | `CONNECT_BROKER_PATH` | connect / log in to the broker | Broker drawer: `ConnectPanel` in `Sheet` |
| dashboard/MyDayCardV2.tsx:16 | dashboard/MyDayCardV2 | `"/positions" ` | see his open positions | Side panel: the positions list in `Sheet` |
| dashboard/NeedsAttentionV2.tsx:106 | dashboard/NeedsAttentionV2 | `action.to` | renderer: the destination comes from data | `<InPlaceLink to>`: the host opens the panel registered for that route |
| dashboard/WeekTilesV2.tsx:44 | dashboard/WeekTilesV2 | `"/markets?period=week" ` | see the sector view | Side panel: the sectors table in `Sheet` |
| dashboard/WeekTilesV2.tsx:47 | dashboard/WeekTilesV2 | `"/markets?period=week"` | see the sector view | Side panel: the sectors table in `Sheet` |
| dashboard/model.ts:442 | dashboard/model | `BROKER_PATH` | connect / log in to the broker | Broker drawer: `ConnectPanel` in `Sheet` |
| dashboard/model.ts:489 | dashboard/model | `"/risk-man` | change his own risk limits | Side panel: the Risk limits section (`RiskLimitsV2`) in `Sheet` |
| dashboard/model.ts:492 | dashboard/model | `"/positions/money" ` | see his money and exposure | Side panel: the money / exposure summary in `Sheet` |
| learn/LearnPageV2.tsx:184 | learn/LearnPageV2 | `lesson.next.path` | renderer: the destination comes from data | `<InPlaceLink to>`: the host opens the panel registered for that route |
| legal/LegalAcceptanceBanner.tsx:56 | legal/LegalAcceptanceBanner | `legalPath(slug)` | read a legal document | Modal: the document (legal markdown) in `Dialog` |
| notices/OneTimeNoticesV2.tsx:17 | notices/OneTimeNoticesV2 | `notice.link` | change his own risk limits | Side panel: the Risk limits section (`RiskLimitsV2`) in `Sheet` |
| rhythm/DailyRhythmCardV2.tsx:31 | rhythm/DailyRhythmCardV2 | `section.link.to` | renderer: the destination comes from data | `<InPlaceLink to>`: the host opens the panel registered for that route |
| shell/TopBarV2.tsx:208 | shell/TopBarV2 | `help.showMe.href` | renderer: the destination comes from data | `<InPlaceLink to>`: the host opens the panel registered for that route |
| shell/TopBarV2.tsx:254 | shell/TopBarV2 | `"/notifications"` | read his notifications | Drawer (right) / bottom sheet: the notifications list |
| shell/TopBarV2.tsx:273 | shell/TopBarV2 | `accountPath` | see his account / plan | Modal: account summary in `Dialog` |
| status/DegradedNotice.tsx:22 | status/DegradedNotice | `"/status"` | check the service status | Modal: the status view in `Dialog` |
| support/HelpCentreV2.tsx:72 | support/HelpCentreV2 | `article.page` | renderer: the destination comes from data | `<InPlaceLink to>`: the host opens the panel registered for that route |
| support/SupportDialog.tsx:80 | support/SupportDialog | `l.url` | renderer: the destination comes from data | `<InPlaceLink to>`: the host opens the panel registered for that route |
| tours/TipsButtonsV2.tsx:23 | tours/TipsButtonsV2 | `"/"` | go back to Today (tips replay / wizard done) | Stay: close the dialog; the tips replay on the current tab |
| tours/TourHost.tsx:58 | tours/TourHost | `closed.go` | a tour's last step points to a page | In place: the tour opens the registered panel |
| ui/BrokerGate.tsx:172 | ui/BrokerGate | `CONNECT_BROKER_PATH` | connect / log in to the broker | Broker drawer: `ConnectPanel` in `Sheet` |
| ui/BrokerGate.tsx:285 | ui/BrokerGate | `CONNECT_BROKER_PATH` | connect / log in to the broker | Broker drawer: `ConnectPanel` in `Sheet` |
| ui/BrokerGate.tsx:333 | ui/BrokerGate | `CONNECT_BROKER_PATH` | connect / log in to the broker | Broker drawer: `ConnectPanel` in `Sheet` |
| ui/BrokerGate.tsx:362 | ui/BrokerGate | `CONNECT_BROKER_PATH` | connect / log in to the broker | Broker drawer: `ConnectPanel` in `Sheet` |
| ui/BrokerGate.tsx:372 | ui/BrokerGate | `CONNECT_BROKER_PATH` | connect / log in to the broker | Broker drawer: `ConnectPanel` in `Sheet` |
| ui/PageIntro.tsx:102 | ui/PageIntro | `showMe.href` | renderer: the destination comes from data | `<InPlaceLink to>`: the host opens the panel registered for that route |

### Settings (26)

| Site (file:line) | Source | Target | What the user wants | In-place pattern |
|---|---|---|---|---|
| aiKeys/ConnectAiPanelV2.tsx:385 | aiKeys/ConnectAiPanelV2 | `SETTINGS_PATH.ai` | connect an AI key | Modal: `ConnectAiPanelV2` in `Dialog` |
| components/AlertChannelsCard.tsx:258 | components/AlertChannelsCard | `OWN_BOT_PATH` | set up alert delivery | Modal: the alerts section in `Dialog` |
| components/AlertChannelsCard.tsx:279 | components/AlertChannelsCard | `ALERT_DELIVERY_PATH` | set up alert delivery | Modal: the alerts section in `Dialog` |
| components/DataSource.tsx:271 | components/DataSource | `"/broker"` | connect / log in to the broker | Broker drawer: `ConnectPanel` in `Sheet` |
| growth/DisciplineCardV2.tsx:101 | growth/DisciplineCardV2 | `"/risk-management"` | change his own risk limits | Side panel: the Risk limits section (`RiskLimitsV2`) in `Sheet` |
| pages/SettingsPage.tsx:45 | pages/SettingsPage (classic UI) | `BROKER_PAGE_PATH` | connect / log in to the broker | Broker drawer: `ConnectPanel` in `Sheet` |
| pages/SettingsPage.tsx:93 | pages/SettingsPage (classic UI) | `GO_LIVE_PATH` | see the Prepare-for-LIVE checklist | Side panel: `GoLiveChecklistV2` in `Sheet` |
| risk/RiskBlockV2.tsx:31 | risk/RiskBlockV2 | `block.link` | change his own risk limits | Side panel: the Risk limits section (`RiskLimitsV2`) in `Sheet` |
| settings/MyLimitsCardV2.tsx:26 | settings/MyLimitsCardV2 | `RISK_LIMITS_PATH` | change his own risk limits | Side panel: the Risk limits section (`RiskLimitsV2`) in `Sheet` |
| settings/SettingsIndexV2.tsx:213 | settings/SettingsIndexV2 | `GO_LIVE_PATH_V2` | see the Prepare-for-LIVE checklist | Side panel: `GoLiveChecklistV2` in `Sheet` |
| settings/SettingsSectionV2.tsx:193 | settings/SettingsSectionV2 | `"/terminal?view=classic"` | use the previous Terminal | In place: a view switch applied on the Terminal tab (no jump) |
| settings/TradeSettingsV2.tsx:81 | settings/TradeSettingsV2 | `section.today.path` | renderer: the destination comes from data | `<InPlaceLink to>`: the host opens the panel registered for that route |
| settings/TradingDefaultsV2.tsx:284 | settings/TradingDefaultsV2 | `RISK_LIMITS_PATH` | change his own risk limits | Side panel: the Risk limits section (`RiskLimitsV2`) in `Sheet` |
| setup/GetStartedCardV2.tsx:77 | setup/GetStartedCardV2 | `step.action.to` | renderer: the destination comes from data | `<InPlaceLink to>`: the host opens the panel registered for that route |
| setup/GetStartedCardV2.tsx:82 | setup/GetStartedCardV2 | `step.action.to` | renderer: the destination comes from data | `<InPlaceLink to>`: the host opens the panel registered for that route |
| setup/GoLiveChecklistV2.tsx:87 | setup/GoLiveChecklistV2 | `row.action.to` | renderer: the destination comes from data | `<InPlaceLink to>`: the host opens the panel registered for that route |
| setup/SetupChipV2.tsx:17 | setup/SetupChipV2 | `to` | renderer: the destination comes from data | `<InPlaceLink to>`: the host opens the panel registered for that route |
| setup/SetupPageV2.tsx:25 | setup/SetupPageV2 | `GO_LIVE_PATH_V2` | see the Prepare-for-LIVE checklist | Side panel: `GoLiveChecklistV2` in `Sheet` |
| setup/SetupWizardV2.tsx:113 | setup/SetupWizardV2 | `"/"` | go back to Today (tips replay / wizard done) | Stay: close the dialog; the tips replay on the current tab |
| setup/ShowGetStartedV2.tsx:22 | setup/ShowGetStartedV2 | `"/"` | go back to Today (tips replay / wizard done) | Stay: close the dialog; the tips replay on the current tab |
| setup/StepsV2.tsx:77 | setup/StepsV2 | `CONNECT_BROKER_PATH` | connect / log in to the broker | Broker drawer: `ConnectPanel` in `Sheet` |
| setup/StepsV2.tsx:90 | setup/StepsV2 | `CONNECT_BROKER_PATH` | connect / log in to the broker | Broker drawer: `ConnectPanel` in `Sheet` |
| setup/StepsV2.tsx:173 | setup/StepsV2 | `"/risk-management"` | change his own risk limits | Side panel: the Risk limits section (`RiskLimitsV2`) in `Sheet` |
| setup/StepsV2.tsx:315 | setup/StepsV2 | `START_BOT_PATH` | start a trading bot | Drawer: the bot start form (`PaperBotV2` form) in `Sheet` |
| setup/StepsV2.tsx:356 | setup/StepsV2 | `"/strategies"` | browse the strategy library | Drawer: the library in `Sheet` |
| setup/StepsV2.tsx:487 | setup/StepsV2 | `ALERTS_PATH` | set up alert delivery | Modal: the alerts section in `Dialog` |

### Admin (1)

| Site (file:line) | Source | Target | What the user wants | In-place pattern |
|---|---|---|---|---|
| admin/AdminSectionsV2.tsx:203 | admin/AdminSectionsV2 | ``/admin?section=support&user=${u.id}`` | open this user's support status | Admin drawer: the support panel in `Sheet` |

## 4. Shared design: one in-place host

### 4.1 Pieces (all in `frontend/src/inplace/`, lazy-loaded so the bundle budget holds)

- `InPlaceProvider` (mounted once in `AppShellV2`, inside the router): a panel STACK `{ id, spec, returnFocus }[]`
  (max 3 deep; opening a 4th replaces the top). The current tab's route element is never unmounted, so its state
  (scroll, filters, chat, a half-filled ticket) is exactly as it was when the panel closes.
- `useInPlace()`: `open(spec | path)`, `close()`, `replace(spec)`, `stack`. `open(path)` resolves the path through the
  registry; a path with no registered panel falls back to today's navigation and logs a dev warning (so a later PR
  can register it), which keeps every button working while the areas convert one by one.
- `InPlaceLink` - a drop-in for `<Link to>`: renders `<a href={to}>` (so a long-press / middle-click / "open in new
  tab" still gives the real address) and on a plain click calls `open(to)` with `preventDefault()`.
- Registry `inplace/registry.ts`: `{ match: (url) => params | null, kind, presentation, load: () => import(...) }`.
  One entry per facility, reusing the existing component with an `embedded` prop where it is a page today:

| Facility (route matched) | Presentation | Existing component |
|---|---|---|
| Chart (`/charts/:symbol`, `chartWindowUrl`) | side panel (desktop, wide) / bottom sheet (phone) | `chart/EmbeddedChartV2` (`openLink={false}`), `ReadChartPanel` |
| Broker connect (`/broker`) | drawer | `broker/ConnectPanel` |
| AI key (`/settings/ai`) | modal | `aiKeys/ConnectAiPanelV2` |
| Risk limits (`/settings/risk-limits`, `/risk-management`) | side panel | `settings/RiskLimitsV2` body |
| Trading defaults, alerts, set-up steps, Prepare for LIVE | side panel / modal | `TradingDefaultsV2`, alerts section, `setup/StepsV2`, `GoLiveChecklistV2` |
| Notifications (`/notifications`) | drawer (right) / bottom sheet | the notifications list (extract from `pages/NotificationsPage`) |
| Positions, orders, money (`/positions*`) | side panel | the lists in `trading/PositionsV2`, `OrdersV2` |
| Signals, scanner (`/signals`, `/screener*`) | side panel | `signals/SignalsV2` list, the scanner board |
| Bot start / test (`/deployments`, paper-bot and test links) | drawer | the `PaperBotV2` start form, the backtest form |
| Chain / basket / plan (`/option-chain*`) | side panel; plan = split view beside the basket | `fno/ChainTradingV2`, `BasketPanelV2` |
| Copilot tools (`/copilot/:tool`, `/chart-analyze`, `/strategies/build`, coach) | split view inside the Copilot tab (CW-1); elsewhere a wide side panel | the tool tabs, `ChartTurnV2` analyze |
| Journal entry, status, legal document, sectors, performance, admin user support | bottom sheet / modal / side panel | `journal/EntrySheetV2`, the status view, legal markdown, the markets table, analytics summary, the admin support panel |

- Presentation shell: the existing `components/primitives/Dialog.tsx` - `Sheet side="right"` from 768 px,
  `side="bottom"` on a phone, `Dialog` for a modal, `wide` for a workspace sheet. No new modal library. Focus returns to
  the clicked button on close (`shell/returnFocus.ts`); Escape and the close button pop one level.
- Split view: a tab may register a slot (`<InPlaceSlot name="copilot-side" />`); when the current tab has a slot,
  `open()` renders the panel there (side by side on desktop, full width with a back chip on a phone) instead of in a
  Sheet. The Copilot workspace (M75 CW-1 / CW-4) is the first slot user.

### 4.2 URL and deep links

- The PATH never changes on an in-place open. The open panel is written as one search parameter on the same path
  (`?panel=chart:NIFTY:15m`), pushed as a history entry: the phone Back button closes the panel (question M75N-2), a
  reload re-opens it, and a copied address reproduces it. Only the search changes, so react-router keeps the tab's
  route element mounted and its state intact.
- Every full route stays registered in `App.tsx` and renders the page as today when opened directly (bookmark,
  notification tap, shared link, a new browser tab). Nothing is removed (CLAUDE.md section 8).
- A panel never shows a "go to the tab" jump; to change tab the user uses the navigation. Features that open a new
  browser window today (ProChart "open in a new tab", the classic chart window) keep that facility as a "Pop out"
  control inside the panel - a deliberate second step, never the first click.

### 4.3 Tests per conversion PR

- The guard: lower `BASELINE` by the number converted (the test fails on a higher OR a lower count).
- A host test (NAV-1): open / close / stack / Back / reload with `?panel=` / unregistered fallback / focus return.
- Per area: for every converted button, a test that clicks it in a `MemoryRouter` and asserts `location.pathname` is
  unchanged and the panel is visible (CW-8), plus one deep-link test that renders the full route directly.
- UI proof per milestone: before / after screenshots, phone and desktop, in ONE comparison page.

## 5. PR split (each small enough to review)

| PR | Scope | Sites | Notes |
|---|---|---|---|
| NAV-0 | This PR: inventory, design, guard | 0 | baseline 141 |
| NAV-1 | `inplace/` host, `InPlaceLink`, registry with chart + broker + AI key panels, host tests | 0 | no behaviour change until a site uses it |
| NAV-2 | Copilot (chat answer chart, Ask AI sheet, analyze, tool hand-offs, AI key chips, news, watchtower) | 19 | first `InPlaceSlot` (Copilot split view) |
| NAV-3 | Terminal / charts (EmbeddedChart "Open in Chart tab", chart toolbar, Markets, Watchlist, evidence chart, Terminal panels) | 18 | |
| NAV-4 | Option chain (chain action sheet, futures chain, insights, compare, cockpit, ticket "Change limit", basket plan) | 14 | |
| NAV-5a | Signals, bots, ideas, journal, scanner | 17 | |
| NAV-5b | Positions, orders, portfolio, risk | 10 | |
| NAV-6a | Today cards and the attention list | 16 | |
| NAV-6b | Shell: bell, account notices, help / support links, tours, notices, broker gate and broker prompt, status, legal banner | 20 | `BrokerGate` is shared: one change converts 5 sites |
| NAV-7 | Settings and set-up | 26 | |
| NAV-8 | Admin | 1 | |

## 6. Open questions (provisional defaults; also in docs/OPEN_QUESTIONS.md)

| # | Question | Provisional default |
|---|---|---|
| M75N-1 | Today "View all" / "Find setups" shortcuts are tab shortcuts. Convert them too? | Yes: they open the list in a side panel on Today; the tab itself is reached from the navigation. |
| M75N-2 | Phone Back with a panel open. | Back closes the panel (one history entry per open), the tab stays. |
| M75N-3 | The classic UI (`App.tsx`, `src/pages/*`, 10 sites). | Converted in the same area PRs while the classic shell is reachable; counted by the guard until then. |
