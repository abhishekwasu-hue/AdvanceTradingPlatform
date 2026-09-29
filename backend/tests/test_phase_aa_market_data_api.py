"""Phase AA: broker candles for the research pages - sources listing, the 409 without a usable
session, resampling to the requested timeframe, per-symbol errors, lookback clamping, the
lookback-aware cache key and metering."""
import asyncio
import json
from datetime import datetime, timedelta, timezone

from app.brokers.base import BrokerInterface
from app.brokers.exceptions import BrokerAPIError
from app.brokers.models import BrokerOrderRequest, BrokerOrderResponse, BrokerProfile, MarginInfo
from app.core.enums import BrokerTokenStatus
from app.core.models import OHLCVBar
from app.db.models import BrokerCredentialRecord
from app.market_data import candles_routes
from app.market_data.service import MarketDataService, DEFAULT_LOOKBACK_DAYS
from app.secrets_store.encryption import encrypt_text
from tests.test_auth_api import _session_factory, client
from tests.test_market_data_service import _MemoryCache, _install_cache
from tests.test_phase_k_commercial import _owner

run = asyncio.run
IST_OPEN = datetime(2026, 9, 28, 9, 15, tzinfo=timezone(timedelta(hours=5, minutes=30)))


class _Broker(BrokerInterface):
    name = "fake"

    def __init__(self):
        self.calls = []

    async def authenticate(self): return BrokerProfile(broker="fake", user_id="x")
    async def get_profile(self): return BrokerProfile(broker="fake", user_id="x")
    async def get_instruments(self, exchange=None): return []
    async def get_ltp(self, symbols): return {}
    async def get_quote(self, symbols): return {}
    async def get_historical_data(self, symbol, exchange, interval, from_date, to_date):
        self.calls.append((symbol, interval, (to_date - from_date).days))
        if symbol == "BAD":
            raise BrokerAPIError("Instrument NSE:BAD not found")
        if symbol == "EMPTY":
            return []
        return [OHLCVBar(timestamp=IST_OPEN + timedelta(minutes=i), open=100 + i, high=101 + i, low=99 + i, close=100.5 + i, volume=10) for i in range(30)]
    async def get_intraday_candles(self, symbol, exchange, interval): return []
    async def get_option_chain(self, underlying, expiry=None): raise NotImplementedError
    async def place_order(self, order: BrokerOrderRequest): return BrokerOrderResponse(order_id="1", status="OPEN")
    async def modify_order(self, order_id, quantity=None, price=None, trigger_price=None, order_type=None): return BrokerOrderResponse(order_id=order_id, status="MODIFIED")
    async def cancel_order(self, order_id): return BrokerOrderResponse(order_id=order_id, status="CANCELLED")
    async def get_order_book(self): return []
    async def get_trade_book(self): return []
    async def get_positions(self): return []
    async def get_holdings(self): return []
    async def get_margins(self): return MarginInfo(available_cash=0)


def _store_credentials(me, broker="upstox", status=BrokerTokenStatus.VALID.value, label="primary"):
    async def go():
        async with _session_factory() as session:
            session.add(BrokerCredentialRecord(tenant_id=me["tenant_id"], user_id=me["id"], broker_name=broker, account_label=label,
                                               encrypted_payload=encrypt_text(json.dumps({"api_key": "k", "api_secret": "s", "access_token": "t"}), me["tenant_id"]),
                                               token_status=status, token_expires_at=None))
            await session.commit()
    run(go())


def test_sources_and_409_without_a_usable_session(monkeypatch):
    headers, me = _owner("aa-nobroker@example.com")
    res = client.get("/api/market-data/sources", headers=headers)
    assert res.status_code == 200 and res.json()["sources"] == [] and res.json()["usable"] is False
    res = client.post("/api/market-data/candles", headers=headers, json={"symbols": ["NIFTY"]})
    assert res.status_code == 409 and "Settings > Brokers" in res.json()["detail"]
    _store_credentials(me, status=BrokerTokenStatus.EXPIRED.value)
    src = client.get("/api/market-data/sources", headers=headers).json()
    assert src["sources"][0]["broker"] == "upstox" and src["sources"][0]["usable"] is False and src["usable"] is False
    assert client.post("/api/market-data/candles", headers=headers, json={"symbols": ["NIFTY"]}).status_code == 409
    assert client.post("/api/market-data/candles", headers=headers, json={"symbols": ["NIFTY"], "broker": "upstox"}).status_code == 409
    assert client.post("/api/market-data/candles", headers=headers, json={"symbols": ["NIFTY"], "broker": "zerodha"}).status_code == 404
    assert client.post("/api/market-data/candles", json={"symbols": ["NIFTY"]}).status_code == 401


def test_candles_resample_report_errors_clamp_and_meter(monkeypatch):
    headers, me = _owner("aa-broker@example.com")
    _store_credentials(me)
    broker = _Broker()
    monkeypatch.setattr(candles_routes, "build_adapter", lambda record: broker)
    _install_cache(monkeypatch, None)
    assert client.get("/api/market-data/sources", headers=headers).json()["usable"] is True

    res = client.post("/api/market-data/candles", headers=headers, json={"symbols": ["nifty", "BAD", "EMPTY", "NIFTY"], "timeframe": "5min", "lookback_days": 45})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["source"] == {"broker": "upstox", "account_label": "primary"} and body["base_interval"] == "1min" and body["lookback_days"] == 30
    assert any("clamped" in w for w in body["warnings"]) and any("EMPTY" in w for w in body["warnings"])
    assert set(body["symbols"]) == {"NIFTY", "BAD", "EMPTY"}                      # duplicate collapsed, upper-cased
    nifty = body["symbols"]["NIFTY"]
    assert nifty["count"] == 6 and nifty["error"] is None                            # 30 one-minute bars -> six 5-minute bars
    first = nifty["bars"][0]
    assert first["open"] == 100 and first["close"] == 104.5 and first["high"] == 105 and first["low"] == 99 and first["volume"] == 50
    assert "09:15" in first["timestamp"]
    assert body["symbols"]["BAD"]["error"].startswith("BrokerAPIError") and body["symbols"]["BAD"]["count"] == 0
    assert broker.calls[0][1] == "1min" and broker.calls[0][2] <= 30

    day = client.post("/api/market-data/candles", headers=headers, json={"symbols": ["NIFTY"], "timeframe": "day", "lookback_days": 400}).json()
    assert day["base_interval"] == "day" and day["lookback_days"] == 400 and day["symbols"]["NIFTY"]["count"] == 30 and broker.calls[-1][1] == "day"
    assert client.post("/api/market-data/candles", headers=headers, json={"symbols": ["NIFTY"], "timeframe": "2min"}).status_code == 400

    usage = client.get("/api/billing/usage", headers=headers).json()
    assert usage["metrics"]["market_data_candles"] == 3                              # NIFTY + EMPTY on the first call, NIFTY on the second


def test_cache_key_distinguishes_lookback_windows(monkeypatch):
    cache = _MemoryCache()
    _install_cache(monkeypatch, cache)
    broker = _Broker()
    run(MarketDataService(broker).get_candles("NIFTY", "NSE", "1min"))
    run(MarketDataService(broker, lookback_days=30).get_candles("NIFTY", "NSE", "1min"))
    run(MarketDataService(broker, lookback_days=DEFAULT_LOOKBACK_DAYS).get_candles("NIFTY", "NSE", "1min"))
    keys = sorted(cache.store)
    assert len(keys) == 2 and keys[0].endswith(":1min") and keys[1].endswith(":1min:30d")
    assert len(broker.calls) == 2                                                    # third call was served from the default-window key
