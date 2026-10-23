"""Terminal master Part 7: the physical-settlement warning for stock F&O (flag `smart_execution`, behind `ui_v2`).

A stock future, or a stock option in the money or within the user's own band of its strike, held into expiry settles by
DELIVERY: shares and their full value change hands. ATP flags such positions in advance - from the user's own number of
days before expiry - on Positions (`for_user`) and, when he keeps the Telegram choice on, once a day per position as a
notification (`send_due`, the trading worker; the alert dispatcher delivers it to his channels). The delivery value is
in rupees. Every number comes from his settings (`smart_execution` prefs) and the live price on his own session.
Information only: nothing is closed or blocked, and the AI never acts on it (ADR-0004, ADR-0006).
"""
from __future__ import annotations

import logging
from datetime import datetime, time, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import InstrumentRecord, NotificationRecord, TradeRecord, User
from app.execution import smart_execution
from app.instruments import master
from app.market_data.calendar import IST, to_ist
from app.paper import fno_ticket

logger = logging.getLogger(__name__)


async def holdings_for(session: AsyncSession, user: User) -> List[smart_execution.Holding]:
    """His own open option / future positions (any mode), with the right and strike from the instrument master."""
    trades = list(await session.scalars(select(TradeRecord).where(
        TradeRecord.tenant_id == user.tenant_id, TradeRecord.user_id == user.id, TradeRecord.exit_time.is_(None),
        TradeRecord.instrument_kind.in_(("OPTION", "FUTURE")), TradeRecord.expiry.is_not(None))))
    if not trades:
        return []
    rows = list(await session.scalars(select(InstrumentRecord).where(
        InstrumentRecord.tradingsymbol.in_(sorted({t.symbol.upper() for t in trades})))))
    by_symbol: Dict[str, InstrumentRecord] = {}
    for rec in rows:
        by_symbol.setdefault(rec.tradingsymbol.upper(), rec)
    out: List[smart_execution.Holding] = []
    for t in trades:
        row = by_symbol.get(t.symbol.upper())
        underlying = master.underlying_of((row.underlying if row is not None and row.underlying else None) or t.underlying_symbol or "")
        if not underlying or t.expiry is None:
            continue
        if t.instrument_kind == "FUTURE":
            right, strike = "FUT", None
        elif row is not None and row.instrument_type in ("CE", "PE") and row.strike is not None:
            right, strike = row.instrument_type, float(row.strike)
        else:
            continue
        out.append(smart_execution.Holding(trade_id=t.id, underlying=underlying, right=right, strike=strike, expiry=t.expiry,
                                           quantity=float(t.quantity), side="BUY" if t.direction == "LONG" else "SELL",
                                           label=fno_ticket.contract_label(underlying, t.expiry, strike, right)))
    return out


async def _spots(session: AsyncSession, user: User, underlyings: List[str]) -> Dict[str, float]:
    from app.ai.worker_bridge import price_lookup_for_user
    lookup = await price_lookup_for_user(session, user)
    out: Dict[str, float] = {}
    if lookup is None:
        return out
    for name in underlyings:
        try:
            value = float(await lookup(name, "NSE"))
        except Exception as exc:  # noqa: BLE001 - no price: the option is not judged (never a guessed price)
            logger.info("Settlement watch: no price for %s (%s)", name, type(exc).__name__)
            continue
        if value > 0:
            out[name] = value
    return out


async def for_user(session: AsyncSession, user: User, now: Optional[datetime] = None) -> Dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    settings = await smart_execution.settings_for(session, user)
    holdings = [h for h in await holdings_for(session, user) if smart_execution.is_stock_underlying(h.underlying)]
    spots = await _spots(session, user, sorted({h.underlying for h in holdings if h.right != "FUT"} | {h.underlying for h in holdings}))
    flags = smart_execution.settlement_flags(holdings, spots, to_ist(now).date(), settings)
    unpriced = sorted({h.underlying for h in holdings if h.underlying not in spots and h.right != "FUT"})
    return {"warnings": flags, "unpriced": unpriced,
            "settings": {"days_before": settings.settlement_days_before, "band_pct": settings.settlement_band_pct,
                         "telegram": settings.settlement_telegram},
            "note": "Stock F&O held into expiry settles by delivery. Close or roll before expiry if you do not want delivery. "
                    "Information only: nothing is closed for you.",
            "as_of": now.isoformat()}


async def send_due(session: AsyncSession, now: Optional[datetime] = None) -> int:
    """Once a day per flagged position, a notification for each user who keeps the Telegram choice on, in organisations
    with `ui_v2` + `smart_execution`. Returns how many were raised. Dedupe: a SETTLEMENT_WARNING for the same position
    since IST midnight."""
    from app.core.enums import NotificationSeverity, NotificationType
    from app.notifications.service import notify
    from app.platform.controls import flag_enabled
    now = now or datetime.now(timezone.utc)
    day_start = datetime.combine(to_ist(now).date(), time(0, 0), tzinfo=IST).astimezone(timezone.utc)
    user_ids = sorted(set(await session.scalars(select(TradeRecord.user_id).where(
        TradeRecord.exit_time.is_(None), TradeRecord.instrument_kind.in_(("OPTION", "FUTURE")), TradeRecord.user_id.is_not(None)))))
    sent = 0
    for user_id in user_ids:
        user = await session.get(User, user_id)
        if user is None or not await flag_enabled(session, "ui_v2", user.tenant_id) or \
                not await flag_enabled(session, smart_execution.FLAG, user.tenant_id):
            continue
        view = await for_user(session, user, now)
        if not view["settings"]["telegram"]:
            continue
        for warning in view["warnings"]:
            seen = await session.scalar(select(func.count()).select_from(NotificationRecord).where(
                NotificationRecord.tenant_id == user.tenant_id, NotificationRecord.user_id == user.id,
                NotificationRecord.event_type == NotificationType.SETTLEMENT_WARNING.value,
                NotificationRecord.related_trade_id == warning["trade_id"], NotificationRecord.created_at >= day_start))
            if seen:
                continue
            await notify(session, user.tenant_id, NotificationType.SETTLEMENT_WARNING, title=f"Delivery ahead: {warning['label']}",
                         message=warning["message"], severity=NotificationSeverity.WARNING, user_id=user.id,
                         related_trade_id=warning["trade_id"])
            sent += 1
    return sent
