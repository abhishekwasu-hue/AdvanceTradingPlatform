from datetime import date

from app.core.enums import PeriodType, ValuationLabel
from app.fundamentals.engines.valuation import DCFEngine, ValuationEngine
from app.fundamentals.models import DCFAssumptions, FinancialPeriod


def _period(**kw) -> FinancialPeriod:
    defaults = dict(
        period_type=PeriodType.ANNUAL, period_label="FY23", period_end_date=date(2023, 3, 31),
        revenue=1000.0, ebitda=200.0, pat=100.0,
    )
    defaults.update(kw)
    return FinancialPeriod(**defaults)


def test_valuation_engine_computes_core_multiples():
    period = _period(eps=10.0)
    result = ValuationEngine().analyze(
        market_price=150.0, shares_outstanding=100.0, latest=period,
        book_value_per_share=50.0, net_debt=200.0,
    )
    assert result.pe == 15.0
    assert result.pb == 3.0
    assert result.ev_ebitda == (150 * 100 + 200) / 200
    assert result.classification == ValuationLabel.FAIRLY_VALUED  # no peer/historical comparison supplied


def test_valuation_engine_classifies_expensive_vs_history_and_peers():
    period = _period(eps=10.0)
    result = ValuationEngine().analyze(
        market_price=200.0, shares_outstanding=100.0, latest=period,
        historical_pe_avg_5y=12.0, peer_pe_avg=14.0,
    )
    # pe = 200/10 = 20, vs history 12 (+66%) and peers 14 (+43%) -> clearly expensive
    assert result.pe == 20.0
    assert result.classification in (ValuationLabel.EXPENSIVE, ValuationLabel.EXTREMELY_EXPENSIVE)


def test_valuation_engine_classifies_undervalued():
    period = _period(eps=10.0)
    result = ValuationEngine().analyze(
        market_price=80.0, shares_outstanding=100.0, latest=period,
        historical_pe_avg_5y=12.0, peer_pe_avg=12.0,
    )
    # pe = 8, vs 12 avg = -33% -> deeply undervalued
    assert result.classification == ValuationLabel.DEEPLY_UNDERVALUED


def test_dcf_engine_produces_bull_base_bear_with_bull_above_bear():
    assumptions = DCFAssumptions(
        base_revenue=1000.0, revenue_growth_pct=[10, 10, 8, 8, 6],
        ebitda_margin_pct=20.0, tax_rate_pct=25.0, capex_pct_of_revenue=5.0,
        working_capital_pct_of_revenue=2.0, wacc_pct=11.0, terminal_growth_pct=4.0,
        net_debt=500.0, shares_outstanding=100.0,
    )
    result = DCFEngine().run(assumptions, current_market_price=120.0)
    assert result.bull.intrinsic_value_per_share > result.base.intrinsic_value_per_share
    assert result.base.intrinsic_value_per_share > result.bear.intrinsic_value_per_share
    assert result.upside_pct is not None
    assert len(result.base.projected_fcf) == 5


def test_dcf_engine_handles_zero_net_debt():
    assumptions = DCFAssumptions(
        base_revenue=500.0, revenue_growth_pct=[12, 12, 10],
        ebitda_margin_pct=25.0, wacc_pct=10.0, terminal_growth_pct=3.0,
        net_debt=0.0, shares_outstanding=50.0,
    )
    result = DCFEngine().run(assumptions)
    assert result.base.equity_value == result.base.enterprise_value
