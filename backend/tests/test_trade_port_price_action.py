"""Ported price-action logic (Trade@0df3e09d0aa5942e3352327d5b998c5f6939ac61): reversal (composite E2 + C1 and the #241
score mode), real vs false break, causal swings, level strength / zone events.

The Trade tests are ported as they were (tests/test_elliott_e2.py, test_price_action_candles.py, test_elliott_swings.py,
test_price_action_level_strength.py); the swing truncation tests run on the session-shaped sample market
(tests/sample_market.py) instead of Trade's NIFTY parquet. Every module also gets a no-lookahead (truncation) test:
a result at bar j computed on the full series equals the one computed on the series cut after bar j.
"""
import numpy as np
import pandas as pd
import pytest

from app.price_action import breaks as B
from app.price_action import causal_swings as W
from app.price_action import level_strength as LS
from app.price_action import pa_settings as PS
from app.price_action import reversal as RV
from tests.sample_market import SESSION_BARS, generate

S0 = PS.settings()
T0 = pd.Timestamp("2019-01-02 09:15")


def frame(rows, base=30):
    """base bars (o=100 h=105 l=95 c=100, range 10) + rows [(o, h, l, c)]."""
    bars = [(100.0, 105.0, 95.0, 100.0)] * base + [tuple(map(float, r)) for r in rows]
    ts = [T0 + pd.Timedelta(minutes=5 * i) for i in range(len(bars))]
    df = pd.DataFrame(bars, columns=["open", "high", "low", "close"])
    df.insert(0, "timestamp", ts)
    df.insert(1, "bar_end", [t + pd.Timedelta(minutes=5) for t in ts])
    return df


def bars_of(df, s=S0):
    return RV.Bars(df, B.median_range(df, s["median_range_n"]))


def mirror(df):
    m = df.copy()
    m["open"], m["high"], m["low"], m["close"] = -df["open"], -df["low"], -df["high"], -df["close"]
    return m


# --- composite reversal (Trade tests/test_elliott_e2.py) ----------------------------------------------------------------
def test_hammer_passes_and_score_components():
    df = frame([(101, 103, 88, 102)])
    j = len(df) - 1
    r = RV.evaluate(bars_of(df), j, 1, [100.0], 0.0, S0, extreme_idx=j, bars_last_subleg=1)
    assert r["ok"] and r["n"] == 1 and r["touched"] == 100.0
    exp = (0.3 * 13 / 15 + 0.3 * 14 / 15 + 0.2 * 1 / 15 + 0.2 * 1.0) / 1.0
    assert r["score"] == pytest.approx(exp)


def test_mirror_symmetry_bear_equals_bull():
    df = frame([(101, 103, 88, 102)])
    j = len(df) - 1
    a = RV.evaluate(bars_of(df), j, 1, [100.0], 0.0, S0, extreme_idx=j, bars_last_subleg=1)
    b = RV.evaluate(bars_of(mirror(df)), j, -1, [-100.0], 0.0, S0, extreme_idx=j, bars_last_subleg=1)
    assert b["ok"] and b["score"] == pytest.approx(a["score"]) and b["touched"] == -100.0 and b["n"] == a["n"]


@pytest.mark.parametrize("row,reason", [
    ((101, 103, 101, 102), RV.NO_TOUCH),
    ((101, 103, 88, 99), RV.NO_RECLAIM),
    ((101, 102, 92, 101.5), RV.WEAK),              # range 10 < 1.2 x MR
    ((101, 130, 88, 128), RV.EXHAUSTION),          # range 42 > 2.5 x MR
])
def test_single_candle_failures(row, reason):
    df = frame([row])
    r = RV.evaluate_window(bars_of(df), len(df) - 1, 1, 1, [100.0], 0.0, S0)
    assert not r["ok"] and r["reason"] == reason


def test_inv_beyond_rejects_and_mid_close_beyond_inv():
    df = frame([(101, 103, 88, 102)])
    assert RV.evaluate_window(bars_of(df), len(df) - 1, 1, 1, [100.0], 0.0, S0, inv=90.0)["reason"] == RV.INV
    df2 = frame([(100, 101, 91, 92), (92, 103, 91, 102)])
    assert RV.evaluate_window(bars_of(df2), len(df2) - 1, 2, 1, [100.0], 0.0, S0, inv=92.5)["reason"] == RV.INV


def test_touched_is_deepest_level_and_reclaim_ref():
    df = frame([(96, 97, 88, 95.5)])
    b, j = bars_of(df), len(df) - 1
    r = RV.evaluate_window(b, j, 1, 1, [100.0, 92.0], 0.0, S0)
    assert r["touched"] == 92.0 and r["reason"] != RV.NO_RECLAIM
    assert RV.evaluate_window(b, j, 1, 1, [100.0, 92.0], 0.0, S0, reclaim_ref="zone_high")["reason"] == RV.NO_RECLAIM


def test_composite_last_candle_must_close_in_trade_direction():
    df = frame([(100, 101, 88, 99), (99, 104, 98, 103), (103, 104, 101, 102)])
    assert RV.evaluate_window(bars_of(df), len(df) - 1, 3, 1, [100.0], 0.0, S0)["reason"] == RV.LAST_AGAINST


def test_indecision_follow_through_allows_n4():
    rows = [(100, 101, 88, 95), (95, 97, 93, 94), (94, 98, 93, 95.5), (95.5, 104, 95, 103)]
    df = frame(rows)
    b, j = bars_of(df), len(df) - 1
    assert RV.evaluate_window(b, j - 1, 3, 1, [94.0], 0.0, S0)["reason"] == RV.INDECISIVE
    r = RV.evaluate(b, j, 1, [94.0], 0.0, S0)
    assert r["ok"] and r["n"] == 4
    df2 = frame(rows[:3] + [(95.5, 104, 95, 95.0)])
    assert not RV.evaluate(bars_of(df2), j, 1, [94.0], 0.0, S0)["ok"]


def test_min_start_excludes_older_bars():
    df = frame([(101, 103, 88, 102)])
    j = len(df) - 1
    assert RV.evaluate_window(bars_of(df), j, 1, 1, [100.0], 0.0, S0, min_start=j + 1)["reason"] == RV.NO_DATA


def test_one_api_composite_mode_and_label_is_only_descriptive():
    df = frame([(101, 103, 88, 102)])
    r = RV.evaluate_reversal(df, 100.0, "BULLISH")
    assert r["valid"] and r["mode"] == "composite" and 0 < r["score"] <= 1 and r["score_pct"] == pytest.approx(100 * r["score"], abs=0.1)
    assert set(r["parts"]) == {"wick", "close_loc", "body"} and r["label"] == "hammer-like"
    bear = RV.evaluate_reversal(mirror(df), -100.0, "BEARISH")
    assert bear["valid"] and bear["score"] == pytest.approx(r["score"])
    bad = RV.evaluate_reversal(frame([(101, 103, 101, 102)]), 90.0, "BULLISH")         # nothing in the windows reaches 90
    assert not bad["valid"] and bad["reason"] == RV.NO_TOUCH and bad["reason_text"]


# --- failed retest break (Trade tests/test_elliott_e2.py) ---------------------------------------------------------------
RETEST_ROWS = [(93, 95, 86, 87), (87, 98, 86, 91), (91, 93, 85.5, 87)]


def test_failed_retest_confirms_break_and_off_switch():
    df = frame(RETEST_ROWS + [(100, 105, 95, 100)] * 5)
    t = 30
    s_off = PS.settings(break_retest_confirm=False)
    assert B.BreakCache(df, S0).confirm_index(t, 90.0, "below", len(df) - 1) == t + 2
    assert B.BreakCache(df, s_off).confirm_index(t, 90.0, "below", len(df) - 1) is None
    assert B.BreakCache(df.iloc[:t + 2].reset_index(drop=True), S0).confirm_index(t, 90.0, "below", t + 1) is None
    assert B.BreakCache(df.iloc[:t + 3].reset_index(drop=True), S0).confirm_index(t, 90.0, "below", t + 2) == t + 2


def test_false_break_paths_unchanged_with_retest():
    wick = frame([(96, 97, 80, 95)] + [(100, 105, 95, 100)] * 5)
    assert B.first_real_break(wick, 30, 90.0, "below", S0) is None
    rec = frame([(93, 95, 86, 87), (87, 98, 86, 97)] + [(100, 105, 95, 100)] * 5)
    assert B.BreakCache(rec, S0).confirm_index(30, 90.0, "below", len(rec) - 1) is None
    acc = frame([(93, 95, 86, 87), (87, 89, 85, 86)] + [(100, 105, 95, 100)] * 3)
    assert B.BreakCache(acc, S0).confirm_index(30, 90.0, "below", len(acc) - 1) == 31


def test_retest_needs_broken_side_start_and_close_beyond_buffer():
    df = frame([(93, 95, 86, 87), (87, 92, 86, 91), (99, 101, 87.8, 88)] + [(100, 105, 95, 100)] * 4)
    assert B.BreakCache(df, S0).confirm_index(30, 90.0, "below", len(df) - 1) is None


def test_earlier_confirm_beats_later_retest():
    df = frame([(93, 95, 86, 87), (91, 92, 89.5, 91), (91, 92, 76, 77), (82, 91, 78.5, 79)] + [(100, 105, 95, 100)] * 3)
    fn = RV.retest_fn(df, S0, B.median_range(df, 20))
    assert fn(df, 30, 90.0, "below", len(df) - 1) == 33
    assert B.BreakCache(df, S0).confirm_index(30, 90.0, "below", len(df) - 1) == 32


def test_displacement_break_and_wick_basis():
    disp = frame([(99, 100, 76, 77)] + [(100, 105, 95, 100)] * 3)                     # range 24 >= 1.2 x MR, close at the low
    assert B.first_real_break(disp, 30, 90.0, "below", S0) == 30
    assert B.BreakCache(frame([(96, 97, 80, 95)]), PS.settings(count_inv_basis="wick")).confirm_index(30, 90.0, "below", 30) == 30


# --- score100 mode (Trade tests/test_price_action_candles.py) -----------------------------------------------------------
SCORE = PS.settings(reversal_mode="score100")
L = 100.0


def _df(window, base_n=22, base=(105.0, 106.0, 104.0, 105.2)):
    rows = [base] * base_n + list(window)
    ts = pd.date_range("2026-10-05 09:00", periods=len(rows), freq="30min")
    return pd.DataFrame([{"timestamp": t, "open": o, "high": h, "low": lo, "close": c} for t, (o, h, lo, c) in zip(ts, rows)])


def _mirror_df(df, axis=210.0):
    return pd.DataFrame({"timestamp": df["timestamp"], "open": axis - df["open"], "high": axis - df["low"], "low": axis - df["high"],
                         "close": axis - df["close"]})


HAMMER = [(102.0, 103.2, 99.5, 103.0)]
ENGULF = [(103.0, 103.3, 100.5, 101.0), (100.8, 104.0, 99.8, 103.8)]
MSTAR = [(104.0, 104.2, 101.0, 101.2), (100.6, 101.0, 99.7, 100.4), (100.6, 103.9, 100.3, 103.7)]
SWEEP = [(101.5, 101.8, 99.0, 99.4), (99.4, 102.3, 99.2, 102.1)]


def test_score_hammer_engulfing_star_sweep():
    r = RV.evaluate_reversal(_df(HAMMER), L, "BULLISH", SCORE)
    assert r["valid"] and r["n"] == 1 and r["label"] == "hammer-like" and r["score"] >= 60 and r["parts"]["speed"] == 15
    e = RV.evaluate_reversal(_df(ENGULF), L, "BULLISH", SCORE)
    assert e["valid"] and e["n"] == 2 and e["label"] == "engulfing-like"
    assert {k: e["composite"][k] for k in ("open", "high", "low", "close")} == {"open": 103.0, "high": 104.0, "low": 99.8, "close": 103.8}
    assert RV.score_window(_df(ENGULF), 1, L, True, SCORE)["score"] < e["score"]
    m = RV.evaluate_reversal(_df(MSTAR), L, "BULLISH", SCORE)
    assert m["valid"] and m["n"] == 3 and m["label"] == "star-like" and m["parts"]["speed"] == 5
    sw = RV.evaluate_reversal(_df(SWEEP), L, "BULLISH", SCORE)
    assert sw["valid"] and sw["sweep"] and sw["parts"]["sweep"] == 15 and sw["label"] == "failed-breakdown-like"


@pytest.mark.parametrize("window, reason", [
    ([(102.0, 104.0, 99.8, 102.1)], RV.INDECISIVE),
    ([(101.0, 101.5, 98.5, 99.5)], RV.NO_RECLAIM),
    ([(101.5, 104.0, 100.6, 103.8)], RV.NO_TOUCH),
    ([(106.0, 106.5, 94.0, 105.0)], RV.EXHAUSTION),
    ([(100.3, 101.5, 99.9, 101.4)], RV.WEAK),
])
def test_score_rejected_windows_report_reason(window, reason):
    r = RV.evaluate_reversal(_df(window), L, "BULLISH", SCORE)
    assert not r["valid"] and r["reason"] == reason and r["reason_code"] == RV.SCORE_REASON[reason]


@pytest.mark.parametrize("window", [HAMMER, ENGULF, MSTAR, SWEEP])
def test_score_bearish_is_the_mirror(window):
    bull = RV.evaluate_reversal(_df(window), L, "BULLISH", SCORE)
    bear = RV.evaluate_reversal(_mirror_df(_df(window)), 210.0 - L, "BEARISH", SCORE)
    assert bear["valid"] == bull["valid"] and bear["score"] == bull["score"] and bear["n"] == bull["n"]


def test_completed_only_drops_the_forming_candle():
    df = _df(HAMMER)
    last = pd.Timestamp(df["timestamp"].iloc[-1])
    assert len(RV.completed_only(df, 30, last + pd.Timedelta(minutes=29))) == len(df) - 1
    assert len(RV.completed_only(df, 30, last + pd.Timedelta(minutes=30))) == len(df)


# --- no lookahead -------------------------------------------------------------------------------------------------------
def _market(days=6, seed=7):
    df = generate(SESSION_BARS * days, 24_500, seed).reset_index().rename(columns={"index": "timestamp"})
    df["timestamp"] = df["timestamp"].dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
    return df


@pytest.mark.parametrize("mode", ["composite", "score100"])
def test_reversal_is_causal(mode):
    """The verdict on bar j is the same with or without the bars after j."""
    df = W.build_frame(_market(), "5m")
    s = PS.settings(reversal_mode=mode)
    mr = B.median_range(df, 20)
    full_bars = RV.Bars(df, mr)
    checked = 0
    for j in range(40, len(df), 7):
        level = float(df["low"].iloc[j - 1])
        cut = RV.evaluate_reversal(df.iloc[: j + 1], level, "BULLISH", s)
        if mode == "composite":
            whole = RV.evaluate(full_bars, j, 1, [level], 0.0, s)
            assert (cut["valid"], cut["reason"], cut["n"]) == (whole["ok"], whole["reason"], whole["n"])
            assert cut["score"] == pytest.approx(round(whole["score"], 4))
        checked += 1
    assert checked > 10


def test_breaks_are_causal_on_a_real_looking_series():
    df = W.build_frame(_market(days=8, seed=3), "5m")
    level = float(df["close"].iloc[100])
    full = B.BreakCache(df, S0)
    for side in ("below", "above"):
        c = full.confirm_index(100, level, side, len(df) - 1)
        for t in range(100, len(df), 13):
            cut = B.BreakCache(df.iloc[: t + 1].reset_index(drop=True), S0).confirm_index(100, level, side, t)
            assert cut == (c if c is not None and c <= t else None)


def test_scan_markers_use_only_past_bars():
    df = W.build_frame(_market(), "5m")
    levels = [float(df["low"].iloc[60:].min()), float(df["high"].iloc[60:].max())]
    full = RV.scan_markers(df, levels)
    half = RV.scan_markers(df.iloc[:200], levels)
    assert [m for m in full if m["index"] < 200] == half


# --- causal swings (Trade tests/test_elliott_swings.py on the sample market) ---------------------------------------------
def _key(p):
    return (p.kind, p.price, p.ts, p.confirmed_at, p.bar_idx, p.confirmed_idx)


@pytest.mark.parametrize("mode,fixed", [("atr", False), ("pct", False), ("fractal", False), ("atr", True)])
def test_swing_truncation_invariance_and_immutability(mode, fixed):
    m1 = _market(days=10, seed=11)
    s = PS.settings(swing_mode=mode, **({"degree_tf_mode": "fixed", "degree_tf": "5m,15m,1h,1d"} if fixed else {}))
    full = W.multi_degree(m1, s)
    ts = m1["timestamp"]
    cuts = sorted(list(pd.to_datetime(ts.iloc[[700, 1400, 2300, 3100]])) +
                  [pd.Timestamp(x) for x in np.random.default_rng(11).choice(ts[ts.dt.minute % 5 != 0].to_numpy(), 4, replace=False)])
    prev = None
    for t in cuts:
        part = W.multi_degree(m1[m1["timestamp"] < t], s)
        via_now = W.multi_degree(m1, s, now=t)
        for d in full:
            exp = W.known_at(full[d]["confirmed"], t)
            assert [_key(p) for p in part[d]["confirmed"]] == [_key(p) for p in exp]
            assert [_key(p) for p in via_now[d]["confirmed"]] == [_key(p) for p in exp]
            tp, tn = part[d]["tentative"], via_now[d]["tentative"]
            assert (tp is None and tn is None) or (tp.kind, tp.price, tp.ts) == (tn.kind, tn.price, tn.ts)
            assert (part[d]["frame"]["bar_end"] <= t).all()
        if prev is not None:
            for d in full:
                assert part[d]["confirmed"][:len(prev[d]["confirmed"])] == prev[d]["confirmed"]
        prev = part


def test_pivot_semantics_and_tentative():
    md = W.multi_degree(_market(days=10, seed=11), S0)
    for d, v in md.items():
        fr, conf = v["frame"], v["confirmed"]
        assert all(a.kind != b.kind for a, b in zip(conf, conf[1:]))
        for p in conf:
            assert p.status == "confirmed" and p.confirmed_idx > p.bar_idx
            assert p.confirmed_at == fr["bar_end"].iloc[p.confirmed_idx] > p.ts
            assert p.price == fr["high" if p.kind == "H" else "low"].iloc[p.bar_idx]
        if conf:
            t = v["tentative"]
            assert t is None or (t.status == "tentative" and t.confirmed_at is None and t.kind != conf[-1].kind)
    counts = [len(md[d]["confirmed"]) for d in sorted(md)]
    assert counts == sorted(counts, reverse=True) and counts[0] > counts[-1]


def test_fractal_legs_point_the_right_way():
    for d, v in W.multi_degree(_market(days=10, seed=11), PS.settings(swing_mode="fractal")).items():
        conf = v["confirmed"]
        for a, b in zip(conf, conf[1:]):
            assert a.kind != b.kind and b.bar_idx > a.bar_idx
            assert (b.price > a.price) if b.kind == "H" else (b.price < a.price)


def test_tentative_tie_picks_last_bar_like_zigzag():
    st = pd.date_range("2019-01-02 09:15", periods=8, freq="5min")
    hi = [10, 12, 11, 10, 9, 9.5, 9, 10]
    fr = pd.DataFrame({"timestamp": st, "bar_end": st + pd.Timedelta(minutes=5), "open": hi, "high": hi,
                       "low": [x - 0.5 for x in hi], "close": hi})
    h = W.Pivot(0, "H", 12.0, 1, st[1], 3, st[3], "confirmed")
    t = W.tentative_pivot(fr, [h], 0)
    assert t.kind == "L" and t.price == 8.5 and t.bar_idx == 6


def test_build_frame_drops_unclosed_bar_and_is_session_aligned():
    m1 = _market(days=2)
    d = m1[m1["timestamp"] < m1["timestamp"].iloc[0] + pd.Timedelta(minutes=12)]          # 09:15..09:26 - the 09:25 bar is open
    fr = W.build_frame(d, "5m")
    assert list(fr["bar_end"].dt.strftime("%H:%M")) == ["09:20", "09:25"]
    f15 = W.build_frame(m1, "15m")
    assert f15["timestamp"].dt.strftime("%H:%M").iloc[0] == "09:15" and f15["bar_end"].dt.strftime("%H:%M").iloc[-1] == "15:30"


def test_atr_is_causal():
    px = 100 + np.cumsum(np.random.default_rng(3).normal(0, 1, 200))
    df = pd.DataFrame({"high": px + 1, "low": px - 1, "close": px})
    a_full, a_cut = W.atr(df, 14), W.atr(df.iloc[:120], 14)
    assert np.isnan(a_full[:13]).all() and np.allclose(a_full[:120], a_cut, equal_nan=True)


def test_similarity_balance():
    assert W.similar_degree((None, None, 100, 10), (None, None, 30, 25), 1 / 3)
    assert not W.similar_degree((None, None, 100, 10), (None, None, 30, 40), 1 / 3)
    assert not W.similar_degree((None, None, 100, 50), (None, None, 20, 5), 1 / 3)
    assert W.similar_degree((None, None, 100, 50), (None, None, 20, 20), 1 / 3)


def _tf_frame(tf, start, n):
    st = pd.date_range(start, periods=n, freq=f"{PS.TF_MIN[tf]}min")
    return pd.DataFrame({"timestamp": st, "bar_end": st + pd.Timedelta(minutes=PS.TF_MIN[tf])})


def test_auto_tf_smallest_in_range_and_closed_only():
    frames = {tf: _tf_frame(tf, "2019-01-02 09:15", 400 // PS.TF_MIN[tf] * 5) for tf in ("5m", "15m", "30m", "1h")}
    a, b = pd.Timestamp("2019-01-02 09:15"), pd.Timestamp("2019-01-02 12:15")
    s = PS.settings(auto_tfs="5m,15m,30m,1h")
    tf, c, ok = W.auto_tf(frames, a, b, s)
    assert c["5m"] == 36 and tf == "5m" and ok
    tf, c, ok = W.auto_tf(frames, a, pd.Timestamp("2019-01-02 15:15"), s)
    assert tf == "15m" and c["15m"] == 24 and ok
    tf, c, _ = W.auto_tf(frames, a, pd.Timestamp("2019-01-02 15:22"), s)
    assert c["15m"] == 24
    tf, _, ok = W.auto_tf(frames, a, pd.Timestamp("2019-01-02 09:35"), s)
    assert tf == "5m" and not ok
    tf, c, ok = W.auto_tf(frames, a, pd.Timestamp("2019-01-02 15:15"), PS.settings(tf_bars_min=8, tf_bars_max=10, auto_tfs="5m,15m,30m,1h"))
    assert tf == "30m" and not ok
    with pytest.raises(ValueError):
        W.auto_tf(frames, a, b, S0)


def test_settings_validation():
    c, e = PS.validate({"swing_atr_mult": "1,2,4,8", "degree_levels": "4"})
    assert c["swing_atr_mult"] == [1.0, 2.0, 4.0, 8.0] and not e
    c, e = PS.validate({"swing_atr_mult": "3,2,4,8"})
    assert c["swing_atr_mult"] == PS.DEFAULTS["swing_atr_mult"] and e
    c, e = PS.validate({"strength_min": 3.0, "reversal_mode": "names", "nope": 1})
    assert c["strength_min"] == 1.2 and c["reversal_mode"] == "composite" and len(e) == 3
    with pytest.raises(ValueError):
        PS.settings(strength_min=3.0)


# --- level strength and zone events (Trade tests/test_price_action_level_strength.py) -----------------------------------
def _zone_df(closes, lows=None, highs=None, opens=None):
    n = len(closes)
    ts = pd.date_range("2026-01-05 09:15", periods=n, freq="15min")
    c = np.asarray(closes, float)
    o = np.asarray(opens if opens is not None else c, float)
    h = np.asarray(highs if highs is not None else np.maximum(o, c) + 1, float)
    lo = np.asarray(lows if lows is not None else np.minimum(o, c) - 1, float)
    return pd.DataFrame({"timestamp": ts, "open": o, "high": h, "low": lo, "close": c})


SUPPORT = {"low": 99.0, "high": 101.0, "kind": "SUPPORT"}


def test_zone_events_sweep_failed_and_break():
    base = [110.0] * 25
    sweep = _zone_df(base + [105, 102], lows=[109] * 25 + [104, 98], highs=[111] * 25 + [106, 103])
    ev = LS.zone_events(SUPPORT, sweep, 25, len(sweep) - 1)
    assert [e["type"] for e in ev] == [LS.SWEEP]
    failed = _zone_df(base + [98, 100, 104])
    ev = LS.zone_events(SUPPORT, failed, 25, len(failed) - 1)
    assert ev[0]["type"] == LS.FAILED_BREAKOUT and ev[0]["known_at"] == 27
    broke = _zone_df(base + [98, 96, 95])
    ev = LS.zone_events(SUPPORT, broke, 25, len(broke) - 1)
    assert ev[0]["type"] == LS.BREAK and ev[0]["bar"] == 25 and ev[0]["known_at"] == 27


def test_zone_break_cascade_and_no_decision_without_bars():
    base = [110.0] * 25
    casc = _zone_df(base + [90, 89, 88], opens=base + [109, 90, 89])                   # a big bearish body through the zone
    assert LS.zone_events(SUPPORT, casc, 25, 27)[0]["type"] == LS.BREAK_CASCADE
    early = _zone_df(base + [98, 96])
    assert LS.zone_events(SUPPORT, early, 25, 26) == []                                # BREAK needs bar 27 to exist


def test_zone_events_are_causal():
    df = _zone_df([110.0] * 25 + [98, 100, 104, 103, 98, 96, 95, 94])
    full = LS.zone_events(SUPPORT, df, 25, len(df) - 1)
    for end in range(25, len(df)):
        cut = LS.zone_events(SUPPORT, df.iloc[: end + 1], 25, end)
        assert cut == [e for e in full if e["known_at"] <= end]


def test_strength_features_and_score():
    closes = [110.0] * 20 + [100.0, 100.5, 100.2] + [108.0, 112.0, 115.0] + [114.0] * 10 + [101.5, 104.0]
    df = _zone_df(closes)
    zone = {"low": 99.0, "high": 101.5, "kind": "DEMAND", "formed_at": df["timestamp"].iloc[22]}
    f = LS.strength_features(zone, df, len(df) - 1)
    assert f["origin_known"] and f["departure_mr"] > 1 and f["base_bars"] >= 1 and f["touches"] == 1 and not f["role_reversal"]
    assert 0 <= LS.strength_score(f) <= 100
    early = LS.strength_features(zone, df, 15)                                          # the zone did not exist at bar 15
    assert not early["origin_known"] and early["departure_mr"] is None
    assert LS.round_distance(24_950, "NIFTY") == 50 and LS.round_distance(51_250, "BANKNIFTY") == 250


# --- API (chart annotations) ----------------------------------------------------------------------------------------------
def test_reversal_endpoints_validate_settings_and_return_plain_json():
    from tests.test_auth_api import _register, client
    token = _register("pa-reversal@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    bars = []
    t0 = pd.Timestamp("2026-10-05 09:15")
    closes = [110, 108, 106, 104, 102, 101, 100.5, 100.2, 103, 104.5]
    for i, c in enumerate(closes):
        o = closes[i - 1] if i else c
        bars.append({"timestamp": (t0 + pd.Timedelta(minutes=15 * i)).isoformat(), "open": o, "high": max(o, c) + 0.3,
                     "low": min(o, c) - (2.5 if i == 8 else 0.3), "close": c, "volume": 1000})
    r = client.post("/api/price-action/reversal", json={"candles": bars, "level": 100.0, "direction": "BULLISH"}, headers=headers)
    assert r.status_code == 200 and set(r.json()) >= {"valid", "score", "parts", "reason", "label"}
    bad = client.post("/api/price-action/reversal", json={"candles": bars, "level": 100.0, "direction": "BULLISH",
                                                          "settings": {"nope": 1}}, headers=headers)
    assert bad.status_code == 400 and "unknown setting" in bad.json()["detail"]
    m = client.post("/api/price-action/reversal-markers", json={"candles": bars, "levels": [100.0]}, headers=headers)
    assert m.status_code == 200 and isinstance(m.json(), list)
    assert client.post("/api/price-action/reversal", json={"candles": bars, "level": 100.0, "direction": "UP"}, headers=headers).status_code == 422
