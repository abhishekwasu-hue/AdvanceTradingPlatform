"""Screener addendum U1-a: securities and symbol history from the exchange's equity lists and symbol-change file.
Fixture files only (tests/fixtures/universe); no network."""
import asyncio
import pathlib
from datetime import date, timedelta

import httpx
import pytest
from sqlalchemy import delete, func, select

from app.db.models import DataQualityEventRecord, SecurityRecord, SymbolHistoryRecord
from app.universe import asof, parsers, sources, sync
from tests.test_auth_api import _session_factory

FIX = pathlib.Path(__file__).resolve().parent / "fixtures" / "universe"
DAY = date(2026, 10, 9)                       # the run's day: any date after the fixtures' listings


def _run(coro):
    return asyncio.run(coro)


def _files(**override):
    files = {"equity_list": (FIX / "EQUITY_L.csv").read_bytes(), "sme_equity_list": (FIX / "SME_EQUITY_L.csv").read_bytes(),
             "etf_list": (FIX / "eq_etfseclist.csv").read_bytes(), "symbol_changes": (FIX / "symbolchange.csv").read_bytes()}
    files.update(override)
    return {k: v for k, v in files.items() if v is not None}


@pytest.fixture(autouse=True)
def _clean():
    async def go():
        async with _session_factory() as session:
            await session.execute(delete(SymbolHistoryRecord))
            await session.execute(delete(SecurityRecord))
            await session.execute(delete(DataQualityEventRecord).where(DataQualityEventRecord.instrument_key.like("IN%")
                                                                         | DataQualityEventRecord.instrument_key.in_(["equity_list", "symbol_changes"])))
            await session.commit()
    _run(go())
    yield


async def _sync(files, today=DAY):
    async with _session_factory() as session:
        return await sync.sync_equity_lists(session, sources.StaticSource(files, source="fixture"), today)


async def _q(fn, *args, **kw):
    async with _session_factory() as session:
        return await fn(session, *args, **kw)


async def _count(model):
    async with _session_factory() as session:
        return int(await session.scalar(select(func.count()).select_from(model)))


def test_parsers_read_the_exchange_layouts_and_refuse_an_unknown_one():
    eq = parsers.parse_equity_list((FIX / "EQUITY_L.csv").read_bytes())
    assert [s.symbol for s in eq] == ["ALPHAIND", "NEWCO", "GAMMA"]                      # the row without an ISIN is skipped
    assert eq[0].listing_date == date(2008, 10, 6) and eq[0].face_value == 10 and eq[0].series == "EQ"
    sme = parsers.parse_equity_list((FIX / "SME_EQUITY_L.csv").read_bytes(), sme=True)
    assert sme[0].is_sme and sme[0].market_lot == 1200 and sme[0].series == "SM"
    etf = parsers.parse_equity_list((FIX / "eq_etfseclist.csv").read_bytes(), etf=True)
    assert etf[0].is_etf and etf[0].underlying == "Nifty 50 Index" and etf[0].isin.startswith("INF")
    changes = parsers.parse_symbol_changes((FIX / "symbolchange.csv").read_bytes())
    assert [(c.old_symbol, c.new_symbol, c.effective) for c in changes][:2] == [("OLDCO", "MIDCO", date(2012, 4, 1)), ("MIDCO", "NEWCO", date(2018, 7, 15))]
    assert parsers.parse_symbol_changes((FIX / "symbolchange_noheader.csv").read_bytes()) == changes[:2]
    with pytest.raises(parsers.LayoutError):
        parsers.parse_equity_list(b"TICKER,COMPANY\nX,Y\n")


def test_sync_loads_securities_and_rebuilds_symbol_history_and_is_idempotent():
    report = _run(_sync(_files()))
    assert report.inserted == 5 and report.history_rebuilt == 1 and not report.refused
    assert set(report.files) == {"equity_list", "sme_equity_list", "etf_list", "symbol_changes"}
    rows, ranges = _run(_count(SecurityRecord)), _run(_count(SymbolHistoryRecord))
    again = _run(_sync(_files()))
    assert again.inserted == 0 and again.updated == 0 and again.history_rebuilt == 0
    assert _run(_count(SecurityRecord)) == rows and _run(_count(SymbolHistoryRecord)) == ranges        # no duplicate ranges


def test_rename_continuity_by_isin():
    _run(_sync(_files()))
    isin = "INE00U1A1029"
    assert _run(_q(asof.symbol_on, isin, date(2011, 1, 3))) == "OLDCO"
    assert _run(_q(asof.symbol_on, isin, date(2012, 4, 1))) == "MIDCO"          # the change day belongs to the new symbol
    assert _run(_q(asof.symbol_on, isin, date(2018, 7, 14))) == "MIDCO"
    assert _run(_q(asof.symbol_on, isin, DAY)) == "NEWCO"
    assert _run(_q(asof.symbol_on, isin, date(2009, 1, 1))) is None             # before its listing: no symbol at all
    assert _run(_q(asof.isin_for, "oldco", date(2011, 1, 3))) == isin
    assert _run(_q(asof.isin_for, "OLDCO", DAY)) is None


def test_a_rename_with_no_change_record_is_dated_at_the_run_never_invented():
    _run(_sync(_files(symbol_changes=None)))
    renamed = (FIX / "EQUITY_L.csv").read_bytes().replace(b"GAMMA,Gamma Bank", b"GAMMAFIN,Gamma Bank")
    later = DAY + timedelta(days=3)
    report = _run(_sync(_files(equity_list=renamed, symbol_changes=None), today=later))
    assert report.observed_renames == 1 and report.updated == 1
    assert _run(_q(asof.symbol_on, "INE00U1A1037", later - timedelta(days=1))) == "GAMMA"
    assert _run(_q(asof.symbol_on, "INE00U1A1037", later)) == "GAMMAFIN"

    async def events():
        async with _session_factory() as session:
            return list(await session.scalars(select(DataQualityEventRecord.kind).where(DataQualityEventRecord.instrument_key == "INE00U1A1037")))
    assert _run(events()) == ["RENAME_NOREC"]


def test_a_truncated_equity_list_is_refused_and_changes_nothing():
    _run(_sync(_files()))
    truncated = b"SYMBOL,NAME OF COMPANY, SERIES, DATE OF LISTING, PAID UP VALUE, MARKET LOT, ISIN NUMBER, FACE VALUE\nALPHAIND,Alpha Industries Limited,EQ,06-OCT-2008,10,1,INE00U1A1011,10\n"
    report = _run(_sync(_files(equity_list=truncated), today=DAY + timedelta(days=1)))
    assert report.refused and "minimum ratio" in report.refused[0] and "equity_list" not in report.files

    async def state():
        async with _session_factory() as session:
            alpha = await session.get(SecurityRecord, "INE00U1A1011")
            kinds = list(await session.scalars(select(DataQualityEventRecord.kind).where(DataQualityEventRecord.instrument_key == "equity_list")))
            return alpha.last_seen_on, kinds
    seen, kinds = _run(state())
    assert seen == DAY and "SHORT_FILE" in kinds


def test_historical_universe_keeps_delisted_names_and_respects_listing_dates():
    _run(_sync(_files()))

    async def delist():
        async with _session_factory() as session:
            row = await session.get(SecurityRecord, "INE00U1A1037")
            row.delisting_date, row.status = date(2020, 6, 1), "DELISTED"
            await session.commit()
    _run(delist())
    before = _run(_q(asof.listed_on, date(2019, 1, 1)))
    after = _run(_q(asof.listed_on, date(2021, 1, 1)))
    assert "INE00U1A1037" in before and "INE00U1A1037" not in after                   # no survivorship bias
    assert "INE00U1A1011" in _run(_q(asof.listed_on, date(2009, 1, 1))) and "INE00U1A1029" not in _run(_q(asof.listed_on, date(2009, 1, 1)))
    assert "INE00U1A1045" not in after and "INE00U1A1045" in _run(_q(asof.listed_on, DAY, include_sme=True))
    assert "INF00U1A1012" in _run(_q(asof.listed_on, DAY, include_etf=True))
    # A name missing from a later (full-size) file is not delisted on that alone.
    smaller = (FIX / "EQUITY_L.csv").read_bytes().replace(b"ALPHAIND,Alpha Industries Limited,EQ,06-OCT-2008,10,1,INE00U1A1011,10\r\n", b"")
    report = _run(_sync(_files(equity_list=smaller), today=DAY + timedelta(days=1)))
    assert "equity_list" in report.files and not report.refused                   # accepted: 2 of the 2 active main-board names

    async def alpha():
        async with _session_factory() as session:
            row = await session.get(SecurityRecord, "INE00U1A1011")
            return row.status, row.last_seen_on
    assert _run(alpha()) == ("ACTIVE", DAY)


def test_archive_source_is_polite_and_returns_bytes_with_checksum(monkeypatch):
    seen = []

    def handler(request):
        seen.append(request.headers.get("user-agent"))
        return httpx.Response(200, content=b"SYMBOL,NAME OF COMPANY,ISIN NUMBER\n")
    real = httpx.AsyncClient
    monkeypatch.setattr(sources.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    src = sources.NseArchiveSource({"equity_list": "https://example.invalid/EQUITY_L.csv"}, pause_seconds=0)
    got = _run(src.fetch("equity_list"))
    assert got.content.startswith(b"SYMBOL") and len(got.checksum) == 64 and got.source == "nse_archives"
    assert seen and "ATP" in seen[0]
    assert _run(src.fetch("not_configured")) is None
