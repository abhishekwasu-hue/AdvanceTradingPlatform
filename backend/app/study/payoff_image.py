"""M77 SX-6: the trade plan's payoff picture for the study PDF (PD-2.7 "payoff image, what-if overlays").

Drawn from the snapshot's stored curve only (the Options Builder engine's own numbers, app/study/plan_sections.py): the
P&L at expiry (dark) and now (grey) over the engine's price grid, the zero line, the study's reference price, and a
marker at each scenario price (the next areas' edges and his what-if prices). Pure and deterministic; light, print-safe.
"""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

from PIL import Image, ImageDraw

from app.study import fonts
from app.study.chart import _dashed, nice_step
from app.study.numfmt import format_number
from app.study.palette import Palette


def _series(values: Sequence[Any] | None, n: int) -> list[float | None]:
    out: list[float | None] = []
    for v in list(values or [])[:n]:
        out.append(float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(float(v)) else None)
    return out + [None] * (n - len(out))


def render_payoff(payoff: Mapping[str, Any], *, spot: float | None, marks: Sequence[Mapping[str, Any]], pal: Palette, width: int,
                  height: int, font_px: int = 15) -> Image.Image:
    img = Image.new("RGB", (width, height), pal.background)
    draw = ImageDraw.Draw(img)
    font = fonts.regular(font_px)
    draw.rectangle((0, 0, width - 1, height - 1), outline=pal.border)
    prices = [p for p in _series(payoff.get("prices"), len(payoff.get("prices") or [])) if p is not None]
    expiry = _series(payoff.get("expiry"), len(prices))
    today = _series(payoff.get("today"), len(prices))
    values = [v for v in expiry + today if v is not None]
    if len(prices) < 2 or not values:
        draw.text((width / 2, height / 2), "Not available: no payoff curve in this study.", font=font, fill=pal.text_muted, anchor="mm")
        return img
    axis_w = int(font.getlength("-00,00,000")) + 20
    foot = font_px + 16
    pw, ph = width - axis_w, height - foot
    lo, hi = min(values + [0.0]), max(values + [0.0])
    if hi - lo < 1e-9:
        lo, hi = lo - 1, hi + 1
    pad = (hi - lo) * 0.08
    lo, hi = lo - pad, hi + pad
    x0, x1 = prices[0], prices[-1]

    def px(p: float) -> float:
        return (p - x0) / (x1 - x0) * (pw - 1)

    def py(v: float) -> float:
        return (hi - v) / (hi - lo) * (ph - 1)

    step = nice_step(hi - lo, 5)
    tick = math.ceil(lo / step) * step
    while tick <= hi:
        y = py(tick)
        draw.line((0, y, pw, y), fill=pal.grid)
        draw.text((pw + 8, y), format_number(tick, 0), font=font, fill=pal.chart_text, anchor="lm")
        tick += step
    xstep = nice_step(x1 - x0, 6)
    xt = math.ceil(x0 / xstep) * xstep
    while xt <= x1:
        draw.text((px(xt), ph + 8), format_number(xt, 0), font=font, fill=pal.chart_text, anchor="mt")
        xt += xstep
    draw.line((0, py(0.0), pw, py(0.0)), fill=pal.axis, width=2)
    for m in marks:
        p = m.get("price")
        if isinstance(p, (int, float)) and x0 <= p <= x1:
            colour = pal.seller if m.get("role") == "supply" else pal.buyer if m.get("role") == "demand" else pal.warn
            _dashed(draw, px(p), 0, px(p), ph, colour, 1, "dotted")
    if isinstance(spot, (int, float)) and x0 <= spot <= x1:
        _dashed(draw, px(spot), 0, px(spot), ph, pal.brand, 2, "dashed")

    def line(series: list[float | None], colour: Any, w: int) -> None:
        pts = [(px(p), py(v)) for p, v in zip(prices, series, strict=False) if v is not None]
        if len(pts) > 1:
            draw.line(pts, fill=colour, width=w, joint="curve")

    line(today, pal.text_muted, 2)
    line(expiry, pal.text, 3)
    draw.line((pw, 0, pw, ph), fill=pal.axis)
    return img


__all__ = ["render_payoff"]
