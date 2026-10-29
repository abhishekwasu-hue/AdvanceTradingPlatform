"""AUD-G2.1: N+1 profile of the top list endpoints - positions, orders, the option chain (strikes and the dashboard
banners), a screener run's evidence and the journal week. Each test seeds 1 row, counts the SQL statements of one read,
seeds up to 20 rows and counts again: the count must not grow with the rows (tests/sql_counter.py). The counts found
are recorded in docs/design/AUDIT_28OCT.md ("G2.1 N+1 profile").
"""
import asyncio
import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, List

from app.db.models import OrderRecord, ScreenEvidenceRecord, ScreenRunRecord, TradeRecord
from app.platform import controls
from tests.sql_counter import count_queries
from tests.test_auth_api import _engine, _session_factory, client
from tests.test_phase_l_ai import _owner

SMALL, LARGE = 1, 20


def _run(coro):
    return asyncio.run(coro)


def _add(rows: List) -> None:
    async def go():
        async with _session_factory() as session:
            session.add_all(rows)
            await session.commit()
    _run(go())


def _set_flags(**flags: bool) -> None:
    async def go():
        async with _session_factory() as session:
            current = await controls._get(session, controls.KEY_FEATURE_FLAGS)
            stored = dict(current.get("flags") or {})
            for name, on in flags.items():
                stored[name] = {"on": on, "tenants": []}
            await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": stored}, None)
            await session.commit()
    _run(go())


def _profile(path: str, headers: Dict[str, str], seed: Callable[[int, int], None], check: Callable[[dict, int], None]) -> Dict[int, int]:
    """{rows: statements} for one read at SMALL and at LARGE rows (a warm-up read first: per-process caches settle)."""
    counts: Dict[int, int] = {}
    have = 0
    client.get(path, headers=headers)
    for total in (SMALL, LARGE):
        seed(have, total - have)
        have = total
        client.get(path, headers=headers)              # a read that writes its first time (the journal) is not the profile
        with count_queries(_engine) as counter:
            response = client.get(path, headers=headers)
        assert response.status_code == 200, response.text
        check(response.json(), total)
        counts[total] = counter.count
    assert counts[LARGE] == counts[SMALL], f"{path}: {counts} statements grow with rows:\n" + "\n".join(counter.statements)
    return counts


def test_positions_list_does_not_grow_with_rows():
    headers, me = _owner("g21-positions@example.com")
    now = datetime.now(timezone.utc)

    def seed(start, n):
        _add([TradeRecord(tenant_id=me["tenant_id"], user_id=me["id"], mode="PAPER", symbol=f"SYM{start + i}", strategy_id="s",
                          direction="LONG", entry_time=now - timedelta(minutes=start + i), entry_price=100.0, quantity=1.0,
                          stop_loss=99.0) for i in range(n)])

    def check(body, n):
        assert len(body) == n
    _profile("/api/positions", headers, seed, check)


def test_orders_list_does_not_grow_with_rows():
    headers, me = _owner("g21-orders@example.com")

    def seed(start, n):
        _add([OrderRecord(tenant_id=me["tenant_id"], user_id=me["id"], mode="PAPER", strategy_id="s", symbol=f"SYM{start + i}",
                          direction="LONG", quantity=1.0, status="FILLED") for i in range(n)])

    def check(body, n):
        assert len(body) == n
    _profile("/api/orders", headers, seed, check)


def test_option_chain_strikes_do_not_grow_with_stored_slots():
    from tests.test_o2_oi_snapshots import DAY, T0, UND, _chain, _collect
    headers, me = _owner("g21-chain@example.com")

    def seed(start, n):
        for i in range(start, start + n):
            _run(_collect(_chain(put=2000.0 + i), T0 + timedelta(minutes=5 * i), tenant_id=me["tenant_id"]))

    def check(body, n):
        assert body["strikes"] and body["data_as_of"]
    try:
        _profile(f"/api/option-chain/{UND}/strikes?date={DAY.isoformat()}", headers, seed, check)
    finally:
        _run(_forget_oi_rows(me["tenant_id"]))


def test_option_chain_banners_do_not_grow_with_enabled_underlyings():
    """The dashboard banners read every enabled underlying in one batch; each entry stays exactly what the one-underlying
    banner read answers (the same settings, slots, staleness and market flags)."""
    from app.option_chain import snapshots
    from tests.test_o2_oi_snapshots import _chain
    headers, me = _owner("g21-banners@example.com")
    now = datetime.now(timezone.utc)

    async def collect(name, put):
        async with _session_factory() as session:
            await snapshots.collect(session, name, _chain(put=put), now, tenant_id=me["tenant_id"])
            await session.commit()

    def seed(start, n):
        for i in range(start, start + n):
            name = f"G21IDX{i:02d}"
            assert client.put(f"/api/option-chain/{name}/settings", json={"enabled": True}, headers=headers).status_code == 200
            if i % 2 == 0:                                   # every other underlying has a slot today, the rest none yet
                _run(collect(name, 2000.0 + i))

    def check(body, n):
        assert len(body["banners"]) == n
        for entry in body["banners"]:
            single = client.get(f"/api/option-chain/{entry['underlying']}/banner", headers=headers).json()
            assert {k: v for k, v in entry.items() if k != "age_minutes"} == {k: v for k, v in single.items() if k != "age_minutes"}
        assert body["banners"][0]["banner"] is not None           # G21IDX00 has a slot today
    try:
        _profile("/api/option-chain/banners", headers, seed, check)
    finally:                                   # the worker's OI collector must not follow these in later test modules
        _run(_forget_oi_rows(me["tenant_id"]))


async def _forget_oi_rows(tenant_id: int) -> None:
    from sqlalchemy import delete, select

    from app.db.models import OIBannerSettingRecord, OIDayBaselineRecord, OISnapshotRecord, StrikeOISnapshotRecord
    async with _session_factory() as session:
        snap_ids = select(OISnapshotRecord.id).where(OISnapshotRecord.tenant_id == tenant_id)
        await session.execute(delete(StrikeOISnapshotRecord).where(StrikeOISnapshotRecord.snapshot_id.in_(snap_ids)))
        for model in (OISnapshotRecord, OIDayBaselineRecord, OIBannerSettingRecord):
            await session.execute(delete(model).where(model.tenant_id == tenant_id))
        await session.commit()


def test_screener_run_evidence_does_not_grow_with_rows():
    headers, me = _owner("g21-screener@example.com")
    _set_flags(screener_v2=True, evidence_charts=True, byob_data_tier=False)
    try:
        run = ScreenRunRecord(tenant_id=me["tenant_id"], user_id=me["id"], ast_sha256="0" * 64, ast_version="1", base_tf="1d",
                              universe_json="[]", data_source="lake")
        _add([run])
        now = datetime.now(timezone.utc)

        def seed(start, n):
            rows = []
            for i in range(start, start + n):
                pack = json.dumps({"symbol": f"SYM{i}"})
                rows.append(ScreenEvidenceRecord(tenant_id=me["tenant_id"], user_id=me["id"], run_id=run.id, symbol=f"SYM{i:02d}", base_tf="1d",
                                                 bar_time=now, pack_json=pack, pack_sha256=hashlib.sha256(pack.encode()).hexdigest()))
            _add(rows)

        def check(body, n):
            assert len(body["evidence"]) == n
        _profile(f"/api/screener/evidence/runs/{run.id}", headers, seed, check)
    finally:
        _set_flags(screener_v2=False, evidence_charts=False)


def test_journal_week_does_not_grow_with_trades():
    from app.journal import capture, service
    from app.market_data.calendar import IST
    headers, me = _owner("g21-journal@example.com")
    _set_flags(discipline_engine=True)
    capture.reset_flag_cache()
    try:
        _, start, _ = service.week_bounds(None)
        monday = start.astimezone(IST)

        def seed(begin, n):
            _add([TradeRecord(tenant_id=me["tenant_id"], user_id=me["id"], mode="PAPER", symbol="NIFTY", strategy_id="setupA",
                              direction="LONG", entry_time=(monday + timedelta(hours=10, minutes=3 * (begin + i))).astimezone(timezone.utc),
                              entry_price=100.0, quantity=1.0, stop_loss=98.0, initial_stop_loss=98.0) for i in range(n)])

        def check(body, n):
            assert len(body["entries"]) == n
        _profile("/api/journal/week", headers, seed, check)
    finally:
        _set_flags(discipline_engine=False)
        capture.reset_flag_cache()
