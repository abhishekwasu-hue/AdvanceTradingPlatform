"""M99 P4: the Wyckoff pack - a trading-range reader on closed bars, flag `wyckoff_pack` (off by default). The ONE
evaluation the ScreenQL functions `Wyckoff(...)` / `WyckoffLevel(...)` (scanners, the "Wyckoff" library screens, the K
templates, so the AI Trading Bot) and the chart layer (app/charts/wyckoff_layer.py) read, so they never disagree.

Built on what exists, never duplicating it:
  * the climax / stopping-volume readings and the test / no-supply / no-demand bars are app/price_action/vsa.py's (P3);
  * swings are app/price_action/structure_series.py's (B1: the causal structure tracker, a swing known from its
    confirm bar), and its regime tag is the fallback of the higher-timeframe bias;
  * spread and volume against the bars before are app/price_action/bar_class.py's.

Pure functions on a bar frame (open / high / low / close / volume, oldest first, CLOSED bars only); no database, clock or
network. Structure is read as one continuous unit (no day, session or clock); one call reads one timeframe.

The range, as known at each bar's close (one range at a time; "fall" / "rise" = the move the climax ended):
  Phase A  a climax (VSA selling climax or stopping volume after a fall; buying climax after a rise) -> the automatic
           reaction (AR: the first confirmed swing of the other kind after it, within `a_bars`) sets the range: the
           climax extreme .. the AR extreme -> the secondary test (ST: the first confirmed swing back toward the climax
           after the AR, within `a_bars`, not beyond the climax extreme by more than `st_below` x the range height, the
           leg into it on lower peak volume than the climax bar). A close beyond `st_below` x height past the climax
           extreme, or beyond the AR extreme, before the ST: no range.
  Phase B  from the ST. An edge poke (a low under the bottom / a high over the top) that closes back inside within
           `k_back` bars is a test of that edge while Phase B is shorter than `b_vs_a` x Phase A.
  Phase C  the same poke once Phase B is long enough: a shake - a spring (under the bottom) or an upthrust after
           distribution (UTAD, over the top); in Phase C a later poke at the same edge is a new shake only beyond the
           last shake's extreme. Spring class: 1 = deep (>= `deep` average ranges) on high volume and a wide
           spread, 3 = shallow on volume and spread below their averages, else 2. Then, within `test_bars`, its test: a
           VSA no-supply or test bar (no demand for a UTAD) that holds beyond the shake extreme within `test_zone` x
           height of the edge; and the last point inside the range: after the move from the shake reached `mid_share` of
           the range, a confirmed swing back that holds beyond the shake extreme on a lighter leg than the move.
  Phase D  `k_back` closes in a row beyond an edge: a break (sign of strength over the top / weakness under the bottom).
           Within `bu_bars`, once the move has left the zone of `bu_zone` x height over (under) the broken edge: the
           back-up - a bar moving back (closing against the break) into that zone that closes on the break
           side, volume lower than each of the `prev_bars` bars before and a spread not wide (-> Phase E); the same bar
           on high volume or a wide spread is a heavy back-up (a warning). A close back inside: the break failed (Phase
           B again).
  Phase E  after the back-up (or `bu_bars` without one). A close back inside the range ends it; a new climax starts a
           new range.

Events are known on the bar where they are confirmed and never relabel an earlier bar: each carries its `origin` (the
bar it is about: the AR's or ST's swing bar, the shake's extreme bar) but appears on the confirming bar. A shake that a
break later follows stays the event it was when it was known (no retro-labelling).

Significant bar (independent of the range): spread above `wide`, volume at least `sig_vol` x the average, close in the
half of the bar in its direction, and the close beyond the last confirmed swing high (low) known before the bar.

Bias (the higher-timeframe context): the side of a range in Phase D / E, else the structure regime (UP -> strength,
DOWN -> weakness), else none. It needs no volume.

No volume data: a frame with no volume at all reads no range and no event (NO_VOLUME); only the bias (structure) is read.
No look-ahead: every answer at bar t reads bars <= t only (a run cut at t gives the same answers up to t; tested).
Every number is a setting with a reference value ("reference - edit before use"). Signals only: nothing here places,
sizes or changes an order, and nothing carries a level to trade at - `WyckoffLevel` gives the range's own edges and
extremes, which the templates use only as the place where the idea is wrong.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from typing import Any

import numpy as np
import pandas as pd

from app.price_action import bar_class as bc
from app.price_action import structure_series as ss
from app.price_action import vsa
from app.price_action.zones.detect import average_range

STRENGTH, WEAKNESS = vsa.STRENGTH, vsa.WEAKNESS
SIDE_CHOICES: tuple[str, ...] = ("any", STRENGTH, WEAKNESS)
NO_VOLUME = vsa.NO_VOLUME
REFERENCE_LABEL = bc.REFERENCE_LABEL
FALL, RISE = "fall", "rise"
PHASES: tuple[str, ...] = ("A", "B", "C", "D", "E")

EVENTS: tuple[str, ...] = ("climax", "ar", "st", "edge_test", "shake", "shake_test", "last_point", "break", "back_up",
                           "heavy_back_up", "failed_break", "significant_bar")
STATES: tuple[str, ...] = ("phase_a", "phase_b", "phase_c", "phase_d", "phase_e", "in_range", "bias", "bias_none")
NAMES_ALL: tuple[str, ...] = EVENTS + STATES
# the user-visible name per event and side (structure words; the Wyckoff terms are the method's own names)
LABEL: dict[str, dict[str, str]] = {
    "climax": {STRENGTH: "Climax at the end of a fall", WEAKNESS: "Climax at the end of a rise"},
    "ar": {STRENGTH: "Automatic rally", WEAKNESS: "Automatic reaction"},
    "st": {STRENGTH: "Secondary test", WEAKNESS: "Secondary test"},
    "edge_test": {STRENGTH: "Upthrust action (test above the range)", WEAKNESS: "Secondary test as weakness (under the range)"},
    "shake": {STRENGTH: "Spring", WEAKNESS: "Upthrust after distribution"},
    "shake_test": {STRENGTH: "Spring test", WEAKNESS: "Upthrust test"},
    "last_point": {STRENGTH: "Last point of support (inside the range)", WEAKNESS: "Last point of supply (inside the range)"},
    "break": {STRENGTH: "Sign of strength (over the range)", WEAKNESS: "Sign of weakness (under the range)"},
    "back_up": {STRENGTH: "Back-up to the range top", WEAKNESS: "Back-up to the range bottom"},
    "heavy_back_up": {STRENGTH: "Heavy back-up (wait for another test)", WEAKNESS: "Heavy back-up (wait for another test)"},
    "failed_break": {STRENGTH: "Break under the range failed", WEAKNESS: "Break over the range failed"},
    "significant_bar": {STRENGTH: "Significant up bar", WEAKNESS: "Significant down bar"},
}
SHORT: dict[str, dict[str, str]] = {
    "climax": {STRENGTH: "SC", WEAKNESS: "BC"}, "ar": {STRENGTH: "AR", WEAKNESS: "AR"}, "st": {STRENGTH: "ST", WEAKNESS: "ST"},
    "edge_test": {STRENGTH: "UA", WEAKNESS: "STW"}, "shake": {STRENGTH: "SPR", WEAKNESS: "UTAD"},
    "shake_test": {STRENGTH: "SPT", WEAKNESS: "UTT"}, "last_point": {STRENGTH: "LPS", WEAKNESS: "LPSY"},
    "break": {STRENGTH: "SOS", WEAKNESS: "SOW"}, "back_up": {STRENGTH: "BUEC", WEAKNESS: "FTI"},
    "heavy_back_up": {STRENGTH: "HBU", WEAKNESS: "HBU"}, "failed_break": {STRENGTH: "FB", WEAKNESS: "FB"},
    "significant_bar": {STRENGTH: "SIG", WEAKNESS: "SIG"},
}
# the general name of each reading (scanner lists, the registry doc)
NAMES: dict[str, str] = {"climax": "Climax that starts a range", "ar": "Automatic rally / reaction", "st": "Secondary test",
                         "edge_test": "Test of a range edge", "shake": "Spring / upthrust after distribution",
                         "shake_test": "Test of the shake", "last_point": "Last point inside the range",
                         "break": "Sign of strength / weakness", "back_up": "Back-up to the broken edge",
                         "heavy_back_up": "Heavy back-up", "failed_break": "Failed break", "significant_bar": "Significant bar",
                         "phase_a": "Range in Phase A", "phase_b": "Range in Phase B", "phase_c": "Range in Phase C",
                         "phase_d": "Range in Phase D", "phase_e": "Range in Phase E", "in_range": "Inside a range",
                         "bias": "Larger structure leans", "bias_none": "Larger structure leans neither way"}
LEVELS: tuple[str, ...] = ("range_top", "range_bottom", "shake_extreme", "break_base")

# (name, kind, reference, bounds): the pack's own settings beside the volume spread ones it reads (vsa.SETTINGS_TABLE)
OWN_SETTINGS: tuple[tuple[str, str, Any, tuple[float, float]], ...] = (
    ("pivot_n", "bars", 3, (1, 20)),
    ("swing_k", "num", 1.5, (0.0, 10.0)),
    ("a_bars", "bars", 40, (3, 500)),
    ("st_below", "num", 0.1, (0.0, 1.0)),
    ("b_vs_a", "num", 1.0, (0.0, 10.0)),
    ("k_back", "bars", 3, (1, 20)),
    ("deep", "num", 0.5, (0.0, 10.0)),
    ("test_bars", "bars", 20, (1, 200)),
    ("test_zone", "num", 0.5, (0.0, 1.0)),
    ("mid_share", "num", 0.5, (0.0, 1.0)),
    ("bu_bars", "bars", 20, (1, 200)),
    ("bu_zone", "num", 0.25, (0.0, 2.0)),
    ("sig_vol", "num", 1.0, (0.0, 10.0)),
)
# the volume spread settings this pack reads (the climaxes, the test bars and the bar classes)
VSA_USED: tuple[str, ...] = ("avg_n", "narrow", "wide", "ultra_wide", "vol_low", "vol_high", "vol_ultra", "close_part",
                             "prev_bars", "new_bars", "trend_bars", "bg_bars", "area_bars", "area_tol", "background")
SETTINGS_TABLE: tuple[tuple[str, str, Any, tuple[float, float]], ...] = tuple(
    row for row in vsa.SETTINGS_TABLE if row[0] in VSA_USED) + OWN_SETTINGS


@dataclass(frozen=True)
class WyckoffSettings:
    """Every default is a reference value ("reference - edit before use")."""
    avg_n: int = 20
    narrow: float = 0.7
    wide: float = 1.3
    ultra_wide: float = 2.0
    vol_low: float = 0.7
    vol_high: float = 1.3
    vol_ultra: float = 2.0
    close_part: float = 1 / 3
    prev_bars: int = 2
    new_bars: int = 20
    trend_bars: int = 10
    bg_bars: int = 20
    area_bars: int = 20
    area_tol: float = 0.25
    background: bool = True
    pivot_n: int = 3
    swing_k: float = 1.5
    a_bars: int = 40
    st_below: float = 0.1
    b_vs_a: float = 1.0
    k_back: int = 3
    deep: float = 0.5
    test_bars: int = 20
    test_zone: float = 0.5
    mid_share: float = 0.5
    bu_bars: int = 20
    bu_zone: float = 0.25
    sig_vol: float = 1.0

    def vsa(self, background: bool | None = None) -> vsa.VsaSettings:
        values = {k: getattr(self, k) for k in VSA_USED}
        if background is not None:
            values["background"] = background
        return vsa.VsaSettings(**values)

    def warmup(self) -> int:
        """Bars before the first answer can exist: the volume spread warm-up (the climax needs it)."""
        return self.vsa().warmup()


def settings_from(values: dict[str, Any] | None = None) -> WyckoffSettings:
    """WyckoffSettings from named values (None or left out: the reference). Raises ValueError on an unknown name or a
    value outside its bounds or of the wrong kind (the volume spread names are checked by vsa.settings_from)."""
    values = {k: v for k, v in (values or {}).items() if v is not None}
    own = {name for name, *_ in OWN_SETTINGS}
    unknown = sorted(set(values) - own - set(VSA_USED))
    if unknown:
        raise ValueError(f"unknown Wyckoff setting(s): {', '.join(unknown)}")
    base = vsa.settings_from({k: v for k, v in values.items() if k not in own})
    out: dict[str, Any] = {k: v for k, v in asdict(base).items() if k in VSA_USED}
    for name, kind, ref, (lo, hi) in OWN_SETTINGS:
        if name not in values:
            out[name] = ref
            continue
        value = values[name]
        if isinstance(value, bool) or not isinstance(value, (int, float, np.integer, np.floating)) or not np.isfinite(value):
            raise ValueError(f"{name} must be a number, got {value!r}")
        if not lo <= float(value) <= hi:
            raise ValueError(f"{name} must be between {lo:g} and {hi:g}, got {float(value):g}")
        if kind == "bars":
            if float(value) != int(value):
                raise ValueError(f"{name} must be a whole number of bars, got {float(value):g}")
            out[name] = int(value)
        else:
            out[name] = float(value)
    return WyckoffSettings(**out)


def reference_settings() -> list[dict[str, Any]]:
    """The settings with their reference values and the label, for a settings form."""
    d = WyckoffSettings()
    return [{"name": f.name, "value": getattr(d, f.name), "label": REFERENCE_LABEL} for f in fields(d)]


# ---------------------------------------------------------------------------------------------------------------------
# the evaluation
# ---------------------------------------------------------------------------------------------------------------------
@dataclass
class Event:
    bar: int                  # the bar on which it is KNOWN (its close)
    name: str
    side: str
    origin: int               # the bar it is about (<= bar)
    variant: str = ""
    info: dict[str, Any] = field(default_factory=dict)


@dataclass
class _Range:
    after: str                # FALL / RISE: the move the climax ended
    start: int                # the climax bar
    climax_vol: float
    ext: float                # the climax extreme so far
    stage: str = "A1"         # A1 (waiting for the AR), A2 (waiting for the ST), B, C, D, E
    ar_bar: int = -1
    top: float = float("nan")
    bottom: float = float("nan")
    a_len: int = 1
    b_start: int = -1
    lean: str = ""
    shake_side: str = ""
    shake_ext: float = float("nan")
    shake_ext_bar: int = -1
    shake_known: int = -1
    test_done: bool = False
    last_done: bool = False
    pend: dict[str, int] = field(default_factory=lambda: {"bottom": -1, "top": -1})
    pend_ext: dict[str, float] = field(default_factory=dict)
    pend_ext_bar: dict[str, int] = field(default_factory=dict)
    break_bar: int = -1
    break_start: int = -1
    bu_done: bool = False
    heavy_done: bool = False

    @property
    def height(self) -> float:
        return self.top - self.bottom


@dataclass
class Readings:
    """Per bar (aligned with the frame): `phase` (A-E or ''), `lean` (the side a shake or a break gave the range, or ''),
    `top` / `bottom` (the range's edges, NaN outside a range), `shake_extreme`, `break_base`, `bias` (STRENGTH /
    WEAKNESS / ''), `bias_known`; `events` (every event, oldest first) and per event name `hit` / `side` arrays on the
    bar it is known; `known` False where nothing can be read (no volume data or the warm-up)."""
    n: int
    events: list[Event]
    hit: dict[str, np.ndarray]
    side: dict[str, np.ndarray]
    phase: np.ndarray
    lean: np.ndarray
    after: np.ndarray
    top: np.ndarray
    bottom: np.ndarray
    shake_extreme: np.ndarray
    break_base: np.ndarray
    range_start: np.ndarray
    bias: np.ndarray
    bias_known: np.ndarray
    known: np.ndarray
    has_volume: bool
    settings: WyckoffSettings

    def value(self, name: str, side: str = "any") -> np.ndarray:
        """1.0 where `name` holds (on `side` when given), 0.0 where not, NaN where it cannot be read."""
        if name == "bias":
            on = self.bias != "" if side == "any" else self.bias == side
            out = on.astype(float)
            out[~self.bias_known] = np.nan
            return out
        if name == "bias_none":
            out = (self.bias == "").astype(float)
            out[~self.bias_known] = np.nan
            return out
        if name in STATES:
            on = (self.phase != "") if name == "in_range" else (self.phase == name[-1].upper())
            if side != "any":
                on = on & (self.lean == side)
        else:
            on = self.hit[name] if side == "any" else self.hit[name] & (self.side[name] == side)
        out = on.astype(float)
        out[~self.known] = np.nan
        return out

    def level(self, what: str) -> np.ndarray:
        return {"range_top": self.top, "range_bottom": self.bottom, "shake_extreme": self.shake_extreme,
                "break_base": self.break_base}[what].copy()


def _swings(frame: pd.DataFrame, s: WyckoffSettings, tf: str) -> ss.StructureSeries:
    st_settings = ss.settings(pivot_n=s.pivot_n, swing_k=s.swing_k, spike_body_edge=False)
    return ss.structure_series(frame, tf, st_settings)


def evaluate(frame: pd.DataFrame, settings: WyckoffSettings | None = None, tf: str = "1h") -> Readings:  # noqa: C901
    """The range reader on the frame's closed bars (the module docstring). `tf`: the frame's timeframe (the swing
    tracker reads its pivot_n / swing_k from the settings, so it only names the frame)."""
    s = settings or WyckoffSettings()
    n = len(frame)
    h, lo, c = (frame[k].astype(float).to_numpy() for k in ("high", "low", "close"))
    v = bc.volume_data(frame).to_numpy(float)
    rr = bc.relative_range(frame, s.avg_n).to_numpy(float)
    rv = bc.relative_volume(frame, s.avg_n).to_numpy(float)
    rng = h - lo
    with np.errstate(divide="ignore", invalid="ignore"):
        cp = np.where(rng > 0, (c - lo) / rng, np.nan)
    avg_rng = average_range(frame, s.avg_n, s.avg_n).shift(1).to_numpy(float) if n else np.zeros(0)
    sig_main = vsa.cached(frame, s.vsa())                         # the climaxes, with the user's background switch
    sig_bar = vsa.cached(frame, s.vsa(background=False))          # the test bars: the range is their context here
    struct = _swings(frame, s, tf)
    has_volume = bool(np.isfinite(v).any())
    warm = np.arange(n) >= s.warmup() - 1
    known = warm & has_volume
    with np.errstate(invalid="ignore"):
        wide = rr > s.wide
        vol_high = rv > s.vol_high
    lower = np.ones(n, dtype=bool)
    for k in range(1, s.prev_bars + 1):
        prev_v = np.r_[np.full(min(k, n), np.nan), v[:-k]] if n > k else np.full(n, np.nan)
        with np.errstate(invalid="ignore"):
            lower &= v < prev_v
    lower &= np.isfinite(rv)

    phase = np.full(n, "", dtype=object)
    lean = np.full(n, "", dtype=object)
    after = np.full(n, "", dtype=object)
    top = np.full(n, np.nan)
    bottom = np.full(n, np.nan)
    shake_x = np.full(n, np.nan)
    break_base = np.full(n, np.nan)
    rstart = np.full(n, -1, dtype=int)
    bias = np.full(n, "", dtype=object)
    bias_known = np.zeros(n, dtype=bool)
    events: list[Event] = []

    def emit(t: int, name: str, side: str, origin: int, variant: str = "", **info: Any) -> None:
        events.append(Event(t, name, side, origin, variant, info))

    def climax_at(t: int) -> tuple[str, int, str] | None:
        # the form in structure words (the signal-language rule: the climaxes are named by where they come, P3)
        for name, side, form in (("selling_climax", STRENGTH, "a climax bar"), ("stopping_volume", STRENGTH, "stopping volume"),
                                 ("buying_climax", WEAKNESS, "a climax bar")):
            if sig_main.hit[name][t]:
                return side, int(sig_main.origin[name][t]), form
        return None

    def depth_units(t: int, depth: float) -> float:
        a = avg_rng[t] if t < n else np.nan
        return float(depth / a) if np.isfinite(a) and a > 0 else float("nan")

    def leg_mean(a: int, b: int) -> float:
        seg = v[a:b + 1] if b >= a else np.zeros(0)
        seg = seg[np.isfinite(seg)]
        return float(seg.mean()) if len(seg) else float("nan")

    R: _Range | None = None
    for t in range(n):
        if known[t]:
            # -- a climax starts a range (from no range, from Phase A before the AR, or after Phase E)
            cl = climax_at(t)
            if cl is not None and (R is None or R.stage in ("A1", "E")):
                side, o, kind = cl
                R = _Range(FALL if side == STRENGTH else RISE, o, float(v[o]), float(lo[o] if side == STRENGTH else h[o]))
                for q in range(o, t + 1):
                    R.ext = min(R.ext, lo[q]) if R.after == FALL else max(R.ext, h[q])
                emit(t, "climax", side, o, kind, rel_volume=_r(rv[o]), rel_range=_r(rr[o]))
            elif R is not None:
                R = _step(R, t, s, h, lo, c, v, rr, rv, wide, vol_high, lower, avg_rng, struct, sig_bar, emit, depth_units, leg_mean)
            # -- the significant bar (independent of the range)
            prev_hi = struct.swing_high[t - 1] if t > 0 else np.nan
            prev_lo = struct.swing_low[t - 1] if t > 0 else np.nan
            with np.errstate(invalid="ignore"):
                big = bool(rr[t] > s.wide and rv[t] >= s.sig_vol)
            if big and np.isfinite(cp[t]):
                if cp[t] >= 0.5 and np.isfinite(prev_hi) and c[t] > prev_hi:
                    emit(t, "significant_bar", STRENGTH, t, rel_range=_r(rr[t]), rel_volume=_r(rv[t]), close_pos=_r(cp[t]))
                elif cp[t] <= 0.5 and np.isfinite(prev_lo) and c[t] < prev_lo:
                    emit(t, "significant_bar", WEAKNESS, t, rel_range=_r(rr[t]), rel_volume=_r(rv[t]), close_pos=_r(cp[t]))
        if R is not None:
            phase[t] = R.stage[0]
            lean[t] = R.lean
            after[t] = R.after
            rstart[t] = R.start
            if R.stage not in ("A1",) and np.isfinite(R.top):
                top[t], bottom[t] = R.top, R.bottom
            shake_x[t] = R.shake_ext if R.stage == "C" else np.nan
            if R.stage in ("D", "E") and R.break_start >= 0:
                seg = slice(R.break_start, t + 1)
                break_base[t] = float(np.min(lo[seg])) if R.lean == STRENGTH else float(np.max(h[seg]))
        # -- the bias: the side of a range that broke, else the structure regime
        regime = str(struct.regime[t])
        if R is not None and R.stage in ("D", "E") and R.lean:
            bias[t], bias_known[t] = R.lean, True
        elif regime:
            bias[t] = STRENGTH if regime == ss.UP else WEAKNESS if regime == ss.DOWN else ""
            bias_known[t] = True

    hit = {name: np.zeros(n, dtype=bool) for name in EVENTS}
    side_arr = {name: np.full(n, "", dtype=object) for name in EVENTS}
    for e in events:
        hit[e.name][e.bar] = True
        side_arr[e.name][e.bar] = e.side
    return Readings(n, events, hit, side_arr, phase, lean, after, top, bottom, shake_x, break_base, rstart, bias, bias_known,
                    known, has_volume, s)


def _r(x: float, nd: int = 2) -> float | None:
    return None if x is None or not np.isfinite(x) else round(float(x), nd)


def _step(R: _Range, t: int, s: WyckoffSettings, h: np.ndarray, lo: np.ndarray, c: np.ndarray, v: np.ndarray,  # noqa: C901
          rr: np.ndarray, rv: np.ndarray, wide: np.ndarray, vol_high: np.ndarray, lower: np.ndarray, avg_rng: np.ndarray,
          struct: ss.StructureSeries, sig_bar: vsa.Signals, emit: Any, depth_units: Any, leg_mean: Any) -> _Range | None:
    """One closed bar for an open range; the range after it (None: it ended)."""
    fall = R.after == FALL
    s_up, s_dn = STRENGTH, WEAKNESS
    if R.stage == "A1":
        R.ext = min(R.ext, lo[t]) if fall else max(R.ext, h[t])
        conf, bar = (struct.swing_high_confirm, struct.swing_high_bar) if fall else (struct.swing_low_confirm, struct.swing_low_bar)
        if conf[t] == t and bar[t] > R.start:
            p = int(bar[t])
            seg = slice(R.start, p + 1)
            far = float(struct.swing_high[t] if fall else struct.swing_low[t])
            near = float(np.min(lo[seg]) if fall else np.max(h[seg]))
            R.top, R.bottom = (far, near) if fall else (near, far)
            if not R.height > 0:
                return None
            R.ar_bar, R.stage = p, "A2"
            half = (p - R.start + 1) // 2
            emit(t, "ar", s_up if fall else s_dn, p, height_ranges=_r(depth_units(t, R.height)), bars=p - R.start,
                 vol_start=_r(leg_mean(R.start, R.start + max(half - 1, 0))), vol_end=_r(leg_mean(R.start + half, p)))
            return _a2_fail(R, t, s, c)
        if t - R.start > s.a_bars:
            return None
        return R
    if R.stage == "A2":
        if _a2_fail(R, t, s, c) is None:
            return None
        conf, bar, price = ((struct.swing_low_confirm, struct.swing_low_bar, struct.swing_low) if fall
                            else (struct.swing_high_confirm, struct.swing_high_bar, struct.swing_high))
        if conf[t] == t and bar[t] > R.ar_bar:
            p, x = int(bar[t]), float(price[t])
            beyond = (R.bottom - x) if fall else (x - R.top)
            if beyond > s.st_below * R.height:
                return None
            leg = v[R.ar_bar + 1:p + 1]
            leg = leg[np.isfinite(leg)]
            if len(leg) and np.isfinite(R.climax_vol) and float(leg.max()) < R.climax_vol:
                share = (x - R.bottom) / R.height if fall else (R.top - x) / R.height
                where = "slightly beyond the climax" if beyond > 0 else "in the half near the climax" if share < 0.5 else "in the half away from the climax"
                R.stage, R.b_start, R.a_len = "B", t, max(1, p - R.start)
                emit(t, "st", s_up if fall else s_dn, p, where, peak_vol_share=_r(float(leg.max()) / R.climax_vol),
                     phase_a_bars=R.a_len)
                return R
        if t - R.ar_bar > s.a_bars:
            return None
        return R
    if R.stage in ("B", "C"):
        _edges(R, t, s, h, lo, c, rr, rv, emit, depth_units)
        if R.stage == "C":
            _phase_c(R, t, s, h, lo, v, struct, sig_bar, emit, leg_mean)
        return R
    if R.stage == "D":
        up = R.lean == s_up
        edge = R.top if up else R.bottom
        inside = c[t] < edge if up else c[t] > edge
        if inside:
            emit(t, "failed_break", s_dn if up else s_up, R.break_bar)
            R.stage, R.lean, R.break_bar, R.break_start = "B", "", -1, -1
            R.pend = {"bottom": -1, "top": -1}
            return R
        zone = s.bu_zone * R.height
        if t > R.break_bar and t - R.break_bar <= s.bu_bars:
            # a back-up comes BACK: the move first left the zone over the broken edge, then a bar moving back (a close
            # against the break's direction) reaches into it
            seg = slice(R.break_start, t)
            away = (np.max(h[seg]) > edge + zone) if up else (np.min(lo[seg]) < edge - zone)
            into = lo[t] <= edge + zone if up else h[t] >= edge - zone
            back = c[t] < c[t - 1] if up else c[t] > c[t - 1]
            if away and into and back:
                if lower[t] and not wide[t] and not R.bu_done:
                    R.bu_done = True
                    emit(t, "back_up", R.lean, t, rel_volume=_r(rv[t]), rel_range=_r(rr[t]), break_bars=t - R.break_bar)
                    R.stage = "E"
                    return R
                if (vol_high[t] or wide[t]) and not R.heavy_done:
                    R.heavy_done = True
                    emit(t, "heavy_back_up", s_dn if up else s_up, t, rel_volume=_r(rv[t]), rel_range=_r(rr[t]))
        if t - R.break_bar > s.bu_bars:
            R.stage = "E"
        return R
    # Phase E: a close back inside the range ends it
    up = R.lean == s_up
    if (c[t] < R.top) if up else (c[t] > R.bottom):
        return None
    return R


def _a2_fail(R: _Range, t: int, s: WyckoffSettings, c: np.ndarray) -> _Range | None:
    """Before the ST: a close past the climax extreme by more than st_below x height, or past the AR extreme -> no range."""
    if R.after == FALL:
        bad = c[t] < R.bottom - s.st_below * R.height or c[t] > R.top
    else:
        bad = c[t] > R.top + s.st_below * R.height or c[t] < R.bottom
    return None if bad else R


def _edges(R: _Range, t: int, s: WyckoffSettings, h: np.ndarray, lo: np.ndarray, c: np.ndarray, rr: np.ndarray,  # noqa: C901
           rv: np.ndarray, emit: Any, depth_units: Any) -> None:
    """Phase B / C: pokes beyond an edge - back inside within k_back bars (a test or a shake), else a break."""
    for edge in ("bottom", "top"):
        if R.stage not in ("B", "C"):
            return
        lower_edge = edge == "bottom"
        level = R.bottom if lower_edge else R.top
        beyond = lo[t] < level if lower_edge else h[t] > level
        x = lo[t] if lower_edge else h[t]
        if R.pend[edge] < 0:
            if not beyond:
                continue
            R.pend[edge], R.pend_ext[edge], R.pend_ext_bar[edge] = t, float(x), t
        elif (x < R.pend_ext[edge]) if lower_edge else (x > R.pend_ext[edge]):
            R.pend_ext[edge], R.pend_ext_bar[edge] = float(x), t
        start, ext, ext_bar = R.pend[edge], R.pend_ext[edge], R.pend_ext_bar[edge]
        back = c[t] >= level if lower_edge else c[t] <= level
        side = STRENGTH if lower_edge else WEAKNESS             # the side a shake of this edge suggests
        if back:
            R.pend[edge] = -1
            depth = (level - ext) if lower_edge else (ext - level)
            units = depth_units(ext_bar, depth)
            info = {"depth_ranges": _r(units), "bars_beyond": t - start + 1, "rel_volume": _r(rv[ext_bar]), "rel_range": _r(rr[ext_bar])}
            if R.stage == "B" and start - R.b_start < s.b_vs_a * R.a_len:
                emit(t, "edge_test", WEAKNESS if lower_edge else STRENGTH, ext_bar, **info)
                continue
            if R.stage == "C" and R.shake_side == side and ((ext >= R.shake_ext) if lower_edge else (ext <= R.shake_ext)):
                continue                       # a poke that holds within the shake's extreme is part of its test area
            cls = _shake_class(s, units, rv[ext_bar], rr[ext_bar])
            R.stage, R.lean, R.shake_side = "C", side, side
            R.shake_ext, R.shake_ext_bar, R.shake_known = float(ext), ext_bar, t
            R.test_done = R.last_done = False
            emit(t, "shake", side, ext_bar, cls, **info)
            continue
        if t - start + 1 >= s.k_back:
            R.pend = {"bottom": -1, "top": -1}
            up = not lower_edge
            prior = R.lean
            form = ("without a shake" if R.stage == "B" or not prior
                    else "after a shake on the other side" if prior == (STRENGTH if up else WEAKNESS)
                    else "after a shake on this side that did not hold")
            R.stage, R.lean, R.break_bar, R.break_start = "D", STRENGTH if up else WEAKNESS, t, start
            R.bu_done = R.heavy_done = False
            emit(t, "break", R.lean, start, form, closes_beyond=t - start + 1, rel_volume=_r(rv[t]), rel_range=_r(rr[t]))
            return


def _shake_class(s: WyckoffSettings, units: float, rel_vol: float, rel_rng: float) -> str:
    if not (np.isfinite(units) and np.isfinite(rel_vol) and np.isfinite(rel_rng)):
        return "class unknown"
    if units >= s.deep and rel_vol > s.vol_high and rel_rng > s.wide:
        return "class 1 (deep, heavy volume, wide spread)"
    if units < s.deep and rel_vol < 1.0 and rel_rng < 1.0:
        return "class 3 (shallow, light volume, small spread)"
    return "class 2 (between)"


def _phase_c(R: _Range, t: int, s: WyckoffSettings, h: np.ndarray, lo: np.ndarray, v: np.ndarray,  # noqa: C901
             struct: ss.StructureSeries, sig_bar: vsa.Signals, emit: Any, leg_mean: Any) -> None:
    """After a shake: its test (a quiet bar holding beyond the shake extreme) and the last point inside the range."""
    up = R.shake_side == STRENGTH
    if not R.test_done and R.shake_known < t <= R.shake_known + s.test_bars:
        if up:
            quiet = bool(sig_bar.hit["no_supply"][t] or sig_bar.hit["test"][t])
            where = lo[t] > R.shake_ext and lo[t] <= R.bottom + s.test_zone * R.height
        else:
            quiet = bool(sig_bar.hit["no_demand"][t])
            where = h[t] < R.shake_ext and h[t] >= R.top - s.test_zone * R.height
        if quiet and where:
            R.test_done = True
            emit(t, "shake_test", R.shake_side, t, "no supply" if up and sig_bar.hit["no_supply"][t] else "test" if up else "no demand",
                 bars_after=t - R.shake_known)
    if R.last_done:
        return
    conf, bar, price = ((struct.swing_low_confirm, struct.swing_low_bar, struct.swing_low) if up
                        else (struct.swing_high_confirm, struct.swing_high_bar, struct.swing_high))
    if conf[t] != t or bar[t] <= R.shake_ext_bar:
        return
    p, x = int(bar[t]), float(price[t])
    seg = slice(R.shake_ext_bar, p + 1)
    far_bar = R.shake_ext_bar + int(np.argmax(h[seg]) if up else np.argmin(lo[seg]))
    far = float(h[far_bar] if up else lo[far_bar])
    reach = (far - R.bottom) / R.height if up else (R.top - far) / R.height
    holds = x > R.shake_ext if up else x < R.shake_ext
    if far_bar >= p or not holds or reach < s.mid_share:
        return
    move_v, back_v = leg_mean(R.shake_ext_bar, far_bar), leg_mean(far_bar + 1, p)
    if np.isfinite(move_v) and np.isfinite(back_v) and back_v < move_v:
        R.last_done = True
        emit(t, "last_point", R.shake_side, p, reach_share=_r(reach), leg_volume_share=_r(back_v / move_v))


_CACHE: list[tuple[pd.DataFrame, int, Any, WyckoffSettings, str, Readings]] = []
_CACHE_SIZE = 8


def cached(frame: pd.DataFrame, settings: WyckoffSettings, tf: str = "1h") -> Readings:
    """`evaluate` once per frame object, settings and timeframe - the several Wyckoff(...) calls of one screen share it."""
    last = frame.index[-1] if len(frame) else None
    for f, n_, l_, s_, tf_, out in _CACHE:
        if f is frame and n_ == len(frame) and l_ == last and s_ == settings and tf_ == tf:
            return out
    out = evaluate(frame, settings, tf)
    _CACHE.append((frame, len(frame), last, settings, tf, out))
    del _CACHE[:-_CACHE_SIZE]
    return out


# ---------------------------------------------------------------------------------------------------------------------
# "Why?" - structure words only: no instruction, no single price
# ---------------------------------------------------------------------------------------------------------------------
_RANGE_WORD = {FALL: "a range after a fall", RISE: "a range after a rise"}


def why(r: Readings, e: Event) -> dict[str, Any]:
    """The reading behind one event, for the chart's "Why?" card: its name, side, phase and the facts in words."""
    i, s = e.bar, r.settings
    ph = str(r.phase[i]) or "-"
    ctx = _RANGE_WORD.get(str(r.after[i]), "no range")
    f = e.info
    parts: list[str] = []
    if e.name == "climax":
        parts.append(f"{e.variant} - volume {f.get('rel_volume')} x and spread {f.get('rel_range')} x their averages of {s.avg_n} bars at the end of the move")
    elif e.name == "ar":
        parts.append(f"the first swing back after the climax, {e.bar - e.origin} bars to confirm; the range height is "
                     f"{f.get('height_ranges')} average ranges; volume along the leg from {f.get('vol_start')} to {f.get('vol_end')} (mean)")
    elif e.name == "st":
        parts.append(f"the first swing back toward the climax, {e.variant}; the peak volume of the leg is {f.get('peak_vol_share')} "
                     f"of the climax bar's; Phase A took {f.get('phase_a_bars')} bars")
    elif e.name in ("edge_test", "shake"):
        parts.append(f"beyond the range edge by {f.get('depth_ranges')} average ranges and back inside within {f.get('bars_beyond')} "
                     f"bar(s); volume {f.get('rel_volume')} x, spread {f.get('rel_range')} x at the extreme")
        if e.name == "edge_test":
            parts.append(f"Phase B was still shorter than {s.b_vs_a:g} x Phase A, so it is a test of the edge")
        else:
            parts.append(e.variant)
    elif e.name == "shake_test":
        parts.append(f"a quiet bar ({e.variant}) {f.get('bars_after')} bars after the shake, holding beyond the shake's extreme")
    elif e.name == "last_point":
        parts.append(f"after the shake the move reached {f.get('reach_share')} of the range height, then a swing back held beyond the "
                     f"shake's extreme; the pullback leg's volume is {f.get('leg_volume_share')} of the move's")
    elif e.name == "break":
        parts.append(f"{f.get('closes_beyond')} closes in a row beyond the range edge ({e.variant}); volume {f.get('rel_volume')} x, "
                     f"spread {f.get('rel_range')} x on the last one")
    elif e.name == "back_up":
        parts.append(f"{f.get('break_bars')} bars after the break, a bar came back to the broken edge and closed on the break side on "
                     f"lower volume ({f.get('rel_volume')} x) and a spread that is not wide ({f.get('rel_range')} x)")
    elif e.name == "heavy_back_up":
        parts.append(f"the pullback to the broken edge came on volume {f.get('rel_volume')} x / spread {f.get('rel_range')} x - "
                     "the other side is still active; another test is read")
    elif e.name == "failed_break":
        parts.append("a close back inside the range after the break")
    elif e.name == "significant_bar":
        parts.append(f"spread {f.get('rel_range')} x and volume {f.get('rel_volume')} x their averages, close at {f.get('close_pos')} of "
                     "the bar, beyond the last confirmed swing")
    label = LABEL[e.name][e.side] if e.side in LABEL[e.name] else NAMES[e.name]
    text = f"{label}: " + "; ".join(p for p in parts if p) + f". {ctx[0].upper() + ctx[1:]}, phase {ph}."
    if e.origin != e.bar:
        text += f" It is about the bar {e.bar - e.origin} bar{'s' if e.bar - e.origin != 1 else ''} before; known on this bar's close."
    text += f" It suggests {e.side}; it is a description of the bars, not advice."
    return {"name": e.name, "label": label, "short": SHORT[e.name].get(e.side, ""), "side": e.side, "bar": e.bar, "origin": e.origin,
            "variant": e.variant, "phase": ph, "text": text}


__all__ = ["WyckoffSettings", "Readings", "Event", "settings_from", "reference_settings", "evaluate", "cached", "why", "EVENTS",
           "STATES", "NAMES_ALL", "NAMES", "LABEL", "SHORT", "LEVELS", "SIDE_CHOICES", "STRENGTH", "WEAKNESS", "NO_VOLUME",
           "SETTINGS_TABLE", "OWN_SETTINGS", "VSA_USED", "REFERENCE_LABEL", "PHASES", "FALL", "RISE"]
