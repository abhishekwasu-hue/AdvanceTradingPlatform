"""TERM-CON C0: gather the panel's sources by calling the SAME functions their own routes use (each keeps its own flag
check and scope rule), one after the other on the caller's session. One source that fails or is off never fails the
panel: its section says so. Read only - no order path, no AI call.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.conclusion import rules as R
from app.conclusion import sections as S
from app.db.models import TradeRecord, User

log = logging.getLogger(__name__)

MAX_POSITIONS = 40


async def _chart(session: AsyncSession, user: User, symbol: str, tf: str) -> dict[str, Any] | None | str:
    from app.charts.read_chart import read_chart
    try:
        return await read_chart(instrument=symbol, tf=tf, user=user, session=session)  # type: ignore[arg-type]
    except HTTPException as exc:
        return "off" if exc.status_code == 404 else "na"


async def _oi(session: AsyncSession, user: User, symbol: str) -> dict[str, Any] | None | str:
    from app.option_chain.routes import banner
    try:
        return await banner(symbol, user=user, session=session)
    except HTTPException as exc:
        return "off" if exc.status_code == 404 else "na"


async def _positions(session: AsyncSession, user: User) -> list[dict[str, Any]]:
    """His own open positions (tenant AND user), PAPER and LIVE alike, grouped as the Copilot's position watch groups them."""
    from app.ai.tools.copilot_trades import group_rows
    rows = list(await session.scalars(select(TradeRecord).where(
        TradeRecord.tenant_id == user.tenant_id, TradeRecord.user_id == user.id, TradeRecord.exit_time.is_(None))
        .order_by(TradeRecord.entry_time.desc(), TradeRecord.id.desc()).limit(MAX_POSITIONS * 4)))
    return group_rows(rows)[:MAX_POSITIONS]


async def _orders(session: AsyncSession, user: User) -> list[dict[str, Any]]:
    from app.trading.routes import list_orders
    return [o.model_dump() for o in await list_orders(user=user, session=session, limit=200, mine=True, today=True)]


async def _signals(session: AsyncSession, user: User) -> list[dict[str, Any]]:
    """His own signals today (the Signals inbox's read; it needs `ui_v2`, which the panel needs too)."""
    from app.signals_feed import service
    return list((await service.feed(session, user, tab="today", limit=50)).get("items") or [])


async def _broker(session: AsyncSession, user: User) -> dict[str, Any]:
    from app.reconciliation.routes import reconciliation_status
    return (await reconciliation_status(user=user, session=session)).model_dump()


async def _safe(session: AsyncSession, name: str, coro: Any) -> Any:
    try:
        return await coro
    except Exception:  # noqa: BLE001 - one source down never fails the panel; the section says "not available"
        log.exception("conclusion: source %s failed", name)
        return "na"


def fingerprint(body: dict[str, Any]) -> str:
    """The same facts give the same fingerprint (the read time is left out), so the panel can skip a repaint."""
    core = {k: v for k, v in body.items() if k not in ("as_of", "fingerprint")}
    return hashlib.sha256(json.dumps(core, sort_keys=True, default=str).encode()).hexdigest()[:16]


async def conclusion(session: AsyncSession, user: User, symbol: str, expiry: str | None, tf: str,
                     now: datetime | None = None, also: tuple[str, ...] = ()) -> dict[str, Any]:
    """`also`: up to two more timeframes (the ones his own strategies on this chart use, TC-Q2); each is its own
    reading and its own section - timeframes are never merged."""
    now = now or datetime.now(UTC)
    read_at = now.isoformat()
    symbol = symbol.strip().upper()
    chart = await _safe(session, "chart", _chart(session, user, symbol, tf))
    oi = await _safe(session, "oi", _oi(session, user, symbol))
    positions = await _safe(session, "positions", _positions(session, user))
    orders = await _safe(session, "orders", _orders(session, user))
    signals = await _safe(session, "signals", _signals(session, user))
    broker = await _safe(session, "broker", _broker(session, user))

    out: list[dict[str, Any]] = []
    readings: list[dict[str, Any]] = []
    if chart == "off":
        out.append(S.off("chart", "Chart", S.SOURCE_CHART))
    elif not isinstance(chart, dict):
        out.append(S.na("chart", "Chart", S.SOURCE_CHART))
    else:
        out.append(S.chart_section(chart, symbol, tf))
        if (r := S.chart_reading(chart, tf)) is not None:
            readings.append(r)
        for extra in [t for t in dict.fromkeys(also) if t != tf][:2]:
            more = await _safe(session, f"chart {extra}", _chart(session, user, symbol, extra))
            if isinstance(more, dict):
                sec = S.chart_section(more, symbol, extra)
                out.append({**sec, "key": f"chart-{extra}", "title": f"Chart {extra}"})
                if (r := S.chart_reading(more, extra)) is not None:
                    readings.append(r)
    if oi == "off":
        out.append(S.off("oi", "OI", S.SOURCE_OI))
    elif not isinstance(oi, dict):
        out.append(S.na("oi", "OI", S.SOURCE_OI))
    else:
        out.append(S.oi_section(oi, expiry))
        if (r := S.oi_reading(oi)) is not None:
            readings.append(r)
    if isinstance(positions, list):
        out.append(S.positions_section(positions, symbol, read_at))
        if (r := S.positions_reading(positions, symbol, read_at)) is not None:
            readings.append(r)
    else:
        out.append(S.na("positions", "My positions", S.SOURCE_POSITIONS))
    if isinstance(signals, list):
        out.append(S.signals_section(signals, symbol, read_at))
        if (r := S.signals_reading(signals, symbol)) is not None:
            readings.append(r)
    else:
        out.append(S.na("signals", "My signals", S.SOURCE_SIGNALS))
    order_rows = orders if isinstance(orders, list) else []
    out.append(S.orders_section(order_rows, symbol, read_at) if isinstance(orders, list)
               else S.na("orders", "My orders", S.SOURCE_ORDERS))
    broker_row = broker if isinstance(broker, dict) else None
    out.append(S.broker_section(broker_row) if broker_row is not None else S.na("broker", "Broker", S.SOURCE_BROKER))

    body: dict[str, Any] = {"symbol": symbol, "expiry": expiry, "tf": tf, "as_of": read_at,
                            "sections": [s for s in out if s["status"] != "off"], "readings": readings,
                            "needs_you": S.needs_you(order_rows, broker_row, symbol),
                            "glance": R.glance(readings, symbol),
                            "note": "Information only: what ATP's readings show, never a recommendation. The decision is yours."}
    body["fingerprint"] = fingerprint(body)
    return body
