"""Part D4 (rule IN-SEBI.static_ip.registered): registered static egress IPs per broker.

- only a public IP can be registered; PRIMARY and BACKUP per broker; every change is recorded;
- a role may change at most `max_changes_per_week` times in 7 days (rule-set data) - the next change is refused;
- two organisations on one IP get a warning; the server's egress IP not among the registered ones is warned;
- with STATIC_IP_REQUIRED_FOR_LIVE on, the worker refuses LIVE entries when SERVER_EGRESS_IP is not registered for the
  deployment's broker (PAPER and exits untouched); off (the default) nothing changes;
- the LIVE readiness checklist carries the item;
- only a broker the registry knows can be registered (`angel_one`, not `angelone`); rows saved under the old `angelone`
  spelling count as `angel_one` and are renamed on the next save.
"""
import asyncio
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.compliance import static_ip
from app.core import config
from app.db.models import EgressIpChangeRecord, EgressIpRecord, StrategyDeploymentRecord
from tests.test_auth_api import _session_factory, client
from tests.test_phase_k_commercial import _owner
from tests.test_trading_worker import OPEN_NOW, _FakeBroker, _deploy, _force_signal, _signal, _tenant, _worker

PUBLIC, OTHER_PUBLIC, BACKUP = "203.0.113.10", "198.51.100.20", "2001:db8::1"


def _run(coro):
    return asyncio.run(coro)


def test_only_public_ips_are_accepted():
    assert static_ip.normalise_ip(" 8.8.8.8 ") == "8.8.8.8"
    for bad in ("10.0.0.5", "192.168.1.2", "127.0.0.1", "not-an-ip", ""):
        with pytest.raises(static_ip.StaticIpError):
            static_ip.normalise_ip(bad)


def test_owner_registers_primary_and_backup_and_the_weekly_change_limit_holds(monkeypatch):
    monkeypatch.setattr(static_ip, "normalise_ip", lambda v: v.strip())       # documentation ranges are not "global"
    headers, me = _owner("d4-owner@example.com")
    assert client.put("/api/compliance/static-ips", json={"broker_name": "upstox", "ip": PUBLIC}).status_code == 401
    first = client.put("/api/compliance/static-ips", headers=headers, json={"broker_name": "Upstox", "ip": PUBLIC})
    assert first.status_code == 200 and first.json()["role"] == "PRIMARY" and first.json()["broker_name"] == "upstox"
    assert client.put("/api/compliance/static-ips", headers=headers, json={"broker_name": "upstox", "role": "BACKUP", "ip": BACKUP}).status_code == 200
    same = client.put("/api/compliance/static-ips", headers=headers, json={"broker_name": "upstox", "ip": PUBLIC})
    assert same.status_code == 200                                              # unchanged: not a change
    changed = client.put("/api/compliance/static-ips", headers=headers, json={"broker_name": "upstox", "ip": OTHER_PUBLIC})
    assert changed.status_code == 200                                           # first change this week
    again = client.put("/api/compliance/static-ips", headers=headers, json={"broker_name": "upstox", "ip": PUBLIC})
    assert again.status_code == 409 and "last 7 days" in again.json()["detail"]
    body = client.get("/api/compliance/static-ips", headers=headers).json()
    assert {(r["role"], r["ip"]) for r in body["ips"]} == {("PRIMARY", OTHER_PUBLIC), ("BACKUP", BACKUP)}
    assert body["max_changes_per_week"] == 1 and body["required_for_live"] is False

    async def changes():
        async with _session_factory() as session:
            return list(await session.scalars(select(EgressIpChangeRecord).where(EgressIpChangeRecord.tenant_id == me["tenant_id"])))
    rows = _run(changes())
    assert [(r.role, r.old_ip, r.new_ip) for r in rows] == [("PRIMARY", None, PUBLIC), ("BACKUP", None, BACKUP), ("PRIMARY", PUBLIC, OTHER_PUBLIC)]


def test_shared_ip_and_unregistered_server_ip_are_warned(monkeypatch):
    monkeypatch.setattr(static_ip, "normalise_ip", lambda v: v.strip())
    a, _ = _owner("d4-shared-a@example.com")
    b, _ = _owner("d4-shared-b@example.com")
    for headers in (a, b):
        assert client.put("/api/compliance/static-ips", headers=headers, json={"broker_name": "zerodha", "ip": "203.0.113.77"}).status_code == 200
    monkeypatch.setattr(config, "SERVER_EGRESS_IP", "203.0.113.99")
    warnings = client.get("/api/compliance/static-ips", headers=a).json()["warnings"]
    assert any("203.0.113.77 is also registered by 1 other" in w for w in warnings)
    assert any("203.0.113.99 is not among your registered IPs" in w for w in warnings)


def test_live_entry_gate_is_off_by_default_and_names_the_gap_when_on(monkeypatch):
    t = _tenant("d4-gate@example.com")

    async def problem(broker="upstox"):
        async with _session_factory() as session:
            return await static_ip.live_entry_problem(session, t["tenant_id"], broker)
    assert _run(problem()) is None                                              # flag off: nothing changes
    monkeypatch.setattr(config, "STATIC_IP_REQUIRED_FOR_LIVE", True)
    monkeypatch.setattr(config, "SERVER_EGRESS_IP", None)
    assert "SERVER_EGRESS_IP is not configured" in _run(problem())
    monkeypatch.setattr(config, "SERVER_EGRESS_IP", PUBLIC)
    assert "not registered for upstox" in _run(problem())
    monkeypatch.setattr(static_ip, "normalise_ip", lambda v: v.strip())

    async def register():
        async with _session_factory() as session:
            await static_ip.set_ip(session, t["tenant_id"], "upstox", "PRIMARY", PUBLIC)
    _run(register())
    assert _run(problem()) is None and "not registered for zerodha" in _run(problem("zerodha"))


def test_worker_refuses_live_entries_without_a_registered_ip_but_paper_trades(monkeypatch):
    monkeypatch.setattr("app.execution.router.OrderRouter.fill_poll_delay_seconds", 0)
    t = _tenant("d4-worker@example.com")
    live_id, paper_id = _deploy(t, mode="LIVE"), _deploy(t, mode="PAPER", symbol="INFY")
    broker = _FakeBroker(ltp=101.0)
    worker = _worker(monkeypatch, broker)
    _force_signal(monkeypatch, _signal)
    monkeypatch.setattr(config, "STATIC_IP_REQUIRED_FOR_LIVE", True)
    monkeypatch.setattr(config, "SERVER_EGRESS_IP", PUBLIC)
    report = _run(worker.run_cycle(now=OPEN_NOW))
    assert broker.placed == [] and report.signals_executed == 1                 # the PAPER sibling traded

    async def errors():
        async with _session_factory() as session:
            return {r.id: r.last_error for r in await session.scalars(select(StrategyDeploymentRecord).where(
                StrategyDeploymentRecord.id.in_([live_id, paper_id])))}
    rows = _run(errors())
    assert "not registered for upstox" in (rows[live_id] or "") and "registered" not in (rows[paper_id] or "")


def test_readiness_lists_the_static_ip_item_for_live(monkeypatch):
    headers, _ = _owner("d4-ready@example.com")
    items = {i["key"]: i for i in client.get("/api/readiness?target=live", headers=headers).json()["items"]}
    assert items["static_ip"]["status"] == "todo" and items["static_ip"]["scope"] == "LIVE"
    assert "the Broker page, under Static IP" in items["static_ip"]["fix"]


def test_only_registry_broker_names_are_accepted(monkeypatch):
    monkeypatch.setattr(static_ip, "normalise_ip", lambda v: v.strip())
    headers, _ = _owner("d4-names@example.com")
    for bad in ("angelone", "kite", "nosuch"):
        res = client.put("/api/compliance/static-ips", headers=headers, json={"broker_name": bad, "ip": PUBLIC})
        assert res.status_code == 400, res.text
        assert f"Unknown broker '{bad}'" in res.json()["detail"] and "angel_one" in res.json()["detail"]
    ok = client.put("/api/compliance/static-ips", headers=headers, json={"broker_name": " Angel_One ", "ip": PUBLIC})
    assert ok.status_code == 200 and ok.json()["broker_name"] == "angel_one"
    assert [r["broker_name"] for r in client.get("/api/compliance/static-ips", headers=headers).json()["ips"]] == ["angel_one"]


def test_a_row_saved_as_angelone_counts_as_angel_one_and_is_renamed_on_the_next_save(monkeypatch):
    monkeypatch.setattr(static_ip, "normalise_ip", lambda v: v.strip())
    headers, me = _owner("d4-legacy@example.com")
    tenant_id = me["tenant_id"]

    async def seed():
        async with _session_factory() as session:
            session.add(EgressIpRecord(tenant_id=tenant_id, broker_name="angelone", role="PRIMARY", ip=PUBLIC))
            session.add(EgressIpChangeRecord(tenant_id=tenant_id, broker_name="angelone", role="PRIMARY", old_ip=OTHER_PUBLIC,
                                             new_ip=PUBLIC, changed_at=datetime.now(timezone.utc)))
            await session.commit()
    _run(seed())

    async def problem():
        async with _session_factory() as session:
            return await static_ip.live_entry_problem(session, tenant_id, "angel_one")
    monkeypatch.setattr(config, "STATIC_IP_REQUIRED_FOR_LIVE", True)
    monkeypatch.setattr(config, "SERVER_EGRESS_IP", PUBLIC)
    assert _run(problem()) is None                                              # the LIVE gate matches the old spelling

    async def readiness_gap():
        from app.platform.readiness import _static_ip_gap
        async with _session_factory() as session:
            return await _static_ip_gap(session, tenant_id, "angel_one", PUBLIC)
    assert _run(readiness_gap()) is None                                        # and so does the readiness checklist
    assert [r["broker_name"] for r in client.get("/api/compliance/static-ips", headers=headers).json()["ips"]] == ["angel_one"]

    # The change made under the old spelling still counts towards the weekly limit.
    res = client.put("/api/compliance/static-ips", headers=headers, json={"broker_name": "angel_one", "ip": OTHER_PUBLIC})
    assert res.status_code == 409 and "last 7 days" in res.json()["detail"]
    # Saving the same IP under the registry name renames the old row in place (no second row).
    res = client.put("/api/compliance/static-ips", headers=headers, json={"broker_name": "angel_one", "ip": PUBLIC})
    assert res.status_code == 200 and res.json()["broker_name"] == "angel_one"

    async def stored():
        async with _session_factory() as session:
            return [(r.broker_name, r.role, r.ip) for r in await session.scalars(
                select(EgressIpRecord).where(EgressIpRecord.tenant_id == tenant_id))]
    assert _run(stored()) == [("angel_one", "PRIMARY", PUBLIC)]
    assert _run(problem()) is None


def test_a_stale_angelone_row_is_ignored_when_an_angel_one_row_exists_and_removed_on_the_next_save(monkeypatch):
    """Both spellings stored for one role (possible only through the API before the registry check): the registry row wins
    everywhere, the stale legacy IP never satisfies the LIVE check, and the next save of that role deletes the legacy row."""
    monkeypatch.setattr(static_ip, "normalise_ip", lambda v: v.strip())
    headers, me = _owner("d4-legacy-both@example.com")
    tenant_id = me["tenant_id"]

    async def seed():
        async with _session_factory() as session:
            session.add(EgressIpRecord(tenant_id=tenant_id, broker_name="angelone", role="PRIMARY", ip=PUBLIC))       # stale
            session.add(EgressIpRecord(tenant_id=tenant_id, broker_name="angel_one", role="PRIMARY", ip=OTHER_PUBLIC))
            await session.commit()
    _run(seed())

    async def problem():
        async with _session_factory() as session:
            return await static_ip.live_entry_problem(session, tenant_id, "angel_one")
    monkeypatch.setattr(config, "STATIC_IP_REQUIRED_FOR_LIVE", True)
    monkeypatch.setattr(config, "SERVER_EGRESS_IP", PUBLIC)                   # the server sits on the stale IP
    assert _run(problem()) is not None                                        # the stale legacy row does not satisfy LIVE
    listed = client.get("/api/compliance/static-ips", headers=headers).json()["ips"]
    assert [(r["broker_name"], r["ip"]) for r in listed] == [("angel_one", OTHER_PUBLIC)]

    res = client.put("/api/compliance/static-ips", headers=headers, json={"broker_name": "angel_one", "ip": OTHER_PUBLIC})
    assert res.status_code == 200

    async def stored():
        async with _session_factory() as session:
            return [(r.broker_name, r.role, r.ip) for r in await session.scalars(
                select(EgressIpRecord).where(EgressIpRecord.tenant_id == tenant_id))]
    assert _run(stored()) == [("angel_one", "PRIMARY", OTHER_PUBLIC)]
