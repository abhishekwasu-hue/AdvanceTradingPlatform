"""Handover 11c-1 (AMW-20 first slice): the Pine Script subset translator - supported constructs, line-numbered
diagnostics with the closest supported alternative for every unsupported construct, ignored order sizing, look-ahead
refusals and ScreenQL injection. docs/design/PINE_SUBSET.md is the subset these tests pin."""
import pytest

from app.lab.pine.parser import parse
from app.lab.pine.templates import TEMPLATES
from app.lab.pine.translate import FLAG, translate
from app.platform import controls
from app.screener import compile_screen

HEAD = '//@version=5\nindicator("t")\n'


def _t(body: str, head: str = HEAD):
    return translate(head + body)


def _errors(result):
    return [d for d in result.diagnostics if d.severity == "error"]


def _one(body: str, head: str = HEAD):
    r = _t(body, head)
    assert r.ok, [d.as_dict() for d in _errors(r)]
    return r.signals[0]


def test_the_flag_is_registered_and_off_by_default():
    assert FLAG == "strategy_lab_pine"
    assert FLAG in controls.FEATURE_FLAGS and FLAG in controls.DEFAULT_OFF_FLAGS


# --- supported constructs ------------------------------------------------------------------------------------------------

def test_header_inputs_and_a_strategy_entry_become_a_screen_with_parameters():
    src = """//@version=5
strategy("My cross", overlay=true)
fastLen = input.int(9, "Fast", minval=1, maxval=200)
slow = input(21, "Slow")
mult = input.float(1.5, "Mult", minval=0.5)
useVol = input.bool(true, "Volume filter")
src = input.source(close, "Source")
f = ta.ema(src, fastLen)
float s = ta.sma(src, slow)
volOk = not useVol or volume > ta.sma(volume, 20) * mult
if ta.crossover(f, s) and volOk
    strategy.entry("Long", strategy.long)
"""
    r = translate(src)
    assert r.ok and r.complete and r.script_kind == "strategy" and r.title == "My cross" and r.version == "5"
    assert {k: (v.type, v.default, v.min, v.max, v.title) for k, v in r.params.items()} == {
        "fastLen": ("int", 9, 1.0, 200.0, "Fast"), "slow": ("int", 21, None, None, "Slow"), "mult": ("float", 1.5, 0.5, None, "Mult"),
        "useVol": ("bool", True, None, None, "Volume filter")}
    (sig,) = r.signals
    assert sig.kind == "long_entry" and sig.line == 12 and sig.ok
    assert sig.text == "CrossAbove(EMA(close, $fastLen), SMA(close, $slow)) AND (NOT $useVol OR volume > SMA(volume, 20) * $mult)"
    assert sig.params == {"fastLen": 9, "mult": 1.5, "slow": 21, "useVol": True}
    assert "crosses above" in sig.words and r.primary() == 0
    assert any(d.code == "input_source" and d.line == 7 for d in r.diagnostics)


def test_fields_arithmetic_comparisons_and_history_references():
    s = _one("x = (high - low) / 2 + hl2 * 1\nalertcondition(x[2] >= ohlc4 and close != open[1] and -close < -open, 'x')\n")
    assert s.text == ("(high[2] - low[2]) / 2 + (high[2] + low[2]) / 2 * 1 >= (open + high + low + close) / 4 AND close != open[1]"
                      " AND -close < -open")


def test_constant_folding_and_negative_literals():
    assert _one("alertcondition(close > 2 * 3 - 1, 'x')\n").text == "close > 5"
    assert _one("alertcondition(ta.change(close) > -1.5, 'x')\n").text == "close - close[1] > -1.5"


def test_every_supported_ta_function_maps_to_registry_functions():
    cases = {
        "ta.ema(close, 9) > 0": "EMA(close, 9) > 0",
        "ta.sma(volume, 5) > 0": "SMA(volume, 5) > 0",
        "ta.rsi(close, 14) > 50": "RSI(14) > 50",
        "ta.atr(14) > 1": "ATR(14) > 1",
        "ta.highest(high, 5) > close": "Max(high, 5) > close",
        "ta.lowest(5) < close": "Min(low, 5) < close",
        "ta.crossunder(close, ta.sma(close, 20))": "CrossBelow(close, SMA(close, 20))",
        "ta.stdev(close, 20) > 1": "StDev(close, 20) > 1",
        "ta.roc(close, 10) > 0": "PctChange(close, 10) > 0",
        "math.min(open, close) > low": "Least(open, close) > low",
    }
    for cond, want in cases.items():
        assert _one(f"alertcondition({cond}, 'x')\n").text == want, cond


def test_strategy_close_when_argument_nested_ifs_and_markers():
    src = HEAD.replace("indicator", "strategy") + """up = close > open
if up
    if volume > volume[1]
        strategy.entry("L", strategy.long)
        strategy.entry("S", strategy.short)
strategy.close("L", when=close < open)
strategy.close_all(when=ta.crossunder(close, ta.ema(close, 50)))
plotshape(up and barstate.isconfirmed, title="Up bar")
plotshape(close, title="a number is drawing only")
"""
    r = translate(src)
    assert r.ok and r.complete, [d.as_dict() for d in _errors(r)]
    assert [(s.kind, s.line, s.text) for s in r.signals] == [
        ("long_entry", 6, "close > open AND volume > volume[1]"),
        ("short_entry", 7, "close > open AND volume > volume[1]"),
        ("exit", 8, "close < open"),
        ("exit", 9, "CrossBelow(close, EMA(close, 50))"),
        ("marker", 10, "close > open AND TRUE"),
    ]
    assert r.signals[0].label.startswith("Long-side condition") and r.signals[2].label == "Exit condition (L)"


def test_line_continuation_and_typed_declarations():
    s = _one("series float x = ta.ema(close,\n     20)\ny = close >\n  x\nalertcondition(y, 'x')\n")
    assert s.text == "close > EMA(close, 20)"


def test_order_sizing_and_stops_are_reported_and_ignored():
    src = """//@version=5
strategy("s", default_qty_type=strategy.percent_of_equity, default_qty_value=10, initial_capital=100000, calc_on_every_tick=true)
if close > open
    strategy.entry("L", strategy.long, qty=5, limit=close - 1)
    strategy.exit("X", "L", stop=low, profit=100)
"""
    r = translate(src)
    assert r.ok and r.complete
    codes = {(d.line, d.code) for d in r.diagnostics if d.severity == "warning"}
    assert {(2, "sizing_ignored"), (2, "closed_bars"), (4, "sizing_ignored"), (4, "order_ignored"), (5, "order_ignored")} <= codes
    assert r.signals[0].text == "close > open"


def test_drawing_calls_are_ignored_not_refused():
    r = _t("p = plot(close, color=color.new(color.red, 50))\nhline(50)\nbgcolor(close > open ? color.green : na)\n"
           "alertcondition(close > open, 'x')\n")
    assert r.ok and r.complete, [d.as_dict() for d in _errors(r)]
    assert any(c.construct == "hline()" and c.status == "ignored" for c in r.constructs)


def test_supertrend_direction_forms():
    head = HEAD + "[st, dir] = ta.supertrend(3, 10)\n"
    assert _one("alertcondition(dir < 0, 'x')\n", head).text == "close > Supertrend(10, 3)"
    assert _one("alertcondition(dir == 1, 'x')\n", head).text == "close < Supertrend(10, 3)"
    assert _one("alertcondition(dir != dir[1], 'x')\n", head).text == (
        "close > Supertrend(10, 3) AND close[1] < Supertrend(10, 3)[1] OR close < Supertrend(10, 3) AND close[1] > Supertrend(10, 3)[1]")
    assert _one("alertcondition(close > st, 'x')\n", head).text == "close > Supertrend(10, 3)"
    r = _t("alertcondition(dir * 2 > 0, 'x')\n", head)
    assert not r.ok and any(d.code == "supertrend_direction" and d.line == 4 for d in r.diagnostics)


def test_version_6_is_read_as_the_subset_and_older_versions_are_refused():
    assert translate('//@version=6\nindicator("x")\nalertcondition(close > open, "x")\n').ok
    v4 = translate('//@version=4\nstudy("x")\nalertcondition(close > open, "x")\n')
    assert not v4.ok and any(d.code == "version" and d.line == 1 and d.alternative for d in v4.diagnostics)
    none = translate('indicator("x")\nalertcondition(close > open, "x")\n')
    assert not none.ok and any(d.code == "version" for d in none.diagnostics)


# --- unsupported constructs: a diagnostic with the line and the closest alternative --------------------------------------

UNSUPPORTED = [
    ("a = ta.wma(close, 10)", "unsupported_function", "ta.ema"),
    ("a = ta.hma(close, 10)", "unsupported_function", "ta.ema"),
    ("a = ta.vwma(close, 10)", "unsupported_function", "ta.ema"),
    ("a = ta.rma(close, 10)", "unsupported_function", "2 * len - 1"),
    ("a = ta.pivothigh(high, 5, 5)", "unsupported_function", "SwingHigh"),
    ("a = ta.valuewhen(close > open, close, 0)", "unsupported_function", "ta.highest"),
    ("a = ta.barssince(close > open)", "unsupported_function", "Count"),
    ("a = ta.cci(close, 20)", "unsupported_function", "ta.rsi"),
    ("a = ta.sar(0.02, 0.02, 0.2)", "unsupported_function", "ta.supertrend"),
    ("a = ta.kc(close, 20, 2)", "unsupported_function", "ta.bb"),
    ("a = ta.frobnicate(close)", "unsupported_function", "ta.ema"),
    ("a = request.security(syminfo.tickerid, 'W', close)", "unsupported_function", "@1w"),
    ("a = math.abs(close - open)", "unsupported_function", "x > k or x < -k"),
    ("a = nz(close)", "unsupported_function", "missing"),
    ("a = na(close)", "unsupported_function", "missing"),
    ("a = close == na", "unsupported_name", "missing"),
    ("a = bar_index > 10", "unsupported_name", "history reference"),
    ("a = hour > 10", "unsupported_name", "TimeOfDay"),
    ("a = close % 2", "modulo", "compare"),
    ("a = close > open ? close : open", "ternary", "two conditions"),
    ("a = ta.rsi(hl2, 14)", "source", "close"),
    ("a = ta.bb(open, 20, 2)", "source", "close"),
    ("a = ta.stdev(close, 20, false)", "stdev_biased", "biased"),
    ("[a, b, c] = ta.dmi(14, 7)", "dmi", "same length"),
    ("a = ta.ema(close, close)", "window", "number"),
    ("a = ta.ema(close, 9.5)", "window", "whole number"),
    ("a = ta.ema(close, 9 + volume)", "window", "input.int"),
    ("a = undefinedThing > 1", "unknown_name", "declare"),
    ("var a = 0", "var", "history reference"),
    ("varip a = 0", "var", "history reference"),
    ("a = 1\na := 2", "reassign", "own name"),
    ("a = 1\na += 2", "reassign", "own name"),
    ("for i = 0 to 10\n    x = i", "loop", "history reference"),
    ("while close > open\n    x = 1", "loop", "history reference"),
    ("s = switch\n    close > open => 1", "switch", "alertcondition"),
    ("f(x) => x + 1", "function", "inline"),
    ("f(x) =>\n    x + 1", "function", "inline"),
    ("import user/lib/1 as lib", "import", "self-contained"),
    ("if close > open\n    strategy.entry('L', strategy.long)\nelse\n    strategy.close('L')", "else", "if not cond"),
    ("strategy.order('L', strategy.long)", "unsupported_function", "strategy.entry"),
    ("label = input.string('EMA', 'Type')\na = label == 'EMA'", "text_value", "input.int"),
    ("a = [1, 2]", "array", "own name"),
    ("a = close > open == true and close", "type", "compare"),
    ("alert('x')", "alert", "alertcondition"),
    ("strategy.entry('L', strategy.long)", "no_condition", "when=cond"),
    ("    a = 1", "indent", "remove the indent"),
    ("a = close > > open", "syntax", "supported subset"),
]


@pytest.mark.parametrize("snippet,code,alternative", UNSUPPORTED)
def test_each_unsupported_construct_has_a_line_numbered_diagnostic_with_an_alternative(snippet, code, alternative):
    r = _t(snippet + "\nalertcondition(close > open, 'still fine')\n")
    hits = [d for d in r.diagnostics if d.code == code]
    assert hits, (snippet, [d.as_dict() for d in r.diagnostics])
    d = hits[0]
    assert d.severity == "error" and 3 <= d.line <= 3 + snippet.count("\n"), d.as_dict()
    assert alternative.lower() in d.alternative.lower(), d.as_dict()
    assert not r.complete
    # a signal that does not depend on the unsupported line still translates
    assert any(s.ok and s.text == "close > open" for s in r.signals)


def test_a_signal_built_on_an_unsupported_line_says_which_line():
    r = _t("w = ta.wma(close, 10)\nup = close > w\nalertcondition(up, 'x')\n")
    (sig,) = r.signals
    assert not sig.ok and sig.text is None and "line 3" in sig.problems[0]
    assert not r.ok and [d.line for d in _errors(r) if d.code == "unsupported_function"] == [3]


def test_a_failed_if_condition_fails_every_signal_in_its_body():
    r = _t("if ta.wma(close, 5) > close\n    if close > open\n        strategy.entry('L', strategy.long)\n")
    assert [s.ok for s in r.signals] == [False] and "line 3" in r.signals[0].problems[0]


def test_the_screenql_validator_runs_on_the_translation():
    r = _t("alertcondition(close > ta.rsi(close, 14), 'price against an oscillator')\n")
    (sig,) = r.signals
    assert not sig.ok and any("compares price with index" in p for p in sig.problems)
    assert any(d.code == "screenql" and d.line == 3 for d in r.diagnostics)


# --- look-ahead ----------------------------------------------------------------------------------------------------------

LOOKAHEAD = [
    "h = request.security(syminfo.tickerid, 'D', high, lookahead=barmerge.lookahead_on)",
    "h = request.security(syminfo.tickerid, 'D', high, barmerge.gaps_off, barmerge.lookahead_on)",
    "plot(request.security(syminfo.tickerid, 'W', close, lookahead = barmerge.lookahead_on))",
    "x = close[-1]",
    "x = (close - open)[-2] > 0",
    "n = input.int(1, minval=-5)\nx = close[n] > 0",
    "plot(close, offset=-1)",
    "var x = close[-1]",
]


@pytest.mark.parametrize("snippet", LOOKAHEAD)
def test_look_ahead_is_refused_for_the_whole_script(snippet):
    r = _t(snippet + "\nalertcondition(close > open, 'x')\n")
    assert r.refused and not r.ok and r.signals == []
    la = [d for d in r.diagnostics if d.code == "look_ahead"]
    assert la and all(d.severity == "error" for d in la) and la[0].line >= 3
    assert any(d.code == "refused" for d in r.diagnostics)


def test_lookahead_off_is_not_a_refusal():
    r = _t("h = request.security(syminfo.tickerid, 'D', high, lookahead=barmerge.lookahead_off)\nalertcondition(close > open, 'x')\n")
    assert not r.refused and any(d.code == "unsupported_function" for d in r.diagnostics)


# --- injection: user text never becomes ScreenQL text --------------------------------------------------------------------

def test_titles_strings_and_names_never_reach_the_screenql_text():
    src = HEAD + """AND = input.int(5, ") OR TRUE OR (")
TRUE = close > ta.sma(close, AND)
alertcondition(TRUE, title="x\\" OR TRUE OR \\"", message="$x {{close}}")
"""
    r = translate(src)
    (sig,) = r.signals
    assert sig.ok and sig.text == "close > SMA(close, $AND)"
    ast, validated = compile_screen(sig.text, params=sig.params)
    assert validated.ok and "OR" not in sig.text


def test_a_string_where_a_number_belongs_is_refused():
    r = _t("alertcondition(close > 'x) OR TRUE', 'x')\n")
    assert not r.ok and any(d.code == "text_value" for d in r.diagnostics)


def test_templates_are_signal_conditions_only():
    assert len(TEMPLATES) == 5
    assert {t["id"] for t in TEMPLATES} == {"ema_cross", "rsi_pullback", "supertrend_flip", "macd_cross", "bollinger_reentry"}
    for t in TEMPLATES:
        text = (t["name"] + t["summary"] + t["source"]).lower()
        for word in ("buy", "sell", "profit", "target", "stop", "win rate", "returns", "qty"):
            assert word not in text, (t["id"], word)
        result = translate(t["source"])
        assert result.ok and result.complete and not _errors(result)


def test_the_parser_keeps_going_after_a_bad_line():
    program = parse(HEAD + "a = close >\nb = close > open\n")
    assert [type(st).__name__ for st in program.statements] == ["SExpr", "SUnsupported", "SAssign"]
    r = _t("a = close > > open\nb = close > open\nalertcondition(b, 'x')\n")
    assert r.ok and [d.line for d in _errors(r)] == [3]
