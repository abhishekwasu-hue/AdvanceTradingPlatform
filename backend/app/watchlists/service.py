"""AMW-2 (docs/design/AMW_PORT.md §4.1): personal named watchlists - an ordered list of symbols the Screener and the
Scanner can load as their universe.

Re-implemented from the AMW reference's behaviour (named lists per user, unique by name, upper-case symbols, another
user sees none of them); none of its code is used. Every read and write here filters on the caller's tenant AND user
(BT1b): a member never reads or changes another member's list, an organisation never another organisation's.
A watchlist is a list of names: nothing here scans, alerts, proposes or places an order.

UI2-4a1 (the Charts page's watchlist panel), additive so AMW-2's callers see no change:
- rows are items `{symbol, exchange}`; an item's symbol may hold spaces ("NIFTY 50" on NSE_INDEX). `symbols` stays the
  plain symbol list the Screener and the Scanner read, kept equal to the items' symbols on every write;
- `position` orders the lists (PUT /api/watchlists/order);
- `version` is bumped by every content write; a PUT naming a stale version is refused (409).
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional

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
FREE_MAX_WATCHLISTS = 5    # AMW-1's Free value: the limit when the plan has no `max_watchlists` (fail closed)
MAX_PER_USER = 100         # a hard cap whatever the plan (Business's provisional max_watchlists), so storage stays bounded
SYMBOL_RE = re.compile(r"^[A-Z0-9&._:-]{1,40}$")
_SPLIT = re.compile(r"[\s,;]+")
# An item's symbol (UI2-4a1): like a chart drawing's symbol, spaces allowed ("NIFTY 50", "NIFTY BANK").
ITEM_SYMBOL_RE = re.compile(r"^[A-Z0-9 &._:-]{1,40}$")
# The chart's exchanges (app/charts/drawings.py) plus the index segments the brokers list indices under.
EXCHANGES = ("NSE", "BSE", "NFO", "BFO", "MCX", "CDS", "CRYPTO", "NYSE", "NASDAQ", "NSE_INDEX", "BSE_INDEX")
DEFAULT_EXCHANGE = "NSE"


def clean_name(value: str) -> str:
    name = " ".join((value or "").split())
    if not name or len(name) > NAME_MAX or "<" in name or ">" in name:
        raise ValueError(f"a watchlist name is 1-{NAME_MAX} characters without < or >")
    return name


def _one_spaced(value: str) -> str:
    return " ".join(str(value or "").split()).upper()


def clean_symbols(values: Iterable[str], keep: Iterable[str] = ()) -> List[str]:
    """Upper-case, trimmed, de-duplicated in first-seen order; an entry may itself hold several symbols separated by
    commas, semicolons or spaces (a pasted list). Refuses an invalid symbol by name, and more than MAX_SYMBOLS.
    `keep`: symbols with a space the list already holds as items (UI2-4a1) - an entry equal to one of them stays whole,
    so an older client re-saving "NIFTY 50" does not turn it into "NIFTY" and "50"."""
    out: List[str] = []
    seen = set()
    bad: List[str] = []
    whole = {k for k in keep if " " in k}
    for value in values:
        spaced = _one_spaced(value)
        for raw in ([spaced] if spaced in whole else _SPLIT.split(str(value or ""))):
            symbol = raw.strip().upper()
            if not symbol or symbol in seen:
                continue
            if not (SYMBOL_RE.match(symbol) or symbol in whole):
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


def clean_items(values: Iterable[Mapping[str, Any]]) -> List[Dict[str, str]]:
    """Items `{symbol, exchange}` in the given order: symbol upper-case with inner spaces collapsed (spaces allowed),
    exchange from EXCHANGES (default NSE), de-duplicated on (symbol, exchange). Refuses an invalid one by name, and more
    than MAX_SYMBOLS."""
    out: List[Dict[str, str]] = []
    seen = set()
    bad: List[str] = []
    for value in values:
        symbol = _one_spaced(value.get("symbol") or "")
        exchange = str(value.get("exchange") or DEFAULT_EXCHANGE).strip().upper()
        if not ITEM_SYMBOL_RE.match(symbol) or exchange not in EXCHANGES:
            bad.append(f"{symbol[:40] or '(empty)'}@{exchange[:12]}")
            continue
        if (symbol, exchange) in seen:
            continue
        seen.add((symbol, exchange))
        out.append({"symbol": symbol, "exchange": exchange})
    if bad:
        raise ValueError(f"invalid item(s): {', '.join(bad[:5])}{' ...' if len(bad) > 5 else ''} - a symbol is letters, digits, "
                         f"spaces and & . _ : - (up to 40), on one of {', '.join(EXCHANGES)}")
    if len(out) > MAX_SYMBOLS:
        raise ValueError(f"at most {MAX_SYMBOLS} symbols in one watchlist; this one has {len(out)}")
    return out


def plain_symbols(items: Iterable[Mapping[str, str]]) -> List[str]:
    """The `symbols` view of items: each symbol once, in order (the Screener and the Scanner read this)."""
    return list(dict.fromkeys(i["symbol"] for i in items))


def _stored_items(row: WatchlistRecord) -> List[Dict[str, str]]:
    try:
        value = json.loads(row.items_json) if row.items_json else []
    except (TypeError, ValueError):
        return []
    if not isinstance(value, list):
        return []
    return [{"symbol": v["symbol"], "exchange": v["exchange"]} for v in value
            if isinstance(v, dict) and isinstance(v.get("symbol"), str) and isinstance(v.get("exchange"), str)]


def items_from_symbols(symbols: Iterable[str], previous: Iterable[Mapping[str, str]] = ()) -> List[Dict[str, str]]:
    """Items for a plain symbol list, in its order: a symbol the list already held keeps every row it had (RELIANCE on
    NSE and on BSE stays two rows), a new symbol is one row on NSE."""
    known: Dict[str, List[Dict[str, str]]] = {}
    for item in previous:
        rows = known.setdefault(item["symbol"], [])
        if item["exchange"] not in {r["exchange"] for r in rows}:
            rows.append({"symbol": item["symbol"], "exchange": item["exchange"]})
    out: List[Dict[str, str]] = []
    for symbol in dict.fromkeys(symbols):
        out.extend(known.get(symbol) or [{"symbol": symbol, "exchange": DEFAULT_EXCHANGE}])
    return out


def items_of(row: WatchlistRecord) -> List[Dict[str, str]]:
    """The list's rows. `items_json` when it agrees with `symbols_json`; otherwise (a list saved before UI2-4a1, or
    changed by an older release that writes `symbols_json` only) the items are rebuilt from `symbols_json`, keeping the
    exchange each symbol had. So `symbols_json` stays the one source every release agrees on."""
    symbols = symbols_of(row)
    stored = _stored_items(row)
    if stored and plain_symbols(stored) == symbols:
        return stored
    return items_from_symbols(symbols, stored)


def as_dict(row: WatchlistRecord) -> Dict[str, Any]:
    """`count` is AMW-2's: the number of distinct symbols, always len(`symbols`) (what the Screener and the Scanner
    load). `items` may hold more rows than that - one symbol on two exchanges is two rows - so a UI counting rows uses
    len(`items`)."""
    symbols = symbols_of(row)
    return {"id": row.id, "name": row.name, "symbols": symbols, "count": len(symbols), "items": items_of(row),
            "position": row.position, "version": row.version or 1,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None}


def _own(tenant_id: int, user_id: int):
    return (WatchlistRecord.tenant_id == tenant_id, WatchlistRecord.user_id == user_id)


async def count_own(session: AsyncSession, tenant_id: int, user_id: int) -> int:
    return int(await session.scalar(select(func.count()).select_from(WatchlistRecord).where(*_own(tenant_id, user_id))) or 0)


async def list_own(session: AsyncSession, tenant_id: int, user_id: int) -> List[WatchlistRecord]:
    # The user's order first (PUT /api/watchlists/order), then lists never placed, by name.
    rows = await session.scalars(select(WatchlistRecord).where(*_own(tenant_id, user_id))
                                 .order_by(WatchlistRecord.position.is_(None), WatchlistRecord.position,
                                           func.lower(WatchlistRecord.name), WatchlistRecord.id))
    return list(rows)


async def get_own(session: AsyncSession, tenant_id: int, user_id: int, watchlist_id: int) -> Optional[WatchlistRecord]:
    return await session.scalar(select(WatchlistRecord).where(WatchlistRecord.id == watchlist_id, *_own(tenant_id, user_id)))


async def name_taken(session: AsyncSession, tenant_id: int, user_id: int, name: str, *, except_id: Optional[int] = None) -> bool:
    """Names are unique per user ignoring case ("Banks" and "banks" are the same list name). The database constraint is
    exact; this check runs under the per-user lock the routes take, so two racing saves are checked one after the other."""
    query = select(WatchlistRecord.id).where(*_own(tenant_id, user_id), func.lower(WatchlistRecord.name) == name.lower())
    if except_id is not None:
        query = query.where(WatchlistRecord.id != except_id)
    return (await session.scalar(query.limit(1))) is not None


def new_row(tenant_id: int, user_id: int, name: str, items: List[Dict[str, str]]) -> WatchlistRecord:
    now = datetime.now(timezone.utc)
    return WatchlistRecord(tenant_id=tenant_id, user_id=user_id, name=name, symbols_json=json.dumps(plain_symbols(items)),
                           items_json=json.dumps(items), version=1, created_at=now, updated_at=now)


def apply(row: WatchlistRecord, name: str, items: List[Dict[str, str]]) -> None:
    """Replaces the name and the rows; `symbols_json` is rewritten from the items so both views agree."""
    row.name = name
    row.items_json = json.dumps(items)
    row.symbols_json = json.dumps(plain_symbols(items))
    row.version = (row.version or 1) + 1
    row.updated_at = datetime.now(timezone.utc)


def reorder(rows: List[WatchlistRecord], ids: List[int]) -> None:
    """Positions 0..n-1: the named lists first in the given order, then the others in their current order (a list made
    on another device since the order was shown is kept, after the named ones). The lists' content and version are
    unchanged: the order is not an edit of any list."""
    by_id = {r.id: r for r in rows}
    ordered = [by_id[i] for i in ids] + [r for r in rows if r.id not in set(ids)]
    for index, row in enumerate(ordered):
        row.position = index


async def check_plan_limit(session: AsyncSession, tenant_id: int, current: int) -> None:
    """The plan's `max_watchlists` (AMW-1), enforced only while `plan_limits_v2` exists and is on. Without AMW-1 on main
    this does nothing: an unknown flag reads as ON in `flag_enabled`, so its existence is checked first. Fails closed: a
    plan without the field (or with a non-integer value) gets the Free value, FREE_MAX_WATCHLISTS.

    TODO(AMW-1): once AMW-1 (`claude/amw-1-plan-limits`) is on main, replace the body with
    `await plan_limits.check_can_add_watchlist(session, await plan_limits.load_tenant(session, tenant_id), current)`.
    """
    from app.platform.controls import FEATURE_FLAGS, flag_enabled
    if PLAN_FLAG not in FEATURE_FLAGS:
        return
    if not await flag_enabled(session, PLAN_FLAG, tenant_id):
        return
    tenant = await plan_limits.load_tenant(session, tenant_id)
    plan = get_plan(tenant.plan)
    allowed = getattr(plan, "max_watchlists", None)
    if not isinstance(allowed, int) or isinstance(allowed, bool):
        allowed = FREE_MAX_WATCHLISTS
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
