from datetime import date

import pytest

from app.option_chain.greeks import (
    BSInputs,
    black_scholes,
    implied_volatility,
    time_to_expiry_years,
)
from app.option_chain.leg_greeks import compute_leg_greeks, compute_strategy_greeks
from app.option_chain.models import OptionLegInput, OptionType


def test_time_to_expiry_years_basic():
    assert time_to_expiry_years(date(2024, 2, 1), date(2024, 1, 1)) == pytest.approx(31 / 365.0)


def test_time_to_expiry_years_floors_at_expiry_or_past():
    # An expired (or same-day) option must not divide by zero - it gets a small positive floor.
    assert time_to_expiry_years(date(2024, 1, 1), date(2024, 1, 1)) > 0
    assert time_to_expiry_years(date(2024, 1, 1), date(2024, 2, 1)) > 0


def test_atm_call_delta_is_near_half():
    result = black_scholes(BSInputs(
        underlying_price=100.0, strike=100.0, time_to_expiry_years=30 / 365.0,
        risk_free_rate=0.07, volatility=0.20, option_type=OptionType.CALL,
    ))
    assert 0.45 < result.delta < 0.65  # slightly above 0.5 due to positive rate drift


def test_atm_put_delta_is_near_negative_half():
    result = black_scholes(BSInputs(
        underlying_price=100.0, strike=100.0, time_to_expiry_years=30 / 365.0,
        risk_free_rate=0.07, volatility=0.20, option_type=OptionType.PUT,
    ))
    assert -0.55 < result.delta < -0.35


def test_deep_itm_call_delta_near_one_deep_otm_near_zero():
    itm = black_scholes(BSInputs(100.0, 50.0, 30 / 365.0, 0.07, 0.20, OptionType.CALL))
    otm = black_scholes(BSInputs(100.0, 200.0, 30 / 365.0, 0.07, 0.20, OptionType.CALL))
    assert itm.delta > 0.95
    assert otm.delta < 0.05


def test_call_and_put_share_the_same_gamma_and_vega_at_same_strike():
    call = black_scholes(BSInputs(100.0, 100.0, 30 / 365.0, 0.07, 0.20, OptionType.CALL))
    put = black_scholes(BSInputs(100.0, 100.0, 30 / 365.0, 0.07, 0.20, OptionType.PUT))
    assert call.gamma == pytest.approx(put.gamma)
    assert call.vega == pytest.approx(put.vega)


def test_gamma_and_vega_are_positive_for_long_options():
    result = black_scholes(BSInputs(100.0, 100.0, 30 / 365.0, 0.07, 0.20, OptionType.CALL))
    assert result.gamma > 0
    assert result.vega > 0


def test_theta_is_negative_for_a_long_atm_option_time_decay():
    call = black_scholes(BSInputs(100.0, 100.0, 30 / 365.0, 0.07, 0.20, OptionType.CALL))
    put = black_scholes(BSInputs(100.0, 100.0, 30 / 365.0, 0.07, 0.20, OptionType.PUT))
    assert call.theta < 0
    assert put.theta < 0


def test_black_scholes_rejects_non_positive_inputs():
    with pytest.raises(ValueError):
        black_scholes(BSInputs(100.0, 100.0, 30 / 365.0, 0.07, 0.0, OptionType.CALL))
    with pytest.raises(ValueError):
        black_scholes(BSInputs(-1.0, 100.0, 30 / 365.0, 0.07, 0.2, OptionType.CALL))


def test_implied_volatility_round_trips_through_black_scholes():
    known_sigma = 0.25
    price = black_scholes(BSInputs(100.0, 105.0, 45 / 365.0, 0.07, known_sigma, OptionType.CALL)).theoretical_price
    solved = implied_volatility(price, 100.0, 105.0, 45 / 365.0, 0.07, OptionType.CALL)
    assert solved == pytest.approx(known_sigma, abs=1e-4)


def test_implied_volatility_returns_none_below_intrinsic_value():
    # A call struck at 50 on a 100 underlying has intrinsic value 50 - a quoted price of 10
    # is a stale/crossed/impossible quote, not something to solve a volatility for.
    assert implied_volatility(10.0, 100.0, 50.0, 30 / 365.0, 0.07, OptionType.CALL) is None


def test_implied_volatility_returns_none_above_theoretical_maximum():
    # A call can never be worth more than the underlying itself.
    assert implied_volatility(150.0, 100.0, 100.0, 30 / 365.0, 0.07, OptionType.CALL) is None


def _leg(strike=100.0, option_type=OptionType.CALL, quantity=50, iv=0.2, expiry="2099-01-01"):
    return OptionLegInput(
        strike=strike, option_type=option_type, quantity=quantity, underlying_ltp=100.0,
        expiry=expiry, implied_volatility=iv, as_of="2098-12-01",
    )


def test_compute_leg_greeks_with_supplied_iv():
    result = compute_leg_greeks(_leg())
    assert result.greeks.implied_volatility == 0.2
    assert result.position_delta == pytest.approx(result.greeks.delta * 50)
    assert result.position_gamma == pytest.approx(result.greeks.gamma * 50)


def test_compute_leg_greeks_solves_iv_from_option_ltp():
    known_price = black_scholes(BSInputs(100.0, 100.0, 30 / 365.0, 0.07, 0.22, OptionType.CALL)).theoretical_price
    leg = OptionLegInput(
        strike=100.0, option_type=OptionType.CALL, quantity=10, underlying_ltp=100.0,
        expiry="2099-01-01", option_ltp=known_price, as_of="2098-12-02",
    )
    result = compute_leg_greeks(leg)
    assert result.greeks.implied_volatility == pytest.approx(0.22, abs=1e-3)


def test_compute_leg_greeks_requires_price_or_iv():
    leg = OptionLegInput(
        strike=100.0, option_type=OptionType.CALL, quantity=10, underlying_ltp=100.0, expiry="2099-01-01",
    )
    with pytest.raises(ValueError):
        compute_leg_greeks(leg)


def test_short_leg_has_inverted_position_greeks_vs_long_leg():
    long_leg = compute_leg_greeks(_leg(quantity=50))
    short_leg = compute_leg_greeks(_leg(quantity=-50))
    assert short_leg.position_delta == pytest.approx(-long_leg.position_delta)
    assert short_leg.position_gamma == pytest.approx(-long_leg.position_gamma)
    assert short_leg.position_theta == pytest.approx(-long_leg.position_theta)
    assert short_leg.position_vega == pytest.approx(-long_leg.position_vega)


def test_strategy_greeks_nets_across_legs():
    # A long call + a short call at the same strike/expiry/IV must net to ~zero Greeks.
    legs = [_leg(quantity=50), _leg(quantity=-50)]
    result = compute_strategy_greeks(legs)
    assert result.net_delta == pytest.approx(0.0, abs=1e-9)
    assert result.net_gamma == pytest.approx(0.0, abs=1e-9)
    assert result.net_theta == pytest.approx(0.0, abs=1e-9)
    assert result.net_vega == pytest.approx(0.0, abs=1e-9)
    assert len(result.legs) == 2


def test_bull_call_spread_has_positive_but_bounded_delta():
    legs = [
        _leg(strike=100.0, quantity=50),
        _leg(strike=110.0, quantity=-50),
    ]
    result = compute_strategy_greeks(legs)
    assert 0 < result.net_delta < 50  # long lower-strike call dominates, but capped by the short leg
