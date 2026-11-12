"""M99 P5: the "Fibonacci + Elliott" scanners - a screen library source of its own, `fib_ew`, behind the `fib_ew_pack` flag
(off by default). Each scanner is a ScreenQL text on the ONE function `FibEw(...)` - the same evaluation the chart layer
(app/charts/fib_ew_layer.py) and the L templates (so the AI Trading Bot) read, so the list, the chart and the bot never
disagree.

* Every number is an editable `$parameter` with its reference value ("Reference value - edit before use"); the run takes
  the values the user sets.
* No volume is read. Closed bars only; the screen's own timeframe (reference 1 hour) - any timeframe the screener runs works.
* Every wave count is an interpretation of the bars (COUNT_NOTE, on every card).

Signals only (ADR-0006): a scanner lists matches; nothing here places, changes or blocks an order. No buy / sell /
target / stop wording in names or cards (the taxonomy lint checks it).
"""
from __future__ import annotations

from typing import Any

from app.price_action import fib_ew
from app.screener import taxonomy as tx
from app.screener.intraday import FOOTER, NOT_SET, REF_LABEL, Param, Scanner
from app.screener.registry import FIB_EW_FLAG

SOURCE = "fib_ew"
FLAG = FIB_EW_FLAG
ALSO: tuple[str, ...] = ()
CHIP = "Fibonacci + Elliott"
BASE_TF = "1h"
NOTE = f"{fib_ew.COUNT_NOTE}. {FOOTER}"

_REF = {name: ref for name, _, ref, _, _ in fib_ew.SETTINGS_TABLE}
_DOCS: dict[str, str] = {
    "degree": "Which swings count as waves: 1 is the smallest, 3 the largest. Larger waves need more history.",
    "atr_n": "Bars for the average true range (the break distance and the cluster band use it).",
    "break_k": "How far beyond the level a close must be, in average true ranges (0: any close beyond).",
    "expiry": "Bars a setup stays watched after the count moves on.",
    "zz_trigger": "wave_iv: the last smaller swing inside wave C (when it lies beyond the end of wave A, else the end of wave B); "
                  "wave_b: the end of wave B.",
    "c_zone": "Only when wave C is inside the C-to-A zone below.",
    "c_min": "C-to-A zone: from, as a multiple of wave A.",
    "c_max": "C-to-A zone: to, as a multiple of wave A.",
    "w3_trigger": "swing: the last smaller swing of wave 2 (none there: the end of wave 1); wave_1_end: the end of wave 1.",
    "w2_zone": "Only when wave 2 retraced wave 1 inside the depth zone below.",
    "w2_min": "Wave 2 depth zone: from, as a share of wave 1.",
    "w2_max": "Wave 2 depth zone: to, as a share of wave 1.",
    "w3_void": "wave_1_start: the start of wave 1; wave_2_extreme: the extreme of wave 2.",
    "void_on": "close: a close beyond the count's limit voids it; wick: any trade beyond it.",
    "retr_1": "Retracement ratio 1.", "retr_2": "Retracement ratio 2.", "retr_3": "Retracement ratio 3.", "retr_4": "Retracement ratio 4.",
    "proj_1": "Extension ratio 1.", "proj_2": "Extension ratio 2.", "proj_3": "Extension ratio 3.",
    "gold_1": "Golden-section share 1.", "gold_2": "Golden-section share 2.", "gold_3": "Golden-section share 3.",
    "band": "Levels closer than this join one cluster (average true ranges, or % of price - see the band unit).",
    "band_mode": "The band's unit: atr (average true ranges) or pct (% of price).",
    "min_levels": "The fewest levels a cluster needs.",
    "min_sources": "The fewest different swings its levels must come from.",
}
_WAVE = ("degree", "atr_n", "break_k", "expiry")
_FIB = ("retr_1", "retr_2", "retr_3", "retr_4", "proj_1", "proj_2", "proj_3", "gold_1", "gold_2", "gold_3", "band", "band_mode",
        "min_levels", "min_sources", "atr_n", "degree")
_OWN: dict[str, tuple[str, ...]] = {
    fib_ew.ZIGZAG_C: (*_WAVE, "zz_trigger", "c_zone", "c_min", "c_max"),
    fib_ew.WAVE3: (*_WAVE, "w3_trigger", "w2_zone", "w2_min", "w2_max", "w3_void"),
    fib_ew.CLUSTER: _FIB,
    fib_ew.IMPULSE_DONE: ("degree",),
    fib_ew.DIAGONAL_DONE: ("degree",),
    fib_ew.VOID: ("degree", "void_on"),
}
# (scanner id, reading, side, name, definition, setup, difficulty, taxonomy direction)
_ROWS: tuple[tuple[str, str, str, str, str, str, str, str], ...] = (
    ("zigzag_c_up", fib_ew.ZIGZAG_C, "up", "Zigzag wave C complete (falling C)",
     "Wave C of a falling zigzag went beyond the end of wave A, then a close back over its last smaller swing.", "pullback",
     "advanced", "long"),
    ("zigzag_c_down", fib_ew.ZIGZAG_C, "down", "Zigzag wave C complete (rising C)",
     "Wave C of a rising zigzag went beyond the end of wave A, then a close back under its last smaller swing.", "pullback",
     "advanced", "short"),
    ("wave3_up", fib_ew.WAVE3, "up", "Wave 3 up may be starting",
     "In wave 2 of a rising impulse, a close over the last smaller swing of wave 2.", "pullback", "advanced", "long"),
    ("wave3_down", fib_ew.WAVE3, "down", "Wave 3 down may be starting",
     "In wave 2 of a falling impulse, a close under the last smaller swing of wave 2.", "pullback", "advanced", "short"),
    ("cluster_up", fib_ew.CLUSTER, "up", "Falling swing reaches a Fibonacci cluster",
     "The falling swing first reaches a cluster of Fibonacci levels from different swings below it.", "reversal", "intermediate",
     "long"),
    ("cluster_down", fib_ew.CLUSTER, "down", "Rising swing reaches a Fibonacci cluster",
     "The rising swing first reaches a cluster of Fibonacci levels from different swings above it.", "reversal", "intermediate",
     "short"),
    ("impulse_up_done", fib_ew.IMPULSE_DONE, "up", "Five waves up complete (impulse)",
     "The swing ending a counted fifth wave up is confirmed and the impulse rules hold.", "reversal", "advanced", "both"),
    ("impulse_down_done", fib_ew.IMPULSE_DONE, "down", "Five waves down complete (impulse)",
     "The swing ending a counted fifth wave down is confirmed and the impulse rules hold.", "reversal", "advanced", "both"),
    ("diagonal_up_done", fib_ew.DIAGONAL_DONE, "up", "Five waves up complete (diagonal)",
     "A rising wedge of five waves (wave 4 into wave 1) ends and the diagonal rules hold.", "reversal", "advanced", "both"),
    ("diagonal_down_done", fib_ew.DIAGONAL_DONE, "down", "Five waves down complete (diagonal)",
     "A falling wedge of five waves (wave 4 into wave 1) ends and the diagonal rules hold.", "reversal", "advanced", "both"),
    ("count_void", fib_ew.VOID, "any", "Wave count void",
     "Price went through the current wave count's own limit: the count read so far is wrong.", "reversal", "advanced", "both"),
)
READING: dict[str, tuple[str, str]] = {row[0]: (row[1], row[2]) for row in _ROWS}
DEFINITIONS: dict[str, str] = {row[0]: row[4] for row in _ROWS}


def _params(reading: str) -> tuple[Param, ...]:
    keys = list(dict.fromkeys(_OWN[reading]))
    return tuple(Param(k, _REF[k], _DOCS[k], True) for k in keys)


def _text(reading: str, side: str, params: tuple[Param, ...]) -> str:
    return f'FibEw("{reading}", "{side}", ' + ", ".join(f"{p.name}=${p.name}" for p in params) + ")"


def _scanner(row: tuple[str, str, str, str, str, str, str, str]) -> Scanner:
    ident, reading, side, name, definition, *_ = row
    params = _params(reading)
    return Scanner(f"{SOURCE}.{ident}", name, definition, _text(reading, side, params), BASE_TF, params)


SCANNERS: tuple[Scanner, ...] = tuple(_scanner(row) for row in _ROWS)
BY_ID: dict[str, Scanner] = {s.id: s for s in SCANNERS}


def scanner(ident: str) -> Scanner | None:
    return BY_ID.get(ident)


def taxonomy_rows() -> list[dict[str, Any]]:
    """The scanners' taxonomy entries (the shape of data/taxonomy.json; tests check the filed copy equals these)."""
    return [{"id": f"{SOURCE}.{ident}", "source": SOURCE, "name": name, "definition": definition, "method": "price_action",
             "setup": setup, "timeframe_family": ["swing", "positional"], "direction": direction,
             "data_tier": ["eod", "live_per_user"], "difficulty": difficulty, "universe_default": "fo_stocks",
             "evidence_ready": False, "fitness_available": False}
            for ident, _, _, name, definition, setup, difficulty, direction in _ROWS]


def payload() -> dict[str, Any]:
    return {"scanners": [s.as_dict() for s in SCANNERS], "footer": NOTE, "ref_label": REF_LABEL, "not_set": NOT_SET, "chip": CHIP}


def _ids() -> list[str]:
    return [s.id for s in SCANNERS]


tx.register_source(SOURCE, _ids, flag=FLAG, also=ALSO, note=NOTE)

__all__ = ["SOURCE", "FLAG", "ALSO", "CHIP", "BASE_TF", "NOTE", "SCANNERS", "BY_ID", "DEFINITIONS", "READING", "scanner", "payload",
           "taxonomy_rows"]
