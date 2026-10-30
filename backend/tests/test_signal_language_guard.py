"""Handover 11l: the CI guard over every place a signal text is produced or served, on fixtures.

Each surface below runs through the one checker (app/signals/language.py) with the flag `signal_language_v2` ON, and
fails on trade-call wording (buy / sell / target / stop), a single price or other number outside the user's own area
range, a currency amount, or a security name on a public / education surface. With the flag OFF each surface's text is
byte-identical to what it was before (the former formula is frozen here).

Surfaces: the signal formatter itself; the strategy templates' signal texts; the Signals inbox's "why" lines; the scan
alert texts (the in-app notification, Telegram, e-mail); the notify-only setup texts of the execution core; the daily
brief's plan texts; the Copilot teacher's concept texts and its "on your chart" lines; share cards (none exist yet - a
new producer must be registered here). The Copilot's own advice-word check (app/ai/wording.py, #354) runs on every text
too. All data here is made up for the test."""
import asyncio
import json
import re
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.ai.wording import banned_terms
from app.signals import language as L

CASES = json.loads((Path(__file__).resolve().parent / "fixtures" / "signal_language.json").read_text(encoding="utf-8"))
NAMES = CASES["security_names"]
APP = Path(__file__).resolve().parents[1] / "app"


def _run(coro):
    return asyncio.run(coro)


def _clean(text: str, surface: str, *, own=(), names=NAMES) -> None:
    problems = L.check(text, surface, names=names if surface != "signal_own" else (), own_symbols=own)
    assert problems == [], (text, problems)
    assert banned_terms(text) == [], (text, banned_terms(text))
    if surface != "education":
        assert L.call_words(text) == [], text


# --- the strategy templates' signal texts (public: no symbol in them) ----------------------------------------------------------

def test_every_strategy_template_signal_text_is_in_the_signal_language():
    from app.strategy_templates import compile as C
    from app.strategy_templates import language as tl
    from app.strategy_templates import library
    for phrase in tl.TOKENS.values():
        _clean(phrase.format(timeframe="15-minute", context_timeframe="Daily", wave_timeframe="1-hour", higher_timeframe="Daily"),
               "signal_public")
    for t in library.templates():
        for tf in t.allowed_timeframes:
            for side in ("long", "short"):
                s = C.Settings(tf, "both", {p.key: (True if p.type == "bool" else p.default) for p in t.params})
                _clean(library.signal_text(t, s, side, "1d"), "signal_public")


# --- the Signals inbox -----------------------------------------------------------------------------------------------------------

def test_the_inbox_keeps_only_why_lines_in_the_signal_language():
    from app.signals_feed.service import signal_text
    item = {"symbol": "DEMO1", "reasons": ["The pullback reached the buyer area", "RSI 62.4 > 55", "Buy above 101.5",
                                           "Buyer area: 100–101. A handover is forming inside the area.", "stop at 98"], "entry": 100.0}
    out = signal_text(item)
    assert out["reasons"] == ["The pullback reached the buyer area", "Buyer area: 100–101. A handover is forming inside the area."]
    assert out["entry"] == 100.0 and item["reasons"][1] == "RSI 62.4 > 55"          # the record is unchanged; the input is not mutated
    for line in out["reasons"]:
        _clean(line, "signal_own", own=["DEMO1"])


def test_the_inbox_is_byte_identical_with_the_flag_off_and_filtered_with_it_on():
    from tests.test_amw2_watchlists import _set_flag
    from tests.test_auth_api import client
    from tests.test_ui2_3_signals_feed import URL, _owner, _proposal
    t = _owner("siglang")
    _proposal(t)
    off = client.get(URL, headers=t["headers"], params={"tab": "today"}).json()
    again = client.get(URL, headers=t["headers"], params={"tab": "today"}).json()
    assert [i["reasons"] for i in off["items"]] == [["Made-up reason one", "Made-up reason two", "Three"]]
    _set_flag(L.FLAG, t["tenant_id"], on=True)
    on = client.get(URL, headers=t["headers"], params={"tab": "today"}).json()
    for a, b in zip(off["items"], again["items"]):
        assert a == b
    assert [i["reasons"] for i in on["items"]] == [["Made-up reason one", "Made-up reason two", "Three"]]   # clean lines stay
    for a, b in zip(off["items"], on["items"]):
        assert {k: v for k, v in a.items() if k != "reasons"} == {k: v for k, v in b.items() if k != "reasons"}


# --- scan alerts (in-app, Telegram, e-mail all carry this title and body) -----------------------------------------------------------

def _alert_rule(name="Pullback to buyer area"):
    return SimpleNamespace(name=name)


def _events(symbols, intrabar=False):
    bar = datetime(2026, 3, 10, 3, 45, tzinfo=timezone.utc)
    return [SimpleNamespace(symbol=s, bar_time=bar, values_json=json.dumps({"close": 101.5, "rsi": 41.2, **({"intrabar": True} if intrabar else {})}))
            for s in symbols]


def _old_alert(rule, events, bucket):
    from app.alerts.engine import MAX_SYMBOLS_IN_MESSAGE
    symbols = sorted({e.symbol for e in events})
    shown = ", ".join(symbols[:MAX_SYMBOLS_IN_MESSAGE]) + (f" and {len(symbols) - MAX_SYMBOLS_IN_MESSAGE} more" if len(symbols) > MAX_SYMBOLS_IN_MESSAGE else "")
    when = sorted({e.bar_time for e in events})[-1].strftime("%Y-%m-%d %H:%M UTC")
    title = f"{rule.name}: {len(symbols)} match(es) in this digest" if bucket else f"{rule.name}: {len(symbols)} symbol(s) matched"
    on = f"the bar closing {when}"
    if any(json.loads(e.values_json).get("intrabar") for e in events):
        on = f"the bar still forming at {when} (intrabar: it may not hold at the close)"
    return title[:200], f"Matched on {on}: {shown}. Matches passed the rule's filters; they are not recommendations."


ALERT_CASES = [(["TCS"], False, None), (["TCS", "M&M", "INFY"], True, None), ([f"DEMO{i}" for i in range(30)], False, "2026-03-10T09"),
               (["RELIANCE"], False, "digest")]


@pytest.mark.parametrize("symbols,intrabar,bucket", ALERT_CASES)
def test_scan_alert_texts(symbols, intrabar, bucket):
    from app.alerts.engine import _message
    rule, events = _alert_rule(), _events(symbols, intrabar)
    assert _message(rule, events, bucket) == _old_alert(rule, events, bucket)                 # flag off: byte-identical
    title, body = _message(rule, events, bucket, signal_v2=True)
    for text in (title, body):
        _clean(text, "signal_own", own=symbols)
    assert ("may not hold" in body) == intrabar and ("others" in body) == (len(symbols) > 10)


def test_the_telegram_and_email_bodies_carry_the_alert_text_and_the_time_in_the_envelope():
    from app.alerts.dispatcher import render_text
    from app.alerts.engine import _message
    title, body = _message(_alert_rule(), _events(["TCS"]), None, signal_v2=True)
    note = SimpleNamespace(severity="INFO", title=title, message=body, event_type="SCREEN_ALERT",
                           created_at=datetime(2026, 3, 10, 4, 0, tzinfo=timezone.utc))
    plain, html = render_text(note)
    assert title in plain and body in plain and "10 Mar 09:30 IST" in plain and escape(body) in html


# --- notify-only setups of the execution core --------------------------------------------------------------------------------------

def _signal(**kw):
    from app.core.models import Signal, SignalDirection
    base = dict(symbol="DEMO1", strategy_id="demo_strategy", strategy_name="Trend pullback", direction=SignalDirection.LONG,
                timestamp=datetime(2026, 3, 10, tzinfo=timezone.utc), entry=100.5, stop_loss=98.25, target1=104.75,
                reasons=["The pullback reached the buyer area", "RSI 41 (oversold bounce)"])
    base.update(kw)
    return Signal(**base)


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_notify_only_setup_texts(direction):
    from app.execution.core.dispatcher import _money, notify_text
    row = SimpleNamespace(direction=direction, symbol="DEMO1", strategy_id="demo_strategy")
    sig = _signal()
    old = (f"Scan setup: {direction} DEMO1",
           f"Scan setup: {direction} DEMO1 at {_money(100.5)} (stop {_money(98.25)}, target {_money(104.75)}) - demo_strategy. "
           "Notify only: nothing is proposed or placed.")
    assert notify_text("Scan setup", row, sig) == old                                          # flag off: byte-identical
    title, message = notify_text("Scan setup", row, sig, signal_v2=True)
    for text in (title, message):
        _clean(text, "signal_own", own=["DEMO1"])
    assert "The pullback reached the buyer area" in message and "RSI" not in message
    assert ("upside" if direction == "LONG" else "downside") in message


# --- the daily brief -------------------------------------------------------------------------------------------------------------

def _memory(regime, vix):
    return {"symbols": [{"symbol": "NIFTY 50", "regime": regime, "bias": "NEUTRAL", "change_pct": 0.1, "higher_regime": regime}],
            "cues": [{"symbol": "INDIA VIX", "last_price": vix, "change_pct": -1.0}], "globals": [], "history": {}}


BRIEF_DAYS = [("TRENDING_UP", 13.0), ("TRENDING_DOWN", 15.0), ("RANGING", 11.0), ("VOLATILE", 18.0), ("TRENDING_UP", 24.0), ("UNKNOWN", 14.0)]


@pytest.mark.parametrize("regime,vix", BRIEF_DAYS)
def test_the_brief_plan_texts(regime, vix):
    from app.ai import briefing
    from app.core.models import RiskConfig
    memory = _memory(regime, vix)
    dt = briefing.day_type(memory)
    events = [{"action": "BLOCK", "description": "RBI policy"}, {"action": "CUT", "description": "Budget"}]
    off = briefing.game_plan("en", dt, memory, "new", events, cfg=RiskConfig(), cfg_saved=True)
    on = briefing.game_plan("en", dt, memory, "new", events, cfg=RiskConfig(), cfg_saved=True, signal_v2=True)
    risk = briefing.risk_line("en", RiskConfig(), True)                     # the user's own settings record, unchanged
    for text in [on["headline"], *(x for x in on["lines"] if x != risk)]:
        problems = [p for p in L.check(text, "education", names=NAMES)] + [{"call_word": w} for w in L.call_words(text)]
        assert problems == [] and banned_terms(text) == [], (text, problems)
    if dt["kind"] != "VOLATILE" and vix < 20:
        assert on == off                                                     # nothing to change: identical even with the flag on


def test_the_brief_texts_with_the_flag_off_are_the_former_ones():
    from app.ai import briefing
    dt = briefing.day_type(_memory("TRENDING_UP", 24.0))
    plan = briefing.game_plan("en", dt, _memory("TRENDING_UP", 24.0), None, [])
    assert plan["headline"] == ("A volatile day on the data: wide swings and gaps, so ATR-based stops are wider and position sizes "
                                "come out smaller.")
    assert "VIX is 20 or higher: fear is high and gaps are more likely; your risk settings size every trade from its stop." in plan["lines"]


# --- the Copilot teacher -----------------------------------------------------------------------------------------------------------

def test_every_teacher_concept_is_education_without_security_names_or_amounts():
    from app.ai.knowledge import CONCEPTS
    for c in CONCEPTS:
        for text in (c.en, c.body_en):
            assert L.check(text, "education", names=NAMES) == [], (c.id, L.check(text, "education", names=NAMES))


def _teach(question, signal_v2):
    from app.ai import copilot_chat as chat
    from evals import runner
    return _run(chat.answer(question, runner.recorded_runner(runner.copilot_fixtures()["default"], []), signal_v2=signal_v2))


@pytest.mark.parametrize("question", ["Teach me: what is a demand zone?", "Teach me: what is a supply zone?", "What is market structure?"])
def test_the_teacher_lines_on_the_users_own_chart(question):
    off, on = _teach(question, False), _teach(question, True)
    chart = [s for s in on.sections if s["title"].startswith("On your")]
    assert chart, on.sections
    for line in chart[0]["lines"]:
        _clean(line, "signal_own", own=["NIFTY"])
        assert "protected level" not in line
    old = [s for s in off.sections if s["title"].startswith("On your")]
    assert [s["title"] for s in old] == [s["title"] for s in chart]
    assert {k: v for k, v in off.as_dict().items() if k != "sections"} == {k: v for k, v in on.as_dict().items() if k != "sections"}


def test_the_teacher_with_the_flag_off_is_the_former_text():
    out = _teach("Teach me: what is a demand zone?", False)
    assert any("demand area at 24,520 - 24,610" in x for s in out.sections for x in s["lines"])
    on = _teach("Teach me: what is a demand zone?", True)
    assert any("Buyer area: 24,520–24,610" in x for s in on.sections for x in s["lines"])          # kept by the number check too


# --- share cards and the register of surfaces --------------------------------------------------------------------------------------

REGISTERED = {
    "app/strategy_templates/language.py": "strategy template signal texts",
    "app/signals_feed/service.py": "Signals inbox why lines",
    "app/alerts/engine.py": "scan alert texts",
    "app/execution/core/dispatcher.py": "notify-only setup texts",
    "app/ai/briefing.py": "daily brief plan texts",
    "app/ai/copilot_chat.py": "Copilot teacher lines",
    "app/chart_analyze/signal_language.py": "Analyze a chart (withheld on a call word)",
    "app/signals_feed/day_one.py": "Signals day one: the adopted watch's state lines (withheld on a problem)",
}


def test_the_day_one_watch_lines_are_his_own_signals_in_the_signal_language():
    from app.signals_feed import day_one as D
    from app.strategy_templates import ids, library
    lines = [*D.WATCHING.values(), D.WATCHING_ANY, *D.PLAIN.values(), *D.WAITING_ANY.values(), D.NO_DATA_NOTE,
             *(w for sides in D.WAITING.values() for w in sides.values())]
    for tid in D.OFFERS:
        t = library.get(tid)
        s = ids.adopt(t, D.TIMEFRAME)[1]
        lines += [library.signal_text(t, s, side) for side in ("long", "short")]
    for line in lines:
        _clean(f"DEMO1 · {L.area_label('buyer', 23000, 23100)} {line}", "signal_own", own=["DEMO1"])


def test_no_share_card_producer_exists_without_a_guard():
    found = [str(p.relative_to(APP.parent)) for p in APP.rglob("*.py") if re.search(r"share[_-]?card", p.name + p.read_text(encoding="utf-8"), re.I)]
    assert found == [], f"a share-card producer must run its text through app/signals/language.py and be registered here: {found}"


def test_every_registered_surface_uses_the_shared_module():
    for path in REGISTERED:
        text = (APP.parent / path).read_text(encoding="utf-8")
        assert "app.signals" in text or "signal_language" in text, path
