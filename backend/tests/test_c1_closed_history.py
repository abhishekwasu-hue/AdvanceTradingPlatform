"""C1 (UI-H bug lane): chart history ends at the last session close from the market calendar - a closed Saturday or
holiday still loads the last session on every timeframe - an empty broker answer carries a plain reason, and the
owner's read-only probe reports counts only."""
import asyncio
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from app.brokers.exceptions import BrokerAPIError
from app.core.models import OHLCVBar
from app.market_data import candles_routes, probe
from app.market_data.calendar import last_session_close, previous_session_close
from app.market_data.service import MarketDataService
from tests.test_auth_api import client
from tests.test_market_data_service import _install_cache
from tests.test_phase_aa_market_data_api import _Broker, _store_credentials
from tests.test_phase_k_commercial import _owner

IST = ZoneInfo("Asia/Kolkata")
run = asyncio.run
FRI = date(2026, 10, 23)
SAT_NOON = datetime(2026, 10, 24, 12, 0, tzinfo=IST)
FRI_CLOSE = datetime(2026, 10, 23, 15, 30, tzinfo=IST)


def _friday_bars(n: int = 375) -> list:
    start = datetime(2026, 10, 23, 9, 15, tzinfo=IST)
    return [OHLCVBar(timestamp=start + timedelta(minutes=i), open=100 + i, high=101 + i, low=99 + i, close=100.5 + i, volume=10)
            for i in range(n)]


class _WindowBroker(_Broker):
    """Answers the history call with Friday's session only when the window reaches Friday's close."""

    def __init__(self):
        super().__init__()
        self.windows = []

    async def get_historical_data(self, symbol, exchange, interval, from_date, to_date):
        self.windows.append((from_date, to_date))
        if symbol == "EMPTY":
            return []
        bars = _friday_bars() if interval == "1min" else [OHLCVBar(timestamp=datetime(2026, 10, 23, tzinfo=IST), open=1, high=2,
                                                                   low=0.5, close=1.5, volume=1)]
        return [b for b in bars if from_date <= b.timestamp <= to_date]


def test_last_and_previous_session_close():
    assert last_session_close(SAT_NOON) == FRI_CLOSE
    assert previous_session_close(SAT_NOON) == FRI_CLOSE
    sunday = datetime(2026, 10, 25, 22, 0, tzinfo=IST)
    assert last_session_close(sunday) == FRI_CLOSE
    # A trading day before the close: the last close is yesterday's; after the close it is today's.
    mon_10 = datetime(2026, 10, 26, 10, 0, tzinfo=IST)
    assert last_session_close(mon_10) == FRI_CLOSE and previous_session_close(mon_10) == FRI_CLOSE
    mon_18 = datetime(2026, 10, 26, 18, 0, tzinfo=IST)
    assert last_session_close(mon_18) == datetime(2026, 10, 26, 15, 30, tzinfo=IST)
    assert previous_session_close(mon_18) == FRI_CLOSE
    # A holiday (Friday) before a weekend: Thursday's close.
    assert last_session_close(SAT_NOON, {FRI}) == datetime(2026, 10, 22, 15, 30, tzinfo=IST)
    # MCX closes at 23:30; crypto never closes.
    assert last_session_close(SAT_NOON, exchange="MCX") == datetime(2026, 10, 23, 23, 30, tzinfo=IST)
    assert last_session_close(SAT_NOON, exchange="CRYPTO") is None


@pytest.mark.parametrize("now,holidays,want", [
    (SAT_NOON, set(), FRI_CLOSE),                                                          # Saturday
    (datetime(2026, 10, 25, 9, 0, tzinfo=IST), set(), FRI_CLOSE),                          # Sunday
    (datetime(2026, 10, 26, 13, 0, tzinfo=IST), {date(2026, 10, 26)}, FRI_CLOSE),          # a Monday holiday
    (datetime(2026, 10, 24, 12, 0, tzinfo=IST), {FRI}, datetime(2026, 10, 22, 15, 30, tzinfo=IST)),
])
def test_history_window_ends_at_the_last_close(now, holidays, want):
    service = MarketDataService(_WindowBroker(), lookback_days=1)
    service.holidays = holidays
    start, end = service.history_window("NFO", "1min", now)
    assert end == want
    # A one-day lookback across a weekend / holiday still starts at that session's day, never after it.
    assert start <= datetime.combine(want.date(), datetime.min.time(), tzinfo=IST)


def test_closed_saturday_loads_friday_on_every_timeframe():
    broker = _WindowBroker()
    for exchange, symbol in (("NSE", "NIFTY 50"), ("NFO", "NIFTY26OCTFUT"), ("NFO", "NIFTY2610325000CE")):
        for tf in ("1min", "5min", "15min", "30min", "60min"):
            bars = run(MarketDataService(broker, lookback_days=1)._fetch(symbol, exchange, "1min", SAT_NOON))
            shaped = candles_routes._shape(list(bars), tf)
            assert shaped, (symbol, tf)
            assert shaped[-1].timestamp.astimezone(IST).date() == FRI          # ends with Friday's session
            assert broker.windows[-1][1] == FRI_CLOSE
        daily = run(MarketDataService(broker)._fetch(symbol, exchange, "day", SAT_NOON))
        assert daily and daily[-1].timestamp.astimezone(IST).date() == FRI


def test_empty_answer_says_why_in_the_api(monkeypatch):
    headers, me = _owner("c1-empty@example.com")
    _store_credentials(me)
    broker = _WindowBroker()
    monkeypatch.setattr(candles_routes, "build_adapter", lambda record: broker)
    _install_cache(monkeypatch, None)
    res = client.post("/api/market-data/candles", headers=headers,
                      json={"symbols": ["EMPTY", "NIFTY 50"], "exchange": "NFO", "timeframe": "30min", "lookback_days": 5})
    assert res.status_code == 200, res.text
    body = res.json()["symbols"]
    empty = body["EMPTY"]
    assert empty["bars"] == [] and empty["error"] is None
    assert empty["empty_reason"].startswith("No candles yet for this contract.")
    assert "last session close" in empty["empty_reason"]
    assert "empty_reason" not in body["NIFTY 50"]


def test_empty_reason_words():
    sat = SAT_NOON.astimezone(timezone.utc)
    text = candles_routes.empty_reason("NIFTY2610325000CE", "NFO", set(), sat)
    assert text == ("No candles yet for this contract. It may be a new contract, or your broker sent no data for this period "
                    "up to the last session close (23 Oct, 15:30 IST).")
    assert candles_routes.empty_reason("RELIANCE", "NSE", set(), sat).startswith("No candles yet for this symbol. Your broker sent")
    assert candles_routes.empty_reason("RELIANCE", "NSE", set(), sat, date(2026, 10, 1)) == "No older candles for this symbol from your broker."


class _ProbeBroker(_WindowBroker):
    async def get_intraday_candles(self, symbol, exchange, interval):
        raise BrokerAPIError("401 token abcdefghijklmnopqrstuvwxyz0123456789 invalid")


def test_probe_reports_counts_only():
    service = MarketDataService(_ProbeBroker(), lookback_days=5)
    out = run(probe.probe_candles(service, "NIFTY2610325000CE", "NFO", "30min", SAT_NOON))
    assert set(out) == {"symbol", "exchange", "timeframe", "base_interval", "window", "history", "intraday", "chart_bars"}
    assert out["window"]["to"] == "2026-10-23T15:30+05:30" and out["window"]["last_session_close"] == "2026-10-23T15:30+05:30"
    h = out["history"]
    assert h["bars"] == 375 and h["first"] == "2026-10-23T09:15+05:30" and h["last"] == "2026-10-23T15:29+05:30"
    assert set(h) == {"bars", "first", "last", "first_close", "last_close", "reason"} and h["reason"] is None
    i = out["intraday"]
    assert i["bars"] == 0 and "abcdefghijklmnop" not in i["reason"] and i["reason"].startswith("BrokerAPIError: 401 token")
    assert out["chart_bars"] == 13
    lines = probe.format_candles({**out, "broker": "upstox"})
    assert len(lines) <= 8 and all(len(line) < 160 for line in lines)
    assert lines[0] == "probe-candles NIFTY2610325000CE (NFO) 30min via upstox"
    assert any(line.startswith("  intraday: 0 bars - BrokerAPIError") for line in lines)
    empty = run(probe.probe_candles(service, "EMPTY", "NFO", "day", SAT_NOON))
    assert empty["history"]["reason"] == "the broker answered with no candles" and empty["chart_bars"] == 0


def test_probe_inputs():
    assert probe.normalise_timeframe("1H") == "60min" and probe.normalise_timeframe("D") == "day" and probe.normalise_timeframe("30m") == "30min"
    with pytest.raises(ValueError):
        probe.normalise_timeframe("2min")
    assert probe.guess_exchange("NIFTY2610325000CE") == "NFO" and probe.guess_exchange("NIFTY 50") == "NSE"


def test_probe_route_is_admin_only():
    headers, _ = _owner("c1-probe-owner@example.com")
    assert client.get("/api/admin/market-data/probe-candles?symbol=NIFTY", headers=headers).status_code == 403
    assert client.get("/api/admin/market-data/probe-candles?symbol=NIFTY").status_code == 401
