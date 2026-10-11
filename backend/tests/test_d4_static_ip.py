"""Part D4 (rule IN-SEBI.static_ip.registered): registered static egress IPs per broker.

- only a public IP can be registered; PRIMARY and BACKUP per broker; every change is recorded;
- a role may change at most `max_changes_per_week` times in 7 days (rule-set data) - the next change is refused;
- two organisations on one IP get a warning; the server's egress IP not among the registered ones is warned;
- with STATIC_IP_REQUIRED_FOR_LIVE on, the worker refuses LIVE entries when SERVER_EGRESS_IP is not registered for the
  deployment's broker (PAPER and exits untouched); off (the default) nothing changes;
- the LIVE readiness checklist carries the item.
"""
import asyncio

import pytest
from sqlalchemy import select

from app.compliance import static_ip
from app.core import config
from app.db.models import EgressIpChangeRecord, StrategyDeploymentRecord
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
    assert "Settings > Static IP" in items["static_ip"]["fix"]
