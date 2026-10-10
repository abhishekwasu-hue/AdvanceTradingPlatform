"""S1b (ADR-0021 §2, §4): the ScreenQL runtime - evaluates a validated AST over server bars, one implementation per
registry entry.

* Input is finalised bars only (the data layer passes closed bars; `[0]` is the last closed bar).
* A field or function on a coarser timeframe is computed on that timeframe's bars, shifted by its own offset, and then
  aligned to the screen's bars by *close time*: a 1d value becomes visible only on the screen bars that close at or
  after that day's bar closes. No bar can see a higher-timeframe bar that had not finished (look-ahead guard, tested).
* A missing coarser frame is resampled from the base bars, and a trailing incomplete bucket is dropped.
* Cross-sectional functions (Rank, PercentileRank) work on the last bar of every symbol in the universe; they are
  computed first (innermost first) and read back as constants per symbol.
* NaN (not enough history, division by zero) never matches: every comparison with NaN is false.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

import numpy as np
import pandas as pd
from pandas.tseries.offsets import DateOffset

from app.indicators.directional import adx as _adx
from app.indicators.momentum import rsi as _rsi
from app.indicators.trend import ema as _ema
from app.indicators.volatility import atr as _atr
from app.screener import nodes as n
from app.screener.registry import FUNCTIONS
from app.screener.validator import Validated

_RESAMPLE = {"1m": "1min", "3m": "3min", "5m": "5min", "15m": "15min", "30m": "30min", "1h": "60min", "1d": "1D", "1w": "W-MON", "1M": "MS"}
_DURATION: Dict[str, Any] = {"1m": timedelta(minutes=1), "3m": timedelta(minutes=3), "5m": timedelta(minutes=5), "15m": timedelta(minutes=15),
                             "30m": timedelta(minutes=30), "1h": timedelta(hours=1), "1d": timedelta(days=1), "1w": timedelta(days=7),
                             "1M": DateOffset(months=1)}


class ScreenRuntimeError(ValueError):
    """The screen cannot run as given (not validated, missing base data)."""


@dataclass
class SymbolData:
    symbol: str
    frames: Dict[str, pd.DataFrame]                       # timeframe -> closed bars (open, high, low, close, volume[, oi]); index = bar start
    sector: Optional[str] = None
    industry: Optional[str] = None
    mcap_bucket: Optional[str] = None
    is_fno: Optional[bool] = None
    indices: Set[str] = field(default_factory=set)
    option_chain: Optional[Any] = None                     # app.brokers.models.OptionChain for PCR / ChainBias / MaxPainDistancePct


@dataclass
class Match:
    symbol: str
    matched: bool
    reason: Optional[str] = None                           # why a symbol could not be evaluated (history too short...)


def bar_close(index: pd.DatetimeIndex, tf: str) -> pd.DatetimeIndex:
    return index + _DURATION[tf]


def closed_only(df: pd.DataFrame, tf: str, now: datetime) -> pd.DataFrame:
    """Bars whose close time is not after `now` - a broker's intraday feed ends with the bar still forming, and a
    screen decides on closed bars only."""
    if df.empty:
        return df
    return df[bar_close(df.index, tf) <= pd.Timestamp(now)]


def resample(base: pd.DataFrame, base_tf: str, tf: str, keep_forming: bool = False) -> pd.DataFrame:
    """Coarser bars from base bars (bar start labels), dropping a trailing bucket that had not closed (kept only for
    S4b-2 intrabar alerts, `keep_forming`). Intraday buckets start at the exchange open, like the broker's charts and
    the Strategy Builder (`declarative._IST_OPEN_UTC`)."""
    agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    if "oi" in base.columns:
        agg["oi"] = "last"
    kwargs: Dict[str, Any] = {"label": "left", "closed": "left"}
    if n.TF_MINUTES[tf] < n.TF_MINUTES["1d"] and len(base):
        from app.strategy_engine.declarative import _IST_OPEN_UTC
        kwargs["origin"] = base.index[0].normalize() + _IST_OPEN_UTC
    out = base.resample(_RESAMPLE[tf], **kwargs).agg(agg).dropna(subset=["close"])
    if len(out) and len(base) and not keep_forming:
        last_base_close = base.index[-1] + _DURATION[base_tf]
        if out.index[-1] + _DURATION[tf] > last_base_close:
            out = out.iloc[:-1]
    return out


class _Eval:
    def __init__(self, data: SymbolData, base_tf: str, params: Dict[str, Any], constants: Dict[int, float]) -> None:
        self.data, self.base_tf, self.params, self.constants = data, base_tf, params, constants
        if base_tf not in data.frames or data.frames[base_tf].empty:
            raise ScreenRuntimeError(f"{data.symbol}: no {base_tf} bars")
        self.frames: Dict[str, pd.DataFrame] = dict(data.frames)

    def frame(self, tf: str) -> pd.DataFrame:
        if tf not in self.frames:
            self.frames[tf] = resample(self.frames[self.base_tf], self.base_tf, tf)
        return self.frames[tf]

    def align(self, value: Any, src_tf: str, dst_tf: str) -> Any:
        """A src_tf series onto dst_tf bars by close time (look-ahead safe)."""
        if not isinstance(value, pd.Series) or src_tf == dst_tf:
            return value
        avail = pd.Series(value.to_numpy(), index=bar_close(value.index, src_tf))
        avail = avail[~avail.index.duplicated(keep="last")].sort_index()
        dst_close = bar_close(self.frame(dst_tf).index, dst_tf)
        out = avail.reindex(avail.index.union(dst_close)).ffill().reindex(dst_close)
        return pd.Series(out.to_numpy(), index=self.frame(dst_tf).index)

    def ev(self, node: Any, tf: str) -> Any:  # noqa: C901 - one switch over the node kinds
        if isinstance(node, n.Num):
            return node.value
        if isinstance(node, (n.Str, n.Bool)):
            return node.value
        if isinstance(node, n.Param):
            return self.params[node.name]
        if isinstance(node, n.Field):
            own = node.tf or tf
            col = self.frame(own).get(node.name)
            series = col.astype(float) if col is not None else pd.Series(np.nan, index=self.frame(own).index)
            return self.align(series.shift(node.offset) if node.offset else series, own, tf)
        if isinstance(node, n.Call):
            if id(node) in self.constants:
                return self.constants[id(node)]
            own = node.tf or tf
            value = self.call(node, own)
            if node.offset and isinstance(value, pd.Series):
                value = value.shift(node.offset)
            return self.align(value, own, tf)
        if isinstance(node, n.Unary):
            return -self.ev(node.operand, tf)
        if isinstance(node, n.Binary):
            a, b = self.ev(node.left, tf), self.ev(node.right, tf)
            if node.op == "+":
                return a + b
            if node.op == "-":
                return a - b
            if node.op == "*":
                return a * b
            with np.errstate(divide="ignore", invalid="ignore"):
                out = a / b if not (np.isscalar(b) and b == 0) else a * np.nan
            return out.replace([np.inf, -np.inf], np.nan) if isinstance(out, pd.Series) else (np.nan if out in (np.inf, -np.inf) else out)
        if isinstance(node, n.Compare):
            return _cmp(node.op, self.ev(node.left, tf), self.ev(node.right, tf))
        if isinstance(node, n.Between):
            v = self.ev(node.value, tf)
            return _and(_cmp(">=", v, self.ev(node.low, tf)), _cmp("<=", v, self.ev(node.high, tf)))
        if isinstance(node, n.In):
            v = self.ev(node.value, tf)
            items = [self.ev(i, tf) for i in node.items]
            if isinstance(v, pd.Series):
                return v.isin(items)
            return v is not None and v in items
        if isinstance(node, n.Logic):
            parts = [self.ev(i, tf) for i in node.items]
            out = parts[0]
            for p in parts[1:]:
                out = _and(out, p) if node.op == "ALL" else _or(out, p)
            return out
        if isinstance(node, n.Not):
            v = self.ev(node.item, tf)
            return ~_as_bool(v) if isinstance(v, pd.Series) else not bool(v)
        raise ScreenRuntimeError(f"cannot evaluate {type(node).__name__}")

    def series(self, node: Any, tf: str) -> pd.Series:
        v = self.ev(node, tf)
        if isinstance(v, pd.Series):
            return v.astype(float) if v.dtype != bool else v
        return pd.Series(float(v) if not isinstance(v, (str, type(None))) else np.nan, index=self.frame(tf).index)

    def call(self, node: n.Call, tf: str) -> Any:  # noqa: C901
        name, args = node.name, node.args
        f = self.frame(tf)

        def win(i: int, default: int = 0) -> int:
            return int(self.ev(args[i], tf)) if len(args) > i else default

        def num(i: int, default: float) -> float:
            return float(_last(self.ev(args[i], tf))) if len(args) > i else default

        impl: Dict[str, Callable[[], Any]] = {
            "SMA": lambda: self.series(args[0], tf).rolling(win(1)).mean(),
            "EMA": lambda: _ema(self.series(args[0], tf), win(1)),
            "RSI": lambda: _rsi(f["close"].astype(float), win(0, 14)),
            "ADX": lambda: _adx(f, win(0, 14))["adx"],
            "ATR": lambda: _atr(f, win(0, 14)),
            "VWAP": lambda: _operand(f, "VWAP"),
            "PlusDI": lambda: _adx(f, win(0, 14))["plus_di"],
            "MinusDI": lambda: _adx(f, win(0, 14))["minus_di"],
            "Supertrend": lambda: _operand(f, "SUPERTREND", win(0, 10), num(1, 3.0)),
            "BBUpper": lambda: _operand(f, "BB_UPPER", win(0, 20), num(1, 2.0)),
            "BBMid": lambda: _operand(f, "BB_MID", win(0, 20), num(1, 2.0)),
            "BBLower": lambda: _operand(f, "BB_LOWER", win(0, 20), num(1, 2.0)),
            "DayOpen": lambda: _operand(f, "DAY_OPEN"),
            "PDH": lambda: _operand(f, "PDH"),
            "PDL": lambda: _operand(f, "PDL"),
            "PDC": lambda: _operand(f, "PDC"),
            "ORHigh": lambda: _operand(f, "OR_HIGH", win(0)),
            "ORLow": lambda: _operand(f, "OR_LOW", win(0)),
            "Trend": lambda: _structure(f, win(0, 3))[0],
            "StructureEvent": lambda: _structure(f, win(0, 3))[1],
            "PatternBullish": lambda: _pattern(f, "BULLISH"),
            "PatternBearish": lambda: _pattern(f, "BEARISH"),
            "NearSupport": lambda: _near_zone(f, "NEAR_SUPPORT", num(0, 0.5), win(1, 3)),
            "NearResistance": lambda: _near_zone(f, "NEAR_RESISTANCE", num(0, 0.5), win(1, 3)),
            "Pattern": lambda: _pattern_series(f, str(self.ev(args[0], tf))),
            "SwingHigh": lambda: _swings(f, int(num(0, 0)))[0],
            "SwingLow": lambda: _swings(f, int(num(0, 0)))[1],
            "SwingDirection": lambda: _swings(f, int(num(0, 0)))[2],
            "MedianRange": lambda: _median_range(f, win(0, 20)),
            "ReversalAt": lambda: _reversal_series(f, self.series(args[0], tf), str(self.ev(args[1], tf))),
            "PCR": lambda: _chain(self.data.option_chain, "pcr"),
            "ChainBias": lambda: _chain(self.data.option_chain, "bias"),
            "MaxPainDistancePct": lambda: _chain(self.data.option_chain, "max_pain_distance_pct"),
            "Max": lambda: self.series(args[0], tf).rolling(win(1)).max(),
            "Min": lambda: self.series(args[0], tf).rolling(win(1)).min(),
            "Greatest": lambda: pd.concat([self.series(a, tf) for a in args], axis=1).max(axis=1, skipna=False),
            "Least": lambda: pd.concat([self.series(a, tf) for a in args], axis=1).min(axis=1, skipna=False),
            "Count": lambda: _as_bool(self.ev(args[1], tf), f.index).astype(float).rolling(win(0)).sum(),
            "CountStreak": lambda: _streak(_as_bool(self.ev(args[0], tf), f.index)),
            "CrossAbove": lambda: _cross(self.series(args[0], tf), self.series(args[1], tf), above=True),
            "CrossBelow": lambda: _cross(self.series(args[0], tf), self.series(args[1], tf), above=False),
            "PctChange": lambda: self.series(args[0], tf).pct_change(win(1), fill_method=None) * 100.0,
            "ZScore": lambda: _zscore(self.series(args[0], tf), win(1)),
            "Lag": lambda: self.series(args[0], tf).shift(win(1)),
            "Sector": lambda: self.data.sector,
            "Industry": lambda: self.data.industry,
            "McapBucket": lambda: self.data.mcap_bucket,
            "IsFnO": lambda: bool(self.data.is_fno),
            "IndexMember": lambda: str(self.ev(args[0], tf)) in self.data.indices,
        }
        if name not in impl:
            raise ScreenRuntimeError(f"{name} has no runtime implementation (cross-sectional functions run through run_screen)")
        return impl[name]()


def _operand(f: pd.DataFrame, indicator: str, period: int = 14, mult: float = 3.0) -> pd.Series:
    """The Strategy Builder's own indicator code (S1c parity with the scanner and the strategies)."""
    from app.strategy_engine.declarative import Operand
    return Operand(type="indicator", indicator=indicator, period=max(1, period), multiplier=mult).series(f).astype(float)  # type: ignore[arg-type]


def _structure(f: pd.DataFrame, window: int) -> Tuple[str, str]:
    from app.price_action.market_structure import analyze_market_structure
    result = analyze_market_structure(f, window=window)
    latest = result.events[-1] if result.events else None
    return result.trend.value, (f"{latest.event}_{latest.direction}".upper() if latest else "")


def _pattern(f: pd.DataFrame, direction: str) -> bool:
    from app.price_action.candlestick_patterns import detect_patterns_at
    return any(m.direction == direction for m in detect_patterns_at(f, len(f) - 1))


def _pattern_series(f: pd.DataFrame, name: str) -> pd.Series:
    """S5-A: True on each bar where the named pattern shows (`candlestick_patterns.pattern_masks`, vectorised: a whole
    frame costs about as much as one bar did). The first two bars are False."""
    from app.price_action.candlestick_patterns import pattern_masks
    masks = pattern_masks(f)
    if name not in masks:
        raise ScreenRuntimeError(f"unknown pattern {name!r}")
    return pd.Series(masks[name], index=f.index)


def _swings(f: pd.DataFrame, degree: int) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """S5-A: (last confirmed swing high, last confirmed swing low, UP/DOWN) on every bar, from the causal swing engine
    (app/price_action/causal_swings.py, default settings). A pivot counts from the bar that CONFIRMED it - when price
    had come back from the extreme by the degree's threshold - never from the extreme bar itself (no look-ahead)."""
    from app.price_action import causal_swings as cs
    from app.price_action import pa_settings
    s = pa_settings.settings()
    if not 0 <= degree < len(s["swing_atr_mult"]):
        raise ScreenRuntimeError(f"swing degree {degree} is not one of 0-{len(s['swing_atr_mult']) - 1}")
    frame = pd.DataFrame({k: f[k].astype(float).to_numpy() for k in ("open", "high", "low", "close")})
    frame["timestamp"] = frame["bar_end"] = f.index
    high, low = np.full(len(f), np.nan), np.full(len(f), np.nan)
    direction = np.full(len(f), None, dtype=object)
    for p in cs.degree_pivots(frame, degree, s):                         # confirmation order
        c = int(p.confirmed_idx)                                          # type: ignore[arg-type]
        (high if p.kind == "H" else low)[c] = p.price
        direction[c] = "DOWN" if p.kind == "H" else "UP"
    hi = pd.Series(high, index=f.index).ffill()
    lo = pd.Series(low, index=f.index).ffill()
    d = pd.Series(direction, index=f.index).ffill()          # None before the first pivot: missing, never matches
    return hi, lo, d


def _reversal_series(f: pd.DataFrame, level: pd.Series, direction: str) -> pd.Series:
    """S5-A2: on each bar, did price logically reverse at that bar's `level` (app/price_action/reversal.py, composite
    mode, default settings)? The same answer `evaluate_reversal` gives on the candles up to that bar - the window always
    ends on the bar, so it is causal. A bar whose last few candles never reached the level cannot pass the touch test,
    so only bars that did are evaluated (a cheap filter; the result is the same)."""
    from app.price_action import pa_settings
    from app.price_action import reversal as rv
    from app.price_action.breaks import median_range
    if direction not in ("bullish", "bearish"):
        raise ScreenRuntimeError(f"unknown reversal direction {direction!r}")
    s = pa_settings.settings()
    dirn = 1 if direction == "bullish" else -1
    frame = pd.DataFrame({k: f[k].astype(float).to_numpy() for k in ("open", "high", "low", "close")})
    mr = median_range(frame, s["median_range_n"])
    bars = rv.Bars(frame, mr)
    lv = level.to_numpy(float) if isinstance(level, pd.Series) else np.full(len(f), float(level))
    tol = s["touch_tol_mr"] * np.where(np.isfinite(mr), mr, 0.0)
    reach_n = s["touch_reclaim_window"] + 1                      # the longest window, with a follow-through candle
    if dirn > 0:
        reached = frame["low"].rolling(reach_n, min_periods=1).min().to_numpy() <= lv + tol
    else:
        reached = frame["high"].rolling(reach_n, min_periods=1).max().to_numpy() >= lv - tol
    out = np.zeros(len(f), dtype=bool)
    for j in np.flatnonzero(reached & np.isfinite(lv)):
        out[j] = bool(rv.evaluate(bars, int(j), dirn, [float(lv[j])], float(tol[j]), s)["ok"])
    return pd.Series(out, index=f.index)


def _median_range(f: pd.DataFrame, n_: int) -> pd.Series:
    from app.price_action.breaks import median_range
    return pd.Series(median_range(f, max(1, n_)), index=f.index)


def _near_zone(f: pd.DataFrame, kind: str, tolerance_pct: float, window: int) -> bool:
    from app.scanner.engine import _check_structure_filter
    from app.scanner.models import StructureFilter, StructureFilterType
    if not tolerance_pct > 0:
        return False
    return _check_structure_filter(StructureFilter(filter_type=StructureFilterType(kind), tolerance_pct=tolerance_pct), f, window)[0]


def _chain(chain: Any, what: str) -> Any:
    if chain is None:
        return None if what == "bias" else np.nan
    from app.option_chain.analysis import analyze_option_chain
    a = analyze_option_chain(chain)
    if what == "pcr":
        return np.nan if a.pcr is None else float(a.pcr)
    if what == "bias":
        return a.bias.value
    if a.max_pain is None or not a.underlying_ltp:
        return np.nan
    return abs(a.underlying_ltp - a.max_pain) / a.underlying_ltp * 100.0


def _as_bool(v: Any, index: Optional[pd.Index] = None) -> Any:
    if isinstance(v, pd.Series):
        return v.fillna(False).astype(bool) if v.dtype != bool else v
    if index is not None:
        return pd.Series(bool(v), index=index)
    return bool(v)


def _cmp(op: str, a: Any, b: Any) -> Any:
    if a is None or b is None:
        return False
    if isinstance(a, pd.Series) or isinstance(b, pd.Series):
        ops = {">": "gt", ">=": "ge", "<": "lt", "<=": "le", "==": "eq", "!=": "ne"}
        left, right = (a, b) if isinstance(a, pd.Series) else (b, a)
        if not isinstance(a, pd.Series):
            ops = {">": "lt", ">=": "le", "<": "gt", "<=": "ge", "==": "eq", "!=": "ne"}
        out = getattr(left, ops[op])(right)
        valid = left.notna() & (right.notna() if isinstance(right, pd.Series) else (right == right))
        return (out & valid).astype(bool)
    if isinstance(a, float) and np.isnan(a) or isinstance(b, float) and np.isnan(b):
        return False
    return {">": a > b, ">=": a >= b, "<": a < b, "<=": a <= b, "==": a == b, "!=": a != b}[op]


def _and(a: Any, b: Any) -> Any:
    if isinstance(a, pd.Series) or isinstance(b, pd.Series):
        return _as_bool(a) & _as_bool(b) if isinstance(a, pd.Series) else _as_bool(b) & bool(a)
    return bool(a) and bool(b)


def _or(a: Any, b: Any) -> Any:
    if isinstance(a, pd.Series) or isinstance(b, pd.Series):
        return _as_bool(a) | _as_bool(b) if isinstance(a, pd.Series) else _as_bool(b) | bool(a)
    return bool(a) or bool(b)


def _streak(cond: pd.Series) -> pd.Series:
    c = cond.astype(int)
    return c.groupby((c == 0).cumsum()).cumsum().astype(float)


def _cross(a: pd.Series, b: pd.Series, *, above: bool) -> pd.Series:
    now = a > b if above else a < b
    before = a.shift(1) <= b.shift(1) if above else a.shift(1) >= b.shift(1)
    valid = a.notna() & b.notna() & a.shift(1).notna() & b.shift(1).notna()
    return (now & before & valid).astype(bool)


def _zscore(x: pd.Series, n_: int) -> pd.Series:
    std = x.rolling(n_).std()
    return ((x - x.rolling(n_).mean()) / std.replace(0.0, np.nan)).astype(float)


def _last(value: Any) -> Any:
    if isinstance(value, pd.Series):
        return value.iloc[-1] if len(value) else np.nan
    return value


def _cross_sectional(ast: Any, universe: List[SymbolData], base_tf: str, params: Dict[str, Any]) -> Dict[str, Dict[int, float]]:
    """Rank / PercentileRank over the universe's last bars, innermost first."""
    constants: Dict[str, Dict[int, float]] = {d.symbol: {} for d in universe}
    cs = [node for node in n.walk(ast) if isinstance(node, n.Call) and FUNCTIONS.get(node.name) and FUNCTIONS[node.name].cross_sectional]
    for node in reversed(cs):                                       # pre-order reversed: inner calls first
        values: Dict[str, float] = {}
        groups: Dict[str, Any] = {}
        for d in universe:
            try:
                ev = _Eval(d, base_tf, params, constants[d.symbol])
                values[d.symbol] = float(_last(ev.ev(node.args[0], base_tf)))
                if "by" in node.kwargs:
                    groups[d.symbol] = ev.ev(node.kwargs["by"], base_tf)
            except (ScreenRuntimeError, TypeError, ValueError):
                values[d.symbol] = np.nan
        s = pd.Series(values, dtype=float)
        if node.name == "Rank":
            ranked = s.rank(ascending=False, method="min")
        else:
            key = pd.Series({k: groups.get(k) for k in s.index}, dtype=object)
            ranked = s.groupby(key.fillna("__none__")).rank(pct=True, method="max") * 100.0 if groups else s.rank(pct=True, method="max") * 100.0
        for sym in s.index:
            constants[sym][id(node)] = float(ranked.get(sym, np.nan))
    return constants


def run_screen(ast: Any, validated: Validated, universe: List[SymbolData], *, base_tf: str, params: Optional[Dict[str, Any]] = None,
               check_history: bool = True) -> List[Match]:
    """Every symbol: does the screen hold on its last closed bar? Runs only a validated screen. With `check_history` a symbol
    shorter than the validator's lookback is reported as such; without it, it is simply evaluated (NaN never matches)."""
    if not validated.ok:
        raise ScreenRuntimeError("the screen did not pass validation")
    params = dict(params or {})
    constants = _cross_sectional(ast, universe, base_tf, params) if validated.cross_sectional else {d.symbol: {} for d in universe}
    out: List[Match] = []
    for d in universe:
        if d.frames.get(base_tf) is None or d.frames[base_tf].empty:
            out.append(Match(d.symbol, False, f"no {base_tf} bars"))
            continue
        ev = _Eval(d, base_tf, params, constants[d.symbol])
        # S5-A review: a higher timeframe built from the base bars is checked too (a 1d swing on a 5m screen needs 100
        # daily bars; a few days of 5m bars would otherwise give NaN with no reason)
        short = [tf for tf, bars in validated.lookback.items()
                 if (tf in d.frames or n.TF_MINUTES.get(tf, 0) > n.TF_MINUTES[base_tf]) and len(ev.frame(tf)) < bars] if check_history else []
        if short:
            out.append(Match(d.symbol, False, f"not enough history on {', '.join(sorted(short))}"))
            continue
        try:
            value = _last(ev.ev(ast, base_tf))
        except ScreenRuntimeError as exc:
            out.append(Match(d.symbol, False, str(exc)))
            continue
        out.append(Match(d.symbol, bool(value) if not (isinstance(value, float) and np.isnan(value)) else False))
    return out


def evaluate(ast: Any, data: SymbolData, *, base_tf: str, params: Optional[Dict[str, Any]] = None) -> Any:
    """The full series (or constant) of an expression for one symbol - for tests, charts and the builder preview."""
    return _Eval(data, base_tf, dict(params or {}), {}).ev(ast, base_tf)


__all__ = ["SymbolData", "Match", "run_screen", "evaluate", "resample", "closed_only", "ScreenRuntimeError"]
