"""CH5-a review (ADR-0004): `/api/market-data/ltp` is limited per user; every broker call it makes takes a token from
ONE budget per broker credential shared by every API process (Redis, keyed by the credential row; an in-process window
without Redis) with per-second, per-minute and - for Upstox - 30-minute windows; and a quote is shared for a few seconds
per (credential, symbol), one request at a time making the call. A fresh tick costs nothing."""
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from app.brokers.models import Quote
from app.cache.client import cache_set
from app.core import rate_limit
from app.db.models import BrokerCredentialRecord
from app.market_data import api_budget, candles_routes
from app.market_data.stream import Tick, _tick_key, tick_cache
from tests.test_phase_aa_market_data_api import _store_credentials
from tests.test_phase_an_ltp import _QuoteBroker, client_get
from tests.test_phase_k_commercial import _owner

run = asyncio.run


def _sym(prefix: str) -> str:
    """A symbol no other test (or an earlier run against the same Redis) has a tick or a cached quote for."""
    import uuid
    return f"{prefix}{uuid.uuid4().hex[:6].upper()}"


class _Counting(_QuoteBroker):
    calls = 0
    delay = 0.0

    async def get_quote_for_symbol(self, symbol, exchange="NSE"):
        type(self).calls += 1
        if type(self).delay:
            await asyncio.sleep(type(self).delay)
        return await super().get_quote_for_symbol(symbol, exchange)


def _quote(seconds_old: float = 2.0):
    return Quote(symbol="RELIANCE", ltp=2874.2, timestamp=datetime.now(timezone.utc) - timedelta(seconds=seconds_old))


def _record(record_id: int, broker: str = "upstox") -> BrokerCredentialRecord:
    return BrokerCredentialRecord(id=record_id, tenant_id=1, user_id=1, broker_name=broker, account_label="primary", encrypted_payload="x")


class FakeRedis:
    """Enough of Redis for the budget script (emulated: check every window, then count in every one), the quote
    cache and the fetch lock - shared by every 'process' that talks to it."""

    def __init__(self):
        self.store: Dict[str, str] = {}
        self.evals: List[tuple] = []

    async def eval(self, script, numkeys, *args):
        keys, argv = list(args[:numkeys]), list(args[numkeys:])
        self.evals.append((tuple(keys), tuple(argv)))
        if "redis.call('get', KEYS[1]) == ARGV[1]" in script:          # cache_release_lock
            if self.store.get(keys[0]) == argv[0]:
                self.store.pop(keys[0], None)
                return 1
            return 0
        for i in range(len(keys) - 1, -1, -1):
            if int(self.store.get(keys[i], "0")) + 1 > int(argv[2 * i + 1]):
                return -(i + 1)
        for key in keys:
            self.store[key] = str(int(self.store.get(key, "0")) + 1)
        return 1

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, ex=None, nx=False):
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True


def _use_redis(monkeypatch, fake: Optional[FakeRedis]):
    import app.cache.client as cache_client
    monkeypatch.setattr(rate_limit, "redis_backed", lambda: True)

    def client():
        if fake is None:
            raise ConnectionError("redis down")
        return fake
    monkeypatch.setattr(cache_client, "_get_client", client)


def test_the_api_share_per_broker_and_its_upstox_30_minute_window():
    up = api_budget.api_limits("upstox")
    assert (up.per_second, up.per_minute, up.per_30min) == (8, 100, 500)            # 8/s burst guard, half of 200/min, 500 of 2,000 per 30 min
    assert [w for w, _ in up.windows()] == [1, 60, 1800]
    zerodha = api_budget.api_limits("zerodha")
    assert (zerodha.per_second, zerodha.per_minute, zerodha.per_30min) == (2, 50, None)


def test_tokens_are_counted_in_redis_per_credential_by_every_process(monkeypatch):
    fake = FakeRedis()
    _use_redis(monkeypatch, fake)
    monkeypatch.setattr(api_budget, "api_limits", lambda broker: api_budget.ApiLimits(per_second=100, per_minute=3, per_30min=10))
    api_budget.reset()
    assert [run(api_budget.try_take(41, "upstox")) for _ in range(3)] == [True, True, True]
    api_budget.reset()                                                             # another API worker: no local state
    assert run(api_budget.try_take(41, "upstox")) is False                          # the shared minute window is full
    assert run(api_budget.try_take(42, "upstox")) is True                           # another member's own credential (BYOK)
    keys, argv = fake.evals[0]
    assert keys == ("ab:41:1", "ab:41:60", "ab:41:1800") and argv == (1, 100, 60, 3, 1800, 10)
    assert fake.store["ab:41:60"] == "3"                                           # the refused call counted nowhere

    _use_redis(monkeypatch, None)                                                  # Redis unreachable: the local window limits
    api_budget.reset()
    assert [run(api_budget.try_take(43, "upstox")) for _ in range(4)] == [True, True, True, False]
    api_budget.reset()


def test_the_shared_quote_and_fetch_lock_live_in_redis(monkeypatch):
    fake = FakeRedis()
    _use_redis(monkeypatch, fake)
    api_budget.reset()
    tick_cache.clear()
    _Counting.calls, _Counting.delay = 0, 0.0
    monkeypatch.setattr(candles_routes, "build_adapter", lambda record: _Counting(quote=_quote()))
    now = datetime.now(timezone.utc)
    sym = _sym("QTE")
    run(candles_routes._ltp_body(_record(61), tick_cache, sym, "NSE", now))
    assert f"ltpq:61:NSE:{sym}" in fake.store and not any(k.startswith("lock:") for k in fake.store)   # cached; lock released
    api_budget.reset()                                                             # another API worker
    body = run(candles_routes._ltp_body(_record(61), tick_cache, sym, "NSE", now))
    assert body["shared"] is True and _Counting.calls == 1
    api_budget.reset()


def test_one_broker_call_per_credential_and_symbol_shared_by_every_request(monkeypatch):
    api_budget.reset()
    tick_cache.clear()
    _Counting.calls, _Counting.delay = 0, 0.3
    monkeypatch.setattr(candles_routes, "build_adapter", lambda record: _Counting(quote=_quote()))
    now = datetime.now(timezone.utc)
    sym = _sym("SHR")

    async def three_at_once():
        return await asyncio.gather(*(candles_routes._ltp_body(_record(51), tick_cache, sym, "NSE", now) for _ in range(3)))
    bodies = run(three_at_once())
    assert _Counting.calls == 1                                                    # three requests, one broker call
    assert {b["ltp"] for b in bodies} == {2874.2} and sum(1 for b in bodies if b.get("shared")) == 2
    later = run(candles_routes._ltp_body(_record(51), tick_cache, sym, "NSE", now + timedelta(seconds=1)))
    assert _Counting.calls == 1 and later["shared"] is True and later["age_seconds"] >= bodies[0]["age_seconds"]
    run(candles_routes._ltp_body(_record(52), tick_cache, sym, "NSE", now))      # a BYOK member's own credential
    assert _Counting.calls == 2
    _Counting.delay = 0.0
    api_budget.reset()


def test_every_broker_call_takes_a_token_and_none_left_answers_429(monkeypatch):
    headers, me = _owner("ch5a-ltp-budget@example.com")
    _store_credentials(me)
    tick_cache.clear()
    api_budget.reset()
    _Counting.calls = 0
    monkeypatch.setattr(candles_routes, "build_adapter", lambda record: _Counting(quote=_quote()))
    monkeypatch.setattr(api_budget, "api_limits", lambda broker: api_budget.ApiLimits(per_second=1, per_minute=1, per_30min=1))
    monkeypatch.setattr(api_budget, "TOKEN_WAIT_SECONDS", 0.0)
    sym = _sym("TOK")
    first = client_get(headers, sym)
    assert first.status_code == 200 and first.json()["source"] == "quote"
    assert client_get(headers, sym).json().get("shared") is True             # within 4 s: the same call, no token
    api_budget._LOCAL_QUOTES.clear()
    second = client_get(headers, sym)
    assert second.status_code == 429 and second.headers.get("retry-after") == "5"
    assert _Counting.calls == 1                                                    # the refused request never reached the broker
    now = datetime.now(timezone.utc)
    try:
        run(tick_cache.put(Tick(broker="upstox", exchange="NSE", symbol=sym, ltp=2875.0, exchange_ts=now, received_at=now)))
        assert client_get(headers, sym).json()["source"] == "tick"           # a fresh tick costs no token
    finally:
        # tick_cache.clear() empties only the process-local dict; the Redis mirror (CI, 180 s TTL) is overwritten too
        # (an empty value reads as no tick) - see claude/n4-ch5 1e3425c, where a RELIANCE tick leaked into the exit tests
        tick_cache.clear()
        run(cache_set(_tick_key("upstox", "NSE", sym), "", 1))
        api_budget.reset()


def test_ltp_is_limited_per_user(monkeypatch):
    headers, me = _owner("ch5a-ltp-limit@example.com")
    _store_credentials(me)
    tick_cache.clear()
    rate_limit.reset("market_data_ltp")
    api_budget.reset()
    monkeypatch.setattr(candles_routes, "build_adapter", lambda record: _QuoteBroker(quote=_quote()))
    sym = _sym("LIM")
    codes = [client_get(headers, sym).status_code for _ in range(61)]
    assert codes[:60] == [200] * 60 and codes[60] == 429
    other, him = _owner("ch5a-ltp-limit-2@example.com")
    _store_credentials(him)
    assert client_get(other, sym).status_code == 200                               # per user, not global
    rate_limit.reset("market_data_ltp")
    api_budget.reset()


# --- the real script and lock on a real Redis (CI's redis:7 service; skipped without REDIS_URL) ----------------------------
def _real_redis_url() -> Optional[str]:
    import os
    return os.environ.get("REDIS_URL") or None


def _with_real_redis(monkeypatch, body):
    """Runs `body(clients)` with two independent Redis clients (two API workers) on the server REDIS_URL names. Every key
    used belongs to a fresh credential id and is deleted afterwards."""
    import uuid

    import pytest
    import redis.asyncio as redis_async

    import app.cache.client as cache_client
    url = _real_redis_url()
    if not url:
        pytest.skip("REDIS_URL not set (CI runs a redis:7 service)")
    monkeypatch.setattr(rate_limit, "redis_backed", lambda: True)
    record_id = 900_000_000 + uuid.uuid4().int % 99_999_999

    async def go():
        clients = [redis_async.from_url(url, decode_responses=True), redis_async.from_url(url, decode_responses=True)]
        current = {"c": clients[0]}
        monkeypatch.setattr(cache_client, "_get_client", lambda: current["c"])
        try:
            await clients[0].ping()
            return await body(clients, current, record_id)
        finally:
            keys = [k async for k in clients[0].scan_iter(match=f"*{record_id}*")]
            if keys:
                await clients[0].delete(*keys)
            for c in clients:
                await c.aclose()
    return run(go())


def test_real_redis_counts_across_clients_refusals_cost_nothing_and_keys_expire(monkeypatch):
    limits = api_budget.ApiLimits(per_second=100, per_minute=3, per_30min=1000)
    monkeypatch.setattr(api_budget, "api_limits", lambda broker: limits)

    async def body(clients, current, rid):
        a, b = clients
        got = []
        for c in (a, b, a):                                      # three tokens through two workers
            current["c"] = c
            got.append(await api_budget.try_take(rid, "upstox"))
        current["c"] = b
        api_budget.reset()                                       # no local state: Redis alone decides
        got.append(await api_budget.try_take(rid, "upstox"))
        counts = {s: await a.get(f"ab:{rid}:{s}") for s in (1, 60, 1800)}
        ttls = {s: await a.ttl(f"ab:{rid}:{s}") for s in (1, 60, 1800)}
        return got, counts, ttls
    got, counts, ttls = _with_real_redis(monkeypatch, body)
    assert got == [True, True, True, False]
    assert counts == {1: "3", 60: "3", 1800: "3"}                  # the refused call counted in no window
    assert 0 < ttls[1] <= 1 and 0 < ttls[60] <= 60 and 1790 < ttls[1800] <= 1800


def test_real_redis_per_second_window_expires_and_the_30_minute_window_holds(monkeypatch):
    async def body(clients, current, rid):
        monkeypatch.setattr(api_budget, "api_limits", lambda broker: api_budget.ApiLimits(per_second=1, per_minute=100, per_30min=2))
        first = [await api_budget.try_take(rid, "upstox"), await api_budget.try_take(rid, "upstox")]
        await asyncio.sleep(1.2)                                 # the 1-second key expired
        second = await api_budget.try_take(rid, "upstox")
        await asyncio.sleep(1.2)
        third = await api_budget.try_take(rid, "upstox")         # per-second room again, but 2 of 2 in 30 minutes used
        return first, second, third, await clients[0].get(f"ab:{rid}:1800")
    first, second, third, used = _with_real_redis(monkeypatch, body)
    assert first == [True, False] and second is True and third is False and used == "2"


def test_real_redis_fetch_lock_and_shared_quote(monkeypatch):
    async def body(clients, current, rid):
        a, b = clients
        current["c"] = a
        holder = await api_budget.claim_fetch(rid, "NSE", "INFY")
        api_budget.reset()
        current["c"] = b
        second = await api_budget.claim_fetch(rid, "NSE", "INFY")              # another worker: the call is taken
        lock_ttl = await a.ttl(f"lock:ltpq:{rid}:NSE:INFY")
        await api_budget.release_fetch(rid, "NSE", "INFY", "someone-else")    # not the holder: stays
        still = await a.get(f"lock:ltpq:{rid}:NSE:INFY")
        current["c"] = a
        await api_budget.store_quote(rid, "NSE", "INFY", {"ltp": 1550.5, "timestamp": None})
        await api_budget.release_fetch(rid, "NSE", "INFY", holder)
        api_budget.reset()
        current["c"] = b
        shared = await api_budget.cached_quote(rid, "NSE", "INFY")
        again = await api_budget.claim_fetch(rid, "NSE", "INFY")
        quote_ttl = await a.ttl(f"ltpq:{rid}:NSE:INFY")
        return holder, second, lock_ttl, still, shared, again, quote_ttl
    holder, second, lock_ttl, still, shared, again, quote_ttl = _with_real_redis(monkeypatch, body)
    assert holder and second is None and 0 < lock_ttl <= api_budget.FETCH_LOCK_SECONDS and still == holder
    assert shared == {"ltp": 1550.5, "timestamp": None} and again is not None and 0 < quote_ttl <= api_budget.QUOTE_CACHE_SECONDS


# --- /candles: the same budget ----------------------------------------------------------------------------------------------
def test_candles_take_a_token_per_broker_call_and_a_symbol_without_one_says_budget_used(monkeypatch):
    from tests.test_auth_api import client
    from tests.test_phase_aa_market_data_api import _Broker
    headers, me = _owner("ch5a-candles-budget@example.com")
    _store_credentials(me)
    api_budget.reset()
    broker = _Broker()
    monkeypatch.setattr(candles_routes, "build_adapter", lambda record: broker)
    monkeypatch.setattr(api_budget, "api_limits", lambda broker_name: api_budget.ApiLimits(per_second=100, per_minute=4, per_30min=100))
    monkeypatch.setattr(api_budget, "CANDLE_WAIT_SECONDS", 0.0)
    import uuid
    tag = uuid.uuid4().hex[:6].upper()                                            # fresh symbols: no candle cache from an earlier run
    a, b, c, d = (f"CB{tag}{x}" for x in "ABCD")
    body = client.post("/api/market-data/candles", headers=headers,
                       json={"symbols": [a, b, c, d], "timeframe": "5min", "lookback_days": 2}).json()
    loaded = sorted(s for s, v in body["symbols"].items() if v["error"] is None)
    used = sorted(s for s, v in body["symbols"].items() if v.get("budget_used"))
    assert loaded == [a, b] and used == [c, d]                                     # two calls per symbol, four tokens
    assert body["symbols"][c]["error"] == "budget used" and any("budget used: 2 symbol(s)" in w for w in body["warnings"])
    assert {call[0] for call in broker.calls} == {a, b}                            # the refused symbols never reached the broker
    api_budget.reset()                                                             # the window rolls over
    again = client.post("/api/market-data/candles", headers=headers, json={"symbols": [c], "timeframe": "5min", "lookback_days": 2})
    assert again.status_code == 200 and again.json()["symbols"][c]["error"] is None        # loads on the next refresh
    api_budget.reset()


def test_the_budgeted_adapter_passes_everything_else_through():
    calls = []

    class Inner:
        name = "upstox"

        async def get_instruments(self, exchange=None):
            calls.append("instruments")
            return []

        async def get_quote_for_symbol(self, symbol, exchange="NSE"):
            calls.append("quote")
            return None
    api_budget.reset()
    wrapped = api_budget.BudgetedAdapter(Inner(), 77, "upstox", wait_seconds=0.0)
    assert wrapped.name == "upstox"
    run(wrapped.get_instruments("NSE"))                                           # the public instrument master: no token
    run(wrapped.get_quote_for_symbol("X"))
    assert calls == ["instruments", "quote"] and wrapped.taken == 1 and wrapped.refused == 0
    api_budget.reset()


def test_only_the_per_second_window_is_waited_for(monkeypatch):
    api_budget.reset()
    monkeypatch.setattr(api_budget, "api_limits", lambda broker: api_budget.ApiLimits(per_second=1, per_minute=2, per_30min=None))
    monkeypatch.setattr(api_budget, "RETRY_EVERY_SECONDS", 0.05)
    assert run(api_budget.take(81, "upstox", 0.0)) is True
    import time as t
    start = t.monotonic()
    assert run(api_budget.take(81, "upstox", 1.5)) is True                         # waited for the next second
    assert 0.5 < t.monotonic() - start < 1.5
    start = t.monotonic()
    assert run(api_budget.take(81, "upstox", 1.5)) is False                        # the minute is full: refused at once
    assert t.monotonic() - start < 0.2
    api_budget.reset()
