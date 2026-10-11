"""Regenerates golden.json from the Trade repo's own functions (run by hand; the tests only read the JSON).

    python make_golden.py /path/to/trade > golden.json

Every case records the Trade function, its arguments and its result; tests/test_p1a_options_builder.py calls the
ported function with the same arguments and expects the same result. The chains are synthetic (no market data).
"""
import json
import subprocess
import sys

trade = sys.argv[1]
sys.path.insert(0, trade)
import strategy as st  # noqa: E402
import strategy_payoff as sp  # noqa: E402


def premium(strike, kind, spot):
    distance = abs(strike - spot)
    time_value = max(10.0, 100.0 - distance * 0.15)
    intrinsic = max(0, spot - strike) if kind == "CE" else max(0, strike - spot)
    return round(intrinsic + time_value, 2)


def chain(spot, step, width, pop_fn, gaps=(), no_ltp=()):
    out = []
    for k in range(spot - width, spot + width + step, step):
        if k in gaps:
            continue
        row = {"strike_price": k, "expiry": "EXP"}
        for side, kind in (("call_options", "CE"), ("put_options", "PE")):
            ltp = None if (k, kind) in no_ltp else premium(k, kind, spot)
            row[side] = {"instrument_key": f"T|{kind}-{k}", "market_data": {"ltp": ltp}, "option_greeks": {"pop": pop_fn(k, kind, spot)}}
        out.append(row)
    return out


def logistic_pop(step):
    """A short option's chance to expire worthless: high far OTM, low ITM (a smooth logistic in the strike distance)."""
    import math

    def pop(k, kind, s):
        x = (k - s) if kind == "CE" else (s - k)
        return round(1 / (1 + math.exp(-x / (4 * step))), 4)
    return pop


CHAINS = {
    "uniform": (24500, 50, chain(24500, 50, 1000, lambda k, t, s: 0.6)),
    "logistic": (24500, 50, chain(24500, 50, 1500, logistic_pop(50))),
    "graded": (51200, 100, chain(51200, 100, 2000, logistic_pop(100),
                                no_ltp={(51700, "CE"), (50500, "PE")})),
    "sparse": (24500, 50, chain(24500, 50, 400, lambda k, t, s: 0.8, gaps={24600, 24350})),
}
cases = []


def add(fn, kwargs, chain_name=None):
    args = dict(kwargs)
    if chain_name:
        args["raw_chain"] = CHAINS[chain_name][2]
    result = getattr(st if hasattr(st, fn) else sp, fn)(**args)
    cases.append({"fn": fn, "chain": chain_name, "kwargs": kwargs, "result": result})


for name, (spot, step, _) in CHAINS.items():
    for w in (step, 2 * step, 3 * step):
        for pop in (10, 50, 70, 90):
            add("select_iron_condor", {"atm_strike": spot, "step": step, "hedge_width_points": w, "pop_threshold_pct": pop}, name)
            add("select_iron_butterfly", {"atm_strike": spot, "hedge_width_points": w, "pop_threshold_pct": pop}, name)
            for d in ("BULLISH", "BEARISH", "SIDEWAYS"):
                add("select_credit_spread", {"direction": d, "hedge_width_points": w, "pop_threshold_pct": pop}, name)
        for d in ("BULLISH", "BEARISH", "SIDEWAYS"):
            for otm in (0, 1, 2, 4):
                add("select_credit_spread_fixed_strikes", {"direction": d, "atm_strike": spot, "strikes_otm": otm, "hedge_width_points": w, "step": step}, name)
            for depth in (-2 * step, -step, 0, step, 2 * step, int(1.4 * step)):
                add("select_credit_spread_itm", {"direction": d, "atm_strike": spot, "itm_depth_points": depth, "hedge_width_points": w, "step": step}, name)
                for hedge in (False, True):
                    add("select_naked_option_itm", {"direction": d, "atm_strike": spot, "itm_depth_points": depth, "hedge_enabled": hedge, "hedge_width_points": w, "step": step}, name)
    add("select_credit_spread_fixed_strikes", {"direction": "BULLISH", "atm_strike": 99999, "strikes_otm": 2, "hedge_width_points": step, "step": step}, name)

for m, r, lm, ls in ((100000, 2, 50, 75), (0, 2, 50, 75), (500000, 1.5, 37.5, 15), (250000, 2, 0, 50), (300000, 3, 12, 0), (1e6, 0.5, 80.25, 30)):
    add("compute_position_size", {"available_margin": m, "risk_pct": r, "max_loss_per_unit": lm, "lot_size": ls})

for spot, n, pct in ((24500.0, 100, 5.0), (51234.5, 200, 3.0), (100.0, 11, 10.0)):
    add("build_default_price_range", {"underlying_price": spot, "num_points": n, "range_pct": pct})

names = [n for group in sp.READY_MADE_CATEGORIES.values() for n in group] + ["Iron Condor", "No Such Strategy"]
for name, (spot, step, raw) in CHAINS.items():
    rng = sp.build_default_price_range(float(spot), num_points=161, range_pct=4.0)
    for strat in dict.fromkeys(names):
        for w in (step, 2 * step):
            add("build_ready_made_strategy", {"strategy_name": strat, "atm_strike": spot, "hedge_width": w})
            legs = sp.build_ready_made_strategy(strat, spot, hedge_width=w)
            if not legs:
                continue
            by_key = {(row["strike_price"], s): row[s] for row in raw for s in ("call_options", "put_options")}
            full = []
            for leg in legs:
                opt = by_key.get((leg["strike"], "call_options" if leg["option_type"] == "CE" else "put_options"))
                if not opt or opt["market_data"]["ltp"] is None:
                    full = []
                    break
                full.append({**leg, "premium": opt["market_data"]["ltp"], "lot_size": 25, "instrument_key": opt["instrument_key"],
                             "delta": 0.5 if leg["option_type"] == "CE" else -0.5, "gamma": 0.001, "theta": -4.2, "vega": 11.0})
            if not full:
                continue
            curve = sp.compute_strategy_payoff_curve(full, rng)
            cases.append({"fn": "payoff_bundle", "chain": name, "kwargs": {"legs": full, "price_range": rng}, "result": {
                "curve": curve, "breakevens": sp.find_breakeven_points(rng, curve), "max_profit_loss": list(sp.compute_max_profit_loss(curve)),
                "greeks": sp.compute_combined_greeks(full), "result": sp.build_strategy_result_from_legs(full, curve)}})
            for lots in (2, 3):
                scaled = [{**leg, "lots": lots} for leg in full]
                c2 = sp.compute_strategy_payoff_curve(scaled, rng)
                cases.append({"fn": "strategy_result", "chain": name, "kwargs": {"legs": scaled, "payoff_curve": c2},
                              "result": sp.build_strategy_result_from_legs(scaled, c2)})

commit = subprocess.run(["git", "-C", trade, "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
print(json.dumps({"source": f"Trade@{commit}", "chains": {k: {"spot": v[0], "step": v[1], "rows": v[2]} for k, v in CHAINS.items()},
                  "cases": [{**c, "kwargs": {k: v for k, v in c["kwargs"].items()}} for c in cases]}, indent=None, separators=(",", ":")))
