"""M99 P3: the "Volume spread" scanners - a screen library source of its own, `vsa`, behind the `vsa_pack` flag (off by
default). One scanner per reading of app/price_action/vsa.py, each a ScreenQL text on the ONE function `VSA(...)` - the
same evaluation the chart layer (app/charts/vsa_layer.py) and the J templates (so the AI Trading Bot) read, so the list,
the chart and the bot never disagree.

* Every number is an editable `$parameter` with its reference value ("Reference value - edit before use"); the run takes
  the values the user sets. Background (the structure before the bar) is a switch, on by default as the readings are
  described; next-bar confirmation is a switch, off by default.
* Volume: a stock's own volume; an index's near-month future's volume (upstream, app/market_data/index_volume.py). A
  symbol without volume data is "not checked: volume not available", never a match and never a silent no-match.
* Closed bars only; the screen's own timeframe (reference Daily) - any timeframe the screener runs works.

Signals only (ADR-0006): a scanner lists matches; nothing here places, changes or blocks an order. No buy / sell /
target / stop wording in names or cards (the taxonomy lint checks it).
"""
from __future__ import annotations

from typing import Any

from app.price_action import vsa
from app.screener import taxonomy as tx
from app.screener.intraday import FOOTER, NOT_SET, REF_LABEL, Param, Scanner
from app.screener.registry import VSA_FLAG, VSA_KWARGS

SOURCE = "vsa"
FLAG = VSA_FLAG
ALSO: tuple[str, ...] = ()
CHIP = "Volume spread"
BASE_TF = "1d"

_REF = {name: ref for name, _, ref, _ in vsa.SETTINGS_TABLE}
_DOCS: dict[str, str] = {
    "n": "Bars before each bar its spread and volume are measured against.",
    "narrow": "A spread below this multiple of the average range is narrow.",
    "wide": "A spread above this multiple of the average range is wide.",
    "ultra": "A spread above this multiple of the average range is ultra-wide.",
    "vol_low": "Volume below this multiple of the average is low (shown in Why?).",
    "vol_high": "Volume above this multiple of the average is high.",
    "vol_ultra": "Volume above this multiple of the average is ultra-high.",
    "part": "The close's place in the bar: below this share of the range is the low third, above 1 minus it the high third.",
    "prev": "Lower volume means lower than each of this many bars before.",
    "new_bars": "Bars before whose highs or lows count as new ground.",
    "trend_bars": "The structure before the bar: these bars against the same number before them (higher high and higher "
                  "low = rising; lower high and lower low = falling).",
    "bg_bars": "Bars before in which a climax counts for the background.",
    "area_bars": "Bars before searched for a high-volume bar whose range the test reaches.",
    "area_tol": "How near that bar's range the low must come, in average ranges.",
    "shake_bars": "Bars after the wide bar in which the quiet return must come.",
    "background": "Count the reading only with the structure before it that the reading needs.",
    "confirm": "Show the reading on the next bar, only when it closes higher (strength) or lower (weakness).",
}
_COMMON = ("n", "trend_bars", "background", "confirm")
_OWN: dict[str, tuple[str, ...]] = {
    "no_demand": ("narrow", "prev", "bg_bars"),
    "no_supply": ("wide", "part", "prev", "bg_bars"),
    "test": ("area_bars", "area_tol", "vol_high", "part", "prev", "bg_bars"),
    "failed_test": ("area_bars", "area_tol", "vol_high", "part", "bg_bars"),
    "up_thrust": ("wide", "new_bars", "part", "vol_high"),
    "reverse_up_thrust": ("wide", "new_bars", "part", "vol_high"),
    "stopping_volume": ("vol_ultra", "part"),
    "selling_climax": ("ultra", "vol_ultra", "part"),
    "buying_climax": ("narrow", "ultra", "vol_ultra", "new_bars", "part"),
    "shake_out": ("wide", "prev", "shake_bars"),
    "falling_pressure": ("wide", "vol_high", "prev", "new_bars", "part"),
    "absorption": ("wide", "vol_high", "vol_ultra", "new_bars", "part"),
    "effort_vs_result": ("narrow", "wide", "vol_high", "prev", "bg_bars"),
}
DEFINITIONS: dict[str, str] = {
    "no_demand": "An up bar on a narrow spread with volume lower than the bars before; background weak.",
    "no_supply": "A down bar, spread not wide, volume lower than the bars before, closing off its low; background strong.",
    "test": "A down bar into the range of an earlier high-volume bar on lower volume, closing in its high third.",
    "failed_test": "The test's shape on high volume: the area is still active.",
    "up_thrust": "A wide bar into new highs that closes in its low third, after a rise.",
    "reverse_up_thrust": "A wide bar into new lows that closes in its high third on high volume, after a fall.",
    "stopping_volume": "A down bar on ultra-high volume closing high, or closing low with the next bar level or higher, after a fall.",
    "selling_climax": "An ultra-wide down bar on ultra-high volume closing off its low, after a fall.",
    "buying_climax": "An up bar on ultra-high volume closing off its high, ultra-wide or narrow into new highs, after a rise.",
    "shake_out": "A wide down bar after a rise, then a quiet bar back inside its range that holds its low.",
    "falling_pressure": "A wide down bar on lower volume closing low (few sellers), or below the lows closing high, after a fall.",
    "absorption": "A wide bar on ultra-high volume closing high at the end of a fall, or a high-volume close above the highs.",
    "effort_vs_result": "High volume on a narrow spread, or a wide spread on lower volume: effort and result disagree.",
}
DIRECTION = {vsa.STRENGTH: "long", vsa.WEAKNESS: "short", None: "both"}


def _params(name: str) -> tuple[Param, ...]:
    keys = (*_COMMON, *_OWN[name])
    return tuple(Param(k, _REF[VSA_KWARGS[k]], _DOCS[k], True) for k in keys)


def _text(name: str, params: tuple[Param, ...]) -> str:
    return f'VSA("{name}", ' + ", ".join(f"{p.name}=${p.name}" for p in params) + ")"


def _scanner(name: str) -> Scanner:
    params = _params(name)
    return Scanner(f"vsa.{name}", vsa.NAMES[name], DEFINITIONS[name], _text(name, params), BASE_TF, params)


SCANNERS: tuple[Scanner, ...] = tuple(_scanner(name) for name in vsa.DETECTORS)
BY_ID: dict[str, Scanner] = {s.id: s for s in SCANNERS}


def scanner(ident: str) -> Scanner | None:
    return BY_ID.get(ident)


def payload() -> dict[str, Any]:
    return {"scanners": [s.as_dict() for s in SCANNERS], "footer": FOOTER, "ref_label": REF_LABEL, "not_set": NOT_SET, "chip": CHIP}


def _ids() -> list[str]:
    return [s.id for s in SCANNERS]


tx.register_source(SOURCE, _ids, flag=FLAG, also=ALSO, note=FOOTER)

__all__ = ["SOURCE", "FLAG", "ALSO", "CHIP", "BASE_TF", "SCANNERS", "BY_ID", "DEFINITIONS", "DIRECTION", "scanner", "payload"]
