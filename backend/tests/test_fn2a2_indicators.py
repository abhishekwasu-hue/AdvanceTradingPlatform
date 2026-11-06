"""FN2-a (part 2): Keltner channel, stochastic RSI, triangular moving average and Ichimoku - in app/indicators and in the
ScreenQL registry as true per-bar series.

Each indicator is checked against a straightforward reference written here (plain loops over Python lists, no pandas
windows), is causal (a run on bars[0..t] gives exactly the full run's value at t, for many t), and its ScreenQL entry is
a series with the units, the literal-only settings and the history need the validator enforces. Ichimoku's leading
spans are checked to read nothing after the bar `shift` bars before (changing every later bar leaves them unchanged)."""
import math

import numpy as np
import pandas as pd
import pytest

from app.indicators import momentum, trend, volatility
from app.quality.lookahead import synthetic_bars
from app.screener import compile_screen, parse
from app.screener.registry import FUNCTIONS, LAST_BAR_ONLY, READS_HISTORY
from app.screener.runtime import SymbolData, evaluate

NEW = ("KeltnerUpper", "KeltnerMid", "KeltnerLower", "StochRSI", "TMA", "IchimokuTenkan", "IchimokuKijun", "IchimokuSpanA",
       "IchimokuSpanB")


def _bars(seed=3, n=400, tf="15m"):
    return synthetic_bars(n, tf=tf, seed=seed)


def _ev(text, df, tf="15m"):
    return evaluate(parse(text), SymbolData("S", {tf: df}), base_tf=tf)


def _same(got, want, tol=1e-9):
    a, b = np.asarray(got, dtype=float), np.asarray(want, dtype=float)
    assert a.shape == b.shape
    assert (np.isnan(a) == np.isnan(b)).all(), np.flatnonzero(np.isnan(a) != np.isnan(b))[:5]
    ok = ~np.isnan(a)
    assert ok.any() and np.allclose(a[ok], b[ok], rtol=0, atol=tol), np.abs(a[ok] - b[ok]).max()


# --- references (plain Python) ----------------------------------------------------------------------------------------

def ref_ema(xs, n):
    """ewm(span=n, adjust=False, min_periods=n) over values without gaps: seeded with the first value."""
    a, out, prev = 2 / (n + 1), [], None
    for i, v in enumerate(xs):
        prev = v if prev is None else a * v + (1 - a) * prev
        out.append(prev if i + 1 >= n else math.nan)
    return out


def ref_true_range(h, lo, c):
    return [h[0] - lo[0]] + [max(h[i] - lo[i], abs(h[i] - c[i - 1]), abs(lo[i] - c[i - 1])) for i in range(1, len(c))]


def ref_tma(xs, n):
    """The triangular weights written out: 1, 2, .., peak, .., 2, 1 over n bars (n odd: peak (n + 1) / 2 once; n even:
    the two middle bars weigh n / 2)."""
    w = [min(i + 1, n - i) for i in range(n)]
    out = []
    for t in range(len(xs)):
        if t + 1 < n:
            out.append(math.nan)
            continue
        window = xs[t - n + 1:t + 1]
        out.append(sum(a * b for a, b in zip(w, window)) / sum(w))
    return out


def ref_stoch(xs, n):
    out = []
    for t in range(len(xs)):
        window = xs[t - n + 1:t + 1] if t + 1 >= n else []
        if len(window) < n or any(math.isnan(v) for v in window):
            out.append(math.nan)
            continue
        lo, hi = min(window), max(window)
        out.append(100 * (xs[t] - lo) / (hi - lo) if hi > lo else math.nan)
    return out


def ref_mid(h, lo, n):
    return [math.nan if t + 1 < n else (max(h[t - n + 1:t + 1]) + min(lo[t - n + 1:t + 1])) / 2 for t in range(len(h))]


def ref_shift(xs, k):
    return [math.nan] * min(k, len(xs)) + list(xs[:max(len(xs) - k, 0)])


def _cols(df):
    return [list(df[c].astype(float)) for c in ("high", "low", "close")]


# --- the indicators against the references ----------------------------------------------------------------------------

@pytest.mark.parametrize("n,k", [(20, 2.0), (10, 1.5), (3, 1.0)])
def test_keltner_matches_the_reference(n, k):
    df = _bars()
    h, lo, c = _cols(df)
    mid = ref_ema(c, n)
    width = ref_ema(ref_true_range(h, lo, c), n)
    got = volatility.keltner(df, n, k)
    _same(got["mid"], mid)
    _same(got["upper"], [m + k * w for m, w in zip(mid, width)])
    _same(got["lower"], [m - k * w for m, w in zip(mid, width)])
    assert got["mid"].iloc[:n - 1].isna().all() and not np.isnan(got["mid"].iloc[n - 1])


@pytest.mark.parametrize("n", [14, 5, 2])
def test_stoch_rsi_matches_the_reference(n):
    df = _bars(seed=5)
    r = list(momentum.rsi(df["close"], n))
    got = momentum.stoch_rsi(df["close"], n)
    _same(got, ref_stoch(r, n))
    assert got.iloc[:2 * n - 1].isna().all() and not np.isnan(got.iloc[2 * n - 1])     # the first value needs 2 x n bars
    assert got.dropna().between(0, 100).all()


def test_stoch_rsi_has_no_value_when_the_rsi_did_not_move():
    up = pd.Series(np.arange(1.0, 40.0))                       # only gains: RSI 100 throughout, no range
    assert momentum.stoch_rsi(up, 5).isna().all()


@pytest.mark.parametrize("n", [1, 2, 3, 4, 9, 10, 21])
def test_tma_is_the_triangular_weighting(n):
    xs = list(_bars(seed=9)["close"].astype(float))
    got = trend.tma(pd.Series(xs), n)
    _same(got, ref_tma(xs, n))
    assert got.iloc[:n - 1].isna().all() and not np.isnan(got.iloc[n - 1])


@pytest.mark.parametrize("settings", [(9, 26, 52, 26), (3, 6, 12, 5), (5, 5, 5, 1)])
def test_ichimoku_matches_the_reference(settings):
    t_n, k_n, b_n, d = settings
    df = _bars(seed=11)
    h, lo, _ = _cols(df)
    tenkan, kijun = ref_mid(h, lo, t_n), ref_mid(h, lo, k_n)
    got = trend.ichimoku(df, t_n, k_n, b_n, d)
    _same(got["tenkan"], tenkan)
    _same(got["kijun"], kijun)
    _same(got["span_a"], ref_shift([(a + b) / 2 for a, b in zip(tenkan, kijun)], d))
    _same(got["span_b"], ref_shift(ref_mid(h, lo, b_n), d))
    assert set(got.columns) == {"tenkan", "kijun", "span_a", "span_b"}           # no lagging span (Chikou)


# --- causality --------------------------------------------------------------------------------------------------------

CAUSAL = {
    "keltner": lambda f: volatility.keltner(f, 20, 2.0),
    "stoch_rsi": lambda f: momentum.stoch_rsi(f["close"], 14).to_frame("v"),
    "tma": lambda f: trend.tma(f["close"], 10).to_frame("v"),
    "ichimoku": lambda f: trend.ichimoku(f),
}


@pytest.mark.parametrize("name", sorted(CAUSAL))
def test_a_prefix_gives_the_full_runs_values(name):
    df = _bars(seed=13)
    full = CAUSAL[name](df)
    for t in (0, 1, 8, 25, 26, 51, 52, 77, 78, 150, 300, len(df) - 1):
        part = CAUSAL[name](df.iloc[:t + 1])
        pd.testing.assert_frame_equal(part, full.iloc[:t + 1], check_exact=True)


def test_ichimoku_spans_read_no_bar_after_shift_bars_before():
    """No look-ahead, beyond the prefix test: the spans on bar t are fixed once bar t - shift has closed - every later
    bar (t - shift + 1 .. t and after) can change and the spans on bar t stay the same. The chart's "26 bars ahead"
    is read here as the value computed 26 bars before, never a value from a later bar."""
    df = _bars(seed=17)
    d = 26
    base = trend.ichimoku(df)
    rng = np.random.default_rng(1)
    for t in (90, 150, 260, len(df) - 1):
        moved = df.copy()
        later = moved.index[t - d + 1:]
        bump = rng.normal(0, 5, len(later))
        for col in ("open", "high", "low", "close"):
            moved.loc[later, col] = moved.loc[later, col] + bump
        moved["high"] = moved[["open", "high", "low", "close"]].max(axis=1)
        moved["low"] = moved[["open", "high", "low", "close"]].min(axis=1)
        got = trend.ichimoku(moved)
        for col in ("span_a", "span_b"):
            assert got[col].iloc[t] == base[col].iloc[t], (col, t)
        # and the spans on bar t are exactly the lines computed on bar t - shift
        assert base["span_a"].iloc[t] == (base["tenkan"].iloc[t - d] + base["kijun"].iloc[t - d]) / 2.0
        assert base["span_b"].iloc[t] == trend.ichimoku(df.iloc[:t - d + 1], kijun=52)["kijun"].iloc[-1]
    assert base["span_a"].iloc[:d + 25].isna().all() and not np.isnan(base["span_a"].iloc[d + 25])  # kijun 26 + shift 26
    assert base["span_b"].iloc[:d + 51].isna().all() and not np.isnan(base["span_b"].iloc[d + 51])  # span_b 52 + shift 26


def test_the_screenql_spans_equal_the_lines_shift_bars_before():
    df = _bars(seed=19)
    tenkan, kijun = _ev("IchimokuTenkan(9)", df), _ev("IchimokuKijun(26)", df)
    _same(_ev("IchimokuSpanA(9, 26, 26)", df), ((tenkan + kijun) / 2.0).shift(26))
    _same(_ev("IchimokuSpanB(52, 26)", df), _ev("IchimokuKijun(52)", df).shift(26))
    _same(_ev("IchimokuSpanA()", df), _ev("IchimokuSpanA(9, 26, 26)", df))
    _same(_ev("IchimokuSpanB()", df), _ev("IchimokuSpanB(52, 26)", df))


# --- the ScreenQL entries ---------------------------------------------------------------------------------------------

def test_the_screenql_entries_are_the_indicator_library():
    df = _bars(seed=21)
    kc = volatility.keltner(df, 14, 1.5)
    _same(_ev("KeltnerUpper(14, 1.5)", df), kc["upper"])
    _same(_ev("KeltnerMid(14, 1.5)", df), kc["mid"])
    _same(_ev("KeltnerLower(14, 1.5)", df), kc["lower"])
    _same(_ev("KeltnerUpper()", df), volatility.keltner(df, 20, 2.0)["upper"])
    _same(_ev("KeltnerMid(20)", df), _ev("EMA(close, 20)", df))                       # the middle line is EMA(close, n)
    _same(_ev("StochRSI(9)", df), momentum.stoch_rsi(df["close"], 9))
    _same(_ev("StochRSI()", df), momentum.stoch_rsi(df["close"], 14))
    _same(_ev("TMA(close, 10)", df), trend.tma(df["close"], 10))
    _same(_ev("TMA(close, 6)", df), _ev("SMA(SMA(close, 3), 4)", df))
    ich = trend.ichimoku(df, 7, 20, 40, 15)
    _same(_ev("IchimokuTenkan(7)", df), ich["tenkan"])
    _same(_ev("IchimokuKijun(20)", df), ich["kijun"])
    _same(_ev("IchimokuSpanA(7, 20, 15)", df), ich["span_a"])
    _same(_ev("IchimokuSpanB(40, 15)", df), ich["span_b"])


def test_the_registry_entries():
    for name in NEW:
        spec = FUNCTIONS[name]
        assert name not in LAST_BAR_ONLY and spec.timeframed and spec.kind == "factor", name
    assert {FUNCTIONS[n].unit for n in NEW if n not in ("StochRSI", "TMA")} == {"price"}
    assert FUNCTIONS["StochRSI"].unit == "index" and FUNCTIONS["TMA"].unit == "same" and "TMA" in READS_HISTORY
    for name in ("KeltnerUpper", "KeltnerMid", "KeltnerLower"):                     # k is read once, like Bollinger's
        assert [a.name for a in FUNCTIONS[name].args if a.literal_only] == ["k"]
        assert FUNCTIONS[name].args[0].default == 20 and FUNCTIONS[name].args[1].default == 2.0


@pytest.mark.parametrize("text,bars", [
    ("StochRSI(14) > 80", 28), ("StochRSI(5) > 80", 10), ("TMA(close, 10) > close", 10), ("KeltnerUpper(20, 2) < close", 20),
    ("IchimokuTenkan(9) > close", 9), ("IchimokuKijun() > close", 26), ("IchimokuSpanB(52, 26) > close", 78),
    ("IchimokuSpanB() > close", 78), ("IchimokuSpanA(9, 26, 26) > close", 61),
])
def test_the_history_each_one_needs(text, bars):
    ast, v = compile_screen(text, base_tf="5m")
    assert v.ok, v.problems
    assert v.lookback["5m"] == bars


@pytest.mark.parametrize("text,why", [
    ("KeltnerUpper(20, ATR(14) / 10) > close", "not an expression"),
    ("KeltnerMid(20, RSI(14)) > close", "not an expression"),
    ("StochRSI(14) > close", "compares index with price"),
    ("IchimokuTenkan(9) > RSI(14)", "compares price with index"),
    ("TMA(PCR(), 3) > 1", "last bar only"),
])
def test_the_validator_refuses(text, why):
    _, v = compile_screen(text, base_tf="5m")
    assert not v.ok and any(why in p.message for p in v.problems), v.problems


@pytest.mark.parametrize("text", ["close > KeltnerUpper(20, 2)", "CrossAbove(close, KeltnerLower(20, $k))", "StochRSI(14) < 20",
                                  "CrossAbove(StochRSI(14), 20)", "SMA(StochRSI(14), 3) > 80", "close > TMA(close, 20)",
                                  "CrossAbove(IchimokuTenkan(9), IchimokuKijun(26))",
                                  "close > Greatest(IchimokuSpanA(), IchimokuSpanB())", "IchimokuSpanA()[1] < IchimokuSpanB()[1]",
                                  "close > IchimokuKijun(26)@1h"])
def test_typical_screens_validate(text):
    assert compile_screen(text, base_tf="5m", params={"k": 1.5} if "$k" in text else {})[1].ok
