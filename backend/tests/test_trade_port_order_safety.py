"""Trade port, part B (order_safety): pure order-path helpers, Upstox market protection (off by default), and the audit
of ATP's LIVE paths - SL-M sized to the filled quantity, wings before shorts (strict full-wing check behind a default-off
flag), the kill switch closing shorts first.

Helper tests ported from Trade@0df3e09d0aa5942e3352327d5b998c5f6939ac61, tests/test_order_safety.py, adapted to ATP's
broker-neutral shapes."""
import asyncio
import gzip
import json

import httpx

from app.brokers.models import BrokerCredentials, BrokerOrderRequest, BrokerOrderResponse
from app.brokers.upstox import UpstoxBroker
from app.core import config
from app.core.enums import OrderSide, SignalDirection
from app.core.models import RiskConfig
from app.execution import order_safety as OS
from app.risk_engine.risk_manager import TradingDayState
from tests.test_auth_api import _session_factory
from tests.test_live_execution import _router, _signal
from tests.test_phase_p0_5_trading_safety import _BookBroker


def _run(coro):
    return asyncio.run(coro)


# --- helpers (ported) -----------------------------------------------------------------------------------------------------
def test_market_protection_setting_defaults_to_none_and_validates():
    assert OS.market_protection_pct(None) is None and OS.market_protection_pct("") is None
    assert OS.market_protection_pct(0) is None and OS.market_protection_pct(30) is None and OS.market_protection_pct(True) is None
    assert OS.market_protection_pct("3") == 3 and OS.market_protection_pct(25) == 25 and OS.market_protection_pct("x") is None


def test_apply_market_protection_returns_the_same_payload_by_default():
    market, limit, slm = {"order_type": "MARKET"}, {"order_type": "LIMIT"}, {"order_type": "SL-M"}
    assert OS.apply_market_protection(market, None) is market
    assert OS.apply_market_protection(market, 2) == {"order_type": "MARKET", "market_protection": 2} and "market_protection" not in market
    assert OS.apply_market_protection(slm, "5")["market_protection"] == 5 and OS.apply_market_protection(limit, 2) is limit


def test_full_failure_only_when_definite():
    assert OS.is_full_failure([{"status": "REJECTED"}, {"status": "cancelled"}], 200)
    assert not OS.is_full_failure([{"status": "REJECTED"}, {"status": "OPEN"}], 200)                      # still pending
    assert not OS.is_full_failure([{"status": "unknown"}], 200)
    assert not OS.is_full_failure(None, None)                                                            # a timeout may have arrived
    assert not OS.is_full_failure(None, 500)
    assert OS.is_full_failure(None, 400) and not OS.is_full_failure(None, 400, order_ids=["X"])
    assert not OS.is_full_failure([{"status": "CANCELLED", "filled_quantity": 25}, {"status": "REJECTED"}], 200)
    assert not OS.is_full_failure([{"status": "REJECTED", "filled_quantity": "?"}], 200)
    assert OS.pending_order_ids([{"order_id": 1, "status": "OPEN"}, {"order_id": 2, "status": "COMPLETE"}, {"order_id": 3}]) == ["1", "3"]


CLOSE = [{"symbol": "PE24400", "side": "SELL", "quantity": 65}, {"symbol": "PE24300", "side": "BUY", "quantity": 65}]


def test_plan_exit_resend_first_attempt_and_remaining_legs():
    assert OS.plan_exit_resend(CLOSE, None, previous_failed=False) == (CLOSE, None)
    # The long wing already closed, the short half closed: only the remainder of the short is sent.
    orders, why = OS.plan_exit_resend(CLOSE, [{"symbol": "PE24400", "quantity": 0}, {"symbol": "PE24300", "quantity": -30}], previous_failed=True)
    assert why is None and orders == [{"symbol": "PE24300", "side": "BUY", "quantity": 30}]
    assert OS.plan_exit_resend(CLOSE, [{"symbol": "PE24400", "quantity": 0}, {"symbol": "PE24300", "quantity": 0}], previous_failed=True) == ([], None)


def test_plan_exit_resend_stops_on_unknown_state():
    pos = [{"symbol": "PE24400", "quantity": 65}, {"symbol": "PE24300", "quantity": -65}]
    assert OS.plan_exit_resend(CLOSE, pos, previous_failed=True, pending_ids=["9"])[0] is None              # status unknown
    assert OS.plan_exit_resend(CLOSE, pos, previous_failed=True, pending_ids=["9"], order_status=lambda _: "OPEN")[0] is None
    assert OS.plan_exit_resend(CLOSE, pos, previous_failed=True, pending_ids=["9"], order_status=lambda _: "CANCELLED")[0] == CLOSE
    assert "other side" in OS.plan_exit_resend(CLOSE, [{"symbol": "PE24400", "quantity": -65}, {"symbol": "PE24300", "quantity": -65}],
                                               previous_failed=True)[1]
    assert "missing" in OS.plan_exit_resend(CLOSE, [{"symbol": "PE24400", "quantity": 65}], previous_failed=True)[1]
    assert "above" in OS.plan_exit_resend(CLOSE, [{"symbol": "PE24400", "quantity": 130}, {"symbol": "PE24300", "quantity": -65}],
                                          previous_failed=True)[1]
    assert "ambiguous" in OS.plan_exit_resend(CLOSE, pos, previous_failed=True, shared_symbols=["PE24300"])[1]
    assert OS.plan_exit_resend(CLOSE, None, previous_failed=True)[0] is None                                  # may have partly filled
    assert OS.plan_exit_resend(CLOSE, None, previous_failed=True, previous_nofill=True) == (CLOSE, None)


# --- Upstox market protection (operator setting, off by default) ---------------------------------------------------------
def _upstox_payloads(monkeypatch, pct, order_type="MARKET"):
    monkeypatch.setattr(config, "ORDER_MARKET_PROTECTION_PCT", pct)
    master = gzip.compress(json.dumps([{"instrument_key": "NSE_EQ|INE002A01018", "exchange": "NSE", "trading_symbol": "RELIANCE",
                                        "instrument_type": "EQ"}]).encode())
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        if "assets.upstox.com" in str(request.url):
            return httpx.Response(200, content=master)
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"status": "success", "data": {"order_id": "UP-1"}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=UpstoxBroker.BASE_URL)
    broker = UpstoxBroker(BrokerCredentials(api_key="k", access_token="t"), client=client)
    _run(broker.place_order(BrokerOrderRequest(symbol="RELIANCE", exchange="NSE", transaction_type=OrderSide.SELL, quantity=5,
                                               order_type=order_type, trigger_price=99.0 if order_type == "SL-M" else None)))
    return seen[0]


def test_upstox_sends_market_protection_only_when_the_operator_sets_it(monkeypatch):
    assert "market_protection" not in _upstox_payloads(monkeypatch, None)
    assert "market_protection" not in _upstox_payloads(monkeypatch, "40")                  # out of range -> not sent
    assert _upstox_payloads(monkeypatch, "3")["market_protection"] == 3
    assert _upstox_payloads(monkeypatch, "3", "SL-M")["market_protection"] == 3
    assert "market_protection" not in _upstox_payloads(monkeypatch, "3", "LIMIT")


# --- ATP LIVE-path audit --------------------------------------------------------------------------------------------------
def test_protective_stop_is_sized_to_the_filled_quantity():
    broker = _BookBroker(fills=200, status="OPEN")                     # 200 of 500 filled, the rest cancelled
    result = _run(_router(broker).execute(_signal(), TradingDayState()))
    assert result.executed and result.partial_fill and result.trade.quantity == 200
    stops = [o for o in broker.placed if o.order_type in ("SL-M", "SL")]
    assert stops and stops[0].quantity == 200


def _structure_run(monkeypatch, strict: bool):
    from app.core.enums import OptionStrategy
    from app.execution.multileg import _place_live_legs
    from app.instruments.spreads import resolve_structure
    from tests.test_contract_rules import TODAY, _load_master
    from tests.test_multileg import SPOT, _OptionBroker, _rules
    monkeypatch.setattr("app.execution.router.OrderRouter.fill_poll_delay_seconds", 0)
    monkeypatch.setattr(config, "LIVE_STRICT_WING_FILL", strict)
    _load_master()

    class _HalfWing(_OptionBroker):
        async def get_order_book(self):
            book = await super().get_order_book()
            return [o.model_copy(update={"filled_quantity": o.quantity / 2}) if "24400 PE" in o.symbol and o.transaction_type == OrderSide.BUY
                    else o for o in book]

        async def cancel_order(self, order_id):
            return BrokerOrderResponse(order_id=order_id, status="CANCELLED")

    async def go(broker):
        async with _session_factory() as session:
            structure = await resolve_structure(session, "NIFTY 50", _rules(), OptionStrategy.BULL_PUT_SPREAD, SignalDirection.LONG,
                                                spread_width=2, spot=SPOT, today=TODAY)
            fills, notes = {}, []
            ok, failure = await _place_live_legs(broker, structure, structure.lot_size, "s", None, fills, notes)
            return ok, failure, structure.lot_size
    broker = _HalfWing()
    ok, failure, lot = _run(go(broker))
    return broker, ok, failure, lot


def test_partial_wing_fill_today_and_with_the_strict_flag(monkeypatch):
    # Default (flag off, today's behaviour, reported for G-LIVE): the short goes at the full quantity over a half wing.
    broker, ok, failure, lot = _structure_run(monkeypatch, strict=False)
    assert ok and [(o.transaction_type.value, o.quantity) for o in broker.placed] == [("BUY", lot), ("SELL", lot)]
    # Strict: no short is sent and the half-filled wing is unwound for exactly what filled.
    broker, ok, failure, lot = _structure_run(monkeypatch, strict=True)
    assert not ok and "shorts not sent" in failure and "unwound 1" in failure
    assert [(o.transaction_type.value, o.quantity) for o in broker.placed] == [("BUY", lot), ("SELL", lot / 2)]
    assert OS.wing_fill_complete(65, 65) and not OS.wing_fill_complete(65, 64)


def test_risk_config_default_is_untouched():
    assert RiskConfig().capital > 0 and config.LIVE_STRICT_WING_FILL is False and config.ORDER_MARKET_PROTECTION_PCT is None
