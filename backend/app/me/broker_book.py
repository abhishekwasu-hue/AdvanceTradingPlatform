"""`GET /api/me/broker-book` - "From your broker" (owner 14 Nov "Ho"; flags `ui_v2` + `broker_book`, 404 while either is
off, X-Feature-Disabled names the flag).

What the user's OWN broker reports, read-only: for each broker session he linked himself (never a teammate's, never
another organisation's), the broker's positions, holdings and today's order book, as plain normalised rows. ATP's own
records are not touched and nothing is placed, modified or cancelled here - this module only ever calls
`get_positions`, `get_holdings` and `get_order_book`.

* Each read goes through the broker's existing rate budget (`RateLimitedBroker` with one `RateBudget` per credential,
  kept here between requests, joining the credential's shared broker windows), and each answer is kept for
  `CACHE_SECONDS`; "Refresh" reads again once the kept answer is `REFRESH_MIN_SECONDS` old.
* A broker error is never raised past here: each list carries a plain sentence instead (a static-IP refusal, a session
  to renew, a busy broker, a list the broker does not offer, or no answer).
* `?parts=positions,holdings,orders` names the lists to read (default all), so a page asks only for its own list.

Reads are not Phase 0 / B1 gated (they place nothing); LIVE switches are untouched.
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.brokers import catalog
from app.brokers.exceptions import BrokerAuthenticationError, BrokerBudgetUsed
from app.db.models import BrokerCredentialRecord, User
from app.db.session import get_session

router = APIRouter(prefix="/api/me", tags=["me"])

FLAG = "broker_book"
PAGE_FLAG = "ui_v2"
PARTS: Tuple[str, ...] = ("positions", "holdings", "orders")
_READ = {"positions": "get_positions", "holdings": "get_holdings", "orders": "get_order_book"}
_WHAT = {"positions": "positions", "holdings": "holdings", "orders": "order list"}
CACHE_SECONDS = 20.0
REFRESH_MIN_SECONDS = 5.0
_CACHE_MAX = 2000

# docs/brokers/EASIEST_LOGIN.md (unverified): the brokers whose READ calls need ATP's static IP registered with them -
# Upstox lists Portfolio (positions / holdings), Shoonya every call. A 403 from these lists reads as that refusal.
STATIC_IP_READS: Dict[str, frozenset] = {
    "upstox": frozenset({"positions", "holdings"}),
    "shoonya": frozenset(PARTS),
}
_STATIC_IP_WORDS = re.compile(r"static\s*ip|white-?list|ip\s*(address)?\s*(is\s*)?not\s*(allowed|registered|whitelisted|permitted)"
                              r"|(invalid|unregistered|unauthori[sz]ed)\s*ip|ip\s*mismatch", re.IGNORECASE)

# Kept between requests (per server process): (credential id, list) -> (when read, the list's answer), and one rate
# budget per credential.
_CACHE: Dict[Tuple[int, str], Tuple[float, Dict[str, Any]]] = {}
_BUDGETS: Dict[int, Any] = {}
_clock: Callable[[], float] = time.monotonic


def build_adapter(record: BrokerCredentialRecord) -> Any:
    """The credential's own adapter (tests replace this)."""
    from app.brokers.token_lifecycle import build_adapter as build
    return build(record)


def _budgeted(record: BrokerCredentialRecord, adapter: Any) -> Any:
    from app.brokers.rate_budget import RateBudget, RateLimitedBroker, daily_budget_for, limits_for
    budget = _BUDGETS.get(record.id)
    if budget is None:
        budget = _BUDGETS[record.id] = RateBudget(limits_for(record.broker_name), daily=daily_budget_for(record.broker_name))
    return RateLimitedBroker(adapter, budget, tenant_id=record.tenant_id)


def _f(value: Any) -> Optional[float]:
    try:
        return None if value is None else round(float(value), 2)
    except (TypeError, ValueError):
        return None


def _side(value: Any) -> str:
    text = str(getattr(value, "value", value) or "").upper()
    return "Buy" if text == "BUY" else "Sell" if text == "SELL" else text.title()


def position_row(p: Any) -> Dict[str, Any]:
    qty = float(p.quantity or 0)
    return {"symbol": p.symbol, "exchange": p.exchange, "product": p.product, "quantity": qty,
            "side": "Long" if qty > 0 else "Short" if qty < 0 else "Closed",
            "average_price": _f(p.average_price), "ltp": _f(p.ltp), "pnl": _f(p.pnl)}


def holding_row(h: Any) -> Dict[str, Any]:
    qty = float(h.quantity or 0)
    ltp = _f(h.ltp)
    return {"symbol": h.symbol, "exchange": h.exchange, "quantity": qty, "average_price": _f(h.average_price),
            "ltp": ltp, "value": _f(qty * ltp) if ltp else None, "pnl": _f(h.pnl)}


def order_row(o: Any) -> Dict[str, Any]:
    placed = getattr(o, "placed_at", None)
    return {"symbol": o.symbol, "exchange": getattr(o, "exchange", None), "side": _side(o.transaction_type),
            "quantity": float(o.quantity or 0), "filled_quantity": float(o.filled_quantity or 0),
            "order_type": o.order_type, "product": getattr(o, "product", None), "status": str(o.status or "").upper(),
            "price": _f(o.price), "average_price": _f(o.average_price),
            "placed_at": placed.astimezone(timezone.utc).isoformat() if isinstance(placed, datetime) and placed.tzinfo else None}


_ROW = {"positions": position_row, "holdings": holding_row, "orders": order_row}


def refusal_message(broker: str, part: str, exc: BaseException) -> str:
    """The plain sentence a failed read shows in place of its list (never the broker's raw text)."""
    name = catalog.display_name(broker)
    text = f"{exc} {getattr(exc, 'raw', '') or ''}"
    status = getattr(exc, "status_code", None)
    if _STATIC_IP_WORDS.search(text) or (status == 403 and part in STATIC_IP_READS.get(broker.lower(), frozenset())):
        return f"{name} needs your static IP for this list. Register ATP's static IP with {name} (steps on the Broker page)."
    if isinstance(exc, BrokerAuthenticationError) or status == 401:
        return f"Your {name} session has ended. Sign in to {name} again to see this list."
    if isinstance(exc, BrokerBudgetUsed) or status == 429:
        return f"{name} is busy right now. Try again in a minute."
    if isinstance(exc, NotImplementedError):
        return f"{name} does not offer its {_WHAT[part]} to ATP."
    return f"{name} did not send your {_WHAT[part]} just now. Try again in a minute."


async def _read(record: BrokerCredentialRecord, part: str, adapter: Any) -> Dict[str, Any]:
    try:
        items = await getattr(adapter, _READ[part])()
        rows = [_ROW[part](i) for i in items or []]
        if part == "positions":
            rows.sort(key=lambda r: (r["quantity"] == 0, r["symbol"]))
        elif part == "orders":
            rows.sort(key=lambda r: r["placed_at"] or "", reverse=True)
        else:
            rows.sort(key=lambda r: r["symbol"])
        return {"rows": rows, "message": None}
    except Exception as exc:  # noqa: BLE001 - every failure is a plain sentence, never raised
        return {"rows": [], "message": refusal_message(record.broker_name, part, exc)}


def _prune(now: float) -> None:
    if len(_CACHE) > _CACHE_MAX:
        for key in [k for k, (at, _) in _CACHE.items() if now - at > CACHE_SECONDS]:
            _CACHE.pop(key, None)


async def account_book(record: BrokerCredentialRecord, parts: List[str], refresh: bool = False) -> Dict[str, Any]:
    """One linked broker session's lists (read, or kept from a read moments ago)."""
    from app.brokers.token_lifecycle import token_is_usable
    name = catalog.display_name(record.broker_name)
    out: Dict[str, Any] = {"broker": record.broker_name, "broker_label": name,
                           "account_label": None if record.account_label == "primary" else record.account_label}
    now = _clock()
    adapter: Any = None
    oldest: Optional[float] = None
    for part in parts:
        kept = _CACHE.get((record.id, part))
        if kept is not None and now - kept[0] < (REFRESH_MIN_SECONDS if refresh else CACHE_SECONDS):
            out[part], at = kept[1], kept[0]
        elif not token_is_usable(record):
            out[part], at = {"rows": [], "message": f"Sign in to {name} to see this list."}, now
        else:
            at = now
            try:
                adapter = adapter or _budgeted(record, build_adapter(record))
            except Exception as exc:  # noqa: BLE001 - a sentence, never an exception
                out[part] = {"rows": [], "message": refusal_message(record.broker_name, part, exc)}
            else:
                out[part] = await _read(record, part, adapter)
                _CACHE[(record.id, part)] = (at, out[part])
        oldest = at if oldest is None else min(oldest, at)
    out["age_seconds"] = round(now - oldest, 1) if oldest is not None else 0.0
    _prune(now)
    return out


async def _flags(session: AsyncSession, user: User) -> None:
    from app.platform.controls import flag_enabled
    for name in (PAGE_FLAG, FLAG):
        if not await flag_enabled(session, name, user.tenant_id):
            raise HTTPException(status_code=404, detail="Not found", headers={"X-Feature-Disabled": name})


def _parts(value: Optional[str]) -> List[str]:
    if not value:
        return list(PARTS)
    wanted = [p.strip().lower() for p in value.split(",") if p.strip()]
    bad = [p for p in wanted if p not in PARTS]
    if bad or not wanted:
        raise HTTPException(status_code=422, detail="parts: positions, holdings or orders")
    return [p for p in PARTS if p in wanted]


@router.get("/broker-book")
async def broker_book(parts: Optional[str] = Query(default=None, max_length=40), refresh: bool = False,
                      user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    await _flags(session, user)
    from app.market_data import entitlement
    wanted = _parts(parts)
    records = await entitlement.own_records(session, user)          # his own sessions, inside his own organisation
    accounts = [await account_book(r, wanted, refresh) for r in records]
    return {"parts": wanted, "accounts": accounts}


__all__ = ["router", "FLAG", "PARTS", "STATIC_IP_READS", "account_book", "refusal_message", "position_row", "holding_row",
           "order_row"]
