"""OI Banner O2: the snapshot collector (idempotent per slot, day baselines), the replay with each tenant's settings,
the history API, stale/closed flags, settings, and the worker hook. Fixture chains only; no broker, no clock."""
import asyncio
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, func, select

from app.brokers.models import OptionChain, OptionChainRow
from app.db.models import OIBannerSettingRecord, OIDayBaselineRecord, OISnapshotRecord, StrikeOISnapshotRecord, User
from app.option_chain import oi_regime, snapshots
from app.option_chain.oi_regime import Direction, OIClass
from tests.test_auth_api import _session_factory, client  # noqa: F401 - fixtures
from tests.test_phase_l_ai import _owner

UND = "TESTIDX"
# A fixed session morning (IST 09:20 = 03:50 UTC); every test passes its own clock.
T0 = datetime(2026, 3, 10, 3, 50, tzinfo=timezone.utc)
DAY = date(2026, 3, 10)


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


def _chain(spot=24510.0, step=50.0, n=41, call=1000.0, put=2000.0, call_ltp=10.0, put_ltp=10.0, expiry="2026-03-26", bump=None):
    centre = round(spot / step) * step
    rows = []
    for i in range(n):
        k = centre + (i - n // 2) * step
        c, p = (bump(k) if bump else (call, put))
        rows.append(OptionChainRow(strike=k, call_oi=c, put_oi=p, call_ltp=call_ltp, put_ltp=put_ltp, call_iv=12.0, put_iv=13.0))
    return OptionChain(underlying=UND, expiry=expiry, underlying_ltp=spot, rows=rows)


async def _collect(chain, when, **kw):
    async with _session_factory() as session:
        result = await snapshots.collect(session, UND, chain, when, **kw)
        await session.commit()
        return result


async def _count(model):
    async with _session_factory() as session:
        return int(await session.scalar(select(func.count()).select_from(model)))


def test_collect_is_idempotent_per_slot_and_keeps_a_wide_window():
    first = _run(_collect(_chain(), T0, span=15))
    assert first.status == "OK" and first.strikes == 31 and first.baselines == 31
    assert first.slot_start == datetime(2026, 3, 10, 3, 50, tzinfo=timezone.utc)
    again = _run(_collect(_chain(call=5000.0), T0 + timedelta(minutes=3), span=15))      # same 5-minute slot
    assert again.status == "ALREADY_EXISTS"
    assert _run(_count(OISnapshotRecord)) == 1 and _run(_count(StrikeOISnapshotRecord)) == 31
    nxt = _run(_collect(_chain(call=5000.0), T0 + timedelta(minutes=5), span=15))
    assert nxt.status == "OK" and nxt.baselines == 0                                     # the day's baseline is written once

    async def base():
        async with _session_factory() as session:
            return await snapshots.baselines(session, UND, DAY)
    assert set(v[0] for v in _run(base()).values()) == {1000.0}                         # the first OI seen, never updated
    assert _run(_collect(OptionChain(underlying=UND, expiry="", underlying_ltp=None, rows=_chain().rows), T0 + timedelta(minutes=10))).status == "EMPTY"
    with pytest.raises(ValueError):
        snapshots.normalise_underlying("BAD;SYMBOL")


async def _replay(settings=None, day=DAY):
    async with _session_factory() as session:
        slots = await snapshots.day_chains(session, UND, day)
    return snapshots.replay(UND, slots, settings or oi_regime.OIRegimeSettings(), day), slots


def test_replay_rebuilds_the_banner_from_stored_strikes():
    _run(_collect(_chain(), T0))
    _run(_collect(_chain(put=2200.0, put_ltp=9.5), T0 + timedelta(minutes=5)))         # put OI up, put premium down
    timeline, slots = _run(_replay())
    assert len(timeline) == 2 and len(slots[0].chain.rows) == 31
    first, second = timeline[0].state, timeline[1].state
    assert first.first_of_day and first.message == oi_regime.INSUFFICIENT_HISTORY_MESSAGE
    assert second.put_class == OIClass.WRITING and second.direction == Direction.BULLISH and f"{UND} bias bullish" in second.message
    assert second.dte == 16 and timeline[1].totals.total_put_oi == 13 * 2200.0                # ATM +- 6 strikes
    assert first.max_pain is not None
    row = snapshots.entry_json(timeline[1])
    assert row["data_as_of"].startswith("2026-03-10T03:55") and row["slot"].startswith("2026-03-10T09:25")


def test_each_tenant_settings_apply_on_read():
    _run(_collect(_chain(), T0))
    _run(_collect(_chain(put=2030.0, put_ltp=9.95), T0 + timedelta(minutes=5)))       # +1.5 % OI, -0.5 % premium
    default, _ = _run(_replay())
    assert default[1].state.put_class == OIClass.FLAT                                    # under the 2 % / 1 % defaults
    loose, _ = _run(_replay(oi_regime.OIRegimeSettings(oi_threshold_pct=1.0, premium_threshold_pct=0.25)))
    assert loose[1].state.put_class == OIClass.WRITING
    narrow, _ = _run(_replay(oi_regime.OIRegimeSettings(atm_range=2)))
    assert narrow[1].totals.total_call_oi == 5 * 1000.0                                  # the reader's ATM range


def test_staleness_flags():
    s = oi_regime.OIRegimeSettings(stale_after_minutes=15)
    assert snapshots.staleness(T0, T0 + timedelta(minutes=20), s, market_open=True)["stale"] is True
    assert snapshots.staleness(T0, T0 + timedelta(minutes=5), s, market_open=True)["stale"] is False
    closed = snapshots.staleness(T0, T0 + timedelta(hours=8), s, market_open=False)
    assert closed["stale"] is False and closed["market_open"] is False and closed["age_minutes"] == 480.0
    assert snapshots.staleness(None, T0, s, market_open=True)["stale"] is True             # nothing collected yet


def test_history_api_intervals_strikes_and_banner():
    headers, _ = _owner("o2-history@example.com")
    for m, put in ((0, 2000.0), (5, 2100.0), (10, 2200.0), (15, 2300.0)):
        _run(_collect(_chain(put=put), T0 + timedelta(minutes=m)))
    five = client.get(f"/api/option-chain/{UND}/history", params={"date": DAY.isoformat()}, headers=headers).json()
    assert len(five["rows"]) == 4 and five["rows"][0]["slot"].startswith("2026-03-10T09:35") and "stale" in five and "market_open" in five
    ten = client.get(f"/api/option-chain/{UND}/history", params={"date": DAY.isoformat(), "interval": 10}, headers=headers).json()
    assert [r["total_put_oi"] for r in ten["rows"]] == [13 * 2300.0, 13 * 2100.0]        # the last value per bucket
    assert client.get(f"/api/option-chain/{UND}/history", params={"interval": 7}, headers=headers).status_code == 422
    strikes = client.get(f"/api/option-chain/{UND}/strikes", params={"date": DAY.isoformat()}, headers=headers).json()
    assert len(strikes["strikes"]) == 13 and len(strikes["strikes"][0]["points"]) == 4 and strikes["strikes"][0]["baseline_put_oi"] == 2000.0
    empty = client.get("/api/option-chain/NOTHING/banner", headers=headers).json()
    assert empty["banner"] is None and empty["message"] == oi_regime.INSUFFICIENT_HISTORY_MESSAGE
    assert client.get("/api/option-chain/BAD;X/banner", headers=headers).status_code in (404, 422)
    assert client.get(f"/api/option-chain/{UND}/history").status_code == 401


def test_settings_are_per_tenant_validated_and_owner_only():
    headers, me = _owner("o2-settings@example.com")
    other_headers, _ = _owner("o2-settings-other@example.com")
    r = client.put(f"/api/option-chain/{UND}/settings", json={"enabled": True, "overrides": {"confirm_count": 4}}, headers=headers)
    assert r.status_code == 200 and r.json()["enabled"] is True and r.json()["effective"]["confirm_count"] == 4
    assert client.put("/api/option-chain/*/settings", json={"overrides": {"atm_range": 3}, "enabled": True}, headers=headers).json()["enabled"] is False
    got = client.get(f"/api/option-chain/{UND}/settings", headers=headers).json()
    assert got["effective"]["atm_range"] == 3 and got["effective"]["confirm_count"] == 4               # "*" then the underlying
    assert client.get(f"/api/option-chain/{UND}/settings", headers=other_headers).json()["effective"]["confirm_count"] == 3
    bad = client.put(f"/api/option-chain/{UND}/settings", json={"overrides": {"no_such": 1}}, headers=headers)
    assert bad.status_code == 422
    assert client.put(f"/api/option-chain/{UND}/settings", json={"overrides": {"pcr_bands": {"oversold_below": 2.0}}}, headers=headers).status_code == 422

    async def demote():
        async with _session_factory() as session:
            user = await session.get(User, me["id"])
            user.role = "USER"
            await session.commit()
    _run(demote())
    assert client.put(f"/api/option-chain/{UND}/settings", json={"enabled": False}, headers=headers).status_code == 403

    async def enabled():
        async with _session_factory() as session:
            return await snapshots.enabled_underlyings(session)
    assert [(u, e) for _, u, e in _run(enabled())] == [(UND, "NSE")]


class _Broker:
    name = "fake"

    def __init__(self):
        self.calls = []

    async def get_option_chain(self, symbol, expiry=None):
        self.calls.append(symbol)
        return _chain()


def test_worker_collects_once_per_slot_only_while_open(monkeypatch):
    from app.workers import trading_worker as tw
    headers, me = _owner("o2-worker@example.com")
    assert client.put(f"/api/option-chain/{UND}/settings", json={"enabled": True}, headers=headers).status_code == 200
    worker = tw.TradingWorker(_session_factory, cycle_seconds=60)
    broker = _Broker()
    is_open = {"v": True}

    async def fake_status(session, now, exchange="NSE"):
        return SimpleNamespace(is_open=is_open["v"])

    async def fake_broker(session, tenant_id, now):
        return broker
    monkeypatch.setattr(tw, "market_session_status", fake_status)
    monkeypatch.setattr(worker, "_first_usable_broker", fake_broker)

    async def cycle(when):
        async with _session_factory() as session:
            return await worker._oi_banner(session, when)
    assert _run(cycle(T0)) == 1
    assert _run(cycle(T0 + timedelta(minutes=2))) == 0                                    # same slot: no second read
    is_open["v"] = False
    assert _run(cycle(T0 + timedelta(minutes=5))) == 0                                    # venue closed
    is_open["v"] = True
    assert _run(cycle(T0 + timedelta(minutes=10))) == 1
    assert broker.calls == [UND, UND] and _run(_count(OISnapshotRecord)) == 2


def test_retention_trims_old_oi_rows_only():
    import dataclasses
    from app.retention import service as retention
    from app.retention.policy import load_policy
    _run(_collect(_chain(), T0))
    _run(_collect(_chain(), T0 + timedelta(days=500)))

    async def go(now):
        async with _session_factory() as session:
            return await retention.run_retention(session, now=now, policy=dataclasses.replace(load_policy(), enabled=True, oi_snapshots_days=400))
    report = _run(go(T0 + timedelta(days=500, minutes=1)))
    assert report.deleted["oi_snapshots"] == 1 and report.deleted["strike_oi_snapshots"] == 31 and report.deleted["oi_day_baselines"] == 31
    assert _run(_count(OISnapshotRecord)) == 1 and _run(_count(StrikeOISnapshotRecord)) == 31


def test_worker_tries_the_next_tenant_when_a_read_fails(monkeypatch):
    from app.workers import trading_worker as tw
    first_headers, first = _owner("o2-fail-a@example.com")
    second_headers, second = _owner("o2-fail-b@example.com")
    for h in (first_headers, second_headers):
        assert client.put(f"/api/option-chain/{UND}/settings", json={"enabled": True}, headers=h).status_code == 200
    worker = tw.TradingWorker(_session_factory, cycle_seconds=60)

    class _Failing(_Broker):
        async def get_option_chain(self, symbol, expiry=None):
            raise RuntimeError("broker down")
    good = _Broker()

    async def fake_status(session, now, exchange="NSE"):
        return SimpleNamespace(is_open=True)

    async def fake_broker(session, tenant_id, now):
        return _Failing() if tenant_id == first["tenant_id"] else good
    monkeypatch.setattr(tw, "market_session_status", fake_status)
    monkeypatch.setattr(worker, "_first_usable_broker", fake_broker)

    async def cycle():
        async with _session_factory() as session:
            return await worker._oi_banner(session, T0)
    assert _run(cycle()) == 1 and good.calls == [UND]


def test_dashboard_banners_list_only_this_tenants_enabled_underlyings():
    headers, _ = _owner("o3-banners@example.com")
    other_headers, _ = _owner("o3-banners-other@example.com")
    assert client.put(f"/api/option-chain/{UND}/settings", json={"enabled": True}, headers=headers).status_code == 200
    assert client.put("/api/option-chain/OTHERIDX/settings", json={"enabled": True}, headers=other_headers).status_code == 200
    _run(_collect(_chain(), datetime.now(timezone.utc)))
    mine = client.get("/api/option-chain/banners", headers=headers).json()["banners"]
    assert [b["underlying"] for b in mine] == [UND] and mine[0]["banner"]["first_of_day"] is True
    assert [b["underlying"] for b in client.get("/api/option-chain/banners", headers=other_headers).json()["banners"]] == ["OTHERIDX"]
