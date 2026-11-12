"""M99 P6: the price-action pattern pack - ONE evaluation for the scanners, the chart layer and the M templates (so the
AI Trading Bot). Flag `pa_pack`, off by default.

It builds on what is there and changes none of it:

* swings: the causal swing engine the screener's SwingHigh / SwingLow read (`causal_swings.degree_pivots`, the
  default price-action settings) at one swing degree - a swing counts from the bar that CONFIRMED it, never earlier;
* candles: `candlestick_patterns.pattern_masks` (the inside bar and the outside bar as they are; the hammer and its
  mirror with the same rule, its two numbers as settings with the same reference values - parity tested);
* the average true range (`indicators.volatility.atr`) and the bar classifier's relative range (`bar_class`);
* the S/R information: `level_strength.strength_features` / `strength_score` as they are (information only).

Readings (each on the bar it becomes known; closed bars only - a value at bar t reads bars up to t, so cutting the bars
at t gives the same values up to t, tested at every bar). Side = the way the reading points (a description, never an
instruction); every reading is also built as its mirror:

| name | what it reads | side |
|---|---|---|
| fault / double / trap | a new confirmed swing extreme against the one of its kind before it: short of it by more than the tolerance (fault), within it (double), beyond it by at most `trap_max` of its own leg with a close back inside within `trap_bars` bars (trap) | at a swing high: down; at a low: up |
| climax | the leg after a swing extreme gives back at least `climax_r` of the leg that made it, within `climax_speed` times that leg's bars | as above |
| missing_link | a trend of at least `impulses` rising highs and higher lows; its last extreme a fault / double / trap / climax; the first leg away from it goes beyond the last pullback; a line from the anchor (fault / climax: that extreme; double / trap: the high before it) resting on the later swings with at least `touches` touches; the first close through the line | the trend's way |
| baston | a close beyond the last swing level (A) in an acceleration of at least `acc_k` average true ranges; a pullback of at most `hook_max` of it that stays on its side of A; a close beyond the acceleration's extreme; then price returns into the area between A and the pullback's extreme and closes back out of it (`entry` "turn"), or touches it ("touch") | the acceleration's way |
| trigger_candle | at a recent swing level validated by a fault / double / trap: an inside bar within a strong arrival bar, a strong outside bar closing away over a bar toward the level, or a strong long-tail bar (`tail_part`, `body_part`); strong = at least `arrive_k` average true ranges | away from the level |
| inside_bar / outside_bar | the existing candle rules (context only) | inside: none; outside: its close against its open |
| hammer | the existing hammer rule (`lower_mult`, `upper_mult`), after a fall (`after_move`, `fall_bars`); the mirror is the existing shooting-star rule after a rise | up / down |
| two_bar_reversal | bar 1 opens at one extreme and closes at the other; bar 2 opens at bar 1's close and closes at its own opposite extreme, at bar 1's open or beyond (`reclaim`); tolerance `two_tol` of the range; both bars at least `tall_k` average ranges (0: any) | bar 2's way |
| head_shoulders | after a rise, three swing highs with the middle (head) above both shoulders, shoulders within `shoulder_tol` of the head's height; the first close through the line of the two lows (neckline) within `expiry` bars; the mirror is the inverse pattern | down (inverse: up) |
| cup_handle | a swing high, a swing low, a swing high back within `rim_tol` of the cup's depth of the first after at least `cup_bars` bars; a handle at most `handle_max` of the depth; the first close above the rim within `expiry` bars (`vol_confirm`: with volume at least `vol_k` times the average) | up (the inverted cup: down) |

Levels (NaN where they do not apply): the missing-link line while it is watched and its void on the signal bar (the
extreme between the anchor and the break), the baston area while it is watched and its void (A less the tolerance),
the trigger's level and void (the extreme of the arrival and trigger bars), the neckline while watched, the head and
shoulders' void (the right shoulder's extreme) and its PROJECTION (the head's height from the break - a projection,
never a target; nothing reads it as one), the cup's rim while watched and its void (the handle's extreme).

Optional evidence layers (all OFF; they never block and never change a reading - each adds one row to the "Why?"):
Bollinger close outside the bands, ADX / DMI, MACD against its zero line, stochastic and RSI leaving their zones, the
moving-average stack, and a second momentum oscillator's divergence. They are for the generic templates and the chart;
the owner's own method reading never uses them.

Every number is a setting (SETTINGS_TABLE, EVIDENCE_TABLE: reference values, "reference - edit before use"). The entry,
stop, target and size are the user's own; there is no reward / risk filter and no order of any kind here. Book splits
(half at a limit and half at a stop; a part at each touch) are order handling, not readings - nothing here models them.
"""
from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, fields
from typing import Any

import numpy as np
import pandas as pd

from app.price_action import bar_class as bc
from app.price_action import candlestick_patterns as cp
from app.price_action import causal_swings as cs

REFERENCE_LABEL = "reference - edit before use"

FAULT, DOUBLE, TRAP, CLIMAX = "fault", "double", "trap", "climax"
EXTREMES: tuple[str, ...] = (FAULT, DOUBLE, TRAP, CLIMAX)
MISSING_LINK, BASTON, TRIGGER = "missing_link", "baston", "trigger_candle"
INSIDE, OUTSIDE, HAMMER, TWO_BAR = "inside_bar", "outside_bar", "hammer", "two_bar_reversal"
HEAD_SHOULDERS, CUP = "head_shoulders", "cup_handle"
NAMES: tuple[str, ...] = (FAULT, DOUBLE, TRAP, CLIMAX, MISSING_LINK, BASTON, TRIGGER, INSIDE, OUTSIDE, HAMMER, TWO_BAR,
                          HEAD_SHOULDERS, CUP)
SWING_READINGS: frozenset[str] = frozenset({FAULT, DOUBLE, TRAP, CLIMAX, MISSING_LINK, BASTON, TRIGGER, HEAD_SHOULDERS, CUP})
UP, DOWN, ANY = "up", "down", "any"
SIDES: tuple[str, ...] = (UP, DOWN)
SIDE_CHOICES: tuple[str, ...] = (ANY, UP, DOWN)
LEVELS: tuple[str, ...] = ("missing_link_line", "missing_link_void", "baston_zone_low", "baston_zone_high", "baston_void",
                           "trigger_level", "trigger_void", "neckline", "hs_void", "hs_projection", "cup_rim", "cup_void")
TRIGGER_FORMS: tuple[str, ...] = ("any", "inside", "outside", "tail")
ENTRY_FORMS: tuple[str, ...] = ("turn", "touch")

TITLES: dict[str, str] = {
    FAULT: "Fault at a swing extreme", DOUBLE: "Double at a swing extreme", TRAP: "Trap beyond a swing extreme",
    CLIMAX: "Climax: the leg given back", MISSING_LINK: "Missing link: trend-end line broken",
    BASTON: "Pullback after an acceleration", TRIGGER: "Trigger candle at a level", INSIDE: "Inside bar", OUTSIDE: "Outside bar",
    HAMMER: "Hammer", TWO_BAR: "Two-bar reversal", HEAD_SHOULDERS: "Head and shoulders", CUP: "Cup and handle",
}
SHORT: dict[str, str] = {FAULT: "F", DOUBLE: "D", TRAP: "TR", CLIMAX: "CX", MISSING_LINK: "ML", BASTON: "BA", TRIGGER: "TG",
                         INSIDE: "IB", OUTSIDE: "OB", HAMMER: "HM", TWO_BAR: "2B", HEAD_SHOULDERS: "HS", CUP: "CH"}
MIRROR_TITLES: dict[str, str] = {HAMMER: "Shooting star", HEAD_SHOULDERS: "Inverse head and shoulders", CUP: "Inverted cup and handle"}

# (name, kind, reference value, bounds / choices, label). kind: int / bars / num / flag / choice. Reference values only -
# "reference - edit before use"; tests/test_m99_p6_pa.py checks the dataclass defaults equal this table.
SETTINGS_TABLE: tuple[tuple[str, str, Any, Any, str], ...] = (
    ("degree", "int", 0, (0, 3), "Swing size (the swing degree read)"),
    ("atr_n", "bars", 14, (2, 200), "Bars for the average true range the tolerances are measured in"),
    ("eq_tol", "num", 0.1, (0.0, 5.0), "Equal-extreme tolerance, in average true ranges"),
    ("trap_max", "num", 0.2, (0.01, 1.0), "Trap: furthest overshoot, as a share of its own leg"),
    ("trap_bars", "bars", 5, (1, 100), "Trap: bars after the extreme for the close back inside"),
    ("climax_r", "num", 1.0, (0.5, 2.0), "Climax: share of the leg given back"),
    ("climax_speed", "num", 1.5, (0.2, 10.0), "Climax: the give-back within this many times the leg's bars"),
    ("impulses", "int", 3, (2, 10), "Missing link: rising highs (falling lows) in the trend before"),
    ("touches", "int", 3, (2, 10), "Missing link: touches of the line, its anchor included"),
    ("touch_tol", "num", 0.25, (0.0, 5.0), "Line touch tolerance, in average true ranges"),
    ("acc_k", "num", 2.0, (0.1, 50.0), "Baston: acceleration at least this many average true ranges"),
    ("hook_max", "num", 0.333, (0.05, 1.0), "Baston: pullback at most this share of the acceleration"),
    ("pen_tol", "num", 0.1, (0.0, 5.0), "Baston: tolerated penetration of the level, in average true ranges"),
    ("entry", "choice", "turn", ENTRY_FORMS, "Baston: signal on the turn out of the area, or on the touch"),
    ("expiry", "bars", 30, (1, 1000), "Bars a pattern stays watched"),
    ("levels_n", "int", 6, (1, 50), "Trigger: recent swing levels read"),
    ("at_tol", "num", 0.5, (0.0, 5.0), "Trigger: nearness to the level, in average true ranges"),
    ("arrive_k", "num", 1.0, (0.0, 20.0), "Trigger: arrival bar range at least this many average true ranges (0: any)"),
    ("trigger", "choice", "any", TRIGGER_FORMS, "Trigger candle form"),
    ("tail_part", "num", 0.6, (0.3, 0.95), "Long-tail trigger: the tail at least this share of the range"),
    ("body_part", "num", 0.3, (0.01, 0.7), "Long-tail trigger: the body at most this share of the range"),
    ("lower_mult", "num", 2.0, (0.5, 20.0), "Hammer: the long shadow at least this many bodies"),
    ("upper_mult", "num", 0.3, (0.0, 5.0), "Hammer: the short shadow at most this many bodies"),
    ("after_move", "flag", True, None, "Hammer: only after a fall (the mirror: after a rise)"),
    ("fall_bars", "bars", 5, (1, 100), "Hammer: the fall measured over this many bars"),
    ("two_tol", "num", 0.2, (0.0, 0.5), "Two-bar reversal: tolerance, as a share of the bar's range"),
    ("reclaim", "flag", True, None, "Two-bar reversal: bar 2 closes at bar 1's open or beyond"),
    ("tall_k", "num", 0.0, (0.0, 10.0), "Two-bar reversal: both bars at least this many average ranges (0: any)"),
    ("avg_n", "bars", 20, (2, 500), "Bars for the average range and volume"),
    ("shoulder_tol", "num", 0.5, (0.0, 2.0), "Head and shoulders: shoulders within this share of the head's height"),
    ("cup_bars", "bars", 15, (3, 1000), "Cup: bars from the left rim to the right rim, at least"),
    ("rim_tol", "num", 0.1, (0.0, 0.5), "Cup: the right rim within this share of the cup's depth of the left rim"),
    ("handle_max", "num", 0.5, (0.05, 1.0), "Cup: the handle at most this share of the cup's depth"),
    ("vol_confirm", "flag", False, None, "Cup: only with volume on the close above the rim"),
    ("vol_k", "num", 1.0, (0.1, 20.0), "Cup: that volume at least this many times the average"),
)
SETTING_NAMES: tuple[str, ...] = tuple(row[0] for row in SETTINGS_TABLE)

# The optional evidence layers (chart and Why? only - a scanner's answer never reads them). All OFF.
EVIDENCE_LAYERS: tuple[str, ...] = ("bollinger", "adx", "macd", "zone_exit", "ma_stack", "osc_divergence")
EVIDENCE_TITLES: dict[str, str] = {"bollinger": "Bollinger bands", "adx": "ADX / DMI", "macd": "MACD and its zero line",
                                   "zone_exit": "Stochastic and RSI leaving their zones", "ma_stack": "Moving-average stack",
                                   "osc_divergence": "Second oscillator divergence"}
EVIDENCE_TABLE: tuple[tuple[str, str, Any, Any, str], ...] = (
    ("bb_n", "bars", 20, (2, 500), "Bollinger: bars"),
    ("bb_k", "num", 2.0, (0.1, 10.0), "Bollinger: standard deviations"),
    ("adx_n", "bars", 14, (2, 200), "ADX / DMI: bars"),
    ("macd_fast", "bars", 12, (1, 200), "MACD: fast average"),
    ("macd_slow", "bars", 26, (2, 500), "MACD: slow average"),
    ("macd_signal", "bars", 9, (1, 200), "MACD: signal line"),
    ("stoch_n", "bars", 14, (2, 200), "Stochastic: bars"),
    ("stoch_smooth", "bars", 3, (1, 50), "Stochastic: smoothing"),
    ("stoch_low", "num", 20.0, (0.0, 50.0), "Stochastic: low zone below"),
    ("stoch_high", "num", 80.0, (50.0, 100.0), "Stochastic: high zone above"),
    ("rsi_n", "bars", 14, (2, 200), "RSI: bars"),
    ("rsi_low", "num", 30.0, (0.0, 50.0), "RSI: low zone below"),
    ("rsi_high", "num", 70.0, (50.0, 100.0), "RSI: high zone above"),
    ("zone_bars", "bars", 3, (1, 50), "Leaving a zone: within this many bars"),
    ("ema_fast", "bars", 10, (1, 500), "Short stack: fast exponential average"),
    ("ema_slow", "bars", 20, (2, 500), "Short stack: slow exponential average"),
    ("sma_fast", "bars", 50, (1, 1000), "Long stack: fast simple average"),
    ("sma_slow", "bars", 200, (2, 2000), "Long stack: slow simple average"),
    ("osc_fast", "bars", 5, (1, 200), "Second oscillator: fast average of the median price"),
    ("osc_slow", "bars", 34, (2, 500), "Second oscillator: slow average of the median price"),
)


def _check(table: tuple[tuple[str, str, Any, Any, str], ...], obj: Any) -> list[str]:
    problems: list[str] = []
    for name, kind, _, rule, label in table:
        v = getattr(obj, name)
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
    return problems


@dataclass(frozen=True)
class PaSettings:
    """Every default is a reference value ("reference - edit before use")."""
    degree: int = 0
    atr_n: int = 14
    eq_tol: float = 0.1
    trap_max: float = 0.2
    trap_bars: int = 5
    climax_r: float = 1.0
    climax_speed: float = 1.5
    impulses: int = 3
    touches: int = 3
    touch_tol: float = 0.25
    acc_k: float = 2.0
    hook_max: float = 0.333
    pen_tol: float = 0.1
    entry: str = "turn"
    expiry: int = 30
    levels_n: int = 6
    at_tol: float = 0.5
    arrive_k: float = 1.0
    trigger: str = "any"
    tail_part: float = 0.6
    body_part: float = 0.3
    lower_mult: float = 2.0
    upper_mult: float = 0.3
    after_move: bool = True
    fall_bars: int = 5
    two_tol: float = 0.2
    reclaim: bool = True
    tall_k: float = 0.0
    avg_n: int = 20
    shoulder_tol: float = 0.5
    cup_bars: int = 15
    rim_tol: float = 0.1
    handle_max: float = 0.5
    vol_confirm: bool = False
    vol_k: float = 1.0

    def __post_init__(self) -> None:
        problems = _check(SETTINGS_TABLE, self)
        if not problems and self.eq_tol > self.at_tol:
            problems.append("the equal-extreme tolerance must be at most the nearness to the level")
        if problems:
            raise ValueError("; ".join(problems))

    def key(self) -> tuple[Any, ...]:
        return tuple(getattr(self, f.name) for f in fields(self))


@dataclass(frozen=True)
class EvidenceSettings:
    """Which optional evidence layers are on (all off) and their reference values."""
    layers: tuple[str, ...] = ()
    bb_n: int = 20
    bb_k: float = 2.0
    adx_n: int = 14
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    stoch_n: int = 14
    stoch_smooth: int = 3
    stoch_low: float = 20.0
    stoch_high: float = 80.0
    rsi_n: int = 14
    rsi_low: float = 30.0
    rsi_high: float = 70.0
    zone_bars: int = 3
    ema_fast: int = 10
    ema_slow: int = 20
    sma_fast: int = 50
    sma_slow: int = 200
    osc_fast: int = 5
    osc_slow: int = 34

    def __post_init__(self) -> None:
        problems = _check(EVIDENCE_TABLE, self)
        bad = [x for x in self.layers if x not in EVIDENCE_LAYERS]
        if bad:
            problems.append(f"unknown evidence layer(s): {', '.join(bad)}")
        for a, b in (("macd_fast", "macd_slow"), ("ema_fast", "ema_slow"), ("sma_fast", "sma_slow"), ("osc_fast", "osc_slow")):
            if not problems and getattr(self, a) >= getattr(self, b):
                problems.append(f"{a} must be below {b}")
        if problems:
            raise ValueError("; ".join(problems))


def _clean(table: tuple[tuple[str, str, Any, Any, str], ...], given: Mapping[str, Any] | None, what: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    kinds = {row[0]: row[1] for row in table}
    for k, v in (given or {}).items():
        if v is None:
            continue
        if k not in kinds:
            raise ValueError(f"unknown {what} setting {k!r}")
        if kinds[k] in ("int", "bars") and isinstance(v, (float, np.floating)) and float(v).is_integer():
            v = int(v)
        if kinds[k] == "flag" and isinstance(v, (int, float, np.integer, np.floating)) and not isinstance(v, bool) and v in (0, 1):
            v = bool(v)
        if kinds[k] == "num" and isinstance(v, (int, np.integer)) and not isinstance(v, bool):
            v = float(v)
        out[k] = v
    return out


def settings_from(given: Mapping[str, Any] | None = None) -> PaSettings:
    """The reference settings updated with `given` (None or left out: the reference; unknown names refused)."""
    return PaSettings(**_clean(SETTINGS_TABLE, given, "price pattern"))


def evidence_from(layers: tuple[str, ...] | list[str] = (), given: Mapping[str, Any] | None = None) -> EvidenceSettings:
    return EvidenceSettings(layers=tuple(layers), **_clean(EVIDENCE_TABLE, given, "evidence"))


def reference_settings() -> list[dict[str, Any]]:
    """The settings with their reference values and the label, for a settings form."""
    return [{"name": n, "value": ref, "label": label, "note": REFERENCE_LABEL} for n, _, ref, _, label in SETTINGS_TABLE]


def warmup(s: PaSettings) -> int:
    """Bars before the first answer can exist (the average true range; the swings need history on top)."""
    return max(s.atr_n, s.avg_n, s.fall_bars + 1) + 2


# --------------------------------------------------------------------------------------------------------- results
@dataclass(frozen=True)
class Event:
    bar: int                  # the bar it became known on
    name: str
    side: str                 # up / down
    origin: int               # the bar the structure it is about began
    variant: str              # a short word ('' when none): the extreme type, the trigger form ...
    why: str                  # structure words: what was read, no instruction, no single price
    levels: tuple[tuple[str, float], ...] = ()


@dataclass
class PaResult:
    n: int
    settings: PaSettings
    flags: dict[tuple[str, str], np.ndarray]
    known: dict[str, np.ndarray]
    levels: dict[str, np.ndarray]
    events: list[Event] = field(default_factory=list)
    pivots: list[tuple[str, int, int, float]] = field(default_factory=list)
    atr: np.ndarray | None = None

    def value(self, name: str, side: str = ANY) -> np.ndarray:
        """1.0 where the reading holds on the bar (on `side` when given), 0.0 where not, NaN where it cannot be read."""
        if name not in NAMES or side not in SIDE_CHOICES:
            raise ValueError(f"unknown reading {name!r} or side {side!r}")
        hit = (self.flags[(name, UP)] | self.flags[(name, DOWN)] | self.flags[(name, "")]) if side == ANY else self.flags[(name, side)]
        out = hit.astype(float)
        out[~self.known[name]] = np.nan
        return out

    def level(self, what: str) -> np.ndarray:
        if what not in LEVELS:
            raise ValueError(f"unknown level {what!r}")
        return self.levels[what]

    def rows(self) -> list[Event]:
        return sorted(self.events, key=lambda e: (e.bar, NAMES.index(e.name)))


# --------------------------------------------------------------------------------------------------------- helpers
@dataclass
class _Bars:
    """One orientation of the bars: as they are (+1), or mirrored (-1: prices negated, highs and lows swapped), so each
    detector is written once and its mirror is the same code (a level comes back with its sign restored)."""
    o: np.ndarray
    h: np.ndarray
    lo: np.ndarray
    c: np.ndarray
    atr: np.ndarray
    sign: int
    piv: list[tuple[str, int, int, float]]      # (kind H / L, pivot bar, confirm bar, price), in confirmation order

    @property
    def n(self) -> int:
        return len(self.c)

    def side(self, native: str) -> str:
        return native if self.sign > 0 else (DOWN if native == UP else UP)

    def price(self, x: float) -> float:
        return float(x) * self.sign


def _mirror(b: _Bars) -> _Bars:
    piv = [("L" if k == "H" else "H", pb, cb, -px) for k, pb, cb, px in b.piv]
    return _Bars(-b.o, -b.lo, -b.h, -b.c, b.atr, -b.sign, piv)


def _pct(x: float) -> str:
    return f"{100.0 * x:.0f} %"


def _x(x: float) -> str:
    return f"{x:.2f}"


def _bars(k: int) -> str:
    return f"{k} bar{'s' if k != 1 else ''}"


def _known_counts(piv: list[tuple[str, int, int, float]], n: int) -> np.ndarray:
    """m[t] = how many pivots are confirmed by bar t (pivots are in confirmation order)."""
    m = np.zeros(n, dtype=int)
    for _, _, cb, _ in piv:
        if 0 <= cb < n:
            m[cb] += 1
    return np.cumsum(m)


# ------------------------------------------------------------------------------------------- the four extreme types
@dataclass(frozen=True)
class _Tag:
    kind: str                 # fault / double / trap / climax
    bar: int                  # the bar the tag is known on
    k: int                    # the pivot's index


def _extremes(b: _Bars, s: PaSettings) -> list[_Tag]:
    """Tags of the swing HIGHS of this orientation (side down). A high E2 against the high before it E1, with L the leg
    that made E2 (from the low between): fault / double / trap at E2's confirmation (a trap needs a close back under E1
    within trap_bars bars of E2, E2 still the extreme), climax when the give-back reaches climax_r of L in time."""
    out: list[_Tag] = []
    piv, h, lo, c, a = b.piv, b.h, b.lo, b.c, b.atr
    for k in range(2, len(piv)):
        kind, e2b, e2c, e2 = piv[k]
        if kind != "H":
            continue
        _, sb, _, sp = piv[k - 1]
        e1 = piv[k - 2][3]
        at = a[e2b]
        if not np.isfinite(at):
            continue
        leg = e2 - sp
        if leg <= 0:
            continue
        tol, d = s.eq_tol * at, e2 - e1
        if d < -tol:
            out.append(_Tag(FAULT, e2c, k))
        elif abs(d) <= tol:
            out.append(_Tag(DOUBLE, e2c, k))
        elif d <= s.trap_max * leg:
            last = min(b.n - 1, e2b + s.trap_bars)
            for u in range(e2b + 1, last + 1):
                if h[u] > e2:
                    break
                if c[u] < e1:
                    out.append(_Tag(TRAP, max(u, e2c), k))
                    break
        # climax: the give-back of the whole leg (climax_r of it) within climax_speed x its bars, E2 still the extreme
        span = max(1, e2b - sb)
        last = min(b.n - 1, e2b + int(math.floor(s.climax_speed * span)))
        goal = e2 - s.climax_r * leg
        for u in range(e2b + 1, last + 1):
            if h[u] > e2:
                break
            if lo[u] <= goal:
                out.append(_Tag(CLIMAX, max(u, e2c), k))
                break
    return out


def _extreme_events(b: _Bars, s: PaSettings, tags: list[_Tag]) -> list[Event]:
    out: list[Event] = []
    for tg in tags:
        _, e2b, _, e2 = b.piv[tg.k]
        _, sb, _, sp = b.piv[tg.k - 1]
        e1b, e1 = b.piv[tg.k - 2][1], b.piv[tg.k - 2][3]
        leg, at = e2 - sp, b.atr[e2b]
        where = "swing high" if b.sign > 0 else "swing low"
        beyond = "above" if b.sign > 0 else "below"
        short = "under" if b.sign > 0 else "over"
        if tg.kind == FAULT:
            text = (f"Fault at a {where}: the swing ended short of the {where} before it by {_x((e1 - e2) / at)} average true"
                    f" ranges ({_bars(e2b - e1b)} apart).")
        elif tg.kind == DOUBLE:
            text = (f"Double at a {where}: the swing reached the {where} before it within {_x(abs(e2 - e1) / at)} average true"
                    f" ranges ({_bars(e2b - e1b)} apart).")
        elif tg.kind == TRAP:
            text = (f"Trap at a {where}: the swing went {beyond} the {where} before it by {_pct((e2 - e1) / leg)} of its own"
                    f" leg, then a close came back {short} it.")
        else:
            text = (f"Climax at a {where}: the leg of {_bars(e2b - sb)} that made the extreme was given back"
                    f" ({_pct(s.climax_r)} of it) within {_bars(tg.bar - e2b)}.")
        out.append(Event(tg.bar, tg.kind, b.side(DOWN), e2b, tg.kind, text + " A description of the swings, not advice."))
    return out


# ------------------------------------------------------------------------------------------------- the missing link
@dataclass
class _Line:
    anchor_bar: int
    anchor: float
    slope: float
    x_type: str
    x_bar: int
    check_from: int
    touches: int
    impulses: int

    def at(self, t: int) -> float:
        return self.anchor + self.slope * (t - self.anchor_bar)


_TYPE_ORDER = (FAULT, DOUBLE, TRAP, CLIMAX)        # a fault / double / trap decides the anchor before a climax does


def _ml_arm(b: _Bars, s: PaSettings, m: int, tags_at: dict[int, list[_Tag]], t: int) -> _Line | None:
    """The newest missing-link line known at bar t, for a trend of rising highs (this orientation) - or None."""
    piv = b.piv[:m]
    highs = [j for j in range(len(piv)) if piv[j][0] == "H"]
    for j in reversed(highs[-20:]):
        if j - 2 < 0 or j + 1 >= len(piv):
            continue
        kinds = {tg.kind for tg in tags_at.get(j, ()) if tg.bar <= t}
        x_type = next((k for k in _TYPE_ORDER if k in kinds), None)
        if x_type is None:
            continue
        anchor, m3 = (j, j - 2) if x_type == FAULT else (j - 2, j - 2) if x_type in (DOUBLE, TRAP) else (j, j)
        q, count = m3, 1
        while q - 3 >= 0 and piv[q - 2][3] < piv[q][3] and piv[q - 3][3] < piv[q - 1][3]:
            count += 1
            q -= 2
        if count < s.impulses or q - 1 < 0:
            continue
        if not piv[j + 1][3] < piv[j - 1][3]:                    # the first leg away goes beyond the last pullback
            continue
        _, ab, _, ap = piv[anchor]
        later = [piv[i] for i in range(j + 2, len(piv)) if piv[i][0] == "H"]
        if not later:
            continue
        slope = max((px - ap) / (pb - ab) for _, pb, _, px in later)
        if slope >= 0:
            continue
        touches = 1 + sum(1 for _, pb, _, px in later
                          if np.isfinite(b.atr[pb]) and (ap + slope * (pb - ab)) - px <= s.touch_tol * b.atr[pb])
        if touches < s.touches:
            continue
        start = piv[j][1] + 1
        seg = np.arange(start, t)
        if len(seg) and (b.c[seg] > ap + slope * (seg - ab)).any():
            continue                                             # the line was already broken
        return _Line(ab, ap, slope, x_type, piv[j][1], start, touches, count)
    return None


def _missing_link(b: _Bars, s: PaSettings, tags: list[_Tag], levels: dict[str, np.ndarray]) -> list[Event]:
    tags_at: dict[int, list[_Tag]] = {}
    for tg in tags:
        tags_at.setdefault(tg.k, []).append(tg)
    tag_bars = {tg.bar for tg in tags}
    m = _known_counts(b.piv, b.n)
    out: list[Event] = []
    line: _Line | None = None
    for t in range(b.n):
        if (t == 0 or m[t] != m[t - 1] or t in tag_bars) and m[t] >= 4:
            line = _ml_arm(b, s, int(m[t]), tags_at, t)
        if line is None:
            continue
        v = line.at(t)
        levels["missing_link_line"][t] = b.price(v)
        if b.c[t] > v:
            void = float(np.min(b.lo[line.anchor_bar:t + 1]))
            levels["missing_link_void"][t] = b.price(void)
            first = {FAULT: "a fault (the last swing ended short of the one before)",
                     DOUBLE: "a double (the last swing ended at the one before)",
                     TRAP: "a trap (the last swing went just beyond the one before and came back)",
                     CLIMAX: "a climax (the last leg was given back in full)"}[line.x_type]
            trend = "rising highs and higher lows" if b.sign > 0 else "falling lows and lower highs"
            way = "over" if b.sign > 0 else "under"
            text = (f"Missing link: a trend of {line.impulses} {trend} ended with {first}; the first leg away went beyond the"
                    f" last pullback; the close went {way} the line from that extreme resting on the later swings"
                    f" ({line.touches} touches). A description of the swings, not advice.")
            out.append(Event(t, MISSING_LINK, b.side(UP), line.anchor_bar, line.x_type, text,
                             (("missing_link_void", b.price(void)),)))
            line = None
    return out


# -------------------------------------------------------------------------------------------------------- the baston
def _baston(b: _Bars, s: PaSettings, levels: dict[str, np.ndarray]) -> list[Event]:
    """A close above the last confirmed swing high A; the run (from the lowest low since A's bar) keeps its extreme BH;
    a pullback (the hook) of at most hook_max of the run that stays above A less the tolerance; a close above BH arms
    the area [A, hook low]; then the return into it and the close back out of it (or the touch) is the signal."""
    m = _known_counts(b.piv, b.n)
    out: list[Event] = []
    st: dict[str, Any] | None = None
    last_high: tuple[int, float] | None = None
    k_seen = 0
    for t in range(b.n):
        while k_seen < m[t]:
            kind, pb, _, px = b.piv[k_seen]
            if kind == "H":
                last_high = (pb, px)
            k_seen += 1
        a = b.atr[t]
        if not np.isfinite(a):
            continue
        if st is not None and st["armed"]:
            A, zl, zh = st["A"], st["zl"], st["zh"]
            if t - st["armed_bar"] > s.expiry or b.c[t] < A - s.pen_tol * st["atr"]:
                st = None
            else:
                levels["baston_zone_low"][t], levels["baston_zone_high"][t] = sorted((b.price(zl), b.price(zh)))
                if b.lo[t] <= zh:
                    st["touched"] = True
                if st["touched"] and (s.entry == "touch" or b.c[t] > zh):
                    void = A - s.pen_tol * st["atr"]
                    levels["baston_void"][t] = b.price(void)
                    way = "over" if b.sign > 0 else "under"
                    form = "touched the area" if s.entry == "touch" else f"came back into the area and closed back {way} it"
                    text = (f"Pullback after an acceleration: a run of {_x(st['acc'] / st['atr'])} average true ranges broke the"
                            f" last swing level; its pullback took {_pct(st['hook'])} of the run and held the level; a close"
                            f" beyond the run's extreme followed; price then {form} (between the level and the pullback's"
                            " extreme). A description of the bars, not advice.")
                    out.append(Event(t, BASTON, b.side(UP), st["start"], s.entry, text, (("baston_void", b.price(void)),)))
                    st = None
                continue
        if st is not None:
            A, L0, BH = st["A"], st["L0"], st["BH"]
            if st["HL"] is not None and b.c[t] > BH:
                run, hook = BH - L0, BH - st["HL"]
                at_bh = st["atr_bh"]
                ok = run >= s.acc_k * at_bh and hook <= s.hook_max * run and st["HL"] >= A - s.pen_tol * at_bh
                if ok:
                    st.update(armed=True, armed_bar=t, zl=min(A, st["HL"]), zh=max(A, st["HL"]), touched=False, acc=run,
                              hook=hook / run if run > 0 else 0.0, atr=at_bh)
                else:
                    st = None
                continue
            if b.h[t] > BH:
                st.update(BH=float(b.h[t]), atr_bh=a, HL=None)
            else:
                st["HL"] = float(b.lo[t]) if st["HL"] is None else min(st["HL"], float(b.lo[t]))
                run = BH - L0
                if BH - st["HL"] > s.hook_max * run or b.c[t] < A - s.pen_tol * st["atr_bh"]:
                    st = None
            continue
        if last_high is not None and t >= 1:
            ab, A = last_high
            if ab < t and b.c[t] > A >= b.c[t - 1]:
                L0 = float(np.min(b.lo[ab:t + 1]))
                st = {"A": A, "L0": L0, "BH": float(b.h[t]), "atr_bh": a, "HL": None, "armed": False, "start": ab}
    return out


# ------------------------------------------------------------------------------------------------ the trigger candle
def _trigger(b: _Bars, s: PaSettings, masks: dict[str, np.ndarray], levels: dict[str, np.ndarray]) -> list[Event]:
    """At a recent swing level (support in this orientation): the arrival validated as a fault / double / trap, then an
    inside bar (inside a strong down arrival bar), an outside bar closing up (covering a down bar; itself a strong bar),
    or a strong bar with a long lower tail - on closed bars. Strong: at least arrive_k average true ranges."""
    o, h, lo, c, a = b.o, b.h, b.lo, b.c, b.atr
    m = _known_counts(b.piv, b.n)
    rng = h - lo
    body = np.abs(c - o)
    upper, lower = h - np.maximum(o, c), np.minimum(o, c) - lo
    with np.errstate(divide="ignore", invalid="ignore"):
        tail = (rng > 0) & (lower >= s.tail_part * rng) & (body <= s.body_part * rng) & (lower > upper)
    inside, outside_up = masks["inside_bar"], masks["outside_up"]
    out: list[Event] = []
    for t in range(1, b.n):
        at = a[t]
        if not np.isfinite(at) or m[t] == 0:
            continue
        forms: list[tuple[str, int, float, float]] = []          # (form, arrival bar, arrival extreme, void)
        down_arrival = c[t - 1] < o[t - 1] and np.isfinite(a[t - 1]) and rng[t - 1] >= s.arrive_k * a[t - 1]
        if s.trigger in ("any", "inside") and inside[t] and down_arrival:
            forms.append(("inside", t - 1, float(lo[t - 1]), float(min(lo[t - 1], lo[t]))))
        if s.trigger in ("any", "outside") and outside_up[t] and c[t - 1] < o[t - 1] and rng[t] >= s.arrive_k * at:
            x = float(min(lo[t - 1], lo[t]))
            forms.append(("outside", t - 1, x, x))
        if s.trigger in ("any", "tail") and tail[t] and rng[t] >= s.arrive_k * at:
            forms.append(("tail", t, float(lo[t]), float(lo[t])))
        if not forms:
            continue
        lv = b.piv[:m[t]][-s.levels_n:]
        tol, near = s.eq_tol * at, s.at_tol * at
        best: tuple[float, str, str, int, float, float, str] | None = None
        for form, ab, x, void in forms:
            for kind, pb, _, px in lv:
                if pb >= ab or not (c[t] > px and o[ab] > px):
                    continue
                d = x - px
                if abs(d) <= tol:
                    val = DOUBLE
                elif tol < d <= near:
                    val = FAULT
                elif -near <= d < -tol:
                    val = TRAP
                else:
                    continue
                if best is None or abs(d) < best[0]:
                    best = (abs(d), form, val, ab, px, void, kind)
            if best is not None:
                break
        if best is None:
            continue
        _, form, val, ab, px, void, kind = best
        levels["trigger_level"][t], levels["trigger_void"][t] = b.price(px), b.price(void)
        # `kind` is in this orientation: an "H" pivot is on the far side of the level (a former swing high below price, or
        # in the mirror a former swing low above it) - a flip level
        role = ("a former swing high (a flip level)" if kind == "H" else "a swing low") if b.sign > 0 else \
               ("a former swing low (a flip level)" if kind == "H" else "a swing high")
        val_text = {DOUBLE: "reached it", FAULT: "ended just short of it", TRAP: "went just beyond it and closed back"}[val]
        form_text = {"inside": "an inside bar after the arrival bar", "outside": "an outside bar closing away from the level",
                     "tail": "a bar with a long tail toward the level, closing away from it"}[form]
        text = (f"Trigger candle at a level: price came to {role} and {val_text} ({val}); then {form_text} (a strong bar: at"
                f" least {_x(s.arrive_k)} average true ranges). A description of the bars, not advice.")
        out.append(Event(t, TRIGGER, b.side(UP), ab, f"{form} / {val}", text, (("trigger_level", b.price(px)), ("trigger_void", b.price(void)))))
    return out


# -------------------------------------------------------------------------------------------------- head and shoulders
def _head_shoulders(b: _Bars, s: PaSettings, levels: dict[str, np.ndarray]) -> list[Event]:
    """A top in this orientation (side down): after a rise, LS < Head > RS with the neckline through the two lows T1, T2
    (T2 the newest confirmed low); RS = the highest high since T2 while it stays under the head; the first close under
    the neckline within `expiry` bars of T2's confirmation, with |LS - RS| within shoulder_tol of the head's height."""
    m = _known_counts(b.piv, b.n)
    out: list[Event] = []
    arm: dict[str, Any] | None = None
    for t in range(b.n):
        if t == 0 or m[t] != m[t - 1]:
            arm = None
            piv = b.piv[:m[t]]
            k2 = len(piv) - 1 if piv and piv[-1][0] == "L" else len(piv) - 2
            if k2 - 4 >= 0 and piv[k2][0] == "L":
                ls, t1, hd, t2, base = piv[k2 - 3], piv[k2 - 2], piv[k2 - 1], piv[k2], piv[k2 - 4]
                if hd[3] > ls[3] and base[3] < t1[3]:
                    slope = (t2[3] - t1[3]) / (t2[1] - t1[1])
                    arm = {"ls": ls, "t1": t1, "hd": hd, "t2": t2, "slope": slope, "rs": -np.inf, "rs_bar": t2[1]}
                    seg = np.arange(t2[1] + 1, t)
                    if len(seg):
                        arm["rs"] = float(np.max(b.h[seg]))
                        arm["rs_bar"] = int(seg[int(np.argmax(b.h[seg]))])
                        if (b.c[seg] < t1[3] + slope * (seg - t1[1])).any():
                            arm = None
        if arm is None or t <= arm["t2"][1]:
            continue
        if b.h[t] > arm["rs"]:
            arm["rs"], arm["rs_bar"] = float(b.h[t]), t
        hd, t1, ls = arm["hd"], arm["t1"], arm["ls"]
        if arm["rs"] >= hd[3] or t - arm["t2"][2] > s.expiry:
            arm = None
            continue
        neck = t1[3] + arm["slope"] * (t - t1[1])
        levels["neckline"][t] = b.price(neck)
        if b.c[t] >= neck:
            continue
        height = hd[3] - (t1[3] + arm["slope"] * (hd[1] - t1[1]))
        if height > 0 and abs(ls[3] - arm["rs"]) <= s.shoulder_tol * height:
            proj = neck - height
            levels["hs_void"][t], levels["hs_projection"][t] = b.price(arm["rs"]), b.price(proj)
            title = "Head and shoulders" if b.sign > 0 else "Inverse head and shoulders"
            way = "under" if b.sign > 0 else "over"
            text = (f"{title}: after a {'rise' if b.sign > 0 else 'fall'}, three swings with the middle one beyond both; the"
                    f" shoulders within {_pct(abs(ls[3] - arm['rs']) / height)} of the head's height of each other; the close"
                    f" went {way} the line through the two swings between them. The head's height measured from the break is"
                    " shown as a projection only. A description of the swings, not advice.")
            out.append(Event(t, HEAD_SHOULDERS, b.side(DOWN), ls[1], "", text,
                             (("hs_void", b.price(arm["rs"])), ("hs_projection", b.price(proj)))))
        arm = None
    return out


# ----------------------------------------------------------------------------------------------------- cup and handle
def _cup(b: _Bars, s: PaSettings, vol: np.ndarray, levels: dict[str, np.ndarray], unknown: np.ndarray) -> list[Event]:
    m = _known_counts(b.piv, b.n)
    out: list[Event] = []
    arm: dict[str, Any] | None = None
    avg_vol = pd.Series(vol).rolling(s.avg_n, min_periods=s.avg_n).mean().shift(1).to_numpy(float)
    for t in range(b.n):
        if t == 0 or m[t] != m[t - 1]:
            arm = None
            piv = b.piv[:m[t]]
            kr = len(piv) - 1 if piv and piv[-1][0] == "H" else len(piv) - 2
            if kr - 2 >= 0 and piv[kr][0] == "H":
                lh, cl, rr = piv[kr - 2], piv[kr - 1], piv[kr]
                depth = lh[3] - cl[3]
                if depth > 0 and abs(rr[3] - lh[3]) <= s.rim_tol * depth and rr[1] - lh[1] >= s.cup_bars:
                    rim = max(lh[3], rr[3])
                    seg = np.arange(rr[1] + 1, t)
                    if not (len(seg) and (b.c[seg] > rim).any()):
                        arm = {"lh": lh, "cl": cl, "rr": rr, "depth": depth, "rim": rim,
                               "hl": float(np.min(b.lo[seg])) if len(seg) else np.inf}
        if arm is None or t <= arm["rr"][1]:
            continue
        rr, depth, rim = arm["rr"], arm["depth"], arm["rim"]
        arm["hl"] = min(arm["hl"], float(b.lo[t]))
        if arm["hl"] < rr[3] - s.handle_max * depth or t - rr[2] > s.expiry:
            arm = None
            continue
        levels["cup_rim"][t] = b.price(rim)
        if b.c[t] <= rim:
            continue
        if t - rr[1] < 2:
            arm = None
            continue
        if s.vol_confirm:
            if not (np.isfinite(vol[t]) and vol[t] > 0 and np.isfinite(avg_vol[t]) and avg_vol[t] > 0):
                unknown[t] = True
                arm = None
                continue
            if vol[t] < s.vol_k * avg_vol[t]:
                arm = None
                continue
        levels["cup_void"][t] = b.price(arm["hl"])
        lh = arm["lh"]
        title = "Cup and handle" if b.sign > 0 else "Inverted cup and handle"
        way = "above" if b.sign > 0 else "below"
        vtxt = ""
        if np.isfinite(vol[t]) and vol[t] > 0 and np.isfinite(avg_vol[t]) and avg_vol[t] > 0:
            vtxt = f" Volume on the close: {_x(vol[t] / avg_vol[t])} x the average of {s.avg_n} bars."
        text = (f"{title}: a swing extreme, a cup of {_bars(rr[1] - lh[1])} back to it (the second rim within"
                f" {_pct(abs(rr[3] - lh[3]) / depth)} of the cup's depth), a handle of {_pct((rr[3] - arm['hl']) / depth)} of"
                f" the depth, then the first close {way} the rim.{vtxt} A description of the swings, not advice.")
        out.append(Event(t, CUP, b.side(UP), lh[1], "", text, (("cup_void", b.price(arm["hl"])),)))
        arm = None
    return out


# ------------------------------------------------------------------------------------------------------ the candles
def _candle_masks(frame: pd.DataFrame, s: PaSettings) -> dict[str, np.ndarray]:
    """The existing masks (candlestick_patterns.pattern_masks) and the hammer / shooting star with their two numbers as
    settings (the same rule; equal to the existing masks at the reference values - parity tested)."""
    pm = cp.pattern_masks(frame)
    o, h, lo, c = (frame[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    body, rng = np.abs(c - o), h - lo
    upper, lower = h - np.maximum(o, c), np.minimum(o, c) - lo
    ok = (rng > 0) & (body > 0)
    hammer = ok & (lower >= s.lower_mult * body) & (upper <= s.upper_mult * body)
    star = ok & (upper >= s.lower_mult * body) & (lower <= s.upper_mult * body)
    hammer[:2] = False
    star[:2] = False
    return {"inside_bar": pm["inside_bar"], "outside_up": pm["bullish_outside_bar"], "outside_down": pm["bearish_outside_bar"],
            "hammer": hammer, "star": star}


def _two_bar(o: np.ndarray, h: np.ndarray, lo: np.ndarray, c: np.ndarray, rel: np.ndarray, s: PaSettings) -> np.ndarray:
    """Bullish two-bar reversal at bar 2 (this orientation): bar 1 opens at its high and closes at its low, bar 2 opens
    at bar 1's close and closes at its high (and at bar 1's open or beyond with `reclaim`), within two_tol of a range."""
    n = len(c)
    out = np.zeros(n, dtype=bool)
    if n < 2:
        return out
    o1, h1, l1, c1 = o[:-1], h[:-1], lo[:-1], c[:-1]
    o2, h2, l2, c2 = o[1:], h[1:], lo[1:], c[1:]
    r1, r2 = h1 - l1, h2 - l2
    tol = s.two_tol
    with np.errstate(invalid="ignore"):
        ok = (r1 > 0) & (r2 > 0) & (c1 < o1) & (c2 > o2)
        ok &= (h1 - o1 <= tol * r1) & (c1 - l1 <= tol * r1) & (np.abs(o2 - c1) <= tol * r1) & (h2 - c2 <= tol * r2)
        if s.reclaim:
            ok &= c2 >= o1 - tol * r1
        if s.tall_k > 0:
            ok &= (np.nan_to_num(rel[:-1]) >= s.tall_k) & (np.nan_to_num(rel[1:]) >= s.tall_k)
    out[1:] = ok
    return out


# --------------------------------------------------------------------------------------------------------- evaluate
def pivots_of(frame: pd.DataFrame, degree: int) -> list[tuple[str, int, int, float]]:
    """The causal swing engine's confirmed pivots at `degree` (the screener's SwingHigh / SwingLow swings), in
    confirmation order: (kind, pivot bar, confirm bar, price)."""
    from app.price_action import pa_settings
    sset = pa_settings.settings()
    fr = pd.DataFrame({k: frame[k].astype(float).to_numpy() for k in ("open", "high", "low", "close")})
    fr["timestamp"] = fr["bar_end"] = np.asarray(frame.index)
    out = []
    for p in cs.degree_pivots(fr, degree, sset):
        if p.confirmed_idx is not None:
            out.append((p.kind, int(p.bar_idx), int(p.confirmed_idx), float(p.price)))
    return out


def evaluate(frame: pd.DataFrame, settings: PaSettings | None = None) -> PaResult:
    """Every reading and level on a closed-bar frame (open / high / low / close, optional volume; oldest first)."""
    from app.indicators.volatility import atr as atr_fn
    s = settings or PaSettings()
    n = len(frame)
    flags = {(name, side): np.zeros(n, dtype=bool) for name in NAMES for side in (UP, DOWN, "")}
    levels = {k: np.full(n, np.nan) for k in LEVELS}
    known = {name: np.ones(n, dtype=bool) for name in NAMES}
    if n == 0:
        return PaResult(0, s, flags, known, levels)
    o, h, lo, c = (frame[k].astype(float).to_numpy() for k in ("open", "high", "low", "close"))
    a = atr_fn(frame[["open", "high", "low", "close"]].astype(float), s.atr_n).to_numpy(float)
    vol = frame["volume"].astype(float).to_numpy() if "volume" in frame.columns else np.full(n, np.nan)
    piv = pivots_of(frame, s.degree)
    up = _Bars(o, h, lo, c, a, 1, piv)
    dn = _mirror(up)
    masks = _candle_masks(frame, s)
    dn_masks = {"inside_bar": masks["inside_bar"], "outside_up": masks["outside_down"]}
    rel = bc.relative_range(frame, s.avg_n).to_numpy(float)
    events: list[Event] = []
    unknown_cup = np.zeros(n, dtype=bool)
    for b, mk in ((up, masks), (dn, dn_masks)):
        lv = {k: np.full(n, np.nan) for k in LEVELS}
        tags = _extremes(b, s)
        events += _extreme_events(b, s, tags)
        events += _missing_link(b, s, tags, lv)
        events += _baston(b, s, lv)
        events += _trigger(b, s, mk, lv)
        events += _head_shoulders(b, s, lv)
        events += _cup(b, s, vol, lv, unknown_cup)
        for k in LEVELS:
            put = np.isfinite(lv[k])
            levels[k][put] = lv[k][put]
    # the candles (vectorized)
    after_fall = np.zeros(n, dtype=bool)
    after_rise = np.zeros(n, dtype=bool)
    fb = s.fall_bars                          # bar i: the close before against the close fall_bars before that one
    if n > fb + 1:
        after_fall[fb + 1:] = c[fb:-1] < c[:-fb - 1]
        after_rise[fb + 1:] = c[fb:-1] > c[:-fb - 1]
    hammer = masks["hammer"] & (after_fall if s.after_move else True)
    star = masks["star"] & (after_rise if s.after_move else True)
    two_up = _two_bar(o, h, lo, c, rel, s)
    two_dn = _two_bar(-o, -lo, -h, -c, rel, s)
    for i in np.flatnonzero(masks["inside_bar"]):
        events.append(Event(int(i), INSIDE, "", int(i) - 1, "", "Inside bar: its high and low stayed within the bar before -"
                            " a pause; context only. A description of the bars, not advice."))
    for mask, side in ((masks["outside_up"], UP), (masks["outside_down"], DOWN)):
        way = "above" if side == UP else "below"
        for i in np.flatnonzero(mask):
            events.append(Event(int(i), OUTSIDE, side, int(i) - 1, "", f"Outside bar: its range covered the bar before and it"
                                f" closed {way} its open; context only. A description of the bars, not advice."))
    for mask, side in ((hammer, UP), (star, DOWN)):
        for i in np.flatnonzero(mask):
            body = abs(c[i] - o[i])
            long_ = (min(o[i], c[i]) - lo[i]) if side == UP else (h[i] - max(o[i], c[i]))
            title = "Hammer" if side == UP else "Shooting star"
            ctx = (f" after a {'fall' if side == UP else 'rise'} over {_bars(s.fall_bars)}" if s.after_move else "")
            events.append(Event(int(i), HAMMER, side, int(i), "" if side == UP else "mirror",
                                f"{title}: a small body at the {'top' if side == UP else 'bottom'} of the bar with a"
                                f" {'lower' if side == UP else 'upper'} shadow of {_x(long_ / body)} bodies{ctx}. A description"
                                " of the bars, not advice."))
    for mask, side in ((two_up, UP), (two_dn, DOWN)):
        for i in np.flatnonzero(mask):
            r = rel[i - 1:i + 1]
            size = (f" Bars of {_x(r[0])} and {_x(r[1])} x the average range of {s.avg_n} bars." if np.isfinite(r).all() else "")
            a1, a2 = ("high", "low") if side == UP else ("low", "high")
            events.append(Event(int(i), TWO_BAR, side, int(i) - 1, "",
                                f"Two-bar reversal: the first bar opened at its {a1} and closed at its {a2}; the second opened"
                                f" at that close and closed at its {a1}{', back to the first open or beyond' if s.reclaim else ''}."
                                f"{size} A description of the bars, not advice."))
    for e in events:
        flags[(e.name, e.side)][e.bar] = True
    # unknown: the swing readings until the average true range exists; the cup where its volume check cannot be read
    no_atr = ~np.isfinite(a)
    for name in SWING_READINGS:
        known[name] = ~no_atr
    known[CUP] &= ~unknown_cup
    for name in (HAMMER,):
        known[name][: s.fall_bars + 1 if s.after_move else 2] = False
    for name in (INSIDE, OUTSIDE):
        known[name][:2] = False
    known[TWO_BAR][:1] = False
    return PaResult(n, s, flags, known, levels, events, piv, a)


_CACHE: list[tuple[pd.DataFrame, int, Any, tuple[Any, ...], PaResult]] = []
_CACHE_SIZE = 8


def cached(frame: pd.DataFrame, settings: PaSettings) -> PaResult:
    """`evaluate` once per frame object and settings - the several PricePattern(...) calls of one screen read one."""
    last = frame.index[-1] if len(frame) else None
    key = settings.key()
    for f, n_, l_, k_, out in _CACHE:
        if f is frame and n_ == len(frame) and l_ == last and k_ == key:
            return out
    out = evaluate(frame, settings)
    _CACHE.append((frame, len(frame), last, key, out))
    del _CACHE[:-_CACHE_SIZE]
    return out


# ----------------------------------------------------------------------------------------------- the evidence layers
def _agree_word(agrees: bool | None) -> str:
    return "agrees with the reading's side" if agrees else "does not agree with the reading's side" if agrees is False else "neutral"


def evidence_rows(frame: pd.DataFrame, res: PaResult, ev: EvidenceSettings, events: list[Event] | None = None) -> dict[int, list[dict[str, Any]]]:
    """Per event (by its position in `events`, default res.rows()): one row per evidence layer that is on - a fact in
    words and whether it agrees with the reading's side. Never changes a reading; reads bars up to the event's bar only
    (every indicator here is causal)."""
    from app.indicators import directional, momentum, trend, volatility
    rows = res.rows() if events is None else events
    out: dict[int, list[dict[str, Any]]] = {}
    if not ev.layers or not rows:
        return out
    close = frame["close"].astype(float)
    ohlc = frame[["open", "high", "low", "close"]].astype(float)
    cache: dict[str, Any] = {}

    def get(name: str, fn: Callable[[], Any]) -> Any:
        if name not in cache:
            cache[name] = fn()
        return cache[name]

    for idx, e in enumerate(rows):
        i, sd = e.bar, (1 if e.side == UP else -1 if e.side == DOWN else 0)
        got: list[dict[str, Any]] = []
        for layer in ev.layers:
            agrees: bool | None = None
            text = ""
            if layer == "bollinger":
                bb = get("bb", lambda: volatility.bollinger(close, ev.bb_n, ev.bb_k))
                up_, mid, lw = (float(bb[k].iloc[i]) for k in ("upper", "mid", "lower"))
                w0 = float(bb["upper"].iloc[i - 1] - bb["lower"].iloc[i - 1]) if i >= 1 else float("nan")
                if np.isfinite(up_):
                    x = float(close.iloc[i])
                    where = "above the upper band" if x > up_ else "below the lower band" if x < lw else \
                            "inside the bands, above the middle line" if x >= mid else "inside the bands, below the middle line"
                    widen = "widening" if np.isfinite(w0) and (up_ - lw) > w0 else "not widening"
                    agrees = (x > up_) if sd > 0 else (x < lw) if sd < 0 else None
                    text = f"Bollinger ({ev.bb_n}, {ev.bb_k:g}): the close is {where}; the bands are {widen}."
            elif layer == "adx":
                d = get("adx", lambda: directional.adx(ohlc, ev.adx_n))
                p, mn, ax = (float(d[k].iloc[i]) for k in ("plus_di", "minus_di", "adx"))
                ax0 = float(d["adx"].iloc[i - 1]) if i >= 1 else float("nan")
                if np.isfinite(ax):
                    rising = np.isfinite(ax0) and ax > ax0
                    lead = "DI+ above DI-" if p > mn else "DI- above DI+"
                    agrees = (p > mn and rising) if sd > 0 else (mn > p and rising) if sd < 0 else None
                    text = f"ADX / DMI ({ev.adx_n}): {lead}; ADX {ax:.0f} and {'rising' if rising else 'not rising'}."
            elif layer == "macd":
                d = get("macd", lambda: trend.macd(close, ev.macd_fast, ev.macd_slow, ev.macd_signal))
                ml, sg = float(d["macd"].iloc[i]), float(d["signal"].iloc[i])
                if np.isfinite(ml) and np.isfinite(sg):
                    rel_ = "above" if ml > sg else "below"
                    zero = "above zero" if ml > 0 and sg > 0 else "below zero" if ml < 0 and sg < 0 else "around zero"
                    agrees = (ml > sg and ml > 0 and sg > 0) if sd > 0 else (ml < sg and ml < 0 and sg < 0) if sd < 0 else None
                    text = f"MACD ({ev.macd_fast}, {ev.macd_slow}, {ev.macd_signal}): the line is {rel_} its signal line, {zero}."
            elif layer == "zone_exit":
                st_ = get("stoch", lambda: momentum.stochastic(ohlc, ev.stoch_n, ev.stoch_smooth))
                rs = get("rsi", lambda: momentum.rsi(close, ev.rsi_n))
                lo_i = max(0, i - ev.zone_bars)
                k_now, r_now = float(st_["k"].iloc[i]), float(rs.iloc[i])
                k_win, r_win = st_["k"].iloc[lo_i:i].to_numpy(float), rs.iloc[lo_i:i].to_numpy(float)
                st_up = bool(np.isfinite(k_now) and k_now > ev.stoch_low and (k_win < ev.stoch_low).any())
                st_dn = bool(np.isfinite(k_now) and k_now < ev.stoch_high and (k_win > ev.stoch_high).any())
                r_up = bool(np.isfinite(r_now) and r_now > ev.rsi_low and (r_win < ev.rsi_low).any())
                r_dn = bool(np.isfinite(r_now) and r_now < ev.rsi_high and (r_win > ev.rsi_high).any())
                parts = []
                parts.append("the stochastic left its low zone" if st_up else "the stochastic left its high zone" if st_dn
                             else "the stochastic left no zone")
                parts.append("RSI left its low zone" if r_up else "RSI left its high zone" if r_dn else "RSI left no zone")
                agrees = (st_up or r_up) if sd > 0 else (st_dn or r_dn) if sd < 0 else None
                text = (f"Stochastic ({ev.stoch_n}, {ev.stoch_smooth}; {ev.stoch_low:g} / {ev.stoch_high:g}) and RSI ({ev.rsi_n};"
                        f" {ev.rsi_low:g} / {ev.rsi_high:g}) within {_bars(ev.zone_bars)}: {', '.join(parts)}.")
            elif layer == "ma_stack":
                ef, es = get("ef", lambda: trend.ema(close, ev.ema_fast)), get("es", lambda: trend.ema(close, ev.ema_slow))
                sf, ss = get("sf", lambda: trend.sma(close, ev.sma_fast)), get("ss", lambda: trend.sma(close, ev.sma_slow))
                vals = [float(x.iloc[i]) for x in (ef, es, sf, ss)]
                short_ = ("fast above slow" if vals[0] > vals[1] else "fast below slow") if np.isfinite(vals[:2]).all() else "not enough bars"
                long_ = ("fast above slow" if vals[2] > vals[3] else "fast below slow") if np.isfinite(vals[2:]).all() else "not enough bars"
                if np.isfinite(vals).all():
                    agrees = (vals[0] > vals[1] and vals[2] > vals[3]) if sd > 0 else (vals[0] < vals[1] and vals[2] < vals[3]) if sd < 0 else None
                text = (f"Averages: short stack (EMA {ev.ema_fast} / {ev.ema_slow}) {short_}; long stack (SMA {ev.sma_fast} /"
                        f" {ev.sma_slow}) {long_}.")
            elif layer == "osc_divergence":
                med = (frame["high"].astype(float) + frame["low"].astype(float)) / 2.0
                osc = get("osc", lambda: (trend.sma(med, ev.osc_fast) - trend.sma(med, ev.osc_slow)).to_numpy(float))
                want = "L" if sd > 0 else "H"
                pts = [(pb, px) for kind, pb, cb, px in res.pivots if cb <= i and kind == want]
                if sd != 0 and len(pts) >= 2 and np.isfinite(osc[pts[-1][0]]) and np.isfinite(osc[pts[-2][0]]):
                    (b1, p1), (b2, p2) = pts[-2], pts[-1]
                    if sd > 0:
                        agrees = bool(p2 < p1 and osc[b2] > osc[b1])
                        fact = "a lower swing low with a higher oscillator low" if agrees else "no lower low with a higher oscillator low"
                    else:
                        agrees = bool(p2 > p1 and osc[b2] < osc[b1])
                        fact = "a higher swing high with a lower oscillator high" if agrees else "no higher high with a lower oscillator high"
                    text = f"Second oscillator (average {ev.osc_fast} - average {ev.osc_slow} of the median price): {fact} in the last two swings."
                else:
                    text = "Second oscillator: not enough confirmed swings."
            if text:
                got.append({"layer": layer, "title": EVIDENCE_TITLES[layer], "text": text, "agrees": agrees,
                            "agree_word": _agree_word(agrees)})
        out[idx] = got
    return out


# ---------------------------------------------------------------------------------------------------- S/R information
def sr_information(frame: pd.DataFrame, res: PaResult, count: int = 2) -> list[dict[str, Any]]:
    """Information only (never a signal): the nearest confirmed swing levels below and above the last close, each as an
    area of +/- eq_tol average true ranges, with the existing level-strength features (touches, bars since the last
    touch, role reversal) and score (app/price_action/level_strength.py, unchanged weights) and the book's touch count
    check (three touches or more)."""
    from app.price_action import level_strength as ls
    n = res.n
    if n == 0 or res.atr is None or not np.isfinite(res.atr[-1]):
        return []
    t = n - 1
    close = float(frame["close"].iloc[-1])
    half = max(res.settings.eq_tol, 0.05) * float(res.atr[-1])
    df = pd.DataFrame({k: frame[k].astype(float).to_numpy() for k in ("open", "high", "low", "close")})
    df["timestamp"] = pd.DatetimeIndex(frame.index).tz_localize(None) if getattr(frame.index, "tz", None) is not None else \
        pd.to_datetime(np.asarray(frame.index))
    bars = ls.prep_bars(df)
    below = sorted({px for _, _, cb, px in res.pivots if cb <= t and px < close}, reverse=True)[:count]
    above = sorted({px for _, _, cb, px in res.pivots if cb <= t and px > close})[:count]
    out = []
    for kind, prices in (("SUPPORT", below), ("RESISTANCE", above)):
        for px in prices:
            pb = max(pb for _, pb, cb, p in res.pivots if p == px and cb <= t)
            zone = {"low": px - half, "high": px + half, "kind": kind, "formed_at": df["timestamp"].iloc[pb]}
            f = ls.strength_features(zone, df, t, bars=bars)
            out.append({"kind": kind.lower(), "low": round(px - half, 2), "high": round(px + half, 2), "touches": f["touches"],
                        "bars_since": f["bars_since"], "role_reversal": bool(f["role_reversal"]),
                        "score": ls.strength_score(f), "three_touches": f["touches"] >= 3,
                        "formed": pd.Timestamp(frame.index[pb]).isoformat()})
    return out


__all__ = ["PaSettings", "EvidenceSettings", "PaResult", "Event", "settings_from", "evidence_from", "reference_settings",
           "evaluate", "cached", "evidence_rows", "sr_information", "pivots_of", "warmup", "NAMES", "SIDES", "SIDE_CHOICES",
           "LEVELS", "TITLES", "MIRROR_TITLES", "SHORT", "SETTINGS_TABLE", "SETTING_NAMES", "EVIDENCE_TABLE", "EVIDENCE_LAYERS",
           "EVIDENCE_TITLES", "REFERENCE_LABEL", "UP", "DOWN", "ANY", "EXTREMES", "TRIGGER_FORMS", "ENTRY_FORMS"]
