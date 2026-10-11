"""OI Banner O5: OI gates as opt-in deployment entry conditions - fail-closed on missing / stale data and unset limits,
each gate wired to the O1 functions, the deployments API, and the worker skipping (never exiting) on a block."""
import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import delete

from app.db.models import OIBannerSettingRecord, OIDayBaselineRecord, OISnapshotRecord, StrategyDeploymentRecord, StrikeOISnapshotRecord
from app.option_chain import oi_gates
from tests.test_auth_api import _session_factory, client  # noqa: F401 - fixtures
from tests.test_o2_oi_snapshots import T0, UND, _chain, _collect
from tests.test_phase_l_ai import _owner
from tests.test_trading_worker import OPEN_NOW, _FakeBroker, _deploy, _force_signal, _get, _signal, _tenant as _worker_tenant, _trades, _worker


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _clean():
    async def go():
        async with _session_factory() as session:
            for model in (StrikeOISnapshotRecord, OISnapshotRecord, OIDayBaselineRecord, OIBannerSettingRecord):
                await session.execute(delete(model))
            await session.commit()
    _run(go())
    yield


async def _check(tenant_id, direction, gates, now):
    async with _session_factory() as session:
        return await oi_gates.check_entry(session, tenant_id, UND, direction, gates, now)


def _settings(headers, **overrides):
    assert client.put(f"/api/option-chain/{UND}/settings", json={"overrides": overrides}, headers=headers).status_code == 200


def test_parse_gates_and_direction():
    assert oi_gates.parse_gates("oi_diff, PCR,OI_DIFF") == ["OI_DIFF", "PCR"]
    assert oi_gates.parse_gates(None) == []
    with pytest.raises(ValueError):
        oi_gates.parse_gates("OI_DIFF,MAGIC")
    assert oi_gates.direction_of("LONG") == "BULLISH" and oi_gates.direction_of("SignalDirection.SHORT") == "BEARISH" and oi_gates.direction_of("NONE") is None


def test_gates_fail_closed_without_fresh_data():
    _, me = _owner("o5-stale@example.com")
    nothing = _run(_check(me["tenant_id"], "BULLISH", ["OI_DIFF"], T0))
    assert not nothing.allowed and "missing or older" in nothing.reasons[0]
    _run(_collect(_chain(), T0))
    stale = _run(_check(me["tenant_id"], "BULLISH", ["OI_DIFF"], T0 + timedelta(minutes=40)))
    assert not stale.allowed
    assert _run(_check(me["tenant_id"], "BULLISH", [], T0)).allowed                          # no gate requested: no effect


def test_each_gate_reads_its_o1_function():
    headers, me = _owner("o5-gates@example.com")
    for i, put in enumerate((2000.0, 2200.0, 2420.0)):                                       # puts above calls, growing
        _run(_collect(_chain(put=put, put_ltp=10.0 - i * 0.5), T0 + timedelta(minutes=5 * i)))
    now = T0 + timedelta(minutes=11)
    bull = _run(_check(me["tenant_id"], "BULLISH", ["OI_DIFF", "OI_CONFIRM"], now))
    assert bull.allowed and bull.passed == ["OI_DIFF", "OI_CONFIRM"]
    bear = _run(_check(me["tenant_id"], "BEARISH", ["OI_DIFF"], now))
    assert not bear.allowed and "does not support a bearish entry" in bear.reasons[0]
    # PCR and IV gates need the organisation's limits: unset = blocked, never "pass by default"
    unset = _run(_check(me["tenant_id"], "BULLISH", ["PCR", "IV_CHANGE"], now))
    assert not unset.allowed and len(unset.reasons) == 2 and all("not set" in r for r in unset.reasons)
    _settings(headers, pcr_bullish_min=0.8, pcr_bearish_max=1.2)
    assert _run(_check(me["tenant_id"], "BULLISH", ["PCR"], now)).allowed                    # PCR 2.42 >= 0.8
    pcr_bear = _run(_check(me["tenant_id"], "BEARISH", ["PCR"], now))
    assert not pcr_bear.allowed and "> 1.2" in pcr_bear.reasons[0]
    _settings(headers, pcr_bullish_min=0.8, pcr_bearish_max=1.2, iv_change_max_pct=15)
    no_history = _run(_check(me["tenant_id"], "BULLISH", ["IV_CHANGE"], now))
    assert not no_history.allowed and "sideways history" in no_history.reasons[0]
    swing = _run(_check(me["tenant_id"], "BULLISH", ["SWING_OI"], now))
    assert swing.allowed or "oppose" in swing.reasons[0]
    wall = _run(_check(me["tenant_id"], "BULLISH", ["OI_WALL"], now))
    assert wall.allowed and wall.passed == ["OI_WALL"]                                       # put OI added since the open
    no_wall = _run(_check(me["tenant_id"], "BEARISH", ["OI_WALL"], now))
    assert not no_wall.allowed and "no CE OI added" in no_wall.reasons[0]                    # call OI flat all day


def test_iv_gate_uses_sideways_sessions_from_stored_snapshots():
    headers, me = _owner("o5-iv@example.com")
    _settings(headers, iv_change_max_pct=15)
    day_before = T0 - timedelta(days=1)
    for m, spot in ((0, 24510.0), (60, 24540.0), (120, 24480.0), (180, 24515.0)):           # a range day: small body
        chain = _chain(spot=spot)
        for r in chain.rows:
            r.call_iv, r.put_iv = 10.0, 10.0
        _run(_collect(chain, day_before + timedelta(minutes=m)))
    today = _chain()
    for r in today.rows:
        r.call_iv, r.put_iv = 13.0, 13.0                                                     # +30 % on the sideways baseline
    _run(_collect(today, T0))
    blocked = _run(_check(me["tenant_id"], "BULLISH", ["IV_CHANGE"], T0 + timedelta(minutes=1)))
    assert not blocked.allowed and "breakout risk" in blocked.reasons[0]
    _settings(headers, iv_change_max_pct=50)
    assert _run(_check(me["tenant_id"], "BULLISH", ["IV_CHANGE"], T0 + timedelta(minutes=1))).allowed


def test_deployments_api_validates_oi_gates():
    t = _worker_tenant("o5-api@example.com")
    ok = client.post("/api/deployments", headers=t["headers"], json={"strategy_id": "ema_rsi_scalper_1m", "symbol": "TCS", "oi_gates": ["oi_diff", "PCR"]})
    assert ok.status_code == 201, ok.text
    assert ok.json()["oi_gates"] == ["OI_DIFF", "PCR"]
    bad = client.post("/api/deployments", headers=t["headers"], json={"strategy_id": "ema_rsi_scalper_1m", "symbol": "TCS", "oi_gates": ["MAGIC"]})
    assert bad.status_code == 422


def test_worker_skips_a_new_entry_the_oi_gates_block(monkeypatch):
    t = _worker_tenant("o5-worker@example.com")
    dep_id = _deploy(t)

    async def set_gates():
        async with _session_factory() as session:
            dep = await session.get(StrategyDeploymentRecord, dep_id)
            dep.oi_gates = "OI_DIFF"
            await session.commit()
    _run(set_gates())
    _force_signal(monkeypatch, _signal)
    worker = _worker(monkeypatch, _FakeBroker(ltp=101.0))
    report = _run(worker.run_cycle(now=OPEN_NOW))                                            # no OI snapshot at all
    assert report.signals_executed == 0 and _trades(t["tenant_id"]) == []
    assert "skipped by OI gates" in _get(StrategyDeploymentRecord, dep_id).last_error
