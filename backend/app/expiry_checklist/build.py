"""AUD-T2 (owner order ATP_ORDER_AUDIT_28OCT T2): the expiry-day and margin checklist for the user's own open F&O positions.

On the session before an expiry (the next trading day on the NSE calendar, holidays respected) and on the expiry day
itself, each of his open option / future positions expiring then gets:
- when it expires (today / next session);
- whether the option is in the money now (the underlying's live price against the strike; no price -> said so, never a
  guessed price);
- how it settles: index F&O in cash; stock F&O by delivery of shares, with his broker's own rule (app/expiry_checklist/
  brokers.py, linked to the broker's help page);
- the next expiry of the same contract from the instrument master, and that contract as a basket leg he may add with one
  tap (the basket only collects legs; placing still goes through the normal confirm).

Information only, in plain words: no advice wording, nothing is placed, closed or blocked (ADR-0004 / ADR-0006). Every
number shown is his position's own or the market's.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import BrokerCredentialRecord, TradeRecord, User
from app.expiry_checklist import brokers as broker_help
from app.instruments import master
from app.market_data.calendar import family_holidays, next_trading_day, to_ist
from app.paper import fno_ticket

logger = logging.getLogger(__name__)

PriceLookup = Callable[[str, str], Awaitable[float]]

TODAY = "today"
NEXT_SESSION = "next_session"

NOTES = {
    "cash": "Index F&O settles in cash. No shares change hands at expiry.",
    "delivery": "Stock F&O held at expiry settles by delivery: shares and their full value change hands.",
    "option_delivery": "A stock option settles by delivery only if it is in the money at expiry.",
    "basket": "Add to basket only adds the contract to your basket. Nothing is placed until you confirm.",
    "info": "Information only. Nothing is closed or placed for you.",
}


def when_due(expiry: date, today: date, holidays: Iterable[date]) -> Optional[str]:
    """`today` on the expiry day; `next_session` when the expiry is the next trading day (holidays and weekends skipped,
    so on a Friday a Monday expiry is next); otherwise None."""
    if expiry == today:
        return TODAY
    if expiry > today and next_trading_day(today, holidays) == expiry:
        return NEXT_SESSION
    return None


def right_of(symbol: str) -> Optional[str]:
    """CE / PE from the trading symbol ('NIFTY 24500 CE 01 OCT 26', 'NIFTY26O0124500CE'); None when neither."""
    m = re.search(r"(?:\b|\d)(CE|PE)(?:\b|$)", (symbol or "").upper())
    return m.group(1) if m else None


def strike_of(symbol: str) -> Optional[float]:
    from app.trading.routes import _strike_of  # the one parser Positions uses
    return _strike_of(symbol or "")


def moneyness(right: str, strike: float, spot: Optional[float]) -> Optional[str]:
    """"in" / "out" / "at" the money now; None without a price."""
    if spot is None:
        return None
    if spot == strike:
        return "at"
    if right == "CE":
        return "in" if spot > strike else "out"
    return "in" if spot < strike else "out"


def money_line(state: Optional[str], right: str, underlying: str) -> str:
    if state is None:
        return f"No live price for {underlying} now, so in or out of the money is not known."
    side = "above" if right == "CE" else "below"
    other = "below" if right == "CE" else "above"
    if state == "in":
        return f"In the money now: price is {side} the strike."
    if state == "at":
        return "At the money now: price is at the strike."
    return f"Out of the money now: price is {other} the strike."


def _day_words(d: date) -> str:
    return d.strftime("%a %d %b %Y").replace(" 0", " ")


async def _spot_lookup(session: AsyncSession, user: User) -> Optional[PriceLookup]:
    from app.ai.worker_bridge import price_lookup_for_user
    return await price_lookup_for_user(session, user)


async def _spots(session: AsyncSession, user: User, underlyings: List[str]) -> Dict[str, float]:
    """The live price of each underlying on the user's own source; one that cannot be priced is left out."""
    if not underlyings:
        return {}
    try:
        lookup = await _spot_lookup(session, user)
    except Exception as exc:  # noqa: BLE001 - no price source: nothing is judged
        logger.info("Expiry checklist: no price source (%s)", type(exc).__name__)
        return {}
    out: Dict[str, float] = {}
    if lookup is None:
        return out
    for name in underlyings:
        symbol = master.INDEX_SYMBOLS.get(name, name)
        exchange = master.INDEX_EXCHANGE.get(name, "NSE")
        try:
            value = float(await lookup(symbol, exchange))
        except Exception as exc:  # noqa: BLE001 - no price: said so, never a guessed one
            logger.info("Expiry checklist: no price for %s (%s)", name, type(exc).__name__)
            continue
        if value > 0:
            out[name] = value
    return out


async def _own_brokers(session: AsyncSession, user: User) -> List[str]:
    rows = await session.scalars(select(BrokerCredentialRecord.broker_name).where(
        BrokerCredentialRecord.tenant_id == user.tenant_id, BrokerCredentialRecord.user_id == user.id).distinct())
    return [r for r in rows if r]


async def _next_contract(session: AsyncSession, underlying: str, right: str, expiry: date,
                         strike: Optional[float]) -> Dict[str, Any]:
    """The next expiry after this one in the instrument master and, when the master holds it, the same-strike contract."""
    later = await master.expiries(session, underlying, instrument_type=right, on_or_after=expiry + timedelta(days=1))
    if not later:
        return {"expiry": None, "record": None}
    nxt = later[0]
    record = (await master.find_future(session, underlying, nxt) if right == "FUT"
              else await master.find_option(session, underlying, nxt, float(strike), right) if strike is not None else None)
    return {"expiry": nxt, "record": record}


def _lots(quantity: float, lot_size: Optional[float]) -> int:
    if lot_size and lot_size > 0:
        return max(1, int(round(abs(quantity) / float(lot_size))))
    return 1


async def for_user(session: AsyncSession, user: User, now: Optional[datetime] = None) -> Dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    today = to_ist(now).date()
    holidays = await family_holidays(session, "NSE")
    trades = list(await session.scalars(select(TradeRecord).where(
        TradeRecord.tenant_id == user.tenant_id, TradeRecord.user_id == user.id, TradeRecord.exit_time.is_(None),
        TradeRecord.instrument_kind.in_(("OPTION", "FUTURE")), TradeRecord.expiry.is_not(None)).order_by(TradeRecord.expiry, TradeRecord.id)))
    due = []
    for t in trades:
        when = when_due(t.expiry, today, holidays)
        if when is None:
            continue
        underlying = master.underlying_of(t.underlying_symbol or "")
        if not underlying:
            continue
        if t.instrument_kind == "FUTURE":
            right, strike = "FUT", None
        else:
            # The instrument master's own row first (a compact symbol like NIFTY26O0124500CE runs the date into the
            # strike, which the text parser can misread); the parser only for a contract the master does not hold.
            record = await master.find_by_symbol(session, t.symbol or "")
            if record is not None and record.strike is not None and record.instrument_type in ("CE", "PE"):
                right, strike = record.instrument_type, float(record.strike)
            else:
                right, strike = right_of(t.symbol), strike_of(t.symbol)
            if right is None or strike is None:
                continue
        due.append((t, when, underlying, right, strike))
    spots = await _spots(session, user, sorted({u for _t, _w, u, r, _s in due if r != "FUT"}))
    rows: List[Dict[str, Any]] = []
    for t, when, underlying, right, strike in due:
        stock = underlying not in master.INDEX_SYMBOLS
        state = moneyness(right, strike, spots.get(underlying)) if right != "FUT" else None
        label = fno_ticket.contract_label(underlying, t.expiry, strike, right)
        headline = f"{label}: expires {'today' if when == TODAY else 'next session, ' + _day_words(t.expiry)}."
        money_text = money_line(state, right, underlying) if right != "FUT" else None
        if stock:
            settlement_text = ("Stock future: settles by delivery of shares at expiry." if right == "FUT"
                               else "Stock option: settles by delivery of shares if in the money at expiry.")
        else:
            settlement_text = "Index contract: settles in cash, no shares change hands."
        nxt = await _next_contract(session, underlying, right, t.expiry, strike)
        side = "BUY" if t.direction == "LONG" else "SELL"
        leg = None
        if nxt["expiry"] is not None:
            next_text = f"Next expiry: {_day_words(nxt['expiry'])}."
            record = nxt["record"]
            if record is not None:
                leg = {"underlying": underlying, "expiry": nxt["expiry"].isoformat(), "strike": float(strike) if strike is not None else 0.0,
                       "right": right, "side": side, "lots": _lots(t.quantity, record.lot_size),
                       "label": fno_ticket.contract_label(underlying, nxt["expiry"], strike, right)}
        else:
            next_text = "Next expiry: not in the contract list yet."
        rows.append({"trade_id": t.id, "label": label, "symbol": t.symbol, "underlying": underlying, "right": right,
                     "strike": strike, "expiry": t.expiry.isoformat(), "when": when, "side": side,
                     "settlement": "delivery" if stock else "cash", "moneyness": state, "priced": right == "FUT" or underlying in spots,
                     "next_expiry": nxt["expiry"].isoformat() if nxt["expiry"] is not None else None, "basket_leg": leg,
                     "headline": headline, "money_text": money_text, "settlement_text": settlement_text, "next_text": next_text})
    any_stock = any(r["settlement"] == "delivery" for r in rows)
    notes = [NOTES["delivery"], NOTES["option_delivery"]] if any_stock else []
    if any(r["settlement"] == "cash" for r in rows):
        notes.append(NOTES["cash"])
    notes += [NOTES["basket"], NOTES["info"]] if rows else []
    return {"as_of": now.isoformat(), "today": today.isoformat(), "rows": rows, "notes": notes,
            "brokers": broker_help.notes_for(await _own_brokers(session, user)) if any_stock else []}
