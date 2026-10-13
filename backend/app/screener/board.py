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
from app.db.models import CompanyRecord, MdCandleRecord, MdCorporateActionRecord, ScreenRunRecord, User
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
                 actions: Optional[Dict[str, List[MdCorporateActionRecord]]] = None, lookback_days: int = LOOKBACK_DAYS,
                 keep: int = CLOSES) -> Dict[str, List[Dict[str, Any]]]:
    """{key: [{t, c}, ...]} - the last `keep` daily closes per key, oldest first (one batched read). One bar per trading
    day (IST date): the preferred shared source, then its highest version. `actions` ({key: splits / bonuses}) adjusts
    each close by app/market_lake/adjust.share_factor for its day."""
    if not keys:
        return {}
    n = len(SHARED_SOURCES)
    rank = case(*[(MdCandleRecord.source.like(f"{p}%"), i) for i, p in enumerate(SHARED_SOURCES)], else_=n)
    q = (select(MdCandleRecord.instrument_key, MdCandleRecord.ts, rank, MdCandleRecord.version, cast(MdCandleRecord.close, Float))
         .where(MdCandleRecord.instrument_key.in_(keys), MdCandleRecord.timeframe.in_(DAILY_LABELS),
                MdCandleRecord.ts > now - timedelta(days=lookback_days), MdCandleRecord.ts <= now,
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
        out[key] = series[-keep:]
    return out


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





# ----------------------------------------------------------------------------------------------------------- D3b: RRG
RRG_WEEKS = 10                    # the averaging window of both axes, in weeks
RRG_TAIL = 5                      # the weeks of tail drawn per symbol
RRG_LOOKBACK_DAYS = 7 * (2 * RRG_WEEKS + RRG_TAIL + 4)
RRG_MAX_SYMBOLS = 50              # tails drawn on one plot (the heatmap context takes up to MAX_SYMBOLS)
WEEK_CLOSE_IST = (15, 30)         # a week is complete after Friday's close (or at the weekend)
QUADRANTS = {(True, True): "Leading", (True, False): "Weakening", (False, False): "Lagging", (False, True): "Improving"}


class RrgBody(BaseModel):
    symbols: List[str] = Field(min_length=1, max_length=RRG_MAX_SYMBOLS)
    exchange: str = Field(default="NSE", pattern=r"^(NSE|BSE)$")
    benchmark: str = Field(default="Nifty 50", min_length=1, max_length=60)


def weekly(series: List[Dict[str, Any]]) -> Dict[Tuple[int, int], float]:
    """The last daily close of each ISO week (by the bar's IST trade date, app/market_lake/ingest.trade_date), oldest first."""
    out: Dict[Tuple[int, int], float] = {}
    for p in series:
        iso = trade_date(datetime.fromtimestamp(p["t"], tz=timezone.utc)).isocalendar()
        out[(iso[0], iso[1])] = p["c"]
    return out


def forming_week(now: datetime) -> Optional[Tuple[int, int]]:
    """The ISO week still forming at `now` (IST): Monday to Friday before the close; None at the weekend / after Friday's close."""
    from app.market_data.calendar import IST
    t = now.astimezone(IST)
    if t.weekday() >= 5 or (t.weekday() == 4 and (t.hour, t.minute) >= WEEK_CLOSE_IST):
        return None
    iso = t.date().isocalendar()
    return (iso[0], iso[1])


def _sma(vals: List[float], i: int, n: int) -> Optional[float]:
    return None if i + 1 < n else sum(vals[i + 1 - n:i + 1]) / n


def rrg_points(sym: Dict[Tuple[int, int], float], bench: Dict[Tuple[int, int], float],
               forming: Optional[Tuple[int, int]] = None) -> List[Dict[str, Any]]:
    """Weekly (week, rs_ratio, rs_momentum, forming) on the weeks both have a close (`forming`: that week has not closed,
    its point is the week to date and moves until Friday's close):
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
            out.append({"week": f"{weeks[i][0]}-W{weeks[i][1]:02d}", "rs_ratio": round(r, 3), "rs_momentum": round(100.0 * r / m, 3),
                        "forming": weeks[i] == forming})
    return out


@router.post("/rrg")
async def board_rrg(body: RrgBody, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """D3b: relative rotation of the scanned symbols against a benchmark index, weekly, from the shared lake's daily
    closes (shared-licensed sources only; nothing after now). The symbols' closes are adjusted for the splits / bonuses
    known now (app/market_lake/adjust.share_factor), so a split is not a collapse in relative strength; the benchmark is
    an index (no share actions). The last point of a week that has not closed is marked `forming`."""
    await _flags(session, user)
    from app.universe.fo_eod import code_for
    symbols = _clean(body.symbols)
    now = datetime.now(timezone.utc)
    bench_key = f"NSE_INDEX:{code_for(body.benchmark)}"
    keys = {s: f"{body.exchange}:{s}" for s in symbols}
    keep = RRG_LOOKBACK_DAYS
    acts = await share_actions(session, symbols, body.exchange, now, now - timedelta(days=RRG_LOOKBACK_DAYS))
    found = await closes(session, [bench_key, *keys.values()], now, {keys[s]: a for s, a in acts.items() if s in keys},
                         lookback_days=RRG_LOOKBACK_DAYS, keep=keep)
    bench = weekly(found.get(bench_key, []))
    forming = forming_week(now)
    rows = []
    for s in symbols:
        pts = rrg_points(weekly(found.get(keys[s], [])), bench, forming) if bench else []
        tail = pts[-RRG_TAIL:]
        last = tail[-1] if tail else None
        rows.append({"symbol": s, "tail": tail, "quadrant": QUADRANTS[(last["rs_ratio"] >= 100, last["rs_momentum"] >= 100)] if last else None})
    return {"as_of": now.isoformat(), "benchmark": body.benchmark, "benchmark_key": bench_key, "weeks": RRG_WEEKS, "symbols": rows,
            "reason": None if bench else f"no shared daily history for the benchmark {body.benchmark}",
            "forming_week": f"{forming[0]}-W{forming[1]:02d}" if forming else None,
            "note": ("Relative rotation: RS-Ratio and RS-Momentum are an open approximation over 10 weeks, from the shared daily "
                     "history adjusted for splits and bonuses - reference only, not signals.")}


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
    hashes = {t.id: ast_hash(t.source) for t in body.templates}
    wanted = sorted({h for h in hashes.values() if h is not None})
    latest: Dict[str, ScreenRunRecord] = {}
    if wanted:                                         # one read: the latest run per hash (a window over the org's runs)
        cond = [ScreenRunRecord.tenant_id == user.tenant_id, ScreenRunRecord.ast_sha256.in_(wanted)]
        if own_only:
            cond.append(ScreenRunRecord.user_id == user.id)
        rn = func.row_number().over(partition_by=ScreenRunRecord.ast_sha256,
                                    order_by=(ScreenRunRecord.created_at.desc(), ScreenRunRecord.id.desc())).label("rn")
        sub = select(ScreenRunRecord.id.label("id"), rn).where(*cond).subquery()
        runs = await session.scalars(select(ScreenRunRecord).join(sub, sub.c.id == ScreenRunRecord.id).where(sub.c.rn == 1))
        latest = {r.ast_sha256: r for r in runs}
    evidence = await evidence_store.first_for_runs(session, user, [r.id for r in latest.values()])
    out: Dict[str, Any] = {}
    for t in body.templates:
        h = hashes[t.id]
        run = latest.get(h) if h is not None else None
        if run is None:
            out[t.id] = None
            continue
        ev = evidence.get(run.id)
        out[t.id] = {"run_id": run.id, "at": run.created_at.isoformat() if run.created_at else None, "base_tf": run.base_tf,
                     "scanned": run.scanned, "matched": run.matched, "evidence_id": ev.id if ev else None}
    return {"runs": out}


__all__ = ["router", "FLAG", "closes", "share_actions", "board_context", "board_rrg", "rrg_points", "weekly", "forming_week", "template_runs", "ast_hash"]
