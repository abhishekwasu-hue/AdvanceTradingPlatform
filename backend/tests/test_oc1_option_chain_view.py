"""OC1: the automatic Option chain page's server side (app/option_chain/chain_view.py, routes in app/option_chain/routes.py).

Every chain here is hand-made (round numbers, no market data). Each summary value is worked out by hand in the test:
- ATM strike: the strike nearest the spot, a tie taking the lower strike;
- PCR (OI): total put OI / total call OI;
- max pain: the settlement strike with the smallest total payout to option holders;
- ATM IV: the mean of the call and put IV at the ATM strike, in percent;
- days to expiry: calendar days from today (IST);
- the strikes with the highest call OI and put OI.
Plus the expiry list and its default (the next expiry on an expiry day), the OI-change states and the two routes.
"""
import asyncio
import json
from datetime import date, datetime, timedelta, timezone

import pytest

from app.brokers.models import OptionChain, OptionChainRow, Quote
from app.cache import client as cache_client
from app.instruments import expiry_data
from app.market_data import api_budget, candles_routes
from app.market_data.calendar import SessionStatus
from app.market_data.stream import tick_cache
from app.option_chain import chain_view, routes as oc_routes
from app.option_chain.snapshots import IST
from tests.test_auth_api import client
from tests.test_phase_aa_market_data_api import _Broker, _store_credentials
from tests.test_phase_k_commercial import _owner

run = asyncio.run
NOW = datetime(2026, 10, 21, 11, 0, tzinfo=IST)          # a Wednesday, during the session
EXPIRY = date(2026, 10, 27)


def _rows(change: bool = False):
    """Four strikes; worked out by hand below."""
    return [
        OptionChainRow(strike=100, call_oi=10, put_oi=60, call_ltp=15.0, put_ltp=0.5,
                       call_change_oi=2 if change else None, put_change_oi=-5 if change else None),
        OptionChainRow(strike=110, call_oi=20, put_oi=40, call_ltp=7.0, put_ltp=2.5, call_iv=14.0, put_iv=0.16,
                       call_volume=300, put_volume=200, call_bid=6.9, call_ask=7.1),
        OptionChainRow(strike=120, call_oi=50, put_oi=30, call_ltp=2.0, put_ltp=7.5),
        OptionChainRow(strike=130, call_oi=40, put_oi=10, call_ltp=0.5, put_ltp=16.0),
    ]


def _chain(change: bool = False, spot=114.0):
    return OptionChain(underlying="NIFTY", expiry=EXPIRY.isoformat(), underlying_ltp=spot, rows=_rows(change))


class _MemoryCache:
    def __init__(self):
        self.store = {}

    async def get(self, key): return self.store.get(key)
    async def set(self, key, value, ex=None): self.store[key] = value


@pytest.fixture
def memory_cache(monkeypatch):
    cache = _MemoryCache()
    monkeypatch.setattr(cache_client, "_get_client", lambda: cache)
    return cache


# ------------------------------------------------------------------------------------------------ the summary

def test_summary_values_by_hand():
    oi = chain_view.OiChange("ready", "broker")
    view = chain_view.build_view(_chain(change=True), spot=114.0, future={"symbol": "NIFTY FUT", "expiry": "2026-10-27", "ltp": 115.0},
                                 expiry=EXPIRY, now=NOW, oi=oi)
    s = view["summary"]
    # |110 - 114| = 4 < |120 - 114| = 6
    assert s["atm_strike"] == 110
    # puts 60 + 40 + 30 + 10 = 140; calls 10 + 20 + 50 + 40 = 120
    assert s["total_put_oi"] == 140 and s["total_call_oi"] == 120
    assert s["pcr_oi"] == pytest.approx(140 / 120)
    # payout at 100: puts 40*10 + 30*20 + 10*30 = 1300; at 110: calls 10*10 + puts 30*10 + 10*20 = 600;
    # at 120: calls 10*20 + 20*10 + puts 10*10 = 500; at 130: calls 10*30 + 20*20 + 50*10 = 1200 -> 120
    assert s["max_pain"] == 120
    # ATM 110: call IV 14.0 (percent), put IV 0.16 (a fraction -> 16.0): mean 15.0
    assert s["atm_iv"] == pytest.approx(15.0)
    # 21 Oct -> 27 Oct
    assert s["days_to_expiry"] == 6
    assert s["max_call_oi_strike"] == 120 and s["max_put_oi_strike"] == 100
    assert s["spot"] == 114.0 and s["futures"]["ltp"] == 115.0
    assert view["expiry"] == "2026-10-27" and view["oi_change"] == {"status": "ready", "source": "broker", "since": None}


def test_rows_carry_both_legs_with_iv_greeks_and_broker_oi_change():
    view = chain_view.build_view(_chain(change=True), spot=114.0, future=None, expiry=EXPIRY, now=NOW,
                                 oi=chain_view.OiChange("ready", "broker"))
    rows = {r["strike"]: r for r in view["rows"]}
    assert [r["strike"] for r in view["rows"]] == [100, 110, 120, 130]
    atm = rows[110]
    assert atm["call"]["iv"] == 14.0 and atm["put"]["iv"] == pytest.approx(16.0)
    assert atm["call"]["volume"] == 300 and atm["call"]["bid"] == 6.9 and atm["call"]["ask"] == 7.1
    assert 0 < atm["call"]["delta"] < 1 and -1 < atm["put"]["delta"] < 0 and atm["call"]["gamma"] > 0
    assert rows[100]["call"]["oi_change"] == 2 and rows[100]["put"]["oi_change"] == -5
    # No broker IV at 120: solved from the option's own price (a real number, or None when the price cannot be solved).
    assert rows[120]["call"]["iv"] is None or rows[120]["call"]["iv"] > 0


def test_ties_and_missing_values():
    assert chain_view.atm_strike([100, 110, 120], 115.0) == 110          # equally near: the lower strike
    assert chain_view.atm_strike([100, 110], None) is None
    # Max pain on a tie: the lower strike (compute_max_pain, the same definition the OC2 analyses use). Payout 0 at both.
    tie = OptionChain(underlying="NIFTY", expiry=EXPIRY.isoformat(), underlying_ltp=105.0,
                      rows=[OptionChainRow(strike=110, call_oi=10, put_oi=0), OptionChainRow(strike=100, call_oi=0, put_oi=10)])
    assert chain_view.build_view(tie, spot=105.0, future=None, expiry=EXPIRY, now=NOW, oi=chain_view.OiChange("none", None))["summary"]["max_pain"] == 100
    assert chain_view.pcr_oi([OptionChainRow(strike=100, put_oi=5)]) is None   # no call OI: no ratio, never 0
    rows = [OptionChainRow(strike=100, call_oi=30), OptionChainRow(strike=90, call_oi=30)]
    assert chain_view.highest_oi_strike(rows, "call") == 90
    assert chain_view.highest_oi_strike([OptionChainRow(strike=100)], "put") is None
    view = chain_view.build_view(OptionChain(underlying="NIFTY", expiry="", rows=[]), spot=None, future=None, expiry=None,
                                 now=NOW, oi=chain_view.OiChange("none", None))
    assert view["rows"] == [] and view["summary"]["atm_strike"] is None and view["summary"]["max_pain"] is None
    assert view["summary"]["days_to_expiry"] is None and view["summary"]["atm_iv"] is None


def test_iv_units():
    assert chain_view.iv_percent(13.2) == 13.2
    assert chain_view.iv_percent(0.132) == pytest.approx(13.2)
    assert chain_view.iv_percent(0) is None and chain_view.iv_percent(None) is None and chain_view.iv_percent("x") is None


# ------------------------------------------------------------------------------------------------ expiries

def test_default_expiry_nearest_and_the_expiry_day_rule():
    days = [date(2026, 10, 27), date(2026, 11, 3), date(2026, 11, 9)]
    assert chain_view.default_expiry(days, date(2026, 10, 21)) == date(2026, 10, 27)     # the nearest
    assert chain_view.default_expiry(days, date(2026, 10, 27)) == date(2026, 11, 3)      # expiry day: the next one
    assert chain_view.default_expiry(days, date(2026, 10, 28)) == date(2026, 11, 3)
    assert chain_view.default_expiry([date(2026, 11, 9)], date(2026, 11, 9)) == date(2026, 11, 9)  # nothing after it
    assert chain_view.default_expiry([], date(2026, 10, 21)) is None
    assert chain_view.default_expiry([date(2026, 10, 1)], date(2026, 10, 21)) is None


def test_expiry_file_list_and_kinds(monkeypatch):
    rows = [(date(2026, 10, 13), "weekly", date(2026, 9, 1)), (date(2026, 10, 27), "monthly", date(2026, 8, 3)),
            (date(2026, 11, 3), "weekly", date(2026, 9, 28)), (date(2026, 11, 24), "monthly", date(2026, 8, 31))]
    monkeypatch.setattr(expiry_data, "listed", lambda symbol: rows if symbol == "NIFTY" else [])
    items = chain_view.from_expiry_file("NIFTY", date(2026, 10, 21))
    assert [(i.day, i.kind) for i in items] == [(date(2026, 10, 27), "monthly"), (date(2026, 11, 3), "weekly"), (date(2026, 11, 24), "monthly")]
    assert chain_view.default_expiry([i.day for i in items], date(2026, 10, 27)) == date(2026, 11, 3)


def test_the_repo_expiry_file_answers_in_order():
    items = chain_view.from_expiry_file("NIFTY", date(2026, 10, 21))
    assert items and all(i.day >= date(2026, 10, 21) for i in items) and [i.day for i in items] == sorted(i.day for i in items)
    assert {i.kind for i in items} <= {"weekly", "monthly"} and len(items) <= chain_view.MAX_EXPIRIES


def test_master_list_kinds_by_month():
    days = [date(2026, 10, 20), date(2026, 10, 27), date(2026, 11, 3), date(2026, 11, 24)]
    items = chain_view.from_master(days, date(2026, 10, 21))
    assert [(i.day, i.kind) for i in items] == [(date(2026, 10, 27), "monthly"), (date(2026, 11, 3), "weekly"), (date(2026, 11, 24), "monthly")]


def test_sensex_rule_calendar_moves_a_holiday_expiry_and_keeps_the_rule():
    # SENSEX weekly on Thursday (rule table); 22 Oct 2026 made a holiday here -> that week's expiry is Wed 21 Oct.
    items = chain_view.from_rules("SENSEX", date(2026, 10, 21), {date(2026, 10, 22)})
    days = [i.day for i in items]
    assert days[:2] == [date(2026, 10, 21), date(2026, 10, 29)]
    assert dict((i.day, i.kind) for i in items)[date(2026, 10, 29)] == "monthly"
    # That day is the expiry day itself: the page opens on the next one.
    assert chain_view.default_expiry(days, date(2026, 10, 21)) == date(2026, 10, 29)
    assert chain_view.from_rules("NIFTY", date(2026, 10, 21), set()) == []        # NIFTY has no weekday rule


# ------------------------------------------------------------------------------------------------ OI change

def test_oi_change_builds_from_the_first_reading(memory_cache):
    chain = _chain()
    first = run(chain_view.oi_change_state(chain, tenant_id=7, underlying="NIFTY", now=NOW, market_open=True))
    assert first.status == "building" and first.source == "first_reading" and first.since == NOW
    view = chain_view.build_view(chain, spot=114.0, future=None, expiry=EXPIRY, now=NOW, oi=first)
    assert all(r["call"]["oi_change"] is None and r["put"]["oi_change"] is None for r in view["rows"])
    soon = run(chain_view.oi_change_state(chain, tenant_id=7, underlying="NIFTY", now=NOW + timedelta(minutes=2), market_open=True))
    assert soon.status == "building" and soon.since == NOW
    later_rows = _rows()
    later_rows[2].call_oi = 65                                                 # 50 -> 65
    later = OptionChain(underlying="NIFTY", expiry=EXPIRY.isoformat(), underlying_ltp=114.0, rows=later_rows)
    ready = run(chain_view.oi_change_state(later, tenant_id=7, underlying="NIFTY", now=NOW + timedelta(minutes=6), market_open=True))
    assert ready.status == "ready" and ready.source == "first_reading" and ready.since == NOW
    rows = {r["strike"]: r for r in chain_view.build_view(later, spot=114.0, future=None, expiry=EXPIRY, now=NOW, oi=ready)["rows"]}
    assert rows[120]["call"]["oi_change"] == 15 and rows[110]["put"]["oi_change"] == 0
    # Another organisation keeps its own first reading.
    other = run(chain_view.oi_change_state(chain, tenant_id=8, underlying="NIFTY", now=NOW + timedelta(minutes=6), market_open=True))
    assert other.status == "building"


def test_oi_change_market_closed_without_a_reading_and_from_the_broker(memory_cache):
    closed = run(chain_view.oi_change_state(_chain(), tenant_id=9, underlying="NIFTY", now=NOW, market_open=False))
    assert closed.status == "none" and memory_cache.store == {}
    broker = run(chain_view.oi_change_state(_chain(change=True), tenant_id=9, underlying="NIFTY", now=NOW, market_open=False))
    assert broker.status == "ready" and broker.source == "broker"


# ------------------------------------------------------------------------------------------------ the routes

class _ChainBroker(_Broker):
    def __init__(self, chain):
        super().__init__()
        self.chain, self.asked = chain, []

    async def get_option_chain(self, underlying, expiry=None):
        self.asked.append((underlying, expiry))
        return self.chain.model_copy(update={"underlying": underlying, "expiry": (expiry or EXPIRY).isoformat()})

    async def get_quote_for_symbol(self, symbol, exchange="NSE"):
        price = {"NIFTY 50": 114.0, "NIFTY26OCTFUT": 115.5}.get(symbol)
        return Quote(symbol=symbol, ltp=price, timestamp=datetime.now(timezone.utc)) if price else None

    async def get_ltp_for_symbol(self, symbol, exchange="NSE"):
        return 0.0


def _setup_routes(monkeypatch, chain, market_open=True, today=date(2026, 10, 21)):
    broker = _ChainBroker(chain)
    monkeypatch.setattr(candles_routes, "build_adapter", lambda record: broker)
    monkeypatch.setattr(chain_view, "today_ist", lambda now=None: today)

    async def master_days(session, underlying, day):
        return [date(2026, 10, 21), date(2026, 10, 27), date(2026, 11, 3)] if underlying == "NIFTY" else []

    async def future(session, underlying, day):
        return chain_view.FutureContract("NIFTY26OCTFUT", "NFO", date(2026, 10, 27)) if underlying == "NIFTY" else None

    async def status(session, now=None, exchange="NSE"):
        return SessionStatus(market_open, "test", None)

    monkeypatch.setattr(chain_view, "master_expiries", master_days)
    monkeypatch.setattr(chain_view, "near_future", future)
    monkeypatch.setattr(oc_routes, "market_session_status", status)
    api_budget.reset()
    tick_cache.clear()
    return broker


def test_routes_need_a_login_and_a_broker():
    assert client.get("/api/option-chain/NIFTY/expiries").status_code == 401
    assert client.get("/api/option-chain/NIFTY/live").status_code == 401
    headers, _ = _owner("oc1-nobroker@example.com")
    res = client.get("/api/option-chain/NIFTY/live", headers=headers)
    assert res.status_code == 409


def test_expiries_route_defaults_and_the_expiry_day(monkeypatch):
    headers, me = _owner("oc1-expiries@example.com")
    _setup_routes(monkeypatch, _chain(change=True), today=date(2026, 10, 20))
    body = client.get("/api/option-chain/nifty/expiries", headers=headers).json()
    assert body["underlying"] == "NIFTY" and body["source"] == "master" and body["default"] == "2026-10-21"
    assert [e["date"] for e in body["expiries"]] == ["2026-10-21", "2026-10-27", "2026-11-03"]
    _setup_routes(monkeypatch, _chain(change=True), today=date(2026, 10, 21))
    body = client.get("/api/option-chain/NIFTY%2050/expiries", headers=headers).json()
    assert body["underlying"] == "NIFTY" and body["default"] == "2026-10-27"            # expiry day: the next one
    assert client.get("/api/option-chain/%3Cx%3E/expiries", headers=headers).status_code == 422


def test_live_route_reads_the_chain_spot_and_future_through_the_broker(monkeypatch, memory_cache):
    headers, me = _owner("oc1-live@example.com")
    _store_credentials(me)
    broker = _setup_routes(monkeypatch, _chain(change=True, spot=None))
    res = client.get("/api/option-chain/NIFTY/live?expiry=2026-10-27", headers=headers)
    assert res.status_code == 200, res.text
    body = res.json()
    assert broker.asked == [("NIFTY", date(2026, 10, 27))]
    s = body["summary"]
    assert s["spot"] == 114.0 and s["futures"] == {"symbol": "NIFTY26OCTFUT", "expiry": "2026-10-27", "ltp": 115.5}
    assert s["atm_strike"] == 110 and s["max_pain"] == 120 and s["days_to_expiry"] == 6 and s["pcr_oi"] == pytest.approx(140 / 120)
    assert body["oi_change"]["status"] == "ready" and body["market_open"] is True and len(body["rows"]) == 4
    # No expiry asked: the default (the next expiry on the 21 Oct expiry day).
    broker.asked.clear()
    body = client.get("/api/option-chain/NIFTY/live", headers=headers).json()
    assert broker.asked == [("NIFTY", date(2026, 10, 27))] and body["expiry"] == "2026-10-27"


def test_live_route_oi_change_building_without_broker_change(monkeypatch, memory_cache):
    headers, me = _owner("oc1-building@example.com")
    _store_credentials(me)
    _setup_routes(monkeypatch, _chain(change=False))
    body = client.get("/api/option-chain/NIFTY/live?expiry=2026-10-27", headers=headers).json()
    assert body["oi_change"]["status"] == "building" and body["oi_change"]["source"] == "first_reading"
    assert all(r["call"]["oi_change"] is None for r in body["rows"])
    # The stored first reading, ten minutes older: the change shows (here 0 - the chain did not move).
    key = next(iter(memory_cache.store))
    stored = json.loads(memory_cache.store[key])
    stored["at"] = (datetime.fromisoformat(stored["at"]) - timedelta(minutes=10)).isoformat()
    memory_cache.store[key] = json.dumps(stored)
    body = client.get("/api/option-chain/NIFTY/live?expiry=2026-10-27", headers=headers).json()
    assert body["oi_change"]["status"] == "ready" and body["rows"][0]["call"]["oi_change"] == 0


def test_live_route_market_closed_shows_the_last_chain_without_oi_change(monkeypatch, memory_cache):
    headers, me = _owner("oc1-closed@example.com")
    _store_credentials(me)
    _setup_routes(monkeypatch, _chain(change=False), market_open=False)
    body = client.get("/api/option-chain/NIFTY/live?expiry=2026-10-27", headers=headers).json()
    assert body["market_open"] is False and body["oi_change"]["status"] == "none" and len(body["rows"]) == 4
