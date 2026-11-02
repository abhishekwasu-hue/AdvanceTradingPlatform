"""M76 MP-6 (S-1): his saved what-if scenarios live in his prefs (`what_if`). Nothing is pre-set (an empty list; each
shift None until he types it), at most 8, strict types, and the namespace is read by the page only."""
import uuid
from pathlib import Path

from app.me import prefs
from tests.test_auth_api import client
from tests.test_phase_l_ai import _owner


def _headers():
    headers, _ = _owner(f"mp6-{uuid.uuid4().hex[:12]}@example.com")
    return headers


def test_default_is_empty_and_his_scenarios_round_trip():
    h = _headers()
    got = client.get("/api/me/prefs", headers=h).json()
    assert got["prefs"]["what_if"] == {"scenarios": []}
    body = {"scenarios": [{"name": "Gap up", "spot_move": 150, "iv_change": -2.5, "days": None}, {"name": "Two days", "days": 2}]}
    put = client.put(f"/api/me/prefs/what_if?version={got['versions']['what_if']}", headers=h, json=body)
    assert put.status_code == 200, put.text
    assert put.json()["value"]["scenarios"] == [{"name": "Gap up", "spot_move": 150.0, "iv_change": -2.5, "days": None},
                                                {"name": "Two days", "spot_move": None, "iv_change": None, "days": 2}]


def test_limits_and_strict_types():
    h = _headers()
    many = {"scenarios": [{"name": f"S{i}", "spot_move": i} for i in range(prefs.MAX_WHAT_IF_SCENARIOS + 1)]}
    assert client.put("/api/me/prefs/what_if?version=0", headers=h, json=many).status_code == 422
    for bad in ({"name": "x", "spot_move": "100"}, {"name": "", "days": 1}, {"name": "x", "days": -1}, {"name": "x", "iv_change": 500},
                {"name": "x", "extra": 1}):
        assert client.put("/api/me/prefs/what_if?version=0", headers=h, json={"scenarios": [bad]}).status_code == 422, bad


def test_no_order_path_reads_it():
    app = Path(__file__).resolve().parents[1] / "app"
    readers = [p for p in app.rglob("*.py") if "WhatIfPrefs" in p.read_text(encoding="utf-8") or "\"what_if\"" in p.read_text(encoding="utf-8")]
    assert [p.relative_to(app).as_posix() for p in readers] == ["me/prefs.py"]
