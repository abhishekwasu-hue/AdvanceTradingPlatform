from datetime import date

import pytest

from app.core.enums import Bias, RiskLevel, ValuationLabel
from app.fundamentals.engines.alerts import AlertEngine
from app.fundamentals.engines.event_impact import EventImpactEngine
from app.fundamentals.engines.sector_specific import SectorSpecificEngine
from app.fundamentals.models import CorporateAction, DCFResult, DCFScenarioResult, RedFlag, SectorMetric, ValuationResult


def _dcf_result(margin_of_safety_pct):
    scenario = DCFScenarioResult(
        scenario="Base", intrinsic_value_per_share=100.0, projected_fcf=[10, 11, 12, 13, 14],
        terminal_value=200.0, enterprise_value=300.0, equity_value=280.0,
    )
    return DCFResult(base=scenario, bull=scenario, bear=scenario, margin_of_safety_pct=margin_of_safety_pct)


def test_sector_specific_engine_classifies_banking_metrics():
    metrics = [
        SectorMetric(period_label="FY24", metric_code="NIM_PCT", value=4.5),
        SectorMetric(period_label="FY24", metric_code="GNPA_PCT", value=6.0),
    ]
    result = SectorSpecificEngine().analyze("banking", metrics)

    assert result.sector == "BANKING"
    nim = next(m for m in result.metrics if m.metric_code == "NIM_PCT")
    gnpa = next(m for m in result.metrics if m.metric_code == "GNPA_PCT")
    assert nim.classification.value == "Strong"
    assert gnpa.classification.value == "Weak"


def test_sector_specific_engine_rejects_unknown_sector():
    with pytest.raises(ValueError):
        SectorSpecificEngine().analyze("NOT_A_SECTOR", [SectorMetric(period_label="FY24", metric_code="NIM_PCT", value=4.0)])


def test_sector_specific_engine_skips_unrecognized_metric_codes():
    with pytest.raises(ValueError):
        SectorSpecificEngine().analyze("BANKING", [SectorMetric(period_label="FY24", metric_code="MADE_UP_METRIC", value=1.0)])


def test_sector_specific_engine_requires_metrics():
    with pytest.raises(ValueError):
        SectorSpecificEngine().analyze("BANKING", [])


def test_event_impact_engine_weights_recent_events_more_and_requires_actions():
    with pytest.raises(ValueError):
        EventImpactEngine().analyze([])

    recent_bullish = CorporateAction(
        action_type="ORDER_WIN", announced_date=date(2024, 3, 1), headline="Large order win",
        expected_revenue_impact=Bias.BULLISH, expected_margin_impact=Bias.BULLISH, expected_eps_impact=Bias.BULLISH,
    )
    old_bearish = CorporateAction(
        action_type="REGULATORY_ACTION", announced_date=date(2020, 1, 1), headline="Old regulatory setback",
        expected_revenue_impact=Bias.BEARISH, expected_margin_impact=Bias.BEARISH, expected_eps_impact=Bias.BEARISH,
    )
    result = EventImpactEngine().analyze([recent_bullish, old_bearish], as_of=date(2024, 3, 15))

    assert result.events_considered == 2
    assert result.score > 0
    assert result.bias == Bias.BULLISH


def test_event_impact_engine_skips_actions_with_no_recorded_impact():
    no_impact = CorporateAction(action_type="AGM", announced_date=date(2024, 1, 1), headline="Annual meeting")
    with pytest.raises(ValueError):
        EventImpactEngine().analyze([no_impact])


def test_alert_engine_surfaces_high_severity_red_flags_and_sorts_by_severity():
    flags = [
        RedFlag(code="LIQUIDITY_RISK", severity=RiskLevel.MEDIUM, description="Current ratio soft"),
        RedFlag(code="DEBT_RISK", severity=RiskLevel.EXTREME, description="Interest coverage critical"),
        RedFlag(code="AUDITOR_CHANGE", severity=RiskLevel.HIGH, description="Auditor resigned"),
    ]
    alerts = AlertEngine().analyze(red_flags=flags)

    codes = [a.code for a in alerts]
    assert "DEBT_RISK" in codes and "AUDITOR_CHANGE" in codes
    assert "LIQUIDITY_RISK" not in codes  # medium severity red flags are not alert-worthy
    assert alerts[0].severity == RiskLevel.EXTREME


def test_alert_engine_flags_extreme_valuation_dcf_overvaluation_and_imminent_earnings():
    valuation = ValuationResult(classification=ValuationLabel.EXTREMELY_EXPENSIVE, notes=[])
    dcf = _dcf_result(margin_of_safety_pct=-35.0)

    alerts = AlertEngine().analyze(valuation=valuation, dcf=dcf, days_to_next_results=3)
    codes = {a.code for a in alerts}
    assert codes == {"VALUATION_EXTREME", "DCF_OVERVALUED", "EARNINGS_IMMINENT"}


def test_alert_engine_returns_empty_list_when_nothing_is_wrong():
    assert AlertEngine().analyze() == []
