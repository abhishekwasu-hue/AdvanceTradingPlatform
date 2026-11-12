"""M99 P3: the volume spread pack - thirteen bar-reading detectors on the per-bar classifier (app/price_action/bar_class.py),
flag `vsa_pack` (off by default). The ONE evaluation the ScreenQL function `VSA(...)` (scanners, library screens, the
strategy templates, so the AI Trading Bot), the chart layer (app/charts/vsa_layer.py) and the evidence runs read.

Pure functions on a bar frame (open / high / low / close / volume, oldest first, CLOSED bars only); nothing here reads
a database, a clock or the network. Signals only: a detector describes what a bar shows (spread, volume, close
position, the structure before it); it never places, sizes or changes an order and it carries no level to trade at.

Words (our own): spread = the bar's range against the average range of the `avg_n` bars before it (bar_class); volume
class the same on volume; close position = where the close sits in the bar's own range (thirds); up / down bar = the
close against the previous close. "Lower volume" = lower than each of the `prev_bars` bars before (the classic test-bar
rule). Classes are bar_class's: NARROW < `narrow`, WIDE > `wide`, ULTRA_WIDE > `ultra_wide`; volume HIGH > `vol_high`,
ULTRA > `vol_ultra`.

Structure before a bar (the background, never the bar itself): the `trend_bars` bars before it against the `trend_bars`
before those - a higher high AND a higher low = rising, a lower high AND a lower low = falling, else sideways.
A strong background = rising, or a strength climax (selling climax, stopping volume, reverse up-thrust, shake-out,
absorption at a low) in the `bg_bars` bars before; a weak background = falling, or a buying climax / up-thrust in the
`bg_bars` bars before.

The detectors (each a user-visible name; the side is what the bar suggests, not an instruction):

| name | the bar | side | background (when `background` is on) |
|---|---|---|---|
| no_demand | up bar, NARROW spread, lower volume | weakness | weak |
| no_supply | down bar, spread not WIDE, lower volume, close not in the low third | strength | strong |
| test | down bar into the range of a HIGH-volume bar of the last `area_bars` (within `area_tol` average ranges), lower volume, close in the high third | strength | strong |
| failed_test | the test's shape on HIGH or ULTRA volume | weakness | strong (where a test is looked for) |
| up_thrust | WIDE spread or more, high above the `new_bars` highs before, close in the low third (variant by volume) | weakness | rising before |
| reverse_up_thrust | WIDE or more, low below the `new_bars` lows before, close in the high third, HIGH volume or more | strength | falling before |
| stopping_volume | down bar on ULTRA volume closing in the high third; or closing in the low third and the next bar closes level or higher (known on that next bar) | strength | falling before |
| selling_climax | down bar, ULTRA_WIDE spread, ULTRA volume, close not in the low third | strength | falling before |
| buying_climax | up bar, ULTRA volume, close not in the high third, ULTRA_WIDE spread or NARROW into the `new_bars` highs | weakness | rising before |
| shake_out | a WIDE-or-more down bar after a rise, then within `shake_bars` bars a lower-volume bar back inside its range that holds its low (known on that bar) | strength | rising before the wide bar |
| falling_pressure | down bar, WIDE or more, lower volume, close in the low third; or below the `new_bars` lows and closing in the high third | strength | falling before |
| absorption | down bar, WIDE or more, ULTRA volume, close in the high third (falling before); or up bar, WIDE or more, HIGH volume or more, closing above the `new_bars` highs in the high third | strength | first form: falling before |
| effort_vs_result | effort without result: HIGH volume or more on a NARROW spread; or result without effort: WIDE spread or more on lower volume. An up bar reads as weakness, a down bar as strength | both | effort: a move in the bar's direction before; result without effort: the weak (up bar) or strong (down bar) background |

Next-bar confirmation (`confirm`, off by default): a signal is shown on the NEXT bar, and only when that bar closes
higher (strength) or lower (weakness) than the signal bar.

No volume data (owner decision M93-Q3; an index takes its near-month future's volume upstream): where a detector needs
the volume of a bar and it is missing or 0, the answer is unknown (NaN), never a guess and never "no signal"; a frame
with no volume at all gives `NO_VOLUME`. Closed bars only and no look-ahead: every answer at bar t reads bars <= t only
(a run cut at t gives the same answers up to t; tested).

Every number is a setting with a reference value ("reference - edit before use"), never a recommendation.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from typing import Any

import numpy as np
import pandas as pd

from app.price_action import bar_class as bc
from app.price_action.zones.detect import average_range

STRENGTH, WEAKNESS = "strength", "weakness"
SIDES: tuple[str, ...] = (STRENGTH, WEAKNESS)
DETECTORS: tuple[str, ...] = ("no_demand", "no_supply", "test", "failed_test", "up_thrust", "reverse_up_thrust", "stopping_volume",
                              "selling_climax", "buying_climax", "shake_out", "falling_pressure", "absorption", "effort_vs_result")
NAMES: dict[str, str] = {"no_demand": "No demand", "no_supply": "No supply", "test": "Test", "failed_test": "Failed test",
                         "up_thrust": "Up-thrust", "reverse_up_thrust": "Reverse up-thrust", "stopping_volume": "Stopping volume",
                         "selling_climax": "Selling climax", "buying_climax": "Buying climax", "shake_out": "Shake-out",
                         "falling_pressure": "Falling pressure", "absorption": "Absorption", "effort_vs_result": "Effort vs result"}
# the chart's "Why?" words: structure words only, so the two climaxes are named by where they come (no trade wording)
WHY_NAMES: dict[str, str] = {**NAMES, "selling_climax": "Climax at the end of a fall", "buying_climax": "Climax at the end of a rise"}
SHORT: dict[str, str] = {"no_demand": "ND", "no_supply": "NS", "test": "T", "failed_test": "FT", "up_thrust": "UT",
                         "reverse_up_thrust": "RUT", "stopping_volume": "SV", "selling_climax": "SC", "buying_climax": "BC",
                         "shake_out": "SO", "falling_pressure": "FP", "absorption": "AB", "effort_vs_result": "EvR"}
# the side each detector suggests (effort_vs_result: by the bar's direction, None here)
SIDE_OF: dict[str, str | None] = {"no_demand": WEAKNESS, "no_supply": STRENGTH, "test": STRENGTH, "failed_test": WEAKNESS,
                                  "up_thrust": WEAKNESS, "reverse_up_thrust": STRENGTH, "stopping_volume": STRENGTH,
                                  "selling_climax": STRENGTH, "buying_climax": WEAKNESS, "shake_out": STRENGTH,
                                  "falling_pressure": STRENGTH, "absorption": STRENGTH, "effort_vs_result": None}
SIDE_CHOICES: tuple[str, ...] = ("any", STRENGTH, WEAKNESS)
NO_VOLUME = "no volume data"
REFERENCE_LABEL = bc.REFERENCE_LABEL
RISING, FALLING, SIDEWAYS = "rising", "falling", "sideways"
STRENGTH_CONTEXT: tuple[str, ...] = ("selling_climax", "stopping_volume", "reverse_up_thrust", "shake_out", "absorption")
WEAKNESS_CONTEXT: tuple[str, ...] = ("buying_climax", "up_thrust")

# (name, kind, reference, bounds): the pack's own settings beside bar_class.SETTINGS_TABLE (kind "flag" = on / off)
OWN_SETTINGS: tuple[tuple[str, str, Any, tuple[float, float]], ...] = (
    ("prev_bars", "bars", 2, (1, 10)),
    ("new_bars", "bars", 20, (2, 500)),
    ("trend_bars", "bars", 10, (2, 250)),
    ("bg_bars", "bars", 20, (1, 500)),
    ("area_bars", "bars", 20, (1, 500)),
    ("area_tol", "num", 0.25, (0.0, 5.0)),
    ("shake_bars", "bars", 5, (1, 50)),
    ("background", "flag", True, (0, 1)),
    ("confirm", "flag", False, (0, 1)),
)
SETTINGS_TABLE: tuple[tuple[str, str, Any, tuple[float, float]], ...] = bc.SETTINGS_TABLE + OWN_SETTINGS


@dataclass(frozen=True)
class VsaSettings:
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
    shake_bars: int = 5
    background: bool = True
    confirm: bool = False

    def bar_class(self) -> bc.BarClassSettings:
        return bc.BarClassSettings(**{f.name: getattr(self, f.name) for f in fields(bc.BarClassSettings)})

    def warmup(self) -> int:
        """Bars before the first answer can exist (the longest look-back of any detector, plus its background)."""
        return max(self.avg_n, 2 * self.trend_bars, self.new_bars, self.area_bars) + self.bg_bars + self.shake_bars + 2


def settings_from(values: dict[str, Any] | None = None) -> VsaSettings:
    """VsaSettings from named values (None or left out: the reference). Raises ValueError on an unknown name, a value
    outside its bounds or of the wrong kind, or `prev_bars` above `avg_n` (the bars compared with must have volume data
    read the same way as the average)."""
    values = {k: v for k, v in (values or {}).items() if v is not None}
    own = {name for name, *_ in OWN_SETTINGS}
    unknown = sorted(set(values) - own - {name for name, *_ in bc.SETTINGS_TABLE})
    if unknown:
        raise ValueError(f"unknown volume spread setting(s): {', '.join(unknown)}")
    base = bc.settings_from({k: v for k, v in values.items() if k not in own})
    out: dict[str, Any] = asdict(base)
    for name, kind, _, (lo, hi) in OWN_SETTINGS:
        if name not in values:
            continue
        value = values[name]
        if kind == "flag":
            if not isinstance(value, (bool, np.bool_)) and value not in (0, 1):
                raise ValueError(f"{name} must be on or off, got {value!r}")
            out[name] = bool(value)
            continue
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
    s = VsaSettings(**out)
    if s.prev_bars > s.avg_n:
        raise ValueError(f"prev_bars must be at most avg_n, got {s.prev_bars} and {s.avg_n}")
    return s


def reference_settings() -> list[dict[str, Any]]:
    """The settings with their reference values and the label, for a settings form."""
    d = VsaSettings()
    return [{"name": f.name, "value": getattr(d, f.name), "label": REFERENCE_LABEL} for f in fields(d)]


# ---------------------------------------------------------------------------------------------------------------------
# the evaluation
# ---------------------------------------------------------------------------------------------------------------------
@dataclass
class Signals:
    """Per detector, aligned with the frame (bar i = the bar on which the signal is KNOWN): `hit` (bool), `known` (False
    where the answer cannot be read - no volume data, or the warm-up), `origin` (the bar the reading is about: the bar
    itself, or an earlier one for the two-bar readings and for a confirmed signal), `variant` (a short word, '' when
    none) and `side` (STRENGTH / WEAKNESS). `structure` is the background word before each bar; `has_volume` False
    when no bar of the frame has volume data."""
    n: int
    hit: dict[str, np.ndarray]
    known: dict[str, np.ndarray]
    origin: dict[str, np.ndarray]
    variant: dict[str, np.ndarray]
    side: dict[str, np.ndarray]
    structure: np.ndarray
    strong_bg: np.ndarray
    weak_bg: np.ndarray
    has_volume: bool
    classes: pd.DataFrame
    settings: VsaSettings

    def value(self, name: str, side: str = "any") -> np.ndarray:
        """1.0 where `name` holds (on `side` when given), 0.0 where it does not, NaN where it cannot be read."""
        hit = self.hit[name] if side == "any" else self.hit[name] & (self.side[name] == side)
        out = hit.astype(float)
        out[~self.known[name]] = np.nan
        return out

    def rows(self, names: tuple[str, ...] = DETECTORS) -> list[tuple[int, str]]:
        """(bar, detector) of every signal, oldest first."""
        out = [(int(i), name) for name in names for i in np.flatnonzero(self.hit[name])]
        return sorted(out)


def _rolling_max_before(x: np.ndarray, k: int) -> np.ndarray:
    """Max of x over the k bars BEFORE each bar (NaN until k bars exist)."""
    return pd.Series(x).shift(1).rolling(k, min_periods=k).max().to_numpy(float)


def _rolling_min_before(x: np.ndarray, k: int) -> np.ndarray:
    return pd.Series(x).shift(1).rolling(k, min_periods=k).min().to_numpy(float)


def _any_before(mask: np.ndarray, k: int) -> np.ndarray:
    """True where `mask` held on any of the k bars before (never the bar itself)."""
    return pd.Series(mask.astype(float)).shift(1).rolling(k, min_periods=1).max().fillna(0).to_numpy(float) > 0


def structure_before(high: np.ndarray, low: np.ndarray, k: int) -> np.ndarray:
    """RISING / FALLING / SIDEWAYS / '' (warm-up) per bar from the 2k bars BEFORE it: the last k against the k before."""
    hi_now, lo_now = _rolling_max_before(high, k), _rolling_min_before(low, k)
    hi_was = pd.Series(hi_now).shift(k).to_numpy(float)
    lo_was = pd.Series(lo_now).shift(k).to_numpy(float)
    out = np.full(len(high), "", dtype=object)
    ok = np.isfinite(hi_now) & np.isfinite(lo_now) & np.isfinite(hi_was) & np.isfinite(lo_was)
    with np.errstate(invalid="ignore"):
        up = ok & (hi_now > hi_was) & (lo_now > lo_was)
        down = ok & (hi_now < hi_was) & (lo_now < lo_was)
    out[ok] = SIDEWAYS
    out[up] = RISING
    out[down] = FALLING
    return out


def evaluate(frame: pd.DataFrame, settings: VsaSettings | None = None) -> Signals:
    """Every detector on the frame's closed bars (the module docstring's table)."""
    s = settings or VsaSettings()
    n = len(frame)
    h, lo, c = (frame[k].astype(float).to_numpy() for k in ("high", "low", "close"))
    v = bc.volume_data(frame).to_numpy(float)
    rr = bc.relative_range(frame, s.avg_n).to_numpy(float)
    rv = bc.relative_volume(frame, s.avg_n).to_numpy(float)
    rng = h - lo
    with np.errstate(divide="ignore", invalid="ignore"):
        cp = np.where(rng > 0, (c - lo) / rng, np.nan)
    prev_c = np.r_[np.nan, c[:-1]] if n else c
    with np.errstate(invalid="ignore"):
        up = c > prev_c
        down = c < prev_c
        narrow = rr < s.narrow
        wide = rr > s.wide
        ultra_w = rr > s.ultra_wide
        known_spread = np.isfinite(rr)
        vol_high = rv > s.vol_high
        vol_ultra = rv > s.vol_ultra
        cp_low = cp < s.close_part
        cp_high = cp > 1 - s.close_part
    vol_ok = np.isfinite(rv) & known_spread              # the bar's own volume and its average both read
    lower = np.ones(n, dtype=bool)
    for k in range(1, s.prev_bars + 1):
        prev_v = np.r_[np.full(min(k, n), np.nan), v[:-k]] if n > k else np.full(n, np.nan)
        with np.errstate(invalid="ignore"):
            lower &= v < prev_v
    lower &= vol_ok
    hi_before = _rolling_max_before(h, s.new_bars)
    lo_before = _rolling_min_before(lo, s.new_bars)
    with np.errstate(invalid="ignore"):
        new_high = h > hi_before
        new_low = lo < lo_before
        close_above = c > hi_before
    struct = structure_before(h, lo, s.trend_bars)
    rising, falling = struct == RISING, struct == FALLING
    bg = s.background

    hit: dict[str, np.ndarray] = {}
    known: dict[str, np.ndarray] = {}
    origin: dict[str, np.ndarray] = {}
    variant: dict[str, np.ndarray] = {}
    side: dict[str, np.ndarray] = {}
    idx = np.arange(n)

    warm = idx >= s.warmup() - 1                        # the longest look-back read: before it, unknown (never a no)

    def put(name: str, h_: np.ndarray, k_: np.ndarray, var: np.ndarray | None = None, org: np.ndarray | None = None,
            sd: np.ndarray | None = None) -> None:
        k_ = k_ & warm
        hit[name] = h_ & k_
        known[name] = k_
        origin[name] = idx.copy() if org is None else org
        variant[name] = np.full(n, "", dtype=object) if var is None else var
        side[name] = np.full(n, SIDE_OF[name] or "", dtype=object) if sd is None else sd

    def need(mask: np.ndarray) -> np.ndarray:
        return mask if bg else np.ones(n, dtype=bool)

    # -- end-of-move readings first: they also make the strong / weak background
    put("selling_climax", down & ultra_w & vol_ultra & ~cp_low & need(falling), vol_ok)
    put("buying_climax", up & vol_ultra & ~cp_high & (ultra_w | (narrow & new_high)) & need(rising), vol_ok,
        np.where(narrow & new_high & ~ultra_w, "narrow into new highs", "ultra-wide spread").astype(object))
    sv_close_high = down & vol_ultra & cp_high & need(falling)
    sv_low = down & vol_ultra & cp_low & need(falling)
    sv_next = np.zeros(n, dtype=bool)
    sv_next_known = np.zeros(n, dtype=bool)
    if n > 1:
        with np.errstate(invalid="ignore"):
            sv_next[1:] = sv_low[:-1] & vol_ok[:-1] & (c[1:] >= c[:-1])
        sv_next_known[1:] = vol_ok[:-1]
    sv_hit = sv_close_high | sv_next
    put("stopping_volume", sv_hit, np.where(sv_next, sv_next_known, vol_ok),
        np.where(sv_next, "closed low, next bar level or higher", "closed high").astype(object), np.where(sv_next, idx - 1, idx))
    ut_var = np.where(vol_high, "high volume", np.where(lower, "lower volume", "average volume")).astype(object)
    put("up_thrust", wide & new_high & cp_low & need(rising), vol_ok, ut_var)
    put("reverse_up_thrust", wide & new_low & cp_high & vol_high & need(falling), vol_ok)
    ab_low = down & wide & vol_ultra & cp_high & need(falling)
    ab_break = up & wide & vol_high & close_above & cp_high
    put("absorption", ab_low | ab_break, vol_ok,
        np.where(ab_low, "at the end of a fall", "through the highs before").astype(object))
    # shake-out: a wide down bar after a rise, then a lower-volume bar back inside its range holding its low
    shake_bar = down & wide & need(rising)
    so_hit = np.zeros(n, dtype=bool)
    so_org = idx.copy()
    for r in range(1, n):
        if not lower[r]:
            continue
        for k in range(1, min(s.shake_bars, r) + 1):
            sb = r - k
            if not shake_bar[sb]:
                continue
            seg = lo[sb + 1:r + 1]
            if lo[r] <= h[sb] and seg.min() >= lo[sb]:
                earlier = [q for q in range(sb + 1, r) if lower[q] and lo[q] <= h[sb] and lo[sb + 1:q + 1].min() >= lo[sb]]
                if not earlier:
                    so_hit[r] = True
                    so_org[r] = sb
                    break
    put("shake_out", so_hit, vol_ok, None, so_org)

    strong_bg = rising | _any_before(np.logical_or.reduce([hit[k] for k in STRENGTH_CONTEXT]), s.bg_bars)
    weak_bg = falling | _any_before(np.logical_or.reduce([hit[k] for k in WEAKNESS_CONTEXT]), s.bg_bars)

    # -- the test-bar readings
    put("no_demand", up & narrow & lower & need(weak_bg), vol_ok)
    put("no_supply", down & ~wide & lower & ~cp_low & known_spread & need(strong_bg), vol_ok)
    avg_rng = average_range(frame, s.avg_n, s.avg_n).shift(1).to_numpy(float) if n else np.zeros(0)
    in_area = np.zeros(n, dtype=bool)
    for k in range(1, s.area_bars + 1):
        if k >= n:
            break
        hv = np.r_[np.zeros(k, dtype=bool), vol_high[:-k]]
        ah = np.r_[np.full(k, np.nan), h[:-k]]
        al = np.r_[np.full(k, np.nan), lo[:-k]]
        tol = s.area_tol * avg_rng
        with np.errstate(invalid="ignore"):
            in_area |= hv & (lo <= ah + tol) & (lo >= al - tol)
    put("test", down & in_area & lower & cp_high & need(strong_bg), vol_ok)
    put("failed_test", down & in_area & vol_high & cp_high & need(strong_bg), vol_ok)
    put("falling_pressure", down & wide & ((lower & cp_low) | (new_low & cp_high & ~vol_high)) & need(falling), vol_ok,
        np.where(new_low & cp_high, "below the lows before, closing high", "closing low on lower volume").astype(object))
    effort = vol_high & narrow
    no_effort = lower & wide
    evr_side = np.where(up, WEAKNESS, np.where(down, STRENGTH, "")).astype(object)
    evr_bg = np.where(effort, np.where(up, rising, falling), np.where(up, weak_bg, strong_bg))
    put("effort_vs_result", (effort | no_effort) & (up | down) & need(evr_bg), vol_ok,
        np.where(effort, "effort without result", "result without effort").astype(object), None, evr_side)

    if s.confirm:
        for name in DETECTORS:
            h0, k0, o0, sd0 = hit[name], known[name], origin[name], side[name]
            ch = np.zeros(n, dtype=bool)
            ck = np.zeros(n, dtype=bool)
            if n > 1:
                with np.errstate(invalid="ignore"):
                    better = np.where(sd0[:-1] == STRENGTH, c[1:] > c[:-1], c[1:] < c[:-1])
                ch[1:] = h0[:-1] & better
                ck[1:] = k0[:-1]
            hit[name] = ch & ck
            known[name] = ck
            origin[name] = np.r_[0, o0[:-1]] if n else o0
            variant[name] = np.r_[np.array([""], dtype=object), variant[name][:-1]] if n else variant[name]
            side[name] = np.r_[np.array([""], dtype=object), sd0[:-1]] if n else sd0
    has_volume = bool(np.isfinite(v).any())
    classes = pd.DataFrame({"rel_range": rr, "rel_volume": rv, "close_pos": cp}, index=frame.index)
    return Signals(n, hit, known, origin, variant, side, struct, strong_bg, weak_bg, has_volume, classes, s)


_CACHE: list[tuple[pd.DataFrame, int, Any, VsaSettings, Signals]] = []
_CACHE_SIZE = 8


def cached(frame: pd.DataFrame, settings: VsaSettings) -> Signals:
    """`evaluate` once per frame object and settings - the several VSA(...) calls of one screen read one evaluation."""
    last = frame.index[-1] if len(frame) else None
    for f, n_, l_, s_, out in _CACHE:
        if f is frame and n_ == len(frame) and l_ == last and s_ == settings:
            return out
    out = evaluate(frame, settings)
    _CACHE.append((frame, len(frame), last, settings, out))
    del _CACHE[:-_CACHE_SIZE]
    return out


# ---------------------------------------------------------------------------------------------------------------------
# "Why?" - structure words only: no instruction, no single price
# ---------------------------------------------------------------------------------------------------------------------
SPREAD_WORD = {bc.NARROW: "a narrow spread", bc.AVERAGE: "an average spread", bc.WIDE: "a wide spread", bc.ULTRA_WIDE: "an ultra-wide spread"}
CLOSE_WORD = {bc.LOW: "in the low third", bc.MIDDLE: "in the middle third", bc.HIGH: "in the high third"}


def _cls(x: float, cuts: tuple[float, float, float], words: tuple[str, str, str, str]) -> str:
    if not np.isfinite(x):
        return "unknown"
    out = words[1]
    if x < cuts[0]:
        out = words[0]
    if x > cuts[1]:
        out = words[2]
    if x > cuts[2]:
        out = words[3]
    return out


def why(sig: Signals, name: str, i: int) -> dict[str, Any]:
    """The reading behind one signal: its name, side, the bar it reads, spread / volume / close in words with their
    multiples of the average, the structure before it and the background - for the chart's "Why?" card."""
    s = sig.settings
    j = int(sig.origin[name][i])
    rr, rv, cp = (float(sig.classes[k].iloc[j]) for k in ("rel_range", "rel_volume", "close_pos"))
    spread = _cls(rr, (s.narrow, s.wide, s.ultra_wide), ("narrow", "average", "wide", "ultra-wide"))
    volume = _cls(rv, (s.vol_low, s.vol_high, s.vol_ultra), ("low", "average", "high", "ultra-high"))
    close = "unknown" if not np.isfinite(cp) else "low" if cp < s.close_part else "high" if cp > 1 - s.close_part else "middle"
    structure = str(sig.structure[j]) or "not enough bars"
    side = str(sig.side[name][i])
    bg = "strong" if sig.strong_bg[j] else "weak" if sig.weak_bg[j] else "neither strong nor weak"
    parts = [f"{spread} spread ({rr:.2f} x the average range of {s.avg_n} bars)" if np.isfinite(rr) else "spread unknown",
             f"{volume} volume ({rv:.2f} x the average of {s.avg_n} bars)" if np.isfinite(rv) else NO_VOLUME,
             f"close in the {close} third" if close != "unknown" else "no range"]
    text = f"{WHY_NAMES[name]}: " + ", ".join(parts) + f". Structure before: {structure} over {s.trend_bars} bars; background {bg}."
    var = str(sig.variant[name][i])
    if var:
        text += f" Form: {var}."
    if j != i:
        text += f" Read from the bar {i - j} bar{'s' if i - j != 1 else ''} before" + (", confirmed by the next close." if s.confirm else ".")
    text += f" It suggests {side}; it is a description of the bars, not advice."
    return {"name": name, "label": WHY_NAMES[name], "short": SHORT[name], "side": side, "bar": i, "origin": j, "variant": var,
            "spread": spread, "rel_range": None if not np.isfinite(rr) else round(rr, 2), "volume": volume,
            "rel_volume": None if not np.isfinite(rv) else round(rv, 2), "close": close, "structure": structure,
            "background": bg, "text": text}


__all__ = ["VsaSettings", "Signals", "settings_from", "reference_settings", "evaluate", "cached", "why", "structure_before",
           "DETECTORS", "NAMES", "WHY_NAMES", "SHORT", "SIDE_OF", "SIDES", "SIDE_CHOICES", "STRENGTH", "WEAKNESS", "NO_VOLUME",
           "SETTINGS_TABLE", "OWN_SETTINGS", "REFERENCE_LABEL", "RISING", "FALLING", "SIDEWAYS", "STRENGTH_CONTEXT",
           "WEAKNESS_CONTEXT", "SPREAD_WORD", "CLOSE_WORD"]
