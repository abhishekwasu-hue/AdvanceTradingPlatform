from datetime import date

from app.core.enums import Bias, PeriodType, RiskLevel, TrendLabel
from app.fundamentals.engines.statement_analysis import (
    BalanceSheetEngine,
    CashFlowEngine,
    EarningsQualityEngine,
    ProfitabilityEngine,
    QuarterlyComparisonEngine,
    RevenueAnalysisEngine,
)
from app.fundamentals.models import FinancialPeriod


def _annual(label: str, year: int, revenue: float, ebitda: float, pat: float, **kw) -> FinancialPeriod:
    return FinancialPeriod(
        period_type=PeriodType.ANNUAL, period_label=label, period_end_date=date(year, 3, 31),
        revenue=revenue, ebitda=ebitda, pat=pat, **kw,
    )


def _quarter(label: str, year: int, month: int, revenue: float, ebitda: float, pat: float, **kw) -> FinancialPeriod:
    return FinancialPeriod(
        period_type=PeriodType.QUARTER, period_label=label, period_end_date=date(year, month, 28),
        revenue=revenue, ebitda=ebitda, pat=pat, **kw,
    )


def test_revenue_engine_computes_yoy_and_cagr():
    periods = [
        _annual("FY20", 2020, 100, 20, 10),
        _annual("FY21", 2021, 110, 22, 11),
        _annual("FY22", 2022, 125, 26, 13),
        _annual("FY23", 2023, 140, 30, 15),
    ]
    result = RevenueAnalysisEngine().analyze(periods)
    assert result.latest_period == "FY23"
    assert round(result.yoy_growth_pct, 1) == 12.0
    assert result.cagr_3y_pct is not None
    assert result.cagr_3y_pct > 0


def test_revenue_engine_requires_at_least_one_period():
    import pytest
    with pytest.raises(ValueError):
        RevenueAnalysisEngine().analyze([])


def test_profitability_engine_detects_improving_margin_with_growing_revenue():
    periods = [
        _annual("FY21", 2021, 100, 18, 8),
        _annual("FY22", 2022, 120, 24, 12),
        _annual("FY23", 2023, 140, 30, 16, ebit=25, shareholders_equity=100, total_assets=350, current_liabilities=200),
    ]
    result = ProfitabilityEngine().analyze(periods)
    assert result.margin_trend == TrendLabel.IMPROVING
    assert result.ebitda_margin_pct is not None
    assert result.roe_pct == 16.0  # pat=16 / equity=100 * 100
    assert result.roce_pct is not None  # capital_employed derived as total_assets - current_liabilities = 150


def test_earnings_quality_flags_low_cfo_relative_to_pat():
    period = _annual("FY23", 2023, 500, 100, 50, cfo=15, other_income=2, exceptional_items=1)
    result = EarningsQualityEngine().analyze(period)
    assert result.cfo_to_pat_ratio == 0.3
    assert len(result.warnings) >= 1
    assert result.label.value in ("Average", "Weak")


def test_earnings_quality_strong_when_cfo_covers_pat():
    period = _annual("FY23", 2023, 500, 100, 50, cfo=55, other_income=1, exceptional_items=0)
    result = EarningsQualityEngine().analyze(period)
    assert result.warnings == []
    assert result.label.value == "Strong"


def test_quarterly_comparison_engine_yoy_and_qoq():
    periods = [
        _quarter("Q2FY23", 2022, 9, 100, 20, 10),
        _quarter("Q3FY23", 2022, 12, 105, 21, 10.5),
        _quarter("Q4FY23", 2023, 3, 110, 22, 11),
        _quarter("Q1FY24", 2023, 6, 115, 23, 11.5),
        _quarter("Q2FY24", 2023, 9, 130, 28, 14),
    ]
    result = QuarterlyComparisonEngine().analyze(periods)
    assert result.period_label == "Q2FY24"
    revenue_cmp = next(c for c in result.comparisons if c.metric == "Revenue")
    assert revenue_cmp.qoq_prior == 115
    assert revenue_cmp.yoy_prior == 100
    assert revenue_cmp.direction == "↑"
    assert result.overall_quality == Bias.BULLISH


def test_balance_sheet_engine_flags_high_debt():
    period = _annual(
        "FY23", 2023, 500, 50, 20,
        total_debt=300, cash_and_equivalents=10, interest_expense=30, ebit=40,
        current_assets=80, current_liabilities=120, inventory=20, shareholders_equity=100,
    )
    result = BalanceSheetEngine().analyze(period)
    assert result.debt_to_equity == 3.0
    assert result.net_debt_to_ebitda == (290 / 50)
    assert result.debt_risk == RiskLevel.EXTREME
    assert result.liquidity_risk == RiskLevel.HIGH


def test_cash_flow_engine_warns_when_pat_outpaces_cfo():
    period = _annual("FY23", 2023, 500, 100, 60, cfo=20, capex=10)
    result = CashFlowEngine().analyze(period, market_cap=1000)
    assert result.fcf == 10
    assert result.warning is not None
