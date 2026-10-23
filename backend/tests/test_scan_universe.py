"""Owner order (23 Oct) "the scan universe is never empty" (app/screener/universes.py): the lists Find setups can scan
(system index-stock / sector / F&O lists, NIFTY 50 first; the stored NIFTY 50 membership when the system lists are not
built), one list's symbols, the batch plan sized from the broker's API share, and a batch run whose every broker call
takes a token from the credential's shared API budget (a symbol without one is reported, the rest still run)."""
import asyncio
import json
import types
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import delete

from app.db.models import IndexMembershipRecord, SecurityRecord, SystemWatchlistRecord
from tests.test_auth_api import _session_factory, client  # noqa: F401 - fixtures
from tests.test_phase_l_ai import _owner
from tests.test_s1d_screener_api import _daily_bars, _flag

U = "/api/screener/universes"


def _run(coro):
    return asyncio.run(coro)


def _list(key, group, name, symbols, position=0, exchange="NSE"):
    items = [{"symbol": s, "exchange": exchange, "name": s.title()} for s in symbols]
    return SystemWatchlistRecord(key=key, group=group, name=name, position=position, items_json=json.dumps(items), count=len(items),
                                 checksum="x" * 64)


def _seed(rows=(), members=()):
    async def go():
        async with _session_factory() as session:
            for model in (SystemWatchlistRecord, IndexMembershipRecord, SecurityRecord):
                await session.execute(delete(model))
            session.add_all(list(rows))
            for i, (isin, symbol, weight) in enumerate(members):
                session.add(SecurityRecord(isin=isin, symbol=symbol, name=symbol.title(), series="EQ", source="fixture",
                                          checksum=str(i)))
                session.add(IndexMembershipRecord(index_code="NIFTY50", isin=isin, weight=weight, valid_from=date(2026, 10, 1),
                                                  source="fixture", checksum=str(i)))
            await session.commit()
    _run(go())


@pytest.fixture
def flag_on():
    _flag(True)
    yield
    _flag(False)


@pytest.fixture(autouse=True)
def clean():
    yield
    _seed()


def test_universes_are_flagged_with_the_screener():
    headers, _ = _owner("su-off@example.com")
    assert client.get(U, headers=headers).status_code == 503
    assert client.get(f"{U}/index-nifty50", headers=headers).status_code == 503
    assert client.get(U).status_code == 401                                       # never anonymous


def test_the_lists_nifty_50_first_and_stock_lists_only(flag_on):
    headers, _ = _owner("su-lists@example.com")
    _seed([_list("indices", "INDICES", "Indices", ["NIFTY 50"]),
           _list("index-niftybank", "INDEX_STOCKS", "NIFTY BANK stocks (2)", ["HDFCBANK", "ICICIBANK"], position=0),
           _list("index-nifty50", "INDEX_STOCKS", "NIFTY 50 stocks (3)", ["RELIANCE", "TCS", "INFY"], position=1),
           _list("sector-it", "SECTORS", "IT", ["TCS", "INFY"]),
           _list("fno-stocks", "FNO", "F&O stocks (2)", ["SBIN", "TCS"])])
    body = client.get(U, headers=headers).json()
    assert body["default_key"] == "index-nifty50"
    assert [g["group"] for g in body["groups"]] == ["INDEX_STOCKS", "SECTORS", "FNO"]        # the Indices list is not a stock list
    assert [x["key"] for x in body["groups"][0]["lists"]] == ["index-nifty50", "index-niftybank"]
    assert body["batch"]["size"] >= 5 and body["batch"]["size"] <= 50 and body["batch"]["max_symbols"] == 500
    one = client.get(f"{U}/index-nifty50", headers=headers).json()
    assert one["symbols"] == ["RELIANCE", "TCS", "INFY"] and one["truncated"] is False and one["exchange"] == "NSE"
    assert client.get(f"{U}/indices", headers=headers).status_code == 404                     # not offered, not served
    assert client.get(f"{U}/no-such-list", headers=headers).status_code == 404
    assert client.get(f"{U}/BAD KEY!", headers=headers).status_code == 404


def test_without_system_lists_nifty_50_comes_from_the_stored_membership(flag_on):
    headers, _ = _owner("su-members@example.com")
    _seed(members=[("INE000000001", "AAA", 1.0), ("INE000000002", "BBB", 9.0), ("INE000000003", "CCC", None)])
    body = client.get(U, headers=headers).json()
    assert body["groups"][0]["lists"] == [{"key": "index-nifty50", "name": "NIFTY 50 stocks (3)", "count": 3}]
    assert client.get(f"{U}/index-nifty50", headers=headers).json()["symbols"] == ["BBB", "AAA", "CCC"]   # by weight
    _seed()
    assert client.get(U, headers=headers).json()["groups"] == []                  # nothing stored: the page's own fallback
    assert client.get(f"{U}/index-nifty50", headers=headers).status_code == 404


def test_a_long_list_is_cut_to_the_universe_cap_and_says_so(flag_on):
    headers, _ = _owner("su-long@example.com")
    many = [f"S{i:03d}" for i in range(620)]
    _seed([_list("index-niftytotalmarket", "INDEX_STOCKS", "NIFTY TOTAL MARKET stocks (620)", many)])
    one = client.get(f"{U}/index-niftytotalmarket", headers=headers).json()
    assert len(one["symbols"]) == 500 and one["truncated"] is True and one["source_count"] == 620
    assert client.get(U, headers=headers).json()["groups"][0]["lists"][0]["count"] == 500


def test_the_batch_plan_follows_the_api_share():
    from app.market_data.api_budget import api_limits
    from app.screener.universes import BATCH_MAX, batch_plan, batch_wait_seconds
    for broker in ("upstox", "zerodha", "dhan", ""):
        plan, limits = batch_plan(broker), api_limits(broker)
        assert 5 <= plan["size"] <= BATCH_MAX and plan["per_minute"] == limits.per_minute
        assert plan["size"] <= max(5, limits.per_second * 10)                      # a batch fetches in about ten seconds
        assert batch_wait_seconds(broker, plan["size"]) <= 15
    assert batch_plan("upstox")["size"] > batch_plan("zerodha")["size"]


class _Broker:
    name = "zerodha"

    def __init__(self):
        self.calls = []

    async def get_historical_data(self, symbol, exchange, interval, start=None, end=None):
        self.calls.append(symbol)
        return _daily_bars(100, 1.0)


def _patch(monkeypatch, broker, verdicts):
    from app.brokers import token_lifecycle
    from app.market_data import api_budget, candles_routes, live_access, service

    async def pick(*a, **k):
        return types.SimpleNamespace(broker_name="zerodha", id=7)

    async def byob_off(session, tenant_id):
        return False

    async def candles(self, symbol, exchange="NSE", interval="1min", now=None, fresh=False):
        return await self.broker.get_historical_data(symbol, exchange, interval)
    asked = []

    async def attempt(record_id, name, kind="quote"):
        asked.append((record_id, name, kind))
        return verdicts.pop(0) if verdicts else -2
    monkeypatch.setattr(candles_routes, "_pick_record", pick)
    monkeypatch.setattr(token_lifecycle, "build_adapter", lambda record: broker)
    monkeypatch.setattr(service.MarketDataService, "get_candles", candles)
    monkeypatch.setattr(live_access, "byob_on", byob_off)
    monkeypatch.setattr(api_budget, "_attempt", attempt)
    return asked


def test_a_batch_takes_a_budget_token_per_broker_call(monkeypatch):
    from app.market_data.api_budget import api_limits
    from app.screener.routes import BUDGET_WAIT, fetch_frames
    broker = _Broker()
    asked = _patch(monkeypatch, broker, [1, 1])                  # two tokens, then the minute is full
    budget: dict = {}
    now = datetime(2026, 3, 5, 12, tzinfo=timezone.utc) + timedelta(days=0)
    universe, problems, source = _run(fetch_frames(None, 1, ["AAA", "BBB", "CCC", "DDD"], "NSE", "1d", {"1d": 20}, now=now,
                                                   budget_out=budget))
    assert sorted(broker.calls) == sorted(d.symbol for d in universe) and len(universe) == 2   # no token, no broker call
    assert len(problems) == 2 and all(why == BUDGET_WAIT for why in problems.values())
    assert budget == {"broker_calls": 2, "refused": 2, "per_minute": api_limits("zerodha").per_minute}
    assert all(a[:2] == (7, "zerodha") and a[2] == "history" for a in asked)
    assert source == "broker:zerodha"


def test_an_ordinary_run_is_unchanged_no_budget_token(monkeypatch):
    from app.screener.routes import fetch_frames
    broker = _Broker()
    asked = _patch(monkeypatch, broker, [])
    universe, problems, _ = _run(fetch_frames(None, 1, ["AAA", "BBB"], "NSE", "1d", {"1d": 20}))
    assert len(universe) == 2 and problems == {} and asked == []                 # as before: the concurrency cap only


def test_the_run_route_returns_a_batch_budget(flag_on, monkeypatch):
    headers, _ = _owner("su-batch@example.com")
    broker = _Broker()
    _patch(monkeypatch, broker, [1, 1, 1])
    out = client.post("/api/screener/run", json={"source": "close > 1", "symbols": ["AAA", "BBB", "CCC", "DDD"], "batch": True}, headers=headers)
    assert out.status_code == 200, out.text
    body = out.json()
    assert body["budget"]["broker_calls"] == 3 and body["budget"]["refused"] == 1 and body["scanned"] == 4
    assert len(body["matched"]) == 3
    plain = client.post("/api/screener/run", json={"source": "close > 1", "symbols": ["AAA"]}, headers=headers).json()
    assert "budget" not in plain                                                  # an ordinary run: as before
    assert client.post("/api/screener/run", json={"source": "close > 1", "symbols": ["A"] * 51, "batch": True},
                       headers=headers).status_code == 422                        # a batch never exceeds the run cap
