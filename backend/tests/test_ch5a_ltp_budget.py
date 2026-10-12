"""CH5-a review (ADR-0004): `/api/market-data/ltp` is limited per user; every broker call it makes takes a token from
ONE budget per broker credential shared by every API process (Redis, keyed by the credential row; an in-process window
without Redis) with per-second, per-minute and - for Upstox - 30-minute windows; and a quote is shared for a few seconds
per (credential, symbol), one request at a time making the call. A fresh tick costs nothing."""
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from app.brokers.models import Quote
from app.core import rate_limit
from app.db.models import BrokerCredentialRecord
from app.market_data import api_budget, candles_routes
from app.market_data.stream import Tick, tick_cache
from tests.test_phase_aa_market_data_api import _store_credentials
from tests.test_phase_an_ltp import _QuoteBroker, client_get
from tests.test_phase_k_commercial import _owner

run = asyncio.run


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
        for i, key in enumerate(keys):
            if int(self.store.get(key, "0")) + 1 > int(argv[2 * i + 1]):
                return 0
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
    assert (up.per_second, up.per_minute, up.per_30min) == (4, 100, 500)            # half of 8/s and 200/min; 500 of 2,000 per 30 min
    assert [w for w, _ in up.windows()] == [1, 60, 1800]
    zerodha = api_budget.api_limits("zerodha")
    assert (zerodha.per_second, zerodha.per_minute, zerodha.per_30min) == (1, 50, None)


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
    run(candles_routes._ltp_body(_record(61), tick_cache, "INFY", "NSE", now))
    assert "ltpq:61:NSE:INFY" in fake.store and not any(k.startswith("lock:") for k in fake.store)   # cached; lock released
    api_budget.reset()                                                             # another API worker
    body = run(candles_routes._ltp_body(_record(61), tick_cache, "INFY", "NSE", now))
    assert body["shared"] is True and _Counting.calls == 1
    api_budget.reset()


def test_one_broker_call_per_credential_and_symbol_shared_by_every_request(monkeypatch):
    api_budget.reset()
    tick_cache.clear()
    _Counting.calls, _Counting.delay = 0, 0.3
    monkeypatch.setattr(candles_routes, "build_adapter", lambda record: _Counting(quote=_quote()))
    now = datetime.now(timezone.utc)

    async def three_at_once():
        return await asyncio.gather(*(candles_routes._ltp_body(_record(51), tick_cache, "RELIANCE", "NSE", now) for _ in range(3)))
    bodies = run(three_at_once())
    assert _Counting.calls == 1                                                    # three requests, one broker call
    assert {b["ltp"] for b in bodies} == {2874.2} and sum(1 for b in bodies if b.get("shared")) == 2
    later = run(candles_routes._ltp_body(_record(51), tick_cache, "RELIANCE", "NSE", now + timedelta(seconds=1)))
    assert _Counting.calls == 1 and later["shared"] is True and later["age_seconds"] >= bodies[0]["age_seconds"]
    run(candles_routes._ltp_body(_record(52), tick_cache, "RELIANCE", "NSE", now))      # a BYOK member's own credential
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
    first = client_get(headers, "RELIANCE")
    assert first.status_code == 200 and first.json()["source"] == "quote"
    assert client_get(headers, "RELIANCE").json().get("shared") is True             # within 4 s: the same call, no token
    api_budget._LOCAL_QUOTES.clear()
    second = client_get(headers, "RELIANCE")
    assert second.status_code == 429 and second.headers.get("retry-after") == "5"
    assert _Counting.calls == 1                                                    # the refused request never reached the broker
    now = datetime.now(timezone.utc)
    run(tick_cache.put(Tick(broker="upstox", exchange="NSE", symbol="RELIANCE", ltp=2875.0, exchange_ts=now, received_at=now)))
    assert client_get(headers, "RELIANCE").json()["source"] == "tick"               # a fresh tick costs no token
    tick_cache.clear()
    api_budget.reset()


def test_ltp_is_limited_per_user(monkeypatch):
    headers, me = _owner("ch5a-ltp-limit@example.com")
    _store_credentials(me)
    tick_cache.clear()
    rate_limit.reset("market_data_ltp")
    api_budget.reset()
    monkeypatch.setattr(candles_routes, "build_adapter", lambda record: _QuoteBroker(quote=_quote()))
    codes = [client_get(headers, "RELIANCE").status_code for _ in range(61)]
    assert codes[:60] == [200] * 60 and codes[60] == 429
    other, him = _owner("ch5a-ltp-limit-2@example.com")
    _store_credentials(him)
    assert client_get(other, "RELIANCE").status_code == 200                       # per user, not global
    rate_limit.reset("market_data_ltp")
    api_budget.reset()
