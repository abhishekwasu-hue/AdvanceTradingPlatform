"""UI2-4a2-pre: `POST /api/market-data/quotes` - the watchlist's batch quotes.

* one broker multi-quote call for many symbols, counted once against the credential's quote budget;
* no multi-quote call: the live feed's fresh ticks first (free), then one quote per symbol within the budget;
* the budget refuses: nothing is called and every symbol says "budget used" (never a made-up price);
* a symbol with no price comes back missing with `ltp: null` - never 0;
* tenant-scoped (another organisation's broker is never used), the BT1 entitlement guard (Tier C 409), the per-user rate
  limit, and the adapters' own multi-quote calls (Kite, Upstox, Angel One: one HTTP call each, previous close parsed).
No network: fake brokers and mock HTTP transports."""
import asyncio
import gzip
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import List
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from app.brokers.angel_one import AngelOneBroker
from app.brokers.contract_symbols import wrap_contract_symbols
from app.brokers.models import BrokerCredentials, Quote
from app.brokers.upstox import UpstoxBroker
from app.brokers.zerodha import ZerodhaBroker
from app.core import rate_limit
from app.market_data import api_budget, quotes_routes
from app.market_data.stream import Tick, tick_cache
from app.platform import controls
from tests.test_auth_api import _session_factory, client
from tests.test_phase_aa_market_data_api import _Broker, _store_credentials
from tests.test_phase_k_commercial import _owner

run = asyncio.run
UTC = timezone.utc


def _email(tag: str) -> str:
    return f"ui24q-{tag}-{uuid.uuid4().hex[:8]}@example.com"


def _sym(prefix: str) -> str:
    """A symbol no other test has a tick or a shared quote for."""
    return f"{prefix}{uuid.uuid4().hex[:5].upper()}"


class _MultiBroker(_Broker):
    """A broker with a multi-quote call. Prices: 100 + index; `absent` are left out, `zero` come back at 0."""
    multi_quote = True

    def __init__(self, absent=(), zero=(), fail=False):
        super().__init__()
        self.batch_calls: List[list] = []
        self.single_calls = 0
        self.absent, self.zero, self.fail = set(absent), set(zero), fail

    async def get_quotes_batch(self, items):
        self.batch_calls.append(list(items))
        if self.fail:
            raise RuntimeError("socket closed by broker")
        now = datetime.now(UTC)
        out = {}
        for i, (exchange, symbol) in enumerate(items):
            if symbol in self.absent:
                continue
            price = 0.0 if symbol in self.zero else 100.0 + i
            out[(exchange, symbol)] = Quote(symbol=symbol, ltp=price, prev_close=price - 2 if price else None,
                                            timestamp=now - timedelta(seconds=1))
        return out

    async def get_quote_for_symbol(self, symbol, exchange="NSE"):
        self.single_calls += 1
        raise AssertionError("a multi-quote broker must not be asked per symbol")


class _SingleBroker(_Broker):
    """No multi-quote call: one quote per symbol."""

    def __init__(self, zero=()):
        super().__init__()
        self.single_calls: List[str] = []
        self.zero = set(zero)

    async def get_quote_for_symbol(self, symbol, exchange="NSE"):
        self.single_calls.append(symbol)
        return Quote(symbol=symbol, ltp=0.0 if symbol in self.zero else 50.0, timestamp=datetime.now(UTC))

    async def get_ltp_for_symbol(self, symbol, exchange="NSE"):
        self.single_calls.append(symbol + ":ltp")
        return 0.0


@pytest.fixture(autouse=True)
def _clean():
    api_budget.reset()
    rate_limit.reset("market_data_quotes")
    yield
    api_budget.reset()
    rate_limit.reset("market_data_quotes")


def _count_tokens(monkeypatch, grant=True):
    taken: List[int] = []

    async def take(record_id, broker, wait_seconds=None, kind="quote"):
        taken.append(record_id)
        return grant(len(taken)) if callable(grant) else grant
    monkeypatch.setattr(api_budget, "take", take)
    return taken


def _post(headers, items, **extra):
    return client.post("/api/market-data/quotes", headers=headers, json={"items": items, **extra})


def test_one_broker_call_for_the_whole_list_counted_once_and_missing_is_never_zero(monkeypatch):
    headers, me = _owner(_email("batch"))
    _store_credentials(me)
    broker = _MultiBroker(absent={"NOSUCH"}, zero={"HALTED"})
    monkeypatch.setattr(quotes_routes, "build_adapter", lambda record: broker)
    taken = _count_tokens(monkeypatch)
    names = [_sym("S") for _ in range(12)]
    items = [{"symbol": n.lower(), "exchange": "nse"} for n in names] + [{"symbol": "NOSUCH"}, {"symbol": "HALTED"},
                                                                         {"symbol": "NIFTY 50", "exchange": "NSE"}, {"symbol": names[0]}]
    res = _post(headers, items)
    assert res.status_code == 200, res.text
    body = res.json()
    assert len(broker.batch_calls) == 1 and broker.single_calls == 0          # ONE broker call for 15 symbols
    assert len(taken) == 1 and body["broker_calls"] == 1                       # counted once against the budget
    assert len(broker.batch_calls[0]) == 15                                    # the duplicate asked once
    rows = body["items"]
    assert [r["symbol"] for r in rows] == names + ["NOSUCH", "HALTED", "NIFTY 50"]
    first = rows[0]
    assert first["ltp"] == 100.0 and first["prev_close"] == 98.0 and first["change"] == 2.0
    assert first["change_pct"] == pytest.approx(2.0 / 98.0 * 100, abs=1e-3)
    assert first["missing"] is False and first["source"] == "quote" and first["stale"] is False and first["timestamp"]
    for row in (rows[12], rows[13]):                                           # not returned / returned at 0
        assert row["missing"] is True and row["ltp"] is None and row["change"] is None and row["error"]
    assert body["missing"] == 2 and body["source"]["broker"] == "upstox"


def test_a_failed_batch_reports_the_brokers_words_and_never_a_price(monkeypatch):
    headers, me = _owner(_email("fail"))
    _store_credentials(me)
    monkeypatch.setattr(quotes_routes, "build_adapter", lambda record: _MultiBroker(fail=True))
    _count_tokens(monkeypatch)
    body = _post(headers, [{"symbol": _sym("A")}, {"symbol": _sym("B")}]).json()
    assert all(r["missing"] and r["ltp"] is None and "socket closed" in r["error"] for r in body["items"])
    assert body["broker_calls"] == 1 and "socket closed" in body["warnings"][0]


def test_budget_refused_means_no_broker_call_and_every_symbol_says_budget_used(monkeypatch):
    headers, me = _owner(_email("budget"))
    _store_credentials(me)
    broker = _MultiBroker()
    monkeypatch.setattr(quotes_routes, "build_adapter", lambda record: broker)
    _count_tokens(monkeypatch, grant=False)
    body = _post(headers, [{"symbol": _sym("A")}, {"symbol": _sym("B")}]).json()
    assert broker.batch_calls == [] and body["broker_calls"] == 0
    assert all(r["missing"] and r["ltp"] is None and r.get("budget_used") for r in body["items"])
    assert any("budget used" in w for w in body["warnings"])


def test_the_real_budget_is_shared_with_ltp_and_refuses_once_the_minute_is_used(monkeypatch):
    """With the credential's API budget at one call a minute, the first poll takes it and the next is refused at once
    (no wait, no broker call) - the same budget /ltp draws from."""
    headers, me = _owner(_email("realbudget"))
    _store_credentials(me)
    broker = _MultiBroker()
    monkeypatch.setattr(quotes_routes, "build_adapter", lambda record: broker)
    monkeypatch.setattr(rate_limit, "redis_backed", lambda: False)
    monkeypatch.setattr(api_budget, "api_limits", lambda name: api_budget.ApiLimits(per_second=1, per_minute=1))
    first = _post(headers, [{"symbol": _sym("A")}, {"symbol": _sym("B")}]).json()
    assert first["broker_calls"] == 1 and first["missing"] == 0
    second = _post(headers, [{"symbol": _sym("A")}, {"symbol": _sym("B")}]).json()
    assert second["broker_calls"] == 0 and second["missing"] == 2 and len(broker.batch_calls) == 1


def test_without_a_multi_quote_call_ticks_first_then_per_symbol_quotes_within_the_budget(monkeypatch):
    headers, me = _owner(_email("single"))
    _store_credentials(me)
    ticked, a, b, c = _sym("T"), _sym("A"), _sym("B"), _sym("C")
    now = datetime.now(UTC)
    run(tick_cache.put(Tick(broker="upstox", exchange="NSE", symbol=ticked, ltp=321.5, exchange_ts=now - timedelta(seconds=1), received_at=now)))
    broker = _SingleBroker(zero={b})
    monkeypatch.setattr(quotes_routes, "build_adapter", lambda record: broker)
    taken = _count_tokens(monkeypatch, grant=lambda n: n <= 3)               # three tokens, then the budget is used
    body = _post(headers, [{"symbol": ticked}, {"symbol": a}, {"symbol": b}, {"symbol": c}]).json()
    rows = {r["symbol"]: r for r in body["items"]}
    assert rows[ticked]["ltp"] == 321.5 and rows[ticked]["source"] == "tick" and rows[ticked]["change"] is None
    assert ticked not in broker.single_calls                                  # a fresh tick costs no broker call
    assert rows[a]["ltp"] == 50.0 and rows[a]["source"] == "quote"
    # b: its quote and its bare LTP were both 0 -> missing, never 0 (two tokens); c: no token left -> budget used.
    assert rows[b]["missing"] and rows[b]["ltp"] is None and not rows[b].get("budget_used")
    assert rows[c]["missing"] and rows[c]["ltp"] is None and rows[c]["budget_used"] is True
    assert broker.single_calls == [a, b, b + ":ltp"] and len(taken) == 4 and body["broker_calls"] == 3


def test_tenant_scoped_another_organisations_broker_is_never_used(monkeypatch):
    owner_headers, owner = _owner(_email("tenant-a"))
    _store_credentials(owner)
    other_headers, other = _owner(_email("tenant-b"))
    used = []

    def build(record):
        used.append(record.tenant_id)
        return _MultiBroker()
    monkeypatch.setattr(quotes_routes, "build_adapter", build)
    _count_tokens(monkeypatch)
    res = _post(other_headers, [{"symbol": "RELIANCE"}])
    assert res.status_code == 409 and "Settings > Brokers" in res.json()["detail"]
    assert _post(other_headers, [{"symbol": "RELIANCE"}], broker="upstox").status_code == 404
    assert used == []                                                          # tenant A's credential was never built
    assert _post(owner_headers, [{"symbol": "RELIANCE"}]).status_code == 200 and used == [owner["tenant_id"]]
    assert client.post("/api/market-data/quotes", json={"items": [{"symbol": "RELIANCE"}]}).status_code == 401


def test_validation_caps_the_list_at_fifty(monkeypatch):
    headers, me = _owner(_email("cap"))
    _store_credentials(me)
    monkeypatch.setattr(quotes_routes, "build_adapter", lambda record: _MultiBroker())
    _count_tokens(monkeypatch)
    assert _post(headers, [{"symbol": f"S{i}"} for i in range(51)]).status_code == 422
    assert _post(headers, []).status_code == 422
    assert _post(headers, [{"symbol": "   "}]).status_code == 422
    assert _post(headers, [{"symbol": f"S{i}"} for i in range(50)]).status_code == 200


def test_rate_limited_per_user(monkeypatch):
    headers, me = _owner(_email("rl"))
    _store_credentials(me)
    other_headers, other = _owner(_email("rl-other"))
    _store_credentials(other)
    monkeypatch.setattr(quotes_routes, "build_adapter", lambda record: _MultiBroker())
    monkeypatch.setattr(rate_limit, "redis_backed", lambda: False)
    _count_tokens(monkeypatch)
    for _ in range(30):
        assert _post(headers, [{"symbol": "RELIANCE"}]).status_code == 200
    assert _post(headers, [{"symbol": "RELIANCE"}]).status_code == 429
    assert _post(other_headers, [{"symbol": "RELIANCE"}]).status_code == 200    # per user, not per organisation


def _byob_flag(on: bool, tenants):
    async def go():
        async with _session_factory() as session:
            current = await controls._get(session, controls.KEY_FEATURE_FLAGS)
            flags = dict(current.get("flags") or {})
            flags["byob_data_tier"] = {"on": on, "tenants": list(tenants)}
            await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": flags}, None)
            await session.commit()
    run(go())


def test_byob_entitlement_tier_c_409_without_the_users_own_confirmed_session(monkeypatch):
    headers, me = _owner(_email("byob"))
    _store_credentials(me, broker="zerodha")                                   # execution-only broker: never Tier A
    monkeypatch.setattr(quotes_routes, "build_adapter", lambda record: _MultiBroker())
    _count_tokens(monkeypatch)
    _byob_flag(False, [me["tenant_id"]])
    try:
        res = _post(headers, [{"symbol": "RELIANCE"}, {"symbol": "TCS"}])
        assert res.status_code == 409 and res.headers.get("X-Data-Tier") == "C"
        _store_credentials(me, broker="upstox")                               # the user's own confirmed session
        res = _post(headers, [{"symbol": "RELIANCE"}])
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["source"]["broker"] == "upstox" and body["items"][0]["ltp"] == 100.0 and body["data_tier"]["tier"] == "A"
    finally:
        _byob_flag(False, [])


# --- the adapters' own multi-quote calls ---------------------------------------------------------------------------------

def test_kite_batch_is_one_quote_call_with_the_previous_close_and_exchange_time():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(parse_qs(urlparse(str(request.url)).query)["i"])
        return httpx.Response(200, json={"status": "success", "data": {
            "NSE:RELIANCE": {"last_price": 2900.0, "ohlc": {"open": 2880, "high": 2910, "low": 2870, "close": 2850.0},
                             "last_trade_time": "2026-10-20 10:15:00"},
            "NSE:NIFTY 50": {"last_price": 25000.0, "ohlc": {"close": 24900.0}},
        }})
    broker = ZerodhaBroker(BrokerCredentials(api_key="k", access_token="t"),
                           client=httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=ZerodhaBroker.BASE_URL))
    wrapped = wrap_contract_symbols(broker)
    assert wrapped.multi_quote is True
    got = run(wrapped.get_quotes_batch([("NSE", "RELIANCE"), ("NSE", "NIFTY 50"), ("NSE", "NOSUCH")]))
    assert len(seen) == 1 and seen[0] == ["NSE:RELIANCE", "NSE:NIFTY 50", "NSE:NOSUCH"]
    assert set(got) == {("NSE", "RELIANCE"), ("NSE", "NIFTY 50")}
    assert got[("NSE", "RELIANCE")].prev_close == 2850.0 and got[("NSE", "RELIANCE")].timestamp is not None
    assert got[("NSE", "RELIANCE")].timestamp.astimezone(UTC).hour == 4                # 10:15 IST


def test_upstox_batch_is_one_quote_call_matched_back_by_instrument_key():
    master = gzip.compress(json.dumps([
        {"instrument_key": "NSE_EQ|INE002A01018", "exchange": "NSE", "trading_symbol": "RELIANCE", "instrument_type": "EQ"},
        {"instrument_key": "NSE_EQ|INE467B01029", "exchange": "NSE", "trading_symbol": "TCS", "instrument_type": "EQ"},
    ]).encode())
    quote_calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        if "assets.upstox.com" in str(request.url):
            return httpx.Response(200, content=master)
        quote_calls.append(request.url.params["instrument_key"])
        return httpx.Response(200, json={"status": "success", "data": {
            "NSE_EQ:RELIANCE": {"instrument_token": "NSE_EQ|INE002A01018", "last_price": 2900.0, "net_change": 50.0,
                                "ohlc": {"close": 2900.0}, "last_trade_time": "1760935500000"},
            "NSE_EQ:TCS": {"instrument_token": "NSE_EQ|INE467B01029", "last_price": 4000.0},
        }})
    broker = UpstoxBroker(BrokerCredentials(api_key="c", access_token="t"),
                          client=httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=UpstoxBroker.BASE_URL))
    got = run(broker.get_quotes_batch([("NSE", "RELIANCE"), ("NSE", "TCS"), ("NSE", "NOSUCH")]))
    assert quote_calls == ["NSE_EQ|INE002A01018,NSE_EQ|INE467B01029"]           # one call; the unknown symbol left out
    assert got[("NSE", "RELIANCE")].ltp == 2900.0 and got[("NSE", "RELIANCE")].prev_close == 2850.0
    assert got[("NSE", "TCS")].prev_close is None                                # no net_change: unknown, not invented


def test_angel_one_batch_is_one_request_across_exchanges():
    bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"status": True, "data": {"fetched": [
            {"exchange": "NSE", "symbolToken": "2885", "ltp": 2900.0, "close": 2850.0},
            {"exchange": "BSE", "symbolToken": "500325", "ltp": 2901.0, "close": 2851.0},
        ]}})
    broker = AngelOneBroker(BrokerCredentials(api_key="k", access_token="jwt"),
                            client=httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=AngelOneBroker.BASE_URL))
    broker._symbol_map.update({("NSE", "RELIANCE"): ("2885", "RELIANCE-EQ"), ("BSE", "RELIANCE"): ("500325", "RELIANCE")})
    got = run(broker.get_quotes_batch([("NSE", "RELIANCE"), ("BSE", "RELIANCE")]))
    assert len(bodies) == 1 and bodies[0]["exchangeTokens"] == {"NSE": ["2885"], "BSE": ["500325"]}
    assert got[("NSE", "RELIANCE")].prev_close == 2850.0 and got[("BSE", "RELIANCE")].ltp == 2901.0
