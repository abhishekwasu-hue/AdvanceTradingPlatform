"""TM94-3 (owner, 23 Oct): the Phase W option-chain recorder (`option_chain_snapshots`) follows the TM-7 rule of the OI
collector - every capture is read through ONE organisation's own broker session and stored as that organisation's rows;
every reader takes the caller's tenant; rows recorded platform-wide before the fix are read only by the confirmed owner.
No network: fake chains and fake brokers."""
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.backtest import chain_recorder
from app.brokers.models import OptionChain, OptionChainRow
from app.core import config
from app.db.models import OptionChainSnapshotRecord, StrategyDeploymentRecord, Tenant
from tests.test_auth_api import _session_factory, client  # noqa: F401 - fixtures
from tests.test_trading_worker import OPEN_NOW, _FakeBroker, _run, _tenant, _worker

EXPIRY = "2026-11-03"
SPAN = {"start": "2026-11-02T00:00:00Z", "end": "2026-11-03T00:00:00Z"}
AT = datetime(2026, 11, 2, 5, 0, tzinfo=timezone.utc)


def _chain(ltp: float, underlying: str = "TMWIDX") -> OptionChain:
    return OptionChain(underlying=underlying, expiry=EXPIRY, underlying_ltp=20000.0,
                       rows=[OptionChainRow(strike=20000.0, call_ltp=ltp, put_ltp=ltp + 1, call_oi=10, put_oi=20, call_iv=12.0)])


def _record(tenant_id, ltp, at=AT, underlying="TMWIDX"):
    async def go():
        async with _session_factory() as session:
            return await chain_recorder.record_chain(session, underlying, _chain(ltp, underlying), at, tenant_id=tenant_id, interval_minutes=5)
    return _run(go())


def _legacy_row(underlying, ltp, at=AT):
    """A row as the release before TM94-3 stored it: platform-wide, no tenant."""
    async def go():
        async with _session_factory() as session:
            session.add(OptionChainSnapshotRecord(underlying=underlying, expiry=date(2026, 11, 3), captured_at=at, strike=20000.0, right="CE",
                                                  ltp=ltp, source="worker"))
            await session.commit()
    _run(go())


def _load(tenant_id, underlying="TMWIDX"):
    async def go():
        async with _session_factory() as session:
            rows = await chain_recorder.load_rows(session, underlying, AT - timedelta(hours=1), AT + timedelta(hours=1), tenant_id=tenant_id)
            cov = await chain_recorder.coverage(session, underlying, tenant_id=tenant_id)
            return sorted(r.ltp for r in rows), cov
    return _run(go())


def _tenant_name(tenant_id):
    async def go():
        async with _session_factory() as session:
            return (await session.get(Tenant, tenant_id)).name
    return _run(go())


def test_each_organisation_records_and_reads_only_its_own_rows():
    a = _tenant("tm943-a@example.com", with_credentials=False)
    b = _tenant("tm943-b@example.com", with_credentials=False)
    # Same underlying, same instant: before the fix the second organisation's capture was skipped (the first one's
    # platform-wide rows stood in for it); now each keeps its own capture.
    assert _record(a["tenant_id"], 10.0) == 2
    assert _record(b["tenant_id"], 50.0) == 2
    assert _record(a["tenant_id"], 11.0, AT + timedelta(minutes=2)) == 0          # within A's own interval
    assert _load(a["tenant_id"])[0] == [10.0, 11.0]
    assert _load(b["tenant_id"])[0] == [50.0, 51.0]
    assert _load(a["tenant_id"])[1][0]["rows"] == 2
    # The read API: each organisation sees its own rows only.
    snaps_a = client.get("/api/backtest/option-chain/snapshots", headers=a["headers"], params={"underlying": "TMWIDX", **SPAN})
    snaps_b = client.get("/api/backtest/option-chain/snapshots", headers=b["headers"], params={"underlying": "TMWIDX", **SPAN})
    assert snaps_a.status_code == 200 and sorted(r["ltp"] for r in snaps_a.json()) == [10.0, 11.0]
    assert sorted(r["ltp"] for r in snaps_b.json()) == [50.0, 51.0]
    cov = client.get("/api/backtest/option-chain/coverage", headers=b["headers"], params={"underlying": "TMWIDX"}).json()
    assert cov == [{"underlying": "TMWIDX", "from": cov[0]["from"], "to": cov[0]["to"], "rows": 2, "expiries": 1}]


def test_a_third_organisation_without_rows_reads_nothing_and_no_tenant_reads_nothing():
    a = _tenant("tm943-c@example.com", with_credentials=False)
    other = _tenant("tm943-d@example.com", with_credentials=False)
    _record(a["tenant_id"], 7.0, underlying="TMWIDX2")
    assert _load(other["tenant_id"], "TMWIDX2") == ([], [])
    assert _load(None, "TMWIDX2") == ([], [])
    with pytest.raises(PermissionError):
        _record(None, 1.0, underlying="TMWIDX2")
    assert client.get("/api/backtest/option-chain/snapshots", headers=other["headers"],
                      params={"underlying": "TMWIDX2", **SPAN}).json() == []


def test_pre_fix_rows_are_read_only_by_the_confirmed_owner(monkeypatch):
    owner = _tenant("tm943-owner@example.com", with_credentials=False)
    other = _tenant("tm943-other@example.com", with_credentials=False)
    _legacy_row("TMWLEG", 3.0)
    monkeypatch.setattr(config, "HISTORY_OWNER_TENANT_ID", None)
    monkeypatch.setattr(config, "HISTORY_OWNER_TENANT_NAME", "")
    assert _load(owner["tenant_id"], "TMWLEG")[0] == [] and _load(other["tenant_id"], "TMWLEG")[0] == []   # no owner: nobody
    monkeypatch.setattr(config, "HISTORY_OWNER_TENANT_ID", owner["tenant_id"])
    monkeypatch.setattr(config, "HISTORY_OWNER_TENANT_NAME", "not the name")
    assert _load(owner["tenant_id"], "TMWLEG")[0] == []                                                     # unconfirmed id
    monkeypatch.setattr(config, "HISTORY_OWNER_TENANT_NAME", _tenant_name(owner["tenant_id"]))
    assert _load(owner["tenant_id"], "TMWLEG")[0] == [3.0]
    assert _load(other["tenant_id"], "TMWLEG")[0] == []
    # The owner's own new capture sits beside the pre-fix row; a pre-fix row never stops the owner's capture.
    assert _record(owner["tenant_id"], 4.0, underlying="TMWLEG") == 2
    assert _load(owner["tenant_id"], "TMWLEG")[0] == [3.0, 4.0, 5.0]
    snaps = client.get("/api/backtest/option-chain/snapshots", headers=other["headers"], params={"underlying": "TMWLEG", **SPAN})
    assert snaps.json() == []


def test_uploads_stay_with_the_uploader_and_carry_its_tenant():
    a = _tenant("tm943-up-a@example.com", with_credentials=False)
    b = _tenant("tm943-up-b@example.com", with_credentials=False)
    _record(b["tenant_id"], 60.0, underlying="TMWUP")
    row = {"timestamp": AT.isoformat(), "expiry": "2026-11-03", "strike": 20000.0, "right": "CE", "ltp": 60.0}
    # B's recorded row with the same contract and time is not "a duplicate" for A.
    up = client.post("/api/backtest/option-chain/snapshots", headers=a["headers"], json={"underlying": "TMWUP", "rows": [row]})
    assert up.status_code == 200 and up.json()["written"] == 1

    async def stored():
        async with _session_factory() as session:
            return list(await session.scalars(select(OptionChainSnapshotRecord).where(OptionChainSnapshotRecord.underlying == "TMWUP",
                                                                                      OptionChainSnapshotRecord.source.like("upload%"))))
    rec = _run(stored())
    assert [(r.tenant_id, r.source) for r in rec] == [(a["tenant_id"], f"upload:{a['tenant_id']}")]
    assert _load(a["tenant_id"], "TMWUP")[0] == [60.0]
    assert _load(b["tenant_id"], "TMWUP")[0] == [60.0, 61.0]


class _ChainBroker(_FakeBroker):
    def __init__(self, ltp):
        super().__init__()
        self.chain_ltp = ltp
        self.chain_calls = []

    async def get_option_chain(self, underlying, expiry=None):
        self.chain_calls.append(underlying)
        return _chain(self.chain_ltp, "NIFTY 50")


def _option_dep(t, symbol="NIFTY 50"):
    async def go():
        async with _session_factory() as session:
            dep = StrategyDeploymentRecord(tenant_id=t["tenant_id"], strategy_id="ema_rsi_scalper_1m", symbol=symbol, exchange="NSE",
                                           timeframe="1min", mode="PAPER", broker_name="upstox", status="ACTIVE", created_by=t["user_id"],
                                           instrument_kind="OPTION", option_strategy="BULL_PUT_SPREAD")
            session.add(dep)
            await session.commit()
            return dep
    return _run(go())


def test_the_worker_records_each_organisation_through_its_own_session_only(monkeypatch):
    """Never another user's token: A's chain is read through A's broker, B's through B's, and C (no usable session)
    records nothing - its underlying is never read through A's or B's session."""
    from app.brokers import token_lifecycle
    from app.workers import trading_worker as tw

    a = _tenant("tm943-w-a@example.com")
    b = _tenant("tm943-w-b@example.com")
    c = _tenant("tm943-w-c@example.com", with_credentials=False)
    brokers = {a["tenant_id"]: _ChainBroker(100.0), b["tenant_id"]: _ChainBroker(200.0)}
    worker = _worker(monkeypatch, _ChainBroker(999.0))
    monkeypatch.setattr(tw, "build_adapter", lambda record, client=None: brokers[record.tenant_id])
    monkeypatch.setattr(token_lifecycle, "build_adapter", lambda record, client=None: brokers[record.tenant_id])

    async def no_eval(*args, **kwargs):
        return False
    monkeypatch.setattr(worker, "_evaluate_deployment", no_eval)
    for t in (a, b, c):
        _option_dep(t)
    now = OPEN_NOW + timedelta(days=-7)          # a mid-session instant no other test records NIFTY at
    report = _run(worker.run_cycle(now=now))
    assert report.chain_rows_recorded == 4
    assert brokers[a["tenant_id"]].chain_calls == ["NIFTY 50"] and brokers[b["tenant_id"]].chain_calls == ["NIFTY 50"]

    async def rows():
        async with _session_factory() as session:
            out = {}
            for t in (a, b, c):
                got = await chain_recorder.load_rows(session, "NIFTY", now - timedelta(minutes=1), now + timedelta(minutes=1), tenant_id=t["tenant_id"])
                out[t["tenant_id"]] = sorted(r.ltp for r in got)
            return out
    got = _run(rows())
    assert got == {a["tenant_id"]: [100.0, 101.0], b["tenant_id"]: [200.0, 201.0], c["tenant_id"]: []}
    # The interval is per organisation: a minute later neither re-reads.
    _run(worker.run_cycle(now=now + timedelta(minutes=1)))
    assert len(brokers[a["tenant_id"]].chain_calls) == 1 and len(brokers[b["tenant_id"]].chain_calls) == 1


def test_record_chains_skips_another_organisations_deployment():
    from app.market_data.service import MarketDataService
    from app.workers.trading_worker import TradingWorker

    a = _tenant("tm943-skip-a@example.com", with_credentials=False)
    b = _tenant("tm943-skip-b@example.com", with_credentials=False)
    dep_b = _option_dep(b)
    broker = _ChainBroker(5.0)
    worker = TradingWorker(_session_factory, cycle_seconds=60)
    now = OPEN_NOW.astimezone(timezone.utc) + timedelta(days=-8)

    async def go():
        async with _session_factory() as session:
            return await worker._record_chains(session, [dep_b], MarketDataService(broker), now, {"NSE"}, tenant_id=a["tenant_id"])
    assert _run(go()) == 0 and broker.chain_calls == []


def test_the_migration_is_additive_rollback_safe_and_the_single_head():
    import pathlib
    import runpy

    from alembic.script import ScriptDirectory
    backend = pathlib.Path(__file__).resolve().parents[1]
    path = backend / "alembic" / "versions" / "3c5e7a9b1d24_tm94_3_phase_w_recorder_tenant.py"
    module = runpy.run_path(str(path), run_name="tm94_3_migration")
    assert module["ROLLBACK_SAFE"] is True and module["down_revision"] == "250d796123b7"
    upgrade = path.read_text().split("def downgrade")[0]
    assert "nullable=True" in upgrade and "drop_" not in upgrade and "execute" not in upgrade   # additive, no row rewrite
    script = ScriptDirectory(str(backend / "alembic"))
    heads = script.get_heads()
    assert len(heads) == 1
    assert "3c5e7a9b1d24" in {rev.revision for rev in script.walk_revisions(base="base", head=heads[0])}
    column = OptionChainSnapshotRecord.__table__.c.tenant_id
    assert column.nullable and {fk.column.table.name for fk in column.foreign_keys} == {"tenants"}
