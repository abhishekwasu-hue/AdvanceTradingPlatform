"""Regenerates golden.json.gz from the Trade repo's own functions (run by hand; the tests only read the file).

    python make_golden.py /path/to/trade | gzip -9 -n > $ATP_PRIVATE_FIXTURES/backend/tests/fixtures/options_builder/golden.json.gz
    # The golden file is Trade output: it lives only in the private fixtures store, never in this public repository
    # (owner decision PR-2, docs/PUBLIC_REPO_POLICY.md section 5); its new sha256 goes into
    # backend/tests/private_fixtures_manifest.json in a reviewed change.

Every case records the Trade function, its arguments and its result; tests/test_p1a_options_builder.py calls the
ported function with the same arguments and expects the same result. The chains are synthetic (no market data) and
built by synthetic_inputs.py next to this file.
"""
import json
import subprocess
import sys

from synthetic_inputs import CHAINS  # this folder (before the Trade export goes on the path)

trade = sys.argv[1]
sys.path.insert(0, trade)
import strategy as st  # noqa: E402
import strategy_payoff as sp  # noqa: E402


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
    # a hedge wider than the chain: the short is found, no hedge is
    for d in ("BULLISH", "BEARISH"):
        add("select_credit_spread", {"direction": d, "hedge_width_points": 100 * step, "pop_threshold_pct": 10}, name)
    # an ATM that is not a listed strike (off the grid) and one far outside the chain
    for atm in (spot + 7, 99999):
        add("select_credit_spread_fixed_strikes", {"direction": "BULLISH", "atm_strike": atm, "strikes_otm": 2, "hedge_width_points": step, "step": step}, name)
        add("select_iron_condor", {"atm_strike": atm, "step": step, "hedge_width_points": step, "pop_threshold_pct": 10}, name)
        add("select_naked_option_itm", {"direction": "BULLISH", "atm_strike": atm, "itm_depth_points": 0, "hedge_enabled": True, "hedge_width_points": step, "step": step}, name)

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
