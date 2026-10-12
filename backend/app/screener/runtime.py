"""S1b (ADR-0021 §2, §4): the ScreenQL runtime - evaluates a validated AST over server bars, one implementation per
registry entry.

* Input is finalised bars only (the data layer passes closed bars; `[0]` is the last closed bar).
* A field or function on a coarser timeframe is computed on that timeframe's bars, shifted by its own offset, and then
  aligned to the screen's bars by *close time*: a 1d value becomes visible only on the screen bars that close at or
  after that day's bar closes. No bar can see a higher-timeframe bar that had not finished (look-ahead guard, tested).
* A missing coarser frame is resampled from the base bars, and a trailing incomplete bucket is dropped.
* Cross-sectional functions (Rank, PercentileRank, RankScore) work on the last bar of every symbol in the universe; they are
  computed first (innermost first) and read back as constants per symbol.
* NaN (not enough history, division by zero) never matches: every comparison with NaN is false.
"""
from __future__ import annotations

import logging
import weakref
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

logger = logging.getLogger(__name__)

_RESAMPLE = {"1m": "1min", "3m": "3min", "5m": "5min", "15m": "15min", "30m": "30min", "1h": "60min", "1d": "1D", "1w": "W-MON", "1M": "MS"}
_DURATION: Dict[str, Any] = {"1m": timedelta(minutes=1), "3m": timedelta(minutes=3), "5m": timedelta(minutes=5), "15m": timedelta(minutes=15),
                             "30m": timedelta(minutes=30), "1h": timedelta(hours=1), "1d": timedelta(days=1), "1w": timedelta(days=7),
                             "1M": DateOffset(months=1)}


EXCHANGE_TZ = "Asia/Kolkata"
# A value in an evaluation is a constant, one symbol's series, or (the vectorised path, app/screener/panel.py) a frame
# with one column per symbol. The operators below treat the last two alike: pandas applies them column by column.
_FRAMES = (pd.Series, pd.DataFrame)


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
    exchange: Optional[str] = None                         # U6 BK2: the session clock of the intraday breakout functions (None = NSE)
    zones: Optional[Any] = None                            # Z7a-3: app.price_action.zones.read.ZoneTimeline for the zone functions
    ownership: Optional[Any] = None                        # N3 S8a: app.screener.ownership.Ownership (point in time)


@dataclass
class Match:
    symbol: str
    matched: bool
    reason: Optional[str] = None                           # why a symbol could not be evaluated (history too short...)


def bar_close(index: pd.DatetimeIndex, tf: str) -> pd.DatetimeIndex:
    """When a bar that starts at `index` has closed. Months are counted in exchange time (BK1 verification B): a month
    labelled 1 Aug 00:00 IST is 31 Jul 18:30 UTC, and one calendar month on from the UTC stamp clipped to 30 Aug -
    a day before the month's last session - so the month was visible early."""
    if tf == "1M" and getattr(index, "tz", None) is not None:
        return (index.tz_convert(EXCHANGE_TZ) + _DURATION[tf]).tz_convert(index.tz)
    return index + _DURATION[tf]


def closed_only(df: pd.DataFrame, tf: str, now: datetime) -> pd.DataFrame:
    """Bars whose close time is not after `now` - a broker's intraday feed ends with the bar still forming, and a
    screen decides on closed bars only."""
    if df.empty:
        return df
    return df[bar_close(df.index, tf) <= pd.Timestamp(now)]


def session_origin(index: pd.DatetimeIndex) -> pd.Timestamp:
    """09:15 IST on the first bar's date, in the index's own time zone. U6 BK1 review (M5): normalising an IST-aware stamp
    gave IST midnight, so 03:45 later was 03:45 IST and 1h bars started at 09:45 / 2h at 11:45 (Fyers / Dhan / Kite
    frames are IST-aware). A naive index is read as UTC, as before."""
    from app.strategy_engine.declarative import _IST_OPEN_UTC
    first = index[0]
    if first.tzinfo is None:
        return first.normalize() + _IST_OPEN_UTC
    return (first.tz_convert("UTC").normalize() + _IST_OPEN_UTC).tz_convert(first.tzinfo)


def resample(base: pd.DataFrame, base_tf: str, tf: str, keep_forming: bool = False) -> pd.DataFrame:
    """Coarser bars from base bars (bar start labels), dropping a trailing bucket that had not closed (kept only for
    S4b-2 intrabar alerts, `keep_forming`). Intraday buckets start at the exchange open, like the broker's charts and
    the Strategy Builder (`declarative._IST_OPEN_UTC`)."""
    agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    if "oi" in base.columns:
        agg["oi"] = "last"
    kwargs: Dict[str, Any] = {"label": "left", "closed": "left"}
    tz = getattr(base.index, "tz", None)
    src = base
    if n.TF_MINUTES[tf] < n.TF_MINUTES["1d"] and len(base):
        kwargs["origin"] = session_origin(base.index)
    elif tz is not None and len(base):
        # U6 BK1 review (M6): sessions, weeks and months are exchange-calendar buckets. Daily bars stamped 00:00 IST are
        # 18:30 UTC the day before; bucketing them in UTC put a Monday into the previous week (a week seen a day early).
        src = base.tz_convert(EXCHANGE_TZ)
    out = src.resample(_RESAMPLE[tf], **kwargs).agg(agg).dropna(subset=["close"])
    if src is not base:
        out.index = out.index.tz_convert(tz)
    if len(out) and len(base) and not keep_forming:
        last_base_close = bar_close(base.index[-1:], base_tf)[0]
        if bar_close(out.index[-1:], tf)[0] > last_base_close:
            out = out.iloc[:-1]
    return out


class _Eval:
    def __init__(self, data: SymbolData, base_tf: str, params: Dict[str, Any], constants: Dict[int, float]) -> None:
        self.data, self.base_tf, self.params, self.constants = data, base_tf, params, constants
        if base_tf not in data.frames or data.frames[base_tf].empty:
            raise ScreenRuntimeError(f"{data.symbol}: no {base_tf} bars")
        self.frames: Dict[str, pd.DataFrame] = dict(data.frames)
        self._session_bars: Dict[Tuple[Any, ...], Any] = {}      # U6 BK2: one session map / VWAP / time-of-day series per frame

    def session_bars(self, tf: str, sessions: int = 20, min_sessions: int = 5, mode: str = "bar") -> Any:
        """U6 BK2: the breakout Bars of a timeframe on the symbol's exchange clock, cached for the screen run, so a screen
        that reads ORBreakout, SessionVWAP and RelVolumeTOD on one frame builds its sessions and averages once."""
        key = (tf, sessions, min_sessions, mode)
        if key not in self._session_bars:
            from app.screener.breakouts import session as ses
            from app.screener.breakouts.screens import Bars
            try:
                calendar = ses.calendar_for(self.data.exchange or "NSE")
            except ValueError as exc:
                raise ScreenRuntimeError(str(exc)) from exc
            b = Bars(self.frame(tf), _breakout_params({"tod_sessions": sessions, "tod_min_sessions": min_sessions, "tod_mode": mode}),
                     tf=tf, calendar=calendar)
            try:
                b.sessions()
            except ValueError as exc:
                raise ScreenRuntimeError(str(exc)) from exc
            self._session_bars[key] = b
        return self._session_bars[key]

    def frame(self, tf: str) -> pd.DataFrame:
        if tf not in self.frames:
            src = self.base_tf
            # U6 BK1 verification (A): weekly / monthly from a given daily frame (the deeper history) on an intraday screen
            if "1d" in self.frames and n.TF_MINUTES[self.base_tf] < n.TF_MINUTES["1d"] < n.TF_MINUTES[tf]:
                src = "1d"
            self.frames[tf] = resample(self.frames[src], src, tf)
        return self.frames[tf]

    def align(self, value: Any, src_tf: str, dst_tf: str) -> Any:
        """A src_tf series onto dst_tf bars by close time (look-ahead safe)."""
        if not isinstance(value, pd.Series) or src_tf == dst_tf:
            return value
        avail = pd.Series(value.to_numpy(), index=bar_close(value.index, src_tf))
        avail = avail[~avail.index.duplicated(keep="last")].sort_index()
        dst_close = bar_close(self.frame(dst_tf).index, dst_tf)
        with pd.option_context("future.no_silent_downcasting", True):       # a category series stays object (no dtype guess)
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
            if node.offset and isinstance(value, _FRAMES):
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
            return out.replace([np.inf, -np.inf], np.nan) if isinstance(out, _FRAMES) else (np.nan if out in (np.inf, -np.inf) else out)
        if isinstance(node, n.Compare):
            return _cmp(node.op, self.ev(node.left, tf), self.ev(node.right, tf))
        if isinstance(node, n.Between):
            v = self.ev(node.value, tf)
            return _and(_cmp(">=", v, self.ev(node.low, tf)), _cmp("<=", v, self.ev(node.high, tf)))
        if isinstance(node, n.In):
            v = self.ev(node.value, tf)
            items = [self.ev(i, tf) for i in node.items]
            if isinstance(v, _FRAMES):
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
            return ~_as_bool(v) if isinstance(v, _FRAMES) else not bool(v)
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
            # one value for the whole series: only a literal or a $parameter reaches here (the validator refuses an
            # expression for a literal_only argument - its last-bar value would be applied to every earlier bar)
            return float(_last(self.ev(args[i], tf))) if len(args) > i else default

        def _text(i: int, default: str) -> str:
            return str(self.ev(args[i], tf)) if len(args) > i else default

        def _bool(i: int, default: bool) -> bool:
            return bool(_last(self.ev(args[i], tf))) if len(args) > i else default

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
            "SwingZoneStrength": lambda: _swing_zone(f, str(self.ev(args[0], tf)), int(num(1, 0)), "strength"),
            "SwingZoneDistance": lambda: _swing_zone(f, str(self.ev(args[0], tf)), int(num(1, 0)), "distance"),
            "RealBreak": lambda: _real_break_series(f, self.series(args[0], tf), str(self.ev(args[1], tf)), win(2, 20)),
            "ReversalAt": lambda: _reversal_series(f, self.series(args[0], tf), str(self.ev(args[1], tf))),
            # U6 BK1: breakout screens (app/screener/breakouts) - the same series the breakout engine and templates use
            "RangeBreakout": lambda: _breakout(f, "B1", str(self.ev(args[0], tf)), {"range_n": win(1, 20), "min_break_atr": num(2, 0.2),
                                                                                       "vol_mult": num(3, 1.5), "range_mode": _text(4, "fixed")}),
            "SwingBreakout": lambda: _breakout(f, "B2", str(self.ev(args[0], tf)), {"swing_degree": int(num(1, 1)), "min_break_atr": num(2, 0.2)}),
            "SwingBreakType": lambda: _breakout(f, "B2", str(self.ev(args[0], tf)), {"swing_degree": int(num(1, 1)), "min_break_atr": num(2, 0.2)},
                                                "type"),
            "NRBreakout": lambda: _breakout(f, "B4", str(self.ev(args[0], tf)), {"nr_setup": _text(1, "nr7"), "vol_mult": num(2, 1.5),
                                                                                 "min_break_atr": num(3, 0.2)}),
            "NewHighBreakout": lambda: _breakout(f, "B7", str(self.ev(args[0], tf)), {"high_weeks": win(1, 52), "vol_mult": num(2, 1.5),
                                                                                      "min_break_atr": num(3, 0.2)}),
            "RangeExpansion": lambda: _breakout(f, "B13", str(self.ev(args[0], tf)), {"expansion_k": num(1, 2.0), "vol_mult": num(2, 1.5)}),
            "VolumeBreakout": lambda: _breakout(f, "B14", str(self.ev(args[0], tf)), {"shock_mult": num(1, 2.0), "range_n": win(2, 20)}),
            "RelVolume": lambda: _rel_volume(f, win(0, 20)),
            "ATRPercentile": lambda: _atr_percentile(f, win(0, 14), win(1, 100)),
            "DowTrend": lambda: _dow_trend(f, int(num(0, 0))),
            # U6 BK2: intraday breakouts on the exchange session (NSE clock; app/screener/breakouts/intraday.py)
            "ORBreakout": lambda: _breakout(f, "B16", str(self.ev(args[0], tf)), {
                "or_minutes": num(1, 15), "tod_vol_mult": num(2, 1.5), "vwap_agree": _bool(3, True), "skip_first_min": num(4, 15),
                "min_break_atr": num(5, 0.2)}, tf=tf, bars=self.session_bars(tf)),
            "DayLevelBreak": lambda: _breakout(f, "B17", str(self.ev(args[0], tf)), {
                "day_level": _text(1, "first_hour"), "tod_vol_mult": num(2, 1.5), "vwap_agree": _bool(3, True), "skip_first_min": num(4, 15),
                "min_break_atr": num(5, 0.2)}, tf=tf, bars=self.session_bars(tf)),
            "DayExtremeBreak": lambda: _breakout(f, "B18", str(self.ev(args[0], tf)), {
                "tod_vol_mult": num(1, 1.5), "vwap_agree": _bool(2, True), "skip_first_min": num(3, 15), "min_break_atr": num(4, 0.2)}, tf=tf,
                bars=self.session_bars(tf)),
            "RelVolumeTOD": lambda: pd.Series(self.session_bars(tf, win(0, 20), min(5, win(0, 20)), _text(1, "bar")).tod_rvol(), index=f.index,
                                              dtype=float),
            "SessionVWAP": lambda: pd.Series(self.session_bars(tf).vwap(), index=f.index, dtype=float),
            # Z7a-3: market zones - the stored snapshot as of each bar's close (app/screener/zones.py)
            "NearZone": lambda: _zone(self.data, f, tf, "near", _text(0, "any"), num(1, 0.5), _text(2, "all")),
            "ZoneRole": lambda: _zone(self.data, f, tf, "role", "any", num(0, 0.5), _text(1, "all")),
            "ZoneState": lambda: _zone(self.data, f, tf, "state", _text(0, "any"), num(1, 0.5), _text(2, "all")),
            "ZoneStrength": lambda: _zone(self.data, f, tf, "strength", _text(0, "any"), num(1, 0.5), _text(2, "all")),
            # N3 S8a: ownership and events, the rows known by each bar's close (app/screener/ownership.py)
            "Holding": lambda: _ownership(self.data, f, tf, "holding", _text(0, "promoter")),
            "HoldingChange": lambda: _ownership(self.data, f, tf, "holding", _text(0, "promoter"), int(num(1, 1))),
            "DaysToEvent": lambda: _ownership(self.data, f, tf, "event", _text(0, "any")),
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


_ZONE_CACHE: Dict[int, Tuple[Any, Any, Dict[Tuple[str, int], Tuple[np.ndarray, np.ndarray]]]] = {}


def _swing_zone(f: pd.DataFrame, side: str, degree: int, what: str) -> pd.Series:
    """S5-A4: the zone at the last CONFIRMED swing low (support) or high (resistance) as of each bar, measured by the
    trade-port level_strength module (default settings) from bars up to that bar.

    - The zone is the pivot candle from its extreme to its body: support [low, min(open, close)], resistance
      [max(open, close), high]. The pivot bar is the zone's origin (departure and base are measured from it); the pivot
      is used only from the bar that confirmed it, and every bar after the origin that the measures read is <= the bar.
    - `strength` is strength_score (0-100) without leg labels (no leg classifier in ScreenQL) and without time at price
      (that needs 1-minute data), so those two parts score as unknown, the same way the module scores them.
    - `distance` is (close - zone mid) / median range (dist_mr). Missing before the first confirmed pivot, and on a bar
      with no price range to measure in (flat bars: a zero median range).
    Strength and distance come from one pass, kept for the frame, so a screen using both pays once."""
    if side not in ("low", "high"):
        raise ScreenRuntimeError(f"unknown swing zone side {side!r}")
    stamp = (len(f), f.index[-1] if len(f) else None, float(f["close"].iloc[-1]) if len(f) else None)
    ref = _ZONE_CACHE.get(id(f))
    if ref is None or ref[0]() is not f or ref[1] != stamp:               # another frame, or this one was changed
        if len(_ZONE_CACHE) > 64:                                          # frames of finished runs: drop them
            _ZONE_CACHE.clear()
        ref = (weakref.ref(f), stamp, {})
        _ZONE_CACHE[id(f)] = ref
    key = (side, degree)
    if key not in ref[2]:
        ref[2][key] = _swing_zone_arrays(f, side, degree)
    strength, distance = ref[2][key]
    return pd.Series((strength if what == "strength" else distance).copy(), index=f.index)   # the cache stays intact


def _swing_zone_arrays(f: pd.DataFrame, side: str, degree: int) -> Tuple[np.ndarray, np.ndarray]:
    from app.price_action import causal_swings as cs
    from app.price_action import level_strength as ls
    from app.price_action import pa_settings
    s = pa_settings.settings()
    if not 0 <= degree < len(s["swing_atr_mult"]):
        raise ScreenRuntimeError(f"swing degree {degree} is not one of 0-{len(s['swing_atr_mult']) - 1}")
    # level_strength finds the origin by timestamp; bar-numbered stamps make that lookup exact whatever the index holds
    # (time zone, duplicates). Nothing else here reads the stamps (time at price is not used).
    stamps = pd.to_datetime(np.arange(len(f)), unit="s")
    frame = pd.DataFrame({k: f[k].astype(float).to_numpy() for k in ("open", "high", "low", "close")})
    frame["timestamp"] = frame["bar_end"] = stamps
    kind = "L" if side == "low" else "H"
    origin = np.full(len(f), -1, dtype=int)                               # the pivot bar known at each bar (-1: none yet)
    for p in cs.degree_pivots(frame, degree, s):
        if p.kind == kind and p.confirmed_idx is not None:
            origin[int(p.confirmed_idx):] = int(p.bar_idx)
    bars = ls.prep_bars(frame, s)
    o, h, l, c, mr = bars["o"], bars["h"], bars["l"], bars["c"], bars["mr"]
    strength, distance = np.full(len(f), np.nan), np.full(len(f), np.nan)
    for t in np.flatnonzero(origin >= 0):
        mr_t = mr[t] if np.isfinite(mr[t]) and mr[t] > 0 else float(np.nanmedian(h[:t + 1] - l[:t + 1]))
        if not (np.isfinite(mr_t) and mr_t > 0):                           # nothing to measure in: missing, not a crash
            continue
        b = int(origin[t])
        if side == "low":
            zone = {"low": float(l[b]), "high": float(max(l[b], min(o[b], c[b]))), "kind": "SUPPORT"}
        else:
            zone = {"low": float(min(h[b], max(o[b], c[b]))), "high": float(h[b]), "kind": "RESISTANCE"}
        zone["formed_at"] = stamps[b]
        feats = ls.strength_features(zone, frame, int(t), symbol="DEFAULT", settings=s, bars=bars)
        strength[t], distance[t] = ls.strength_score(feats), feats["dist_mr"]
    return strength, distance


def _reversal_series(f: pd.DataFrame, level: pd.Series, direction: str) -> pd.Series:
    """S5-A2: on each bar, did price logically reverse at that bar's `level` (app/price_action/reversal.py, composite
    mode, default settings; another reversal_mode is refused)? The same answer `evaluate_reversal` gives on the candles up to that bar - the window always
    ends on the bar, so it is causal. A bar whose last few candles never reached the level cannot pass the touch test,
    so only bars that did are evaluated (a cheap filter; the result is the same)."""
    from app.price_action import pa_settings
    from app.price_action import reversal as rv
    from app.price_action.breaks import median_range
    if direction not in ("bullish", "bearish"):
        raise ScreenRuntimeError(f"unknown reversal direction {direction!r}")
    s = pa_settings.settings()
    if s["reversal_mode"] != "composite":                    # this series is the composite rule only (rv.evaluate)
        raise ScreenRuntimeError(f"ReversalAt supports the composite reversal rule, not {s['reversal_mode']!r}")
    dirn = 1 if direction == "bullish" else -1
    frame = pd.DataFrame({k: f[k].astype(float).to_numpy() for k in ("open", "high", "low", "close")})
    mr = median_range(frame, s["median_range_n"])
    bars = rv.Bars(frame, mr)
    lv = level.to_numpy(float) if isinstance(level, pd.Series) else np.full(len(f), float(level))
    tol = s["touch_tol_mr"] * np.where(np.isfinite(mr), mr, 0.0)
    # the longest window evaluate() reads: the composite (1..N) plus the legacy follow-through candle; the addendum mode
    # also reads composites ending up to followthrough_max_bars before the bar
    reach_n = s["touch_reclaim_window"] + 1
    if s.get("followthrough_mode", "legacy") != "legacy":
        reach_n += int(s.get("followthrough_max_bars", 1))
    if dirn > 0:
        reached = frame["low"].rolling(reach_n, min_periods=1).min().to_numpy() <= lv + tol
    else:
        reached = frame["high"].rolling(reach_n, min_periods=1).max().to_numpy() >= lv - tol
    out = np.zeros(len(f), dtype=bool)
    for j in np.flatnonzero(reached & np.isfinite(lv)):
        out[j] = bool(rv.evaluate(bars, int(j), dirn, [float(lv[j])], float(tol[j]), s)["ok"])
    return pd.Series(out, index=f.index)


def _real_break_series(f: pd.DataFrame, level: pd.Series, side: str, n_: int) -> pd.Series:
    """S5-A3: on each bar j, was a REAL break of the level (its value at j) confirmed within bars j-n+1..j
    (app/price_action/breaks.first_real_break, default settings, with the failed-retest confirmation)? It reads only bars
    up to j (`end=j` - the rule and the retest check stop there): a break still waiting for its acceptance bars is not
    counted yet. An event in the window, like the other windowed filters: a break confirmed and later reclaimed still
    reads true until it leaves the window (AND it with the close for "still beyond")."""
    from app.price_action import pa_settings
    from app.price_action.breaks import first_real_break, median_range
    from app.price_action.reversal import retest_fn
    if side not in ("above", "below"):
        raise ScreenRuntimeError(f"unknown break side {side!r}")
    s = pa_settings.settings()
    frame = pd.DataFrame({k: f[k].astype(float).to_numpy() for k in ("open", "high", "low", "close")})
    mr = median_range(frame, s["median_range_n"])
    retest = retest_fn(frame, s, mr) if s.get("break_retest_confirm") else None
    lv = level.to_numpy(float) if isinstance(level, pd.Series) else np.full(len(f), float(level))
    close = frame["close"]
    buffer = s["break_buffer_mr"] * pd.Series(mr)
    n_ = max(1, n_)
    out = np.zeros(len(f), dtype=bool)
    # A real break starts with a close beyond THIS bar's level by the buffer somewhere in the window, so a window whose
    # most extreme (close -/+ buffer) never got there cannot hold one; only the others are scanned (same result). The
    # rule tests close < level - buffer, which can round differently from close + buffer < level on tick prices, so the
    # filter is loosened by a relative epsilon: it may let an extra bar through (the rule then decides), never drop one.
    eps = 1e-9 * np.maximum(1.0, np.abs(lv))
    if side == "below":
        reach = (close + buffer).rolling(n_, min_periods=1).min().to_numpy() < lv + eps
    else:
        reach = (close - buffer).rolling(n_, min_periods=1).max().to_numpy() > lv - eps
    closes = close.to_numpy(float)
    for j in np.flatnonzero(reach & np.isfinite(lv)):
        start = break_scan_start(closes, int(j), float(lv[j]), side, n_)
        if start is None:
            continue
        out[j] = first_real_break(frame, start, float(lv[j]), side, s, mr=mr, end=int(j), retest_fn=retest) is not None
    return pd.Series(out, index=f.index)


def break_scan_start(closes: np.ndarray, j: int, level: float, side: str, n_: int) -> Optional[int]:
    """A break is a CROSSING: price comes from the near side of the level. The scan starts after the FIRST bar of
    j-n..j that closed on the near side, so every break inside the window that crossed from the near side is found;
    None when price stayed beyond the level all window (an older break, still held, is not a new one)."""
    lo = max(0, j - n_)
    near = closes[lo:j + 1] >= level if side == "below" else closes[lo:j + 1] <= level
    hit = np.flatnonzero(near)
    return None if hit.size == 0 else lo + int(hit[0]) + 1


def _breakout_params(overrides: Dict[str, Any]) -> Dict[str, Any]:
    from app.screener.breakouts import params as bp
    try:
        return bp.settings(**overrides)
    except ValueError as exc:
        raise ScreenRuntimeError(str(exc)) from exc


def _breakout(f: pd.DataFrame, screen: str, side: str, overrides: Dict[str, Any], what: str = "signal", tf: Optional[str] = None,
              bars: Any = None) -> pd.Series:
    """U6 BK1: one breakout screen's per-leg signal on each bar (or, `type`, BOS / CHOCH on B2 signal bars), from
    app/screener/breakouts/screens.py with every other parameter at its default (closed bars, causal). `tf`: the bars'
    timeframe; `bars`: the BK2 intraday screens' cached Bars on the symbol's exchange clock (_Eval.session_bars)."""
    from app.screener.breakouts.screens import evaluate_screen
    if side not in ("up", "down"):
        raise ScreenRuntimeError(f"unknown breakout side {side!r}")
    p = _breakout_params(overrides)
    try:
        out = evaluate_screen(f, screen, p, sides=(side,), tf=tf, bars=bars)[side]
    except ValueError as exc:
        raise ScreenRuntimeError(str(exc)) from exc
    if what == "type":
        return out["event"].where(out["signal"], "").astype(object)
    return out["signal"].astype(bool)


def _rel_volume(f: pd.DataFrame, n_: int) -> pd.Series:
    from app.screener.breakouts.screens import Bars
    return pd.Series(Bars(f, _breakout_params({"vol_avg_n": n_})).rvol, index=f.index)


def _atr_percentile(f: pd.DataFrame, atr_n: int, lookback: int) -> pd.Series:
    from app.screener.breakouts.quality import atr_percentile_series
    from app.screener.breakouts.screens import Bars
    b = Bars(f, _breakout_params({"atr_n": atr_n}))
    return pd.Series(atr_percentile_series(b.atr_prev, b.c, lookback), index=f.index, dtype=float)


def _dow_trend(f: pd.DataFrame, degree: int) -> pd.Series:
    from app.screener.breakouts.screens import Bars
    try:
        return pd.Series(Bars(f, _breakout_params({})).dow(degree), index=f.index, dtype=object)
    except ValueError as exc:
        raise ScreenRuntimeError(str(exc)) from exc
def _zone(data: SymbolData, f: pd.DataFrame, tf: str, what: str, role: str, tolerance_pct: float, family: str) -> pd.Series:
    from app.screener.zones import zone_series
    return zone_series(data, f, tf, what, role, tolerance_pct, family)


def _ownership(data: SymbolData, f: pd.DataFrame, tf: str, what: str, arg: str, quarters: int = 0) -> pd.Series:
    from app.screener import ownership
    if what == "event":
        return ownership.days_to_event_series(data, f, tf, arg)
    return ownership.holding_series(data, f, tf, arg, quarters)


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


def bool_frame(v: pd.DataFrame) -> bool:
    """Every column of `v` is boolean (the vectorised path's frames hold one dtype)."""
    return all(pd.api.types.is_bool_dtype(t) for t in set(v.dtypes))


def _as_bool(v: Any, index: Optional[pd.Index] = None) -> Any:
    if isinstance(v, pd.Series):
        return v.fillna(False).astype(bool) if v.dtype != bool else v
    if isinstance(v, pd.DataFrame):                               # the vectorised path (app/screener/panel.py)
        return v if bool_frame(v) else v.fillna(False).astype(bool)
    if index is not None:
        return pd.Series(bool(v), index=index)
    return bool(v)


def _cmp(op: str, a: Any, b: Any) -> Any:
    if a is None or b is None:
        return False
    if isinstance(a, _FRAMES) or isinstance(b, _FRAMES):
        ops = {">": "gt", ">=": "ge", "<": "lt", "<=": "le", "==": "eq", "!=": "ne"}
        left, right = (a, b) if isinstance(a, _FRAMES) else (b, a)
        if not isinstance(a, _FRAMES):
            ops = {">": "lt", ">=": "le", "<": "gt", "<=": "ge", "==": "eq", "!=": "ne"}
        out = getattr(left, ops[op])(right)
        if isinstance(left, pd.DataFrame) and op != "!=":         # vectorised path (float frames): NaN already compares False
            return out
        valid = left.notna() & (right.notna() if isinstance(right, _FRAMES) else (right == right))
        return (out & valid).astype(bool)
    if isinstance(a, float) and np.isnan(a) or isinstance(b, float) and np.isnan(b):
        return False
    return {">": a > b, ">=": a >= b, "<": a < b, "<=": a <= b, "==": a == b, "!=": a != b}[op]


def _and(a: Any, b: Any) -> Any:
    if isinstance(a, _FRAMES) or isinstance(b, _FRAMES):
        return _as_bool(a) & _as_bool(b) if isinstance(a, _FRAMES) else _as_bool(b) & bool(a)
    return bool(a) and bool(b)


def _or(a: Any, b: Any) -> Any:
    if isinstance(a, _FRAMES) or isinstance(b, _FRAMES):
        return _as_bool(a) | _as_bool(b) if isinstance(a, _FRAMES) else _as_bool(b) | bool(a)
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


def stage_survivors(ast: Any, validated: Validated, universe: List[SymbolData], *, base_tf: str,
                    params: Optional[Dict[str, Any]] = None, matches: Optional[List[Match]] = None, vectorized: bool = False) -> Dict[str, Any]:
    """U5: the screen as a funnel. A top-level ALL is a list of stages (anything else is one stage).

    Each stage is evaluated ONCE over the whole universe (so cross-sectional functions - Rank, PercentileRank - see the
    same universe as in the full screen) and the results are ANDed cumulatively: the count after a stage is the
    symbols that passed it and every stage before it, and the last count equals the screen's match count.
    Symbols the screen cannot evaluate at all (not enough history, a runtime problem) are left out of the funnel
    (`with_data`) instead of being "removed" by the first stage.

    U5 D2: `passes` gives, per evaluated symbol, whether it passed each stage on its own (not only while it was still
    in) - the "why it matched / where it fell out" chips on a result row."""
    full = {m.symbol: m for m in (matches if matches is not None else run_screen(ast, validated, universe, base_tf=base_tf, params=params,
                                                                                vectorized=vectorized))}
    usable = [d for d in universe if full[d.symbol].reason is None]
    items = list(ast.items) if isinstance(ast, n.Logic) and ast.op == "ALL" else [ast]
    alive = [d.symbol for d in usable]
    stages: List[Dict[str, Any]] = []
    passes: Dict[str, List[bool]] = {d.symbol: [] for d in usable}
    for item in items:
        passed = {m.symbol for m in run_screen(item, validated, usable, base_tf=base_tf, params=params, vectorized=vectorized) if m.matched}
        removed = [s for s in alive if s not in passed]
        alive = [s for s in alive if s in passed]
        stages.append({"text": n.to_text(item), "survivors": len(alive), "removed": removed})
        for symbol, row in passes.items():
            row.append(symbol in passed)
    return {"with_data": len(usable), "stages": stages, "passes": passes}


def _last(value: Any) -> Any:
    if isinstance(value, pd.Series):
        return value.iloc[-1] if len(value) else np.nan
    return value


def _cross_sectional(ast: Any, universe: List[SymbolData], base_tf: str, params: Dict[str, Any]) -> Dict[str, Dict[int, float]]:
    """Rank / PercentileRank over the universe's last bars, innermost first."""
    constants: Dict[str, Dict[int, float]] = {d.symbol: {} for d in universe}
    cs = [node for node in n.walk(ast) if isinstance(node, n.Call) and FUNCTIONS.get(node.name) and FUNCTIONS[node.name].cross_sectional]
    for node in reversed(cs):                                       # pre-order reversed: inner calls first
        if node.name == "RankScore":
            for sym, r in _rank_score(node, universe, base_tf, params).items():
                constants[sym][id(node)] = r
            continue
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


def _rank_score(node: Any, universe: List[SymbolData], base_tf: str, params: Dict[str, Any]) -> Dict[str, float]:
    """N3 S7a: RankScore("name") - the system named (from the run's `ranking.ACTIVE`, set by the route that loaded it)
    scored over the universe's last bars. A leaf is NaN for a symbol with fewer bars than the leaf's lookback (the S6
    history read does the same, so a live run and a history date agree). An unknown system: NaN (never matches; the
    routes refuse it before a run)."""
    from app.screener import ranking as RK
    system = RK.ACTIVE.get().get(RK.system_name(node.args[0], params) or "")
    if system is None:
        return {d.symbol: float("nan") for d in universe}
    sectors = {d.symbol: d.sector for d in universe} if system.sector_neutral else None
    rank, _ = RK.score(system, RK.live_values(system, universe, base_tf), sectors)
    return {d.symbol: float(rank.get(d.symbol, np.nan)) for d in universe}


def run_screen(ast: Any, validated: Validated, universe: List[SymbolData], *, base_tf: str, params: Optional[Dict[str, Any]] = None,
               check_history: bool = True, vectorized: bool = False) -> List[Match]:
    """Every symbol: does the screen hold on its last closed bar? Runs only a validated screen. With `check_history` a symbol
    shorter than the validator's lookback is reported as such; without it, it is simply evaluated (NaN never matches).
    Z10-d: `vectorized` evaluates the symbols that share a bar index together (app/screener/panel.py) when every part of
    the screen has a vectorised form; the matches are the same, only the time changes."""
    if not validated.ok:
        raise ScreenRuntimeError("the screen did not pass validation")
    params = dict(params or {})
    if vectorized:
        from app.screener import panel
        if panel.supported(ast, validated, base_tf):
            return panel.run_panel(ast, validated, universe, base_tf=base_tf, params=params, check_history=check_history,
                                   per_symbol=lambda rest: run_screen(ast, validated, rest, base_tf=base_tf, params=params, check_history=check_history))
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
        except Exception as exc:  # noqa: BLE001 - S5-A4 review: one symbol's bad data must not abort the whole universe
            logger.exception("Screen evaluation failed for %s", d.symbol)
            out.append(Match(d.symbol, False, f"could not be evaluated ({type(exc).__name__})"))
            continue
        out.append(Match(d.symbol, bool(value) if not (isinstance(value, float) and np.isnan(value)) else False))
    return out


def evaluate(ast: Any, data: SymbolData, *, base_tf: str, params: Optional[Dict[str, Any]] = None) -> Any:
    """The full series (or constant) of an expression for one symbol - for tests, charts and the builder preview."""
    return _Eval(data, base_tf, dict(params or {}), {}).ev(ast, base_tf)


__all__ = ["SymbolData", "Match", "run_screen", "evaluate", "resample", "closed_only", "ScreenRuntimeError"]
