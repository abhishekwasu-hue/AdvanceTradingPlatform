"""Z10-a (U3 G2): the no-look-ahead harness.

Every function that turns bars into something a screen, an alert, a chart or a strategy reads - an indicator, a
candlestick pattern, a ScreenQL function, a swing or zone detector - is registered here as a `CausalCase`. The harness
then proves, on deterministic session-shaped bars, that what the function says about bar t never depends on a bar after
t:

1. truncation - `fn(bars[:t+1])` equals `fn(bars)` up to t (for events: the records known by t);
2. future mutation - the bars after t are replaced (an up-spike, a down-spike, an independent random walk; same
   timestamps) and the output up to t must not move. This catches what truncation alone can miss: a window centred on
   the bar, a min / max or a normalisation over the whole frame, a cache keyed on something that does not change;
3. immutability - the input frame is the same after the call (a function that writes into its input corrupts every
   caller after it).

Three kinds of case:

* `series`   - one value per bar (a Series, DataFrame, ndarray, or a tuple / list / dict of them);
* `events`   - a list of records, each with the bar index at which it became KNOWN (`known_at`, e.g. a swing's
  confirmation bar, a zone's impulse bar), compared by `key`;
* `last_bar` - an answer about the last bar only (the ScreenQL structure / option filters). These only ever see the bars
  they are given, so they are causal by construction; the harness checks they are deterministic and leave their input
  alone, and the validator keeps them out of functions that read a history (Count, CrossAbove, SMA ...).

`check(case)` returns the violations (empty = causal); the test suite runs it over the whole registry
(tests/test_z10a_no_lookahead.py) and a coverage test makes sure no shipped function is missing from it.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Hashable, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

SERIES, EVENTS, LAST_BAR = "series", "events", "last_bar"
KINDS = (SERIES, EVENTS, LAST_BAR)
PERTURBATIONS = ("spike_up", "spike_down", "walk")

IST_OPEN_UTC = pd.Timedelta(hours=3, minutes=45)       # 09:15 IST
SESSION_MINUTES = 375                                  # 09:15 - 15:30 IST


@dataclass(frozen=True)
class CausalCase:
    name: str
    fn: Callable[[pd.DataFrame], Any]
    kind: str = SERIES
    group: str = ""                                    # indicator / operand / pattern / screenql / swing / structure / zone
    bars: str = "5m"                                   # which synthetic data set: "1m", "5m", "15m", "1d"
    known_at: Optional[Callable[[Any], int]] = None    # events: the bar index a record became known at
    key: Optional[Callable[[Any], Hashable]] = None    # events: what identifies a record (default: the record itself)
    samples: int = 10                                  # bars t checked (spread over the frame, plus session edges)
    min_t: int = 20                                    # the first bar t checked
    length: int = 0                                    # bars in the frame (0 = the data set's default)
    perturbations: Tuple[str, ...] = PERTURBATIONS
    note: str = ""


@dataclass
class Violation:
    case: str
    t: int
    check: str                                         # truncation / future:<perturbation> / immutability / determinism
    detail: str

    def __str__(self) -> str:
        return f"{self.case} @ t={self.t} [{self.check}]: {self.detail}"


REGISTRY: Dict[str, CausalCase] = {}


def register(name: str, fn: Optional[Callable[[pd.DataFrame], Any]] = None, **options: Any) -> Any:
    """Adds a case. `register("ema_20", lambda f: ema(f["close"], 20), group="indicator")`, or as a decorator
    (`@register("x", group=...)`). A duplicate name is refused (two cases would silently shadow each other)."""
    def add(func: Callable[[pd.DataFrame], Any]) -> Callable[[pd.DataFrame], Any]:
        if name in REGISTRY:
            raise ValueError(f"causal case {name!r} is already registered")
        case = CausalCase(name=name, fn=func, **options)
        if case.kind not in KINDS:
            raise ValueError(f"{name}: kind must be one of {KINDS}, got {case.kind!r}")
        if case.kind == EVENTS and case.known_at is None:
            raise ValueError(f"{name}: an events case needs known_at (the bar a record became known at)")
        REGISTRY[name] = case
        return func
    return add(fn) if fn is not None else add


# --------------------------------------------------------------------------------------------------- synthetic bars
_DEFAULT_LENGTH = {"1m": 900, "5m": 600, "15m": 400, "1d": 400}


def synthetic_bars(n: int = 600, *, tf: str = "5m", seed: int = 0, start: str = "2026-01-05") -> pd.DataFrame:
    """Deterministic OHLCV bars shaped like NSE sessions (09:15-15:30 IST on weekdays, UTC index = bar start), or daily
    bars (one per weekday, midnight UTC). The walk has trending and ranging stretches (so patterns, swings and zones
    fire), overnight gaps, ties at highs and lows (prices on a 0.05 tick), a few flat bars and a few zero-volume bars."""
    rng = np.random.default_rng(seed)
    if tf == "1d":
        days = pd.bdate_range(start, periods=n, tz="UTC")
        index = pd.DatetimeIndex(days)
        gaps = np.zeros(n)
    else:
        minutes = int(tf[:-1])
        per_day = -(-SESSION_MINUTES // minutes)
        days = pd.bdate_range(start, periods=-(-n // per_day) + 1, tz="UTC")
        stamps = [d + IST_OPEN_UTC + pd.Timedelta(minutes=minutes * k) for d in days for k in range(per_day)]
        index = pd.DatetimeIndex(stamps[:n])
        gaps = np.r_[0.0, (index[1:].normalize() != index[:-1].normalize()).astype(float)]
    regime = np.repeat(rng.normal(0.0, 0.0015, size=n // 40 + 1), 40)[:n]          # drift changes every 40 bars
    ret = regime + rng.normal(0.0, 0.004, size=n) + gaps * rng.normal(0.0, 0.012, size=n)
    close = 100.0 * np.exp(np.cumsum(ret))
    open_ = np.r_[100.0, close[:-1]] * np.exp(gaps * rng.normal(0.0, 0.006, size=n))
    wick = np.abs(rng.normal(0.0, 0.003, size=(2, n))) * close
    high = np.maximum(open_, close) + wick[0]
    low = np.minimum(open_, close) - wick[1]
    tick = lambda a: np.round(a / 0.05) * 0.05                                       # noqa: E731
    o, h, lo, c = tick(open_), tick(high), tick(low), tick(close)
    flat = rng.random(n) < 0.01
    o[flat] = h[flat] = lo[flat] = c[flat]
    h, lo = np.maximum.reduce([h, o, c]), np.minimum.reduce([lo, o, c])
    volume = np.round(rng.lognormal(10.0, 0.6, size=n))
    volume[rng.random(n) < 0.01] = 0.0
    return pd.DataFrame({"open": o, "high": h, "low": lo, "close": c, "volume": volume}, index=index)


def perturb_future(bars: pd.DataFrame, t: int, how: str, seed: int = 1) -> pd.DataFrame:
    """The same frame with every bar after t replaced (timestamps kept): `spike_up` / `spike_down` put the future far
    above / below everything seen so far, `walk` is an independent random walk from bar t's close."""
    out = bars.copy()
    m = len(out) - t - 1
    if m <= 0:
        return out
    last = float(bars["close"].iloc[t])
    if how == "spike_up":
        c = np.full(m, last * 3.0)
    elif how == "spike_down":
        c = np.full(m, last * 0.3)
    elif how == "walk":
        rng = np.random.default_rng(seed + t)
        c = last * np.exp(np.cumsum(rng.normal(0.0, 0.02, size=m)))
    else:
        raise ValueError(f"unknown perturbation {how!r}")
    o = np.r_[last, c[:-1]] if how == "walk" else c * 0.99
    rows = out.index[t + 1:]
    out.loc[rows, "open"] = np.round(o / 0.05) * 0.05
    out.loc[rows, "close"] = np.round(c / 0.05) * 0.05
    out.loc[rows, "high"] = np.round(np.maximum(o, c) * 1.01 / 0.05) * 0.05
    out.loc[rows, "low"] = np.round(np.minimum(o, c) * 0.99 / 0.05) * 0.05
    if "volume" in out.columns:
        out.loc[rows, "volume"] = float(bars["volume"].max()) * 10.0
    return out


# ------------------------------------------------------------------------------------------------------- comparison
def _parts(value: Any) -> List[Tuple[str, Any]]:
    """A result as named one-value-per-bar parts (a DataFrame by column, a tuple / dict by element)."""
    if isinstance(value, pd.DataFrame):
        return [(str(c), value[c]) for c in value.columns]
    if isinstance(value, dict):
        return [(str(k), p) for k, v in value.items() for _, p in _parts(v)]
    if isinstance(value, (tuple, list)):
        out: List[Tuple[str, Any]] = []
        for i, v in enumerate(value):
            out += [(f"{i}.{name}" if name else str(i), p) for name, p in _parts(v)]
        return out
    return [("", value)]


def _values(part: Any) -> np.ndarray:
    if isinstance(part, pd.Series):
        return part.to_numpy()
    return np.asarray(part)


def _same(a: Any, b: Any) -> bool:
    if a is None or b is None:
        return a is None and b is None or _missing(a) and _missing(b)
    if isinstance(a, (float, np.floating, int, np.integer)) and isinstance(b, (float, np.floating, int, np.integer)):
        if np.isnan(a) or np.isnan(b):
            return bool(np.isnan(a) and np.isnan(b))
        return bool(np.isclose(a, b, rtol=1e-9, atol=1e-12))
    if _missing(a) or _missing(b):
        return _missing(a) and _missing(b)
    return bool(a == b)


def _missing(v: Any) -> bool:
    return v is None or v is pd.NA or v is pd.NaT or isinstance(v, (float, np.floating)) and bool(np.isnan(v))


def first_difference(full: Any, other: Any, upto: int) -> Optional[str]:
    """Where `other` disagrees with `full` on bars 0..upto (None when they agree). Both are per-bar results."""
    fp, op = _parts(full), _parts(other)
    if [n for n, _ in fp] != [n for n, _ in op]:
        return f"different shape: {[n for n, _ in fp]} vs {[n for n, _ in op]}"
    for (name, a), (_, b) in zip(fp, op):
        va, vb = _values(a), _values(b)
        if va.ndim != vb.ndim:
            return f"{name or 'value'}: a single value on one side, one per bar on the other"
        if va.ndim == 0:
            if not _same(va.item(), vb.item()):
                return f"{name or 'value'}: {va!r} vs {vb!r}"
            continue
        if len(va) <= upto or len(vb) <= upto:
            return f"{name or 'value'}: {len(va)} / {len(vb)} values for {upto + 1} bars"
        for i in range(upto + 1):
            if not _same(va[i], vb[i]):
                return f"{name or 'value'} at bar {i}: {va[i]!r} with the whole frame vs {vb[i]!r}"
    return None


# ------------------------------------------------------------------------------------------------------------ checks
def sample_bars(bars: pd.DataFrame, case: CausalCase) -> List[int]:
    """The bars t checked: `samples` spread evenly from min_t, plus the first and last bar of a session in the middle
    (session functions - VWAP, previous-day levels, the opening range - change behaviour there)."""
    n = len(bars)
    hi = n - 2
    if hi < case.min_t:
        return []
    ts = {int(x) for x in np.linspace(case.min_t, hi, num=max(2, case.samples))}
    day = bars.index.normalize()
    starts = np.flatnonzero(np.r_[False, day[1:] != day[:-1]])
    mid = [int(s) for s in starts if case.min_t < s < hi]
    if mid:
        s = mid[len(mid) // 2]
        ts.update({s, s - 1})
    return sorted(ts)


def _events(case: CausalCase, records: Any, upto: Optional[int]) -> List[Any]:
    known = case.known_at
    assert known is not None
    key = case.key or (lambda r: r)
    return [key(r) for r in records if upto is None or known(r) <= upto]


def check(case: CausalCase, bars: Optional[pd.DataFrame] = None) -> List[Violation]:
    """Runs the three checks on `bars` (default: the case's synthetic data set) and returns every violation found."""
    if bars is None:
        bars = synthetic_bars(case.length or _DEFAULT_LENGTH.get(case.bars, 600), tf=case.bars, seed=7)
    out: List[Violation] = []
    snapshot = bars.copy()
    full = case.fn(bars)
    if not bars.equals(snapshot) or not bars.index.equals(snapshot.index):
        out.append(Violation(case.name, len(bars) - 1, "immutability", "the function changed its input frame"))
        bars = snapshot
        full = case.fn(bars.copy())
    for t in sample_bars(bars, case):
        prefix = bars.iloc[: t + 1]
        if case.kind == LAST_BAR:
            a, b = case.fn(prefix), case.fn(prefix.copy())
            if first_difference(a, b, 0) is not None:
                out.append(Violation(case.name, t, "determinism", f"two calls on the same bars disagree: {a!r} vs {b!r}"))
            continue
        if case.kind == SERIES:
            diff = first_difference(full, case.fn(prefix), t)
        else:
            got, want = _events(case, case.fn(prefix), None), _events(case, full, t)
            diff = None if got == want else _event_diff(want, got, "whole frame", "bars up to t")
        if diff is not None:
            out.append(Violation(case.name, t, "truncation", diff))
        for how in case.perturbations:
            moved = perturb_future(bars, t, how)
            other = case.fn(moved)
            if case.kind == SERIES:
                diff = first_difference(full, other, t)
            else:
                got, want = _events(case, other, t), _events(case, full, t)
                diff = None if got == want else _event_diff(want, got, "whole frame", f"future {how}")
            if diff is not None:
                out.append(Violation(case.name, t, f"future:{how}", diff))
    return out


def _event_diff(want: Sequence[Any], got: Sequence[Any], a: str, b: str) -> str:
    only_a = [w for w in want if w not in got][:3]
    only_b = [g for g in got if g not in want][:3]
    return f"records known by t differ - only with {a}: {only_a}; only with {b}: {only_b}" if (only_a or only_b) else \
        f"same records in a different order ({a} vs {b})"


def check_all(names: Optional[Sequence[str]] = None) -> Dict[str, List[Violation]]:
    """Every registered case (or the named ones) -> its violations."""
    return {name: check(REGISTRY[name]) for name in (names or sorted(REGISTRY))}


__all__ = ["CausalCase", "Violation", "REGISTRY", "register", "check", "check_all", "synthetic_bars", "perturb_future",
           "first_difference", "sample_bars", "SERIES", "EVENTS", "LAST_BAR", "PERTURBATIONS"]
