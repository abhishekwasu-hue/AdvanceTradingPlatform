"""Handover 11l: the one signal-language module (app/signals/language.py).

- the shared cases (tests/fixtures/signal_language.json) - the formatter, the refusals, the checker, the call words, the
  Indian digit grouping, the setup notice - and the frontend's byte-identical copy;
- the refactor is pure: the older guards (Analyze a chart's call words, #351; the strategy templates' token guard, #343)
  give exactly the outputs of their frozen former implementations on a corpus;
- every phrase of the formatter is in the strict template language too;
- the flag `signal_language_v2` is registered, OFF by default and in the admin catalog.
All texts here are made up for the test."""
import json
import re
from pathlib import Path
from typing import List

import pytest

from app.signals import language as L

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "signal_language.json"
FRONTEND_COPY = ROOT / "frontend" / "src" / "signals" / "__fixtures__" / "signal_language.json"
CASES = json.loads(FIXTURE.read_text(encoding="utf-8"))


def _structure(d: dict) -> L.SignalStructure:
    return L.SignalStructure(trend=d.get("trend"), side=d.get("side"), steps=tuple(d.get("steps", [])), setup=d.get("setup"),
                             evidence=tuple(d.get("evidence", [])), timeframe=d.get("timeframe"), symbol=d.get("symbol"),
                             area_range=tuple(d["area_range"]) if d.get("area_range") else None)


# --- the shared cases ---------------------------------------------------------------------------------------------------------

def test_the_frontend_copy_of_the_cases_is_byte_identical():
    assert FRONTEND_COPY.read_bytes() == FIXTURE.read_bytes()


@pytest.mark.parametrize("case", CASES["format"], ids=lambda c: f"{c['surface']}:{c['text'][:30]}")
def test_the_formatter_writes_the_shared_text(case):
    assert L.format_signal(_structure(case["structure"]), case["surface"]) == case["text"]


@pytest.mark.parametrize("case", CASES["refuse"], ids=lambda c: json.dumps(c["structure"]))
def test_the_formatter_refuses_what_it_has_no_words_for(case):
    with pytest.raises(ValueError):
        L.format_signal(_structure(case["structure"]), case["surface"])


@pytest.mark.parametrize("case", CASES["check"], ids=lambda c: f"{c['surface']}:{c['text'][:30]}")
def test_the_checker_finds_the_shared_problems(case):
    got = [p.as_dict() for p in L.check(case["text"], case["surface"], names=case["names"], own_symbols=case["own_symbols"])]
    assert got == case["problems"]


def test_call_words_and_digit_grouping_follow_the_shared_cases():
    for case in CASES["call_words"]:
        assert L.call_words(case["text"]) == case["words"], case
    for value, text in CASES["group_in"]:
        assert L.group_in(value) == text


def test_the_setup_notice_follows_the_shared_cases():
    for case in CASES["setup_notice"]:
        assert list(L.setup_notice(case["label"], case["symbol"], case["direction"], case["strategy_name"], case["reasons"])) == case["out"]


def test_every_formatted_text_passes_its_own_check():
    for case in CASES["format"]:
        own = [case["structure"].get("symbol") or ""]
        assert L.check(case["text"], case["surface"], names=CASES["security_names"] if case["surface"] != "signal_own" else (),
                       own_symbols=own) == [], case["text"]
        if case["surface"] == "signal_public":
            assert not re.search(r"\d", case["text"]) or re.search(r"\d+-(?:minute|hour)", case["text"])


def test_a_public_signal_never_shows_the_symbol_or_the_area_whatever_is_passed():
    s = L.SignalStructure(trend="down", side="seller", steps=("pullback_reached_area",), symbol="RELIANCE", area_range=(2800, 2810))
    text = L.format_signal(s, "signal_public")
    assert "RELIANCE" not in text and "2,8" not in text and "area:" not in text


def test_withheld_and_shown_lines():
    assert L.withheld("Buy above the high", "signal_own") == L.WITHHELD
    assert L.withheld("A handover is forming inside the area.", "signal_own") == "A handover is forming inside the area."
    assert L.shown_lines(["RSI 62", "a handover is forming", "target hit"], "signal_own") == ["a handover is forming"]
    with pytest.raises(ValueError):
        L.check("x", "marketing")  # type: ignore[arg-type]


# --- the refactor is pure: frozen copies of the former implementations ----------------------------------------------------------

_OLD_WORDS = re.compile(
    r"\b(?:buy|buys|buying|bought|sell|sells|selling|sold|targets?|targeted|targeting|stops?|stop[\s-]?loss(?:es)?|stoploss(?:es)?)\b",
    re.IGNORECASE,
)
_OLD_SL = re.compile(r"\bSL\b")


def _old_call_words(text: str) -> list:
    if not text:
        return []
    found = [m.group(0).lower() for m in _OLD_WORDS.finditer(text)]
    found += ["sl" for _ in _OLD_SL.finditer(text)]
    return found


_OLD_TF = {"1m": "1-minute", "3m": "3-minute", "5m": "5-minute", "15m": "15-minute", "30m": "30-minute", "1h": "1-hour", "1d": "Daily",
           "1w": "Weekly", "1M": "Monthly"}
_OLD_FORBIDDEN = ("buy", "buying", "bought", "sell", "selling", "sold", "target", "targets", "stop", "stops", "stoploss", "sl",
                  "loss", "profit", "profits", "entry", "enter", "exit", "long", "short", "order", "trade", "trades", "price",
                  "guaranteed", "sure", "recommend", "recommended", "should", "must")
_OLD_WORD = re.compile(r"[A-Za-z]+")
_OLD_PLACEHOLDER = re.compile(r"\{([a-z_]+)\}")
_OLD_TF_DIGITS = re.compile("|".join(re.escape(w) for w in sorted(_OLD_TF.values(), key=len, reverse=True)))


def _old_guard(text: str) -> List[str]:
    problems: List[str] = []
    for word in _OLD_WORD.findall(text):
        if word.lower() in _OLD_FORBIDDEN:
            problems.append(f"forbidden word {word!r}")
    for name in _OLD_PLACEHOLDER.findall(text):
        if name not in ("timeframe", "context_timeframe"):
            problems.append(f"unknown placeholder {{{name}}}")
    rest = _OLD_TF_DIGITS.sub("", _OLD_PLACEHOLDER.sub("", text))
    if re.search(r"[0-9]", rest):
        problems.append("a number (signal text carries no prices or values)")
    if re.search(r"[^A-Za-z0-9 ,;.\-'{}_()]", text):
        problems.append("a character outside plain English text")
    return problems


def _corpus() -> List[str]:
    from app.strategy_templates import language as tl
    texts = [c["text"] for c in CASES["check"]] + [c["text"] for c in CASES["call_words"]] + [c["text"] for c in CASES["format"]]
    texts += list(tl.TOKENS.values()) + [tl.render([t], timeframe=tf, context_timeframe="1d") for t in tl.TOKENS for tf in tl.TIMEFRAME_WORDS]
    texts += ["Buy the dip", "close above 1450", "stop-loss below the low", "the target is near", "RSI at 70", "go long now",
              "price {strike}", "trend turned bullish →", "SL", "sl", "Stop Losses everywhere", "the buyer area", "STOPS", "{timeframe} 15-minute",
              "a 4-hour chart", "Exit; entry: profit", "", " ", "Seller area: 23,000–23,100", "TARGETED", "stop loss", "re-buy", "sell-off"]
    return texts


def test_the_chart_reading_call_words_are_unchanged():
    from app.chart_analyze import signal_language as ca
    for text in _corpus():
        assert ca.call_words(text) == _old_call_words(text), text
        assert ca.is_clean(text) == (not _old_call_words(text)), text


def test_the_template_guard_is_unchanged():
    from app.strategy_templates import language as tl
    assert tl.FORBIDDEN_WORDS == _OLD_FORBIDDEN
    for text in _corpus():
        assert tl.guard(text) == _old_guard(text), text


def test_the_former_modules_now_route_through_the_shared_one():
    import inspect

    from app.chart_analyze import signal_language as ca
    from app.strategy_templates import language as tl
    assert "_language.call_words" in inspect.getsource(ca) and "_language.strict_guard" in inspect.getsource(tl)


# --- the formatter's own phrases --------------------------------------------------------------------------------------------------

def test_every_phrase_is_in_the_strict_template_language():
    phrases = list(L.TRENDS.values()) + list(L.SETUPS.values()) + list(L.EVIDENCE.values())
    phrases += [p.format(the=the, side=side) for p in L.STEPS.values() for the in ("the", "a", "this") for side in L.SIDES]
    for phrase in phrases:
        assert L.strict_guard(phrase, timeframe_words=L.TIMEFRAME_WORDS, placeholders=()) == [], phrase
        assert L.check(phrase, "signal_public") == [], phrase


def test_every_token_combination_formats_cleanly():
    import itertools
    names = ["RELIANCE", "TCS"]
    for trend, side, step, setup, ev in itertools.product([None, *L.TRENDS], L.SIDES, L.STEPS, [None, *L.SETUPS], [(), tuple(L.EVIDENCE)[:2]]):
        s = L.SignalStructure(trend=trend, side=side, steps=(step,), setup=setup, evidence=ev, timeframe="15m", symbol="RELIANCE",
                              area_range=(2841.5, 2856.25))
        own, public = L.format_signal(s, "signal_own"), L.format_signal(s, "signal_public")
        assert L.check(own, "signal_own", own_symbols=["RELIANCE"]) == [], own
        assert L.check(public, "signal_public", names=names) == [], public
        assert own.startswith("RELIANCE · Buyer area: 2,841.5–2,856.25.") or own.startswith("RELIANCE · Seller area: 2,841.5–2,856.25.")


# --- the flag ---------------------------------------------------------------------------------------------------------------------

def test_the_flag_is_registered_off_by_default_and_in_the_catalog():
    from app.platform import controls, flag_catalog
    assert L.FLAG == "signal_language_v2"
    assert L.FLAG in controls.FEATURE_FLAGS and L.FLAG in controls.DEFAULT_OFF_FLAGS and L.FLAG not in controls.DEFAULT_ON_FLAGS
    assert L.FLAG in flag_catalog.FLAG_INFO
