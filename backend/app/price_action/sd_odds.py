"""M99 P1 (flag `sd_pack`): the Supply & Demand zones' entry types, compression, higher-timeframe relation and the
odds-enhancer score - the ONE evaluation the scanners, the strategy templates (and so the bot) and the chart layer read.

Built on `sd_zones` (the zones and their states); pure functions on a bar frame, nothing here reads a database, a clock
or the network. Information and signals only: nothing here places, blocks or changes an order, and the score never
filters anything - a scan or a template uses it only where the user writes it in.

Definitions (our own words; every number a setting in ODDS_TABLE, "reference - edit before use"):

* Entry types (`entry`):
  - touch: the bar that starts the first visit of a fresh zone (its near line is touched for the first time);
  - confirm: within `confirm_bars` candles from that first touch (the touch candle included) and before the zone is
    violated, the first candle in the zone's direction (demand: close > open; supply: close < open) whose body is at
    least `confirm_body_pct` % of its range and whose close is back outside the near line (demand: above the proximal;
    supply: below it);
  - break_retest: the bar that starts the first visit of a swap zone (a broken zone's box, now the other side), i.e.
    the first return to the broken level (`sd_zones` swap setting on).
  Touch and confirm read the zones that are not swap zones; break_retest reads only swap zones.
* Compression into a zone at bar t: over the `comp_bars` candles before t, price stepped towards the zone - at least
  `comp_steps` of them made a higher low (into supply) / a lower high (into demand), none was wider than `comp_mult` x
  the average range before it, none touched the zone, and the close moved towards the zone across the window.
* Higher-timeframe relation (`htf`): a zone is "with" a higher-timeframe zone when its box overlaps a live zone of the
  same side on the higher timeframe, "against" when it overlaps a live zone of the other side. A higher-timeframe zone
  counts only from the close of the higher-timeframe candle that made it (closed bars only) until the close of the one
  that violated it. Nothing is merged: the two zones stay apart; this is a tag on the lower one.
* Odds-enhancer score (0-100) of a zone at bar t, each part 0..1 times its weight (`w_*`, 0 leaves a part out):
  - fresh: 1 / (1 + the visits that started before t) - each test weakens a zone;
  - leg_out: the departure candle's range in multiples of the reference (average range or ATR), divided by
    `strong_out_mult`, at most 1;
  - base_time: 1 - (base candles - 1) / base_max, at least 0 - a short stay in the base;
  - htf: 1 when the zone is "with" a higher-timeframe zone at t (counted only when `htf` is set);
  - compression: 1 when the approach to the zone was compressed at t.
  Score = 100 x sum(weight x part) / sum(weights of the counted parts). Shown with every part; it blocks nothing.

No look-ahead: every value at bar t reads bars <= t only (and higher-timeframe candles closed by bar t's close).
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, fields
from typing import Any

import numpy as np
import pandas as pd

from app.price_action import sd_zones as sd
from app.price_action.zones.detect import average_range

ENTRY_TYPES: tuple[str, ...] = ("touch", "confirm", "break_retest")
PARTS: tuple[str, ...] = ("fresh", "leg_out", "base_time", "htf", "compression")
WITH, AGAINST = "with", "against"
NO_HTF = "none"
LINES: tuple[str, ...] = ("proximal", "distal", "score")

# (name, kind, reference, bounds or choices) - the settings of the entries, the compression and the score
ODDS_TABLE: tuple[tuple[str, str, Any, Any], ...] = (
    ("confirm_bars", "bars", 3, (1, 20)),
    ("confirm_body_pct", "num", 50.0, (0.0, 100.0)),
    ("comp_bars", "bars", 5, (2, 50)),
    ("comp_steps", "bars", 3, (1, 50)),
    ("comp_mult", "num", 1.0, (0.1, 5.0)),
    ("strong_out_mult", "num", 3.0, (1.0, 10.0)),
    ("w_fresh", "num", 1.0, (0.0, 10.0)),
    ("w_leg_out", "num", 1.0, (0.0, 10.0)),
    ("w_base_time", "num", 1.0, (0.0, 10.0)),
    ("w_htf", "num", 1.0, (0.0, 10.0)),
    ("w_compression", "num", 1.0, (0.0, 10.0)),
)


@dataclass(frozen=True)
class OddsSettings:
    """The entries' / score's settings; every default is a reference value, never a recommendation."""
    confirm_bars: int = 3
    confirm_body_pct: float = 50.0
    comp_bars: int = 5
    comp_steps: int = 3
    comp_mult: float = 1.0
    strong_out_mult: float = 3.0
    w_fresh: float = 1.0
    w_leg_out: float = 1.0
    w_base_time: float = 1.0
    w_htf: float = 1.0
    w_compression: float = 1.0

    def weight(self, part: str) -> float:
        return float(getattr(self, f"w_{part}"))


def odds_from(values: dict[str, Any] | None = None) -> OddsSettings:
    """OddsSettings from named values (None or left out: the reference); ValueError on an unknown name, a value outside
    its bounds or a bar count that is not whole, or comp_steps above comp_bars."""
    values = {k: v for k, v in (values or {}).items() if v is not None}
    table = {name: (kind, bounds) for name, kind, _, bounds in ODDS_TABLE}
    unknown = sorted(set(values) - set(table))
    if unknown:
        raise ValueError(f"unknown supply / demand setting(s): {', '.join(unknown)}")
    out: dict[str, Any] = {}
    for name, value in values.items():
        kind, (lo, hi) = table[name]
        if isinstance(value, bool) or not isinstance(value, (int, float, np.integer, np.floating)) or not np.isfinite(value):
            raise ValueError(f"{name} must be a number, got {value!r}")
        if not lo <= float(value) <= hi:
            raise ValueError(f"{name} must be between {lo:g} and {hi:g}, got {float(value):g}")
        if kind == "bars" and float(value) != int(value):
            raise ValueError(f"{name} must be a whole number of candles, got {float(value):g}")
        out[name] = int(value) if kind == "bars" else float(value)
    o = OddsSettings(**out)
    if o.comp_steps > o.comp_bars:
        raise ValueError(f"comp_steps must be at most comp_bars, got {o.comp_steps} and {o.comp_bars}")
    return o


def split_settings(values: dict[str, Any] | None) -> tuple[sd.SDSettings, OddsSettings, str]:
    """A ScreenQL call's named settings -> (detector settings, these settings, the higher timeframe or "none")."""
    values = dict(values or {})
    htf = str(values.pop("htf", NO_HTF) or NO_HTF)
    mine = {k: values.pop(k) for k in list(values) if k in OddsSettings.__dataclass_fields__}
    return sd.settings_from(values), odds_from(mine), htf


# ---------------------------------------------------------------------------------------------------------------------
# Higher-timeframe context
# ---------------------------------------------------------------------------------------------------------------------
@dataclass
class HtfContext:
    """The higher timeframe's zones, each with the lower-timeframe bars it is live on: [known, dead)."""
    tf: str
    zones: list[sd.SDZone]
    known: np.ndarray        # per zone: the first lower-timeframe bar whose close is at or after the zone's formed close
    dead: np.ndarray         # per zone: the same for its violation (n when never violated)


def htf_context(tf: str, zones: list[sd.SDZone], htf_close_ns: np.ndarray, ltf_close_ns: np.ndarray) -> HtfContext:
    """Maps each higher-timeframe zone onto the lower timeframe's bars by close times (int64 ns): it is known on the
    first lower bar that closes at or after the higher candle that formed it, and dead from the one that violated it."""
    n = len(ltf_close_ns)
    known = np.array([np.searchsorted(ltf_close_ns, htf_close_ns[z.formed], side="left") for z in zones], dtype=int)
    dead = np.array([np.searchsorted(ltf_close_ns, htf_close_ns[z.violated], side="left") if z.violated is not None else n
                     for z in zones], dtype=int)
    return HtfContext(tf, zones, known, dead)


def htf_relation_mask(z: sd.SDZone, ctx: HtfContext | None, idx: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Per bar of `idx`: (with, against) for the lower-timeframe zone `z` (boxes overlap a live higher zone)."""
    with_ = np.zeros(len(idx), dtype=bool)
    against = np.zeros(len(idx), dtype=bool)
    if ctx is None:
        return with_, against
    lo, hi = min(z.proximal, z.distal), max(z.proximal, z.distal)
    for Z, a, b in zip(ctx.zones, ctx.known, ctx.dead, strict=True):
        if max(lo, min(Z.proximal, Z.distal)) > min(hi, max(Z.proximal, Z.distal)):
            continue                                       # the boxes do not overlap
        live = (idx >= a) & (idx < b)
        if Z.side == z.side:
            with_ |= live
        else:
            against |= live
    return with_, against


# ---------------------------------------------------------------------------------------------------------------------
# The evaluation
# ---------------------------------------------------------------------------------------------------------------------
class SDView:
    """The zones of one frame and one set of settings, with the per-bar reads every consumer shares."""

    def __init__(self, df: pd.DataFrame, series: sd.SDSeries, s: sd.SDSettings, o: OddsSettings, htf: HtfContext | None) -> None:
        self.series, self.s, self.o, self.htf = series, s, o, htf
        self.op, self.h = df["open"].to_numpy(float), df["high"].to_numpy(float)
        self.lo, self.c = df["low"].to_numpy(float), df["close"].to_numpy(float)
        self.rng = self.h - self.lo
        self.avg = average_range(df, s.avg_n, s.avg_n).shift(1).to_numpy(float)
        self.ref = sd.candle_numbers(df, s)["ref"]
        with np.errstate(invalid="ignore", divide="ignore"):
            self.body_pct = np.where(self.rng > 0, np.abs(self.c - self.op) / self.rng * 100.0, 0.0)
        self._comp: dict[str, np.ndarray] | None = None

    @property
    def zones(self) -> list[sd.SDZone]:
        return self.series.zones

    @property
    def n(self) -> int:
        return self.series.n

    # --- the parts -------------------------------------------------------------------------------------------------
    def _compression_base(self) -> dict[str, np.ndarray]:
        """The zone-free half of the compression test, per bar t (window = the comp_bars candles before t)."""
        if self._comp is not None:
            return self._comp
        w, n = self.o.comp_bars, self.n
        up = np.zeros(n, dtype=bool)
        dn = np.zeros(n, dtype=bool)
        if n > w + 1:
            with np.errstate(invalid="ignore"):
                hl = np.r_[False, self.lo[1:] > self.lo[:-1]].astype(int)
                lh = np.r_[False, self.h[1:] < self.h[:-1]].astype(int)
                small = (self.rng <= self.o.comp_mult * self.avg) & np.isfinite(self.avg)
            cs = lambda x: np.r_[0, np.cumsum(x)]          # noqa: E731 - window sums by prefix sums
            s_hl, s_lh, s_small = cs(hl), cs(lh), cs(small.astype(int))
            t = np.arange(w + 1, n)
            steps_up = s_hl[t] - s_hl[t - w]               # bars t-w .. t-1
            steps_dn = s_lh[t] - s_lh[t - w]
            all_small = (s_small[t] - s_small[t - w]) == w
            up[t] = (steps_up >= self.o.comp_steps) & all_small & (self.c[t - 1] > self.c[t - w - 1])
            dn[t] = (steps_dn >= self.o.comp_steps) & all_small & (self.c[t - 1] < self.c[t - w - 1])
        self._comp = {"up": up, "dn": dn, "hi": pd.Series(self.h).rolling(w).max().to_numpy(),
                      "lo": pd.Series(self.lo).rolling(w).min().to_numpy()}
        return self._comp

    def compressed(self, z: sd.SDZone, idx: np.ndarray) -> np.ndarray:
        """Per bar of `idx`: the approach to `z` over the comp_bars candles before the bar was compressed."""
        k = self._compression_base()
        w = self.o.comp_bars
        out = np.zeros(len(idx), dtype=bool)
        ok = idx >= w + 1
        t = idx[ok]
        if not len(t):
            return out
        if z.side == sd.SUPPLY:          # stepping up into supply: no high touched the near line before the bar
            out[ok] = k["up"][t] & (k["hi"][t - 1] < z.proximal)
        else:
            out[ok] = k["dn"][t] & (k["lo"][t - 1] > z.proximal)
        return out

    def leg_out_mult(self, z: sd.SDZone) -> float:
        d = z.depart if z.depart >= 0 else z.formed
        r = self.ref[d] if z.kind == "base" else self.avg[d]
        return float(self.rng[d] / r) if np.isfinite(r) and r > 0 else float("nan")

    def base_candles(self, z: sd.SDZone) -> int:
        src = self.zones[z.swap_of] if z.swap_of is not None else z
        return src.base_end - src.base_start + 1

    def parts(self, z: sd.SDZone, idx: np.ndarray) -> dict[str, np.ndarray]:
        """Each part's value (0..1) of zone `z` on the bars of `idx` (NaN: the part is not counted)."""
        before = np.searchsorted(np.asarray(z.visits, dtype=int), idx, side="left") if z.visits else np.zeros(len(idx), int)
        m = self.leg_out_mult(z)
        leg = min(1.0, m / self.o.strong_out_mult) if np.isfinite(m) else 0.0
        base = max(0.0, 1.0 - (self.base_candles(z) - 1) / self.s.base_max)
        with_, _ = htf_relation_mask(z, self.htf, idx)
        return {"fresh": 1.0 / (1.0 + before), "leg_out": np.full(len(idx), leg), "base_time": np.full(len(idx), base),
                "htf": with_.astype(float) if self.htf is not None else np.full(len(idx), np.nan),
                "compression": self.compressed(z, idx).astype(float)}

    def score(self, z: sd.SDZone, idx: np.ndarray) -> np.ndarray:
        parts = self.parts(z, idx)
        num = np.zeros(len(idx))
        den = np.zeros(len(idx))
        for name, value in parts.items():
            w = self.o.weight(name)
            counted = np.isfinite(value)
            num += np.where(counted, w * np.nan_to_num(value), 0.0)
            den += np.where(counted, w, 0.0)
        with np.errstate(invalid="ignore", divide="ignore"):
            return np.where(den > 0, num / den * 100.0, np.nan)

    def breakdown(self, z: sd.SDZone, t: int) -> dict[str, Any]:
        """One zone's score at bar t with every part and its weight (the chart's tap card, the Copilot facts)."""
        idx = np.array([t])
        parts = self.parts(z, idx)
        total = float(self.score(z, idx)[0])
        with_, against = htf_relation_mask(z, self.htf, idx)
        return {"score": None if not np.isfinite(total) else round(total, 1),
                "parts": [{"name": p, "value": None if not np.isfinite(parts[p][0]) else round(float(parts[p][0]), 3),
                           "weight": self.o.weight(p)} for p in PARTS],
                "leg_out_mult": None if not np.isfinite(self.leg_out_mult(z)) else round(self.leg_out_mult(z), 2),
                "htf": None if self.htf is None else {"timeframe": self.htf.tf,
                                                      "relation": WITH if with_[0] else AGAINST if against[0] else "none"}}

    # --- per-bar reads ---------------------------------------------------------------------------------------------
    def entries(self, side: str, entry: str) -> tuple[np.ndarray, np.ndarray]:
        """Per bar: (an entry of that type on a zone of `side`, the zone's position in `zones` or -1). Two zones on one
        bar: the one whose near line is closest to the close."""
        if entry not in ENTRY_TYPES:
            raise ValueError(f"entry must be one of {', '.join(ENTRY_TYPES)}, got {entry!r}")
        n = self.n
        best = np.full(n, -1, dtype=int)
        dist = np.full(n, np.inf)
        for i, z in enumerate(self.zones):
            if z.side != side or not z.visits or ((z.kind == "swap") != (entry == "break_retest")):
                continue
            t0 = z.visits[0]
            t = t0 if entry != "confirm" else self._confirm_bar(z, t0)
            if t is None:
                continue
            d = abs(self.c[t] - z.proximal)
            if d < dist[t]:
                best[t], dist[t] = i, d
        return best >= 0, best

    def _confirm_bar(self, z: sd.SDZone, t0: int) -> int | None:
        for t in range(t0, min(self.n, t0 + self.o.confirm_bars)):
            if z.violated is not None and z.violated <= t:
                return None
            up = z.side == sd.DEMAND
            if ((self.c[t] > self.op[t]) if up else (self.c[t] < self.op[t])) and self.body_pct[t] >= self.o.confirm_body_pct \
                    and ((self.c[t] > z.proximal) if up else (self.c[t] < z.proximal)):
                return t
        return None

    def entry_ref(self, side: str, entry: str, line: str) -> np.ndarray:
        """On entry bars: the entered zone's proximal / distal line or its score at that bar; NaN elsewhere."""
        flags, which = self.entries(side, entry)
        out = np.full(self.n, np.nan)
        for t in np.flatnonzero(flags):
            z = self.zones[which[t]]
            out[t] = z.proximal if line == "proximal" else z.distal if line == "distal" else self.score(z, np.array([t]))[0]
        return out

    def nearest_score(self, side: str, state: str) -> np.ndarray:
        """Per bar: the score of the nearest live zone of `side` in `state` (the same zone SDZoneDistancePct reads)."""
        which = self.series.nearest_index(side, state)
        out = np.full(self.n, np.nan)
        for i in np.unique(which[which >= 0]):
            bars = np.flatnonzero(which == i)
            out[bars] = self.score(self.zones[int(i)], bars)
        return out

    def compression_hits(self, side: str) -> np.ndarray:
        """True on a bar that starts a visit of a live zone of `side` after a compressed approach."""
        out = np.zeros(self.n, dtype=bool)
        for z in self.zones:
            if z.side != side or not z.visits:
                continue
            v = np.asarray(z.visits, dtype=int)
            out[v[self.compressed(z, v)]] = True
        return out

    def htf_with(self, side: str, state: str) -> np.ndarray:
        """True on a bar whose nearest live zone of `side` in `state` is "with" a higher-timeframe zone."""
        which = self.series.nearest_index(side, state)
        out = np.zeros(self.n, dtype=bool)
        for i in np.unique(which[which >= 0]):
            bars = np.flatnonzero(which == i)
            out[bars] = htf_relation_mask(self.zones[int(i)], self.htf, bars)[0]
        return out


def view(df: pd.DataFrame, s: sd.SDSettings | None = None, o: OddsSettings | None = None, htf: HtfContext | None = None,
         series: sd.SDSeries | None = None) -> SDView:
    """The shared evaluation of one frame (index = bar start, oldest first) - zones, states and the reads above."""
    s = s or sd.SDSettings()
    o = o or OddsSettings()
    series = series if series is not None else sd.cached_sd_series(df, s)
    return SDView(df, series, s, o, htf)


def htf_for(tf: str, ltf_close_ns: np.ndarray, frame_of: Callable[[str], pd.DataFrame], close_ns_of: Callable[[str], np.ndarray],
            s: sd.SDSettings) -> HtfContext:
    """The higher timeframe's zones (same detector settings, on its own closed bars) mapped onto the lower bars."""
    f = frame_of(tf)
    series = sd.cached_sd_series(f, s) if len(f) >= 3 else sd.SDSeries([], len(f), f["close"].to_numpy(float))
    return htf_context(tf, series.zones, close_ns_of(tf), ltf_close_ns)


def settings_dict(o: OddsSettings) -> dict[str, Any]:
    return {f.name: getattr(o, f.name) for f in fields(o)}


__all__ = ["ENTRY_TYPES", "PARTS", "WITH", "AGAINST", "NO_HTF", "LINES", "ODDS_TABLE", "OddsSettings", "odds_from", "split_settings",
           "HtfContext", "htf_context", "htf_relation_mask", "SDView", "view", "htf_for", "settings_dict"]
