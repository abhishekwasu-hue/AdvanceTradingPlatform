"""Z10-d (G4, ATP_SCREENER_SPEC §1.1 mode (a), §1.5): the vectorised screen - the same ScreenQL runtime evaluated once
over a whole universe instead of once per symbol.

The per-symbol runtime (`runtime._Eval`) pays pandas' fixed cost (tens of microseconds per operation) for every symbol
and every factor: a 50-factor screen over 2,500 symbols is about 125,000 small operations, minutes of work. Here the
symbols that share the same bar index are stacked into one frame per field (one column per symbol), and every
operation runs once on the stack. Shifts, arithmetic, comparisons and the logic are elementwise; the window functions
(rolling mean / standard deviation, exponential means) are kernels below that repeat pandas' own online algorithms
operation for operation. So each column is the very series the per-symbol runtime computes
(`tests/test_z10d_vectorised_screen.py` checks this bit for bit, function by function, and match for match on random
screens).

What it covers: fields and the functions in `FUNCTIONS` below on the screen's base timeframe, numbers, parameters,
arithmetic, comparisons, BETWEEN / IN, ALL / ANY / NOT. Anything else (another timeframe, a cross-sectional rank, a
price-action or option-chain function, a classifier) runs through the per-symbol runtime unchanged, and so does a group
whose evaluation raised (each symbol then gets its own reason, exactly as before). Closed bars only, as ever: the
input is the same closed frames.

Opt-in: `run_screen(..., vectorized=True)`. The routes and the bar-close engine pass it only when the operator flag
`screener_vectorized` is on (off by default).
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from app.screener import nodes as n
from app.screener.runtime import Match, ScreenRuntimeError, SymbolData, _as_bool, _cross, _Eval, bool_frame
from app.screener.validator import Validated

logger = logging.getLogger(__name__)

FLAG = "screener_vectorized"      # operator flag (off by default): the routes and the bar-close engine opt in with it
FUNCTIONS = frozenset({"SMA", "EMA", "RSI", "ATR", "ADX", "PlusDI", "MinusDI", "Max", "Min", "Greatest", "Least", "Count", "CountStreak",
                       "CrossAbove", "CrossBelow", "PctChange", "ZScore", "Lag"})
MIN_GROUP = 2                     # a symbol alone in its index group gains nothing from stacking: per-symbol path
_COLUMNS = ("open", "high", "low", "close", "volume", "oi")


def supported(ast: Any, validated: Validated, base_tf: str) -> bool:
    """True when every node of the screen has a vectorised form on `base_tf` (else the per-symbol runtime runs it)."""
    if validated.cross_sectional or any(tf != base_tf for tf in validated.lookback):
        return False
    for node in n.walk(ast):
        if isinstance(node, (n.Field, n.Call)) and node.tf not in (None, base_tf):
            return False
        if isinstance(node, n.Field) and node.name not in _COLUMNS:
            return False
        if isinstance(node, n.Call) and (node.name not in FUNCTIONS or node.kwargs):
            return False
        if not isinstance(node, (n.Num, n.Str, n.Bool, n.Param, n.Field, n.Call, n.Unary, n.Binary, n.Compare, n.Between, n.In, n.Logic, n.Not)):
            return False
    return True


class Panel:
    """The bars of a group of symbols that share one index: `panel["close"]` is a frame (bars x symbols), built on first
    use from each symbol's own column (a symbol without the column gives NaN, as `frame.get` does per symbol)."""

    def __init__(self, frames: List[pd.DataFrame], symbols: List[str]) -> None:
        self.frames, self.symbols = frames, symbols
        self.index = frames[0].index
        self._built: Dict[str, pd.DataFrame] = {}

    def __len__(self) -> int:
        return len(self.index)

    def get(self, column: str) -> Optional[pd.DataFrame]:
        if column not in self._built:
            if not any(column in f.columns for f in self.frames):
                return None
            stack = np.column_stack([f[column].to_numpy(dtype=float) if column in f.columns else np.full(len(self.index), np.nan) for f in self.frames])
            self._built[column] = pd.DataFrame(stack, index=self.index, columns=self.symbols)
        return self._built[column]

    def __getitem__(self, column: str) -> pd.DataFrame:
        """An indicator's input column: like `frame[column]` per symbol, a symbol without it is an error (the group then
        runs per symbol, where that symbol gets its reason)."""
        if not all(column in f.columns for f in self.frames):
            raise KeyError(column)
        return self.get(column)  # type: ignore[return-value]


# --- kernels: pandas' own window algorithms, run across all symbols at once ------------------------------------------
# pandas computes a rolling mean / variance and an exponential mean column by column with an online (compensated)
# update; the loops below make the same floating-point operations in the same order, one bar at a time for every
# symbol together, so each column equals what pandas returns for that symbol's series (tested bit for bit). A frame
# with few symbols and many bars is cheaper column by column, so it is handed to pandas itself.

def _pandas_is_cheaper(x: np.ndarray) -> bool:
    return x.shape[1] < x.shape[0]


def _leading_gap(x: np.ndarray) -> Optional[int]:
    """k when the first k rows are all missing and no value is missing after them (a diff or a shift); else None.
    pandas skips missing values and counts only the others, so such a frame is the frame from row k, padded."""
    missing = np.isnan(x)
    if not missing.any():
        return 0
    full = missing.all(axis=1)
    k = int(np.argmin(full)) if not full.all() else len(x)
    return k if not missing[k:].any() else None


def _padded(x: np.ndarray, k: int, fn: Callable[[np.ndarray], np.ndarray]) -> np.ndarray:
    out = np.full(x.shape, np.nan)
    if k < len(x):
        out[k:] = fn(x[k:])
    return out


def _ewm_mean(x: np.ndarray, *, span: Optional[float] = None, alpha: Optional[float] = None, min_periods: int = 0) -> np.ndarray:
    """`ewm(span= | alpha=, adjust=False, min_periods=).mean()` (ignore_na False)."""
    com = (span - 1) / 2.0 if span is not None else (1.0 - alpha) / alpha  # type: ignore[operator]
    if _pandas_is_cheaper(x):
        return pd.DataFrame(x).ewm(com=com, adjust=False, min_periods=min_periods).mean().to_numpy()
    a = 1.0 / (1.0 + com)
    f = 1.0 - a
    minp = max(min_periods, 1)
    k = _leading_gap(x)
    if k:
        return _padded(x, k, lambda part: _ewm_mean(part, span=span, alpha=alpha, min_periods=min_periods))
    rows, cols = x.shape
    out = np.empty_like(x)
    w = x[0].copy()
    if k == 0:                                                 # the usual case: every bar has a value
        out[0] = w if minp <= 1 else np.nan
        for i in range(1, rows):
            cur = x[i]
            w = np.where(w != cur, (f * w + a * cur) / (f + a), w)
            out[i] = w if i + 1 >= minp else np.nan
        return out
    nobs = (w == w).astype(np.int64)
    old = np.ones(cols)
    out[0] = np.where(nobs >= minp, w, np.nan)
    with np.errstate(invalid="ignore"):
        for i in range(1, rows):
            cur = x[i]
            obs = cur == cur
            nobs += obs
            had = w == w
            old = np.where(had, old * f, old)
            w = np.where(had & obs & (w != cur), (old * w + a * cur) / (old + a), np.where(~had & obs, cur, w))
            old = np.where(had & obs, 1.0, old)
            out[i] = np.where(nobs >= minp, w, np.nan)
    return out


def _rolling_mean(x: np.ndarray, win: int) -> np.ndarray:
    """`rolling(win).mean()` (min_periods = win): a compensated running sum; a window of one repeated value is that
    value, and a window of all non-negative (non-positive) values never averages below (above) zero."""
    if _pandas_is_cheaper(x):
        return pd.DataFrame(x).rolling(win).mean().to_numpy()
    k = _leading_gap(x)
    if k is None:
        return _rolling_mean_gaps(x, win)
    if k:
        return _padded(x, k, lambda part: _rolling_mean(part, win))
    rows, cols = x.shape
    out = np.full((rows, cols), np.nan)
    total, comp_add, comp_rem = np.zeros(cols), np.zeros(cols), np.zeros(cols)
    neg, same = np.zeros(cols, np.int64), np.zeros(cols, np.int64)
    prev = np.full(cols, np.nan)
    negative = np.signbit(x)
    for i in range(rows):                                      # every value present: nobs is min(i + 1, win)
        if i >= win:
            y = -x[i - win] - comp_rem
            t = total + y
            comp_rem = t - total - y
            total = t
            neg -= negative[i - win]
        v = x[i]
        y = v - comp_add
        t = total + y
        comp_add = t - total - y
        total = t
        neg += negative[i]
        same = np.where(v == prev, same + 1, 1)
        prev = v
        if i >= win - 1:
            r = np.where(same >= win, v, total / win)
            r[((neg == 0) & (r < 0)) | ((neg == win) & (r > 0))] = 0.0
            out[i] = r
    return out


def _rolling_mean_gaps(x: np.ndarray, win: int) -> np.ndarray:
    """`_rolling_mean` with missing values (they are skipped and counted out, as pandas does)."""
    rows, cols = x.shape
    out = np.full((rows, cols), np.nan)
    total, comp_add, comp_rem = np.zeros(cols), np.zeros(cols), np.zeros(cols)
    nobs, neg, same = np.zeros(cols, np.int64), np.zeros(cols, np.int64), np.zeros(cols, np.int64)
    prev = np.full(cols, np.nan)
    ok_all = ~np.isnan(x)
    negative = np.signbit(x) & ok_all
    with np.errstate(invalid="ignore", divide="ignore"):
        for i in range(rows):
            if i >= win:
                v, ok = x[i - win], ok_all[i - win]
                y = -v - comp_rem
                t = total + y
                comp_rem = np.where(ok, t - total - y, comp_rem)
                total = np.where(ok, t, total)
                nobs -= ok
                neg -= negative[i - win]
            v, ok = x[i], ok_all[i]
            y = v - comp_add
            t = total + y
            comp_add = np.where(ok, t - total - y, comp_add)
            total = np.where(ok, t, total)
            nobs += ok
            neg += negative[i]
            same = np.where(ok, np.where(v == prev, same + 1, 1), same)
            prev = np.where(ok, v, prev)
            if i >= win - 1:
                r = total / nobs
                r = np.where(same >= nobs, prev, np.where((neg == 0) & (r < 0), 0.0, np.where((neg == nobs) & (r > 0), 0.0, r)))
                out[i] = np.where(nobs >= win, r, np.nan)
    return out


def _rolling_std(x: np.ndarray, win: int) -> np.ndarray:
    """`rolling(win).std()` (ddof 1): pandas' compensated Welford update, then the square root."""
    if _pandas_is_cheaper(x):
        return pd.DataFrame(x).rolling(win).std().to_numpy()
    k = _leading_gap(x)
    if k is None:
        return _rolling_std_gaps(x, win)
    if k:
        return _padded(x, k, lambda part: _rolling_std(part, win))
    rows, cols = x.shape
    out = np.full((rows, cols), np.nan)
    mean, ssq, comp_add, comp_rem = np.zeros(cols), np.zeros(cols), np.zeros(cols), np.zeros(cols)
    same = np.zeros(cols, np.int64)
    prev = np.full(cols, np.nan)
    with np.errstate(invalid="ignore", divide="ignore"):
        for i in range(rows):                                  # every value present: nobs is min(i + 1, win)
            if i >= win:                                       # a full window loses one value: win - 1 >= 1 remain
                v = x[i - win]
                nobs = win - 1
                if nobs:
                    prev_mean = mean - comp_rem
                    y = v - comp_rem
                    t = y - mean
                    comp_rem = t + mean - y
                    mean = mean - t / nobs
                    ssq = ssq - (v - prev_mean) * (v - mean)
                else:
                    mean, ssq = np.zeros(cols), np.zeros(cols)
            nobs = min(i + 1, win)
            v = x[i]
            same = np.where(v == prev, same + 1, 1)
            prev = v
            prev_mean = mean - comp_add
            y = v - comp_add
            t = y - mean
            comp_add = t + mean - y
            mean = mean + t / nobs
            ssq = ssq + (v - prev_mean) * (v - mean)
            if i >= win - 1:
                if win > 1:
                    var = ssq / (win - 1)
                    var[(same >= win) | (var < 0)] = 0.0
                    out[i] = np.sqrt(var)
    return out


def _rolling_std_gaps(x: np.ndarray, win: int) -> np.ndarray:
    """`_rolling_std` with missing values (skipped and counted out, as pandas does)."""
    rows, cols = x.shape
    out = np.full((rows, cols), np.nan)
    mean, ssq, comp_add, comp_rem = np.zeros(cols), np.zeros(cols), np.zeros(cols), np.zeros(cols)
    nobs, same = np.zeros(cols, np.int64), np.zeros(cols, np.int64)
    prev = np.full(cols, np.nan)
    ok_all = ~np.isnan(x)
    with np.errstate(invalid="ignore", divide="ignore"):
        for i in range(rows):
            if i >= win:
                v, ok = x[i - win], ok_all[i - win]
                nobs = nobs - ok
                left = ok & (nobs > 0)
                prev_mean = mean - comp_rem
                y = v - comp_rem
                t = y - mean
                new_mean = mean - t / nobs
                new_ssq = ssq - (v - prev_mean) * (v - new_mean)
                comp_rem = np.where(left, t + mean - y, comp_rem)
                mean = np.where(left, new_mean, np.where(ok, 0.0, mean))
                ssq = np.where(left, new_ssq, np.where(ok, 0.0, ssq))
            v, ok = x[i], ok_all[i]
            same = np.where(ok, np.where(v == prev, same + 1, 1), same)
            prev = np.where(ok, v, prev)
            nobs = nobs + ok
            prev_mean = mean - comp_add
            y = v - comp_add
            t = y - mean
            new_mean = mean + t / nobs
            new_ssq = ssq + (v - prev_mean) * (v - new_mean)
            comp_add = np.where(ok, t + mean - y, comp_add)
            mean = np.where(ok, new_mean, mean)
            ssq = np.where(ok, new_ssq, ssq)
            if i >= win - 1:
                var = ssq / (nobs - 1)
                var = np.where((nobs == 1) | (same >= nobs), 0.0, np.where(var < 0, 0.0, var))
                out[i] = np.where((nobs >= win) & (nobs > 1), np.sqrt(var), np.nan)
    return out


def _rolling_extreme(x: np.ndarray, win: int, op: Callable[..., Any]) -> np.ndarray:
    """`rolling(win).max()` / `.min()`: exact whatever the order, so a doubling table (log2(win) passes); a window with a
    missing value is missing, like pandas with min_periods = win."""
    rows = x.shape[0]
    out = np.full(x.shape, np.nan)
    if win > rows:
        return out
    table, span = x.copy(), 1
    while span * 2 <= win:                    # table[t] = extreme of x[t - 2 span + 1 .. t] (complete from t = 2 span - 1)
        table[span:] = op(table[span:], table[:-span])
        span *= 2
    rest = win - span                         # the window ending at t = the span ending at t and the one ending rest earlier
    out[win - 1:] = op(table[win - 1:], table[win - 1 - rest:rows - rest]) if rest else table[win - 1:]
    return out


def _rolling_count(x: np.ndarray, win: int) -> np.ndarray:
    """`rolling(win).sum()` of 0/1 values: whole numbers, so a cumulative difference is exact."""
    out = np.full(x.shape, np.nan)
    if win > x.shape[0]:
        return out
    c = np.cumsum(x, axis=0)
    out[win - 1] = c[win - 1]
    out[win:] = c[win:] - c[:-win]
    return out


def _true_range(f: Panel) -> np.ndarray:
    """`app.indicators.volatility.true_range` per column: the largest of the three ranges, NaN ignored (fmax)."""
    high, low, close = f["high"].to_numpy(), f["low"].to_numpy(), f["close"].to_numpy()
    prev_close = np.r_[np.full((1, close.shape[1]), np.nan), close[:-1]]
    return np.fmax(np.fmax(high - low, np.abs(high - prev_close)), np.abs(low - prev_close))


def _nan_zero(x: np.ndarray) -> np.ndarray:
    return np.where(x == 0.0, np.nan, x)


def _adx(f: Panel, period: int) -> Dict[str, np.ndarray]:
    """`app.indicators.directional.adx` per column, step for step."""
    high, low = f["high"].to_numpy(), f["low"].to_numpy()
    nan_row = np.full((1, high.shape[1]), np.nan)
    up_move = np.r_[nan_row, high[1:] - high[:-1]]
    down_move = -np.r_[nan_row, low[1:] - low[:-1]]
    with np.errstate(invalid="ignore", divide="ignore"):
        plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
        minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
        alpha = 1 / period
        smooth_tr = _ewm_mean(_true_range(f), alpha=alpha, min_periods=period)
        smooth_plus_dm = _ewm_mean(plus_dm, alpha=alpha, min_periods=period)
        smooth_minus_dm = _ewm_mean(minus_dm, alpha=alpha, min_periods=period)
        plus_di = 100 * (smooth_plus_dm / _nan_zero(smooth_tr))
        minus_di = 100 * (smooth_minus_dm / _nan_zero(smooth_tr))
        dx = 100 * np.abs(plus_di - minus_di) / _nan_zero(plus_di + minus_di)
    return {"plus_di": plus_di, "minus_di": minus_di, "adx": _ewm_mean(dx, alpha=alpha, min_periods=period)}


def _rsi(close: np.ndarray, period: int) -> np.ndarray:
    """`app.indicators.momentum.rsi` per column, step for step."""
    delta = np.r_[np.full((1, close.shape[1]), np.nan), close[1:] - close[:-1]]
    with np.errstate(invalid="ignore"):
        gain = np.where(delta < 0.0, 0.0, delta)               # clip(lower=0.0): NaN stays NaN
        loss = -np.where(delta > 0.0, 0.0, delta)              # -clip(upper=0.0)
    avg_gain = _ewm_mean(gain, alpha=1 / period, min_periods=period)
    avg_loss = _ewm_mean(loss, alpha=1 / period, min_periods=period)
    with np.errstate(invalid="ignore", divide="ignore"):
        rs = avg_gain / _nan_zero(avg_loss)
        result = 100 - (100 / (1 + rs))
    return np.where(avg_loss != 0, result, 100.0)


def _streak(cond: np.ndarray) -> np.ndarray:
    """`runtime._streak` per column: bars since the last False, counting this one (whole numbers, so exact)."""
    c = cond.astype(np.int64)
    total = np.cumsum(c, axis=0)
    reset = pd.DataFrame(np.where(c == 0, total, np.nan)).ffill().fillna(0).to_numpy()
    return (total - reset).astype(float)


class _PanelEval(_Eval):
    """`runtime._Eval` over a Panel: the node switch is inherited; fields, series and calls make frames (bars x
    symbols, one block each, sharing one index and one column list)."""

    def __init__(self, panel: Panel, base_tf: str, params: Dict[str, Any]) -> None:
        self.data = None  # type: ignore[assignment]
        self.base_tf, self.params, self.constants = base_tf, params, {}
        self.frames = {base_tf: panel}  # type: ignore[dict-item]
        self.panel = panel
        self.columns = pd.Index(panel.symbols)
        self._memo: Dict[str, Any] = {}

    def frame(self, tf: str) -> Any:
        if tf != self.base_tf:
            raise ScreenRuntimeError(f"the vectorised screen runs on {self.base_tf} bars only")
        return self.panel

    def wrap(self, values: Any) -> pd.DataFrame:
        return pd.DataFrame(values, index=self.panel.index, columns=self.columns)

    def ev(self, node: Any, tf: str) -> Any:
        if isinstance(node, n.Field):
            col = self.panel.get(node.name)
            frame = col if col is not None else self.wrap(np.nan)
            return frame.shift(node.offset) if node.offset else frame
        if isinstance(node, n.Call):                             # the same call twice in a screen is computed once
            key = n.to_text(node)
            if key not in self._memo:
                self._memo[key] = super().ev(node, tf)
            return self._memo[key]
        return super().ev(node, tf)

    def series(self, node: Any, tf: str) -> pd.DataFrame:  # type: ignore[override]
        v = self.ev(node, tf)
        if isinstance(v, pd.DataFrame):
            return v if bool_frame(v) or all(t == np.float64 for t in set(v.dtypes)) else v.astype(float)
        return self.wrap(float(v) if not isinstance(v, (str, type(None))) else np.nan)

    def values(self, node: Any, tf: str) -> np.ndarray:
        return self.series(node, tf).to_numpy(dtype=float)

    def flags(self, node: Any, tf: str) -> np.ndarray:
        v = self.ev(node, tf)
        return _as_bool(v).to_numpy(dtype=bool) if isinstance(v, pd.DataFrame) else np.full((len(self.panel), len(self.columns)), bool(v))

    def adx(self, period: int) -> Dict[str, np.ndarray]:
        key = f"adx:{period}"                                    # ADX, PlusDI and MinusDI share one pass
        if key not in self._memo:
            self._memo[key] = _adx(self.panel, period)
        return self._memo[key]

    def call(self, node: n.Call, tf: str) -> Any:
        name, args = node.name, node.args
        f = self.panel

        def win(i: int, default: int = 0) -> int:
            return int(self.ev(args[i], tf)) if len(args) > i else default

        def zscore() -> pd.DataFrame:
            x, w = self.values(args[0], tf), win(1)
            with np.errstate(invalid="ignore", divide="ignore"):
                return self.wrap((x - _rolling_mean(x, w)) / _nan_zero(_rolling_std(x, w)))

        impl: Dict[str, Callable[[], Any]] = {
            "SMA": lambda: self.wrap(_rolling_mean(self.values(args[0], tf), win(1))),
            "EMA": lambda: self.wrap(_ewm_mean(self.values(args[0], tf), span=win(1), min_periods=win(1))),
            "RSI": lambda: self.wrap(_rsi(f["close"].to_numpy(), win(0, 14))),
            "ATR": lambda: self.wrap(_ewm_mean(_true_range(f), alpha=1 / win(0, 14), min_periods=win(0, 14))),
            "ADX": lambda: self.wrap(self.adx(win(0, 14))["adx"]),
            "PlusDI": lambda: self.wrap(self.adx(win(0, 14))["plus_di"]),
            "MinusDI": lambda: self.wrap(self.adx(win(0, 14))["minus_di"]),
            "Max": lambda: self.wrap(_rolling_extreme(self.values(args[0], tf), win(1), np.maximum)),
            "Min": lambda: self.wrap(_rolling_extreme(self.values(args[0], tf), win(1), np.minimum)),
            "Greatest": lambda: self.wrap(_extreme([self.values(a, tf) for a in args], np.maximum)),
            "Least": lambda: self.wrap(_extreme([self.values(a, tf) for a in args], np.minimum)),
            "Count": lambda: self.wrap(_rolling_count(self.flags(args[1], tf).astype(float), win(0))),
            "CountStreak": lambda: self.wrap(_streak(self.flags(args[0], tf))),
            "CrossAbove": lambda: _cross(self.series(args[0], tf), self.series(args[1], tf), above=True),  # type: ignore[arg-type]
            "CrossBelow": lambda: _cross(self.series(args[0], tf), self.series(args[1], tf), above=False),  # type: ignore[arg-type]
            "PctChange": lambda: self.series(args[0], tf).pct_change(win(1), fill_method=None) * 100.0,
            "ZScore": zscore,
            "Lag": lambda: self.series(args[0], tf).shift(win(1)),
        }
        if name not in impl:
            raise ScreenRuntimeError(f"{name} has no vectorised form")
        return impl[name]()


def _extreme(arrays: List[np.ndarray], op: Callable[..., Any]) -> np.ndarray:
    """Greatest / Least per bar and symbol; NaN in any input gives NaN (the per-symbol `max(axis=1, skipna=False)`)."""
    out = arrays[0]
    for other in arrays[1:]:
        out = op(out, other)
    return out


def _groups(universe: List[SymbolData], base_tf: str) -> List[List[int]]:
    """Positions in `universe` of the symbols with bars, grouped by identical bar index (order kept)."""
    by_key: Dict[Tuple[int, Any, Any], List[Tuple[pd.DatetimeIndex, List[int]]]] = {}
    for pos, d in enumerate(universe):
        frame = d.frames.get(base_tf)
        if frame is None or frame.empty:
            continue
        idx = frame.index
        bucket = by_key.setdefault((len(idx), idx[0], idx[-1]), [])
        for ref, members in bucket:
            if ref is idx or ref.equals(idx):
                members.append(pos)
                break
        else:
            bucket.append((idx, [pos]))
    return [members for bucket in by_key.values() for _idx, members in bucket]


def run_panel(ast: Any, validated: Validated, universe: List[SymbolData], *, base_tf: str, params: Dict[str, Any], check_history: bool,
              per_symbol: Callable[[List[SymbolData]], List[Match]]) -> List[Match]:
    """`run_screen` for a supported screen: each index group evaluated at once; the rest through `per_symbol`."""
    out: Dict[int, Match] = {}
    rest: List[int] = []
    need = validated.lookback.get(base_tf, 0) if check_history else 0
    for members in _groups(universe, base_tf):
        if len(members) < MIN_GROUP:
            rest.extend(members)
            continue
        symbols = [universe[i].symbol for i in members]
        panel = Panel([universe[i].frames[base_tf] for i in members], [f"#{k}" for k in range(len(members))])   # symbols may repeat
        if len(panel) < need:
            for i in members:
                out[i] = Match(universe[i].symbol, False, f"not enough history on {base_tf}")
            continue
        try:
            value = _PanelEval(panel, base_tf, params).ev(ast, base_tf)
        except Exception as exc:  # noqa: BLE001 - the per-symbol runtime gives each symbol its own reason
            logger.warning("Vectorised screen fell back to per-symbol evaluation for %d symbols: %s: %s", len(members), type(exc).__name__,
                           str(exc)[:200])
            logger.debug("Vectorised screen fallback traceback", exc_info=True)
            rest.extend(members)
            continue
        last = value.iloc[-1].tolist() if isinstance(value, pd.DataFrame) else [value] * len(members)
        for i, symbol, v in zip(members, symbols, last):
            out[i] = Match(symbol, bool(v) if not (isinstance(v, float) and np.isnan(v)) else False)
    rest_set = set(rest)
    leftovers = [d for i, d in enumerate(universe) if i not in out and (i in rest_set or not _has_bars(d, base_tf))]
    fallback = iter(per_symbol(leftovers)) if leftovers else iter(())
    return [out[i] if i in out else next(fallback) for i in range(len(universe))]


def _has_bars(d: SymbolData, base_tf: str) -> bool:
    frame = d.frames.get(base_tf)
    return frame is not None and not frame.empty


__all__ = ["FLAG", "FUNCTIONS", "Panel", "supported", "run_panel"]
