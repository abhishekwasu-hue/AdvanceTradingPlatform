"""UI2-4a2: an index chart's volume from its near-month future.

* `near_future`: NIFTY 50 -> the first NIFTY FUT expiring on or after the day in the instrument master (the expiry day
  still the expiring contract's, the next day the next one); SENSEX -> BFO; not an index, the wrong exchange or no
  contract in the master -> None;
* the candles route replaces each bar's volume by the future bar's at the same timestamp (0 where none), at every
  timeframe, and says which contract (`volume_source`); a non-index symbol, `volume_from_future: false`, no contract, or
  the future's bars refused by the budget or the entitlement guard -> volume unchanged and `volume_source: null`;
* the future's bars go through the requesting user's own session and budget - never another tenant's."""
import asyncio
import uuid
from datetime import date, datetime, time, timedelta, timezone
from typing import List

import pytest
from sqlalchemy import delete

from app.core.models import OHLCVBar
from app.db.models import InstrumentRecord
from app.market_data import candles_routes, entitlement, index_volume, periods, shared_candles
from app.market_data.calendar import IST
from app.market_data.entitlement import DataTier, Entitlement
from tests.test_auth_api import _session_factory
from tests.test_bt1_byob_data_tier import _flag
from tests.test_phase_aa_market_data_api import _store_credentials
from tests.test_phase_k_commercial import _owner
from tests.test_ui2_4a_week_month import DailyBroker, _generous, _post, _setup, daily_series

run = asyncio.run
UTC = timezone.utc


def _today() -> date:
    return datetime.now(UTC).astimezone(IST).date()


@pytest.fixture()
def master(monkeypatch):
    """A private instrument master (its own broker name), removed afterwards: NIFTY near / next FUT, SENSEX FUT on BFO."""
    name = f"ui24iv{uuid.uuid4().hex[:6]}"
    monkeypatch.setattr(index_volume, "MASTER_BROKER", name)
    today = _today()
    near, nxt = today + timedelta(days=7), today + timedelta(days=35)
    rows = [InstrumentRecord(broker=name, exchange=x, instrument_key=f"{name}|{t}", tradingsymbol=t, underlying=u, instrument_type="FUT",
                             expiry=e, lot_size=75)
            for x, t, u, e in (("NFO", f"NIFTY{near:%y%b}FUT".upper(), "NIFTY", near), ("NFO", f"NIFTY{nxt:%y%b}FUT".upper(), "NIFTY", nxt),
                               ("BFO", f"SENSEX{near:%y%b}FUT".upper(), "SENSEX", near))]
    rows.append(InstrumentRecord(broker=name, exchange="NFO", instrument_key=f"{name}|NIFTYCE", tradingsymbol="NIFTYCE", underlying="NIFTY",
                                 instrument_type="CE", expiry=today + timedelta(days=2), strike=25000.0, lot_size=75))

    async def put():
        async with _session_factory() as session:
            session.add_all(rows)
            await session.commit()
    run(put())
    yield {"broker": name, "near": near, "next": nxt, "near_symbol": rows[0].tradingsymbol, "next_symbol": rows[1].tradingsymbol}

    async def clean():
        async with _session_factory() as session:
            await session.execute(delete(InstrumentRecord).where(InstrumentRecord.broker == name))
            await session.commit()
    run(clean())


def _near(symbol: str, exchange: str, on: date):
    async def go():
        async with _session_factory() as session:
            return await index_volume.near_future(session, symbol, exchange, on)
    return run(go())


def test_near_future_from_the_instrument_master(master):
    fut = _near("NIFTY 50", "NSE", _today())
    assert fut is not None and (fut.symbol, fut.expiry, fut.exchange, fut.underlying) == (master["near_symbol"], master["near"], "NFO", "NIFTY")
    assert _near("NIFTY 50", "NSE", master["near"]).expiry == master["near"]                         # expiry day: still that one
    assert _near("NIFTY 50", "NSE", master["near"] + timedelta(days=1)).symbol == master["next_symbol"]   # the day after: next
    assert _near("nifty  50", "nse", _today()).symbol == master["near_symbol"]
    assert _near("SENSEX", "BSE", _today()).exchange == "BFO"
    assert _near("NIFTY 50", "BSE", _today()) is None                                                # the index is on NSE
    assert _near("RELIANCE", "NSE", _today()) is None                                                # not an index
    assert _near("NIFTY BANK", "NSE", _today()) is None                                              # no contract in the master
    assert _near("NIFTY 50", "NSE", master["next"] + timedelta(days=1)) is None                       # none left
    source = fut.source(date(2026, 8, 1))
    assert {k: source[k] for k in ("symbol", "expiry", "exchange")} == {"symbol": master["near_symbol"], "expiry": master["near"].isoformat(),
                                                                       "exchange": "NFO"}
    assert source["from"] == "2026-08-01" and "current near month only" in source["label"]


def test_replace_volume_matches_the_instant_whatever_the_zone():
    ist = datetime(2026, 10, 19, 9, 15, tzinfo=IST)
    bars = [OHLCVBar(timestamp=ist + timedelta(minutes=i), open=1, high=1, low=1, close=1, volume=5) for i in range(3)]
    fut = [OHLCVBar(timestamp=(ist + timedelta(minutes=1)).astimezone(UTC), open=1, high=1, low=1, close=1, volume=42)]
    assert [b.volume for b in index_volume.replace_volume(bars, fut)] == [0.0, 42.0, 0.0]


def _minutes(day: date, n: int, volume) -> List[OHLCVBar]:
    start = datetime.combine(day, time(9, 15), tzinfo=IST)
    return [OHLCVBar(timestamp=start + timedelta(minutes=i), open=100 + i, high=101 + i, low=99 + i, close=100.5 + i, volume=volume(i))
            for i in range(n)]


def _index_broker(master, *, index_volume_each: float = 0.0) -> DailyBroker:
    today = _today()
    day = today - timedelta(days=1)
    broker = DailyBroker(
        daily={"NIFTY 50": daily_series(today - timedelta(days=900), today - timedelta(days=1), scale=index_volume_each / 1000 if index_volume_each else 0.0),
               "TCS": daily_series(today - timedelta(days=900), today - timedelta(days=1)),
               master["near_symbol"]: daily_series(master["near"] - timedelta(days=60), today - timedelta(days=1), scale=3.0)},
        minutes={"NIFTY 50": _minutes(day, 30, lambda i: index_volume_each), "TCS": _minutes(day, 30, lambda i: 11.0),
                 master["near_symbol"]: _minutes(day, 20, lambda i: 7.0 + i)})
    broker.name = f"ui24ivb{uuid.uuid4().hex[:4]}"           # its own shared copies (the index symbol is shared by name)
    return broker


def test_intraday_volume_from_the_future_by_timestamp(monkeypatch, master):
    broker = _index_broker(master)
    headers, _ = _setup(monkeypatch, broker, "intraday")
    body = _post(headers, symbols=["NIFTY 50", "TCS"], timeframe="5min", lookback_days=3)
    nifty = body["symbols"]["NIFTY 50"]
    assert nifty["error"] is None and [b["volume"] for b in nifty["bars"]] == [sum(7.0 + i for i in range(k, k + 5)) for k in range(0, 20, 5)] + [0.0, 0.0]
    assert nifty["volume_source"]["symbol"] == master["near_symbol"] and nifty["volume_source"]["exchange"] == "NFO"
    assert nifty["volume_source"]["expiry"] == master["near"].isoformat()
    assert body["symbols"]["TCS"]["volume_source"] is None and {b["volume"] for b in body["symbols"]["TCS"]["bars"]} == {55.0}
    fut_calls = [w for w in broker.windows if w[0] == master["near_symbol"]]
    assert fut_calls and all(w[1] == "NFO" for w in fut_calls)
    # Opted out: untouched, no call for the future.
    broker.windows.clear()
    shared_candles.reset()
    off = _post(headers, symbols=["NIFTY 50"], timeframe="5min", lookback_days=3, volume_from_future=False)
    assert off["symbols"]["NIFTY 50"]["volume_source"] is None and {b["volume"] for b in off["symbols"]["NIFTY 50"]["bars"]} == {0.0}
    assert not any(w[0] == master["near_symbol"] for w in broker.windows)


def test_daily_and_weekly_volume_from_the_current_near_month_only(monkeypatch, master):
    broker = _index_broker(master)
    headers, _ = _setup(monkeypatch, broker, "daily")
    fut_daily = {b.timestamp: b.volume for b in broker.daily[master["near_symbol"]]}
    first = min(fut_daily).date()
    body = _post(headers, symbols=["NIFTY 50"], timeframe="day", lookback_days=400)
    nifty = body["symbols"]["NIFTY 50"]
    for bar in nifty["bars"]:
        ts = datetime.fromisoformat(bar["timestamp"])
        assert bar["volume"] == fut_daily.get(ts, 0.0)
    assert any(b["volume"] > 0 for b in nifty["bars"]) and nifty["bars"][0]["volume"] == 0.0
    assert nifty["volume_source"]["from"] == first.isoformat()

    week = _post(headers, symbols=["NIFTY 50"], timeframe="week")["symbols"]["NIFTY 50"]
    fut_weeks = {b.timestamp: b.volume for b in periods.aggregate(broker.daily[master["near_symbol"]], "week")}
    for bar in week["bars"]:
        ts = datetime.fromisoformat(bar["timestamp"])
        assert bar["volume"] == fut_weeks.get(ts, 0.0)                       # the listing's cut week: different stamp -> 0
    assert week["volume_source"]["symbol"] == master["near_symbol"]
    fut_windows = [w for w in broker.windows if w[0] == master["near_symbol"] and w[2] == "day"]
    listed = master["near"] - timedelta(days=index_volume.LISTED_DAYS_BEFORE_EXPIRY)
    assert all(w[3].astimezone(IST).date() >= min(listed, w[4].astimezone(IST).date()) for w in fut_windows[1:])   # W: from the listing


def test_no_future_in_the_master_leaves_the_volume_and_says_null(monkeypatch, master):
    broker = _index_broker(master, index_volume_each=5.0)
    headers, _ = _setup(monkeypatch, broker, "nofuture")
    body = _post(headers, symbols=["NIFTY BANK"], timeframe="5min", lookback_days=3)
    item = body["symbols"]["NIFTY BANK"]
    assert item["error"] is None and item["volume_source"] is None
    monkeypatch.setattr(index_volume, "MASTER_BROKER", "ui24iv-empty")                 # a missing master
    body = _post(headers, symbols=["NIFTY 50"], timeframe="1min", lookback_days=3)
    assert body["symbols"]["NIFTY 50"]["volume_source"] is None and {b["volume"] for b in body["symbols"]["NIFTY 50"]["bars"]} == {5.0}


def test_budget_refusal_for_the_future_keeps_the_index_volume(monkeypatch, master):
    broker = _index_broker(master, index_volume_each=5.0)
    headers, _ = _setup(monkeypatch, broker, "budget")
    _generous(monkeypatch, per_minute=2)                                    # the index's two calls (history, today), no more
    body = _post(headers, symbols=["NIFTY 50"], timeframe="5min", lookback_days=3)
    item = body["symbols"]["NIFTY 50"]
    assert item["error"] is None and item["count"] == 6 and item["volume_source"] is None
    assert {b["volume"] for b in item["bars"]} == {25.0}
    assert any(f"volume from {master['near_symbol']} not loaded this time (budget used)" in w for w in body["warnings"])
    assert not any(w[0] == master["near_symbol"] for w in broker.windows)   # refused before reaching the broker


def test_the_future_goes_through_the_requesting_tenants_own_session(monkeypatch, master):
    a_headers, a = _owner(f"ui24iv-a-{uuid.uuid4().hex[:6]}@example.com")
    b_headers, b = _owner(f"ui24iv-b-{uuid.uuid4().hex[:6]}@example.com")
    _store_credentials(a)
    _store_credentials(b)
    name = f"ui24ivt{uuid.uuid4().hex[:4]}"
    brokers = {a["tenant_id"]: _index_broker(master), b["tenant_id"]: _index_broker(master)}
    for broker in brokers.values():
        broker.name = name                                                  # one broker: the public bars are shared
    seen = []

    def build(record):
        seen.append(record.tenant_id)
        return brokers[record.tenant_id]
    _setup(monkeypatch, brokers[a["tenant_id"]], "tenant-x")
    monkeypatch.setattr(candles_routes, "build_adapter", build)
    rb = _post(b_headers, symbols=["NIFTY 50"], timeframe="5min", lookback_days=3)
    assert seen == [b["tenant_id"]] and brokers[a["tenant_id"]].windows == []
    assert any(w[0] == master["near_symbol"] for w in brokers[b["tenant_id"]].windows)
    ra = _post(a_headers, symbols=["NIFTY 50"], timeframe="5min", lookback_days=3)
    assert seen == [b["tenant_id"], a["tenant_id"]]
    assert ra["symbols"]["NIFTY 50"]["bars"] == rb["symbols"]["NIFTY 50"]["bars"]   # the same public bars, A's own session asked


@pytest.fixture()
def byob():
    tenants: List[int] = []
    yield lambda tenant_id: (tenants.append(tenant_id), _flag(False, tenants))
    _flag(False, [])


def test_byob_the_future_passes_the_same_entitlement_as_the_index(monkeypatch, master, byob):
    broker = _index_broker(master)
    headers, me = _setup(monkeypatch, broker, "byob")
    byob(me["tenant_id"])
    real = entitlement.resolve
    asked = []

    async def resolve(session, user, symbol, exchange="NSE", segment=None, **kw):
        asked.append((symbol, exchange, kw.get("broker"), kw.get("account_label")))
        return await real(session, user, symbol, exchange, segment, **kw)
    monkeypatch.setattr(entitlement, "resolve", resolve)
    body = _post(headers, symbols=["NIFTY 50"], timeframe="5min", lookback_days=3)
    assert body["data_tier"]["tier"] == "A" and body["symbols"]["NIFTY 50"]["volume_source"]["symbol"] == master["near_symbol"]
    assert asked == [(master["near_symbol"], "NFO", "upstox", "primary")]

    async def refused(session, user, symbol, exchange="NSE", segment=None, **kw):
        return Entitlement(tier=DataTier.EOD, symbol=symbol, exchange=exchange, reason="outside your session budget")
    monkeypatch.setattr(entitlement, "resolve", refused)
    shared_candles.reset()
    broker.windows.clear()
    body = _post(headers, symbols=["NIFTY 50"], timeframe="5min", lookback_days=3)
    assert body["symbols"]["NIFTY 50"]["volume_source"] is None and {b["volume"] for b in body["symbols"]["NIFTY 50"]["bars"]} == {0.0}
    assert any("not shown (outside your session budget)" in w for w in body["warnings"])
    assert not any(w[0] == master["near_symbol"] for w in broker.windows)
