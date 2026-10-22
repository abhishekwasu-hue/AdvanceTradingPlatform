"""UI2-4a1: the caller's own UI preferences (`GET /api/me/prefs`, `PUT /api/me/prefs/{namespace}`) - defaults, schema
validation, the size cap, optimistic versions, and isolation per user and per organisation - plus the migration."""
import asyncio
import json
import uuid
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

import pytest

from app.db.models import UserPrefsRecord
from app.me import prefs
from tests.test_auth_api import _session_factory, client
from tests.test_phase_l_ai import _owner

BASE = "/api/me/prefs"
CHART_DEFAULTS = {"theme": "app", "grid": False, "candle_colors": None, "indicators": {"volume": True, "rsi": True},
                  "reader_overlay": False}


def _run(coro):
    return asyncio.run(coro)


def _who(tag: str):
    headers, me = _owner(f"ui24a1-{tag}-{uuid.uuid4().hex[:6]}@example.com")
    return {"headers": headers, "tenant_id": me["tenant_id"], "user_id": me["id"]}


def _invite(owner: dict, role: str) -> dict:
    email = f"ui24a1-{role.lower()}-{uuid.uuid4().hex[:6]}@example.com"
    invite = client.post("/api/team/invites", headers=owner["headers"], json={"email": email, "role": role})
    assert invite.status_code in (200, 201), invite.text
    token = parse_qs(urlparse(invite.json()["invite_url"]).query)["invite"][0]
    access = client.post(f"/api/auth/invite/{token}/accept", json={"password": "Member-Pass-2026"}).json()["access_token"]
    headers = {"Authorization": f"Bearer {access}"}
    me = client.get("/api/auth/me", headers=headers).json()
    return {"headers": headers, "tenant_id": me["tenant_id"], "user_id": me["id"]}


def _put(who: dict, body, namespace: str = "chart", **params):
    data = body if isinstance(body, (bytes, str)) else json.dumps(body)
    return client.put(f"{BASE}/{namespace}", headers={**who["headers"], "Content-Type": "application/json"}, content=data,
                      params=params)


def test_defaults_before_anything_is_saved():
    who = _who("defaults")
    got = client.get(BASE, headers=who["headers"])
    assert got.status_code == 200, got.text
    assert got.json()["prefs"]["chart"] == CHART_DEFAULTS and got.json()["versions"]["chart"] == 0
    assert set(got.json()["prefs"]) == set(got.json()["versions"]) == {"chart", "setup", "oc_analyses", "appearance"}      # the allow-list, exactly (OC3 added oc_analyses, item 1 appearance)
    assert prefs.defaults("chart") == CHART_DEFAULTS                    # no grid by default; volume and RSI on


def test_round_trip_fills_defaults_and_bumps_the_version():
    who = _who("round")
    saved = _put(who, {"theme": "dark", "candle_colors": {"up": "#26A69A", "down": "#ef5350"}})
    assert saved.status_code == 200, saved.text
    assert saved.json() == {"namespace": "chart", "version": 1,
                            "value": {**CHART_DEFAULTS, "theme": "dark", "candle_colors": {"up": "#26A69A", "down": "#ef5350"}}}
    again = _put(who, {"theme": "light", "grid": True, "indicators": {"volume": True, "rsi": False}, "reader_overlay": True})
    assert again.status_code == 200 and again.json()["version"] == 2
    got = client.get(BASE, headers=who["headers"]).json()
    assert got["versions"]["chart"] == 2
    # a PUT replaces the namespace: the colours left out are back to the theme's own
    assert got["prefs"]["chart"] == {"theme": "light", "grid": True, "candle_colors": None,
                                     "indicators": {"volume": True, "rsi": False}, "reader_overlay": True}


def test_optional_version_is_a_compare_and_set():
    who = _who("cas")
    assert _put(who, {"theme": "dark"}, version=0).json()["version"] == 1     # 0 = never saved
    stale = _put(who, {"theme": "light"}, version=0)
    assert stale.status_code == 409
    detail = stale.json()["detail"]
    assert detail["version"] == 1 and detail["current"]["theme"] == "dark"
    assert _put(who, {"theme": "light"}, version=1).json()["version"] == 2
    assert _put(who, {"grid": True}).status_code == 200                     # without a version the save replaces


@pytest.mark.parametrize("body", [
    {"theme": "blue"},
    {"theme": None},
    {"grid": "true"},                                   # strict: a string is not a boolean
    {"grid": 1},
    {"candle_colors": {"up": "green", "down": "#ef5350"}},
    {"candle_colors": {"up": "#26a69a"}},
    {"candle_colors": {"up": "#26a69a", "down": "#26A69A"}},
    {"indicators": {"volume": True, "macd": True}},
    {"unknown": 1},
    ["theme", "dark"],
    "not json",
    "",
])
def test_schema_validation(body):
    who = _who("bad")
    response = _put(who, body)
    assert response.status_code == 422, response.text
    assert client.get(BASE, headers=who["headers"]).json()["versions"]["chart"] == 0


def test_the_rejected_input_is_not_echoed():
    who = _who("echo")
    response = _put(who, {"theme": "secret-value-xyz"})
    assert response.status_code == 422 and "secret-value-xyz" not in response.text


def test_unknown_namespace_is_404():
    who = _who("ns")
    assert _put(who, {}, namespace="wizard").status_code == 404
    assert _put(who, {}, namespace="..").status_code == 404


def test_size_cap():
    who = _who("size")
    big = json.dumps({"theme": "dark"}) + " " * prefs.MAX_BODY_BYTES
    response = _put(who, big)
    assert response.status_code == 413 and str(prefs.MAX_BODY_BYTES) in response.json()["detail"]
    assert client.get(BASE, headers=who["headers"]).json()["versions"]["chart"] == 0


def test_anonymous_is_refused():
    assert client.get(BASE).status_code == 401
    assert client.put(f"{BASE}/chart", json={}).status_code == 401


def test_per_user_and_per_organisation():
    owner, other_org = _who("iso-a"), _who("iso-b")
    member = _invite(owner, "USER")
    assert _put(owner, {"theme": "dark", "grid": True}).status_code == 200
    for who in (member, other_org):
        got = client.get(BASE, headers=who["headers"]).json()
        assert got["prefs"]["chart"] == CHART_DEFAULTS and got["versions"]["chart"] == 0, who
    # their own saves never touch the owner's
    assert _put(member, {"theme": "light"}).json()["version"] == 1
    assert _put(other_org, {"theme": "light"}).json()["version"] == 1
    mine = client.get(BASE, headers=owner["headers"]).json()
    assert mine["prefs"]["chart"]["theme"] == "dark" and mine["versions"]["chart"] == 1


def test_a_row_of_the_same_user_id_under_another_tenant_is_never_read():
    a, b = _who("cross-a"), _who("cross-b")

    async def plant():
        async with _session_factory() as session:
            session.add(UserPrefsRecord(tenant_id=b["tenant_id"], user_id=a["user_id"], namespace="chart",
                                        value_json=json.dumps({"theme": "dark"}), version=7, updated_at=datetime.now(timezone.utc)))
            await session.commit()
    _run(plant())
    got = client.get(BASE, headers=a["headers"]).json()
    assert got["versions"]["chart"] == 0 and got["prefs"]["chart"]["theme"] == "app"
    assert client.get(BASE, headers=b["headers"]).json()["versions"]["chart"] == 0


def test_a_viewer_may_change_their_own():
    viewer = _invite(_who("viewer-owner"), "VIEWER")
    saved = _put(viewer, {"theme": "dark"})
    assert saved.status_code == 200, saved.text
    assert client.get(BASE, headers=viewer["headers"]).json()["prefs"]["chart"]["theme"] == "dark"


def test_stored_values_read_leniently():
    assert prefs.stored_value("chart", None) == CHART_DEFAULTS
    assert prefs.stored_value("chart", "not json") == CHART_DEFAULTS
    assert prefs.stored_value("chart", '["x"]') == CHART_DEFAULTS
    # a key a newer release saved is dropped; the rest is kept
    assert prefs.stored_value("chart", json.dumps({"theme": "dark", "newer": 1}))["theme"] == "dark"
    # a value that no longer validates reads as the defaults instead of failing the page
    assert prefs.stored_value("chart", json.dumps({"theme": "sepia"})) == CHART_DEFAULTS


# --- the migration ----------------------------------------------------------------------------------------------------

def test_migration_is_rollback_safe_additive_and_keeps_one_head():
    import importlib.util
    from pathlib import Path

    from alembic.config import Config
    from alembic.script import ScriptDirectory

    from scripts.migration_rollback_safety import lint, risky_operations

    root = Path(__file__).resolve().parents[1]
    path = root / "alembic" / "versions" / "d9f1b3c5e7a2_ui2_4a1_prefs_watchlists_drawings.py"
    spec = importlib.util.spec_from_file_location("ui2_4a1_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.ROLLBACK_SAFE is True and module.revision == "d9f1b3c5e7a2"
    assert module.down_revision == "c4e8a2d6f0b1"                     # re-chained at merge if another lands first
    assert risky_operations(path.read_text(encoding="utf-8")) == []   # additive only: the lint needs no exception
    assert not hasattr(module, "ROLLBACK_SAFE_REASON")                 # so a risky step added later is caught by the lint
    assert not [p for p in lint() if p.startswith(path.name)]
    script = ScriptDirectory.from_config(Config(str(root / "alembic.ini")))
    assert len(script.get_heads()) == 1
    assert script.get_revision("d9f1b3c5e7a2") is not None


def _hide_the_row_once(monkeypatch):
    """The first-save race: this request's read misses the row another device inserted a moment before."""
    from sqlalchemy.ext.asyncio import AsyncSession
    real = AsyncSession.scalar
    state = {"hidden": False}

    async def scalar(self, statement, *args, **kwargs):
        if not state["hidden"] and "user_prefs" in str(statement):
            state["hidden"] = True
            return None
        return await real(self, statement, *args, **kwargs)
    monkeypatch.setattr(AsyncSession, "scalar", scalar)
    return state


def test_first_save_race_without_a_version_retries_once(monkeypatch):
    who = _who("race")
    assert _put(who, {"theme": "dark"}).json()["version"] == 1                # the other device's first save
    state = _hide_the_row_once(monkeypatch)
    saved = _put(who, {"theme": "light"})
    assert state["hidden"] and saved.status_code == 200, saved.text
    assert saved.json()["version"] == 2                                     # retried as an update, nothing lost or refused
    assert client.get(BASE, headers=who["headers"]).json()["prefs"]["chart"]["theme"] == "light"


def test_first_save_race_with_a_version_is_a_409(monkeypatch):
    who = _who("race-v")
    assert _put(who, {"theme": "dark"}).json()["version"] == 1
    _hide_the_row_once(monkeypatch)
    assert _put(who, {"theme": "light"}, version=0).status_code == 409
    assert client.get(BASE, headers=who["headers"]).json()["prefs"]["chart"]["theme"] == "dark"
