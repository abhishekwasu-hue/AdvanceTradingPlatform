"""N4 CH4a: the trades layer - the caller's OWN PAPER trades on the chart of an instrument, as of a time.

GET /api/charts/layers/trades?instrument=NSE:RELIANCE&at=<ISO time>&start=<ISO time>
  * Only the caller's own trades: `user_id` = the caller AND `tenant_id` = the caller's organisation. Never a teammate's
    trade (the trade list itself is organisation-wide; a chart layer is personal) and never another organisation's.
  * PAPER only (Phase 0: no LIVE trade exists; if one ever does, it is not drawn here).
  * Which trades: those on this instrument (`symbol`, or its `instrument_key`) - drawn at their own prices - and the
    derived ones (an option or a future) whose `underlying_symbol` is this instrument - drawn at their times only, since
    their prices are the contract's, not the chart's (`on_price_axis: false`).
  * As of `at` (timezone-aware; default now; a future time reads as now): a trade entered after `at` is not shown, and an
    exit after `at` is not shown either - the trade is open as of then, without its P&L. So replay never shows a trade
    or an exit before it happened.
  * The stop: the position monitor moves `stop_loss` as a stop trails, so the row's stop is today's, not the one in
    force at `at`. Replaying the past (`at` before now, or a trade that exits after `at`) therefore draws the trade's
    `initial_stop_loss` (its `stop_loss` when no initial stop was recorded), labelled "initial stop"; only the live view
    (`at` = now) draws the current stop. The stop history is not recorded, so the trail itself is not replayed.
  * The window: trades active in (`start`, `at`] - entered by `at` and not exited before `start` (default `start`: 30
    days before `at`; at most MAX_DAYS before it). At most MAX_TRADES, the most recent entries first; the exchange
    filter (an underlying on another exchange is another chart) is in the query, before the cap.
  * An index chart (`NSE:NIFTY 50`) also shows the derived trades whose underlying the F&O master names `NIFTY`
    (app.instruments.master aliases), and the other way round.
Behind the `chart_trades_layer` flag (off by default -> 503). Read-only: nothing here places, changes or proposes an
order, and the chart draws no control that could.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.db.models import TradeRecord, User
from app.db.session import get_session
from app.instruments.master import INDEX_SYMBOLS, underlying_of
from app.platform.controls import require_flag

router = APIRouter(prefix="/api/charts/layers", tags=["charts"])
FLAG = "chart_trades_layer"
SCHEMA = "trades/1"
MODE = "PAPER"
MAX_TRADES = 200
DEFAULT_DAYS = 30
MAX_DAYS = 3660
NOTE = "Your own PAPER trades, as recorded - for review only. The chart places no orders."


def _aware(t: Optional[datetime], name: str) -> Optional[datetime]:
    if t is not None and t.tzinfo is None:
        raise HTTPException(status_code=422, detail=f"`{name}` must carry its time zone (for example 2026-10-09T15:30:00+05:30)")
    return None if t is None else t.astimezone(timezone.utc)


def _utc(t: Optional[datetime]) -> Optional[datetime]:
    """A stored time as UTC (SQLite hands back naive datetimes, stored as UTC)."""
    if t is None:
        return None
    return t.replace(tzinfo=timezone.utc) if t.tzinfo is None else t.astimezone(timezone.utc)


def split_instrument(instrument: str) -> tuple[Optional[str], str]:
    """("NSE", "RELIANCE") from "NSE:RELIANCE"; (None, "RELIANCE") from a bare symbol. Upper case, spaces collapsed."""
    value = " ".join(str(instrument or "").upper().split())
    if ":" in value:
        exchange, symbol = value.split(":", 1)
        return exchange.strip() or None, symbol.strip()
    return None, value


def _bare(symbol: Optional[str]) -> str:
    return split_instrument(symbol or "")[1]


def _num(v: Any) -> Optional[float]:
    return None if v is None else round(float(v), 4)


def _epoch(t: datetime) -> int:
    return int(t.timestamp())


STOP = "stop"
INITIAL_STOP = "initial stop"


def underlying_names(symbol: str) -> List[str]:
    """The names a derived trade's `underlying_symbol` may carry for this chart: `NIFTY 50` <-> `NIFTY` (the F&O master's
    name for the index); an equity is just itself."""
    fo = underlying_of(symbol)
    return sorted({symbol, fo, INDEX_SYMBOLS.get(fo, fo)})


def trade_item(r: TradeRecord, *, on_price_axis: bool, at: datetime, live: bool = False) -> Dict[str, Any]:
    """One trade as the chart draws it, as of `at` (an exit after `at` is not known yet).

    `live`: `at` is now. Only then is the row's `stop_loss` (moved by the position monitor as the stop trails) the stop in
    force; as of a past `at` - or for a trade whose exit is after `at` - it may be a later, trailed stop (look-ahead), so
    the trade's initial stop is drawn instead."""
    entry_t = _utc(r.entry_time)
    exit_t = _utc(r.exit_time)
    exited = exit_t is not None and exit_t <= at and r.exit_price is not None
    current = live and not (exit_t is not None and exit_t > at)
    stop = r.stop_loss if current or r.initial_stop_loss is None else r.initial_stop_loss
    long = str(r.direction).upper() in ("LONG", "BUY")
    kind = (r.instrument_kind or "UNDERLYING").upper()
    contract = None if kind == "UNDERLYING" else {"symbol": r.symbol, "kind": kind, "expiry": r.expiry.isoformat() if r.expiry else None,
                                                  "position": r.option_position, "lot_size": r.lot_size}
    side = "long" if long else "short"
    qty = float(r.quantity)
    label = f"{'Buy' if long else 'Sell'} {qty:g}" + (f" {r.symbol}" if not on_price_axis or contract else "") + f" @ {float(r.entry_price):,.2f}"
    assert entry_t is not None
    return {
        "id": r.id, "symbol": r.symbol, "side": side, "mode": r.mode, "quantity": qty, "on_price_axis": on_price_axis,
        "contract": contract, "strategy": r.strategy_id,
        "entry": {"time": _epoch(entry_t), "price": _num(r.entry_price)},
        "exit": {"time": _epoch(exit_t), "price": _num(r.exit_price), "reason": r.exit_reason} if exited and exit_t else None,
        "open": not exited,
        # the trade's own levels are on its own price axis: drawn only on its own chart
        "stop_loss": _num(stop) if on_price_axis and not exited else None,
        "stop_label": (STOP if current else INITIAL_STOP) if on_price_axis and not exited else None,
        "target": _num(r.target1) if on_price_axis and not exited else None,
        "pnl": _num(r.pnl) if exited else None,
        "label": label,
    }


async def own_trades(session: AsyncSession, user: User, instrument: str, start: datetime, at: datetime, *,
                     live: bool = False) -> List[Dict[str, Any]]:
    """The caller's own PAPER trades on `instrument` active in (start, at], most recent entries first (MAX_TRADES)."""
    exchange, symbol = split_instrument(instrument)
    key = f"{exchange}:{symbol}" if exchange else None
    names = [symbol] + ([key] if key else [])
    underlyings = underlying_names(symbol) + ([key] if key else [])
    filters = [TradeRecord.tenant_id == user.tenant_id, TradeRecord.user_id == user.id, TradeRecord.mode == MODE,
               TradeRecord.entry_time <= at,
               or_(TradeRecord.exit_time.is_(None), TradeRecord.exit_time >= start),
               or_(func.upper(TradeRecord.symbol).in_(names), func.upper(TradeRecord.underlying_symbol).in_(underlyings),
                   *([func.upper(TradeRecord.instrument_key) == key] if key else []))]
    if exchange:                                          # RELIANCE on BSE is not the NSE:RELIANCE chart - before the cap
        filters.append(or_(TradeRecord.exchange.is_(None), func.upper(TradeRecord.exchange) == exchange,
                           func.upper(TradeRecord.instrument_kind) != "UNDERLYING"))
    q = select(TradeRecord).where(*filters).order_by(TradeRecord.entry_time.desc(), TradeRecord.id.desc()).limit(MAX_TRADES)
    out: List[Dict[str, Any]] = []
    for r in (await session.scalars(q)).all():
        same = _bare(r.symbol) == symbol or (key is not None and (r.instrument_key or "").upper() == key)
        out.append(trade_item(r, on_price_axis=same, at=at, live=live))
    return out


@router.get("/trades")
async def trades_layer(instrument: str = Query(..., min_length=1, max_length=100), at: Optional[datetime] = None,
                       start: Optional[datetime] = None,
                       user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    await require_flag(session, FLAG, user.tenant_id)
    now = datetime.now(timezone.utc)
    at_utc, start_utc = _aware(at, "at"), _aware(start, "start")
    live = at_utc is None or at_utc >= now                       # a future time reads as now
    when = now if live or at_utc is None else at_utc
    begin = start_utc if start_utc is not None else when - timedelta(days=DEFAULT_DAYS)
    if begin >= when:
        raise HTTPException(status_code=422, detail="`start` must be before `at`")
    begin = max(begin, when - timedelta(days=MAX_DAYS))           # a long daily chart: its last MAX_DAYS days
    trades = await own_trades(session, user, instrument, begin, when, live=live)
    exchange, symbol = split_instrument(instrument)
    return {"schema": SCHEMA, "instrument": f"{exchange}:{symbol}" if exchange else symbol, "at": when.isoformat(), "start": begin.isoformat(),
            "mode": MODE, "trades": trades, "open": sum(1 for t in trades if t["open"]), "capped": len(trades) >= MAX_TRADES, "note": NOTE}


__all__ = ["router", "FLAG", "own_trades", "trade_item", "split_instrument", "underlying_names"]
