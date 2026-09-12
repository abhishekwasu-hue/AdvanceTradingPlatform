from app.core.enums import FundamentalGrade, FusionBias, InvestmentHorizon, SignalDirection
from app.fundamentals.engines.score import FundamentalScoreEngine, FusionEngine, WEIGHTS


def test_weights_sum_to_100():
    assert sum(WEIGHTS.values()) == 100.0


def test_fundamental_score_engine_full_marks_all_components():
    components = {k: 90.0 for k in WEIGHTS}
    result = FundamentalScoreEngine().compute(components)
    assert result.score == 90.0
    assert result.grade == FundamentalGrade.EXCEPTIONAL
    assert len(result.breakdown) == len(WEIGHTS)


def test_fundamental_score_engine_missing_component_scored_neutral():
    components = {k: 90.0 for k in WEIGHTS if k != "valuation"}
    result = FundamentalScoreEngine().compute(components)
    valuation_row = next(b for b in result.breakdown if b.component == "Valuation")
    assert valuation_row.score_0_100 == 50.0
    assert "neutral" in valuation_row.note.lower()


def test_fundamental_score_classification_bands():
    engine = FundamentalScoreEngine()
    assert engine.compute({k: 95 for k in WEIGHTS}).grade == FundamentalGrade.EXCEPTIONAL
    assert engine.compute({k: 55 for k in WEIGHTS}).grade == FundamentalGrade.WEAK
    assert engine.compute({k: 30 for k in WEIGHTS}).grade == FundamentalGrade.POOR


def test_fusion_a1_long_bias_when_both_strong_and_composite_high():
    result = FusionEngine().compute(
        fundamental_score=88, technical_score=91, technical_direction=SignalDirection.LONG,
    )
    assert result.bias == FusionBias.A1_LONG_BIAS
    assert result.final_score > 85


def test_fusion_a1_short_bias_respects_technical_direction():
    result = FusionEngine().compute(
        fundamental_score=88, technical_score=91, technical_direction=SignalDirection.SHORT,
    )
    assert result.bias == FusionBias.A1_SHORT_BIAS


def test_fusion_caution_when_technical_strong_but_fundamental_weak():
    result = FusionEngine().compute(
        fundamental_score=40, technical_score=90, technical_direction=SignalDirection.LONG,
    )
    assert result.bias == FusionBias.CAUTION


def test_fusion_watchlist_when_fundamental_strong_but_technical_weak():
    result = FusionEngine().compute(
        fundamental_score=85, technical_score=40, technical_direction=SignalDirection.LONG,
    )
    assert result.bias == FusionBias.WATCHLIST


def test_fusion_no_trade_when_both_weak():
    result = FusionEngine().compute(
        fundamental_score=30, technical_score=30, technical_direction=SignalDirection.LONG,
    )
    assert result.bias == FusionBias.NO_TRADE


def test_fusion_no_trade_when_technical_direction_is_no_trade_regardless_of_fundamentals():
    result = FusionEngine().compute(
        fundamental_score=95, technical_score=95, technical_direction=SignalDirection.NO_TRADE,
    )
    assert result.bias == FusionBias.NO_TRADE


def test_fusion_includes_sector_and_event_scores_in_final_average():
    result = FusionEngine().compute(
        fundamental_score=84, technical_score=91, technical_direction=SignalDirection.LONG,
        sector_score=82, event_risk_score=76, horizon=InvestmentHorizon.SWING,
    )
    assert result.final_score == round((84 + 91 + 82 + 76) / 4, 1)
    assert result.horizon == InvestmentHorizon.SWING
