"""Phase F1: instrument master - parsing (epoch-ms expiries, segments -> exchanges), atomic
replace, lookups, API, admin sync with a mocked download, worker daily sync."""
import asyncio
from datetime import date, datetime, timedelta

import httpx
import pytest
from sqlalchemy import func, select

from app.db.models import InstrumentRecord
from app.instruments import master
from app.instruments.master import (
    MasterRow, derivatives_exchange, normalise_expiry, parse_upstox_master, replace_master, underlying_of,
)
from tests.master_fixture import BANKNIFTY_LOT, NIFTY_EXPIRIES, NIFTY_LOT, build_master, master_gzip
from tests.test_admin_api import _admin
from tests.test_auth_api import _register, _session_factory, client


def _run(coro):
    return asyncio.run(coro)


def _load_fixture():
    rows = parse_upstox_master(build_master(), "NSE")

    async def go():
        async with _session_factory() as session:
            return await replace_master(session, "upstox", ["NSE", "NFO"], rows)
    return _run(go())


def test_underlying_aliases_and_exchanges():
    assert underlying_of("NIFTY 50") == "NIFTY" and underlying_of("nifty bank") == "BANKNIFTY"
    assert underlying_of("RELIANCE") == "RELIANCE" and underlying_of("SENSEX") == "SENSEX"
    assert derivatives_exchange("NIFTY") == "NFO" and derivatives_exchange("SENSEX") == "BFO"


def test_normalise_expiry_accepts_every_master_format():
    assert normalise_expiry(1790812800000) == date(2026, 10, 1)      # epoch ms (Upstox)
    assert normalise_expiry(1790812800) == date(2026, 10, 1)         # epoch s
    assert normalise_expiry("2026-10-01") == date(2026, 10, 1)       # Kite ISO
    assert normalise_expiry("2026-10-01T15:30:00") == date(2026, 10, 1)
    assert normalise_expiry("01-Oct-2026") == date(2026, 10, 1)
    assert normalise_expiry(None) is None and normalise_expiry("") is None and normalise_expiry("garbage") is None


def test_parse_upstox_master_maps_segments_types_and_underlyings():
    rows = parse_upstox_master(build_master(), "NSE")
    by_type = {}
    for r in rows:
        by_type.setdefault(r.instrument_type, []).append(r)
    assert {r.exchange for r in by_type["CE"]} == {"NFO"} and {r.exchange for r in by_type["INDEX"]} == {"NSE"}
    nifty_ce = [r for r in by_type["CE"] if r.underlying == "NIFTY"]
    assert nifty_ce and all(r.lot_size == NIFTY_LOT for r in nifty_ce)
    assert sorted({r.expiry for r in nifty_ce}) == NIFTY_EXPIRIES
    assert any(r.weekly for r in nifty_ce) and any(not r.weekly for r in nifty_ce)
    index = next(r for r in by_type["INDEX"] if r.tradingsymbol == "NIFTY 50")
    assert index.underlying == "NIFTY" and index.expiry is None
    eq = next(r for r in by_type["EQ"])
    assert eq.underlying == "RELIANCE" and eq.strike is None
    fut = [r for r in by_type["FUT"] if r.underlying == "NIFTY"]
    assert len(fut) == 1 and fut[0].strike is None and fut[0].expiry == NIFTY_EXPIRIES[-1]


def test_replace_master_is_atomic_and_idempotent():
    first = _load_fixture()
    second = _load_fixture()
    assert first == second > 0

    async def count():
        async with _session_factory() as session:
            return await session.scalar(select(func.count()).select_from(InstrumentRecord).where(InstrumentRecord.broker == "upstox"))
    assert _run(count()) == first


def test_lookups_expiries_strikes_and_contracts():
    _load_fixture()

    async def go():
        async with _session_factory() as session:
            exp = await master.expiries(session, "NIFTY 50", instrument_type="CE", on_or_after=date(2026, 10, 2))
            stk = await master.strikes(session, "NIFTY", NIFTY_EXPIRIES[0], instrument_type="PE")
            opt = await master.find_option(session, "NIFTY 50", NIFTY_EXPIRIES[0], 24500.0, "CE")
            fut = await master.find_future(session, "NIFTY", NIFTY_EXPIRIES[-1])
            missing = await master.find_option(session, "NIFTY", NIFTY_EXPIRIES[0], 99999.0, "CE")
            bn = await master.strikes(session, "NIFTY BANK", date(2026, 10, 29), instrument_type="CE")
            found = await master.search_instruments(session, "24500 CE", exchange="NFO", limit=10)
            return exp, stk, opt, fut, missing, bn, found
    exp, stk, opt, fut, missing, bn, found = _run(go())
    assert exp == NIFTY_EXPIRIES[1:]                      # 1 Oct excluded by on_or_after
    assert stk[0] == 24000.0 and stk[-1] == 25000.0 and stk[1] - stk[0] == 50.0
    assert opt is not None and opt.lot_size == NIFTY_LOT and opt.exchange == "NFO" and opt.instrument_key.startswith("NSE_FO|")
    assert fut is not None and fut.instrument_type == "FUT"
    assert missing is None
    assert bn[1] - bn[0] == 100.0
    assert found and all("24500 CE" in r.tradingsymbol for r in found)


def _headers(email: str) -> dict:
    return {"Authorization": f"Bearer {_register(email)}"}


def test_master_api_search_expiries_strikes_status():
    _load_fixture()
    headers = _headers("master-user@example.com")
    status = client.get("/api/instrument-master/status", headers=headers).json()
    assert any(s["broker"] == "upstox" and s["exchange"] == "NFO" and s["rows"] > 0 for s in status["sources"])

    search = client.get("/api/instrument-master/search?q=BANKNIFTY&type=CE&limit=5", headers=headers).json()
    assert len(search) == 5 and all(r["underlying"] == "BANKNIFTY" and r["lot_size"] == BANKNIFTY_LOT for r in search)

    exp = client.get("/api/instrument-master/expiries?underlying=NIFTY%2050&type=CE", headers=headers).json()
    assert exp["underlying"] == "NIFTY" and exp["expiries"]  # today's date filter keeps the fixture's future dates

    strikes = client.get(f"/api/instrument-master/strikes?underlying=NIFTY&expiry={NIFTY_EXPIRIES[0]}&type=PE", headers=headers).json()
    assert strikes["lot_size"] == NIFTY_LOT and 24500.0 in strikes["strikes"]

    assert client.get("/api/instrument-master/search").status_code == 401


def test_admin_sync_pulls_upstox_master(monkeypatch):
    calls = []

    async def fake_download(exchange, client=None):
        calls.append(exchange)
        return build_master()
    monkeypatch.setattr(master, "download_upstox_master", fake_download)
    headers, _ = _admin("master-admin@example.com")
    resp = client.post("/api/instrument-master/sync", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert calls == ["NSE"] and body["synced"]["NSE"] > 0
    assert any(s["exchange"] == "NFO" for s in body["sources"])

    owner = _headers("master-owner@example.com")
    assert client.post("/api/instrument-master/sync", headers=owner).status_code == 403


def test_download_failure_is_a_502_not_a_500(monkeypatch):
    async def boom(exchange, client=None):
        raise httpx.ConnectError("egress blocked")
    monkeypatch.setattr(master, "download_upstox_master", boom)
    headers, _ = _admin("master-admin2@example.com")
    resp = client.post("/api/instrument-master/sync", headers=headers)
    assert resp.status_code == 502 and "egress blocked" in resp.text


def test_download_parses_gzip_json():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/NSE.json.gz")
        return httpx.Response(200, content=master_gzip())
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    rows = _run(master.download_upstox_master("NSE", http))
    assert len(rows) == len(build_master())


def test_worker_syncs_master_once_per_day_from_sync_hour(monkeypatch):
    from app.market_data.calendar import IST
    from app.workers import trading_worker as tw
    from tests.test_trading_worker import _FakeBroker, _worker

    calls = []

    async def fake_sync(session, exchanges, client=None):
        calls.append(tuple(exchanges))
        return {"NSE": 42}
    worker = _worker(monkeypatch, _FakeBroker())
    monkeypatch.setattr(tw, "sync_upstox", fake_sync)

    early = datetime(2026, 9, 28, 7, 30, tzinfo=IST)   # Monday 07:30 - before the sync hour
    assert _run(worker.run_cycle(now=early)).master_synced is None
    first = _run(worker.run_cycle(now=early + timedelta(hours=1)))   # 08:30
    again = _run(worker.run_cycle(now=early + timedelta(hours=2)))
    tomorrow = _run(worker.run_cycle(now=early + timedelta(days=1, hours=1)))
    assert first.master_synced == {"NSE": 42} and again.master_synced is None and tomorrow.master_synced is not None
    assert calls == [("NSE",), ("NSE",)]


def test_worker_master_sync_failure_is_reported_and_not_retried_every_minute(monkeypatch):
    from app.market_data.calendar import IST
    from app.workers import trading_worker as tw
    from tests.test_trading_worker import _FakeBroker, _worker

    attempts = []

    async def failing(session, exchanges, client=None):
        attempts.append(1)
        raise httpx.ConnectError("no network")
    worker = _worker(monkeypatch, _FakeBroker())
    monkeypatch.setattr(tw, "sync_upstox", failing)
    at = datetime(2026, 9, 28, 8, 30, tzinfo=IST)
    report = _run(worker.run_cycle(now=at))
    assert any("instrument master" in e for e in report.errors)
    _run(worker.run_cycle(now=at + timedelta(minutes=1)))
    assert len(attempts) == 1
