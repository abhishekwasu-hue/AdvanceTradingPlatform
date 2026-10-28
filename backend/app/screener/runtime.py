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
from app.indicators.momentum import stochastic as _stochastic
from app.indicators.trend import ema as _ema
from app.indicators.trend import hma as _hma
from app.indicators.trend import macd as _macd
from app.indicators.trend import parabolic_sar as _psar
from app.indicators.trend import wma as _wma
from app.indicators.volatility import atr as _atr
from app.indicators.volatility import supertrend_dir as _supertrend_dir
from app.screener import nodes as n
from app.screener import reasons
from app.screener.registry import FUNCTIONS, RETEST_WAVE_OF
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
    fundamentals: Optional[Any] = None                     # U7 LG3: app.fundamentals.pit versions (point in time) for the fundamentals functions


@dataclass
class Match:
    symbol: str
    matched: bool
    reason: Optional[str] = None                           # why a symbol could not be evaluated (history too short...)


# SCR-FIX A5: the NSE-family session close (09:15-15:30 IST). A 30m / 1h session is not a whole number of buckets, so
# the day's last bucket (15:15) ends at the close, not 30 / 60 minutes after it starts - as on Charts and every broker.
_SESSION_CLOSE_IST = pd.Timedelta(hours=15, minutes=30)


def _session_capped(tf: str, exchange: Optional[str]) -> bool:
    """True for an intraday timeframe whose buckets do not tile the NSE-family session, on that session's clock (an
    exchange of None is NSE, as SymbolData says); another venue's session (MCX, CDS) keeps whole buckets."""
    minutes = n.TF_MINUTES.get(tf, 0)
    if not minutes or minutes >= n.TF_MINUTES["1d"] or n.TF_MINUTES["1d"] % minutes == 0:
        return False
    if exchange is None:
        return True
    from app.market_data.calendar import session_family
    return session_family(exchange) == "NSE"


def bar_close(index: pd.DatetimeIndex, tf: str, exchange: Optional[str] = None) -> pd.DatetimeIndex:
    """When a bar that starts at `index` has closed. Months are counted in exchange time (BK1 verification B): a month
    labelled 1 Aug 00:00 IST is 31 Jul 18:30 UTC, and one calendar month on from the UTC stamp clipped to 30 Aug -
    a day before the month's last session - so the month was visible early. SCR-FIX A5: a 30m / 1h bar that starts
    before the session close and would end after it closes at the session close (`_SESSION_CLOSE_IST`)."""
    if tf == "1M" and getattr(index, "tz", None) is not None:
        return (index.tz_convert(EXCHANGE_TZ) + _DURATION[tf]).tz_convert(index.tz)
    end = index + _DURATION[tf]
    if len(index) and _session_capped(tf, exchange):
        tz = getattr(index, "tz", None)
        local = (index.tz_localize("UTC") if tz is None else index).tz_convert(EXCHANGE_TZ)
        close = local.normalize() + _SESSION_CLOSE_IST
        close = close.tz_convert("UTC").tz_localize(None) if tz is None else close.tz_convert(tz)
        end = end.where(~((index < close) & (close < end)), close)
    return end


def closed_only(df: pd.DataFrame, tf: str, now: datetime, exchange: Optional[str] = None) -> pd.DataFrame:
    """Bars whose close time is not after `now` - a broker's intraday feed ends with the bar still forming, and a
    screen decides on closed bars only."""
    if df.empty:
        return df
    return df[bar_close(df.index, tf, exchange) <= pd.Timestamp(now)]


def session_origin(index: pd.DatetimeIndex) -> pd.Timestamp:
    """09:15 IST on the first bar's date, in the index's own time zone. U6 BK1 review (M5): normalising an IST-aware stamp
    gave IST midnight, so 03:45 later was 03:45 IST and 1h bars started at 09:45 / 2h at 11:45 (Fyers / Dhan / Kite
    frames are IST-aware). A naive index is read as UTC, as before."""
    from app.strategy_engine.declarative import _IST_OPEN_UTC
    first = index[0]
    if first.tzinfo is None:
        return first.normalize() + _IST_OPEN_UTC
    return (first.tz_convert("UTC").normalize() + _IST_OPEN_UTC).tz_convert(first.tzinfo)


def resample(base: pd.DataFrame, base_tf: str, tf: str, keep_forming: bool = False, exchange: Optional[str] = None) -> pd.DataFrame:
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
        last_base_close = bar_close(base.index[-1:], base_tf, exchange)[0]
        if bar_close(out.index[-1:], tf, exchange)[0] > last_base_close:
            out = out.iloc[:-1]
    return out


# LD-7: one screen run's evaluated nodes, per symbol (keyed by its SymbolData object: id(data) -> (id(node), timeframe) ->
# value). The funnel (`stage_survivors`) evaluates the screen's top-level items again on the same symbols; with the memo
# of the full evaluation they are read back, not computed again. Valid for one run only: the same screen objects, bars,
# parameters and base timeframe - and never for a cross-sectional screen (its constants depend on the universe given).
ScreenMemo = Dict[int, Dict[Tuple[int, str], Any]]
_LEAVES = (n.Num, n.Str, n.Bool, n.Param)
_MISSING = object()


class _Eval:
    memo: Optional[Dict[Tuple[int, str], Any]] = None          # a subclass that sets up its own state (panel.py) has none

    def __init__(self, data: SymbolData, base_tf: str, params: Dict[str, Any], constants: Dict[int, float],
                 memo: Optional[Dict[Tuple[int, str], Any]] = None) -> None:
        self.data, self.base_tf, self.params, self.constants = data, base_tf, params, constants
        if base_tf not in data.frames or data.frames[base_tf].empty:
            raise ScreenRuntimeError(f"{data.symbol}: no {base_tf} bars")
        self.frames: Dict[str, pd.DataFrame] = dict(data.frames)
        self._session_bars: Dict[Tuple[Any, ...], Any] = {}      # U6 BK2: one session map / VWAP / time-of-day series per frame
        # LD-7: this symbol's evaluated nodes, (id(node), timeframe) -> value, shared by the evaluations of one screen run
        # (`ScreenMemo`): the funnel's stages are the screen's own top-level items, already evaluated by the full screen.
        self.memo = memo

    def ev(self, node: Any, tf: str) -> Any:
        if self.memo is None or isinstance(node, _LEAVES):
            return self._ev(node, tf)
        key = (id(node), tf)
        value = self.memo.get(key, _MISSING)
        if value is _MISSING:
            value = self.memo[key] = self._ev(node, tf)
        return value

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

    def _gap_bars(self, tf: str) -> Any:
        """U6 BK4: B8 reads the exchange session on intraday bars (the BK2 session Bars); on Daily it needs none."""
        return self.session_bars(tf) if n.TF_MINUTES.get(tf, 0) < n.TF_MINUTES["1d"] else None

    def frame(self, tf: str) -> pd.DataFrame:
        if tf not in self.frames:
            src = self.base_tf
            # U6 BK1 verification (A): weekly / monthly from a given daily frame (the deeper history) on an intraday screen
            if "1d" in self.frames and n.TF_MINUTES[self.base_tf] < n.TF_MINUTES["1d"] < n.TF_MINUTES[tf]:
                src = "1d"
            self.frames[tf] = resample(self.frames[src], src, tf, exchange=getattr(self.data, "exchange", None))
        return self.frames[tf]

    def align(self, value: Any, src_tf: str, dst_tf: str) -> Any:
        """A src_tf series onto dst_tf bars by close time (look-ahead safe)."""
        if not isinstance(value, pd.Series) or src_tf == dst_tf:
            return value
        avail = pd.Series(value.to_numpy(), index=bar_close(value.index, src_tf, getattr(self.data, "exchange", None)))
        avail = avail[~avail.index.duplicated(keep="last")].sort_index()
        dst_close = bar_close(self.frame(dst_tf).index, dst_tf, getattr(self.data, "exchange", None))
        with pd.option_context("future.no_silent_downcasting", True):       # a category series stays object (no dtype guess)
            out = avail.reindex(avail.index.union(dst_close)).ffill().reindex(dst_close)
        return pd.Series(out.to_numpy(), index=self.frame(dst_tf).index)

    def _ev(self, node: Any, tf: str) -> Any:  # noqa: C901 - one switch over the node kinds
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

        def kw(name: str) -> Optional[Any]:
            """A named argument's value (None when it is left out): one value for the whole series, as `num` / `_text`."""
            node_ = node.kwargs.get(name)
            return None if node_ is None else _last(self.ev(node_, tf))

        def retest_forward(of: str, k_at: Optional[int]) -> Dict[str, Any]:
            """Strategy templates R7: the base breakout's own settings given to RetestEntry / RetestEntryRef as named
            arguments (k= also positional at `k_at`); each one left out keeps the breakout parameter's default, exactly as
            before R7."""
            out: Dict[str, Any] = {}
            k = kw("k")
            if k is None and k_at is not None and len(args) > k_at:
                k = num(k_at, _bkd("min_break_atr"))
            if k is not None:
                out["min_break_atr"] = float(k)
            given = {name: kw(name) for name in ("n", "v", "mode", "touches", "swing")}
            if given["n"] is not None and of in ("range", "donchian"):
                out["range_n" if of == "range" else "donchian_n"] = int(given["n"])
            if of == "range":
                if given["v"] is not None:
                    out["vol_mult"] = float(given["v"])
                if given["mode"] is not None:
                    out["range_mode"] = str(given["mode"])
            if given["touches"] is not None and of in ("trendline", "channel", "trendline_projected"):
                out["tl_min_touches"] = float(given["touches"])
            if given["swing"] is not None and of == "swing":      # step 5 (C2): the swing breakout's own swing degree
                out["swing_degree"] = int(given["swing"])
            return out

        def retest_entry() -> pd.Series:
            side, of = str(self.ev(args[0], tf)), _text(1, _bkd("retest_of"))
            if of not in RETEST_WAVE_OF:                     # range / swing / donchian / trendline / channel / projected: the B9 path
                return _breakout(f, "B9", side, {"retest_of": of, "retest_max_bars": num(2, _bkd("retest_max_bars")),
                                                 "retest_reversal": _bool(3, _bkd("retest_reversal")), **retest_forward(of, 7)})
            # R7 (wave templates): the retest of a broken wave line / wave end (app/screener/breakouts/wave_breaks.py)
            return _wave_retest(f, side, of, int(num(2, _bkd("retest_max_bars"))), _bool(3, _bkd("retest_reversal")), int(num(4, 1)),
                                _text(5, "channel_B"), _text(6, "A"), num(7, _bkd("min_break_atr")))

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
            # TPL-1: the add-indicator work (app/indicators) - the Supertrend's own direction, MACD, WMA / HMA, the
            # stochastic and the parabolic SAR, all bar by bar on closed bars
            "SupertrendDir": lambda: _supertrend_dir(f, max(1, win(0, 10)), num(1, 3.0)),
            "MACD": lambda: _macd(f["close"].astype(float), win(0, 12), win(1, 26), win(2, 9))["macd"],
            "MACDSignal": lambda: _macd(f["close"].astype(float), win(0, 12), win(1, 26), win(2, 9))["signal"],
            "MACDHist": lambda: _macd(f["close"].astype(float), win(0, 12), win(1, 26), win(2, 9))["hist"],
            "WMA": lambda: _wma(self.series(args[0], tf), win(1)),
            "HMA": lambda: _hma(self.series(args[0], tf), win(1)),
            "MA": lambda: _moving_average(self.series(args[0], tf), win(1), _text(2, "EMA")),
            "StochK": lambda: _stochastic(f, win(0, 14), win(1, 3), 1)["k"],
            "StochD": lambda: _stochastic(f, win(0, 14), win(1, 3), win(2, 3))["d"],
            "PSAR": lambda: _psar(f, num(0, 0.02), num(1, 0.02), num(2, 0.2))["sar"],
            "PSARDir": lambda: _psar(f, num(0, 0.02), num(1, 0.02), num(2, 0.2))["dir"],
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
            "PatternBullish": lambda: _pattern(f, "BULLISH", _text(0, "any")),
            "PatternBearish": lambda: _pattern(f, "BEARISH", _text(0, "any")),
            "NearSupport": lambda: _near_zone(f, "NEAR_SUPPORT", num(0, 0.5), win(1, 3)),
            "NearResistance": lambda: _near_zone(f, "NEAR_RESISTANCE", num(0, 0.5), win(1, 3)),
            "Pattern": lambda: _pattern_series(f, str(self.ev(args[0], tf)), _text(1, "any")),
            # AMW-3 (flag screenql_amw_functions): the AMW screener's predicates as causal series
            "StDev": lambda: self.series(args[0], tf).rolling(win(1)).std(ddof=0),
            "RsiDivergence": lambda: _rsi_divergence(f, _text(0, "bullish"), win(1, 15), int(num(2, 0)), int(kw("period") or RSI_DIVERGENCE_PERIOD),
                                                     str(kw("kind") or RSI_DIVERGENCE_KIND)),
            "RsiDivergenceRef": lambda: _rsi_divergence_ref(f, _text(0, "extreme"), _text(1, "bullish"), int(num(2, 0)),
                                                            int(kw("period") or RSI_DIVERGENCE_PERIOD), str(kw("kind") or RSI_DIVERGENCE_KIND)),
            "SwingLabel": lambda: _swing_label(f, _text(0, "high"), int(num(1, 0))),
            "TimeOfDay": lambda: pd.Series(time_of_day(f.index, _text(0, ""), _text(1, "")), index=f.index),
            # Wave templates W1 (flag screener_waves): the causal wave count (app/price_action/waves), close-only in alerts
            "WaveState": lambda: _wave(f, int(num(0, 1)), "state"),
            "WaveType": lambda: _wave(f, int(num(0, 1)), "type"),
            "WaveAlt": lambda: _wave(f, int(num(0, 1)), "alt"),
            "WaveClarity": lambda: _wave(f, int(num(0, 1)), "clarity"),
            # Wave templates W2 / W3 / W4 (same flag): the main count's levels, ratios and anchored lines (NaN off their waves)
            "WaveOrigin": lambda: _wave_level(f, int(num(0, 1)), "origin"),
            "WaveEnd": lambda: _wave_level(f, int(num(0, 1)), "end", _text(1, "current")),
            "WaveInvalidation": lambda: _wave_level(f, int(num(0, 1)), "invalidation"),
            "WaveCap": lambda: _wave_level(f, int(num(0, 1)), "cap"),
            "WaveBeyondOrigin": lambda: _wave_level(f, int(num(0, 1)), "beyond_origin"),
            "WaveRetrace": lambda: _wave_level(f, int(num(0, 1)), "retrace", num(1, float("nan"))),
            "WaveProjection": lambda: _wave_level(f, int(num(0, 1)), "projection", _text(1, "previous"), num(2, float("nan"))),
            "WaveRatio": lambda: _wave_level(f, int(num(0, 1)), "ratio", _text(1, "previous")),
            "WaveLineLevel": lambda: _wave_level(f, int(num(0, 1)), "line", _text(1, "")),
            # Wave templates companions (same flag): the corrective wave's volume against the wave before it
            "WaveVolumeLower": lambda: _wave_volume_lower(f, int(num(0, 1)), _text(1, "current"), _text(2, "previous")),
            "WaveLineBreak": lambda: _wave_line_break(f, int(num(0, 1)), _text(1, ""), _text(2, "up"), num(3, _bkd("min_break_atr"))),
            "SwingHigh": lambda: _swings(f, int(num(0, 0)))[0],
            "SwingLow": lambda: _swings(f, int(num(0, 0)))[1],
            "SwingDirection": lambda: _swings(f, int(num(0, 0)))[2],
            # idea library IDEA-14: the confirmed swing high / low `back` swings before the latest one (head and shoulders)
            "PriorSwingHigh": lambda: _prior_swing(f, int(num(0, 0)), _text(1, "one"), "H"),
            "PriorSwingLow": lambda: _prior_swing(f, int(num(0, 0)), _text(1, "one"), "L"),
            "MedianRange": lambda: _median_range(f, win(0, 20)),
            "SwingZoneStrength": lambda: _swing_zone(f, str(self.ev(args[0], tf)), int(num(1, 0)), "strength"),
            "SwingZoneDistance": lambda: _swing_zone(f, str(self.ev(args[0], tf)), int(num(1, 0)), "distance"),
            "SwingZoneEdge": lambda: _swing_zone(f, str(self.ev(args[0], tf)), int(num(1, 0)), "edge", num(2, 0.0)),
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
            # Strategy templates step 2 (owner order R2): the level companions of RangeBreakout / TrendlineBreakout
            "RangeBreakoutRef": lambda: _breakout_ref(f, "B1", _text(0, "level"), str(self.ev(args[1], tf)), {
                "range_n": win(2, _bkd("range_n")), "min_break_atr": num(3, _bkd("min_break_atr")), "vol_mult": num(4, _bkd("vol_mult")),
                "range_mode": _text(5, _bkd("range_mode"))}, num(6, _bkd("stop_atr_mult"))),
            "LineBreakoutRef": lambda: _breakout_ref(f, "B5", _text(0, "level"), str(self.ev(args[1], tf)), {
                "tl_mode": _text(2, _bkd("tl_mode")), "tl_min_touches": num(3, _bkd("tl_min_touches")),
                "min_break_atr": num(4, _bkd("min_break_atr"))}, num(5, _bkd("stop_atr_mult"))),
            # Strategy templates step 4 (R2 for the other breakout families): the same _breakout_ref on each family's screen
            "SwingBreakoutRef": lambda: _breakout_ref(f, "B2", _text(0, "level"), str(self.ev(args[1], tf)), {
                "swing_degree": int(num(2, _bkd("swing_degree"))), "min_break_atr": num(3, _bkd("min_break_atr"))}, num(4, _bkd("stop_atr_mult"))),
            "NRBreakoutRef": lambda: _breakout_ref(f, "B4", _text(0, "level"), str(self.ev(args[1], tf)), {
                "nr_setup": _text(2, _bkd("nr_setup")), "vol_mult": num(3, _bkd("vol_mult")), "min_break_atr": num(4, _bkd("min_break_atr"))},
                num(5, _bkd("stop_atr_mult"))),
            "VolumeBreakoutRef": lambda: _breakout_ref(f, "B14", _text(0, "level"), str(self.ev(args[1], tf)), {
                "shock_mult": num(2, _bkd("shock_mult")), "range_n": win(3, _bkd("range_n"))}, num(4, _bkd("stop_atr_mult"))),
            "DonchianBreakoutRef": lambda: _breakout_ref(f, "B10", _text(0, "level"), str(self.ev(args[1], tf)), {
                "donchian_n": win(2, _bkd("donchian_n")), "min_break_atr": num(3, _bkd("min_break_atr"))}, num(4, _bkd("stop_atr_mult"))),
            "MABreakoutRef": lambda: _breakout_ref(f, "B12", _text(0, "level"), str(self.ev(args[1], tf)), {
                "ma_n": win(2, _bkd("ma_n")), "ma_type": _text(3, _bkd("ma_type")), "min_break_atr": num(4, _bkd("min_break_atr"))},
                num(5, _bkd("stop_atr_mult"))),
            "PatternBreakoutRef": lambda: _breakout_ref(f, "B6", _text(0, "level"), str(self.ev(args[1], tf)), {
                "pattern": _text(2, _bkd("pattern")), "vol_mult": num(3, _bkd("vol_mult")), "min_break_atr": num(4, _bkd("min_break_atr"))},
                num(5, _bkd("stop_atr_mult"))),
            "VCPBreakoutRef": lambda: _breakout_ref(f, "B3", _text(0, "level"), str(self.ev(args[1], tf)), {
                "vcp_min_contractions": num(2, _bkd("vcp_min_contractions")), "vcp_ratio": num(3, _bkd("vcp_ratio")),
                "vol_mult": num(4, _bkd("vol_mult")), "min_break_atr": num(5, _bkd("min_break_atr")), "tt_filter": _bool(6, _bkd("tt_filter")),
                "vcp_vol_dryup": _bool(7, _bkd("vcp_vol_dryup"))}, num(8, _bkd("stop_atr_mult"))),
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
            # U6 BK3: VCP and the indicator breakouts (app/screener/breakouts/vcp.py, indicator_breaks.py); defaults = the parameters'
            "VCPBreakout": lambda: _breakout(f, "B3", str(self.ev(args[0], tf)), {
                "vcp_min_contractions": num(1, _bkd("vcp_min_contractions")), "vcp_ratio": num(2, _bkd("vcp_ratio")),
                "vol_mult": num(3, _bkd("vol_mult")), "min_break_atr": num(4, _bkd("min_break_atr")), "tt_filter": _bool(5, _bkd("tt_filter")),
                "vcp_vol_dryup": _bool(6, _bkd("vcp_vol_dryup"))}),
            "VCPReady": lambda: _breakout(f, "B3", str(self.ev(args[0], tf)), {
                "vcp_mode": "watchlist", "vcp_min_contractions": num(1, _bkd("vcp_min_contractions")), "vcp_ratio": num(2, _bkd("vcp_ratio")),
                "vcp_ready_pct": num(3, _bkd("vcp_ready_pct")), "tt_filter": _bool(4, _bkd("tt_filter")),
                "vcp_vol_dryup": _bool(5, _bkd("vcp_vol_dryup"))}),
            "DonchianBreakout": lambda: _breakout(f, "B10", str(self.ev(args[0], tf)), {
                "donchian_n": win(1, _bkd("donchian_n")), "min_break_atr": num(2, _bkd("min_break_atr"))}),
            "SqueezeBreakout": lambda: _breakout(f, "B11", str(self.ev(args[0], tf)), {
                "squeeze_mode": _text(1, _bkd("squeeze_mode")), "bb_n": win(2, _bkd("bb_n")), "squeeze_lookback": win(3, _bkd("squeeze_lookback")),
                "vol_mult": num(4, _bkd("vol_mult"))}),
            "MABreakout": lambda: _breakout(f, "B12", str(self.ev(args[0], tf)), {
                "ma_n": win(1, _bkd("ma_n")), "ma_type": _text(2, _bkd("ma_type")), "min_break_atr": num(3, _bkd("min_break_atr"))}),
            "RegimeBreakout": lambda: _breakout(f, "B15", str(self.ev(args[0], tf)), {
                "rsi_band": num(1, _bkd("rsi_band")), "regime_n": win(2, _bkd("regime_n")), "adx_low": num(3, _bkd("adx_low")),
                "min_break_atr": num(4, _bkd("min_break_atr"))}),
            # U6 BK4: trendline / pattern / gap / retest breakouts (app/screener/breakouts/lines.py, gaps.py, retest.py)
            "TrendlineBreakout": lambda: _breakout(f, "B5", str(self.ev(args[0], tf)), {
                "tl_mode": _text(1, _bkd("tl_mode")), "tl_min_touches": num(2, _bkd("tl_min_touches")), "min_break_atr": num(3, _bkd("min_break_atr"))}),
            # idea library IDEA-12: the B5 channel's trend-side line on every bar (app/screener/breakouts/lines.channel_line)
            "ChannelLine": lambda: _channel_line(f, str(self.ev(args[0], tf)), num(1, _bkd("tl_min_touches")), int(num(2, 0))),
            "PatternBreakout": lambda: _breakout(f, "B6", str(self.ev(args[0], tf)), {
                "pattern": _text(1, _bkd("pattern")), "vol_mult": num(2, _bkd("vol_mult")), "min_break_atr": num(3, _bkd("min_break_atr"))}),
            "GapBreakout": lambda: _breakout(f, "B8", str(self.ev(args[0], tf)), {
                "gap_level": _text(1, _bkd("gap_level")), "gap_hold_min": num(2, _bkd("gap_hold_min"))}, tf=tf, bars=self._gap_bars(tf)),
            "GapType": lambda: _breakout(f, "B8", str(self.ev(args[0], tf)), {
                "gap_level": _text(1, _bkd("gap_level")), "gap_hold_min": num(2, _bkd("gap_hold_min"))}, "class", tf=tf, bars=self._gap_bars(tf)),
            # Strategy templates TPL-8 (R2 for the gap family): the same B8 gaps on the same session Bars as GapBreakout
            "GapBreakoutRef": lambda: _breakout_ref(f, "B8", _text(0, "level"), str(self.ev(args[1], tf)), {
                "gap_level": _text(2, _bkd("gap_level")), "gap_hold_min": num(3, _bkd("gap_hold_min"))}, num(4, _bkd("stop_atr_mult")),
                tf=tf, bars=self._gap_bars(tf)),
            "RetestEntry": lambda: retest_entry(),
            # Strategy templates R7 / R2: the retest's prices (the same B9 retests as RetestEntry with the same settings)
            "RetestEntryRef": lambda: _retest_ref(f, _text(0, "level"), str(self.ev(args[1], tf)), {
                "retest_of": _text(2, _bkd("retest_of")), "retest_max_bars": num(3, _bkd("retest_max_bars")),
                "retest_reversal": _bool(4, _bkd("retest_reversal")), **retest_forward(_text(2, _bkd("retest_of")), None)},
                num(5, _bkd("stop_atr_mult"))),
            # Z7a-3: market zones - the stored snapshot as of each bar's close (app/screener/zones.py)
            "NearZone": lambda: _zone(self.data, f, tf, "near", _text(0, "any"), num(1, 0.5), _text(2, "all")),
            "ZoneRole": lambda: _zone(self.data, f, tf, "role", "any", num(0, 0.5), _text(1, "all")),
            "ZoneState": lambda: _zone(self.data, f, tf, "state", _text(0, "any"), num(1, 0.5), _text(2, "all")),
            "ZoneStrength": lambda: _zone(self.data, f, tf, "strength", _text(0, "any"), num(1, 0.5), _text(2, "all")),
            # N3 S8a: ownership and events, the rows known by each bar's close (app/screener/ownership.py)
            "Holding": lambda: _ownership(self.data, f, tf, "holding", _text(0, "promoter")),
            "HoldingChange": lambda: _ownership(self.data, f, tf, "holding", _text(0, "promoter"), int(num(1, 1))),
            "DaysToEvent": lambda: _ownership(self.data, f, tf, "event", _text(0, "any")),
            # U7 LG3: point-in-time fundamentals - the versions known by each bar's close (app/screener/fundamentals.py)
            "EPSGrowthYoY": lambda: _fundamental(self.data, f, tf, "eps_growth"),
            "SalesGrowthYoY": lambda: _fundamental(self.data, f, tf, "sales_growth"),
            "EarningsYield": lambda: _fundamental(self.data, f, tf, "earnings_yield"),
            "ReturnOnCapital": lambda: _fundamental(self.data, f, tf, "roc"),
            "GrahamNumber": lambda: _fundamental(self.data, f, tf, "graham_number"),
            "NCAVPerShare": lambda: _fundamental(self.data, f, tf, "ncav"),
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


def _pattern(f: pd.DataFrame, direction: str, confirm: str = "any") -> bool:
    """A pattern of the direction on the last bar (confirm any), or - AMW-3 - a pattern of that direction on the bar
    before whose next-bar status (`candlestick_patterns.confirmation_masks`) the last bar gave is `confirm`."""
    from app.price_action.candlestick_patterns import CONFIRM_STATES, detect_patterns_at
    if confirm not in CONFIRM_STATES:
        raise ScreenRuntimeError(f"unknown confirmation status {confirm!r}")
    if confirm == "any":
        return any(m.direction == direction for m in detect_patterns_at(f, len(f) - 1))
    if len(f) < 2 or not any(m.direction == direction for m in detect_patterns_at(f, len(f) - 2)):
        return False
    return _status(f["high"].iloc[-2], f["low"].iloc[-2], f["close"].iloc[-1], direction) == confirm


def _status(high: float, low: float, close: float, direction: str) -> str:
    """The next-bar status of a pattern bar (high, low) given the next close (the rule of confirmation_masks)."""
    good, bad = (close > high, close < low) if direction == "BULLISH" else (close < low, close > high)
    return "confirmed" if good else "failed" if bad else "unconfirmed"


def _pattern_series(f: pd.DataFrame, name: str, confirm: str = "any") -> pd.Series:
    """S5-A: True on each bar where the named pattern shows (`candlestick_patterns.pattern_masks`, vectorised: a whole
    frame costs about as much as one bar did). The first two bars are False. AMW-3: the v2 candles
    (`pattern_masks_v2`), and with `confirm` the status the bar gave the pattern on the bar before (`confirmation_masks`)."""
    from app.price_action.candlestick_patterns import (CONFIRM_STATES, PATTERN_DIRECTION, V2_PATTERN_NAMES, confirmation_masks, pattern_masks,
                                                       pattern_masks_v2)
    if confirm not in CONFIRM_STATES:
        raise ScreenRuntimeError(f"unknown confirmation status {confirm!r}")
    masks = pattern_masks_v2(f) if name in V2_PATTERN_NAMES else pattern_masks(f)
    if name not in masks:
        raise ScreenRuntimeError(f"unknown pattern {name!r}")
    if confirm == "any":
        return pd.Series(masks[name], index=f.index)
    return pd.Series(confirmation_masks(f, masks[name], PATTERN_DIRECTION[name])[confirm], index=f.index)


def _swing_pivots(f: pd.DataFrame, degree: int) -> List[Any]:
    """The causal swing engine's confirmed pivots of a degree (default settings), in confirmation order."""
    from app.price_action import causal_swings as cs
    from app.price_action import pa_settings
    s = pa_settings.settings()
    if not 0 <= degree < len(s["swing_atr_mult"]):
        raise ScreenRuntimeError(f"swing degree {degree} is not one of 0-{len(s['swing_atr_mult']) - 1}")
    frame = pd.DataFrame({k: f[k].astype(float).to_numpy() for k in ("open", "high", "low", "close")})
    frame["timestamp"] = frame["bar_end"] = f.index
    return [p for p in cs.degree_pivots(frame, degree, s) if p.confirmed_idx is not None]


def _swing_label(f: pd.DataFrame, kind: str, degree: int) -> pd.Series:
    """AMW-3: on every bar, the label of the last swing high (HH / LH) or low (HL / LL) CONFIRMED by that bar against the
    one of the same kind before it - the Dow trend's labels. An equal price does not extend the structure: an equal high
    reads LH, an equal low HL (TPL-F6: the two sides are mirror images; before it an equal low read LL). Missing (None)
    until two swings of the kind are confirmed. A pivot counts from its confirmation bar, never from its extreme (no
    look-ahead)."""
    if kind not in ("high", "low"):
        raise ScreenRuntimeError(f"unknown swing kind {kind!r}")
    k = "H" if kind == "high" else "L"
    out = np.full(len(f), None, dtype=object)
    prev: Optional[float] = None
    for p in _swing_pivots(f, degree):
        if p.kind != k:
            continue
        if prev is not None:
            out[int(p.confirmed_idx)] = ("HH" if p.price > prev else "LH") if k == "H" else ("LL" if p.price < prev else "HL")
        prev = p.price
    return pd.Series(out, index=f.index, dtype=object).ffill()


RSI_DIVERGENCE_PERIOD = 14         # RsiDivergence's RSI period when `period=` is left out (AMW-3: always 14)
RSI_DIVERGENCE_KIND = "regular"    # RsiDivergence's kind when `kind=` is left out (AMW-3 / E2: regular only)


@dataclass(frozen=True)
class _Divergence:
    """One confirmed swing of the divergence's kind (strategy templates E2): where it was confirmed, its extreme bar and
    price, and whether it diverged from the swing of the same kind before it."""
    confirmed: int
    bar: int
    price: float
    diverges: bool


def _divergence_swings(f: pd.DataFrame, side: str, degree: int, period: int, kind: str = RSI_DIVERGENCE_KIND) -> List[_Divergence]:
    """Every confirmed swing low (bullish) / high (bearish) of the degree, in confirmation order, with its divergence state
    against the one before (a lower low with a higher RSI(period) / a higher high with a lower RSI(period); RSI read at
    each swing's extreme bar, known by the confirmation bar). Shared by RsiDivergence and RsiDivergenceRef. Wave templates
    companions: `kind` "hidden" - a higher low with a lower RSI (bullish) / a lower high with a higher RSI (bearish)."""
    if side not in ("bullish", "bearish"):
        raise ScreenRuntimeError(f"unknown divergence side {side!r}")
    if kind not in ("regular", "hidden"):
        raise ScreenRuntimeError(f"unknown divergence kind {kind!r}")
    k = "L" if side == "bullish" else "H"
    rsi = _rsi(f["close"].astype(float), period).to_numpy(float)
    out: List[_Divergence] = []
    prev: Optional[Any] = None
    for p in _swing_pivots(f, degree):
        if p.kind != k:
            continue
        b = int(p.bar_idx)
        state = False
        if prev is not None:
            r0, r1 = rsi[int(prev.bar_idx)], rsi[b]
            if np.isfinite(r0) and np.isfinite(r1):
                if kind == "regular":
                    state = bool(p.price < prev.price and r1 > r0) if k == "L" else bool(p.price > prev.price and r1 < r0)
                else:
                    state = bool(p.price > prev.price and r1 < r0) if k == "L" else bool(p.price < prev.price and r1 > r0)
        out.append(_Divergence(int(p.confirmed_idx), b, float(p.price), state))
        prev = p
    return out


def _rsi_divergence(f: pd.DataFrame, side: str, lookback: int, degree: int, period: int = RSI_DIVERGENCE_PERIOD,
                    kind: str = RSI_DIVERGENCE_KIND) -> pd.Series:
    """AMW-3: a regular RSI divergence on every bar, from the swings CONFIRMED by that bar. Bullish: the last swing low
    is lower than the one before and the RSI at it is higher; bearish: a higher swing high with a lower RSI. RSI is read
    at each swing's extreme bar (known by the confirmation bar). True while the latest swing's extreme is at most
    `lookback` bars before the bar; false before two swings of the kind or where RSI has no value yet. The RSI period is
    `period` (strategy templates E2; 14 when left out, as before); `kind` "hidden" reads the hidden divergence instead (wave
    templates companions; regular when left out, as before)."""
    n_ = len(f)
    div = np.zeros(n_, dtype=bool)                      # the divergence state from each confirmation on
    last_bar = np.full(n_, -1, dtype=np.int64)          # the latest swing's extreme bar, known at each bar
    for d in _divergence_swings(f, side, degree, period, kind):
        div[d.confirmed:], last_bar[d.confirmed:] = d.diverges, d.bar
    age = np.arange(n_) - last_bar
    return pd.Series(div & (last_bar >= 0) & (age <= lookback), index=f.index)


def _rsi_divergence_ref(f: pd.DataFrame, what: str, side: str, degree: int, period: int = RSI_DIVERGENCE_PERIOD,
                        kind: str = RSI_DIVERGENCE_KIND) -> pd.Series:
    """Strategy templates E2: the latest divergence's prices, held from the bar that confirmed its swing until the next
    divergence of the side is confirmed (a later swing that does not diverge leaves it as it is); missing before the first
    one. `extreme`: the divergent swing's own price (its low - bullish - or high - bearish); `confirm_bar`: the far side of
    the bar that confirmed it (its high - bullish - or its low - bearish). Reads only bars up to each bar (the swings are
    used from their confirmation bar on)."""
    if what not in ("extreme", "confirm_bar"):
        raise ScreenRuntimeError(f"unknown divergence price {what!r}")
    far = f["high" if side == "bullish" else "low"].to_numpy(float)
    out = np.full(len(f), np.nan)
    for d in _divergence_swings(f, side, degree, period, kind):
        if d.diverges:
            out[d.confirmed:] = d.price if what == "extreme" else far[d.confirmed]
    return pd.Series(out, index=f.index)


def time_of_day(index: pd.DatetimeIndex, start: str, end: str) -> np.ndarray:
    """AMW-3 TimeOfDay: each bar's START time (exchange time, IST; a naive index is read as UTC) between start and end
    (HH:MM, both included). start after end wraps past midnight."""
    def minutes(text: str) -> int:
        try:
            hh, mm = text.split(":")
            value = int(hh) * 60 + int(mm)
        except ValueError as exc:
            raise ScreenRuntimeError(f"TimeOfDay needs HH:MM, got {text!r}") from exc
        if not (len(hh) == 2 and len(mm) == 2 and 0 <= int(hh) <= 23 and 0 <= int(mm) <= 59):
            raise ScreenRuntimeError(f"TimeOfDay needs HH:MM, got {text!r}")
        return value
    lo, hi = minutes(start), minutes(end)
    idx = pd.DatetimeIndex(index)
    local = (idx.tz_localize("UTC") if idx.tz is None else idx).tz_convert(EXCHANGE_TZ)
    m = np.asarray(local.hour * 60 + local.minute)
    return (m >= lo) & (m <= hi) if lo <= hi else (m >= lo) | (m <= hi)


def _wave(f: pd.DataFrame, degree: int, what: str) -> pd.Series:
    """Wave templates W1: one column of the causal wave count (state / type / alt / clarity) on every bar - computed once
    per frame and degree for every wave function of a screen (app/price_action/waves/series.py). Missing (None) before
    the first count and, for the alternate, when there is none: never matches."""
    from app.price_action.waves.series import WAVE_DEGREES, cached_wave_series
    if degree not in WAVE_DEGREES:
        raise ScreenRuntimeError(f"wave degree {degree} is not one of {', '.join(map(str, WAVE_DEGREES))}")
    values = getattr(cached_wave_series(f, degree), what)
    return pd.Series(np.array(values, dtype=object, copy=True), index=f.index, dtype=object)


def _wave_levels(f: pd.DataFrame, degree: int) -> Any:
    from app.price_action.waves.levels import cached_wave_levels
    from app.price_action.waves.series import WAVE_DEGREES
    if degree not in WAVE_DEGREES:
        raise ScreenRuntimeError(f"wave degree {degree} is not one of {', '.join(map(str, WAVE_DEGREES))}")
    return cached_wave_levels(f, degree)


def _wave_level(f: pd.DataFrame, degree: int, what: str, *args: Any) -> pd.Series:
    """Wave templates W2 / W3 / W4: one level, ratio or line of the main count on every bar (app/price_action/waves/
    levels.py) - NaN (FALSE for WaveBeyondOrigin) where it does not apply; one count per frame and degree."""
    levels = _wave_levels(f, degree)
    try:
        values = getattr(levels, what)(*args)
    except ValueError as exc:
        raise ScreenRuntimeError(str(exc)) from exc
    if what == "line":
        values = values[0]
    if values.dtype == bool:
        return pd.Series(values.copy(), index=f.index, dtype=bool)
    return pd.Series(np.array(values, dtype=float, copy=True), index=f.index, dtype=float)


def _wave_volume_lower(f: pd.DataFrame, degree: int, wave: str, base: str) -> pd.Series:
    """Wave templates companions: the mean volume of `wave` below the base wave's (app/price_action/waves/levels.py
    `volume_lower`); FALSE where it does not apply or the bars have no volume (an index)."""
    levels = _wave_levels(f, degree)
    volume = f["volume"].to_numpy(float) if "volume" in f.columns else np.full(len(f), np.nan)
    try:
        values = levels.volume_lower(volume, wave, base)
    except ValueError as exc:
        raise ScreenRuntimeError(str(exc)) from exc
    return pd.Series(values, index=f.index, dtype=bool)


def _wave_bars(f: pd.DataFrame, side: str) -> Tuple[Any, Any]:
    from app.screener.breakouts.screens import Bars
    if side not in ("up", "down"):
        raise ScreenRuntimeError(f"unknown breakout side {side!r}")
    b = Bars(f, _breakout_params({}))
    return b, b.side(1 if side == "up" else -1)


def _wave_breaks(f: pd.DataFrame, degree: int, of: str, line: str, wave: str, side: str, k: float) -> Tuple[Any, Any, List[Any]]:
    from app.screener.breakouts import wave_breaks as wb
    levels = _wave_levels(f, degree)
    b, s = _wave_bars(f, side)
    try:
        values, anchors = levels.line(line) if of == "wave_line" else levels.end_line(wave)
    except ValueError as exc:
        raise ScreenRuntimeError(str(exc)) from exc
    return b, s, wb.line_breaks(b, s, values, anchors, k)


def _wave_line_break(f: pd.DataFrame, degree: int, line: str, side: str, k: float) -> pd.Series:
    """Wave templates W4: the first close beyond a wave line by k ATR, once per line (app/screener/breakouts/wave_breaks.py)."""
    from app.screener.breakouts import wave_breaks as wb
    b, _, breaks = _wave_breaks(f, degree, "wave_line", line, "", side, k)
    return pd.Series(wb.break_series(b.n, breaks), index=f.index, dtype=bool)


def _wave_retest(f: pd.DataFrame, side: str, of: str, max_bars: int, reversal: bool, degree: int, line: str, wave: str,
                 k: float) -> pd.Series:
    """Wave templates R7: RetestEntry of a broken wave line (`of` wave_line) or wave end (`of` wave_end) - the B9 rule with
    the broken line's value at each bar (app/screener/breakouts/wave_breaks.py)."""
    from app.screener.breakouts import wave_breaks as wb
    b, s, breaks = _wave_breaks(f, degree, of, line, wave, side, k)
    return pd.Series(wb.retests(b, s, breaks, max_bars, reversal, float(b.p["retest_tol_atr"])), index=f.index, dtype=bool)


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


PRIOR_SWING_BACK: Dict[str, int] = {"one": 1, "two": 2, "three": 3}


def _prior_swing(f: pd.DataFrame, degree: int, which: str, kind: str) -> pd.Series:
    """Idea library IDEA-14: on every bar, the confirmed swing high (kind H) / low (L) `which` swings of its kind before the
    latest one (one: the one before SwingHigh / SwingLow), from the same causal swings as `_swings` - each pivot counts
    from the bar that confirmed it, so the value changes only when a new swing of that kind is confirmed. Missing until
    back + 1 swings of the kind are confirmed."""
    from app.price_action import causal_swings as cs
    from app.price_action import pa_settings
    s = pa_settings.settings()
    if not 0 <= degree < len(s["swing_atr_mult"]):
        raise ScreenRuntimeError(f"swing degree {degree} is not one of 0-{len(s['swing_atr_mult']) - 1}")
    if which not in PRIOR_SWING_BACK:
        raise ScreenRuntimeError(f"unknown swing position {which!r} (one, two or three swings back)")
    back = PRIOR_SWING_BACK[which]
    frame = pd.DataFrame({k: f[k].astype(float).to_numpy() for k in ("open", "high", "low", "close")})
    frame["timestamp"] = frame["bar_end"] = f.index
    out = np.full(len(f), np.nan)
    seen: List[float] = []
    for p in cs.degree_pivots(frame, degree, s):                         # confirmation order, as _swings
        if p.kind != kind:
            continue
        seen.append(float(p.price))
        if len(seen) > back:
            out[int(p.confirmed_idx)] = seen[-1 - back]                  # type: ignore[arg-type]
    return pd.Series(out, index=f.index).ffill()


def _channel_line(f: pd.DataFrame, side: str, touches: float, edge_touches: int) -> pd.Series:
    """Idea library IDEA-12: ChannelLine - the trend-side line of the B5 channel on every bar, in real prices (up: the
    lower line of a rising channel; down: the upper line of a falling one), with the B5 parameters' defaults otherwise."""
    from app.screener.breakouts.lines import channel_line
    from app.screener.breakouts.screens import Bars
    if side not in ("up", "down"):
        raise ScreenRuntimeError(f"unknown channel side {side!r}")
    p = _breakout_params({"tl_mode": "channel", "tl_min_touches": touches})
    b = Bars(f, p)
    s = b.side(1 if side == "up" else -1)
    return pd.Series(s.back(channel_line(b, s, p, edge_touches)), index=f.index, dtype=float)


_ZONE_CACHE: Dict[int, Tuple[Any, Any, Dict[Tuple[str, int], Tuple[np.ndarray, ...]]]] = {}


def _swing_zone(f: pd.DataFrame, side: str, degree: int, what: str, width: float = 0.0) -> pd.Series:
    """S5-A4: the zone at the last CONFIRMED swing low (support) or high (resistance) as of each bar, measured by the
    trade-port level_strength module (default settings) from bars up to that bar.

    - The zone is the pivot candle from its extreme to its body: support [low, min(open, close)], resistance
      [max(open, close), high]. The pivot bar is the zone's origin (departure and base are measured from it); the pivot
      is used only from the bar that confirmed it, and every bar after the origin that the measures read is <= the bar.
    - `strength` is strength_score (0-100) without leg labels (no leg classifier in ScreenQL) and without time at price
      (that needs 1-minute data), so those two parts score as unknown, the same way the module scores them.
    - `distance` is (close - zone mid) / median range (dist_mr). Missing before the first confirmed pivot, and on a bar
      with no price range to measure in (flat bars: a zero median range).
    - `edge` (the wave templates' zone-edge companion) is the far side of the band that `distance` within +/- `width`
      reads: the zone mid less (low) / plus (high) `width` median ranges, the same median range; missing where
      `distance` is.
    Strength, distance and the edge come from one pass, kept for the frame, so a screen using several pays once."""
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
    strength, distance, mid, mr = ref[2][key]
    if what == "edge":
        return pd.Series(mid - width * mr if side == "low" else mid + width * mr, index=f.index)
    return pd.Series((strength if what == "strength" else distance).copy(), index=f.index)   # the cache stays intact


def _swing_zone_arrays(f: pd.DataFrame, side: str, degree: int) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
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
    mid, used_mr = np.full(len(f), np.nan), np.full(len(f), np.nan)      # the zone's mid and the median range dist_mr used
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
        zlo, zhi = (float(l[b]), float(max(l[b], min(o[b], c[b])))) if side == "low" else (float(min(h[b], max(o[b], c[b]))), float(h[b]))
        mid[t], used_mr[t] = (zlo + zhi) / 2, mr_t                         # as strength_features measures dist_mr
    return strength, distance, mid, used_mr


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


def _bkd(name: str) -> Any:
    """U6 BK3: a breakout parameter's default (the ScreenQL argument's default when it is left out)."""
    from app.screener.breakouts.params import DEFAULTS
    return DEFAULTS[name]


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
        out = evaluate_screen(f, screen, p, sides=(side,), tf=tf, bars=bars, columns=("signal", "event"))[side]
    except ValueError as exc:
        raise ScreenRuntimeError(str(exc)) from exc
    if what == "type":
        return out["event"].where(out["signal"], "").astype(object)
    if what == "class":                                     # U6 BK4 GapType: the class on every gap bar, a fill included
        from app.screener.breakouts.gaps import CLASSES
        return out["event"].where(out["event"].isin(CLASSES), "").astype(object)
    return out["signal"].astype(bool)


def _moving_average(x: pd.Series, n_: int, kind: str) -> pd.Series:
    """Strategy templates step 2: MA(x, n, type) - exactly the series of SMA / EMA / WMA / HMA."""
    if kind == "SMA":
        return x.rolling(n_).mean()
    if kind == "EMA":
        return _ema(x, n_)
    if kind == "WMA":
        return _wma(x, n_)
    if kind == "HMA":
        return _hma(x, n_)
    raise ScreenRuntimeError(f"unknown moving average type {kind!r}")


def _breakout_ref(f: pd.DataFrame, screen: str, what: str, side: str, overrides: Dict[str, Any], stop_mult: float,
                  tf: Optional[str] = None, bars: Any = None) -> pd.Series:
    """Strategy templates step 2 (owner order R2): a breakout's prices as a series - the columns evaluate_screen keeps for
    the same breakout (one per leg, closed bars), on its signal bars only (NaN elsewhere); `held_level` holds the latest
    signal's level until the next one; the stop references are the breakout quality overlay's own (quality.stop_reference).
    `tf` / `bars` (TPL-8, the gap family): the bars' timeframe and the session Bars on the exchange clock, exactly as the
    breakout's own function reads them (_breakout); left out, every other family runs as before."""
    from app.screener.breakouts.quality import stop_reference
    from app.screener.breakouts.screens import Bars, evaluate_screen
    if side not in ("up", "down"):
        raise ScreenRuntimeError(f"unknown breakout side {side!r}")
    p = _breakout_params(overrides)
    b = bars if bars is not None else Bars(f, p, tf=tf)
    try:
        out = evaluate_screen(f, screen, p, bars=b, sides=(side,), columns=("signal", "level", "fail_level", "base_high", "base_low"))[side]
    except ValueError as exc:
        raise ScreenRuntimeError(str(exc)) from exc
    on = np.flatnonzero(out["signal"].to_numpy(dtype=bool))
    res = np.full(len(f), np.nan)
    if what in ("level", "fail_level", "base_high", "base_low"):
        res[on] = out[what].to_numpy(dtype=float)[on]
    elif what == "held_level":
        res[on] = out["level"].to_numpy(dtype=float)[on]
        return pd.Series(res, index=f.index).ffill()
    elif what in ("stop_level_atr", "stop_base", "stop_midpoint"):
        level, low, high = (out[k].to_numpy(dtype=float) for k in ("level", "base_low", "base_high"))
        for j in on:
            res[j] = stop_reference(level[j], low[j], high[j], float(b.atr_prev[j]), side, what.removeprefix("stop_"), stop_mult)
    else:
        raise ScreenRuntimeError(f"unknown breakout reference {what!r}")
    return pd.Series(res, index=f.index)


def _retest_ref(f: pd.DataFrame, what: str, side: str, overrides: Dict[str, Any], stop_mult: float) -> pd.Series:
    """Strategy templates R7 / R2: a B9 retest's prices as a series - read from the same retests the B9 screen finds
    (app/screener/breakouts/retest.py `retest_events`), on the retest bars only (NaN elsewhere); `held_level` holds the
    latest retest's level (a channel edge or a projected trendline along its line) until the next retest. Real prices
    (the down side's mirrored orientation turned back), as RangeBreakoutRef / LineBreakoutRef."""
    from app.screener.breakouts.quality import stop_reference
    from app.screener.breakouts.retest import retest_events
    from app.screener.breakouts.screens import Bars
    if side not in ("up", "down"):
        raise ScreenRuntimeError(f"unknown breakout side {side!r}")
    p = _breakout_params(overrides)
    b = Bars(f, p)
    s = b.side(1 if side == "up" else -1)
    try:
        events = retest_events(b, s, p)
    except ValueError as exc:
        raise ScreenRuntimeError(str(exc)) from exc
    res = np.full(len(f), np.nan)
    # in retest-bar order (the retests of two breakouts can interleave); on a bar with two, the later breakout's - as B9
    events = sorted(events, key=lambda e: e.bar)
    for k, r in enumerate(events):
        j = r.bar
        level = float(s.back(r.level))
        # the base in real prices: the down side's high / low swap back (as evaluate_screen does)
        base_high, base_low = (float(s.back(r.base_high)), float(s.back(r.base_low))) if s.sign > 0 else \
            (float(s.back(r.base_low)), float(s.back(r.base_high)))
        if what == "level":
            res[j] = level
        elif what == "retest_low":
            res[j] = float(s.back(np.min(s.l[r.breakout + 1:j + 1])))
        elif what == "base_high":
            res[j] = base_high
        elif what == "base_low":
            res[j] = base_low
        elif what == "held_level":
            end = next((e.bar for e in events[k + 1:] if e.bar > j), len(f))
            res[j:end] = [float(s.back(r.level_at(x))) for x in range(j, end)]
        elif what in ("stop_level_atr", "stop_base", "stop_midpoint"):
            res[j] = stop_reference(level, base_low, base_high, float(b.atr_prev[j]), side, what.removeprefix("stop_"), stop_mult)
        else:
            raise ScreenRuntimeError(f"unknown retest reference {what!r}")
    return pd.Series(res, index=f.index)


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


def _fundamental(data: SymbolData, f: pd.DataFrame, tf: str, measure: str) -> pd.Series:
    from app.screener.fundamentals import series
    return series(data, f, tf, measure)


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
                    params: Optional[Dict[str, Any]] = None, matches: Optional[List[Match]] = None, vectorized: bool = False,
                    memo: Optional[ScreenMemo] = None) -> Dict[str, Any]:
    """U5: the screen as a funnel. A top-level ALL is a list of stages (anything else is one stage).

    Each stage is evaluated ONCE over the whole universe (so cross-sectional functions - Rank, PercentileRank - see the
    same universe as in the full screen) and the results are ANDed cumulatively: the count after a stage is the
    symbols that passed it and every stage before it, and the last count equals the screen's match count.
    Symbols the screen cannot evaluate at all (not enough history, a runtime problem) are left out of the funnel
    (`with_data`) instead of being "removed" by the first stage.

    U5 D2: `passes` gives, per evaluated symbol, whether it passed each stage on its own (not only while it was still
    in) - the "why it matched / where it fell out" chips on a result row.

    LD-7: `memo` - the one the full screen ran with (`run_screen(..., memo=)`): each stage is one of the screen's top-level
    items, so its values per symbol are read back instead of computed again (the same values; see `ScreenMemo`)."""
    full = {m.symbol: m for m in (matches if matches is not None else run_screen(ast, validated, universe, base_tf=base_tf, params=params,
                                                                                vectorized=vectorized, memo=memo))}
    usable = [d for d in universe if full[d.symbol].reason is None]
    items = list(ast.items) if isinstance(ast, n.Logic) and ast.op == "ALL" else [ast]
    alive = [d.symbol for d in usable]
    stages: List[Dict[str, Any]] = []
    passes: Dict[str, List[bool]] = {d.symbol: [] for d in usable}
    for item in items:
        passed = {m.symbol for m in run_screen(item, validated, usable, base_tf=base_tf, params=params, vectorized=vectorized, memo=memo)
                  if m.matched}
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
               check_history: bool = True, vectorized: bool = False, memo: Optional[ScreenMemo] = None) -> List[Match]:
    """Every symbol: does the screen hold on its last closed bar? Runs only a validated screen. With `check_history` a symbol
    shorter than the validator's lookback is reported as such; without it, it is simply evaluated (NaN never matches).
    Z10-d: `vectorized` evaluates the symbols that share a bar index together (app/screener/panel.py) when every part of
    the screen has a vectorised form; the matches are the same, only the time changes.
    LD-7: `memo` (see `ScreenMemo`) keeps each symbol's evaluated nodes for the next evaluation in the same run."""
    if not validated.ok:
        raise ScreenRuntimeError("the screen did not pass validation")
    params = dict(params or {})
    if vectorized:
        from app.screener import panel
        if panel.supported(ast, validated, base_tf):
            return panel.run_panel(ast, validated, universe, base_tf=base_tf, params=params, check_history=check_history,
                                   per_symbol=lambda rest: run_screen(ast, validated, rest, base_tf=base_tf, params=params, check_history=check_history))
    constants = _cross_sectional(ast, universe, base_tf, params) if validated.cross_sectional else {d.symbol: {} for d in universe}
    if validated.cross_sectional:
        memo = None                        # Rank / PercentileRank depend on the universe, which differs between the calls
    out: List[Match] = []
    for d in universe:
        if d.frames.get(base_tf) is None or d.frames[base_tf].empty:
            out.append(Match(d.symbol, False, reasons.Reason(f"no {base_tf} bars", reasons.NO_BARS)))
            continue
        ev = _Eval(d, base_tf, params, constants[d.symbol], memo=memo.setdefault(id(d), {}) if memo is not None else None)
        # S5-A review: a higher timeframe built from the base bars is checked too (a 1d swing on a 5m screen needs 100
        # daily bars; a few days of 5m bars would otherwise give NaN with no reason)
        short = [tf for tf, bars in validated.lookback.items()
                 if (tf in d.frames or n.TF_MINUTES.get(tf, 0) > n.TF_MINUTES[base_tf]) and len(ev.frame(tf)) < bars] if check_history else []
        if short:
            out.append(Match(d.symbol, False, reasons.short_history(short)))
            continue
        try:
            value = _last(ev.ev(ast, base_tf))
        except ScreenRuntimeError as exc:
            out.append(Match(d.symbol, False, reasons.Reason(str(exc), reasons.NOT_CHECKED)))
            continue
        except Exception as exc:  # noqa: BLE001 - S5-A4 review: one symbol's bad data must not abort the whole universe
            # SCR-FIX B3: the full text in the log, a plain sentence (never the exception's class name) for the user
            out.append(Match(d.symbol, False, reasons.not_checked(exc, d.symbol)))
            continue
        out.append(last_match(d.symbol, value))
    return out


def last_match(symbol: str, value: Any) -> Match:
    """SCR-FIX B1: the screen's value on the last bar -> a Match. No value (NaN: the indicators' warm-up is longer than
    the bars) is "could not be checked" with its reason, never "did not match"."""
    if isinstance(value, float) and np.isnan(value):
        return Match(symbol, False, reasons.of(reasons.NO_VALUE))
    return Match(symbol, bool(value))


def evaluate(ast: Any, data: SymbolData, *, base_tf: str, params: Optional[Dict[str, Any]] = None) -> Any:
    """The full series (or constant) of an expression for one symbol - for tests, charts and the builder preview."""
    return _Eval(data, base_tf, dict(params or {}), {}).ev(ast, base_tf)


__all__ = ["SymbolData", "Match", "last_match", "run_screen", "evaluate", "resample", "closed_only", "ScreenRuntimeError"]
