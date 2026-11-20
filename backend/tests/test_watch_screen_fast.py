"""Owner order (15 Nov): the watchlist Screen check - saved results, no re-check while the market is closed, fewer broker
calls (cached history tails, coarse timeframes from 30-minute bars, the daily context from the NSE store).

Synthetic bars and a fake broker only; no network.
"""
import asyncio
import types
from datetime import UTC, date, datetime, timedelta

import pandas as pd
import pytest

from app.core.models import OHLCVBar
from app.market_data import shared_candles
from app.market_data.calendar import IST
from app.screener import routes as R
from app.screener import watch_fetch as WF
from app.screener import watch_screen_routes as WR
from app.screener.runtime import closed_only, resample


def _run(coro):
    return asyncio.run(coro)


def _ist(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=IST)


def _weekdays(lo: date, hi: date):
    d = lo
    while d <= hi:
        if d.weekday() < 5:
            yield d
        d += timedelta(days=1)


def _price(t: datetime) -> float:
    k = int(t.timestamp() // 60)
    return 100.0 + (k % 97) * 0.1 + (k % 13) * 0.05


class FakeUpstox:
    """Upstox-like: dates-only history, 1minute / 30minute / day native. Counts every call. `sparse`: two one-minute bars
    a session (the call count is what is measured, not the bars)."""
    name = "upstox"
    history_by_date = True
    native_intervals = frozenset({"1min", "30min", "day", "week", "month"})

    def __init__(self, sparse: bool = True):
        self.calls: list[tuple[str, str, date | None, date | None]] = []
        self.sparse = sparse

    def _bars(self, interval: str, lo: date, hi: date) -> list[OHLCVBar]:
        out = []
        for d in _weekdays(lo, hi):
            if interval == "day":
                stamps = [datetime.combine(d, datetime.min.time(), IST)]
            elif interval == "30min":                    # the broker's own 30-minute bars: its one-minute bars grouped
                ones = [_ist(d.year, d.month, d.day, 9, 15) + timedelta(minutes=i) for i in range(375)]
                for g in range(0, 375, 30):
                    part = ones[g:g + 30]
                    out.append(OHLCVBar(timestamp=part[0], open=_price(part[0]), high=max(_price(t) for t in part) + 0.5,
                                        low=min(_price(t) for t in part) - 0.5, close=_price(part[-1]) + 0.1, volume=1000 * len(part)))
                continue
            elif self.sparse:
                stamps = [_ist(d.year, d.month, d.day, 9, 15), _ist(d.year, d.month, d.day, 15, 29)]
            else:
                stamps = [_ist(d.year, d.month, d.day, 9, 15) + timedelta(minutes=i) for i in range(375)]
            for t in stamps:
                p = _price(t)
                out.append(OHLCVBar(timestamp=t, open=p, high=p + 0.5, low=p - 0.5, close=p + 0.1, volume=1000))
        return out

    async def get_historical_data(self, symbol, exchange, interval, start=None, end=None):
        self.calls.append(("hist", interval, start.astimezone(IST).date(), end.astimezone(IST).date()))
        return self._bars(interval, start.astimezone(IST).date(), end.astimezone(IST).date())

    async def get_intraday_candles(self, symbol, exchange, interval):
        self.calls.append(("today", interval, None, None))
        return []


@pytest.fixture
def fake_cache(monkeypatch):
    store: dict[str, str] = {}

    async def get(key):
        return store.get(key), (float(WF.tail_ttl()) if key in store else None)

    async def put(key, value, ttl):
        store[key] = value
    monkeypatch.setattr(WF, "_get", get)
    monkeypatch.setattr(WF, "_set", put)
    # Redis (present in CI, shared by the whole suite) holds candle and daily-context copies: keep it out so the broker
    # calls are counted the same everywhere (the cache client fails open, as when Redis is down)
    from app.cache import client as cache_client

    def no_redis():
        raise ConnectionError("no Redis in this test")
    monkeypatch.setattr(cache_client, "_get_client", no_redis)
    shared_candles.reset()
    yield store
    shared_candles.reset()


# --- the tail cache ---------------------------------------------------------------------------------------------------

def test_the_tail_cache_serves_a_repeat_from_cache_and_asks_only_for_bars_after_the_tail(fake_cache):
    broker = FakeUpstox()
    a = WF.TailCachedAdapter(broker)
    lo, hi = _ist(2026, 9, 1, 0, 0), _ist(2026, 10, 30, 15, 30)
    first = _run(a.get_historical_data("AAA", "NSE", "30min", lo, hi))
    assert len(broker.calls) == 1
    again = _run(a.get_historical_data("AAA", "NSE", "30min", lo, hi))
    assert len(broker.calls) == 1 and [b.timestamp for b in again] == [b.timestamp for b in first]     # no broker call
    later = _run(a.get_historical_data("AAA", "NSE", "30min", lo + timedelta(days=3), _ist(2026, 11, 3, 15, 30)))
    assert broker.calls[-1] == ("hist", "30min", date(2026, 10, 31), date(2026, 11, 3))                # only after the tail
    assert len(broker.calls) == 2
    want = broker._bars("30min", (lo + timedelta(days=3)).date(), date(2026, 11, 3))
    assert [b.timestamp for b in later] == [b.timestamp for b in want]                                 # the same bars
    # another session's scope never reads this copy
    other = FakeUpstox()
    _run(WF.TailCachedAdapter(other).get_historical_data("AAA", "NSE", "30min", lo, hi))
    assert len(other.calls) == 1


def test_todays_bars_are_never_cached(fake_cache):
    broker = FakeUpstox()
    a = WF.TailCachedAdapter(broker)
    now = datetime.now(UTC)
    _run(a.get_historical_data("AAA", "NSE", "1min", now - timedelta(days=2), now))
    _run(a.get_historical_data("AAA", "NSE", "1min", now - timedelta(days=2), now))
    assert len(broker.calls) == 2 and not fake_cache


# --- 1h from 30-minute bars ---------------------------------------------------------------------------------------------

def _frame(bars):
    from app.core.models import bars_to_dataframe
    df = bars_to_dataframe(bars)
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    return df


def test_1h_from_30_minute_bars_equals_1h_from_one_minute_bars_and_uses_no_bar_after_the_read_time():
    broker = FakeUpstox(sparse=False)
    days = list(_weekdays(date(2026, 10, 19), date(2026, 10, 30)))
    now = _ist(2026, 10, 30, 12, 50)                    # mid-session: the 12:15 1h bar is still forming
    one = closed_only(_frame(broker._bars("1min", days[0], days[-1])), "1m", now)
    thirty = _frame(broker._bars("30min", days[0], days[-1]))     # holds bars after `now` too: they must not be used
    base = resample(one[one.index >= pd.Timestamp(days[-4]).tz_localize(IST)], "1m", "15m")   # the short 1-minute window
    got = WF.coarse_frame(thirty, base, "15m", "1h", now, "NSE")
    want = resample(resample(one, "1m", "15m"), "15m", "1h")                                   # all of it from one-minute bars
    pd.testing.assert_frame_equal(got[["open", "high", "low", "close"]], want[["open", "high", "low", "close"]], check_freq=False)
    assert (got["volume"].to_numpy() == want["volume"].to_numpy()).all()
    from app.screener.runtime import bar_close
    assert bar_close(got.index, "1h", "NSE").max() <= pd.Timestamp(now)                       # no look-ahead
    assert got.index[-1] == pd.Timestamp(_ist(2026, 10, 30, 11, 15))
    # the older sessions come from the 30-minute bars, the newest from the one-minute window
    assert got.index[0].tz_convert(IST).date() == days[0] and base.index[0].tz_convert(IST).date() == days[-4]


def test_the_plan_cuts_the_one_minute_window_only_when_a_coarser_timeframe_needs_more():
    a = FakeUpstox()
    plan = WF.coarse_plan({"15m": 50, "1h": 200}, "15m", "1min", a, R.fetch_days)
    assert plan is not None and plan.coarse == {"1h": 200} and R.fetch_days(plan.fine, "15m") < R.fetch_days({"15m": 50, "1h": 200}, "15m")
    assert WF.coarse_plan({"15m": 50}, "15m", "1min", a, R.fetch_days) is None
    assert WF.coarse_plan({"15m": 50, "1h": 200}, "15m", "15min", a, R.fetch_days) is None        # not a one-minute fetch
    zerodha = types.SimpleNamespace(native_intervals=frozenset({"1min"}))
    assert WF.coarse_plan({"15m": 50, "1h": 200}, "15m", "1min", zerodha, R.fetch_days) is None


# --- the call count for a 218-stock list ------------------------------------------------------------------------------

def _patch_broker(monkeypatch, broker):
    from app.brokers import token_lifecycle
    from app.market_data import live_access, reader_credential

    async def byob_off(session, tenant_id):
        return False

    async def cred(session, tenant_id, user_id):
        return types.SimpleNamespace(broker_name="upstox", id=7, user_id=user_id)
    monkeypatch.setattr(live_access, "byob_on", byob_off)
    monkeypatch.setattr(reader_credential, "credential_for_reader", cred)
    monkeypatch.setattr(token_lifecycle, "build_adapter", lambda record: broker)


LOOKBACK = {"15m": 50, "1h": 200, "1d": 60}          # a 15m screen with a 1h trend and a daily context
LIST = [f"FNO{i:03d}" for i in range(218)]


def _check(lean: bool, now: datetime):
    async def go():
        if lean:
            with WF.lean():
                return await R.fetch_frames(None, 1, LIST, "NSE", "15m", LOOKBACK, now, user_id=3)
        return await R.fetch_frames(None, 1, LIST, "NSE", "15m", LOOKBACK, now, user_id=3)
    return _run(go())


def test_a_218_stock_list_makes_far_fewer_broker_calls(monkeypatch, fake_cache):
    async def stored(session, symbols, exchange, days, now):
        return {s: pd.DataFrame({"open": [1.0], "high": [1.0], "low": [1.0], "close": [1.0], "volume": [1]},
                                index=pd.DatetimeIndex([pd.Timestamp("2026-11-12", tz=IST)])) for s in symbols}
    monkeypatch.setattr(WF, "stored_daily", stored)
    # two checks a bar apart, on the weekday before the real today: the fetch path splits history from today by the real
    # clock, so a fixed date drifted out of the window a week after it was written (it failed from 20 Nov 2026 on)
    day = datetime.now(UTC).astimezone(IST).date() - timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    t1, t2 = _ist(day.year, day.month, day.day, 10, 0), _ist(day.year, day.month, day.day, 10, 15)
    counts = {}
    for lean in (False, True):
        broker = FakeUpstox()
        _patch_broker(monkeypatch, broker)
        shared_candles.reset()
        fake_cache.clear()
        universe, problems, _ = _check(lean, t1)
        assert len(universe) == 218 and not problems
        first = len(broker.calls)
        shared_candles.reset()                                               # the shared copies (5 min) are gone a bar later
        _check(lean, t2)
        counts[lean] = (first, len(broker.calls) - first)
        if lean:
            assert all(c[1] != "day" for c in broker.calls)                    # the daily context came from the store
            assert all("1h" in d.frames for d in universe)                     # 1h from the 30-minute history
    before, after = counts[False], counts[True]
    print(f"\nbroker calls for 218 stocks - first check: {before[0]} -> {after[0]}; the next bar: {before[1]} -> {after[1]}")
    assert after[0] <= before[0]                                             # the first check: never more
    assert after[1] * 3 <= before[1] and after[1] == 218                     # a later check: today's bars only
    session = 25                                                             # 15m bars in a session: the first check + 24
    assert (after[0] + (session - 1) * after[1]) * 3 <= before[0] + (session - 1) * before[1]


# --- saved results and the market clock ------------------------------------------------------------------------------

def test_the_market_is_closed_on_a_weekend_and_a_holiday_and_open_in_the_session():
    assert WR.market_state(_ist(2026, 11, 13, 11, 0).astimezone(UTC), "NSE", set()) == (True, None)
    closed, last = WR.market_state(_ist(2026, 11, 15, 11, 0).astimezone(UTC), "NSE", set())        # a Sunday
    assert not closed and last == _ist(2026, 11, 13, 15, 30).astimezone(UTC).isoformat()
    closed, last = WR.market_state(_ist(2026, 11, 16, 11, 0).astimezone(UTC), "NSE", {date(2026, 11, 16)})   # a holiday
    assert not closed and last == _ist(2026, 11, 13, 15, 30).astimezone(UTC).isoformat()
    # the bar a kept result is valid for stays the same while the market is closed, and changes with a new bar
    sat, sun = _ist(2026, 11, 14, 10, 0).astimezone(UTC), _ist(2026, 11, 15, 20, 0).astimezone(UTC)
    assert WR.current_bar(sat, "15m", set()) == WR.current_bar(sun, "15m", set()) == _ist(2026, 11, 13, 15, 15).astimezone(UTC).isoformat()
    assert WR.current_bar(_ist(2026, 11, 16, 9, 31).astimezone(UTC), "15m", set()) != WR.current_bar(sun, "15m", set())


def test_the_saved_route_returns_the_kept_result_without_a_broker_call(monkeypatch):
    from app.screener.runtime import SymbolData
    from tests.test_auth_api import client
    from tests.test_copilot_v2_chat import _flag
    from tests.test_m91_watchlist_screen import UNIVERSE, _check, _screen
    from tests.test_phase_l_ai import _owner
    from tests.test_s1d_screener_api import _flag as _screener_flag
    _screener_flag(True)
    _run(_flag("watchlist_screen", True))
    loads = []

    async def fake_load(session, user, ast, validated, params, symbols, exchange, base_tf, tier=None, **kw):
        loads.append(list(symbols))
        return [SymbolData(s, {base_tf: UNIVERSE[s]}, exchange=exchange) for s in symbols if s in UNIVERSE], {}, "lake:daily", {}
    monkeypatch.setattr(R, "load_universe", fake_load)
    try:
        headers, _ = _owner("watch-fast-saved@example.com")
        sid = _screen(headers)
        checked = _check(headers, sid)
        body = {"list_key": "o:1", "list_name": "Swing", "screen_id": sid, "symbols": list(UNIVERSE), "exchange": "NSE"}
        out = client.post("/api/screener/watchlist-screen/saved", headers=headers, json=body)
        assert out.status_code == 200, out.text
        kept = out.json()
        assert len(loads) == 1                                               # the saved answer read no bars
        assert {r["symbol"]: r["matched"] for r in kept["results"]} == {r["symbol"]: r["matched"] for r in checked["results"]}
        assert sorted(kept["fresh"]) == sorted(UNIVERSE)                      # read on the newest closed bar: nothing to check
        assert kept["next_check_at"] and isinstance(kept["market_open"], bool)
        # another screen for the list: nothing kept for it
        other = _screen(headers, name="Other")
        out = client.post("/api/screener/watchlist-screen/saved", headers=headers, json={**body, "screen_id": other})
        assert out.json()["results"] == [] and out.json()["fresh"] == []
    finally:
        _run(_flag("watchlist_screen", False))
        _screener_flag(False)
