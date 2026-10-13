"""N4 CH4c: an underlying's futures basis, cost of carry, calendar spread and rollover - daily, from the shared lake.

GET /api/charts/contracts/basis?underlying=NIFTY&at=&start=
  Per trade date, from the F&O bhavcopy's futures rows (`NFO:<SYM>:FUT:<expiry>`) and the underlying's own close
  (`NSE:<SYM>`, or the index close `NSE_INDEX:<code>` for an index future), as of `at`:
  * the near, next and far months - the live contracts (expiry on or after the trade date) by expiry, so the expiry day
    is still the near month's - with each one's close and OI;
  * basis = near close - spot close, and as % of the spot; the annualised cost of carry = basis % x 365 / the
    calendar days to the near expiry (at least 1);
  * the calendar spread = next close - near close;
  * all expiries' OI and the rollover % = (all - near OI) / all OI.
  Reads: shared-licensed sources only (NSE bhavcopy, vendor, the operator's history - never a row an organisation's
  broker session or stream wrote), per contract and trade date the preferred source, then its highest version; a
  correction (version > 1) counts only once it was ingested by `at`. The futures keys are every date a monthly expiry
  can fall on in the window (the 15th to the month's end, plus four months ahead), read by exact key in chunks.
Behind `chart_futures_basis` on top of `chart_contracts` (both off by default -> 503). Reference only: no signal, no
order path. The participant-wise OI chart waits for that NSE file to be ingested (OPEN_QUESTIONS CH4-4).
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import Float, case, cast, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.charts import contracts as C
from app.charts.price_action_layer import SHARED_SOURCES
from app.db.models import MdCandleRecord, User
from app.db.session import get_session
from app.platform.controls import require_flag

router = APIRouter(prefix="/api/charts/contracts", tags=["charts"])
FLAG = "chart_futures_basis"
SCHEMA = "basis/1"
KEYS_PER_QUERY = 5000                 # instrument keys bound in one IN (...), well under Postgres's 32,767 parameters
MAX_ROWS = 40_000                     # raw rows read for one answer (contracts x days x sources x versions)
MONTHS = 3                            # near, next, far
NOTE = ("Futures basis, carry and rollover from the shared daily history - reference only, not signals or "
        "recommendations.")
_SYMBOL = re.compile(r"^[A-Z0-9&_\-]{1,30}$")
# Index futures: the index whose close is the spot (U1 index catalogue names).
INDEX_SPOT: Dict[str, str] = {"NIFTY": "NIFTY 50", "BANKNIFTY": "NIFTY BANK", "FINNIFTY": "NIFTY FINANCIAL SERVICES",
                              "MIDCPNIFTY": "NIFTY MIDCAP SELECT", "NIFTYNXT50": "NIFTY NEXT 50"}


def spot_key(underlying: str) -> str:
    sym = underlying.upper()
    if sym in INDEX_SPOT:
        from app.universe.fo_eod import code_for
        return f"NSE_INDEX:{code_for(INDEX_SPOT[sym])}"
    return f"NSE:{sym}"


def expiry_candidates(start: date, end: date, ahead_months: int = 4) -> List[date]:
    """Every date a monthly futures expiry can fall on from `start` to `ahead_months` after `end`: the 15th to the month's
    end (the last Thursday - the last Tuesday from September 2025 - or earlier for a holiday)."""
    out: List[date] = []
    y, m = start.year, start.month
    last = (end.year * 12 + end.month - 1) + ahead_months
    while y * 12 + m - 1 <= last:
        nxt = date(y + (m == 12), m % 12 + 1, 1)
        out += [date(y, m, d) for d in range(15, (nxt - timedelta(days=1)).day + 1) if date(y, m, d) >= start]
        y, m = nxt.year, nxt.month
    return out


def _chunks(keys: Sequence[str], size: int = KEYS_PER_QUERY) -> List[List[str]]:
    return [list(keys[i:i + size]) for i in range(0, len(keys), size)]


async def read_daily(session: AsyncSession, keys: Sequence[str], start: datetime, end: datetime) -> Dict[str, Dict[date, Dict[str, Any]]]:
    """{key: {trade date: {c, oi, source}}} for bars that ENDED in (start, end]: shared sources only, per key and trade
    date the preferred source then its highest version; a correction counts once ingested by `end`."""
    rank = case(*[(MdCandleRecord.source.like(f"{p}%"), i) for i, p in enumerate(SHARED_SOURCES)], else_=len(SHARED_SOURCES))
    best: Dict[Tuple[str, date], Tuple[Tuple[int, int], Dict[str, Any]]] = {}
    seen = 0
    for chunk in _chunks(list(keys)):
        q = (select(MdCandleRecord.instrument_key, MdCandleRecord.ts, cast(MdCandleRecord.close, Float), MdCandleRecord.oi,
                    MdCandleRecord.source, MdCandleRecord.version, rank.label("rk"))
             .where(MdCandleRecord.instrument_key.in_(chunk), MdCandleRecord.timeframe.in_(C.DAILY_LABELS), MdCandleRecord.ts > start,
                    MdCandleRecord.ts <= end, or_(*[MdCandleRecord.source.like(f"{p}%") for p in SHARED_SOURCES]),
                    or_(MdCandleRecord.version == 1, MdCandleRecord.ingested_at <= end))
             .limit(MAX_ROWS + 1 - seen))
        rows = (await session.execute(q)).all()
        seen += len(rows)
        if seen > MAX_ROWS:
            raise HTTPException(status_code=422, detail="the window holds too many rows; choose a later `start`")
        for key, ts, close, oi, src, version, rk in rows:
            t = ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
            slot, pref = (str(key), t.date()), (int(rk), -int(version))
            if slot not in best or pref < best[slot][0]:
                best[slot] = (pref, {"c": float(close), "oi": None if oi is None else int(oi), "source": str(src).split(":")[0]})
    out: Dict[str, Dict[date, Dict[str, Any]]] = {}
    for (key, day), (_p, row) in best.items():
        out.setdefault(key, {})[day] = row
    return out


def _r(x: Optional[float], k: int = 4) -> Optional[float]:
    return None if x is None else round(float(x), k)


def build(underlying: str, futures: Dict[str, Dict[date, Dict[str, Any]]], spot: Dict[date, Dict[str, Any]]) -> Dict[str, Any]:
    """The per-day series (pure)."""
    by_day: Dict[date, List[Tuple[date, Dict[str, Any]]]] = {}
    for key, rows_by_day in futures.items():
        expiry = C.parse_key(key)["expiry"]
        for d, row in rows_by_day.items():
            if expiry >= d:                                          # live on that day (the expiry day is the near month's)
                by_day.setdefault(d, []).append((expiry, row))
    days = sorted(by_day)
    months: List[Dict[str, List[Any]]] = [{"expiry": [], "close": [], "oi": []} for _ in range(MONTHS)]
    out: Dict[str, List[Any]] = {k: [] for k in ("spot", "basis", "basis_pct", "carry_pct", "calendar", "total_oi", "rollover_pct", "dte")}
    sources: set = set()
    for d in days:
        live = sorted(by_day[d], key=lambda c: c[0])
        for i in range(MONTHS):
            if i < len(live):
                exp, row = live[i]
                months[i]["expiry"].append(exp.isoformat())
                months[i]["close"].append(row["c"])
                months[i]["oi"].append(row["oi"])
                sources.add(row["source"])
            else:
                for k in ("expiry", "close", "oi"):
                    months[i][k].append(None)
        near_exp, near = live[0]
        s = spot.get(d, {}).get("c")
        if s is not None:
            sources.add(spot[d]["source"])
        dte = max(1, (near_exp - d).days)
        basis = None if s is None else near["c"] - s
        basis_pct = None if s is None or s <= 0 else (near["c"] / s - 1.0) * 100.0
        ois = [row["oi"] for _e, row in live]
        total = float(sum(ois)) if ois and all(o is not None for o in ois) else None
        out["spot"].append(s)
        out["basis"].append(_r(basis))
        out["basis_pct"].append(_r(basis_pct))
        out["carry_pct"].append(_r(None if basis_pct is None else basis_pct * 365.0 / dte))
        out["calendar"].append(_r(live[1][1]["c"] - near["c"]) if len(live) > 1 else None)
        out["total_oi"].append(None if total is None else int(total))
        out["rollover_pct"].append(_r(None if not total or near["oi"] is None else (total - near["oi"]) / total * 100.0))
        out["dte"].append((near_exp - d).days)
    rolls = [days[i].isoformat() for i in range(1, len(days)) if months[0]["expiry"][i] != months[0]["expiry"][i - 1]]
    return {"underlying": underlying, "days": [d.isoformat() for d in days], "times": [C._start_epoch(d) for d in days],
            "near": months[0], "next": months[1], "far": months[2], **out, "rolls": rolls, "sources": sorted(sources)}


@router.get("/basis")
async def basis(underlying: str = Query(..., min_length=1, max_length=30), at: Optional[datetime] = None, start: Optional[datetime] = None,
                user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """The underlying's futures basis, carry, calendar spread and rollover per trade date, as of `at` (module note)."""
    await require_flag(session, C.FLAG, user.tenant_id)
    await require_flag(session, FLAG, user.tenant_id)
    sym = " ".join(underlying.upper().split())
    if not _SYMBOL.match(sym):
        raise HTTPException(status_code=422, detail=f"not an F&O underlying: {underlying!r}")
    now = datetime.now(timezone.utc)
    at_utc, start_utc = C._aware(at, "at"), C._aware(start, "start")
    when = now if at_utc is None or at_utc > now else at_utc
    begin = start_utc if start_utc is not None else when - timedelta(days=C.DEFAULT_DAYS)
    if begin >= when:
        raise HTTPException(status_code=422, detail="`start` must be before `at`")
    begin = max(begin, when - timedelta(days=C.MAX_DAYS))
    keys = [C.contract_key(sym, "FUT", d) for d in expiry_candidates(begin.date(), when.date())]
    futures = await read_daily(session, keys, begin, when)
    spot = (await read_daily(session, [spot_key(sym)], begin, when)).get(spot_key(sym), {})
    body: Dict[str, Any] = {"schema": SCHEMA, "at": when.isoformat(), "start": begin.isoformat(), "spot_key": spot_key(sym),
                            **build(sym, futures, spot), "note": NOTE}
    if not body["days"]:
        body["reason"] = "no shared daily futures bars for this underlying in the window"
    elif all(s is None for s in body["spot"]):
        body["reason"] = f"no shared daily close for the spot ({spot_key(sym)}): basis and carry are not shown"
    return body


__all__ = ["router", "FLAG", "SCHEMA", "build", "read_daily", "spot_key", "expiry_candidates", "INDEX_SPOT"]
