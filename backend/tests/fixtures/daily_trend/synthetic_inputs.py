"""The synthetic, seeded Daily series of the Daily-trend goldens (no market data) and the fold's setting variants.
make_golden.py records Trade's results on them; tests/test_n1_l2_daily_trend.py builds them here too, so the tests that
need only bars (no look-ahead, refused settings) run without the golden file (a Trade output kept in the private
fixtures store, docs/PUBLIC_REPO_POLICY.md section 5). make_golden.py imports this file from its own folder.
"""
import numpy as np
import pandas as pd

VARIANTS = {"default": {}, "pivot3": {"daily_pivot_n": 3}, "dc": {"daily_swing_method": "dc"},
            "ratio": {"corr_degree_rule": "ratio"}, "leg_origin": {"q49_impulse_origin": "leg"},
            "no_gates": {"daily_parent_gate": False, "daily_start_nature_gate": False, "range_after_trend": False}}

# The random-walk series, in golden order (series 0-5); the EXTRA series follow them.
SEEDS = [101, 102, 103, 104, 105, 106]


def daily_series(seed, n=1000, start="2010-01-04", sideways=0.15, flip=0.25):
    """Nested legs: a primary direction lasting months, with impulse runs and corrections, plus noise and gaps."""
    rng = np.random.default_rng(seed)
    price = 10000.0 * rng.uniform(0.8, 1.6)
    rows = []
    days = pd.bdate_range(start, periods=n)
    i = 0
    primary = rng.choice([-1, 1])
    while i < n:
        if rng.random() < flip:
            primary = -primary
        for sub in range(int(rng.integers(3, 8))):
            impulse = sub % 2 == 0
            length = int(rng.integers(4, 18) if impulse else rng.integers(3, 12))
            drift = primary * (rng.uniform(0.004, 0.012) if impulse else -rng.uniform(0.002, 0.008))
            if rng.random() < sideways:
                drift = rng.uniform(-0.0015, 0.0015)                    # sideways
            for _ in range(length):
                if i >= n:
                    break
                vol = rng.uniform(0.006, 0.016)
                o = price * (1 + rng.normal(0, 0.003) + (rng.normal(0, 0.01) if rng.random() < 0.05 else 0.0))
                c = o * (1 + drift + rng.normal(0, vol))
                h = max(o, c) * (1 + abs(rng.normal(0, vol / 2)))
                lo = min(o, c) * (1 - abs(rng.normal(0, vol / 2)))
                rows.append({"timestamp": days[i], "open": round(o, 2), "high": round(h, 2), "low": round(lo, 2), "close": round(c, 2)})
                price = c
                i += 1
    return pd.DataFrame(rows)


def _closes(pts):
    closes, price = [], pts[0][1]
    for bars, level in pts[1:]:
        for k in range(1, bars + 1):
            closes.append(price + (level - price) * k / bars)
        price = level
    return closes


def range_after_break(seed):
    """Hand-built: an UP trend, a drop through its origin, then a range with about-equal highs and lows (Q27)."""
    rng = np.random.default_rng(seed)
    pts, lvl = [(0, 1000.0)], 1000.0
    for _ in range(int(rng.integers(3, 6))):
        lvl *= 1 + rng.uniform(0.06, 0.12)
        pts.append((int(rng.integers(6, 12)), lvl))
        lvl *= 1 - rng.uniform(0.02, 0.05)
        pts.append((int(rng.integers(3, 7)), lvl))
    top = lvl * 1.08
    pts.append((8, top))
    low = top * (1 - rng.uniform(0.18, 0.28))
    pts.append((int(rng.integers(5, 10)), low))
    hi = low * (1 + rng.uniform(0.04, 0.07))
    for _ in range(int(rng.integers(3, 5))):
        pts.append((int(rng.integers(4, 8)), hi))
        pts.append((int(rng.integers(4, 8)), low))
    pts.append((3, (hi + low) / 2))
    rows, prev = [], None
    for c in _closes(pts):
        c = c * (1 + rng.normal(0, 0.002))
        o = c if prev is None else prev
        rows.append([o, max(o, c) * (1 + abs(rng.normal(0, 0.003))), min(o, c) * (1 - abs(rng.normal(0, 0.003))), c])
        prev = c
    return _frame(rows, "2012-01-02")


def flip_spike(seed):
    """Hand-built for flip(): an UP trend, the origin broken, a lower high, a close below the low between - and the next
    candle spiking above that lower high (a one-bar look-ahead in flip() reads that spike)."""
    rng = np.random.default_rng(seed)
    pts, lvl = [(0, 1000.0)], 1000.0
    for _ in range(int(rng.integers(3, 5))):
        lvl *= 1 + rng.uniform(0.07, 0.12)
        pts.append((int(rng.integers(6, 10)), lvl))
        lvl *= 1 - rng.uniform(0.02, 0.04)
        pts.append((int(rng.integers(3, 6)), lvl))
    top = lvl * 1.08
    pts.append((7, top))
    l1 = top * (1 - rng.uniform(0.15, 0.22))
    pts.append((int(rng.integers(5, 8)), l1))
    h = l1 + (top - l1) * rng.uniform(0.4, 0.7)
    pts.append((int(rng.integers(4, 7)), h))
    pts.append((int(rng.integers(4, 7)), l1 * (1 - rng.uniform(0.02, 0.05))))
    rows, prev = [], _closes(pts)[0]
    for c in _closes(pts):
        c *= 1 + rng.normal(0, 0.002)
        o = prev
        rows.append([o, max(o, c) * (1 + abs(rng.normal(0, 0.003))), min(o, c) * (1 - abs(rng.normal(0, 0.003))), c])
        prev = c
    hi_h = max(r[1] for r in rows[-12:-4])
    rows.append([prev, hi_h * 1.01, prev * 0.995, prev * 1.002])
    for k in range(5):
        c = prev * (1 - 0.01 * (k + 1))
        rows.append([prev, max(prev, c) * 1.002, min(prev, c) * 0.998, c])
        prev = c
    return _frame(rows, "2013-01-01")


def _frame(rows, start):
    days = pd.bdate_range(start, periods=len(rows))
    return pd.DataFrame({"timestamp": days, **{k: [round(float(r[j]), 2) for r in rows] for j, k in enumerate(("open", "high", "low", "close"))}})


# Series that reach the branches the random walks above do not (found with a line tracer over the ATP port; parity with
# Trade still comes from Trade's own fold on them): Q49's origin move, Q27's RANGE after an origin break, the counter-move
# read as one leg, Q49's pool with the trend start, flip() next to a spike, and Q49's pool that gets the trend start
# appended (`pool.append(tstart_org)`; recorded in "branches").
EXTRA = [("walk", (216, 700, 0.45, 0.4)), ("walk", (227, 700, 0.45, 0.4)), ("walk", (302, 1200, 0.15, 0.25)),
         ("range_after_break", (3,)), ("range_after_break", (4,)), ("flip_spike", (2,)), ("flip_spike", (3,)),
         ("walk", (530, 1000, 0.1, 0.15))]


def extra_series(kind, args):
    if kind == "walk":
        seed, n, sideways, flip = args
        return daily_series(seed, n=n, sideways=sideways, flip=flip)
    return {"range_after_break": range_after_break, "flip_spike": flip_spike}[kind](*args)


def all_series():
    """Every golden series in order, as (label, bars): the label is the golden's `seed` field."""
    out = [(seed, daily_series(seed)) for seed in SEEDS]
    out += [(f"{kind}:{','.join(map(str, args))}", extra_series(kind, args)) for kind, args in EXTRA]
    return out
