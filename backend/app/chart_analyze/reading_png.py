"""Analyze a chart, hybrid mode (M59 CR-4): ATP's reading drawn by code on a server-rendered chart of the stored candles.

The picture the user uploaded has no known price axis, so nothing is drawn on it; this chart is drawn from the candles
the reading was made from (stored with the result), with the reading's annotations:
- the areas of interest as bands (above the price: a red tint; below: a green tint; at the price: an amber tint);
- the protected level as a solid line, the nearest swing levels as dashed lines, the last break's level dotted;
- the trigger candle (the candle of the last break) boxed and marked.
Deterministic (no clock, no randomness, the same input gives the same bytes), standard library only (the PNG encoder
and the bitmap font of app/ai/chart_png.py). The download and the page show the same picture. Labels are structure
words only ("AREA", "PROTECTED", "BREAK", "TRIGGER"); no order, no call wording.
"""
from __future__ import annotations

from typing import Any

from app.ai.chart_png import AXIS, BG, DOWN, GRID, LABEL_BG, SCALE, UP, WICK, Color, _Canvas

WIDTH = 960
HEIGHT = 540
AREA_ABOVE: Color = (252, 226, 226)
AREA_ABOVE_EDGE: Color = (214, 56, 56)
AREA_BELOW: Color = (221, 244, 231)
AREA_BELOW_EDGE: Color = (22, 150, 92)
AREA_INSIDE: Color = (254, 241, 199)
AREA_INSIDE_EDGE: Color = (180, 120, 10)
PROTECTED: Color = (37, 99, 235)
SWING: Color = (120, 126, 136)
BREAK: Color = (124, 58, 237)
TRIGGER: Color = (217, 119, 6)
TITLE: Color = (30, 34, 42)
MUTED: Color = (110, 116, 126)
REACH = 0.25            # a band or line further than this share of the candles' range is listed, not drawn


def _fmt(price: float) -> str:
    """A label price that fits the axis column: whole points from 1,000 up, else two decimals (the page shows both)."""
    return f"{price:.0f}" if abs(price) >= 1000 else f"{price:.2f}"


def _outline(canvas: _Canvas, x0: int, y0: int, x1: int, y1: int, color: Color, thick: int = 2) -> None:
    for t in range(thick):
        canvas.hline(x0 - t, x1 + t, y0 - t, color)
        canvas.hline(x0 - t, x1 + t, y1 + t, color)
        canvas.vline(x0 - t, y0 - t, y1 + t, color)
        canvas.vline(x1 + t, y0 - t, y1 + t, color)


def render(reading: dict[str, Any], *, width: int = WIDTH, height: int = HEIGHT) -> bytes:
    """The PNG bytes of ATP's reading. Raises ValueError without candles."""
    bars = [b for b in reading.get("bars") or [] if min(b["o"], b["h"], b["l"], b["c"]) > 0]
    if not bars:
        raise ValueError("no candles to draw")
    canvas = _Canvas(width, height, BG)
    left, right, top, bottom = 12, width - 160, 40, height - 30
    lo = min(b["l"] for b in bars)
    hi = max(b["h"] for b in bars)
    span = max(hi - lo, hi * 0.001, 0.01)
    reach_lo, reach_hi = lo - span * REACH, hi + span * REACH

    def near(p: float | None) -> bool:
        return p is not None and reach_lo <= p <= reach_hi

    areas = [a for a in reading.get("areas") or [] if near(a.get("low")) or near(a.get("high"))]
    protected = reading.get("protected")
    brk = reading.get("last_break") or None
    levels = [lv for lv in reading.get("levels") or [] if near(lv.get("price"))]
    shown = [p for p in ([a["low"] for a in areas] + [a["high"] for a in areas] + [lv["price"] for lv in levels]
                         + ([float(protected)] if isinstance(protected, (int, float)) and near(protected) else []) + ([brk["price"]] if brk and near(brk.get("price")) else []))]
    lo, hi = min([lo, *shown]), max([hi, *shown])
    pad = (hi - lo) * 0.05 or 0.01
    lo, hi = lo - pad, hi + pad

    def y_of(price: float) -> int:
        return int(round(bottom - (price - lo) / (hi - lo) * (bottom - top)))

    title = f"{reading.get('symbol') or ''} {reading.get('timeframe_label') or reading.get('timeframe') or ''}"
    canvas.text(left, 12, title, TITLE)
    canvas.text(left + 8 * SCALE * (len(title) + 2), 12, "ATP READING OF THE STORED CANDLES", MUTED)
    for i in range(1, 6):
        canvas.hline(left, right, top + (bottom - top) * i // 6, GRID)

    # the areas of interest first (under everything), then the candles, then the lines and the labels on top
    edges = {"above": (AREA_ABOVE, AREA_ABOVE_EDGE), "below": (AREA_BELOW, AREA_BELOW_EDGE), "inside": (AREA_INSIDE, AREA_INSIDE_EDGE)}
    labels: list[tuple[int, str, Color]] = []
    for a in areas:
        fill, edge = edges.get(a.get("where"), edges["below"])
        y_hi, y_lo = y_of(a["high"]), y_of(a["low"])
        canvas.rect(left, y_hi, right, max(y_hi, y_lo), fill)
        canvas.hline(left, right, y_hi, edge, dash=4)
        canvas.hline(left, right, y_lo, edge, dash=4)
        labels.append(((y_hi + y_lo) // 2, f"AREA {_fmt(a['low'])}-{_fmt(a['high'])}", edge))
    canvas.vline(right, top, bottom, AXIS)
    canvas.hline(left, right, bottom, AXIS)

    slot = (right - left - 4) / len(bars)
    body = max(1, int(slot * 0.6))
    centres: list[int] = []
    for i, b in enumerate(bars):
        cx = int(left + 2 + slot * i + slot / 2)
        centres.append(cx)
        canvas.vline(cx, y_of(b["h"]), y_of(b["l"]), WICK)
        y0, y1 = y_of(max(b["o"], b["c"])), y_of(min(b["o"], b["c"]))
        canvas.rect(cx - body // 2, y0, cx - body // 2 + body - 1, max(y0, y1), UP if b["c"] >= b["o"] else DOWN)

    for lv in levels:
        y = y_of(lv["price"])
        canvas.hline(left, right, y, SWING, dash=6)
        labels.append((y, f"{'HIGH' if lv.get('kind') == 'swing high' else 'LOW'} {_fmt(lv['price'])}", SWING))
    if brk and near(brk.get("price")):
        y = y_of(brk["price"])
        canvas.hline(left, right, y, BREAK, dash=2)
        labels.append((y, f"BREAK {_fmt(brk['price'])}", BREAK))
    if isinstance(protected, (int, float)) and near(protected):
        y = y_of(float(protected))
        canvas.hline(left, right, y, PROTECTED)
        canvas.hline(left, right, y + 1, PROTECTED)
        labels.append((y, f"PROTECTED {_fmt(float(protected))}", PROTECTED))

    trig = reading.get("trigger_candle")
    if isinstance(trig, dict) and isinstance(trig.get("index"), int) and 0 <= trig["index"] < len(bars):
        b = bars[trig["index"]]
        cx = centres[trig["index"]]
        half = max(body // 2 + 3, 4)
        _outline(canvas, cx - half, y_of(b["h"]) - 4, cx + half, y_of(b["l"]) + 4, TRIGGER)
        tip = y_of(b["l"]) + 10
        for row in range(8):                                  # a small triangle under the candle, pointing at it
            canvas.hline(cx - row, cx + row, tip + row, TRIGGER)
        tx = min(max(left, cx - 28), right - 60)
        canvas.text(tx, min(tip + 12, bottom - 12), "TRIGGER", TRIGGER)

    used: list[int] = []
    for y, text, color in sorted(labels):
        ly = min(max(top, y - 5), bottom - 10)
        while any(abs(ly - u) < 13 for u in used):            # keep labels from overlapping
            ly += 13
        used.append(ly)
        canvas.rect(right + 3, ly - 1, width - 2, ly + 5 * SCALE, LABEL_BG)
        canvas.text(right + 5, ly, text[:18], color)
    canvas.text(left, height - 18, "EDUCATIONAL ONLY - REFERENCE, NOT ADVICE. NO ORDER IS PLACED.", MUTED)
    return canvas.png()


__all__ = ["render", "WIDTH", "HEIGHT"]
