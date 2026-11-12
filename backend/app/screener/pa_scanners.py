"""M99 P6: the "Price patterns" scanners - a screen library source of its own, `pa`, behind the `pa_pack` flag (off by
default). Each scanner is a ScreenQL text on the ONE function `PricePattern(...)` - the same evaluation the chart layer
(app/charts/pa_layer.py) and the M templates (so the AI Trading Bot) read, so the list, the chart and the bot never
disagree.

* Every number is an editable `$parameter` with its reference value ("Reference value - edit before use"); the run takes
  the values the user sets.
* No volume is needed (the cup and handle's volume check is a switch, off by default). Closed bars only; the screen's own
  timeframe (reference Daily) - any timeframe the screener runs works.
* The optional indicator evidence layers are on the chart only: they never change a scanner's answer.

Signals only (ADR-0006): a scanner lists matches; nothing here places, changes or blocks an order. No buy / sell /
target / stop wording in names or cards (the taxonomy lint checks it).
"""
from __future__ import annotations

from typing import Any

from app.price_action import pa_pack as pa
from app.screener import taxonomy as tx
from app.screener.intraday import FOOTER, NOT_SET, REF_LABEL, Param, Scanner
from app.screener.registry import PA_FLAG

SOURCE = "pa"
FLAG = PA_FLAG
ALSO: tuple[str, ...] = ()
CHIP = "Price patterns"
BASE_TF = "1d"
NOTE = f"Descriptions of the bars and swings, never instructions. {FOOTER}"

_REF = {name: ref for name, _, ref, _, _ in pa.SETTINGS_TABLE}
_DOCS: dict[str, str] = {
    "degree": "Which swings are read: 0 is the smallest, 3 the largest. Larger swings need more history.",
    "atr_n": "Bars for the average true range the tolerances are measured in.",
    "eq_tol": "Two swing extremes within this many average true ranges are equal (a double).",
    "trap_max": "A trap goes beyond the swing before by at most this share of its own leg.",
    "trap_bars": "A trap's close back inside must come within this many bars of its extreme.",
    "climax_r": "A climax gives back at least this share of the leg that made the extreme.",
    "climax_speed": "A climax's give-back comes within this many times the bars of that leg.",
    "impulses": "The trend before the missing link: at least this many rising highs with higher lows (or the mirror).",
    "touches": "The missing link's line: at least this many touches, its anchor included.",
    "touch_tol": "A swing within this many average true ranges of the line touches it.",
    "acc_k": "The acceleration that breaks the level: at least this many average true ranges.",
    "hook_max": "Its pullback: at most this share of the acceleration.",
    "pen_tol": "How far beyond the broken level the pullback may go, in average true ranges.",
    "entry": "turn: the close back out of the area after price came into it; touch: price reaching the area.",
    "expiry": "Bars a pattern stays watched (after its pullback, its second low or its second rim).",
    "levels_n": "Recent swing levels a trigger candle is looked for at.",
    "at_tol": "How near the level the arrival must come, in average true ranges.",
    "arrive_k": "The arrival bar: at least this many average true ranges (0: any).",
    "trigger": "any, inside (an inside bar after the arrival bar), outside (an outside bar closing away) or tail (a long tail).",
    "tail_part": "A long-tail bar: the tail at least this share of the bar's range.",
    "body_part": "A long-tail bar: the body at most this share of the bar's range.",
    "lower_mult": "The long shadow at least this many bodies.",
    "upper_mult": "The short shadow at most this many bodies.",
    "after_move": "Only after a fall (the mirror: after a rise).",
    "fall_bars": "The fall (rise) is the close before against the close this many bars before it.",
    "two_tol": "Opens and closes count as at an extreme within this share of the bar's range.",
    "reclaim": "The second bar closes back at the first bar's open or beyond.",
    "tall_k": "Both bars at least this many times the average range (0: any size).",
    "avg_n": "Bars for the average range (and the average volume).",
    "shoulder_tol": "The two shoulders within this share of the head's height of each other.",
    "cup_bars": "Bars from the first rim to the second, at least.",
    "rim_tol": "The second rim within this share of the cup's depth of the first.",
    "handle_max": "The handle at most this share of the cup's depth.",
    "vol_confirm": "Only with volume on the close beyond the rim (an index uses its near-month future's volume).",
    "vol_k": "That volume at least this many times the average.",
}
_SWING = ("degree", "atr_n")
_EXTREME = (*_SWING, "eq_tol", "trap_max", "trap_bars", "climax_r", "climax_speed")
_OWN: dict[str, tuple[str, ...]] = {
    pa.FAULT: (*_SWING, "eq_tol"),
    pa.DOUBLE: (*_SWING, "eq_tol"),
    pa.TRAP: (*_SWING, "eq_tol", "trap_max", "trap_bars"),
    pa.CLIMAX: (*_SWING, "climax_r", "climax_speed"),
    pa.MISSING_LINK: (*_EXTREME, "impulses", "touches", "touch_tol"),
    pa.BASTON: (*_SWING, "acc_k", "hook_max", "pen_tol", "entry", "expiry"),
    pa.TRIGGER: (*_SWING, "eq_tol", "levels_n", "at_tol", "arrive_k", "trigger", "tail_part", "body_part"),
    pa.INSIDE: (),
    pa.OUTSIDE: (),
    pa.HAMMER: ("lower_mult", "upper_mult", "after_move", "fall_bars"),
    pa.TWO_BAR: ("two_tol", "reclaim", "tall_k", "avg_n"),
    pa.HEAD_SHOULDERS: (*_SWING, "shoulder_tol", "expiry"),
    pa.CUP: (*_SWING, "cup_bars", "rim_tol", "handle_max", "expiry", "vol_confirm", "vol_k", "avg_n"),
}
# (scanner id, reading, side, name, definition, setup, difficulty, taxonomy direction)
_ROWS: tuple[tuple[str, str, str, str, str, str, str, str], ...] = (
    ("fault_high", pa.FAULT, "down", "Fault at a swing high",
     "A new swing high ended short of the swing high before it.", "reversal", "intermediate", "short"),
    ("fault_low", pa.FAULT, "up", "Fault at a swing low",
     "A new swing low ended short of the swing low before it.", "reversal", "intermediate", "long"),
    ("double_high", pa.DOUBLE, "down", "Double at a swing high",
     "A new swing high reached the swing high before it, within the tolerance.", "reversal", "intermediate", "short"),
    ("double_low", pa.DOUBLE, "up", "Double at a swing low",
     "A new swing low reached the swing low before it, within the tolerance.", "reversal", "intermediate", "long"),
    ("trap_high", pa.TRAP, "down", "Trap above a swing high",
     "A swing went just above the swing high before it, then a close came back under it.", "reversal", "intermediate", "short"),
    ("trap_low", pa.TRAP, "up", "Trap below a swing low",
     "A swing went just below the swing low before it, then a close came back over it.", "reversal", "intermediate", "long"),
    ("climax_high", pa.CLIMAX, "down", "Climax at a swing high",
     "The leg that made a swing high was given back in full, about as fast as it came.", "reversal", "intermediate", "short"),
    ("climax_low", pa.CLIMAX, "up", "Climax at a swing low",
     "The leg that made a swing low was given back in full, about as fast as it came.", "reversal", "intermediate", "long"),
    ("missing_link_up", pa.MISSING_LINK, "up", "Missing link line broken upward",
     "After a rising trend ended in a fault, double, trap or climax, a close over the line from that extreme.", "pullback",
     "advanced", "long"),
    ("missing_link_down", pa.MISSING_LINK, "down", "Missing link line broken downward",
     "After a falling trend ended in a fault, double, trap or climax, a close under the line from that extreme.", "pullback",
     "advanced", "short"),
    ("baston_up", pa.BASTON, "up", "Pullback after an upward acceleration",
     "A run broke the last swing high, paused shallowly, went on; then price came back to the area and turned up.",
     "pullback", "intermediate", "long"),
    ("baston_down", pa.BASTON, "down", "Pullback after a downward acceleration",
     "A run broke the last swing low, paused shallowly, went on; then price came back to the area and turned down.",
     "pullback", "intermediate", "short"),
    ("trigger_up", pa.TRIGGER, "up", "Trigger candle at a support level",
     "At a recent swing level below, an inside, outside or long-tail bar after the arrival.", "reversal", "intermediate", "long"),
    ("trigger_down", pa.TRIGGER, "down", "Trigger candle at a resistance level",
     "At a recent swing level above, an inside, outside or long-tail bar after the arrival.", "reversal", "intermediate", "short"),
    ("inside_bar", pa.INSIDE, "any", "Inside bar",
     "The bar's high and low stayed within the bar before: a pause (context).", "mean_reversion", "beginner", "both"),
    ("outside_up", pa.OUTSIDE, "up", "Outside bar closing up",
     "The bar's range covered the bar before and it closed above its open (context).", "momentum", "beginner", "long"),
    ("outside_down", pa.OUTSIDE, "down", "Outside bar closing down",
     "The bar's range covered the bar before and it closed below its open (context).", "momentum", "beginner", "short"),
    ("hammer", pa.HAMMER, "up", "Hammer after a fall",
     "A small body at the top of the bar with a long lower shadow, after a fall.", "reversal", "beginner", "long"),
    ("shooting_star", pa.HAMMER, "down", "Shooting star after a rise",
     "A small body at the bottom of the bar with a long upper shadow, after a rise.", "reversal", "beginner", "short"),
    ("two_bar_up", pa.TWO_BAR, "up", "Two-bar reversal up",
     "A bar from its high to its low, then a bar from that close back to its own high.", "reversal", "beginner", "long"),
    ("two_bar_down", pa.TWO_BAR, "down", "Two-bar reversal down",
     "A bar from its low to its high, then a bar from that close back to its own low.", "reversal", "beginner", "short"),
    ("head_shoulders", pa.HEAD_SHOULDERS, "down", "Head and shoulders",
     "After a rise, three swing highs with the middle one highest; a close under the line of the two lows between.",
     "reversal", "intermediate", "short"),
    ("inverse_head_shoulders", pa.HEAD_SHOULDERS, "up", "Inverse head and shoulders",
     "After a fall, three swing lows with the middle one lowest; a close over the line of the two highs between.",
     "reversal", "intermediate", "long"),
    ("cup_handle", pa.CUP, "up", "Cup and handle",
     "A swing high, a rounded fall and return to it, a shallow handle, then a close above the rim.", "breakout",
     "intermediate", "long"),
    ("inverted_cup", pa.CUP, "down", "Inverted cup and handle",
     "A swing low, a rise and return to it, a shallow handle, then a close below the rim.", "breakout", "intermediate", "short"),
)
READING: dict[str, tuple[str, str]] = {row[0]: (row[1], row[2]) for row in _ROWS}
DEFINITIONS: dict[str, str] = {row[0]: row[4] for row in _ROWS}


def _params(reading: str) -> tuple[Param, ...]:
    keys = list(dict.fromkeys(_OWN[reading]))
    return tuple(Param(k, _REF[k], _DOCS[k], True) for k in keys)


def _text(reading: str, side: str, params: tuple[Param, ...]) -> str:
    return f'PricePattern("{reading}", "{side}"' + "".join(f", {p.name}=${p.name}" for p in params) + ")"


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
