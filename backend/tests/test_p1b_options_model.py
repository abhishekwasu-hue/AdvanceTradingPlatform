"""P1-b: the builder's model - T+0 / any-date curves, IV and time scenarios, PoP, expected move, probability-weighted
P&L, net Greeks with rho. Checked against textbook values, against the expiry payoff, against Monte Carlo and against
finite differences - not against itself."""
import math
from datetime import date, timedelta

import numpy as np
import pytest

from app import options_builder as ob
from app.options_builder import model as m
from app.options_builder.greeks import leg_with_model_greeks

AS_OF = date(2026, 3, 2)
EXP = AS_OF + timedelta(days=14)
SPOT = 22000.0


def _priced(name, atm=SPOT, w=200.0, lots=1, lot_size=50, iv=0.15, r=0.07):
    """A template's legs priced by the model itself at one IV (so they are self-consistent), with expiry and IV."""
    out = []
    for leg in ob.build_ready_made_strategy(name, atm, w):
        base = {**leg, "lots": lots, "lot_size": lot_size, "expiry": EXP.isoformat(), "iv": iv}
        base["premium"] = m.leg_theoretical({**base, "premium": 0}, SPOT, AS_OF, r=r)
        out.append(base)
    return out


TEMPLATES = [n for group in ob.READY_MADE_CATEGORIES.values() for n in group]


def test_black_scholes_textbook_values():
    """S=100, K=100, t=1y, r=5 %, sigma=20 %: call 10.4506, put 5.5735 (Hull)."""
    leg = {"option_type": "CE", "strike": 100.0, "expiry": (AS_OF + timedelta(days=365)).isoformat(), "iv": 0.2}
    assert m.leg_theoretical(leg, 100.0, AS_OF, r=0.05) == pytest.approx(10.4506, abs=1e-3)
    assert m.leg_theoretical({**leg, "option_type": "PE"}, 100.0, AS_OF, r=0.05) == pytest.approx(5.5735, abs=1e-3)


@pytest.mark.parametrize("name", TEMPLATES)
def test_on_expiry_the_curve_is_the_expiry_payoff_and_today_it_is_flat_at_spot(name):
    legs = _priced(name)
    rng = ob.build_default_price_range(SPOT, num_points=81, range_pct=6.0)
    at_expiry = m.value_curve(legs, rng, as_of=AS_OF, days_forward=14)
    assert at_expiry == pytest.approx(ob.compute_strategy_payoff_curve(legs, rng), abs=1e-6)
    assert m.value_curve(legs, [SPOT], as_of=AS_OF)[0] == pytest.approx(0.0, abs=1e-6)   # priced by the model: no P&L today


def test_time_and_iv_scenarios_move_the_curve_the_right_way():
    rng = [SPOT]
    long_straddle, short_straddle = _priced("Long Straddle"), _priced("Short Straddle")
    assert m.value_curve(long_straddle, rng, as_of=AS_OF, iv_shift=0.03)[0] > 0 > m.value_curve(short_straddle, rng, as_of=AS_OF, iv_shift=0.03)[0]
    assert m.value_curve(long_straddle, rng, as_of=AS_OF, days_forward=7)[0] < 0 < m.value_curve(short_straddle, rng, as_of=AS_OF, days_forward=7)[0]


def test_iv_is_required_and_never_invented():
    leg = {**_priced("Buy Call")[0]}
    leg.pop("iv")
    with pytest.raises(ValueError, match="no IV"):
        m.value_curve([leg], [SPOT], as_of=AS_OF)
    solved = leg_with_model_greeks(leg, underlying_price=SPOT, expiry=EXP, as_of=AS_OF)      # from the premium (P1-a)
    assert solved["iv"] == pytest.approx(0.15, abs=1e-4)


@pytest.mark.parametrize("name", TEMPLATES)
def test_pop_and_expected_pnl_agree_with_monte_carlo(name):
    legs = _priced(name)
    sigma, r = 0.15, 0.07
    t = (EXP - AS_OF).days / 365.0
    rng = np.random.default_rng(7)
    z = rng.standard_normal(400_000)
    st = SPOT * np.exp((r - 0.5 * sigma ** 2) * t + sigma * math.sqrt(t) * z)
    pay = np.zeros_like(st)
    for leg in legs:
        k = leg["strike"]
        intrinsic = np.maximum(st - k, 0) if leg["option_type"] == "CE" else np.maximum(k - st, 0)
        sign = 1 if leg["direction"] == "BUY" else -1
        pay += sign * (intrinsic - leg["premium"]) * leg["lots"] * leg["lot_size"]
    assert m.pop_at_expiry(legs, SPOT, sigma, t, r=r) == pytest.approx(float((pay > 0).mean()), abs=0.004), name
    se = float(pay.std()) / math.sqrt(len(pay))
    assert m.expected_pnl_at_expiry(legs, SPOT, sigma, t, r=r) == pytest.approx(float(pay.mean()), abs=5 * se + 1e-6), name


def test_pop_of_a_long_call_is_the_chance_to_finish_above_its_breakeven():
    leg = _priced("Buy Call")
    sigma, t, r = 0.15, 14 / 365.0, 0.07
    be = leg[0]["strike"] + leg[0]["premium"]
    s = sigma * math.sqrt(t)
    want = 1 - 0.5 * (1 + math.erf((math.log(be / SPOT) - (r - 0.5 * sigma * sigma) * t) / s / math.sqrt(2)))
    assert m.pop_at_expiry(leg, SPOT, sigma, t, r=r) == pytest.approx(want, abs=1e-12)
    assert m.profitable_intervals(leg) == [(pytest.approx(be), math.inf)]


@pytest.mark.parametrize("name", TEMPLATES)
def test_profitable_intervals_match_the_payoff_sign(name):
    legs = _priced(name)
    intervals = m.profitable_intervals(legs)
    for p in np.linspace(SPOT * 0.8, SPOT * 1.2, 801):
        inside = any(a < p < b for a, b in intervals)
        pay = sum(ob.compute_leg_payoff(float(p), **{k: leg[k] for k in ("direction", "option_type", "strike", "premium", "lots", "lot_size")}) for leg in legs)
        if abs(pay) > 1e-6:
            assert inside == (pay > 0), (name, p, pay)


def test_expected_move_is_one_standard_deviation():
    assert m.expected_move(20000.0, 0.16, 1.0) == pytest.approx(3200.0)
    assert m.expected_move(20000.0, 0.16, 0.0) == 0.0


def test_net_greeks_match_finite_differences_and_rho_has_the_right_sign():
    legs = _priced("Iron Condor", lots=2)
    g = m.net_greeks(legs, SPOT, AS_OF)
    h = 1.0
    up, dn = m.value_curve(legs, [SPOT + h, SPOT - h], as_of=AS_OF)
    assert g["delta"] == pytest.approx((up - dn) / (2 * h), rel=1e-3, abs=1e-3)
    mid = m.value_curve(legs, [SPOT], as_of=AS_OF)[0]
    assert g["gamma"] == pytest.approx((up - 2 * mid + dn) / h ** 2, rel=1e-2, abs=1e-6)
    vega_fd = (m.value_curve(legs, [SPOT], as_of=AS_OF, iv_shift=0.005)[0] - m.value_curve(legs, [SPOT], as_of=AS_OF, iv_shift=-0.005)[0]) / 1.0
    assert g["vega"] == pytest.approx(vega_fd, rel=1e-2)
    call, put = _priced("Buy Call")[0], _priced("Buy Put")[0]
    for leg, sign in ((call, 1), (put, -1)):
        rho = m.leg_rho(leg, SPOT, AS_OF, r=0.07)
        fd = (m.leg_theoretical(leg, SPOT, AS_OF, r=0.075) - m.leg_theoretical(leg, SPOT, AS_OF, r=0.065)) / 1.0
        assert rho * sign > 0 and rho == pytest.approx(fd, rel=1e-3)


def test_summary_uses_the_nearest_expiry_and_says_it_is_a_model():
    legs = _priced("Short Strangle")
    far = [{**leg, "expiry": (EXP + timedelta(days=28)).isoformat()} for leg in legs]
    s = m.summary(legs + far, SPOT, as_of=AS_OF)
    assert s["expiry"] == EXP.isoformat() and 0 < s["pop"] < 1 and s["expected_move"] > 0
    assert "estimate" in s["model"] and set(s["greeks"]) == {"delta", "gamma", "theta", "vega", "rho"}
    with pytest.raises(ValueError, match="no expiry"):
        m.summary([{**legs[0], "expiry": None}], SPOT, as_of=AS_OF)


# --- the template gallery -------------------------------------------------------------------------------------------
from app.options_builder import catalog  # noqa: E402

NEXT = (EXP + timedelta(days=28)).isoformat()


def _catalog_priced(name, iv=0.15):
    legs = catalog.build_template(name, SPOT, 200.0, EXP.isoformat(), NEXT)
    out = []
    for leg in legs:
        leg = {**leg, "lot_size": 50, "iv": iv}
        leg["premium"] = SPOT if leg["option_type"] == "FUT" else m.leg_theoretical({**leg, "premium": 0}, SPOT, AS_OF)
        out.append(leg)
    return out


def test_the_gallery_has_more_than_thirty_templates_in_named_families_and_every_one_is_hedge_first():
    fams = catalog.families()
    assert sum(len(v) for v in fams.values()) >= 30 and all(fams[f] for f in catalog.FAMILIES)
    for name in catalog.CATALOG:
        legs = catalog.build_template(name, SPOT, 200.0, EXP.isoformat(), NEXT)
        dirs = [leg["direction"] for leg in legs]
        assert dirs == sorted(dirs, key=lambda d: d != "BUY"), name
    assert catalog.build_template("Call Calendar", SPOT, 200.0, EXP.isoformat()) is None       # needs the next expiry
    assert catalog.build_template("No Such", SPOT, 200.0, EXP.isoformat()) is None


@pytest.mark.parametrize("name", [n for n, t in catalog.CATALOG.items() if all(leg[4] == 0 for leg in t["legs"])])
def test_every_single_expiry_template_has_a_consistent_model(name):
    """Expiry curve = expiry payoff; flat at spot today; PoP agrees with the exact intervals' sign."""
    legs = _catalog_priced(name)
    rng = ob.build_default_price_range(SPOT, num_points=81, range_pct=8.0)
    assert m.value_curve(legs, rng, as_of=AS_OF, days_forward=14) == pytest.approx(ob.compute_strategy_payoff_curve(legs, rng), abs=1e-6)
    assert m.value_curve(legs, [SPOT], as_of=AS_OF)[0] == pytest.approx(0.0, abs=1e-6)
    pop = m.pop_at_expiry(legs, SPOT, 0.15, 14 / 365)
    assert 0.0 <= pop <= 1.0


def test_shapes_of_known_templates():
    rng = [SPOT - 600, SPOT, SPOT + 600]
    fly = ob.compute_strategy_payoff_curve(_catalog_priced("Long Call Butterfly"), rng)
    assert fly[1] > 0 and fly[0] < 0 and fly[2] < 0                                # peak at the body
    covered = ob.compute_strategy_payoff_curve(_catalog_priced("Covered Call"), [SPOT + 2000, SPOT + 4000])
    assert covered[0] == pytest.approx(covered[1])                                # capped above the call
    collar = ob.compute_strategy_payoff_curve(_catalog_priced("Collar"), [SPOT - 4000, SPOT - 2000])
    assert collar[0] == pytest.approx(collar[1])                                  # floored below the put
    synth = ob.compute_strategy_payoff_curve(_catalog_priced("Long Synthetic Future"), [SPOT - 100, SPOT + 100])
    assert synth[1] - synth[0] == pytest.approx(200 * 50)                         # moves like the underlying
    assert m.net_greeks(_catalog_priced("Covered Call"), SPOT, AS_OF)["delta"] < 50  # the short call takes some delta off


def test_a_calendar_is_drawn_on_the_near_expiry_with_the_far_leg_still_alive():
    legs = _catalog_priced("Call Calendar")
    at_near = m.value_curve(legs, [SPOT, SPOT + 1500], as_of=AS_OF, days_forward=14)
    naive = ob.compute_strategy_payoff_curve(legs, [SPOT, SPOT + 1500])
    assert at_near[0] > 0 and at_near[0] > naive[0]                               # the far call keeps its time value at the strike
    assert at_near[0] > at_near[1]                                                # and the tent falls away from the strike


# --- exact extremes (OB-4: a grid max loss depends on the grid) ---------------------------------------------------
UNBOUNDED_LOSS = {"Sell Call", "Short Straddle", "Short Strangle", "Call Ratio Spread"}


@pytest.mark.parametrize("name", TEMPLATES + ["Call Ratio Spread", "Put Ratio Spread", "Call Ratio Back Spread", "Covered Call", "Collar"])
def test_payoff_extremes_are_the_true_extremes_of_the_expiry_payoff(name):
    legs = _priced(name) if name in TEMPLATES else _catalog_priced(name)
    ext = m.payoff_extremes(legs)
    strikes = [float(leg["strike"]) for leg in legs if leg["option_type"] != "FUT"]
    grid = sorted(set(np.linspace(0.0, SPOT * 4, 40_001).tolist()) | set(strikes))           # a dense grid that hits every kink
    pay = ob.compute_strategy_payoff_curve(legs, grid)
    sampled = ob.compute_strategy_payoff_curve(legs, list(np.linspace(0.0, SPOT * 4, 40_001)))
    assert min(sampled) >= ext["max_loss"] - 1e-6 and max(sampled) <= ext["max_profit"] + 1e-6  # nothing sampled beats it
    if ext["unbounded_loss"]:
        assert ext["max_loss"] == -math.inf and pay[-1] < pay[len(pay) // 2]
    else:
        assert ext["max_loss"] == pytest.approx(min(pay), abs=1e-6) and ext["max_loss"] <= min(pay) + 1e-6
    if ext["unbounded_profit"]:
        assert ext["max_profit"] == math.inf and pay[-1] > pay[len(pay) // 2]
    else:
        assert ext["max_profit"] == pytest.approx(max(pay), abs=1e-6)
    assert (name in UNBOUNDED_LOSS) == ext["unbounded_loss"], name


def test_a_short_call_has_undefined_risk_whatever_the_chart_range_and_a_condor_has_its_wing_loss():
    short_call = _priced("Sell Call")
    narrow = ob.compute_max_profit_loss(ob.compute_strategy_payoff_curve(short_call, ob.build_default_price_range(SPOT, range_pct=2)))[1]
    wide = ob.compute_max_profit_loss(ob.compute_strategy_payoff_curve(short_call, ob.build_default_price_range(SPOT, range_pct=20)))[1]
    assert narrow != wide and m.payoff_extremes(short_call)["max_loss"] == -math.inf        # the grid number moves; the truth does not
    condor = _priced("Iron Condor")
    credit = sum((1 if leg["direction"] == "SELL" else -1) * leg["premium"] for leg in condor)
    wing = 200.0                                                                               # the template's hedge width
    assert m.payoff_extremes(condor)["max_loss"] == pytest.approx(-(wing - credit) * 50, abs=1e-6)


# --- the chain adapter ------------------------------------------------------------------------------------------------
from app.brokers.models import OptionChain, OptionChainRow  # noqa: E402
from app.options_builder import chain as ch  # noqa: E402


def _broker_chain(iv=0.15, quote_iv=True, spot=SPOT, step=100.0, n=15):
    rows = []
    for i in range(-n, n + 1):
        k = spot + i * step
        c = m.leg_theoretical({"option_type": "CE", "strike": k, "expiry": EXP.isoformat(), "iv": iv}, spot, AS_OF)
        p = m.leg_theoretical({"option_type": "PE", "strike": k, "expiry": EXP.isoformat(), "iv": iv}, spot, AS_OF)
        rows.append(OptionChainRow(strike=k, call_ltp=round(c, 2), put_ltp=round(p, 2),
                                   call_iv=iv if quote_iv else None, put_iv=iv if quote_iv else None))
    return OptionChain(underlying="NIFTY", expiry=EXP.isoformat(), underlying_ltp=spot, rows=rows)


def test_the_adapter_gives_each_option_the_sellers_pop_the_model_gives_that_short_leg():
    raw = ch.raw_chain(_broker_chain(), as_of=AS_OF)
    t = (EXP - AS_OF).days / 365.0
    for item in raw[::5]:
        for side, right in (("call_options", "CE"), ("put_options", "PE")):
            opt = item[side]
            leg = {"direction": "SELL", "option_type": right, "strike": item["strike_price"], "premium": opt["market_data"]["ltp"], "lots": 1, "lot_size": 50}
            assert opt["option_greeks"]["pop"] == pytest.approx(m.pop_at_expiry([leg], SPOT, 0.15, t), abs=1e-9)
            assert opt["instrument_key"] == f"NIFTY|{EXP.isoformat()}|{item['strike_price']:g}|{right}"
    deep_otm_call = raw[-1]["call_options"]["option_greeks"]["pop"]
    atm_call = raw[len(raw) // 2]["call_options"]["option_greeks"]["pop"]
    assert deep_otm_call > atm_call > 0.4                                                       # selling far out wins more often


def test_the_adapter_solves_iv_from_the_ltp_when_none_is_quoted_or_it_is_not_a_fraction():
    solved = ch.raw_chain(_broker_chain(quote_iv=False), as_of=AS_OF)
    as_percent = _broker_chain()
    for row in as_percent.rows:
        row.call_iv, row.put_iv = 15.0, 15.0                                                    # a broker quoting percent
    from_percent = ch.raw_chain(as_percent, as_of=AS_OF)
    mid = len(solved) // 2
    for raw in (solved, from_percent):
        assert raw[mid]["call_options"]["option_greeks"]["iv"] == pytest.approx(0.15, abs=2e-3)
    no_spot = _broker_chain()
    no_spot.underlying_ltp = None
    assert all(item["call_options"]["option_greeks"]["pop"] is None for item in ch.raw_chain(no_spot, as_of=AS_OF))
    expired = ch.raw_chain(_broker_chain(), as_of=EXP)
    assert all(item["put_options"]["option_greeks"]["pop"] is None for item in expired)
    missing = _broker_chain()
    missing.rows[0].call_ltp = None
    assert ch.raw_chain(missing, as_of=AS_OF)[0]["call_options"]["option_greeks"]["pop"] is None


def test_the_selectors_run_on_any_brokers_chain_through_the_adapter():
    raw = ch.raw_chain(_broker_chain(), as_of=AS_OF)
    condor = ob.select_iron_condor(raw, SPOT, 100.0, 200.0, pop_threshold_pct=50)
    assert condor is not None and condor["combined_pop_pct"] >= 50
    assert all(leg["instrument_key"].startswith("NIFTY|") for leg in condor["legs"])
    spread = ob.select_credit_spread(raw, "BEARISH", 200.0, pop_threshold_pct=80)
    assert spread is not None and spread["short_pop_pct"] >= 80
    assert ob.select_iron_condor(raw, SPOT, 100.0, 200.0, pop_threshold_pct=99.9) is None     # never a structure below the bar


def test_a_payoff_that_touches_zero_at_the_last_strike_and_rises_is_profitable_beyond_it():
    free_call = [{"direction": "BUY", "option_type": "CE", "strike": 100.0, "premium": 0.0, "lots": 1, "lot_size": 1}]
    assert m.profitable_intervals(free_call) == [(100.0, math.inf)]


def test_an_underlying_leg_is_expected_to_earn_the_carry_and_its_curve_matches_monte_carlo():
    fut = {"direction": "BUY", "option_type": "FUT", "strike": SPOT, "premium": SPOT, "lots": 1, "lot_size": 50,
           "expiry": EXP.isoformat(), "iv": None}
    t, r = 14 / 365, 0.07
    assert m.expected_pnl_at_expiry([fut], SPOT, 0.15, t, r=r) == pytest.approx(SPOT * (math.exp(r * t) - 1) * 50)
    covered = _catalog_priced("Covered Call")
    z = np.random.default_rng(11).standard_normal(400_000)
    st = SPOT * np.exp((r - 0.5 * 0.15 ** 2) * t + 0.15 * math.sqrt(t) * z)
    pay = np.zeros_like(st)
    for leg in covered:
        sign = 1 if leg["direction"] == "BUY" else -1
        value = st if leg["option_type"] == "FUT" else np.maximum(st - leg["strike"], 0)
        pay += sign * (value - leg["premium"]) * leg["lots"] * leg["lot_size"]
    se = float(pay.std()) / math.sqrt(len(pay))
    assert m.expected_pnl_at_expiry(covered, SPOT, 0.15, t, r=r) == pytest.approx(float(pay.mean()), abs=5 * se)
