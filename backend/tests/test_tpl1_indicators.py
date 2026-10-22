"""Strategy templates step 1, the add-indicator work (owner order section 7): MACD, the stochastic, WMA, HMA, the
parabolic SAR and the Supertrend direction - in app/indicators and in the ScreenQL registry as true per-bar series.

Each indicator is checked against a straightforward reference written here (plain loops over Python lists, no pandas
windows), is causal (a run on bars[0..t] gives exactly the full run's value at t, for many t), and its ScreenQL entry is
a series (not LAST_BAR_ONLY) with the bounds, the argument order and the units the validator enforces."""
import math

import numpy as np
import pandas as pd
import pytest

from app.indicators import momentum, trend, volatility
from app.quality.lookahead import synthetic_bars
from app.screener import compile_screen, parse
from app.screener.registry import FUNCTIONS, LAST_BAR_ONLY
from app.screener.runtime import SymbolData, evaluate

NEW = ("SupertrendDir", "MACD", "MACDSignal", "MACDHist", "WMA", "HMA", "StochK", "StochD", "PSAR", "PSARDir")


def _bars(seed=3, n=400, tf="15m"):
    return synthetic_bars(n, tf=tf, seed=seed)


# --- references (plain Python) ----------------------------------------------------------------------------------------

def ref_wma(xs, n):
    out = []
    for t in range(len(xs)):
        if t + 1 < n:
            out.append(math.nan)
            continue
        window = xs[t - n + 1:t + 1]
        out.append(sum((i + 1) * v for i, v in enumerate(window)) / (n * (n + 1) / 2))
    return out


def ref_hma(xs, n):
    half, root = ref_wma(xs, n // 2), ref_wma(xs, n)
    raw = [2 * a - b for a, b in zip(half, root)]
    out = []
    m = int(math.isqrt(n))
    for t in range(len(raw)):
        window = raw[t - m + 1:t + 1] if t + 1 >= m else []
        if len(window) < m or any(math.isnan(v) for v in window):
            out.append(math.nan)
        else:
            out.append(sum((i + 1) * v for i, v in enumerate(window)) / (m * (m + 1) / 2))
    return out


def ref_ema(xs, n):
    """pandas ewm(span=n, adjust=False, min_periods=n) over a series that may start with NaN: the recursion starts at the
    first value, and a value shows once n values have been seen."""
    a = 2 / (n + 1)
    out, prev, seen = [], None, 0
    for v in xs:
        if math.isnan(v):
            out.append(math.nan)
            continue
        prev = v if prev is None else a * v + (1 - a) * prev
        seen += 1
        out.append(prev if seen >= n else math.nan)
    return out


def ref_stoch(highs, lows, closes, n, smooth, d):
    raw = []
    for t in range(len(closes)):
        if t + 1 < n:
            raw.append(math.nan)
            continue
        hh, ll = max(highs[t - n + 1:t + 1]), min(lows[t - n + 1:t + 1])
        raw.append(100 * (closes[t] - ll) / (hh - ll) if hh > ll else math.nan)

    def avg(xs, m):
        return [math.nan if t + 1 < m or any(math.isnan(v) for v in xs[t - m + 1:t + 1]) else sum(xs[t - m + 1:t + 1]) / m
                for t in range(len(xs))]
    k = avg(raw, smooth)
    return k, avg(k, d)


def ref_psar(highs, lows, closes, start, step, mx):
    """Wilder's SAR as TradingView's ta.sar steps it, written as one state record per bar."""
    out, dirs = [math.nan], [math.nan]
    state = None
    for i in range(1, len(closes)):
        if state is None:
            up = closes[1] > closes[0]
            state = {"up": up, "ep": highs[1] if up else lows[1], "sar": lows[0] if up else highs[0], "af": start}
            reversed_now = True
        else:
            reversed_now = False
        sar = state["sar"] + state["af"] * (state["ep"] - state["sar"])
        old_ep = state["ep"]
        if state["up"] and sar > lows[i]:                     # the up move ends: the SAR jumps to the extreme it tracked
            state.update(up=False, ep=lows[i], af=start)
            sar = max(highs[i], old_ep)
            reversed_now = True
        elif not state["up"] and sar < highs[i]:
            state.update(up=True, ep=highs[i], af=start)
            sar = min(lows[i], old_ep)
            reversed_now = True
        if not reversed_now:
            if state["up"] and highs[i] > state["ep"]:
                state.update(ep=highs[i], af=min(state["af"] + step, mx))
            elif not state["up"] and lows[i] < state["ep"]:
                state.update(ep=lows[i], af=min(state["af"] + step, mx))
        prior = [lows[i - 1]] + ([lows[i - 2]] if i > 1 else []) if state["up"] else [highs[i - 1]] + ([highs[i - 2]] if i > 1 else [])
        sar = min([sar] + prior) if state["up"] else max([sar] + prior)
        state["sar"] = sar
        out.append(sar)
        dirs.append(1.0 if state["up"] else -1.0)
    return out, dirs


def _same(a, b, tol=1e-9):
    a, b = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    assert a.shape == b.shape
    assert np.array_equal(np.isnan(a), np.isnan(b)), np.flatnonzero(np.isnan(a) != np.isnan(b))[:5]
    ok = ~np.isnan(a)
    assert np.allclose(a[ok], b[ok], rtol=0, atol=tol), np.max(np.abs(a[ok] - b[ok]))


# --- parity with the references ----------------------------------------------------------------------------------------

@pytest.mark.parametrize("seed", [1, 2, 3])
def test_wma_hma_match_reference(seed):
    closes = _bars(seed)["close"].tolist()
    for n in (1, 2, 5, 20):
        _same(trend.wma(pd.Series(closes), n), ref_wma(closes, n))
    for n in (2, 4, 9, 16, 21):
        _same(trend.hma(pd.Series(closes), n), ref_hma(closes, n))


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_macd_matches_reference(seed):
    closes = _bars(seed)["close"].tolist()
    for fast, slow, sig in ((12, 26, 9), (5, 13, 4), (2, 3, 2)):
        out = trend.macd(pd.Series(closes), fast, slow, sig)
        line = [a - b for a, b in zip(ref_ema(closes, fast), ref_ema(closes, slow))]
        signal = ref_ema(line, sig)
        _same(out["macd"], line, 1e-7)
        _same(out["signal"], signal, 1e-7)
        _same(out["hist"], [a - b for a, b in zip(line, signal)], 1e-7)


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_stochastic_matches_reference(seed):
    df = _bars(seed)
    for n, smooth, d in ((14, 3, 3), (5, 1, 3), (9, 2, 2)):
        out = momentum.stochastic(df, n, smooth, d)
        k, dl = ref_stoch(df["high"].tolist(), df["low"].tolist(), df["close"].tolist(), n, smooth, d)
        _same(out["k"], k, 1e-9)
        _same(out["d"], dl, 1e-9)
        assert out["k"].dropna().between(0, 100).all()


def test_stochastic_without_range_has_no_value():
    flat = pd.DataFrame({"open": [5.0] * 6, "high": [5.0] * 6, "low": [5.0] * 6, "close": [5.0] * 6})
    assert momentum.stochastic(flat, 3, 1, 1)["k"].isna().all()


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_psar_matches_reference(seed):
    df = _bars(seed)
    for start, step, mx in ((0.02, 0.02, 0.2), (0.01, 0.03, 0.3), (0.1, 0.1, 0.1)):
        out = trend.parabolic_sar(df, start, step, mx)
        sar, dirs = ref_psar(df["high"].tolist(), df["low"].tolist(), df["close"].tolist(), start, step, mx)
        _same(out["sar"], sar, 1e-9)
        _same(out["dir"], dirs)


def test_psar_sits_on_the_right_side_of_price():
    df = _bars(5)
    out = trend.parabolic_sar(df)
    up = out["dir"] == 1
    assert (out["sar"][up] <= df["low"][up] + 1e-9).all() and (out["sar"][~up & out["dir"].notna()] >= df["high"][~up & out["dir"].notna()] - 1e-9).all()
    assert out["dir"].diff().abs().gt(0).sum() >= 5            # it flips on these bars (not a vacuous check)


def ref_supertrend_dir(df, period, mult):
    """The textbook Supertrend direction (final bands that only tighten, a flip when the close crosses the active band)."""
    h, l, c = df["high"].tolist(), df["low"].tolist(), df["close"].tolist()
    trs = [h[0] - l[0]] + [max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])) for i in range(1, len(c))]
    atr, prev = [], None
    for i, tr in enumerate(trs):
        prev = tr if prev is None else prev + (tr - prev) / period
        atr.append(prev if i + 1 >= period else math.nan)
    out = [math.nan] * len(c)
    fu = fl = None
    direction = 0
    for i in range(period, len(c)):
        mid = (h[i] + l[i]) / 2
        bu, bl = mid + mult * atr[i], mid - mult * atr[i]
        if fu is None:
            fu, fl = bu, bl
            direction = 1 if c[i] >= fl else -1
        else:
            fu = bu if (bu < fu or c[i - 1] > fu) else fu
            fl = bl if (bl > fl or c[i - 1] < fl) else fl
            direction = (-1 if c[i] < fl else 1) if direction == 1 else (1 if c[i] > fu else -1)
        out[i] = float(direction)
    return out


@pytest.mark.parametrize("seed", [1, 2, 3, 4])
def test_supertrend_dir_matches_reference_and_the_line(seed):
    df = _bars(seed)
    for period, mult in ((10, 3.0), (7, 2.0), (3, 1.0)):
        got = volatility.supertrend_dir(df, period, mult)
        _same(got, ref_supertrend_dir(df, period, mult))
        st = volatility.supertrend(df, period, mult)["supertrend"]
        valid = got.notna()
        # +1 exactly when the line is at or below the close, -1 when above (the line is the band of the side it is on)
        assert ((got[valid] == 1) == (st[valid] <= df["close"][valid])).all()
    assert volatility.supertrend_dir(df, 7, 2.0).diff().abs().gt(0).sum() >= 5


# --- causal: a run on bars[0..t] gives the full run's value at t --------------------------------------------------------

CAUSAL = {
    "wma": lambda f: trend.wma(f["close"], 10),
    "hma": lambda f: trend.hma(f["close"], 16),
    "macd": lambda f: trend.macd(f["close"], 12, 26, 9),
    "stoch": lambda f: momentum.stochastic(f, 14, 3, 3),
    "psar": lambda f: trend.parabolic_sar(f),
    "st_dir": lambda f: volatility.supertrend_dir(f, 10, 3.0),
}


@pytest.mark.parametrize("name", sorted(CAUSAL))
def test_truncation_no_look_ahead(name):
    df = _bars(7, n=300)
    full = CAUSAL[name](df)
    for t in range(5, len(df), 11):
        part = CAUSAL[name](df.iloc[:t + 1])
        a = np.asarray(part.iloc[-1], dtype=float)
        b = np.asarray(full.iloc[t], dtype=float)
        assert np.array_equal(np.isnan(a), np.isnan(b)) and np.allclose(a[~np.isnan(a)], b[~np.isnan(b)], atol=1e-9), (name, t)


# --- the ScreenQL entries ----------------------------------------------------------------------------------------------

def test_registered_as_series_not_last_bar_only():
    for name in NEW:
        assert name in FUNCTIONS and name not in LAST_BAR_ONLY and FUNCTIONS[name].timeframed, name


def _sym(df, tf="15m"):
    return SymbolData("S", {tf: df})


def test_screenql_values_equal_the_indicators():
    df = _bars(9)
    d = _sym(df)
    ev = lambda text: evaluate(parse(text), d, base_tf="15m")   # noqa: E731
    _same(ev("MACD(5, 13, 4)"), trend.macd(df["close"], 5, 13, 4)["macd"])
    _same(ev("MACDSignal(5, 13, 4)"), trend.macd(df["close"], 5, 13, 4)["signal"])
    _same(ev("MACDHist()"), trend.macd(df["close"])["hist"])
    _same(ev("WMA(high, 7)"), trend.wma(df["high"], 7))
    _same(ev("HMA(close, 9)"), trend.hma(df["close"], 9))
    _same(ev("StochK(9, 2)"), momentum.stochastic(df, 9, 2, 1)["k"])
    _same(ev("StochD(9, 2, 4)"), momentum.stochastic(df, 9, 2, 4)["d"])
    _same(ev("PSAR(0.01, 0.02, 0.1)"), trend.parabolic_sar(df, 0.01, 0.02, 0.1)["sar"])
    _same(ev("PSARDir()"), trend.parabolic_sar(df)["dir"])
    _same(ev("SupertrendDir(7, 2)"), volatility.supertrend_dir(df, 7, 2.0))


def test_higher_timeframe_value_is_seen_only_after_its_bar_closed():
    df = _bars(4, n=200)
    d = _sym(df)
    on_15m = evaluate(parse("SupertrendDir(5, 2)@1h"), d, base_tf="15m")
    from app.screener.runtime import bar_close, resample
    hourly = resample(df, "15m", "1h")                         # what the runtime builds from the 15m bars
    closes_1h = bar_close(hourly.index, "1h")
    closes_15 = bar_close(df.index, "15m")
    raw = volatility.supertrend_dir(hourly, 5, 2.0)
    assert on_15m.notna().sum() > 100 and set(on_15m.dropna().unique()) == {1.0, -1.0}
    for t in range(0, len(df), 5):
        seen = closes_1h <= closes_15[t]
        expected = raw[seen].iloc[-1] if seen.any() else np.nan
        got = on_15m.iloc[t]
        assert (np.isnan(expected) and np.isnan(got)) or got == expected, t


@pytest.mark.parametrize("text, fragment", [
    ("HMA(close, 1) > 0", "between 2"),
    ("MACD(26, 12, 9) > 0", "fast must be below slow"),
    ("MACD(12, 12, 9) > 0", "fast must be below slow"),
    ("MACDHist(30, 20) > 0", "fast must be below slow"),
    ("PSAR(0.3, 0.02, 0.2) < close", "start must be at most max"),
    ("PSAR(0.02, 0.5, 0.2) < close", "step must be at most max"),
    ("PSAR(0, 0.02, 0.2) < close", "between 0.001"),
    ("SupertrendDir(10, 25) == 1", "between 0.1"),
    ("SupertrendDir(10, ATR(5)) == 1", "one value for the whole series"),
    ("MACD(12, 26, 9) > close", "compares price_diff with price"),
    ("StochK(14, 3) > close", "compares index with price"),
    ("PSARDir() > RSI(14)", "compares sign with index"),
])
def test_validator_refuses(text, fragment):
    _, v = compile_screen(text, base_tf="15m")
    assert not v.ok and any(fragment in p.message for p in v.problems), [p.message for p in v.problems]


@pytest.mark.parametrize("text, shown", [
    ("MACD(26, 12, 9) > 0", "MACD: fast must be below slow"),
    ("PSAR(0.3, 0.02, 0.2) < close", "PSAR: start must be at most max"),
    ("PSARDir(0.02, 0.5, 0.2) == 1", "PSARDir: step must be at most max"),
])
def test_argument_order_has_a_copilot_template(text, shown):
    """The natural-language box shows a registry-only message for the new problem kind (never the draft's numbers)."""
    from app.screener.nl import shown_problem
    _, v = compile_screen(text, base_tf="15m")
    problem = next(p for p in v.problems if p.kind == "argument_order")
    assert shown_problem(problem) == {"kind": "argument_order", "message": shown, "pos": problem.pos}


@pytest.mark.parametrize("text", ["MACD(12, 26, 9) > MACDSignal(12, 26, 9)", "CrossAbove(MACD(), MACDSignal())", "StochK(14, 3) < 20",
                                  "CrossAbove(StochK(14, 3), StochD(14, 3, 3))", "close > HMA(close, 21)", "WMA(close, 10) > EMA(close, 20)",
                                  "PSARDir() == 1 AND PSARDir()[1] == -1", "SupertrendDir(10, 3)@1h == 1", "close > PSAR()",
                                  "PSAR(0.2, 0.2, 0.2) < close", "Count(5, SupertrendDir(10, 3) == 1) == 5"])
def test_validator_accepts(text):
    _, v = compile_screen(text, base_tf="15m")
    assert v.ok, [p.message for p in v.problems]


def test_history_needed():
    _, v = compile_screen("MACDSignal(12, 26, 9) > 0", base_tf="15m")
    assert v.lookback["15m"] >= 26 + 9 - 1                    # the signal line's first value
    _, v = compile_screen("SupertrendDir(10, 3)[1] == -1", base_tf="15m")
    assert v.lookback["15m"] >= 10 + 2                         # the line from bar `period`, one bar back
    _, v = compile_screen("StochD(14, 3, 3) > 0", base_tf="15m")
    assert v.lookback["15m"] >= 14 + 3 + 3 - 2
