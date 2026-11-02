"""M75 W-4 (owner order M75 CW-1 / CW-2; M75Q-5 default confirmed 1 Nov): the Copilot workspace's AI card.

* Analyze a chart's hybrid run reused on ATP's own rendered chart (no upload): an item of the user (source "workspace"),
  so his plan's analysis counts, the cost cap and his AI key apply as on Analyze a chart.
* Once per (user, symbol, timeframe, last closed bar): a later open of the same bar reuses the card (no second model call).
* Every number on the card is one of the facts' numbers; a line with any other number is withheld.
* A cap, no hybrid entry point or no stored candles: no card, one line why (the code reading stands on its own).
* Never in the answer: the provider, the model, the engine's name / version or the cost.
Made-up data only (the public fixture session, a fake engine and a fake model reply); no network.
"""
import json

from sqlalchemy import select

from app.chart_analyze import HYBRID_FLAG, limits, provider
from app.chart_analyze import workspace as W
from app.chart_analyze.engine import Answer
from app.db.models import ChartAnalysisRecord
from app.signals import language
from tests.chart_analyze_fakes import ImageOnlyFakeProvider, answer
from tests.test_auth_api import _session_factory, client  # noqa: F401 - the app client
from tests.test_chart_analyze_api import _run
from tests.test_chart_analyze_hybrid import EMPTY_SYMBOL, SYMBOL, _allow, _fake_ask, _hybrid_owner, _isolated, _lake, _texts, _zones  # noqa: F401 - fixtures

URL = "/api/chart-analyze/workspace"
LEAKS = ("anthropic", "claude-test", "fake-engine", "0.0-test-hybrid", "test-hybrid-hash", "cost_usd", "provider", "model")


def _owner(tag: str, *, hybrid_on: bool = True) -> dict:
    who = _hybrid_owner(f"w4-{tag}", hybrid_on=hybrid_on)
    for name in ("ui_v2", W.WORKSPACE_FLAG):
        _allow(name, who["tenant_id"])
    return who


def _post(who: dict, symbol: str = SYMBOL, tf: str = "15m"):
    return client.post(URL, headers=who["headers"], json={"symbol": symbol, "timeframe": tf})


def _items(who: dict) -> list[ChartAnalysisRecord]:
    async def go():
        async with _session_factory() as session:
            return list((await session.execute(select(ChartAnalysisRecord).where(ChartAnalysisRecord.user_id == who["user_id"]))).scalars())
    return _run(go())


def _clean(body: dict) -> None:
    text = json.dumps(body)
    for leak in LEAKS:
        assert leak not in text, leak


def test_off_until_the_flags_say_so(monkeypatch):
    who = _hybrid_owner("w4-off")
    assert _post(who).status_code == 404                                  # ui_v2 / copilot_workspace off
    for name in ("ui_v2", W.WORKSPACE_FLAG):
        _allow(name, who["tenant_id"])
    _allow(HYBRID_FLAG, who["tenant_id"], on=False)
    calls = _fake_ask(monkeypatch)
    r = _post(who)
    assert r.status_code == 200 and r.json()["status"] == "off" and r.json()["card"] is None and not calls and not _items(who)


def test_one_model_call_per_closed_bar_and_the_plan_counts_it(monkeypatch):
    _zones(monkeypatch)
    calls = _fake_ask(monkeypatch)
    who = _owner("once")
    first = _post(who)
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["status"] == "ready" and body["card"] and not body["cached"] and body["as_of"]
    assert len(calls) == 1 and "hybrid" in calls[0]["user"]                # ATP's rendered chart + facts, the hybrid entry point
    again = _post(who).json()
    assert again["cached"] and again["card"] == body["card"] and len(calls) == 1      # the same bar: reused, no second call
    rows = _items(who)
    assert len(rows) == 1 and rows[0].source == W.SOURCE and rows[0].mode == "hybrid"  # one item: the plan's counts see it
    _clean(body)
    assert all(language.is_clean(t) for t in _texts(body["card"]))
    other = _post(who, tf="1h").json()                                     # another timeframe: never the 15m card
    assert not other["cached"] and other["card"] != body["card"]


def test_every_number_on_the_card_is_in_the_facts(monkeypatch):
    _zones(monkeypatch)
    _fake_ask(monkeypatch)
    who = _owner("grounded")
    body = _post(who).json()
    done = W.service.as_dict(_items(who)[0])["hybrid"]
    allowed = W.allowed_numbers(done)
    from app.ai.grounding import check_numbers
    assert all(check_numbers(t, allowed)[0] or t == W.WITHHELD for t in _texts(body["card"]))
    card = W.ground({"story": "Price sits near 123456.78 now.", "trend": {"why": "A higher high on the 15-minute chart."}}, allowed)
    assert card["story"] == W.WITHHELD and card["trend"]["why"] == "A higher high on the 15-minute chart."


def test_a_cap_or_a_missing_part_leaves_the_code_reading_alone(monkeypatch):
    _zones(monkeypatch)
    calls = _fake_ask(monkeypatch)
    who = _owner("cap")

    async def refuse(*a, **k):
        raise limits.LimitReached("Limit reached: your plan allows 0 chart analyses a day and you have used them.")
    monkeypatch.setattr(limits, "check_can_add", refuse)
    r = _post(who).json()
    assert r["status"] == "limit" and r["card"] is None and "code reading" in r["message"] and not calls and not _items(who)
    monkeypatch.undo()
    _zones(monkeypatch)
    calls = _fake_ask(monkeypatch)
    provider.set_for_tests(ImageOnlyFakeProvider())                      # no hybrid entry point on this server
    r = _post(who).json()
    assert r["status"] == "code_only" and r["card"] is None and not calls and not _items(who)
    provider.set_for_tests(None)
    assert _post(who).json()["status"] == "code_only"


def test_no_stored_candles_says_so(monkeypatch):
    calls = _fake_ask(monkeypatch)
    who = _owner("empty")
    r = _post(who, symbol=EMPTY_SYMBOL).json()
    assert r["status"] == "no_data" and not calls and not _items(who)
    assert _post(who, tf="3m").status_code == 422                          # a timeframe the hybrid reading does not offer


def test_a_model_error_is_one_plain_line(monkeypatch):
    _zones(monkeypatch)

    async def fake_ask(session, user, tenant, instr, png, settings, *, now, client=None):
        return Answer(text=json.dumps(answer("forming")), error="Busy right now.", provider="anthropic", model="claude-test",
                      cost_usd=0.0, billed_to_user_key=False)
    monkeypatch.setattr(W.service, "ask", fake_ask)
    body = _post(_owner("err")).json()
    assert body["status"] == "code_only" and body["card"] is None and body["message"]
    _clean(body)


def test_the_card_never_carries_setup_levels_or_invalidation_prices():
    ai = answer("forming")
    card = W.short_card(ai)
    assert set(card) <= set(W.CARD_KEYS) and "setup" not in card and "key_levels" not in card
    assert set(card.get("scenarios") or {}) <= {"primary", "alternative"}
    assert set(card.get("what_to_wait_for") or {}) <= {"condition"}
    assert "primary_invalidated_at" not in json.dumps(card) and '"invalidation"' not in json.dumps(card) and '"trigger"' not in json.dumps(card)


def test_a_refused_run_is_not_retried_on_the_same_bar(monkeypatch):
    _zones(monkeypatch)
    from fastapi import HTTPException
    calls = _fake_ask(monkeypatch, raises=HTTPException(status_code=429, detail={"message": "This month's AI budget is used up."}))
    who = _owner("tried")
    first = _post(who).json()
    assert first["status"] == "unavailable" and "budget" in first["message"] and len(calls) == 1
    again = _post(who).json()
    assert again["status"] == "unavailable" and again["message"] == W.TRIED and len(calls) == 1 and len(_items(who)) == 1
