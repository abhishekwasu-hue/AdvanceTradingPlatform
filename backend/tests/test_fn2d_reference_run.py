"""FN2-d: Sector / Industry / IndexMember / IsFnO filter a screener run and a bar-close alert rule - the reference data
(U1 tables) is loaded on the run path; the current classification only (a range that ended, or one that starts after
the run date, is never read); McapBucket (no source) stays refused; history / studies / the library keep refusing."""
import asyncio
import json
import types
from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.db.models import (
    AlertEventRecord,
    ClassificationRecord,
    FoMembershipRecord,
    IndexMembershipRecord,
    IndexRecord,
    ScreenRecord,
    SecurityRecord,
    SymbolHistoryRecord,
)
from app.screener import compile_screen, nodes
from app.screener import reference as R
from app.screener.routes import missing_data
from app.screener.runtime import SymbolData
from tests.test_auth_api import _session_factory, client  # noqa: F401 - fixtures
from tests.test_phase_l_ai import _owner
from tests.test_s1d_screener_api import _daily_bars, _flag
from tests.test_s4a_barclose import TUE, FakeFetch, _bars, _evaluate, _rule, ist

OLD = date(2024, 1, 1)
# made-up symbols / ISINs (never a real company): IT + F&O + index member; a bank outside both; a security known only
# to the securities master; one with no reference data at all
IT, BANK, MASTER, NONE_ = "FN2DIT", "FN2DBANK", "FN2DMASTER", "FN2DNONE"
ISIN = {IT: "INFN2D000011", BANK: "INFN2D000022", MASTER: "INFN2D000033"}


def _run(coro):
    return asyncio.run(coro)


def _seed():
    async def go():
        async with _session_factory() as session:
            if await session.scalar(select(SymbolHistoryRecord.id).where(SymbolHistoryRecord.symbol == IT)) is not None:
                return                                                    # seeded once per test database
            today = R.run_date()
            for sym in (IT, BANK):
                session.add(SymbolHistoryRecord(isin=ISIN[sym], symbol=sym, valid_from=None, valid_to=None, source="test", checksum="t"))
            session.add(SecurityRecord(isin=ISIN[MASTER], symbol=MASTER, name="Made Up", series="EQ", source="test", checksum="t"))
            add = session.add
            add(ClassificationRecord(isin=ISIN[IT], scheme="NSE", sector="Information Technology", industry="IT - Software",
                                     valid_from=OLD, source="test", checksum="t"))
            # look-ahead guard: a reclassification that starts after the run date is never read
            add(ClassificationRecord(isin=ISIN[IT], scheme="NSE", sector="Later Sector", industry="Later", valid_from=today + timedelta(days=30),
                                     source="test", checksum="t"))
            # an ended range is never read: BANK was IT once, it is Financial Services now
            add(ClassificationRecord(isin=ISIN[BANK], scheme="NSE", sector="Information Technology", valid_from=OLD, valid_to=date(2024, 6, 1),
                                     source="test", checksum="t"))
            add(ClassificationRecord(isin=ISIN[BANK], scheme="NSE", sector="Financial Services", industry="Banks", valid_from=date(2024, 6, 1),
                                     source="test", checksum="t"))
            add(ClassificationRecord(isin=ISIN[MASTER], scheme="NSE", sector="Chemicals", valid_from=OLD, source="test", checksum="t"))
            if await session.get(IndexRecord, "NIFTY50") is None:
                add(IndexRecord(index_code="NIFTY50", name="NIFTY 50", family="BROAD", source="test", checksum="t"))
            add(IndexRecord(index_code="FN2DIDX", name="FN2D MADE UP", family="THEMATIC", source="test", checksum="t"))
            add(IndexMembershipRecord(index_code="NIFTY50", isin=ISIN[IT], valid_from=OLD, source="test", checksum="t"))
            add(IndexMembershipRecord(index_code="NIFTY50", isin=ISIN[BANK], valid_from=OLD, valid_to=date(2024, 6, 1), source="test",
                                      checksum="t"))                                      # left the index: not a member now
            add(IndexMembershipRecord(index_code="FN2DIDX", isin=ISIN[BANK], valid_from=today + timedelta(days=5), source="test",
                                      checksum="t"))                                      # joins after the run date: not yet
            add(FoMembershipRecord(underlying=IT, isin=ISIN[IT], is_index=False, lot_size=100, valid_from=OLD, source="test", checksum="t"))
            add(FoMembershipRecord(underlying=BANK, isin=ISIN[BANK], is_index=False, lot_size=50, valid_from=OLD, valid_to=date(2024, 6, 1),
                                   source="test", checksum="t"))                          # exited F&O
            await session.commit()
    _run(go())


@pytest.fixture
def seeded():
    _seed()


def _attach(symbols, day=None):
    universe = [SymbolData(s, {}) for s in symbols]

    async def go():
        async with _session_factory() as session:
            await R.attach(session, universe, day)
    _run(go())
    return {d.symbol: d for d in universe}


def test_attach_reads_the_current_classification_only(seeded):
    got = _attach([IT, BANK, MASTER, NONE_])
    it, bank, master, none_ = got[IT], got[BANK], got[MASTER], got[NONE_]
    assert (it.sector, it.industry, it.is_fno) == ("Information Technology", "IT - Software", True)       # not "Later Sector"
    assert {"NIFTY50", "NIFTY 50"} <= it.indices and "FN2DIDX" not in it.indices
    assert (bank.sector, bank.industry, bank.is_fno) == ("Financial Services", "Banks", False)           # ended ranges never read
    assert not ({"NIFTY50", "NIFTY 50", "FN2DIDX", "FN2D MADE UP"} & bank.indices)                       # left one, not yet in the other
    assert master.sector == "Chemicals" and master.is_fno is False                                      # ISIN from the securities master
    assert (none_.sector, none_.industry, none_.indices, none_.is_fno) == (None, None, set(), False)      # never guessed
    # on an older date the older ranges answer (the date decides, never a later reclassification)
    past = _attach([BANK], date(2024, 3, 1))[BANK]
    assert past.sector == "Information Technology" and "NIFTY 50" in past.indices and past.is_fno is True


def test_missing_data_counts_reference_as_loaded_only_on_the_run_path():
    def miss(text, **kw):
        ast, _ = compile_screen(text, base_tf="1d")
        return missing_data(ast, **kw)
    text = 'Sector() == "IT" AND Industry() == "x" AND IndexMember("NIFTY 50") AND IsFnO()'
    assert miss(text, reference=True) == []
    assert miss(text) == ["Sector", "Industry", "IndexMember", "IsFnO"]                 # history / studies / library: refused
    assert miss('McapBucket() == "large" AND IsFnO()', reference=True) == ["McapBucket"]  # no source: refused everywhere
    assert miss("PCR() > 1", reference=True) == ["PCR"]
    assert R.calls(compile_screen("close > 1", base_tf="1d")[0]) == []


def _bars_for_all(monkeypatch):
    from app.brokers import token_lifecycle
    from app.market_data import candles_routes, service

    async def pick(session, tenant_id, broker, label, **_k):
        return types.SimpleNamespace(broker_name="fake")
    adapter = types.SimpleNamespace(name="fake", native_intervals=frozenset({"1min", "30min", "day", "week", "month"}))

    async def candles(self, symbol, exchange="NSE", interval="1min", now=None):
        return _daily_bars(100, 1.0)
    monkeypatch.setattr(candles_routes, "_pick_record", pick)
    monkeypatch.setattr(token_lifecycle, "build_adapter", lambda record: adapter)
    monkeypatch.setattr(service.MarketDataService, "get_candles", candles)


@pytest.fixture
def flag_on():
    _flag(True)
    yield
    _flag(False)


def test_a_run_filters_on_sector_index_and_fno(seeded, flag_on, monkeypatch):
    headers, _ = _owner("fn2d-run@example.com")
    _bars_for_all(monkeypatch)
    symbols = [IT, BANK, MASTER, NONE_]

    def matched(source):
        out = client.post("/api/screener/run", json={"source": source, "symbols": symbols}, headers=headers)
        assert out.status_code == 200, out.text
        return sorted(out.json()["matched"])
    assert matched("close > 1") == sorted(symbols)                                            # every symbol has bars
    assert matched('close > 1 AND Sector() == "Information Technology"') == [IT]
    assert matched('Sector() == "Financial Services"') == [BANK]
    assert matched('Industry() == "Banks"') == [BANK]
    assert matched('Sector() IN ("Chemicals", "Financial Services")') == sorted([BANK, MASTER])
    assert matched('Sector() == "Later Sector"') == []                                         # a later reclassification: never
    assert matched('IndexMember("NIFTY 50")') == [IT] and matched('IndexMember("NIFTY50")') == [IT]
    assert matched('IndexMember("FN2D MADE UP")') == []                                        # joins after the run date
    assert matched("IsFnO()") == [IT]
    assert matched("NOT IsFnO()") == sorted([BANK, MASTER, NONE_])
    refused = client.post("/api/screener/run", json={"source": 'McapBucket() == "large"', "symbols": symbols}, headers=headers)
    assert refused.status_code == 422 and "McapBucket()" in refused.text                       # no source: refused, never "nothing matched"


def test_a_bar_close_screen_rule_filters_on_fno_and_sector(seeded):
    _, me = _owner("fn2d-alert@example.com")

    async def screen():
        async with _session_factory() as session:
            text = 'close > 100 AND IsFnO() AND Sector() == "Information Technology"'
            ast, _ = compile_screen(text, base_tf="5m")
            row = ScreenRecord(tenant_id=me["tenant_id"], name="ref", source_text=text, ast_json=json.dumps(nodes.to_json(ast)), base_tf="5m",
                               params_json="{}")
            session.add(row)
            await session.commit()
            return row.id
    sid = _run(screen())
    rid = _rule(me["tenant_id"], kind="screen", symbol=None, condition=None, screen_id=sid, universe_json=json.dumps([IT, BANK, NONE_]))
    fetch = FakeFetch({s: _bars(ist(TUE, 9, 15), [99, 105]) for s in (IT, BANK, NONE_)})
    assert _evaluate(rid, ist(TUE, 9, 26), fetch)[0] == ("done", 1)

    async def events():
        async with _session_factory() as session:
            return [e.symbol for e in await session.scalars(select(AlertEventRecord).where(AlertEventRecord.rule_id == rid))]
    assert _run(events()) == [IT]
