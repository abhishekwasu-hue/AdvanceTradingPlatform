"""FNO-4 (owner order ATP_SESSION_FNO_WORKSPACE.md, sections 2 and 8): what a saved F&O contract IS - for a watchlist's
contract rows and the positions page's Add / Adjust.

A watchlist row of a contract is the existing item `{symbol, exchange}` (the trading symbol on NFO / BFO, as the chain's
"Add to watchlist" saves it); nothing new is stored. This module reads, for each such row:

* the contract from the instrument master (underlying, expiry, strike, CE / PE / FUT, lot size) and its name as traders
  write it ("NIFTY 28 OCT 22800 CE");
* when the master no longer holds it (the daily sync drops an expired contract), the trading symbol itself is read -
  Upstox's "NIFTY 22800 CE 28 OCT 25" / "NIFTY FUT 28 OCT 25" and Kite's "NIFTY25OCT22800CE" / "NIFTY25O2822800CE" /
  "NIFTY25OCTFUT" - so an expired row still says what it was;
* whether it has EXPIRED (its expiry day is past, or it is the expiry day after the close at 15:30 IST) and the days left;
* for an expired contract, the ROLL target: the same strike and right (or the future) in the next listed expiry - the
  next monthly for a monthly contract, the nearest expiry otherwise. Found in the master only; never guessed.

Read-only reference data: nothing here places, changes or proposes an order, and nothing reads a price. Rolling a row
is the watchlist's own edit (the frontend replaces the item); it opens no position.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import InstrumentRecord
from app.instruments import master
from app.market_data.calendar import IST, MARKET_CLOSE
from app.paper.fno_ticket import MONTHS, contract_label

DERIVATIVE_EXCHANGES = ("NFO", "BFO")
MAX_ITEMS = 60
RIGHTS = ("CE", "PE", "FUT")
# Kite's weekly month code: 1-9 for January-September, O / N / D for October-December.
_WEEKLY_MONTH = {**{str(i): i for i in range(1, 10)}, "O": 10, "N": 11, "D": 12}
_MON = "|".join(MONTHS)
_UPSTOX_OPTION = re.compile(rf"^([A-Z0-9&-]+) (\d+(?:\.\d+)?) (CE|PE) (\d{{2}}) ({_MON}) (\d{{2}})$")
_UPSTOX_FUTURE = re.compile(rf"^([A-Z0-9&-]+) FUT (\d{{2}}) ({_MON}) (\d{{2}})$")
_KITE_MONTHLY_OPTION = re.compile(rf"^([A-Z&-]+)(\d{{2}})({_MON})(\d+(?:\.\d+)?)(CE|PE)$")
_KITE_MONTHLY_FUTURE = re.compile(rf"^([A-Z&-]+)(\d{{2}})({_MON})FUT$")
_KITE_WEEKLY_OPTION = re.compile(r"^([A-Z&-]+)(\d{2})([1-9OND])(\d{2})(\d+(?:\.\d+)?)(CE|PE)$")


@dataclass(frozen=True)
class Parsed:
    """What a trading symbol says by itself. `expiry` is None when only the month is written (Kite's monthly form);
    `month` is then (year, month)."""
    underlying: str
    right: str
    strike: Optional[float]
    expiry: Optional[date]
    month: Tuple[int, int]
    monthly: Optional[bool]


def _day(yy: str, mon: int, dd: str) -> Optional[date]:
    try:
        return date(2000 + int(yy), mon, int(dd))
    except ValueError:
        return None


def parse_symbol(symbol: str) -> Optional[Parsed]:
    """The contract a trading symbol names, in Upstox's or Kite's spelling; None when it is neither. Pure."""
    s = " ".join((symbol or "").split()).upper()
    m = _UPSTOX_OPTION.match(s)
    if m:
        day = _day(m.group(6), MONTHS.index(m.group(5)) + 1, m.group(4))
        if day is None:
            return None
        return Parsed(m.group(1), m.group(3), float(m.group(2)), day, (day.year, day.month), None)
    m = _UPSTOX_FUTURE.match(s)
    if m:
        day = _day(m.group(4), MONTHS.index(m.group(3)) + 1, m.group(2))
        if day is None:
            return None
        return Parsed(m.group(1), "FUT", None, day, (day.year, day.month), True)
    m = _KITE_MONTHLY_FUTURE.match(s)
    if m:
        return Parsed(m.group(1), "FUT", None, None, (2000 + int(m.group(2)), MONTHS.index(m.group(3)) + 1), True)
    m = _KITE_MONTHLY_OPTION.match(s)
    if m:
        return Parsed(m.group(1), m.group(5), float(m.group(4)), None, (2000 + int(m.group(2)), MONTHS.index(m.group(3)) + 1), True)
    m = _KITE_WEEKLY_OPTION.match(s)
    if m:
        day = _day(m.group(2), _WEEKLY_MONTH[m.group(3)], m.group(4))
        if day is None:
            return None
        return Parsed(m.group(1), m.group(6), float(m.group(5)), day, (day.year, day.month), False)
    return None


def is_expired(expiry: Optional[date], now: datetime, month: Optional[Tuple[int, int]] = None) -> Optional[bool]:
    """True once the expiry day is past, or on the expiry day from the close (15:30 IST). With only the month known: True
    once that month is past, None (unknown) during it. Pure."""
    local = now.astimezone(IST)
    if expiry is not None:
        return expiry < local.date() or (expiry == local.date() and local.time() >= MARKET_CLOSE)
    if month is not None:
        return True if month < (local.year, local.month) else None
    return None


def days_left(expiry: Optional[date], now: datetime) -> Optional[int]:
    """Calendar days to the expiry day (0 on the day itself); None when the day is not known or it has expired."""
    if expiry is None:
        return None
    left = (expiry - now.astimezone(IST).date()).days
    return left if left >= 0 else None


def label_of(underlying: str, right: str, strike: Optional[float], expiry: Optional[date], month: Optional[Tuple[int, int]]) -> str:
    """The contract as traders write it; with only the month known, "NIFTY OCT 22800 CE"."""
    if expiry is not None:
        return contract_label(underlying, expiry, strike, right)
    mon = MONTHS[(month or (2000, 1))[1] - 1]
    if right == "FUT" or strike is None:
        return f"{underlying} {mon} FUT"
    k = int(strike) if float(strike).is_integer() else round(float(strike), 2)
    return f"{underlying} {mon} {k} {right}"


def _contract_dict(row: InstrumentRecord) -> Dict[str, Any]:
    right = (row.instrument_type or "").upper()
    strike = None if right == "FUT" else (float(row.strike) if row.strike is not None else None)
    return {"symbol": row.tradingsymbol.upper(), "exchange": (row.exchange or master.derivatives_exchange(row.underlying or "")).upper(),
            "underlying": (row.underlying or "").upper(), "right": right, "strike": strike,
            "expiry": row.expiry.isoformat() if row.expiry else None, "lot_size": int(row.lot_size or 1),
            "label": contract_label((row.underlying or "").upper(), row.expiry, strike, right) if row.expiry else row.tradingsymbol.upper()}


def _prefer(rows: Iterable[InstrumentRecord]) -> Dict[str, InstrumentRecord]:
    """One master row per trading symbol: Upstox's (ATP's own data connection) first, then any broker's."""
    out: Dict[str, InstrumentRecord] = {}
    for row in sorted(rows, key=lambda r: (r.broker != master.UPSTOX_BROKER, r.broker, r.id)):
        out.setdefault(row.tradingsymbol.upper(), row)
    return out


async def roll_target(session: AsyncSession, underlying: str, right: str, strike: Optional[float], *, after: date,
                      monthly: Optional[bool]) -> Optional[InstrumentRecord]:
    """The same strike and right (or the future) in the next listed expiry after `after`: for a monthly contract the next
    monthly one (the master's `weekly` False), otherwise the nearest. None when the master lists none."""
    query = select(InstrumentRecord).where(InstrumentRecord.underlying == master.underlying_of(underlying),
                                           InstrumentRecord.instrument_type == right, InstrumentRecord.expiry > after)
    if right != "FUT":
        if strike is None:
            return None
        query = query.where(func.abs(InstrumentRecord.strike - strike) < 1e-6)
    rows = list(await session.scalars(query.order_by(InstrumentRecord.expiry, InstrumentRecord.id).limit(200)))
    if monthly and right != "FUT":
        monthly_rows = [r for r in rows if not r.weekly]
        rows = monthly_rows or rows
    if not rows:
        return None
    first = rows[0].expiry
    return next(iter(_prefer(r for r in rows if r.expiry == first).values()))


async def lookup(session: AsyncSession, items: Sequence[Tuple[str, str]], *, now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """Each `(exchange, symbol)` as a contract row: `known` False for a symbol that is not an F&O contract ATP can read
    (it then shows as a plain row). In the order given."""
    now = now or datetime.now(timezone.utc)
    today = now.astimezone(IST).date()
    wanted = [(e.upper(), s.upper()) for e, s in items]
    symbols = sorted({s for e, s in wanted if e in DERIVATIVE_EXCHANGES})
    found = _prefer(await session.scalars(select(InstrumentRecord).where(
        InstrumentRecord.tradingsymbol.in_(symbols), InstrumentRecord.instrument_type.in_(RIGHTS)))) if symbols else {}
    out: List[Dict[str, Any]] = []
    for exchange, symbol in wanted:
        base: Dict[str, Any] = {"symbol": symbol, "exchange": exchange, "known": False}
        if exchange not in DERIVATIVE_EXCHANGES:
            out.append(base)
            continue
        row = found.get(symbol)
        parsed = parse_symbol(symbol)
        if row is not None and row.expiry is not None:
            facts = _contract_dict(row)
            underlying, right, strike, expiry = facts["underlying"], facts["right"], facts["strike"], row.expiry
            month: Optional[Tuple[int, int]] = (expiry.year, expiry.month)
            monthly: Optional[bool] = not row.weekly
            expired = is_expired(expiry, now)
            info = {**base, "known": True, "listed": True, "underlying": underlying, "right": right, "strike": strike,
                    "expiry": expiry.isoformat(), "lot_size": facts["lot_size"], "label": facts["label"]}
        elif parsed is not None:
            underlying, right, strike, expiry, month, monthly = (parsed.underlying, parsed.right, parsed.strike, parsed.expiry,
                                                                 parsed.month, parsed.monthly)
            expired = is_expired(expiry, now, month)
            if expired is None:
                # Not in the master and only the month known: the master lists every live contract, so a contract it no
                # longer holds while it holds this underlying's others has expired. Without any row, it stays unknown.
                listed_any = await session.scalar(select(InstrumentRecord.id).where(
                    InstrumentRecord.underlying == underlying, InstrumentRecord.instrument_type.in_(RIGHTS)).limit(1))
                expired = True if listed_any is not None else None
            info = {**base, "known": True, "listed": False, "underlying": underlying, "right": right, "strike": strike,
                    "expiry": expiry.isoformat() if expiry else None, "lot_size": None,
                    "label": label_of(underlying, right, strike, expiry, month)}
        else:
            out.append(base)
            continue
        spot_symbol, spot_exchange = _spot(underlying)
        info.update({"expired": bool(expired), "days_left": None if expired else days_left(expiry, now),
                     "spot_symbol": spot_symbol, "spot_exchange": spot_exchange, "roll": None, "roll_note": None})
        if expired:
            after = max(expiry or today, today) if expiry is not None else today
            if expiry is None and month is not None:
                after = max(today, date(month[0], month[1], 1))
            target = await roll_target(session, underlying, right, strike, after=after, monthly=monthly)
            if target is not None:
                info["roll"] = _contract_dict(target)
            else:
                info["roll_note"] = ("The next expiry of this strike is not in the instrument list yet. It updates every "
                                     "morning; try again later.")
        out.append(info)
    return out


def _spot(underlying: str) -> Tuple[str, str]:
    from app.option_chain.chain_view import spot_instrument
    return spot_instrument(underlying)


# --- the positions page: legs placed together ----------------------------------------------------------------------------

# A basket leg's order carries the idempotency key "fno:<user>:<basket ref>-<seq>" (fno_basket.execute); a single ticket's
# is "fno:<user>:<ticket ref>" with no "-<seq>" ending (a UUID ends in 12 hex characters, never 1-2 digits).
_BASKET_KEY = re.compile(r"^fno:(\d+):([A-Za-z0-9-]{8,48})-(\d{1,2})$")


def basket_ref_of(idempotency_key: Optional[str], user_id: int) -> Optional[str]:
    """The basket a ticket order came from, or None (a single ticket, another user's key, anything else). Pure."""
    m = _BASKET_KEY.match(idempotency_key or "")
    if not m or int(m.group(1)) != user_id:
        return None
    return m.group(2)


async def position_groups(session: AsyncSession, user: Any) -> List[Dict[str, Any]]:
    """The caller's own open PAPER positions that belong together: the legs of one basket (from their orders' keys) and
    the legs of one multi-leg structure (`leg_group_id`). Only groups of two or more open legs; tenant and user scoped.
    Read only: the positions page sums their P&L; every leg keeps its own Exit."""
    from app.db.models import OrderRecord, TradeRecord
    trades = list(await session.scalars(select(TradeRecord).where(
        TradeRecord.tenant_id == user.tenant_id, TradeRecord.user_id == user.id, TradeRecord.mode == "PAPER",
        TradeRecord.exit_time.is_(None))))
    if not trades:
        return []
    ids = [t.id for t in trades]
    orders = await session.execute(select(OrderRecord.trade_id, OrderRecord.idempotency_key).where(
        OrderRecord.tenant_id == user.tenant_id, OrderRecord.user_id == user.id, OrderRecord.trade_id.in_(ids)))
    basket_of: Dict[int, str] = {}
    for trade_id, key in orders.all():
        ref = basket_ref_of(key, user.id)
        if ref and trade_id is not None:
            basket_of.setdefault(int(trade_id), ref)
    groups: Dict[str, Dict[str, Any]] = {}
    for t in sorted(trades, key=lambda x: x.id):
        if t.id in basket_of:
            key, kind, name = f"basket:{basket_of[t.id]}", "basket", "Basket"
        elif t.leg_group_id:
            key, kind, name = f"structure:{t.leg_group_id}", "structure", (t.option_strategy or "Strategy").replace("_", " ").title()
        else:
            continue
        groups.setdefault(key, {"key": key, "kind": kind, "name": name, "trade_ids": []})["trade_ids"].append(t.id)
    return [g for g in groups.values() if len(g["trade_ids"]) >= 2]
