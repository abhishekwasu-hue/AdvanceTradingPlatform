"""B1 (bug lane, UI-H Part 1): with the own-broker data tier on, the user's own option-chain reads are HIS session
snapshots (app/option_chain/own_snapshots.py) and the Option chain analyses, charts and OI reads use them.

Covered: his read is stored under his tenant with his holder source; the analyses / OI reads use his earlier reads;
another member of his organisation and another organisation never see them; with none yet the plain "builds through
the session" state, never a refusal. Made-up chains only (the DEMO-style names of test_oc2)."""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.brokers.models import OptionChain
from app.db.models import MdTenantChainSnapshotRecord
from app.market_lake import chain_store
from app.option_chain import analyses as A
from app.option_chain import own_snapshots
from app.option_chain.snapshots import IST
from tests.test_amw2_watchlists import _set_flag
from tests.test_auth_api import _session_factory, client
from tests.test_oc2_option_chain_analyses import CURRENT, EXPIRY, REFERENCE, URL, _ChainBroker, _install, _name, _v2_owner
from tests.test_oc1_option_chain_view import memory_cache  # noqa: F401 - the fixture
from tests.test_phase_aa_market_data_api import _store_credentials

UTC = timezone.utc
run = asyncio.run


@pytest.fixture
def byob_owner():
    headers, me = _v2_owner("b1")
    _set_flag("byob_data_tier", me["tenant_id"], on=True)
    _store_credentials(me)
    yield headers, me
    _set_flag("byob_data_tier", me["tenant_id"], on=False)


def _rows(tenant_id: int, underlying: str):
    async def go():
        async with _session_factory() as session:
            return list(await session.scalars(select(MdTenantChainSnapshotRecord).where(
                MdTenantChainSnapshotRecord.tenant_id == tenant_id, MdTenantChainSnapshotRecord.underlying == underlying)))
    return run(go())


def _seed_own(tenant_id: int, user_id: int, underlying: str, chain: OptionChain, at: datetime) -> None:
    """One earlier read of the user's own session, stored as the analyses store it."""
    async def go():
        async with _session_factory() as session:
            result = await chain_store.write(session, tenant_id, underlying, datetime.fromisoformat(EXPIRY).date(), chain, at,
                                             slot_minutes=5, source=f"broker:upstox:u{user_id}")
            assert result.status == "OK"
            await session.commit()
    run(go())


def _live(name: str) -> OptionChain:
    return OptionChain(underlying=name, expiry=EXPIRY, underlying_ltp=118.0, rows=CURRENT.rows)


def _earlier(name: str) -> OptionChain:
    return OptionChain(underlying=name, expiry=EXPIRY, underlying_ltp=116.0, rows=REFERENCE.rows)


def _ten_minutes_ago() -> datetime:
    now = datetime.now(UTC)
    at = now - timedelta(minutes=10)
    if at.astimezone(IST).date() != now.astimezone(IST).date():       # never across the IST day boundary
        pytest.skip("too close to IST midnight for a same-day snapshot")
    return at


def test_his_read_is_stored_as_his_own_session_snapshot(monkeypatch, byob_owner):
    headers, me = byob_owner
    name = _name()
    _install(monkeypatch, _ChainBroker({name: _live(name)}))
    assert client.get(URL, headers=headers, params={"symbol": name}).json()["state"] == "ok"
    rows = _rows(me["tenant_id"], name)
    assert len(rows) == 1 and rows[0].source == f"broker:upstox:u{me['id']}" and rows[0].expiry.isoformat() == EXPIRY
    assert {r.strike for r in chain_store.decode(rows[0].payload)} == {r.strike for r in CURRENT.rows}
    # a second read in the same slot is not stored again
    assert client.get(URL, headers=headers, params={"symbol": name}).json()["state"] == "ok"
    assert len(_rows(me["tenant_id"], name)) == 1


def test_no_own_snapshot_yet_is_the_builds_through_the_session_state_not_a_refusal(monkeypatch, byob_owner):
    headers, me = byob_owner
    name = _name()
    _install(monkeypatch, _ChainBroker({name: _live(name)}))
    body = client.get(URL, headers=headers, params={"symbol": name}).json()
    assert body["state"] == "ok" and body["session"] == {"snapshots": 0, "since": None, "note": None}
    tabs = {a["key"]: a for a in body["analyses"]}
    assert tabs["build_up"]["state"] == "no_data" and A.BU_HISTORY_OFF not in tabs["build_up"]["sentences"]
    assert "none is stored for this expiry yet" in tabs["build_up"]["sentences"][0]
    assert A.NO_HISTORY in tabs["pcr"]["sentences"] and A.HISTORY_OFF not in tabs["pcr"]["sentences"]
    # the OI reads: 200 with an empty day, never the 409 refusal
    other = _name()
    res = client.get(f"/api/option-chain/{other}/banner", headers=headers)
    assert res.status_code == 200 and res.json()["banner"] is None
    res = client.get(f"/api/option-chain/{other}/strikes", headers=headers)
    assert res.status_code == 200 and res.json()["strikes"] == [] and res.json()["data_as_of"] is None


def test_analyses_and_oi_reads_use_his_earlier_reads_with_byob_on(monkeypatch, byob_owner):
    headers, me = byob_owner
    name = _name()
    _install(monkeypatch, _ChainBroker({name: _live(name)}))
    _seed_own(me["tenant_id"], me["id"], name, _earlier(name), _ten_minutes_ago())
    body = client.get(URL, headers=headers, params={"symbol": name}).json()
    assert body["state"] == "ok" and body["session"]["snapshots"] == 1
    tabs = {a["key"]: a for a in body["analyses"]}
    assert tabs["build_up"]["state"] == "ok"                                 # compared with his own earlier read
    assert A.NO_HISTORY not in tabs["pcr"]["sentences"]
    # the OI reads: his two reads (the earlier one and the one just stored) are the day
    history = client.get(f"/api/option-chain/{name}/history", headers=headers)
    assert history.status_code == 200 and len(history.json()["rows"]) >= 1
    strikes = client.get(f"/api/option-chain/{name}/strikes", headers=headers).json()
    assert strikes["data_as_of"] is not None and strikes["strikes"]
    first = {s["strike"]: s["baseline_call_oi"] for s in strikes["strikes"]}
    assert first[120.0] == 40                                                # the first OI of his own day (REFERENCE)
    names = [b["underlying"] for b in client.get("/api/option-chain/banners", headers=headers).json()["banners"]]
    assert name in names


def test_another_member_and_another_organisation_never_see_his_snapshots(monkeypatch, byob_owner):
    from tests.test_trading_worker import _upgrade_plan
    from tests.test_z3_members import member
    headers, me = byob_owner
    name = _name()
    _install(monkeypatch, _ChainBroker({name: _live(name)}))
    _seed_own(me["tenant_id"], me["id"], name, _earlier(name), _ten_minutes_ago())
    # another member of the same organisation, with his own session at the broker
    _upgrade_plan(me["tenant_id"])
    mate = member({"headers": headers})
    _store_credentials({"tenant_id": mate["tenant_id"], "id": mate["user_id"]}, label="mate")
    body = client.get(URL, headers=mate["headers"], params={"symbol": name}).json()
    assert body["state"] == "ok" and body["session"]["snapshots"] == 0
    mate_strikes = client.get(f"/api/option-chain/{name}/strikes", headers=mate["headers"]).json()["strikes"]
    # only the mate's own read (CURRENT, just stored): one point per strike, his first OI - never the owner's REFERENCE
    assert mate_strikes and all(len(s["points"]) == 1 for s in mate_strikes)
    assert {s["strike"]: s["baseline_call_oi"] for s in mate_strikes}[100.0] == 15
    assert name in [b["underlying"] for b in client.get("/api/option-chain/banners", headers=mate["headers"]).json()["banners"]]
    # another organisation with the tier on and its own session
    other_headers, other = _v2_owner("b1-other")
    _set_flag("byob_data_tier", other["tenant_id"], on=True)
    try:
        _store_credentials(other)
        body = client.get(URL, headers=other_headers, params={"symbol": name}).json()
        assert body["state"] == "ok" and body["session"]["snapshots"] == 0
        other_strikes = client.get(f"/api/option-chain/{name}/strikes", headers=other_headers).json()["strikes"]
        assert all(len(s["points"]) == 1 for s in other_strikes)          # its own read only
    finally:
        _set_flag("byob_data_tier", other["tenant_id"], on=False)

    async def go():
        async with _session_factory() as session:
            today = datetime.now(UTC).astimezone(IST).date()
            mine = await chain_store.holder_day(session, me["tenant_id"], me["id"], name, today)
            theirs = await chain_store.holder_day(session, me["tenant_id"], mate["user_id"], name, today)
            foreign = await chain_store.holder_day(session, other["tenant_id"], me["id"], name, today)
            return len(mine), theirs, foreign
    mine, theirs, foreign = run(go())
    assert mine == 1 and foreign == []                       # his one earlier read; the mate's read is not his
    assert len(theirs) == 1                                  # the mate's own read is the mate's alone


def test_only_his_own_credential_is_stored(monkeypatch):
    from types import SimpleNamespace
    user = SimpleNamespace(id=1, tenant_id=1)

    async def go():
        async with _session_factory() as session:
            borrowed = SimpleNamespace(user_id=2, tenant_id=1, broker_name="upstox")
            return await own_snapshots.record(session, user, borrowed, "NIFTY", CURRENT, datetime.now(UTC))   # type: ignore[arg-type]
    assert run(go()) == "SKIPPED"


def test_one_expiry_picks_the_nearest_expiry_ahead():
    from datetime import date
    day = date(2026, 10, 21)
    at = datetime(2026, 10, 21, 4, 0, tzinfo=UTC)
    near = chain_store.StoredChain("X", date(2026, 10, 27), at, at, 1.0, list(CURRENT.rows))
    far = chain_store.StoredChain("X", date(2026, 11, 3), at, at, 1.0, list(REFERENCE.rows))
    old = chain_store.StoredChain("X", date(2026, 10, 20), at, at, 1.0, list(REFERENCE.rows))
    slots = own_snapshots.one_expiry([far, near, old], day)
    assert len(slots) == 1 and slots[0].chain.expiry == "2026-10-27"
    assert own_snapshots.one_expiry([], day) == []


def test_a_storage_failure_never_fails_the_read(monkeypatch, byob_owner):
    headers, me = byob_owner
    name = _name()
    _install(monkeypatch, _ChainBroker({name: _live(name)}))

    async def broken(*args, **kwargs):
        raise RuntimeError("disk full")
    monkeypatch.setattr(own_snapshots.chain_store, "write", broken)
    body = client.get(URL, headers=headers, params={"symbol": name}).json()
    assert body["state"] == "ok" and body["source"]["broker"] == "upstox"
    assert client.get("/api/option-chain/charts", headers=headers, params={"symbol": name}).json()["state"] == "ok"
    assert _rows(me["tenant_id"], name) == []


# ---------------------------------------------------------------- the live chain view and Ask AI (B1-2: no split by data source)

@pytest.mark.usefixtures("memory_cache")
def test_the_live_chain_view_read_is_stored_as_his_snapshot(monkeypatch, byob_owner):
    from tests.test_oc1_option_chain_view import _chain, _setup_routes
    headers, me = byob_owner
    _setup_routes(monkeypatch, _chain(change=True))
    res = client.get("/api/option-chain/NIFTY/live?expiry=2026-10-27", headers=headers)
    assert res.status_code == 200, res.text
    rows = _rows(me["tenant_id"], "NIFTY")
    assert len(rows) == 1 and rows[0].source == f"broker:upstox:u{me['id']}" and rows[0].expiry.isoformat() == "2026-10-27"
    assert client.get("/api/option-chain/NIFTY/live?expiry=2026-10-27", headers=headers).status_code == 200
    assert len(_rows(me["tenant_id"], "NIFTY")) == 1                         # the same 5-minute slot: stored once


@pytest.mark.usefixtures("memory_cache")
def test_the_live_chain_view_stores_nothing_without_the_tier(monkeypatch):
    from tests.test_oc1_option_chain_view import _chain, _setup_routes
    headers, me = _v2_owner("b1-live-off")
    _store_credentials(me)
    _setup_routes(monkeypatch, _chain(change=True))
    assert client.get("/api/option-chain/NIFTY/live?expiry=2026-10-27", headers=headers).status_code == 200
    assert _rows(me["tenant_id"], "NIFTY") == []


def _ask_read(user_id: int, name: str):
    from app.db.models import User
    from app.option_chain import ask_adapter

    async def go():
        async with _session_factory() as session:
            user = await session.get(User, user_id)
            return await ask_adapter.read(session, user, name, None, datetime.now(UTC))
    return run(go())


def test_ask_ai_uses_his_own_snapshots_for_the_session_path(monkeypatch, byob_owner):
    from app.option_chain import ask_adapter
    headers, me = byob_owner
    name = _name()
    _install(monkeypatch, _ChainBroker({name: _live(name)}))
    first = _ask_read(me["id"], name)                                        # none yet: an empty path, no refusal note
    assert first.state == "ok" and first.history == [] and first.history_note != ask_adapter.NO_HISTORY_BYOB
    assert len(_rows(me["tenant_id"], name)) == 1                            # his Ask read is his snapshot too
    _seed_own(me["tenant_id"], me["id"], name, _earlier(name), _ten_minutes_ago())
    again = _ask_read(me["id"], name)
    assert again.state == "ok" and len(again.history) == 2                   # the seeded read and his first Ask read
    assert again.history[0].rows[120.0]["ce_oi"] == 40                       # oldest first: his earlier read (REFERENCE)


def test_ask_ai_never_reads_another_members_snapshots(monkeypatch, byob_owner):
    from tests.test_trading_worker import _upgrade_plan
    from tests.test_z3_members import member
    headers, me = byob_owner
    name = _name()
    _install(monkeypatch, _ChainBroker({name: _live(name)}))
    _seed_own(me["tenant_id"], me["id"], name, _earlier(name), _ten_minutes_ago())
    _upgrade_plan(me["tenant_id"])
    mate = member({"headers": headers})
    _store_credentials({"tenant_id": mate["tenant_id"], "id": mate["user_id"]}, label="mate")
    assert _ask_read(mate["user_id"], name).history == []
