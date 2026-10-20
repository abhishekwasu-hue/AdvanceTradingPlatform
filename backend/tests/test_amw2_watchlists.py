"""AMW-2 (docs/design/AMW_PORT.md §4.1): personal named watchlists behind the `watchlists` flag (off by default).

Behaviour specifications ported from the AMW reference tests (re-written on ATP's API and fixtures, no AMW code):
- `test_platform.py::test_tenant_isolation_and_roles`: a list saved by one user is not visible to another
  (`watchlists(b) == []`); here: neither another organisation nor another member of the same organisation;
- AMW's save semantics: a list is named, unique by name per user, and its symbols are upper-case.
ATP-specific: flag off -> 404 with `X-Feature-Disabled`; another organisation or member gets 404 and changes nothing;
the plan's `max_watchlists` (AMW-1) applies only while `plan_limits_v2` exists and is on.
"""
import asyncio
import uuid
from types import SimpleNamespace

import pytest

from app.db.models import Tenant, WatchlistRecord
from app.platform import controls
from app.watchlists import service
from tests.test_auth_api import _register, _session_factory, client
from tests.test_z3b_paper_account import _member

BASE = "/api/watchlists"


def _run(coro):
    return asyncio.run(coro)


def _email(tag: str) -> str:
    return f"amw2-{tag}-{uuid.uuid4().hex[:6]}@example.com"


def _set_flag(name: str, tenant_id: int, on: bool = True) -> None:
    async def go():
        async with _session_factory() as session:
            current = await controls._get(session, controls.KEY_FEATURE_FLAGS)
            flags = dict(current.get("flags") or {})
            entry = flags.get(name) or {"on": False, "tenants": []}
            tenants = set(entry.get("tenants") or [])
            (tenants.add if on else tenants.discard)(tenant_id)
            flags[name] = {"on": False, "tenants": sorted(tenants)}
            await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": flags}, None)
            await session.commit()
    _run(go())


def _set_plan(tenant_id: int, plan: str) -> None:
    async def go():
        async with _session_factory() as session:
            tenant = await session.get(Tenant, tenant_id)
            tenant.plan = plan
            await session.commit()
    _run(go())


def _owner(tag: str, *, flag: bool = True) -> dict:
    headers = {"Authorization": f"Bearer {_register(_email(tag))}"}
    me = client.get("/api/auth/me", headers=headers).json()
    if flag:
        _set_flag(service.FLAG, me["tenant_id"])
    return {"headers": headers, "tenant_id": me["tenant_id"], "user_id": me["id"]}


def _create(who: dict, name: str = "Banks", symbols=("HDFCBANK", "ICICIBANK")):
    return client.post(BASE, headers=who["headers"], json={"name": name, "symbols": list(symbols)})


def _row(watchlist_id: int):
    async def go():
        async with _session_factory() as session:
            row = await session.get(WatchlistRecord, watchlist_id)
            return None if row is None else (row.tenant_id, row.user_id, row.name, row.symbols_json, row.updated_at)
    return _run(go())


# --- the flag ---------------------------------------------------------------------------------------------------------

def test_flag_is_registered_and_off_by_default():
    assert service.FLAG in controls.FEATURE_FLAGS and service.FLAG in controls.DEFAULT_OFF_FLAGS

    async def go():
        async with _session_factory() as session:
            return (await controls.feature_flags(session))[service.FLAG]["on"]
    assert _run(go()) is False


def test_flag_off_answers_404_on_every_route():
    who = _owner("off", flag=False)
    for method, path, body in (("GET", BASE, None), ("POST", BASE, {"name": "x", "symbols": ["A"]}),
                               ("PUT", f"{BASE}/1", {"name": "x", "symbols": ["A"]}), ("DELETE", f"{BASE}/1", None)):
        response = client.request(method, path, headers=who["headers"], json=body)
        assert response.status_code == 404, (method, path, response.text)
        assert response.headers.get("X-Feature-Disabled") == service.FLAG


def test_anonymous_is_refused():
    assert client.get(BASE).status_code in (401, 403)


# --- behaviour --------------------------------------------------------------------------------------------------------

def test_create_list_update_delete():
    who = _owner("crud")
    created = _create(who, "  Banks   large ", [" hdfcbank, icicibank ", "SBIN", "hdfcbank", "m&m", "bajaj-auto"])
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["name"] == "Banks large"
    assert body["symbols"] == ["HDFCBANK", "ICICIBANK", "SBIN", "M&M", "BAJAJ-AUTO"] and body["count"] == 5
    assert _create(who, "Autos", ["TATAMOTORS"]).status_code == 201

    listed = client.get(BASE, headers=who["headers"]).json()
    assert [w["name"] for w in listed] == ["Autos", "Banks large"]           # by name

    wid = body["id"]
    updated = client.put(f"{BASE}/{wid}", headers=who["headers"], json={"name": "Banks", "symbols": ["axisbank"]})
    assert updated.status_code == 200 and updated.json()["name"] == "Banks" and updated.json()["symbols"] == ["AXISBANK"]

    assert client.delete(f"{BASE}/{wid}", headers=who["headers"]).json() == {"deleted": wid}
    assert [w["name"] for w in client.get(BASE, headers=who["headers"]).json()] == ["Autos"]
    assert client.delete(f"{BASE}/{wid}", headers=who["headers"]).status_code == 404


def test_an_empty_list_may_be_saved_and_filled_later():
    who = _owner("empty")
    created = _create(who, "Later", [])
    assert created.status_code == 201 and created.json()["symbols"] == []


def test_names_are_unique_per_user():
    who = _owner("dup")
    first = _create(who, "Banks").json()
    assert _create(who, "Banks").status_code == 409
    other = _create(who, "Autos").json()
    rename = client.put(f"{BASE}/{other['id']}", headers=who["headers"], json={"name": "Banks", "symbols": []})
    assert rename.status_code == 409 and "already have a watchlist named" in rename.json()["detail"]
    assert _row(first["id"])[2] == "Banks" and _row(other["id"])[2] == "Autos"
    # names compare ignoring case, on create and on rename
    assert _create(who, "banks").status_code == 409 and _create(who, " BANKS ").status_code == 409
    assert client.put(f"{BASE}/{other['id']}", headers=who["headers"], json={"name": "bAnKs", "symbols": []}).status_code == 409
    # a list may change the case of its own name
    recased = client.put(f"{BASE}/{first['id']}", headers=who["headers"], json={"name": "BANKS", "symbols": ["SBIN"]})
    assert recased.status_code == 200 and recased.json()["name"] == "BANKS"
    # another user may use the same name
    assert _create(_owner("dup2"), "Banks").status_code == 201


@pytest.mark.parametrize("body", [
    {"name": "", "symbols": []},
    {"name": "a<b", "symbols": []},
    {"name": "x" * 61, "symbols": []},
    {"name": "Bad", "symbols": ["RELIANCE", "NOT/OK"]},
    {"name": "Bad", "symbols": ["X" * 41]},
    {"name": "Bad", "symbols": [], "shared": True},
])
def test_invalid_bodies_are_refused(body):
    who = _owner("bad")
    assert client.post(BASE, headers=who["headers"], json=body).status_code == 422
    assert client.get(BASE, headers=who["headers"]).json() == []


def test_too_many_symbols_are_refused():
    who = _owner("many")
    symbols = [f"S{i}" for i in range(service.MAX_SYMBOLS + 1)]
    response = client.post(BASE, headers=who["headers"], json={"name": "Big", "symbols": [" ".join(symbols)]})
    assert response.status_code == 422 and f"at most {service.MAX_SYMBOLS} symbols" in response.text
    assert _create(who, "Max", symbols[:service.MAX_SYMBOLS]).status_code == 201


def test_hard_cap_per_user(monkeypatch):
    monkeypatch.setattr(service, "MAX_PER_USER", 2)
    who = _owner("cap")
    assert _create(who, "A").status_code == 201 and _create(who, "B").status_code == 201
    third = _create(who, "C")
    assert third.status_code == 409 and "at most 2 watchlists" in third.json()["detail"]


def test_a_stored_list_that_is_not_a_json_list_reads_as_empty():
    row = WatchlistRecord(id=1, tenant_id=1, user_id=1, name="x", symbols_json='{"not": "a list"}')
    assert service.symbols_of(row) == []
    row.symbols_json = "not json"
    assert service.symbols_of(row) == []


# --- tenancy ----------------------------------------------------------------------------------------------------------

def test_another_organisation_cannot_see_or_change_a_list():
    a, b = _owner("ta"), _owner("tb")
    wid = _create(a, "Mine", ["INFY"]).json()["id"]
    before = _row(wid)
    assert client.get(BASE, headers=b["headers"]).json() == []
    assert client.put(f"{BASE}/{wid}", headers=b["headers"], json={"name": "Taken", "symbols": ["TCS"]}).status_code == 404
    assert client.delete(f"{BASE}/{wid}", headers=b["headers"]).status_code == 404
    assert _row(wid) == before
    # b's own list does not leak into a's
    _create(b, "Theirs", ["TCS"])
    assert [w["name"] for w in client.get(BASE, headers=a["headers"]).json()] == ["Mine"]


def test_another_member_of_the_same_organisation_cannot_see_or_change_a_list():
    owner = _owner("own")
    _set_plan(owner["tenant_id"], "pro")                  # Free allows one member
    member = _member(owner)
    wid = _create(owner, "Owner list", ["INFY"]).json()["id"]
    before = _row(wid)
    assert client.get(BASE, headers=member["headers"]).json() == []
    assert client.put(f"{BASE}/{wid}", headers=member["headers"], json={"name": "x", "symbols": []}).status_code == 404
    assert client.delete(f"{BASE}/{wid}", headers=member["headers"]).status_code == 404
    assert _row(wid) == before
    mine = _create(member, "Owner list", ["TCS"])           # the same name is the member's own list
    assert mine.status_code == 201 and _row(mine.json()["id"])[1] == member["user_id"]
    assert [w["symbols"] for w in client.get(BASE, headers=owner["headers"]).json()] == [["INFY"]]


def test_the_flag_is_per_organisation():
    a, b = _owner("fa"), _owner("fb", flag=False)
    assert client.get(BASE, headers=a["headers"]).status_code == 200
    assert client.get(BASE, headers=b["headers"]).status_code == 404


# --- the plan limit hook (AMW-1) ---------------------------------------------------------------------------------------

def _clear_flag(name: str) -> None:
    async def go():
        async with _session_factory() as session:
            current = await controls._get(session, controls.KEY_FEATURE_FLAGS)
            flags = dict(current.get("flags") or {})
            flags[name] = {"on": False, "tenants": []}
            await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": flags}, None)
            await session.commit()
    _run(go())


@pytest.fixture
def plan_flag_cleared():
    """Each plan-limit case starts and ends with `plan_limits_v2` off for every tenant (ids may be reused)."""
    _clear_flag(service.PLAN_FLAG)
    yield
    _clear_flag(service.PLAN_FLAG)


def _plan_limits_flag(monkeypatch, tenant_id: int) -> None:
    monkeypatch.setitem(controls.FEATURE_FLAGS, service.PLAN_FLAG, "plan limits v2 (test)")
    _set_flag(service.PLAN_FLAG, tenant_id)


def test_no_plan_limit_without_amw1(monkeypatch, plan_flag_cleared):
    who = _owner("noamw1")
    monkeypatch.setattr(service, "get_plan", lambda _plan: SimpleNamespace(id="free", name="Free", max_watchlists=1))
    if service.PLAN_FLAG in controls.FEATURE_FLAGS:          # AMW-1 merged: its flag stays off here
        monkeypatch.delitem(controls.FEATURE_FLAGS, service.PLAN_FLAG)
    assert _create(who, "A").status_code == 201 and _create(who, "B").status_code == 201


def test_plan_limit_applies_when_plan_limits_v2_is_on_and_the_plan_has_the_field(monkeypatch, plan_flag_cleared):
    who = _owner("limit")
    monkeypatch.setattr(service, "get_plan", lambda _plan: SimpleNamespace(id="free", name="Free", max_watchlists=1))
    _plan_limits_flag(monkeypatch, who["tenant_id"])
    assert _create(who, "A").status_code == 201
    refused = _create(who, "B")
    assert refused.status_code == 402 and "allows 1 watchlists; you have 1" in refused.json()["detail"]
    assert "Upgrade the plan" in refused.json()["detail"]
    assert [w["name"] for w in client.get(BASE, headers=who["headers"]).json()] == ["A"]


def test_plan_limit_is_skipped_when_the_flag_is_off_and_fails_closed_without_the_field(monkeypatch, plan_flag_cleared):
    who = _owner("limit-off")
    monkeypatch.setitem(controls.FEATURE_FLAGS, service.PLAN_FLAG, "plan limits v2 (test)")
    monkeypatch.setattr(service, "get_plan", lambda _plan: SimpleNamespace(id="free", name="Free", max_watchlists=1))
    assert _create(who, "A").status_code == 201 and _create(who, "B").status_code == 201      # flag off for the tenant
    _set_flag(service.PLAN_FLAG, who["tenant_id"])
    # A plan without the field gets the Free value (fail closed): with 2 lists, 3 more fit, the 6th is refused.
    monkeypatch.setattr(service, "get_plan", lambda _plan: SimpleNamespace(id="free", name="Free"))
    for name in ("C", "D", "E"):
        assert _create(who, name).status_code == 201, name
    refused = _create(who, "F")
    assert refused.status_code == 402 and f"allows {service.FREE_MAX_WATCHLISTS} watchlists; you have 5" in refused.json()["detail"]


def test_usage_counter_counts_the_callers_own_lists():
    a, b = _owner("ua"), _owner("ub")
    _create(a, "One")
    _create(a, "Two")
    _create(b, "Other")

    async def go():
        async with _session_factory() as session:
            return await service._usage_counter(session, a["tenant_id"], a["user_id"])
    assert _run(go()) == 2.0


# --- UI2-4a1: items with an exchange (spaces allowed), list order, version ------------------------------------------

def _items(who: dict, name: str, items):
    return client.post(BASE, headers=who["headers"], json={"name": name, "items": items})


def test_items_keep_spaces_and_exchange_and_feed_the_plain_symbols():
    who = _owner("items")
    made = _items(who, "Indices", [{"symbol": "nifty  50", "exchange": "nse_index"}, {"symbol": "NIFTY BANK", "exchange": "NSE_INDEX"},
                                   {"symbol": "reliance"}, {"symbol": "RELIANCE", "exchange": "BSE"},
                                   {"symbol": "Nifty 50", "exchange": "NSE_INDEX"}])
    assert made.status_code == 201, made.text
    body = made.json()
    assert body["items"] == [{"symbol": "NIFTY 50", "exchange": "NSE_INDEX"}, {"symbol": "NIFTY BANK", "exchange": "NSE_INDEX"},
                             {"symbol": "RELIANCE", "exchange": "NSE"}, {"symbol": "RELIANCE", "exchange": "BSE"}]
    # the Screener / Scanner view: each symbol once, in row order
    assert body["symbols"] == ["NIFTY 50", "NIFTY BANK", "RELIANCE"] and body["count"] == 3
    assert body["version"] == 1 and body["position"] is None
    listed = client.get(BASE, headers=who["headers"]).json()
    assert listed[0]["items"] == body["items"]
    # row order is kept on a replace (drag to reorder)
    wid = body["id"]
    moved = client.put(f"{BASE}/{wid}", headers=who["headers"],
                       json={"name": "Indices", "items": [body["items"][2], body["items"][0]], "version": 1})
    assert moved.status_code == 200, moved.text
    assert [i["symbol"] for i in moved.json()["items"]] == ["RELIANCE", "NIFTY 50"] and moved.json()["version"] == 2


@pytest.mark.parametrize("items", [
    [{"symbol": "NIFTY/50"}],
    [{"symbol": ""}],
    [{"symbol": "   "}],
    [{"symbol": "X" * 41}],
    [{"symbol": "INFY", "exchange": "LSE"}],
    [{"symbol": "INFY", "segment": "EQ"}],
])
def test_invalid_items_are_refused(items):
    who = _owner("bad-items")
    assert _items(who, "Bad", items).status_code == 422
    assert client.get(BASE, headers=who["headers"]).json() == []


def test_symbols_and_items_together_are_refused():
    who = _owner("both")
    response = client.post(BASE, headers=who["headers"], json={"name": "X", "symbols": ["INFY"], "items": [{"symbol": "INFY"}]})
    assert response.status_code == 422


def test_too_many_items_are_refused():
    who = _owner("many-items")
    items = [{"symbol": f"S{i}"} for i in range(service.MAX_SYMBOLS + 1)]
    assert _items(who, "Big", items).status_code == 422
    assert _items(who, "Max", items[:service.MAX_SYMBOLS]).status_code == 201


def test_old_clients_keep_working():
    """An AMW-2 client sends and reads `symbols` only: same parsing, same response fields, items built on NSE; and its
    save of a list made with items keeps each symbol's exchange and a spaced symbol whole."""
    who = _owner("old")
    old = _create(who, "Old", ["infy, tcs"]).json()
    assert old["symbols"] == ["INFY", "TCS"]
    assert old["items"] == [{"symbol": "INFY", "exchange": "NSE"}, {"symbol": "TCS", "exchange": "NSE"}]
    assert {"id", "name", "symbols", "count", "created_at", "updated_at"} <= set(old)
    new = _items(who, "New", [{"symbol": "NIFTY 50", "exchange": "NSE_INDEX"}, {"symbol": "SBIN", "exchange": "BSE"}]).json()
    # the old client re-saves what it read, plus one symbol, without a version
    saved = client.put(f"{BASE}/{new['id']}", headers=who["headers"], json={"name": "New", "symbols": new["symbols"] + ["wipro"]})
    assert saved.status_code == 200, saved.text
    assert saved.json()["items"] == [{"symbol": "NIFTY 50", "exchange": "NSE_INDEX"}, {"symbol": "SBIN", "exchange": "BSE"},
                                     {"symbol": "WIPRO", "exchange": "NSE"}]
    assert saved.json()["symbols"] == ["NIFTY 50", "SBIN", "WIPRO"]
    # a pasted "NIFTY 50" on a list that never held it is still split, as AMW-2 does
    assert client.put(f"{BASE}/{old['id']}", headers=who["headers"], json={"name": "Old", "symbols": ["NIFTY 50"]}).json()["symbols"] == ["NIFTY", "50"]


def test_an_old_client_save_keeps_every_exchange_of_a_symbol_and_count_is_distinct_symbols():
    who = _owner("two-exchanges")
    made = _items(who, "Dual", [{"symbol": "RELIANCE", "exchange": "NSE"}, {"symbol": "INFY"},
                                {"symbol": "RELIANCE", "exchange": "BSE"}]).json()
    # `count` is AMW-2's: distinct symbols (= len(symbols)); `items` holds one row per symbol and exchange
    assert made["count"] == len(made["symbols"]) == 2 and len(made["items"]) == 3
    saved = client.put(f"{BASE}/{made['id']}", headers=who["headers"], json={"name": "Dual", "symbols": ["reliance", "tcs"]})
    assert saved.status_code == 200, saved.text
    assert saved.json()["items"] == [{"symbol": "RELIANCE", "exchange": "NSE"}, {"symbol": "RELIANCE", "exchange": "BSE"},
                                     {"symbol": "TCS", "exchange": "NSE"}]                # INFY dropped, BSE row kept
    assert saved.json()["symbols"] == ["RELIANCE", "TCS"] and saved.json()["count"] == 2
    # read back unchanged (items_json and symbols_json agree)
    assert client.get(BASE, headers=who["headers"]).json()[0]["items"] == saved.json()["items"]


def test_a_list_changed_by_an_older_release_is_read_from_its_symbols():
    """An older release (a code-only rollback) rewrites `symbols_json` only; the rows follow it, keeping exchanges."""
    who = _owner("rollback")
    wid = _items(who, "Mixed", [{"symbol": "NIFTY 50", "exchange": "NSE_INDEX"}, {"symbol": "SBIN", "exchange": "BSE"}]).json()["id"]

    async def older_release_write():
        async with _session_factory() as session:
            row = await session.get(WatchlistRecord, wid)
            row.symbols_json = '["SBIN", "TCS"]'
            await session.commit()
    _run(older_release_write())
    got = client.get(BASE, headers=who["headers"]).json()[0]
    assert got["symbols"] == ["SBIN", "TCS"]
    assert got["items"] == [{"symbol": "SBIN", "exchange": "BSE"}, {"symbol": "TCS", "exchange": "NSE"}]
    # a list saved before UI2-4a1 (no items_json) reads as NSE rows
    row = WatchlistRecord(id=1, tenant_id=1, user_id=1, name="x", symbols_json='["INFY"]', items_json=None)
    assert service.items_of(row) == [{"symbol": "INFY", "exchange": "NSE"}]
    row.items_json = '{"not": "a list"}'
    assert service.items_of(row) == [{"symbol": "INFY", "exchange": "NSE"}]


def test_version_conflict_is_a_clean_409():
    who = _owner("version")
    made = _items(who, "Desk", [{"symbol": "INFY"}]).json()
    wid = made["id"]
    phone = client.put(f"{BASE}/{wid}", headers=who["headers"], json={"name": "Desk", "items": [{"symbol": "TCS"}], "version": 1})
    assert phone.status_code == 200 and phone.json()["version"] == 2
    desktop = client.put(f"{BASE}/{wid}", headers=who["headers"], json={"name": "Desk", "items": [{"symbol": "SBIN"}], "version": 1})
    assert desktop.status_code == 409
    detail = desktop.json()["detail"]
    assert detail["current"]["version"] == 2 and detail["current"]["symbols"] == ["TCS"]      # the phone's edit is not lost
    assert client.get(BASE, headers=who["headers"]).json()[0]["symbols"] == ["TCS"]
    retry = client.put(f"{BASE}/{wid}", headers=who["headers"], json={"name": "Desk", "items": [{"symbol": "TCS"}, {"symbol": "SBIN"}], "version": 2})
    assert retry.status_code == 200 and retry.json()["version"] == 3
    # an old client's save (no version) bumps the version too, so a new client holding version 3 sees the change
    assert client.put(f"{BASE}/{wid}", headers=who["headers"], json={"name": "Desk", "symbols": ["INFY"]}).json()["version"] == 4
    assert client.put(f"{BASE}/{wid}", headers=who["headers"], json={"name": "Desk", "items": [], "version": 3}).status_code == 409
    assert client.put(f"{BASE}/{wid}", headers=who["headers"], json={"name": "Desk", "items": [], "version": 0}).status_code == 422


def test_order_of_lists():
    who = _owner("order")
    ids = {n: _create(who, n).json()["id"] for n in ("Alpha", "Bravo", "Charlie", "Delta")}
    assert [w["name"] for w in client.get(BASE, headers=who["headers"]).json()] == ["Alpha", "Bravo", "Charlie", "Delta"]
    ordered = client.put(f"{BASE}/order", headers=who["headers"], json={"ids": [ids["Charlie"], ids["Alpha"]]})
    assert ordered.status_code == 200, ordered.text
    # the named lists first, then the others in their current order
    assert [w["name"] for w in ordered.json()] == ["Charlie", "Alpha", "Bravo", "Delta"]
    assert [w["position"] for w in ordered.json()] == [0, 1, 2, 3]
    assert [w["name"] for w in client.get(BASE, headers=who["headers"]).json()] == ["Charlie", "Alpha", "Bravo", "Delta"]
    # the order is not an edit of a list: versions are unchanged
    assert {w["version"] for w in ordered.json()} == {1}
    # a list made later goes last, by name, until placed
    _create(who, "Aardvark")
    assert [w["name"] for w in client.get(BASE, headers=who["headers"]).json()][-1] == "Aardvark"
    assert client.put(f"{BASE}/order", headers=who["headers"], json={"ids": [ids["Alpha"], ids["Alpha"]]}).status_code == 422


def test_order_never_touches_another_users_lists():
    a, b = _owner("order-a"), _owner("order-b")
    mine = _create(a, "Mine").json()["id"]
    theirs = _create(b, "Theirs").json()["id"]
    assert client.put(f"{BASE}/order", headers=a["headers"], json={"ids": [theirs, mine]}).status_code == 404
    assert client.get(BASE, headers=b["headers"]).json()[0]["position"] is None
    assert client.get(BASE, headers=a["headers"]).json()[0]["position"] is None
    assert client.put(f"{BASE}/order", headers=_owner("order-off", flag=False)["headers"], json={"ids": []}).status_code == 404


# --- the migration ----------------------------------------------------------------------------------------------------

def test_migration_is_rollback_safe_and_keeps_one_head():
    import importlib.util
    from pathlib import Path

    from alembic.config import Config
    from alembic.script import ScriptDirectory

    root = Path(__file__).resolve().parents[1]
    path = root / "alembic" / "versions" / "f3b5d7e9a1c2_amw2_watchlists.py"
    spec = importlib.util.spec_from_file_location("amw2_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.ROLLBACK_SAFE is True and module.down_revision == "e2a4c6e8f0b3"
    script = ScriptDirectory.from_config(Config(str(root / "alembic.ini")))
    assert len(script.get_heads()) == 1                      # re-chained at merge; later migrations may sit on top
    assert script.get_revision("f3b5d7e9a1c2") is not None
