"""UI2-4a2: weekly and monthly candles, aggregated in IST from daily bars (never the broker's own week / month).

* W and M equal a hand aggregation of the daily series (O first, H max, L min, C last, V sum), stamped at the period's
  first trading day 00:00 IST: a week starting on a holiday Monday at its Tuesday, no bar on a Sunday even when the
  broker stamps daily bars in UTC, a Diwali muhurat session counted in its week and month;
* defaults W 5 years, M 20 years, allowed past the daily cap for W / M only;
* the daily bars come in whole-date chunks of at most the broker's per-request cap, shared (LD-10) and stopped where the
  broker has nothing older, and `before` pages back by whole weeks / months."""
import asyncio
import uuid
from collections import OrderedDict
from datetime import date, datetime, time, timedelta, timezone
from typing import Dict, Iterable, List, Optional

import pytest

from app.core.models import OHLCVBar
from app.market_data import api_budget, candles_routes, periods, shared_candles
from app.market_data.calendar import IST
from app.market_data.service import MarketDataService
from tests.test_auth_api import client
from tests.test_market_data_service import _install_cache
from tests.test_phase_aa_market_data_api import _Broker, _store_credentials
from tests.test_phase_k_commercial import _owner

run = asyncio.run
UTC = timezone.utc
HOLIDAYS = {date(2026, 1, 26), date(2023, 11, 14), date(2024, 3, 25), date(2025, 10, 21)}
SPECIAL = {date(2023, 11, 12)}                         # Diwali muhurat 2023: a Sunday evening session


def _email(tag: str) -> str:
    return f"ui24wm-{tag}-{uuid.uuid4().hex[:8]}@example.com"


def _sym(prefix: str = "WM") -> str:
    return f"{prefix}{uuid.uuid4().hex[:6].upper()}"


def daily_series(start: date, end: date, *, utc_stamp: bool = False, scale: float = 1.0) -> List[OHLCVBar]:
    """A deterministic daily series: weekdays except HOLIDAYS, plus SPECIAL sessions. Stamped 00:00 IST like Upstox, or
    (utc_stamp) 18:30 UTC of the day before - the same instant spelt in UTC."""
    out: List[OHLCVBar] = []
    day = start
    while day <= end:
        if (day.weekday() < 5 and day not in HOLIDAYS) or day in SPECIAL:
            n = day.toordinal()
            o = 100 + n % 37
            ts = datetime.combine(day, time(0, 0), tzinfo=IST)
            out.append(OHLCVBar(timestamp=ts.astimezone(UTC) if utc_stamp else ts, open=o, high=o + 3 + n % 5, low=o - 2 - n % 3,
                                close=o + n % 7 - 3, volume=(1000 + n % 101) * scale))
        day += timedelta(days=1)
    return out


def hand(bars: Iterable[OHLCVBar], period: str) -> List[dict]:
    """The expected aggregation, by a different route: ISO (year, week) or (year, month) of the IST date."""
    groups: "OrderedDict[tuple, list]" = OrderedDict()
    for b in sorted(bars, key=lambda x: x.timestamp):
        d = b.timestamp.astimezone(IST).date()
        k = d.isocalendar()[:2] if period == "week" else (d.year, d.month)
        groups.setdefault(k, []).append((d, b))
    out = []
    for rows in groups.values():
        stamp = next((d for d, _ in rows if d.weekday() < 5), rows[0][0])
        bs = [b for _, b in rows]
        out.append({"day": stamp, "open": bs[0].open, "high": max(b.high for b in bs), "low": min(b.low for b in bs),
                    "close": bs[-1].close, "volume": sum(b.volume for b in bs)})
    return out


def _as_rows(bars: Iterable[OHLCVBar]) -> List[dict]:
    return [{"day": b.timestamp.astimezone(IST).date(), "open": b.open, "high": b.high, "low": b.low, "close": b.close,
             "volume": b.volume} for b in bars]


def _json_rows(items: List[dict]) -> List[dict]:
    out = []
    for item in items:
        ts = datetime.fromisoformat(item["timestamp"])
        assert ts.astimezone(IST).time() == time(0, 0)                      # IST midnight, like a daily bar
        out.append({"day": ts.astimezone(IST).date(), "open": item["open"], "high": item["high"], "low": item["low"],
                    "close": item["close"], "volume": item["volume"]})
    return out


def _ist(dt: datetime) -> datetime:
    return (dt if dt.tzinfo else dt.replace(tzinfo=UTC)).astimezone(IST)


class DailyBroker(_Broker):
    """Serves each symbol's daily series for any window (as a broker would), and today's one-minute bars."""
    name = "ui24wm"
    daily_days_per_request = 3650

    def __init__(self, daily: Optional[Dict[str, List[OHLCVBar]]] = None, today: Optional[Dict[str, List[OHLCVBar]]] = None,
                 default: Optional[List[OHLCVBar]] = None, minutes: Optional[Dict[str, List[OHLCVBar]]] = None) -> None:
        super().__init__()
        self.daily = daily or {}
        self.today = today or {}
        self.minutes = minutes or {}
        self.default = default
        self.windows: List[tuple] = []
        self.intraday: List[tuple] = []

    async def get_historical_data(self, symbol, exchange, interval, from_date, to_date):
        self.windows.append((symbol, exchange, interval, from_date, to_date))
        if interval == "1min":
            lo, hi = _ist(from_date), _ist(to_date)
            return [b for b in self.minutes.get(symbol, []) if lo <= _ist(b.timestamp) <= hi]
        assert interval == "day"
        lo, hi = _ist(from_date).date(), _ist(to_date).date()
        series = self.daily.get(symbol, self.default or [])
        return [b for b in series if lo <= _ist(b.timestamp).date() <= hi]

    async def get_intraday_candles(self, symbol, exchange, interval):
        self.intraday.append((symbol, exchange, interval))
        return list(self.today.get(symbol, []))


def _generous(monkeypatch, per_minute: int = 10_000) -> None:
    monkeypatch.setattr(api_budget, "api_limits",
                        lambda broker_name: api_budget.ApiLimits(per_second=1000, per_minute=per_minute, per_30min=100_000, burst=1000))
    api_budget.reset()


def _refuse_every_token(monkeypatch) -> None:
    async def attempt(*args, **kwargs):
        return 0

    async def take(*args, **kwargs):
        return False
    monkeypatch.setattr(api_budget, "_attempt", attempt)
    monkeypatch.setattr(api_budget, "take", take)


def _setup(monkeypatch, broker: DailyBroker, tag: str):
    headers, me = _owner(_email(tag))
    _store_credentials(me)
    monkeypatch.setattr(candles_routes, "build_adapter", lambda record: broker)
    _install_cache(monkeypatch, None)
    shared_candles.reset()
    _generous(monkeypatch)
    return headers, me


def _today() -> date:
    return datetime.now(UTC).astimezone(IST).date()


def _post(headers, **body):
    res = client.post("/api/market-data/candles", headers=headers, json=body)
    assert res.status_code == 200, res.text
    return res.json()


# --- pure aggregation --------------------------------------------------------------------------------------------------

def test_week_and_month_equal_a_hand_aggregation_of_the_daily_series():
    daily = daily_series(date(2022, 1, 1), date(2026, 10, 16))
    for period in ("week", "month"):
        assert _as_rows(periods.aggregate(daily, period)) == hand(daily, period)
    weeks = periods.aggregate(daily, "week")
    assert all(b.timestamp.tzinfo is not None and b.timestamp.utcoffset() == timedelta(hours=5, minutes=30) for b in weeks)
    # Any order and a duplicated date in: the same answer (the later copy of a date wins, as merge_bars does).
    shuffled = list(reversed(daily)) + [daily[-1]]
    assert periods.aggregate(shuffled, "month") == periods.aggregate(daily, "month")


def test_a_week_starting_on_a_holiday_monday_is_stamped_at_its_first_session():
    daily = daily_series(date(2026, 1, 19), date(2026, 2, 6))
    weeks = {b.timestamp.astimezone(IST).date(): b for b in periods.aggregate(daily, "week")}
    assert sorted(weeks) == [date(2026, 1, 19), date(2026, 1, 27), date(2026, 2, 2)]       # 26 Jan (Republic Day) closed
    held = [b for b in daily if date(2026, 1, 27) <= b.timestamp.date() <= date(2026, 1, 30)]
    week = weeks[date(2026, 1, 27)]
    assert (week.open, week.close, week.volume) == (held[0].open, held[-1].close, sum(b.volume for b in held))
    assert week.timestamp == datetime(2026, 1, 27, tzinfo=IST)


def test_a_month_across_diwali_counts_the_muhurat_session_and_skips_the_holiday():
    daily = daily_series(date(2023, 10, 25), date(2023, 12, 5))
    months = {b.timestamp.astimezone(IST).date(): b for b in periods.aggregate(daily, "month")}
    nov = months[date(2023, 11, 1)]                                     # the month's first trading day (a Wednesday)
    held = [b for b in daily if b.timestamp.date().month == 11]
    assert any(b.timestamp.date() == date(2023, 11, 12) for b in held)                     # the Sunday muhurat session
    assert not any(b.timestamp.date() == date(2023, 11, 14) for b in held)                 # Balipratipada: closed
    assert nov.volume == sum(b.volume for b in held) and nov.high == max(b.high for b in held) and nov.close == held[-1].close
    weeks = {b.timestamp.astimezone(IST).date(): b for b in periods.aggregate(daily, "week")}
    muhurat_week = weeks[date(2023, 11, 6)]                             # Mon 6 .. Sun 12: the muhurat session is its last bar
    assert muhurat_week.close == next(b.close for b in daily if b.timestamp.date() == date(2023, 11, 12))
    assert date(2023, 11, 12) not in weeks and date(2023, 11, 13) in weeks


def test_ist_alignment_daily_bars_stamped_in_utc_never_make_a_sunday_bar():
    utc_daily = daily_series(date(2025, 1, 1), date(2026, 6, 30), utc_stamp=True)
    assert any(b.timestamp.astimezone(UTC).weekday() == 6 for b in utc_daily)             # Monday 00:00 IST = Sunday in UTC
    ist_daily = daily_series(date(2025, 1, 1), date(2026, 6, 30))
    for period in ("week", "month"):
        bars = periods.aggregate(utc_daily, period)
        assert all(b.timestamp.astimezone(IST).weekday() < 5 for b in bars)                  # no Sunday (or Saturday) bar
        assert _as_rows(bars) == _as_rows(periods.aggregate(ist_daily, period))              # the same weeks either way
    weeks = periods.aggregate(utc_daily, "week")[1:]                    # (the first week starts with the series, a Wednesday)
    assert all(b.timestamp.astimezone(IST).date().weekday() == 0 or b.timestamp.astimezone(IST).date() - timedelta(days=1) in HOLIDAYS
               for b in weeks)


def test_chunks_are_whole_dates_newest_first_within_the_cap():
    got = periods.chunks(date(2020, 1, 6), date(2026, 10, 19), 365)
    assert got[0][1] == date(2026, 10, 19) and got[-1][0] == date(2020, 1, 6)
    assert all((hi - lo).days + 1 <= 365 for lo, hi in got)
    assert all(newer[0] - timedelta(days=1) == older[1] for newer, older in zip(got, got[1:]))     # contiguous, no gap
    assert periods.chunks(date(2026, 1, 2), date(2026, 1, 1), 365) == []


# --- the route ---------------------------------------------------------------------------------------------------------

def test_week_and_month_through_the_route_match_the_daily_bars(monkeypatch):
    today = _today()
    sym = _sym()
    daily = daily_series(today - timedelta(days=7400), today - timedelta(days=1))
    broker = DailyBroker({sym: daily})
    headers, _ = _setup(monkeypatch, broker, "route")
    src = client.get("/api/market-data/sources", headers=headers).json()
    assert "week" in src["timeframes"] and "month" in src["timeframes"] and src["monthly_max_lookback_days"] == 7300

    for period, default in (("week", 1826), ("month", 7300)):
        body = _post(headers, symbols=[sym], timeframe=period)
        assert body["lookback_days"] == default and body["base_interval"] == "day" and body["timeframe"] == period
        end = today - timedelta(days=1)
        start = periods.period_start(end - timedelta(days=default - 1), period)
        expected = hand([b for b in daily if start <= b.timestamp.date() <= end], period)
        item = body["symbols"][sym]
        assert item["error"] is None and item["volume_source"] is None
        assert _json_rows(item["bars"]) == expected and item["count"] == len(expected)
        assert not any("forming" in b for b in item["bars"])                       # not asked for: completed days only
    assert all(w[2] == "day" for w in broker.windows) and broker.intraday == []     # never the broker's own W / M


def test_lookback_caps_week_month_past_the_daily_cap_and_day_still_refuses(monkeypatch):
    sym = _sym()
    broker = DailyBroker(default=daily_series(_today() - timedelta(days=60), _today() - timedelta(days=1)))
    headers, _ = _setup(monkeypatch, broker, "caps")
    body = _post(headers, symbols=[sym], timeframe="week", lookback_days=5000)
    assert body["lookback_days"] == 3650 and any("clamped to 3650" in w for w in body["warnings"])
    body = _post(headers, symbols=[sym], timeframe="month", lookback_days=400)
    assert body["lookback_days"] == 400
    res = client.post("/api/market-data/candles", headers=headers, json={"symbols": [sym], "timeframe": "day", "lookback_days": 731})
    assert res.status_code == 422                                                  # as before for D and intraday
    res = client.post("/api/market-data/candles", headers=headers, json={"symbols": [sym], "timeframe": "month", "lookback_days": 7301})
    assert res.status_code == 422
    res = client.post("/api/market-data/candles", headers=headers, json={"symbols": [sym], "timeframe": "quarter"})
    assert res.status_code == 400


def test_chunking_respects_the_brokers_cap_and_the_chunks_are_shared(monkeypatch):
    today = _today()
    sym = _sym()
    daily = daily_series(today - timedelta(days=2000), today - timedelta(days=1))
    broker = DailyBroker({sym: daily})
    broker.daily_days_per_request = 365
    headers, _ = _setup(monkeypatch, broker, "chunks")
    body = _post(headers, symbols=[sym], timeframe="week")
    wins = [(_ist(f), _ist(t)) for s, _x, _i, f, t in broker.windows if s == sym]
    assert len(wins) == 6                                                          # 1,826+ days in chunks of 365
    for lo, hi in wins:
        assert lo.time() == time(0, 0) and hi.time() == time(23, 59)              # whole IST dates (one shared copy per day)
        assert (hi.date() - lo.date()).days + 1 <= 365
    assert all(newer[0].date() - timedelta(days=1) == older[1].date() for newer, older in zip(wins, wins[1:]))   # newest first
    end = today - timedelta(days=1)
    start = periods.period_start(end - timedelta(days=1825), "week")
    assert wins[0][1].date() == end and wins[-1][0].date() == start
    assert _json_rows(body["symbols"][sym]["bars"]) == hand([b for b in daily if start <= b.timestamp.date() <= end], "week")

    # Another user's monthly chart the same day: the newest chunks are the same windows - shared, not fetched again.
    other = DailyBroker({sym: daily})
    other.daily_days_per_request = 365
    monkeypatch.setattr(candles_routes, "build_adapter", lambda record: other)
    headers2, me2 = _owner(_email("chunks2"))
    _store_credentials(me2)
    _post(headers2, symbols=[sym], timeframe="month", lookback_days=1826)
    assert len(other.windows) <= 1                                                 # at most the one older partial chunk

    # A broker serving 2,000 days a request (Kite, Angel One): one call for five years.
    kite = DailyBroker({sym: daily})
    kite.name = "ui24wmkite"
    kite.daily_days_per_request = 2000
    monkeypatch.setattr(candles_routes, "build_adapter", lambda record: kite)
    _post(headers, symbols=[sym], timeframe="week")
    assert len(kite.windows) == 1


def test_no_calls_beyond_where_the_broker_has_data(monkeypatch):
    today = _today()
    sym = _sym()
    listed = today - timedelta(days=500)
    broker = DailyBroker({sym: daily_series(listed, today - timedelta(days=1))})
    broker.daily_days_per_request = 365
    headers, _ = _setup(monkeypatch, broker, "listed")
    body = _post(headers, symbols=[sym], timeframe="month")                       # twenty years asked for
    assert len(broker.windows) == 2                                               # stops in the chunk where the data begins
    assert body["symbols"][sym]["count"] == len(hand(daily_series(listed, today - timedelta(days=1)), "month"))


def test_a_budget_stop_keeps_whole_periods_and_the_next_request_continues_from_the_shared_chunks(monkeypatch):
    today = _today()
    sym = _sym()
    daily = daily_series(today - timedelta(days=2000), today - timedelta(days=1))
    broker = DailyBroker({sym: daily})
    broker.daily_days_per_request = 365
    headers, _ = _setup(monkeypatch, broker, "stop")
    _generous(monkeypatch, per_minute=3)
    body = _post(headers, symbols=[sym], timeframe="week")
    item = body["symbols"][sym]
    assert item["error"] is None and any("older weekly bars not loaded this time (budget used)" in w for w in body["warnings"])
    assert len(broker.windows) == 3
    oldest = datetime.fromisoformat(item["bars"][0]["timestamp"]).astimezone(IST).date()
    covered_from = _ist(broker.windows[-1][3]).date()
    assert oldest >= covered_from and periods.period_start(oldest, "week") >= covered_from      # never a cut week
    api_budget.reset()                                                            # the minute rolls over
    body = _post(headers, symbols=[sym], timeframe="week")
    assert len(broker.windows) == 6 and not any("not loaded" in w for w in body["warnings"])   # three new calls only
    assert body["symbols"][sym]["count"] > item["count"]

    # The newest chunk without a token: the symbol says budget used, like any other candle request.
    shared_candles.reset()
    _refuse_every_token(monkeypatch)
    body = _post(headers, symbols=[sym], timeframe="month")
    assert body["symbols"][sym]["error"] == "budget used" and body["symbols"][sym]["volume_source"] is None


@pytest.mark.parametrize("period, page", [("week", 1826), ("month", 7300)])
def test_before_pages_back_by_whole_periods(monkeypatch, period, page):
    today = _today()
    sym = _sym()
    daily = daily_series(today - timedelta(days=16000), today - timedelta(days=1))
    broker = DailyBroker({sym: daily})
    headers, _ = _setup(monkeypatch, broker, f"before-{period}")
    first = _post(headers, symbols=[sym], timeframe=period)["symbols"][sym]["bars"]
    oldest = datetime.fromisoformat(first[0]["timestamp"]).astimezone(IST).date()
    broker.windows.clear()
    body = _post(headers, symbols=[sym], timeframe=period, before=oldest.isoformat())
    assert body["before"] == oldest.isoformat() and broker.intraday == []
    end = periods.period_start(oldest, period) - timedelta(days=1)
    start = periods.period_start(end - timedelta(days=page - 1), period)
    assert _ist(broker.windows[0][4]).date() == end and min(_ist(w[3]).date() for w in broker.windows) == start
    older = body["symbols"][sym]["bars"]
    assert _json_rows(older) == hand([b for b in daily if start <= b.timestamp.date() <= end], period)
    newest_older = datetime.fromisoformat(older[-1]["timestamp"]).astimezone(IST).date()
    assert periods.next_period_start(newest_older, period) == periods.period_start(oldest, period)   # no gap, no overlap
    # A `before` in the middle of a period: that whole period is left for the newer page.
    mid = oldest + timedelta(days=2)
    body = _post(headers, symbols=[sym], timeframe=period, before=mid.isoformat())
    assert body["symbols"][sym]["bars"][-1] == older[-1]


def test_service_daily_range_reads_the_shared_page_copy_of_its_dates():
    async def go():
        shared_candles.reset()
        sym = _sym()
        broker = DailyBroker(default=daily_series(date(2024, 1, 1), date(2024, 12, 31)))
        now = datetime(2025, 1, 6, 12, 0, tzinfo=IST)
        got = await MarketDataService(broker).get_daily_range(sym, "NSE", date(2024, 1, 1), date(2025, 1, 10), now=now)
        again = await MarketDataService(DailyBroker()).get_daily_range(sym, "NSE", date(2024, 1, 1), date(2025, 1, 10), now=now)
        return broker, got, again
    broker, got, again = run(go())
    assert _ist(broker.windows[0][4]).date() == date(2025, 1, 5)                  # never today (or later)
    assert got.bars == again.bars and got.stopped is None and len(got.bars) > 200   # the second service read the shared copy


def test_the_brokers_cap_survives_the_production_wrappers():
    from app.brokers.angel_one import AngelOneBroker
    from app.brokers.contract_symbols import wrap_contract_symbols
    from app.brokers.dhan import DhanBroker
    from app.brokers.fyers import FyersBroker
    from app.brokers.models import BrokerCredentials
    from app.brokers.rate_budget import RateBudget, RateLimitedBroker, limits_for
    from app.brokers.upstox import UpstoxBroker
    from app.brokers.zerodha import ZerodhaBroker
    creds = BrokerCredentials(api_key="k", api_secret="s", access_token="t", client_id="c", user_id="u", password="p", totp_secret="JBSWY3DPEHPK3PXP")
    expected = {ZerodhaBroker: 2000, AngelOneBroker: 2000, FyersBroker: 365, DhanBroker: 365, UpstoxBroker: 365}
    for cls, days in expected.items():
        adapter = wrap_contract_symbols(cls(creds))
        assert adapter.daily_days_per_request == days
        assert RateLimitedBroker(adapter, RateBudget(limits_for(cls.name))).daily_days_per_request == days
        assert api_budget.BudgetedAdapter(adapter, 1, cls.name).daily_days_per_request == days
