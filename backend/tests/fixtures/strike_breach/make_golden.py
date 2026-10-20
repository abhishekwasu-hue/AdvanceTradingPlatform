"""Regenerates golden.json from the Trade repo's own `strike_breach_model.py` (run by hand; the tests only read the JSON).

    python make_golden.py /path/to/trade | gzip -9 > golden.json.gz
    # The golden file is grandfathered in scripts/check_public_repo.py (Trade output, open question PR-2): it may not
    # change here; a Trade re-sync goes to the private fixtures store (docs/design/TRADE_TAKEOVER.md section 3).

Records, on a synthetic daily series (no market data): the Trade feature table (`daily_features`, without the structure
column ATP does not port), breach probabilities from the frozen NIFTY coefficients through Trade's own `design` +
`predict_frozen`, `required_z` inversions, `regime_of` and `wilder`. tests/test_z5_p1d_strike_breach.py calls the ported
functions with the same inputs and expects the same outputs.
"""
import json
import math
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, sys.argv[1])
import strike_breach_model as M  # noqa: E402


def nan_none(values):
    return [None if (v is None or (isinstance(v, float) and math.isnan(v))) else float(v) for v in values]


def series(n=90, seed=7):
    rng = np.random.default_rng(seed)
    ts = pd.bdate_range("2023-01-02", periods=n)
    c = 18000 * np.exp(np.cumsum(rng.normal(0, 0.009, n)))
    o = np.r_[c[0], c[:-1]] * np.exp(rng.normal(0, 0.002, n))
    h = np.maximum(o, c) * (1 + np.abs(rng.normal(0.003, 0.002, n)))
    lo = np.minimum(o, c) * (1 - np.abs(rng.normal(0.003, 0.002, n)))
    return pd.DataFrame({"timestamp": ts, "open": o, "high": h, "low": lo, "close": c})


coef = json.load(open(M.FROZEN_PATH, encoding="utf-8"))["models"]
# The full model on z_rv (Trade's "M_RV"), picked by its spec rather than its (non-English) name.
specs = {oc: next(v for v in coef[oc].values() if v["zcol"] == "z_rv" and v["full"]) for oc in ("close", "touch")}

dd = series()
f = M.daily_features(dd)
out = {"source": "Trade strike_breach_model.py (frozen NIFTY IS 2015-2021 fit, M_RV full model)",
       "coef": {oc: {"names": specs[oc]["names"], "coef": specs[oc]["coef"]} for oc in specs},
       "bars": {k: [float(x) for x in dd[k]] for k in ("open", "high", "low", "close")},
       "features": {k: nan_none(f[k]) for k in ("sigma20", "atr14", "adx", "di_dir")},
       "wilder": {"x": list(range(1, 21)), "n": 5, "out": nan_none(M.wilder(np.arange(1, 21, dtype=float), 5))},
       "regime": [{"adx": a, "align": s, "regime": M.regime_of(a, s)} for a in (15.0, 20.0, 22.0, 25.0, 30.0) for s in (-1, 0, 1)]}

cases = []
for z in (0.25, 1.0, 2.2, 3.0):
    for h in (1, 2, 3, 5, 8):
        for weekday in (0, 2, 3):
            for adx, align in ((18.0, 1), (22.0, -1), (30.0, 1), (30.0, -1)):
                for side in (1, -1):
                    for struct in (0, 1, -1):
                        cases.append({"z_rv": z, "h": h, "dte": h - 1, "weekday": weekday, "adx": adx, "align_adx": align * side, "di_dir": align,
                                      "align_struct": struct * side, "struct": struct, "side": side})
rows = pd.DataFrame(cases)
for oc in specs:
    p = M.predict_frozen(rows, specs[oc])
    for case, value in zip(cases, p):
        case[f"p_{oc}"] = float(value)
out["predict"] = cases

inverse = []
for oc in specs:
    b, names = np.asarray(specs[oc]["coef"]), specs[oc]["names"]
    for target in (0.05, 0.10, 0.20, 0.30):
        for h in (1, 3, 5):
            for regime in M.REGIMES:
                for struct in (0, 1, -1):
                    for call in (0, 1):
                        z = M.required_z(b, names, target, h, (3 - (h - 1)) % 5, regime, struct=struct, call=call)
                        inverse.append({"outcome": oc, "target": target, "h": h, "weekday": (3 - (h - 1)) % 5, "regime": regime, "struct": struct,
                                        "call": call, "z": None if math.isnan(z) else float(z)})
out["required_z"] = inverse
json.dump(out, sys.stdout, indent=None, separators=(",", ":"))
