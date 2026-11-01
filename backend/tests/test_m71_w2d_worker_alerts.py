"""M71 W2-D: the trading worker's strategy bars, the bar-close alerts and the India VIX gate read the own store first
(flag `market_store_reads`). Synthetic store rows and spy brokers only - no network.

- Flag OFF: the strategy reads `get_frames` exactly as before. ON: the same strategy inputs from the store.
- A second cycle on the same bar makes ZERO history calls.
- Every broker call left goes through the credential's DATA budget (api_budget.BudgetedAdapter), never the worker's
  order / exit adapter, and never waits for a token.
- A missing bar is a plain reason (the deployment waits), never a zero-filled bar.
"""
import asyncio
import itertools
from datetime import UTC, date, datetime, time, timedelta
from types import SimpleNamespace

import pandas as pd
import pytest

from app.core.models import OHLCVBar
from app.market_data import api_budget
from app.market_data.calendar import IST
from app.market_data.freshness import closed_frames
from app.market_data.service import MarketDataService
from app.market_store import models as M
from app.market_store import reader as R
from app.workers import strategy_bars
from tests.test_auth_api import _session_factory
from tests.test_copilot_v2_chat import _flag
from tests.test_deployments_api import _auth, _me
from tests.test_m71_pl3_read_service import DAY, _clear, _minutes, _store_days, _store_minutes, _user, _weekdays

MIN = timedelta(minutes=1)
_SEQ = itertools.count(int(datetime.now(UTC).timestamp()) % 100_000)
_RECORD = itertools.count(8_100_000 + (int(datetime.now(UTC).timestamp()) % 100_000) * 10)


def _run(coro):
    return asyncio.run(coro)


def _sym(prefix: str) -> str:
    return f"{prefix}{next(_SEQ)}"           # a symbol unique to this run (the broker path's candle copies live in Redis)


@pytest.fixture(autouse=True)
def _fresh():
    R.reset_cache()
    api_budget.reset()
    yield
    R.reset_cache()


class RowBroker:
    """A broker answering the bars of the store rows it is given (END-labelled rows -> START-stamped IST bars), so the
    flag-off read and the store read see the same market. Counts every call."""
    name = "upstox"

    def __init__(self, rows, now, daily=None):
        self.bars = [OHLCVBar(timestamp=(ts - MIN).astimezone(IST), open=o, high=h, low=lo, close=c, volume=float(v))
                     for ts, o, h, lo, c, v in rows]
        self.daily = daily or []
        self.now = now
        self.history = 0
        self.intraday = 0
        self.quotes = 0

    @staticmethod
    def _agg(bars, interval):
        """1-minute bars -> `interval` bars from the 09:15 open, START-stamped (a partial last bucket kept, as a broker's)."""
        n = int(interval.replace("min", "") or 1)
        out = {}
        for b in bars:
            ist = b.timestamp.astimezone(IST)
            since = (ist - datetime.combine(ist.date(), time(9, 15), IST)) // MIN
            start = datetime.combine(ist.date(), time(9, 15), IST) + (since // n) * n * MIN
            got = out.get(start)
            out[start] = (OHLCVBar(timestamp=start, open=b.open, high=b.high, low=b.low, close=b.close, volume=b.volume) if got is None else
                          OHLCVBar(timestamp=start, open=got.open, high=max(got.high, b.high), low=min(got.low, b.low), close=b.close,
                                   volume=got.volume + b.volume))
        return [out[k] for k in sorted(out)]

    async def get_historical_data(self, symbol, exchange, interval, start, end):
        self.history += 1
        if interval == "day":                                # by date, as Upstox's history API (history_by_date)
            lo, hi = start.astimezone(IST).date(), end.astimezone(IST).date()
            return [b for b in self.daily if lo <= b.timestamp.astimezone(IST).date() <= hi]
        return self._agg([b for b in self.bars if start <= b.timestamp <= end], interval)

    async def get_intraday_candles(self, symbol, exchange, interval):
        self.intraday += 1
        today = self.now.astimezone(IST).date()
        out = [b for b in self.bars if b.timestamp.astimezone(IST).date() == today and b.timestamp < self.now]
        forming = OHLCVBar(timestamp=self.now.replace(second=0, microsecond=0), open=1, high=1, low=1, close=1, volume=1.0)
        return self._agg(out + [forming], interval)

    async def get_quote_for_symbol(self, symbol, exchange):
        self.quotes += 1
        return None

    async def get_ltp_for_symbol(self, symbol, exchange):
        self.quotes += 1
        return 18.0

    @property
    def calls(self) -> int:
        return self.history + self.intraday + self.quotes


class ExitPool:
    """The worker's order / exit adapter: a data read must never reach it."""
    name = "upstox"

    def __getattr__(self, name):
        raise AssertionError(f"a data read used the order / exit adapter ({name})")


def _rest_for(spy):
    return M.RestSource(adapter=spy, record_id=next(_RECORD), broker="upstox", wait_seconds=0.0)


def _frames(user, market_data, symbol, tf, tfs, now, store):
    async def go():
        async with _session_factory() as session:
            return await strategy_bars.frames(session, user, market_data, symbol, "NSE", tf, tfs, now=now, store=store)
    return _run(go())


def _freeze(monkeypatch, now):
    """The broker path reads the wall clock (`get_frames` passes no `now`): frozen at the test's `now`."""
    from app.market_data import service as service_module

    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return now.astimezone(tz) if tz is not None else now.replace(tzinfo=None)
    monkeypatch.setattr(service_module, "datetime", Frozen)


def _two_sessions(now):
    prev = DAY - timedelta(days=1)
    return _minutes(prev, datetime.combine(prev, time(15, 30), IST)) + _minutes(DAY, now)


# --- worker strategies -----------------------------------------------------------------------------------------------------

def test_flag_off_reads_get_frames_exactly_and_on_gives_the_same_strategy_inputs(monkeypatch):
    user = _user()
    sym = _sym("M71WP")
    now = datetime.combine(DAY, time(13, 5), IST)
    rows = _two_sessions(now)
    _store_minutes(f"NSE:{sym}", rows, user.tenant_id, f"broker:upstox:u{user.id}")
    _freeze(monkeypatch, now)
    off_broker = RowBroker(rows, now)
    off = _frames(user, MarketDataService(off_broker, lookback_days=2), sym, "5min", ["5min", "15min"], now, False)
    assert off_broker.history == 1 and off_broker.intraday == 1                       # OFF: the broker path, unchanged

    async def no_session(*a, **k):
        return None
    monkeypatch.setattr(strategy_bars, "_own_rest", no_session)
    on_broker = RowBroker(rows, now)
    on = _frames(user, MarketDataService(on_broker, lookback_days=2), sym, "5min", ["5min", "15min"], now, True)
    assert on_broker.calls == 0                                                        # ON: a store hit, no broker call
    off_closed, on_closed = closed_frames(off, now), closed_frames(on, now)
    for tf in ("5min", "15min"):
        pd.testing.assert_frame_equal(on_closed[tf], off_closed[tf], check_freq=False)
    assert on["5min"].index[-1] + timedelta(minutes=5) <= now                          # closed bars only (no look-ahead)
    _clear(f"NSE:{sym}")


def test_daily_strategy_inputs_are_the_same_and_need_no_broker(monkeypatch):
    user = _user()
    sym = _sym("M71WD")
    now = datetime.combine(DAY, time(13, 5), IST)
    _freeze(monkeypatch, now)
    days = _weekdays(300, DAY - timedelta(days=1))
    _store_days(f"NSE:{sym}", days)
    daily = [OHLCVBar(timestamp=datetime.combine(d, time(0, 0), IST), open=100.0 + k, high=103.0 + k, low=98.0 + k, close=101.0 + k,
                      volume=float(1000 + k)) for k, d in enumerate(days)]
    off = _frames(user, MarketDataService(RowBroker([], now, daily), lookback_days=5), sym, "day", ["day"], now, False)
    spy = RowBroker([], now, daily)
    on = _frames(user, MarketDataService(spy, lookback_days=5), sym, "day", ["day"], now, True)
    assert spy.calls == 0
    pd.testing.assert_frame_equal(on["day"], off["day"], check_freq=False)
    _clear(f"NSE:{sym}")


def test_a_second_cycle_on_the_same_bar_makes_zero_history_calls_through_the_data_budget(monkeypatch):
    user = _user()
    sym = _sym("M71WT")
    now = datetime.combine(DAY, time(13, 5), IST)
    _store_minutes(f"NSE:{sym}", _minutes(DAY, datetime.combine(DAY, time(11, 0), IST)), user.tenant_id, f"broker:upstox:u{user.id}")
    spy = RowBroker(_minutes(DAY, now), now)
    rest = _rest_for(spy)

    async def own(*a, **k):
        return rest
    monkeypatch.setattr(strategy_bars, "_own_rest", own)
    attempts = []
    real = api_budget._attempt

    async def counted(record_id, broker, kind):
        attempts.append((record_id, kind))
        return await real(record_id, broker, kind)
    monkeypatch.setattr(api_budget, "_attempt", counted)
    market_data = MarketDataService(ExitPool(), lookback_days=2)
    first = _frames(user, market_data, sym, "15min", ["15min"], now, True)
    assert spy.intraday == 1 and spy.history == 0                                     # only the newest bars, once
    assert attempts == [(rest.record_id, "history")]                                  # the credential's DATA budget
    second = _frames(user, market_data, sym, "15min", ["15min"], now + timedelta(seconds=40), True)
    assert spy.calls == 1 and len(attempts) == 1                                      # SECOND cycle: zero history calls
    pd.testing.assert_frame_equal(first["15min"], second["15min"])
    _clear(f"NSE:{sym}")


def test_a_cold_symbol_reads_the_old_path_on_the_own_session_and_the_data_budget_never_the_exit_adapter(monkeypatch):
    user = _user()
    sym = _sym("M71WC")
    now = datetime.combine(DAY, time(13, 5), IST)
    spy = RowBroker(_two_sessions(now), now)
    rest = _rest_for(spy)

    async def own(*a, **k):
        return rest
    monkeypatch.setattr(strategy_bars, "_own_rest", own)
    got = _frames(user, MarketDataService(ExitPool(), lookback_days=2), sym, "5min", ["5min"], now, True)
    assert spy.history == 1 and spy.intraday == 1 and len(got["5min"]) > 0


def test_a_budget_refusal_never_waits_and_is_a_plain_reason(monkeypatch):
    user = _user()
    sym = _sym("M71WB")
    now = datetime.combine(DAY, time(13, 5), IST)
    _store_minutes(f"NSE:{sym}", _minutes(DAY, datetime.combine(DAY, time(11, 0), IST)), user.tenant_id, f"broker:upstox:u{user.id}")
    spy = RowBroker(_minutes(DAY, now), now)

    async def own(*a, **k):
        return _rest_for(spy)
    monkeypatch.setattr(strategy_bars, "_own_rest", own)

    async def empty(*a, **k):
        return -1                                                                     # no token this instant
    monkeypatch.setattr(api_budget, "_attempt", empty)

    async def never(*a, **k):
        raise AssertionError("a worker data read must not wait for a token")
    monkeypatch.setattr(api_budget, "take", never)
    with pytest.raises(strategy_bars.BarsNotRead) as refused:
        _frames(user, MarketDataService(ExitPool(), lookback_days=2), sym, "15min", ["15min"], now, True)
    assert str(refused.value) == M.NEWEST_BUDGET and spy.calls == 0
    _clear(f"NSE:{sym}")


def test_missing_bars_are_a_plain_reason_never_zero_filled(monkeypatch):
    user = _user()
    now = datetime.combine(DAY, time(13, 5), IST)

    async def no_session(*a, **k):
        return None
    monkeypatch.setattr(strategy_bars, "_own_rest", no_session)
    with pytest.raises(strategy_bars.BarsNotRead) as cold:
        _frames(user, MarketDataService(ExitPool(), lookback_days=2), _sym("M71WN"), "5min", ["5min"], now, True)
    assert str(cold.value) == M.CONNECT_FOR_INTRADAY
    sym = _sym("M71WG")
    _store_minutes(f"NSE:{sym}", _minutes(DAY, datetime.combine(DAY, time(11, 0), IST)), user.tenant_id, f"broker:upstox:u{user.id}")
    with pytest.raises(strategy_bars.BarsNotRead) as behind:                          # stored bars behind, no own session
        _frames(user, MarketDataService(ExitPool(), lookback_days=2), sym, "5min", ["5min"], now, True)
    assert str(behind.value) == M.CONNECT_FOR_INTRADAY
    _clear(f"NSE:{sym}")


def test_the_worker_reads_strategy_bars_through_the_helper():
    import inspect

    from app.workers import trading_worker
    src = inspect.getsource(trading_worker.TradingWorker._evaluate_deployment)
    assert "strategy_bars.frames(session, user, _history_source(market_data, strategy)" in src
    assert ".get_frames(" not in src                                                   # OFF is the helper's own get_frames


# --- bar-close alerts ------------------------------------------------------------------------------------------------------

@pytest.fixture
def alerts_store():
    yield
    _run(_flag(M.FLAG, False))


def _server_fetch(*args, **kw):
    from app.alerts import barclose

    async def go():
        async with _session_factory() as session:
            return await barclose.server_fetch(session, *args, **kw)
    return _run(go())


def test_bar_close_alerts_read_the_store_with_zero_broker_calls(alerts_store):
    from fastapi import HTTPException
    headers = _auth("m71-w2d-alerts@example.com")
    user = _user(headers)
    syms = [_sym("M71WA"), _sym("M71WA")]
    now = datetime.now(UTC)

    async def calendar():
        async with _session_factory() as session:
            return await R.holidays_for(session, "NSE")
    hol = _run(calendar())
    end = R.last_closed_end(now, 1, hol)
    days = [d for d in _weekdays(4, end.astimezone(IST).date()) if d not in hol][-2:]
    rows = [r for d in days for r in _minutes(d, end.astimezone(IST))]
    for s in syms:
        _store_minutes(f"NSE:{s}", rows, user.tenant_id, f"broker:upstox:u{user.id}")
    with pytest.raises(HTTPException):                                                # OFF: the broker path (no session here)
        _server_fetch(user.tenant_id, syms, "NSE", "1h", {"1h": 3}, user_id=user.id)
    universe, problems, source = _server_fetch(user.tenant_id, syms, "NSE", "1h", {"1h": 3}, user_id=user.id, store=True)
    assert source.startswith("lake:intraday") and not problems
    assert sorted(d.symbol for d in universe) == sorted(syms) and all(len(d.frames["1h"]) >= 3 for d in universe)
    _clear(*[f"NSE:{s}" for s in syms])


def test_a_daily_rule_reads_the_nse_files_and_no_intraday_rebuild(alerts_store):
    headers = _auth("m71-w2d-daily-alerts@example.com")
    tenant_id = _me(headers)["tenant_id"]
    sym = _sym("M71WDA")
    today = datetime.now(UTC).astimezone(IST).date()
    _store_days(f"NSE:{sym}", _weekdays(60, today))
    universe, problems, source = _server_fetch(tenant_id, [sym], "NSE", "1d", {"1d": 20}, store=True)
    assert source == "lake:daily" and not problems and len(universe[0].frames["1d"]) >= 20
    _clear(f"NSE:{sym}")


def test_run_due_passes_the_store_flag_and_keys_the_cycle_cache_per_member(alerts_store, monkeypatch):
    from app.alerts import barclose
    seen = []

    async def fetch(session, tenant_id, symbols, exchange, tf, lookback, **kw):
        seen.append(kw)
        return [], {s: "not stored" for s in symbols}, "lake:intraday"
    rule = SimpleNamespace(tenant_id=1, created_by=42, exchange="NSE", base_tf="15m")
    cache = barclose.CycleCache()
    _run(barclose._current_universe(None, rule, ["ABC"], datetime(2026, 10, 28, 5, 0, tzinfo=UTC), {"15m": 10}, fetch, cache, store=True))
    assert seen == [{"user_id": 42, "store": True}]
    assert next(iter(cache.frames))[1] == 42
    seen.clear()
    _run(barclose._current_universe(None, rule, ["XYZ"], datetime(2026, 10, 28, 5, 0, tzinfo=UTC), {"15m": 10}, fetch, cache))
    assert seen == [{}]                                                               # OFF: the call exactly as before


# --- the India VIX gate ----------------------------------------------------------------------------------------------------

def _last_session_before(day: date) -> date:
    d = day - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def test_vix_previous_close_comes_from_the_store_and_the_live_price_through_the_data_budget(monkeypatch, alerts_store):
    from app.brokers import token_lifecycle
    from app.market_data import candles_routes
    from app.risk_engine import vix_gate
    headers = _auth("m71-w2d-vix@example.com")
    tenant_id = _me(headers)["tenant_id"]
    key = "NSE_INDEX:INDIAVIX"
    now = datetime.now(UTC)
    last = _last_session_before(now.astimezone(IST).date())
    _store_days(key, _weekdays(8, last))
    spy = RowBroker([], now)
    record = SimpleNamespace(id=next(_RECORD), broker_name="upstox")

    async def pick(*a, **k):
        return record
    monkeypatch.setattr(candles_routes, "_pick_record", pick)
    monkeypatch.setattr(token_lifecycle, "build_adapter", lambda rec: spy)
    attempts = []
    real = api_budget._attempt

    async def counted(record_id, broker, kind):
        attempts.append((record_id, kind))
        return await real(record_id, broker, kind)
    monkeypatch.setattr(api_budget, "_attempt", counted)
    _run(_flag(M.FLAG, True))

    async def go():
        async with _session_factory() as session:
            row = await vix_gate.capture_for_tenant(session, tenant_id, None, org_adapter=ExitPool(),
                                                    market_data_factory=lambda a, lookback_days: MarketDataService(a, lookback_days=lookback_days),
                                                    now=now)
            return row.last_price, row.payload_json
    price, payload = _run(go())
    assert price == 18.0 and spy.history == 0                                         # no daily broker call
    assert '"prev_close": 108.0' in payload                                           # the store's last session (8th day)
    assert attempts and all(r == record.id for r, _k in attempts)                     # the DATA budget, not the exit adapter
    _clear(key)


def test_vix_store_without_the_last_session_falls_back_to_the_daily_candles():
    from app.risk_engine import vix_gate
    key = "NSE_INDEX:INDIAVIX"
    now = datetime.now(UTC)
    last = _last_session_before(now.astimezone(IST).date())
    _store_days(key, _weekdays(5, _last_session_before(last)))                       # one session behind

    async def go():
        async with _session_factory() as session:
            return await vix_gate.stored_day_bars(session, now)
    assert _run(go()) is None
    _store_days(key, _weekdays(5, last))

    async def again():
        async with _session_factory() as session:
            return await vix_gate.stored_day_bars(session, now)
    bars = _run(again())
    assert bars and bars[-1].timestamp.astimezone(IST).date() == last
    _clear(key)


def test_vix_flag_off_reads_exactly_as_before():
    from app.risk_engine import vix_gate
    now = datetime.now(UTC)
    spy = RowBroker([], now, [OHLCVBar(timestamp=datetime.combine(_last_session_before(now.astimezone(IST).date()), time(0, 0), IST),
                                       open=15, high=16, low=14, close=15.0, volume=0.0)])
    spy.name = f"spy{next(_SEQ)}"                                                    # its own shared candle copies
    reading = _run(vix_gate.read(MarketDataService(spy, lookback_days=10), now))
    assert spy.history == 1 and reading["prev_close"] == 15.0 and reading["last"] == 18.0
