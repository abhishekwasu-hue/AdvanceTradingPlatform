"""Phase AN: `GET /api/market-data/ltp` for the live chart - tick first, then the quote with its
staleness flag, then the bare LTP; 409 without a usable session; 502 in the broker's words."""
import asyncio
from datetime import datetime, timedelta, timezone

from app.brokers.models import Quote
from app.market_data import candles_routes
from app.market_data.stream import Tick, tick_cache
from tests.test_phase_aa_market_data_api import _Broker, _store_credentials
from tests.test_phase_k_commercial import _owner

run = asyncio.run


class _QuoteBroker(_Broker):
    def __init__(self, quote=None, ltp=101.5, fail=False):
        super().__init__()
        self.quote, self.ltp_value, self.fail = quote, ltp, fail

    async def get_quote_for_symbol(self, symbol, exchange="NSE"):
        if self.fail:
            raise RuntimeError("socket closed by broker")
        return self.quote

    async def get_ltp_for_symbol(self, symbol, exchange="NSE"):
        return self.ltp_value


def test_ltp_requires_a_usable_session():
    headers, me = _owner("an-ltp-nobroker@example.com")
    assert client_get(headers, "NIFTY 50").status_code == 409
    assert candles_routes.router is not None


def client_get(headers, symbol, extra=""):
    from tests.test_auth_api import client
    return client.get(f"/api/market-data/ltp?symbol={symbol}{extra}", headers=headers)


def test_quote_then_ltp_fallback_with_staleness_flag(monkeypatch):
    headers, me = _owner("an-ltp@example.com")
    _store_credentials(me)
    tick_cache.clear()
    now = datetime.now(timezone.utc)

    fresh = Quote(symbol="RELIANCE", ltp=2874.2, bid=2874.0, ask=2874.4, volume=12000, timestamp=now - timedelta(seconds=2))
    monkeypatch.setattr(candles_routes, "build_adapter", lambda record: _QuoteBroker(quote=fresh))
    body = client_get(headers, "reliance").json()
    assert body["symbol"] == "RELIANCE" and body["ltp"] == 2874.2 and body["source"] == "quote" and body["stale"] is False
    assert body["bid"] == 2874.0 and body["age_seconds"] is not None and body["age_seconds"] < 10

    old = Quote(symbol="RELIANCE", ltp=2850.0, timestamp=now - timedelta(hours=18))
    monkeypatch.setattr(candles_routes, "build_adapter", lambda record: _QuoteBroker(quote=old))
    body = client_get(headers, "RELIANCE").json()
    assert body["ltp"] == 2850.0 and body["stale"] is True and body["stale_reason"]

    monkeypatch.setattr(candles_routes, "build_adapter", lambda record: _QuoteBroker(quote=None, ltp=99.0))
    body = client_get(headers, "TCS").json()
    assert body["ltp"] == 99.0 and body["source"] == "ltp" and body["timestamp"] is None and body["stale"] is False

    monkeypatch.setattr(candles_routes, "build_adapter", lambda record: _QuoteBroker(fail=True))
    res = client_get(headers, "TCS")
    assert res.status_code == 502 and "socket closed" in res.json()["detail"]
    assert client_get(headers, "").status_code in (400, 422)


def test_fresh_tick_wins_over_the_rest_call(monkeypatch):
    headers, me = _owner("an-ltp-tick@example.com")
    _store_credentials(me)
    tick_cache.clear()
    now = datetime.now(timezone.utc)
    run(tick_cache.put(Tick(broker="upstox", exchange="NSE", symbol="NIFTY 50", ltp=26012.5, exchange_ts=now - timedelta(seconds=1), received_at=now)))
    monkeypatch.setattr(candles_routes, "build_adapter", lambda record: _QuoteBroker(fail=True))   # must not be called
    body = client_get(headers, "NIFTY%2050").json()
    assert body["ltp"] == 26012.5 and body["source"] == "tick" and body["stale"] is False
    tick_cache.clear()
