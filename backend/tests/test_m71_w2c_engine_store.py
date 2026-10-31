"""M71 W2-C: the signal engine reads ATP's own store and draws the DATA budget pool.

Covered: flag `market_store_reads` ON -> the engine adapter's 1-minute bars come through `market_store.read_bars`
(rows as stored, `dh3=False`; the DH3 post-close flag stays a mark; END labels for `frames`); only the newest bars are
read through the reader's own session and a second run makes zero history calls; no own session -> the newest bars are
a reported gap, never zero-filled; a failed newest read is a gap; nothing stored -> the broker path as before; an
index's near-month future through the store. The runner: the worker's session is read through the data pool
(`acquire_spare`, never the exits' `acquire()`), a refused token is a gap and never reaches the broker, a data call is
not reported to the order breaker, and the flag decides whether the store read is handed in. Made-up data only (prices
around 1000, a made-up future week).
"""
import asyncio
import uuid
from datetime import UTC, date, datetime, time, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, select

from app.brokers.rate_budget import RateBudget, RateLimitedBroker, RateLimits
from app.core.models import OHLCVBar
from app.db.models import MdBarFlagRecord, MdTenantCandleRecord, User
from app.engine_provider import frames as F
from app.engine_provider import loader
from app.engine_provider import sources as S
from app.market_data import api_budget
from app.market_store import models as MS
from app.market_store import reader as MR
from app.workers import engine_runner as R
from tests.test_amw2_watchlists import _set_flag
from tests.test_auth_api import _register, _session_factory

IST = F.IST
D1 = date(2030, 1, 7)                       # a made-up Monday
M = timedelta(minutes=1)
SYMBOL = "M71WCSTOCK"
KEY = f"NSE:{SYMBOL}"


def _run(coro):
    return asyncio.run(coro)


def at(hh: int, mm: int, day: date = D1) -> datetime:
    return datetime.combine(day, time(hh, mm), tzinfo=IST)


class Spy:
    """A broker session that counts its history calls and answers made-up 1-minute START-stamped bars (price 2000+)."""
    name = "upstox"

    def __init__(self, upto: datetime | None = None, volume: float | None = 7.0):
        self.history = self.intraday = 0
        self.upto, self.volume = upto, volume

    def _bars(self, day: date) -> list[OHLCVBar]:
        out, t, i = [], at(9, 15, day).astimezone(UTC), 0
        while t + M <= at(15, 30, day) and (self.upto is None or t + M <= self.upto):
            out.append(OHLCVBar(timestamp=t, open=2000.0 + i, high=2001.0 + i, low=1999.0 + i, close=2000.0 + i, volume=self.volume))
            t += M
            i += 1
        return out

    async def get_historical_data(self, symbol, exchange, interval, start, end):
        self.history += 1
        out, d = [], start.astimezone(IST).date()
        while d <= end.astimezone(IST).date():
            if d.weekday() < 5:
                out += self._bars(d)
            d += timedelta(days=1)
        return out

    async def get_intraday_candles(self, symbol, exchange, interval):
        self.intraday += 1
        return self._bars(D1)

    @property
    def calls(self) -> int:
        return self.history + self.intraday


_RECORD = [8_300_000 + (int(datetime.now(UTC).timestamp()) % 100_000) * 10]


def _rest(spy) -> MS.RestSource:
    _RECORD[0] += 1                           # the top-up copies are kept per credential: one id per test run
    return MS.RestSource(adapter=spy, record_id=_RECORD[0], broker="upstox")


_USERS: dict = {}


def _user() -> SimpleNamespace:
    if "u" not in _USERS:
        email = f"m71w2c-{uuid.uuid4().hex[:8]}@example.com"
        _register(email)

        async def go():
            async with _session_factory() as session:
                return (await session.scalars(select(User).where(User.email == email))).one()
        u = _run(go())
        _USERS["u"] = SimpleNamespace(id=int(u.id), tenant_id=int(u.tenant_id), role="OWNER")
    return _USERS["u"]


def _store(user, key, upto_end: datetime, *, flag_post_close=()):
    """The reader's own stored 1-minute rows (END labels) from 09:16 up to `upto_end`, base price 1000."""
    async def go():
        async with _session_factory() as session:
            await session.execute(delete(MdTenantCandleRecord).where(MdTenantCandleRecord.instrument_key == key))
            await session.execute(delete(MdBarFlagRecord).where(MdBarFlagRecord.instrument_key == key))
            end, i = at(9, 16), 0
            while end <= upto_end:
                ts = end.astimezone(UTC)
                session.add(MdTenantCandleRecord(tenant_id=user.tenant_id, instrument_key=key, timeframe="1m", ts=ts,
                                                 source=f"broker:upstox:u{user.id}", version=1, open=1000 + i, high=1002 + i,
                                                 low=998 + i, close=1001 + i, volume=100 + i, ingested_at=ts))
                end += M
                i += 1
            for ts in flag_post_close:
                session.add(MdBarFlagRecord(tenant_id=user.tenant_id, instrument_key=key, timeframe="1m", ts=ts.astimezone(UTC),
                                            kind="post_close", value=None, detail="test"))
            await session.commit()
    _run(go())


def _clear(*keys):
    async def go():
        async with _session_factory() as session:
            for key in keys:
                await session.execute(delete(MdTenantCandleRecord).where(MdTenantCandleRecord.instrument_key == key))
                await session.execute(delete(MdBarFlagRecord).where(MdBarFlagRecord.instrument_key == key))
            await session.commit()
    _run(go())


def _cal():
    return F.market_calendar("NSE", (), D1 - timedelta(days=30), D1 + timedelta(days=1))


def _collect(now, *, store=None, broker=None, key=KEY, symbol=SYMBOL, timeframes=("5m",)):
    starts = {tf: at(9, 15) for tf in timeframes}

    async def go():
        async with _session_factory() as session:
            return await S.collect(session, key=key, exchange="NSE", timeframes=list(timeframes), starts=starts, now=now, cal=_cal(),
                                   broker=broker, broker_symbol=symbol, lake_timeframes=(), store=store)
    data, notes = _run(go())
    return data, notes, F.build_request(data, list(timeframes), timeframes[0], now, starts, _cal())


@pytest.fixture(autouse=True)
def _fresh():
    MR.reset_cache()
    api_budget.reset()
    yield
    MR.reset_cache()


# --- the adapter through the own store ---------------------------------------------------------------------------------

def test_flag_on_reads_the_store_and_a_second_run_makes_zero_history_calls():
    user = _user()
    now = at(13, 5)
    _store(user, KEY, at(11, 0))
    spy = Spy(upto=now)
    store = S.StoreRead(user=user, rest=_rest(spy))
    data, notes, built = _collect(now, store=store)
    assert spy.intraday == 1 and spy.history == 0                         # only the newest bars, once
    bars = data.minutes.bars
    assert data.minutes.source == S.STORE and S.STORE_TRIED in data.sources_tried
    assert (bars[0].start, bars[0].end, bars[0].open) == (at(9, 15), at(9, 16), 1000.0)   # END 09:16 row -> START 09:15
    assert bars[-1].end == now and bars[-1].open > 2000                    # topped up to the last closed minute
    assert all(b.end <= now for b in bars)                                # closed bars only
    assert built.request is not None and not built.gaps and notes == []
    again, _, rebuilt = _collect(now, store=store)
    assert spy.calls == 1                                                 # SECOND run: zero history calls
    assert again.minutes == data.minutes and rebuilt.request.frames == built.request.frames
    _clear(KEY)


def test_without_an_own_session_the_newest_bars_are_a_reported_gap_never_zero_filled():
    user = _user()
    now = at(13, 5)
    _store(user, KEY, at(11, 0))
    data, notes, built = _collect(now, store=S.StoreRead(user=user, rest=None))
    assert data.minutes.source == S.OWN_LAKE and data.minutes.bars[-1].end == at(11, 0)    # nothing made up after it
    assert any(MS.CONNECT_FOR_INTRADAY in n for n in notes)
    tail = [g for g in built.gaps if g.kind == F.MISSING_BARS and g.first is not None and g.first >= at(11, 0)]
    assert tail and built.data_problems                                   # the unread bars are a gap: no signal
    _clear(KEY)


def test_rows_stay_as_stored_and_a_post_close_flag_is_a_mark():
    user = _user()
    now = at(15, 40)
    _store(user, KEY, at(15, 32), flag_post_close=(at(15, 31), at(15, 32)))
    data, _, _ = _collect(now, store=S.StoreRead(user=user, rest=None), timeframes=("1m",))
    bars = data.minutes.bars
    assert [b.end for b in bars[-2:]] == [at(15, 31), at(15, 32)]           # dh3=False: not dropped ...
    assert [b.closing_auction for b in bars[-3:]] == [False, True, True]    # ... but marked, as EN-2's lake path does
    _clear(KEY)


def test_a_broker_that_cannot_give_the_newest_bars_is_a_gap_never_made_up_bars():
    user = _user()
    now = at(11, 30)
    _store(user, KEY, at(11, 0))
    bad = Spy(upto=now, volume=None)                                      # answers bars a candle cannot hold
    data, notes, built = _collect(now, store=S.StoreRead(user=user, rest=_rest(bad)), timeframes=("1m",))
    assert bad.intraday == 1 and data.minutes.bars[-1].end == at(11, 0) and data.minutes.source == S.OWN_LAKE
    assert any("could not be read from your broker" in n for n in notes) and built.data_problems
    _clear(KEY)


def test_a_symbol_the_store_does_not_hold_keeps_the_broker_path():
    user = _user()
    now = at(10, 0)
    _clear("NSE:M71WCCOLD")
    broker = Spy(upto=now)
    data, _, _ = _collect(now, store=S.StoreRead(user=user, rest=None), broker=broker, key="NSE:M71WCCOLD", symbol="M71WCCOLD")
    assert data.minutes.source == S.BROKER and broker.calls >= 1


def test_flag_off_is_the_broker_path_exactly():
    user = _user()
    now = at(13, 5)
    _store(user, KEY, at(11, 0))
    broker = Spy(upto=now)
    data, _, _ = _collect(now, broker=broker)
    assert data.minutes.source == S.BROKER and broker.history == 1 and broker.intraday == 1
    assert S.STORE_TRIED not in data.sources_tried
    _clear(KEY)


def test_the_index_future_is_read_through_the_store(monkeypatch):
    from app.market_data import index_volume
    user = _user()
    now = at(13, 5)
    fut = "M71WCIDXFUT"
    _store(user, f"NFO:{fut}", at(13, 5))
    monkeypatch.setattr(index_volume, "index_underlying", lambda symbol, exchange: "M71WCIDX")

    async def near(session, symbol, exchange, day):
        return SimpleNamespace(symbol=fut, exchange="NFO", expiry=D1 + timedelta(days=17))
    monkeypatch.setattr(index_volume, "near_future", near)

    async def go():
        async with _session_factory() as session:
            return await S.futures_contracts(session, None, "M71WCIDX", "NSE", at(9, 15), now, _cal(),
                                             store=S.StoreRead(user=user, rest=None))
    expected, contracts, notes = _run(go())
    assert expected and len(contracts) == 1 and contracts[0].symbol == fut and notes == []
    assert contracts[0].bars[0].volume == 100.0 and contracts[0].bars[-1].end == now
    _clear(f"NFO:{fut}")


# --- the data pool -----------------------------------------------------------------------------------------------------

class SpyBudget:
    daily = None

    def __init__(self, grant: bool = True):
        self.grant, self.spare = grant, []

    async def acquire(self):                     # the exits' own path: the runner must never draw it
        raise AssertionError("the engine runner drew the exits' budget")

    def now(self) -> float:
        return 0.0

    async def acquire_spare(self, reserve, *, deadline=None):
        self.spare.append((reserve, deadline))
        return self.grant


class Inner:
    name = "upstox"

    def __init__(self):
        self.calls = 0

    async def get_historical_data(self, *args):
        self.calls += 1
        return []

    async def place_order(self, order):          # pragma: no cover - must never be reachable through the pool
        raise AssertionError


def test_the_runner_reads_through_the_data_pool_never_the_exit_budget():
    inner, budget = Inner(), SpyBudget()
    pooled = R.data_pool(RateLimitedBroker(inner, budget))
    assert isinstance(pooled, R.DataPoolBroker) and pooled.name == "upstox"
    _run(pooled.get_historical_data("X", "NSE", "1min", at(9, 15), at(10, 0)))
    assert inner.calls == 1 and budget.spare == [(R.DATA_RESERVE, R.DATA_WAIT_SECONDS)]
    with pytest.raises(AttributeError):
        pooled.place_order                       # only data reads are exposed
    assert R.data_pool(None) is None and R.data_pool(pooled) is pooled


def test_a_refused_data_token_is_a_gap_and_never_reaches_the_broker(monkeypatch):
    from app.brokers import circuit_breaker
    seen = []
    monkeypatch.setattr(circuit_breaker, "observe_call", lambda *a, **k: seen.append(a), raising=False)
    inner = Inner()
    pooled = R.DataPoolBroker(inner, SpyBudget(grant=False))
    got, notes = _run(S.broker_bars(pooled, "X", "NSE", "1m", at(9, 15), at(10, 0), _cal()))
    assert got is None and inner.calls == 0 and "DataBudgetUsed" in notes[0] and pooled.refused == 1
    assert seen == []                                                     # never the order breaker


def test_the_exit_reserve_stays_free_while_the_runner_waits():
    t = [0.0]

    async def sleep(seconds):
        t[0] += seconds
    budget = RateBudget(RateLimits(per_second=1.0, burst=4, per_minute=60), clock=lambda: t[0], sleep=sleep)
    pooled = R.DataPoolBroker(Inner(), budget, reserve=0.5, wait_seconds=0.0)

    async def go():
        got = []
        for _ in range(4):
            try:
                await pooled.get_historical_data("X")
                got.append(True)
            except R.DataBudgetUsed:
                got.append(False)
        await budget.acquire()                   # an exit still finds its token at once
        return got
    got = _run(go())
    assert got[0] is True and got[-1] is False and t[0] == 0.0 and budget.spare_calls == got.count(True)


# --- the runner wiring -------------------------------------------------------------------------------------------------

class RecordingCollect:
    def __init__(self):
        self.kwargs: list[dict] = []

    async def __call__(self, session, **kw):
        self.kwargs.append(kw)
        return F.SymbolData(symbol=kw["key"], exchange=kw["exchange"]), []


class Engine:
    name, version, interface, timeframes = "test-double", "0.0.0-test", R.INTERFACE_VERSION, ("5m",)

    def read(self, request):                     # pragma: no cover - no bars, so the runner never asks
        raise AssertionError


def _org() -> tuple[int, int]:
    email = f"m71w2c-run-{uuid.uuid4().hex[:8]}@example.com"
    _register(email)

    async def go():
        async with _session_factory() as session:
            user = (await session.scalars(select(User).where(User.email == email))).one()
            await R.save_settings(session, user.tenant_id, {"user_id": user.id, "intraday_days": 1,
                                                            "symbols": [{"key": KEY, "exchange": "NSE", "broker_symbol": SYMBOL}]}, None)
            return user.tenant_id, user.id
    tenant_id, user_id = _run(go())
    _set_flag(R.FLAG, tenant_id)
    return tenant_id, user_id


async def _forget():
    from app.db.models import PlatformControlRecord
    async with _session_factory() as session:
        await session.execute(delete(PlatformControlRecord).where(PlatformControlRecord.key == R.SETTINGS_KEY))
        await session.commit()


@pytest.mark.parametrize("store_flag", [False, True])
def test_the_runner_hands_the_data_pool_and_the_store_read_only_under_the_flag(store_flag):
    _run(_forget())
    R._held.clear()
    tenant_id, user_id = _org()
    if store_flag:
        _set_flag(MS.FLAG, tenant_id)
    budget = SpyBudget()

    async def broker_for(session, tid, user, now):
        return RateLimitedBroker(Inner(), budget)
    collect = RecordingCollect()
    loader.set_for_tests(Engine())
    try:
        _run(R.run_due(_session_factory, at(10, 0, D1) + timedelta(seconds=10), broker_for=broker_for, collect=collect))
    finally:
        loader.reset_for_tests()
        R._held.clear()
        _run(_forget())
    [kw] = collect.kwargs
    assert isinstance(kw["broker"], R.DataPoolBroker) and kw["broker"].budget is budget
    if store_flag:
        assert isinstance(kw["store"], S.StoreRead) and kw["store"].user.id == user_id and kw["store"].rest is None
    else:
        assert "store" not in kw
