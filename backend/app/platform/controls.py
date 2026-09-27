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
