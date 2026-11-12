"""M99 P2: the information grade of a key level (the M94 intraday pack's levels) - `LevelGrade(level, what, ...)`.

Information only: a count of the qualities a level shows as of each bar, never a gate, never a trading rule. Five checks,
each one point (the grade is 0-5), every number the user's setting:

* touches     - separate visits of the level's band (`near` average true ranges either side; 0 = the bar must reach the
  level itself) in the last `look` bars, at least `touches` (the bar that made the level is its first visit);
* recent      - the last visit at most `recent` bars ago (the level is still in play);
* source      - the level's kind is at or above `tier`: 1 the previous close, 2 the previous session's high / low,
  3 the pre-open and the session's own high / low (the ranking of the M94 study's level list);
* round       - the level sits within the band of a round number: multiples of `step`, or with step 0 the ATP's own round
  steps of the symbol (`level_strength.round_distance`, the zone strength's round-number feature);
* confluence  - at least one other key level lies within the band.

`what` returns the grade or one component: touches (the count), bars_since (bars since the last visit), source (the
tier), round (1 / 0), confluence (how many other levels are in the band). Closed bars only: the value on bar t reads
bars up to and including t and the levels as they stood on t (no look-ahead). Missing (NaN) where the level or the ATR has
no value yet.
"""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

import numpy as np

LEVEL_TIERS: dict[str, int] = {"PDC": 1, "PDH": 2, "PDL": 2, "PREOPEN_HIGH": 3, "PREOPEN_LOW": 3, "SESSION_HIGH": 3, "SESSION_LOW": 3}
LEVEL_NAMES = tuple(LEVEL_TIERS)
WHATS = ("grade", "touches", "bars_since", "source", "round", "confluence")
DEFAULT_LOOK = 400          # level_strength's look-back for a zone with no known origin (strength_features)
DEFAULT_RECENT = 200        # pa_settings zone_recency_tau_bars (the zone strength's recency scale)


def _round_off(level: float, steps: Sequence[float]) -> float:
    return min(abs(level - round(level / s) * s) for s in steps if s > 0) if any(s > 0 for s in steps) else math.inf


def grade_series(levels: Mapping[str, np.ndarray], name: str, high: np.ndarray, low: np.ndarray, atr: np.ndarray, *,
                 what: str = "grade", near: float = 0.0, look: int = DEFAULT_LOOK, touches: int = 2, recent: int = DEFAULT_RECENT,
                 tier: int = 2, round_steps: Sequence[float] = ()) -> np.ndarray:
    """The grade (or one component) of level `name` on every bar. `levels`: every key level's series (name -> values on
    the bars), `high` / `low` the bars, `atr` the average true range on them (the band's unit)."""
    if name not in LEVEL_TIERS:
        raise ValueError(f"unknown level {name!r}")
    if what not in WHATS:
        raise ValueError(f"unknown part {what!r}")
    lv_all = np.asarray(levels[name], dtype=float)
    h, lo_ = np.asarray(high, dtype=float), np.asarray(low, dtype=float)
    a = np.asarray(atr, dtype=float)
    size = len(lv_all)
    out = np.full(size, np.nan)
    look = max(int(look), 1)
    others = [np.asarray(v, dtype=float) for k, v in levels.items() if k != name]
    src = LEVEL_TIERS[name]
    for t in range(size):
        lv = lv_all[t]
        band = float(near) * a[t] if float(near) > 0 else 0.0
        if not (math.isfinite(lv) and (band == 0.0 or math.isfinite(band))) or (float(near) > 0 and not math.isfinite(a[t])):
            continue
        s = max(0, t - look + 1)
        inside = (lo_[s:t + 1] <= lv + band) & (h[s:t + 1] >= lv - band)
        starts = inside & ~np.r_[False, inside[:-1]]
        n_touch = int(starts.sum())
        idx = np.flatnonzero(inside)
        since = float(t - (s + int(idx[-1]))) if len(idx) else math.nan
        conf = sum(1 for o in others if math.isfinite(o[t]) and abs(o[t] - lv) <= band)
        rnd = _round_off(lv, round_steps) <= band + 1e-9
        if what == "touches":
            out[t] = n_touch
        elif what == "bars_since":
            out[t] = since
        elif what == "source":
            out[t] = src
        elif what == "round":
            out[t] = 1.0 if rnd else 0.0
        elif what == "confluence":
            out[t] = conf
        else:
            out[t] = (int(n_touch >= int(touches)) + int(math.isfinite(since) and since <= int(recent)) + int(src <= int(tier))
                      + int(rnd) + int(conf >= 1))
    return out


__all__ = ["grade_series", "LEVEL_TIERS", "LEVEL_NAMES", "WHATS", "DEFAULT_LOOK", "DEFAULT_RECENT"]
