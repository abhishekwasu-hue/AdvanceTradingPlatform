from datetime import date

import pytest

from app.core.enums import PeriodType, QualityLabel, RiskLevel, TrendLabel
from app.fundamentals.engines.business_quality import BusinessQualityEngine
from app.fundamentals.engines.red_flags import RedFlagEngine
from app.fundamentals.engines.scenario import ScenarioEngine
from app.fundamentals.engines.swot import SWOTEngine
from app.fundamentals.models import (
    BalanceSheetAnalysis,
    CashFlowAnalysis,
    CorporateAction,
    EarningsQualityResult,
    FinancialPeriod,
    ProfitabilityAnalysis,
    QualitativeFactor,
    ShareholdingSnapshot,
)


def test_business_quality_engine_averages_moat_factors():
    factors = [
        QualitativeFactor(category="BUSINESS_QUALITY_MOAT", label="Pricing Power", score=80),
        QualitativeFactor(category="BUSINESS_QUALITY_MOAT", label="Brand Strength", score=90),
        QualitativeFactor(category="BUSINESS_QUALITY_MOAT", label="Entry Barriers", score=70),
    ]
    result = BusinessQualityEngine().analyze(factors)
    assert result.score == 80.0
    assert result.label == QualityLabel.STRONG


def test_business_quality_engine_requires_at_least_one_factor():
    with pytest.raises(ValueError):
        BusinessQualityEngine().analyze([])


def test_red_flag_engine_detects_promoter_pledge_increase():
    history = [
        ShareholdingSnapshot(as_of_date=date(2023, 3, 31), promoter_pct=55, promoter_pledge_pct=2),
        ShareholdingSnapshot(as_of_date=date(2023, 12, 31), promoter_pct=52, promoter_pledge_pct=8),
    ]
    flags = RedFlagEngine().analyze(shareholding_history=history)
    codes = {f.code for f in flags}
    assert "PROMOTER_PLEDGE_INCREASE" in codes
    assert "PROMOTER_HOLDING_DECREASE" in codes


def test_red_flag_engine_aggregates_across_sub_engines():
    eq = EarningsQualityResult(cfo_to_pat_ratio=0.2, label=QualityLabel.WEAK, warnings=["CFO well below PAT"])
    bs = BalanceSheetAnalysis(debt_risk=RiskLevel.EXTREME, liquidity_risk=RiskLevel.HIGH, current_ratio=0.8, notes=["Net Debt/EBITDA very high"])
    cf = CashFlowAnalysis(warning="PAT outpaces cash flow")
    actions = [CorporateAction(action_type="AUDITOR_CHANGE", announced_date=date(2024, 1, 1), headline="Auditor resigned")]

    flags = RedFlagEngine().analyze(earnings_quality=eq, balance_sheet=bs, cash_flow=cf, corporate_actions=actions)
    codes = {f.code for f in flags}
    assert "EARNINGS_QUALITY" in codes
    assert "DEBT_RISK" in codes
    assert "LIQUIDITY_RISK" in codes
    assert "CASH_FLOW" in codes
    assert "ACTION_AUDITOR_CHANGE" in codes


def test_swot_engine_combines_qualitative_and_computed_signals():
    qualitative = [
        QualitativeFactor(category="SWOT_OPPORTUNITY", label="New export market opening up"),
        QualitativeFactor(category="SWOT_THREAT", label="New low-cost competitor entering"),
    ]
    profitability = ProfitabilityAnalysis(margin_trend=TrendLabel.IMPROVING)
    balance_sheet = BalanceSheetAnalysis(debt_risk=RiskLevel.HIGH, liquidity_risk=RiskLevel.LOW)
    result = SWOTEngine().analyze(qualitative, profitability=profitability, balance_sheet=balance_sheet)
    assert any("export" in s.lower() for s in result.opportunities)
    assert any("competitor" in t.lower() for t in result.threats)
    assert any("expanding" in s.lower() for s in result.strengths)
    assert any("debt" in w.lower() for w in result.weaknesses)


def test_scenario_engine_bull_above_base_above_bear():
    latest = FinancialPeriod(
        period_type=PeriodType.ANNUAL, period_label="FY23", period_end_date=date(2023, 3, 31),
        revenue=1000.0, ebitda=200.0, pat=100.0, shares_outstanding=50.0,
    )
    scenarios = ScenarioEngine().project(latest)
    assert scenarios["bull"].revenue > scenarios["base"].revenue > scenarios["bear"].revenue
    assert scenarios["bull"].pat > scenarios["bear"].pat
