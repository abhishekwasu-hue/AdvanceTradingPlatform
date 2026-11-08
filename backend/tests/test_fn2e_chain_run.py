"""FN2-e: PCR / ChainBias / MaxPainDistancePct / FuturesOI on the screener run and bar-close alert paths, from the STORED
option-chain snapshots (the organisation's OI collector slots; with the own-broker tier, the user's own reads) - never a
live chain call. A bar sees only a snapshot read at or before its close (no look-ahead); a symbol with no snapshot, or
FuturesOI with no future in it, is "not evaluated" with the reason, never a pass or a 0. Made-up underlyings only."""
import asyncio
import types
from datetime import UTC, date, datetime, timedelta

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import delete, select

from app.brokers.models import OptionChain, OptionChainRow
from app.db.models import AlertEventRecord, MdTenantChainSnapshotRecord, OIDayBaselineRecord, OISnapshotRecord, StrikeOISnapshotRecord
from app.option_chain import snapshots
from app.screener import chains as C
from app.screener import compile_screen
from app.screener.parser import parse
from app.screener.routes import missing_data
from app.screener.runtime import SymbolData, evaluate, run_screen
from tests.test_auth_api import _session_factory, client  # noqa: F401 - fixtures
from tests.test_phase_l_ai import _owner
from tests.test_s1d_screener_api import _daily_bars, _flag
from tests.test_s4a_barclose import TUE, FakeFetch, _bars, _evaluate, _rule, ist

IDX, NONE_ = "FN2EIDX", "FN2ENONE"
# the fixture's daily bars run 2026-01-01 .. 2026-03-01; the last one closes 2026-03-01 15:30 IST (10:00 UTC)
LAST_CLOSE = datetime(2026, 3, 1, 10, 0, tzinfo=UTC)


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _clean():
    async def go():
        async with _session_factory() as session:
            ids = list(await session.scalars(select(OISnapshotRecord.id).where(OISnapshotRecord.underlying.like("FN2E%"))))
            if ids:
                await session.execute(delete(StrikeOISnapshotRecord).where(StrikeOISnapshotRecord.snapshot_id.in_(ids)))
            for model in (OISnapshotRecord, OIDayBaselineRecord, MdTenantChainSnapshotRecord):
                await session.execute(delete(model).where(model.underlying.like("FN2E%")))
            await session.commit()
    _run(go())
    yield


def _chain(put, call=1000.0, spot=100.0, expiry="2026-03-26", underlying=IDX):
    rows = [OptionChainRow(strike=k, call_oi=call, put_oi=put, call_ltp=1.0, put_ltp=1.0) for k in (90.0, 95.0, 100.0, 105.0, 110.0)]
    return OptionChain(underlying=underlying, expiry=expiry, underlying_ltp=spot, rows=rows)


def _collect(tenant_id, chain, when, future_oi=None, underlying=IDX, slot_minutes=None):
    async def go():
        async with _session_factory() as session:
            await snapshots.collect(session, underlying, chain, when, tenant_id=tenant_id, future_oi=future_oi, slot_minutes=slot_minutes)
            await session.commit()
    _run(go())


def _broker(monkeypatch):
    from app.brokers import token_lifecycle
    from app.market_data import candles_routes, service

    async def pick(session, tenant_id, broker, label, **_k):
        return types.SimpleNamespace(broker_name="fake")

    async def candles(self, symbol, exchange="NSE", interval="1min", now=None):
        return _daily_bars(100, 1.0)
    monkeypatch.setattr(candles_routes, "_pick_record", pick)
    monkeypatch.setattr(token_lifecycle, "build_adapter", lambda record: types.SimpleNamespace(name="fake", native_intervals=frozenset({"day"})))
    monkeypatch.setattr(service.MarketDataService, "get_candles", candles)


@pytest.fixture
def flag_on():
    _flag(True)
    yield
    _flag(False)


def test_the_run_path_counts_the_chain_as_loaded_and_the_others_still_refuse():
    def miss(text, **kw):
        return missing_data(compile_screen(text, base_tf="1d")[0], **kw)
    text = 'PCR() > 1 AND ChainBias() != "CONFLICTING" AND MaxPainDistancePct() < 50 AND FuturesOI() > 0'
    assert miss(text, reference=True, chain=True) == []
    assert miss(text) == ["PCR", "ChainBias", "MaxPainDistancePct", "FuturesOI"]     # history / studies / library: refused as before
    assert C.calls(compile_screen(text, base_tf="1d")[0]) == ["PCR", "ChainBias", "MaxPainDistancePct", "FuturesOI"]


def test_a_run_reads_the_snapshot_at_or_before_the_last_close_and_explains_a_symbol_without_one(flag_on, monkeypatch):
    headers, me = _owner("fn2e-run@example.com")
    _broker(monkeypatch)
    _collect(me["tenant_id"], _chain(put=2000.0), LAST_CLOSE - timedelta(minutes=10), future_oi=12345.0)   # PCR 2, read before the close
    _collect(me["tenant_id"], _chain(put=500.0), LAST_CLOSE + timedelta(days=1))                           # PCR 0.5, read AFTER: never used

    def run(source):
        out = client.post("/api/screener/run", json={"source": source, "symbols": [IDX, NONE_], "base_tf": "1d"}, headers=headers)
        assert out.status_code == 200, out.text
        return out.json()
    body = run("PCR() > 1")
    assert body["matched"] == [IDX]
    rows = {r["symbol"]: r for r in body["results"]}
    assert rows[NONE_]["matched"] is False and "no stored option chain of FN2ENONE" in rows[NONE_]["reason"]
    assert rows[NONE_]["code"] == "not_checked" and body["unread"] == 1                    # not evaluated - never a pass or "no match"
    assert run("PCR() < 1")["matched"] == []                                               # the later snapshot (PCR 0.5) is never read
    assert run("FuturesOI() > 12000 AND FuturesOI() < 13000")["matched"] == [IDX]
    assert run('ChainBias() == "BULLISH" OR ChainBias() != "BULLISH"')["matched"] == [IDX]


def test_futures_oi_without_a_stored_future_is_not_evaluated_never_zero(flag_on, monkeypatch):
    headers, me = _owner("fn2e-fut@example.com")
    _broker(monkeypatch)
    _collect(me["tenant_id"], _chain(put=2000.0), LAST_CLOSE - timedelta(minutes=10))                     # no future quote in the slot
    _collect(me["tenant_id"], _chain(put=2000.0, underlying="FN2EFUT"), LAST_CLOSE - timedelta(minutes=10), future_oi=0.0, underlying="FN2EFUT")
    out = client.post("/api/screener/run", json={"source": "FuturesOI() >= 0", "symbols": [IDX, "FN2EFUT"], "base_tf": "1d"}, headers=headers)
    assert out.status_code == 200, out.text
    rows = {r["symbol"]: r for r in out.json()["results"]}
    assert rows[IDX]["matched"] is False and "no near-month future open interest" in rows[IDX]["reason"] and rows[IDX]["code"] == "not_checked"
    assert rows["FN2EFUT"]["matched"] is True                                              # a stored 0 is a real reading, kept as 0


def test_no_look_ahead_a_bar_sees_only_snapshots_read_at_or_before_its_close():
    idx = pd.date_range("2026-03-10 03:45", periods=4, freq="5min", tz="UTC")              # 09:15 .. 09:30 IST, closes 09:20 .. 09:35
    f = pd.DataFrame({"open": 1.0, "high": 1.0, "low": 1.0, "close": 100.0, "volume": 1.0}, index=idx)
    early = C.ChainPoint(idx[1].to_pydatetime() + timedelta(minutes=5), _chain(put=2000.0))              # read at bar 1's close
    late = C.ChainPoint(idx[2].to_pydatetime() + timedelta(minutes=6), _chain(put=500.0))                # one minute after bar 2 closed
    data = SymbolData(IDX, {"5m": f}, chains=C.ChainTimeline(IDX, C.SOURCE_ORG, [early, late]))
    got = evaluate(parse("PCR()"), data, base_tf="5m")
    assert np.isnan(got.iloc[0]) and got.iloc[1] == 2.0 and got.iloc[2] == 2.0 and got.iloc[3] == 0.5
    # appending a later snapshot never changes an earlier bar's value
    later = C.ChainPoint(idx[3].to_pydatetime() + timedelta(hours=1), _chain(put=9000.0))
    again = evaluate(parse("PCR()"), SymbolData(IDX, {"5m": f}, chains=C.ChainTimeline(IDX, C.SOURCE_ORG, [early, late, later])), base_tf="5m")
    pd.testing.assert_series_equal(got, again)
    # only a snapshot read after the decided bar: not evaluated, with the reason
    only_late = SymbolData(IDX, {"5m": f.iloc[:3]}, chains=C.ChainTimeline(IDX, C.SOURCE_ORG, [late]))
    ast, validated = compile_screen("PCR() > 0", base_tf="5m")
    [m] = run_screen(ast, validated, [only_late], base_tf="5m", check_history=False)
    assert not m.matched and "at or before this bar's close" in m.reason


def test_the_loader_cuts_at_the_last_close_and_picks_the_nearest_expiry_of_own_reads():
    _, me = _owner("fn2e-own@example.com")
    f = pd.DataFrame({"open": [1.0], "high": [1.0], "low": [1.0], "close": [100.0], "volume": [1.0]},
                     index=pd.DatetimeIndex([pd.Timestamp("2026-03-10 03:45", tz="UTC")]))              # closes 09:20 IST = 03:50 UTC
    close = datetime(2026, 3, 10, 3, 50, tzinfo=UTC)

    async def go():
        from app.market_lake import chain_store, intraday_history
        async with _session_factory() as session:
            source = intraday_history.holder_source("fake", me["id"])
            for exp, put in ((date(2026, 4, 30), 9000.0), (date(2026, 3, 26), 3000.0)):                    # two expiries, one slot
                await chain_store.write(session, me["tenant_id"], IDX, exp, _chain(put=put, expiry=exp.isoformat()), close - timedelta(minutes=4),
                                        slot_minutes=5, source=source)
            await chain_store.write(session, me["tenant_id"], IDX, date(2026, 3, 26), _chain(put=100.0), close + timedelta(minutes=1),
                                    slot_minutes=5, source=source)                                          # after the close: never read
            await chain_store.write(session, me["tenant_id"], IDX, date(2026, 3, 26), _chain(put=7000.0), close - timedelta(minutes=2),
                                    slot_minutes=5, source=intraday_history.holder_source("fake", me["id"] + 999))   # another member's
            await session.commit()
            mine, org = SymbolData(IDX, {"5m": f}), SymbolData(IDX, {"5m": f})
            await C.attach(session, [mine], tenant_id=me["tenant_id"], user_id=me["id"], own=True, base_tf="5m", now=close + timedelta(hours=1))
            await C.attach(session, [org], tenant_id=me["tenant_id"], user_id=me["id"], own=False, base_tf="5m", now=close + timedelta(hours=1))
            return mine, org
    mine, org = _run(go())
    assert C.cut_of(mine, "5m", close + timedelta(hours=1)) == close
    assert mine.chains.source == C.SOURCE_OWN and [p.chain.expiry for p in mine.chains.points] == ["2026-03-26"]
    assert evaluate(parse("PCR()"), mine, base_tf="5m").iloc[-1] == 3.0                     # nearest expiry, own read, before the close
    assert not org.chains.points and "no stored option chain" in (org.chains.reason or "")  # the collector holds nothing for it


def test_an_alert_rule_with_pcr_evaluates_at_bar_close_on_the_snapshot_before_the_close():
    _, me = _owner("fn2e-alert@example.com")
    # stored in UTC, as the collector stores them (SQLite in the tests keeps no offset)
    _collect(me["tenant_id"], _chain(put=2000.0), ist(TUE, 9, 22).astimezone(UTC))          # PCR 2, read inside the 09:20 bar (closes 09:25)
    _collect(me["tenant_id"], _chain(put=500.0), ist(TUE, 9, 25).astimezone(UTC) + timedelta(seconds=30), slot_minutes=1)   # after the close
    rid = _rule(me["tenant_id"], symbol=IDX, condition="PCR() > 1")
    fetch = FakeFetch({IDX: _bars(ist(TUE, 9, 15), [99, 105])})
    assert _evaluate(rid, ist(TUE, 9, 26), fetch)[0] == ("done", 1)
    low = _rule(me["tenant_id"], symbol=IDX, condition="PCR() < 1")
    assert _evaluate(low, ist(TUE, 9, 26), fetch)[0] == ("done", 0)        # the later snapshot (PCR 0.5) is not read at this bar

    async def events():
        async with _session_factory() as session:
            return [(e.rule_id, e.symbol) for e in await session.scalars(select(AlertEventRecord).where(AlertEventRecord.rule_id.in_([rid, low])))]
    assert _run(events()) == [(rid, IDX)]


def test_a_snapshot_from_an_earlier_session_is_not_used(flag_on, monkeypatch):
    headers, me = _owner("fn2e-stale@example.com")
    _broker(monkeypatch)
    _collect(me["tenant_id"], _chain(put=2000.0), LAST_CLOSE - timedelta(days=1), future_oi=100.0)          # the session before
    out = client.post("/api/screener/run", json={"source": "PCR() > 0 OR FuturesOI() > 0", "symbols": [IDX, NONE_], "base_tf": "1d"},
                      headers=headers)
    assert out.status_code == 422                                             # nothing could be checked: never a stale match
    for source in ("PCR() > 0", "FuturesOI() > 0"):
        idx = pd.date_range("2026-03-10 03:45", periods=2, freq="5min", tz="UTC")
        f = pd.DataFrame({"open": 1.0, "high": 1.0, "low": 1.0, "close": 100.0, "volume": 1.0}, index=idx)
        old = C.ChainPoint(datetime(2026, 3, 9, 9, 55, tzinfo=UTC), _chain(put=2000.0), 100.0)              # 15:25 IST the day before
        ast, validated = compile_screen(source, base_tf="5m")
        [m] = run_screen(ast, validated, [SymbolData(IDX, {"5m": f}, chains=C.ChainTimeline(IDX, C.SOURCE_ORG, [old]))],
                         base_tf="5m", check_history=False)
        assert not m.matched and "option chain last read on 2026-03-09" in m.reason and m.reason.code == "not_checked"
