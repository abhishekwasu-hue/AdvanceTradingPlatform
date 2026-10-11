"""P1-a: the Options Strategy Builder core, ported from the Trade repo.

1. Golden parity: every case in tests/fixtures/options_builder/golden.json.gz was produced by the Trade function itself
   (make_golden.py); the ported function, given the same arguments, must give the same result.
2. The Trade repo's own unit tests (tests/test_strategy_selection.py, the strategy-result tests of
   tests/test_dashboard_strategy_builder.py), ported as they are.
3. The rules the port adds or makes explicit: hedge first, equal lots, positive max loss, no NIFTY defaults.
"""
import gzip
import inspect
import json
from pathlib import Path

import pytest

from app import options_builder as ob

GOLDEN = json.loads(gzip.decompress((Path(__file__).parent / "fixtures" / "options_builder" / "golden.json.gz").read_bytes()))
CHAINS = {name: c["rows"] for name, c in GOLDEN["chains"].items()}


def _same(a, b, path="result"):
    """Bit-for-bit: the same type (int stays int, bool stays bool) and the same value - no summation is reordered in
    the port, so floats must be exactly equal too. Tuples and lists are the same thing in JSON."""
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        pass
    else:
        assert type(a) is type(b), (path, type(a), type(b), a, b)
    if isinstance(a, dict):
        assert isinstance(b, dict) and set(a) == set(b), (path, a, b)
        for k in a:
            _same(a[k], b[k], f"{path}.{k}")
    elif isinstance(a, (list, tuple)):
        assert isinstance(b, (list, tuple)) and len(a) == len(b), (path, a, b)
        for i, (x, y) in enumerate(zip(a, b)):
            _same(x, y, f"{path}[{i}]")
    else:
        assert a == b, (path, a, b)


def _call(case):
    fn, kwargs = case["fn"], dict(case["kwargs"])
    if case["chain"] and fn.startswith("select_"):
        kwargs["raw_chain"] = CHAINS[case["chain"]]
    if fn == "payoff_bundle":
        legs, rng = kwargs["legs"], kwargs["price_range"]
        curve = ob.compute_strategy_payoff_curve(legs, rng)
        return {"curve": curve, "breakevens": ob.find_breakeven_points(rng, curve), "max_profit_loss": list(ob.compute_max_profit_loss(curve)),
                "greeks": ob.compute_combined_greeks(legs), "result": ob.build_strategy_result_from_legs(legs, curve)}
    if fn == "strategy_result":
        return ob.build_strategy_result_from_legs(kwargs["legs"], kwargs["payoff_curve"])
    out = getattr(ob, fn)(**kwargs)
    return list(out) if isinstance(out, tuple) else out


def test_the_golden_set_comes_from_the_trade_repo_and_is_not_empty():
    assert GOLDEN["source"].startswith("Trade@")
    fns = {c["fn"] for c in GOLDEN["cases"]}
    assert {"select_iron_condor", "select_iron_butterfly", "select_credit_spread", "select_credit_spread_fixed_strikes",
            "select_credit_spread_itm", "select_naked_option_itm", "compute_position_size", "build_default_price_range",
            "build_ready_made_strategy", "payoff_bundle", "strategy_result"} <= fns
    for fn in ("select_iron_condor", "select_iron_butterfly", "select_credit_spread", "select_naked_option_itm"):
        results = [c["result"] for c in GOLDEN["cases"] if c["fn"] == fn]
        assert any(r is None for r in results) and any(r is not None for r in results), fn   # both paths covered


@pytest.mark.parametrize("i", range(len(GOLDEN["cases"])), ids=lambda i: f"{GOLDEN['cases'][i]['fn']}-{i}")
def test_golden_parity(i):
    case = GOLDEN["cases"][i]
    _same(case["result"], _call(case))


# --- the Trade repo's own tests (tests/test_strategy_selection.py), ported --------------------------------------------
@pytest.fixture
def sample_option_chain():
    """Trade's conftest fixture: spot 24500, strikes 24000-25000 every 50, ITM dearer than OTM, PoP 0.6 everywhere."""
    return CHAINS["uniform"]


SPOT, STEP = 24500, 50


class TestFixedStrikeSelection:
    def test_bullish_gives_bull_put_spread_with_correct_strikes(self, sample_option_chain):
        r = ob.select_credit_spread_fixed_strikes(sample_option_chain, "BULLISH", atm_strike=SPOT, hedge_width_points=100, step=STEP)
        assert r["strategy"] == "BULL_PUT_SPREAD" and r["short_leg"]["strike"] == 24400 and r["long_leg"]["strike"] == 24300

    def test_bearish_gives_bear_call_spread_with_correct_strikes(self, sample_option_chain):
        r = ob.select_credit_spread_fixed_strikes(sample_option_chain, "BEARISH", atm_strike=SPOT, hedge_width_points=100, step=STEP)
        assert r["strategy"] == "BEAR_CALL_SPREAD" and r["short_leg"]["strike"] == 24600 and r["long_leg"]["strike"] == 24700

    def test_max_profit_equals_net_credit(self, sample_option_chain):
        r = ob.select_credit_spread_fixed_strikes(sample_option_chain, "BULLISH", atm_strike=SPOT, hedge_width_points=100, step=STEP)
        assert r["max_profit"] == r["net_credit"]

    def test_invalid_direction_and_missing_strike_return_none(self, sample_option_chain):
        assert ob.select_credit_spread_fixed_strikes(sample_option_chain, "SIDEWAYS", atm_strike=SPOT, hedge_width_points=100, step=STEP) is None
        assert ob.select_credit_spread_fixed_strikes(sample_option_chain, "BULLISH", atm_strike=99999, hedge_width_points=100, step=STEP) is None


def test_pop_based_selection_respects_threshold():
    r = ob.select_credit_spread(CHAINS["logistic"], "BULLISH", hedge_width_points=100, pop_threshold_pct=70)
    assert r is not None and r["short_pop_pct"] >= 70


class TestPositionSizing:
    def test_normal_calculation_rounds_down(self):
        lots, risk = ob.compute_position_size(available_margin=100000, risk_pct=2, max_loss_per_unit=50, lot_size=75)
        assert risk == 2000 and lots == 0          # 2000 / 3750 -> never rounded up

    def test_zero_margin_returns_zero(self):
        assert ob.compute_position_size(available_margin=0, risk_pct=2, max_loss_per_unit=50, lot_size=75)[0] == 0


class TestITMCreditSpreadSelection:
    def test_bullish_short_put_is_above_atm(self, sample_option_chain):
        r = ob.select_credit_spread_itm(sample_option_chain, "BULLISH", atm_strike=SPOT, itm_depth_points=100, hedge_width_points=150, step=STEP)
        assert r["strategy"] == "BULL_PUT_SPREAD" and r["short_leg"]["strike"] == 24600 and r["long_leg"]["strike"] == 24450

    def test_bearish_short_call_is_below_atm(self, sample_option_chain):
        r = ob.select_credit_spread_itm(sample_option_chain, "BEARISH", atm_strike=SPOT, itm_depth_points=100, hedge_width_points=150, step=STEP)
        assert r["strategy"] == "BEAR_CALL_SPREAD" and r["short_leg"]["strike"] == 24400 and r["long_leg"]["strike"] == 24550

    def test_zero_depth_is_atm_and_negative_depth_is_otm(self, sample_option_chain):
        kw = {"atm_strike": SPOT, "hedge_width_points": 150, "step": STEP}
        assert ob.select_credit_spread_itm(sample_option_chain, "BULLISH", itm_depth_points=0, **kw)["short_leg"]["strike"] == 24500
        assert ob.select_credit_spread_itm(sample_option_chain, "BEARISH", itm_depth_points=0, **kw)["short_leg"]["strike"] == 24500
        assert ob.select_credit_spread_itm(sample_option_chain, "BULLISH", itm_depth_points=-100, **kw)["short_leg"]["strike"] == 24400
        assert ob.select_credit_spread_itm(sample_option_chain, "BEARISH", itm_depth_points=-100, **kw)["short_leg"]["strike"] == 24600
        assert ob.select_credit_spread_itm(sample_option_chain, "SIDEWAYS", itm_depth_points=100, **kw) is None


class TestNakedOptionSelection:
    def test_bullish_buys_itm_call_without_hedge_by_default(self, sample_option_chain):
        r = ob.select_naked_option_itm(sample_option_chain, "BULLISH", SPOT, 100, hedge_width_points=150, step=STEP)
        assert r["strategy"] == "NAKED_CALL" and r["buy_leg"]["strike"] == 24400 and "hedge_leg" not in r and r["net_credit"] < 0

    def test_bearish_buys_itm_put(self, sample_option_chain):
        r = ob.select_naked_option_itm(sample_option_chain, "BEARISH", SPOT, 100, hedge_width_points=150, step=STEP)
        assert r["strategy"] == "NAKED_PUT" and r["buy_leg"]["strike"] == 24600 and "hedge_leg" not in r

    def test_hedge_enabled_adds_a_sold_leg(self, sample_option_chain):
        r = ob.select_naked_option_itm(sample_option_chain, "BULLISH", SPOT, 100, hedge_enabled=True, hedge_width_points=150, step=STEP)
        assert r["hedge_leg"]["strike"] == 24550 and r["net_credit"] < 0

    def test_zero_and_negative_depth(self, sample_option_chain):
        kw = {"hedge_width_points": 150, "step": STEP}
        assert ob.select_naked_option_itm(sample_option_chain, "BULLISH", SPOT, 0, **kw)["buy_leg"]["strike"] == 24500
        assert ob.select_naked_option_itm(sample_option_chain, "BEARISH", SPOT, 0, **kw)["buy_leg"]["strike"] == 24500
        assert ob.select_naked_option_itm(sample_option_chain, "BULLISH", SPOT, -100, **kw)["buy_leg"]["strike"] == 24600
        assert ob.select_naked_option_itm(sample_option_chain, "BEARISH", SPOT, -100, **kw)["buy_leg"]["strike"] == 24400
        assert ob.select_naked_option_itm(sample_option_chain, "SIDEWAYS", SPOT, 100, **kw) is None


# --- tests/test_dashboard_strategy_builder.py (strategy result), ported -----------------------------------------------
def _spread_legs(lots):
    return [
        {"direction": "SELL", "option_type": "CE", "strike": 24000.0, "premium": 100.0, "lots": lots, "lot_size": 75, "instrument_key": "A"},
        {"direction": "BUY", "option_type": "CE", "strike": 24100.0, "premium": 40.0, "lots": lots, "lot_size": 75, "instrument_key": "B"},
    ]


def test_strategy_result_is_per_lot_whatever_the_lots():
    rng = ob.build_default_price_range(24000.0, num_points=200, range_pct=5.0)
    res = {lots: ob.build_strategy_result_from_legs(_spread_legs(lots), ob.compute_strategy_payoff_curve(_spread_legs(lots), rng)) for lots in (1, 3)}
    for key in ("net_credit", "max_profit", "max_loss"):
        assert abs(res[1][key] - res[3][key]) < 1e-9, key
    assert res[1]["max_loss"] > 0 and res[1]["is_credit_strategy"] and res[1]["strategy"] == "CUSTOM_MULTI_LEG"
    assert res[1]["net_credit"] == 60.0 and res[1]["max_profit"] == pytest.approx(60.0) and res[1]["max_loss"] == pytest.approx(40.0)


def test_strategy_result_rejects_uneven_lots_in_english():
    legs = _spread_legs(1)
    legs[1]["lots"] = 2
    with pytest.raises(ValueError, match="same lots"):
        ob.build_strategy_result_from_legs(legs, [0.0, 1.0])


# --- what the port makes explicit -------------------------------------------------------------------------------------
@pytest.mark.parametrize("name", [n for group in ob.READY_MADE_CATEGORIES.values() for n in group])
def test_every_template_lists_its_hedges_before_its_shorts(name):
    legs = ob.build_ready_made_strategy(name, 100.0, 10.0)
    dirs = [leg["direction"] for leg in legs]
    assert dirs == sorted(dirs, key=lambda d: d != "BUY")                        # every BUY before every SELL
    assert ob.hedge_first(legs) == legs


def test_hedge_first_reorders_a_basket_stably():
    legs = [{"direction": "SELL", "id": 1}, {"direction": "BUY", "id": 2}, {"direction": "SELL", "id": 3}, {"direction": "BUY", "id": 4}]
    assert [leg["id"] for leg in ob.hedge_first(legs)] == [2, 4, 1, 3]


@pytest.mark.parametrize("name", [n for group in ob.READY_MADE_CATEGORIES.values() for n in group])
def test_breakevens_are_where_the_expiry_payoff_crosses_zero(name):
    """Every template, priced with a simple premium model: each reported breakeven really has a zero payoff, and the
    payoff has opposite signs just either side of it (none of these templates has a flat zero run or a touch)."""
    atm, w = 1000.0, 50.0
    legs = [{**leg, "premium": max(5.0, 40.0 - abs(leg["strike"] - atm) * 0.3), "lot_size": 10} for leg in ob.build_ready_made_strategy(name, atm, w)]
    rng = ob.build_default_price_range(atm, num_points=401, range_pct=30.0)
    curve = ob.compute_strategy_payoff_curve(legs, rng)
    bes = ob.find_breakeven_points(rng, curve)
    assert bes, name
    def pay(x):
        return sum(ob.compute_leg_payoff(x, **leg) for leg in legs)
    for be in bes:
        assert abs(pay(be)) < 10 * 0.01 * 2 + 1e-6, (name, be)                   # rounding to 0.01 of price
        assert pay(be - 1.0) * pay(be + 1.0) < 0, (name, be)                     # a real crossing


def test_no_instrument_specific_defaults_remain():
    """The Trade defaults (step 50, hedge 100 / 150) were NIFTY's; the caller now passes them from the instrument
    master."""
    for fn in (ob.select_credit_spread_fixed_strikes, ob.select_credit_spread_itm, ob.select_naked_option_itm, ob.build_ready_made_strategy):
        params = inspect.signature(fn).parameters
        for name in ("step", "hedge_width_points", "hedge_width", "itm_depth_points"):
            if name in params:
                assert params[name].default is inspect.Parameter.empty, (fn.__name__, name)


def test_model_greeks_net_like_the_platform_strategy_greeks():
    """Leg Greeks come from the platform's Black-Scholes; the ported netting (sign by direction, x lots x lot size)
    gives the same net Greeks as app/option_chain/leg_greeks.compute_strategy_greeks for the same legs."""
    from datetime import date

    from app.option_chain.leg_greeks import compute_strategy_greeks
    from app.option_chain.models import OptionLegInput, OptionType
    from app.options_builder.greeks import leg_with_model_greeks
    expiry, as_of, spot = date(2026, 3, 26), date(2026, 3, 12), 22000.0
    legs = [{**leg, "premium": p, "lot_size": 50, "lots": 2} for leg, p in
            zip(ob.build_ready_made_strategy("Iron Condor", 22000.0, 200.0), (35.0, 30.0, 95.0, 90.0))]
    priced = [leg_with_model_greeks(leg, underlying_price=spot, expiry=expiry, as_of=as_of) for leg in legs]
    net = ob.compute_combined_greeks(priced)
    ref = compute_strategy_greeks([OptionLegInput(strike=leg["strike"], option_type=OptionType.CALL if leg["option_type"] == "CE" else OptionType.PUT,
                                                  quantity=(1 if leg["direction"] == "BUY" else -1) * leg["lots"] * leg["lot_size"],
                                                  underlying_ltp=spot, expiry=expiry.isoformat(), option_ltp=leg["premium"], as_of=as_of.isoformat())
                                   for leg in legs])
    assert net["delta"] == pytest.approx(ref.net_delta) and net["gamma"] == pytest.approx(ref.net_gamma)
    assert net["theta"] == pytest.approx(ref.net_theta) and net["vega"] == pytest.approx(ref.net_vega)
    assert net["theta"] > 0 and net["vega"] < 0                                   # a short condor earns time, loses on IV
    with pytest.raises(ValueError, match="no-arbitrage"):
        leg_with_model_greeks({**legs[0], "premium": 0.0001, "strike": 21000.0, "option_type": "CE"}, underlying_price=spot, expiry=expiry, as_of=as_of)


def _row(k, ce, pe, pop=0.9):
    return {"strike_price": k, "call_options": {"instrument_key": f"C{k}", "market_data": {"ltp": ce}, "option_greeks": {"pop": pop}},
            "put_options": {"instrument_key": f"P{k}", "market_data": {"ltp": pe}, "option_greeks": {"pop": pop}}}


def test_a_condor_or_butterfly_whose_credit_covers_the_wings_is_refused():
    """Quotes that would leave no risk (credit >= wing width) mean a stale or crossed chain, not an opportunity: the
    selectors refuse them (the Trade rule: max_loss > 0). Each case has a positive control with sane quotes."""
    def condor_chain(short, wing):
        return [_row(80, 30, wing), _row(90, 20, short), _row(100, 10, 10), _row(110, short, 20), _row(120, wing, 30)]
    ok = ob.select_iron_condor(condor_chain(6, 2), 100, 10, 10, 50, max_widen_steps=1)
    assert ok is not None and ok["net_credit"] == 8 and ok["max_loss"] == 2
    assert ob.select_iron_condor(condor_chain(25, 2), 100, 10, 10, 50, max_widen_steps=1) is None      # credit 46 > width 10

    def fly_chain(short, wing):
        return [_row(90, 20, wing), _row(100, short, short), _row(110, wing, 20)]
    ok = ob.select_iron_butterfly(fly_chain(5, 1), 100, 10, 0)
    assert ok is not None and ok["net_credit"] == 8 and ok["max_loss"] == 2
    assert ob.select_iron_butterfly(fly_chain(30, 1), 100, 10, 0) is None                             # credit 58 > width 10


def test_inherited_breakeven_edges_are_kept_and_documented():
    """Parity with the source, edges included (docs/design/OPTIONS_BUILDER.md, OB-5): a flat zero run lists every
    point, a touch is listed, the last grid point is not."""
    assert ob.find_breakeven_points([90, 95, 100, 105, 110], [-1, 0, 0, 0, 1]) == [95, 100, 105]
    assert ob.find_breakeven_points([90, 95, 100, 105, 110], [-1, -0.5, 0, -0.5, -1]) == [100]
    assert ob.find_breakeven_points([90, 95, 100], [-2, -1, 0]) == []


def test_model_greeks_take_the_legs_own_iv_and_refuse_percent():
    from datetime import date

    from app.options_builder.greeks import leg_with_model_greeks
    leg = {"direction": "BUY", "option_type": "CE", "strike": 22200.0, "premium": 80.0, "lots": 1, "lot_size": 50}
    kw = {"underlying_price": 22000.0, "expiry": date(2026, 3, 26), "as_of": date(2026, 3, 12)}
    assert leg_with_model_greeks({**leg, "iv": 0.14}, **kw)["iv"] == 0.14                    # the leg's own IV is used
    assert leg_with_model_greeks({**leg, "iv": 0.14}, iv=0.2, **kw)["iv"] == 0.2              # an explicit one wins
    with pytest.raises(ValueError, match="decimal"):
        leg_with_model_greeks({**leg, "iv": 14.5}, **kw)                                      # percent is refused
    with pytest.raises(ValueError, match="no premium"):
        leg_with_model_greeks({**leg, "premium": None}, **kw)
