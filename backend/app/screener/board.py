"""U5 D3a: context for the result board's heatmap and chart-grid views - per symbol its reference data (sector,
industry, market cap from `companies`) and its recent daily closes from the shared lake (a sparkline and the last
day's change), as of now.

POST /api/screener/board/context  {"symbols": [...], "exchange": "NSE"}
  * Reference data is platform-wide (`companies`, user-entered with a citation): a symbol without a row has none, and
    the heatmap files it under "Unclassified" - never a guessed sector.
  * Closes: the lake's daily bars that ended by now, shared-licensed sources only (NSE bhavcopy, vendor, the operator's
    import - never a broker stream), the preferred source then its highest version per day; at most CLOSES bars.
  * `change_pct` is the last close vs the one before; null with fewer than two closes.
Behind `screener_board_views` (off by default -> 404, like the screener). Research only: nothing here places an order.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import Float, case, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.charts.price_action_layer import SHARED_SOURCES
from app.db.models import CompanyRecord, MdCandleRecord, User
from app.db.session import get_session
from app.platform.controls import flag_enabled

router = APIRouter(prefix="/api/screener/board", tags=["screener"])
FLAG = "screener_board_views"
MAX_SYMBOLS = 50
CLOSES = 30
LOOKBACK_DAYS = 60
DAILY_LABELS = ("day", "1d")
UNCLASSIFIED = "Unclassified"


class ContextBody(BaseModel):
    symbols: List[str] = Field(min_length=1, max_length=MAX_SYMBOLS)
    exchange: str = Field(default="NSE", pattern=r"^(NSE|BSE)$")


def _clean(symbols: List[str]) -> List[str]:
    return list(dict.fromkeys(" ".join(s.upper().split()) for s in symbols if s and s.strip()))


async def closes(session: AsyncSession, keys: List[str], now: datetime) -> Dict[str, List[Dict[str, Any]]]:
    """{key: [{t, c}, ...]} - the last CLOSES daily closes per key, oldest first (one batched read)."""
    if not keys:
        return {}
    rank = case(*[(MdCandleRecord.source.like(f"{p}%"), i) for i, p in enumerate(SHARED_SOURCES)], else_=len(SHARED_SOURCES))
    q = (select(MdCandleRecord.instrument_key, MdCandleRecord.ts, cast(MdCandleRecord.close, Float))
         .where(MdCandleRecord.instrument_key.in_(keys), MdCandleRecord.timeframe.in_(DAILY_LABELS),
                MdCandleRecord.ts > now - timedelta(days=LOOKBACK_DAYS), MdCandleRecord.ts <= now,
                or_(*[MdCandleRecord.source.like(f"{p}%") for p in SHARED_SOURCES]),
                or_(MdCandleRecord.version == 1, MdCandleRecord.ingested_at <= now))
         .order_by(MdCandleRecord.instrument_key, MdCandleRecord.ts, rank.desc(), MdCandleRecord.version))
    by: Dict[str, Dict[Any, float]] = {}
    for key, ts, c in (await session.execute(q)).all():
        t = ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
        by.setdefault(key, {})[t] = float(c)                    # the winner comes last per day
    return {k: [{"t": int(t.timestamp()), "c": round(c, 4)} for t, c in sorted(v.items())][-CLOSES:] for k, v in by.items()}


def _change(series: List[Dict[str, Any]]) -> Optional[float]:
    if len(series) < 2 or not series[-2]["c"]:
        return None
    return round((series[-1]["c"] / series[-2]["c"] - 1.0) * 100.0, 2)


@router.post("/context")
async def board_context(body: ContextBody, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    for flag in ("screener_v2", FLAG):
        if not await flag_enabled(session, flag, user.tenant_id):
            raise HTTPException(status_code=404, detail="Not Found")
    symbols = _clean(body.symbols)
    now = datetime.now(timezone.utc)
    rows = (await session.scalars(select(CompanyRecord).where(func.upper(CompanyRecord.symbol).in_(symbols)))).all()
    ref = {r.symbol.upper(): r for r in rows}
    keys = {s: f"{body.exchange}:{s}" for s in symbols}
    found = await closes(session, list(keys.values()), now)
    out = []
    for s in symbols:
        r = ref.get(s)
        series = found.get(keys[s], [])
        out.append({"symbol": s, "sector": (r.sector if r and r.sector else UNCLASSIFIED), "industry": r.industry if r else None,
                    "market_cap": r.market_cap if r else None, "cap_category": r.cap_category if r else None,
                    "closes": series, "change_pct": _change(series)})
    return {"as_of": now.isoformat(), "exchange": body.exchange, "symbols": out,
            "note": "Sector and market cap from the company reference data; closes from the shared daily history (reference only)."}


__all__ = ["router", "FLAG", "closes", "board_context"]
