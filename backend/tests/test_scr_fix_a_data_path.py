"""SCR-FIX PR A (owner order 27 Oct): the data path. A screen / alert / Copilot read never sends a broker an interval it
does not serve (A1, A2); long windows go out in chunks; daily windows are sized by the need (A3); today's finished
session is attached after the close (A4); the session's last 30m / 1h bucket ends at the close (A5); symbols outside
the live session budget are still read (A6); an index takes its future's volume (A7); the batch plan reads the
credential the run reads (A8). The fakes here are strict (A9): an interval outside the adapter's own map is refused, as
the real broker refuses it ("Interval accepts one of (1minute,30minute,day,week,month)")."""
from __future__ import annotations

import asyncio
import types
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Dict, List

import pandas as pd
import pytest

from app.brokers.angel_one import AngelOneBroker
from app.brokers.coindcx import CoinDCXBroker
from app.brokers.dhan import DhanBroker
from app.brokers.fyers import FyersBroker
from app.brokers.groww import GrowwBroker
from app.brokers.kotak_neo import KotakNeoBroker
from app.brokers.shoonya import ShoonyaBroker
from app.brokers.upstox import UpstoxBroker
from app.brokers.zerodha import ZerodhaBroker
from app.core.models import OHLCVBar, bars_to_dataframe
from app.market_data import intervals, shared_candles
from app.market_data.calendar import IST
from app.market_data.service import MarketDataService, build_frames
from app.screener import nodes

ADAPTERS = [UpstoxBroker, DhanBroker, ZerodhaBroker, AngelOneBroker, FyersBroker, KotakNeoBroker, ShoonyaBroker, GrowwBroker,
            CoinDCXBroker]
SCREEN_TFS = ["1m", "3m", "5m", "15m", "30m", "1h", "1d", "1w", "1M"]
UPSTOX_NATIVE = UpstoxBroker.native_intervals


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _fresh_copies():
    shared_candles._LOCAL.clear()
    yield
    shared_candles._LOCAL.clear()


def _price(ts: datetime) -> float:
    return 100.0 + (int(ts.timestamp()) // 60) % 97 * 0.5


def _session_minutes(day: date, step: int) -> List[datetime]:
    """Bar starts of one NSE session at `step` minutes, 09:15 IST on (the last bar may be partial, as a broker sends it)."""
    out, t = [], datetime.combine(day, time(9, 15), IST)
    close = datetime.combine(day, time(15, 30), IST)
    while t < close:
        out.append(t)
        t += timedelta(minutes=step)
    return out


def _minute_bars(day: date, step: int = 1, volume: float = 10.0) -> List[OHLCVBar]:
    """A session's bars at `step` minutes, each the aggregate of the same one-minute prices (so a 30min bar equals the
    30 one-minute bars it holds)."""
    bars = []
    for start in _session_minutes(day, step):
        mins = [start + timedelta(minutes=i) for i in range(step)]
        mins = [m for m in mins if m < datetime.combine(day, time(15, 30), IST)]
        prices = [_price(m) for m in mins]
        bars.append(OHLCVBar(timestamp=start, open=prices[0], high=max(prices) + 1, low=min(prices) - 1, close=prices[-1],
                             volume=volume * len(mins)))
    return bars


class StrictBroker:
    """A broker double with an interval map: anything outside `native_intervals` is refused, like the real API."""

    history_by_date = True
    daily_days_per_request = 365

    def __init__(self, native=UPSTOX_NATIVE, name: str = "upstox", volume: Dict[str, float] = None) -> None:
        self.native_intervals = frozenset(native)
        self.name = name
        self.asked: List[tuple] = []
        self.volume = volume or {}

    def _check(self, interval: str) -> None:
        if interval not in self.native_intervals:
            raise RuntimeError(f"BrokerAPI: Interval accepts one of ({','.join(sorted(self.native_intervals))})")

    def _bars(self, symbol: str, interval: str, start: datetime, end: datetime) -> List[OHLCVBar]:
        vol = self.volume.get(symbol, 10.0)
        out: List[OHLCVBar] = []
        day = start.astimezone(IST).date()
        while day <= end.astimezone(IST).date():
            if day.weekday() < 5:
                if interval == "day":
                    out.append(OHLCVBar(timestamp=datetime.combine(day, time(0, 0), IST), open=100, high=110, low=90,
                                        close=100 + day.toordinal() % 7, volume=vol * 375))
                else:
                    out += _minute_bars(day, intervals.minutes_of(interval) or 1, vol)
            day += timedelta(days=1)
        return out

    async def get_historical_data(self, symbol: str, exchange: str, interval: str, start: datetime, end: datetime) -> List[OHLCVBar]:
        self._check(interval)
        self.asked.append(("history", symbol, interval, start, end))
        return self._bars(symbol, interval, start, end)

    async def get_intraday_candles(self, symbol: str, exchange: str, interval: str) -> List[OHLCVBar]:
        self._check(interval)
        self.asked.append(("intraday", symbol, interval))
        now = datetime.now(timezone.utc)
        return [b for b in self._bars(symbol, interval, now, now) if b.timestamp <= now]


def _patch_org_broker(monkeypatch, broker: StrictBroker) -> None:
    """The organisation path (byob off) with `broker` as the adapter."""
    from app.brokers import token_lifecycle
    from app.market_data import candles_routes, live_access

    async def pick(session, tenant_id, b, label, **_k):
        return types.SimpleNamespace(id=1, broker_name=broker.name, account_label="primary")

    async def off(session, tenant_id):
        return False
    monkeypatch.setattr(candles_routes, "_pick_record", pick)
    monkeypatch.setattr(token_lifecycle, "build_adapter", lambda record: broker)
    monkeypatch.setattr(live_access, "byob_on", off)
    _not_after_close(monkeypatch)


def _not_after_close(monkeypatch) -> None:
    """These tests run at any clock time: today's bar (A4) is attached only where a test asks for it."""
    from app.screener import routes

    async def open_(session, exchange, now):
        return False
    monkeypatch.setattr(routes, "_closed_for_the_day", open_)


# ---------------------------------------------------------------------------------------------------- A1 / A9: intervals

@pytest.mark.parametrize("adapter", ADAPTERS, ids=lambda a: a.__name__)
@pytest.mark.parametrize("tf", SCREEN_TFS)
def test_every_screen_timeframe_is_native_or_resampled_from_a_native_divisor(adapter, tf):
    from app.screener.routes import _FETCH, screen_interval
    sent = screen_interval(adapter, tf)
    assert sent in adapter.native_intervals                                   # never a raw non-native interval
    want = intervals.minutes_of(_FETCH[tf])
    if want is None:
        assert sent == "day"                                                  # 1d / 1w / 1M from daily bars
    else:
        assert want % intervals.minutes_of(sent) == 0                         # resampled up on the 09:15 grid
        assert sent == _FETCH[tf] or _FETCH[tf] not in adapter.native_intervals


@pytest.mark.parametrize("adapter", ADAPTERS, ids=lambda a: a.__name__)
@pytest.mark.parametrize("interval", ["1min", "5min", "15min", "30min", "60min", "120min", "240min", "day", "week", "month"])
def test_every_interval_any_caller_asks_for_is_fetched_natively(adapter, interval):
    sent = intervals.fetch_interval(adapter, interval)
    assert sent in adapter.native_intervals
    if intervals.minutes_of(interval):
        assert intervals.minutes_of(interval) % intervals.minutes_of(sent) == 0


def test_upstox_uses_30min_for_hours_and_1min_for_5_and_15_minutes():
    assert [intervals.fetch_interval(UpstoxBroker, i) for i in ("5min", "15min", "30min", "60min", "240min")] == \
        ["1min", "1min", "30min", "30min", "30min"]
    assert intervals.fetch_interval(DhanBroker, "30min") == "15min"           # Dhan has no 30: never sent as "30"


def test_a_long_intraday_window_goes_out_in_chunks_of_at_most_30_days():
    from app.market_data.service import INTRADAY_DAYS_PER_REQUEST
    broker = StrictBroker()
    service = MarketDataService(broker, lookback_days=60)  # type: ignore[arg-type]
    bars = _run(service.get_candles("CHUNKED", "NSE", "1min"))
    history = [a for a in broker.asked if a[0] == "history"]
    assert len(history) >= 2
    for _, _, _, start, end in history:
        assert (end.date() - start.date()).days + 1 <= INTRADAY_DAYS_PER_REQUEST    # whole dates, date-only broker
    days = sorted({b.timestamp.astimezone(IST).date() for b in bars})
    assert (days[-1] - days[0]).days > INTRADAY_DAYS_PER_REQUEST                 # the whole window came back
    assert len(days) == len(set(days)) and len(bars) == len({b.timestamp for b in bars})


def test_a_15m_screen_on_upstox_fetches_1min_and_resamples(monkeypatch):
    from app.screener.routes import fetch_frames
    broker = StrictBroker()
    _patch_org_broker(monkeypatch, broker)
    universe, problems, source = _run(fetch_frames(None, 1, ["RELIANCE"], "NSE", "15m", {"15m": 30}))
    assert problems == {} and source == "broker:upstox"
    assert {a[2] for a in broker.asked} == {"1min"}
    frame = universe[0].frames["15m"]
    assert set((frame.index.tz_convert(IST).minute % 15)) == {0} and len(frame) >= 30


def test_a_1h_screen_on_upstox_fetches_30min(monkeypatch):
    from app.screener.routes import fetch_frames
    broker = StrictBroker()
    _patch_org_broker(monkeypatch, broker)
    universe, problems, _ = _run(fetch_frames(None, 1, ["TCS"], "NSE", "1h", {"1h": 20}))
    assert problems == {} and {a[2] for a in broker.asked} == {"30min"}
    starts = {t.strftime("%H:%M") for t in universe[0].frames["1h"].index.tz_convert(IST)}
    assert starts <= {"09:15", "10:15", "11:15", "12:15", "13:15", "14:15", "15:15"} and "15:15" in starts


def test_a_30m_screen_on_dhan_never_sends_30(monkeypatch):
    from app.screener.routes import fetch_frames
    broker = StrictBroker(DhanBroker.native_intervals, "dhan")
    _patch_org_broker(monkeypatch, broker)
    universe, problems, _ = _run(fetch_frames(None, 1, ["INFY"], "NSE", "30m", {"30m": 20}))
    assert problems == {} and {a[2] for a in broker.asked} == {"15min"} and len(universe[0].frames["30m"]) >= 20


# ---------------------------------------------------------------------------------------------------- A9: parity

def _days(n: int) -> List[date]:
    out, d = [], date(2026, 10, 19)
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _frame_close(df: pd.DataFrame) -> pd.DataFrame:
    out = df[["open", "high", "low", "close", "volume"]].astype(float)
    out.index = pd.DatetimeIndex(out.index).tz_convert("UTC")
    return out


@pytest.mark.parametrize("tf,rule,base_step", [("15m", "15min", 1), ("5m", "5min", 1), ("1h", "60min", 30), ("30m", "30min", 1),
                                              ("1h", "60min", 1)])
def test_resampled_screener_bars_equal_the_charts_bars_on_the_same_window(tf, rule, base_step):
    from app.screener.routes import _symbol_frames
    minutes = [b for d in _days(3) for b in _minute_bars(d, 1)]
    charts = build_frames(minutes, "1min", [rule])[rule]                     # Charts: 1min base, build_frames
    base = minutes if base_step == 1 else [b for d in _days(3) for b in _minute_bars(d, base_step)]
    base_interval = "1min" if base_step == 1 else f"{base_step}min"
    later = datetime(2026, 12, 1, tzinfo=timezone.utc)
    _, frames, problem = _symbol_frames(base, base_interval, tf, False, later, exchange="NSE")
    assert problem == ""
    pd.testing.assert_frame_equal(_frame_close(frames[tf]), _frame_close(charts), check_freq=False, check_names=False)
    helper = bars_to_dataframe(intervals.reshape(base, base_interval, rule))     # the shared helper: the same bars
    pd.testing.assert_frame_equal(_frame_close(helper), _frame_close(charts), check_freq=False, check_names=False)


# ---------------------------------------------------------------------------------------------------- A2: every caller

def test_daily_alerts_report_a_failed_today_read_instead_of_dropping_it(monkeypatch):
    from app.alerts import barclose
    from app.screener import routes
    from app.screener.runtime import SymbolData

    daily = pd.DataFrame({"open": [1.0], "high": [1.0], "low": [1.0], "close": [1.0], "volume": [1.0]},
                         index=pd.DatetimeIndex([pd.Timestamp("2026-10-23", tz="UTC")]))

    async def fake(session, tenant_id, symbols, exchange, base_tf, lookback, include_forming=False, user_id=None):
        if base_tf == "1d":
            return [SymbolData(s, {"1d": daily}) for s in symbols], {}, "broker:upstox"
        return [], {s: "RuntimeError: Interval accepts one of (1minute,30minute,day,week,month)" for s in symbols}, "broker:upstox"
    monkeypatch.setattr(routes, "fetch_frames", fake)
    universe, problems, _ = _run(barclose.server_fetch(None, 1, ["SBIN"], "NSE", "1d", {"1d": 20}))
    assert [d.symbol for d in universe] == ["SBIN"]
    assert problems["SBIN"].startswith("today's bar could not be read") and "Interval" in problems["SBIN"]


def test_daily_alerts_extra_15m_read_works_on_upstox(monkeypatch):
    from app.alerts import barclose
    broker = StrictBroker()
    _patch_org_broker(monkeypatch, broker)
    universe, problems, _ = _run(barclose.server_fetch(None, 1, ["SBIN"], "NSE", "1d", {"1d": 20}))
    assert problems == {} and [d.symbol for d in universe] == ["SBIN"]
    assert {a[2] for a in broker.asked} <= UPSTOX_NATIVE and "1min" in {a[2] for a in broker.asked}


@pytest.mark.parametrize("signal_tf", ["15m", "1h", "2h", "4h"])
def test_breakout_runs_fetch_native_intervals(monkeypatch, signal_tf):
    from app.screener.breakouts import routes as bk
    broker = StrictBroker()
    _patch_org_broker(monkeypatch, broker)
    user = types.SimpleNamespace(id=1, tenant_id=1)
    frames, problems, _ = _run(bk._frames(None, user, ["HDFCBANK"], "NSE", signal_tf, (), bars=40))
    assert problems == {} and not frames["HDFCBANK"][signal_tf].empty
    assert {a[2] for a in broker.asked} <= UPSTOX_NATIVE


def test_copilot_market_and_backtest_reads_fetch_native_intervals(monkeypatch):
    from app.ai import evidence
    from app.backtest import bar_source
    broker = StrictBroker()
    _patch_org_broker(monkeypatch, broker)
    df, source = _run(evidence.server_frame(None, 1, "ITC", "NSE", "15min", lookback_days=5))
    assert source == "broker:upstox" and not df.empty and set(df.index.tz_convert(IST).minute % 15) == {0}
    bars = _run(bar_source.resolve(None, 1, "ITC", "NSE", "60min", lookback_days=5))
    assert bars.source == "broker:upstox" and not bars.frame.empty
    assert {a[2] for a in broker.asked} <= UPSTOX_NATIVE


def test_copilot_screener_reads_fetch_native_intervals(monkeypatch):
    from app.screener.routes import _compiled, load_universe
    broker = StrictBroker()
    _patch_org_broker(monkeypatch, broker)
    ast, validated = _compiled("close > 1", "15m", {})
    user = types.SimpleNamespace(id=1, tenant_id=1)
    universe, problems, source, _ = _run(load_universe(None, user, ast, validated, {}, ["LT"], "NSE", "15m"))
    assert problems == {} and [d.symbol for d in universe] == ["LT"] and {a[2] for a in broker.asked} == {"1min"}


# ---------------------------------------------------------------------------------------------------- A3 / A4 / A5

def test_weekly_and_monthly_screens_size_the_daily_window_by_the_need(monkeypatch):
    from app.screener.routes import daily_fetch_days, fetch_frames
    assert daily_fetch_days({"1w": 100}, "1w") >= 700 and daily_fetch_days({"1M": 30}, "1M") >= 900
    broker = StrictBroker()
    _patch_org_broker(monkeypatch, broker)
    universe, problems, _ = _run(fetch_frames(None, 1, ["ACC"], "NSE", "1w", {"1w": 100}))
    assert problems == {} and len(universe[0].frames["1w"]) >= 100
    history = [a for a in broker.asked if a[0] == "history"]
    assert all(a[2] == "day" for a in history) and len(history) >= 2               # chunked by daily_days_per_request
    assert all((a[4].date() - a[3].date()).days + 1 <= broker.daily_days_per_request for a in history)


def test_todays_finished_session_is_attached_after_the_close(monkeypatch):
    from app.screener import routes
    broker = StrictBroker()
    _patch_org_broker(monkeypatch, broker)

    async def closed(session, exchange, now):
        return True
    today = datetime.now(timezone.utc).astimezone(IST).date()
    today_bar = OHLCVBar(timestamp=datetime.combine(today, time(0, 0), IST), open=1, high=2, low=0.5, close=1.5, volume=9)

    async def get_today_bar(self, symbol, exchange, now=None):
        return today_bar
    monkeypatch.setattr(routes, "_closed_for_the_day", closed)
    monkeypatch.setattr(MarketDataService, "get_today_bar", get_today_bar)
    universe, problems, _ = _run(routes.fetch_frames(None, 1, ["WIPRO"], "NSE", "1d", {"1d": 20}))
    last = universe[0].frames["1d"].iloc[-1]
    assert problems == {} and universe[0].frames["1d"].index[-1].tz_convert(IST).date() == today and last["close"] == 1.5


def test_closed_for_the_day_follows_the_exchange_calendar(monkeypatch):
    from app.market_data import calendar
    from app.screener.routes import _closed_for_the_day

    async def holidays(session, family="NSE", year=None):
        return {date(2026, 10, 28)}
    monkeypatch.setattr(calendar, "family_holidays", holidays)
    db = object()                                   # holidays come from the patched calendar read
    at = lambda d, h, m: datetime.combine(d, time(h, m), IST)  # noqa: E731
    assert _run(_closed_for_the_day(db, "NSE", at(date(2026, 10, 27), 16, 0))) is True
    assert _run(_closed_for_the_day(db, "NSE", at(date(2026, 10, 27), 15, 0))) is False      # still open
    assert _run(_closed_for_the_day(db, "NSE", at(date(2026, 10, 28), 16, 0))) is False      # a holiday
    assert _run(_closed_for_the_day(db, "NSE", at(date(2026, 10, 31), 16, 0))) is False      # Saturday


def test_the_last_30m_and_1h_buckets_end_at_the_session_close():
    from app.screener.runtime import bar_close, closed_only
    idx = pd.DatetimeIndex(["2026-10-26 14:15", "2026-10-26 15:15"], tz=IST)
    assert [t.strftime("%H:%M") for t in bar_close(idx, "1h")] == ["15:15", "15:30"]
    assert [t.strftime("%H:%M") for t in bar_close(idx, "30m")] == ["14:45", "15:30"]
    assert [t.strftime("%H:%M") for t in bar_close(idx.tz_convert("UTC"), "1h").tz_convert(IST)] == ["15:15", "15:30"]
    assert [t.strftime("%H:%M") for t in bar_close(idx, "1h", "MCX")] == ["15:15", "16:15"]   # another session's clock
    frame = pd.DataFrame({"close": [1.0, 2.0]}, index=idx)
    assert len(closed_only(frame, "1h", datetime(2026, 10, 26, 15, 31, tzinfo=IST))) == 2      # counts as closed, as on Charts
    assert len(closed_only(frame, "1h", datetime(2026, 10, 26, 15, 29, tzinfo=IST))) == 1


def test_a_1h_screen_keeps_the_sessions_last_bucket():
    from app.screener.routes import _symbol_frames
    minutes = [b for d in _days(2) for b in _minute_bars(d, 1)]
    after_close = datetime.combine(_days(2)[-1], time(15, 31), IST)
    _, frames, _ = _symbol_frames(minutes, "1min", "1h", False, after_close, exchange="NSE")
    assert frames["1h"].index[-1].tz_convert(IST).strftime("%H:%M") == "15:15"


# ---------------------------------------------------------------------------------------------------- A6 / A7 / A8

def _patch_own(monkeypatch, broker: StrictBroker, live: set) -> None:
    from app.brokers import token_lifecycle
    from app.market_data import live_access

    record = types.SimpleNamespace(id=7, broker_name=broker.name, account_label="primary")

    class Own:
        def __init__(self) -> None:
            self.record = record

        def allows(self, symbol, exchange):
            return symbol in live

        def reason(self, symbol, exchange):
            return "outside your session budget"

        def adapter(self):
            return broker

        def market_data(self, adapter=None, **kw):
            return MarketDataService(adapter if adapter is not None else broker, cache_namespace="u1.c7", **kw)

        def badge(self, **kw):
            return {"tier": "A"}

    async def on(session, tenant_id):
        return True

    async def load_user(session, user_id):
        return types.SimpleNamespace(id=user_id, tenant_id=1)

    async def source(session, user, symbols, **kw):
        return Own()
    monkeypatch.setattr(live_access, "byob_on", on)
    monkeypatch.setattr(live_access, "load_user", load_user)
    monkeypatch.setattr(live_access, "user_source", source)
    monkeypatch.setattr(token_lifecycle, "build_adapter", lambda record: broker)
    _not_after_close(monkeypatch)


def test_symbols_outside_the_session_budget_are_still_read_and_counted(monkeypatch):
    from app.screener.routes import fetch_frames
    broker = StrictBroker()
    _patch_own(monkeypatch, broker, live={"AAA"})
    tier: Dict[str, Any] = {}
    universe, problems, _ = _run(fetch_frames(None, 1, ["AAA", "BBB"], "NSE", "15m", {"15m": 20}, user_id=1, tier_out=tier))
    assert problems == {} and [d.symbol for d in universe] == ["AAA", "BBB"] and tier["held_back"] == 1


def test_an_intrabar_read_keeps_the_session_budget(monkeypatch):
    from app.screener.routes import fetch_frames
    broker = StrictBroker()
    _patch_own(monkeypatch, broker, live={"AAA"})
    universe, problems, _ = _run(fetch_frames(None, 1, ["AAA", "BBB"], "NSE", "15m", {"15m": 20}, include_forming=True, user_id=1))
    assert [d.symbol for d in universe] == ["AAA"] and "budget" in problems["BBB"]


def test_a_held_back_daily_symbol_the_api_budget_refuses_reads_stored_daily_bars(monkeypatch):
    from app.market_data import api_budget
    from app.screener import routes, stored_history
    from app.screener.runtime import SymbolData
    broker = StrictBroker()
    _patch_own(monkeypatch, broker, live={"AAA"})

    async def no_token(*a, **kw):
        return False
    monkeypatch.setattr(api_budget, "take", no_token)
    stored_frame = pd.DataFrame({"open": [1.0], "high": [1.0], "low": [1.0], "close": [1.0], "volume": [1.0]},
                                index=pd.DatetimeIndex([pd.Timestamp("2026-10-23", tz="UTC")]))

    async def stored(session, tenant_id, exc, symbols, exchange, base_tf, lookback):
        return [SymbolData(s, {base_tf: stored_frame}, exchange=exchange) for s in symbols], {}, "lake:daily"
    monkeypatch.setattr(stored_history, "stored_universe", stored)
    tier: Dict[str, Any] = {}
    universe, problems, _ = _run(routes.fetch_frames(None, 1, ["AAA", "BBB"], "NSE", "1d", {"1d": 20}, user_id=1, tier_out=tier))
    assert problems == {} and sorted(d.symbol for d in universe) == ["AAA", "BBB"] and tier["held_back"] == 1


def test_a_run_says_once_how_many_were_held_back():
    from app.screener.routes import held_back_notice
    assert held_back_notice(3).startswith("3 held back by the session budget")


def test_an_index_takes_its_near_month_futures_volume(monkeypatch):
    from app.market_data import index_volume
    from app.screener.routes import fetch_frames
    broker = StrictBroker(volume={"NIFTY 50": 0.0, "NIFTY26NOVFUT": 7.0})
    _patch_org_broker(monkeypatch, broker)

    async def near(session, symbol, exchange, on):
        return index_volume.NearFuture(underlying="NIFTY", symbol="NIFTY26NOVFUT", expiry=date(2026, 11, 24), exchange="NFO")
    monkeypatch.setattr(index_volume, "near_future", near)
    universe, problems, _ = _run(fetch_frames(None, 1, ["NIFTY 50", "SBIN"], "NSE", "15m", {"15m": 20}))
    frames = {d.symbol: d.frames["15m"] for d in universe}
    assert problems == {} and (frames["NIFTY 50"]["volume"] == 7.0 * 15).all()          # the future's volume, shaped alike
    assert (frames["SBIN"]["volume"] == 10.0 * 15).all()                                 # a stock keeps its own


def test_the_batch_plan_reads_the_credential_the_run_reads(monkeypatch):
    from app.market_data import candles_routes, live_access
    from app.screener import universes

    async def pick(session, tenant_id, broker, label, **_k):
        assert label == "primary"
        return types.SimpleNamespace(broker_name="zerodha")

    async def off(session, tenant_id):
        return False
    monkeypatch.setattr(candles_routes, "_pick_record", pick)
    monkeypatch.setattr(live_access, "byob_on", off)
    assert _run(universes._broker_name(None, 1, types.SimpleNamespace(id=1, tenant_id=1))) == "zerodha"


def test_intraday_fetch_days_still_cap_at_the_screener_window():
    from app.screener.routes import MAX_INTRADAY_FETCH_DAYS, fetch_days
    assert fetch_days({"15m": 10_000}, "15m") == MAX_INTRADAY_FETCH_DAYS
    assert nodes.TF_MINUTES["1h"] == 60


def test_the_legacy_higher_timeframe_operand_closes_the_last_bucket_at_the_session_close():
    """A5 in the legacy engine too (strategy_engine.declarative, used by the old scanner and the parity fuzz): on the
    session's last 5m bar (15:25 IST) a 30m operand reads the 15:15-15:30 bucket, closed at 15:30 - not 14:45-15:15."""
    from app.strategy_engine.declarative import Operand
    idx = pd.date_range(pd.Timestamp("2026-03-02 03:45", tz="UTC"), periods=75, freq="5min")     # 09:15-15:30 IST
    closes = [100.0 + i for i in range(75)]
    df = pd.DataFrame({"open": closes, "high": closes, "low": closes, "close": closes, "volume": [1.0] * 75}, index=idx)
    htf = Operand(type="indicator", indicator="CLOSE", timeframe="30min").series(df, "5min")
    assert htf.iloc[-1] == closes[-1]                       # the 15:15-15:30 bucket's close
    assert htf.iloc[-4] == closes[-4]                       # 15:10 bar ends 15:15: the 14:45-15:15 bucket
