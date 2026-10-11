"""Phase M / V4.13: platform-wide operator controls, read on every new entry.

- **Maintenance mode**: no new entries anywhere (PAPER or LIVE); exits, monitoring and the API
  keep working; the UI shows the operator's message. Distinct from the global kill switch: this is
  planned and announced, the kill switch is the emergency stop.
- **Disabled brokers**: no new LIVE entries through a named broker (its API is degraded, or the
  operator is rotating credentials). Exits still go through.
- **Per-user trading disable** lives on `users.trading_disabled_reason` (set by the tenant OWNER
  or a platform admin) and is checked here too.
"""
import json
from typing import Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.log import write_audit_log
from app.db.models import PlatformControlRecord, User

KEY_MAINTENANCE = "maintenance_mode"
KEY_DISABLED_BROKERS = "disabled_brokers"


async def _get(session: AsyncSession, key: str) -> Dict:
    row = await session.get(PlatformControlRecord, key)
    if row is None:
        return {}
    try:
        return json.loads(row.value_json or "{}")
    except ValueError:
        return {}


async def _set(session: AsyncSession, key: str, value: Dict, user: Optional[User]) -> None:
    row = await session.get(PlatformControlRecord, key)
    if row is None:
        row = PlatformControlRecord(key=key)
        session.add(row)
    row.value_json = json.dumps(value)
    row.updated_by = user.id if user else None


async def status(session: AsyncSession) -> Dict:
    maintenance = await _get(session, KEY_MAINTENANCE)
    brokers = await _get(session, KEY_DISABLED_BROKERS)
    return {"maintenance_mode": bool(maintenance.get("on")), "maintenance_message": maintenance.get("message") or None,
            "disabled_brokers": sorted(brokers.get("names") or [])}


async def set_maintenance(session: AsyncSession, user: User, *, on: bool, message: Optional[str]) -> Dict:
    await _set(session, KEY_MAINTENANCE, {"on": on, "message": (message or "").strip()[:300]}, user)
    await write_audit_log(session, None, user.id, "maintenance_mode_" + ("on" if on else "off"), (message or "")[:200])
    await session.commit()
    return await status(session)


async def set_disabled_brokers(session: AsyncSession, user: User, names: List[str]) -> Dict:
    clean = sorted({n.strip().lower() for n in names if n and n.strip()})
    await _set(session, KEY_DISABLED_BROKERS, {"names": clean}, user)
    await write_audit_log(session, None, user.id, "brokers_disabled", ",".join(clean) or "-")
    await session.commit()
    return await status(session)


async def entry_blocks(session: AsyncSession, *, user: Optional[User], mode: str, broker_name: Optional[str]) -> List[str]:
    """Reasons a new entry is refused by platform controls (empty when none apply)."""
    reasons: List[str] = []
    current = await status(session)
    if current["maintenance_mode"]:
        reasons.append("Platform maintenance: no new entries" + (f" - {current['maintenance_message']}" if current["maintenance_message"] else ""))
    if mode == "LIVE" and broker_name and broker_name.lower() in current["disabled_brokers"]:
        reasons.append(f"Broker {broker_name} is disabled by the platform operator - LIVE entries refused")
    if user is not None and user.trading_disabled_reason:
        reasons.append(f"Trading disabled for this user: {user.trading_disabled_reason}")
    return reasons


# --- Phase N4: feature flags ---------------------------------------------------------------------
# Kill-flag semantics: a feature is ON unless the operator turned its flag off, optionally keeping
# it on for an allow-list of tenants (staged rollouts / beta access). Unknown flags are ON.
KEY_FEATURE_FLAGS = "feature_flags"

FEATURE_FLAGS: Dict[str, str] = {
    "ai_copilot": "AI strategy drafts, regime engine and monitoring agent",
    "marketplace": "Strategy marketplace listings and subscriptions",
    "public_api": "Public REST API keys and /api/public/v1",
    "backtest_optimizer": "Parameter optimisation grid runs",
    "live_trading": "New LIVE deployments (PAPER unaffected; exits always work)",
    "self_signup": "Public registration (off: invite-only)",
    "news_feed": "Live news feed from public sources (unverified items, shared ingest, tenant-key classification)",
    "telegram_inbound": "Telegram commands and approve/reject buttons from whitelisted chats (PAPER + reduce/pause only)",
    "market_thesis": "Per-symbol market thesis with scenarios and a shadow-only size multiplier (never applied)",
    "options_builder": "Options Strategy Builder: payoff, T+0 model, PoP and strike suggestions (research only, places no orders)",
    # P0.8-D compliance switches, OFF until the operator has the SEBI RA/IA position cleared.
    "marketplace_ai_listings": "Allow AI-originated strategies (AI drafts, strategist adoptions) to be listed on the marketplace",
    "thesis_stock_targets": "Show price targets and a confidence % in the thesis of a single stock (indices always show them)",
    # S1d (ADR-0021): the ScreenQL screener API (validate, saved screens, runs on server data). OFF: the scanner is unchanged.
    "screener_v2": "Screener v2: ScreenQL validation, saved screens and runs on the organisation's broker data",
    "screener_intrabar": "Intrabar alerts: rules may fire on the bar still forming (at most once per bar); bar-close stays the default",
    # H-C2 (ADR-0019): the Copilot agent (typed read tools, bounded loop). OFF: the single-shot Copilot is unchanged.
    "ai_agent": "Copilot agent: the AI reads typed, read-only tools (market memory, news, positions, P&L, risk limits) before answering",
}
# Phase BB/BE: flags that start OFF until the operator turns them on (everything else is a kill flag).
DEFAULT_OFF_FLAGS = frozenset({"news_feed", "telegram_inbound", "market_thesis", "marketplace_ai_listings", "thesis_stock_targets", "screener_v2", "screener_intrabar", "ai_agent"})


async def feature_flags(session: AsyncSession) -> Dict[str, Dict]:
    stored = (await _get(session, KEY_FEATURE_FLAGS)).get("flags") or {}
    result: Dict[str, Dict] = {}
    for name, description in FEATURE_FLAGS.items():
        entry = stored.get(name) or {}
        result[name] = {"on": bool(entry.get("on", name not in DEFAULT_OFF_FLAGS)), "tenants": sorted(int(t) for t in entry.get("tenants") or []),
                        "description": description}
    return result


async def flag_enabled(session: AsyncSession, name: str, tenant_id: Optional[int] = None) -> bool:
    flags = await feature_flags(session)
    entry = flags.get(name)
    if entry is None:
        return True
    if entry["on"]:
        return True
    return tenant_id is not None and tenant_id in entry["tenants"]


async def set_flag(session: AsyncSession, user: User, name: str, *, on: bool, tenants: Optional[List[int]] = None) -> Dict[str, Dict]:
    if name not in FEATURE_FLAGS:
        raise ValueError(f"Unknown feature flag '{name}' - known: {sorted(FEATURE_FLAGS)}")
    current = await _get(session, KEY_FEATURE_FLAGS)
    flags = current.get("flags") or {}
    flags[name] = {"on": on, "tenants": sorted({int(t) for t in (tenants or [])})}
    await _set(session, KEY_FEATURE_FLAGS, {"flags": flags}, user)
    await write_audit_log(session, None, user.id, "feature_flag_set", f"{name}: on={on} tenants={flags[name]['tenants'] or '-'}")
    await session.commit()
    return await feature_flags(session)


async def features_for_tenant(session: AsyncSession, tenant_id: Optional[int]) -> Dict[str, bool]:
    return {name: await flag_enabled(session, name, tenant_id) for name in FEATURE_FLAGS}


async def require_flag(session: AsyncSession, name: str, tenant_id: Optional[int]) -> None:
    """Raises 503 with a machine-readable header when the operator has turned a feature off."""
    if not await flag_enabled(session, name, tenant_id):
        from fastapi import HTTPException
        raise HTTPException(status_code=503, detail=f"The '{name}' feature is currently disabled by the platform operator",
                            headers={"X-Feature-Disabled": name})


# --- Phase V1: risk ceilings ----------------------------------------------------------------------
# The Risk Guardian spec keeps every default admin-configurable *with hard ceilings*: a tenant may
# set its own risk per trade, daily loss, portfolio risk and drawdown pause level, but never above
# these, and never a stop cool-down shorter than the minimum. Enforced at the settings API and,
# belt and braces, clamped at runtime on every entry.
KEY_RISK_CEILINGS = "risk_ceilings"
RISK_CEILINGS_DEFAULT: Dict[str, float] = {
    "risk_per_trade_pct": 2.0,        # spec R2: hard ceiling 2%
    "max_daily_loss_pct": 5.0,        # spec R5 default 2%; the ceiling leaves room for a wider tenant setting
    "max_portfolio_risk_pct": 10.0,   # spec R4 default 6%
    "dd_level_2_pct": 25.0,           # the pause level cannot be pushed beyond a quarter of the peak
    "min_stop_cooldown_minutes": 0.0, # spec R10: an operator may force a minimum cool-down
}
CEILING_MAX_KEYS = ("risk_per_trade_pct", "max_daily_loss_pct", "max_portfolio_risk_pct", "dd_level_2_pct")


async def risk_ceilings(session: AsyncSession) -> Dict[str, float]:
    stored = await _get(session, KEY_RISK_CEILINGS)
    out = dict(RISK_CEILINGS_DEFAULT)
    for key in out:
        if key in stored:
            try:
                out[key] = float(stored[key])
            except (TypeError, ValueError):
                continue
    return out


async def set_risk_ceilings(session: AsyncSession, user: User, values: Dict[str, float]) -> Dict[str, float]:
    current = await risk_ceilings(session)
    for key, value in values.items():
        if key not in RISK_CEILINGS_DEFAULT:
            raise ValueError(f"Unknown risk ceiling '{key}' - known: {sorted(RISK_CEILINGS_DEFAULT)}")
        number = float(value)
        if number < 0 or (key in CEILING_MAX_KEYS and number <= 0):
            raise ValueError(f"{key} must be a positive number")
        current[key] = number
    await _set(session, KEY_RISK_CEILINGS, current, user)
    await write_audit_log(session, None, user.id, "risk_ceilings_set", ", ".join(f"{k}={v:g}" for k, v in sorted(current.items())))
    await session.commit()
    return current


def ceiling_violations(cfg, ceilings: Dict[str, float]) -> List[str]:
    """Why a tenant's RiskConfig may not be saved as given."""
    errors: List[str] = []
    for key in CEILING_MAX_KEYS:
        value = float(getattr(cfg, key, 0.0) or 0.0)
        if value > ceilings[key]:
            errors.append(f"{key} {value:g} exceeds the platform ceiling {ceilings[key]:g}")
    minimum = ceilings.get("min_stop_cooldown_minutes", 0.0)
    if float(getattr(cfg, "stop_cooldown_minutes", 0) or 0) < minimum:
        errors.append(f"stop_cooldown_minutes must be at least {minimum:g}")
    return errors


def clamp_config(cfg, ceilings: Dict[str, float]):
    """(config within the ceilings, notes saying what was clamped). Runtime safety net for
    settings saved before a ceiling was lowered."""
    updates = {}
    notes: List[str] = []
    for key in CEILING_MAX_KEYS:
        value = float(getattr(cfg, key, 0.0) or 0.0)
        if value > ceilings[key]:
            updates[key] = ceilings[key]
            notes.append(f"{key} {value:g} capped at the platform ceiling {ceilings[key]:g}")
    minimum = ceilings.get("min_stop_cooldown_minutes", 0.0)
    if float(getattr(cfg, "stop_cooldown_minutes", 0) or 0) < minimum:
        updates["stop_cooldown_minutes"] = int(minimum)
        notes.append(f"stop_cooldown_minutes raised to the platform minimum {minimum:g}")
    return (cfg.model_copy(update=updates) if updates else cfg), notes


# --- Phase X: marketplace terms ---------------------------------------------------------------------

KEY_MARKETPLACE_TERMS = "marketplace_terms"
MARKETPLACE_TERMS_DEFAULT: Dict[str, float] = {
    "platform_fee_pct": 20.0,        # the platform's share of every paid listing, frozen per listing at publish
    "min_payout": 500.0,             # a creator can request a payout once this much is available (INR)
    "max_listing_price": 50000.0,    # the highest one-time price a creator may ask (INR)
}


async def marketplace_terms(session: AsyncSession) -> Dict[str, float]:
    stored = await _get(session, KEY_MARKETPLACE_TERMS)
    out = dict(MARKETPLACE_TERMS_DEFAULT)
    for key in out:
        if key in stored:
            try:
                out[key] = float(stored[key])
            except (TypeError, ValueError):
                continue
    return out


async def set_marketplace_terms(session: AsyncSession, user: User, values: Dict[str, float]) -> Dict[str, float]:
    current = await marketplace_terms(session)
    for key, value in values.items():
        if key not in MARKETPLACE_TERMS_DEFAULT:
            raise ValueError(f"Unknown marketplace term '{key}' - known: {sorted(MARKETPLACE_TERMS_DEFAULT)}")
        number = float(value)
        if key == "platform_fee_pct" and not 0 <= number <= 90:
            raise ValueError("platform_fee_pct must be between 0 and 90")
        if key != "platform_fee_pct" and number < 0:
            raise ValueError(f"{key} cannot be negative")
        current[key] = number
    await _set(session, KEY_MARKETPLACE_TERMS, current, user)
    await write_audit_log(session, None, user.id, "marketplace_terms_set", ", ".join(f"{k}={v:g}" for k, v in sorted(current.items())))
    await session.commit()
    return current
