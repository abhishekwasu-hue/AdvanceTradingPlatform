"""TERM-CON C4 (plan 3.4): the AI summary - on tap, his own key, only the read's facts, the Copilot's checks plus no
trade-call word; refused twice = no summary. No key: a plain line, the readings need none."""
from __future__ import annotations

import asyncio
import uuid

from app.conclusion import FLAGS
from app.conclusion import summary as SUM
from tests.test_amw2_watchlists import _set_flag
from tests.test_auth_api import client
from tests.test_phase_l_ai import _owner

BODY = {"glance": {"lean": {"words": "Lean up · 3 of 4"}, "meter": {"up": 3, "down": 1, "range": 0},
                   "line": "15-minute structure up · OI up", "agrees": [{"text": "15-minute structure up · OI up"}],
                   "conflicts": [{"text": "OI up · Positions down"}]},
        "needs_you": [{"text": "1 order not placed today"}],
        "sections": [{"title": "OI", "lines": [{"text": "PCR 1.12 (balanced)."}]}]}


class Fake:
    name = "fake"

    def __init__(self, *texts: str) -> None:
        self.texts, self.calls = list(texts), []

    async def complete(self, system: str, user: str, max_tokens: int = 0) -> str:
        self.calls.append((system, user))
        return self.texts.pop(0) if self.texts else ""


def _run(coro):
    return asyncio.run(coro)


def test_the_facts_are_only_the_read():
    facts = SUM.facts_of(BODY)
    assert facts[0] == "Lean up · 3 of 4" and "Readings: 3 up, 1 down, 0 in a range or mixed" in facts
    assert "Conflicts: OI up · Positions down" in facts and "Needs attention: 1 order not placed today" in facts
    assert "OI: PCR 1.12 (balanced)." in facts


def test_a_clean_description_is_shown():
    text = "Most readings lean up: the 15-minute structure and OI agree. Your positions point the other way. One order was not placed today."
    p = Fake(text)
    got, why = _run(SUM.summarize(p, BODY))
    assert got and "lean up" in got and why == "ok" and len(p.calls) == 1
    assert "never use the words buy, sell, target, stop" in " ".join(p.calls[0][0].split())


def test_a_call_word_or_an_unknown_number_is_refused_twice_then_no_summary():
    p = Fake("The readings lean up, so buy now.", "Readings lean up; consider a target at 25,000.")
    got, why = _run(SUM.summarize(p, BODY))
    assert got is None and len(p.calls) == 2 and "refused" in p.calls[1][1]
    q = Fake("PCR is 1.4 today.", "PCR is 1.4.")
    assert _run(SUM.summarize(q, BODY))[0] is None


def test_a_provider_error_keeps_the_rule_lines():
    class Broken(Fake):
        async def complete(self, *a, **k):
            raise RuntimeError("down")
    got, why = _run(SUM.summarize(Broken(), BODY))
    assert got is None and why == "provider error: RuntimeError"


def test_the_route_without_a_key_says_so_and_needs_the_flags():
    headers, me = _owner(f"tc4-{uuid.uuid4().hex[:6]}@example.com")
    url = "/api/terminal/conclusion/summary"
    assert client.post(url, json={"symbol": "NIFTY"}).status_code == 401
    off = client.post(url, json={"symbol": "NIFTY"}, headers=headers)
    assert off.status_code == 404
    for flag in FLAGS:
        _set_flag(flag, me["tenant_id"], on=True)
    # (the owner helper has accepted the AI acknowledgement already; the route depends on it: require_ai_acknowledged)
    got = client.post(url, json={"symbol": "NIFTY"}, headers=headers)
    assert got.status_code == 200, got.text
    body = got.json()
    assert body["ready"] is False and body["text"] is None and "AI key" in body["reason"]
