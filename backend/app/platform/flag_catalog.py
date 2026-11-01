"""UX1 (audit section 7, handover 8b): what each feature flag means, for the admin Feature flags list only.

One plain entry per flag in `controls.FEATURE_FLAGS`: a short plain description, the area it belongs to, what a user
sees when it is on, the other flags it needs (`depends_on`, flag names), anything else it needs (`needs`, plain words)
and its status. The status is the audit judgement of 22 Oct (docs/design/UX_AUDIT_FULL.md section 7), not a staging
test:
- `ready`: ready for users;
- `behind_the_scenes`: ready, nothing new to see;
- `unfinished`: unfinished or needs setup first;
- `decision`: an owner / compliance decision.

Admin-only: served by GET /api/admin/controls/flags (SUPER_ADMIN + two-factor). A trader never sees a flag name; a page
behind an OFF flag says "Coming soon". The server does not refuse a flag whose dependency is off (an operator may stage
them in any order); the admin list greys the switch with "Needs ... first".
New flags: add ONE entry, in the same place as in FEATURE_FLAGS; tests/test_ux1_flag_catalog.py checks both lists match.
"""
from __future__ import annotations

from typing import Dict, Literal, NamedTuple, Tuple

FlagStatus = Literal["ready", "behind_the_scenes", "unfinished", "decision"]
FlagArea = Literal["trading", "charts", "screens", "ai", "data", "platform"]

AREAS: Dict[str, str] = {
    "trading": "Trading and risk",
    "charts": "Charts and tools",
    "screens": "Find setups (screens)",
    "ai": "AI",
    "data": "Data and alerts",
    "platform": "Accounts and platform",
}
STATUSES: Dict[str, str] = {
    "ready": "Ready for users",
    "behind_the_scenes": "Ready (behind the scenes)",
    "unfinished": "Unfinished / needs setup",
    "decision": "Owner / compliance decision",
}


class FlagInfo(NamedTuple):
    plain: str
    area: FlagArea
    turns_on: str
    depends_on: Tuple[str, ...]
    needs: str
    status: FlagStatus


FLAG_INFO: Dict[str, FlagInfo] = {
    "ai_copilot": FlagInfo("The AI Copilot page and AI strategy drafts", "ai", "Copilot in the sidebar; AI drafts", (), "an AI key (else the built-in parser)", "ready"),
    "marketplace": FlagInfo("Strategies other users publish", "platform", "Marketplace page", (), "", "decision"),
    "public_api": FlagInfo("API keys for developers", "platform", "Settings › Advanced › Personal API keys", (), "", "ready"),
    "backtest_optimizer": FlagInfo("Try many settings of a strategy at once", "trading", "Optimise button in Backtest", (), "", "ready"),
    "live_trading": FlagInfo("New LIVE (real money) bots", "trading", "LIVE mode", (), "go-live checklist, two-factor", "decision"),
    "phase0_paper_only": FlagInfo("Paper only: every new real-money entry is refused (exits still work)", "trading", "nothing visible", (), "", "behind_the_scenes"),
    "self_signup": FlagInfo("Anyone can create an account", "platform", "Register link", (), "", "decision"),
    "news_feed": FlagInfo("Live headlines from public sources (marked unverified)", "data", "rows on News & events, News Radar", (), "", "ready"),
    "telegram_inbound": FlagInfo("Approve / reject paper trades from Telegram buttons", "data", "buttons in Telegram messages", (), "a Telegram bot set up", "ready"),
    "market_thesis": FlagInfo("A per-symbol market reading with scenarios", "ai", "thesis card in the Copilot", (), "market memory", "unfinished"),
    "colour_v2": FlagInfo("Colour v2: purposeful colour on the redesigned pages (approved by the owner 24 Oct, ON by default)", "platform", "tinted icons, option chain OI bars and in-the-money shading, a tinted P&L, index sparklines, Long / Short chips, tinted navigation", ("ui_v2",), "", "ready"),
    "colour_v3": FlagInfo("Colour v3: the professional palette (neutral surfaces, one accent, meaning colours; ON by default, owner 28 Oct)", "platform", "calmer surfaces and borders, one blue for actions, no dot backdrop", ("ui_v2", "colour_v2"), "", "ready"),
    "context_panel": FlagInfo("Market context: trend, breadth, VIX, events", "data", "context panel", (), "FII / DII source optional", "ready"),
    "options_builder": FlagInfo("Plan an options trade (payoff)", "trading", "Plan an options trade", (), "instrument master", "ready"),
    "options_builder_saved": FlagInfo("Save option plans", "trading", "Save button in Plan a trade", ("options_builder",), "", "ready"),
    "marketplace_ai_listings": FlagInfo("AI-made strategies on the marketplace", "platform", "", ("marketplace",), "", "decision"),
    "thesis_stock_targets": FlagInfo("Price targets and confidence for single stocks", "ai", "numbers in the thesis", ("market_thesis",), "", "decision"),
    "screener_v2": FlagInfo("The screener: saved screens and runs on broker data", "screens", "Find setups page", (), "a broker or stored data", "ready"),
    "screener_intrabar": FlagInfo("Alerts on the bar still forming", "screens", "an option on alert rules", ("screener_v2",), "", "ready"),
    "screener_builder_u2": FlagInfo("Formula blocks in the screen builder", "screens", "builder blocks", ("screener_v2",), "", "ready"),
    "ai_agent": FlagInfo("Copilot reads your positions and news before answering", "ai", "better answers", ("ai_copilot",), "an AI key", "ready"),
    "ai_research": FlagInfo("AI drafts are back-tested and scored automatically", "ai", "research loop", ("ai_copilot",), "an AI key", "unfinished"),
    "ai_one_key": FlagInfo("Connect AI in one paste: one key for every AI feature", "ai", "Connect AI panel on Copilot and Settings › AI", ("ui_v2",), "", "ready"),
    "copilot_agent": FlagInfo("The Copilot chat reads your own data with the AI before answering", "ai", "AI answers built from your orders, settings, journal and watchlists", ("ai_copilot", "ui_v2"), "an AI with tool use (a key)", "unfinished"),
    "ai_platform_key": FlagInfo("AI answers on the platform's own key, without a key of the user's own", "ai", "AI answers out of the box; today's use on Settings › AI", ("ui_v2",), "the platform key on the server, each plan's AI budget and the per-user daily cap (Admin › Controls › Limits)", "unfinished"),
    "cost_meter": FlagInfo("Cost of trading: what charges took from your own closed trades this year and this month", "trading", "a Cost of trading card on My trades", ("ui_v2",), "", "ready"),
    "daily_rhythm": FlagInfo("Daily rhythm: a brief before the open, a pulse, a closing wrap and a weekly review", "data", "a card on the Dashboard; Telegram by the user's choice (Settings › Alerts)", ("ui_v2",), "Telegram connected for the messages", "unfinished"),
    "data_hub": FlagInfo("One live price stream per broker connection for every page; live prices in the browser", "data", "prices that move on their own on charts and watchlists; a notice while prices reconnect or the broker login needs renewing", ("ui_v2",), "a broker with a live price stream (Upstox, Zerodha) for streaming, else prices refresh every few seconds", "unfinished"),
    "smart_execution": FlagInfo("Smart execution for multi-leg orders: hedge first, no half-placed order left silently, freeze-limit slicing, margin with the hedge benefit, the delivery warning for stock F&O", "trading", "the basket's confirm step (order of the legs, margin, free balance after) and its incomplete state; the order ticket's slicing line; a delivery warning on Positions and in Telegram", ("ui_v2",), "your own broker session for the broker's margin (else ATP's estimate, labelled)", "ready"),
    "status_page": FlagInfo("Uptime per market session and the service targets on the status page; a notice in the app when the platform is degraded", "platform", "a Markets block and Service targets on /status; a notice above every page while degraded", ("public_status_page",), "the notice shows in the new layout", "ready"),
    "beta_cohort_gate": FlagInfo("Sign-up needs an invite code; cap on users", "platform", "invite code field", (), "", "decision"),
    "vision_byok": FlagInfo("Use your own AI key to read chart images", "ai", "Settings › AI › Vision key", (), "a user's AI key", "ready"),
    "vision_byok_v3": FlagInfo("Your AI key also reviews scan alerts and a daily chart review", "ai", "comments on alerts", ("vision_byok",), "", "unfinished"),
    "broker_app_links": FlagInfo("Links to trade in your broker app with a pre-filled basket", "trading", "a link on proposals", (), "", "decision"),
    "discipline_engine": FlagInfo("Automatic journal, behaviour tags, weekly discipline score", "trading", "Journal page", (), "", "ready"),
    "byob_data_tier": FlagInfo("Live data only through each user's own broker; data badges", "data", "data badges", (), "broker data terms", "decision"),
    "history_import": FlagInfo("Import stored daily / 1-minute history", "data", "nothing visible", (), "the off-site history store", "behind_the_scenes"),
    "history_backfill": FlagInfo("Nightly NSE end-of-day download", "data", "nothing visible", (), "", "behind_the_scenes"),
    "history_intraday": FlagInfo("Owner's own 1-minute history", "data", "nothing visible", (), "owner broker session", "behind_the_scenes"),
    "chain_history": FlagInfo("Owner's own option-chain history (recorded every minute in market hours)", "data", "Admin › System › Chain history card", (), "owner broker session", "behind_the_scenes"),
    "market_store_reads": FlagInfo("Scans and backtests read ATP's stored bars first (fewer broker calls)", "data", "nothing visible", (), "", "behind_the_scenes"),
    "approval_only_entries": FlagInfo("Every automatic entry waits for your Approve", "trading", "Signals waiting for you", ("paper_account",), "", "ready"),
    "paper_account": FlagInfo("Your own paper account and progress ladder", "trading", "paper account card, Telegram /status", (), "", "ready"),
    "risk_by_default": FlagInfo("Plan-before-trade ticket, risk-based size, daily loss lockout", "trading", "paper ticket", (), "", "ready"),
    "vix_gate": FlagInfo("A VIX spike pauses new bullish index entries", "trading", "a note on blocked entries", (), "VIX feed", "ready"),
    "screener_gallery": FlagInfo("Ready-made screens to browse", "screens", "gallery on Find setups", ("screener_v2",), "", "ready"),
    "screener_vectorized": FlagInfo("Faster screens (same results)", "screens", "nothing visible", ("screener_v2",), "", "behind_the_scenes"),
    "screenql_amw_functions": FlagInfo("Extra screen functions (divergence, swings, candles)", "screens", "builder functions", ("screener_v2",), "", "ready"),
    "strategy_lab_pine": FlagInfo("Paste a TradingView Pine script and run its signal condition as a screen (never executed)", "screens", "Pine Script button on the Screener", (), "", "unfinished"),
    "signal_language_v2": FlagInfo("Signals, alerts, the brief and the teacher in structure words only (no buy / sell / target / stop wording, no single prices)", "ai", "reworded signal and alert texts", (), "", "ready"),
    "idea_library": FlagInfo("Idea library: method ideas in plain words, each with a scan to try (education only)", "screens", "Ideas page under Strategies", ("ui_v2",), "Find setups and its builder for the scans", "unfinished"),
    "alerts_stale_guard": FlagInfo("No alert is sent on old data", "data", "nothing visible", (), "", "behind_the_scenes"),
    "alerts_reliable_outbox": FlagInfo("Alerts never sent twice, retried if lost", "data", "nothing visible", (), "", "behind_the_scenes"),
    "support_bot": FlagInfo("How-to help bot", "ai", "Help button", (), "help pages", "unfinished"),
    "help_centre": FlagInfo("Help centre, Report a problem and the support contact link", "platform", "Help centre and Report a problem under Settings › Help & tips", ("ui_v2",), "the support number or e-mail in the admin console for the contact link", "unfinished"),
    "legal_pages": FlagInfo("Privacy, Terms, Risk disclosure and acceptance", "platform", "legal pages, sign-up checkboxes", (), "placeholders filled", "decision"),
    "screener_breakouts": FlagInfo("Breakout screens, set 1", "screens", "screens in the gallery", ("screener_v2",), "", "ready"),
    "screener_breakouts_bk3": FlagInfo("Breakout screens, set 2", "screens", "screens", ("screener_breakouts",), "", "ready"),
    "screener_breakouts_bk4": FlagInfo("Breakout screens, set 3", "screens", "screens", ("screener_breakouts",), "", "ready"),
    "screener_breakouts_fitness": FlagInfo("What followed past breakouts", "screens", "a history box", ("screener_breakouts",), "shared daily data", "ready"),
    "evidence_context": FlagInfo("OI walls, trend strip and replay on evidence charts", "screens", "more on match charts", ("evidence_charts",), "", "ready"),
    "evidence_charts": FlagInfo("Each screen match drawn on a chart, also on Telegram", "screens", "match charts", ("screener_v2",), "", "ready"),
    "public_status_page": FlagInfo("A public status page", "platform", "/status", (), "", "ready"),
    "live_push": FlagInfo("Live prices pushed to charts and sparklines instead of timers; the delayed chip from the age of the last tick", "data", "charts and sparklines that move with each live price; a delayed chip that reads the age of the last price", ("ui_v2", "data_hub"), "a broker with a live price stream (Upstox, Zerodha); without one prices refresh on their timers as before", "unfinished"),
    "market_zones": FlagInfo("Nightly demand / supply and S/R zones on the chart", "charts", "chart layer, zone functions", (), "nightly job", "ready"),
    "markets_view": FlagInfo("Sectors & indices page and the option chain summary in plain words", "data", "Sectors & indices page, a summary on the Option chain page", ("ui_v2",), "the daily market data update", "unfinished"),
    "mobile_push": FlagInfo("Phone notifications in the Android app", "data", "Phone notifications in the app's Settings; notifications that open the right screen", ("ui_v2",), "the server's FCM key and the app with the notification plug-in", "unfinished"),
    "multi_account": FlagInfo("Several of your own broker accounts: one Primary, Secondaries in groups, simulated paper accounts", "trading", "Primary / Secondary roles on Settings › Broker accounts", ("ui_v2",), "", "unfinished"),
    "my_day_card": FlagInfo("My day: each open position's nearest area, expiry and state in one card", "trading", "a My day card at the top of Today", ("ui_v2",), "", "ready"),
    "screener_legends": FlagInfo("Cards on public trading methods with a screen", "screens", "Legends cards", ("screener_v2",), "", "ready"),
    "screener_board_views": FlagInfo("Heat map and chart grid of matches", "screens", "result views", ("screener_v2",), "", "ready"),
    "scan_auto_paper": FlagInfo("Armed scans place PAPER orders without asking", "screens", "paper orders from scans", ("execution_core",), "the Approve step for automatic entries switched off", "decision"),
    "screener_legends_lg2": FlagInfo("More Legends cards", "screens", "cards", ("screener_legends",), "", "ready"),
    "screener_fundamentals": FlagInfo("Reported company figures in screens", "screens", "fields", ("screener_v2",), "fundamentals data", "unfinished"),
    "screener_legends_lg3": FlagInfo("Value and growth Legends cards", "screens", "cards", ("screener_fundamentals",), "", "unfinished"),
    "scan_then": FlagInfo("After a scan match, wait for a level, then notify or propose", "screens", "watch-then-trigger", ("execution_core",), "", "ready"),
    "chart_workspace": FlagInfo("Multi-chart layouts (1-8 charts)", "charts", "Multi-chart tab", (), "", "ready"),
    "execution_core": FlagInfo("One queue routes scan entries to a notification or a paper proposal", "screens", "nothing new", (), "", "behind_the_scenes"),
    "ladder_evidence": FlagInfo("Your own figures on the paper ladder card", "trading", "numbers on the card", ("paper_account",), "", "ready"),
    "experience_levels": FlagInfo("Futures and naked option selling open with your paper record", "trading", "a gate message", ("paper_account",), "", "ready"),
    "expiry_checklist": FlagInfo("Expiry checklist: positions expiring today or next session, settlement and the broker rule", "trading", "an Expiry check card on Positions", ("ui_v2",), "", "ready"),
    "screener_copilot": FlagInfo("Build and run screens from the Copilot", "screens", "screen box", ("screener_v2", "ai_copilot"), "", "ready"),
    "screener_venues": FlagInfo("Refuse fields a venue does not carry", "screens", "clearer errors", ("screener_v2",), "", "behind_the_scenes"),
    "screener_ownership": FlagInfo("Promoter / FII / DII stakes and events in screens", "screens", "fields", ("screener_v2",), "ownership data", "unfinished"),
    "screener_waves": FlagInfo("Wave count in screens (running wave, type, alternate, clarity, wave levels and lines)", "screens", "builder functions", ("screener_v2",), "", "unfinished"),
    "term_margin_pair": FlagInfo("Margin required and available in the basket, re-read on every lot change", "trading",
                                 "a margin pair in the Terminal basket", ("ui_v2", "terminal"), "your broker's margin where the broker offers it, else an estimate", "unfinished"),
    "term_roll": FlagInfo("Roll a position or strategy to the next weekly expiry, as a preview you confirm", "trading",
                          "a Roll button on Terminal positions", ("ui_v2", "terminal", "paper_account"), "", "unfinished"),
    "term_strike_area": FlagInfo("Distance from each short strike to the nearest area", "trading",
                                 "an area mark on short positions and the Terminal chain", ("ui_v2", "terminal", "market_zones"), "stored areas", "unfinished"),
    "term_whatif": FlagInfo("What-if payoff: your open positions plus the basket's new legs", "trading",
                            "a combined payoff in the Terminal basket", ("ui_v2", "terminal", "paper_account"), "", "unfinished"),
    "terminal": FlagInfo("The Terminal: chart, option chain, order panel, positions and orders on one screen, with saved layouts and a status bar",
                         "trading", "a Terminal entry under Trading", ("ui_v2",),
                         "the data hub for live prices; smart execution for multi-leg orders", "ready"),
    "trade_engine": FlagInfo("Signal engine from the private package (admin / owner only; places no orders)", "trading",
                             "nothing yet: the engine's signals and card come in later steps", (),
                             "the private engine package on the server", "unfinished"),
    "strategy_templates": FlagInfo("Ready strategy templates adopted as paper strategies", "trading", "templates in the trading bot form", ("ui_v2",), "", "unfinished"),
    "screener_rankings": FlagInfo("Rank a universe 0-100", "screens", "rankings", ("screener_v2",), "", "ready"),
    "screener_backtest": FlagInfo("Back-test a screen over time", "screens", "screen backtest", ("screener_v2",), "", "ready"),
    "screener_history": FlagInfo("Who matched on a past date, and what followed", "screens", "history", ("screener_v2",), "", "ready"),
    "portfolio_risk": FlagInfo("Risk view of your paper book", "trading", "a card on My positions", (), "", "ready"),
    "portfolio_risk_caps": FlagInfo("Caps on new paper entries by sector and open risk", "trading", "blocked-entry messages", ("portfolio_risk",), "", "ready"),
    "chart_analyze": FlagInfo("Analyze a chart: an educational reading of an uploaded chart image or link", "charts", "Analyze a chart page and its Charts button", (), "an AI key with image support", "unfinished"),
    "chart_analyze_strikes": FlagInfo("Analyze a chart: the option strikes your own options bots would use at the setup", "charts", "a strikes block on the Analyze a chart result", ("chart_analyze",), "an options trading bot", "unfinished"),
    "chart_analyze_share": FlagInfo("Analyze a chart: share a structure-only copy of an analysis with your organisation", "charts", "a Share button on the Analyze a chart result", ("chart_analyze",), "", "unfinished"),
    "chart_analyze_hybrid": FlagInfo("Analyze a chart: advanced analysis - ATP's own structure reading of the symbol and timeframe you pick, drawn beside your chart", "charts", "an Advanced analysis choice on the Analyze a chart page", ("chart_analyze", "chart_price_action"), "stored candles for the symbol; areas of interest need the market zones", "unfinished"),
    "chart_studies": FlagInfo("Screen formulas drawn on the chart", "screens", "a chart layer", ("screener_v2",), "", "ready"),
    "chart_price_action": FlagInfo("Swings, legs and trend drawn on the chart", "charts", "a chart layer", (), "", "ready"),
    "ai_multiview": FlagInfo("Technical, options, news and macro views of a symbol", "ai", "a Copilot view", ("ai_copilot",), "", "ready"),
    "chart_trades_layer": FlagInfo("Your paper trades on the chart", "charts", "a chart layer", (), "", "ready"),
    "chart_contracts": FlagInfo("Daily option / future charts with OI", "charts", "contract chart window", ("history_backfill",), "the nightly download has run", "unfinished"),
    "chart_futures_basis": FlagInfo("Futures basis and rollover charts", "charts", "basis window", ("history_backfill",), "the nightly download has run", "unfinished"),
    "screener_tx2": FlagInfo("Confluence view, short mirrors, watch-then-trigger lists", "screens", "screen tools", ("screener_v2",), "", "ready"),
    "ui_v2": FlagInfo("The redesigned app (navigation and pages)", "platform", "the whole v2 shell", (), "", "ready"),
    "watchlists": FlagInfo("Your own symbol lists", "charts", "watchlists on Charts / screens", (), "", "ready"),
    "decision_tools": FlagInfo("Risk-of-ruin, what-if, correlation, expected move", "charts", "tools on the chart", (), "", "ready"),
    "backtest_models": FlagInfo("More realistic fills in backtests (opt-in per run)", "trading", "options in Advanced", (), "", "ready"),
    "backtest_stored_history": FlagInfo("Backtests on ATP's stored daily history when no broker is connected", "trading",
                                        "Daily backtests run without a broker login; the result says where the prices came from",
                                        ("ui_v2",), "the daily history stored (the nightly download or the import)", "decision"),
    "screener_etf": FlagInfo("ETF screen", "screens", "a screen", ("screener_v2",), "daily history", "ready"),
    "alerts_whatsapp_dlt": FlagInfo("WhatsApp and SMS alerts (India DLT)", "data", "channels", (), "DLT registration", "unfinished"),
    "growth_v2": FlagInfo("Your plan and usage, a discipline card to share, activation in the admin console", "platform", "a usage meter on Settings › Account & plan, a Share my week card on the Journal, Activation in the admin console", ("ui_v2",), "", "unfinished"),
    "alert_webhooks": FlagInfo("Send your own alerts to your own tools (signed webhooks)", "data", "Webhooks in Settings › Alerts: add an https address, copy the secret once, send a test", ("ui_v2",), "", "ready"),
    "learn": FlagInfo("Learn: short lessons, the glossary and video links (education only)", "platform", "a Learn page in the sidebar", ("ui_v2",), "video links in Admin › Users & business (optional)", "unfinished"),
    "marketing_site": FlagInfo("Public pages: Features, How it works, About (no claims, no prices)", "platform", "links under the sign-in screen and the public pages", (), "", "unfinished"),
    "onboarding_emails": FlagInfo("First-week e-mails: connect broker, first trading bot, first signal review", "platform", "nothing in the app; e-mails in the first week", (), "the platform mailer (PLATFORM_SMTP_*) and PUBLIC_BASE_URL", "unfinished"),
    "referrals": FlagInfo("Invite a friend: each person's own invite link", "platform", "an Invite a friend card in Settings › Account & plan", ("ui_v2",), "the reward wording in Admin › Users & business", "decision"),
    "plan_limits_v2": FlagInfo("Per-plan limits for watchlists, reports, Lab", "platform", "limits", (), "plans", "decision"),
    "paper_report_card": FlagInfo("Paper report card: your own closed paper trades per bot and per strategy", "trading", "a Paper report card on the Trading bot page, with a Share image button", ("ui_v2",), "", "ready"),
    "setup_recall": FlagInfo("Similar setups: your own past trades with the same setup, and what the context said vs what happened", "trading", "a Similar setups fold on each journal trade and on each entry approval in Signals", ("ui_v2",), "", "ready"),
    "read_chart": FlagInfo("Read this chart: the areas, their state, the pullback state and the commitment candle", "charts", "a Read this chart button on the Charts page toolbar", ("ui_v2", "market_zones"), "nightly zones", "ready"),
    "why_no_trade": FlagInfo("Why no trade: each bot's last closed bars with the reason it did or did not trade", "trading", "a Why no trade list on each bot (Bots page)", ("ui_v2",), "", "ready"),
}


# ADM-1 (owner 30 Oct: "too many controls, very confusing"): the admin Feature flags list groups by these finer
# categories (what a trader would call that part of the app), each folded; `area` stays the broad area. Every flag has
# exactly one group (tests/test_adm1_flag_groups.py). A flag without a group falls back to its area in the list.
GROUPS: dict[str, str] = {
    "safety": "Safety and LIVE",
    "orders": "Orders and positions",
    "paper": "Paper trading and journal",
    "backtest": "Backtests",
    "charts": "Charts and chart tools",
    "screens": "Screens: the screener and its builder",
    "screen_library": "Screens: ready-made screens and ideas",
    "scan_actions": "Scan matches: charts and actions",
    "ai": "AI",
    "alerts": "Alerts and notifications",
    "market_data": "Market data and history",
    "look": "Look and layout",
    "accounts": "Sign-up, plans and growth",
    "help": "Help, legal and status",
    "developers": "Developers and marketplace",
}

FLAG_GROUP: dict[str, str] = {
    # Safety and LIVE
    "live_trading": "safety",
    "phase0_paper_only": "safety",
    "approval_only_entries": "safety",
    "risk_by_default": "safety",
    "vix_gate": "safety",
    "portfolio_risk": "safety",
    "portfolio_risk_caps": "safety",
    "experience_levels": "safety",
    # Orders and positions
    "smart_execution": "orders",
    "terminal": "orders",
    "term_margin_pair": "orders",
    "term_roll": "orders",
    "term_strike_area": "orders",
    "term_whatif": "orders",
    "multi_account": "orders",
    "broker_app_links": "orders",
    "expiry_checklist": "orders",
    "my_day_card": "orders",
    "options_builder": "orders",
    "options_builder_saved": "orders",
    # Paper trading and journal
    "paper_account": "paper",
    "ladder_evidence": "paper",
    "paper_report_card": "paper",
    "discipline_engine": "paper",
    "setup_recall": "paper",
    "why_no_trade": "paper",
    "trade_engine": "paper",
    "cost_meter": "paper",
    "strategy_templates": "paper",
    # Backtests
    "backtest_optimizer": "backtest",
    "backtest_models": "backtest",
    "backtest_stored_history": "backtest",
    # Charts and chart tools
    "market_zones": "charts",
    "chart_workspace": "charts",
    "chart_analyze": "charts",
    "chart_analyze_strikes": "charts",
    "chart_analyze_share": "charts",
    "chart_analyze_hybrid": "charts",
    "chart_price_action": "charts",
    "chart_trades_layer": "charts",
    "chart_contracts": "charts",
    "chart_futures_basis": "charts",
    "watchlists": "charts",
    "decision_tools": "charts",
    "read_chart": "charts",
    "chart_studies": "charts",
    # Screens: the screener and its builder
    "screener_v2": "screens",
    "screener_intrabar": "screens",
    "screener_builder_u2": "screens",
    "screener_vectorized": "screens",
    "screenql_amw_functions": "screens",
    "strategy_lab_pine": "screens",
    "screener_board_views": "screens",
    "screener_copilot": "screens",
    "screener_venues": "screens",
    "screener_rankings": "screens",
    "screener_backtest": "screens",
    "screener_history": "screens",
    "screener_tx2": "screens",
    "screener_fundamentals": "screens",
    "screener_ownership": "screens",
    "screener_waves": "screens",
    # Screens: ready-made screens and ideas
    "screener_gallery": "screen_library",
    "idea_library": "screen_library",
    "screener_breakouts": "screen_library",
    "screener_breakouts_bk3": "screen_library",
    "screener_breakouts_bk4": "screen_library",
    "screener_breakouts_fitness": "screen_library",
    "screener_legends": "screen_library",
    "screener_legends_lg2": "screen_library",
    "screener_legends_lg3": "screen_library",
    "screener_etf": "screen_library",
    # Scan matches: charts and actions
    "evidence_charts": "scan_actions",
    "evidence_context": "scan_actions",
    "execution_core": "scan_actions",
    "scan_then": "scan_actions",
    "scan_auto_paper": "scan_actions",
    # AI
    "ai_copilot": "ai",
    "market_thesis": "ai",
    "thesis_stock_targets": "ai",
    "ai_agent": "ai",
    "ai_research": "ai",
    "ai_one_key": "ai",
    "ai_platform_key": "ai",
    "copilot_agent": "ai",
    "vision_byok": "ai",
    "vision_byok_v3": "ai",
    "signal_language_v2": "ai",
    "support_bot": "ai",
    "ai_multiview": "ai",
    # Alerts and notifications
    "telegram_inbound": "alerts",
    "daily_rhythm": "alerts",
    "alerts_stale_guard": "alerts",
    "alerts_reliable_outbox": "alerts",
    "mobile_push": "alerts",
    "alerts_whatsapp_dlt": "alerts",
    "alert_webhooks": "alerts",
    # Market data and history
    "news_feed": "market_data",
    "context_panel": "market_data",
    "data_hub": "market_data",
    "byob_data_tier": "market_data",
    "history_import": "market_data",
    "history_backfill": "market_data",
    "history_intraday": "market_data",
    "chain_history": "market_data",
    "market_store_reads": "market_data",
    "live_push": "market_data",
    "markets_view": "market_data",
    # Look and layout
    "ui_v2": "look",
    "colour_v2": "look",
    "colour_v3": "look",
    # Sign-up, plans and growth
    "self_signup": "accounts",
    "beta_cohort_gate": "accounts",
    "growth_v2": "accounts",
    "referrals": "accounts",
    "plan_limits_v2": "accounts",
    "onboarding_emails": "accounts",
    "marketing_site": "accounts",
    # Help, legal and status
    "help_centre": "help",
    "legal_pages": "help",
    "learn": "help",
    "public_status_page": "help",
    "status_page": "help",
    # Developers and marketplace
    "public_api": "developers",
    "marketplace": "developers",
    "marketplace_ai_listings": "developers",
}


# P2-1 (docs/design/P2_1_FLAG_AUDIT.md, owner ✅ 26 Oct, R6): the fate of every "unfinished" flag, shown in the admin
# list. FINISH: the roadmap item that finishes it. KEEP_OFF: stays off and hidden, with the reason. REMOVED_FROM_VIEW:
# stays off, its code kept, and leaves the admin "Turn on" list (FA-2) - the server refuses to turn it on.
FINISH: Dict[str, str] = {
    "data_hub": "P1-5", "live_push": "AUD-G2.2", "markets_view": "FN7", "strategy_templates": "P2-3 / FN4", "idea_library": "P2-3 / FN4",
    "strategy_lab_pine": "FN3", "daily_rhythm": "P2-4 / FN7", "chart_contracts": "DATA-H / FN7",
    "chart_futures_basis": "DATA-H / FN7", "help_centre": "P2-6 (early, once the support contact is set)",
    "chart_analyze": "P2-5", "multi_account": "M45 (PAPER built; LIVE only on the owner's words)",
    "term_roll": "TERM-2 R1", "term_whatif": "TERM-2 R2", "term_strike_area": "TERM-2 R3", "term_margin_pair": "TERM-2 R4",
    "trade_engine": "M66 (owner only, after parity is green)",
    "copilot_agent": "M72 PR-2 (the answer format with cards)",
    "ai_platform_key": "M72 PR-1b (owner's organisation only until each plan's budget and the daily cap are set)",
}
KEEP_OFF: Dict[str, str] = {
    "chart_analyze_strikes": "strikes next to a setup read close to advice; after P2-5 is on",
    "chart_analyze_share": "download only, no share link (CPV-6)",
    "chart_analyze_hybrid": "until the owner's ✅ (M59 CR-4)",
    "market_thesis": "needs market memory; per-symbol scenarios sit close to advice",
    "ai_research": "AI cost per draft; after FN5",
    "support_bot": "answers from help pages that are not complete; after P2-6",
    "screener_fundamentals": "no licensed fundamentals source",
    "screener_legends_lg3": "needs company fundamentals",
    "screener_ownership": "no ownership data source",
    "screener_waves": "wave reading is method content; after the wave templates / P5-1",
    "mobile_push": "needs the FCM key and the app (P5-4)",
    "alerts_whatsapp_dlt": "needs DLT registration (owner / legal)",
    "growth_v2": "plans come with payments (P5-2)",
    "learn": "education text needs the legal review (P3-5)",
    "marketing_site": "public launch only (P5-3)",
    "onboarding_emails": "only with self-signup (P5-3) and the platform mailer",
}
REMOVED_FROM_VIEW: Dict[str, str] = {
    "vision_byok_v3": "superseded by the Copilot chart reading (CP-V); kept off, code kept",
}


def fate_for(name: str) -> Dict[str, str]:
    """P2-1: {"fate": finish | keep_off | removed_from_view, "fate_note": ...} for a flag that has one, else {}."""
    if name in REMOVED_FROM_VIEW:
        return {"fate": "removed_from_view", "fate_note": f"Removed from view: {REMOVED_FROM_VIEW[name]}"}
    if name in FINISH:
        return {"fate": "finish", "fate_note": f"Finishing in {FINISH[name]}"}
    if name in KEEP_OFF:
        return {"fate": "keep_off", "fate_note": f"Kept off: {KEEP_OFF[name]}"}
    return {}


def info_for(name: str) -> Dict[str, object]:
    """The admin list's fields for one flag (a flag missing here reads as unfinished, in Accounts and platform)."""
    info = FLAG_INFO.get(name) or FlagInfo(name, "platform", "", (), "", "unfinished")
    return {"plain": info.plain, "area": info.area, "area_label": AREAS[info.area],
            "group": FLAG_GROUP.get(name, info.area), "group_label": GROUPS.get(FLAG_GROUP.get(name, ""), AREAS[info.area]),
            "turns_on": info.turns_on,
            "depends_on": list(info.depends_on), "needs": info.needs, "status": info.status,
            "status_label": STATUSES[info.status], **fate_for(name)}


__all__ = ["AREAS", "FINISH", "FLAG_GROUP", "FLAG_INFO", "GROUPS", "KEEP_OFF", "REMOVED_FROM_VIEW", "STATUSES", "FlagInfo", "fate_for", "info_for"]
