"""N2-11 (P1 spec 1.6, docs/design/N2_P1FGH.md): "learn from your journal" cards behind `journal_lessons` (off by
default).

Covers:
- the caller's own counted tags on their own trades in the window;
- only tags with a losing trade get a card, the costliest first;
- DISMISSED never counts, and SUGGESTED counts only while count_unconfirmed is on;
- another user's tags never count, including a teammate's in the same organisation with the same tags (and a
  teammate's tag on the caller's own trade);
- each card's lesson is a step of the method library that lists the tag (drift check against the frontend);
- flag off 404, discipline engine off 503.
"""
import asyncio
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.db.models import JournalTagRecord, TradeRecord
from app.journal import capture, lessons
from app.journal.settings import TAGS
from app.platform import controls
from tests.test_auth_api import _session_factory, client
from tests.test_trading_worker import _tenant
from tests.test_z3b_paper_account import _member

UTC = timezone.utc
LEARN_TS = Path(__file__).resolve().parents[2] / "frontend" / "src" / "progression" / "learn.ts"


def _run(coro):
    return asyncio.run(coro)


def _email(tag: str) -> str:
    return f"n2l-{tag}-{uuid.uuid4().hex[:6]}@example.com"


def _set(name: str, entry: dict) -> None:
    async def go():
        async with _session_factory() as session:
            current = await controls._get(session, controls.KEY_FEATURE_FLAGS)
            flags = dict(current.get("flags") or {})
            flags[name] = entry
            await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": flags}, None)
            await session.commit()
    _run(go())
    capture.reset_flag_cache()


@pytest.fixture
def engine_on():
    _set("discipline_engine", {"on": True, "tenants": []})
    yield
    _set("discipline_engine", {"on": False, "tenants": []})


def _trade(t, pnl, *, days_ago=2, open_=False):
    async def go():
        async with _session_factory() as session:
            entry = datetime.now(UTC) - timedelta(days=days_ago)
            row = TradeRecord(tenant_id=t["tenant_id"], user_id=t["user_id"], mode="PAPER", symbol="RELIANCE", strategy_id="manual", direction="LONG",
                              entry_time=entry, entry_price=100.0, quantity=10.0, stop_loss=95.0, exchange="NSE",
                              pnl=None if open_ else pnl, exit_time=None if open_ else entry + timedelta(hours=1),
                              exit_price=None if open_ else 100.0 + pnl / 10.0)
            session.add(row)
            await session.commit()
            return row.id
    return _run(go())


def _tag(t, trade_id, tag, status="SUGGESTED"):
    async def go():
        async with _session_factory() as session:
            session.add(JournalTagRecord(tenant_id=t["tenant_id"], user_id=t["user_id"], trade_id=trade_id, tag=tag, source="AUTO", status=status))
            await session.commit()
    _run(go())


def test_every_tag_has_a_lesson_listed_under_that_step_in_the_library():
    assert set(lessons.LESSON_FOR_TAG) == set(TAGS)
    src = LEARN_TS.read_text()
    block = src[src.index("CONCEPT_LINKS"):src.index("};", src.index("CONCEPT_LINKS"))]
    listed = {concept: set(re.findall(r'"([a-z_]+)"', tags)) for concept, tags in re.findall(r"(\w+): \{[^}]*tags: \[([^\]]*)\]", block)}
    for tag, concept in lessons.LESSON_FOR_TAG.items():
        assert tag in listed.get(concept, set()), (tag, concept)


def test_flag_off_404_and_engine_off_503():
    assert lessons.FLAG in controls.FEATURE_FLAGS and lessons.FLAG in controls.DEFAULT_OFF_FLAGS
    t = _tenant(_email("off"))
    r = client.get("/api/journal/lessons", headers=t["headers"])
    assert r.status_code == 404 and r.headers.get("X-Feature-Disabled") == "journal_lessons"
    _set(lessons.FLAG, {"on": False, "tenants": [t["tenant_id"]]})
    r = client.get("/api/journal/lessons", headers=t["headers"])
    assert r.status_code == 503 and r.headers.get("X-Feature-Disabled") == "discipline_engine"


def test_cards_are_the_callers_own_losing_patterns(engine_on):
    owner = _tenant(_email("owner"))
    _set(lessons.FLAG, {"on": False, "tenants": [owner["tenant_id"]]})
    member = _member(owner)
    a = _trade(owner, -300.0)                      # fomo + no_stop
    b = _trade(owner, -100.0)                      # fomo
    c = _trade(owner, 250.0)                       # fomo on a winner
    d = _trade(owner, -50.0)                       # early_exit, but DISMISSED
    e = _trade(owner, -400.0, days_ago=120)        # outside the window
    f = _trade(owner, 0.0, open_=True)             # overtrading on an open trade: no losing trade, no card
    for tid in (a, b, c):
        _tag(owner, tid, "fomo_entry")
    _tag(owner, a, "no_stop", status="CONFIRMED")
    _tag(owner, d, "early_exit", status="DISMISSED")
    _tag(owner, e, "revenge_trade", status="CONFIRMED")
    _tag(owner, f, "overtrading")
    m = _trade(member, -900.0)
    _tag(member, m, "averaging_loser", status="CONFIRMED")

    body = client.get("/api/journal/lessons", headers=owner["headers"]).json()
    cards = body["cards"]
    assert [c["tag"] for c in cards] == ["fomo_entry", "no_stop"]            # costliest first; dismissed, old, open-only, member's: none
    fomo = cards[0]
    assert fomo == {"tag": "fomo_entry", "lesson": "pullback", "trades": 3, "losing_trades": 2, "losing_net_pnl": -400.0, "last_date": fomo["last_date"]}
    assert cards[1]["lesson"] == "level" and cards[1]["losing_net_pnl"] == -300.0 and body["count_unconfirmed"] is True
    # With count_unconfirmed off, SUGGESTED tags stop counting.
    assert client.put("/api/journal/settings", headers=owner["headers"], json={"count_unconfirmed": False}).status_code == 200
    body = client.get("/api/journal/lessons", headers=owner["headers"]).json()
    assert [c["tag"] for c in body["cards"]] == ["no_stop"]
    # The member sees only their own card.
    _set(lessons.FLAG, {"on": False, "tenants": [owner["tenant_id"]]})
    mine = client.get("/api/journal/lessons", headers=member["headers"]).json()["cards"]
    assert [c["tag"] for c in mine] == ["averaging_loser"] and mine[0]["losing_net_pnl"] == -900.0


def test_a_teammates_tags_never_count(engine_on):
    owner = _tenant(_email("own2"))
    _set(lessons.FLAG, {"on": False, "tenants": [owner["tenant_id"]]})
    mate = _member(owner)                                    # a second user of the same organisation
    assert mate["tenant_id"] == owner["tenant_id"] and mate["user_id"] != owner["user_id"]
    a = _trade(owner, -200.0)
    _tag(owner, a, "fomo_entry", status="CONFIRMED")
    # The teammate has the same tags on their own losing trades, and one on the owner's trade.
    for pnl in (-700.0, -500.0):
        tid = _trade(mate, pnl)
        _tag(mate, tid, "fomo_entry", status="CONFIRMED")
        _tag(mate, tid, "no_stop", status="CONFIRMED")
    _tag(mate, a, "no_stop", status="CONFIRMED")

    cards = client.get("/api/journal/lessons", headers=owner["headers"]).json()["cards"]
    assert cards == [{"tag": "fomo_entry", "lesson": "pullback", "trades": 1, "losing_trades": 1, "losing_net_pnl": -200.0,
                      "last_date": cards[0]["last_date"]}]
    theirs = client.get("/api/journal/lessons", headers=mate["headers"]).json()["cards"]
    assert {c["tag"]: (c["trades"], c["losing_trades"], c["losing_net_pnl"]) for c in theirs} == {
        "fomo_entry": (2, 2, -1200.0), "no_stop": (2, 2, -1200.0)}
