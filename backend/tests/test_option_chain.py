from datetime import date

import pytest

from app.brokers.models import OptionChain, OptionChainRow
from app.option_chain.analysis import analyze_option_chain, compute_max_pain
from app.option_chain.greeks import BSInputs, black_scholes
from app.option_chain.models import Moneyness, OIActivity, OptionChainBias, OptionType


def test_compute_max_pain_finds_minimum_payout_strike():
    rows = [
        OptionChainRow(strike=100, call_oi=50, put_oi=10),
        OptionChainRow(strike=110, call_oi=30, put_oi=30),
        OptionChainRow(strike=120, call_oi=10, put_oi=50),
    ]
    assert compute_max_pain(rows) == 110


def test_compute_max_pain_empty_returns_none():
    assert compute_max_pain([]) is None


def _chain(rows, underlying_ltp=110.0):
    return OptionChain(underlying="NIFTY", expiry="2024-01-25", underlying_ltp=underlying_ltp, rows=rows)


def test_bullish_bias_requires_pcr_and_oi_agreement():
    rows = [
        OptionChainRow(strike=100, call_oi=20, call_change_oi=-5, put_oi=40, put_change_oi=15),
        OptionChainRow(strike=110, call_oi=15, call_change_oi=-2, put_oi=35, put_change_oi=10),
        OptionChainRow(strike=120, call_oi=10, call_change_oi=-1, put_oi=30, put_change_oi=8),
    ]
    result = analyze_option_chain(_chain(rows))
    assert result.pcr > 1.2
    assert result.bias == OptionChainBias.BULLISH
    assert any("Put OI building" in r for r in result.bias_reasons)


def test_bearish_bias_requires_pcr_and_oi_agreement():
    rows = [
        OptionChainRow(strike=100, call_oi=40, call_change_oi=15, put_oi=20, put_change_oi=-5),
        OptionChainRow(strike=110, call_oi=35, call_change_oi=10, put_oi=15, put_change_oi=-2),
        OptionChainRow(strike=120, call_oi=30, call_change_oi=8, put_oi=10, put_change_oi=-1),
    ]
    result = analyze_option_chain(_chain(rows))
    assert result.pcr < 0.8
    assert result.bias == OptionChainBias.BEARISH


def test_conflicting_bias_when_pcr_and_oi_disagree():
    # PCR heavily bullish (lots of put OI) but the *change* in OI is call-writing (bearish)
    rows = [
        OptionChainRow(strike=100, call_oi=10, call_change_oi=20, put_oi=40, put_change_oi=-10),
        OptionChainRow(strike=110, call_oi=10, call_change_oi=15, put_oi=35, put_change_oi=-8),
        OptionChainRow(strike=120, call_oi=10, call_change_oi=12, put_oi=30, put_change_oi=-5),
    ]
    result = analyze_option_chain(_chain(rows))
    assert result.pcr > 1.2
    assert result.bias == OptionChainBias.CONFLICTING


def test_neutral_bias_when_oi_change_data_unavailable():
    rows = [
        OptionChainRow(strike=100, call_oi=20, put_oi=40),
        OptionChainRow(strike=110, call_oi=15, put_oi=35),
    ]
    result = analyze_option_chain(_chain(rows))
    assert result.total_call_oi_change is None
    assert result.bias == OptionChainBias.NEUTRAL


def test_atm_strike_and_moneyness_classification():
    rows = [
        OptionChainRow(strike=100, call_oi=1, put_oi=1),
        OptionChainRow(strike=110, call_oi=1, put_oi=1),
        OptionChainRow(strike=120, call_oi=1, put_oi=1),
    ]
    result = analyze_option_chain(_chain(rows, underlying_ltp=111.0))
    assert result.atm_strike == 110

    itm_call = next(s for s in result.strikes if s.strike == 100)
    otm_call = next(s for s in result.strikes if s.strike == 120)
    assert itm_call.call_moneyness == Moneyness.ITM
    assert otm_call.call_moneyness == Moneyness.OTM

    otm_put = next(s for s in result.strikes if s.strike == 100)
    itm_put = next(s for s in result.strikes if s.strike == 120)
    assert otm_put.put_moneyness == Moneyness.OTM
    assert itm_put.put_moneyness == Moneyness.ITM


def test_atm_strike_itself_is_tagged_atm_for_a_realistic_non_integer_ltp():
    """Regression test: a real underlying price is essentially never exactly equal to a strike,
    so classifying ATM by `strike == underlying_ltp` would never mark any strike ATM at all.
    The strike nearest the underlying (already exposed as `atm_strike`) must be the one tagged
    Moneyness.ATM in the per-strike breakdown, for both legs.
    """
    rows = [
        OptionChainRow(strike=24100, call_oi=1, put_oi=1),
        OptionChainRow(strike=24150, call_oi=1, put_oi=1),
        OptionChainRow(strike=24200, call_oi=1, put_oi=1),
    ]
    result = analyze_option_chain(_chain(rows, underlying_ltp=24152.35))
    assert result.atm_strike == 24150

    atm_row = next(s for s in result.strikes if s.strike == 24150)
    assert atm_row.call_moneyness == Moneyness.ATM
    assert atm_row.put_moneyness == Moneyness.ATM


def test_call_writing_and_put_writing_activity_labels():
    rows = [OptionChainRow(strike=100, call_oi=20, call_change_oi=5, put_oi=20, put_change_oi=-3)]
    result = analyze_option_chain(_chain(rows))
    strike = result.strikes[0]
    assert strike.call_activity == OIActivity.CALL_WRITING
    assert strike.put_activity == OIActivity.PUT_UNWINDING


def test_call_resistance_and_put_support_strikes_ranked_by_oi():
    rows = [
        OptionChainRow(strike=100, call_oi=5, put_oi=50),
        OptionChainRow(strike=110, call_oi=80, put_oi=5),
        OptionChainRow(strike=120, call_oi=10, put_oi=8),
    ]
    result = analyze_option_chain(_chain(rows), top_n=1)
    assert result.call_resistance_strikes == [110]
    assert result.put_support_strikes == [100]


def test_greeks_solved_from_real_quoted_ltp_when_chain_has_enough_data():
    as_of = date(2098, 12, 1)
    known_price = black_scholes(
        BSInputs(110.0, 110.0, 45 / 365.0, 0.07, 0.22, OptionType.CALL)
    ).theoretical_price
    rows = [OptionChainRow(strike=110, call_oi=20, call_ltp=known_price, put_oi=20, put_ltp=5.0)]
    chain = OptionChain(underlying="NIFTY", expiry="2099-01-15", underlying_ltp=110.0, rows=rows)

    result = analyze_option_chain(chain, as_of=as_of)
    strike = result.strikes[0]
    assert strike.call_greeks is not None
    assert strike.call_greeks.implied_volatility == pytest.approx(0.22, abs=1e-3)
    assert strike.call_greeks.delta > 0


def test_greeks_none_when_expiry_or_underlying_ltp_missing():
    rows = [OptionChainRow(strike=100, call_oi=20, call_ltp=5.0)]
    chain_no_expiry = OptionChain(underlying="NIFTY", expiry="", underlying_ltp=100.0, rows=rows)
    result = analyze_option_chain(chain_no_expiry)
    assert result.strikes[0].call_greeks is None


def test_greeks_none_for_unpriced_strikes():
    # No call_ltp/call_iv supplied at all - never fabricate Greeks from nothing.
    result = analyze_option_chain(_chain([OptionChainRow(strike=110, call_oi=20, put_oi=20)]))
    assert result.strikes[0].call_greeks is None
    assert result.strikes[0].put_greeks is None
