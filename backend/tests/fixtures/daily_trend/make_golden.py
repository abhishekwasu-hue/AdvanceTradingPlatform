"""Regenerates golden.json.gz from the Trade repo's OWN Daily trend code (run by hand; the tests only read the file).

    git -C /path/to/trade archive <rev> | tar -x -C /tmp/trade_export        # a read-only export, never the repo itself
    python make_golden.py /tmp/trade_export <the full 40-character SHA of rev> golden.json.gz

Inputs are synthetic and seeded (no market data): Daily series built from nested trend / correction legs with noise, so
the fold meets starts, impulses, corrections (candidate and A1), origin breaks, flips, ranges, sharp zones and wave A.
Recorded, from Trade's functions:
* `decision3.daily.fold` per Daily candle (every DState field except the free-text `why`, whose presence is recorded),
  for the default settings and several setting variants; the final pivot list; `state_at`;
* `decision3.daily_swings.prepare`, `bots_common.level_reaction.prepare_daily`, `sigma_d`, `pivots_pivot`, `pivots_dc`;
* `decision3.daily_legs`: `classify`, `read_counter`, `structure`, `reduce_waves`, `reduce_counted`, `unfolded_123`,
  `leg_points`, `c_class`, `tag`; `patterns2.rules` impulse / wedge / triangle and `rules2` zigzag / flat;
* truncated runs (the fold on the first m + 1 candles, as a live day sees it) and short series (UNKNOWN warm-up);
* the defaults of the settings modules for the keys ATP ports.
"""
import gzip
import json
import math
import re
import sys

import numpy as np
import pandas as pd

if len(sys.argv) != 4:
    raise SystemExit(__doc__)
TRADE_DIR, TRADE_REV, OUT = sys.argv[1], sys.argv[2], sys.argv[3]
if not re.fullmatch(r"[0-9a-f]{40}", TRADE_REV):
    raise SystemExit("give the full 40-character Trade commit SHA (git -C <trade> rev-parse <rev>), not a short one")
sys.path.insert(0, TRADE_DIR)
from bots_common import level_reaction as LR  # noqa: E402
from decision3 import daily as DD  # noqa: E402
from decision3 import daily_legs as DL  # noqa: E402
from decision3 import daily_swings as DS  # noqa: E402
from decision3 import settings as S3  # noqa: E402
from legs2 import settings2 as LS2  # noqa: E402
from patterns2 import rules as PR  # noqa: E402
from patterns2 import rules2 as PR2  # noqa: E402
from patterns2 import settings2 as PS2  # noqa: E402

FOLD_KEYS = ["daily_trend_mode", "corr_degree_rule", "corr_degree_frac", "daily_start_nature_gate", "daily_parent_gate",
             "q49_impulse_origin", "range_after_trend", "maturity_sigma_d", "daily_swing_method", "daily_pivot_n", "daily_dc_k",
             "daily_sigma_sessions", "daily_min_sessions", "range_eq_sigma_d"]
LEGS_KEYS = ["baseline_legs", "baseline_min", "c_hi", "c_lo", "short_leg_bars"]
PATTERN_KEYS = ["zz_max_ba", "flat_min_ba", "flat_b_max", "flat_expanded_min"]
VARIANTS = {"default": {}, "pivot3": {"daily_pivot_n": 3}, "dc": {"daily_swing_method": "dc"},
            "ratio": {"corr_degree_rule": "ratio"}, "leg_origin": {"q49_impulse_origin": "leg"},
            "no_gates": {"daily_parent_gate": False, "daily_start_nature_gate": False, "range_after_trend": False}}


def num(x):
    if x is None:
        return None
    v = float(x)
    return None if (math.isnan(v) or math.isinf(v)) else v


def pivot(p):
    """[kind, price, bar, confirm_bar]; day = the bar's date and known_at = the confirm bar's close (checked in the tests)."""
    return None if p is None else [p.kind, num(p.price), int(p.bar), int(p.confirm_bar)]


def state(st):
    return {"bar": int(st.bar), "trend": st.trend, "protected": pivot(st.protected),
            "band": None if st.band is None else [num(x) for x in st.band], "why": bool(st.why), "phase": st.phase,
            "wave": st.wave, "corr_label": st.corr_label, "imp_end": pivot(st.imp_end), "mature": bool(st.mature),
            "targets": [num(x) for x in st.targets], "parent": pivot(st.parent), "counter": st.counter,
            "leg_start": None if st.leg_start is None else int(st.leg_start), "corr_bars": [int(x) for x in st.corr_bars],
            "origin_risk": bool(st.origin_risk), "sharp": None if st.sharp is None else [num(st.sharp[0]), num(st.sharp[1]), int(st.sharp[2])],
            "evidence": list(st.evidence), "corr_zone": None if st.corr_zone is None else [num(st.corr_zone[0]), num(st.corr_zone[1]),
                                                                                          int(st.corr_zone[2])],
            "leg_from": None if st.leg_from is None else int(st.leg_from), "target_passed": bool(st.target_passed),
            "wave_a": pivot(st.wave_a), "alt": st.alt, "n_pivots": len(st.pivots)}


def daily_series(seed, n=1000, start="2010-01-04"):
    """Nested legs: a primary direction lasting months, with impulse runs and corrections, plus noise and gaps."""
    rng = np.random.default_rng(seed)
    price = 10000.0 * rng.uniform(0.8, 1.6)
    rows = []
    days = pd.bdate_range(start, periods=n)
    i = 0
    primary = rng.choice([-1, 1])
    while i < n:
        if rng.random() < 0.25:
            primary = -primary
        for sub in range(int(rng.integers(3, 8))):
            impulse = sub % 2 == 0
            length = int(rng.integers(4, 18) if impulse else rng.integers(3, 12))
            drift = primary * (rng.uniform(0.004, 0.012) if impulse else -rng.uniform(0.002, 0.008))
            if rng.random() < 0.15:
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


def bars_json(df):
    return {"timestamp": [str(x) for x in df["timestamp"]], **{k: [float(x) for x in df[k]] for k in ("open", "high", "low", "close")}}


def main():
    golden = {"trade_revision": TRADE_REV,
              "source": "Trade decision3/daily.py, daily_legs.py, daily_swings.prepare, bots_common/level_reaction.prepare_daily, "
                        "legs2/measure.py, patterns2/rules.py, rules2.py",
              "settings": {"fold": {k: S3.DEFAULTS[k] for k in FOLD_KEYS}, "legs": {k: LS2.DEFAULTS[k] for k in LEGS_KEYS},
                           "patterns": {k: PS2.DEFAULTS[k] for k in PATTERN_KEYS}},
              "series": [], "folds": [], "truncated": [], "short": [], "state_at": [], "pivots": [], "legs": [], "read_counter": [], "rules": [], "misc": {}}
    seeds = [101, 102, 103, 104, 105, 106]
    for sid, seed in enumerate(seeds):
        raw = daily_series(seed)
        d = DS.prepare(raw)
        golden["series"].append({"seed": seed, "bars": bars_json(raw)})
        for vname, over in VARIANTS.items():
            if vname != "default" and sid >= 2:
                continue                                             # variants on the first two series
            states = DD.fold(d, over)
            golden["folds"].append({"series": sid, "variant": vname, "settings": over, "states": [state(st) for st in states],
                                    "final_pivots": [pivot(p) for p in states[-1].pivots]})
            if vname == "default" and sid < 4:
                for m in (int(len(d) * 0.3), int(len(d) * 0.55), int(len(d) * 0.8)):
                    golden["truncated"].append({"series": sid, "m": m, "state": state(DD.fold(d.iloc[:m + 1], over)[-1])})
            if vname == "default" and sid == 0:
                for m in (5, 9, 10, 11, 24):
                    golden["short"].append({"m": m, "states": [state(st) for st in DD.fold(d.iloc[:m + 1], over)]})
            if vname == "default":
                for ts in (d["bar_end"].iloc[50] - pd.Timedelta(minutes=1), d["bar_end"].iloc[50], d["timestamp"].iloc[0],
                           d["bar_end"].iloc[-1] + pd.Timedelta(days=3)):
                    golden["state_at"].append({"series": sid, "ts": str(ts), "bar": int(DD.state_at(states, ts).bar),
                                               "trend": DD.state_at(states, ts).trend})
                final = states[-1].pivots
                A2 = DL.arrays(d)
                low = DD.pivots_pivot(d, 1, DD.known_at_of(d))
                rng = np.random.default_rng(seed + 7)
                for _ in range(60):
                    a, b = sorted(int(x) for x in rng.choice(len(final), 2, replace=False))
                    pa, pb = final[a], final[b]
                    upto = int(min(len(d) - 1, pb.confirm_bar + int(rng.integers(0, 20))))
                    for elliott in (False, True):
                        for inner in (None, "low"):
                            c = DL.classify(A2, final, pa.bar, pa.price, pb.bar, pb.price, upto, inner_seq=low if inner else None,
                                            elliott=elliott)
                            golden["legs"].append({"series": sid, "a": a, "b": b, "upto": upto, "elliott": elliott, "inner": inner,
                                                   "out": {k: (num(v) if k == "C" else v) for k, v in c.items()}, "tag": DL.tag(c),
                                                   "points": [list(map(num, DL.leg_points(final, pa.bar, pa.price, pb.bar, pb.price, upto,
                                                                                          low if inner else None)[0])),
                                                              DL.leg_points(final, pa.bar, pa.price, pb.bar, pb.price, upto,
                                                                            low if inner else None)[1]]})
        kat = DD.known_at_of(d)
        sig = DD.sigma_d(d, 10)
        golden["pivots"].append({"series": sid, "sigma": [num(x) for x in sig],
                                 "pivot2": [pivot(p) for p in DD.pivots_pivot(d, 2, kat)],
                                 "pivot1": [pivot(p) for p in DD.pivots_pivot(d, 1, kat)],
                                 "dc": [pivot(p) for p in DD.pivots_dc(d, 1.0, sig, kat)]})
    rng = np.random.default_rng(4242)
    for _ in range(1500):
        n = int(rng.choice([2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13]))
        dirn = rng.choice([-1, 1])
        x = [100.0]
        for k in range(n):
            leg = rng.uniform(1, 12) * (dirn if k % 2 == 0 else -dirn) * (1.0 if k % 2 == 0 else rng.uniform(0.2, 1.4))
            x.append(round(x[-1] + leg, 2))
        if (x[-1] - x[0]) * dirn <= 0:
            x[-1] = round(x[0] + dirn * rng.uniform(1, 5), 2)
        rc = DL.read_counter(x)
        golden["read_counter"].append({"x": x, "read": rc, "structure": DL.structure(x), "structure_e": DL.structure(x, elliott=True),
                                       "reduce": {str(k): v for k, v in DL.reduce_waves(x).items()},
                                       "counted": {str(k): [v[0], v[1]] for k, v in DL.reduce_counted(x).items()},
                                       "unfolded_123": bool(DL.unfolded_123(x)),
                                       "motive": DL._motive_segs(x)})
    s2 = PS2.DEFAULTS
    for _ in range(800):
        n = int(rng.integers(2, 6))
        x = [100.0]
        for k in range(n):
            x.append(round(x[-1] + rng.uniform(-10, 10), 2))
        done = bool(rng.random() < 0.6)
        golden["rules"].append({"x": x, "done": done, "impulse": PR.impulse(x, done), "wedge_c": PR.wedge(x, done, "contracting"),
                                "wedge_e": PR.wedge(x, done, "expanding"), "tri_c": list(PR.triangle(x, done, "contracting", 0.5)),
                                "tri_e": list(PR.triangle(x, done, "expanding")), "zigzag": PR2.zigzag(x, done, s2)[0],
                                "flat": [PR2.flat(x, done, s2)[0], PR2.flat(x, done, s2)[1]]})
    base = [{"er": float(rng.random()), "overlap": float(rng.random()), "body": float(rng.random()), "dirc": float(rng.random())}
            for _ in range(45)]
    golden["misc"]["c_class"] = {"base": base, "cases": [
        {"raw": r, "n_base": nb, "out": [num(DL.c_class(r, base[:nb])[0]), str(DL.CODE.get(DL.c_class(r, base[:nb])[1],
                                                                                             DL.c_class(r, base[:nb])[1]))]}
        for r, nb in ((base[0], 45), (base[1], 9), (base[2], 10), (None, 20), (base[3], 30), ({"er": 0.9, "overlap": None,
                                                                                                "body": 0.95, "dirc": 1.0}, 40))]}
    messy = daily_series(999, n=80)
    messy = pd.concat([messy, messy.iloc[[5, 9]]]).sample(frac=1.0, random_state=3)
    messy["timestamp"] = pd.to_datetime(messy["timestamp"]).dt.tz_localize("Asia/Kolkata") + pd.Timedelta(hours=9, minutes=15)
    prep = DS.prepare(messy)
    pd_ = LR.prepare_daily(messy, "2010-02-15")
    golden["misc"]["prepare"] = {"input": {"timestamp": [str(x) for x in messy["timestamp"]],
                                           **{k: [float(x) for x in messy[k]] for k in ("open", "high", "low", "close")}},
                                 "rows": len(prep), "first": str(prep["timestamp"].iloc[0]), "last_end": str(prep["bar_end"].iloc[-1]),
                                 "close_sum": float(prep["close"].sum()), "columns": list(prep.columns),
                                 "daily_rows": len(pd_), "daily_last": str(pd_["timestamp"].iloc[-1])}
    counts = {}
    for f in golden["folds"]:
        for st in f["states"]:
            for key in ("trend", "phase", "counter"):
                tag = f"{key}:{st[key]}"
                counts[tag] = counts.get(tag, 0) + 1
            for key in ("wave_a", "sharp", "corr_zone", "mature", "origin_risk", "target_passed"):
                if st[key]:
                    counts[key] = counts.get(key, 0) + 1
    golden["coverage"] = counts
    payload = json.dumps(golden, sort_keys=True, separators=(",", ":")).encode("utf-8")
    with open(OUT, "wb") as fh_raw, gzip.GzipFile(fileobj=fh_raw, mode="wb", mtime=0, filename="") as fh:
        fh.write(payload)
    print(json.dumps({"folds": len(golden["folds"]), "legs": len(golden["legs"]), "coverage": counts}, sort_keys=True))


if __name__ == "__main__":
    main()
