"""U8 EV2: the evidence chart as a PNG for Telegram (and for download) - drawn from an evidence pack only.

The same deterministic renderer as the Vision audit chart (`app/ai/chart_png.py`: the standard-library PNG encoder and
its canvas; no network, no clock, no font files), with a fuller built-in bitmap font for the labels. The same pack always
gives the same bytes (tested by hash), so a stored SHA-256 identifies exactly what was sent.

What is drawn is the pack and nothing else (owner rule 0): the bars, the matched bar, and the drawables of the blocks
that define the signal - zone bands, breakout boxes and levels first, then highlighted candles, then price overlays, then
at most one lower pane (volume or an oscillator with its threshold) - at most IMAGE_MAX_DRAWABLES; every other condition
is in the caption (`evidence.caption_text`). The drawables' colours are the Okabe-Ito set (readable with colour-blind
vision). SCR-FIX C3: laid out like the app's chart - candles and volume in the Charts page's up / down colours, the price
axis on the right, a volume pane, the matched bar's O / H / L / C under the title and the bar times on the time axis.
Light and dark themes.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.ai import chart_png

Color = Tuple[int, int, int]
WIDTH, HEIGHT = 800, 520
SUB_PANE_HEIGHT = 92
VOLUME_PANE_HEIGHT = 56
IMAGE_MAX_DRAWABLES = 4
MAX_PNG_BYTES = 512 * 1024
SCALE = 2

# 3x5 glyphs (rows top to bottom; bits 4 = left, 2 = middle, 1 = right). Lower case is drawn as upper case; anything
# else (another script, an emoji) as "?" - the caption carries the exact text.
GLYPHS: Dict[str, Sequence[int]] = {
    **chart_png._FONT,
    "B": (6, 5, 6, 5, 6), "C": (7, 4, 4, 4, 7), "D": (6, 5, 5, 5, 6), "F": (7, 4, 6, 4, 4), "J": (1, 1, 1, 5, 7), "K": (5, 5, 6, 5, 5),
    "M": (5, 7, 7, 5, 5), "N": (6, 5, 5, 5, 5), "Q": (7, 5, 5, 6, 3), "U": (5, 5, 5, 5, 7), "V": (5, 5, 5, 5, 2), "Z": (7, 1, 2, 4, 7),
    "(": (1, 2, 2, 2, 1), ")": (4, 2, 2, 2, 4), ">": (4, 2, 1, 2, 4), "<": (1, 2, 4, 2, 1), "=": (0, 7, 0, 7, 0), "%": (5, 1, 2, 4, 5),
    ":": (0, 2, 0, 2, 0), ",": (0, 0, 0, 2, 4), "+": (0, 2, 7, 2, 0), "*": (0, 5, 2, 5, 0), "'": (2, 2, 0, 0, 0), '"': (5, 5, 0, 0, 0),
    "[": (3, 2, 2, 2, 3), "]": (6, 2, 2, 2, 6), "@": (7, 5, 7, 4, 7), "_": (0, 0, 0, 0, 7), "#": (5, 7, 5, 7, 5), "!": (2, 2, 2, 0, 2),
    "?": (7, 1, 2, 0, 2), "&": (2, 5, 2, 5, 3), "$": (7, 6, 7, 3, 7), "·": (0, 0, 2, 0, 0), "|": (2, 2, 2, 2, 2),
}


@dataclass(frozen=True)
class Theme:
    bg: Color
    grid: Color
    axis: Color
    text: Color
    muted: Color
    wick: Color
    up: Color
    down: Color
    marker: Color


# SCR-FIX C3: candles and volume in the Charts page's own up / down colours (frontend tokens.css --chart-up / --chart-down
# for the light and the dark chart theme), so the Telegram image reads like the chart the trader opens in the app.
LIGHT = Theme(bg=(255, 255, 255), grid=(232, 234, 238), axis=(120, 126, 136), text=(31, 41, 55), muted=(107, 114, 128),
              wick=(75, 85, 99), up=(21, 128, 61), down=(185, 28, 28), marker=(17, 24, 39))
DARK = Theme(bg=(9, 12, 18), grid=(26, 35, 51), axis=(107, 114, 128), text=(229, 231, 235), muted=(156, 163, 175),
             wick=(156, 163, 175), up=(34, 197, 94), down=(239, 68, 68), marker=(243, 244, 246))
THEMES = {"light": LIGHT, "dark": DARK}
# drawable colours (Okabe-Ito): zone bands by role (bluish green / reddish purple), the others in order
PALETTE: Tuple[Color, ...] = ((230, 159, 0), (86, 180, 233), (0, 114, 178), (213, 94, 0))
ROLE_COLORS: Dict[str, Color] = {"demand": (0, 158, 115), "supply": (204, 121, 167)}
KIND_RANK = {"band": 0, "box": 0, "bars": 1, "overlay": 2, "pane": 3}


class Canvas(chart_png._Canvas):
    """The Vision chart's canvas plus lines, blended fills and the fuller font."""

    def text(self, x: int, y: int, s: str, c: Color) -> int:
        cursor = x
        for ch in s.upper():
            glyph = GLYPHS.get(ch, GLYPHS["?"] if ch.strip() else GLYPHS[" "])
            for row, bits in enumerate(glyph):
                for col in range(3):
                    if bits & (4 >> col):
                        self.rect(cursor + col * SCALE, y + row * SCALE, cursor + col * SCALE + SCALE - 1, y + row * SCALE + SCALE - 1, c)
            cursor += 4 * SCALE
        return cursor - x

    def line(self, x0: int, y0: int, x1: int, y1: int, c: Color, width: int = 1) -> None:
        dx, dy = abs(x1 - x0), -abs(y1 - y0)
        sx, sy = (1 if x0 < x1 else -1), (1 if y0 < y1 else -1)
        err = dx + dy
        while True:
            for k in range(width):
                self.set(x0, y0 + k, c)
            if x0 == x1 and y0 == y1:
                break
            e2 = 2 * err
            if e2 >= dy:
                err += dy
                x0 += sx
            if e2 <= dx:
                err += dx
                y0 += sy

    def blend(self, x0: int, y0: int, x1: int, y1: int, c: Color, alpha: float) -> None:
        x0, x1 = sorted((x0, x1))
        y0, y1 = sorted((y0, y1))
        x0, x1, y0, y1 = max(0, x0), min(self.width - 1, x1), max(0, y0), min(self.height - 1, y1)
        a = int(round(alpha * 256))
        for y in range(y0, y1 + 1):
            for x in range(x0, x1 + 1):
                i = (y * self.width + x) * 3
                self.px[i] = (self.px[i] * (256 - a) + c[0] * a) >> 8
                self.px[i + 1] = (self.px[i + 1] * (256 - a) + c[1] * a) >> 8
                self.px[i + 2] = (self.px[i + 2] * (256 - a) + c[2] * a) >> 8

    def outline(self, x0: int, y0: int, x1: int, y1: int, c: Color) -> None:
        self.hline(x0, x1, y0, c)
        self.hline(x0, x1, y1, c)
        self.vline(x0, y0, y1, c)
        self.vline(x1, y0, y1, c)


def text_width(s: str) -> int:
    return len(s) * 4 * SCALE


def fit(s: str, width: int) -> str:
    """`s` cut to `width` pixels of the font (an ellipsis-like ".." when cut)."""
    max_chars = max(0, width // (4 * SCALE))
    return s if len(s) <= max_chars else s[:max(0, max_chars - 2)] + ".."


def image_drawables(pack: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The drawables an image shows: the signal-defining kinds first (zone bands, breakout boxes / levels), then candles,
    overlays, then ONE lower pane; at most IMAGE_MAX_DRAWABLES. The rest stay in the caption."""
    ordered = sorted(enumerate(pack.get("drawables") or []), key=lambda item: (KIND_RANK.get(item[1].get("kind"), 9), item[0]))
    out: List[Dict[str, Any]] = []
    pane_taken = False
    for _i, d in ordered:
        if d.get("pane") not in (None, "price"):
            if pane_taken:
                continue
            pane_taken = True
        out.append(d)
        if len(out) >= IMAGE_MAX_DRAWABLES:
            break
    return out


def _finite(values: Sequence[Any]) -> List[float]:
    return [float(v) for v in values if isinstance(v, (int, float)) and v == v]


def render(pack: Dict[str, Any], *, theme: str = "light", width: int = WIDTH, height: int = HEIGHT) -> bytes:
    """The PNG bytes of one evidence pack. Raises ValueError without bars or when the image would exceed MAX_PNG_BYTES."""
    png = draw(pack, theme=theme, width=width, height=height).png()
    if len(png) > MAX_PNG_BYTES:
        raise ValueError(f"evidence image is {len(png)} bytes, over the {MAX_PNG_BYTES} cap")
    return png


def pixels(pack: Dict[str, Any], *, theme: str = "light", width: int = WIDTH, height: int = HEIGHT) -> bytes:
    """The raw RGB pixels (row by row) - what the determinism test pins, independent of the zlib build."""
    return bytes(draw(pack, theme=theme, width=width, height=height).px)


def draw(pack: Dict[str, Any], *, theme: str = "light", width: int = WIDTH, height: int = HEIGHT) -> Canvas:  # noqa: C901
    """The canvas with the pack drawn on it - laid out like the app's chart: the price pane with its price axis on the
    right, the volume pane under it (bars in the up / down colours, faded), the screen's lower pane when it has one, and
    the time axis at the bottom."""
    th = THEMES.get(theme, LIGHT)
    bars = pack.get("bars") or {}
    o, h, l, c = bars.get("o") or [], bars.get("h") or [], bars.get("l") or [], bars.get("c") or []
    count = min(len(o), len(h), len(l), len(c))
    if count == 0:
        raise ValueError("no bars to draw")
    vol = list(bars.get("v") or [])[:count]
    times = list(bars.get("t") or [])[:count]
    shown = image_drawables(pack)
    others = [d for d in shown if d.get("kind") != "band"]
    colors = {d["id"]: ROLE_COLORS.get(str(d.get("role", "")), PALETTE[0]) if d.get("kind") == "band" else PALETTE[others.index(d) % len(PALETTE)]
              for d in shown}
    sub = next((d for d in shown if d.get("pane") not in (None, "price")), None)
    # the bars' own volume pane, unless the screen's lower pane is the volume itself
    vol_pane = sub is None or sub.get("pane") != "volume"
    vol_pane = vol_pane and any(isinstance(v, (int, float)) and v == v and v > 0 for v in vol)
    canvas = Canvas(width, height, th.bg)
    left, right = 10, width - 112
    top = 38
    legend_h = 18
    axis_h = 16
    floor = height - legend_h - 6 - axis_h                    # the lowest pane ends here; the time labels sit under it
    sub_top = floor - SUB_PANE_HEIGHT if sub is not None else floor
    vol_top = (sub_top - (8 if sub is not None else 0)) - VOLUME_PANE_HEIGHT if vol_pane else sub_top
    bottom = vol_top - 8 if (vol_pane or sub is not None) else floor

    # the price range: bars plus everything drawn on the price pane
    prices: List[float] = _finite(h[:count]) + _finite(l[:count])
    for d in shown:
        if d.get("pane") not in (None, "price"):
            continue
        for s in d.get("series") or []:
            prices += _finite(s.get("values") or [])
        prices += _finite([lv.get("value") for lv in d.get("levels") or []])
        prices += _finite([d.get("low"), d.get("high"), d.get("level")])
    lo, hi = min(prices), max(prices)
    if hi <= lo:
        hi, lo = hi * 1.001 + 0.01, lo * 0.999
    pad = (hi - lo) * 0.05
    lo, hi = lo - pad, hi + pad

    def y_of(price: float, y_top: int = top, y_bot: int = bottom, a: float = lo, b: float = hi) -> int:
        return int(round(y_bot - (price - a) / (b - a) * (y_bot - y_top)))

    slot = (right - left - 4) / count

    def x_of(i: int) -> int:
        return int(left + 2 + slot * i + slot / 2)

    # header: symbol, timeframe and the matched bar's time; under it the matched bar's own O / H / L / C / V
    tf = str(pack.get("base_tf") or "")
    head = f"{pack.get('symbol', '')} {tf} {_bar_text(pack)}"
    canvas.text(left, 6, fit(head, right - left - 120), th.text)
    canvas.text(width - text_width("INFORMATIONAL") - 8, 6, "INFORMATIONAL", th.muted)
    j = int(pack.get("matched_index", count - 1))
    if 0 <= j < count:
        canvas.text(left, 20, fit(_ohlc_text(o[j], h[j], l[j], c[j], vol[j] if j < len(vol) else None), right - left), th.muted)

    # the price pane: grid, the price axis with its labels on the right
    ticks: List[Tuple[int, str]] = []
    for i in range(1, 5):
        y = top + (bottom - top) * i // 5
        canvas.hline(left, right, y, th.grid)
        ticks.append((y, _price_text(hi - (hi - lo) * i / 5)))
    canvas.vline(right, top, floor, th.axis)
    canvas.hline(left, right, bottom, th.axis)

    # bands and boxes under the candles
    for d in shown:
        col = colors[d["id"]]
        if d.get("kind") == "band" and d.get("low") is not None and d.get("high") is not None:
            x0 = x_of(int(d.get("from", 0))) - int(slot / 2)
            ya, yb = y_of(float(d["high"])), y_of(float(d["low"]))
            canvas.blend(x0, ya, right - 1, max(ya, yb), col, 0.22)
            canvas.hline(x0, right - 1, ya, col)
            canvas.hline(x0, right - 1, max(ya, yb), col)
        elif d.get("kind") == "box" and d.get("low") is not None and d.get("high") is not None:
            x0 = x_of(int(d.get("from", 0))) - int(slot / 2)
            x1 = x_of(int(d.get("to", count - 1))) + int(slot / 2)
            ya, yb = y_of(float(d["high"])), y_of(float(d["low"]))
            canvas.blend(x0, ya, x1, yb, col, 0.18)
            canvas.outline(x0, ya, x1, yb, col)

    # candles in the chart's up / down colours (the same as the Charts page)
    body = max(1, int(slot * 0.6))
    for i in range(count):
        cx = x_of(i)
        color = th.up if c[i] >= o[i] else th.down
        canvas.vline(cx, y_of(h[i]), y_of(l[i]), color)
        y0, y1 = y_of(max(o[i], c[i])), y_of(min(o[i], c[i]))
        x0 = cx - body // 2
        canvas.rect(x0, y0, x0 + body - 1, max(y0, y1), color)

    labels: List[Tuple[int, str, Color]] = []
    for d in shown:
        col = colors[d["id"]]
        kind = d.get("kind")
        if kind == "bars":
            for i in d.get("index") or []:
                if 0 <= int(i) < count:
                    cx = x_of(int(i))
                    canvas.outline(cx - body // 2 - 3, y_of(h[int(i)]) - 3, cx - body // 2 + body + 2, y_of(l[int(i)]) + 3, col)
            if d.get("level") is not None:
                labels.append((y_of(float(d["level"])), f"{float(d['level']):.2f}", col))
        elif kind in ("box",) and d.get("level") is not None:
            y = y_of(float(d["level"]))
            canvas.hline(x_of(int(d.get("from", 0))), right - 1, y, col, dash=6)
            labels.append((y, f"{float(d['level']):.2f}", col))
        elif kind == "band":
            edge = float(d["high"]) if d.get("role") == "demand" else float(d["low"])
            labels.append((y_of(edge), f"{edge:.2f}", col))
        elif kind == "overlay":
            _series(canvas, d, x_of, y_of, count, col)
            for lv in d.get("levels") or []:
                y = y_of(float(lv["value"]))
                canvas.hline(left, right - 1, y, col, dash=6)
                labels.append((y, str(lv.get("value_text", "")), col))
            if d.get("marker"):
                _marker(canvas, x_of(int(d["marker"]["index"])), bottom - 2, col)

    # the matched bar
    if 0 <= j < count:
        mx = x_of(j)
        for y in range(top, bottom, 4):
            canvas.vline(mx, y, min(bottom, y + 1), th.marker)
        _marker(canvas, mx, bottom - 2, th.marker)

    # the volume pane: each bar's volume in its candle's colour, faded as on the Charts page
    if vol_pane:
        v_top, v_bot = vol_top, vol_top + VOLUME_PANE_HEIGHT
        vmax = max(_finite(vol) or [1.0]) or 1.0
        for i, v in enumerate(vol):
            if isinstance(v, (int, float)) and v == v and v > 0:
                yv = int(round(v_bot - float(v) / vmax * (v_bot - v_top - 2)))
                canvas.blend(x_of(i) - body // 2, yv, x_of(i) - body // 2 + body - 1, v_bot, th.up if c[i] >= o[i] else th.down, 0.45)
        canvas.hline(left, right, v_bot, th.axis)
        canvas.rect(left, v_top - 1, left + 3 + text_width("VOLUME"), v_top + 5 * SCALE, th.bg)
        canvas.text(left + 2, v_top, "VOLUME", th.muted)
        ticks.append((v_top + 5, _volume_text(vmax)))
        if 0 <= j < count:
            for y in range(v_top, v_bot, 4):
                canvas.vline(x_of(j), y, min(v_bot, y + 1), th.marker)

    # the screen's lower pane
    if sub is not None:
        col = colors[sub["id"]]
        p_top, p_bot = sub_top + 2, floor
        vals: List[float] = []
        for s in sub.get("series") or []:
            vals += _finite(s.get("values") or [])
        vals += _finite([lv.get("value") for lv in sub.get("levels") or []])
        if vals:
            a, b = min(vals), max(vals)
            if any((s.get("style") == "histogram") for s in sub.get("series") or []):
                a = min(a, 0.0)
            if b <= a:
                b, a = b + 1.0, a - 1.0
            pad2 = (b - a) * 0.08
            a, b = a - pad2, b + pad2

            def y2(v: float) -> int:
                return y_of(v, p_top, p_bot, a, b)
            canvas.hline(left, right, p_bot, th.axis)
            for si, s in enumerate(sub.get("series") or []):
                scol = col if si == 0 else PALETTE[(list(colors).index(sub["id"]) + 3) % len(PALETTE)]
                values = s.get("values") or []
                if s.get("style") == "histogram":
                    for i, v in enumerate(values[:count]):
                        if isinstance(v, (int, float)) and v == v:
                            canvas.rect(x_of(i) - body // 2, y2(float(v)), x_of(i) - body // 2 + body - 1, y2(max(a, 0.0)), scol)
                else:
                    _polyline(canvas, values, x_of, y2, count, scol)
            for lv in sub.get("levels") or []:
                y = y2(float(lv["value"]))
                canvas.hline(left, right - 1, y, th.text, dash=4)
                canvas.text(right + 4, y - 5, fit(f"{lv.get('value_text', '')}", width - right - 6), th.text)
            canvas.text(left + 2, p_top, fit(str(sub.get("label", "")), right - left - 8), col)
            if 0 <= j < count:
                for y in range(p_top, p_bot, 4):
                    canvas.vline(x_of(j), y, min(p_bot, y + 1), th.marker)

    # the time axis: a few bar times under the lowest pane, in IST (intraday with the time of day)
    canvas.hline(left, right, floor, th.axis)
    _time_axis(canvas, times, x_of, floor, left, right, str(pack.get("base_tf") or ""), th)

    # the price axis labels (muted), then the drawables' own labels over them (no overlaps among those)
    for y, text in ticks:
        canvas.text(right + 4, y - 5, fit(text, width - right - 6), th.muted)
    used: List[int] = []
    for y, text, col in sorted(labels):
        ly = y - 5
        while any(abs(ly - u) < 12 for u in used):
            ly += 12
        used.append(ly)
        canvas.rect(right + 3, ly - 1, width - 2, ly + 5 * SCALE, th.bg)
        canvas.text(right + 4, ly, fit(text, width - right - 6), col)

    # legend: one swatch + label per drawable shown
    x = left
    ly = height - legend_h
    for d in shown:
        col = colors[d["id"]]
        label = fit(str(d.get("label", "")), 180)
        if x + 14 + text_width(label) > width - 8:
            break
        canvas.rect(x, ly + 1, x + 8, ly + 9, col)
        x += 12
        x += canvas.text(x, ly, label, th.text) + 12

    return canvas


def _price_text(p: float) -> str:
    return f"{p:,.2f}" if abs(p) < 100000 else f"{p:,.0f}"


def _volume_text(v: float) -> str:
    for size, unit in ((1e7, "CR"), (1e5, "L"), (1e3, "K")):
        if v >= size:
            return f"{v / size:.1f}{unit}"
    return f"{v:.0f}"


def _ohlc_text(o: Any, h: Any, l: Any, c: Any, v: Any) -> str:
    parts = [f"{k} {_price_text(float(x))}" for k, x in (("O", o), ("H", h), ("L", l), ("C", c)) if isinstance(x, (int, float))]
    if isinstance(v, (int, float)) and v == v:
        parts.append(f"V {_volume_text(float(v))}")
    return "  ".join(parts)


def _time_labels(times: Sequence[Any], base_tf: str) -> List[str]:
    """Each bar's start in IST: "10 OCT 11:15" for intraday bars, "19 JUL 2026" for a day, week or month."""
    import pandas as pd
    from app.screener.evidence import INTRADAY_TFS
    out: List[str] = []
    for t in times:
        try:
            ts = pd.Timestamp(t)
            ts = (ts.tz_localize("UTC") if ts.tzinfo is None else ts).tz_convert("Asia/Kolkata")
        except (ValueError, TypeError):
            out.append("")
            continue
        out.append(ts.strftime("%d %b %H:%M") if base_tf in INTRADAY_TFS else ts.strftime("%d %b %Y"))
    return out


def _time_axis(canvas: Canvas, times: Sequence[Any], x_of: Any, y: int, left: int, right: int, base_tf: str, th: Theme) -> None:
    labels = _time_labels(times, base_tf)
    if not labels:
        return
    step = max(1, -(-len(labels) * text_width("00 XXX 00:00") // max(1, (right - left) * 3 // 4)))
    last_end = right + 100
    for i in range(len(labels) - 1, -1, -step):              # from the newest bar back, so the matched bar's time shows
        text = labels[i]
        if not text:
            continue
        x = x_of(i)
        x0 = min(right - text_width(text), max(left, x - text_width(text) // 2))
        if i != len(labels) - 1 and x0 + text_width(text) > last_end - 8:
            continue
        canvas.vline(x, y, y + 3, th.axis)
        canvas.text(x0, y + 5, text, th.muted)
        last_end = x0


def _bar_text(pack: Dict[str, Any]) -> str:
    from app.screener.evidence import bar_time_text
    try:
        return bar_time_text(pack)
    except (KeyError, ValueError, TypeError):
        return ""


def _marker(canvas: Canvas, x: int, y: int, col: Color) -> None:
    for k in range(4):
        canvas.hline(x - k, x + k, y - 3 + k, col)


def _polyline(canvas: Canvas, values: Sequence[Any], x_of: Any, y_of: Any, count: int, col: Color) -> None:
    prev: Optional[Tuple[int, int]] = None
    for i, v in enumerate(list(values)[:count]):
        if not (isinstance(v, (int, float)) and v == v):
            prev = None
            continue
        pt = (x_of(i), y_of(float(v)))
        if prev is not None:
            canvas.line(prev[0], prev[1], pt[0], pt[1], col, width=2)
        else:
            canvas.rect(pt[0], pt[1], pt[0], pt[1] + 1, col)
        prev = pt


def _series(canvas: Canvas, d: Dict[str, Any], x_of: Any, y_of: Any, count: int, col: Color) -> None:
    for si, s in enumerate(d.get("series") or []):
        scol = col if si == 0 else tuple(max(0, int(ch * 0.6)) for ch in col)
        _polyline(canvas, s.get("values") or [], x_of, y_of, count, scol)  # type: ignore[arg-type]


__all__ = ["render", "pixels", "draw", "Canvas", "image_drawables", "IMAGE_MAX_DRAWABLES", "MAX_PNG_BYTES", "THEMES", "PALETTE", "GLYPHS", "WIDTH", "HEIGHT", "VOLUME_PANE_HEIGHT", "LIGHT", "DARK"]
