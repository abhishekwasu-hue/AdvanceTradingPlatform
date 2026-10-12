"""N4 CH4b: option / future contract charts and synthetic spreads - daily, from the shared lake's F&O bars.

GET /api/charts/contracts/search?underlying=NIFTY&kind=CE&expiry=2026-10-27&near=25000&limit=20
  Contracts from the instrument master (`instruments`, platform reference data) with their lake key
  (`NFO:<SYM>:FUT:<expiry>`, `NFO:<SYM>:<CE|PE>:<expiry>:<strike>` - the F&O bhavcopy's keys, HB1).
GET /api/charts/contracts/series?leg=NFO:NIFTY:CE:2026-10-27:25000&leg=-1*NFO:NIFTY:CE:2026-10-27:25500&at=&start=&iv=true
  * Each `leg` is `[qty*]KEY` (qty a signed whole number, default 1; at most MAX_LEGS legs). Daily bars only (the lake
    has no intraday contract history yet - OPEN_QUESTIONS CH4-1).
  * As of `at`: bars that ended by `at` from shared-licensed sources only (NSE bhavcopy, vendor, the operator's import -
    never a broker stream), the preferred source per day then its highest version; a correction counts only once it was
    ingested by `at`. Expired contracts stay chartable (the lake keeps them).
  * One leg: its own OHLC, volume and OI. Several legs: the spread's close = the sum of each leg's close x its qty, on
    the days every leg has a bar (no other OHLC is invented), plus each leg's OI.
  * `iv=true`: each option leg's IV per day, COMPUTED (Black-76 from the leg's close and the same-expiry future's close,
    r = RISK_FREE_RATE) - never a quoted IV; null where it cannot be solved or there is no future that day.
Behind the `chart_contracts` flag (off by default -> 503). Reference data only: nothing here places or proposes an order.
"""
from __future__ import annotations

import math
import re
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import Float, case, cast, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.charts.price_action_layer import SHARED_SOURCES
from app.db.models import InstrumentRecord, MdCandleRecord, User
from app.db.session import get_session
from app.platform.controls import require_flag

router = APIRouter(prefix="/api/charts/contracts", tags=["charts"])
FLAG = "chart_contracts"
SCHEMA = "contract/1"
MAX_LEGS = 4
MAX_QTY = 100
MAX_DAYS = 3660
DEFAULT_DAYS = 365
MAX_ROWS = 4 * 3660
DAILY_LABELS = ("day", "1d")
NOTE = "Daily contract bars from the shared market-data history - reference only, not signals or recommendations."
IV_NOTE = ("Computed IV: Black-76 from the day's close and the same-expiry future's close (risk-free rate {r:.2%}). "
           "Not a quoted IV.")
_KEY = re.compile(r"^NFO:([A-Z0-9&_\-]{1,30}):(FUT|CE|PE):(\d{4}-\d{2}-\d{2})(?::(\d+(?:\.\d+)?))?$")
_LEG = re.compile(r"^(?:([+-]?\d{1,3})\*)?(.+)$")
IST_OPEN_UTC = time(3, 45)


def strike_text(value: float) -> str:
    """The bhavcopy's strike text (`bhavcopy._strike`): 25000 -> "25000", 112.5 -> "112.5"."""
    return f"{value:.4f}".rstrip("0").rstrip(".")


def contract_key(underlying: str, kind: str, expiry: date, strike: Optional[float] = None) -> str:
    kind = kind.upper()
    base = f"NFO:{underlying.upper()}:{kind}:{expiry.isoformat()}"
    return base if kind == "FUT" else f"{base}:{strike_text(float(strike or 0))}"


def parse_key(key: str) -> Dict[str, Any]:
    """{key, underlying, kind, expiry, strike} of a lake F&O key; ValueError for anything else."""
    m = _KEY.match(" ".join(str(key or "").upper().split()))
    if not m or (m.group(2) == "FUT") != (m.group(4) is None):
        raise ValueError(f"not an F&O contract key: {key!r} (NFO:<SYMBOL>:FUT:<expiry> or NFO:<SYMBOL>:<CE|PE>:<expiry>:<strike>)")
    exp = date.fromisoformat(m.group(3))
    strike = float(m.group(4)) if m.group(4) else None
    return {"key": contract_key(m.group(1), m.group(2), exp, strike), "underlying": m.group(1), "kind": m.group(2), "expiry": exp, "strike": strike}


def parse_leg(text: str) -> Tuple[int, Dict[str, Any]]:
    m = _LEG.match(str(text or "").strip())
    if not m:
        raise ValueError(f"bad leg {text!r}")
    qty = int(m.group(1)) if m.group(1) else 1
    if qty == 0 or abs(qty) > MAX_QTY:
        raise ValueError(f"a leg's quantity is a whole number from -{MAX_QTY} to {MAX_QTY}, not 0 ({text!r})")
    return qty, parse_key(m.group(2))


async def daily_bars(session: AsyncSession, key: str, start: datetime, end: datetime) -> Dict[date, Dict[str, Any]]:
    """{trade date: {o, h, l, c, v, oi, source}} with END in (start, end], shared-licensed sources only, per day the
    preferred source then its highest version; a correction (version > 1) counts once ingested by `end`."""
    rank = case(*[(MdCandleRecord.source.like(f"{p}%"), i) for i, p in enumerate(SHARED_SOURCES)], else_=len(SHARED_SOURCES))
    q = (select(MdCandleRecord.ts, MdCandleRecord.version, cast(MdCandleRecord.open, Float), cast(MdCandleRecord.high, Float),
                cast(MdCandleRecord.low, Float), cast(MdCandleRecord.close, Float), MdCandleRecord.volume, MdCandleRecord.oi, MdCandleRecord.source)
         .where(MdCandleRecord.instrument_key == key, MdCandleRecord.timeframe.in_(DAILY_LABELS), MdCandleRecord.ts > start, MdCandleRecord.ts <= end,
                or_(*[MdCandleRecord.source.like(f"{p}%") for p in SHARED_SOURCES]),
                or_(MdCandleRecord.version == 1, MdCandleRecord.ingested_at <= end))
         .order_by(MdCandleRecord.ts, rank.desc(), MdCandleRecord.version).limit(MAX_ROWS + 1))
    rows = (await session.execute(q)).all()
    if len(rows) > MAX_ROWS:
        raise HTTPException(status_code=422, detail="the window holds too many rows; choose a later `start`")
    out: Dict[date, Dict[str, Any]] = {}
    for ts, _v, o, h, lo, c, vol, oi, src in rows:                # the winner comes last per day
        t = ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
        out[t.date()] = {"o": o, "h": h, "l": lo, "c": c, "v": int(vol or 0), "oi": None if oi is None else int(oi), "source": src.split(":")[0]}
    return out


def computed_iv(price: Optional[float], future: Optional[float], strike: float, kind: str, expiry: date, day: date, r: float) -> Optional[float]:
    """Black-76 IV (per year, e.g. 0.18) of a daily option close against the same-expiry future's close; None when it
    cannot be solved (no future, expired, a price outside the no-arbitrage bounds)."""
    from app.option_chain.greeks import implied_volatility, time_to_expiry_years
    from app.option_chain.models import OptionType
    if price is None or future is None or price <= 0 or future <= 0 or day > expiry:
        return None
    close_instant = datetime.combine(day, time(10, 0), tzinfo=timezone.utc)          # the bar's END, 15:30 IST
    t = time_to_expiry_years(expiry, close_instant)
    spot = future * math.exp(-r * t)                                                  # Black-76 through Black-Scholes
    iv = implied_volatility(price, spot, strike, t, r, OptionType.CALL if kind == "CE" else OptionType.PUT)
    return None if iv is None else round(iv, 4)


def _aware(t: Optional[datetime], name: str) -> Optional[datetime]:
    if t is not None and t.tzinfo is None:
        raise HTTPException(status_code=422, detail=f"`{name}` must carry its time zone (for example 2026-10-09T15:30:00+05:30)")
    return None if t is None else t.astimezone(timezone.utc)


def _start_epoch(day: date) -> int:
    return int(datetime.combine(day, IST_OPEN_UTC, tzinfo=timezone.utc).timestamp())    # the chart's daily candle time (09:15 IST)


@router.get("/search")
async def search(underlying: str = Query(..., min_length=1, max_length=30), kind: Optional[str] = Query(default=None, pattern="^(CE|PE|FUT)$"),
                 expiry: Optional[date] = None, near: Optional[float] = Query(default=None, gt=0), limit: int = Query(default=20, ge=1, le=100),
                 user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """Contracts of an underlying from the instrument master, nearest expiry first, then nearest to `near` (a strike)."""
    await require_flag(session, FLAG, user.tenant_id)
    name = " ".join(underlying.upper().split())
    q = select(InstrumentRecord).where(InstrumentRecord.underlying == name, InstrumentRecord.instrument_type.in_([kind] if kind else ["CE", "PE", "FUT"]),
                                       InstrumentRecord.expiry.is_not(None))
    if expiry is not None:
        q = q.where(InstrumentRecord.expiry == expiry)
    seen: Dict[str, Dict[str, Any]] = {}
    for r in (await session.scalars(q.limit(5000))).all():
        if r.expiry is None or (r.instrument_type != "FUT" and r.strike is None):
            continue
        key = contract_key(name, r.instrument_type, r.expiry, r.strike)
        if key not in seen:                                    # one row per contract, whichever broker's master listed it
            seen[key] = {"key": key, "tradingsymbol": r.tradingsymbol, "underlying": name, "kind": r.instrument_type, "expiry": r.expiry.isoformat(),
                         "strike": r.strike, "lot_size": r.lot_size}
    rows = sorted(seen.values(), key=lambda c: (c["expiry"], abs((c["strike"] or 0) - near) if near else (c["strike"] or 0), c["kind"]))
    return {"underlying": name, "contracts": rows[:limit], "total": len(rows), "note": "From the instrument master: today's listed contracts."}


@router.get("/series")
async def series(leg: List[str] = Query(..., min_length=1, max_length=MAX_LEGS), at: Optional[datetime] = None, start: Optional[datetime] = None,
                 iv: bool = False, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    await require_flag(session, FLAG, user.tenant_id)
    try:
        legs = [parse_leg(text) for text in leg]
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    if len({c["key"] for _q, c in legs}) != len(legs):
        raise HTTPException(status_code=422, detail="each contract appears once; put its quantity on one leg")
    now = datetime.now(timezone.utc)
    at_utc, start_utc = _aware(at, "at"), _aware(start, "start")
    when = now if at_utc is None or at_utc > now else at_utc
    begin = start_utc if start_utc is not None else when - timedelta(days=DEFAULT_DAYS)
    if begin >= when:
        raise HTTPException(status_code=422, detail="`start` must be before `at`")
    begin = max(begin, when - timedelta(days=MAX_DAYS))
    bars = [await daily_bars(session, c["key"], begin, when) for _q, c in legs]
    days = sorted(set.intersection(*[set(b) for b in bars])) if bars else []
    out_legs: List[Dict[str, Any]] = []
    from app.core.config import RISK_FREE_RATE
    for (qty, c), b in zip(legs, bars):
        row: Dict[str, Any] = {"key": c["key"], "qty": qty, "underlying": c["underlying"], "kind": c["kind"], "expiry": c["expiry"].isoformat(),
                               "strike": c["strike"], "bars": len(b), "oi": [b[d]["oi"] for d in days],
                               "close": [b[d]["c"] for d in days], "sources": sorted({b[d]["source"] for d in days})}
        if iv and c["kind"] != "FUT":
            fut = await daily_bars(session, contract_key(c["underlying"], "FUT", c["expiry"]), begin, when)
            row["iv"] = [computed_iv(b[d]["c"], fut.get(d, {}).get("c"), float(c["strike"]), c["kind"], c["expiry"], d, RISK_FREE_RATE) for d in days]
        out_legs.append(row)
    body: Dict[str, Any] = {"schema": SCHEMA, "at": when.isoformat(), "start": begin.isoformat(), "days": [d.isoformat() for d in days],
                            "times": [_start_epoch(d) for d in days], "legs": out_legs, "note": NOTE}
    if len(legs) == 1:
        b = bars[0]
        body["ohlc"] = {k: [b[d][k] for d in days] for k in ("o", "h", "l", "c", "v")}
    body["spread"] = [round(sum(q * bars[i][d]["c"] for i, (q, _c) in enumerate(legs)), 4) for d in days]
    if iv:
        body["iv_note"] = IV_NOTE.format(r=RISK_FREE_RATE)
    if not days:
        body["reason"] = "no shared daily bars for these contracts in the window" + (" on common days" if len(legs) > 1 else "")
    return body


__all__ = ["router", "FLAG", "contract_key", "parse_key", "parse_leg", "daily_bars", "computed_iv", "strike_text"]
