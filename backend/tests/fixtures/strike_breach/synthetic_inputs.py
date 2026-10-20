"""The synthetic daily series of the strike-breach goldens (seeded, no market data): make_golden.py records Trade's
results on it, and tests/test_z5_p1d_strike_breach.py builds it here too, so the tests that only need bars run without
the golden file (a Trade output kept in the private fixtures store, docs/PUBLIC_REPO_POLICY.md section 5).
make_golden.py imports this file from its own folder.
"""
import numpy as np
import pandas as pd


def series(n=90, seed=7):
    rng = np.random.default_rng(seed)
    ts = pd.bdate_range("2023-01-02", periods=n)
    c = 18000 * np.exp(np.cumsum(rng.normal(0, 0.009, n)))
    o = np.r_[c[0], c[:-1]] * np.exp(rng.normal(0, 0.002, n))
    h = np.maximum(o, c) * (1 + np.abs(rng.normal(0.003, 0.002, n)))
    lo = np.minimum(o, c) * (1 - np.abs(rng.normal(0.003, 0.002, n)))
    return pd.DataFrame({"timestamp": ts, "open": o, "high": h, "low": lo, "close": c})
