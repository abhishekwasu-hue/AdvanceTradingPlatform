"""Screener addendum U1-c: index daily closes, F&O membership with lots, the exchange's ban list. Fixtures only."""
import asyncio
import pathlib
from datetime import date, timedelta

import pytest
from sqlalchemy import delete, select

from app.db.models import DataQualityEventRecord, FoBanRecord, FoMembershipRecord, IndexEodRecord, IndexRecord, SecurityRecord, SymbolHistoryRecord
from app.universe import fo_eod, sources, sync
from tests.test_auth_api import _session_factory

FIX = pathlib.Path(__file__).resolve().parent / "fixtures" / "universe"
DAY = date(2026, 10, 9)


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _clean():
    async def go():
        async with _session_factory() as session:
            for model in (IndexEodRecord, FoMembershipRecord, FoBanRecord, SymbolHistoryRecord, SecurityRecord):
                await session.execute(delete(model))
            await session.execute(delete(IndexRecord).where(IndexRecord.family == "OTHER"))
            await session.execute(delete(DataQualityEventRecord).where(DataQualityEventRecord.instrument_key.in_(["fo_lots", "equity_list"])))
            await session.commit()
        async with _session_factory() as session:
            await sync.sync_equity_lists(session, sources.StaticSource({"equity_list": (FIX / "EQUITY_L.csv").read_bytes()}), DAY)
    _run(go())
    yield


def _files(**override):
    files = {"index_close_all": (FIX / "ind_close_all_09102026.csv").read_bytes(), "fo_lots": (FIX / "fo_mktlots.csv").read_bytes(),
             "fo_ban": (FIX / "fo_secban.csv").read_bytes()}
    files.update(override)
    return {k: v for k, v in files.items() if v is not None}


async def _sync(files, day=DAY):
    async with _session_factory() as session:
        return await fo_eod.sync_fo_and_index_eod(session, sources.StaticSource(files, source="fixture"), day)


async def _all(model, *where):
    async with _session_factory() as session:
        return list(await session.scalars(select(model).where(*where)))


def test_parsers_read_the_exchange_layouts():
    closes = fo_eod.parse_index_closes((FIX / "ind_close_all_09102026.csv").read_bytes())
    assert [c.name for c in closes] == ["Nifty 50", "Nifty Bank", "Nifty Test New Index"]
    assert closes[0].trade_date == DAY and closes[0].close == 101.0 and closes[0].pe == 22.1 and closes[2].pe is None
    lots = dict(fo_eod.parse_fo_lots((FIX / "fo_mktlots.csv").read_bytes()))
    assert lots == {"NIFTY": 75, "ALPHAIND": 500, "NEWCO": 1200, "GAMMA": 700}            # an empty first month uses the next
    day, banned = fo_eod.parse_ban_list((FIX / "fo_secban.csv").read_bytes(), DAY + timedelta(days=5))
    assert day == DAY and banned == ["GAMMA", "NEWCO"]                                      # the file's own trade date wins
    assert fo_eod.parse_ban_list(b"Sr.No,Symbol\n1,GAMMA\n", DAY) == (DAY, ["GAMMA"])               # a header row is not a symbol
    assert fo_eod.code_for("Nifty 50") == "NIFTY50" and fo_eod.code_for("Nifty Test New Index") == "NIFTYTESTNEWINDEX"
    assert "{ddmmyyyy}" not in fo_eod.dated_urls(DAY)["index_close_all"] and "09102026" in fo_eod.dated_urls(DAY)["index_close_all"]


def test_sync_loads_closes_lots_and_ban_and_is_idempotent():
    report = _run(_sync(_files()))
    assert report.index_rows == 3 and report.discovered_indices == ["NIFTYTESTNEWINDEX"] and not report.refused
    assert sorted(report.banned) == ["GAMMA", "NEWCO"] and report.fo_entered == []          # a first load is not "entered"
    rows = {r.underlying: r for r in _run(_all(FoMembershipRecord))}
    assert rows["NIFTY"].is_index and rows["NIFTY"].isin is None and rows["ALPHAIND"].isin == "INE00U1A1011" and rows["ALPHAIND"].start_observed
    again = _run(_sync(_files()))
    assert again.index_rows == 0 and again.index_corrections == 0 and again.banned == [] and again.lot_changes == []
    assert len(_run(_all(FoMembershipRecord))) == 4 and len(_run(_all(FoBanRecord))) == 2


def test_lot_change_and_exit_append_ranges(monkeypatch):
    from app.core import config
    monkeypatch.setattr(config, "UNIVERSE_MAX_CHURN_RATIO", 0.5)            # a 4-name fixture: one exit is 25 %
    monkeypatch.setattr(config, "UNIVERSE_MIN_ROWS_RATIO", 0.5)
    _run(_sync(_files()))
    later = DAY + timedelta(days=30)
    changed = (FIX / "fo_mktlots.csv").read_bytes().replace(b",500       ,500", b",250       ,500").replace(
        b"GAMMA BANK LIMITED                                ,GAMMA     ,          ,700       ,700       \n", b"")
    report = _run(_sync(_files(fo_lots=changed, index_close_all=None, fo_ban=None), day=later))
    assert report.lot_changes == ["ALPHAIND 500->250"] and report.fo_exited == ["GAMMA"]
    alpha = sorted(_run(_all(FoMembershipRecord, FoMembershipRecord.underlying == "ALPHAIND")), key=lambda r: r.valid_from)
    assert [(r.lot_size, r.valid_from, r.valid_to) for r in alpha] == [(500, DAY, later), (250, later, None)]
    gamma = _run(_all(FoMembershipRecord, FoMembershipRecord.underlying == "GAMMA"))
    assert gamma[0].valid_to == later


def test_a_suspicious_lot_file_is_refused():
    _run(_sync(_files()))
    tiny = b"UNDERLYING,SYMBOL,OCT-26\nNIFTY 50,NIFTY,75\n"
    report = _run(_sync(_files(fo_lots=tiny, index_close_all=None, fo_ban=None), day=DAY + timedelta(days=1)))
    assert report.refused and "underlyings against" in report.refused[0]
    assert len(_run(_all(FoMembershipRecord, FoMembershipRecord.valid_to.is_(None)))) == 4      # nothing closed


def test_a_corrected_close_updates_the_row_and_is_counted():
    _run(_sync(_files()))
    corrected = (FIX / "ind_close_all_09102026.csv").read_bytes().replace(b",101.00,", b",101.20,")
    report = _run(_sync(_files(index_close_all=corrected, fo_lots=None, fo_ban=None)))
    assert report.index_corrections == 1
    row = _run(_all(IndexEodRecord, IndexEodRecord.index_code == "NIFTY50"))[0]
    assert row.close == 101.2
