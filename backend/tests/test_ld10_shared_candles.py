"""LD-10 (owner, 20 Oct): a chart spends fewer broker calls instead of a larger per-user budget.

* history and today's candles are one shared copy per (broker, exchange, symbol, interval, window) for every user and
  tenant - one broker fetch, made through the requesting user's own adapter - with one fetch at a time (single-flight,
  in one process and across processes through Redis); today's copy never outlives the bar forming when it was fetched;
* `/ltp` takes a fresh tick of the shared live feed (same broker) before a broker call, and falls back as before;
* the API budget's per-second rate is a token bucket: a burst of API_BURST, the same sustained rate, the minute window
  still refusing beyond the budget."""
import asyncio
import json
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import List

from app.core.models import OHLCVBar
from app.market_data import api_budget, candles_routes, shared_candles
from app.market_data.calendar import IST
from app.market_data.service import MarketDataService
from app.market_data.stream import Tick, TickCache, tick_cache
from tests.test_bt1_byob_data_tier import _PriceBroker, _email, _flag, _ns
from tests.test_ch5a_ltp_budget import FakeRedis, _use_redis
from tests.test_market_data_service import _FakeBroker, _install_cache, _MemoryCache
from tests.test_auth_api import client
from tests.test_phase_aa_market_data_api import _store_credentials
from tests.test_phase_k_commercial import _owner

run = asyncio.run
UTC = timezone.utc


def _sym() -> str:
    return f"LD{uuid.uuid4().hex[:6].upper()}"


def _bars(start: datetime, n: int, base: float = 100.0) -> List[OHLCVBar]:
    return [OHLCVBar(timestamp=start + timedelta(minutes=i), open=base + i, high=base + i + 1, low=base + i - 1, close=base + i + 0.5,
                     volume=10) for i in range(n)]


class _Slow(_FakeBroker):
    """A broker session of one user: its own counters, a short round trip (so concurrent requests overlap)."""
    history_by_date = True

    def __init__(self, history, intraday, delay: float = 0.05):
        super().__init__(history=history, intraday=intraday, ltp={})
        self.delay = delay

    async def get_historical_data(self, symbol, exchange, interval, from_date, to_date):
        self.history_calls += 1
        await asyncio.sleep(self.delay)
        return list(self.history)

    async def get_intraday_candles(self, symbol, exchange, interval):
        self.intraday_calls += 1
        await asyncio.sleep(self.delay)
        return list(self.intraday)


NOW = datetime(2026, 10, 21, 10, 2, 30, tzinfo=IST)           # inside the session: the 10:02 bar is forming
HISTORY = _bars(datetime(2026, 10, 20, 9, 15, tzinfo=IST), 30)
TODAY = _bars(datetime(2026, 10, 21, 9, 15, tzinfo=IST), 48, base=200.0)   # 09:15 .. 10:02 (forming)


# --- sharing and single-flight ---------------------------------------------------------------------------------------------

def test_two_users_of_two_tenants_share_one_broker_fetch_with_identical_bars(monkeypatch):
    _install_cache(monkeypatch, None)                     # Redis down: no per-request cache, only the shared copy

    async def go():
        a, b = _Slow(HISTORY, TODAY), _Slow(HISTORY, TODAY)   # user A's session (tenant 1), user B's (tenant 2)
        sym = _sym()
        bars_a = await MarketDataService(a, cache_namespace="u1.c1").get_candles(sym, "NSE", "1min", now=NOW)
        bars_b = await MarketDataService(b, cache_namespace="u2.c2").get_candles(sym, "NSE", "1min", now=NOW)
        shared_candles.reset()                                            # what B's own fetch would have returned
        unshared = await MarketDataService(_Slow(HISTORY, TODAY), cache_namespace="u2.c2").get_candles(sym, "NSE", "1min", now=NOW)
        return a, b, bars_a, bars_b, unshared
    a, b, bars_a, bars_b, unshared = run(go())
    assert (a.history_calls, a.intraday_calls) == (1, 1) and (b.history_calls, b.intraday_calls) == (0, 0)
    assert bars_a == bars_b == unshared and len(bars_a) == 78                 # the same bars a fetch of one's own returns
    assert bars_a[-1].timestamp == TODAY[-1].timestamp                         # nothing after the broker's newest bar


def test_five_hundred_concurrent_chart_opens_make_one_fetch_per_part(monkeypatch):
    _install_cache(monkeypatch, None)

    async def go():
        sym = _sym()
        sessions = [_Slow(HISTORY, TODAY, delay=0.1) for _ in range(500)]
        results = await asyncio.gather(*(MarketDataService(s, cache_namespace=f"u{i}.c{i}").get_candles(sym, "NSE", "1min", now=NOW)
                                         for i, s in enumerate(sessions)))
        return sessions, results
    sessions, results = run(go())
    assert sum(s.history_calls for s in sessions) == 1 and sum(s.intraday_calls for s in sessions) == 1
    assert all(r == results[0] for r in results) and len(results[0]) == 78


def test_a_failed_fetch_is_not_shared_the_next_request_fetches_with_its_own_session(monkeypatch):
    _install_cache(monkeypatch, None)

    class _NoBudget(_Slow):
        async def get_intraday_candles(self, symbol, exchange, interval):
            self.intraday_calls += 1
            await asyncio.sleep(self.delay)
            raise api_budget.BudgetUsed("budget used")

    async def go():
        sym = _sym()
        empty, ok = _NoBudget(HISTORY, TODAY), _Slow(HISTORY, TODAY)
        got = await asyncio.gather(MarketDataService(empty).get_candles(sym, "NSE", "1min", now=NOW),
                                   MarketDataService(ok).get_candles(sym, "NSE", "1min", now=NOW), return_exceptions=True)
        return empty, ok, got
    empty, ok, got = run(go())
    assert isinstance(got[0], api_budget.BudgetUsed)                   # the first user's own error, as before
    assert isinstance(got[1], list) and len(got[1]) == 78 and ok.intraday_calls == 1   # the second retried with its own


def test_single_flight_across_processes_waits_for_the_holder_and_takes_over_when_it_gives_up(monkeypatch):
    fake = FakeRedis()
    _use_redis(monkeypatch, fake)
    calls = []

    async def fetch():
        calls.append(1)
        return HISTORY[:3]

    async def other_process_fills(name):
        await asyncio.sleep(0.2)
        fake.store[name] = json.dumps([b.model_dump(mode="json") for b in HISTORY[:5]])
        fake.store.pop("lock:" + name, None)

    async def go():
        name = shared_candles.key("dhan", "NSE", _sym(), "1min", "hist", "d:x:y")
        fake.store["lock:" + name] = "another-process"                    # another API process is fetching
        filler = asyncio.ensure_future(other_process_fills(name))
        got = await shared_candles.get_or_fetch(name, fetch, lambda bars: 60)
        await filler
        shared_candles.reset()
        gone = shared_candles.key("dhan", "NSE", _sym(), "1min", "hist", "d:x:y")
        fake.store["lock:" + gone] = "another-process"

        async def gives_up():
            await asyncio.sleep(0.2)
            fake.store.pop("lock:" + gone, None)                          # released without a result
        quitter = asyncio.ensure_future(gives_up())
        own = await shared_candles.get_or_fetch(gone, fetch, lambda bars: 60)
        await quitter
        return got, own, gone
    got, own, gone = run(go())
    assert got == HISTORY[:5]                                             # the other process's result, no fetch here
    assert own == HISTORY[:3] and calls == [1] and gone in fake.store       # fetched once itself, stored for the others
    shared_candles.reset()


# --- keys and lifetimes ----------------------------------------------------------------------------------------------------

def test_the_window_key_follows_how_the_broker_reads_its_history_window():
    morning, afternoon = datetime(2026, 10, 21, 9, 20, tzinfo=IST), datetime(2026, 10, 21, 14, 5, tzinfo=IST)
    by_date = [shared_candles.window_anchor(True, t - timedelta(days=5), t - timedelta(days=1)) for t in (morning, afternoon)]
    by_time = [shared_candles.window_anchor(False, t - timedelta(days=5), t - timedelta(days=1)) for t in (morning, afternoon)]
    assert by_date[0] == by_date[1] == "d:2026-10-16:2026-10-20"           # Dhan / Upstox: the same request all day
    assert by_time[0] != by_time[1] and by_time[0] == "m:2026-10-16T09:20:2026-10-20T09:20"
    same_minute = shared_candles.window_anchor(False, morning + timedelta(seconds=40), morning + timedelta(seconds=40))
    assert same_minute == shared_candles.window_anchor(False, morning, morning)
    k = shared_candles.key("Dhan", "nse", "reliance", "1min", "hist", by_date[0])
    assert k == "md:shared:dhan:NSE:RELIANCE:1min:hist:d:2026-10-16:2026-10-20"
    assert "byob" not in k and "u1" not in k                                # nothing per user or account in the key


def test_todays_copy_expires_when_the_forming_bar_closes_history_lives_longer(monkeypatch):
    _install_cache(monkeypatch, None)
    clock = {"t": 1000.0}
    monkeypatch.setattr(shared_candles, "_clock", lambda: clock["t"])
    stored = {}
    real_store = shared_candles.store

    async def spy(name, bars, ttl):
        stored[name.split(":")[6]] = ttl
        await real_store(name, bars, ttl)
    monkeypatch.setattr(shared_candles, "store", spy)

    async def go():
        sym = _sym()
        broker = _Slow(HISTORY, TODAY)
        svc = MarketDataService(broker)
        await svc.get_candles(sym, "NSE", "1min", now=NOW)
        assert stored == {"hist": shared_candles.HISTORY_TTL_SECONDS, "today": 30}   # 10:02:30 -> the 10:02 bar closes at 10:03:00
        clock["t"] += 29
        await MarketDataService(_Slow(HISTORY, TODAY)).get_candles(sym, "NSE", "1min", now=NOW)
        later = _Slow(HISTORY, TODAY)
        clock["t"] += 1                                                     # 10:03:00: the copy of today's bars is gone
        await MarketDataService(later).get_candles(sym, "NSE", "1min", now=NOW + timedelta(seconds=30))
        return broker, later
    broker, later = run(go())
    assert (broker.history_calls, broker.intraday_calls) == (1, 1)
    assert (later.history_calls, later.intraday_calls) == (0, 1)            # history still shared, today's fetched again


def test_a_fresh_read_never_takes_todays_copy_but_still_shares_history(monkeypatch):
    _install_cache(monkeypatch, None)

    async def go():
        sym = _sym()
        first, second = _Slow(HISTORY, TODAY), _Slow(HISTORY, TODAY)
        await MarketDataService(first).get_candles(sym, "NSE", "1min", now=NOW)
        await MarketDataService(second).get_candles(sym, "NSE", "1min", now=NOW, fresh=True)
        return second
    second = run(go())
    assert (second.history_calls, second.intraday_calls) == (0, 1)


def test_another_broker_never_reads_the_copy():
    class _Other(_Slow):
        name = "other"

    async def go():
        sym = _sym()
        await MarketDataService(_Slow(HISTORY, TODAY)).get_candles(sym, "NSE", "1min", now=NOW)
        other = _Other(HISTORY, TODAY)
        await MarketDataService(other).get_candles(sym, "NSE", "1min", now=NOW)
        return other
    other = run(go())
    assert (other.history_calls, other.intraday_calls) == (1, 1)


# --- the route: two tenants, one fetch -------------------------------------------------------------------------------------

class _RouteBroker(_PriceBroker):
    """The route's fake session: today's bars too (one closed bar, so the copy lives its full minute)."""

    async def get_intraday_candles(self, symbol, exchange, interval):
        self.calls.append((symbol, "today"))
        start = datetime.now(UTC).replace(second=0, microsecond=0) - timedelta(minutes=3)
        return [OHLCVBar(timestamp=start, open=self.price, high=self.price, low=self.price, close=self.price, volume=1)]


def test_candles_route_two_tenants_one_broker_fetch(monkeypatch):
    _install_cache(monkeypatch, _MemoryCache())
    a_headers, a = _owner(_email("ld10-a"))
    b_headers, b = _owner(_email("ld10-b"))
    _flag(False, [a["tenant_id"], b["tenant_id"]])                       # the data tier on: per-session request caches
    _store_credentials(a)
    _store_credentials(b)
    brokers = {a["tenant_id"]: _RouteBroker(111.0), b["tenant_id"]: _RouteBroker(222.0)}
    monkeypatch.setattr(candles_routes, "build_adapter", lambda record: brokers[record.tenant_id])
    api_budget.reset()
    sym = _sym()
    body = {"symbols": [sym], "timeframe": "5min"}
    ra = client.post("/api/market-data/candles", headers=a_headers, json=body).json()
    rb = client.post("/api/market-data/candles", headers=b_headers, json=body).json()
    assert ra["symbols"][sym]["error"] is None and rb["symbols"][sym]["bars"] == ra["symbols"][sym]["bars"]
    assert sorted(c[1] for c in brokers[a["tenant_id"]].calls) == ["1min", "today"]   # history + today's, once, through A
    assert brokers[b["tenant_id"]].calls == []
    assert rb["data_tier"]["tier"] == "A"
    api_budget.reset()
    _flag(False)


# --- /ltp: the shared live feed first ---------------------------------------------------------------------------------------

def test_ltp_prefers_the_shared_live_feed_and_falls_back(monkeypatch):
    memory = _MemoryCache()
    _install_cache(monkeypatch, memory)
    headers, me = _owner(_email("ld10-ltp"))
    _flag(False, [me["tenant_id"]])
    _store_credentials(me)
    broker = _PriceBroker(500.0)
    monkeypatch.setattr(candles_routes, "build_adapter", lambda record: broker)
    api_budget.reset()
    tick_cache.clear()
    now = datetime.now(UTC)
    sym = _sym()

    def ltp():
        return client.get(f"/api/market-data/ltp?symbol={sym}", headers=headers).json()
    # The shared feed has the symbol (same broker): its tick, no broker call.
    run(tick_cache.put(Tick(broker="upstox", exchange="NSE", symbol=sym, ltp=501.5, exchange_ts=now, received_at=now)))
    body = ltp()
    assert body["source"] == "tick" and body["ltp"] == 501.5 and broker.quote_calls == 0 and body["data_tier"]["tier"] == "A"
    # The user's own session tick wins over the shared feed.
    run(TickCache(namespace=_ns(me)).put(Tick(broker="upstox", exchange="NSE", symbol=sym, ltp=502.0, exchange_ts=now, received_at=now)))
    assert ltp()["ltp"] == 502.0 and broker.quote_calls == 0
    for key in [k for k in memory.store if ":tick:" in k]:
        memory.store.pop(key)
    tick_cache.clear()
    # Another broker's tick is not this user's data: fall back to the user's own broker.
    run(tick_cache.put(Tick(broker="dhan", exchange="NSE", symbol=sym, ltp=499.0, exchange_ts=now, received_at=now)))
    tick_cache.clear()                                                    # (only its Redis mirror remains, under "dhan")
    body = ltp()
    assert body["source"] == "quote" and body["ltp"] == 500.0 and broker.quote_calls == 1
    # A stale shared tick is no price either.
    api_budget.reset()
    old = now - timedelta(minutes=10)
    run(tick_cache.put(Tick(broker="upstox", exchange="NSE", symbol=sym + "X", ltp=1.0, exchange_ts=old, received_at=old)))
    body = client.get(f"/api/market-data/ltp?symbol={sym}X", headers=headers).json()
    assert body["source"] == "quote" and broker.quote_calls == 2
    tick_cache.clear()
    api_budget.reset()
    _flag(False)


# --- the token bucket -------------------------------------------------------------------------------------------------------

def test_bucket_bursts_then_holds_the_sustained_rate_and_the_minute_still_refuses(monkeypatch):
    _use_redis(monkeypatch, None)                                         # in-process (one API process)
    monkeypatch.setenv("API_WORKERS", "1")
    clock = {"t": 50.0}
    monkeypatch.setattr(api_budget, "_clock", lambda: clock["t"])
    api_budget.reset()
    take = lambda: run(api_budget.try_take(81, "dhan", "history"))  # noqa: E731 - Dhan candles: 1/s, burst 2, 50 a minute
    assert [take(), take(), take()] == [True, True, False]                # a chart's two calls at once, not a third
    clock["t"] += 0.5
    assert take() is False                                                # half a token
    clock["t"] += 0.5
    assert [take(), take()] == [True, False]                              # one a second sustained
    granted = 3
    for _ in range(55):                                                   # a call a second for most of a minute
        clock["t"] += 1.0
        granted += take()
    assert granted == 50                                                  # the minute window refuses beyond the budget
    assert take() is False                                                # the bucket is full again (8 s idle), the minute is not
    api_budget.reset()


def test_bucket_in_the_shared_script_bursts_and_refills(monkeypatch):
    fake = FakeRedis()
    _use_redis(monkeypatch, fake)
    api_budget.reset()
    got = [run(api_budget.try_take(82, "dhan", "history")) for _ in range(3)]
    keys, argv = fake.evals[0]
    assert got == [True, True, False] and keys == ("ab:{82}:tb", "ab:{82}:60") and argv[:4] == (1, 2, 60, 50) and argv[-1] == 0
    time.sleep(1.05)
    assert run(api_budget.try_take(82, "dhan")) is False                    # a quote must leave the burst: 1 token is not 2
    assert [run(api_budget.try_take(82, "dhan", "history")), run(api_budget.try_take(82, "dhan", "history"))] == [True, False]
    assert fake.evals[-3][1][-1] == 1                                       # the quote's reserve went to the script
    api_budget.reset()


def test_a_cold_chart_makes_its_two_calls_without_waiting(monkeypatch):
    """#249 made the second call wait for the next second (chart p50 1.1 s); with the burst both go out at once."""
    _install_cache(monkeypatch, None)
    _use_redis(monkeypatch, None)
    monkeypatch.setenv("API_WORKERS", "1")
    api_budget.reset()

    async def go():
        inner = _Slow(HISTORY, TODAY, delay=0.01)
        inner.name = "dhan"
        budgeted = api_budget.BudgetedAdapter(inner, 83, "dhan")
        started = time.monotonic()
        bars = await MarketDataService(budgeted).get_candles(_sym(), "NSE", "1min", now=NOW)
        return time.monotonic() - started, bars, budgeted
    took, bars, budgeted = run(go())
    assert len(bars) == 78 and budgeted.taken == 2 and took < 0.5
    api_budget.reset()


# --- review fixes (PR #282) -------------------------------------------------------------------------------------------------

def test_dhan_history_is_keyed_by_date_through_the_production_wrappers(monkeypatch):
    """Review 1: build_adapter wraps Dhan in ContractSymbolBroker; the date-keyed history must survive every wrapper."""
    from app.brokers.contract_symbols import wrap_contract_symbols
    from app.brokers.dhan import DhanBroker
    from app.brokers.models import BrokerCredentials
    from app.brokers.rate_budget import RateBudget, RateLimitedBroker, limits_for
    from app.brokers.token_lifecycle import build_adapter
    from tests.test_bt1_byob_data_tier import _record
    wrapped = wrap_contract_symbols(DhanBroker(BrokerCredentials(access_token="t", client_id="c")))
    assert type(wrapped).__name__ == "ContractSymbolBroker" and wrapped.history_by_date is True
    assert api_budget.BudgetedAdapter(wrapped, 1, "dhan").history_by_date is True
    assert RateLimitedBroker(wrapped, RateBudget(limits_for("dhan"))).history_by_date is True       # the worker's wrapper
    _, me = _owner(_email("ld10-dhan"))

    async def store_dhan():
        from app.core.enums import BrokerTokenStatus
        from app.secrets_store.encryption import encrypt_text
        from app.db.models import BrokerCredentialRecord
        from tests.test_auth_api import _session_factory
        async with _session_factory() as session:
            session.add(BrokerCredentialRecord(tenant_id=me["tenant_id"], user_id=me["id"], broker_name="dhan", account_label="primary",
                                               encrypted_payload=encrypt_text(json.dumps({"access_token": "t", "client_id": "c"}), me["tenant_id"]),
                                               token_status=BrokerTokenStatus.VALID.value, token_expires_at=None))
            await session.commit()
    run(store_dhan())
    built = build_adapter(_record(me, "dhan"))
    assert built.name == "dhan" and built.history_by_date is True
    names = []
    real = shared_candles.get_or_fetch_ttl

    async def spy(name, fetch, ttl_for, *, read=True):
        names.append(name)
        return await real(name, fetch, ttl_for, read=read)
    monkeypatch.setattr(shared_candles, "get_or_fetch_ttl", spy)
    _install_cache(monkeypatch, None)

    class _Inner(_Slow):
        name = "dhan"
        history_by_date = True
    run(MarketDataService(api_budget.BudgetedAdapter(wrap_contract_symbols(_Inner(HISTORY, TODAY)), 1, "dhan"))
        .get_candles(_sym(), "NSE", "1min", now=NOW))
    assert any(":hist:d:2026-10-16:2026-10-20" in n for n in names)          # dates, not minutes


def test_a_joiner_never_waits_long_on_another_requests_slow_fetch(monkeypatch):
    """Review 2: a request joined to another's fill waits at most JOIN_WAIT_SECONDS, then fetches with its own session."""
    monkeypatch.setattr(shared_candles, "JOIN_WAIT_SECONDS", 0.2)
    calls = []

    async def slow():
        calls.append("slow")
        await asyncio.sleep(2.0)
        return HISTORY[:2]

    async def own():
        calls.append("own")
        return HISTORY[:3]

    async def go():
        name = shared_candles.key("dhan", "NSE", _sym(), "1min", "hist", "d:a:b")
        first = asyncio.ensure_future(shared_candles.get_or_fetch(name, slow, lambda bars: 60))
        await asyncio.sleep(0.05)
        started = time.monotonic()
        mine = await shared_candles.get_or_fetch(name, own, lambda bars: 60)
        took = time.monotonic() - started
        first.cancel()
        return mine, took
    mine, took = run(go())
    assert mine == HISTORY[:3] and took < 1.0 and calls == ["slow", "own"]


def test_a_fill_never_waits_for_its_users_budget_so_nobody_else_does(monkeypatch):
    """Review 2: user A's bucket is empty - A's fill gives up at once (no lock or fill held while A waits for its token)
    and B, whose token is there, fetches and answers at once; A gets its bars after its own wait."""
    fake = FakeRedis()
    _use_redis(monkeypatch, fake)
    monkeypatch.setenv("API_WORKERS", "1")
    api_budget.reset()
    assert [run(api_budget.try_take(101, "dhan", "history")) for _ in range(2)] == [True, True]   # A's burst spent
    locks_while_a_waits = []

    async def go():
        sym = _sym()
        a_broker, b_broker = _Slow(HISTORY, TODAY, delay=0.02), _Slow(HISTORY, TODAY, delay=0.02)
        a = MarketDataService(api_budget.BudgetedAdapter(a_broker, 101, "dhan"), cache_namespace="ua.ca")
        b = MarketDataService(api_budget.BudgetedAdapter(b_broker, 102, "dhan"), cache_namespace="ub.cb")
        a_task = asyncio.ensure_future(a.get_candles(sym, "NSE", "1min", now=NOW))
        await asyncio.sleep(0.05)
        locks_while_a_waits.extend(k for k in fake.store if k.startswith("lock:"))
        started = time.monotonic()
        b_bars = await b.get_candles(sym, "NSE", "1min", now=NOW)
        b_took = time.monotonic() - started
        a_bars = await a_task
        return a_bars, b_bars, b_took, b_broker
    a_bars, b_bars, b_took, b_broker = run(go())
    assert locks_while_a_waits == []                                         # A holds no fill lock while it waits
    assert b_took < 0.5 and (b_broker.history_calls, b_broker.intraday_calls) == (1, 1)
    assert a_bars == b_bars and len(a_bars) == 78
    api_budget.reset()


def test_an_empty_answer_from_another_session_is_not_taken_as_ones_own():
    """Review 6a: a joiner whose filler got no bars fetches with its own session."""
    async def empty():
        await asyncio.sleep(0.1)
        return []

    async def own():
        return HISTORY[:4]

    async def go():
        name = shared_candles.key("dhan", "NSE", _sym(), "1min", "today", "t:x")
        first = asyncio.ensure_future(shared_candles.get_or_fetch(name, empty, lambda bars: 60))
        await asyncio.sleep(0.02)
        mine = await shared_candles.get_or_fetch(name, own, lambda bars: 60)
        return await first, mine
    theirs, mine = run(go())
    assert theirs == [] and mine == HISTORY[:4]


def test_a_cache_built_from_a_shared_copy_never_outlives_it(monkeypatch):
    """Review 3: the per-request cache (and the worker's md:candles) is capped at the copy's remaining life, so today's
    bars stay at most 60 s old end to end."""
    class _Recording(_MemoryCache):
        def __init__(self):
            super().__init__()
            self.ttls = {}

        async def set(self, key, value, ex=None):
            self.ttls[key] = ex
            await super().set(key, value, ex)
    memory = _Recording()
    _install_cache(monkeypatch, memory)
    clock = {"t": 500.0}
    monkeypatch.setattr(shared_candles, "_clock", lambda: clock["t"])

    async def go():
        sym = _sym()
        await MarketDataService(_Slow(HISTORY, TODAY), cache_namespace="u1.c1").get_candles(sym, "NSE", "1min", now=NOW)
        clock["t"] += 20                                                     # the copy of today's bars has 10 s left
        await MarketDataService(_Slow(HISTORY, TODAY), cache_namespace="u2.c2").get_candles(sym, "NSE", "1min", now=NOW)
        await MarketDataService(_Slow(HISTORY, TODAY)).get_candles(sym, "NSE", "1min", now=NOW)   # the worker's cache too
        return sym
    sym = run(go())
    first = next(v for k, v in memory.ttls.items() if k.startswith("md:byob:u1.c1:") and sym in k)
    second = next(v for k, v in memory.ttls.items() if k.startswith("md:byob:u2.c2:") and sym in k)
    worker = next(v for k, v in memory.ttls.items() if k.startswith("md:candles:") and sym in k)
    assert first == 30 and second == 10 and worker == 10


def test_redis_reports_the_seconds_a_shared_copy_has_left(monkeypatch):
    """Review 3: a copy read from Redis carries its PTTL (another API process filled it)."""
    import os

    import pytest
    import redis.asyncio as redis_async

    import app.cache.client as cache_client
    from app.core import rate_limit
    url = os.environ.get("REDIS_URL")
    if not url:
        pytest.skip("REDIS_URL not set (CI runs a redis:7 service)")
    monkeypatch.setattr(rate_limit, "redis_backed", lambda: True)

    async def go():
        client_ = redis_async.from_url(url, decode_responses=True)
        monkeypatch.setattr(cache_client, "_get_client", lambda: client_)
        name = shared_candles.key("dhan", "NSE", _sym(), "1min", "today", "t:r")
        try:
            await shared_candles.store(name, HISTORY[:2], 30)
            shared_candles.reset()                                           # another process: Redis only
            return await shared_candles.load_with_ttl(name)
        finally:
            await client_.delete(name)
            await client_.aclose()
    bars, left = run(go())
    assert bars == HISTORY[:2] and 25 < left <= 30


def test_a_worker_never_takes_the_fill_lock_nor_waits_on_an_api_processs_fill(monkeypatch):
    """Review 4: the trading worker reads a copy when there is one, else fetches at once with its own budget."""
    fake = FakeRedis()
    _use_redis(monkeypatch, fake)
    monkeypatch.setattr(shared_candles, "WAIT_ON_OTHER_PROCESSES", True)
    shared_candles.never_wait_on_other_processes()
    assert shared_candles.WAIT_ON_OTHER_PROCESSES is False
    calls = []

    async def fetch():
        calls.append(1)
        return HISTORY[:2]

    async def go():
        name = shared_candles.key("dhan", "NSE", _sym(), "1min", "hist", "d:w:w")
        fake.store["lock:" + name] = "an-api-process"                         # an API process is filling
        started = time.monotonic()
        bars = await shared_candles.get_or_fetch(name, fetch, lambda bars: 60)
        return bars, time.monotonic() - started, name
    bars, took, name = run(go())
    assert bars == HISTORY[:2] and calls == [1] and took < 0.2 and fake.store["lock:" + name] == "an-api-process"
    from app.workers import research_worker, trading_worker
    import inspect
    for module in (trading_worker, research_worker):
        assert "shared_candles.never_wait_on_other_processes()" in inspect.getsource(module.main)
