"""U5 D3a: context for the result board's heatmap and chart-grid views - per symbol its reference data (sector,
industry, market cap from `companies`) and its recent daily closes from the shared lake (a sparkline and the last
day's change), as of now.

POST /api/screener/board/context  {"symbols": [...], "exchange": "NSE"}
  * Reference data (`companies`): the shared rows plus the caller's organisation's own contributed rows
    (app/fundamentals/visibility.visible) - never another organisation's. A symbol without a visible row has none, and
    the heatmap files it under "Unclassified" - never a guessed sector.
  * Closes: the lake's daily bars that ended by now, shared-licensed sources only (NSE bhavcopy, vendor, the operator's
    import - never a broker stream); one bar per trading day (the IST date, app/market_lake/ingest.trade_date): the
    preferred source, then its highest version; at most CLOSES bars, adjusted for the splits / bonuses known now
    (app/market_lake/adjust.share_factor), so a split inside the window is not a fake crash.
  * `change_pct` is the last adjusted close vs the one before; null with fewer than two closes.
  * Up to MAX_SYMBOLS symbols in one call (one batched read each for companies, bars and corporate actions), so a
    NIFTY 500 scan gets its heatmap from a single call.
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
from app.db.models import CompanyRecord, MdCandleRecord, MdCorporateActionRecord, User
from app.db.session import get_session
from app.fundamentals.visibility import visible
from app.market_lake.adjust import SHARE_ACTIONS, share_factor
from app.market_lake.ingest import trade_date
from app.platform.controls import flag_enabled

router = APIRouter(prefix="/api/screener/board", tags=["screener"])
FLAG = "screener_board_views"
MAX_SYMBOLS = 500                     # one call covers a NIFTY 500 scan (batched reads, no per-symbol query)
CLOSES = 30
LOOKBACK_DAYS = 60
DAILY_LABELS = ("day", "1d")
UNCLASSIFIED = "Unclassified"


class ContextBody(BaseModel):
    symbols: List[str] = Field(min_length=1, max_length=MAX_SYMBOLS)
    exchange: str = Field(default="NSE", pattern=r"^(NSE|BSE)$")


def _clean(symbols: List[str]) -> List[str]:
    return list(dict.fromkeys(" ".join(s.upper().split()) for s in symbols if s and s.strip()))


async def share_actions(session: AsyncSession, symbols: List[str], exchange: str, now: datetime,
                        since: datetime) -> Dict[str, List[MdCorporateActionRecord]]:
    """{SYMBOL: [the latest visible version of each split / bonus (ex_date, action)]} known by `now` whose ex-date is
    after `since` (an older one moves no bar in the window) - app/market_lake/adjust.actions_as_of in one batched read."""
    if not symbols:
        return {}
    q = (select(MdCorporateActionRecord)
         .where(MdCorporateActionRecord.symbol.in_(symbols), MdCorporateActionRecord.exchange == exchange.upper(),
                MdCorporateActionRecord.action.in_(SHARE_ACTIONS), MdCorporateActionRecord.ingested_at <= now,
                MdCorporateActionRecord.ex_date > trade_date(since)))
    latest: Dict[str, Dict[Any, MdCorporateActionRecord]] = {}
    for row in await session.scalars(q):
        per = latest.setdefault(row.symbol.upper(), {})
        k = (row.ex_date, row.action)
        if k not in per or row.version > per[k].version:
            per[k] = row
    return {s: sorted(v.values(), key=lambda r: r.ex_date) for s, v in latest.items()}


async def closes(session: AsyncSession, keys: List[str], now: datetime,
                 actions: Optional[Dict[str, List[MdCorporateActionRecord]]] = None) -> Dict[str, List[Dict[str, Any]]]:
    """{key: [{t, c}, ...]} - the last CLOSES daily closes per key, oldest first (one batched read). One bar per trading
    day (IST date): the preferred shared source, then its highest version. `actions` ({key: splits / bonuses}) adjusts
    each close by app/market_lake/adjust.share_factor for its day."""
    if not keys:
        return {}
    n = len(SHARED_SOURCES)
    rank = case(*[(MdCandleRecord.source.like(f"{p}%"), i) for i, p in enumerate(SHARED_SOURCES)], else_=n)
    q = (select(MdCandleRecord.instrument_key, MdCandleRecord.ts, rank, MdCandleRecord.version, cast(MdCandleRecord.close, Float))
         .where(MdCandleRecord.instrument_key.in_(keys), MdCandleRecord.timeframe.in_(DAILY_LABELS),
                MdCandleRecord.ts > now - timedelta(days=LOOKBACK_DAYS), MdCandleRecord.ts <= now,
                or_(*[MdCandleRecord.source.like(f"{p}%") for p in SHARED_SOURCES]),
                or_(MdCandleRecord.version == 1, MdCandleRecord.ingested_at <= now)))
    best: Dict[str, Dict[Any, tuple]] = {}
    for key, ts, r, version, c in (await session.execute(q)).all():
        t = ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
        day = trade_date(t)
        pref = (-int(r), int(version))                         # the preferred source (lowest rank), then the highest version
        cur = best.setdefault(key, {}).get(day)
        if cur is None or pref > cur[0]:
            best[key][day] = (pref, t, float(c))
    out: Dict[str, List[Dict[str, Any]]] = {}
    for key, days in best.items():
        acts = (actions or {}).get(key, [])
        series = [{"t": int(t.timestamp()), "c": round(c * share_factor(acts, day), 4)} for day, (_, t, c) in sorted(days.items())]
        out[key] = series[-CLOSES:]
    return out


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
    rows = (await session.scalars(select(CompanyRecord).where(func.upper(CompanyRecord.symbol).in_(symbols),
                                                              visible(CompanyRecord, user.tenant_id)))).all()
    ref = {r.symbol.upper(): r for r in rows}
    keys = {s: f"{body.exchange}:{s}" for s in symbols}
    acts = await share_actions(session, symbols, body.exchange, now, now - timedelta(days=LOOKBACK_DAYS))
    found = await closes(session, list(keys.values()), now, {keys[s]: a for s, a in acts.items() if s in keys})
    out = []
    for s in symbols:
        r = ref.get(s)
        series = found.get(keys[s], [])
        out.append({"symbol": s, "sector": (r.sector if r and r.sector else UNCLASSIFIED), "industry": r.industry if r else None,
                    "market_cap": r.market_cap if r else None, "cap_category": r.cap_category if r else None,
                    "closes": series, "change_pct": _change(series)})
    return {"as_of": now.isoformat(), "exchange": body.exchange, "symbols": out,
            "note": "Sector and market cap from the company reference data; closes from the shared daily history, adjusted for splits and bonuses (reference only)."}


__all__ = ["router", "FLAG", "closes", "share_actions", "board_context"]
