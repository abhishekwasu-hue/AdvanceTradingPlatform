"""M71 W2-A: Charts, Multi-chart, Terminal and the index sparks (`POST /api/market-data/candles`) read the own store first
under the flag `market_store_reads` (OFF = the broker path, unchanged). Synthetic store rows and a spy broker only.

- history comes from the store; only the newest missing bars by paced REST; a reload makes ZERO broker calls;
- bars keep the chart's START labels in India time; the forming bucket comes from the reader's own stored minutes;
- a user without his own broker gets Daily / Weekly and the plain "Connect your broker for intraday bars." line;
- an index's volume from its future: a bar the future has no bar for has no volume (null, never 0);
- gaps are shown in plain words, never filled.
"""
import random
import string
from datetime import UTC, date, datetime, time, timedelta

import pytest

from app.core.models import OHLCVBar
from app.market_data import api_budget, candles_routes, candles_store, periods
from app.market_data.calendar import IST
from app.market_store import models as M
from app.market_store import reader as R
from tests.test_auth_api import _session_factory, client
from tests.test_copilot_v2_chat import _flag
from tests.test_m71_pl3_read_service import Spy, _clear, _minutes, _run, _store_days, _store_minutes, _weekdays
from tests.test_phase_aa_market_data_api import _store_credentials
from tests.test_phase_k_commercial import _owner

RUN = "".join(random.choice(string.ascii_uppercase) for _ in range(5))       # fresh symbols: no copy from an earlier run


def _sym(name: str) -> str:
    return f"W2A{name}{RUN}"


@pytest.fixture(autouse=True)
def _fresh():
    R.reset_cache()
    api_budget.reset()
    yield
    R.reset_cache()
    _run(_flag(M.FLAG, False))


def _holidays():
    async def go():
        async with _session_factory() as session:
            return await R.holidays_for(session, "NSE")
    return _run(go())


def _recent_rows(sessions=2, upto=None):
    """Store rows (END-labelled) of the newest `sessions` sessions up to the last closed minute (or `upto`)."""
    hol = _holidays()
    end = upto or R.last_closed_end(datetime.now(UTC), 1, hol)
    days = [d for d in _weekdays(sessions + 3, end.astimezone(IST).date()) if d not in hol][-sessions:]
    return [r for d in days for r in _minutes(d, end.astimezone(IST))], days


def _patch(monkeypatch, spy):
    monkeypatch.setattr(candles_store, "build_adapter", lambda record: spy)
    monkeypatch.setattr(candles_routes, "build_adapter", lambda record: spy)


def _post(headers, symbols, timeframe="15min", **kw):
    return client.post("/api/market-data/candles", headers=headers, json={"symbols": symbols, "timeframe": timeframe, **kw})


def test_flag_off_reads_the_broker_and_flag_on_reads_the_store_with_zero_calls_on_reload(monkeypatch):
    headers, me = _owner("m71-w2a-store@example.com")
    _store_credentials(me)
    spy = Spy()
    _patch(monkeypatch, spy)
    sym = _sym("A")
    rows, days = _recent_rows()
    _store_minutes(f"NSE:{sym}", rows, me["tenant_id"], f"broker:upstox:u{me['id']}")

    off = _post(headers, [sym])
    assert off.status_code == 200 and spy.history >= 1                       # flag off: the broker path, unchanged
    assert "broker_calls" not in off.json()

    _run(_flag(M.FLAG, True))
    spy.history = spy.intraday = 0
    first = _post(headers, [sym])
    assert first.status_code == 200, first.text
    got = first.json()
    entry = got["symbols"][sym]
    assert spy.calls == 0 and got["broker_calls"] == 0                          # a store hit: no broker call at all
    assert entry["count"] > 0 and entry["error"] is None
    stamps = [datetime.fromisoformat(b["timestamp"]) for b in entry["bars"]]
    assert all(b["timestamp"].endswith("+05:30") for b in entry["bars"])          # the broker's own form, India time
    assert stamps[0] == datetime.combine(days[0], time(9, 15), IST)               # START label of the session's first bar
    assert all((s - datetime.combine(s.date(), time(9, 15), IST)) % timedelta(minutes=15) == timedelta(0) for s in stamps)
    # bars match the stored minutes: the first 15-minute bar is the first 15 one-minute rows
    first_rows = [r for r in rows if r[0] <= (datetime.combine(days[0], time(9, 30), IST)).astimezone(UTC)]
    assert entry["bars"][0]["open"] == first_rows[0][1] and entry["bars"][0]["close"] == first_rows[-1][4]
    assert entry["bars"][0]["high"] == max(r[2] for r in first_rows)
    assert entry["bars"][0]["volume"] == sum(r[5] for r in first_rows)

    second = _post(headers, [sym], lookback_days=2)                               # the chart's minute refresh
    assert second.status_code == 200 and spy.calls == 0 and second.json()["broker_calls"] == 0
    _clear(f"NSE:{sym}")


def test_only_the_newest_bars_are_topped_up_once_and_a_reload_makes_zero_history_calls(monkeypatch):
    headers, me = _owner("m71-w2a-topup@example.com")
    _store_credentials(me)
    hol = _holidays()
    now = datetime.now(UTC)
    last = R.last_closed_end(now, 1, hol)
    rows, days = _recent_rows(upto=last - timedelta(hours=2))                     # the store is two hours behind
    sym = _sym("B")
    _store_minutes(f"NSE:{sym}", rows, me["tenant_id"], f"broker:upstox:u{me['id']}")
    spy = Spy(day=last.astimezone(IST).date())
    _patch(monkeypatch, spy)
    _run(_flag(M.FLAG, True))
    first = _post(headers, [sym], timeframe="60min").json()
    assert 1 <= spy.calls <= 2 and first["broker_calls"] == spy.calls             # past session + running session at most
    assert first["symbols"][sym]["count"] > 0
    calls = spy.calls
    again = _post(headers, [sym], timeframe="60min", lookback_days=2).json()
    assert spy.calls == calls and again["broker_calls"] == 0                      # kept until the next 1H bar closes
    _clear(f"NSE:{sym}")


def test_a_budget_refusal_is_a_plain_gap_and_the_stored_bars_still_show(monkeypatch):
    headers, me = _owner("m71-w2a-budget@example.com")
    _store_credentials(me)
    hol = _holidays()
    last = R.last_closed_end(datetime.now(UTC), 1, hol)
    rows, _days = _recent_rows(upto=last - timedelta(hours=2))
    sym = _sym("C")
    _store_minutes(f"NSE:{sym}", rows, me["tenant_id"], f"broker:upstox:u{me['id']}")
    spy = Spy()
    _patch(monkeypatch, spy)

    async def refuse(*a, **k):
        return False
    monkeypatch.setattr(api_budget, "take", refuse)
    _run(_flag(M.FLAG, True))
    got = _post(headers, [sym], timeframe="60min").json()["symbols"][sym]
    assert spy.calls == 0 and got["count"] > 0 and got["error"] is None
    assert M.NEWEST_BUDGET in got["gaps"]
    _clear(f"NSE:{sym}")


def test_without_an_own_broker_daily_and_weekly_show_and_intraday_says_connect_your_broker(monkeypatch):
    headers, me = _owner("m71-w2a-nobroker@example.com")
    sym = _sym("D")
    today = datetime.now(UTC).astimezone(IST).date()
    stored_days = _weekdays(60, today - timedelta(days=1))
    _store_days(f"NSE:{sym}", stored_days)
    assert _post(headers, [sym], timeframe="day").status_code == 409             # flag off: no session, as before
    _run(_flag(M.FLAG, True))
    day = _post(headers, [sym], timeframe="day", lookback_days=60)
    assert day.status_code == 200, day.text
    entry = day.json()["symbols"][sym]
    assert entry["count"] > 0 and entry["bars"][-1]["timestamp"].endswith("T00:00:00+05:30")
    week = _post(headers, [sym], timeframe="week", lookback_days=60).json()["symbols"][sym]
    want = periods.aggregate([OHLCVBar(timestamp=datetime.fromisoformat(b["timestamp"]), open=b["open"], high=b["high"],
                                       low=b["low"], close=b["close"], volume=b["volume"]) for b in entry["bars"]], "week")
    assert [b["close"] for b in week["bars"]] == [b.close for b in want][-len(week["bars"]):]
    intraday = _post(headers, [sym], timeframe="15min").json()["symbols"][sym]
    assert intraday["bars"] == [] and intraday["error"] is None and intraday["empty_reason"] == M.CONNECT_FOR_INTRADAY
    _clear(f"NSE:{sym}")


def test_a_symbol_the_store_holds_nothing_of_reads_the_broker_path_as_before(monkeypatch):
    headers, me = _owner("m71-w2a-cold@example.com")
    _store_credentials(me)
    spy = Spy()
    _patch(monkeypatch, spy)
    _run(_flag(M.FLAG, True))
    sym = _sym("E")
    got = _post(headers, [sym])
    assert got.status_code == 200, got.text
    assert spy.history >= 1 and got.json()["symbols"][sym]["count"] > 0
    assert got.json()["source"]["broker"] == "upstox"


def test_intraday_through_another_members_credential_is_never_the_broker_path():
    record = type("Rec", (), {"broker_name": "upstox", "account_label": "primary"})()
    member = candles_store.Own(record=record, mine=False)
    assert member.broker_path("RELIANCE", "NSE", intraday=True) is False         # M71Q-2: his own credential only
    assert member.broker_path("RELIANCE", "NSE", intraday=False) is True
    assert member.tops_up("RELIANCE", "NSE") is None
    assert candles_store.Own().broker_path("RELIANCE", "NSE", intraday=False) is False


def _bar(ts, price=100.0, volume=10.0):
    return OHLCVBar(timestamp=ts, open=price, high=price + 1, low=price - 1, close=price + 0.5, volume=volume)


def test_a_missing_future_bar_has_no_volume_never_zero():
    t0 = datetime(2026, 10, 28, 9, 15, tzinfo=IST)
    index = [_bar(t0 + timedelta(minutes=15 * i), volume=float("nan")) for i in range(3)]
    fut = [_bar(t0, volume=500.0), _bar(t0 + timedelta(minutes=30), volume=700.0)]
    out = candles_store.to_json(candles_store.future_volume(index, fut))
    assert [b["volume"] for b in out] == [500.0, None, 700.0]


def test_the_forming_bucket_comes_from_the_own_minutes_after_the_last_closed_bar_only():
    day = date(2026, 10, 28)
    now = datetime.combine(day, time(10, 7, 30), IST).astimezone(UTC)
    minutes = [_bar((datetime.combine(day, time(9, 15), IST) + timedelta(minutes=i)).astimezone(UTC), price=100 + i)
               for i in range(52)]                                                   # 09:15 .. 10:06, all closed by 10:07:30
    bar = candles_store.forming_bar(minutes, "15min", now, set(), "NSE", after=datetime.combine(day, time(9, 45), IST))
    assert bar is not None and bar.timestamp == datetime.combine(day, time(10, 0), IST)
    assert bar.open == 100 + 45 and bar.close == 100 + 51 + 0.5 and bar.volume == 70.0  # 10:00 .. 10:06, nothing later
    # already a closed bar there, a one-minute chart, or no minutes after the cut: no forming bar
    assert candles_store.forming_bar(minutes, "15min", now, set(), "NSE", after=datetime.combine(day, time(10, 0), IST)) is None
    assert candles_store.forming_bar(minutes, "1min", now, set(), "NSE", after=None) is None
    assert candles_store.forming_bar(minutes[:45], "15min", now, set(), "NSE", after=None) is None


def test_the_windows_match_the_broker_path():
    sat = datetime(2026, 10, 31, 12, 0, tzinfo=IST).astimezone(UTC)
    start, end = candles_store.intraday_window(sat, 1, None, set(), "NSE")
    assert start == datetime(2026, 10, 30, 0, 0, tzinfo=IST) and end == sat           # a 1-day lookback still holds Friday
    start, end = candles_store.intraday_window(sat, 5, date(2026, 10, 28), set(), "NSE")
    assert start.astimezone(IST).date() == date(2026, 10, 23) and end.astimezone(IST).date() == date(2026, 10, 27)
    assert candles_store.daily_window(sat, 5, None, None, None) == (date(2026, 10, 31) - timedelta(days=400), date(2026, 10, 30))


class _RowsBroker(Spy):
    """A broker that answers exactly the stored minutes (START = END - 1 minute, India time)."""

    def __init__(self, rows):
        super().__init__()
        self.rows = [OHLCVBar(timestamp=(ts - timedelta(minutes=1)).astimezone(IST), open=o, high=h, low=lo, close=c, volume=v)
                     for ts, o, h, lo, c, v in rows]

    async def get_historical_data(self, symbol, exchange, interval, start, end):
        self.history += 1
        return [b for b in self.rows if start <= b.timestamp <= end]

    async def get_intraday_candles(self, symbol, exchange, interval):
        self.intraday += 1
        today = datetime.now(UTC).astimezone(IST).date()
        return [b for b in self.rows if b.timestamp.date() == today]


@pytest.mark.parametrize("timeframe", ["5min", "15min", "60min"])
def test_the_store_gives_the_same_closed_bars_as_the_broker_path(monkeypatch, timeframe):
    headers, me = _owner(f"m71-w2a-parity-{timeframe}@example.com")
    _store_credentials(me)
    rows, days = _recent_rows()
    sym = _sym(f"P{timeframe[:-3]}")
    _store_minutes(f"NSE:{sym}", rows, me["tenant_id"], f"broker:upstox:u{me['id']}")
    broker = _RowsBroker(rows)
    _patch(monkeypatch, broker)
    before = _post(headers, [sym], timeframe=timeframe).json()["symbols"][sym]["bars"]
    _run(_flag(M.FLAG, True))
    after = _post(headers, [sym], timeframe=timeframe).json()["symbols"][sym]["bars"]
    past = days[0]                                       # a whole closed session: every bar closed on both paths
    pick = [b for b in before if datetime.fromisoformat(b["timestamp"]).date() == past]
    got = [b for b in after if datetime.fromisoformat(b["timestamp"]).date() == past]
    assert pick and got == pick
    _clear(f"NSE:{sym}")
