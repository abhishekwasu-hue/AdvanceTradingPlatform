"""M68 PR-0: Settings > Trade settings - one registry, every field "Not set" until the user saves it, partial saves only,
validation against the registry, facts never stored, unwired fields only for a platform admin, isolation per user and
per organisation. No behaviour change: nothing outside app/me reads the namespace."""
import asyncio
import json
import subprocess
import uuid
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.db.models import User, UserPrefsRecord
from app.me import prefs
from app.me import trade_settings as ts
from tests.test_auth_api import _session_factory, client
from tests.test_phase_l_ai import _owner
from tests.test_ui2_4a1_prefs import _invite

URL = "/api/me/trade-settings"
pytestmark = pytest.mark.real_order_defaults
BACKEND = Path(__file__).resolve().parents[1]
# The TS-1 audit's 60 rows whose new home is "user setting: <section.field>" (docs/design/M68_TS1_AUDIT.md).
AUDIT_USER_ROWS = {f"TS1-{n:03d}" for n in (*range(1, 9), *range(10, 16), 17, *range(19, 25), 26, 27, 28, 30, 31, 32,
                                            *range(35, 40), *range(41, 46), *range(48, 55), *range(56, 68), 69, 70, 71, 100)}


def _who(tag: str) -> dict:
    headers, me = _owner(f"m68-{tag}-{uuid.uuid4().hex[:10]}@example.com")
    return {"headers": headers, "tenant_id": me["tenant_id"], "user_id": me["id"]}


def _admin(tag: str) -> dict:
    who = _who(tag)

    async def promote():
        async with _session_factory() as session:
            user = await session.get(User, who["user_id"])
            user.role = "SUPER_ADMIN"
            await session.commit()
    asyncio.run(promote())
    return who


def _fields(body: dict) -> dict:
    return {f["key"]: f for s in body["sections"] for f in s["fields"]}


@pytest.fixture
def wired(monkeypatch):
    """Marks a few fields wired, as their PR will, so a USER can see and save them."""
    keys = ("order_defaults.product", "position_size.lots", "exits.square_off_time", "options.expiry_day_roll",
            "paper_account.capital", "options.custom_legs")
    new = tuple(replace(f, wired=True) if f.key in keys else f for f in ts.FIELDS)
    monkeypatch.setattr(ts, "FIELDS", new)
    monkeypatch.setattr(ts, "BY_KEY", {f.key: f for f in new})
    return keys


# --- the registry ----------------------------------------------------------------------------------------------------------

def test_registry_seven_sections_and_every_audit_user_row():
    assert [s.id for s in ts.SECTIONS] == ["order_defaults", "position_size", "exits", "options", "risk_limits", "automation",
                                           "paper_account"]
    assert [s.title for s in ts.SECTIONS] == ["Order defaults", "Position size", "Exits", "Options", "Risk limits", "Automation",
                                              "Paper account"]
    covered = {r for f in ts.FIELDS if not f.fact for r in f.rows} | set(ts.ON_RISK_LIMITS_PAGE)
    assert AUDIT_USER_ROWS <= covered, sorted(AUDIT_USER_ROWS - covered)
    assert len(AUDIT_USER_ROWS) == 60
    for s in ts.SECTIONS:
        assert any(f.section == s.id and not f.fact for f in ts.FIELDS), s.id


# M68 PR-6: Automation fields nothing reads yet - exit requests are off since M55 S2, the trading window is TS1-026 / 028
# (PR-3) and re-entry has no TS-1 row (no pre-chosen value in the code).
PR6_NOT_WIRED = {"automation.approval_exit_minutes", "automation.window_start", "automation.window_end", "automation.reentry",
                 "automation.reentry_max"}


def test_registry_fields_are_well_formed_and_wired_by_their_pr():
    for f in ts.FIELDS:
        assert f.label and f.help and f.label[0].isupper(), f.key
        assert f.wired is (f.pr in (1, 5, 6) and not f.fact and f.key not in PR6_NOT_WIRED), f.key   # PR-1 / PR-5 / PR-6 wired; later PRs the rest
        assert 1 <= f.pr <= 7, f.key
        if f.kind == "choice":
            assert len(f.choices) >= 2 and len({c for c, _ in f.choices}) == len(f.choices), f.key
        else:
            assert not f.choices, f.key
        if f.kind in ("number", "whole"):
            assert f.low is not None and f.high is not None and f.low < f.high, f.key
        if f.fact:
            assert f.scope == "user" and not ts.storable(f), f.key
    facts = [f for f in ts.FIELDS if f.fact]
    assert len(facts) >= 10 and {f.section for f in facts} == set(ts.SECTION_IDS)


def test_glossary_keys_exist_in_the_frontend_glossary():
    text = (BACKEND.parent / "frontend" / "src" / "ui" / "glossary.ts").read_text(encoding="utf-8")
    for f in ts.FIELDS:
        if f.term:
            assert f'"{f.term}":' in text or f"  {f.term}:" in text, f.key


def test_the_namespace_is_read_by_one_order_path_reader():
    """M68 PR-1: order paths read the Trade settings only through app/execution/order_defaults.py (one reader); M68 PR-5:
    the Risk limits section through app/risk_engine/own_risk.py (one reader)."""
    out = subprocess.run(["grep", "-rlE", "--include=*.py", r"trade_settings|TradeSettings", "app"], cwd=BACKEND, capture_output=True, text=True).stdout
    assert sorted(out.split()) == ["app/execution/automation.py", "app/execution/order_defaults.py", "app/main.py", "app/me/prefs.py",
                                   "app/me/trade_settings.py", "app/risk_engine/own_risk.py"]   # PR-5: + Risk limits; PR-6: + Automation


def test_value_checks():
    by = ts.BY_KEY
    assert ts.problem(by["order_defaults.product"], "MIS") is None
    assert ts.problem(by["order_defaults.product"], "CNC") and ts.problem(by["order_defaults.product"], 1)
    assert ts.problem(by["position_size.lots"], 3) is None
    for bad in (0, 2.5, True, "3", -1):
        assert ts.problem(by["position_size.lots"], bad), bad
    assert ts.problem(by["order_defaults.price_protection_pct"], 0.5) is None
    for bad in (0, 101, float("nan"), float("inf"), False):
        assert ts.problem(by["order_defaults.price_protection_pct"], bad), bad
    assert ts.problem(by["exits.square_off_time"], "15:10") is None
    for bad in ("3pm", "24:00", "9:30", 1510):
        assert ts.problem(by["exits.square_off_time"], bad), bad
    assert ts.problem(by["options.expiry_day_roll"], False) is None and ts.problem(by["options.expiry_day_roll"], 0)
    assert ts.problem(by["paper_account.brokerage_per_order"], 0) is None            # zero brokerage is his choice
    leg = {"right": "PE", "side": "SELL", "strike_rule": "ATM", "offset": 0, "ratio": 1}
    assert ts.problem(by["options.custom_legs"], [leg]) is None
    assert ts.problem(by["options.custom_legs"], [leg] * 7) and ts.problem(by["options.custom_legs"], [{**leg, "ratio": 0}])
    assert ts.problem(by["position_size.lot_size"], 50)                             # a fact is never stored
    assert ts.problem(by["position_size.lots"], None) is None                        # Not set is always valid


def test_clean_drops_only_what_no_longer_reads():
    assert ts.clean({"position_size.lots": 2, "position_size.lots_old": 4, "order_defaults.product": "XYZ",
                     "position_size.lot_size": 75, "paper_account.capital": 500000}) == \
        {"position_size.lots": 2, "paper_account.capital": 500000.0}
    assert ts.clean(None) == {} and ts.clean([1]) == {}


# --- the routes ------------------------------------------------------------------------------------------------------------

def test_a_new_user_sees_not_set_and_no_dead_control():
    who = _who("new")
    got = client.get(URL, headers=who["headers"])
    assert got.status_code == 200, got.text
    body = got.json()
    assert body["admin"] is False and body["page"] == "/settings/trade-settings"
    assert [s["id"] for s in body["sections"]] == list(ts.SECTION_IDS)
    fields = _fields(body)
    # PR-1 / PR-5 / PR-6: the Order defaults, Risk limits and Automation controls are wired; every other control is not yet
    assert fields and all(f["fact"] or f["key"].startswith(("order_defaults.", "risk_limits.", "automation.")) for f in fields.values())
    # M68 PR-6: a field of a feature that is off for him (the scanner's Auto PAPER, flag scan_auto_paper) is not shown
    assert {k for k, f in fields.items() if not f["fact"]} == {f.key for f in ts.FIELDS if f.wired and f.flag is None}
    assert all(f["value"] is None and f["saved"] is False for f in fields.values())
    risk = next(s for s in body["sections"] if s["id"] == "risk_limits")
    assert risk["today"] == {"label": "Risk limits", "path": "/settings/risk-limits"}


def test_an_admin_sees_every_field_not_set():
    body = client.get(URL, headers=_admin("adm")["headers"]).json()
    fields = _fields(body)
    assert body["admin"] is True and set(fields) == set(ts.BY_KEY)
    assert all(f["value"] is None and f["saved"] is False for f in fields.values())


def test_partial_save_keeps_every_other_field(wired):
    who = _who("partial")
    one = client.put(URL, headers=who["headers"], json={"values": {"order_defaults.product": "NRML"}})
    assert one.status_code == 200, one.text
    two = client.put(URL, headers=who["headers"], json={"values": {"position_size.lots": 2, "exits.square_off_time": "15:05"}})
    assert two.status_code == 200, two.text
    fields = _fields(client.get(URL, headers=who["headers"]).json())
    assert fields["order_defaults.product"]["value"] == "NRML" and fields["order_defaults.product"]["saved"] is True
    assert fields["position_size.lots"]["value"] == 2 and fields["exits.square_off_time"]["value"] == "15:05"
    assert fields["options.expiry_day_roll"] == {**fields["options.expiry_day_roll"], "value": None, "saved": False}
    assert fields["paper_account.capital"]["value"] is None                         # never filled
    # off is a saved choice, not "Not set"
    client.put(URL, headers=who["headers"], json={"values": {"options.expiry_day_roll": False}})
    fields = _fields(client.get(URL, headers=who["headers"]).json())
    assert fields["options.expiry_day_roll"]["value"] is False and fields["options.expiry_day_roll"]["saved"] is True
    # null clears one field back to Not set; the others stay
    client.put(URL, headers=who["headers"], json={"values": {"position_size.lots": None}})
    fields = _fields(client.get(URL, headers=who["headers"]).json())
    assert fields["position_size.lots"]["saved"] is False and fields["order_defaults.product"]["value"] == "NRML"
    stored = client.get("/api/me/prefs", headers=who["headers"]).json()["prefs"]["trade_settings"]["values"]
    assert stored == {"order_defaults.product": "NRML", "exits.square_off_time": "15:05", "options.expiry_day_roll": False}


@pytest.mark.parametrize("values", [
    {"order_defaults.product": "CNC"}, {"position_size.lots": 0}, {"position_size.lots": 1.5}, {"position_size.lots": "2"},
    {"exits.square_off_time": "3pm"}, {"paper_account.capital": -1}, {"no_such.field": 1}, {"position_size.lot_size": 75},
    {"risk_limits.ceiling_risk_per_trade_pct": 2}, {"order_defaults.product": "NRML", "position_size.lots": 0},
])
def test_validation_refuses_and_saves_nothing(wired, values):
    who = _who("bad")
    got = client.put(URL, headers=who["headers"], json={"values": values})
    assert got.status_code == 422, got.text
    assert "CNC" not in got.text and "3pm" not in got.text                           # the input is never echoed back
    assert client.get("/api/me/prefs", headers=who["headers"]).json()["versions"]["trade_settings"] == 0


def test_the_body_shape_is_strict():
    who = _who("shape")
    for body in ({}, {"values": [1]}, {"values": {}, "extra": 1}):
        assert client.put(URL, headers=who["headers"], json=body).status_code == 422, body


def test_a_user_cannot_save_an_unwired_field_an_admin_can():
    user, admin = _who("user"), _admin("admin")
    refused = client.put(URL, headers=user["headers"], json={"values": {"position_size.lots": 2}})
    assert refused.status_code == 422 and "not available yet" in refused.text
    saved = client.put(URL, headers=admin["headers"], json={"values": {"position_size.lots": 2}})
    assert saved.status_code == 200, saved.text
    assert _fields(saved.json())["position_size.lots"]["value"] == 2


def test_the_generic_prefs_route_does_not_write_trade_settings():
    who = _who("generic")
    got = client.put("/api/me/prefs/trade_settings", headers=who["headers"], json={"values": {}})
    assert got.status_code == 405
    assert prefs.defaults("trade_settings") == {"values": {}}


def test_isolation_per_user_and_per_organisation(wired):
    owner, other_org = _who("iso-a"), _who("iso-b")
    member = _invite(owner, "USER")
    assert client.put(URL, headers=owner["headers"], json={"values": {"position_size.lots": 4}}).status_code == 200
    for who in (member, other_org):
        assert _fields(client.get(URL, headers=who["headers"]).json())["position_size.lots"]["value"] is None
    assert client.put(URL, headers=member["headers"], json={"values": {"position_size.lots": 1}}).status_code == 200
    assert _fields(client.get(URL, headers=owner["headers"]).json())["position_size.lots"]["value"] == 4


def test_a_row_of_the_same_user_id_under_another_tenant_is_never_read(wired):
    a, b = _who("cross-a"), _who("cross-b")

    async def plant():
        async with _session_factory() as session:
            session.add(UserPrefsRecord(tenant_id=b["tenant_id"], user_id=a["user_id"], namespace="trade_settings",
                                        value_json=json.dumps({"values": {"position_size.lots": 9}}), version=3,
                                        updated_at=datetime.now(UTC)))
            await session.commit()
    asyncio.run(plant())
    assert _fields(client.get(URL, headers=a["headers"]).json())["position_size.lots"]["value"] is None


def test_anonymous_is_refused():
    assert client.get(URL).status_code == 401
    assert client.put(URL, json={"values": {}}).status_code == 401


def test_the_e2e_fixtures_are_the_registry_view():
    """frontend/e2e/fixtures/trade-settings-{user,admin}.json are this registry's own replies (regenerate on a change)."""
    for name, admin in (("user", False), ("admin", True)):
        path = BACKEND.parent / "frontend" / "e2e" / "fixtures" / f"trade-settings-{name}.json"
        assert json.loads(path.read_text(encoding="utf-8")) == ts.view({}, admin), name
