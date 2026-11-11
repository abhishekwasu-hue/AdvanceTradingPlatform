"""M97 (docs/design/M97_SUPPLY_DEMAND.md, PR-1): a generic Supply & Demand zone detector and its states.

A separate, generic module (M97-Q1): the market zones of Z7a (`app/price_action/zones`), template A1, the AreaGate and
the owner's own method areas are not changed and not read here. It reuses only `indicators.volatility.atr` and
`zones.detect.average_range`. Pure functions on a bar frame (open / high / low / close, oldest first); nothing here
reads a database, a clock or the network. Signals only: a zone is a price area with a state, never an order.

Definitions (our own words):

* Average range at a candle: the mean (high - low) of the `avg_n` candles before it (so a candle is never measured
  against itself). The leg-out may use the ATR(`avg_n`) known at the candle before instead (`out_ref` "atr").
* Leg candle: range >= `leg_mult` x average range, and a body >= `leg_body_pct` % of its own range. Its direction is
  up (close > open) or down (close < open).
* Base candle: range <= `base_mult` x average range, and a body <= `base_body_pct` % of its own range.
* Base: 1 to `base_max` base candles in a row. A longer run of base candles is not a base.
* Leg-in: the leg candle just before the base, up or down.
* Leg-out: the candle just after the base, explosive: range >= `out_mult` x the reference (average range or ATR), up or
  down (by its close against its open).
* Formation, from the leg-in and leg-out directions: leg-out up = demand (DBR: down-in, RBR: up-in); leg-out down =
  supply (RBD: up-in, DBD: down-in).
* Lines (M97-Q3, setting `edge` "wick" | "body", reference "wick"):
  - demand: proximal = the base top (the highest high, or the highest body top); distal = the lowest low of the base
    and the leg-out candle;
  - supply: proximal = the base bottom (the lowest low, or the lowest body bottom); distal = the highest high of the
    base and the leg-out candle.
* A zone exists only once its leg-out candle has closed (its `formed` bar); nothing before that bar sees it.

States (M97-Q6), computed causally bar by bar from the bar after the leg-out:

* Fresh: no visit since the leg-out.
* Tested (n): n separate visits. A visit starts on a bar whose wick touches the proximal line (demand: low <= proximal;
  supply: high >= proximal) and ends on the first bar that stays wholly outside the zone (demand: low > proximal;
  supply: high < proximal). The leg-out bar itself is not a visit.
* Violated: a close beyond the distal line (demand: close < distal; supply: close > distal). Final: a violated zone is
  hidden from the nearest-zone reads.

Reference values (M97-Q2, each a setting, shown "reference - edit before use"): avg_n 20, leg_mult 1.5, base_mult 0.7,
out_mult 1.5, leg_body_pct 75, base_body_pct 50, base_max 5, out_ref "average", edge "wick".

No look-ahead: every quantity at bar t uses bars <= t only, so a run on the bars cut at t gives the same values up to t
as the full run (tested). The ScreenQL functions (flag `sd_pack`) read the per-bar series below.
"""
from __future__ import annotations

import hashlib
from collections import OrderedDict
from dataclasses import dataclass, field, fields
from typing import Any

import numpy as np
import pandas as pd

from app.indicators.volatility import atr
from app.price_action.zones.detect import average_range

DEMAND, SUPPLY = "demand", "supply"
SIDES: tuple[str, ...] = (DEMAND, SUPPLY)
FORMATIONS: tuple[str, ...] = ("DBR", "RBR", "RBD", "DBD")
FORMATION_SIDE: dict[str, str] = {"DBR": DEMAND, "RBR": DEMAND, "RBD": SUPPLY, "DBD": SUPPLY}
FRESH, TESTED, VIOLATED = "fresh", "tested", "violated"
STATE_CHOICES: tuple[str, ...] = (FRESH, TESTED, "any")      # the nearest-zone reads: a violated zone is hidden
EDGES: tuple[str, ...] = ("wick", "body")
OUT_REFS: tuple[str, ...] = ("average", "atr")
LINES: tuple[str, ...] = ("proximal", "distal")

# (name, kind, reference, bounds or choices) - the one list of the detector's settings; the ScreenQL kwargs mirror it
# (registry.SD_KWARGS, checked by tests/test_m97_sd_zones.py)
SETTINGS_TABLE: tuple[tuple[str, str, Any, Any], ...] = (
    ("avg_n", "bars", 20, (5, 200)),
    ("leg_mult", "num", 1.5, (1.0, 5.0)),
    ("base_mult", "num", 0.7, (0.1, 1.0)),
    ("out_mult", "num", 1.5, (1.0, 5.0)),
    ("leg_body_pct", "num", 75.0, (50.0, 100.0)),
    ("base_body_pct", "num", 50.0, (5.0, 90.0)),
    ("base_max", "bars", 5, (1, 10)),
    ("out_ref", "choice", "average", OUT_REFS),
    ("edge", "choice", "wick", EDGES),
)
REFERENCE_LABEL = "reference - edit before use"


@dataclass(frozen=True)
class SDSettings:
    """The detector's settings; every default is a reference value (M97-Q2), never a recommendation."""
    avg_n: int = 20
    leg_mult: float = 1.5
    base_mult: float = 0.7
    out_mult: float = 1.5
    leg_body_pct: float = 75.0
    base_body_pct: float = 50.0
    base_max: int = 5
    out_ref: str = "average"
    edge: str = "wick"


def settings_from(values: dict[str, Any] | None = None) -> SDSettings:
    """SDSettings from named values (None or left out: the reference). Raises ValueError on an unknown name, a value
    outside its bounds, a bar count that is not whole, or a base limit not below the leg / leg-out limits."""
    values = {k: v for k, v in (values or {}).items() if v is not None}
    table = {name: (kind, bounds) for name, kind, _, bounds in SETTINGS_TABLE}
    unknown = sorted(set(values) - set(table))
    if unknown:
        raise ValueError(f"unknown supply / demand setting(s): {', '.join(unknown)}")
    out: dict[str, Any] = {}
    for name, value in values.items():
        kind, bounds = table[name]
        if kind == "choice":
            if value not in bounds:
                raise ValueError(f"{name} must be one of {', '.join(bounds)}, got {value!r}")
            out[name] = str(value)
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float, np.integer, np.floating)) or not np.isfinite(value):
            raise ValueError(f"{name} must be a number, got {value!r}")
        lo, hi = bounds
        if not lo <= float(value) <= hi:
            raise ValueError(f"{name} must be between {lo:g} and {hi:g}, got {float(value):g}")
        if kind == "bars":
            if float(value) != int(value):
                raise ValueError(f"{name} must be a whole number of candles, got {float(value):g}")
            out[name] = int(value)
        else:
            out[name] = float(value)
    s = SDSettings(**out)
    if not s.base_mult < s.leg_mult:
        raise ValueError(f"base_mult must be below leg_mult, got {s.base_mult:g} and {s.leg_mult:g}")
    if not s.base_mult < s.out_mult:
        raise ValueError(f"base_mult must be below out_mult, got {s.base_mult:g} and {s.out_mult:g}")
    return s


@dataclass
class SDZone:
    """One zone: its side, formation, lines and the bars that made it; `violated` and `visits` are filled by the states
    pass (`track`). Bar numbers are positions in the frame."""
    side: str
    formation: str
    proximal: float
    distal: float
    leg_in: int
    base_start: int
    base_end: int
    formed: int                                  # the leg-out bar: the zone is known from this bar's close
    violated: int | None = None                  # the bar whose close went beyond the distal line
    visits: list[int] = field(default_factory=list)   # the bars on which a visit started

    def visits_by(self, t: int) -> int:
        return sum(1 for v in self.visits if v <= t)

    def state_at(self, t: int) -> str | None:
        """The state at bar t's close (None before the zone exists)."""
        if t < self.formed:
            return None
        if self.violated is not None and t >= self.violated:
            return VIOLATED
        return TESTED if self.visits_by(t) else FRESH

    def as_dict(self) -> dict[str, Any]:
        return {f.name: getattr(self, f.name) for f in fields(self)}


def _candles(df: pd.DataFrame, s: SDSettings) -> dict[str, np.ndarray]:
    o, h = df["open"].to_numpy(float), df["high"].to_numpy(float)
    lo, c = df["low"].to_numpy(float), df["close"].to_numpy(float)
    rng = h - lo
    body = np.abs(c - o)
    with np.errstate(invalid="ignore", divide="ignore"):
        body_pct = np.where(rng > 0, body / rng * 100.0, 0.0)
    avg = average_range(df, s.avg_n, s.avg_n).shift(1).to_numpy(float)          # the avg_n candles BEFORE each candle
    ref = avg if s.out_ref == "average" else atr(df, s.avg_n).shift(1).to_numpy(float)
    known = np.isfinite(avg)
    with np.errstate(invalid="ignore"):
        leg = known & (rng >= s.leg_mult * avg) & (body_pct >= s.leg_body_pct) & (c != o)
        base = known & (rng <= s.base_mult * avg) & (body_pct <= s.base_body_pct)
        out = np.isfinite(ref) & (rng >= s.out_mult * ref) & (c != o)
    return {"o": o, "h": h, "l": lo, "c": c, "up": c > o, "leg": leg, "base": base, "out": out}


def detect(df: pd.DataFrame, settings: SDSettings | None = None) -> list[SDZone]:
    """Every zone in the frame, in the order of its leg-out bar (states not filled: `track`)."""
    s = settings or SDSettings()
    n = len(df)
    if n < 3:
        return []
    k = _candles(df, s)
    run = np.zeros(n, dtype=int)                 # base candles in a row ending at each bar
    for i in range(n):
        run[i] = (run[i - 1] + 1 if i else 1) if k["base"][i] else 0
    zones: list[SDZone] = []
    for j in range(2, n):
        if not k["out"][j]:
            continue
        b = int(run[j - 1])
        if b < 1 or b > s.base_max:              # no base, or a base too long
            continue
        start, end, leg_in = j - b, j - 1, j - b - 1
        if leg_in < 0 or not k["leg"][leg_in]:
            continue
        in_up, out_up = bool(k["up"][leg_in]), bool(k["up"][j])
        formation = ("RBR" if in_up else "DBR") if out_up else ("RBD" if in_up else "DBD")
        sl = slice(start, end + 1)
        if out_up:
            top = float(k["h"][sl].max()) if s.edge == "wick" else float(np.maximum(k["o"][sl], k["c"][sl]).max())
            distal = float(min(k["l"][sl].min(), k["l"][j]))
            zones.append(SDZone(DEMAND, formation, top, distal, leg_in, start, end, j))
        else:
            bottom = float(k["l"][sl].min()) if s.edge == "wick" else float(np.minimum(k["o"][sl], k["c"][sl]).min())
            distal = float(max(k["h"][sl].max(), k["h"][j]))
            zones.append(SDZone(SUPPLY, formation, bottom, distal, leg_in, start, end, j))
    return zones


def track(df: pd.DataFrame, zones: list[SDZone]) -> list[SDZone]:
    """Fills each zone's visits and violation bar from the bars after its leg-out (in place; returns the list)."""
    h, lo, c = df["high"].to_numpy(float), df["low"].to_numpy(float), df["close"].to_numpy(float)
    for z in zones:
        a = z.formed + 1
        if a >= len(df):
            continue
        if z.side == DEMAND:
            touch, beyond = lo[a:] <= z.proximal, c[a:] < z.distal
        else:
            touch, beyond = h[a:] >= z.proximal, c[a:] > z.distal
        hit = np.flatnonzero(beyond)
        stop = int(hit[0]) if len(hit) else len(touch) - 1
        if len(hit):
            z.violated = a + stop
        t = touch[:stop + 1]
        starts = t & ~np.r_[False, t[:-1]]       # a visit starts where a touch follows a bar outside the zone
        z.visits = [a + int(i) for i in np.flatnonzero(starts)]
    return zones


@dataclass
class SDSeries:
    """The per-bar reads the ScreenQL functions use, for one frame and one set of settings."""
    zones: list[SDZone]
    n: int
    close: np.ndarray

    def _live(self, side: str, t0: int, t1: int) -> list[tuple[SDZone, np.ndarray, np.ndarray]]:
        """Zones of a side alive somewhere in bars [t0, t1): (zone, alive mask, visits so far) over that chunk."""
        out = []
        idx = np.arange(t0, t1)
        for z in self.zones:
            if z.side != side or z.formed >= t1 or (z.violated is not None and z.violated < t0):
                continue
            alive = (idx >= z.formed) & ((idx < z.violated) if z.violated is not None else True)
            visits = np.searchsorted(np.asarray(z.visits, dtype=int), idx, side="right") if z.visits else np.zeros(len(idx), int)
            out.append((z, alive, visits))
        return out

    def nearest(self, side: str, state: str, chunk: int = 4096) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Per bar: (signed distance % from the close to the nearest live zone's proximal line, its proximal, its
        distal) among the zones of `side` in `state` ("fresh", "tested" or "any"); NaN where there is none. The distance
        is positive while the close is outside the zone (above a demand line, below a supply line), negative inside."""
        dist = np.full(self.n, np.nan)
        prox = np.full(self.n, np.nan)
        dis = np.full(self.n, np.nan)
        for t0 in range(0, self.n, chunk):
            t1 = min(self.n, t0 + chunk)
            live = self._live(side, t0, t1)
            if not live:
                continue
            c = self.close[t0:t1]
            p = np.array([z.proximal for z, _, _ in live])[:, None]
            d_all = (c - p) if side == DEMAND else (p - c)
            with np.errstate(invalid="ignore", divide="ignore"):
                d_all = d_all / c * 100.0
            ok = np.vstack([a & ((v == 0) if state == FRESH else (v > 0) if state == TESTED else True) for _, a, v in live])
            score = np.where(ok, np.abs(d_all), np.inf)
            best = np.argmin(score, axis=0)
            has = np.isfinite(score[best, np.arange(t1 - t0)])
            cols = np.flatnonzero(has)
            dist[t0 + cols] = d_all[best[cols], cols]
            prox[t0 + cols] = np.array([live[i][0].proximal for i in best[cols]])
            dis[t0 + cols] = np.array([live[i][0].distal for i in best[cols]])
        return dist, prox, dis

    def formed(self, formation: str) -> np.ndarray:
        """True on the leg-out bar of each zone of `formation` (a name, a side, or "any")."""
        out = np.zeros(self.n, dtype=bool)
        for z in self.zones:
            if formation in ("any", z.formation, z.side):
                out[z.formed] = True
        return out

    def entered(self, side: str) -> np.ndarray:
        """True on a bar that starts the FIRST visit of a zone of `side` (the zone was fresh until this bar)."""
        out = np.zeros(self.n, dtype=bool)
        for z in self.zones:
            if z.side == side and z.visits:
                out[z.visits[0]] = True
        return out

    def violated(self, side: str) -> np.ndarray:
        """True on the bar whose close went beyond the distal line of a zone of `side`."""
        out = np.zeros(self.n, dtype=bool)
        for z in self.zones:
            if z.side == side and z.violated is not None:
                out[z.violated] = True
        return out


def sd_series(df: pd.DataFrame, settings: SDSettings | None = None) -> SDSeries:
    s = settings or SDSettings()
    zones = track(df, detect(df, s))
    return SDSeries(zones, len(df), df["close"].to_numpy(float))


_CACHE: OrderedDict[tuple[str, SDSettings], SDSeries] = OrderedDict()
_CACHE_SIZE = 64


def _fingerprint(df: pd.DataFrame) -> str:
    h = hashlib.sha256()
    h.update(np.asarray(df.index.asi8 if isinstance(df.index, pd.DatetimeIndex) else np.arange(len(df))).tobytes())
    for col in ("open", "high", "low", "close"):
        h.update(df[col].to_numpy(dtype=float).tobytes())
    return h.hexdigest()


def cached_sd_series(df: pd.DataFrame, settings: SDSettings | None = None) -> SDSeries:
    """`sd_series` memoised by the frame's content and the settings (every SD function of a screen shares one pass)."""
    s = settings or SDSettings()
    key = (_fingerprint(df), s)
    hit = _CACHE.get(key)
    if hit is not None:
        _CACHE.move_to_end(key)
        return hit
    value = sd_series(df, s)
    _CACHE[key] = value
    if len(_CACHE) > _CACHE_SIZE:
        _CACHE.popitem(last=False)
    return value


__all__ = ["SDSettings", "SDZone", "SDSeries", "SETTINGS_TABLE", "REFERENCE_LABEL", "settings_from", "detect", "track",
           "sd_series", "cached_sd_series", "DEMAND", "SUPPLY", "SIDES", "FORMATIONS", "FORMATION_SIDE", "FRESH", "TESTED",
           "VIOLATED", "STATE_CHOICES", "EDGES", "OUT_REFS", "LINES"]
