# Always-visible prose inventory (ATP frontend)

Owner order (UI): no theory / explanation text visible by default on any page; numbers first, explanations behind a closed ⓘ Details.
How this list was made: a TypeScript AST scan of frontend/src (*.tsx, tests/fixtures/admin/stories excluded) for rendered text of 60+ characters that is NOT inside <Details>/<details>, a title/aria/placeholder attribute, and NOT conditional (?:, &&, ||, ??, set*/toast/Error calls) - i.e. text a user sees every time the page opens.

Structural sources (every page): page subtitles (`data-page-subtitle`: PageHeader line/description, PageTitleV2 subtitle) - 30 call sites; PageIntro cards - 14 call sites.
Plus 129 always-visible sentences below (file:line, first 160 characters).

## pages/DeploymentsPage.tsx (2)
- :744 (optional; a diagnostic only - recorded for review, it never blocks an entry)
- :755 (optional; checked before each new entry from the option chain's OI - missing or stale OI data blocks the entry; exits are never gated. Limits come from the Opt

## components/VisionByokCard.tsx (3)
- :104 Vision with your own AI key is not enabled for this organisation yet (feature
- :136 Vision reviews charts and comments on them using your own AI provider API key. You pay the provider directly; the platform stores your key encrypted, never bill
- :244 Estimates from the platform's own metering; your provider's bill is the authority.

## fno/BasketPanelV2.tsx (1)
- :795 Strategy exit set on the filled legs: … Change or remove it on Positions.

## pages/RiskManagementPage.tsx (4)
- :113 Log in from the Account tab to configure your own risk limits. Every paper (and, once wired, live) order passes through the Risk Engine using these settings bef
- :128 Position sizing and daily-loss/trade-count/consecutive-loss guards the Risk Engine checks before every paper-execute order. Anonymous calls always use the platf
- :150 Enforced by the engine on every entry, paper or live, whatever built the strategy. Values cannot exceed the platform ceilings.
- :214 Budget, RBI policy, expiry, results: on the day (and inside the time window, if given) a BLOCK event refuses new entries in the named underlying, a SIZE_CUT eve

## strategyFlow/StrategyFlowV2.tsx (4)
- :103 Five short steps: start, market, entry, exits, result. Then start it on paper.
- :131 Backtests use past prices; past results do not promise future ones. Paper money only.
- :291 This template brings its own entry, with its usual settings:
- :330 Coming soon to the builder. Until then, use the strategy templates for these ideas.

## chartAnalyze/ChartAnalyzePageV2.tsx (3)
- :356 Upload or paste a chart screenshot, or a TradingView snapshot link, then press Analyze for an educational reading.
- :414 Your chart is sent to the AI provider that runs the analysis (your own key's provider, or your organisation's). It is stored privately
- :494 The chart image and its analysis are deleted now and cannot be brought back.…

## components/AppearanceCard.tsx (3)
- :44 Makes all text, numbers and charts bigger on every device you use.
- :90 Profit shows blue and loss orange instead of green and red. Signs (+ / -) are always shown too.
- :102 Turns off animations, card tilt and the AI Copilot's 3D scene (a still image is shown). The device's own reduced-motion setting does the same.

## components/ui.tsx (1)
- :108 Using deterministic sample OHLCV data. Every strategy, score and chart below is computed for real by the backend against this data; pages with a Data switch can

## markets/MarketsPageV2.tsx (1)
- :406 How each sector and index moved. Tap one to see its stocks, then a stock to open its chart.

## pages/AnalyticsPage.tsx (3)
- :40 Not tax advice. Estimates from your own trades only, to reconcile with your broker&apos;s statement; verify with a chartered accountant.
- :117 Log in from the Account tab to see win rate and P&amp;L broken down by strategy and symbol, computed from your persisted trade history.
- :130 Aggregated from your full persisted trade history (Positions/Trade Journal).

## pages/ScannerPage.tsx (3)
- :150 Filter a watchlist by indicator conditions (same building blocks as the Strategy Builder), price-action structure, and option-chain signals - only symbols clear
- :152 Option-chain filters read the broker's live chain in Broker mode and a sample chain otherwise.
- :164 The AI only translates your words into the filters below (same building blocks as the Strategy Builder); you review them and press Run. Without an AI key (Setti

## pages/SystemLogsPage.tsx (3)
- :30 Log in from the Account tab to see your account's audit trail.
- :38 Your own security-relevant event history - register/login, broker credentials stored/deleted, broker authentication attempts. Private to your account, not a glo
- :46 Every attempt to log in as you, successful or not. Ten failures in fifteen minutes lock the account for fifteen minutes; a login from a device you have not used

## paperbot/PaperBotV2.tsx (3)
- :183 Your strategy trades with paper money here, so you can watch how it does.
- :195 Your strategy trades with paper money here, so you can watch how it does. No real money is used.
- :197 Pick a strategy and a symbol, then start it. The bot keeps checking the market even while the app is closed; pause or stop it any time.

## setup/StepsV2.tsx (3)
- :355 We could not load the strategy library. Try again in a moment, or open the Library.
- :363 Pick one to try. A paper bot then trades it with paper money, so you can watch how it does.
- :457 Pick where alerts reach you when the app is closed: a lost broker login, a daily loss limit, a trade waiting for your approval.

## terminal/SpreadBuilderV2.tsx (3)
- :57 : tap a price on the option chain and choose Add to basket - the leg you sell and the leg that protects it (bought further out). They are placed and shown as on
- :106 Placed one after another: the hedge (buy) first, then the sell. On exit the sold leg closes first, then the hedge. If a leg does not go through you are told at 
- :110 Next, under &quot;Exits for this strategy&quot;, you can set an overall stop-loss and target for the whole spread (the spot level by default).

## chartAnalyze/ResultViewV2.tsx (1)
- :172 These are conditions the method watches, not an instruction to trade.

## components/AlertChannelsCard.tsx (2)
- :240 The trading worker raises CRITICAL alerts (broker session expired, stop-loss could not be placed, deployment auto-paused, daily loss limit) while no browser is 
- :310 , so your receiver (a bot, n8n, Zapier, a Slack relay) can verify it came from here. HTTPS only.

## components/BetaRolloutCard.tsx (2)
- :23 Sign-up through invite codes stops at the effective cap. The cohort grows in stages; move up only when the current stage has run cleanly. Moving down closes sig
- :69 Move the cohort down to …? Sign-up stops once … places are used.

## components/GuideChat.tsx (1)
- :55 Ask about any trading concept, or "What is NIFTY doing today?". Answers come from the concept library and the market memory; with an AI key in Settings the AI a

## pages/AccountPage.tsx (2)
- :195 For your safety you are asked to sign in again after a while, even on a device you use every day.
- :223 ATP support can open a read-only view of your account to help you. Nothing can be changed, placed or downloaded during it, and every view is listed here.

## pages/BacktestPage.tsx (2)
- :280 Event-driven simulation with position sizing, stop / target management and dated Indian charges.
- :282 Bars below applies to sample data; broker candles use the lookback.

## pages/ChartWorkspace.tsx (2)
- :60 Log in in the main tab first, then open the workspace again.
- :62 The multi-chart workspace is not enabled for your organisation yet. Single charts open from any chart&apos;s &quot;New tab&quot; button.

## pages/MarketplacePage.tsx (2)
- :99 Strategies other users published, each with a saved backtest as its documented performance. A copy you subscribe to runs in your own account; nothing here is ad
- :160 A listing freezes one version of your strategy - later edits never reach subscribers. Attach a saved backtest run as the documented performance (required); the 

## pages/NewsEventsPage.tsx (2)
- :152 Structured, cited entries for RBI policy decisions, the Union Budget, government policy changes, corporate news, and other market-moving events. Entries a perso
- :152 come from a publisher's own public feed (RBI, SEBI, ...): headline and link only, never checked by the platform - read the source before acting.

## pages/NotificationsPage.tsx (2)
- :74 Log in from the Account tab to see your account's notification feed.
- :127 Entries, exits, rejections, broker disconnects, risk/daily-loss limit breaches, emergency exits, and system failures - shared across your account.

## pages/OrdersPage.tsx (2)
- :70 Every entry and exit fill from your paper trades, most recent first - a broker-style order blotter view.
- :79 Execute a signal from the Signals page; its entry and exit fills appear here.

## pages/PortfolioPage.tsx (2)
- :64 Capital deployed across open positions and cumulative realized P&amp;L.
- :142 Set portfolio-wide caps (max gross exposure, max symbol concentration) under Risk Management → limits, scope "Portfolio".

## pages/PositionsPage.tsx (2)
- :105 Log in from the Account tab to see your paper-execute positions and trade history. Anonymous paper-execute calls (from the Signals tab without signing in) aren'
- :118 Paper trades executed from the Signals tab while signed in as ….

## pages/QuantPage.tsx (2)
- :74 Cross-sectional factor scores and a descriptive risk model on your watchlist. Ranks and statistics of the supplied window; never a signal or an order.
- :75 Value and quality come from the Fundamentals module: symbols with a company profile and financials get PE, PB, ROE, debt/equity and PAT growth filled in; the re

## pages/SignalsPage.tsx (1)
- :140 Generate a signal from any inbuilt strategy and see the full "why this trade" breakdown.

## pages/TeamPage.tsx (1)
- :128 Everyone in your organisation shares the same broker connections, deployments, positions and alerts. Owners manage the team; traders and strategy creators can t

## settings/chainSettings.tsx (2)
- :103 PCR zones: where the put-call ratio counts as very low, low, balanced, high or very high in &quot;What the chain shows&quot;.
- :117 On an expiry day, open the chain and the spread builder on the next expiry. Today&apos;s expiry stays one tap away.

## settings/smartExecutionSettings.tsx (2)
- :112 A stock F&amp;O position in or near the money, held to expiry, settles by delivery. You get a warning on Positions this many days ahead.
- :139 When a leg does not fill, nothing more is sent and you choose: complete the rest, or close the filled legs. If you do not answer in time:

## status/StatusView.tsx (1)
- :129 Cannot reach the platform. It may be down, or your connection may be offline. This page tries again every minute.

## support/SupportDialog.tsx (1)
- :49 How-to answers from the help pages and the legal pages. The help desk cannot see your account, give trading advice or touch orders.

## terminal/PanelsV2.tsx (2)
- :468 Your running bots and the signals waiting for you will show here, with pause and exit for each bot. Until then, use the Signals and Paper bot pages.
- :479 The payoff of your basket and your open positions, with what-if, will show here. Until then, the basket shows its payoff before you place it.

## trading/PortfolioV2.tsx (2)
- :50 Each symbol shows its share of your capital, its open profit or loss and what it would lose at its stop.
- :51 Where your money is right now, and what you have booked so far.

## trading/RiskV2.tsx (2)
- :148 How much you can lose today, and the rules every entry is checked against.
- :164 How much you can lose today, and the rules … is checked against.

## alerts/WebPushSection.tsx (1)
- :55 Alerts as system notifications on this phone or computer, even with the app closed. Messages are encrypted to this device; the push service in between cannot re

## alerts/WhatsAppSection.tsx (1)
- :51 WhatsApp delivers alerts only as a template your provider has approved, to numbers that opted in to messages from your organisation. Placeholders:

## broker/BrokerAdvanced.tsx (1)
- :44 A second account at the same broker gets its own name, for example &quot;family&quot;.

## broker/MultiAccountCard.tsx (1)
- :75 Choose one Primary account and, if you like, Secondary accounts in groups. Only accounts you connected yourself are listed. This only records your choice; it do

## broker/PracticeAccountsCard.tsx (1)
- :52 Simulated accounts for trying one order on several accounts in practice mode. Choose them in the order ticket under "Run on". Each places its own practice order

## components/AiAcknowledgementGate.tsx (1)
- :35 I have read and understood: the AI Copilot is not an adviser; the decision is mine.

## components/AiProviderCard.tsx (1)
- :49 Powers the AI Copilot's strategy drafts, its written answers and the wording of monitoring proposals. Claude (Anthropic) is the default provider: paste an Anthr

## components/ApiKeysCard.tsx (1)
- :42 (never the console endpoints), only within their scopes, and are metered against the plan's daily allowance. The developer reference is at

## components/BrokerAccountsCard.tsx (1)
- :56 One account per stored credential; store a second credential for the same broker with a different account label to trade two accounts. LIVE deployments route to

## components/BrokerTokenBanner.tsx (1)
- :24 The saved Redirect URI differs from this site&apos;s address - see Advanced on the Broker page.

## components/BrokerUncertainBanner.tsx (1)
- :59 . Exits keep running. The worker reconciles against the broker every cycle and lifts the block the moment the books agree; if a position at the broker is not on

## components/ExportCard.tsx (1)
- :44 records for the date range (inclusive, UTC). Each file carries its SHA-256 and a manifest; the export itself is written to the audit trail. Keep these for the r

## components/MfaCard.tsx (1)
- :49 Works with Google Authenticator, Authy, 1Password and any RFC 6238 app.

## components/RiskLimitsCard.tsx (1)
- :80 Every order is checked against all limits that apply to it - organisation, user, broker account, strategy and instrument - and the strictest of each kind wins. 

## components/StaticIpCard.tsx (1)
- :47 SEBI&apos;s retail-algo framework accepts API orders only from an IP the broker has on file for you. Register this server&apos;s public IP in each broker&apos;s

## components/SystemListsCard.tsx (1)
- :63 :00 IST with the market closed, from the index and classification files the universe sync holds (universe sync is

## components/WebhookCard.tsx (1)
- :46 Paste this URL into a TradingView alert's "Webhook URL" field, with a JSON message body of

## copilot/tabs/IdeaBuilderTab.tsx (1)
- :66 Pick a template or describe your idea in words, choose the market, the entry and your exits, then test it and start it on paper.

## fno/CompareV2.tsx (1)
- :383 Two baskets are needed to compare: add legs, or open the chain to use the ready strategies.

## fno/IncompleteOrdersV2.tsx (1)
- :40 A leg did not fill, so nothing more was sent and nothing was undone.

## fno/OrderTicketV2.tsx (1)
- :287 This closes the position on … account… at the current price.

## growth/DisciplineCardV2.tsx (1)
- :86 Discipline facts from your own paper trading. No stock names, no prices, no advice.

## growth/InviteFriendV2.tsx (1)
- :22 Know someone who wants to learn trading the careful way? Send them your link.

## journal/SimilarSetupsV2.tsx (1)
- :59 , as recorded when each trade was taken; only trades that closed before this entry count. The totals show from

## optionChain/AskAiV2.tsx (1)
- :142 Describe the analysis you want in your own words. The AI writes it as an analysis; the numbers come from the chain.

## optionsBuilder/BasketDialog.tsx (1)
- :31 The legs in the order they would be placed: hedges first, so the margin sees the protection before the short.

## optionsBuilder/MetricsCard.tsx (1)
- :80 … orders at … each, STT …, other charges …. Open and close: ….

## pages/ChartWindow.tsx (1)
- :92 refreshes every minute · live price every 5 s · scroll left for older history

## pages/CoachGuidePage.tsx (1)
- :24 The coach shows patterns in your own trades (rules followed or broken); the guide explains the concepts behind them. Decisions are yours.

## pages/FundamentalsPage.tsx (1)
- :389 Institutional-grade company intelligence: business quality, earnings quality, valuation, DCF, red flags, SWOT, and a composite Fundamental Score fused with the 

## pages/InstrumentsPage.tsx (1)
- :28 MCX commodity and crypto contract specs the platform knows about, beyond plain NSE/BSE equity & index options (which already size correctly off your Risk Manage

## pages/JournalPage.tsx (1)
- :308 Only the proportions matter. The defaults and the reasoning behind each one are in the discipline engine's design note.

## pages/OptionChainPage.tsx (1)
- :82 PCR, Max Pain, ATM/ITM/OTM and a bias that never relies on PCR alone.

## pages/OptionsBuilderPage.tsx (1)
- :273 Placing goes to PAPER with the plan-before-trade ticket; this page places no order.

## pages/StrategyBuilderPage.tsx (1)
- :452 Your own values only. A blank box is off: no stop-loss, no target, no minimum R:R.

## paperbot/CopilotDraftV2.tsx (1)
- :63 Built from your own words with ATP&apos;s building blocks. Nothing is saved yet: fill in your values, save it as your strategy, then start it on paper below. Ev

## rhythm/RhythmTelegramV2.tsx (1)
- :55 A short brief before the open, a closing wrap and your week, from your own lists and trades. It describes the market and your record; it never tells you what to

## screener/ResultViews.tsx (1)
- :56 Sized by market cap (a symbol without one is shown at the median) · colour: the last day's change · ringed: matched

## screener/ScanAlertPanel.tsx (1)
- :157 An alert only tells you; it never places or changes an order.

## screener/ScanVersionsPanel.tsx (1)
- :47 for each scan. Opening a version changes nothing until you save; saving adds it as the newest version.

## settings/VisionReviewSwitchesV2.tsx (1)
- :56 Vision looks at charts in the background with your AI key and writes a short comment. It never places, blocks or changes an order.

## signals/DayOneV2.tsx (1)
- :218 Signals only. What to trade, how much and when to get out stay your own choices.

## signals/SignalsV2.tsx (1)
- :109 Open a signal to see its levels and why it was suggested, then approve or skip it.

## signals/TryOnSymbolV2.tsx (1)
- :94 on one symbol now and see what it would signal, with its levels and reasons.

## terminal/TerminalClassicV2.tsx (1)
- :243 The chart, the option chain and your order on one screen. Every order is checked and shown to you before it is placed.

## terminal/TerminalPageV2.tsx (1)
- :272 One symbol and expiry for the chart, chain, basket and your positions.

## trading/CostMeterV2.tsx (1)
- :86 Charges against your gross P&L, from your own closed trades.

## trading/PositionsV2.tsx (1)
- :229 Open a position's chart, or exit a paper position at the current price.

## ui/BrokerGate.tsx (1)
- :152 Your prices, positions and signals appear here once you sign in.
