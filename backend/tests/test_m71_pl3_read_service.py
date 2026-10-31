"""M71 PL-3 + PL-1(a): the one store-first read service (app/market_store) and its first consumers (Screener run,
backtests). Synthetic store rows and a spy broker only - no network.

Read order: the own store -> only the missing NEWEST bars by REST (paced, the reader's own session) -> never the full
history. A second run on the same symbols makes ZERO history calls. Daily / Weekly never call the broker. Gaps are
reported (and queued when the gap-fill queue exists), never filled with zeros. Only closed bars (no look-ahead).
M71Q-2: another member's broker bars are never read.
"""
import asyncio
from datetime import UTC, date, datetime, time, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import delete

from app.backtest import bar_source
from app.core.models import OHLCVBar
from app.db.models import MdCandleRecord, MdTenantCandleRecord
from app.market_data import api_budget
from app.market_data.calendar import IST
from app.market_lake import ingest
from app.market_lake.bhavcopy_backfill import bar_end
from app.market_store import models as M
from app.market_store import read_bars, read_many
from app.market_store import reader as R
from tests.test_auth_api import _session_factory, client
from tests.test_copilot_v2_chat import _flag
from tests.test_deployments_api import _auth, _me

DAY = date(2026, 10, 28)                                  # a Wednesday (a trading day in the synthetic calendar)
OPEN = datetime.combine(DAY, time(9, 15), IST)
MIN = timedelta(minutes=1)


def _run(coro):
    return asyncio.run(coro)


class Spy:
    """A broker adapter that counts history calls and answers 1-minute START-stamped bars at 2000 + minute index."""
    name = "upstox"

    def __init__(self, day: date = DAY, upto: datetime | None = None):
        self.history = 0
        self.intraday = 0
        self.day = day
        self.upto = upto

    def _bars(self, day: date, price: float = 2000.0):
        start = datetime.combine(day, time(9, 15), IST).astimezone(UTC)
        out = []
        for i in range(375):
            ts = start + i * MIN
            if self.upto is not None and ts + MIN > self.upto:
                break
            out.append(OHLCVBar(timestamp=ts, open=price + i, high=price + i + 1, low=price + i - 1, close=price + i, volume=5.0))
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
        return self._bars(self.day)

    @property
    def calls(self) -> int:
        return self.history + self.intraday


_RECORD = [7_100_000 + (int(datetime.now(UTC).timestamp()) % 100_000) * 10]


def _rest(spy, record_id=None):
    """A REST source on a credential id unique to this test run (the top-up copies are kept per credential in Redis)."""
    if record_id is None:
        _RECORD[0] += 1
        record_id = _RECORD[0]
    return M.RestSource(adapter=spy, record_id=record_id, broker="upstox")


_HEADERS: dict = {}


def _user(headers=None, role="OWNER"):
    if headers is None:
        headers = _HEADERS.get("reader") or _HEADERS.setdefault("reader", _auth("m71-pl3-reader@example.com"))
    me = _me(headers)
    return SimpleNamespace(id=int(me["id"]), tenant_id=int(me["tenant_id"]), role=role)


def _minutes(day: date, upto_end: datetime, base: float = 1000.0):
    """Store rows of one session: 1-minute bars labelled by their END, from 09:16 up to `upto_end`."""
    out, end = [], datetime.combine(day, time(9, 16), IST)
    close = datetime.combine(day, time(15, 30), IST)
    i = 0
    while end <= min(upto_end, close):
        out.append((end.astimezone(UTC), base + i, base + i + 2, base + i - 2, base + i + 1, 100 + i))
        end += MIN
        i += 1
    return out


def _store_minutes(key, rows, tenant_id, source):
    async def go():
        async with _session_factory() as session:
            await session.execute(delete(MdTenantCandleRecord).where(MdTenantCandleRecord.instrument_key == key))
            bars = [ingest.LakeBar(key, "1m", ts, o, h, lo, c, v) for ts, o, h, lo, c, v in rows]
            await ingest.write_candles_bulk(session, bars, source, tenant_id=tenant_id)
            await session.commit()
    _run(go())


def _store_days(key, days, source="nse_bhav:cm"):
    async def go():
        async with _session_factory() as session:
            await session.execute(delete(MdCandleRecord).where(MdCandleRecord.instrument_key == key))
            bars = [ingest.LakeBar(key, "day", bar_end(d), 100.0 + k, 103.0 + k, 98.0 + k, 101.0 + k, 1000 + k) for k, d in enumerate(days)]
            await ingest.write_candles_bulk(session, bars, source)
            await session.commit()
    _run(go())


def _clear(*keys):
    async def go():
        async with _session_factory() as session:
            for key in keys:
                await session.execute(delete(MdCandleRecord).where(MdCandleRecord.instrument_key == key))
                await session.execute(delete(MdTenantCandleRecord).where(MdTenantCandleRecord.instrument_key == key))
            await session.commit()
    _run(go())


def _read(*args, **kw):
    async def go():
        async with _session_factory() as session:
            return await read_bars(session, *args, **kw)
    return _run(go())


def _weekdays(n, last):
    out, d = [], last
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return sorted(out)


@pytest.fixture(autouse=True)
def _fresh():
    R.reset_cache()
    api_budget.reset()
    yield
    R.reset_cache()


# --- the calendar --------------------------------------------------------------------------------------------------------

def test_last_closed_bar_follows_the_session_never_the_forming_bar():
    at = datetime.combine(DAY, time(13, 5), IST)
    assert R.last_closed_end(at, 60, set()) == datetime.combine(DAY, time(12, 15), IST).astimezone(UTC)
    assert R.last_closed_end(at, 1, set()) == datetime.combine(DAY, time(13, 5), IST).astimezone(UTC)
    after = datetime.combine(DAY, time(16, 0), IST)
    assert R.last_closed_end(after, 60, set()) == datetime.combine(DAY, time(15, 30), IST).astimezone(UTC)   # cut at the close
    before = datetime.combine(DAY, time(9, 20), IST)
    assert R.last_closed_end(before, 60, set()) == datetime.combine(DAY - timedelta(days=1), time(15, 30), IST).astimezone(UTC)
    assert R.last_closed_day(datetime.combine(DAY, time(15, 0), IST), set()) == DAY - timedelta(days=1)
    assert R.last_closed_day(datetime.combine(DAY, time(15, 31), IST), set()) == DAY


def test_intervals_have_one_canonical_name():
    assert M.canonical("60min") == M.canonical("1h") == "1h" and M.canonical("day") == "1d" and M.canonical("week") == "1w"
    with pytest.raises(M.UnknownInterval):
        M.canonical("2h")


# --- Daily / Weekly: the NSE files, zero broker calls --------------------------------------------------------------------

def test_daily_and_weekly_come_from_the_nse_files_and_never_call_the_broker():
    key = "NSE:M71DAILY"
    now = datetime.combine(DAY, time(16, 0), IST)
    _store_days(key, _weekdays(60, DAY))
    spy = Spy()
    day = _read("M71DAILY", "1d", now - timedelta(days=120), now, rest=_rest(spy), now=now)
    week = _read("M71DAILY", "week", now - timedelta(days=120), now, rest=_rest(spy), now=now)
    assert spy.calls == 0 and day.rest_calls == 0 and week.rest_calls == 0
    assert day.source == bar_source.LAKE_SOURCE and len(day.frame) == 60
    assert day.frame.index[-1].tz_convert(IST).date() == DAY                     # today's file after the close
    assert not week.frame.empty and week.frame.index[-1].tz_convert(IST).date() < DAY - timedelta(days=DAY.weekday())  # week still trading dropped
    assert all(g.kind != M.NEWEST for g in day.gaps)
    _clear(key)


def test_a_daily_bar_of_a_session_not_closed_is_never_read_and_a_missing_file_is_a_gap():
    key = "NSE:M71DAILY2"
    _store_days(key, _weekdays(30, DAY))
    during = datetime.combine(DAY, time(11, 0), IST)
    got = _read("M71DAILY2", "1d", during - timedelta(days=60), during, now=during)
    assert got.frame.index[-1].tz_convert(IST).date() == DAY - timedelta(days=1)            # no look-ahead
    later = datetime.combine(DAY + timedelta(days=2), time(16, 0), IST)                      # Friday: two files missing
    stale = _read("M71DAILY2", "1d", later - timedelta(days=60), later, now=later)
    assert [g.kind for g in stale.gaps if g.kind == M.NEWEST] == [M.NEWEST] and stale.gaps[-1].reason == M.NEWEST_DAILY
    assert not stale.frame.isna().any().any()                                                # never zero / filled bars
    _clear(key)


# --- intraday: own store, newest bars only, second run zero --------------------------------------------------------------

def test_a_store_hit_makes_zero_rest_calls_and_end_labels_become_start_labels():
    user = _user()
    key = "NSE:M71HIT"
    now = datetime.combine(DAY, time(13, 5), IST)
    _store_minutes(key, _minutes(DAY, now), user.tenant_id, f"broker:upstox:u{user.id}")
    spy = Spy()
    got = _read("M71HIT", "1h", now - timedelta(days=3), now, user=user, rest=_rest(spy), now=now)
    assert spy.calls == 0 and got.rest_calls == 0 and got.source == "lake:intraday"
    starts = [t.tz_convert(IST).time() for t in got.frame.index]
    assert starts == [time(9, 15), time(10, 15), time(11, 15)]                    # 12:15 bar still forming at 13:05
    first = got.frame.iloc[0]
    assert first.open == 1000.0 and first.close == 1000.0 + 59 + 1                # END 09:16 row = START 09:15 minute
    one = _read("M71HIT", "1m", now - timedelta(days=1), now, user=user, now=now)
    assert one.frame.index[0].tz_convert(IST).time() == time(9, 15) and one.frame.index[-1] + MIN <= now
    _clear(key)


def test_only_the_newest_bars_are_topped_up_and_a_second_run_makes_zero_history_calls():
    user = _user()
    key = "NSE:M71TOP"
    now = datetime.combine(DAY, time(13, 5), IST)
    _store_minutes(key, _minutes(DAY, datetime.combine(DAY, time(11, 0), IST)), user.tenant_id, f"broker:upstox:u{user.id}")
    spy = Spy(upto=now)
    rest = _rest(spy)
    got = _read("M71TOP", "1h", now - timedelta(days=3), now, user=user, rest=rest, now=now)
    assert spy.intraday == 1 and spy.history == 0 and got.rest_calls == 1               # today's endpoint only, once
    assert got.source == "lake:intraday+broker:upstox" and got.topped_up_bars == (now - datetime.combine(DAY, time(11, 0), IST)) // MIN
    assert [t.tz_convert(IST).time() for t in got.frame.index] == [time(9, 15), time(10, 15), time(11, 15)]
    assert got.frame.iloc[0].open == 1000.0                                             # the store's own bars win
    again = _read("M71TOP", "1h", now - timedelta(days=3), now, user=user, rest=rest, now=now)
    assert spy.calls == 1 and again.rest_calls == 0                                     # SECOND run: zero history calls
    assert again.frame.equals(got.frame)
    _clear(key)


def test_a_store_behind_by_days_reads_past_sessions_once_then_today_and_never_more_than_the_top_up_window():
    user = _user()
    key = "NSE:M71DAYS"
    now = datetime.combine(DAY, time(13, 5), IST)
    old = DAY - timedelta(days=14)
    _store_minutes(key, _minutes(old, datetime.combine(old, time(15, 30), IST)), user.tenant_id, f"broker:upstox:u{user.id}")
    spy = Spy(upto=now)
    got = _read("M71DAYS", "1h", now - timedelta(days=20), now, user=user, rest=_rest(spy), now=now)
    assert spy.history == 1 and spy.intraday == 1                                       # one history + one today call
    older = [g for g in got.gaps if g.kind == M.OLDER and g.start > datetime.combine(old, time(9, 15), IST)]
    assert older and older[0].start == datetime.combine(old, time(15, 30), IST).astimezone(UTC)
    assert older[0].end == (now - timedelta(days=R.TOPUP_MAX_DAYS)).astimezone(UTC)     # older than the window: a gap
    _clear(key)


def test_without_an_own_session_the_newest_bars_are_a_plain_gap_and_the_stored_bars_still_serve():
    user = _user()
    key = "NSE:M71NOS"
    now = datetime.combine(DAY, time(13, 5), IST)
    _store_minutes(key, _minutes(DAY, datetime.combine(DAY, time(11, 0), IST)), user.tenant_id, f"broker:upstox:u{user.id}")
    got = _read("M71NOS", "1h", now - timedelta(days=3), now, user=user, now=now)
    assert got.served and got.rest_calls == 0
    assert [g.reason for g in got.gaps if g.kind == M.NEWEST] == [M.CONNECT_FOR_INTRADAY]
    assert [t.tz_convert(IST).time() for t in got.frame.index] == [time(9, 15)]       # 10:15 bar incomplete in the store
    _clear(key)


def test_a_budget_refusal_is_a_newest_gap_never_a_failure(monkeypatch):
    user = _user()
    key = "NSE:M71BUD"
    now = datetime.combine(DAY, time(13, 5), IST)
    _store_minutes(key, _minutes(DAY, datetime.combine(DAY, time(11, 0), IST)), user.tenant_id, f"broker:upstox:u{user.id}")

    async def refuse(*a, **k):
        return False
    monkeypatch.setattr(api_budget, "take", refuse)
    spy = Spy(upto=now)
    got = _read("M71BUD", "1h", now - timedelta(days=3), now, user=user, rest=_rest(spy), now=now)
    assert spy.calls == 0 and got.served and [g.reason for g in got.gaps if g.kind == M.NEWEST] == [M.NEWEST_BUDGET]
    _clear(key)


def test_another_members_broker_bars_are_never_read():
    owner = _user(_auth("m71-pl3-owner@example.com"))
    key = "NSE:M71OTHER"
    now = datetime.combine(DAY, time(13, 5), IST)
    _store_minutes(key, _minutes(DAY, now), owner.tenant_id, f"broker:upstox:u{owner.id}")
    member = SimpleNamespace(id=owner.id + 100000, tenant_id=owner.tenant_id, role="USER")
    got = _read("M71OTHER", "1h", now - timedelta(days=3), now, user=member, now=now)
    assert not got.served and got.cold and got.gaps[0].reason == M.CONNECT_FOR_INTRADAY
    other_tenant = SimpleNamespace(id=owner.id, tenant_id=owner.tenant_id + 100000, role="OWNER")
    assert not _read("M71OTHER", "1h", now - timedelta(days=3), now, user=other_tenant, now=now).served
    assert not R.may_top_up(member, SimpleNamespace(id=1, tenant_id=owner.tenant_id, user_id=owner.id))
    assert R.may_top_up(owner, SimpleNamespace(id=1, tenant_id=owner.tenant_id, user_id=owner.id + 5))   # the tenant's OWNER
    _clear(key)


def test_older_history_is_reported_and_queued_when_the_gap_fill_queue_exists(monkeypatch):
    user = _user()
    key = "NSE:M71GAP"
    now = datetime.combine(DAY, time(16, 0), IST)
    _store_minutes(key, _minutes(DAY, now), user.tenant_id, f"broker:upstox:u{user.id}")
    queued = []
    monkeypatch.setattr(R, "_gap_fill", lambda: (lambda s, i, a, b: queued.append((s, i, a, b))))
    got = _read("M71GAP", "15m", now - timedelta(days=30), now, user=user, now=now)
    older = [g for g in got.gaps if g.kind == M.OLDER]
    assert older and older[0].queued and queued[0][:2] == ("M71GAP", "15m")
    cold = _read("M71NOTHING", "15m", now - timedelta(days=30), now, user=user, now=now)
    assert cold.cold and cold.gaps[0].queued and cold.frame.empty
    _clear(key)


def test_the_batch_read_shares_one_store_pass_and_a_second_scan_makes_zero_history_calls():
    user = _user()
    now = datetime.combine(DAY, time(13, 5), IST)
    syms = [f"M71B{i:02d}" for i in range(5)]
    for s in syms:
        _store_minutes(f"NSE:{s}", _minutes(DAY, datetime.combine(DAY, time(11, 0), IST)), user.tenant_id, f"broker:upstox:u{user.id}")
    spy = Spy(upto=now)
    released = []

    async def go():
        async with _session_factory() as session:
            async def release():
                released.append(True)
            rest = _rest(spy)
            first = await read_many(session, syms, "1h", now - timedelta(days=3), now, user=user, rest=rest, now=now, release=release)
            second = await read_many(session, syms, "1h", now - timedelta(days=3), now, user=user, rest=rest, now=now)
            return first, second
    first, second = _run(go())
    assert spy.calls == len(syms) and released == [True]                    # one paced top-up per symbol, after the DB pass
    assert sum(r.rest_calls for r in second.values()) == 0 and spy.calls == len(syms)
    assert all(len(r.frame) == 3 for r in first.values())
    _clear(*[f"NSE:{s}" for s in syms])


# --- consumers: the Screener run and backtests ---------------------------------------------------------------------------

@pytest.fixture
def screener_store():
    _run(_flag("screener_v2", True))
    yield
    _run(_flag("screener_v2", False))
    _run(_flag(M.FLAG, False))


def test_a_nifty_50_1h_scan_on_the_own_store_reads_every_symbol(screener_store):
    headers = _auth("m71-pl3-scan@example.com")
    user = _user(headers)
    syms = [f"M71N{i:02d}" for i in range(50)]
    now = datetime.now(UTC)

    async def calendar():
        async with _session_factory() as session:
            return await R.holidays_for(session, "NSE")
    hol = _run(calendar())
    end = R.last_closed_end(now, 1, hol)
    days = [d for d in _weekdays(4, end.astimezone(IST).date()) if d.weekday() < 5 and d not in hol][-2:]
    rows = [r for d in days for r in _minutes(d, end.astimezone(IST))]
    for s in syms:
        _store_minutes(f"NSE:{s}", rows, user.tenant_id, f"broker:upstox:u{user.id}")
    body = {"source": "close > 0", "base_tf": "1h", "symbols": syms, "exchange": "NSE"}
    off = client.post("/api/screener/run", json=body, headers=headers)
    assert off.status_code == 409                                             # flag off: the broker path, unchanged
    _run(_flag(M.FLAG, True))
    out = client.post("/api/screener/run", json=body, headers=headers)
    assert out.status_code == 200, out.text
    got = out.json()
    assert got["data_source"].startswith("lake:intraday")
    assert sorted(got["matched"]) == syms
    assert not [r for r in got["results"] if not r["matched"]]               # no "could not be read" rows
    _clear(*[f"NSE:{s}" for s in syms])


def test_a_daily_scan_reads_the_nse_files_without_any_broker(screener_store):
    headers = _auth("m71-pl3-daily-scan@example.com")
    key = "NSE:M71DSCAN"
    today = datetime.now(UTC).astimezone(IST).date()
    _store_days(key, _weekdays(200, today - timedelta(days=1)))
    _run(_flag(M.FLAG, True))
    out = client.post("/api/screener/run", json={"source": "close > 0", "base_tf": "1d", "symbols": ["M71DSCAN", "M71NOFILE"],
                                                 "exchange": "NSE"}, headers=headers)
    assert out.status_code == 200, out.text
    got = out.json()
    assert got["data_source"] == "lake:daily" and got["matched"] == ["M71DSCAN"]
    missing = next(r for r in got["results"] if r["symbol"] == "M71NOFILE")
    assert missing["code"] == "not_stored"
    _clear(key)


def test_a_backtest_reads_the_store_first_and_never_the_broker_on_daily(monkeypatch):
    headers = _auth("m71-pl3-backtest@example.com")
    tenant_id = _me(headers)["tenant_id"]
    key = "NSE:M71BT"
    today = datetime.now(UTC).astimezone(IST).date()
    _store_days(key, _weekdays(300, today - timedelta(days=1)))

    async def no_broker(*a, **k):
        raise AssertionError("the broker must not be called for Daily bars on the store")
    from app.ai import evidence
    monkeypatch.setattr(evidence, "server_frame", no_broker)
    _run(_flag(M.FLAG, True))
    try:
        async def go():
            async with _session_factory() as session:
                return await bar_source.resolve(session, tenant_id, "M71BT", "NSE", "day", lookback_days=400)
        bars = _run(go())
        assert bars.source == bar_source.LAKE_SOURCE and bars.kind == "stored" and len(bars.frame) > 250
    finally:
        _run(_flag(M.FLAG, False))
        _clear(key)
