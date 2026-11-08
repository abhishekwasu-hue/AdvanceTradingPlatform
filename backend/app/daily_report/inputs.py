"""M88-R S2: the report's input - END-OF-DAY DATA ONLY, from the shared store every user reads (M86):

* Daily bars: the licensed NSE daily files (`eod_routes._daily`: `nse_bhav:*`, adjusted for splits / bonuses) - never a
  live tick, an intraday bar or a row a broker session fetched. Weekly bars are built from those Daily bars.
* Per symbol: OHLC, % change, volume against its own recent average, delivery % (only where a stored file carries it),
  the position against the recent high / low and the last confirmed swing high / low, structure on the Daily and
  Weekly chart (ATP's own reading, `price_action_layer.compute_layer` + `read_chart.structure_part`), the stored areas
  (the market-zones snapshot as of that close, platform scope) and the F&O open interest change and PCR from the stored
  F&O bhavcopy where it holds them.
* Breadth (advances / declines, share at a new recent high / low) and sectors (NSE's industry from the index's own list).

Pure reads; nothing is written, nothing is fetched from a broker. "Recent" is the admin's `recent_bars` setting.
"""
from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.daily_report.universe import Item
from app.db.models import MdCandleRecord
from app.instruments.master import INDEX_SYMBOLS
from app.market_data.calendar import IST
from app.market_lake.bhavcopy_backfill import bar_end

HISTORY_DAYS = 730                   # Daily history read per symbol (about two years: the Weekly structure needs it)
FO_SOURCE = "nse_bhav:fo"
FO_NAME = {name.upper(): sym for sym, name in INDEX_SYMBOLS.items()}       # "NIFTY 50" -> "NIFTY"
AREA_WORDS = {"RESISTANCE_SUPPLY": "supply", "SUPPORT_DEMAND": "demand"}


def _r(x: float | None, n: int = 2) -> float | None:
    return None if x is None else round(float(x), n)


def pct(now: float | None, then: float | None) -> float | None:
    if now is None or then in (None, 0):
        return None
    return round((float(now) - float(then)) / float(then) * 100.0, 2)


def _day(ts: datetime) -> date:
    ts = ts if ts.tzinfo else ts.replace(tzinfo=UTC)
    return ts.astimezone(IST).date()


def layer_frame(rows: Sequence[Any]) -> pd.DataFrame:
    """Stored Daily rows -> the reader's frame (timestamp = bar start, bar_end = the 15:30 close)."""
    if not rows:
        return pd.DataFrame(columns=["timestamp", "bar_end", "open", "high", "low", "close", "volume"])
    ends = [r.ts if r.ts.tzinfo else r.ts.replace(tzinfo=UTC) for r in rows]
    return pd.DataFrame({"timestamp": pd.to_datetime([e - timedelta(hours=6, minutes=15) for e in ends], utc=True),
                         "bar_end": pd.to_datetime(ends, utc=True),
                         "open": [float(r.open) for r in rows], "high": [float(r.high) for r in rows],
                         "low": [float(r.low) for r in rows], "close": [float(r.close) for r in rows],
                         "volume": [float(r.volume or 0) for r in rows]})


def weekly_frame(daily: pd.DataFrame) -> pd.DataFrame:
    """Daily -> Weekly by the India calendar week; the newest week ends at the newest Daily close (nothing filled)."""
    if not len(daily):
        return daily
    local = daily["bar_end"].dt.tz_convert("Asia/Kolkata")
    iso = local.dt.isocalendar()
    groups = (iso["year"].astype(int) * 100 + iso["week"].astype(int)).to_numpy()
    out = daily.groupby(groups, sort=True).agg(timestamp=("timestamp", "first"), bar_end=("bar_end", "last"), open=("open", "first"),
                                               high=("high", "max"), low=("low", "min"), close=("close", "last"),
                                               volume=("volume", "sum"))
    return out.reset_index(drop=True)


def _structure(frame: pd.DataFrame, tf: str) -> dict[str, Any]:
    from app.charts import price_action_layer as pal
    from app.charts.read_chart import structure_part
    if len(frame) < 3:
        return {"direction": "none", "words": "Not enough stored bars to read the structure yet.", "swing_high": None, "swing_low": None}
    try:
        layer = pal.compute_layer(frame, tf)
    except (ValueError, KeyError, IndexError):
        return {"direction": "none", "words": "The structure could not be read from the stored bars.", "swing_high": None, "swing_low": None}
    part = structure_part(layer, tf)
    swings = [s for s in layer.get("swings") or [] if s.get("status") == "confirmed"]
    high = next((s["price"] for s in reversed(swings) if s["kind"] == "H"), None)
    low = next((s["price"] for s in reversed(swings) if s["kind"] == "L"), None)
    last = part.get("last_event") or {}
    return {"direction": part["direction"], "words": part["words"], "last_event": last.get("words"),
            "swing_high": _r(high), "swing_low": _r(low)}


def read_structure(daily: pd.DataFrame) -> dict[str, Any]:
    """CPU work (the reader on two timeframes) - run in a worker thread."""
    return {"d": _structure(daily, "1d"), "w": _structure(weekly_frame(daily), "1w")}


async def _areas(session: AsyncSession, symbol: str, at: datetime, close: float) -> list[dict[str, Any]]:
    """The stored live areas (platform snapshot as of that close) nearest above and below the close."""
    from app.ai.tools.copilot import lake_key
    from app.price_action.zones import store
    from app.price_action.zones.chart import is_live
    try:
        read = await store.zones_as_of(session, lake_key(symbol), at, None)
    except ValueError:
        return []
    rows = [r for r in read.rows if is_live(r) and r.get("zone_low") is not None and r.get("zone_high") is not None]
    above = sorted((r for r in rows if float(r["zone_low"]) > close), key=lambda r: float(r["zone_low"]))[:1]
    below = sorted((r for r in rows if float(r["zone_high"]) < close), key=lambda r: -float(r["zone_high"]))[:1]
    inside = [r for r in rows if float(r["zone_low"]) <= close <= float(r["zone_high"])][:1]
    out = []
    for where, picked in (("inside", inside), ("above", above), ("below", below)):
        for r in picked:
            out.append({"where": where, "kind": AREA_WORDS.get(str(r.get("role") or ""), "area"), "low": _r(r["zone_low"]),
                        "high": _r(r["zone_high"]), "timeframe": str(r.get("timeframe") or ""), "state": str(r.get("state") or "")})
    return out


async def _oi_on(session: AsyncSession, prefix: str, ts: datetime) -> dict[str, int]:
    rows = (await session.execute(select(MdCandleRecord.instrument_key, MdCandleRecord.oi, MdCandleRecord.version).where(
        MdCandleRecord.timeframe == "day", MdCandleRecord.source == FO_SOURCE, MdCandleRecord.ts == ts,
        MdCandleRecord.instrument_key.startswith(prefix)))).all()
    best: dict[str, tuple[int, int]] = {}
    for key, oi, version in rows:
        if oi is not None and (key not in best or version > best[key][1]):
            best[key] = (int(oi), int(version))
    return {k: v[0] for k, v in best.items()}


async def fo_figures(session: AsyncSession, item: Item, day: date) -> dict[str, Any]:
    """Futures OI (all expiries) and its change against the previous stored F&O day; PCR of the nearest expiry's
    options by OI. None where the stored F&O files do not hold them."""
    fo = FO_NAME.get(item.symbol.upper(), item.symbol.replace(" ", "")) if item.kind == "INDEX" else item.symbol
    out: dict[str, Any] = {"fut_oi": None, "fut_oi_change_pct": None, "pcr": None}
    ts = bar_end(day)
    fut = f"NFO:{fo}:FUT:"
    now = await _oi_on(session, fut, ts)
    if now:
        out["fut_oi"] = sum(now.values())
        prev_ts = await session.scalar(select(func.max(MdCandleRecord.ts)).where(
            MdCandleRecord.timeframe == "day", MdCandleRecord.source == FO_SOURCE, MdCandleRecord.ts < ts,
            MdCandleRecord.instrument_key.startswith(fut)))
        if prev_ts is not None:
            before = await _oi_on(session, fut, prev_ts)
            if before and sum(before.values()):
                out["fut_oi_change_pct"] = pct(out["fut_oi"], sum(before.values()))
    opts = {**await _oi_on(session, f"NFO:{fo}:CE:", ts), **await _oi_on(session, f"NFO:{fo}:PE:", ts)}
    if opts:
        nearest = min(k.split(":")[3] for k in opts)
        calls = sum(v for k, v in opts.items() if k.split(":")[2] == "CE" and k.split(":")[3] == nearest)
        puts = sum(v for k, v in opts.items() if k.split(":")[2] == "PE" and k.split(":")[3] == nearest)
        out["pcr"] = round(puts / calls, 2) if calls else None
    return out


async def symbol_row(session: AsyncSession, item: Item, day: date, recent_bars: int) -> dict[str, Any]:
    """One symbol's EOD figures on `day` (the close of that bhavcopy day); `missing` when the store has no close then."""
    from app.market_data import eod_routes
    end = bar_end(day)
    rows = await eod_routes._daily(session, item.symbol, end - timedelta(days=HISTORY_DAYS), end)  # noqa: SLF001 - the licensed EOD read
    base: dict[str, Any] = {"symbol": item.symbol, "name": item.name or item.symbol, "kind": item.kind, "sector": item.sector,
                            "missing": True}
    if not rows or _day(rows[-1].ts) != day:
        return base
    last = rows[-1]
    prior = rows[:-1][-recent_bars:]
    vol_avg = sum(float(r.volume or 0) for r in prior) / len(prior) if prior else None
    recent_high = max((float(r.high) for r in prior), default=None)
    recent_low = min((float(r.low) for r in prior), default=None)
    window = rows[-(recent_bars + 1):]
    w_hi, w_lo = max(float(r.high) for r in window), min(float(r.low) for r in window)
    close = float(last.close)
    frame = layer_frame(rows)
    structure = await asyncio.to_thread(read_structure, frame)
    prev = float(rows[-2].close) if len(rows) > 1 else None
    base.update(
        missing=False, open=_r(last.open), high=_r(last.high), low=_r(last.low), close=_r(close), prev_close=_r(prev),
        change_pct=pct(close, prev), volume=int(last.volume or 0) if item.kind == "EQ" else None,
        volume_ratio=_r(float(last.volume or 0) / vol_avg, 2) if item.kind == "EQ" and vol_avg else None,
        delivery_pct=None,                                    # no stored daily file carries delivery yet (M88R-Q1)
        recent_high=_r(recent_high), recent_low=_r(recent_low),
        new_recent_high=recent_high is not None and close > recent_high, new_recent_low=recent_low is not None and close < recent_low,
        range_position_pct=round((close - w_lo) / (w_hi - w_lo) * 100.0, 1) if w_hi > w_lo else None,
        structure_d=structure["d"], structure_w=structure["w"],
        swing_high_pct=pct(close, structure["d"]["swing_high"]), swing_low_pct=pct(close, structure["d"]["swing_low"]),
        areas=await _areas(session, item.symbol, end, close),
        **await fo_figures(session, item, day))
    return base


def breadth(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    from app.markets.service import breadth as count
    stocks = [r for r in rows if r["kind"] == "EQ" and not r["missing"]]
    counts = count(r.get("change_pct") for r in stocks)
    n = len(stocks)
    highs = sum(1 for r in stocks if r.get("new_recent_high"))
    lows = sum(1 for r in stocks if r.get("new_recent_low"))
    return {**counts, "stocks": n, "new_recent_high": highs, "new_recent_low": lows,
            "new_recent_high_pct": round(highs / n * 100.0, 1) if n else None,
            "new_recent_low_pct": round(lows / n * 100.0, 1) if n else None}


def sectors(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    from app.markets.service import average
    groups: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        if r["kind"] == "EQ" and not r["missing"] and r.get("sector"):
            groups.setdefault(str(r["sector"]), []).append(r)
    out = [{"sector": name, "stocks": len(m), "avg_change_pct": _r(average(x.get("change_pct") for x in m)),
            "advances": sum(1 for x in m if (x.get("change_pct") or 0) > 0), "declines": sum(1 for x in m if (x.get("change_pct") or 0) < 0)}
           for name, m in groups.items()]
    return sorted(out, key=lambda s: (-(s["avg_change_pct"] if s["avg_change_pct"] is not None else -1e9), s["sector"]))
