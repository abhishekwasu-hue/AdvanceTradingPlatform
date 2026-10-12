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

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import Float, case, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.charts.price_action_layer import SHARED_SOURCES
from app.db.models import CompanyRecord, MdCandleRecord, ScreenRunRecord, User
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


async def closes(session: AsyncSession, keys: List[str], now: datetime, lookback_days: int = LOOKBACK_DAYS,
                 keep: int = CLOSES) -> Dict[str, List[Dict[str, Any]]]:
    """{key: [{t, c}, ...]} - the last `keep` daily closes per key, oldest first (one batched read)."""
    if not keys:
        return {}
    rank = case(*[(MdCandleRecord.source.like(f"{p}%"), i) for i, p in enumerate(SHARED_SOURCES)], else_=len(SHARED_SOURCES))
    q = (select(MdCandleRecord.instrument_key, MdCandleRecord.ts, cast(MdCandleRecord.close, Float))
         .where(MdCandleRecord.instrument_key.in_(keys), MdCandleRecord.timeframe.in_(DAILY_LABELS),
                MdCandleRecord.ts > now - timedelta(days=lookback_days), MdCandleRecord.ts <= now,
                or_(*[MdCandleRecord.source.like(f"{p}%") for p in SHARED_SOURCES]),
                or_(MdCandleRecord.version == 1, MdCandleRecord.ingested_at <= now))
         .order_by(MdCandleRecord.instrument_key, MdCandleRecord.ts, rank.desc(), MdCandleRecord.version))
    by: Dict[str, Dict[Any, float]] = {}
    for key, ts, c in (await session.execute(q)).all():
        t = ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
        by.setdefault(key, {})[t] = float(c)                    # the winner comes last per day
    return {k: [{"t": int(t.timestamp()), "c": round(c, 4)} for t, c in sorted(v.items())][-keep:] for k, v in by.items()}


def _change(series: List[Dict[str, Any]]) -> Optional[float]:
    if len(series) < 2 or not series[-2]["c"]:
        return None
    return round((series[-1]["c"] / series[-2]["c"] - 1.0) * 100.0, 2)


async def _flags(session: AsyncSession, user: User) -> None:
    for flag in ("screener_v2", FLAG):
        if not await flag_enabled(session, flag, user.tenant_id):
            raise HTTPException(status_code=404, detail="Not Found")


@router.post("/context")
async def board_context(body: ContextBody, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    await _flags(session, user)
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





# ----------------------------------------------------------------------------------------------------------- D3b: RRG
RRG_WEEKS = 10                    # the averaging window of both axes, in weeks
RRG_TAIL = 5                      # the weeks of tail drawn per symbol
RRG_LOOKBACK_DAYS = 7 * (2 * RRG_WEEKS + RRG_TAIL + 4)
QUADRANTS = {(True, True): "Leading", (True, False): "Weakening", (False, False): "Lagging", (False, True): "Improving"}


class RrgBody(BaseModel):
    symbols: List[str] = Field(min_length=1, max_length=MAX_SYMBOLS)
    exchange: str = Field(default="NSE", pattern=r"^(NSE|BSE)$")
    benchmark: str = Field(default="Nifty 50", min_length=1, max_length=60)


def weekly(series: List[Dict[str, Any]]) -> Dict[Tuple[int, int], float]:
    """The last daily close of each ISO week (IST trade date = the bar's UTC date for daily bars), oldest first."""
    out: Dict[Tuple[int, int], float] = {}
    for p in series:
        d = datetime.fromtimestamp(p["t"], tz=timezone.utc).date()
        iso = d.isocalendar()
        out[(iso[0], iso[1])] = p["c"]
    return out


def _sma(vals: List[float], i: int, n: int) -> Optional[float]:
    return None if i + 1 < n else sum(vals[i + 1 - n:i + 1]) / n


def rrg_points(sym: Dict[Tuple[int, int], float], bench: Dict[Tuple[int, int], float]) -> List[Dict[str, Any]]:
    """Weekly (week, rs_ratio, rs_momentum) on the weeks both have a close:
    RS = 100 x price / benchmark; RS-Ratio = 100 x RS / SMA(RS, 10 weeks); RS-Momentum = 100 x RS-Ratio / SMA(RS-Ratio, 10
    weeks). An open approximation of the relative-rotation idea, not the proprietary JdK formulas (OPEN_QUESTIONS D3-2)."""
    weeks = sorted(set(sym) & set(bench))
    rs = [100.0 * sym[w] / bench[w] for w in weeks if bench[w]]
    weeks = [w for w in weeks if bench[w]]
    ratio: List[Optional[float]] = [None if (m := _sma(rs, i, RRG_WEEKS)) is None or not m else 100.0 * rs[i] / m for i in range(len(rs))]
    known = [(i, r) for i, r in enumerate(ratio) if r is not None]
    rvals = [r for _i, r in known]
    out = []
    for k, (i, r) in enumerate(known):
        m = _sma(rvals, k, RRG_WEEKS)
        if m:
            out.append({"week": f"{weeks[i][0]}-W{weeks[i][1]:02d}", "rs_ratio": round(r, 3), "rs_momentum": round(100.0 * r / m, 3)})
    return out


@router.post("/rrg")
async def board_rrg(body: RrgBody, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """D3b: relative rotation of the scanned symbols against a benchmark index, weekly, from the shared lake's daily
    closes (shared-licensed sources only; nothing after now)."""
    await _flags(session, user)
    from app.universe.fo_eod import code_for
    symbols = _clean(body.symbols)
    now = datetime.now(timezone.utc)
    bench_key = f"NSE_INDEX:{code_for(body.benchmark)}"
    keys = {s: f"{body.exchange}:{s}" for s in symbols}
    keep = RRG_LOOKBACK_DAYS
    found = await closes(session, [bench_key, *keys.values()], now, lookback_days=RRG_LOOKBACK_DAYS, keep=keep)
    bench = weekly(found.get(bench_key, []))
    rows = []
    for s in symbols:
        pts = rrg_points(weekly(found.get(keys[s], [])), bench) if bench else []
        tail = pts[-RRG_TAIL:]
        last = tail[-1] if tail else None
        rows.append({"symbol": s, "tail": tail, "quadrant": QUADRANTS[(last["rs_ratio"] >= 100, last["rs_momentum"] >= 100)] if last else None})
    return {"as_of": now.isoformat(), "benchmark": body.benchmark, "benchmark_key": bench_key, "weeks": RRG_WEEKS, "symbols": rows,
            "reason": None if bench else f"no shared daily history for the benchmark {body.benchmark}",
            "note": ("Relative rotation: RS-Ratio and RS-Momentum are an open approximation over 10 weeks, from the shared daily "
                     "history - reference only, not signals.")}


# ---------------------------------------------------------------------------------------- D3b: the template sheet
class TemplateRef(BaseModel):
    id: str = Field(min_length=1, max_length=120)
    source: str = Field(min_length=1, max_length=4000)


class TemplateRunsBody(BaseModel):
    templates: List[TemplateRef] = Field(min_length=1, max_length=100)


def ast_hash(source: str) -> Optional[str]:
    """The hash a run stores for this screen text (`routes.run`: sha256 of the canonical AST JSON); None if it does not parse."""
    from app.screener import nodes
    from app.screener.parser import ScreenQLSyntaxError, parse
    try:
        ast = parse(source)
    except (ScreenQLSyntaxError, ValueError):
        return None
    return hashlib.sha256(json.dumps(nodes.to_json(ast), sort_keys=True).encode()).hexdigest()


@router.post("/template-runs")
async def template_runs(body: TemplateRunsBody, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """D3b: per template, the organisation's latest stored run of the same screen (its counts and when), and the first
    evidence pack of that run this user may read - the sheet's live thumbnail. With BT1 `byob_data_tier` on, only the
    user's own runs (a run read the runner's broker feed)."""
    await _flags(session, user)
    from app.market_data import live_access
    from app.screener import evidence_store
    own_only = await live_access.byob_on(session, user.tenant_id)
    out: Dict[str, Any] = {}
    for t in body.templates:
        h = ast_hash(t.source)
        if h is None:
            out[t.id] = None
            continue
        q = select(ScreenRunRecord).where(ScreenRunRecord.tenant_id == user.tenant_id, ScreenRunRecord.ast_sha256 == h)
        if own_only:
            q = q.where(ScreenRunRecord.user_id == user.id)
        run = await session.scalar(q.order_by(ScreenRunRecord.created_at.desc(), ScreenRunRecord.id.desc()).limit(1))
        if run is None:
            out[t.id] = None
            continue
        evidence = await evidence_store.for_run(session, user, run.id)
        out[t.id] = {"run_id": run.id, "at": run.created_at.isoformat() if run.created_at else None, "base_tf": run.base_tf,
                     "scanned": run.scanned, "matched": run.matched, "evidence_id": evidence[0].id if evidence else None}
    return {"runs": out}


__all__ = ["router", "FLAG", "closes", "board_context", "board_rrg", "rrg_points", "weekly", "template_runs", "ast_hash"]
