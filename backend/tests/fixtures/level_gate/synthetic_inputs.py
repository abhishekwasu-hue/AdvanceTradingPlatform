"""The synthetic, seeded inputs of the level-gate goldens (no market data): 5-minute IST sessions, candidate areas and
hand-made Daily states. make_golden.py records Trade's results on them; the tests that only need inputs build them here
(tests/test_n1_x1_scan_then.py, tests/test_n1_l1_level_gate.py), so they run without the golden file (a Trade output
kept in the private fixtures store, docs/PUBLIC_REPO_POLICY.md section 5). make_golden.py imports this file from its own
folder.
"""
from types import SimpleNamespace

import numpy as np
import pandas as pd

# The golden's series, in order: (seed, volume) - True / False, or a special series ("holes", "gap", "short").
SERIES_SPECS = [(11, True), (12, True), (13, True), (14, False), (15, True), (16, True), (17, False), (18, True),
                (19, True), (20, True), (21, "holes"), (22, "gap"), (23, "short")]


def session_bars(seed, days=3, per_day=75, volume=True, start="2026-09-21"):
    """5-minute IST bars (timestamp = bar start, naive IST), a random walk with regime drifts, pullbacks into old
    extremes and occasional wide drives; volume follows the body size (with noise)."""
    rng = np.random.default_rng(seed)
    rows = []
    price = 22000.0 + 500 * rng.standard_normal()
    for day in pd.bdate_range(start, periods=days):
        drift = rng.choice([-1.0, 1.0]) * rng.uniform(0.2, 1.2)
        for i in range(per_day):
            ts = day + pd.Timedelta(hours=9, minutes=15 + 5 * i)
            if i % 20 == 0:
                drift = -drift if rng.random() < 0.6 else drift
            shock = rng.standard_normal() * 9.0 + drift * 3.0
            if rng.random() < 0.06:
                shock *= rng.uniform(2.5, 4.0)                            # a wide drive
            o = price + rng.standard_normal() * 2.0
            c = o + shock
            h = max(o, c) + abs(rng.standard_normal()) * 5.0
            lo = min(o, c) - abs(rng.standard_normal()) * 5.0
            v = (1000 + 80 * abs(c - o)) * rng.uniform(0.5, 1.6)
            rows.append({"timestamp": ts, "open": round(o, 2), "high": round(h, 2), "low": round(lo, 2), "close": round(c, 2),
                         "volume": round(v, 0)})
            price = c
    df = pd.DataFrame(rows)
    return df if volume else df.drop(columns=["volume"])


def holes_bars(seed):
    """Volume partly zero and NaN (a feed gap)."""
    df = session_bars(seed)
    rng = np.random.default_rng(seed)
    vol = df["volume"].to_numpy(float)
    vol[rng.choice(len(vol), 40, replace=False)] = 0.0
    vol[rng.choice(len(vol), 25, replace=False)] = np.nan
    return df.assign(volume=vol), []


def gap_bars(seed):
    """An overnight gap through a demand area's far edge at the session start (the arrival candle opens below it)."""
    base = session_bars(seed, days=2)
    rng = np.random.default_rng(seed)
    prev = float(base["close"].iloc[-1])
    mr = float((base["high"] - base["low"]).median())
    lo, hi = round(prev - 2.0 * mr, 2), round(prev - 1.0 * mr, 2)
    day = pd.Timestamp(base["timestamp"].iloc[-1]).normalize() + pd.Timedelta(days=1)
    rows, price = [], lo - 0.5 * mr
    first = {"timestamp": day + pd.Timedelta(hours=9, minutes=15), "open": round(lo - 0.6 * mr, 2), "high": round((lo + hi) / 2, 2),
             "low": round(lo - mr, 2), "close": round(price, 2), "volume": 5000.0}
    rows.append(first)
    for i in range(1, 75):
        o = price + rng.standard_normal() * 2.0
        c = o + rng.standard_normal() * 9.0
        rows.append({"timestamp": day + pd.Timedelta(hours=9, minutes=15 + 5 * i), "open": round(o, 2),
                     "high": round(max(o, c) + abs(rng.standard_normal()) * 5, 2), "low": round(min(o, c) - abs(rng.standard_normal()) * 5, 2),
                     "close": round(c, 2), "volume": round((1000 + 80 * abs(c - o)) * rng.uniform(0.5, 1.6), 0)})
        price = c
    gap_area = (lo, hi)
    return pd.concat([base, pd.DataFrame(rows)], ignore_index=True), [gap_area, (round(lo + 0.3 * mr, 2), round(hi + 0.3 * mr, 2))]


def candidate_areas(df, start, rng, k=10):
    """Areas around swing extremes BEFORE today's session and inside today's range: demand under lows, supply over highs."""
    hist = df.iloc[:start]
    today = df.iloc[start:]
    lo_t, hi_t = float(today["low"].min()), float(today["high"].max())
    ref = hist if len(hist) else today                                   # a one-session series has no history
    mr = float((ref["high"] - ref["low"]).median())
    out = []
    picks = rng.choice(len(today), size=min(k, len(today)), replace=False)
    for p in sorted(int(x) for x in picks):
        bar = today.iloc[p]
        base = float(bar["low"]) if rng.random() < 0.5 else float(bar["high"])
        width = mr * rng.uniform(0.2, 0.8)
        out.append((round(base - width / 2, 2), round(base + width / 2, 2)))
    out.append((round(lo_t - 3 * mr, 2), round(lo_t - 2.5 * mr, 2)))      # far below today
    out.append((round(hi_t + 2.5 * mr, 2), round(hi_t + 3 * mr, 2)))      # far above today
    return out


def state(trend, band=None, phase=None, counter=None, protected=None, origin_risk=False, mature=False):
    return SimpleNamespace(trend=trend, band=band, phase=phase, counter=counter,
                           protected=None if protected is None else SimpleNamespace(price=protected),
                           origin_risk=origin_risk, mature=mature)


def states_for(df):
    mid = float(df["close"].iloc[-1])
    return {
        "up": state("UP", phase="impulse", protected=mid - 400.1234),
        "down": state("DOWN", phase="impulse", protected=mid + 400.5678),
        "range": state("RANGE", band=(mid - 120.0, mid + 120.0)),
        "neutral": state("NEUTRAL"),
        "unknown": state("UNKNOWN"),
        "up_origin_broken": state("UP", phase="origin_broken"),
        "down_origin_risk": state("DOWN", origin_risk=True),
        "up_parent_correction": state("UP", counter="correction"),
        "range_no_band": state("RANGE"),
    }
