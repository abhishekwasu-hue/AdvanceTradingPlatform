"""Part D4 (rule IN-SEBI.static_ip.registered): registered static egress IPs per broker.

SEBI's retail-algo framework accepts API orders only from a static IP the broker has on file for the client. Each
organisation records, per broker, a PRIMARY IP and optionally a BACKUP (`egress_ips`); every change is appended to
`egress_ip_changes`, and the rule-set's `max_changes_per_week` caps how often a role may change (brokers limit it).
The server's own egress IP is operator configuration (SERVER_EGRESS_IP). With STATIC_IP_REQUIRED_FOR_LIVE on, a LIVE
entry is refused unless that IP is registered for the deployment's broker; exits are never refused (ADR-0004).
Two organisations registering the same IP get a warning (brokers can refuse a shared IP).
"""
import ipaddress
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from sqlalchemy import distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.compliance import rules
from app.core import config
from app.db.models import EgressIpChangeRecord, EgressIpRecord

RULE = "IN-SEBI.static_ip.registered"
ROLES = ("PRIMARY", "BACKUP")


# Broker names the Static IP card saved before it used the registry's spelling (it offered `angelone`). Rows stored under
# an old spelling are read as the registry name, and the next save of that role renames the row in place.
LEGACY_BROKER_NAMES = {"angelone": "angel_one"}


class StaticIpError(ValueError):
    pass


def canonical_broker(name: Optional[str]) -> str:
    """The registry spelling of a stored or requested broker name (lower case; `angelone` -> `angel_one`)."""
    value = (name or "").strip().lower()
    return LEGACY_BROKER_NAMES.get(value, value)


def _spellings(broker_name: str) -> List[str]:
    """The registry name first, then any old spelling rows may still be stored under."""
    return [broker_name] + [old for old, new in LEGACY_BROKER_NAMES.items() if new == broker_name]


def normalise_ip(value: str) -> str:
    try:
        ip = ipaddress.ip_address((value or "").strip())
    except ValueError as exc:
        raise StaticIpError(f"not an IP address: {value!r}") from exc
    if not ip.is_global:
        raise StaticIpError(f"{ip} is not a public address - brokers whitelist the server's public egress IP")
    return str(ip)


def _week_ago(now: datetime) -> datetime:
    return now - timedelta(days=7)


async def changes_this_week(session: AsyncSession, tenant_id: int, broker_name: str, role: str, now: Optional[datetime] = None) -> int:
    now = now or datetime.now(timezone.utc)
    return int(await session.scalar(select(func.count()).select_from(EgressIpChangeRecord).where(
        EgressIpChangeRecord.tenant_id == tenant_id, EgressIpChangeRecord.broker_name.in_(_spellings(canonical_broker(broker_name))),
        EgressIpChangeRecord.role == role, EgressIpChangeRecord.old_ip.is_not(None),        # the first registration is not a change
        EgressIpChangeRecord.changed_at >= _week_ago(now))) or 0)


async def set_ip(session: AsyncSession, tenant_id: int, broker_name: str, role: str, ip: str, *, user_id: Optional[int] = None,
                 registered_at: Optional[datetime] = None, now: Optional[datetime] = None) -> EgressIpRecord:
    role = (role or "").upper()
    if role not in ROLES:
        raise StaticIpError(f"role must be one of {ROLES}")
    if role == "BACKUP" and not rules.load().param(RULE, "backup_ip_allowed", True):
        raise StaticIpError("backup IPs are not allowed by the rule-set")
    ip = normalise_ip(ip)
    broker_name = canonical_broker(broker_name)
    now = now or datetime.now(timezone.utc)
    rows = {r.broker_name: r for r in await session.scalars(select(EgressIpRecord).where(
        EgressIpRecord.tenant_id == tenant_id, EgressIpRecord.broker_name.in_(_spellings(broker_name)), EgressIpRecord.role == role))}
    # The row under the registry name wins; otherwise a row saved under an old spelling is this role's row. A legacy row
    # shadowed by a registry row is removed, so the owner is never left with a stale IP they cannot edit.
    record = next((rows[n] for n in _spellings(broker_name) if n in rows), None)
    shadowed = [row for row in rows.values() if row is not record]
    for row in shadowed:
        await session.delete(row)
    if record is not None and record.broker_name != broker_name:
        record.broker_name = broker_name
    if record is not None and record.ip == ip:
        if shadowed or record in session.dirty:                         # only the old spelling was fixed
            await session.commit()
            await session.refresh(record)
        return record
    if record is not None:
        limit = int(rules.load().param(RULE, "max_changes_per_week"))
        if await changes_this_week(session, tenant_id, broker_name, role, now) >= limit:
            raise StaticIpError(f"the {role} IP for {broker_name} was already changed {limit} time(s) in the last 7 days "
                                f"({RULE}); brokers limit IP changes - try again later")
    session.add(EgressIpChangeRecord(tenant_id=tenant_id, broker_name=broker_name, role=role, old_ip=record.ip if record else None,
                                     new_ip=ip, changed_by=user_id, changed_at=now))
    if record is None:
        record = EgressIpRecord(tenant_id=tenant_id, broker_name=broker_name, role=role, ip=ip)
        session.add(record)
    record.ip = ip
    record.registered_at = registered_at
    record.updated_by = user_id
    await session.commit()
    await session.refresh(record)
    return record


async def registered(session: AsyncSession, tenant_id: int) -> List[EgressIpRecord]:
    """The organisation's rows, one per (broker, role). A row under an old spelling is dropped when the same role also has
    a row under the registry name, so a stale legacy IP can never satisfy the LIVE check or show twice."""
    rows = list(await session.scalars(select(EgressIpRecord).where(EgressIpRecord.tenant_id == tenant_id)
                                      .order_by(EgressIpRecord.broker_name, EgressIpRecord.role)))
    current = {(r.broker_name, r.role) for r in rows if canonical_broker(r.broker_name) == r.broker_name}
    return [r for r in rows if canonical_broker(r.broker_name) == r.broker_name
            or (canonical_broker(r.broker_name), r.role) not in current]


async def shared_with_others(session: AsyncSession, tenant_id: int) -> Dict[str, int]:
    """ip -> number of *other* organisations that registered the same IP."""
    mine = {r.ip for r in await registered(session, tenant_id)}
    if not mine:
        return {}
    rows = await session.execute(select(EgressIpRecord.ip, func.count(distinct(EgressIpRecord.tenant_id))).where(
        EgressIpRecord.ip.in_(mine), EgressIpRecord.tenant_id != tenant_id).group_by(EgressIpRecord.ip))
    return {ip: int(n) for ip, n in rows.all() if n}


async def live_entry_problem(session: AsyncSession, tenant_id: int, broker_name: str) -> Optional[str]:
    """Why a LIVE entry to `broker_name` must not go out, or None. Only enforced with STATIC_IP_REQUIRED_FOR_LIVE."""
    if not config.STATIC_IP_REQUIRED_FOR_LIVE:
        return None
    server_ip = config.SERVER_EGRESS_IP
    if not server_ip:
        return f"Static IP required ({RULE}) but SERVER_EGRESS_IP is not configured - LIVE entries paused; exits continue"
    ips = {r.ip for r in await registered(session, tenant_id) if canonical_broker(r.broker_name) == canonical_broker(broker_name)}
    if server_ip not in ips:
        return (f"This server's IP {server_ip} is not registered for {broker_name} ({RULE}) - register it with the broker "
                "and on Settings > Static IP; LIVE entries paused, exits continue")
    return None
