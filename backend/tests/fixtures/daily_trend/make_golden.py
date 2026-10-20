"""Regenerates golden.json.gz from the Trade repo's OWN Daily trend code (run by hand; the tests only read the file).

    git -C /path/to/trade archive <rev> | tar -x -C /tmp/trade_export        # a read-only export, never the repo itself
    python make_golden.py /tmp/trade_export <the full 40-character SHA of rev> golden.json.gz
    # The golden file is grandfathered in scripts/check_public_repo.py (Trade output, open question PR-2): it may not
    # change here; a Trade re-sync goes to the private fixtures store (docs/design/TRADE_TAKEOVER.md section 3).

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

# Branches a state does not show, recorded from Trade's own fold by a line tracer: name -> a line of decision3/daily.py.
BRANCH_LINES = {"q49_pool_trend_start": "pool.append(tstart_org)"}


def branch_bars(fn, *args):
    """Runs fn(*args) under a line tracer on Trade's decision3/daily.py: {branch: sorted fold bars `i` where it ran}."""
    import inspect
    path = DD.__file__
    lines = inspect.getsource(DD).split("\n")
    where = {}
    for name, text in BRANCH_LINES.items():
        hits = [n + 1 for n, line in enumerate(lines) if line.strip() == text]
        if len(hits) != 1:
            raise SystemExit(f"branch {name}: {text!r} found {len(hits)} times in {path}")
        where[hits[0]] = name
    seen = {name: set() for name in BRANCH_LINES}

    def tracer(frame, event, arg):
        if frame.f_code.co_filename != path:
            return None

        def local(fr, ev, a):
            if ev == "line" and fr.f_lineno in where:
                seen[where[fr.f_lineno]].add(int(fr.f_locals.get("i", -1)))
            return local
        return local
    old = sys.gettrace()
    sys.settrace(tracer)
    try:
        out = fn(*args)
    finally:
        sys.settrace(old)
    return out, {name: sorted(bars) for name, bars in seen.items()}


def extra_series(kind, args):
    if kind == "walk":
        seed, n, sideways, flip = args
        return daily_series(seed, n=n, sideways=sideways, flip=flip)
    return {"range_after_break": range_after_break, "flip_spike": flip_spike}[kind](*args)


# The reference implementation (Trade) writes these reasons in Marathi; ATP is English only, so they are mapped to English.
# The Marathi side is written with \u escapes (no Devanagari in ATP source - scripts/check_english_only.py).
TRANSLATE = [(": protected origin close \u0928\u0947 \u0924\u0941\u091f\u0932\u093e)", ": the protected origin was broken by a close)"),
             (": origin \u0927\u094b\u0915\u094d\u092f\u093e\u0924 — Q47)", ": origin at risk - Q47)"),
             (": impulse origin \u0924\u0941\u091f\u0932\u093e, parent correction \u091a\u093e\u0932\u0942)", ": impulse origin broken, a parent correction is running)"),
             (" (\u0926\u094b\u0928\u094d\u0939\u0940 \u0915\u0921\u093e)", " (both edges)"), ("Daily data \u0905\u092a\u0941\u0930\u093e: ", "Daily data too short: ")]


def tr(text):
    for a, b in TRANSLATE:
        text = text.replace(a, b)
    if any(ord(ch) > 127 and ch not in "–·" for ch in text):
        raise SystemExit(f"untranslated Trade text: {text!r}")
    return text


def ctx_json(ctx):
    out = {}
    for k, v in ctx.items():
        if k == "sides":
            out[k] = sorted(v)
        elif k == "band":
            out[k] = None if v is None else [num(x) for x in v]
        elif k == "reason":
            out[k] = tr(str(v))
        elif isinstance(v, float):
            out[k] = num(v)
        else:
            out[k] = v
    return out


def frame_of(bars):
    df = pd.DataFrame({k: v for k, v in bars.items() if k != "timestamp"})
    df.insert(0, "timestamp", pd.to_datetime(bars["timestamp"]))
    return df


def bars_json(df):
    return {"timestamp": [str(x) for x in df["timestamp"]], **{k: [float(x) for x in df[k]] for k in ("open", "high", "low", "close")}}


def main():
    golden = {"trade_revision": TRADE_REV,
              "source": "Trade decision3/daily.py, daily_legs.py, daily_swings.prepare, bots_common/level_reaction.prepare_daily, "
                        "legs2/measure.py, patterns2/rules.py, rules2.py",
              "settings": {"fold": {k: S3.DEFAULTS[k] for k in FOLD_KEYS}, "legs": {k: LS2.DEFAULTS[k] for k in LEGS_KEYS},
                           "patterns": {k: PS2.DEFAULTS[k] for k in PATTERN_KEYS}},
              "series": [], "folds": [], "branches": [], "trend_context": [], "truncated": [], "short": [], "state_at": [], "pivots": [], "legs": [], "read_counter": [], "rules": [], "misc": {}}
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
    for kind, args in EXTRA:
        sid = len(golden["series"])
        raw = extra_series(kind, args)
        d = DS.prepare(raw)
        golden["series"].append({"seed": f"{kind}:{','.join(map(str, args))}", "bars": bars_json(raw)})
        states, hit = branch_bars(DD.fold, d)
        for name, bars in hit.items():
            if bars:
                golden["branches"].append({"series": sid, "branch": name, "bars": bars})
        golden["folds"].append({"series": sid, "variant": "default", "settings": {}, "states": [state(st) for st in states],
                                "final_pivots": [pivot(p) for p in states[-1].pivots]})
    # Trade `level_reaction.trend_context` (the gate's bridge): known at yesterday's close, < 30 bars -> trend unclear.
    for sid, entry in enumerate(golden["series"]):
        raw = frame_of(entry["bars"])
        days = list(DS.prepare(raw)["timestamp"])
        for idx in (20, 31, len(days) // 2, len(days) - 1):
            today = days[min(idx, len(days) - 1)]
            for over in ({}, {"allow_parent_correction": True}):
                golden["trend_context"].append({"series": sid, "today": str(today), "settings": over,
                                                "ctx": ctx_json(LR.trend_context(raw, today, over))})
        golden["trend_context"].append({"series": sid, "today": None, "settings": {},
                                        "ctx": ctx_json(LR.trend_context(raw, None, {}))})
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
        sts = f["states"]
        for a, b in zip(sts, sts[1:]):
            if a["phase"] == "origin_broken" and b["trend"] == "RANGE":
                counts["q27_range_after_break"] = counts.get("q27_range_after_break", 0) + 1
            if b["phase"] == "origin_broken" and a["phase"] != "origin_broken" and a["protected"] and b["protected"] \
                    and a["protected"] != b["protected"]:
                counts["q49_origin_moved_on_break"] = counts.get("q49_origin_moved_on_break", 0) + 1
            if a["trend"] in ("UP", "DOWN") and b["trend"] in ("UP", "DOWN") and a["trend"] != b["trend"]:
                counts["flip"] = counts.get("flip", 0) + 1
        for st in f["states"]:
            for key in ("trend", "phase", "counter"):
                tag = f"{key}:{st[key]}"
                counts[tag] = counts.get(tag, 0) + 1
            for key in ("wave_a", "sharp", "corr_zone", "mature", "origin_risk", "target_passed"):
                if st[key]:
                    counts[key] = counts.get(key, 0) + 1
    for b in golden["branches"]:
        counts[b["branch"]] = counts.get(b["branch"], 0) + len(b["bars"])
    golden["coverage"] = counts
    payload = json.dumps(golden, sort_keys=True, separators=(",", ":")).encode("utf-8")
    with open(OUT, "wb") as fh_raw, gzip.GzipFile(fileobj=fh_raw, mode="wb", mtime=0, filename="") as fh:
        fh.write(payload)
    print(json.dumps({"folds": len(golden["folds"]), "legs": len(golden["legs"]), "coverage": counts}, sort_keys=True))


if __name__ == "__main__":
    main()
