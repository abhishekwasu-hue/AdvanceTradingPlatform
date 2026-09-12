from datetime import date

import pytest

from app.core.enums import Bias, PeriodType, RiskLevel
from app.fundamentals.engines.peer_comparison import PeerComparisonEngine
from app.fundamentals.engines.pre_earnings import PreEarningsEngine
from app.fundamentals.models import FinancialPeriod, RedFlag


def _period(revenue=1000.0, ebitda=250.0, ebit=200.0, pat=100.0, total_debt=300.0, shareholders_equity=500.0,
            label="FY24", end_date=date(2024, 3, 31)):
    return FinancialPeriod(
        period_type=PeriodType.ANNUAL, period_label=label, period_end_date=end_date,
        revenue=revenue, ebitda=ebitda, ebit=ebit, pat=pat, eps=20.0, shares_outstanding=50.0,
        cfo=90.0, capex=40.0, total_debt=total_debt, cash_and_equivalents=50.0, current_assets=400.0,
        current_liabilities=300.0, shareholders_equity=shareholders_equity, total_assets=900.0, interest_expense=20.0,
    )


def test_peer_comparison_ranks_by_roce_and_skips_companies_without_periods():
    strong = ("STRONG", "Strong Co", [_period(ebit=200.0, shareholders_equity=500.0)])
    weak = ("WEAK", "Weak Co", [_period(ebit=20.0, shareholders_equity=500.0)])
    no_data = ("EMPTY", "Empty Co", [])

    results = PeerComparisonEngine().compare([strong, weak, no_data])

    symbols = [r.symbol for r in results]
    assert symbols == ["STRONG", "WEAK"]
    assert results[0].roce_pct > results[1].roce_pct


def test_peer_comparison_falls_back_to_ebitda_margin_when_roce_unavailable():
    a = ("A", "A Co", [_period(ebitda=400.0, ebit=None)])
    b = ("B", "B Co", [_period(ebitda=100.0, ebit=None)])

    results = PeerComparisonEngine().compare([a, b])

    assert [r.symbol for r in results] == ["A", "B"]
    assert results[0].roce_pct is None


def test_pre_earnings_engine_requires_periods():
    with pytest.raises(ValueError):
        PreEarningsEngine().analyze([], upcoming_event_date=date(2099, 1, 1), red_flags=[])


def test_pre_earnings_engine_bullish_when_growth_healthy_and_no_high_severity_flags():
    fy22 = _period(revenue=700.0, ebitda=140.0, label="FY22", end_date=date(2022, 3, 31))
    fy23 = _period(revenue=850.0, ebitda=200.0, label="FY23", end_date=date(2023, 3, 31))
    fy24 = _period(revenue=1000.0, ebitda=280.0, label="FY24", end_date=date(2024, 3, 31))
    result = PreEarningsEngine().analyze([fy22, fy23, fy24], upcoming_event_date=date(2099, 1, 1), red_flags=[])

    assert result.earnings_bias == Bias.BULLISH
    assert result.risk_level == RiskLevel.LOW
    assert result.upcoming_event_date == date(2099, 1, 1)


def test_pre_earnings_engine_bearish_with_high_severity_red_flags():
    period = _period()
    flags = [
        RedFlag(code="LIQUIDITY_RISK", description="Current ratio below 1", severity=RiskLevel.HIGH),
        RedFlag(code="DEBT_RISK", description="Interest coverage weak", severity=RiskLevel.EXTREME),
    ]
    result = PreEarningsEngine().analyze([period], upcoming_event_date=date(2099, 1, 1), red_flags=flags)

    assert result.earnings_bias == Bias.BEARISH
    assert result.risk_level == RiskLevel.EXTREME
    assert result.red_flag_count == 2
