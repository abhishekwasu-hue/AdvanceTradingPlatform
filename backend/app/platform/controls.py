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
}


async def feature_flags(session: AsyncSession) -> Dict[str, Dict]:
    stored = (await _get(session, KEY_FEATURE_FLAGS)).get("flags") or {}
    result: Dict[str, Dict] = {}
    for name, description in FEATURE_FLAGS.items():
        entry = stored.get(name) or {}
        result[name] = {"on": bool(entry.get("on", True)), "tenants": sorted(int(t) for t in entry.get("tenants") or []),
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
