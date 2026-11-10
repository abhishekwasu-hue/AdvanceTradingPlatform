"""M95 PR-5: build and store the guest snapshot - the operator's command, run once on the server:

    python -m app.guest_demo.build --as-of 2026-07-31          (deploy/atp.sh guest-demo --as-of 2026-07-31)

`--as-of` must be at least MIN_AGE_DAYS before today (refused otherwise, nothing written). The day used is the last
stored NSE close on or before it. Reads ONLY the shared end-of-day lake (`md_candles`, licensed `nse_bhav:*` rows, never
a broker session's row - `public_rows`) for NIFTY 50 and BANK NIFTY:

* Daily bars up to and including that close (no later bar is read: no look-ahead);
* the areas from the platform's own zone code (`price_action.zones`: the platform's Daily settings, `build_snapshot`
  and the chart's band choice `bands_from_rows`) on those bars;
* the structure words of the daily report's reader (`daily_report.inputs.read_structure`) and its futures OI change;
* the F&O bhavcopy option rows of that day, when stored (open interest per strike, the nearest expiry);
* the ready-made starter screens through the production screener (`screener.runtime.run_screen`) on those bars.

Then every price becomes a distance (`snapshot.py`) and the snapshot is checked: a number in an index's price range or a
buy / sell / target / stop word anywhere refuses it. Stored in one `platform_controls` row (KEY, no migration);
`--dry-run` prints the summary without storing. Exit codes: 0 stored, 2 refused (date too recent, no data, check failed).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Any, TextIO

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import MdCandleRecord
from app.guest_demo import INDICES, KEY, MIN_AGE_DAYS, VERSION
from app.guest_demo import snapshot as snap
from app.market_data.calendar import IST

BHAV_PREFIX = "nse_bhav:"
FO_SOURCE = "nse_bhav:fo"
TIMEFRAME = "day"


class Refused(ValueError):
    """The snapshot was not built (the reason is the message); nothing was stored."""


def check_age(as_of: date, today: date) -> None:
    """M95-Q1: a public chart is at least MIN_AGE_DAYS old."""
    if as_of > today - timedelta(days=MIN_AGE_DAYS):
        raise Refused(f"--as-of {as_of.isoformat()} is too recent: a public market day must be at least {MIN_AGE_DAYS} days "
                      f"old (on or before {(today - timedelta(days=MIN_AGE_DAYS)).isoformat()}).")


def _day(ts: datetime) -> date:
    return (ts if ts.tzinfo else ts.replace(tzinfo=UTC)).astimezone(IST).date()


async def _sources(session: AsyncSession, key: str) -> list[str]:
    from app.backtest import bar_source
    return [s for s in await bar_source.lake_sources(session, key) if s.startswith(BHAV_PREFIX)]


async def trading_day(session: AsyncSession, as_of: date, lake_name: str) -> date:
    """The last stored NSE close of `lake_name` on or before `as_of`."""
    from app.ai.tools.copilot import lake_key
    from app.market_lake.bhavcopy_backfill import bar_end
    from app.market_lake.ingest import public_rows
    key = lake_key(lake_name)
    sources = await _sources(session, key)
    if not sources:
        raise Refused(f"no stored NSE daily bars for {INDICES[lake_name]} - load the index history first.")
    newest = await session.scalar(select(func.max(MdCandleRecord.ts)).where(
        MdCandleRecord.instrument_key == key, MdCandleRecord.timeframe == TIMEFRAME, MdCandleRecord.source.in_(sources),
        MdCandleRecord.ts <= bar_end(as_of), public_rows(MdCandleRecord.source)))
    if newest is None:
        raise Refused(f"no stored NSE close of {INDICES[lake_name]} on or before {as_of.isoformat()}.")
    return _day(newest)


async def daily_rows(session: AsyncSession, lake_name: str, day: date, days: int) -> list[Any]:
    """The licensed Daily bars of one index over `days` days up to and including `day`'s close (never a later bar)."""
    from app.ai.tools.copilot import lake_key
    from app.market_lake import adjust
    from app.market_lake.bhavcopy_backfill import bar_end
    key = lake_key(lake_name)
    sources = await _sources(session, key)
    if not sources:
        return []
    end = bar_end(day)
    return await adjust.candles(session, key, TIMEFRAME, end - timedelta(days=days), end, symbol=key.split(":", 1)[-1],
                                exchange="NSE", kind="INDEX", adjusted=True, sources=sources)


async def option_rows(session: AsyncSession, lake_name: str, day: date) -> tuple[str | None, dict[float, dict[str, int | None]]]:
    """(nearest expiry, {strike: {"call": oi, "put": oi}}) of the F&O bhavcopy of `day`; (None, {}) when not stored."""
    from app.daily_report.inputs import FO_NAME
    from app.market_lake.bhavcopy_backfill import bar_end
    from app.market_lake.ingest import public_rows
    fo = FO_NAME.get(lake_name.upper(), lake_name.replace(" ", ""))
    calls, puts = f"NFO:{fo}:CE:", f"NFO:{fo}:PE:"
    found = (await session.execute(select(MdCandleRecord.instrument_key, MdCandleRecord.oi, MdCandleRecord.version).where(
        MdCandleRecord.timeframe == TIMEFRAME, MdCandleRecord.source == FO_SOURCE, MdCandleRecord.ts == bar_end(day),
        MdCandleRecord.instrument_key.startswith(calls) | MdCandleRecord.instrument_key.startswith(puts),
        public_rows(MdCandleRecord.source)))).all()
    latest: dict[str, tuple[int | None, int]] = {}
    for key, oi, version in found:
        if key not in latest or version > latest[key][1]:
            latest[key] = (oi, version)
    by_expiry: dict[str, dict[float, dict[str, int | None]]] = {}
    for key, (oi, _v) in latest.items():
        parts = key.split(":")                                    # NFO:<UL>:<CE|PE>:<expiry>:<strike>
        if len(parts) != 5 or parts[3] < day.isoformat():
            continue
        try:
            strike = float(parts[4])
        except ValueError:
            continue
        cell = by_expiry.setdefault(parts[3], {}).setdefault(strike, {"call": None, "put": None})
        cell["call" if parts[2] == "CE" else "put"] = None if oi is None else int(oi)
    if not by_expiry:
        return None, {}
    nearest = min(by_expiry)
    return nearest, by_expiry[nearest]


def zone_bands(rows: Sequence[Any], close: float) -> list[dict[str, Any]]:
    """The areas of the platform's zone code on these Daily bars (as of the last one) and the chart's band choice (at
    most six, nearest the close)."""
    from app.price_action.zones.chart import bands_from_rows
    from app.price_action.zones.job import bars_to_frame, exchange_session
    from app.price_action.zones.snapshot import build_snapshot
    from app.price_action.zones.store import platform_settings
    s = platform_settings()
    raw = pd.DataFrame({"ts": [r.ts for r in rows], "open": [r.open for r in rows], "high": [r.high for r in rows],
                        "low": [r.low for r in rows], "close": [r.close for r in rows], "volume": [r.volume for r in rows]})
    frame = bars_to_frame(raw, "1d", exchange_session("NSE_INDEX:NIFTY50"))
    recent = int(s.recent_days_by_tf.get("1d", 20))
    newest = pd.Timestamp(frame["close_at"].iloc[-1])
    dyn = {tf: frame[frame["close_at"] > newest - pd.Timedelta(days=recent)].reset_index(drop=True) for tf in s.dynamic_sr_timeframes
           if tf == "1d"}
    frames = {tf: frame for tf in {s.sr_timeframe, s.zone_timeframe, s.gap_timeframe} if tf == "1d"}
    found = build_snapshot(frames, dyn, s, previous=None)
    return bands_from_rows(found, close, family="zones")    # the areas (bases, order blocks, gaps), not the S/R lines


def screen_runs(frames: dict[str, pd.DataFrame]) -> list[dict[str, Any]]:
    """The starter screens (the screener's ready-made library entries) through the production runtime, per index."""
    from app.screener import compile_screen
    from app.screener import taxonomy as tx
    from app.screener.library import STARTERS
    from app.screener.runtime import SymbolData, run_screen
    names = {s.id: s for s in tx.load().screens}
    out: list[dict[str, Any]] = []
    for screen_id, (text, base_tf) in STARTERS.items():
        ast, validated = compile_screen(text, base_tf=base_tf)
        if ast is None or not validated.ok:
            continue
        universe = [SymbolData(name, {base_tf: frame}, exchange="NSE") for name, frame in frames.items()]
        matches = run_screen(ast, validated, universe, base_tf=base_tf)
        meta = names.get(screen_id)
        out.append({"name": meta.name if meta else screen_id, "definition": meta.definition if meta else text, "timeframe": base_tf,
                    "results": [{"name": m.symbol, "matched": bool(m.matched), "note": m.reason or m.note} for m in matches]})
    return out


async def index_part(session: AsyncSession, lake_name: str, day: date) -> tuple[dict[str, Any], pd.DataFrame, list[tuple[float, float]]]:
    """One index's Today facts, chart and chain (prices already turned into distances), its screener frame and the
    (low, high) spans whose numbers must not appear in the snapshot."""
    from app.backtest import bar_source
    from app.daily_report import inputs
    from app.daily_report.settings import DEFAULTS
    from app.daily_report.universe import Item
    shown = INDICES[lake_name]
    rows = await daily_rows(session, lake_name, day, inputs.HISTORY_DAYS)
    if not rows or _day(rows[-1].ts) != day:
        raise Refused(f"{shown} has no stored NSE close on {day.isoformat()}.")
    close = float(rows[-1].close)
    recent = int(DEFAULTS["recent_bars"])
    window = rows[-(recent + 1):]
    w_hi, w_lo = max(float(r.high) for r in window), min(float(r.low) for r in window)
    structure = await asyncio.to_thread(inputs.read_structure, inputs.layer_frame(rows))
    prev = float(rows[-2].close) if len(rows) > 1 else None
    swing_hi, swing_lo = structure["d"].get("swing_high"), structure["d"].get("swing_low")
    facts = {"change_pct": inputs.pct(close, prev),
             "range_position_pct": round((close - w_lo) / (w_hi - w_lo) * 100.0, 1) if w_hi > w_lo else None,
             "structure_daily": structure["d"]["words"], "structure_weekly": structure["w"]["words"],
             "direction_daily": structure["d"]["direction"], "direction_weekly": structure["w"]["direction"],
             "swing_high_pct": None if swing_hi is None else snap.pct_from(swing_hi, close),
             "swing_low_pct": None if swing_lo is None else snap.pct_from(swing_lo, close)}
    bands = await asyncio.to_thread(zone_bands, rows, close)
    areas = [snap.area_of(b, close) for b in bands]
    shown_rows = rows[-snap.CHART_BARS:]
    chart = {"timeframe": "1d", "first_date": _day(shown_rows[0].ts).isoformat(), "last_date": day.isoformat(),
             "bars": snap.chart_bars([(r.open, r.high, r.low, r.close) for r in shown_rows], close), "areas": areas}
    expiry, strikes = await option_rows(session, lake_name, day)
    view = snap.chain_view(strikes, close) if strikes else None
    chain = None if view is None else {"expiry": expiry, **view}
    fo = await inputs.fo_figures(session, Item(symbol=lake_name, kind="INDEX", name=shown), day)
    spans = [(min(float(r.low) for r in rows), max(float(r.high) for r in rows))]
    if strikes:
        spans.append((min(strikes), max(strikes)))
    part = {"name": shown, "today": {**facts, "lines": snap.today_lines(shown, facts)}, "chart": chart, "chain": chain,
            "report": snap.report_lines(shown, facts, areas, chain, fo.get("fut_oi_change_pct"))}
    return part, bar_source.to_frame(rows), spans


async def build(session: AsyncSession, as_of: date, *, today: date | None = None) -> dict[str, Any]:
    """The snapshot for the last stored close on or before `as_of` (refused when too recent, missing or not price-free)."""
    check_age(as_of, today or datetime.now(IST).date())
    first = next(iter(INDICES))
    day = await trading_day(session, as_of, first)
    parts: list[dict[str, Any]] = []
    frames: dict[str, pd.DataFrame] = {}
    spans: list[tuple[float, float]] = []
    for lake_name in INDICES:
        part, frame, found = await index_part(session, lake_name, day)
        parts.append(part)
        frames[part["name"]] = frame
        spans += found
    screens = await asyncio.to_thread(screen_runs, frames)
    payload: dict[str, Any] = {"version": VERSION, "as_of": day.isoformat(), "indices": parts, "screens": screens,
                               "built_at": datetime.now(IST).date().isoformat()}
    figures = snap.price_figures(payload, snap.price_ranges(spans))
    if figures:
        raise Refused(f"the snapshot still holds {len(figures)} number(s) in an index's price range - not stored.")
    words = snap.advice_words(payload)
    if words:
        raise Refused(f"the snapshot's text holds advice words ({', '.join(words)}) - not stored.")
    return payload


async def store(session: AsyncSession, payload: dict[str, Any]) -> None:
    from app.platform.controls import _set
    await _set(session, KEY, payload, None)
    await session.commit()


async def stored(session: AsyncSession) -> dict[str, Any] | None:
    """The stored snapshot, or None (nothing stored, or a row of another version)."""
    from app.platform.controls import _get
    value = await _get(session, KEY)
    return value if value.get("version") == VERSION and value.get("indices") else None


def summary(payload: dict[str, Any]) -> list[str]:
    lines = [f"as of {payload['as_of']}"]
    for part in payload["indices"]:
        chain = part["chain"]
        lines.append(f"{part['name']}: {len(part['chart']['bars'])} bars, {len(part['chart']['areas'])} areas, "
                     + (f"chain {len(chain['rows'])} strikes (expiry {chain['expiry']})" if chain else "no stored option rows"))
    lines += [f"screen {s['name']}: " + ", ".join(f"{r['name']} {'matched' if r['matched'] else 'no match'}" for r in s["results"])
              for s in payload["screens"]]
    return lines


async def main(argv: Sequence[str] | None = None, out: TextIO = sys.stdout, session_factory: Any = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.guest_demo.build", description=__doc__.splitlines()[0])
    parser.add_argument("--as-of", required=True, type=date.fromisoformat, help="a market day at least 90 days old (YYYY-MM-DD)")
    parser.add_argument("--dry-run", action="store_true", help="build and print the summary; store nothing")
    parser.add_argument("--json", action="store_true", help="print the whole snapshot")
    args = parser.parse_args(argv)
    if session_factory is None:
        from app.db.session import _session_factory
        session_factory = _session_factory
    async with session_factory() as session:
        try:
            payload = await build(session, args.as_of)
        except Refused as exc:
            print(f"REFUSED: {exc}", file=out)
            return 2
        if not args.dry_run:
            await store(session, payload)
    for line in summary(payload):
        print(line, file=out)
    if args.json:
        print(json.dumps(payload, indent=1), file=out)
    print("dry run: nothing stored" if args.dry_run else "stored: the guest view serves this day while the guest_demo flag is on",
          file=out)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
