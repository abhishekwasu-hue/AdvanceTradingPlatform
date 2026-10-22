"""Handover 11c-1 / 11i: the Pine translator is a parser of untrusted text (threat model, host modes). Random, mutated,
huge and deeply nested inputs never crash it, never hang it, and hit the documented limits (size, lines, tokens,
nesting, translated size) with a diagnostic instead. Seeded, so a failure reproduces."""
import random
import sys
import time

import pytest

from app.lab.pine import parser as pine_parser
from app.lab.pine import tokens as pine_tokens
from app.lab.pine.templates import TEMPLATES
from app.lab.pine.translate import MAX_NODES, Translation, translate

HEAD = '//@version=5\nindicator("f")\n'
BUDGET_S = 3.0
VOCAB = ["close", "open", "high", "low", "volume", "hl2", "ta.ema", "ta.sma", "ta.rsi", "ta.crossover", "ta.supertrend", "ta.macd",
         "ta.bb", "ta.change", "ta.highest", "input.int", "input.bool", "alertcondition", "plotshape", "strategy.entry",
         "strategy.long", "if", "else", "var", "for", "and", "or", "not", "true", "false", "na", "x", "y", "dir", "(", ")",
         "[", "]", ",", "=", ":=", "==", "!=", "<", ">", "<=", ">=", "+", "-", "*", "/", "%", "?", ":", "=>", "1", "2", "-1",
         "14", "0.5", "1e309", '"t"', "'s'", "#ff0000", "\n", "\n    ", "\n  ", "//c\n", "barmerge.lookahead_on",
         "request.security", "\t"]


def _check(text: str) -> Translation:
    start = time.monotonic()
    result = translate(text)
    elapsed = time.monotonic() - start
    assert isinstance(result, Translation)
    assert elapsed < BUDGET_S, f"{elapsed:.2f}s for {len(text)} characters"
    for d in result.diagnostics:
        assert d.line >= 1 and d.severity in ("error", "warning", "info") and d.code
    for s in result.signals:
        assert (s.text is not None) or not s.ok
    result.as_dict()                     # serialisable
    return result


def test_random_token_soup_never_crashes():
    rng = random.Random(20261022)
    for _ in range(600):
        body = " ".join(rng.choice(VOCAB) for _ in range(rng.randint(1, 60)))
        _check(HEAD + body)


def test_random_characters_never_crash():
    rng = random.Random(11)
    alphabet = "abcxyz_.0123456789 ()[]{},;:=<>!?+-*/%#@$\"'\\\n\t\u00e9\u0915\U0001F600\x00\x1b"
    for _ in range(400):
        _check("".join(rng.choice(alphabet) for _ in range(rng.randint(0, 300))))


def test_mutated_templates_never_crash():
    rng = random.Random(7)
    for _ in range(400):
        src = list(rng.choice(TEMPLATES)["source"])
        for _ in range(rng.randint(1, 8)):
            op = rng.random()
            pos = rng.randrange(len(src)) if src else 0
            if op < 0.4 and src:
                del src[pos]
            elif op < 0.8:
                src.insert(pos, rng.choice("()[]=,-?:\n \"'#x1"))
            elif src:
                src[pos:pos] = src[pos:pos + rng.randint(1, 30)]
        _check("".join(src))


@pytest.mark.parametrize("text", [
    HEAD + "x = " + "(" * 5000 + "close" + ")" * 5000,
    HEAD + "x = " + "- " * 5000 + "close",
    HEAD + "x = " + "not " * 5000 + "true",
    HEAD + "x = close" + "[1]" * 2000,
    HEAD + "x = " + "true ? " * 3000 + "true" + " : false" * 3000,
    HEAD + "x = ta.ema(" * 3000 + "close" + ", 2)" * 3000,
    HEAD + "x = [" * 3000 + "]" * 3000,
])
def test_deep_nesting_is_refused_by_the_depth_limit(text):
    before = sys.getrecursionlimit()
    r = _check(text)
    assert sys.getrecursionlimit() == before
    assert any(d.code in ("limit", "syntax") for d in r.diagnostics), [d.as_dict() for d in r.diagnostics][:3]


def test_the_nesting_limit_is_exact():
    ok = HEAD + "x = " + "(" * (pine_parser.MAX_DEPTH - 2) + "close > open" + ")" * (pine_parser.MAX_DEPTH - 2) + "\nalertcondition(x, 'x')\n"
    assert _check(ok).ok
    deep = HEAD + "x = " + "(" * (pine_parser.MAX_DEPTH + 1) + "close > open" + ")" * (pine_parser.MAX_DEPTH + 1) + "\n"
    assert any(d.code == "limit" for d in _check(deep).diagnostics)


def test_size_line_and_token_limits():
    big = HEAD + "x = close\n" * (pine_tokens.MAX_SOURCE // 10)
    r = _check(big)
    assert not r.ok and r.diagnostics[0].code == "limit" and "characters" in r.diagnostics[0].message
    lines = "\n" * (pine_tokens.MAX_LINES + 1)
    assert _check(lines).diagnostics[0].code == "limit"
    tokens = HEAD + "x = " + "+".join(["1"] * (pine_tokens.MAX_TOKENS // 2 + 10))
    r = _check(tokens)
    assert r.diagnostics[0].code == "limit" and "tokens" in r.diagnostics[0].message


def test_exponential_inlining_is_stopped_by_the_translated_size_limit():
    body = "a0 = close\n" + "".join(f"a{i} = a{i - 1} + a{i - 1}\n" for i in range(1, 40)) + "alertcondition(a39 > 0, 'x')\n"
    r = _check(HEAD + body)
    assert not r.ok and any(d.code == "limit" and str(f"{MAX_NODES:,}") in d.message for d in r.diagnostics)


def test_a_long_chain_of_declarations_is_stopped_by_the_depth_limit():
    body = "a0 = close\n" + "".join(f"a{i} = a{i - 1} + 1\n" for i in range(1, 400)) + "alertcondition(a399 > 0, 'x')\n"
    r = _check(HEAD + body)
    assert not r.ok and any(d.code == "limit" for d in r.diagnostics)


def test_a_flat_but_long_condition_stays_within_the_screen_limits():
    cond = " and ".join(f"close[{i}] > open[{i}]" for i in range(400))
    r = _check(HEAD + f"alertcondition({cond}, 'x')\n")
    assert not r.ok                       # longer than a ScreenQL screen may be: refused with the validator's reason
    assert any(d.severity == "error" for d in r.diagnostics)


def test_numbers_out_of_range_are_refused():
    r = _check(HEAD + "alertcondition(close > 1e309, 'x')\n")
    assert not r.ok and any(d.code == "number" for d in r.diagnostics)
    r = _check(HEAD + "alertcondition(close > 1e300 * 1e300, 'x')\n")
    assert not r.ok
    r = _check(HEAD + "alertcondition(close > 5 / 0, 'x')\n")
    assert not r.ok


def test_non_text_input_is_a_diagnostic():
    assert translate(None).diagnostics[0].code == "syntax"           # type: ignore[arg-type]


def test_the_operator_limit_stops_long_flat_chains_before_they_become_deep_trees():
    ok = HEAD + "x = close" + " + 1" * (pine_parser.MAX_OPERATORS - 1) + " > 0\nalertcondition(x, 'x')\n"
    assert not any(d.code == "limit" and "operators" in d.message for d in _check(ok).diagnostics)
    long = HEAD + "x = close" + " + close" * 5000 + " > 0\n"
    r = _check(long)
    assert any(d.code == "limit" and "operators" in d.message for d in r.diagnostics)
