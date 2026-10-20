"""The synthetic option chains of the options-builder goldens (no market data): make_golden.py records Trade's results
on them, and tests/test_p1a_options_builder.py builds them here too, so the tests that only need a chain run without
the golden file (a Trade output kept in the private fixtures store, docs/PUBLIC_REPO_POLICY.md section 5). Plain Python;
make_golden.py imports this file from its own folder.
"""
import copy
import math


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


def dirty_chain():
    """Broker data as it really arrives: zero and missing LTPs, a missing instrument key, null greeks, PoP of 0 and
    None, a duplicated strike row - every refusal branch of the selectors gets exercised."""
    rows = copy.deepcopy(CHAINS["logistic"][2])
    by = {r["strike_price"]: r for r in rows}
    by[24600]["call_options"]["market_data"]["ltp"] = 0
    by[24400]["put_options"]["market_data"]["ltp"] = 0
    by[24650]["call_options"].pop("instrument_key")
    by[24350]["put_options"]["option_greeks"] = None
    by[24700]["call_options"]["option_greeks"]["pop"] = None
    by[24300]["put_options"]["option_greeks"]["pop"] = 0.0
    by[24550]["call_options"]["option_greeks"]["pop"] = 0.0
    by[24750]["call_options"]["market_data"] = None
    by[24450]["put_options"]["market_data"]["ltp"] = None
    rows.insert(5, copy.deepcopy(by[24800]))                                   # a duplicated strike row
    return rows


def inverted_chain():
    """Premiums that rise away from the money (a crossed / stale chain): credits and debits come out <= 0."""
    rows = chain(24500, 50, 600, lambda k, t, s: 0.9)
    for r in rows:
        for side in ("call_options", "put_options"):
            r[side]["market_data"]["ltp"] = round(10 + abs(r["strike_price"] - 24500) * 0.3, 2)
    return rows


CHAINS["dirty"] = (24500, 50, dirty_chain())
CHAINS["inverted"] = (24500, 50, inverted_chain())
CHAINS["empty"] = (24500, 50, [])
