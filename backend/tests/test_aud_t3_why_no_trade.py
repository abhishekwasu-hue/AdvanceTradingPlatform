"""AUD-T3 (owner audit 28 Oct): the "Why no trade" timeline.

- the worker stores one reason per closed bar a bot looked at (no setup -> the strategy's own words; a trade -> entered),
  a bar read again keeps one row, and the log changes no decision;
- reasons with no bar collapse; unknown codes fail loudly; each bot keeps the newest WHY_NO_TRADE_KEEP_BARS rows;
- GET /api/deployments/{id}/why: 404 while `why_no_trade` (or `ui_v2`) is off; the organisation's own bots only.
"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.core import config
from app.core.enums import SignalDirection
from app.db.models import DeploymentBarReasonRecord, StrategyDeploymentRecord
from app.deployments import why
from app.market_data.calendar import IST
from app.strategy_engine.registry import registry
from tests.test_settings_a_connect_ai import _flags
from tests.test_trading_worker import STRATEGY, _deploy, _FakeBroker, _run, _signal, _tenant, _worker
from tests.test_auth_api import _session_factory, client

NOW = datetime(2026, 9, 25, 10, 32, 30, tzinfo=IST)


def _rows(dep_id):
    async def go():
        async with _session_factory() as session:
            return list(await session.scalars(select(DeploymentBarReasonRecord).where(
                DeploymentBarReasonRecord.deployment_id == dep_id).order_by(DeploymentBarReasonRecord.id)))
    return _run(go())


def _no_setup(monkeypatch, reasons):
    def analyze(data, symbol):
        s = _signal(direction=SignalDirection.NO_TRADE)
        s.reasons = list(reasons)
        return s
    monkeypatch.setattr(registry.get(STRATEGY), "analyze", analyze)


def test_the_worker_stores_one_reason_per_closed_bar(monkeypatch):
    t = _tenant("aud-t3-worker@example.com")
    dep_id = _deploy(t)
    _no_setup(monkeypatch, ["price has not reached the demand area"])
    worker = _worker(monkeypatch, _FakeBroker(intraday_bars=78))
    _run(worker.run_cycle(now=NOW))
    _run(worker.run_cycle(now=NOW + timedelta(seconds=10)))       # the same closed bar read again: still one row
    rows = _rows(dep_id)
    assert [r.code for r in rows] == ["no_signal"]
    assert rows[0].detail == "price has not reached the demand area" and rows[0].bar_time is not None
    bar = rows[0].bar_time if rows[0].bar_time.tzinfo else rows[0].bar_time.replace(tzinfo=timezone.utc)
    assert bar + timedelta(minutes=1) <= NOW                       # the closed bar, never the one still forming

    monkeypatch.setattr(registry.get(STRATEGY), "analyze", lambda data, symbol: _signal(ts=data["1min"].index[-1].to_pydatetime()))
    worker = _worker(monkeypatch, _FakeBroker(intraday_bars=79))
    _run(worker.run_cycle(now=NOW + timedelta(minutes=1)))
    codes = [r.code for r in _rows(dep_id)]
    assert codes[0] == "no_signal" and codes[-1] in why.CODES and len(codes) == 2
    assert codes[-1] in ("entered", "risk_setting")                # the decision itself is the worker's, unchanged


def test_record_collapses_barless_repeats_prunes_and_refuses_unknown_codes(monkeypatch):
    t = _tenant("aud-t3-record@example.com")
    dep_id = _deploy(t)
    monkeypatch.setattr(config, "WHY_NO_TRADE_KEEP_BARS", 3)

    async def go():
        async with _session_factory() as session:
            dep = await session.get(StrategyDeploymentRecord, dep_id)
            with pytest.raises(ValueError):
                await why.record(session, dep, "made_up", None, None, NOW)
            await why.record(session, dep, "outside_hours", None, None, NOW)
            await why.record(session, dep, "outside_hours", None, None, NOW + timedelta(minutes=1))
            await session.commit()
            assert len(await why.recent(session, dep_id, 10)) == 1
            for i in range(5):
                await why.record(session, dep, "no_signal", NOW + timedelta(minutes=i), f"bar {i}", NOW + timedelta(minutes=2 + i))
            await session.commit()
            return await why.recent(session, dep_id, 10)
    rows = _run(go())
    assert [r["detail"] for r in rows] == ["bar 4", "bar 3", "bar 2"]
    assert rows[0]["label"] == why.CODES["no_signal"]


def test_the_route_is_flagged_and_tenant_scoped():
    t = _tenant("aud-t3-route@example.com")
    dep_id = _deploy(t)
    other = _tenant("aud-t3-other@example.com")
    _flags(ui_v2=True, why_no_trade=False)
    try:
        assert client.get(f"/api/deployments/{dep_id}/why", headers=t["headers"]).status_code == 404
        _flags(ui_v2=True, why_no_trade=True)

        async def seed():
            async with _session_factory() as session:
                dep = await session.get(StrategyDeploymentRecord, dep_id)
                await why.record(session, dep, "stale_data", None, "prices are 6 minutes late", NOW)
                await session.commit()
        _run(seed())
        ok = client.get(f"/api/deployments/{dep_id}/why", headers=t["headers"])
        assert ok.status_code == 200
        assert ok.json()["rows"][0]["code"] == "stale_data" and "late" in ok.json()["rows"][0]["label"]
        assert client.get(f"/api/deployments/{dep_id}/why", headers=other["headers"]).status_code == 404
    finally:
        _flags(ui_v2=False, why_no_trade=False)
