"""Owner decision 23 Oct: ONE admin action, "Enable trader features" - the trader pack.

The pack turns ON, in one audited step, every feature flag a trader needs (the Copilot, the strategy templates and the
Pine Lab, the screens, the idea library, watchlists, the charts' layers, the paper account and its risk tools, and the
`ui_v2` pages they live on), and shows what is ON and whether it can work. It is reversible: the stored state of every
flag it changed is saved first (controls key `trader_pack`, no migration) and "Undo" restores exactly that state.

It NEVER touches a flag in `FORBIDDEN` (the LIVE switches, the Phase 0 lock and the flags the owner turned off on 21 Oct)
nor any flag whose catalog status is "decision" (tests/test_trader_pack.py pins both, and `enable` checks again at run
time). `excluded()` lists every other flag left out, with the reason, for the PR and the admin card.

- `enable` is idempotent: a flag already ON is not written again and its saved "before" state is kept, so a second
  enable never overwrites what Undo restores. A flag ON for an allow-list only keeps that list and becomes ON for all.
- `undo` restores each saved flag to its stored entry from before the first enable (no entry = back to its default),
  then forgets the saved state. With nothing saved it changes nothing.
- `status` answers per flag: ON / OFF, the plain catalog description, and a cheap, real "working" check where one exists
  (counts only - an AI key is never read, decrypted or returned).

Admin-only: the routes (app/platform/routes.py) sit on the SUPER_ADMIN + two-factor admin router. Nothing here places,
blocks or changes an order; PAPER only (Phase 0) is untouched.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.log import write_audit_log
from app.db.models import (AiProviderConfigRecord, BrokerCredentialRecord, InstrumentRecord, MdCandleRecord,
                           SystemWatchlistRecord, User, UserAiKeyRecord)
from app.platform import controls
from app.platform.flag_catalog import FLAG_INFO

KEY_TRADER_PACK = "trader_pack"

# Never in the pack, whatever the catalog says (owner, 23 Oct): LIVE, the Phase 0 lock, the flags turned off on 21 Oct.
FORBIDDEN: frozenset[str] = frozenset({
    "marketplace", "public_api", "self_signup", "scan_auto_paper", "live_trading", "phase0_paper_only",
})

# The pack, in dependency order (a flag's dependencies come before it). Read from the catalog (app/platform/flag_catalog.py):
# "ready" / "behind_the_scenes" flags that serve a trader, and "unfinished" ones only where the code works end to end
# today (marked below; docs/OPEN_QUESTIONS.md TP-2).
PACK: Tuple[str, ...] = (
    # The redesigned app every trader page lives on.
    "ui_v2",
    # Data and alerts.
    "history_backfill",
    "chain_history",           # TM 9.4: records only through the owner's own session into his private store (TM-7)
    "alerts_stale_guard", "alerts_reliable_outbox", "context_panel", "news_feed",
    "markets_view",            # unfinished in the audit: the page and the summary work on the daily data the pack loads
    "telegram_inbound",
    "data_hub",                # TM Part 1 (unfinished in the catalog): one live stream per broker connection, live prices,
                               # the reconnecting / renew notice - works end to end today; OFF = everything as before
    # Paper account, risk and journal (the restrictive tools too: they only ever hold back a new paper entry).
    "paper_account", "approval_only_entries", "risk_by_default", "ladder_evidence", "experience_levels",
    "portfolio_risk", "portfolio_risk_caps", "vix_gate", "discipline_engine", "decision_tools",
    "options_builder", "options_builder_saved", "backtest_optimizer", "backtest_models",
    "smart_execution",         # TM Part 7: hedge-first sequencing, incomplete state, slicing, margin, delivery warning
    "execution_core", "strategy_templates",   # unfinished in the audit: adopt a template as a PAPER strategy works
    # Watchlists and the screener.
    "watchlists",
    "screener_v2", "screener_vectorized", "screener_venues", "screener_gallery", "screener_builder_u2", "screener_intrabar",
    "screenql_amw_functions", "screener_breakouts", "screener_breakouts_bk3", "screener_breakouts_bk4",
    "screener_breakouts_fitness", "screener_legends", "screener_legends_lg2", "screener_board_views", "screener_rankings",
    "screener_history", "screener_backtest", "screener_tx2", "screener_etf", "evidence_charts", "evidence_context",
    "scan_then",
    "strategy_lab_pine",       # unfinished in the audit: translate and "Run as screen" work; never executed
    "idea_library",            # unfinished in the audit: the cards, their scans and the two adoptable ideas work
    # Charts.
    "chart_price_action", "chart_studies", "chart_trades_layer", "chart_workspace", "market_zones",
    # AI (the AI never places, blocks or changes an order).
    "ai_copilot", "ai_one_key",  # ai_one_key: unfinished in the audit; the Connect AI panel works
    "ai_agent", "ai_multiview", "screener_copilot", "vision_byok", "signal_language_v2",
    "daily_rhythm",            # unfinished in the audit: the Dashboard card works; Telegram only by the user's choice
)

# Every other flag, with the reason it is left out (shown in the PR; the forbidden and "decision" ones are computed).
EXCLUDED_REASONS: Dict[str, str] = {
    "public_status_page": "A public page for visitors, not a trader feature",
    "growth_v2": "Plans, usage and activation: a business feature, not a trading one (unfinished)",
    "alert_webhooks": "For people who run their own tools; turned on after the owner's ✅ (AUD-G2.5)",
    "support_bot": "Unfinished: the help pages it answers from are not complete",
    "history_import": "Needs the off-site history store, which is not set up",
    "history_intraday": "The owner's own one-minute history through his broker session",
    "screener_fundamentals": "Needs a company-figures data source, which is not set up",
    "screener_ownership": "Needs an ownership data source, which is not set up",
    "screener_legends_lg3": "Needs company figures (the value and growth cards read them)",
    "alerts_whatsapp_dlt": "Needs the India DLT registration",
    "market_thesis": "Unfinished: needs market memory",
    "ai_research": "Unfinished: the research loop is not complete",
    "vision_byok_v3": "Unfinished: alert reviews and the daily chart review are not complete",
    "screener_waves": "Unfinished: the wave-count work is still open",
    "chart_analyze": "Unfinished: beta, needs an AI key with image support",
    "chart_analyze_strikes": "Unfinished (needs Analyze a chart)",
    "chart_analyze_share": "Unfinished (needs Analyze a chart)",
    "chart_contracts": "Unfinished: needs the nightly download to have run; turn on later",
    "chart_futures_basis": "Unfinished: needs the nightly download to have run; turn on later",
    "terminal": "ON by default for everyone (owner ✅ 25 Oct, T-D1), so the pack does not need it",
}


def is_decision(name: str) -> bool:
    info = FLAG_INFO.get(name)
    return info is not None and info.status == "decision"


def excluded() -> Dict[str, str]:
    """Every registered flag that is not in the pack, with the plain reason."""
    out: Dict[str, str] = {}
    for name in controls.FEATURE_FLAGS:
        if name in PACK:
            continue
        if name in FORBIDDEN:
            out[name] = "Never part of the pack (owner, 23 Oct)"
        elif is_decision(name):
            out[name] = "An owner / compliance decision"
        else:
            out[name] = EXCLUDED_REASONS.get(name, "Not part of the trader pack")
    return out


def _check_pack() -> None:
    bad = [name for name in PACK if name in FORBIDDEN or is_decision(name) or name not in controls.FEATURE_FLAGS]
    if bad:                                     # a programming error: never write anything then
        raise RuntimeError(f"The trader pack holds flags it must never hold: {bad}")


async def _saved(session: AsyncSession) -> Dict[str, Any]:
    value = await controls._get(session, KEY_TRADER_PACK)
    before = value.get("before")
    return value if isinstance(before, dict) and before else {}


async def enable(session: AsyncSession, user: User) -> Dict[str, Any]:
    """Turn every pack flag ON. Returns the flags changed now (empty when everything was already ON)."""
    _check_pack()
    resolved = await controls.feature_flags(session)
    current = await controls._get(session, controls.KEY_FEATURE_FLAGS)
    flags: Dict[str, Any] = dict(current.get("flags") or {})
    saved = await _saved(session)
    before: Dict[str, Optional[Dict[str, Any]]] = dict(saved.get("before") or {})
    changed: List[str] = []
    for name in PACK:
        if resolved[name]["on"]:
            continue
        stored = flags.get(name)
        if name not in before:                  # keep the state from before the FIRST enable
            before[name] = dict(stored) if isinstance(stored, dict) else None
        tenants = sorted({int(t) for t in ((stored or {}).get("tenants") or [])}) if isinstance(stored, dict) else []
        flags[name] = {"on": True, "tenants": tenants}
        changed.append(name)
    if changed:
        await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": flags}, user)
        await controls._set(session, KEY_TRADER_PACK, {
            "before": before, "enabled_at": saved.get("enabled_at") or datetime.now(timezone.utc).isoformat(),
            "enabled_by": saved.get("enabled_by") or user.id}, user)
        for name in changed:
            await write_audit_log(session, None, user.id, "feature_flag_set", f"{name}: on=True (trader pack)")
        await write_audit_log(session, None, user.id, "trader_pack_enabled", ", ".join(changed))
        await session.commit()
    return {"changed": changed}


async def undo(session: AsyncSession, user: User) -> Dict[str, Any]:
    """Restore every flag the pack changed to its stored state from before the first enable."""
    saved = await _saved(session)
    before: Dict[str, Any] = saved.get("before") or {}
    if not before:
        return {"restored": []}
    current = await controls._get(session, controls.KEY_FEATURE_FLAGS)
    flags: Dict[str, Any] = dict(current.get("flags") or {})
    restored: List[str] = []
    for name, prior in before.items():
        if name not in controls.FEATURE_FLAGS:
            continue
        if isinstance(prior, dict):
            flags[name] = prior
            detail = f"{name}: on={bool(prior.get('on'))} tenants={prior.get('tenants') or '-'} (trader pack undo)"
        else:
            flags.pop(name, None)
            detail = f"{name}: back to its default (trader pack undo)"
        restored.append(name)
        await write_audit_log(session, None, user.id, "feature_flag_set", detail)
    await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": flags}, user)
    await controls._set(session, KEY_TRADER_PACK, {}, user)
    await write_audit_log(session, None, user.id, "trader_pack_undone", ", ".join(restored) or "-")
    await session.commit()
    return {"restored": restored}



# --- the "working" checks: cheap reads, counts only --------------------------------------------------------------

async def _exists(session: AsyncSession, stmt) -> bool:
    return (await session.scalar(stmt.limit(1))) is not None


async def _facts(session: AsyncSession) -> Dict[str, Any]:
    lists = int(await session.scalar(select(func.count()).select_from(SystemWatchlistRecord)
                                     .where(SystemWatchlistRecord.count > 0)) or 0)
    org_keys = int(await session.scalar(select(func.count()).select_from(AiProviderConfigRecord).where(
        AiProviderConfigRecord.enabled.is_(True), AiProviderConfigRecord.encrypted_api_key.is_not(None))) or 0)
    user_keys = int(await session.scalar(select(func.count()).select_from(UserAiKeyRecord)) or 0)
    brokers = int(await session.scalar(select(func.count(func.distinct(BrokerCredentialRecord.tenant_id)))) or 0)
    instruments = await _exists(session, select(InstrumentRecord.id))
    daily = await _exists(session, select(MdCandleRecord.ts).where(MdCandleRecord.timeframe == "day"))
    return {"lists": lists, "org_keys": org_keys, "user_keys": user_keys, "brokers": brokers,
            "instruments": instruments, "daily": daily}


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _check(name: str, f: Dict[str, Any]) -> Optional[Tuple[str, str]]:
    """(state, plain text) for a flag with a real check; None when there is nothing cheap to check."""
    if name == "watchlists":
        return ("working", f"{_plural(f['lists'], 'ready-made list')} with symbols") if f["lists"] else \
            ("needs_setup", "The ready-made lists are empty: refresh them on the System tab")
    if name == "screener_v2":
        if f["brokers"]:
            return "working", f"{_plural(f['brokers'], 'organisation')} with a broker connection for screen data"
        if f["daily"]:
            return "working", "No broker connected yet; screens run on the stored daily data"
        return "needs_setup", "No broker connected and no stored daily data yet"
    if name in ("ai_copilot", "ai_agent", "ai_multiview", "screener_copilot", "ai_one_key"):
        if f["org_keys"] or f["user_keys"]:
            return "working", (f"AI keys: {_plural(f['org_keys'], 'organisation')}, {_plural(f['user_keys'], 'user')}"
                               " (the keys are never shown)")
        return "needs_setup", "No AI key yet: answers come from the built-in rules until a key is connected"
    if name in ("options_builder", "options_builder_saved"):
        return ("working", "The instrument list is loaded") if f["instruments"] else \
            ("needs_setup", "The instrument list is empty: it loads with the daily instrument sync")
    if name in ("history_backfill", "markets_view", "market_zones"):
        return ("working", "Daily bars are stored") if f["daily"] else \
            ("needs_setup", "No daily bars stored yet: the nightly download fills them")
    return None


async def status(session: AsyncSession) -> Dict[str, Any]:
    resolved = await controls.feature_flags(session)
    facts = await _facts(session)
    rows = []
    for name in PACK:
        on = bool(resolved[name]["on"])
        info = FLAG_INFO.get(name)
        check = _check(name, facts) if on else None
        state, text = check if check else (("on", "On, nothing to check") if on else ("off", "Off"))
        rows.append({"name": name, "plain": info.plain if info else name, "on": on, "state": state, "check": text})
    saved = await _saved(session)
    return {"flags": rows, "all_on": all(r["on"] for r in rows), "on_count": sum(1 for r in rows if r["on"]),
            "total": len(rows), "can_undo": bool(saved),
            "enabled_at": saved.get("enabled_at") if saved else None}


__all__ = ["EXCLUDED_REASONS", "FORBIDDEN", "KEY_TRADER_PACK", "PACK", "enable", "excluded", "is_decision", "status", "undo"]
