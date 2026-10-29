"""AUD-T7 (owner audit 28 Oct; flag `cost_meter`, needs `ui_v2`): the cost-of-trading meter on My trades.

For the caller's OWN closed trades, this financial year (1 April - 31 March, IST) and this month (IST): the charges
against the gross P&L before charges and the net after them, the charges as a share of the gross profit, and the
per-trade figures. PAPER and LIVE are two separate books and are never added together.

What a stored trade holds (the evidence for gross vs net):
- `TradeRecord.pnl` is NET of charges. The one close path, `app/trading/position_monitor.py` (close_position), writes
  `trade.charges = <estimate>` and `trade.pnl = round(gross_pnl - charges, 2)`; the contract-note import,
  `app/contract_notes/service.py`, recomputes `new_pnl = gross - new_charges` when it applies a note. So the gross P&L
  of a closed trade is `pnl + charges`.
- `TradeRecord.charges` holds the broker's contract-note charges once a note was matched and applied (the import
  overwrites the estimate and sets `charges_source = "CONTRACT_NOTE"`); until then the platform's own estimate
  (`charges_source = "ESTIMATED"`). The contract-note figure is therefore preferred whenever one exists, and each
  period says how many of its trades are from contract notes and how many are estimated.

Read-only: it changes no trade and places no orders. Trades priced from anything but the platform's own quotes
(`price_source`, review H2) are left out, as in the PAPER account.
"""
from __future__ import annotations

from datetime import datetime, timezone
from statistics import median
from typing import Any, Dict, Iterable, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.db.models import TradeRecord, User
from app.db.session import get_session
from app.market_data.calendar import IST

FLAG = "cost_meter"
CONTRACT_NOTE = "CONTRACT_NOTE"
MODES = ("PAPER", "LIVE")

router = APIRouter(prefix="/api/trades", tags=["trading"])


def _ist(when: datetime) -> datetime:
    return (when if when.tzinfo else when.replace(tzinfo=timezone.utc)).astimezone(IST)


def year_start(now: datetime) -> datetime:
    """The start of the Indian financial year (1 April, 00:00 IST) that `now` falls in."""
    local = _ist(now)
    year = local.year if local.month >= 4 else local.year - 1
    return datetime(year, 4, 1, tzinfo=IST)


def month_start(now: datetime) -> datetime:
    local = _ist(now)
    return datetime(local.year, local.month, 1, tzinfo=IST)


def _share(part: float, whole: float) -> Optional[float]:
    """part / whole as a fraction, or None when `whole` is not a profit (zero or a loss): no division by zero, and no
    share of a loss."""
    return round(part / whole, 4) if whole > 0 else None


def period(trades: Iterable[TradeRecord]) -> Dict[str, Any]:
    """The figures of one period of one mode's closed trades."""
    rows = list(trades)
    charges = [float(t.charges or 0.0) for t in rows]
    nets = [float(t.pnl or 0.0) for t in rows]
    grosses = [n + c for n, c in zip(nets, charges)]           # pnl is net of charges (module docstring)
    gross, total_charges, net = sum(grosses), sum(charges), sum(nets)
    # Per trade: the share of that trade's own gross profit its charges took (trades with a gross profit only).
    shares = [c / g for g, c in zip(grosses, charges) if g > 0]
    from_notes = sum(1 for t in rows if (t.charges_source or "") == CONTRACT_NOTE)
    return {
        "trades": len(rows),
        "gross": round(gross, 2),
        "charges": round(total_charges, 2),
        "net": round(net, 2),
        "charges_share_of_gross": _share(total_charges, gross),
        "charges_per_trade": round(total_charges / len(rows), 2) if rows else None,
        "median_trade_share": round(median(shares), 4) if shares else None,
        "trades_with_gross_profit": len(shares),
        "charges_took_whole_profit": sum(1 for g, c in zip(grosses, charges) if 0 < g <= c),
        "from_contract_notes": from_notes,
        "estimated": len(rows) - from_notes,
    }


def meter(trades: Iterable[TradeRecord], now: datetime) -> Dict[str, Any]:
    """Per mode (PAPER, LIVE - never mixed): this financial year and this month, by each trade's exit time in IST."""
    fy, month = year_start(now), month_start(now)
    by_mode: Dict[str, List[TradeRecord]] = {}
    for t in trades:
        if t.exit_time is None or t.pnl is None or t.price_source:
            continue
        if not (fy <= _ist(t.exit_time) <= _ist(now)):
            continue
        by_mode.setdefault((t.mode or "PAPER").upper(), []).append(t)
    modes = [m for m in MODES if m in by_mode] + sorted(m for m in by_mode if m not in MODES)
    return {
        "year_start": fy.date().isoformat(), "month_start": month.date().isoformat(),
        "modes": [{"mode": m, "year": period(by_mode[m]), "month": period(t for t in by_mode[m] if _ist(t.exit_time) >= month)}
                  for m in modes],
    }


async def _flags(session: AsyncSession, user: User) -> None:
    from app.platform.controls import flag_enabled
    for name in ("ui_v2", FLAG):
        if not await flag_enabled(session, name, user.tenant_id):
            raise HTTPException(status_code=404, detail="Not found", headers={"X-Feature-Disabled": name})


@router.get("/cost-meter")
async def cost_meter(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """The caller's own closed trades only (tenant AND user), this financial year."""
    await _flags(session, user)
    now = datetime.now(timezone.utc)
    rows = await session.scalars(select(TradeRecord).where(
        TradeRecord.tenant_id == user.tenant_id, TradeRecord.user_id == user.id,
        TradeRecord.exit_time.is_not(None), TradeRecord.exit_time >= year_start(now).astimezone(timezone.utc)))
    return meter(rows, now)


__all__ = ["router", "meter", "period", "year_start", "month_start", "FLAG"]
