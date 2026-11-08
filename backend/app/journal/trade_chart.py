"""M88: an outside-ATP trade (imported or typed in) drawn on its own chart - the candles around it with the entry and the
exit marked at their own times and prices.

* Candles: the licensed end-of-day exchange files (the same Daily bars every user sees, `eod_routes.daily_bars`; never
  a bar a broker session fetched, so never another user's data - M86). Intraday candles are not stored for an outside
  trade, so the chart is Daily and says so; each mark sits on the candle of its day.
* Marks: his own fill prices and times, as recorded. His own stop is drawn only when he gave one. Nothing is computed,
  suggested or filled in; there are no buy / sell or target words - "Entry" and "Exit" with their prices.
* `chart()` answers the candles and marks for the in-place chart; `png()` draws the same with the study renderer
  (`app/study/chart.render_chart`, the M77 "Save as image" pixels) for "Save image".
Personal (the caller's own entry, checked by the route) and read-only: nothing here places, changes or blocks an order.
"""
from __future__ import annotations

import asyncio
import io
import math
from datetime import UTC, datetime, timedelta
from typing import Any

from PIL import Image, ImageDraw
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import JournalManualTradeRecord

DAYS_BEFORE = 90            # calendar days of candles shown before the entry
DAYS_AFTER = 15             # and after the exit (or the entry, while the trade is open)
TIMEFRAME = "day"
LABEL = "Daily candles"
NOTE = ("Daily candles from the end-of-day exchange files - intraday candles are not stored for this trade, so each mark "
        "sits on the candle of its day.")
PNG_W, PNG_H, PAD, HEAD = 1600, 900, 32, 26
LABEL_PX = 120             # the width a mark's label takes on the image


def _utc(t: datetime | None) -> datetime | None:
    if t is None:
        return None
    return t.replace(tzinfo=UTC) if t.tzinfo is None else t.astimezone(UTC)


def _bar_at(candles: list[dict[str, Any]], when: datetime) -> str | None:
    """The timestamp of the last candle that started at or before `when` (the candle the fill happened in)."""
    hit = None
    for c in candles:
        start = datetime.fromisoformat(str(c["timestamp"]))
        if _utc(start) <= when:      # type: ignore[operator]
            hit = c["timestamp"]
        else:
            break
    return hit


async def chart(session: AsyncSession, row: JournalManualTradeRecord, *, now: datetime | None = None) -> dict[str, Any]:
    from app.backtest import bar_source
    from app.market_data.eod_routes import daily_bars
    now = now or datetime.now(UTC)
    entry_time, exit_time = _utc(row.entry_time), _utc(row.exit_time)
    assert entry_time is not None
    start = entry_time - timedelta(days=DAYS_BEFORE)
    end = min(now, (exit_time or entry_time) + timedelta(days=DAYS_AFTER))
    rows = await daily_bars(session, row.symbol, start, end)
    frame = bar_source.to_frame(rows)
    candles = bar_source.frame_bars(frame) if len(frame) else []
    side_in = "BUY" if row.direction == "LONG" else "SELL"
    marks: list[dict[str, Any]] = [{"kind": "entry", "label": "Entry", "at": entry_time.isoformat(), "price": float(row.entry_price),
                                    "side": side_in, "bar": _bar_at(candles, entry_time)}]
    if exit_time is not None and row.exit_price is not None:
        marks.append({"kind": "exit", "label": "Exit", "at": exit_time.isoformat(), "price": float(row.exit_price),
                      "side": "SELL" if side_in == "BUY" else "BUY", "bar": _bar_at(candles, exit_time)})
    levels = [{"kind": "stop", "label": "Your stop", "price": float(row.stop_loss)}] if row.stop_loss else []
    return {"symbol": row.symbol, "exchange": row.exchange or "NSE", "direction": row.direction, "timeframe": TIMEFRAME,
            "timeframe_label": LABEL, "note": NOTE, "candles": candles, "marks": marks, "levels": levels,
            "missing": not candles,
            "missing_text": None if candles else f"No daily candles are stored for {row.symbol} around this trade, so it cannot be drawn.",
            "image": f"/api/journal/manual/{row.id}/trade-chart.png"}


def _hex(rgb: tuple[int, int, int]) -> str:
    return "#{:02x}{:02x}{:02x}".format(*rgb)


def render(data: dict[str, Any], *, cvd: bool = False) -> bytes:
    """The trade's chart as a PNG (pure): a header line, the candles and his marks, the note at the foot."""
    from app.study import fonts
    from app.study import pages as P
    from app.study.chart import render_chart
    from app.study.palette import palette
    pal = palette(cvd=cvd)
    bars = []
    for c in data["candles"]:
        t = datetime.fromisoformat(str(c["timestamp"])).timestamp()
        bars.append([t, c["open"], c["high"], c["low"], c["close"], c.get("volume") or 0])
    marks = {m["kind"]: m for m in data["marks"] if m.get("bar")}
    drawings: list[dict[str, Any]] = []
    entry, exit_ = marks.get("entry"), marks.get("exit")
    if entry and exit_:
        drawings.append({"kind": "trendline", "anchors": [{"t": entry["bar"], "p": entry["price"]}, {"t": exit_["bar"], "p": exit_["price"]}],
                         "style": {"color": _hex(pal.text_muted), "width": 1, "line": "dashed"}})
    for lv in data.get("levels") or []:
        drawings.append({"kind": "hline", "anchors": [{"p": lv["price"]}], "style": {"color": _hex(pal.warn), "width": 1, "line": "dotted"}})
    stamps = [str(c["timestamp"]) for c in data["candles"]]
    slot = PNG_W * 0.85 / max(1, len(stamps))                       # about one candle's width in pixels
    room = max(1, math.ceil(LABEL_PX / slot))                        # candles a label needs to its right
    for m in (entry, exit_):
        if m:
            colour = pal.buyer if m["side"] == "BUY" else pal.seller
            i = stamps.index(m["bar"]) if m["bar"] in stamps else 0
            left = i + room >= len(stamps)                              # near the right edge: the label sits left of the candle
            at = stamps[max(0, i - room - 2)] if left else m["bar"]
            if left and i >= 1:                                         # a short leader from the label to its candle
                drawings.append({"kind": "trendline", "anchors": [{"t": stamps[max(0, i - 2)], "p": m["price"]}, {"t": m["bar"], "p": m["price"]}],
                                 "style": {"color": _hex(colour), "width": 2, "line": "solid"}})
            drawings.append({"kind": "text", "anchors": [{"t": at, "p": m["price"]}], "text": f"{m['label']} {m['price']:,.2f}",
                             "style": {"color": _hex(colour)}})
    chart_img, _ = render_chart({"bars": bars, "tf": "1d", "reason": data.get("missing_text") or "no candles"}, areas=[],
                                drawings=drawings, protected=None, reference=None, pal=pal, width=PNG_W, height=PNG_H, font_px=18)
    w, h = PNG_W + 2 * PAD, PAD + int(HEAD * P.LINE_GAP) + 12 + PNG_H + 12 + int(2 * 20 * P.LINE_GAP) + PAD
    img = Image.new("RGB", (w, h), pal.background)
    draw = ImageDraw.Draw(img)
    head = f"{data['symbol']} - {data['timeframe_label']} - your trade: " + ", ".join(
        f"{m['label'].lower()} {P.ist(m['at'])} at {m['price']:,.2f}" for m in data["marks"])
    draw.text((PAD, PAD), head, font=fonts.regular(HEAD), fill=pal.text)
    top = PAD + int(HEAD * P.LINE_GAP) + 12
    img.paste(chart_img, (PAD, top))
    draw.text((PAD, top + PNG_H + 12), data["note"], font=fonts.regular(20), fill=pal.text_muted)
    buf = io.BytesIO()
    img.save(buf, format="PNG", compress_level=9, optimize=False)
    return buf.getvalue()


async def png(session: AsyncSession, row: JournalManualTradeRecord, *, cvd: bool = False) -> bytes:
    data = await chart(session, row)
    return await asyncio.to_thread(render, data, cvd=cvd)
