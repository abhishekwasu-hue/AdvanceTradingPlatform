"""U7 LG4a: actions on a Legends card (flag `screener_legends_actions`, off by default) - Run on my list for every card,
an alert rule only where a one-symbol bar-close rule can hold the screen (not a list ranking, not fundamentals), with
the reason otherwise. The actions reuse POST /api/screener/run and POST /api/alerts/rules (no new route). Nothing here
places an order."""
import re
import uuid

from app.platform import controls
from app.screener import legends as lg
from tests.test_auth_api import client  # noqa: F401 - fixtures
from tests.test_phase_l_ai import _owner
from tests.test_z8_tx1_taxonomy import _set_flags


def _filled(card):
    by = {p["name"]: p["default"] for p in card["screen"]["params"]}
    return re.sub(r"\$([A-Za-z_][A-Za-z0-9_]*)", lambda m: str(by.get(m.group(1), m.group(0))), card["screen"]["source"])


def test_card_actions_follow_their_flag_and_say_why_an_alert_cannot_hold_a_screen():
    assert lg.ACTIONS_FLAG in controls.DEFAULT_OFF_FLAGS and "places no orders" in controls.FEATURE_FLAGS[lg.ACTIONS_FLAG]
    headers, _ = _owner(f"lg4-{uuid.uuid4().hex[:8]}@example.com")
    _set_flags(screener_v2=True, screener_legends=True, screener_legends_lg3=True)
    try:
        assert "actions" not in client.get("/api/screener/legends", headers=headers).json()
        _set_flags(screener_legends_actions=True)
        body = client.get("/api/screener/legends", headers=headers).json()
        acts = body["actions"]
        assert set(acts) == {c["id"] for c in body["cards"]} and all(a["run"] for a in acts.values())
        assert not acts["legends.minervini_trend_template"]["alert"] and "ranks" in acts["legends.minervini_trend_template"]["alert_reason"]
        assert not acts["legends.graham_net_net"]["alert"] and "fundamentals" in acts["legends.graham_net_net"]["alert_reason"]
        assert acts["legends.darvas_box_breakout"] == {"run": True, "alert": True, "alert_reason": None}
        one = client.get("/api/screener/legends/legends.darvas_box_breakout", headers=headers).json()
        assert one["actions"]["alert"] is True
        darvas = next(c for c in body["cards"] if c["id"] == "legends.darvas_box_breakout")
        made = client.post("/api/alerts/rules", json={"name": darvas["name"], "kind": "instrument", "symbol": "RELIANCE", "condition": _filled(darvas),
                                                      "base_tf": darvas["screen"]["base_tf"]}, headers=headers)
        assert made.status_code == 201, made.text                                       # what the card's alert action sends
    finally:
        _set_flags(screener_v2=False, screener_legends=False, screener_legends_lg3=False, screener_legends_actions=False)


def test_alert_me_is_refused_with_the_reason_where_a_rule_would_not_run_or_be_made():
    """LG4a review: a weekly card offers no alert (the bar-close engine evaluates 1m-1d only); the organisation's and
    the user's refusals come from the same checks POST /api/alerts/rules runs, per request."""
    import asyncio

    from app.alerts.barclose import SUPPORTED
    from tests.test_auth_api import _session_factory
    weinstein = lg.load().card("legends.weinstein_stage2_breakout")
    assert weinstein.base_tf not in SUPPORTED
    static = lg.card_actions(weinstein)
    assert static["alert"] is False and "1-minute to daily" in static["alert_reason"]
    headers, me = _owner(f"lg4-tf-{uuid.uuid4().hex[:8]}@example.com")
    _set_flags(screener_v2=True, screener_legends=True, screener_legends_actions=True)
    try:
        acts = client.get("/api/screener/legends", headers=headers).json()["actions"]
        assert acts["legends.weinstein_stage2_breakout"]["alert"] is False
        assert acts["legends.darvas_box_breakout"]["alert"] is True
        darvas = lg.load().card("legends.darvas_box_breakout")
        assert "$" not in lg.filled_source(darvas)

        async def demote():
            async with _session_factory() as session:
                from app.db.models import User
                user = await session.get(User, me["id"])
                user.role = "VIEWER"
                await session.commit()
        asyncio.run(demote())
        viewer = client.get("/api/screener/legends/legends.darvas_box_breakout", headers=headers).json()["actions"]
        assert viewer["alert"] is False and "role" in viewer["alert_reason"]                 # create_rule would answer 403
        assert viewer["run"] is True
    finally:
        _set_flags(screener_v2=False, screener_legends=False, screener_legends_actions=False)


def test_the_refusal_is_the_create_routes_own():
    """instrument_rule_refusal runs create_rule's checks: a condition the route refuses is refused with its reason."""
    import asyncio

    from app.alerts.rules_routes import instrument_rule_refusal
    from tests.test_auth_api import _session_factory
    _, me = _owner(f"lg4-chk-{uuid.uuid4().hex[:8]}@example.com")
    _set_flags(screener_v2=True)
    try:
        async def go(condition, tf="1d"):
            async with _session_factory() as session:
                from app.db.models import User
                user = await session.get(User, me["id"])
                return await instrument_rule_refusal(session, user, condition, tf)
        assert asyncio.run(go("close > SMA(close, 20)")) is None
        assert "do not read fundamentals" in asyncio.run(go("EarningsYield() > 5"))
        assert "did not pass validation" in asyncio.run(go("close >"))
        assert "1-minute to daily" in asyncio.run(go("close > 1", "1M"))
        _set_flags(screener_v2=False)
        assert "screener_v2" in asyncio.run(go("close > 1"))
    finally:
        _set_flags(screener_v2=False)


def test_a_card_a_rule_cannot_hold_is_refused_with_the_reason_never_a_500(monkeypatch):
    """LG4a review (LOW): a card screen the rule body refuses (e.g. longer than a rule condition may be) answers with the
    reason; the Legends page never fails."""
    import asyncio

    from app.alerts.rules_routes import instrument_rule_refusal
    from tests.test_auth_api import _session_factory
    headers, me = _owner(f"lg4-val-{uuid.uuid4().hex[:8]}@example.com")
    _set_flags(screener_v2=True, screener_legends=True, screener_legends_actions=True)
    try:
        async def go(condition, exchange="NSE"):
            async with _session_factory() as session:
                from app.db.models import User
                user = await session.get(User, me["id"])
                return await instrument_rule_refusal(session, user, condition, "1d", exchange=exchange)
        long_condition = "close > 1 and " * 400 + "close > 1"
        assert "cannot hold this screen" in asyncio.run(go(long_condition))
        assert "cannot hold this screen" in asyncio.run(go("close > 1", exchange="nse!"))
        monkeypatch.setattr(lg, "filled_source", lambda card: long_condition)
        r = client.get("/api/screener/legends", headers=headers)
        assert r.status_code == 200, r.text
        darvas = r.json()["actions"]["legends.darvas_box_breakout"]
        assert darvas["alert"] is False and "cannot hold this screen" in darvas["alert_reason"] and darvas["run"] is True
    finally:
        _set_flags(screener_v2=False, screener_legends=False, screener_legends_actions=False)
