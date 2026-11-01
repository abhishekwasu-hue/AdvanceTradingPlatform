"""M77 SX-1: the server palette (app/study/palette.py) is the frontend's design tokens, never its own colours.

Reads frontend/src/styles/tokens.css, resolves each token for the light theme (and the light theme with the
colour-vision setting on) by the CSS cascade - specificity, then source order - and fails when a server colour drifts
from it. Also pins the area alphas to the ones the app draws with (frontend/src/chartAnalyze/shapes.ts).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.study import palette as P

FRONTEND = Path(__file__).resolve().parents[2] / "frontend" / "src"
TOKENS = FRONTEND / "styles" / "tokens.css"
SHAPES = FRONTEND / "chartAnalyze" / "shapes.ts"

ROOT = ":root"
LIGHT = ':root[data-theme="light"]'
CVD = ':root[data-cvd="on"]'
LIGHT_CVD = ':root[data-theme="light"][data-cvd="on"]'
SPECIFICITY = {ROOT: 1, LIGHT: 2, CVD: 2, LIGHT_CVD: 3}


def _blocks() -> list[tuple[str, dict[str, tuple[int, int, int]]]]:
    """(selector, {token: rgb}) for each top-level rule of tokens.css, in source order; @media blocks are skipped."""
    css = re.sub(r"/\*.*?\*/", "", TOKENS.read_text(encoding="utf-8"), flags=re.S)
    css = re.sub(r"@media[^{]*\{(?:[^{}]*\{[^{}]*\})*[^{}]*\}", "", css)
    out = []
    for selector, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
        values = {}
        for name, raw in re.findall(r"--([\w-]+)\s*:\s*([^;]+);", body):
            parts = raw.split()
            if len(parts) == 3 and all(p.isdigit() for p in parts):
                values[name] = (int(parts[0]), int(parts[1]), int(parts[2]))
        out.append((" ".join(selector.split()), values))
    return out


def _resolve(selectors: set[str]) -> dict[str, tuple[int, int, int]]:
    applying = [(SPECIFICITY[sel], i, vals) for i, (sel, vals) in enumerate(_blocks()) if sel in selectors]
    resolved: dict[str, tuple[int, int, int]] = {}
    for _spec, _i, vals in sorted(applying, key=lambda x: (x[0], x[1])):
        resolved.update(vals)
    return resolved


@pytest.mark.parametrize(("server", "selectors"), [
    (P.TOKENS_LIGHT, {ROOT, LIGHT}),
    (P.TOKENS_LIGHT_CVD, {ROOT, LIGHT, CVD, LIGHT_CVD}),
], ids=["light", "light-cvd"])
def test_server_colours_equal_the_frontend_tokens(server: dict, selectors: set[str]) -> None:
    css = _resolve(selectors)
    for name, rgb in server.items():
        assert name in css, f"--{name} is not a token in tokens.css"
        assert rgb == css[name], f"--{name}: server {rgb} != tokens.css {css[name]} - update app/study/palette.py"


def test_the_resolver_sees_the_cascade() -> None:
    """A sanity check on the parser itself: the light background is white and colour-blind mode changes up / down."""
    light, cvd = _resolve({ROOT, LIGHT}), _resolve({ROOT, LIGHT, CVD, LIGHT_CVD})
    assert light["chart-bg"] == (255, 255, 255)
    assert light["up"] != cvd["up"] and light["down"] != cvd["down"]
    assert _resolve({ROOT})["chart-bg"] != light["chart-bg"]          # the dark default is not what the server prints


def test_area_alphas_match_the_app() -> None:
    shapes = SHAPES.read_text(encoding="utf-8")
    assert f'area.state === "broken" ? {P.AREA_BROKEN_FILL_ALPHA} : {P.AREA_FILL_ALPHA}' in shapes
    assert f"colour(s.token, {P.AREA_EDGE_ALPHA})" in FRONTEND.joinpath("chartAnalyze", "exportImage.ts").read_text(encoding="utf-8")


@pytest.mark.parametrize("cvd", [False, True])
def test_palette_is_print_safe_and_by_meaning(cvd: bool) -> None:
    pal = P.palette(cvd=cvd)
    tokens = P.TOKENS_LIGHT_CVD if cvd else P.TOKENS_LIGHT
    assert pal.background == (255, 255, 255)
    assert pal.buyer == tokens["up"] and pal.seller == tokens["down"]
    assert pal.text == tokens["fg"] and pal.grid == tokens["chart-grid"]
    assert pal.buyer_band == P.blend(tokens["up"], 0.16, (255, 255, 255))
    assert min(pal.seller_band) > 200 and min(pal.buyer_band) > 200        # a light band: the text on it stays readable
    assert pal.buyer != pal.seller


def test_blend() -> None:
    assert P.blend((0, 0, 0), 0.5, (255, 255, 255)) == (128, 128, 128)
    assert P.blend((185, 28, 28), 1.0, (255, 255, 255)) == (185, 28, 28)
