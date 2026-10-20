"""EO-3 follow-up (owner decision): keep the romanised Hinglish advice list and add the Hinglish word order - the verb
after the instrument ("RELIANCE kharido", "HDFCBANK le lo", "INFY ghya", "SBIN vika") is a call, blocked like the rest.

The pattern needs an instrument (an upper-case ticker or an indexed name) right before the romanised verb / light verb,
so ordinary English and lone Hinglish words pass. Also: the terms file holds one "calls" block (no duplicate keys
anywhere) and `light_verbs` is read.
"""
import json
from pathlib import Path

import pytest

from app.ai import call_detect, knowledge, output_filter

BACKEND = Path(__file__).resolve().parents[1]

BLOCKED = [
    "RELIANCE kharido.", "HDFCBANK le lo.", "INFY ghya.", "SBIN vika.",
    # variants: case of the verb, an indexed name in any case, two connecting words, inside a longer answer
    "RELIANCE Kharido!", "Reliance kharido.", "hdfc bank le lo", "RELIANCE ko becho.", "INFY che shares ghya.", "TCS abhi le lo",
    "M&M kharido", "BAJAJ-AUTO le lo", "NIFTY 50 is near its 20-day high. ITC kharido, risk is small.",
]

PASSES = [
    # the decoys of the earlier review
    "Vikas", "Vikas looked at the SBIN chart.", "Avika and vikas are names, not trades.", "Leo looked at the logo",
    "becho fund", "Hold on", "TCS vikaas", "RSI ghya",
    # twenty ordinary English Copilot answers
    "RELIANCE is trading above its 20-day EMA; the trend on the daily chart is up.",
    "RSI on INFY is 62, which is neither overbought nor oversold.",
    "Your PAPER portfolio holds 3 positions: RELIANCE, TCS and HDFCBANK.",
    "NIFTY 50 closed 0.4% higher; banks led and IT lagged.",
    "A stop-loss limits the loss on a trade; position size decides how much that loss is.",
    "The MACD on SBIN crossed above its signal line yesterday.",
    "Support for BANKNIFTY sits near 51200 and resistance near 52000.",
    "Theta is the value an option loses each day as expiry approaches.",
    "Your backtest of the EMA crossover on TCS returned 8% with a 4% drawdown.",
    "Volume in ITC was twice its 20-day average today.",
    "Implied volatility is high before results, so option premiums are expensive.",
    "The win rate of this strategy is 48%, and the average win is 1.6 times the average loss.",
    "HDFCBANK has underperformed NIFTY BANK over the last month.",
    "FII flows were negative for three sessions in a row.",
    "The 200-day EMA is a long-term trend filter several templates here use.",
    "Leo looked at the logo of the exchange on the TCS page.",
    "This is not a recommendation to buy or sell anything.",
    "Bollinger Bands widen when volatility rises.",
    "Your daily loss limit is 2% of capital; today you are down 0.5%.",
    "A gap up in RELIANCE at the open filled within the first hour.",
]


@pytest.mark.parametrize("text", BLOCKED)
def test_instrument_then_romanised_verb_is_blocked(text):
    ok, shown, why = output_filter.check(text, "en")
    assert not ok and shown is None and "advice/guarantee" in why, (text, why)
    assert any(item.startswith("call: ") for item in output_filter.call_terms(text)), text


@pytest.mark.parametrize("text", PASSES)
def test_decoys_and_ordinary_english_pass(text):
    assert output_filter.findings(text) == [], text


def test_the_verb_alone_or_after_a_share_word_is_not_this_rule():
    L = call_detect.lists(str(output_filter.DEFAULT_FILE))
    for text in ("kharido.", "le lo", "share le lo", "the stock vika", "RELIANCE, kharido", "RELIANCE mat kharido"):
        s = call_detect.normalise(text)
        assert [m for m in L.after_instrument.finditer(s) if call_detect._instrument_before(s, m.start("verb"), L)] == [], text


def test_records_and_rules_stay_exempt():
    for text in ("Status: RELIANCE kharido filled", "Rule: RELIANCE kharido when RSI < 30"):
        assert output_filter.call_terms(text) == [], text


def _no_duplicate_keys(pairs):
    keys = [k for k, _ in pairs]
    duplicates = sorted({k for k in keys if keys.count(k) > 1})
    assert not duplicates, f"duplicate keys in advice_terms.json: {duplicates}"
    return dict(pairs)


def test_terms_file_has_no_duplicate_keys_and_one_calls_block():
    data = json.loads(output_filter.DEFAULT_FILE.read_text(encoding="utf-8"), object_pairs_hook=_no_duplicate_keys)
    calls = data["calls"]
    assert set(calls["verbs"]) == {"en", "romanised"} and len(calls["verbs"]["romanised"]) == 17
    assert calls["light_verbs"] == ["le lo", "ghya"] and "pakka" in calls["certainty"] and "pakka" in calls["certainty_lead"]
    assert {"ko", "che", "shares"} <= set(calls["romanised_between"])


def test_light_verbs_are_read(tmp_path, monkeypatch):
    """`light_verbs` feeds the verb-after-instrument pattern: a light verb that is not in verbs.romanised still blocks."""
    data = json.loads(output_filter.DEFAULT_FILE.read_text(encoding="utf-8"))
    assert "lelo" not in data["calls"]["verbs"]["romanised"]
    data["calls"]["light_verbs"] = ["lelo"]
    path = tmp_path / "terms-light-verbs.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    assert output_filter.call_terms("HDFCBANK lelo.") == []
    monkeypatch.setenv("AI_ADVICE_TERMS_FILE", str(path))
    assert output_filter.call_terms("HDFCBANK lelo.") == ["call: hdfcbank lelo"]


def _new_rule_hits(text: str) -> list:
    L = call_detect.lists(str(output_filter.DEFAULT_FILE))
    hits = []
    for s in call_detect.sentences(call_detect.normalise(text)):
        hits += [m.group(0) for m in L.after_instrument.finditer(s) if call_detect._instrument_before(s, m.start("verb"), L)]
    return hits


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from _strings(v)


def test_knowledge_library_and_eval_texts_are_untouched_by_the_new_rule():
    texts = [t for c in knowledge.CONCEPTS for t in (c.en, c.body_en)]
    rows = [json.loads(line) for path in sorted((BACKEND / "evals" / "sets").glob("*.jsonl"))
            for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    texts += [s for row in rows for s in _strings(row)]
    assert len(texts) > 300
    assert [t for t in texts if _new_rule_hits(t)] == []
    shown = [row for row in rows if row.get("expect") == "shown"]
    assert shown and all(output_filter.findings(s) == [] for row in shown for s in _strings(row.get("answer")))
