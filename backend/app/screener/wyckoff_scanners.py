"""M99 P4: the "Wyckoff" scanners - a screen library source of its own, `wyckoff`, behind the `wyckoff_pack` flag (off by
default). Each scanner is a ScreenQL text on the ONE function `Wyckoff(...)` - the same evaluation the chart layer
(app/charts/wyckoff_layer.py) and the K templates (so the AI Trading Bot) read, so the list, the chart and the bot never
disagree.

* Every number is an editable `$parameter` with its reference value ("Reference value - edit before use"); the run takes
  the values the user sets.
* Volume: a stock's own volume; an index's near-month future's volume (upstream). A symbol without volume data is "not
  checked: volume not available", never a match and never a silent no-match.
* Closed bars only; the screen's own timeframe (reference 1 hour) - any timeframe the screener runs works.

Signals only (ADR-0006): a scanner lists matches; nothing here places, changes or blocks an order. No buy / sell /
target / stop wording in names or cards (the taxonomy lint checks it).
"""
from __future__ import annotations

from typing import Any

from app.price_action import wyckoff
from app.screener import taxonomy as tx
from app.screener.intraday import FOOTER, NOT_SET, REF_LABEL, Param, Scanner
from app.screener.registry import WYCKOFF_FLAG, WYCKOFF_KWARGS

SOURCE = "wyckoff"
FLAG = WYCKOFF_FLAG
ALSO: tuple[str, ...] = ()
CHIP = "Wyckoff"
BASE_TF = "1h"

_REF = {name: ref for name, _, ref, _ in wyckoff.SETTINGS_TABLE}
_DOCS: dict[str, str] = {
    "n": "Bars before each bar its spread and volume are measured against.",
    "narrow": "A spread below this multiple of the average range is narrow.",
    "wide": "A spread above this multiple of the average range is wide.",
    "ultra": "A spread above this multiple of the average range is ultra-wide (the climax).",
    "vol_low": "Volume below this multiple of the average is low.",
    "vol_high": "Volume above this multiple of the average is high.",
    "vol_ultra": "Volume above this multiple of the average is ultra-high (the climax).",
    "part": "The close's place in the bar: below this share of the range is the low third, above 1 minus it the high third.",
    "prev": "Lower volume means lower than each of this many bars before.",
    "new_bars": "Bars before whose highs count as new ground (a climax narrow into new highs).",
    "trend_bars": "The move before a climax: these bars against the same number before them.",
    "bg_bars": "Bars before in which a climax counts for a test bar's background.",
    "area_bars": "Bars before searched for a high-volume bar whose range a test bar reaches.",
    "area_tol": "How near that bar's range a test bar's low must come, in average ranges.",
    "background": "Count a climax only after the move it ends.",
    "pivot": "A swing needs this many bars on each side; it is known this many bars after its own bar.",
    "swing_k": "A new swing must be this many reference ranges from the last one.",
    "a_bars": "The automatic reaction, then the secondary test, must each come within this many bars.",
    "st_below": "How far past the climax the secondary test may go, as a share of the range height.",
    "b_vs_a": "A poke beyond an edge is a shake only once Phase B has lasted this many times Phase A.",
    "k_back": "Back inside within this many bars: a test or a shake; this many closes beyond: a break.",
    "deep": "A shake this many average ranges beyond the edge is deep.",
    "test_bars": "The shake's test must come within this many bars.",
    "test_zone": "The test stays within this share of the range height from the shaken edge.",
    "mid_share": "The move after the shake must reach this share of the range before the last point.",
    "bu_bars": "The back-up must come within this many bars of the break.",
    "bu_zone": "The back-up reaches within this share of the range height of the broken edge.",
    "sig_vol": "A significant bar's volume is at least this multiple of the average.",
}
_RANGE = ("n", "vol_ultra", "ultra", "trend_bars", "background", "pivot", "swing_k", "a_bars", "st_below")
_EDGE = (*_RANGE, "b_vs_a", "k_back")
_OWN: dict[str, tuple[str, ...]] = {
    "climax": ("n", "ultra", "vol_ultra", "part", "trend_bars", "background"),
    "ar": _RANGE,
    "st": _RANGE,
    "shake": (*_EDGE, "deep", "wide", "vol_high"),
    "shake_test": (*_EDGE, "test_bars", "test_zone", "narrow", "wide", "prev"),
    "last_point": (*_EDGE, "mid_share"),
    "break": _EDGE,
    "back_up": (*_EDGE, "bu_bars", "bu_zone", "wide", "prev"),
    "significant_bar": ("n", "wide", "sig_vol", "pivot", "swing_k"),
}
# (scanner id, reading, side, name, definition, setup, difficulty)
_ROWS: tuple[tuple[str, str, str, str, str, str, str], ...] = (
    ("climax", "climax", "any", "Climax that starts a range",
     "A climax at the end of a fall or a rise (very wide spread, very heavy volume): Phase A may begin.", "reversal", "intermediate"),
    ("automatic_reaction", "ar", "any", "Automatic rally / reaction",
     "The first swing back after a climax: the range's other edge is set.", "reversal", "advanced"),
    ("secondary_test", "st", "any", "Secondary test",
     "The first swing back toward the climax on lighter volume: Phase A ends, the range is set.", "reversal", "advanced"),
    ("spring", "shake", "strength", "Spring",
     "Under the range bottom and back inside within a few bars, after a long Phase B (class by depth and volume).", "reversal", "advanced"),
    ("utad", "shake", "weakness", "Upthrust after distribution",
     "Over the range top and back inside within a few bars, after a long Phase B (class by depth and volume).", "reversal", "advanced"),
    ("spring_test", "shake_test", "strength", "Spring test",
     "A quiet down bar after a spring that holds over the spring's low, near the range bottom.", "pullback", "advanced"),
    ("utad_test", "shake_test", "weakness", "Upthrust test",
     "A quiet up bar after an upthrust that holds under its high, near the range top.", "pullback", "advanced"),
    ("lps_inside", "last_point", "strength", "Last point of support (inside the range)",
     "After a spring the move reached the middle, then a lighter swing back held over the spring's low.", "pullback", "advanced"),
    ("lpsy_inside", "last_point", "weakness", "Last point of supply (inside the range)",
     "After an upthrust the move reached the middle, then a lighter swing back held under its high.", "pullback", "advanced"),
    ("sos", "break", "strength", "Sign of strength (over the range)",
     "Several closes in a row over the range top: the range is left upward (Phase D).", "breakout", "intermediate"),
    ("sow", "break", "weakness", "Sign of weakness (under the range)",
     "Several closes in a row under the range bottom: the range is left downward (Phase D).", "breakout", "intermediate"),
    ("back_up_sos", "back_up", "strength", "Back-up to the range top",
     "After a sign of strength, a quiet bar back to the range top that closes over it.", "pullback", "advanced"),
    ("back_up_sow", "back_up", "weakness", "Back-up to the range bottom",
     "After a sign of weakness, a quiet bar back to the range bottom that closes under it.", "pullback", "advanced"),
    ("significant_bar", "significant_bar", "any", "Significant bar",
     "A wide bar on above-average volume closing in its direction's half beyond the last swing.", "breakout", "intermediate"),
)
DIRECTION = {"strength": "long", "weakness": "short", "any": "both"}
READING: dict[str, tuple[str, str]] = {row[0]: (row[1], row[2]) for row in _ROWS}
DEFINITIONS: dict[str, str] = {row[0]: row[4] for row in _ROWS}


def _params(reading: str) -> tuple[Param, ...]:
    keys = list(dict.fromkeys(_OWN[reading]))
    return tuple(Param(k, _REF[WYCKOFF_KWARGS[k]], _DOCS[k], True) for k in keys)


def _text(reading: str, side: str, params: tuple[Param, ...]) -> str:
    return f'Wyckoff("{reading}", "{side}", ' + ", ".join(f"{p.name}=${p.name}" for p in params) + ")"


def _scanner(row: tuple[str, str, str, str, str, str, str]) -> Scanner:
    ident, reading, side, name, definition, *_ = row
    params = _params(reading)
    return Scanner(f"wyckoff.{ident}", name, definition, _text(reading, side, params), BASE_TF, params)


SCANNERS: tuple[Scanner, ...] = tuple(_scanner(row) for row in _ROWS)
BY_ID: dict[str, Scanner] = {s.id: s for s in SCANNERS}


def scanner(ident: str) -> Scanner | None:
    return BY_ID.get(ident)


def taxonomy_rows() -> list[dict[str, Any]]:
    """The scanners' taxonomy entries (the shape of data/taxonomy.json; tests check the filed copy equals these)."""
    return [{"id": f"wyckoff.{ident}", "source": SOURCE, "name": name, "definition": definition, "method": "volume_flow",
             "setup": setup, "timeframe_family": ["swing", "positional"], "direction": DIRECTION[side],
             "data_tier": ["eod", "live_per_user"], "difficulty": difficulty, "universe_default": "fo_stocks",
             "evidence_ready": False, "fitness_available": False}
            for ident, _, side, name, definition, setup, difficulty in _ROWS]


def payload() -> dict[str, Any]:
    return {"scanners": [s.as_dict() for s in SCANNERS], "footer": FOOTER, "ref_label": REF_LABEL, "not_set": NOT_SET, "chip": CHIP}


def _ids() -> list[str]:
    return [s.id for s in SCANNERS]


tx.register_source(SOURCE, _ids, flag=FLAG, also=ALSO, note=FOOTER)

__all__ = ["SOURCE", "FLAG", "ALSO", "CHIP", "BASE_TF", "SCANNERS", "BY_ID", "DEFINITIONS", "DIRECTION", "READING", "scanner",
           "payload", "taxonomy_rows"]
