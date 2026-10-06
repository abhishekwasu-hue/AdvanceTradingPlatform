"""Phase AI: derived-contract symbols translated into each broker's own spelling at the adapter boundary."""
import asyncio
from datetime import date
from typing import Dict, List

import pytest

from app.brokers import token_lifecycle
from app.brokers.base import BrokerInterface
from app.brokers.contract_symbols import ContractKey, ContractSymbolBroker, contract_keys, parse_contract, wrap_contract_symbols
from app.brokers.exceptions import BrokerAPIError
from app.brokers.models import (
    BrokerCredentials, BrokerOrderRequest, BrokerOrderResponse, BrokerOrderStatus, BrokerPosition, BrokerProfile, Instrument, MarginInfo, Quote,
)
from app.core.enums import InstrumentKind, OptionPosition, OrderSide, SignalDirection
from app.execution.contract_execution import contract_ltp
from app.instruments.contracts import ResolvedContract

EXPIRY = date(2026, 10, 30)
NIFTY_CE = ContractKey("NIFTY", EXPIRY, "CE", 26000.0)


def test_parse_and_canonical_round_trip():
    assert parse_contract("NIFTY 26000 CE 30 OCT 26") == NIFTY_CE
    assert parse_contract("nifty 26000 pe 30 oct 26") == ContractKey("NIFTY", EXPIRY, "PE", 26000.0)
    assert parse_contract("NIFTY FUT 30 OCT 26") == ContractKey("NIFTY", EXPIRY, "FUT", None)
    assert parse_contract("RELIANCE 3000 CE 30 OCT 26").underlying == "RELIANCE"
    assert parse_contract("BANKNIFTY 57500 CE 7 OCT 26").expiry == date(2026, 10, 7)
    assert parse_contract("SENSEX 82000.5 CE 30 OCT 26").canonical == "SENSEX 82000.5 CE 30 OCT 26"
    for plain in ("RELIANCE", "NIFTY 50", "NIFTY BANK", "", "NSE_FO|56789", "NIFTY26OCT26000CE"):
        assert parse_contract(plain) is None, plain
    assert NIFTY_CE.canonical == "NIFTY 26000 CE 30 OCT 26"
    assert parse_contract(NIFTY_CE.canonical) == NIFTY_CE
    assert ContractKey("NIFTY", date(2026, 10, 7), "FUT", None).canonical == "NIFTY FUT 07 OCT 26"


@pytest.mark.parametrize("instrument, expected", [
    # Zerodha / Kite dump: name = underlying, ISO expiry, CE/PE/FUT types.
    (Instrument(instrument_token="1", exchange="NFO", tradingsymbol="NIFTY26OCT26000CE", name="NIFTY", instrument_type="CE", expiry="2026-10-30", strike=26000.0), NIFTY_CE),
    (Instrument(instrument_token="2", exchange="NFO", tradingsymbol="NIFTY26OCTFUT", name="NIFTY", instrument_type="FUT", expiry="2026-10-30", strike=None), ContractKey("NIFTY", EXPIRY, "FUT", None)),
    # Angel One scrip master: OPTIDX/FUTIDX types, right only in the symbol.
    (Instrument(instrument_token="3", exchange="NFO", tradingsymbol="NIFTY30OCT2626000CE", name="NIFTY", instrument_type="OPTIDX", expiry="2026-10-30", strike=26000.0), NIFTY_CE),
    (Instrument(instrument_token="4", exchange="NFO", tradingsymbol="NIFTY30OCT26FUT", name="NIFTY", instrument_type="FUTIDX", expiry="2026-10-30", strike=None), ContractKey("NIFTY", EXPIRY, "FUT", None)),
    # Fyers: platform symbol without the NSE: prefix, underlying column.
    (Instrument(instrument_token="5", exchange="NFO", tradingsymbol="NIFTY26OCT26000CE", name="NIFTY", instrument_type="CE", expiry="2026-10-30", strike=26000.0), NIFTY_CE),
    # Dhan: dashed symbol, underlying derived.
    (Instrument(instrument_token="6", exchange="NFO", tradingsymbol="NIFTY-OCT2026-26000-CE", name="NIFTY", instrument_type="CE", expiry="2026-10-30", strike=26000.0), NIFTY_CE),
    # Shoonya: no name, DD-MON-YYYY expiry, C/P before the strike, F for futures.
    (Instrument(instrument_token="7", exchange="NFO", tradingsymbol="NIFTY30OCT26C26000", name=None, instrument_type="OPTIDX", expiry="30-OCT-2026", strike=26000.0), NIFTY_CE),
    (Instrument(instrument_token="8", exchange="NFO", tradingsymbol="NIFTY30OCT26P26000", name=None, instrument_type="OPTIDX", expiry="30-OCT-2026", strike=None), ContractKey("NIFTY", EXPIRY, "PE", 26000.0)),
    (Instrument(instrument_token="9", exchange="NFO", tradingsymbol="NIFTY30OCT26F", name=None, instrument_type="FUTIDX", expiry="30-OCT-2026", strike=None), ContractKey("NIFTY", EXPIRY, "FUT", None)),
    # An index spelt the long way still keys on the master name.
    (Instrument(instrument_token="10", exchange="NFO", tradingsymbol="BANKNIFTY26OCT57000PE", name="NIFTY BANK", instrument_type="PE", expiry="2026-10-30", strike=57000.0), ContractKey("BANKNIFTY", EXPIRY, "PE", 57000.0)),
])
def test_contract_keys_across_broker_spellings(instrument, expected):
    assert expected in contract_keys(instrument)


def test_equities_and_indices_have_no_contract_key():
    assert contract_keys(Instrument(instrument_token="1", exchange="NSE", tradingsymbol="RELIANCE", name="RELIANCE", instrument_type="EQ")) == []
    assert contract_keys(Instrument(instrument_token="2", exchange="NSE", tradingsymbol="NIFTY 50", name="NIFTY 50", instrument_type="INDEX")) == []


class _KiteLike(BrokerInterface):
    """A Kite-spelling broker that records what reached it."""
    name = "zerodha"
    max_tag_length = 20

    def __init__(self) -> None:
        self._access_token = "tok"
        self.orders: List[BrokerOrderRequest] = []
        self.ltp_calls: List[List[str]] = []
        self.instrument_calls = 0
        self.private_thing = "reachable"
        self._nfo = [
            Instrument(instrument_token="1", exchange="NFO", tradingsymbol="NIFTY26OCT26000CE", name="NIFTY", instrument_type="CE", expiry="2026-10-30", strike=26000.0, lot_size=75),
            Instrument(instrument_token="2", exchange="NFO", tradingsymbol="NIFTY26OCT26000PE", name="NIFTY", instrument_type="PE", expiry="2026-10-30", strike=26000.0, lot_size=75),
            Instrument(instrument_token="3", exchange="NFO", tradingsymbol="NIFTY26OCTFUT", name="NIFTY", instrument_type="FUT", expiry="2026-10-30", lot_size=75),
        ]

    async def authenticate(self): return BrokerProfile(user_id="u", user_name="n", broker="zerodha")
    async def get_profile(self): return BrokerProfile(user_id="u", user_name="n", broker="zerodha")
    async def get_instruments(self, exchange=None):
        self.instrument_calls += 1
        if exchange == "NFO":
            return self._nfo
        return [Instrument(instrument_token="9", exchange="NSE", tradingsymbol="RELIANCE", name="RELIANCE", instrument_type="EQ")]
    async def get_ltp(self, symbols):
        self.ltp_calls.append(list(symbols))
        return {s: (250.0 if "26000CE" in s else 2900.0) for s in symbols}
    async def get_quote(self, symbols):
        return {s: Quote(symbol=s, ltp=250.0) for s in symbols}
    async def get_historical_data(self, symbol, exchange, interval, from_date, to_date): return []
    async def get_option_chain(self, underlying, expiry=None): raise NotImplementedError
    async def place_order(self, order):
        self.orders.append(order)
        return BrokerOrderResponse(order_id=f"o{len(self.orders)}", status="COMPLETE")
    async def modify_order(self, order_id, quantity=None, price=None, trigger_price=None, order_type=None):
        return BrokerOrderResponse(order_id=order_id, status="MODIFIED")
    async def cancel_order(self, order_id): return BrokerOrderResponse(order_id=order_id, status="CANCELLED")
    async def get_order_book(self):
        return [BrokerOrderStatus(order_id="o1", symbol="NIFTY26OCT26000CE", transaction_type=OrderSide.BUY, quantity=75, order_type="MARKET", status="COMPLETE"),
                BrokerOrderStatus(order_id="o2", symbol="RELIANCE", transaction_type=OrderSide.BUY, quantity=1, order_type="MARKET", status="COMPLETE")]
    async def get_trade_book(self): return []
    async def get_positions(self):
        return [BrokerPosition(symbol="NIFTY26OCT26000CE", exchange="NFO", quantity=75, average_price=250.0),
                BrokerPosition(symbol="RELIANCE", exchange="NSE", quantity=1, average_price=2900.0)]
    async def get_holdings(self): return []
    async def get_margins(self): return MarginInfo(available_cash=100000.0, available_margin=100000.0, used_margin=0.0)
    async def get_order_margin(self, order): return 15000.0 if order.symbol == "NIFTY26OCT26000PE" else None


def test_wrapper_translates_outbound_and_restores_inbound():
    inner = _KiteLike()
    broker = wrap_contract_symbols(inner)
    assert isinstance(broker, ContractSymbolBroker) and broker.name == "zerodha" and broker.access_token == "tok"
    assert broker.private_thing == "reachable" and broker.max_tag_length == 20        # adapter internals stay reachable (streams)

    async def scenario():
        # Orders, stops, exits and margin probes go out in Kite's spelling; the request object the caller built is untouched.
        request = BrokerOrderRequest(symbol="NIFTY 26000 CE 30 OCT 26", exchange="NFO", transaction_type=OrderSide.BUY, quantity=75, order_type="MARKET", product="MIS")
        await broker.place_order(request)
        await broker.place_stop_loss_order("NIFTY 26000 CE 30 OCT 26", "NFO", OrderSide.SELL, 75, 200.0)
        await broker.exit_position("NIFTY FUT 30 OCT 26", "NFO", 75, OrderSide.BUY)
        assert [o.symbol for o in inner.orders] == ["NIFTY26OCT26000CE", "NIFTY26OCT26000CE", "NIFTY26OCTFUT"]
        assert inner.orders[1].order_type == "SL-M" and inner.orders[2].transaction_type == OrderSide.SELL
        assert request.symbol == "NIFTY 26000 CE 30 OCT 26"
        probe = BrokerOrderRequest(symbol="NIFTY 26000 PE 30 OCT 26", exchange="NFO", transaction_type=OrderSide.SELL, quantity=75, order_type="MARKET", product="MIS")
        assert await broker.get_order_margin(probe) == 15000.0

        # Quotes: keys are translated on the way out and the caller's keys come back.
        prices = await broker.get_ltp(["NFO:NIFTY 26000 CE 30 OCT 26", "NSE:RELIANCE"])
        assert prices == {"NFO:NIFTY 26000 CE 30 OCT 26": 250.0, "NSE:RELIANCE": 2900.0}
        assert inner.ltp_calls[-1] == ["NFO:NIFTY26OCT26000CE", "NSE:RELIANCE"]
        assert await broker.get_ltp_for_symbol("NIFTY 26000 CE 30 OCT 26", "NFO") == 250.0
        quotes = await broker.get_quote(["NIFTY 26000 CE 30 OCT 26"])
        assert quotes["NIFTY 26000 CE 30 OCT 26"].symbol == "NIFTY 26000 CE 30 OCT 26"

        # Equities never touch the instrument list.
        before = inner.instrument_calls
        await broker.place_order(BrokerOrderRequest(symbol="RELIANCE", exchange="NSE", transaction_type=OrderSide.BUY, quantity=1, order_type="MARKET", product="CNC"))
        assert inner.orders[-1].symbol == "RELIANCE" and inner.instrument_calls == before

        # Inbound: positions and the order book come back in the platform's spelling; equities untouched.
        positions = await broker.get_positions()
        assert [p.symbol for p in positions] == ["NIFTY 26000 CE 30 OCT 26", "RELIANCE"]
        book = await broker.get_order_book()
        assert [o.symbol for o in book] == ["NIFTY 26000 CE 30 OCT 26", "RELIANCE"]

        # A contract the broker does not list is refused, not guessed.
        with pytest.raises(BrokerAPIError, match="NIFTY 99000 CE 30 OCT 26"):
            await broker.place_order(BrokerOrderRequest(symbol="NIFTY 99000 CE 30 OCT 26", exchange="NFO", transaction_type=OrderSide.BUY, quantity=75, order_type="MARKET", product="MIS"))
        assert broker.translations >= 6

    asyncio.run(scenario())


def test_wrap_only_non_upstox_and_build_adapter_wraps(monkeypatch):
    class _Upstox(_KiteLike):
        name = "upstox"

    upstox = _Upstox()
    assert wrap_contract_symbols(upstox) is upstox
    wrapped = wrap_contract_symbols(_KiteLike())
    assert wrap_contract_symbols(wrapped) is wrapped                                          # never double-wrapped

    built: Dict[str, BrokerInterface] = {"zerodha": _KiteLike(), "upstox": _Upstox()}
    monkeypatch.setattr(token_lifecycle, "get_broker_adapter", lambda name, creds, client=None: built[name])
    monkeypatch.setattr(token_lifecycle, "load_credentials", lambda record: BrokerCredentials())

    class _Record:
        def __init__(self, broker_name): self.broker_name = broker_name

    assert isinstance(token_lifecycle.build_adapter(_Record("zerodha")), ContractSymbolBroker)
    assert token_lifecycle.build_adapter(_Record("upstox")) is built["upstox"]


def test_contract_ltp_sends_instrument_keys_to_upstox_only():
    contract = ResolvedContract(
        kind=InstrumentKind.OPTION, underlying="NIFTY", underlying_symbol="NIFTY 50", tradingsymbol="NIFTY 26000 CE 30 OCT 26", exchange="NFO",
        instrument_key="NSE_FO|56789", lot_size=75, tick_size=0.05, expiry=EXPIRY, strike=26000.0, right="CE",
        entry_side=OrderSide.BUY, trade_direction=SignalDirection.LONG, position=OptionPosition.BUY,
    )

    class _Strict(_KiteLike):
        async def get_ltp_for_symbol(self, symbol, exchange="NSE"):
            assert "|" not in symbol, "an Upstox instrument key reached a non-Upstox broker"
            return 250.0

    assert asyncio.run(contract_ltp(wrap_contract_symbols(_Strict()), contract)) == 250.0

    class _UpstoxLike(_KiteLike):
        name = "upstox"
        seen: List[str] = []
        async def get_ltp_for_symbol(self, symbol, exchange="NSE"):
            self.seen.append(symbol)
            return 250.0

    up = _UpstoxLike()
    assert asyncio.run(contract_ltp(up, contract)) == 250.0 and up.seen == ["NSE_FO|56789"]
