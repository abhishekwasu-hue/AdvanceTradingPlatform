"""Charts load older history on scroll-back: `before` on POST /api/market-data/candles returns the
completed bars of the lookback window ending the day before it - no today's bars, own cache key."""
from datetime import date, datetime, timedelta

from app.market_data import candles_routes
from app.market_data.calendar import IST
from tests.test_auth_api import client
from tests.test_market_data_service import _install_cache
from tests.test_phase_aa_market_data_api import _Broker, _store_credentials
from tests.test_phase_k_commercial import _owner


class _Windowed(_Broker):
    """Remembers the window asked for; intraday must never be called for older history."""
    def __init__(self):
        super().__init__()
        self.windows = []

    async def get_historical_data(self, symbol, exchange, interval, from_date, to_date):
        self.windows.append((interval, from_date, to_date))
        return await super().get_historical_data(symbol, exchange, interval, from_date, to_date)

    async def get_intraday_candles(self, symbol, exchange, interval):
        raise AssertionError("older history must not fetch today's bars")


def test_before_fetches_the_window_ending_the_previous_day(monkeypatch):
    headers, me = _owner("chart-history@example.com")
    _store_credentials(me)
    broker = _Windowed()
    monkeypatch.setattr(candles_routes, "build_adapter", lambda record: broker)
    _install_cache(monkeypatch, None)

    res = client.post("/api/market-data/candles", headers=headers,
                      json={"symbols": ["NIFTY 50"], "timeframe": "15min", "lookback_days": 20, "before": "2026-09-01"})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["before"] == "2026-09-01" and body["symbols"]["NIFTY 50"]["count"] == 2      # 30 one-minute bars -> two 15m bars
    interval, start, end = broker.windows[0]
    assert interval == "1min" and start.astimezone(IST).date() == date(2026, 8, 12) and end.astimezone(IST).date() == date(2026, 8, 31)

    future = (datetime.now(IST) + timedelta(days=30)).date().isoformat()
    client.post("/api/market-data/candles", headers=headers, json={"symbols": ["TCS"], "timeframe": "day", "lookback_days": 730, "before": future})
    interval, start, end = broker.windows[-1]
    assert interval == "day" and end.astimezone(IST).date() < datetime.now(IST).date()      # never the forming day
    assert client.post("/api/market-data/candles", headers=headers, json={"symbols": ["TCS"], "before": "not-a-date"}).status_code == 422
