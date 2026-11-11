"""M99 P5: the Fibonacci + Elliott pack - ONE evaluation for the scanners, the chart layer and the templates (so the bot).

Owner order M99 P5 (flag `fib_ew_pack`, off by default). It builds on what is there and changes none of it:

* the wave count of the wave templates (app/price_action/waves: `cached_wave_levels` - the main reading, the running end,
  the smaller swings inside each wave, the count's own invalidation level), at one swing degree;
* the rule checklist (app/price_action/elliott_rules.py) for the reasons and the invalidation lines;
* the Fibonacci level functions and the cluster finder of M99 B1 (fib_levels, price_clusters, fib_causal's confirmed
  pivots).

Readings, each on the bar it becomes known (closed bars only; a value at bar t reads bars up to t, so cutting the bars at
t gives the same value - tested at every bar):

* `zigzag_c_entry` (up / down): a zigzag correction whose wave C has gone beyond the end of wave A; then the first close
  back beyond the last smaller swing inside wave C (its wave iv) when that swing lies beyond the end of wave A, else
  beyond the end of wave B (setting `zz_trigger`: "wave_iv" or the more patient "wave_b"). Up = after a falling C.
* `wave3_entry` (up / down): an impulse in its wave 2 (or wave 3 not yet past the end of wave 1); then the first close
  beyond the last smaller swing of wave 2 (setting `w3_trigger` "swing"; none there: the end of wave 1), or beyond the end
  of wave 1 ("wave_1_end").
* `cluster_touch` (up / down): the open swing reaches a price cluster - at least `min_levels` Fibonacci levels from at
  least `min_sources` different swings of the same degree inside the band - for the first time. Up = a falling swing
  reaching a cluster below it.
* `impulse_complete` / `diagonal_complete` (up / down): the swing that ends a counted fifth wave is confirmed and the
  five waves keep every hard rule (elliott_rules). Up = five waves that rose.
* `count_void` (up / down): the close (or the wick, `void_on`) goes beyond the count's invalidation level known on the
  bar before - the count is wrong. Up / down = the way price went through.

Levels (NaN where they do not apply): `invalidation` (the count's hard-rule level, the wave templates' own), the two
entries' trigger levels while armed and their idea-void levels on their signal bars (`zigzag_c_void`: the extreme of
wave C; `wave3_void`: the start of wave 1 or wave 2's extreme, setting `w3_void`), `cluster_low` / `cluster_high` (the
nearest qualifying cluster ahead of the open swing) and `thrust` - a contracting triangle's post-triangle thrust from
the end of its wave E: a PROJECTION (a minimum estimate), never a target, and nothing reads it as one.

Every number is a setting (SETTINGS_TABLE: reference values, "reference - edit before use"). The entry, stop, target and
size are the user's own; there is no reward / risk filter and no order of any kind here. Every count is an interpretation
(COUNT_NOTE) and every text says so.
"""
from __future__ import annotations

import hashlib
import math
from bisect import bisect_left, bisect_right
from collections import OrderedDict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, fields
from typing import Any

import numpy as np
import pandas as pd

from app.price_action import causal_swings as cs
from app.price_action import elliott_rules as er
from app.price_action import fib_causal as fc
from app.price_action import fib_levels as fl
from app.price_action import price_clusters as pc
from app.price_action.daily_trend.rules import beyond
from app.price_action.waves import counter as wc
from app.price_action.waves.levels import WaveLevels, cached_wave_levels
from app.price_action.waves.series import WAVE_DEGREES

REFERENCE_LABEL = fl.REFERENCE_LABEL
COUNT_NOTE = er.COUNT_NOTE

ZIGZAG_C, WAVE3, CLUSTER, IMPULSE_DONE, DIAGONAL_DONE, VOID = (
    "zigzag_c_entry", "wave3_entry", "cluster_touch", "impulse_complete", "diagonal_complete", "count_void")
NAMES: tuple[str, ...] = (ZIGZAG_C, WAVE3, CLUSTER, IMPULSE_DONE, DIAGONAL_DONE, VOID)
UP, DOWN, ANY = "up", "down", "any"
SIDES: tuple[str, ...] = (UP, DOWN)
SIDE_CHOICES: tuple[str, ...] = (ANY, UP, DOWN)
LEVELS: tuple[str, ...] = ("invalidation", "zigzag_c_trigger", "zigzag_c_void", "wave3_trigger", "wave3_void", "cluster_low",
                           "cluster_high", "thrust")

# (name, kind, reference value, bounds / choices, label). kind: int / bars / num / flag / choice. Reference values only -
# "reference - edit before use"; tests/test_m99_p5_fib_ew.py checks the dataclass defaults equal this table.
SETTINGS_TABLE: tuple[tuple[str, str, Any, Any, str], ...] = (
    ("degree", "int", 1, (1, 3), "Wave size (swing degree the waves are counted at)"),
    ("atr_n", "bars", 14, (2, 200), "Bars for the average true range"),
    ("break_k", "num", 0.0, (0.0, 5.0), "Break distance beyond a level, in average true ranges (0: any close beyond)"),
    ("expiry", "bars", 20, (1, 500), "Bars a setup stays watched after the count moves on"),
    ("zz_trigger", "choice", "wave_iv", ("wave_iv", "wave_b"), "Zigzag trigger: the last smaller swing of C, or the end of B"),
    ("c_zone", "flag", False, None, "Only when wave C is inside the C-to-A zone"),
    ("c_min", "num", 0.618, (0.0, 5.0), "C-to-A zone: from (multiple of wave A)"),
    ("c_max", "num", 1.618, (0.0, 5.0), "C-to-A zone: to (multiple of wave A)"),
    ("w3_trigger", "choice", "swing", ("swing", "wave_1_end"), "Wave 3 trigger: the last smaller swing of wave 2, or the end of wave 1"),
    ("w2_zone", "flag", False, None, "Only when wave 2 is inside the depth zone"),
    ("w2_min", "num", 0.5, (0.0, 1.0), "Wave 2 depth zone: from (share of wave 1)"),
    ("w2_max", "num", 0.786, (0.0, 1.0), "Wave 2 depth zone: to (share of wave 1)"),
    ("w3_void", "choice", "wave_1_start", ("wave_1_start", "wave_2_extreme"), "Wave 3 idea void beyond"),
    ("void_on", "choice", "close", ("close", "wick"), "The count is void on a close or on a wick beyond its level"),
    ("retr_1", "num", 0.382, (0.0, 1.0), "Retracement ratio 1"),
    ("retr_2", "num", 0.5, (0.0, 1.0), "Retracement ratio 2"),
    ("retr_3", "num", 0.618, (0.0, 1.0), "Retracement ratio 3"),
    ("retr_4", "num", 0.786, (0.0, 1.0), "Retracement ratio 4"),
    ("proj_1", "num", 0.618, (0.0, 5.0), "Extension ratio 1"),
    ("proj_2", "num", 1.0, (0.0, 5.0), "Extension ratio 2"),
    ("proj_3", "num", 1.618, (0.0, 5.0), "Extension ratio 3"),
    ("gold_1", "num", 0.382, (0.01, 1.0), "Golden-section share 1"),
    ("gold_2", "num", 0.5, (0.01, 1.0), "Golden-section share 2"),
    ("gold_3", "num", 0.618, (0.01, 1.0), "Golden-section share 3"),
    ("band", "num", 0.5, (0.0, 10.0), "Cluster band (average true ranges, or % of price)"),
    ("band_mode", "choice", "atr", ("atr", "pct"), "Cluster band measured in"),
    ("min_levels", "int", 3, (2, 20), "Fewest levels in a cluster"),
    ("min_sources", "int", 2, (1, 10), "Fewest different swings in a cluster"),
)
SETTING_NAMES: tuple[str, ...] = tuple(row[0] for row in SETTINGS_TABLE)


@dataclass(frozen=True)
class FibEwSettings:
    degree: int = 1
    atr_n: int = 14
    break_k: float = 0.0
    expiry: int = 20
    zz_trigger: str = "wave_iv"
    c_zone: bool = False
    c_min: float = 0.618
    c_max: float = 1.618
    w3_trigger: str = "swing"
    w2_zone: bool = False
    w2_min: float = 0.5
    w2_max: float = 0.786
    w3_void: str = "wave_1_start"
    void_on: str = "close"
    retr_1: float = 0.382
    retr_2: float = 0.5
    retr_3: float = 0.618
    retr_4: float = 0.786
    proj_1: float = 0.618
    proj_2: float = 1.0
    proj_3: float = 1.618
    gold_1: float = 0.382
    gold_2: float = 0.5
    gold_3: float = 0.618
    band: float = 0.5
    band_mode: str = "atr"
    min_levels: int = 3
    min_sources: int = 2

    def __post_init__(self) -> None:
        problems: list[str] = []
        for name, kind, _, rule, label in SETTINGS_TABLE:
            v = getattr(self, name)
            if kind == "flag":
                if not isinstance(v, bool):
                    problems.append(f"{label} must be on or off")
            elif kind == "choice":
                if v not in rule:
                    problems.append(f"{label} must be one of {', '.join(rule)}")
            else:
                if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(float(v)):
                    problems.append(f"{label} must be a number")
                    continue
                if kind in ("int", "bars") and float(v) != int(v):
                    problems.append(f"{label} must be a whole number")
                low, high = rule
                if not low <= float(v) <= high:
                    problems.append(f"{label} must be between {low:g} and {high:g}")
        if not problems:
            if self.c_min >= self.c_max:
                problems.append("the C-to-A zone must start below its end")
            if self.w2_min >= self.w2_max:
                problems.append("the wave 2 depth zone must start below its end")
        if problems:
            raise ValueError("; ".join(problems))

    def retr(self) -> tuple[float, ...]:
        return (self.retr_1, self.retr_2, self.retr_3, self.retr_4)

    def proj(self) -> tuple[float, ...]:
        return (self.proj_1, self.proj_2, self.proj_3)

    def gold(self) -> tuple[float, ...]:
        return (self.gold_1, self.gold_2, self.gold_3)

    def key(self) -> tuple[Any, ...]:
        return tuple(getattr(self, f.name) for f in fields(self))


def settings_from(given: Mapping[str, Any] | None = None) -> FibEwSettings:
    """The reference settings updated with `given` (unknown names refused; whole numbers kept whole)."""
    out: dict[str, Any] = {}
    kinds = {row[0]: row[1] for row in SETTINGS_TABLE}
    for k, v in (given or {}).items():
        if k not in kinds:
            raise ValueError(f"unknown Fibonacci / wave setting {k!r}")
        if kinds[k] in ("int", "bars") and isinstance(v, float) and v.is_integer():
            v = int(v)
        out[k] = v
    return FibEwSettings(**out)


# --------------------------------------------------------------------------------------------------------- results
@dataclass(frozen=True)
class Event:
    bar: int                  # the bar it became known on
    name: str
    side: str                 # up / down
    origin: int               # the bar the structure it is about began (wave A / wave 1 start, the swing's pivot)
    label: str                # a short label for the chart mark
    title: str                # the reading in words
    why: str                  # structure words: what was read, no instruction, no single price
    levels: tuple[tuple[str, float], ...] = ()


@dataclass
class FibEwResult:
    n: int
    settings: FibEwSettings
    flags: dict[tuple[str, str], np.ndarray]
    levels: dict[str, np.ndarray]
    events: list[Event] = field(default_factory=list)
    waves: WaveLevels | None = None
    pivots: list[cs.Pivot] = field(default_factory=list)
    atr: np.ndarray | None = None

    def value(self, name: str, side: str = ANY) -> np.ndarray:
        """1.0 where the reading holds on the bar, 0.0 elsewhere."""
        if name not in NAMES or side not in SIDE_CHOICES:
            raise ValueError(f"unknown reading {name!r} or side {side!r}")
        if side == ANY:
            return (self.flags[(name, UP)] | self.flags[(name, DOWN)]).astype(float)
        return self.flags[(name, side)].astype(float)

    def level(self, what: str) -> np.ndarray:
        if what not in LEVELS:
            raise ValueError(f"unknown level {what!r}")
        return self.levels[what]


def _side_word(side: int) -> str:
    return UP if side > 0 else DOWN


def _word_up(side: int) -> str:
    return "up" if side > 0 else "down"


@dataclass
class _Armed:
    side: int
    key: tuple[Any, ...]
    trigger: float
    extreme: float            # wave C's / wave 2's extreme as known (idea void side)
    start: float              # wave 1's start (wave 3 entry) / wave B's end (zigzag)
    last_live: int            # the last bar whose count still held the setup
    origin: int
    words: dict[str, float]
    via: str


def _last_inner(inner: Sequence[wc.Swing], inner_bars: Sequence[int], kind: str, after: int, before: int, known_by: int) -> wc.Swing | None:
    """The latest smaller swing of `kind` with its extreme bar in (after, before), confirmed by `known_by`."""
    lo, hi = bisect_right(inner_bars, after), bisect_left(inner_bars, before)
    for s in reversed(inner[lo:hi]):
        if s.kind == kind and s.confirm_bar <= known_by:
            return s
    return None


def _first_close_beyond(close: np.ndarray, atr: np.ndarray, t: int, level: float, side: int, k: float) -> bool:
    if not math.isfinite(level):
        return False
    a = float(atr[t]) if math.isfinite(float(atr[t])) else 0.0
    need = level + side * k * a
    return beyond(float(close[t]), need, side) and not beyond(float(close[t - 1]), level, side)


# ------------------------------------------------------------------------------------------------------- clusters
def cluster_levels(known: Sequence[cs.Pivot], s: FibEwSettings) -> tuple[list[pc.PriceLevel], int]:
    """The labelled levels ahead of the open swing (from the latest pivot P1, against the swing P2 -> P1) and the open
    swing's direction (+1 rising, -1 falling; 0 when fewer than three pivots are known). Sources are the swings measured:
    the last swing's retracements, the larger swing's retracements (P4 -> P1, when it is larger), the swing before laid
    from P1 (extensions), the golden projections from the start of the swing before through P1, and the end of the swing
    before. Only levels beyond P1 in the open swing's direction are kept."""
    if len(known) < 3:
        return [], 0
    p1, p2, p3 = known[-1].price, known[-2].price, known[-3].price
    direction = -1 if p1 > p2 else 1
    out: list[pc.PriceLevel] = []
    out += [pc.PriceLevel(x.price, x.label, "last swing") for x in fl.retracement_levels(p2, p1, s.retr(), "the last swing")]
    if len(known) >= 4:
        p4 = known[-4].price
        if beyond(p4, p2, direction):
            out += [pc.PriceLevel(x.price, x.label, "larger swing") for x in fl.retracement_levels(p4, p1, s.retr(), "the larger swing")]
    out += [pc.PriceLevel(x.price, x.label, "swing before") for x in fl.extension_levels(p3, p2, p1, s.proj(), "the swing before")]
    if p1 != p3:
        out += [pc.PriceLevel(x.price, x.label, "golden section")
                for x in fl.golden_projection_levels(p3, p1, s.gold(), "the swing before")]
    out.append(pc.PriceLevel(p2, "end of the last swing", "swing end"))
    return [lv for lv in out if beyond(lv.price, p1, direction)], direction


def qualifying(levels: Sequence[pc.PriceLevel], s: FibEwSettings, atr: float | None) -> list[pc.Cluster]:
    """Clusters of at least `min_levels` levels from at least `min_sources` different swings inside the band."""
    found = pc.find_clusters(levels, s.band, s.band_mode, atr=atr, min_members=s.min_levels)
    return [c for c in found if len(c.sources) >= s.min_sources]


# ------------------------------------------------------------------------------------------------------- evaluation
def _frame_ok(bars: pd.DataFrame) -> bool:
    return len(bars) >= 3 and all(k in bars for k in ("open", "high", "low", "close"))


def evaluate(bars: pd.DataFrame, settings: FibEwSettings | None = None) -> FibEwResult:
    """Every reading and level of the pack on every bar of `bars` (closed bars, open / high / low / close)."""
    s = settings or FibEwSettings()
    if s.degree not in WAVE_DEGREES:
        raise ValueError(f"wave degree {s.degree} is not one of {', '.join(map(str, WAVE_DEGREES))}")
    n = len(bars)
    flags = {(name, side): np.zeros(n, dtype=bool) for name in NAMES for side in SIDES}
    levels = {what: np.full(n, np.nan) for what in LEVELS}
    out = FibEwResult(n, s, flags, levels)
    if not _frame_ok(bars):
        return out
    high, low, close = (bars[k].to_numpy(float) for k in ("high", "low", "close"))
    atr = cs.atr(fc.swing_frame(bars), int(s.atr_n))
    out.atr = atr
    L = cached_wave_levels(bars, int(s.degree))
    out.waves = L
    inv = L.invalidation()
    levels["invalidation"][:] = inv
    inner, inner_bars = L.inner, [x.bar for x in L.inner]
    pivots = fc.confirmed_pivots(bars, int(s.degree))
    out.pivots = pivots
    confirm_at: dict[int, list[cs.Pivot]] = {}
    for p in pivots:
        confirm_at.setdefault(int(p.confirmed_idx), []).append(p)                    # type: ignore[arg-type]

    zz: _Armed | None = None
    w3: _Armed | None = None
    fired: set[tuple[Any, ...]] = set()
    for t in range(1, n):
        r = L.main[t - 1]
        run, run_bar = float(L.running[t - 1]), int(L.running_bar[t - 1])
        # ---- zigzag wave C entry ------------------------------------------------------------------------------------
        live_zz = False
        if r is not None and r.type == wc.ZIGZAG and r.label == "COR_C" and len(r.points) == 4:
            d = r.direction
            x0, x1, x2 = r.points[0], r.points[1], r.points[2]
            key = ("zz", r.bars[0], r.bars[2])
            if key not in fired and beyond(run, x1, d):
                a_len = abs(x1 - x0)
                c_ratio = abs(run - x2) / a_len if a_len else float("nan")
                if not s.c_zone or (s.c_min <= c_ratio <= s.c_max):
                    iv = _last_inner(inner, inner_bars, "H" if d < 0 else "L", r.bars[2], run_bar, t - 1)
                    use_iv = s.zz_trigger == "wave_iv" and iv is not None and beyond(iv.price, x1, d)
                    trig = iv.price if use_iv and iv is not None else x2
                    zz = _Armed(-d, key, trig, run, x2, t - 1, r.bars[0],
                                {"b": abs(x2 - x1) / a_len if a_len else float("nan"), "c": c_ratio},
                                "the last smaller swing inside wave C" if use_iv else "the end of wave B")
                    live_zz = True
                elif zz is not None and zz.key == key:
                    zz = None
        if zz is not None and not live_zz:
            d = -zz.side
            if (t - 1) - zz.last_live > s.expiry or beyond(float(low[t - 1] if d < 0 else high[t - 1]), zz.extreme, d):
                zz = None
        if zz is not None:
            levels["zigzag_c_trigger"][t] = zz.trigger
            if _first_close_beyond(close, atr, t, zz.trigger, zz.side, s.break_k):
                d = -zz.side
                ext = min(zz.extreme, float(low[t])) if d < 0 else max(zz.extreme, float(high[t]))
                levels["zigzag_c_void"][t] = ext
                flags[(ZIGZAG_C, _side_word(zz.side))][t] = True
                up = _word_up(zz.side)
                out.events.append(Event(
                    t, ZIGZAG_C, _side_word(zz.side), zz.origin, "ZZ-C",
                    f"Zigzag wave C may be complete - the larger move may resume {up}",
                    f"A zigzag correction {_word_up(d)} (waves A-B-C): wave B retraced {zz.words['b']:.2f} of wave A and wave C"
                    f" went beyond the end of wave A ({zz.words['c']:.2f} times wave A). The close went back beyond {zz.via}."
                    f" The idea is void beyond the extreme of wave C. {COUNT_NOTE}; a description of past bars, not advice.",
                    (("trigger", zz.trigger), ("void", ext))))
                fired.add(zz.key)
                zz = None
        # ---- wave 3 entry -------------------------------------------------------------------------------------------
        live_w3 = False
        if r is not None and r.type == wc.IMPULSE and (
                (r.label == "IMP_2" and len(r.points) == 3) or (r.label == "IMP_3" and len(r.points) == 4 and not beyond(run, r.points[1], r.direction))):
            d = r.direction
            x0, x1 = r.points[0], r.points[1]
            key = ("w3", r.bars[0], r.bars[1])
            w2_ext, w2_bar = (run, run_bar) if r.label == "IMP_2" else (r.points[2], r.bars[2])
            one = abs(x1 - x0)
            depth = abs(x1 - w2_ext) / one if one else float("nan")
            if beyond(w2_ext, x0, -d):
                w3 = None if w3 is not None and w3.key == key else w3   # wave 2 went beyond the start of wave 1: the count is void
            elif key not in fired:
                if not s.w2_zone or (s.w2_min <= depth <= s.w2_max):
                    sw = _last_inner(inner, inner_bars, "H" if d > 0 else "L", r.bars[1], w2_bar, t - 1) if s.w3_trigger == "swing" else None
                    trig = sw.price if sw is not None else x1
                    w3 = _Armed(d, key, trig, w2_ext, x0, t - 1, r.bars[0], {"depth": depth},
                                "the last smaller swing of wave 2" if sw is not None else "the end of wave 1")
                    live_w3 = True
                elif w3 is not None and w3.key == key:
                    w3 = None
        if w3 is not None and not live_w3:
            d = w3.side
            lo_hi = float(low[t - 1] if d > 0 else high[t - 1])
            if (t - 1) - w3.last_live > s.expiry or beyond(lo_hi, w3.extreme, -d) or beyond(lo_hi, w3.start, -d):
                w3 = None
        if w3 is not None:
            levels["wave3_trigger"][t] = w3.trigger
            if _first_close_beyond(close, atr, t, w3.trigger, w3.side, s.break_k):
                d = w3.side
                ext = min(w3.extreme, float(low[t])) if d > 0 else max(w3.extreme, float(high[t]))
                void = w3.start if s.w3_void == "wave_1_start" else ext
                levels["wave3_void"][t] = void
                flags[(WAVE3, _side_word(d))][t] = True
                out.events.append(Event(
                    t, WAVE3, _side_word(d), w3.origin, "W3",
                    f"Wave 3 {_word_up(d)} may be starting",
                    f"An impulse {_word_up(d)}: wave 2 retraced {w3.words['depth']:.2f} of wave 1 and stayed inside its start."
                    f" The close went beyond {w3.via}. The idea is void beyond"
                    f" {'the start of wave 1' if s.w3_void == 'wave_1_start' else 'the extreme of wave 2'}. {COUNT_NOTE};"
                    " a description of past bars, not advice.",
                    (("trigger", w3.trigger), ("void", void))))
                fired.add(w3.key)
                w3 = None
        # ---- the count is void ---------------------------------------------------------------------------------------
        lvl = float(inv[t - 1])
        if r is not None and math.isfinite(lvl):
            x = r.points
            ref = x[-2] if x[-2] != lvl else run
            valid_side = 1 if ref > lvl else -1 if ref < lvl else 0
            probe = float(close[t]) if s.void_on == "close" else float(low[t] if valid_side > 0 else high[t])
            key = ("void", r.bars[0], len(x))
            if valid_side and key not in fired and beyond(probe, lvl, -valid_side):
                flags[(VOID, _side_word(-valid_side))][t] = True
                fired.add(key)
                out.events.append(Event(
                    t, VOID, _side_word(-valid_side), r.bars[0], "VOID", "The wave count is void",
                    f"Price went {_word_up(-valid_side)} through the count's own limit ({_rule_words(r)}). The count read so far"
                    f" is wrong and is read again from the swings. {COUNT_NOTE}.", (("limit", lvl),)))
        # ---- five waves complete --------------------------------------------------------------------------------------
        prev = L.main[t - 1]
        if prev is not None and prev.label == "IMP_5" and len(prev.points) == 6 and t in confirm_at:
            d = prev.direction
            end_kind = "H" if d > 0 else "L"
            for p in confirm_at[t]:
                if p.kind != end_kind or p.bar_idx <= prev.bars[4]:
                    continue
                x = list(prev.points[:5]) + [float(p.price)]
                if prev.type == wc.IMPULSE:
                    v = er.validate(x, er.IMPULSE, done=True)
                    name, label, what = IMPULSE_DONE, "5W", "impulse"
                else:
                    shape = er.CONTRACTING if er.validate(x, er.DIAGONAL, True, er.CONTRACTING).valid else er.EXPANDING
                    v = er.validate(x, er.DIAGONAL, done=True, shape=shape)
                    name, label, what = DIAGONAL_DONE, "DIAG", f"{shape} diagonal"
                key = ("done", prev.bars[0], name)
                if v.valid and key not in fired:
                    fired.add(key)
                    flags[(name, _side_word(d))][t] = True
                    held = "; ".join(c.words.lower() for c in v.checks if c.kind == er.HARD and c.status == er.PASS)
                    notes = "".join(f" Note: {c.words.lower()} - not met." for c in v.notes)
                    out.events.append(Event(
                        t, name, _side_word(d), prev.bars[0], label, f"Five waves {_word_up(d)} complete ({what})",
                        f"Five waves {_word_up(d)} read as {'an' if what == 'impulse' else 'a'} {what}; the swing that ends wave 5"
                        f" is confirmed. The rules hold: {held}.{notes} A correction of the five waves may follow."
                        f" {COUNT_NOTE}; a description of past bars, not advice."))
                break
    # ---- the triangle's thrust (a projection, never a target) -----------------------------------------------------------
    for t in range(n):
        r = L.main[t]
        if r is not None and r.type == wc.TRIANGLE and r.label == "COR_E" and len(r.points) == 6:
            # a contracting triangle whose rules still hold with wave E's latest end (E not beyond the end of C)
            if er.validate(list(r.points[:5]) + [float(L.running[t])], er.TRIANGLE).valid:
                proj = er.thrust_projection(r.points, r.bars, float(L.running[t]), r.trend)
                if proj is not None:
                    levels["thrust"][t] = proj
    _clusters(bars, s, pivots, atr, high, low, close, flags, levels, out)
    out.events.sort(key=lambda e: (e.bar, NAMES.index(e.name), e.side))            # in bar order (a list shows the newest first)
    return out


def _rule_words(r: wc.Reading) -> str:
    n = len(r.points) - 1
    if r.family == "impulse":
        return {2: "the start of wave 1", 3: "the start of wave 1 or the end of wave 2", 4: "the end of wave 1" if r.type == wc.IMPULSE
                else "the end of wave 2", 5: "the end of wave 4"}.get(n, "its limit")
    return {2: "the start of wave A", 3: "the end of wave B", 4: "the end of wave B", 5: "the end of wave C"}.get(n, "its limit")


def _clusters(bars: pd.DataFrame, s: FibEwSettings, pivots: list[cs.Pivot], atr: np.ndarray, high: np.ndarray, low: np.ndarray,
              close: np.ndarray, flags: dict[tuple[str, str], np.ndarray], levels: dict[str, np.ndarray], out: FibEwResult) -> None:
    """The cluster ahead of the open swing on every bar and its first touch (from the pivots confirmed by that bar)."""
    n = len(bars)
    counts = fc.known_counts(pivots, n)
    sets: dict[int, tuple[list[pc.PriceLevel], int]] = {}
    pct_memo: dict[int, list[pc.Cluster]] = {}
    touched: set[tuple[int, float, float]] = set()
    for t in range(n):
        m = int(counts[t])
        if m < 3:
            continue
        if m not in sets:
            sets[m] = cluster_levels(pivots[:m], s)
        lv, direction = sets[m]
        if not lv:
            continue
        if s.band_mode == pc.PCT:
            if m not in pct_memo:
                pct_memo[m] = qualifying(lv, s, None)
            found = pct_memo[m]
        else:
            a = float(atr[t])
            found = qualifying(lv, s, a) if math.isfinite(a) and a > 0 else []
        if not found:
            continue
        p1 = pivots[m - 1]
        start = int(p1.bar_idx) + 1
        # the nearest cluster ahead of the close (or the one it is in), in the open swing's direction
        cl = float(close[t])
        if direction < 0:
            ahead = sorted((c for c in found if c.low <= cl), key=lambda c: max(0.0, cl - c.high))
        else:
            ahead = sorted((c for c in found if c.high >= cl), key=lambda c: max(0.0, c.low - cl))
        if ahead:
            levels["cluster_low"][t], levels["cluster_high"][t] = ahead[0].low, ahead[0].high
        for c in found:
            near = c.high if direction < 0 else c.low
            key = (int(p1.bar_idx), round(c.low, 10), round(c.high, 10))
            if key in touched:
                continue
            reach = low[t] if direction < 0 else high[t]
            if not (reach <= near if direction < 0 else reach >= near):
                continue
            before = low[start:t] if direction < 0 else high[start:t]
            touched.add(key)
            if len(before) and ((before.min() <= near) if direction < 0 else (before.max() >= near)):
                continue                                         # reached before this bar (or before the pivot was known)
            side = -direction
            flags[(CLUSTER, _side_word(side))][t] = True
            srcs = ", ".join(c.sources)
            out.events.append(Event(
                t, CLUSTER, _side_word(side), int(p1.bar_idx), "FIB",
                f"Price reached a Fibonacci cluster ({c.count} levels)",
                f"The {'falling' if direction < 0 else 'rising'} swing reached a cluster of {c.count} Fibonacci levels from"
                f" {len(c.sources)} different swings ({srcs}): {'; '.join(c.labels)}. A reaction area to watch, not a forecast;"
                " a description of past bars, not advice.", (("cluster_low", c.low), ("cluster_high", c.high))))


# ----------------------------------------------------------------------------------------------------- the tool (chart)
def fib_tool(known: Sequence[cs.Pivot], s: FibEwSettings) -> list[dict[str, Any]]:
    """The Fibonacci tool on the latest confirmed swings: the retracements of the last swing, the swing before laid from
    the latest pivot (extensions) and the golden projections from the start of the swing before through it."""
    if len(known) < 2:
        return []
    out: list[dict[str, Any]] = []
    p2, p1 = known[-2], known[-1]
    out += [{"kind": fl.RETRACE, "ratio": x.ratio, "price": x.price, "label": x.label} for x in fl.retracement_levels(p2.price, p1.price, s.retr(), "the last swing")]
    if len(known) >= 3:
        p3 = known[-3]
        out += [{"kind": fl.EXTEND, "ratio": x.ratio, "price": x.price, "label": x.label}
                for x in fl.extension_levels(p3.price, p2.price, p1.price, s.proj(), "the swing before")]
        if p1.price != p3.price:
            out += [{"kind": fl.GOLDEN_PROJECTION, "ratio": x.ratio, "price": x.price, "label": x.label}
                    for x in fl.golden_projection_levels(p3.price, p1.price, s.gold(), "the swing before")]
    return out


# ----------------------------------------------------------------------------------------------------------- cache
_CACHE: OrderedDict[tuple[Any, ...], FibEwResult] = OrderedDict()
_CACHE_SIZE = 32


def _fingerprint(bars: pd.DataFrame) -> str:
    h = hashlib.sha256()
    h.update(np.asarray(bars.index.asi8 if isinstance(bars.index, pd.DatetimeIndex) else np.arange(len(bars))).tobytes())
    for k in ("open", "high", "low", "close"):
        h.update(bars[k].to_numpy(dtype=float).tobytes())
    return h.hexdigest()


def cached(bars: pd.DataFrame, settings: FibEwSettings | None = None) -> FibEwResult:
    """`evaluate` memoised by the frame's content and the settings (a screen reading several FibEw(...) calls with the same
    settings evaluates once)."""
    from app.price_action import pa_settings
    s = settings or FibEwSettings()
    key = (_fingerprint(bars), s.key(), repr(pa_settings.settings()))
    hit = _CACHE.get(key)
    if hit is not None:
        _CACHE.move_to_end(key)
        return hit
    value = evaluate(bars, s)
    _CACHE[key] = value
    if len(_CACHE) > _CACHE_SIZE:
        _CACHE.popitem(last=False)
    return value


__all__ = ["REFERENCE_LABEL", "COUNT_NOTE", "NAMES", "SIDES", "SIDE_CHOICES", "LEVELS", "SETTINGS_TABLE", "SETTING_NAMES", "FibEwSettings",
           "settings_from", "Event", "FibEwResult", "evaluate", "cached", "fib_tool", "cluster_levels", "qualifying", "ZIGZAG_C",
           "WAVE3", "CLUSTER", "IMPULSE_DONE", "DIAGONAL_DONE", "VOID", "UP", "DOWN", "ANY"]
