"""U2 remaining blocks (SCREENER_SPEC §3 builder; Phase 0: the blocks the shipped screens use): `POST /api/screener/stages`
turns a screen into the funnel's stages - its top-level conjuncts with parameters inlined - so Legends and breakout
screens open in the builder (formula blocks for what the other forms cannot hold). Flag `screener_builder_u2`, off."""
import asyncio

import pytest

import app.main  # noqa: F401 - registers the template sources
from app.platform import controls
from app.screener import legends as lg
from app.screener import nodes as n
from app.screener import parse
from app.screener import stages as st
from app.screener.breakouts.templates import TEMPLATES, screenql, template_params
from tests.test_auth_api import _session_factory, client
from tests.test_phase_l_ai import _owner


def _run(coro):
    return asyncio.run(coro)


def _set_flags(**flags):
    async def go():
        async with _session_factory() as session:
            current = await controls._get(session, controls.KEY_FEATURE_FLAGS)
            stored = dict(current.get("flags") or {})
            for name, on in flags.items():
                stored[name] = {"on": on, "tenants": []}
            await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": stored}, None)
            await session.commit()
    _run(go())


@pytest.fixture
def u2_on():
    _set_flags(screener_v2=True, **{st.FLAG: True})
    yield
    _set_flags(screener_v2=False, screener_legends=False, **{st.FLAG: False})


def _rejoined(stages):
    return parse(" AND ".join(s["wrapped"] for s in stages))


def test_every_shipped_screen_splits_into_stages_that_join_back_to_the_same_screen():
    for card in lg.load().cards:
        flat = st.inline(parse(card.source), card.defaults())
        stages = st.split(flat)
        assert "$" not in n.to_text(flat)
        assert _rejoined(stages) == flat, card.id
    for t in TEMPLATES:
        text, _ = screenql(t.screen, t.signal_tf, template_params(t))
        if text is None:
            continue
        ast = parse(text)
        assert _rejoined(st.split(ast)) == ast, t.id


def test_a_conjunct_that_is_an_or_is_bracketed_to_stay_one_stage():
    stages = st.split(parse("close > 1 AND (high > 2 OR low < 1) AND (open > 1 AND volume > 0)"))
    assert [s["wrapped"] for s in stages] == ["close > 1", "(high > 2 OR low < 1)", "(open > 1 AND volume > 0)"]
    assert stages[1]["text"] == "high > 2 OR low < 1" and stages[0]["ast"]["k"] == "Compare"


def test_the_route_is_off_by_default():
    headers, _ = _owner("u2-off@example.com")
    _set_flags(screener_v2=True)
    try:
        r = client.post("/api/screener/stages", json={"source": "close > 1"}, headers=headers)
        assert r.status_code == 503 and r.headers.get("X-Feature-Disabled") == st.FLAG
    finally:
        _set_flags(screener_v2=False)
    assert st.FLAG in controls.DEFAULT_OFF_FLAGS and st.FLAG in controls.FEATURE_FLAGS


def test_stages_of_a_source_and_of_a_library_screen(u2_on):
    headers, _ = _owner("u2-stages@example.com")
    out = client.post("/api/screener/stages", json={"source": "RSI(14) > $lvl AND high > Max(high, 20)[1]", "params": {"lvl": 60}},
                      headers=headers).json()
    assert [s["text"] for s in out["stages"]] == ["RSI(14) > 60", "high > Max(high, 20)[1]"] and out["base_tf"] == "1d"
    assert client.post("/api/screener/stages", json={"source": "close > > 1"}, headers=headers).status_code == 422
    assert client.post("/api/screener/stages", json={}, headers=headers).status_code == 422
    hidden = client.post("/api/screener/stages", json={"library_id": "legends.turtle_channel_breakout"}, headers=headers)
    assert hidden.status_code == 404                                                    # the Legends flag is off
    _set_flags(screener_legends=True)
    card = client.post("/api/screener/stages", json={"library_id": "legends.turtle_channel_breakout"}, headers=headers).json()
    assert card["name"] and card["text"] == "high > Max(high, 20)[1]" and len(card["stages"]) == 1
    minervini = client.post("/api/screener/stages", json={"library_id": "legends.minervini_trend_template"}, headers=headers).json()
    assert len(minervini["stages"]) == 7 and all("$" not in s["text"] for s in minervini["stages"])
    assert client.post("/api/screener/stages", json={"library_id": "legends.nope"}, headers=headers).status_code == 404
    assert client.post("/api/screener/stages", json={"library_id": "starter.momentum"}, headers=headers).status_code == 404
    assert client.post("/api/screener/stages", json={"library_id": "breakouts.daily_52w_high"}, headers=headers).status_code == 404  # flag off


def test_a_card_of_a_slice_that_is_off_is_not_read_through_stages(u2_on):
    """U7 LG2: an LG2 card (flag `screener_legends_lg2` on top of `screener_legends`) is not there while its slice is off -
    /stages answers as `GET /legends/{id}` does (404), so its source cannot be read through the builder."""
    headers, _ = _owner("u2-lg2@example.com")
    lg2 = next(c for c in lg.load().cards if c.slice == "lg2")
    _set_flags(screener_legends=True, **{lg.FLAG_LG2: False})
    try:
        hidden = client.post("/api/screener/stages", json={"library_id": lg2.id}, headers=headers)
        assert hidden.status_code == 404, hidden.text
        assert client.get(f"/api/screener/legends/{lg2.id}", headers=headers).status_code == 404     # the same answer
        assert client.post("/api/screener/stages", json={"library_id": "legends.turtle_channel_breakout"}, headers=headers).status_code == 200
        _set_flags(**{lg.FLAG_LG2: True})
        shown = client.post("/api/screener/stages", json={"library_id": lg2.id}, headers=headers)
        assert shown.status_code == 200 and shown.json()["stages"], shown.text
    finally:
        _set_flags(**{lg.FLAG_LG2: False})
