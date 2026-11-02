"""M77 SX-4: the study as PNG pictures (PD-3 / PD-4), rebuilt from the stored snapshot - same study = same bytes.

* `chart_png(snap, tf)` - "Save as image" of one chart: the timeframe's chart at the visible range he saved, with a
  header line (symbol, timeframe, closed bars up to, IST) and the information-only line below it.
* `study_png(snap)` - the phone-friendly single column: every timeframe studied, one chart under the other (1080 px
  wide), each with its header, and the information-only line and the data sources at the foot.
Pixels from app/study/chart.py (the same renderer as the PDF's charts); PNG written without any time or text chunk, so
the bytes depend only on the snapshot and his colour-vision setting. Over STUDY_PNG_MAX_BYTES: a plain refusal.
"""
from __future__ import annotations

import io
from collections.abc import Mapping
from typing import Any

from PIL import Image, ImageDraw

from app.charts.read_chart import TF_WORDS
from app.study import fonts
from app.study import pages as P
from app.study import snapshot as S
from app.study.chart import render_chart
from app.study.palette import Palette, palette

ONE_W, ONE_H = 1600, 900            # one chart ("Save as image")
PHONE_W, PHONE_CHART_H = 1080, 760  # the single column (a phone screen at 3x)
PAD = 32
HEAD, FOOT = 26, 20


class PngTooLarge(ValueError):
    """The picture is over STUDY_PNG_MAX_BYTES; the message is the plain sentence the member sees."""


class NoSuchChart(LookupError):
    """The timeframe is not one the study holds a chart for."""


def _encode(img: Image.Image, limit: int) -> bytes:
    from app.core import config
    buf = io.BytesIO()
    img.save(buf, format="PNG", compress_level=9, optimize=False)
    data = buf.getvalue()
    limit = limit or config.STUDY_PNG_MAX_BYTES
    if len(data) > limit:
        raise PngTooLarge(f"This picture would be larger than {limit // (1024 * 1024)} MB. Save the study again with fewer "
                          "timeframes or a shorter visible range.")
    return data


def _header(snap: Mapping[str, Any], tf: str) -> str:
    inst = snap.get("instrument") or {}
    chart = P.chart_inputs(snap, tf)["chart"]
    return (f"{inst.get('symbol') or P.NOT_AVAILABLE} - {TF_WORDS.get(tf, tf)} chart - closed bars up to "
            f"{P.ist(chart.get('as_of'))}")


def _chart(snap: Mapping[str, Any], tf: str, pal: Palette, w: int, h: int, font_px: int) -> Image.Image:
    parts = P.chart_inputs(snap, tf)
    img, _ = render_chart(parts["chart"], areas=parts["areas"], drawings=parts["drawings"], protected=parts["protected"],
                          reference=parts["reference"], pal=pal, width=w, height=h, font_px=font_px)
    return img


def _lines(draw: ImageDraw.ImageDraw, x: float, y: float, text: str, size: int, colour: Any, max_w: float) -> float:
    font = fonts.regular(size)
    for line in P.wrap(text, size, max_w):
        draw.text((x, y), line, font=font, fill=colour)
        y += size * P.LINE_GAP
    return y


def chart_png(snap: Mapping[str, Any], tf: str, *, cvd: bool = False, limit: int = 0) -> bytes:
    """One chart of the study as a PNG (pure)."""
    if tf not in (snap.get("timeframes") or []) or "charts" not in (snap.get("data") or {}):
        raise NoSuchChart(tf)
    pal = palette(cvd=cvd)
    w = ONE_W + 2 * PAD
    h = PAD + int(HEAD * P.LINE_GAP) + 12 + ONE_H + 12 + int(2 * FOOT * P.LINE_GAP) + PAD
    img = Image.new("RGB", (w, h), pal.background)
    draw = ImageDraw.Draw(img)
    _lines(draw, PAD, PAD, _header(snap, tf), HEAD, pal.text, ONE_W)
    top = PAD + int(HEAD * P.LINE_GAP) + 12
    img.paste(_chart(snap, tf, pal, ONE_W, ONE_H, 18), (PAD, top))
    _lines(draw, PAD, top + ONE_H + 12, str((snap.get("footer") or {}).get("text") or S.INFO_LINE), FOOT, pal.text_muted, ONE_W)
    return _encode(img, limit)


def study_png(snap: Mapping[str, Any], *, cvd: bool = False, limit: int = 0) -> bytes:
    """Every chart of the study in one phone-friendly column (pure)."""
    if "charts" not in (snap.get("data") or {}):
        raise NoSuchChart("charts")
    pal = palette(cvd=cvd)
    inner = PHONE_W - 2 * PAD
    tfs = list(snap.get("timeframes") or [])
    foot = P.footer_lines(snap)
    title_h = int(34 * P.LINE_GAP) * 2
    head_h = int(HEAD * P.LINE_GAP) * 2 + 8
    foot_h = int(FOOT * P.LINE_GAP) * 8
    h = PAD + title_h + len(tfs) * (head_h + PHONE_CHART_H + 24) + foot_h + PAD
    img = Image.new("RGB", (PHONE_W, h), pal.background)
    draw = ImageDraw.Draw(img)
    inst = snap.get("instrument") or {}
    y = _lines(draw, PAD, PAD, f"Study: {inst.get('symbol') or P.NOT_AVAILABLE}", 34, pal.text, inner)
    y = _lines(draw, PAD, y, P.ist(snap.get("created_at")), HEAD, pal.text_muted, inner) + 8
    for tf in tfs:
        y = _lines(draw, PAD, y, _header(snap, tf), HEAD, pal.text, inner) + 8
        img.paste(_chart(snap, tf, pal, inner, PHONE_CHART_H, 22), (PAD, int(y)))
        y += PHONE_CHART_H + 24
    y = _lines(draw, PAD, y, foot[0], FOOT, pal.text, inner)
    y = _lines(draw, PAD, y, foot[1], FOOT - 2, pal.text_muted, inner)
    return _encode(img.crop((0, 0, PHONE_W, min(h, int(y) + PAD))), limit)


__all__ = ["chart_png", "study_png", "PngTooLarge", "NoSuchChart", "ONE_W", "ONE_H", "PHONE_W"]
