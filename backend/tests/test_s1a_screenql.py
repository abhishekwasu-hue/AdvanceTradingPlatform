"""S1a (ADR-0021): ScreenQL - grammar, AST, text and builder round trips, a seeded grammar fuzz, offsets and mixed
timeframes, the look-ahead guard, units, parameters and the cost cap."""
import random

import pytest

from app.screener import compile_screen, nodes as n, parse, validate
from app.screener.parser import MAX_TEXT, ScreenQLSyntaxError
from app.screener.registry import FIELDS, FUNCTIONS, describe

EXAMPLES = [
    "close > SMA(close, 20) AND RSI(14)@1d BETWEEN 40 AND 60",
    "NOT (volume[1] * 2 < volume OR Sector() IN (\"Banks\", \"IT\"))",
    "CrossAbove(EMA(close, 9), EMA(close, 21))[1]@1w",
    "PercentileRank(PctChange(close, 20), by=Sector()) > 80 AND IndexMember(\"NIFTY 200\")",
    "Count(5, close > open) >= 4 AND close > Max(high, 20)[1]",
    "Greatest(open, close) - Least(open, close) < 0.3 * (high - low)",
    "-(5) + -ATR(14) < close - -2.5",
    "ZScore(volume, 20) > $z AND IsFnO()",
]


@pytest.mark.parametrize("text", EXAMPLES)
def test_text_round_trip_and_json_round_trip(text):
    ast = parse(text)
    printed = n.to_text(ast)
    assert parse(printed) == ast and n.to_text(parse(printed)) == printed               # canonical text is a fixed point
    assert n.from_json(n.to_json(ast)) == ast                                            # builder wire format
    assert n.to_json(ast)["k"] in {"Logic", "Not", "Compare", "Between", "Call"}


def test_builder_tree_and_text_produce_the_same_ast():
    built = n.Logic("ALL", [n.Compare(">", n.Field("close"), n.Call("SMA", [n.Field("close"), n.Num(20)])),
                            n.Between(n.Call("RSI", [n.Num(14)], tf="1d"), n.Num(40), n.Num(60))])
    assert parse(n.to_text(built)) == built == parse(EXAMPLES[0])
    assert parse("ALL(close > 1, ANY(open > 2, high > 3))") == parse("close > 1 AND (open > 2 OR high > 3)")
    with pytest.raises(ValueError):
        n.from_json({"k": "Exec", "code": "import os"})
    with pytest.raises(ValueError):
        n.from_json({"k": "Num", "v": "1; DROP"})


# --- fuzz -----------------------------------------------------------------------------------------------------------

def _num(rng):
    return n.Num(rng.choice([0, 1, 2.5, 14, 20, 100, -3, 0.001, 1e6]))


def _value(rng, depth):
    if depth <= 0 or rng.random() < 0.3:
        return rng.choice([lambda: n.Field(rng.choice(list(FIELDS)), rng.choice([0, 0, 1, 5]), rng.choice([None, None, "1d", "1w"])),
                           lambda: _num(rng), lambda: n.Param(rng.choice(["a", "lb", "z1"]))])()
    k = rng.randrange(4)
    if k == 0:
        return n.Binary(rng.choice(n.ARITH_OPS), _value(rng, depth - 1), _value(rng, depth - 1))
    if k == 1:
        operand = _value(rng, depth - 1)
        return n.Unary("-", operand) if not isinstance(operand, n.Unary) else operand
    if k == 2:
        return n.Call(rng.choice(["SMA", "EMA", "Max", "Lag"]), [_value(rng, depth - 1), n.Num(rng.choice([5, 20]))], {},
                      rng.choice([0, 1]), rng.choice([None, "1d"]))
    return n.Call("Greatest", [_value(rng, depth - 1), _value(rng, depth - 1)])


def _cond(rng, depth):
    k = rng.randrange(6) if depth > 0 else rng.randrange(3)
    if k == 0:
        return n.Compare(rng.choice(n.COMPARE_OPS), _value(rng, depth), _value(rng, depth))
    if k == 1:
        return n.Between(_value(rng, depth), _value(rng, depth), _value(rng, depth))
    if k == 2:
        return n.In(n.Call("Sector"), [n.Str(rng.choice(["IT", 'Ban"ks', "a\\b"])), n.Str("Auto")])
    if k == 3:
        return n.Logic(rng.choice(["ALL", "ANY"]), [_cond(rng, depth - 1) for _ in range(rng.randint(2, 3))])
    if k == 4:
        return n.Not(_cond(rng, depth - 1))
    return n.Call("CrossAbove", [_value(rng, depth - 1), _value(rng, depth - 1)], {}, rng.choice([0, 2]))


def test_fuzz_random_trees_round_trip_through_text():
    rng = random.Random(20261011)
    for _ in range(600):
        tree = _cond(rng, 3)
        text = n.to_text(tree)
        assert parse(text) == tree, text


def test_fuzz_garbage_only_ever_raises_syntax_errors():
    rng = random.Random(7)
    alphabet = list("()[],=<>!+-*/@$\"\\ .0123456789eE_") + ["AND ", "OR ", "NOT ", "BETWEEN ", "IN ", "close", "SMA", "1d", "@1M", "\n"]
    for _ in range(3000):
        text = "".join(rng.choice(alphabet) for _ in range(rng.randint(1, 40)))
        try:
            ast = parse(text)
        except ScreenQLSyntaxError as exc:
            assert 0 <= exc.pos <= len(text)
            continue
        validate(ast)                                                                    # a parsed tree always validates without raising
        _, v = compile_screen(text)
        assert isinstance(v.ok, bool)


def test_syntax_errors_carry_positions():
    for text, pos, words in (("close > ", 8, "expected a value"), ("close >> 1", 7, "expected a value"), ("close@2h > 1", 5, "unknown timeframe"),
                             ("SMA(close, 20", 13, "expected )"), ("1 BETWEEN 0 OR 2", 12, "AND"), ("5[1] > 1", 1, "fields and functions only"),
                             ("close # 1", 6, "unexpected character")):
        with pytest.raises(ScreenQLSyntaxError) as info:
            parse(text)
        assert info.value.pos == pos and words in info.value.message, (text, info.value)
    with pytest.raises(ScreenQLSyntaxError):
        parse("x" * (MAX_TEXT + 1))
    with pytest.raises(ScreenQLSyntaxError, match="nested too deeply"):
        parse("(" * 200 + "close > 1" + ")" * 200)
    with pytest.raises(ScreenQLSyntaxError, match="empty"):
        parse("   ")


def test_offsets_mixed_timeframes_and_the_look_ahead_guard():
    v = validate(parse("close > Max(high, 20)[1] AND close@1d > SMA(close, 50)@1d AND RSI(14)@1h > 50"), base_tf="15m")
    assert v.ok and v.timeframes == {"15m", "1d", "1h"} and v.lookback == {"15m": 21, "1d": 50, "1h": 14} and v.closed_bars_only
    finer = validate(parse("RSI(14)@5m > 50"), base_tf="1d")
    assert not finer.ok and "finer than the screen's timeframe" in finer.problems[0].message
    with pytest.raises(ScreenQLSyntaxError, match="look-ahead"):
        parse("close[-1] > close")
    built = n.Compare(">", n.Field("close", offset=-1), n.Field("close"))               # a builder tree cannot sneak it in
    assert any("look-ahead" in p.message for p in validate(built).problems)
    assert not validate(parse("close[501] > 1")).ok


def test_types_units_names_and_arity():
    cases = {
        "close > RSI(14)": "compares price with index",
        "close + volume > 1": "mixes units price and volume",
        "CrossAbove(close, volume)": "CrossAbove mixes units",
        "close": "must be a condition",
        "SMA(close) > 1": "SMA takes 2 argument(s), got 1",
        "SMA(close, close) > 1": "window must be a whole number of bars",
        "SMA(close, 2.5) > 1": "whole number from 1 to 500",
        "Close > 1": "unknown field 'Close'",
        "RSI > 50": "a function needs parentheses",
        "close() > 1": "a field takes no parentheses",
        "exec(\"rm\") > 1": "unknown function 'exec'",
        "close > 1 AND 5": "joins conditions",
        "Sector() > 3": "cannot compare cat with num",
        "Sector()[1] == \"IT\"": "takes no [offset]",
        "SMA(close, 20, by=Sector()) > 1": "has no argument 'by'",
    }
    for text, words in cases.items():
        v = validate(parse(text))
        assert not v.ok and any(words in p.message for p in v.problems), (text, [p.message for p in v.problems])
    ok = validate(parse("close / SMA(close, 20) > 1.02 AND volume > 2 * SMA(volume, 20) AND Sector() == \"IT\""))
    assert ok.ok, ok.problems


def test_parameters_and_the_cost_cap():
    text = "volume > $k * SMA(volume, $lb)"
    assert validate(parse(text), params={"k": 2, "lb": 20}).ok
    missing = validate(parse(text), params={"k": 2})
    assert any("$lb has no value" in p.message for p in missing.problems)
    bad_window = validate(parse(text), params={"k": 2, "lb": 9999})
    assert any("1 to 500" in p.message for p in bad_window.problems)
    unused = validate(parse("close > 1"), params={"ghost": 1})
    assert any("unknown parameter(s): $ghost" in p.message for p in unused.problems)
    heavy = " AND ".join(f"Rank(PctChange(close, {w})) < 50" for w in range(100, 400, 20))
    capped = validate(parse(heavy), cost_cap=50)
    assert capped.ok is False and "over this organisation's cap 50" in capped.problems[-1].message and capped.cross_sectional
    assert validate(parse(heavy)).ok                                                       # the default cap allows it


def test_compile_screen_takes_text_or_a_builder_tree_and_one_error_shape():
    ast, v = compile_screen("close > SMA(close, 20)")
    assert v.ok and ast == parse("close > SMA(close, 20)")
    ast2, v2 = compile_screen(n.to_json(ast))
    assert v2.ok and ast2 == ast
    none, bad = compile_screen("close > > 1")
    assert none is None and not bad.ok and bad.problems[0].pos == 8
    _, junk = compile_screen({"k": "Nope"})
    assert not junk.ok and "not a ScreenQL node" in junk.problems[0].message
    assert set(describe()) == set(FIELDS) | set(FUNCTIONS) and describe()["RSI"]["unit"] == "index"
