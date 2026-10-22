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
    "public_api": FlagInfo("API keys for developers", "platform", "Settings › API keys", (), "", "ready"),
    "backtest_optimizer": FlagInfo("Try many settings of a strategy at once", "trading", "Optimise button in Backtest", (), "", "ready"),
    "live_trading": FlagInfo("New LIVE (real money) bots", "trading", "LIVE mode", (), "go-live checklist, two-factor", "decision"),
    "phase0_paper_only": FlagInfo("Paper only: every new real-money entry is refused (exits still work)", "trading", "nothing visible", (), "", "behind_the_scenes"),
    "self_signup": FlagInfo("Anyone can create an account", "platform", "Register link", (), "", "decision"),
    "news_feed": FlagInfo("Live headlines from public sources (marked unverified)", "data", "rows on News & events, News Radar", (), "", "ready"),
    "telegram_inbound": FlagInfo("Approve / reject paper trades from Telegram buttons", "data", "buttons in Telegram messages", (), "a Telegram bot set up", "ready"),
    "market_thesis": FlagInfo("A per-symbol market reading with scenarios", "ai", "thesis card in the Copilot", (), "market memory", "unfinished"),
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
    "beta_cohort_gate": FlagInfo("Sign-up needs an invite code; cap on users", "platform", "invite code field", (), "", "decision"),
    "vision_byok": FlagInfo("Use your own AI key to read chart images", "ai", "Settings › AI › Vision key", (), "a user's AI key", "ready"),
    "vision_byok_v3": FlagInfo("Your AI key also reviews scan alerts and a daily chart review", "ai", "comments on alerts", ("vision_byok",), "", "unfinished"),
    "broker_app_links": FlagInfo("Links to trade in your broker app with a pre-filled basket", "trading", "a link on proposals", (), "", "decision"),
    "discipline_engine": FlagInfo("Automatic journal, behaviour tags, weekly discipline score", "trading", "Journal page", (), "", "ready"),
    "byob_data_tier": FlagInfo("Live data only through each user's own broker; data badges", "data", "data badges", (), "broker data terms", "decision"),
    "history_import": FlagInfo("Import stored daily / 1-minute history", "data", "nothing visible", (), "the off-site history store", "behind_the_scenes"),
    "history_backfill": FlagInfo("Nightly NSE end-of-day download", "data", "nothing visible", (), "", "behind_the_scenes"),
    "history_intraday": FlagInfo("Owner's own 1-minute history", "data", "nothing visible", (), "owner broker session", "behind_the_scenes"),
    "approval_only_entries": FlagInfo("Every automatic entry waits for your Approve", "trading", "Signals waiting for you", ("paper_account",), "", "ready"),
    "paper_account": FlagInfo("Your own paper account and progress ladder", "trading", "paper account card, Telegram /status", (), "", "ready"),
    "risk_by_default": FlagInfo("Plan-before-trade ticket, risk-based size, daily loss lockout", "trading", "paper ticket", (), "", "ready"),
    "vix_gate": FlagInfo("A VIX spike pauses new bullish index entries", "trading", "a note on blocked entries", (), "VIX feed", "ready"),
    "screener_gallery": FlagInfo("Ready-made screens to browse", "screens", "gallery on Find setups", ("screener_v2",), "", "ready"),
    "screener_vectorized": FlagInfo("Faster screens (same results)", "screens", "nothing visible", ("screener_v2",), "", "behind_the_scenes"),
    "screenql_amw_functions": FlagInfo("Extra screen functions (divergence, swings, candles)", "screens", "builder functions", ("screener_v2",), "", "ready"),
    "alerts_stale_guard": FlagInfo("No alert is sent on old data", "data", "nothing visible", (), "", "behind_the_scenes"),
    "alerts_reliable_outbox": FlagInfo("Alerts never sent twice, retried if lost", "data", "nothing visible", (), "", "behind_the_scenes"),
    "support_bot": FlagInfo("How-to help bot", "ai", "Help button", (), "help pages", "unfinished"),
    "legal_pages": FlagInfo("Privacy, Terms, Risk disclosure and acceptance", "platform", "legal pages, sign-up checkboxes", (), "placeholders filled", "decision"),
    "screener_breakouts": FlagInfo("Breakout screens, set 1", "screens", "screens in the gallery", ("screener_v2",), "", "ready"),
    "screener_breakouts_bk3": FlagInfo("Breakout screens, set 2", "screens", "screens", ("screener_breakouts",), "", "ready"),
    "screener_breakouts_bk4": FlagInfo("Breakout screens, set 3", "screens", "screens", ("screener_breakouts",), "", "ready"),
    "screener_breakouts_fitness": FlagInfo("What followed past breakouts", "screens", "a history box", ("screener_breakouts",), "shared daily data", "ready"),
    "evidence_context": FlagInfo("OI walls, trend strip and replay on evidence charts", "screens", "more on match charts", ("evidence_charts",), "", "ready"),
    "evidence_charts": FlagInfo("Each screen match drawn on a chart, also on Telegram", "screens", "match charts", ("screener_v2",), "", "ready"),
    "public_status_page": FlagInfo("A public status page", "platform", "/status", (), "", "ready"),
    "market_zones": FlagInfo("Nightly demand / supply and S/R zones on the chart", "charts", "chart layer, zone functions", (), "nightly job", "ready"),
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
    "screener_copilot": FlagInfo("Build and run screens from the Copilot", "screens", "screen box", ("screener_v2", "ai_copilot"), "", "ready"),
    "screener_venues": FlagInfo("Refuse fields a venue does not carry", "screens", "clearer errors", ("screener_v2",), "", "behind_the_scenes"),
    "screener_ownership": FlagInfo("Promoter / FII / DII stakes and events in screens", "screens", "fields", ("screener_v2",), "ownership data", "unfinished"),
    "screener_rankings": FlagInfo("Rank a universe 0-100", "screens", "rankings", ("screener_v2",), "", "ready"),
    "screener_backtest": FlagInfo("Back-test a screen over time", "screens", "screen backtest", ("screener_v2",), "", "ready"),
    "screener_history": FlagInfo("Who matched on a past date, and what followed", "screens", "history", ("screener_v2",), "", "ready"),
    "portfolio_risk": FlagInfo("Risk view of your paper book", "trading", "a card on My positions", (), "", "ready"),
    "portfolio_risk_caps": FlagInfo("Caps on new paper entries by sector and open risk", "trading", "blocked-entry messages", ("portfolio_risk",), "", "ready"),
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
    "screener_etf": FlagInfo("ETF screen", "screens", "a screen", ("screener_v2",), "daily history", "ready"),
    "alerts_whatsapp_dlt": FlagInfo("WhatsApp and SMS alerts (India DLT)", "data", "channels", (), "DLT registration", "unfinished"),
    "plan_limits_v2": FlagInfo("Per-plan limits for watchlists, reports, Lab", "platform", "limits", (), "plans", "decision"),
}


def info_for(name: str) -> Dict[str, object]:
    """The admin list's fields for one flag (a flag missing here reads as unfinished, in Accounts and platform)."""
    info = FLAG_INFO.get(name) or FlagInfo(name, "platform", "", (), "", "unfinished")
    return {"plain": info.plain, "area": info.area, "area_label": AREAS[info.area], "turns_on": info.turns_on,
            "depends_on": list(info.depends_on), "needs": info.needs, "status": info.status,
            "status_label": STATUSES[info.status]}


__all__ = ["AREAS", "FLAG_INFO", "STATUSES", "FlagInfo", "info_for"]
