"""Master prompt Section 50: property-based/fuzz tests on the DSL parser. `parse_strategy_
description` (app/strategy_engine/nlu_parser.py) is a hand-written regex parser fed directly with
untrusted user text via `POST /api/custom-strategies/parse` (no auth required, same as
`/backtest`) - exactly the kind of input surface Section 48's "chat treated as untrusted like
webhook input" guardrail is about. A regex-heavy parser is also a classic source of crashes
(unicode edge cases, catastrophic backtracking) that example-based tests rarely stumble onto by
chance - which is precisely the gap property-based testing is for for.

The one property that actually matters here: **no input text can ever make this function raise**.
An unrecognized phrase must always come back as a `warnings` entry (the parser's own designed
behavior - see its module docstring), never an exception - the same "never fabricate, never crash"
guarantee the rest of this platform gives its data sources.
"""
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from app.strategy_engine.nlu_parser import ParsedStrategyPreview, parse_strategy_description


@given(text=st.text(max_size=500))
@settings(max_examples=300, suppress_health_check=[HealthCheck.too_slow])
def test_parser_never_raises_on_arbitrary_text(text: str):
    result = parse_strategy_description(text)
    assert isinstance(result.config, ParsedStrategyPreview)
    assert isinstance(result.interpreted, list)
    assert isinstance(result.warnings, list)


@given(text=st.text(alphabet=st.characters(min_codepoint=0x1F300, max_codepoint=0x1FAFF), max_size=200))
@settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
def test_parser_never_raises_on_emoji_and_high_unicode(text: str):
    """Regex character classes and Unicode case-folding are a common source of surprising
    crashes on astral-plane codepoints (emoji, rare scripts) that plain ASCII fuzzing won't
    reach - tested as its own targeted strategy rather than trusting general text fuzzing to
    stumble into this range by chance.
    """
    result = parse_strategy_description(text)
    assert isinstance(result.config, ParsedStrategyPreview)


@given(
    text=st.text(alphabet=".0123456789\n; ", max_size=300),
)
@settings(max_examples=300, suppress_health_check=[HealthCheck.too_slow])
def test_parser_never_raises_on_pathological_decimal_and_sentence_boundaries(text: str):
    """Targets the exact edge the parser's own sentence-splitter was already fixed for once
    (decimal points vs. sentence-ending periods, see app/strategy_engine/nlu_parser.py's
    docstring) - fuzzing the character classes involved in that fix specifically, rather than
    hoping general text fuzzing reconstructs a similar case by chance.
    """
    result = parse_strategy_description(text)
    assert isinstance(result.config, ParsedStrategyPreview)


@given(text=st.text(max_size=2000))
@settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
def test_parser_never_raises_on_long_input(text: str):
    """A longer input surface than the other cases here - guards specifically against
    catastrophic-backtracking-style slowdowns/crashes that only show up past a length threshold,
    not just correctness on short strings.
    """
    result = parse_strategy_description(text)
    assert isinstance(result.config, ParsedStrategyPreview)


def test_parser_handles_empty_and_whitespace_only_input():
    for text in ["", " ", "\n", "\n\n\n", "   \t  ", ".", "...", ";;;"]:
        result = parse_strategy_description(text)
        assert result.config.long_conditions == []
        assert result.config.short_conditions == []
