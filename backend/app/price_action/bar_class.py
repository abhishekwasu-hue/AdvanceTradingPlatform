"""M99 B1-a: a per-bar classifier - relative range, relative volume, spread class, close position, bar direction and
volume class (flag `m99_primitives`, off by default, for the ScreenQL functions).

Pure functions on a bar frame (open / high / low / close / volume, oldest first, closed bars only); nothing here reads
a database, a clock or the network. Signals only: a class is a description of a bar, never an order or a trading rule.

Definitions (our own words):

* Range of a bar: high - low.
* Average range at a bar: the mean range of the `avg_n` bars BEFORE it (the bar itself is never measured against
  itself) - `zones.detect.average_range` of the bars before, so the two definitions cannot drift. Missing until
  `avg_n` bars exist before the bar.
* Relative range: range / average range. Missing where the average is missing or 0.
* Relative volume: the same rule on volume - volume / the mean volume of the `avg_n` bars before. A missing or 0
  volume is no volume data (owner decision M93-Q3): it is never averaged in and never read as a traded volume, so a
  window holding one gives a missing value, never a guess. On positive volumes it equals the screener's RelVolume
  (`screener.breakouts.screens.Bars.rvol`, checked by tests/test_m99_b1a_bar_class.py).
* Spread class, from relative range: NARROW below `narrow`, WIDE above `wide`, ULTRA_WIDE above `ultra_wide`, else
  AVERAGE (a value on a boundary takes the lower class). Missing where the relative range is missing.
* Close position, where the close sits in the bar's own range, (close - low) / range: LOW below `close_part`, HIGH
  above 1 - `close_part`, else MIDDLE (`close_part` 1/3: thirds). Missing on a bar with no range (high = low).
* Bar direction against the previous close: UP (higher), DOWN (lower), LEVEL (equal). Missing on the first bar.
* Volume class, from relative volume: LOW below `vol_low`, HIGH above `vol_high`, ULTRA above `vol_ultra`, else
  AVERAGE. NO_VOLUME where the bar's own volume is missing or 0; missing where the bar has volume but the average
  cannot be read (warm-up, or a window holding a bar without volume data).

Reference values (each a setting, shown "reference - edit before use"; never a recommendation): avg_n 20, narrow 0.7,
wide 1.3, ultra_wide 2.0, vol_low 0.7, vol_high 1.3, vol_ultra 2.0, close_part 1/3.

No look-ahead: every value at bar t reads bars <= t only, so a run on the bars cut at t gives the same values up to t
as the full run (tested; the ScreenQL functions are in the Z10-a harness, app/quality/causal_cases.py).
"""
from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any

import numpy as np
import pandas as pd

from app.price_action.zones.detect import average_range

NARROW, AVERAGE, WIDE, ULTRA_WIDE = "NARROW", "AVERAGE", "WIDE", "ULTRA_WIDE"
SPREAD_CLASSES: tuple[str, ...] = (NARROW, AVERAGE, WIDE, ULTRA_WIDE)
LOW, MIDDLE, HIGH = "LOW", "MIDDLE", "HIGH"
CLOSE_POSITIONS: tuple[str, ...] = (LOW, MIDDLE, HIGH)
UP, DOWN, LEVEL = "UP", "DOWN", "LEVEL"
DIRECTIONS: tuple[str, ...] = (UP, DOWN, LEVEL)
ULTRA, NO_VOLUME = "ULTRA", "NO_VOLUME"
VOLUME_CLASSES: tuple[str, ...] = (LOW, AVERAGE, HIGH, ULTRA, NO_VOLUME)

# (name, kind, reference, bounds) - the one list of the classifier's settings; the ScreenQL arguments mirror it
# (registry.BAR_CLASS_KWARGS, checked by tests/test_m99_b1a_bar_class.py)
SETTINGS_TABLE: tuple[tuple[str, str, Any, tuple[float, float]], ...] = (
    ("avg_n", "bars", 20, (1, 500)),
    ("narrow", "num", 0.7, (0.01, 10.0)),
    ("wide", "num", 1.3, (0.01, 10.0)),
    ("ultra_wide", "num", 2.0, (0.01, 20.0)),
    ("vol_low", "num", 0.7, (0.01, 10.0)),
    ("vol_high", "num", 1.3, (0.01, 10.0)),
    ("vol_ultra", "num", 2.0, (0.01, 20.0)),
    ("close_part", "num", 1 / 3, (0.01, 0.5)),
)
# (lower, upper): the lower setting must be below the upper one
ORDERED: tuple[tuple[str, str], ...] = (("narrow", "wide"), ("wide", "ultra_wide"), ("vol_low", "vol_high"), ("vol_high", "vol_ultra"))
REFERENCE_LABEL = "reference - edit before use"


@dataclass(frozen=True)
class BarClassSettings:
    """The classifier's settings; every default is a reference value ("reference - edit before use")."""
    avg_n: int = 20
    narrow: float = 0.7
    wide: float = 1.3
    ultra_wide: float = 2.0
    vol_low: float = 0.7
    vol_high: float = 1.3
    vol_ultra: float = 2.0
    close_part: float = 1 / 3


def settings_from(values: dict[str, Any] | None = None) -> BarClassSettings:
    """BarClassSettings from named values (None or left out: the reference). Raises ValueError on an unknown name, a
    value that is not a number or outside its bounds, a bar count that is not whole, or a lower class limit that is
    not below the next one."""
    values = {k: v for k, v in (values or {}).items() if v is not None}
    table = {name: (kind, bounds) for name, kind, _, bounds in SETTINGS_TABLE}
    unknown = sorted(set(values) - set(table))
    if unknown:
        raise ValueError(f"unknown bar class setting(s): {', '.join(unknown)}")
    out: dict[str, Any] = {}
    for name, value in values.items():
        kind, (lo, hi) = table[name]
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
    s = BarClassSettings(**out)
    for low_name, high_name in ORDERED:
        if not getattr(s, low_name) < getattr(s, high_name):
            raise ValueError(f"{low_name} must be below {high_name}, got {getattr(s, low_name):g} and {getattr(s, high_name):g}")
    return s


def reference_settings() -> list[dict[str, Any]]:
    """The settings with their reference values and the label, for a settings form."""
    defaults = BarClassSettings()
    return [{"name": f.name, "value": getattr(defaults, f.name), "label": REFERENCE_LABEL} for f in fields(defaults)]


def _float(frame: pd.DataFrame, col: str) -> pd.Series:
    return frame[col].astype(float)


def _ratio(value: pd.Series, average: pd.Series) -> pd.Series:
    avg = average.to_numpy(float)
    with np.errstate(divide="ignore", invalid="ignore"):
        out = np.where(avg > 0, value.to_numpy(float) / avg, np.nan)
    return pd.Series(out, index=value.index, dtype=float)


def relative_range(frame: pd.DataFrame, avg_n: int) -> pd.Series:
    """Range / the mean range of the `avg_n` bars before (NaN until those bars exist, or where that mean is 0)."""
    n = max(1, int(avg_n))
    before = average_range(frame, n, n).shift(1)
    return _ratio(_float(frame, "high") - _float(frame, "low"), before)


def volume_data(frame: pd.DataFrame) -> pd.Series:
    """The frame's volume with every missing or 0 (or negative) value as NaN: no volume data (M93-Q3)."""
    if "volume" not in frame.columns:
        return pd.Series(np.nan, index=frame.index, dtype=float)
    v = _float(frame, "volume")
    return v.where(np.isfinite(v) & (v > 0))


def relative_volume(frame: pd.DataFrame, avg_n: int) -> pd.Series:
    """Volume / the mean volume of the `avg_n` bars before; NaN where this bar or any bar of that window has no volume
    data (missing or 0), or during the warm-up."""
    n = max(1, int(avg_n))
    v = volume_data(frame)
    return _ratio(v, v.shift(1).rolling(n, min_periods=n).mean())


def _classes(values: np.ndarray, conds: list[np.ndarray], labels: list[str], default: str) -> np.ndarray:
    out = np.full(len(values), None, dtype=object)
    known = np.isfinite(values)
    out[known] = default
    for cond, label in zip(conds, labels):          # later conditions override earlier ones (ultra over wide)
        out[known & cond] = label
    return out


def spread_class(frame: pd.DataFrame, settings: BarClassSettings | None = None) -> pd.Series:
    """NARROW / AVERAGE / WIDE / ULTRA_WIDE from the relative range (None while it is missing)."""
    s = settings or BarClassSettings()
    rel = relative_range(frame, s.avg_n).to_numpy(float)
    with np.errstate(invalid="ignore"):
        out = _classes(rel, [rel < s.narrow, rel > s.wide, rel > s.ultra_wide], [NARROW, WIDE, ULTRA_WIDE], AVERAGE)
    return pd.Series(out, index=frame.index, dtype=object)


def close_position(frame: pd.DataFrame, settings: BarClassSettings | None = None) -> pd.Series:
    """LOW / MIDDLE / HIGH: where the close sits in the bar's own range (None on a bar with no range)."""
    s = settings or BarClassSettings()
    lo = _float(frame, "low").to_numpy(float)
    rng = _float(frame, "high").to_numpy(float) - lo
    with np.errstate(divide="ignore", invalid="ignore"):
        cp = np.where(rng > 0, (_float(frame, "close").to_numpy(float) - lo) / rng, np.nan)
        out = _classes(cp, [cp < s.close_part, cp > 1 - s.close_part], [LOW, HIGH], MIDDLE)
    return pd.Series(out, index=frame.index, dtype=object)


def bar_direction(frame: pd.DataFrame) -> pd.Series:
    """UP / DOWN / LEVEL: the close against the previous bar's close (None on the first bar)."""
    c = _float(frame, "close")
    diff = (c - c.shift(1)).to_numpy(float)
    with np.errstate(invalid="ignore"):
        out = _classes(diff, [diff > 0, diff < 0], [UP, DOWN], LEVEL)
    return pd.Series(out, index=frame.index, dtype=object)


def volume_class(frame: pd.DataFrame, settings: BarClassSettings | None = None) -> pd.Series:
    """LOW / AVERAGE / HIGH / ULTRA from the relative volume; NO_VOLUME where the bar's own volume is missing or 0;
    None where the bar has volume but its average cannot be read."""
    s = settings or BarClassSettings()
    rel = relative_volume(frame, s.avg_n).to_numpy(float)
    with np.errstate(invalid="ignore"):
        out = _classes(rel, [rel < s.vol_low, rel > s.vol_high, rel > s.vol_ultra], [LOW, HIGH, ULTRA], AVERAGE)
    out[volume_data(frame).isna().to_numpy()] = NO_VOLUME
    return pd.Series(out, index=frame.index, dtype=object)


def classify(frame: pd.DataFrame, settings: BarClassSettings | None = None) -> pd.DataFrame:
    """Every per-bar series in one frame (same index): rel_range, rel_volume, spread, close_position, direction, volume."""
    s = settings or BarClassSettings()
    return pd.DataFrame({
        "rel_range": relative_range(frame, s.avg_n),
        "rel_volume": relative_volume(frame, s.avg_n),
        "spread": spread_class(frame, s),
        "close_position": close_position(frame, s),
        "direction": bar_direction(frame),
        "volume": volume_class(frame, s),
    }, index=frame.index)


__all__ = ["BarClassSettings", "settings_from", "reference_settings", "SETTINGS_TABLE", "ORDERED", "REFERENCE_LABEL",
           "relative_range", "relative_volume", "volume_data", "spread_class", "close_position", "bar_direction",
           "volume_class", "classify", "SPREAD_CLASSES", "CLOSE_POSITIONS", "DIRECTIONS", "VOLUME_CLASSES",
           "NARROW", "AVERAGE", "WIDE", "ULTRA_WIDE", "LOW", "MIDDLE", "HIGH", "UP", "DOWN", "LEVEL", "ULTRA", "NO_VOLUME"]
