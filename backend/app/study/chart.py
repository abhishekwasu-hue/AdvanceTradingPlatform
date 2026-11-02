"""M77 SX-4: the study chart renderer (PD-2.2) - one timeframe of a stored study snapshot drawn with Pillow: the closed
bars, the areas as filled bands (seller / buyer, each labelled with its own timeframe and state - never merged), the
confirmed swings, the structure events (BOS / CHoCH), the protected level, the reference price on the price axis and
the member's own drawings (every `drawing/1` kind, at their time / price anchors).

* Every number comes from the snapshot (the server's own reads, app/study/snapshot.py); nothing reads a clock or the
  network, and the same snapshot (and `cvd`) gives the same pixels.
* Band labels are placed by `place_band_labels`, a port of the Charts tab's `placeBandLabels`
  (frontend/src/charting/geometry.ts, M75 W-1 + M78 chips) - both run the same fixture
  (frontend/src/charting/bandLabelsFixture.json): never on the price axis inset, never over another label. Swing and
  event labels go only where no band label or other label already is (left out otherwise; the mark is still drawn).
* Colours are the app's light tokens (app/study/palette.py); a drawing keeps the colour he gave it.
* Words: structure words only (areas, swings, BOS / CHoCH, protected level); his position drawings show their prices,
  never a word. Read-only: nothing here places, changes or blocks an order.
"""
from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

from PIL import Image, ImageDraw

from app.study import fonts
from app.study.numfmt import format_number
from app.study.palette import Palette, Rgb

# The Charts tab's label geometry (frontend/src/charting/geometry.ts) - the same constants, in the renderer's pixels.
LABEL_FONT_PX, LABEL_CHAR_PX = 11, 7
CHIP_PAD_X, CHIP_PAD_Y, LABEL_PAD = 4, 3, 4
FIB_RETRACEMENT_LEVELS = (0, 0.236, 0.382, 0.5, 0.618, 0.786, 1)
FIB_EXTENSION_LEVELS = (0, 0.618, 1, 1.618, 2.618)
DEFAULT_COLOR = "#2962ff"
BROKEN_STATES = frozenset({"flipped", "broken", "dead"})
ROLE_WORDS = {"supply": "Seller area", "demand": "Buyer area"}
_IST = timezone(timedelta(hours=5, minutes=30))


@dataclass(frozen=True)
class Rect:
    x: float
    y: float
    w: float
    h: float


def overlaps(a: Rect, b: Rect) -> bool:
    return a.x < b.x + b.w and b.x < a.x + a.w and a.y < b.y + b.h and b.y < a.y + a.h


def chip_height(font_px: int) -> int:
    return font_px + 2 * CHIP_PAD_Y


def default_measure(font_px: int) -> Callable[[str], float]:
    per = math.ceil(font_px * LABEL_CHAR_PX / LABEL_FONT_PX)
    return lambda text: len(text) * per


def place_band_labels(items: Sequence[Mapping[str, Any]], width: float, height: float, *, avoid: Sequence[Rect] = (),
                      font_px: int = 12, measure: Callable[[str], float] | None = None) -> list[tuple[Rect, int]]:
    """Port of `placeBandLabels` (geometry.ts): for each item ({top, bottom, text}) in order, its chip rectangle - tried
    at the left of the pane inside the band (tall enough) or on a thin band's own line, just above, just below, beside a
    reserved box near the band; then the same spots at the right, then just left of each reserved box; the first spot
    inside the pane (LABEL_PAD clear of both sides - the price axis is outside the pane) and clear of the reserved boxes
    and the chips already placed wins. A label with no free spot is left out. Returns (rect, item index)."""
    taken: list[Rect] = list(avoid)
    measure = measure or default_measure(font_px)
    h = chip_height(font_px)
    near = 2 * h
    out: list[tuple[Rect, int]] = []
    for index, it in enumerate(items):
        top, bottom = float(it["top"]), float(it["bottom"])
        w = math.ceil(measure(str(it["text"]))) + 2 * CHIP_PAD_X
        if w + 2 * LABEL_PAD > width:
            continue
        shown_top = max(top, 0.0)
        ys: list[float] = [shown_top + 2 if bottom - shown_top >= h + 4 else (top + bottom) / 2 - h / 2]
        ys += [top - 2 - h, bottom + 2]
        for a in avoid:
            for y in (a.y - 2 - h, a.y + a.h + 2):
                if max(top - (y + h), y - bottom) <= near:
                    ys.append(y)
        xs = [float(LABEL_PAD), width - LABEL_PAD - w, *(a.x - LABEL_PAD - w for a in avoid)]
        spot: Rect | None = None
        for x in xs:
            for y in ys:
                r = Rect(x, y, w, h)
                if y < 0 or y + h > height or x < LABEL_PAD - 0.5 or x + w > width - LABEL_PAD + 0.5:
                    continue
                if any(overlaps(r, t) for t in taken):
                    continue
                spot = r
                break
            if spot:
                break
        if spot is None:
            continue
        taken.append(spot)
        out.append((spot, index))
    return out


# ---------------------------------------------------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------------------------------------------------
def hex_rgb(value: Any, fallback: str = DEFAULT_COLOR) -> Rgb:
    text = str(value or "").strip()
    if not (len(text) == 7 and text.startswith("#")):
        text = fallback
    try:
        return (int(text[1:3], 16), int(text[3:5], 16), int(text[5:7], 16))
    except ValueError:
        return hex_rgb(fallback)


def _epoch(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    try:
        when = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return (when if when.tzinfo else when.replace(tzinfo=UTC)).timestamp()


def time_to_logical(times: Sequence[float], t: float | None) -> float | None:
    """Port of `timeToLogical` (geometry.ts): the fractional bar index of a time; interpolated between bars,
    extrapolated with the nearest bar spacing beyond either end."""
    n = len(times)
    if t is None or not math.isfinite(t) or n == 0:
        return None
    if n == 1:
        return 0.0 if t == times[0] else None
    if t <= times[0]:
        return (t - times[0]) / ((times[1] - times[0]) or 1)
    if t >= times[-1]:
        return n - 1 + (t - times[-1]) / ((times[-1] - times[-2]) or 1)
    lo, hi = 0, n - 1
    while hi - lo > 1:
        mid = (lo + hi) >> 1
        if times[mid] <= t:
            lo = mid
        else:
            hi = mid
    return lo + (t - times[lo]) / ((times[hi] - times[lo]) or 1)


def nice_step(span: float, ticks: int = 6) -> float:
    raw = span / max(1, ticks)
    if raw <= 0 or not math.isfinite(raw):
        return 1.0
    mag = 10 ** math.floor(math.log10(raw))
    for m in (1, 2, 2.5, 5, 10):
        if m * mag >= raw:
            return m * mag
    return 10 * mag


def _price_text(value: float, step: float) -> str:
    return format_number(value, 0 if step >= 1 else 2)


def _fmt(v: float) -> str:
    return format_number(v, 1 if abs(v) >= 1000 else 2)


def _pct(v: float, base: float) -> str:
    return f"{'+' if v >= 0 else ''}{format_number(v / base * 100, 2)}%" if base else ""


def _dashed(draw: ImageDraw.ImageDraw, x1: float, y1: float, x2: float, y2: float, colour: Rgb, width: int, dash: str) -> None:
    if dash == "solid":
        draw.line((x1, y1, x2, y2), fill=colour, width=width)
        return
    on, off = (8, 6) if dash == "dashed" else (2, 4)
    length = math.hypot(x2 - x1, y2 - y1)
    if length == 0:
        return
    ux, uy = (x2 - x1) / length, (y2 - y1) / length
    pos = 0.0
    while pos < length:
        end = min(length, pos + on)
        draw.line((x1 + ux * pos, y1 + uy * pos, x1 + ux * end, y1 + uy * end), fill=colour, width=width)
        pos = end + off


def extend_segment(x1: float, y1: float, x2: float, y2: float, width: float, extend: str) -> tuple[float, float, float, float]:
    """Port of `extendSegment` (geometry.ts)."""
    if extend == "none" or x1 == x2:
        return x1, y1, x2, y2
    slope = (y2 - y1) / (x2 - x1)
    lx, ly, rx, ry = (x1, y1, x2, y2) if x1 < x2 else (x2, y2, x1, y1)
    left, right = extend in ("left", "both"), extend in ("right", "both")
    return (0 if left else lx, ly - slope * lx if left else ly, width if right else rx, ry + slope * (width - rx) if right else ry)


def fib_levels(kind: str, prices: Sequence[float | None], levels: Sequence[float] | None) -> list[tuple[float, float]]:
    """Port of `fibLevels` (geometry.ts)."""
    p = [float(x) if isinstance(x, (int, float)) else math.nan for x in prices]
    if kind == "fib_retracement" and len(p) == 2:
        return [(lv, p[1] - (p[1] - p[0]) * lv) for lv in (levels or FIB_RETRACEMENT_LEVELS)]
    if kind == "fib_extension" and len(p) == 3:
        return [(lv, p[2] + (p[1] - p[0]) * lv) for lv in (levels or FIB_EXTENSION_LEVELS)]
    return []


# ---------------------------------------------------------------------------------------------------------------------
# The renderer
# ---------------------------------------------------------------------------------------------------------------------
@dataclass
class Drawn:
    """What a chart drew (for the tests and the caption): candles, the placed label chips, his drawings drawn."""
    candles: int = 0
    labels: list[tuple[Rect, str]] = field(default_factory=list)
    drawings: list[str] = field(default_factory=list)
    pane: Rect = field(default_factory=lambda: Rect(0, 0, 0, 0))


class _Pane:
    def __init__(self, bars: Sequence[Sequence[Any]], w: int, h: int, extra: Sequence[float]) -> None:
        self.w, self.h = w, h
        self.times = [float(b[0]) for b in bars]
        highs = [float(b[2]) for b in bars if b[2] is not None]
        lows = [float(b[3]) for b in bars if b[3] is not None]
        hi, lo = max(highs + list(extra)), min(lows + list(extra))
        pad = (hi - lo) * 0.06 or max(1.0, abs(hi) * 0.01)
        self.hi, self.lo = hi + pad, lo - pad
        self.slot = w / max(1, len(bars))

    def x_of_index(self, i: float) -> float:
        return (i + 0.5) * self.slot

    def x(self, t: Any) -> float | None:
        logical = time_to_logical(self.times, _epoch(t))
        return None if logical is None else self.x_of_index(logical)

    def y(self, p: Any) -> float | None:
        if not isinstance(p, (int, float)) or isinstance(p, bool) or not math.isfinite(p):
            return None
        return self.h - (float(p) - self.lo) / (self.hi - self.lo) * self.h


def _drawing(draw: ImageDraw.ImageDraw, fill: ImageDraw.ImageDraw, d: Mapping[str, Any], pane: _Pane, pal: Palette,
             font_px: int, labels: list[tuple[float, float, str, Rgb, str]]) -> bool:
    """One of his drawings (port of `shapes`, geometry.ts); False when an anchor cannot be placed."""
    kind = str(d.get("kind") or "")
    style = d.get("style") or {}
    colour = hex_rgb(style.get("color"))
    width = int(min(4, max(1, float(style.get("width") or 1))))
    dash = str(style.get("line") or "solid")
    anchors = list(d.get("anchors") or [])
    xs = [pane.x(a.get("t")) if isinstance(a, Mapping) and a.get("t") is not None else None for a in anchors]
    ys = [pane.y(a.get("p")) if isinstance(a, Mapping) else None for a in anchors]

    def placed(i: int, need: str) -> bool:
        return i < len(anchors) and ("t" not in need or xs[i] is not None) and ("p" not in need or ys[i] is not None)

    def tint(c: Rgb, alpha: float) -> tuple[int, int, int, int]:
        return (c[0], c[1], c[2], round(255 * alpha))

    def seg(x1: float, y1: float, x2: float, y2: float, w: int = width, dsh: str = dash) -> None:
        _dashed(draw, x1, y1, x2, y2, colour, w, dsh)

    if kind == "hline" and placed(0, "p"):
        seg(0, ys[0], pane.w, ys[0])  # type: ignore[arg-type]
    elif kind == "vline" and placed(0, "t"):
        seg(xs[0], 0, xs[0], pane.h)  # type: ignore[arg-type]
    elif kind in ("trendline", "ray", "measure") and placed(0, "tp") and placed(1, "tp"):
        x1, y1, x2, y2 = xs[0], ys[0], xs[1], ys[1]
        assert x1 is not None and y1 is not None and x2 is not None and y2 is not None
        if kind == "ray" and x1 == x2:
            seg(x1, y1, x2, y1 if y2 == y1 else 0 if y2 < y1 else pane.h)
            return True
        mode = ("right" if x2 > x1 else "left") if kind == "ray" else "none" if kind == "measure" else str(style.get("extend") or "none")
        seg(*extend_segment(x1, y1, x2, y2, pane.w, mode))
        if kind == "measure":
            p1, p2 = float(anchors[0]["p"]), float(anchors[1]["p"])
            labels.append((x2, y2 - 4, f"{'+' if p2 - p1 >= 0 else ''}{_fmt(p2 - p1)} ({_pct(p2 - p1, p1)})", colour, "bottom"))
    elif kind == "rectangle" and placed(0, "tp") and placed(1, "tp"):
        x1, y1, x2, y2 = xs[0], ys[0], xs[1], ys[1]
        assert x1 is not None and y1 is not None and x2 is not None and y2 is not None
        box = (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))
        fill.rectangle(box, fill=tint(colour, 0.15))
        draw.rectangle(box, outline=colour, width=width)
    elif kind == "channel" and all(placed(i, "tp") for i in range(3)):
        x1, x2, x3, y1, y2, y3 = (float(v) for v in (xs[0], xs[1], xs[2], ys[0], ys[1], ys[2]))  # type: ignore[arg-type]
        base = y1 if x1 == x2 else y1 + (y2 - y1) * (x3 - x1) / (x2 - x1)
        dy = y3 - base
        ext = str(style.get("extend") or "none")
        a = extend_segment(x1, y1, x2, y2, pane.w, ext)
        b = extend_segment(x1, y1 + dy, x2, y2 + dy, pane.w, ext)
        fill.polygon([(a[0], a[1]), (a[2], a[3]), (b[2], b[3]), (b[0], b[1])], fill=tint(colour, 0.1))
        seg(*a)
        seg(*b)
        seg((a[0] + b[0]) / 2, (a[1] + b[1]) / 2, (a[2] + b[2]) / 2, (a[3] + b[3]) / 2, 1, "dashed")
    elif kind in ("fib_retracement", "fib_extension") and all(placed(i, "tp") for i in range(2 if kind == "fib_retracement" else 3)):
        x0, x1 = float(xs[0]), float(xs[1])  # type: ignore[arg-type]
        left = min(x0, x1) if kind == "fib_retracement" else float(xs[2])  # type: ignore[arg-type]
        span = abs(x1 - x0)
        ext = str(style.get("extend") or "none")
        right = pane.w if ext in ("right", "both") else max(x0, x1) if kind == "fib_retracement" else left + span
        for level, value in fib_levels(kind, [a.get("p") for a in anchors], d.get("levels")):
            y = pane.y(value)
            if y is None:
                continue
            seg(left, y, right, y, 1)
            labels.append((left + 2, y - 2, f"{format_number(level, 3, trim=True)} ({_fmt(value)})", colour, "bottom"))
    elif kind == "text" and placed(0, "tp"):
        labels.append((float(xs[0]), float(ys[0]), str(d.get("text") or ""), colour, "middle"))  # type: ignore[arg-type]
    elif kind in ("long_position", "short_position") and placed(0, "tp") and placed(1, "p") and placed(2, "p"):
        entry, stop, target = (float(anchors[i]["p"]) for i in range(3))
        x, ye, ys_, yt = float(xs[0]), float(ys[0]), float(ys[1]), float(ys[2])  # type: ignore[arg-type]
        w = max(0.0, pane.w - x)
        fill.rectangle((x, min(ye, yt), x + w, max(ye, yt)), fill=tint(pal.buyer, 0.18))
        fill.rectangle((x, min(ye, ys_), x + w, max(ye, ys_)), fill=tint(pal.seller, 0.18))
        seg(x, ye, x + w, ye, 1)
        # his own levels, as prices only (no words: they are his marks, not a call)
        labels.append((x + 4, yt, f"{_fmt(target)} ({_pct(target - entry, entry)})", pal.buyer, "top" if yt < ye else "bottom"))
        labels.append((x + 4, ys_, f"{_fmt(stop)} ({_pct(stop - entry, entry)})", pal.seller, "top" if ys_ < ye else "bottom"))
        labels.append((x + 4, ye - 2, _fmt(entry), colour, "bottom"))
    else:
        return False
    return True


def render_chart(chart: Mapping[str, Any], *, areas: Sequence[Mapping[str, Any]], drawings: Sequence[Mapping[str, Any]],
                 protected: Mapping[str, Any] | None, reference: float | None, pal: Palette, width: int, height: int,
                 font_px: int = 15) -> tuple[Image.Image, Drawn]:
    """One timeframe's chart as an RGB image of `width` x `height` (price axis inset on the right, time axis below)."""
    img = Image.new("RGB", (width, height), pal.background)
    draw = ImageDraw.Draw(img)
    font = fonts.regular(font_px)
    small = fonts.regular(font_px - 2)
    bars = [b for b in chart.get("bars") or [] if len(b) >= 5 and all(isinstance(v, (int, float)) for v in b[1:5])]
    axis_w = int(font.getlength("00,00,000.00")) + 24
    time_h = font_px + 18
    pw, ph = width - axis_w, height - time_h
    info = Drawn(pane=Rect(0, 0, pw, ph))
    draw.rectangle((0, 0, width - 1, height - 1), outline=pal.border)
    if not bars:
        reason = str(chart.get("reason") or "no closed bars for this timeframe")
        draw.text((width / 2, height / 2), f"Not available: {reason}.", font=font, fill=pal.text_muted, anchor="mm")
        return img, info
    extra = [float(reference)] if isinstance(reference, (int, float)) else []
    pane = _Pane(bars, pw, ph, extra)
    layer = Image.new("RGB", (pw, ph), pal.background)
    d = ImageDraw.Draw(layer)
    overlay = Image.new("RGBA", (pw, ph), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    # grid + price axis
    step = nice_step(pane.hi - pane.lo)
    tick = math.ceil(pane.lo / step) * step
    ticks: list[float] = []
    while tick <= pane.hi:
        ticks.append(tick)
        tick += step
    for t in ticks:
        y = pane.y(t)
        if y is not None:
            d.line((0, y, pw, y), fill=pal.grid)
    # areas (filled bands; a broken area fainter), every area labelled with its own timeframe and state
    band_items: list[dict[str, Any]] = []
    for a in areas:
        lo_, hi_ = a.get("low"), a.get("high")
        y_hi, y_lo = pane.y(hi_), pane.y(lo_)
        if y_hi is None or y_lo is None or y_lo < 0 or y_hi > ph:
            continue
        buyer = a.get("role") == "demand"
        broken = str(a.get("state") or "") in BROKEN_STATES
        fill_c = (pal.buyer_band_broken if broken else pal.buyer_band) if buyer else (pal.seller_band_broken if broken else pal.seller_band)
        edge = pal.buyer_edge if buyer else pal.seller_edge
        top, bottom = min(y_hi, y_lo), max(y_hi, y_lo)
        if bottom - top < 1:
            d.line((0, top, pw, top), fill=edge, width=2)
        else:
            d.rectangle((0, top, pw, bottom), fill=fill_c, outline=edge)
        text = " · ".join(p for p in (str(a.get("label") or ""), ROLE_WORDS.get(str(a.get("role")), "Area"),
                                      str(a.get("timeframe") or ""), str(a.get("state") or "")) if p)
        band_items.append({"top": top, "bottom": bottom, "text": text, "colour": pal.buyer if buyer else pal.seller})
    prot = protected.get("price") if protected else None
    y_prot = pane.y(prot)
    if y_prot is not None and 0 <= y_prot <= ph:
        _dashed(d, 0, y_prot, pw, y_prot, pal.warn, 2, "dashed")
        band_items.append({"top": y_prot, "bottom": y_prot, "text": "Protected level", "colour": pal.text})
    # candles (the snapshot's closed bars only)
    body_w = max(1.0, pane.slot * 0.7)
    for i, b in enumerate(bars):
        o, h, lo, c = (float(v) for v in b[1:5])
        x = pane.x_of_index(i)
        colour = pal.buyer if c >= o else pal.seller
        yh, yl, yo, yc = pane.y(h), pane.y(lo), pane.y(o), pane.y(c)
        assert yh is not None and yl is not None and yo is not None and yc is not None
        d.line((x, yh, x, yl), fill=colour, width=1)
        top, bot = min(yo, yc), max(yo, yc)
        d.rectangle((x - body_w / 2, top, x + body_w / 2, max(top + 1, bot)), fill=colour)
        info.candles += 1
    # structure events: a dashed level ending at the bar that made it
    marks: list[tuple[float, float, str, Rgb, str]] = []
    for e in chart.get("events") or []:
        ex, ey = pane.x(e.get("time")), pane.y(e.get("price"))
        if ex is None or ey is None:
            continue
        x, y = ex, ey
        colour = pal.buyer if e.get("direction") == "up" else pal.seller
        _dashed(d, max(0.0, x - 8 * pane.slot), y, x, y, colour, 1, "dashed")
        marks.append((max(0.0, x - 8 * pane.slot), y - 2, str(e.get("name") or e.get("type") or ""), colour, "bottom"))
    for s in chart.get("swings") or []:
        sx, sy = pane.x(s.get("time")), pane.y(s.get("price"))
        if sx is None or sy is None:
            continue
        x, y = sx, sy
        high = str(s.get("kind") or "").upper().startswith("H")
        marks.append((x, y - 4 if high else y + 4, str(s.get("label") or ""), pal.chart_text, "bottom" if high else "top"))
    # his drawings (drawn on every timeframe, at their own anchors)
    texts: list[tuple[float, float, str, Rgb, str]] = []
    for dr in drawings:
        if _drawing(d, od, dr, pane, pal, font_px, texts):
            info.drawings.append(str(dr.get("kind")))
    layer = Image.alpha_composite(layer.convert("RGBA"), overlay).convert("RGB")
    d = ImageDraw.Draw(layer)
    for x, y, text, colour, baseline in texts:                                  # his own words / numbers: always drawn
        if text:
            d.text((x, y), text, font=small, fill=colour, anchor={"top": "la", "middle": "lm", "bottom": "ld"}[baseline])
    # label chips: band labels first (the Charts tab's placer), then marks wherever free
    measure = font.getlength
    placed = place_band_labels(band_items, pw, ph, font_px=font_px, measure=measure)
    taken = [r for r, _ in placed]
    for rect, idx in placed:
        it = band_items[idx]
        d.rounded_rectangle((rect.x, rect.y, rect.x + rect.w, rect.y + rect.h), radius=3, fill=pal.background, outline=it["colour"])
        d.text((rect.x + CHIP_PAD_X, rect.y + rect.h / 2), it["text"], font=font, fill=it["colour"], anchor="lm")
        info.labels.append((rect, it["text"]))
    for x, y, text, colour, baseline in marks:
        if not text:
            continue
        w = math.ceil(small.getlength(text)) + 2 * CHIP_PAD_X
        sh = chip_height(font_px - 2)
        ry = y - sh if baseline == "bottom" else y if baseline == "top" else y - sh / 2
        rect = Rect(min(max(LABEL_PAD, x - w / 2), pw - LABEL_PAD - w), ry, w, sh)
        if rect.y < 0 or rect.y + rect.h > ph or any(overlaps(rect, t) for t in taken):
            continue
        taken.append(rect)
        d.rounded_rectangle((rect.x, rect.y, rect.x + rect.w, rect.y + rect.h), radius=3, fill=pal.background)
        d.text((rect.x + CHIP_PAD_X, rect.y + sh / 2), text, font=small, fill=colour, anchor="lm")
        info.labels.append((rect, text))
    img.paste(layer, (0, 0))
    # the price axis inset (outside the pane) and the reference price tag
    draw.line((pw, 0, pw, ph), fill=pal.axis)
    for t in ticks:
        y = pane.y(t)
        if y is not None and font_px / 2 <= y <= ph - font_px / 2:
            draw.text((pw + 8, y), _price_text(t, step), font=small, fill=pal.chart_text, anchor="lm")
    y_ref = pane.y(reference)
    if y_ref is not None and 0 <= y_ref <= ph:
        tag = format_number(float(reference), 2)  # type: ignore[arg-type]
        th = chip_height(font_px)
        draw.rectangle((pw + 1, y_ref - th / 2, width - 2, y_ref + th / 2), fill=pal.brand)
        draw.text((pw + 8, y_ref), tag, font=small, fill=pal.background, anchor="lm")
    # the time axis (IST)
    draw.line((0, ph, pw, ph), fill=pal.axis)
    daily = str(chart.get("tf")) == "1d"
    n = len(bars)
    count = max(2, min(6, pw // 220))
    seen: set[int] = set()
    for k in range(count):
        i = round(k * (n - 1) / (count - 1)) if n > 1 else 0
        if i in seen:
            continue
        seen.add(i)
        when = datetime.fromtimestamp(float(bars[i][0]), tz=UTC).astimezone(_IST)
        text = when.strftime("%d %b %Y") if daily else when.strftime("%d %b %H:%M")
        x = pane.x_of_index(i)
        anchor = "la" if k == 0 else "ra" if k == count - 1 else "ma"
        draw.text((min(max(x, 4), pw - 4), ph + 6), text, font=small, fill=pal.chart_text, anchor=anchor)
    return img, info


__all__ = ["Drawn", "Rect", "chip_height", "fib_levels", "hex_rgb", "nice_step", "overlaps", "place_band_labels", "render_chart",
           "time_to_logical"]
