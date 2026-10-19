"""AMW-2 (docs/design/AMW_PORT.md §4.1): personal named watchlists - an ordered list of symbols the Screener and the
Scanner can load as their universe.

Re-implemented from the AMW reference's behaviour (named lists per user, unique by name, upper-case symbols, another
user sees none of them); none of its code is used. Every read and write here filters on the caller's tenant AND user
(BT1b): a member never reads or changes another member's list, an organisation never another organisation's.
A watchlist is a list of names: nothing here scans, alerts, proposes or places an order.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import WatchlistRecord
from app.plans import limits as plan_limits
from app.plans.registry import get_plan

FLAG = "watchlists"
# AMW-1's flag: while it is on, the plan's `max_watchlists` caps how many lists a user keeps.
PLAN_FLAG = "plan_limits_v2"
NAME_MAX = 60
MAX_SYMBOLS = 500          # symbols in one list (the Screener runs the first 50 of it; the Scanner takes them all)
MAX_PER_USER = 100         # a hard cap whatever the plan (Business's provisional max_watchlists), so storage stays bounded
SYMBOL_RE = re.compile(r"^[A-Z0-9&._:-]{1,40}$")
_SPLIT = re.compile(r"[\s,;]+")


def clean_name(value: str) -> str:
    name = " ".join((value or "").split())
    if not name or len(name) > NAME_MAX or "<" in name or ">" in name:
        raise ValueError(f"a watchlist name is 1-{NAME_MAX} characters without < or >")
    return name


def clean_symbols(values: Iterable[str]) -> List[str]:
    """Upper-case, trimmed, de-duplicated in first-seen order; an entry may itself hold several symbols separated by
    commas, semicolons or spaces (a pasted list). Refuses an invalid symbol by name, and more than MAX_SYMBOLS."""
    out: List[str] = []
    seen = set()
    bad: List[str] = []
    for value in values:
        for raw in _SPLIT.split(str(value or "")):
            symbol = raw.strip().upper()
            if not symbol or symbol in seen:
                continue
            if not SYMBOL_RE.match(symbol):
                bad.append(raw.strip()[:40])
                continue
            seen.add(symbol)
            out.append(symbol)
    if bad:
        raise ValueError(f"invalid symbol(s): {', '.join(bad[:5])}{' ...' if len(bad) > 5 else ''} - letters, digits and & . _ : - only")
    if len(out) > MAX_SYMBOLS:
        raise ValueError(f"at most {MAX_SYMBOLS} symbols in one watchlist; this one has {len(out)}")
    return out


def symbols_of(row: WatchlistRecord) -> List[str]:
    try:
        value = json.loads(row.symbols_json or "[]")
    except (TypeError, ValueError):
        return []
    return [str(s) for s in value if isinstance(s, str)] if isinstance(value, list) else []


def as_dict(row: WatchlistRecord) -> Dict[str, Any]:
    symbols = symbols_of(row)
    return {"id": row.id, "name": row.name, "symbols": symbols, "count": len(symbols),
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None}


def _own(tenant_id: int, user_id: int):
    return (WatchlistRecord.tenant_id == tenant_id, WatchlistRecord.user_id == user_id)


async def count_own(session: AsyncSession, tenant_id: int, user_id: int) -> int:
    return int(await session.scalar(select(func.count()).select_from(WatchlistRecord).where(*_own(tenant_id, user_id))) or 0)


async def list_own(session: AsyncSession, tenant_id: int, user_id: int) -> List[WatchlistRecord]:
    rows = await session.scalars(select(WatchlistRecord).where(*_own(tenant_id, user_id))
                                 .order_by(func.lower(WatchlistRecord.name), WatchlistRecord.id))
    return list(rows)


async def get_own(session: AsyncSession, tenant_id: int, user_id: int, watchlist_id: int) -> Optional[WatchlistRecord]:
    return await session.scalar(select(WatchlistRecord).where(WatchlistRecord.id == watchlist_id, *_own(tenant_id, user_id)))


def new_row(tenant_id: int, user_id: int, name: str, symbols: List[str]) -> WatchlistRecord:
    now = datetime.now(timezone.utc)
    return WatchlistRecord(tenant_id=tenant_id, user_id=user_id, name=name, symbols_json=json.dumps(symbols),
                           created_at=now, updated_at=now)


def apply(row: WatchlistRecord, name: str, symbols: List[str]) -> None:
    row.name = name
    row.symbols_json = json.dumps(symbols)
    row.updated_at = datetime.now(timezone.utc)


async def check_plan_limit(session: AsyncSession, tenant_id: int, current: int) -> None:
    """The plan's `max_watchlists` (AMW-1), enforced only while `plan_limits_v2` exists and is on and the plan has the
    field. Without AMW-1 on main this does nothing: an unknown flag reads as ON in `flag_enabled`, so its existence is
    checked first.

    TODO(AMW-1): once AMW-1 (`claude/amw-1-plan-limits`) is on main, replace the body with
    `await plan_limits.check_can_add_watchlist(session, await plan_limits.load_tenant(session, tenant_id), current)`.
    """
    from app.platform.controls import FEATURE_FLAGS, flag_enabled
    if PLAN_FLAG not in FEATURE_FLAGS:
        return
    tenant = await plan_limits.load_tenant(session, tenant_id)
    plan = get_plan(tenant.plan)
    allowed = getattr(plan, "max_watchlists", None)
    if not isinstance(allowed, int) or not await flag_enabled(session, PLAN_FLAG, tenant_id):
        return
    plan_limits.ensure_tenant_active(tenant)
    if current >= allowed:
        hint = "" if plan.id == "business" else " Upgrade the plan to add more."
        raise plan_limits.PlanLimitExceeded(f"The {plan.name} plan allows {allowed} watchlists; you have {current}.{hint}")


async def _usage_counter(session: AsyncSession, tenant_id: int, user_id: int) -> float:
    return float(await count_own(session, tenant_id, user_id))


# The caller's count in Billing (AMW-1's usage counters). TODO(AMW-1): call `plan_limits.register_usage_counter`
# directly once AMW-1 is on main; until then the registry does not exist and nothing is registered.
_register = getattr(plan_limits, "register_usage_counter", None)
if _register is not None:
    _register("watchlists", _usage_counter)
