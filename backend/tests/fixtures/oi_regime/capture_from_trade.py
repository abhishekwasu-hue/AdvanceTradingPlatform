"""Captures golden cases for app/option_chain/oi_regime.py from the Trade repo's own oi_analysis.py.

Run by hand from a checkout of the Trade repo (never in CI):
    cd <trade checkout> && PYTHONPATH=. python <atp>/backend/tests/fixtures/oi_regime/capture_from_trade.py > <atp>/backend/tests/fixtures/oi_regime/golden.json

Inputs are drawn from a fixed seed, so a re-run gives the same file. Labels are stored as Trade prints them; the test
reads them with OISignal.parse / the class names, so the Marathi strings never reach ATP's code or UI.
"""
import json
import random
import sys

import pandas as pd

import oi_analysis as oa

rng = random.Random(20261011)
cases = {"classify": [], "rotation": [], "rotation2": [], "hysteresis": [], "pcr": [], "matrix": [], "psych": [], "gate": [],
         "swing": [], "rollover": [], "max_pain": []}
CLASS = {"Writing": "Writing", "Buying": "Buying", "Short Covering": "Short covering", "Long Unwinding": "Long unwinding"}


def cls_name(text):
    for key, name in CLASS.items():
        if key in text:
            return name
    insufficient = "\u0905\u092a\u0941\u0930\u093e"            # Trade's word for "insufficient"
    return "Insufficient data" if insufficient in text else "Flat/unclear"


def jitter(base, pct):
    return round(base * (1 + rng.uniform(-pct, pct) / 100))


for _ in range(300):
    oi_prev = rng.choice([None, 0] + [rng.randint(1000, 10_000_000)] * 8)
    prem_prev = rng.choice([None, 0] + [round(rng.uniform(1, 500), 2)] * 8)
    oi_now = jitter(oi_prev or 5000, 6)
    prem_now = round((prem_prev or 100) * (1 + rng.uniform(-3, 3) / 100), 2)
    out = oa.classify_oi_price_action(oi_now, oi_prev, prem_now, prem_prev)
    cases["classify"].append({"in": [oi_now, oi_prev, prem_now, prem_prev], "out": cls_name(out)})

for _ in range(300):
    c_prev = rng.choice([None, 0] + [rng.randint(100, 5000)] * 8)
    p_prev = rng.choice([None, 0] + [rng.randint(100, 5000)] * 8)
    c_now, p_now = jitter(c_prev or 1000, 6), jitter(p_prev or 1000, 6)
    cases["rotation"].append({"in": [c_now, c_prev, p_now, p_prev], "out": oa.is_genuine_rotation(c_now, c_prev, p_now, p_prev)})

for _ in range(200):
    rows = [(rng.randint(800, 1200), rng.randint(800, 1200))]
    drift = rng.choice([-4, 0, 4])
    for _ in range(rng.randint(1, 3)):
        rows.append((round(rows[-1][0] * (1 + (drift + rng.uniform(-3, 3)) / 100)), round(rows[-1][1] * (1 + (-drift + rng.uniform(-3, 3)) / 100))))
    cases["rotation2"].append({"in": rows, "out": oa.rotation_confirmed_for_2_snapshots(rows)})

for _ in range(150):
    history = pd.DataFrame(columns=["diff", "total_put_oi", "total_call_oi", "signal"])
    call, put = rng.randint(4000, 6000), rng.randint(4000, 6000)
    steps = []
    drift = rng.choice([-3, 0, 3])
    for i in range(rng.randint(4, 14)):
        if i == 6:
            drift = -drift
        call = round(call * (1 + (drift + rng.uniform(-4, 4)) / 100))
        put = round(put * (1 + (-drift + rng.uniform(-4, 4)) / 100))
        diff = put - call
        sig = oa.compute_oi_signal_with_hysteresis(diff, put, call, history.tail(5).reset_index(drop=True))
        steps.append({"diff": diff, "put": put, "call": call, "signal": sig})
        history = pd.concat([history, pd.DataFrame([{"diff": diff, "total_put_oi": put, "total_call_oi": call, "signal": sig}])], ignore_index=True)
    cases["hysteresis"].append(steps)

for put, call in [(600, 1000), (700, 1000), (800, 1000), (899, 1000), (900, 1000), (950, 1000), (1000, 1000), (1001, 1000),
                  (1300, 1000), (1301, 1000), (1500, 1000), (1000, 0), (6999, 10000), (8995, 10000), (13004, 10000)]:
    pcr, bias = oa.compute_pcr_signal(put, call)
    cases["pcr"].append({"in": [put, call], "pcr": pcr, "bias": bias})

for _ in range(80):
    vals = [rng.choice([None, rng.randint(90, 110)]) for _ in range(4)]
    cases["matrix"].append({"in": vals, "out": oa.compute_oi_price_matrix(*vals)})

for _ in range(80):
    step = rng.choice([25, 50, 100, 500])
    price = rng.choice([rng.randint(1, 400) * step, round(rng.uniform(1000, 80000), 2)])
    direction = rng.choice(["BULLISH", "BEARISH"])
    cases["psych"].append({"in": [price, direction, step], "out": oa.find_psychological_level(price, direction, step)})

for label in ["🟢 BULLISH (Strong)", "🟡 BULLISH (Weakening)", "🔴 BEARISH (Strong)", "🟠 BEARISH (Weakening)", "⚪ NEUTRAL", None]:
    for d in ["BULLISH", "BEARISH", None]:
        cases["gate"].append({"in": [d, label], "diff_gate": oa.check_oi_diff_entry_gate(d, label),
                              "confirm_a": oa.check_oi_confirmation(d, label, "A")[0]})

for _ in range(120):
    matrix = rng.choice([None, oa.compute_oi_price_matrix(*[rng.randint(90, 110) for _ in range(4)])])
    pcr_bias = rng.choice([None, "NEUTRAL", "BULLISH", "BEARISH", "SIDEWAYS"])
    price = rng.choice([None, 0, 24000.0])
    mp = rng.choice([None, 23900.0, 24000.0, 24100.0])
    roll = rng.choice([None, {"bias": "NEUTRAL"}, {"bias": "BULLISH"}, {"bias": "BEARISH"}])
    d = rng.choice(["BULLISH", "BEARISH"])
    mo = rng.choice([0, 1, 2])
    ok, detail = oa.swing_oi_gate(d, matrix, pcr_bias, mp, price, roll, max_opposing=mo)
    cases["swing"].append({"in": [d, matrix, pcr_bias, mp, price, roll, mo], "ok": ok, "supporting": len(detail["supporting"]),
                           "opposing": len(detail["opposing"]), "total": detail["total_signals"]})


def chain(strikes, oi_scale, ltp):
    return [{"strike_price": k, "call_options": {"market_data": {"oi": rng.randint(0, oi_scale), "ltp": ltp(k, "CE")}},
             "put_options": {"market_data": {"oi": rng.randint(0, oi_scale), "ltp": ltp(k, "PE")}}} for k in strikes]


for _ in range(20):
    step = rng.choice([50, 100])
    atm = rng.randint(200, 500) * step
    strikes = [atm + i * step for i in range(-5, 6)]
    near = chain(strikes, 100000, lambda k, s: round(rng.uniform(1, 300), 2) if rng.random() > 0.1 else None)
    nxt = chain(strikes, 50000, lambda k, s: round(rng.uniform(1, 300), 2) if rng.random() > 0.1 else None)
    cases["rollover"].append({"near": near, "next": nxt, "atm": atm, "out": oa.compute_rollover_proxy(near, nxt, atm)})
    cases["max_pain"].append({"chain": near, "out": oa.compute_max_pain(near)})

json.dump(cases, sys.stdout, ensure_ascii=False, separators=(",", ":"))
