"""Angel One contract map on the server path (owner, 15 Nov; the runner S4b finding): ATP's F&O contracts are spelt the
Upstox master's way; an Angel One order goes out only as the ONE Angel One scrip-master row that is the same contract
(underlying, expiry, strike, right) with the same lot size. An entry that cannot be matched is refused in plain words
with nothing sent; an exit is never blocked - unmatched, it is still sent under the position's stored name and alerted.
Upstox and the other brokers keep their behaviour."""
import asyncio
import json
import logging
from datetime import date, datetime, timezone
from typing import List

import httpx
import pytest

from app.brokers.angel_one import BASE_URL, SCRIP_MASTER_URL, AngelOneBroker
from app.brokers.base import BrokerInterface
from app.brokers.contract_symbols import (
    ContractKey,
    ContractMatchError,
    ContractSymbolBroker,
    match_instrument,
    wrap_contract_symbols,
)
from app.brokers.exceptions import BrokerAPIError
from app.brokers.models import ORDER_INTENT_ENTRY, BrokerCredentials, BrokerOrderRequest, BrokerOrderResponse, Instrument
from app.core.enums import AssetClass, ExecutionMode, OrderSide, SignalDirection, SignalGrade
from app.core.models import Signal
from app.execution.multileg_smart import LegSpec
from app.execution.router import OrderRouter
from app.instruments.models import ContractSpec
from app.risk_engine.risk_manager import TradingDayState
from tests.def0_legacy import legacy

run = asyncio.run


def _row(token, symbol, name, expiry, strike, lot, itype):
    return {"token": token, "symbol": symbol, "name": name, "expiry": expiry, "strike": f"{strike * 100:.6f}", "lotsize": str(lot),
            "instrumenttype": itype, "exch_seg": "NFO", "tick_size": "5.000000"}


MASTER = [
    {"token": "2885", "symbol": "RELIANCE-EQ", "name": "RELIANCE", "expiry": "", "strike": "-1.000000", "lotsize": "1",
     "instrumenttype": "", "exch_seg": "NSE", "tick_size": "5.000000"},
    _row("43001", "NIFTY24NOV2626000CE", "NIFTY", "24NOV2026", 26000, 75, "OPTIDX"),     # monthly
    _row("43002", "NIFTY24NOV2626000PE", "NIFTY", "24NOV2026", 26000, 75, "OPTIDX"),
    _row("43003", "NIFTY17NOV2625900PE", "NIFTY", "17NOV2026", 25900, 75, "OPTIDX"),     # weekly
    _row("43004", "NIFTY24NOV26FUT", "NIFTY", "24NOV2026", -0.01, 75, "FUTIDX"),
    _row("43005", "RELIANCE24NOV26FUT", "RELIANCE", "24NOV2026", -0.01, 500, "FUTSTK"),
    _row("43006", "BANKNIFTY24NOV2657000CE", "BANKNIFTY", "24NOV2026", 57000, 35, "OPTIDX"),
]


class _SmartAPI:
    """A fake SmartAPI: the scrip master, and every order body that reached it."""

    def __init__(self, master=None):
        self.master = master or MASTER
        self.orders: List[dict] = []
        self.master_downloads = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if str(request.url) == SCRIP_MASTER_URL:
            self.master_downloads += 1
            return httpx.Response(200, content=json.dumps(self.master).encode())
        if request.url.path.endswith("/placeOrder"):
            self.orders.append(json.loads(request.content))
            return httpx.Response(200, json={"status": True, "message": "SUCCESS", "errorcode": "",
                                             "data": {"orderid": f"A{len(self.orders)}"}})
        return httpx.Response(404, json={"status": False, "message": "not here"})


def _angel(server: _SmartAPI) -> ContractSymbolBroker:
    client = httpx.AsyncClient(base_url=BASE_URL, transport=httpx.MockTransport(server))
    adapter = AngelOneBroker(BrokerCredentials(api_key="app-key", access_token="jwt-1"), client=client)
    wrapped = wrap_contract_symbols(adapter)
    assert isinstance(wrapped, ContractSymbolBroker) and wrapped.strict
    return wrapped


def _order(symbol, *, side=OrderSide.BUY, qty=75, entry=True, lot=75, exchange="NFO") -> BrokerOrderRequest:
    return BrokerOrderRequest(symbol=symbol, exchange=exchange, transaction_type=side, quantity=qty, order_type="MARKET",
                              product="NRML", intent=ORDER_INTENT_ENTRY if entry else None, lot_size=lot if entry else None)


# --- the mapping ------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("platform, angel, token, lot", [
    ("NIFTY 26000 CE 24 NOV 26", "NIFTY24NOV2626000CE", "43001", 75),        # monthly call
    ("NIFTY 26000 PE 24 NOV 26", "NIFTY24NOV2626000PE", "43002", 75),        # monthly put
    ("NIFTY 25900 PE 17 NOV 26", "NIFTY17NOV2625900PE", "43003", 75),        # weekly put
    ("NIFTY FUT 24 NOV 26", "NIFTY24NOV26FUT", "43004", 75),                 # index future
    ("RELIANCE FUT 24 NOV 26", "RELIANCE24NOV26FUT", "43005", 500),          # stock future
    ("BANKNIFTY 57000 CE 24 NOV 26", "BANKNIFTY24NOV2657000CE", "43006", 35),
])
def test_entry_goes_out_as_angel_ones_own_instrument(platform, angel, token, lot):
    server = _SmartAPI()
    broker = _angel(server)
    response = run(broker.place_order(_order(platform, qty=lot, lot=lot)))
    assert response.order_id == "A1"
    assert server.orders[0]["tradingsymbol"] == angel and server.orders[0]["symboltoken"] == token
    assert server.orders[0]["quantity"] == str(lot) and server.orders[0]["exchange"] == "NFO"
    assert "lot_size" not in server.orders[0] and "intent" not in server.orders[0]       # never sent to the broker


def test_entry_with_a_different_lot_size_is_refused_and_nothing_is_sent():
    server = _SmartAPI()
    response = run(_angel(server).place_order(_order("NIFTY 26000 CE 24 NOV 26", lot=65, qty=65)))
    assert response.status == "REJECTED" and response.order_id == ""
    assert "lot size here is 65 and Angel One's is 75" in response.message and "nothing was placed" in response.message
    assert server.orders == []


def test_entry_for_a_contract_angel_one_does_not_list_is_refused_in_plain_words():
    server = _SmartAPI()
    broker = _angel(server)
    for symbol in ("NIFTY 99000 CE 24 NOV 26", "NIFTY 26000 CE 01 DEC 26", "SENSEX FUT 24 NOV 26"):
        response = run(broker.place_order(_order(symbol)))
        assert response.status == "REJECTED", symbol
        assert response.message == f"Angel One does not list {symbol} - nothing was placed"
    assert server.orders == []


def test_entry_listed_twice_is_refused():
    twice = MASTER + [_row("49999", "NIFTY24NOV2626000CE", "NIFTY", "24NOV2026", 26000, 75, "OPTIDX")]
    server = _SmartAPI(twice)
    response = run(_angel(server).place_order(_order("NIFTY 26000 CE 24 NOV 26")))
    assert response.status == "REJECTED" and "more than once" in response.message
    assert server.orders == []


def test_entry_without_a_stated_lot_is_matched_on_the_contract_alone():
    server = _SmartAPI()
    run(_angel(server).place_order(_order("NIFTY 26000 PE 24 NOV 26", lot=None)))
    assert server.orders[0]["tradingsymbol"] == "NIFTY24NOV2626000PE"


def test_equities_pass_through_without_the_instrument_list():
    server = _SmartAPI()
    run(_angel(server).place_order(_order("RELIANCE", exchange="NSE", qty=1, lot=None)))
    assert server.orders[0]["tradingsymbol"] == "RELIANCE-EQ"
    assert server.master_downloads == 1           # the adapter's own NSE lookup only; no NFO list for an equity


# --- exits are never blocked ----------------------------------------------------------------------------------------

def test_exit_of_a_matched_contract_ignores_lot_size_and_goes_out_in_angel_spelling():
    server = _SmartAPI()
    broker = _angel(server)
    run(broker.place_order(_order("NIFTY 26000 CE 24 NOV 26", side=OrderSide.SELL, qty=130, entry=False)))
    run(broker.exit_position("NIFTY FUT 24 NOV 26", "NFO", 75, OrderSide.BUY, product="NRML"))
    run(broker.place_stop_loss_order("NIFTY 25900 PE 17 NOV 26", "NFO", OrderSide.SELL, 75, 40.0, product="NRML"))
    assert [o["tradingsymbol"] for o in server.orders] == ["NIFTY24NOV2626000CE", "NIFTY24NOV26FUT", "NIFTY17NOV2625900PE"]
    assert broker.unmatched_exits == []


class _AngelLike(BrokerInterface):
    """An Angel One-named adapter that takes any symbol (to see what reached it)."""
    name = "angel_one"

    def __init__(self, instruments=None, fail_list=False):
        self._access_token = "tok"
        self.instruments = instruments or []
        self.fail_list = fail_list
        self.orders: List[BrokerOrderRequest] = []

    async def get_instruments(self, exchange=None):
        if self.fail_list:
            raise BrokerAPIError("Failed to fetch the Angel One scrip master", 503)
        return self.instruments

    async def place_order(self, order):
        self.orders.append(order)
        return BrokerOrderResponse(order_id=f"o{len(self.orders)}", status="OPEN")

    async def authenticate(self): raise NotImplementedError
    async def get_profile(self): raise NotImplementedError
    async def get_ltp(self, symbols): raise NotImplementedError
    async def get_quote(self, symbols): raise NotImplementedError
    async def get_historical_data(self, symbol, exchange, interval, from_date, to_date): raise NotImplementedError
    async def get_option_chain(self, underlying, expiry=None): raise NotImplementedError
    async def modify_order(self, order_id, quantity=None, price=None, trigger_price=None, order_type=None): raise NotImplementedError
    async def cancel_order(self, order_id): raise NotImplementedError
    async def get_order_book(self): return []
    async def get_trade_book(self): return []
    async def get_positions(self): return []
    async def get_holdings(self): return []
    async def get_margins(self): raise NotImplementedError


@pytest.mark.parametrize("fail_list", [False, True])
def test_exit_with_an_unmatched_contract_is_still_sent_under_the_stored_name_and_alerted(fail_list, caplog):
    inner = _AngelLike(fail_list=fail_list)
    broker = wrap_contract_symbols(inner)
    with caplog.at_level(logging.ERROR, logger="app.brokers.contract_symbols"):
        response = run(broker.place_order(_order("NIFTY 26000 CE 24 NOV 26", side=OrderSide.SELL, entry=False)))
        run(broker.exit_position("NIFTY FUT 24 NOV 26", "NFO", 75, OrderSide.BUY))
    assert response.order_id == "o1"
    assert [o.symbol for o in inner.orders] == ["NIFTY 26000 CE 24 NOV 26", "NIFTY FUT 24 NOV 26"]
    assert len(broker.unmatched_exits) == 2 and "sent under the position's own name" in broker.unmatched_exits[0]
    assert sum("ALERT contract_unmatched_exit" in r.getMessage() for r in caplog.records) == 2


def test_unmatched_exit_reaches_the_real_adapter_and_its_answer_is_not_hidden(caplog):
    """With the real adapter the stored name is tried at Angel One's own lookup; its error reaches the exit path
    (which records it and tries again next check), so the exit is never silently dropped."""
    server = _SmartAPI()
    broker = _angel(server)
    with caplog.at_level(logging.ERROR, logger="app.brokers.contract_symbols"):
        with pytest.raises(BrokerAPIError, match="NIFTY 99000 CE 24 NOV 26"):
            run(broker.place_order(_order("NIFTY 99000 CE 24 NOV 26", side=OrderSide.SELL, entry=False)))
    assert broker.unmatched_exits and any("ALERT contract_unmatched_exit" in r.getMessage() for r in caplog.records)


def test_an_entry_is_never_sent_when_the_list_cannot_be_read():
    inner = _AngelLike(fail_list=True)
    with pytest.raises(BrokerAPIError, match="scrip master"):
        run(wrap_contract_symbols(inner).place_order(_order("NIFTY 26000 CE 24 NOV 26")))
    assert inner.orders == []


# --- the shared matcher and the entry paths that state the lot --------------------------------------------------------

def test_match_instrument_is_the_shared_matcher():
    rows = [Instrument(instrument_token="1", exchange="NFO", tradingsymbol="NIFTY24NOV2626000CE", name="NIFTY", instrument_type="OPTIDX",
                       expiry="2026-11-24", strike=26000.0, lot_size=75),
            Instrument(instrument_token="2", exchange="NFO", tradingsymbol="NIFTY24NOV2626000PE", name="NIFTY", instrument_type="OPTIDX",
                       expiry="2026-11-24", strike=26000.0, lot_size=75)]
    key = ContractKey("NIFTY", date(2026, 11, 24), "PE", 26000.0)
    assert match_instrument(rows, key, lot_size=75, broker_label="Angel One").instrument_token == "2"
    with pytest.raises(ContractMatchError, match="lot size here is 50"):
        match_instrument(rows, key, lot_size=50, broker_label="Angel One")
    with pytest.raises(ContractMatchError, match="does not list"):
        match_instrument(rows, ContractKey("NIFTY", date(2026, 11, 24), "CE", 26100.0))
    assert isinstance(ContractMatchError("x"), BrokerAPIError)


def test_leg_spec_carries_the_lot_and_reads_old_rows():
    spec = LegSpec(key="NIFTY 26000 CE 24 NOV 26", exchange="NFO", side="BUY", role="LONG", quantity=75, lot_size=75)
    assert LegSpec.from_dict(spec.as_dict()) == spec
    old = {k: v for k, v in spec.as_dict().items() if k != "lot_size"}
    assert LegSpec.from_dict(old).lot_size is None


def _contract_signal() -> Signal:
    return Signal(symbol="NIFTY 26000 CE 24 NOV 26", strategy_id="s1", strategy_name="S1", direction=SignalDirection.LONG,
                  timestamp=datetime.now(timezone.utc), entry=100.0, stop_loss=95.0, target1=110.0, target2=None, risk_reward=2.0,
                  score=80, grade=SignalGrade.HIGH_QUALITY, reasons=["test"], timeframe_combo="5min")


@pytest.mark.usefixtures("owner_switched_live_on")
@pytest.mark.parametrize("platform_lot, sent", [(75, True), (65, False)])
def test_router_entry_states_the_contract_lot_and_a_mismatch_is_a_plain_refusal(platform_lot, sent):
    server = _SmartAPI()
    router = OrderRouter(mode=ExecutionMode.LIVE, risk_config=legacy(risk_per_trade_pct=1.0), broker=_angel(server), exchange="NFO",
                         product="NRML", order_style="MARKET", place_protective_stop=False)
    router.fill_poll_delay_seconds = 0
    spec = ContractSpec(symbol="NIFTY 26000 CE 24 NOV 26", exchange="NFO", asset_class=AssetClass.INDEX_OPTION, description="NIFTY option",
                        lot_size=float(platform_lot), tick_size=0.05)
    result = run(router.execute(_contract_signal(), TradingDayState(), contract_spec=spec, max_quantity=float(platform_lot)))
    if sent:
        assert server.orders and server.orders[0]["tradingsymbol"] == "NIFTY24NOV2626000CE"
    else:
        assert server.orders == [] and not result.executed and not result.system_failure
        assert any("lot size here is 65 and Angel One's is 75" in r for r in result.reasons)


# --- other brokers unchanged ----------------------------------------------------------------------------------------

def test_other_brokers_keep_the_lenient_lookup_and_upstox_is_never_wrapped():
    class _Upstox(_AngelLike):
        name = "upstox"

    class _Zerodha(_AngelLike):
        name = "zerodha"

    upstox = _Upstox()
    assert wrap_contract_symbols(upstox) is upstox
    rows = [Instrument(instrument_token="1", exchange="NFO", tradingsymbol="NIFTY24NOV26000CE", name="NIFTY", instrument_type="CE",
                       expiry="2026-11-24", strike=26000.0, lot_size=75)] * 2          # listed twice, other lot: still sent
    inner = _Zerodha(instruments=rows)
    broker = wrap_contract_symbols(inner)
    assert not broker.strict
    run(broker.place_order(_order("NIFTY 26000 CE 24 NOV 26", lot=65, qty=65)))
    assert inner.orders[0].symbol == "NIFTY24NOV26000CE"
    with pytest.raises(BrokerAPIError, match="lists no instrument"):       # unchanged: an unknown contract raises
        run(broker.place_order(_order("NIFTY 99000 CE 24 NOV 26", entry=False)))
