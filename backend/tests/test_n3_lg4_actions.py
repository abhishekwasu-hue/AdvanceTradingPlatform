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
