from app.strategy_engine.nlu_parser import parse_strategy_description
from tests.test_auth_api import client


def test_parses_indicator_conditions_with_explicit_and_default_periods():
    result = parse_strategy_description("Buy when RSI(14) crosses above 60 and price is above EMA 50.")
    assert len(result.config.long_conditions) == 2
    assert result.config.short_conditions == []

    rsi_condition = result.config.long_conditions[0]
    assert rsi_condition.left.indicator == "RSI"
    assert rsi_condition.left.period == 14
    assert rsi_condition.operator == "CROSSES_ABOVE"
    assert rsi_condition.right.type == "value"
    assert rsi_condition.right.value == 60.0

    price_condition = result.config.long_conditions[1]
    assert price_condition.left.indicator == "CLOSE"
    assert price_condition.operator == "GT"
    assert price_condition.right.indicator == "EMA"
    assert price_condition.right.period == 50

    assert not result.warnings
    assert len(result.interpreted) == 2


def test_bare_indicator_defaults_to_period_14():
    result = parse_strategy_description("Sell when RSI crosses below 40.")
    condition = result.config.short_conditions[0]
    assert condition.left.indicator == "RSI"
    assert condition.left.period == 14


def test_period_before_indicator_syntax():
    result = parse_strategy_description("Buy when 20 EMA crosses above 50 EMA.")
    condition = result.config.long_conditions[0]
    assert condition.left.indicator == "EMA" and condition.left.period == 20
    assert condition.right.indicator == "EMA" and condition.right.period == 50


def test_risk_parameters_parsed_from_separate_sentences():
    text = (
        "Buy when RSI crosses above 60. "
        "Use 1.5x ATR stop loss with ATR period 10. "
        "Target risk reward of 2 with a minimum risk reward of 1.2. "
        "Use the 15min timeframe."
    )
    result = parse_strategy_description(text)
    assert result.config.stop_loss_atr_mult == 1.5
    assert result.config.atr_period == 10
    assert result.config.target_rr == (2.0, 2.5)
    assert result.config.min_rr == 1.2
    assert result.config.timeframe == "15min"
    assert not result.warnings


def test_decimal_numbers_do_not_get_split_as_sentence_boundaries():
    # A period inside "1.5" must never be treated as ending the sentence.
    result = parse_strategy_description("Use 1.5x ATR stop loss.")
    assert result.config.stop_loss_atr_mult == 1.5
    assert not result.warnings


def test_unrecognized_clause_becomes_a_warning_not_a_fabricated_condition():
    result = parse_strategy_description("Buy when volume spikes.")
    assert result.config.long_conditions == []
    assert len(result.warnings) == 1
    assert "volume spikes" in result.warnings[0]


def test_unrecognized_global_sentence_becomes_a_warning():
    result = parse_strategy_description("Only trade during market hours.")
    assert result.config.long_conditions == []
    assert result.config.short_conditions == []
    assert len(result.warnings) == 1


def test_empty_parse_never_raises():
    result = parse_strategy_description("This sentence has nothing recognizable in it at all.")
    assert result.config.long_conditions == []
    assert result.config.short_conditions == []
    assert len(result.warnings) == 1


def test_parse_endpoint_returns_preview_without_persisting():
    response = client.post(
        "/api/custom-strategies/parse",
        json={"text": "Buy when RSI crosses above 60.", "name": "My Parsed Strategy"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["config"]["name"] == "My Parsed Strategy"
    assert len(body["config"]["long_conditions"]) == 1
    assert body["warnings"] == []

    # Confirm it's a preview only - nothing was saved (parse endpoint needs no auth, unlike save).
    unauth_list = client.get("/api/custom-strategies")
    assert unauth_list.status_code in (401, 403)


def test_parse_endpoint_handles_fully_unparseable_text_without_error():
    response = client.post("/api/custom-strategies/parse", json={"text": "gibberish nonsense text"})
    assert response.status_code == 200
    body = response.json()
    assert body["config"]["long_conditions"] == []
    assert len(body["warnings"]) == 1
