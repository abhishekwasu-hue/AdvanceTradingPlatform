"""M77 SX-1: the server palette for rendered images and PDFs - the app's own colours, print-safe.

Every colour is a design token of the frontend's LIGHT theme (frontend/src/styles/tokens.css, `:root` +
`:root[data-theme="light"]`; with the user's colour-vision setting also `[data-cvd="on"]`), copied here because the
backend image does not carry the frontend. tests/test_m77_sx1_palette.py reads tokens.css and fails when any value here
drifts from it. Light background, so a printed page needs no ink for the background.

Areas as the app draws them (frontend/src/chartAnalyze/shapes.ts): a buyer area in the `up` colour, a seller area in
`down`, filled at alpha 0.16 (a broken area 0.06), its edge at 0.9 - here blended over the white chart background, so a
PNG or PDF page needs no transparency.
"""
from __future__ import annotations

from dataclasses import dataclass

Rgb = tuple[int, int, int]

# token name (without "--") -> RGB, as tokens.css resolves it for the light theme
TOKENS_LIGHT: dict[str, Rgb] = {
    "chart-bg": (255, 255, 255),
    "surface-1": (255, 255, 255),
    "surface-2": (238, 241, 246),
    "border": (211, 217, 227),
    "fg": (15, 23, 42),
    "fg-muted": (71, 85, 105),
    "chart-text": (71, 85, 105),
    "chart-grid": (229, 233, 240),
    "chart-axis": (211, 217, 227),
    "up": (21, 128, 61),
    "down": (185, 28, 28),
    "up-tint": (236, 253, 243),
    "down-tint": (254, 242, 242),
    "warn": (180, 83, 9),
    "brand": (37, 99, 235),
}

# the same tokens with `data-cvd="on"` (red-green colour blindness: profit blue, loss orange); unchanged ones repeat
TOKENS_LIGHT_CVD: dict[str, Rgb] = {
    **TOKENS_LIGHT,
    "up": (0, 114, 178),
    "down": (194, 65, 12),
    "up-tint": (235, 245, 255),
    "down-tint": (255, 244, 232),
    "warn": (110, 90, 10),
}

AREA_FILL_ALPHA = 0.16
AREA_BROKEN_FILL_ALPHA = 0.06
AREA_EDGE_ALPHA = 0.9


def blend(colour: Rgb, alpha: float, background: Rgb) -> Rgb:
    """`colour` at `alpha` over an opaque `background`, as the browser composites it (rounded per channel)."""
    r, g, b = (round(c * alpha + bg * (1 - alpha)) for c, bg in zip(colour, background, strict=True))
    return (r, g, b)


@dataclass(frozen=True)
class Palette:
    background: Rgb          # chart and page background (white: print-safe)
    surface: Rgb             # a table's header / zebra row
    border: Rgb
    text: Rgb
    text_muted: Rgb
    chart_text: Rgb
    grid: Rgb
    axis: Rgb
    buyer: Rgb               # buyer area line / text (the app's `up`)
    seller: Rgb              # seller area line / text (the app's `down`)
    buyer_tint: Rgb          # a buyer row's soft background in a table
    seller_tint: Rgb
    buyer_band: Rgb          # a buyer area's band fill on a chart
    seller_band: Rgb
    buyer_band_broken: Rgb
    seller_band_broken: Rgb
    buyer_edge: Rgb
    seller_edge: Rgb
    warn: Rgb
    brand: Rgb


def palette(*, cvd: bool = False) -> Palette:
    """The print palette; `cvd` = the user's colour-vision setting is on."""
    t = TOKENS_LIGHT_CVD if cvd else TOKENS_LIGHT
    bg = t["chart-bg"]
    return Palette(
        background=bg, surface=t["surface-2"], border=t["border"], text=t["fg"], text_muted=t["fg-muted"],
        chart_text=t["chart-text"], grid=t["chart-grid"], axis=t["chart-axis"],
        buyer=t["up"], seller=t["down"], buyer_tint=t["up-tint"], seller_tint=t["down-tint"],
        buyer_band=blend(t["up"], AREA_FILL_ALPHA, bg), seller_band=blend(t["down"], AREA_FILL_ALPHA, bg),
        buyer_band_broken=blend(t["up"], AREA_BROKEN_FILL_ALPHA, bg),
        seller_band_broken=blend(t["down"], AREA_BROKEN_FILL_ALPHA, bg),
        buyer_edge=blend(t["up"], AREA_EDGE_ALPHA, bg), seller_edge=blend(t["down"], AREA_EDGE_ALPHA, bg),
        warn=t["warn"], brand=t["brand"],
    )


__all__ = ["AREA_BROKEN_FILL_ALPHA", "AREA_EDGE_ALPHA", "AREA_FILL_ALPHA", "TOKENS_LIGHT", "TOKENS_LIGHT_CVD",
           "Palette", "blend", "palette"]
