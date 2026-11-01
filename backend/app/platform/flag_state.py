"""M80 (owner order 1 Nov): the truthful status of every feature flag, for the admin Feature flags list only.

The old `FlagInfo.status` (app/platform/flag_catalog.py) is the static audit label of 22 Oct; it stays as it was for
every reader of it (the trader pack's "decision" rule, the P2-1 fates). This module is the re-audit against the code of
main (docs/design/M80_FLAG_AUDIT.md holds the evidence per flag) and splits the one badge into exact states:

- `ready`             - built and tested; a user gets it when it is on;
- `behind_the_scenes` - built and tested; nothing new to see;
- `needs_setup`       - built; works once the named item is set up (`item`), in the named place (`where`);
- `not_built`         - a part is missing (`item` says what, `order` the roadmap item): the admin switch is disabled and
                        the server refuses to turn it on (409); turning it off always works;
- `decision`          - an owner / compliance decision (`item` says which).

`probes` names the live checks (F3) the admin list runs for the flag: cheap reads of this server's own state (a row in
the database, a setting, whether a package imports) - never a network call, never a secret or any part of one. The
probes answer only inside the admin flag list (SUPER_ADMIN + two-factor, app/platform/routes.py).
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Literal, NamedTuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User

FlagState = Literal["ready", "behind_the_scenes", "needs_setup", "not_built", "decision"]

STATE_LABELS: dict[str, str] = {
    "ready": "Ready",
    "behind_the_scenes": "Ready (behind the scenes)",
    "needs_setup": "Needs your setup",
    "not_built": "Not built yet",
    "decision": "Owner decision",
}
# The states a user gets something from today (no reason required).
READY_STATES = ("ready", "behind_the_scenes")

PROBE_LABELS: dict[str, str] = {
    "ai_key": "AI key",
    "broker": "Broker connected",
    "chart_engine": "Private package on this server",
    "signal_engine": "Private package on this server",
    "telegram": "Telegram set up",
}

# Places, written once so every row names them the same way.
AI = "Settings › AI"
ALERTS = "Settings › Alerts"
BROKER = "Settings › Broker"
HOST = "Owner host block"
LEGAL = "Owner / legal"
SUPPORT = "Admin › Support"
BUSINESS = "Admin › Users & business"
SAFETY = "Admin › Controls › Safety"


class FlagStatus(NamedTuple):
    state: FlagState
    item: str = ""            # needs_setup: the item; not_built: what is missing; decision: which decision; else a note
    where: str = ""           # where to do it (needs_setup always names one)
    order: str = ""           # the roadmap item that finishes it (not_built always names one)
    probes: tuple[str, ...] = ()


def _ready(note: str = "", where: str = "", probes: tuple[str, ...] = ()) -> FlagStatus:
    return FlagStatus("ready", note, where, "", probes)


def _behind(note: str = "", probes: tuple[str, ...] = ()) -> FlagStatus:
    return FlagStatus("behind_the_scenes", note, "", "", probes)


def _setup(item: str, where: str, probes: tuple[str, ...] = ()) -> FlagStatus:
    return FlagStatus("needs_setup", item, where, "", probes)


def _not_built(missing: str, order: str) -> FlagStatus:
    return FlagStatus("not_built", missing, "", order)


def _decision(which: str, where: str = "") -> FlagStatus:
    return FlagStatus("decision", which, where)


# One entry per flag in `controls.FEATURE_FLAGS` (tests/test_m80_flag_state.py checks both lists match).
FLAG_STATUS: dict[str, FlagStatus] = {
    "ai_copilot": _ready("Answers with the built-in parser; an AI key gives AI answers", AI, ("ai_key",)),
    "marketplace": _decision("The marketplace is the last phase; off until you decide"),
    "public_api": _ready(),
    "backtest_optimizer": _ready(),
    "live_trading": _decision("LIVE only on your words: the go-live checklist and two-factor", SAFETY),
    "phase0_paper_only": _behind(),
    "self_signup": _decision("Open sign-up: off for the invite-only beta"),
    "news_feed": _ready(),
    "telegram_inbound": _setup("Telegram bot", ALERTS, ("telegram",)),
    "market_thesis": _decision("Per-symbol scenarios sit close to advice; needs market memory (kept off)"),
    "colour_v2": _ready(),
    "colour_v3": _ready(),
    "context_panel": _ready("A FII / DII source is optional"),
    "options_builder": _ready(),
    "options_builder_saved": _ready(),
    "marketplace_ai_listings": _decision("Part of the marketplace (last phase)"),
    "thesis_stock_targets": _decision("No price targets for single stocks (kept off)"),
    "screener_v2": _ready("Runs on a connected broker or on the stored data"),
    "screener_intrabar": _ready(),
    "screener_builder_u2": _ready(),
    "ai_agent": _setup("AI key", AI, ("ai_key",)),
    "ai_research": _not_built("No screen to start a study or read its results; only the server side and its worker exist", "FN5"),
    "ai_one_key": _ready(),
    "cost_meter": _ready(),
    "daily_rhythm": _ready("The Dashboard card works; the messages need Telegram", ALERTS, ("telegram",)),
    "data_hub": _setup("Broker with a live price stream (Upstox or Zerodha)", BROKER, ("broker",)),
    "smart_execution": _ready(),
    "status_page": _ready(),
    "beta_cohort_gate": _decision("Invite codes and the user cap for the closed beta"),
    "vision_byok": _ready("Each user adds their own AI key", AI),
    "vision_byok_v3": _decision("Removed from view (owner ✅ 26 Oct): replaced by the Copilot chart reading; code kept"),
    "broker_app_links": _decision("Kept off until the compliance review"),
    "discipline_engine": _ready(),
    "byob_data_tier": _decision("Data licence: live data only through each user's own broker"),
    "history_import": _setup("Off-site history store", HOST),
    "history_backfill": _behind(),
    "history_intraday": _behind("Records through your own broker session", ("broker",)),
    "chain_history": _behind("Records through your own broker session", ("broker",)),
    "market_store_reads": _behind(),
    "approval_only_entries": _ready(),
    "paper_account": _ready(),
    "risk_by_default": _ready(),
    "vix_gate": _ready(),
    "screener_gallery": _ready(),
    "screener_vectorized": _behind(),
    "screenql_amw_functions": _ready(),
    "strategy_lab_pine": _ready("Translated and run as a screen; never executed"),
    "signal_language_v2": _ready(),
    "idea_library": _ready(),
    "alerts_stale_guard": _behind(),
    "alerts_reliable_outbox": _behind(),
    "support_bot": _not_built("The help pages it answers from are not complete", "P2-6"),
    "help_centre": _setup("Support WhatsApp number or e-mail", SUPPORT),
    "legal_pages": _decision("The lawyer's text: the legal pages still have open placeholders", LEGAL),
    "screener_breakouts": _ready(),
    "screener_breakouts_bk3": _ready(),
    "screener_breakouts_bk4": _ready(),
    "screener_breakouts_fitness": _ready(),
    "evidence_context": _ready(),
    "evidence_charts": _ready(),
    "public_status_page": _ready(),
    "live_push": _setup("Broker with a live price stream (Upstox or Zerodha)", BROKER, ("broker",)),
    "market_zones": _ready(),
    "markets_view": _ready("Reads the nightly market data download"),
    "mobile_push": _setup("Phone-notification key on the server and the Android app published", HOST),
    "multi_account": _ready("PAPER fan-out; LIVE fan-out only with your LIVE switch"),
    "my_day_card": _ready(),
    "screener_legends": _ready(),
    "screener_board_views": _ready(),
    "scan_auto_paper": _decision("Paper orders from scans without the Approve step"),
    "screener_legends_lg2": _ready(),
    "screener_fundamentals": _decision("A licensed source of company figures (none chosen)"),
    "screener_legends_lg3": _decision("A licensed source of company figures (none chosen)"),
    "scan_then": _ready(),
    "chart_workspace": _ready(),
    "execution_core": _behind(),
    "ladder_evidence": _ready(),
    "experience_levels": _ready(),
    "expiry_checklist": _ready(),
    "screener_copilot": _ready(),
    "screener_venues": _behind(),
    "screener_ownership": _decision("A licensed source of ownership data (none chosen)"),
    "screener_waves": _decision("Wave reading is method content; kept off until the chart reader port"),
    "term_margin_pair": _ready("Your broker's margin where it offers one, else a labelled estimate"),
    "term_roll": _ready(),
    "term_strike_area": _ready("Reads the stored areas"),
    "term_whatif": _ready(),
    "terminal": _ready(),
    "trade_engine": _setup("Private package on the server", HOST, ("signal_engine",)),
    "strategy_templates": _ready(),
    "screener_rankings": _ready(),
    "screener_backtest": _ready(),
    "screener_history": _ready(),
    "portfolio_risk": _ready(),
    "portfolio_risk_caps": _ready(),
    "chart_analyze": _setup("Private package on the server; an AI key with image support", f"{HOST}; {AI}",
                            ("chart_engine", "ai_key")),
    "chart_analyze_strikes": _decision("Strikes next to a setup read close to advice (kept off)"),
    "chart_analyze_share": _decision("Download only, no share link (kept off)"),
    "chart_analyze_hybrid": _decision("Advanced analysis waits for your ✅"),
    "chart_studies": _ready(),
    "chart_price_action": _ready(),
    "ai_multiview": _ready(),
    "chart_trades_layer": _ready(),
    "chart_contracts": _ready("Reads the nightly end-of-day download"),
    "chart_futures_basis": _ready("Reads the nightly end-of-day download"),
    "screener_tx2": _ready(),
    "ui_v2": _ready(),
    "watchlists": _ready(),
    "decision_tools": _ready(),
    "backtest_models": _ready(),
    "backtest_stored_history": _decision("Other organisations wait for the lawyer on the exchange data terms", LEGAL),
    "screener_etf": _ready(),
    "alerts_whatsapp_dlt": _setup("India DLT registration", LEGAL),
    "growth_v2": _decision("Plans come with payments (last phase)"),
    "alert_webhooks": _ready(),
    "learn": _decision("The lesson text needs the legal review", LEGAL),
    "marketing_site": _decision("Public pages: for the public launch only"),
    "onboarding_emails": _setup("Platform mailer and the public address", HOST),
    "referrals": _decision("Prices and rewards: you set them before launch", BUSINESS),
    "plan_limits_v2": _decision("Pricing: the limits per plan"),
    "paper_report_card": _ready(),
    "setup_recall": _ready(),
    "read_chart": _ready(),
    "why_no_trade": _ready(),
}

_UNKNOWN = FlagStatus("not_built", "Not described in the flag list yet", "", "M80")


def status_for(name: str) -> FlagStatus:
    return FLAG_STATUS.get(name, _UNKNOWN)


def is_not_built(name: str) -> bool:
    return status_for(name).state == "not_built"


def not_built_reason(name: str) -> str:
    entry = status_for(name)
    return f"This feature is not built yet: {entry.item} ({entry.order}). Turning it on would give users nothing."


def fields_for(name: str) -> dict[str, object]:
    """The admin list's fields: `state` (one of STATE_LABELS), its label, `state_reason`, `state_where`, `state_order`
    and the probe ids. The old `status` / `status_label` fields stay as they were (flag_catalog.info_for)."""
    entry = status_for(name)
    return {"state": entry.state, "state_label": STATE_LABELS[entry.state], "state_reason": entry.item,
            "state_where": entry.where, "state_order": entry.order, "probes": list(entry.probes)}


# --- F3: the live checks ---------------------------------------------------------------------------------------------
# Each probe reads only this server's own state for the admin's own organisation. None calls out, none returns or logs
# a key, a token or any part of one: the answer is a bool.

async def _ai_key(session: AsyncSession, user: User) -> bool:
    from app.db.models import AiProviderConfigRecord, UserAiKeyRecord
    own = await session.scalar(select(UserAiKeyRecord.id).where(UserAiKeyRecord.user_id == user.id).limit(1))
    if own is not None:
        return True
    org = await session.scalar(select(AiProviderConfigRecord.id).where(
        AiProviderConfigRecord.tenant_id == user.tenant_id, AiProviderConfigRecord.enabled.is_(True),
        AiProviderConfigRecord.provider != "rule_based", AiProviderConfigRecord.encrypted_api_key.is_not(None)).limit(1))
    return org is not None


async def _broker(session: AsyncSession, user: User) -> bool:
    from app.brokers.connect import CONNECTED, connection_view
    from app.db.models import BrokerCredentialRecord
    records = await session.scalars(select(BrokerCredentialRecord).where(BrokerCredentialRecord.tenant_id == user.tenant_id))
    return any(connection_view(record)["status"] == CONNECTED for record in records)


async def _chart_engine(session: AsyncSession, user: User) -> bool:
    from app.chart_analyze import provider
    return provider.load() is not None


async def _signal_engine(session: AsyncSession, user: User) -> bool:
    from app.engine_provider import loader
    return loader.load() is not None


async def _telegram(session: AsyncSession, user: User) -> bool:
    from app.core import config
    from app.core.enums import AlertChannelType
    from app.db.models import AlertChannelRecord
    if config.ATP_TELEGRAM_BOT_TOKEN:                     # the platform's one-tap bot is set on this server
        return True
    own = await session.scalar(select(AlertChannelRecord.id).where(
        AlertChannelRecord.tenant_id == user.tenant_id, AlertChannelRecord.enabled.is_(True),
        AlertChannelRecord.channel_type == AlertChannelType.TELEGRAM.value).limit(1))
    return own is not None


PROBES: dict[str, Callable[[AsyncSession, User], Awaitable[bool]]] = {
    "ai_key": _ai_key, "broker": _broker, "chart_engine": _chart_engine, "signal_engine": _signal_engine,
    "telegram": _telegram,
}


async def run_probes(session: AsyncSession, user: User, ids: list[str]) -> dict[str, bool]:
    """{probe id: True / False} for each id asked (each probe runs once, whatever the number of flags naming it)."""
    results: dict[str, bool] = {}
    for probe_id in ids:
        if probe_id in PROBES and probe_id not in results:
            results[probe_id] = bool(await PROBES[probe_id](session, user))
    return results


def checks_for(name: str, results: dict[str, bool]) -> list[dict[str, object]]:
    """The ✓ / ✗ lines of one flag: [{id, label, ok}] from `run_probes`'s results."""
    return [{"id": p, "label": PROBE_LABELS[p], "ok": results[p]} for p in status_for(name).probes if p in results]


__all__ = ["FLAG_STATUS", "PROBES", "PROBE_LABELS", "READY_STATES", "STATE_LABELS", "FlagStatus", "checks_for",
           "fields_for", "is_not_built", "not_built_reason", "run_probes", "status_for"]
