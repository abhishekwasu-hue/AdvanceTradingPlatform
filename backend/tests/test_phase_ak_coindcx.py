"""Phase AK: the CoinDCX spot adapter (mocked transport built from the public docs)."""
import asyncio
import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.brokers import token_lifecycle
from app.brokers.coindcx import CoinDCXBroker
from app.brokers.contract_symbols import wrap_contract_symbols
from app.brokers.exceptions import BrokerAPIError, BrokerAuthenticationError
from app.brokers.models import BrokerCredentials, BrokerOrderRequest
from app.brokers.registry import available_brokers, get_broker_adapter
from app.brokers.smoke import run_smoke
from app.core.enums import OrderSide

SECRET = "s3cret"
MARKETS = [
    {"coindcx_name": "BTCINR", "base_currency_short_name": "INR", "target_currency_short_name": "BTC", "pair": "I-BTC_INR", "status": "active",
     "min_quantity": 0.0001, "max_quantity": 10, "step": 0.0001, "base_currency_precision": 2, "target_currency_precision": 4, "order_types": ["limit_order", "market_order", "stop_limit"]},
    {"coindcx_name": "ETHINR", "base_currency_short_name": "INR", "target_currency_short_name": "ETH", "pair": "I-ETH_INR", "status": "active",
     "min_quantity": 0.001, "step": 0.001, "base_currency_precision": 2},
    {"coindcx_name": "BTCUSDT", "base_currency_short_name": "USDT", "target_currency_short_name": "BTC", "pair": "B-BTC_USDT", "status": "active", "step": 0.00001},
    {"coindcx_name": "OLDINR", "base_currency_short_name": "INR", "target_currency_short_name": "OLD", "pair": "I-OLD_INR", "status": "inactive", "step": 1},
]
NOW_MS = 1_790_000_000_000


def _make(handler, api_key="key1", api_secret=SECRET):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://api.coindcx.com")
    return CoinDCXBroker(BrokerCredentials(api_key=api_key, api_secret=api_secret), client)


def _handler(calls):
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        path = request.url.path
        if request.method == "GET":
            if path == "/exchange/v1/markets_details":
                return httpx.Response(200, json=MARKETS)
            if path == "/exchange/ticker":
                return httpx.Response(200, json=[
                    {"market": "BTCINR", "last_price": "5000000.5", "bid": "4999900", "ask": "5000100", "high": "5100000", "low": "4900000", "volume": "12.5", "timestamp": NOW_MS},
                    {"market": "ETHINR", "last_price": "250000", "bid": "249900", "ask": "250100", "volume": "100", "timestamp": NOW_MS},
                ])
            if request.url.host == "public.coindcx.com" and path == "/market_data/candles":
                assert request.url.params["pair"] == "I-BTC_INR" and request.url.params["interval"] == "5m"
                return httpx.Response(200, json=[
                    {"time": NOW_MS, "open": "5000000", "high": "5001000", "low": "4999000", "close": "5000500", "volume": "1.2"},
                    {"time": NOW_MS - 300_000, "open": "4999000", "high": "5000200", "low": "4998000", "close": "5000000", "volume": "0.8"},
                ])
            raise AssertionError(f"unexpected GET {request.url}")
        # private: every call must carry a valid HMAC over the exact body and a timestamp
        body = request.content.decode()
        expected = hmac.new(SECRET.encode(), body.encode(), hashlib.sha256).hexdigest()
        if request.headers.get("X-AUTH-APIKEY") != "key1":
            return httpx.Response(401, json={"code": 401, "message": "Invalid API key", "status": "error"})
        assert request.headers["X-AUTH-SIGNATURE"] == expected, "bad signature"
        payload = json.loads(body)
        assert isinstance(payload.get("timestamp"), int)
        if path == "/exchange/v1/users/info":
            return httpx.Response(200, json={"coindcx_id": "u-42", "first_name": "Abhi", "last_name": "W", "email": "a@example.com"})
        if path == "/exchange/v1/users/balances":
            return httpx.Response(200, json=[{"currency": "INR", "balance": "150000.5", "locked_balance": "5000"},
                                             {"currency": "BTC", "balance": "0.02", "locked_balance": "0.0"},
                                             {"currency": "DOGE", "balance": "0", "locked_balance": "0"}])
        if path == "/exchange/v1/orders/create":
            handler.last_create = payload
            return httpx.Response(200, json={"orders": [{"id": "ord-1", "client_order_id": payload.get("client_order_id"), "market": payload["market"], "order_type": payload["order_type"],
                                                         "side": payload["side"], "status": "open", "total_quantity": payload["total_quantity"], "remaining_quantity": payload["total_quantity"],
                                                         "price_per_unit": payload.get("price_per_unit", 0), "created_at": NOW_MS}]})
        if path == "/exchange/v1/orders/edit":
            return httpx.Response(200, json={"id": payload["id"], "status": "open", "price_per_unit": payload["price_per_unit"]})
        if path == "/exchange/v1/orders/cancel":
            return httpx.Response(200, json={"message": "success"})
        if path == "/exchange/v1/orders/active_orders":
            return httpx.Response(200, json={"orders": [{"id": "ord-1", "market": "BTCINR", "side": "buy", "order_type": "limit_order", "status": "partially_filled",
                                                         "total_quantity": 0.01, "remaining_quantity": 0.004, "price_per_unit": 4990000, "avg_price": 4989000, "created_at": NOW_MS}]})
        if path == "/exchange/v1/orders/trade_history":
            return httpx.Response(200, json=[{"id": 77, "order_id": "ord-1", "side": "buy", "quantity": "0.006", "price": "4989000", "symbol": "BTCINR", "timestamp": NOW_MS}])
        raise AssertionError(f"unexpected POST {path}")
    handler.last_create = None
    return handler


def test_registry_lists_coindcx_as_a_real_adapter_with_no_daily_expiry():
    assert "coindcx" in available_brokers()
    assert isinstance(get_broker_adapter("coindcx", BrokerCredentials(api_key="k", api_secret="s")), CoinDCXBroker)
    assert token_lifecycle.default_token_expiry("coindcx") is None
    assert token_lifecycle.default_token_expiry("zerodha") is not None


def test_session_markets_quotes_and_candles():
    calls = []
    broker = _make(_handler(calls))
    profile = asyncio.run(broker.authenticate())
    assert profile.broker == "coindcx" and profile.user_id == "u-42" and profile.name == "Abhi W" and broker.access_token == "key1"

    instruments = asyncio.run(broker.get_instruments("CRYPTO"))
    assert [i.tradingsymbol for i in instruments] == ["BTCINR", "ETHINR"]            # INR spot only, inactive dropped, USDT pair dropped
    assert instruments[0].lot_size == 0.0001 and instruments[0].tick_size == 0.01 and instruments[0].instrument_token == "I-BTC_INR" and instruments[0].name == "BTC"
    assert asyncio.run(broker.get_instruments("NSE")) == []

    quotes = asyncio.run(broker.get_quote(["BTCINR", "CRYPTO:ETHINR", "XRPINR"]))
    assert set(quotes) == {"BTCINR", "CRYPTO:ETHINR"} and quotes["BTCINR"].ltp == 5000000.5 and quotes["BTCINR"].bid == 4999900.0
    assert quotes["BTCINR"].timestamp == datetime.fromtimestamp(NOW_MS / 1000, tz=timezone.utc)
    assert asyncio.run(broker.get_ltp_for_symbol("BTCINR", "CRYPTO")) == 5000000.5
    assert asyncio.run(broker.get_ltp(["BTCINR_CRYPTO"])) == {"BTCINR_CRYPTO": 5000000.5}      # platform contract-spec spelling accepted
    with pytest.raises(BrokerAPIError, match="No ticker"):
        asyncio.run(broker.get_ltp_for_symbol("XRPINR", "CRYPTO"))

    end = datetime.fromtimestamp(NOW_MS / 1000, tz=timezone.utc)
    bars = asyncio.run(broker.get_historical_data("BTCINR", "CRYPTO", "5minute", end - timedelta(hours=1), end))
    assert [b.close for b in bars] == [5000000.0, 5000500.0] and bars[0].timestamp < bars[1].timestamp   # oldest first
    with pytest.raises(NotImplementedError):
        asyncio.run(broker.get_option_chain("BTCINR"))


def test_orders_wallet_and_errors():
    calls = []
    handler = _handler(calls)
    broker = _make(handler)

    # Market buy: quantity floored to the step, product ignored, tag becomes client_order_id.
    res = asyncio.run(broker.place_order(BrokerOrderRequest(symbol="BTCINR", exchange="CRYPTO", transaction_type=OrderSide.BUY, quantity=0.00123456, order_type="MARKET", product="MIS", tag="ATP-DEP7")))
    assert res.order_id == "ord-1" and res.status == "OPEN"
    assert handler.last_create["order_type"] == "market_order" and handler.last_create["side"] == "buy" and handler.last_create["client_order_id"] == "ATP-DEP7"
    assert handler.last_create["total_quantity"] == pytest.approx(0.0012) and "price_per_unit" not in handler.last_create

    # Protective stop (SL-M) becomes a stop-limit with the limit 0.5% past the trigger on the sell side.
    asyncio.run(broker.place_stop_loss_order("BTCINR", "CRYPTO", OrderSide.SELL, 0.0012, 4900000.0))
    assert handler.last_create["order_type"] == "stop_limit" and handler.last_create["stop_price"] == 4900000.0
    assert handler.last_create["price_per_unit"] == pytest.approx(4900000.0 * 0.995, rel=1e-6) and handler.last_create["side"] == "sell"

    # LIMIT needs a price; SL uses the given limit; below-step quantity refused; unknown market refused.
    with pytest.raises(BrokerAPIError, match="needs a price"):
        asyncio.run(broker.place_order(BrokerOrderRequest(symbol="BTCINR", exchange="CRYPTO", transaction_type=OrderSide.BUY, quantity=0.001, order_type="LIMIT", product="CNC")))
    asyncio.run(broker.place_order(BrokerOrderRequest(symbol="ETHINR", exchange="CRYPTO", transaction_type=OrderSide.BUY, quantity=0.5, order_type="SL", product="CNC", price=251000.0, trigger_price=250500.0)))
    assert handler.last_create["price_per_unit"] == 251000.0 and handler.last_create["stop_price"] == 250500.0 and handler.last_create["market"] == "ETHINR"
    with pytest.raises(BrokerAPIError, match="below the CoinDCX step"):
        asyncio.run(broker.place_order(BrokerOrderRequest(symbol="BTCINR", exchange="CRYPTO", transaction_type=OrderSide.BUY, quantity=0.00001, order_type="MARKET", product="CNC")))
    with pytest.raises(BrokerAPIError, match="not listed"):
        asyncio.run(broker.place_order(BrokerOrderRequest(symbol="XRPINR", exchange="CRYPTO", transaction_type=OrderSide.BUY, quantity=1, order_type="MARKET", product="CNC")))

    assert asyncio.run(broker.modify_order("ord-1", price=4995000.0)).status == "OPEN"
    with pytest.raises(BrokerAPIError, match="only edit"):
        asyncio.run(broker.modify_order("ord-1", quantity=0.02))
    assert asyncio.run(broker.cancel_order("ord-1")).status == "CANCELLED"

    book = asyncio.run(broker.get_order_book())
    assert book[0].symbol == "BTCINR" and book[0].status == "PARTIAL_FILL" and book[0].filled_quantity == pytest.approx(0.006) and book[0].order_type == "LIMIT"
    trades = asyncio.run(broker.get_trade_book())
    assert trades[0].order_id == "ord-1" and trades[0].quantity == 0.006 and trades[0].transaction_type == OrderSide.BUY

    positions = asyncio.run(broker.get_positions())
    assert [(p.symbol, p.quantity, p.ltp) for p in positions] == [("BTCINR", 0.02, 5000000.5)]     # INR and zero balances are not positions
    margins = asyncio.run(broker.get_margins())
    assert margins.available_cash == 150000.5 and margins.used_margin == 5000.0 and margins.total_margin == 155000.5
    assert asyncio.run(broker.get_holdings())[0].symbol == "BTCINR"

    # Exit is a market order on the opposite side.
    asyncio.run(broker.exit_position("BTCINR", "CRYPTO", 0.02, OrderSide.BUY))
    assert handler.last_create["side"] == "sell" and handler.last_create["order_type"] == "market_order"

    # A wrong key is an authentication error, a missing secret is refused before any call.
    bad = _make(_handler([]), api_key="wrong")
    with pytest.raises(BrokerAuthenticationError):
        asyncio.run(bad.get_profile())
    with pytest.raises(BrokerAuthenticationError, match="API key and secret"):
        asyncio.run(_make(_handler([]), api_secret=None).get_profile())


def test_smoke_test_uses_the_crypto_venue_profile():
    broker = wrap_contract_symbols(_make(_handler([])))
    report = asyncio.run(run_smoke(broker, account_label="primary"))
    by_name = {s.name: s for s in report.steps}
    assert report.ok
    assert by_name["instruments"].detail == "2 CRYPTO instruments" and by_name["quote"].detail.startswith("BTCINR 5,000,000.50")
    assert by_name["derivatives"].status == "skip" and by_name["contract_quote"].status == "skip"
    assert by_name["positions"].detail.startswith("1 open position(s): BTCINR")
