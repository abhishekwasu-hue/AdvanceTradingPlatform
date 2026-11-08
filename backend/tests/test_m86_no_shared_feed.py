"""M86 D4 (owner decision): NO live or intraday market data fetched with one user's broker session (the owner's and an
admin's included) is ever served to another user; EOD bhavcopy rows are shared with everyone; D5: the fetching user
keeps reading his own data exactly as before.

User A has a broker session (a fake adapter stamped like `token_lifecycle.build_adapter` stamps a real one); user B has
none, or another session of his own. Through each shared path B never gets A's rows - he gets EOD or "no live data" -
and A still gets his own:

* candles (MarketDataService's cache and LD-10's single-flight copies), the screener's daily context cache;
* quotes / ticks (`/ltp` and the watchlist's tick feed read the reader's own session only; the worker's exit-price
  fallback is unchanged - OPEN_QUESTIONS M86-Q1);
* option chain (the data hub's `shared_chain`);
* the market lake (`md_candles` refuses broker / stream rows; EOD bhavcopy rows are read by both) and the private
  store / index history (`own_bars.shared_scope`, `tenant_store.holder_visible`, `market_store.reader.may_top_up`);
* screener / alert frames (a rule's bars come only through its creator's own session);
* a structural scan: every cache key of broker data in app/ is built with the fetching session's scope.
No network: fake adapters, a fake Redis-less cache, rows written straight into the stores.
"""
import asyncio
import re
import uuid
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.brokers.models import OptionChain
from app.core import config
from app.core.models import OHLCVBar
from app.db.models import MdCandleRecord, MdTenantCandleRecord
from app.market_data import feed_scope, hub, shared_candles
from app.market_data.calendar import IST
from app.market_data.service import MarketDataService
from app.market_data.stream import Tick, tick_cache
from app.market_lake import ingest, own_bars
from app.market_lake.ingest import LakeBar
from tests.test_auth_api import _session_factory
from tests.test_data_h_lake_backfill import FRI, NOW, Owner, _teammate, _user, minute_bars
from tests.test_market_data_service import _FakeBroker, _install_cache, _MemoryCache

run = asyncio.run
APP = Path(__file__).resolve().parents[1] / "app"
TODAY = datetime(2026, 10, 21, 10, 2, 30, tzinfo=IST)


def _bars(start: datetime, n: int, base: float) -> list[OHLCVBar]:
    return [OHLCVBar(timestamp=start + timedelta(minutes=i), open=base + i, high=base + i + 1, low=base + i - 1,
                     close=base + i + 0.5, volume=10) for i in range(n)]


def _session(user_id: int, credential_id: int, base: float) -> _FakeBroker:
    """A user's own broker session as build_adapter makes it: the credential and its holder stamped on the adapter."""
    broker = _FakeBroker(history=_bars(datetime(2026, 10, 20, 9, 15, tzinfo=IST), 30, base),
                         intraday=_bars(datetime(2026, 10, 21, 9, 15, tzinfo=IST), 48, base + 1000), ltp={})
    broker.name = "upstox"
    broker.atp_credential_id = credential_id
    broker.atp_user_id = user_id
    return broker


def _sym() -> str:
    return f"M86{uuid.uuid4().hex[:6].upper()}"


# --- the scope ----------------------------------------------------------------------------------------------------------

def test_the_scope_is_the_fetching_users_session_through_every_wrapper():
    from app.brokers.rate_budget import RateBudget, RateLimitedBroker, RateLimits
    from app.market_data.api_budget import BudgetedAdapter
    a = _session(7, 70, 100)
    assert feed_scope.holder_scope(a) == "u7.c70"
    assert feed_scope.holder_scope(RateLimitedBroker(a, RateBudget(RateLimits(100.0, 100, 1000)))) == "u7.c70"
    assert feed_scope.holder_scope(BudgetedAdapter(RateLimitedBroker(a, RateBudget(RateLimits(100.0, 100, 1000))), 70, "upstox")) == "u7.c70"
    assert feed_scope.record_scope(SimpleNamespace(id=70, user_id=7)) == "u7.c70"
    # the bring-your-own-broker session namespace is the same string: one set of caches per user, whichever path wrote it
    from app.market_data.fanin import SessionRef
    assert SessionRef(user_id=7, tenant_id=1, credential_id=70, broker="upstox", account_label="primary").namespace == "u7.c70"
    bare = _FakeBroker(history=[], intraday=[], ltp={})                 # no credential: its copies are its own only
    assert feed_scope.holder_scope(bare) is None
    assert feed_scope.fetch_scope(bare) == feed_scope.fetch_scope(bare) != feed_scope.fetch_scope(_FakeBroker([], [], {}))
    with pytest.raises(feed_scope.UnscopedFeed):
        feed_scope.scoped_key(None, "candles")
    with pytest.raises(feed_scope.UnscopedFeed):
        shared_candles.key("upstox", "NSE", "X", "1min", "today", "t", scope="")


def test_build_adapter_stamps_whose_session_it_is(monkeypatch):
    from app.brokers import token_lifecycle
    monkeypatch.setattr(token_lifecycle, "load_credentials", lambda record: {})
    monkeypatch.setattr(token_lifecycle, "get_broker_adapter", lambda name, creds, client=None: _FakeBroker([], [], {}))
    record = SimpleNamespace(id=41, user_id=9, tenant_id=3, broker_name="fake", account_label="primary")
    adapter = token_lifecycle.build_adapter(record)
    assert feed_scope.holder_scope(adapter) == "u9.c41"


# --- candles: the service cache, LD-10's copies, the screener's daily context -----------------------------------------------

def test_candles_fetched_by_one_user_are_never_another_users(monkeypatch):
    cache = _MemoryCache()
    _install_cache(monkeypatch, cache)
    sym = _sym()

    async def go():
        a, b = _session(1, 11, 100.0), _session(2, 22, 500.0)
        mine = await MarketDataService(a).get_candles(sym, "NSE", "1min", now=TODAY)
        again = await MarketDataService(_session(1, 11, 100.0)).get_candles(sym, "NSE", "1min", now=TODAY)
        theirs = await MarketDataService(b).get_candles(sym, "NSE", "1min", now=TODAY)
        return a, b, mine, again, theirs
    a, b, mine, again, theirs = run(go())
    assert (a.history_calls, a.intraday_calls) == (1, 1)                   # A's own fetch
    assert again == mine                                                    # D5: A's next read is his own copy
    assert (b.history_calls, b.intraday_calls) == (1, 1)                   # B fetched through his own session...
    assert {x.close for x in theirs}.isdisjoint({x.close for x in mine})    # ...and got none of A's bars
    assert all(":u1.c11:" in k for k in cache.store if sym in k and "u2.c22" not in k)
    assert not any(k.startswith("md:candles:") for k in cache.store)        # no platform-wide candle key any more


def test_a_user_without_a_broker_reads_no_live_copy(monkeypatch):
    """B has no broker: the only way he could see A's candles is a key without a scope - none can be built."""
    _install_cache(monkeypatch, None)
    sym = _sym()
    run(MarketDataService(_session(1, 11, 100.0)).get_candles(sym, "NSE", "1min", now=TODAY))
    keys = list(shared_candles._LOCAL)
    assert keys and all(k.startswith("md:shared:u1.c11:") for k in keys)
    for k in keys:                                                          # every copy names its fetching session
        assert re.match(r"^md:shared:u\d+\.c\d+:", k)


def test_the_screener_daily_context_cache_is_per_session(monkeypatch):
    from app.screener import routes as screener_routes
    from app.screener.runtime import SymbolData
    cache = _MemoryCache()
    _install_cache(monkeypatch, cache)
    sym = _sym()

    async def go():
        a, b = _session(1, 11, 100.0), _session(2, 22, 500.0)
        ua, ub = [SymbolData(symbol=sym, frames={})], [SymbolData(symbol=sym, frames={})]
        await screener_routes._attach_daily(MarketDataService(a, lookback_days=400), ua, "NSE", TODAY)
        await screener_routes._attach_daily(MarketDataService(b, lookback_days=400), ub, "NSE", TODAY)
        return [k for k in cache.store if k.startswith("md:dailyctx:")]
    keys = run(go())
    assert any(":u1.c11:" in k for k in keys) and any(":u2.c22:" in k for k in keys)
    assert not any(k.startswith("md:dailyctx:shared:") for k in keys)


# --- quotes / ticks ------------------------------------------------------------------------------------------------------

def _tick(symbol: str, ltp: float) -> Tick:
    now = datetime.now(UTC)
    return Tick(broker="upstox", exchange="NSE", symbol=symbol, ltp=ltp, exchange_ts=now, received_at=now)


def test_ltp_and_watchlist_ticks_are_the_readers_own_session_only(monkeypatch):
    from app.market_data import candles_routes
    from app.market_data.stream import TickCache
    _install_cache(monkeypatch, _MemoryCache())                             # the keys another process reads
    hub.reset()
    sym = _sym()
    run(TickCache(namespace="u1.c11").put(_tick(sym, 123.5)))               # A's own session stream (fan-in / hub keys)
    run(tick_cache.put(_tick(sym, 999.0)))                                  # an organisation stream's platform-wide key
    a_record, b_record = SimpleNamespace(id=11, user_id=1), SimpleNamespace(id=22, user_id=2)
    assert run(candles_routes.reader_ticks(b_record).fresh("upstox", "NSE", sym)) is None     # B: no live price of anyone's
    got = run(candles_routes.reader_ticks(a_record).fresh("upstox", "NSE", sym))
    assert got is not None and got.ltp == 123.5                                               # D5: A's own price, never 999


def test_ltp_and_quotes_never_read_the_platform_feed():
    """The old LD-10 'shared live feed' read (`shared_feed=tick_cache`) and the watchlist's `[tick_cache]` are gone."""
    text = (APP / "market_data" / "candles_routes.py").read_text() + (APP / "market_data" / "quotes_routes.py").read_text()
    assert "shared_feed=tick_cache" not in text and "feeds = [tick_cache]" not in text
    assert re.search(r"^from app\.market_data\.stream import .*\btick_cache\b", text, re.M) is None


# --- option chain (the data hub) -----------------------------------------------------------------------------------------

def test_a_chain_snapshot_is_never_read_under_another_credential(monkeypatch):
    _install_cache(monkeypatch, None)
    monkeypatch.setattr(hub, "CHAIN_SNAPSHOT_SECONDS", 30)
    hub.reset()
    underlying = _sym()
    calls = []

    def fetch(who: str, ltp: float):
        async def go():
            calls.append(who)
            return OptionChain(underlying=underlying, expiry="2026-10-28", underlying_ltp=ltp, rows=[])
        return go
    a1 = run(hub.shared_chain(11, underlying, None, fetch("A", 100.0)))
    a2 = run(hub.shared_chain(11, underlying, None, fetch("A", 0.0)))
    b = run(hub.shared_chain(22, underlying, None, fetch("B", 500.0)))
    assert a1.underlying_ltp == a2.underlying_ltp == 100.0 and b.underlying_ltp == 500.0
    assert calls == ["A", "B"]                                              # B's chain is B's own fetch, never A's copy


# --- the market lake: shared EOD, private intraday -----------------------------------------------------------------------

def test_the_shared_lake_refuses_broker_session_rows_and_serves_eod_to_everyone():
    key = f"NSE:{_sym()}"
    end = datetime(2026, 10, 20, 10, 0, tzinfo=UTC)
    minute = [LakeBar(key, "1m", end, 1, 2, 0.5, 1.5, 10)]

    async def go():
        async with _session_factory() as session:
            for source in ("broker:upstox", "stream:upstox", "Stream:kite"):
                with pytest.raises(ingest.SharedLakeRefused):
                    await ingest.write_candles(session, minute, source)
                with pytest.raises(ingest.SharedLakeRefused):
                    await ingest.write_candles_bulk(session, minute, source)
            await ingest.write_candles(session, [LakeBar(key, "day", end, 10, 12, 9, 11, 1000)], "nse_bhav:cm")
            # a row written before the guard (the old tick writer) is not read by the shared readers
            session.add(MdCandleRecord(instrument_key=key, timeframe="1m", ts=end, source="stream:upstox", version=1, open=1,
                                       high=2, low=0.5, close=1.5, volume=1, ingested_at=end))
            await session.commit()
            from app.market_lake.asof import candles_as_of
            day = await candles_as_of(session, key, "day", end - timedelta(days=1), end)
            mins = await candles_as_of(session, key, "1m", end - timedelta(days=1), end)
            return day, mins
    day, mins = run(go())
    assert [float(r.close) for r in day] == [11.0] and day[0].source == "nse_bhav:cm"   # EOD: the same for A and B
    assert mins == []


def test_the_recorder_drops_streamed_bars_instead_of_sharing_them():
    from app.market_lake.recorder import LakeRecorder
    rec = LakeRecorder("1m")
    t0 = datetime(2026, 10, 20, 4, 0, tzinfo=UTC)
    sym = _sym()
    for i, ltp in enumerate((10.0, 12.0, 11.0)):
        rec.on_tick(Tick(broker="upstox", exchange="NSE", symbol=sym, ltp=ltp, exchange_ts=t0 + timedelta(seconds=10 * i),
                         received_at=t0))

    async def go():
        async with _session_factory() as session:
            res = await rec.drain(session, t0 + timedelta(minutes=2))
            await session.commit()
            from sqlalchemy import func, select
            n = await session.scalar(select(func.count()).select_from(MdCandleRecord).where(MdCandleRecord.instrument_key == f"NSE:{sym}"))
            return res, n
    res, n = run(go())
    assert res.inserted == 0 and n == 0 and rec.refused == 1


@pytest.fixture
def lake(monkeypatch):
    uid = uuid.uuid4().hex[:8]
    owner = Owner(f"m86-owner-{uid}@example.com", monkeypatch, indices=("NIFTY",))
    other = _user(f"m86-other-{uid}@example.com")
    mate = _teammate(owner.user, f"m86-mate-{uid}@example.com")
    key = "NSE_INDEX:NIFTY50"

    async def seed():
        async with _session_factory() as session:
            for source in (f"broker:upstox:u{owner.user.id}", "seed:owner-history"):
                for b in minute_bars(FRI, 18000 if source.startswith("broker") else 17000):
                    end = (b.timestamp + timedelta(minutes=1)).astimezone(UTC)
                    session.add(MdTenantCandleRecord(tenant_id=owner.tenant_id, instrument_key=key, timeframe="1m", ts=end,
                                                     source=source, version=1, open=b.open, high=b.high, low=b.low,
                                                     close=b.close, volume=0, ingested_at=end))
            await session.commit()
    run(seed())
    return owner, other, mate, key


def test_the_owners_index_history_is_never_read_by_anyone_else(lake):
    """Supersedes M55 T4: another organisation (byob flag OFF - the old sharing condition) and a teammate in the owner's
    own organisation read none of the owner's intraday rows; the owner reads his own (D5)."""
    owner, other, mate, key = lake
    lo, hi = datetime.combine(FRI, time(9, 0), tzinfo=IST).astimezone(UTC), NOW.astimezone(UTC)

    async def go():
        async with _session_factory() as session:
            shared = await own_bars.shared_scope(session, other.tenant_id, key)
            theirs = await own_bars.scope_for(session, other, key)
            mates = await own_bars.scope_for(session, mate, key)
            mates_rows = await own_bars.fetch_rows(session, (mate.tenant_id, mate.id), key, "1m", lo, hi)
            mine = await own_bars.scope_for(session, owner.user, key)
            my_rows = await own_bars.fetch_rows(session, mine, key, "1m", lo, hi) if mine else []
            off = await own_bars.shared_off(session, other.tenant_id, owner.tenant_id)
            return shared, theirs, mates, mates_rows, mine, my_rows, off
    shared, theirs, mates, mates_rows, mine, my_rows, off = run(go())
    assert shared is None and theirs is None and off is True
    assert mates is None and mates_rows == []                    # neither his broker rows nor his own imported (seed) rows
    assert mine == owner.scope and len(my_rows) >= 375           # D5: the owner's own history, as before


def test_a_teammate_reads_his_own_broker_rows_only(lake):
    from sqlalchemy import select

    from app.market_lake.tenant_store import holder_visible
    owner, _other, mate, key = lake

    async def go():
        async with _session_factory() as session:
            end = datetime(2023, 3, 10, 4, 0, tzinfo=UTC)
            session.add(MdTenantCandleRecord(tenant_id=owner.tenant_id, instrument_key=key, timeframe="1m", ts=end,
                                             source=f"broker:upstox:u{mate.id}", version=1, open=1, high=2, low=0.5, close=1.5,
                                             volume=0, ingested_at=end))
            await session.commit()
            q = select(MdTenantCandleRecord.source).where(MdTenantCandleRecord.tenant_id == owner.tenant_id,
                                                          MdTenantCandleRecord.instrument_key == key).distinct()
            mates = set(await session.scalars(q.where(holder_visible(MdTenantCandleRecord.source, mate.id))))
            owners = set(await session.scalars(q.where(holder_visible(MdTenantCandleRecord.source, owner.user.id))))
            return mates, owners
    mates, owners = run(go())
    assert mates == {f"broker:upstox:u{mate.id}"}
    assert owners == {f"broker:upstox:u{owner.user.id}", "seed:owner-history"}   # never the teammate's broker rows


def test_the_store_tops_up_only_through_the_readers_own_credential():
    from app.market_store import reader as R
    owner = SimpleNamespace(id=1, tenant_id=5, role="OWNER")
    member = SimpleNamespace(id=2, tenant_id=5, role="USER")
    assert R.may_top_up(owner, SimpleNamespace(id=9, tenant_id=5, user_id=1))
    assert not R.may_top_up(owner, SimpleNamespace(id=9, tenant_id=5, user_id=2))    # owner/admin included: not a member's
    assert not R.may_top_up(member, SimpleNamespace(id=9, tenant_id=5, user_id=1))


# --- screener / alert frames ---------------------------------------------------------------------------------------------

def test_an_alert_rule_reads_only_through_its_creators_own_session():
    """With the data tier flag OFF too (the old path read the organisation's first credential, maybe a teammate's)."""
    from app.alerts import barclose
    seen = []

    async def fetch(session, tenant_id, symbols, exchange, tf, lookback, **kw):
        seen.append(kw.get("user_id"))
        return [], {}, "fake"
    rule = SimpleNamespace(tenant_id=3, created_by=77, exchange="NSE", base_tf="15m", universe_key=None)
    bar = datetime(2026, 10, 20, 5, 0, tzinfo=UTC)
    run(barclose._current_universe(None, rule, ["X"], bar, {"15m": 10}, fetch, barclose.CycleCache(), per_user=False))
    assert seen == [77]


# --- structural guard ----------------------------------------------------------------------------------------------------

def _sources() -> list[tuple[Path, str]]:
    return [(p, p.read_text()) for p in APP.rglob("*.py")]


def test_every_cache_key_of_broker_data_carries_the_fetching_scope():
    """A scan: no platform-wide key of live / intraday broker data is built anywhere in app/, and every LD-10 copy key
    passes the session scope. A new cache must take the fetching user's (or credential's) scope, or this test fails."""
    platform_keys = re.compile(r"""f?["']md:(candles|tick|shared):\{(?!scope\b)""")
    for path, text in _sources():
        for m in platform_keys.finditer(text):
            line = text[:m.start()].count("\n") + 1
            if path.name == "stream.py" and text[m.start():m.start() + 30].startswith('f"md:tick:{broker}'):
                continue        # `_tick_key` without a namespace: the worker's own stream / exit-price keys (OPEN_QUESTIONS M86-Q1)
            pytest.fail(f"{path.relative_to(APP)}:{line} builds a platform-wide market-data key")
        for m in re.finditer(r"shared_candles\.key\(", text):
            call = text[m.start():text.index("\n", text.index(")", m.start()))]
            assert "scope=" in call, f"{path.relative_to(APP)}: shared_candles.key without scope="
        if "md:dailyctx:" in text:
            assert "'shared'" not in text[text.index("md:dailyctx:") - 600:text.index("md:dailyctx:")], path


def test_no_shared_scope_is_built_any_more():
    for path, text in _sources():
        if path.name == "own_bars.py":
            continue
        assert "SharedScope(" not in text, f"{path.relative_to(APP)} builds a SharedScope"


def test_config_has_no_switch_that_shares_a_feed(monkeypatch):
    """The universe feed's MARKET_FEED_SHARE_WITH_OTHERS only stops holding index keys back from the owner's own store
    now: with nothing shared (`shared_scope` is None) it cannot widen a path."""
    monkeypatch.setattr(config, "MARKET_FEED_SHARE_WITH_OTHERS", True)

    async def go():
        async with _session_factory() as session:
            return await own_bars.shared_scope(session, 1, "NSE_INDEX:NIFTY50")
    assert run(go()) is None
