"""TT12-1 (owner, DECISIONS 24 Oct): the one-time notice "Your risk limits are now not set - set your own in Settings >
Risk" (app/me/notices.py). Due only for an existing user whose stored limits were the old platform defaults (now "Not
set"), behind `ui_v2` (and `risk_by_default`, the page it points to). Never for a new user or one who had his own
values. Dismissing it, or setting any limit, marks it seen in the prefs namespace `notices` (no migration)."""
import json

from app.me import notices
from app.risk_engine import by_default
from tests.test_auth_api import client
from tests.test_tt12_limits_off import OLD, _store
from tests.test_z5_p1d_risk_by_default import _flag, _tenant_t

TEXT = "Your risk limits are now OFF by default — set your own in Settings → Risk"


def _org(tag: str, ui_v2: bool = True, risk: bool = True) -> dict:
    t = _tenant_t(f"tt12n-{tag}")
    _flag("ui_v2", t["tenant_id"], ui_v2)
    _flag(by_default.FLAG, t["tenant_id"], risk)
    return t


def _due(t: dict) -> list:
    got = client.get("/api/me/notices", headers=t["headers"])
    assert got.status_code == 200
    return got.json()["notices"]


def test_who_qualifies_from_the_stored_row():
    assert by_default.reset_by_tt12(OLD) is True                                       # every limit was the old default
    assert by_default.reset_by_tt12(json.dumps({"min_rr": 1.5})) is True                # one old-default limit is enough
    assert by_default.reset_by_tt12(None) is False and by_default.reset_by_tt12("") is False    # a new user: no row
    assert by_default.reset_by_tt12("{}") is False and by_default.reset_by_tt12("not json") is False
    own = json.dumps({**json.loads(OLD), "daily_loss_pct": 3.0})
    assert by_default.reset_by_tt12(own) is False                                     # he had a value of his own
    assert by_default.reset_by_tt12(json.dumps({**json.loads(OLD), "_set": []})) is False           # saved since TT-12
    assert by_default.reset_by_tt12(json.dumps({**json.loads(OLD), "_set": ["min_rr"]})) is False
    zeros = json.dumps({k: 0 for k in by_default.BOUNDS})
    assert by_default.reset_by_tt12(zeros) is False                                   # nothing was on: nothing changed
    waiting = json.dumps({**json.loads(OLD), "_pending": {"values": {"min_rr": 1.2}, "from": "2026-10-20"}})
    assert by_default.reset_by_tt12(waiting) is False                                 # a deferred value he chose


def test_an_existing_user_on_the_old_defaults_sees_the_notice_once_until_dismissed():
    t = _org("old")
    _store(t, OLD)
    assert _due(t) == [{"id": "limits_not_set", "text": TEXT, "link": "/risk-management"}]
    assert notices.TEXT[notices.LIMITS_NOT_SET] == TEXT
    assert _due(t) == _due(t)                                                          # reading does not mark it
    out = client.post("/api/me/notices/limits_not_set/seen", headers=t["headers"])
    assert out.status_code == 200 and out.json()["notices"] == []
    assert _due(t) == []
    prefs = client.get("/api/me/prefs", headers=t["headers"]).json()
    assert prefs["prefs"]["notices"]["limits_not_set_seen_at"] and prefs["versions"]["notices"] == 1
    # A second dismissal keeps the first mark (the version does not move).
    client.post("/api/me/notices/limits_not_set/seen", headers=t["headers"])
    assert client.get("/api/me/prefs", headers=t["headers"]).json()["versions"]["notices"] == 1
    assert client.post("/api/me/notices/other/seen", headers=t["headers"]).status_code == 404


def test_setting_any_limit_marks_it_seen():
    t = _org("set")
    _store(t, OLD)
    assert len(_due(t)) == 1
    assert client.put("/api/paper/risk", headers=t["headers"], json={"max_trades_per_day": 4}).status_code == 200
    assert _due(t) == []
    assert client.get("/api/me/prefs", headers=t["headers"]).json()["prefs"]["notices"]["limits_not_set_seen_at"]


def test_never_for_a_new_user_or_one_with_his_own_values():
    new = _org("new")
    assert _due(new) == []
    client.get("/api/paper/risk", headers=new["headers"])                              # an account row, nothing stored
    assert _due(new) == []
    own = _org("own")
    _store(own, json.dumps({**json.loads(OLD), "risk_per_trade_pct": 0.5}))
    assert _due(own) == []


def test_behind_ui_v2_and_the_risk_page():
    off = _org("v2off", ui_v2=False)
    _store(off, OLD)
    assert _due(off) == []
    no_page = _org("riskoff", risk=False)
    _store(no_page, OLD)
    assert _due(no_page) == []


def test_per_user_a_teammate_mark_is_not_mine():
    a, b = _org("a"), _org("b")
    _store(a, OLD)
    _store(b, OLD)
    client.post("/api/me/notices/limits_not_set/seen", headers=a["headers"])
    assert _due(a) == [] and len(_due(b)) == 1
