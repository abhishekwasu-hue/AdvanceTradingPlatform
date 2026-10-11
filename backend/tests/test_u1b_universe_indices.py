"""Screener addendum U1-b: index catalogue, membership ranges (as-of) and the NSE classification from constituent files.
Fixture files only; no network."""
import asyncio
import json
import pathlib
from datetime import date, timedelta

import pytest
from sqlalchemy import delete, func, select

from app.db.models import ClassificationRecord, DataQualityEventRecord, IndexMembershipRecord, IndexRecord, SecurityRecord, SymbolHistoryRecord
from app.universe import indices, sources, sync
from tests.test_auth_api import _session_factory

FIX = pathlib.Path(__file__).resolve().parent / "fixtures" / "universe"
D0 = date(2026, 3, 27)                        # the first file we hold (any day works; nothing depends on the calendar)
CODE = "NIFTY50"


def _run(coro):
    return asyncio.run(coro)


def _file(drop=(), add=(), industry=None) -> bytes:
    lines = (FIX / "ind_testindexlist.csv").read_text(encoding="utf-8").splitlines()
    head, rows = lines[0], [r for r in lines[1:] if not any(d in r for d in drop)]
    rows += list(add)
    if industry:
        rows = [r.replace(industry[0], industry[1]) for r in rows]
    return ("\n".join([head, *rows]) + "\n").encode()


@pytest.fixture(autouse=True)
def _clean():
    async def go():
        async with _session_factory() as session:
            for model in (IndexMembershipRecord, ClassificationRecord, IndexRecord, SymbolHistoryRecord, SecurityRecord):
                await session.execute(delete(model))
            await session.execute(delete(DataQualityEventRecord).where(DataQualityEventRecord.instrument_key.in_([CODE, "equity_list"])))
            await session.commit()
        async with _session_factory() as session:                    # the securities the constituent ISINs refer to
            await sync.sync_equity_lists(session, sources.StaticSource({"equity_list": (FIX / "EQUITY_L.csv").read_bytes()}), D0)
    _run(go())
    yield


async def _apply(content: bytes, day: date, code: str = CODE):
    async with _session_factory() as session:
        reports = await indices.sync_indices(session, sources.StaticSource({f"constituents:{code}": content}, source="fixture"), day, codes=[code])
        return reports[0]


async def _q(fn, *args):
    async with _session_factory() as session:
        return await fn(session, *args)


def test_catalogue_is_data_and_loads_idempotently():
    items = indices.catalogue()
    codes = {i["index_code"] for i in items}
    assert {"NIFTY50", "NIFTYBANK", "NIFTY500"} <= codes and len(codes) == len(items)
    assert all(i["family"] in ("BROAD", "SECTORAL", "THEMATIC", "STRATEGY") for i in items)
    assert all(i["constituents_url"].startswith("https://") for i in items)

    async def load():
        async with _session_factory() as session:
            first = await indices.load_catalogue(session)
            await session.commit()
            again = await indices.load_catalogue(session)
            row = await session.get(IndexRecord, "NIFTYBANK")
            return first, again, row.has_derivatives, json.loads(row.broker_symbols)
    first, again, derivatives, symbols = _run(load())
    assert first == len(items) and again == 0 and derivatives and symbols.get("upstox")


def test_parse_constituents_reads_the_provider_layout():
    rows = indices.parse_constituents((FIX / "ind_testindexlist.csv").read_bytes())
    assert [r.symbol for r in rows][:2] == ["ALPHAIND", "NEWCO"] and rows[0].industry == "Capital Goods" and rows[0].isin == "INE00U1A1011"


def test_membership_is_as_of_and_rebalances_append_ranges(monkeypatch):
    from app.core import config
    monkeypatch.setattr(config, "UNIVERSE_MAX_CHURN_RATIO", 0.5)        # a 5-name fixture: one swap is 40 % churn
    first = _run(_apply(_file(), D0))
    assert not first.refused and first.entered == [] and first.exited == []                 # a first load is not a rebalance
    assert "INE00U1A1052" in first.unknown_isins                                              # not in the equity list fixture: reported
    later = D0 + timedelta(days=91)
    # Rebalance: GAMMA leaves, ZETA joins.
    zeta = "Zeta Power Ltd.,Power,ZETA,EQ,INE00U1A1078"
    report = _run(_apply(_file(drop=("GAMMA",), add=(zeta,)), later))
    assert report.entered == ["INE00U1A1078"] and report.exited == ["INE00U1A1037"]
    before, on, after = later - timedelta(days=1), later, later + timedelta(days=10)
    assert "INE00U1A1037" in _run(_q(indices.members_on, CODE, before)) and "INE00U1A1078" not in _run(_q(indices.members_on, CODE, before))
    assert "INE00U1A1037" not in _run(_q(indices.members_on, CODE, on)) and "INE00U1A1078" in _run(_q(indices.members_on, CODE, on))
    assert "INE00U1A1037" not in _run(_q(indices.members_on, CODE, after))
    # Before the first file we hold, membership is unknown: nothing is returned, and the coverage date says why.
    assert _run(_q(indices.members_on, CODE, D0 - timedelta(days=1))) == []
    assert _run(_q(indices.coverage_from, CODE)) == D0
    assert _run(_q(indices.indices_of, "INE00U1A1011", on)) == [CODE]


def test_same_file_twice_adds_no_ranges():
    _run(_apply(_file(), D0))

    async def count():
        async with _session_factory() as session:
            return (int(await session.scalar(select(func.count()).select_from(IndexMembershipRecord))),
                    int(await session.scalar(select(func.count()).select_from(ClassificationRecord))))
    before = _run(count())
    report = _run(_apply(_file(), D0 + timedelta(days=1)))
    assert report.entered == [] and report.exited == [] and report.classified == 0 and _run(count()) == before


def test_suspicious_files_are_refused_with_an_event():
    _run(_apply(_file(), D0))
    empty = _run(_apply(b"Company Name,Industry,Symbol,Series,ISIN Code\n", D0 + timedelta(days=1)))
    assert empty.refused == "empty constituent list"
    swapped = ("Eta Ltd.,Power,ETA,EQ,INE00U1A1086", "Theta Ltd.,Power,THETA,EQ,INE00U1A1094")
    churn = _run(_apply(_file(drop=("GAMMA", "DELTA"), add=swapped), D0 + timedelta(days=2)))   # 4 changes on 5 names (> 20 %)
    assert churn.refused and "membership changes" in churn.refused
    assert len(_run(_q(indices.members_on, CODE, D0 + timedelta(days=3)))) == 5      # nothing was rewritten

    async def events():
        async with _session_factory() as session:
            return list(await session.scalars(select(DataQualityEventRecord.kind).where(DataQualityEventRecord.instrument_key == CODE)))
    assert _run(events()).count("CONSTITUENTS") == 2


def test_classification_history_by_isin():
    _run(_apply(_file(), D0))
    assert _run(_q(indices.classification_on, "INE00U1A1011", D0)) == {"macro_sector": None, "sector": "Capital Goods", "industry": None, "basic_industry": None}
    moved = D0 + timedelta(days=30)
    _run(_apply(_file(industry=("Capital Goods", "Construction")), moved))
    assert _run(_q(indices.classification_on, "INE00U1A1011", moved - timedelta(days=1)))["sector"] == "Capital Goods"
    assert _run(_q(indices.classification_on, "INE00U1A1011", moved))["sector"] == "Construction"
    assert _run(_q(indices.classification_on, "INE00U1A1011", D0 - timedelta(days=1))) is None
