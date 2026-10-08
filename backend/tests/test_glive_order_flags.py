"""G-LIVE order-path fixes, each behind a default-off flag (nothing changes until the operator turns it on), checked
against mock brokers (no real broker, nothing sent anywhere):

- LIVE_MARKET_PROTECTION: Zerodha and Upstox MARKET / SL-M orders carry `market_protection` (-1 = the broker's automatic
  band, or ORDER_MARKET_PROTECTION_PCT); limit orders never do.
- LIVE_UPSTOX_OPTION_STOP_LIMIT: Upstox option stops go as SL (stop-limit), limit STOP_LIMIT_BAND_PCT past the trigger.
- LIVE_EXIT_IF_NO_STOP: a LIVE position the broker clearly refused a stop for is closed at once with a market exit -
  never after a timeout (the stop may stand), never while the tenant is broker-uncertain or the market is shut, at
  most MAX_EXIT_ATTEMPTS times; an exit no longer tries to cancel a stop the broker already rejected (that cancel
  failed and blocked the exit), and a stop-limit's partial fill is netted off the exit.
- STOP_LIMIT_BAND_PCT: unset = today's 1% band; set = rounded outward, at least one tick past the trigger.
"""
import asyncio
import gzip
import json
from urllib.parse import parse_qs

import httpx
import pytest

from app.brokers.exceptions import BrokerAPIError, BrokerOrderRejected, is_clear_rejection
from app.brokers.models import BrokerCredentials, BrokerOrderRequest, BrokerOrderResponse, BrokerOrderStatus
from app.brokers.upstox import UpstoxBroker
from app.brokers.zerodha import ZerodhaBroker
from app.core import config
from app.core.enums import OrderSide
from app.db.models import Tenant, TradeRecord
from app.trading.position_monitor import close_position
from app.market_data.calendar import SessionStatus
from app.trading import stop_guard
from app.trading.stop_guard import verify_protective_stops
from tests.test_auth_api import _session_factory, client
from tests.test_phase_p_closure import _BookBroker, _seed
from tests.test_trading_worker import _get, _tenant


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _flags_off(monkeypatch):
    for name in ("LIVE_MARKET_PROTECTION", "LIVE_UPSTOX_OPTION_STOP_LIMIT", "LIVE_EXIT_IF_NO_STOP"):
        monkeypatch.setattr(config, name, False)
    monkeypatch.setattr(config, "ORDER_MARKET_PROTECTION_PCT", None)
    monkeypatch.setattr(config, "STOP_LIMIT_BAND_PCT", None)
    monkeypatch.setattr(stop_guard, "_exit_attempts", {})
    monkeypatch.setattr(stop_guard, "_last_failure_alert", {})
    _market(monkeypatch, True)


def _market(monkeypatch, is_open):
    async def status(session, now=None, exchange="NSE"):
        return SessionStatus(is_open, "open" if is_open else "NSE closed for the day", None)
    monkeypatch.setattr(stop_guard, "market_session_status", status)


def _upstox_payload(order_type="MARKET"):
    master = gzip.compress(json.dumps([{"instrument_key": "NSE_EQ|INE002A01018", "exchange": "NSE", "trading_symbol": "RELIANCE",
                                        "instrument_type": "EQ"}]).encode())
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        if "assets.upstox.com" in str(request.url):
            return httpx.Response(200, content=master)
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"status": "success", "data": {"order_id": "UP-1"}})

    broker = UpstoxBroker(BrokerCredentials(api_key="k", access_token="t"),
                          client=httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=UpstoxBroker.BASE_URL))
    _run(broker.place_order(BrokerOrderRequest(symbol="RELIANCE", exchange="NSE", transaction_type=OrderSide.SELL, quantity=5,
                                               order_type=order_type, price=99.0 if order_type == "LIMIT" else None,
                                               trigger_price=99.0 if order_type == "SL-M" else None)))
    return seen[0]


def _zerodha_payload(order_type="MARKET"):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append({k: v[0] for k, v in parse_qs(request.content.decode()).items()})
        return httpx.Response(200, json={"status": "success", "data": {"order_id": "Z-1"}})

    broker = ZerodhaBroker(BrokerCredentials(api_key="k", access_token="t"),
                           client=httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://api.kite.trade"))
    _run(broker.place_order(BrokerOrderRequest(symbol="NIFTY26OCT24500PE", exchange="NFO", transaction_type=OrderSide.BUY, quantity=65,
                                               order_type=order_type, price=99.0 if order_type == "LIMIT" else None)))
    return seen[0]


def test_market_protection_flag_zerodha_and_upstox(monkeypatch):
    # off (today): Zerodha sends no field, Upstox only an operator percentage
    assert "market_protection" not in _zerodha_payload() and "market_protection" not in _upstox_payload()
    monkeypatch.setattr(config, "LIVE_MARKET_PROTECTION", True)
    assert _zerodha_payload()["market_protection"] == "-1" and _upstox_payload()["market_protection"] == -1
    assert _upstox_payload("SL-M")["market_protection"] == -1
    assert "market_protection" not in _zerodha_payload("LIMIT") and "market_protection" not in _upstox_payload("LIMIT")
    monkeypatch.setattr(config, "ORDER_MARKET_PROTECTION_PCT", "5")                 # the operator's percentage wins
    assert _zerodha_payload()["market_protection"] == "5" and _upstox_payload()["market_protection"] == 5


def test_upstox_option_stops_become_stop_limit_with_the_band(monkeypatch):
    broker = UpstoxBroker(BrokerCredentials(api_key="k", access_token="t"))
    opt = "NIFTY 24500 PE 13 OCT 26"
    assert broker.stop_order_params(opt, OrderSide.SELL, 100.0, is_option=True) == ("SL-M", None)          # today
    monkeypatch.setattr(config, "LIVE_UPSTOX_OPTION_STOP_LIMIT", True)
    assert broker.stop_order_params(opt, OrderSide.SELL, 100.0, is_option=True) == ("SL", 99.0)             # 1% band
    assert broker.stop_order_params(opt, OrderSide.BUY, 100.0, is_option=True) == ("SL", 101.0)             # a short's stop
    assert broker.stop_order_params("RELIANCE", OrderSide.SELL, 100.0, is_option=False) == ("SL-M", None)  # stocks unchanged
    monkeypatch.setattr(config, "STOP_LIMIT_BAND_PCT", 3.0)
    assert broker.stop_order_params(opt, OrderSide.SELL, 100.0, is_option=True) == ("SL", 97.0)


@pytest.mark.parametrize("side,trigger,band,limit", [
    (OrderSide.SELL, 10.03, 1.0, 9.90),     # trigger on the tick (10.05); 9.9495 rounds DOWN, never up to 9.95
    (OrderSide.BUY, 10.03, 1.0, 10.20),     # 10.1505 rounds UP
    (OrderSide.SELL, 2.00, 0.1, 1.95),      # a band under one tick still leaves one tick of room
    (OrderSide.BUY, 2.00, 0.0, 2.05),
    (OrderSide.SELL, 0.05, 3.0, 0.05),      # never below the minimum tick
])
def test_a_set_band_rounds_outward_and_keeps_a_tick_of_room(monkeypatch, side, trigger, band, limit):
    monkeypatch.setattr(config, "LIVE_UPSTOX_OPTION_STOP_LIMIT", True)
    monkeypatch.setattr(config, "STOP_LIMIT_BAND_PCT", band)
    broker = UpstoxBroker(BrokerCredentials(api_key="k", access_token="t"))
    assert broker.stop_order_params("NIFTY 24500 PE 13 OCT 26", side, trigger, is_option=True) == ("SL", limit)


def test_only_a_definite_broker_no_counts_as_a_rejection():
    assert is_clear_rejection(BrokerOrderRejected("margin")) and is_clear_rejection(BrokerAPIError("bad", 400))
    for exc in (BrokerAPIError("down", 503), BrokerAPIError("slow", 429), BrokerAPIError("t/o", 408),
                BrokerAPIError("no code"), ConnectionError("reset"), TimeoutError()):
        assert not is_clear_rejection(exc)


class _RejectingStopBroker(_BookBroker):
    """The book shows the stop REJECTED; cancelling it errors (a real broker refuses to cancel a rejected order)."""

    async def cancel_order(self, order_id):
        raise RuntimeError(f"order {order_id} is already rejected")

    async def get_ltp_for_symbol(self, symbol, exchange="NSE"):
        return 97.0


def test_exit_is_blocked_by_a_rejected_stop_today_and_goes_through_with_the_flag(monkeypatch):
    t = _tenant("glive-dead-stop@example.com")
    trade_id = _seed(t["tenant_id"], t["user_id"], sl_order_id="SL-X")
    rejected = BrokerOrderStatus(order_id="SL-X", symbol="RELIANCE", transaction_type=OrderSide.SELL, quantity=10, order_type="SL-M",
                                 status="REJECTED")

    def close():
        broker = _RejectingStopBroker([rejected])

        async def go():
            async with _session_factory() as session:
                trade = await session.get(TradeRecord, trade_id)
                return await close_position(session, trade, 97.0, "Stop Loss", broker=broker), broker
        return _run(go())
    outcome, broker = close()
    assert not outcome.closed and "Could not cancel" in outcome.warnings[0] and broker.placed == []      # today: blocked
    monkeypatch.setattr(config, "LIVE_EXIT_IF_NO_STOP", True)
    outcome, broker = close()
    assert outcome.closed and [(o.order_type, o.transaction_type) for o in broker.placed] == [("MARKET", OrderSide.SELL)]


class _StopRefusedBroker(_BookBroker):
    """Re-placing the stop fails with `stop_error`; the exit order fails while `exit_fails`."""

    def __init__(self, book, stop_error, exit_fails=False):
        super().__init__(book)
        self.stop_error, self.exit_fails, self.cancels = stop_error, exit_fails, []

    async def place_stop_loss_order(self, *args, **kwargs):
        raise self.stop_error

    async def place_order(self, order):
        if self.exit_fails:
            raise ConnectionError("exit refused")
        return await super().place_order(order)

    async def cancel_order(self, order_id):
        self.cancels.append(order_id)
        raise BrokerAPIError(f"order {order_id} not found", 404)


def _guard(t, broker):
    async def go():
        async with _session_factory() as session:
            tenant = await session.get(Tenant, t["tenant_id"])
            return await verify_protective_stops(session, tenant, broker, user_id=t["user_id"])
    return _run(go())


def _critical(t):
    notes = client.get("/api/notifications", headers=t["headers"]).json()
    return [n["title"] for n in (notes if isinstance(notes, list) else notes.get("items", [])) if n["severity"] == "CRITICAL"]


def test_a_position_whose_stop_is_refused_is_closed_at_once(monkeypatch):
    monkeypatch.setattr(config, "LIVE_EXIT_IF_NO_STOP", True)
    t = _tenant("glive-no-stop@example.com")
    trade_id = _seed(t["tenant_id"], t["user_id"], sl_order_id="SL-GONE")       # its stop id is missing from the book
    broker = _StopRefusedBroker([], BrokerOrderRejected("RMS: margin shortfall"))
    counts = _guard(t, broker)
    assert counts["failed"] == 1 and counts["closed"] == 1
    closed = _get(TradeRecord, trade_id)
    assert closed.exit_time is not None and closed.exit_reason.startswith("No protective stop - closed at once")
    assert [(o.order_type, o.transaction_type, o.quantity) for o in broker.placed] == [("MARKET", OrderSide.SELL, 10)]
    assert broker.cancels == []          # a stop missing at the broker is not cancelled first (that blocked the exit)
    assert _critical(t) == ["Closed RELIANCE: no broker-side stop"]


def test_no_immediate_exit_after_a_timeout_or_while_unsure_or_shut(monkeypatch):
    """Any of these leaves the position open under the software stop, with the usual one CRITICAL."""
    monkeypatch.setattr(config, "LIVE_EXIT_IF_NO_STOP", True)
    cases = {"timeout": BrokerAPIError("gateway timeout", 504), "uncertain": BrokerOrderRejected("no"),
             "closed": BrokerOrderRejected("no")}
    for case, error in cases.items():
        t = _tenant(f"glive-skip-{case}@example.com")
        trade_id = _seed(t["tenant_id"], t["user_id"], sl_order_id=None)
        if case == "uncertain":
            async def flag():
                async with _session_factory() as session:
                    from app.reconciliation.service import mark_broker_uncertain
                    await mark_broker_uncertain(session, await session.get(Tenant, t["tenant_id"]), "test")
                    await session.commit()
            _run(flag())
        _market(monkeypatch, case != "closed")
        broker = _StopRefusedBroker([], error)
        counts = _guard(t, broker)
        assert counts["closed"] == 0 and broker.placed == [], case
        assert _get(TradeRecord, trade_id).exit_time is None, case
        assert _critical(t) == ["No broker-side stop on RELIANCE"], case


def test_failed_immediate_exits_stop_after_the_limit_and_alert_once(monkeypatch):
    monkeypatch.setattr(config, "LIVE_EXIT_IF_NO_STOP", True)
    t = _tenant("glive-exit-retry@example.com")
    trade_id = _seed(t["tenant_id"], t["user_id"], sl_order_id=None)
    broker = _StopRefusedBroker([], BrokerOrderRejected("no"), exit_fails=True)
    for _ in range(stop_guard.MAX_EXIT_ATTEMPTS + 2):
        assert _guard(t, broker)["closed"] == 0
    assert stop_guard._exit_attempts[trade_id] == stop_guard.MAX_EXIT_ATTEMPTS
    assert _critical(t) == ["Could NOT close RELIANCE: no broker-side stop"]       # cooldown: one alert, not five
    assert _get(TradeRecord, trade_id).exit_time is None


def test_a_partly_filled_stop_limit_is_netted_off_the_exit(monkeypatch):
    monkeypatch.setattr(config, "LIVE_UPSTOX_OPTION_STOP_LIMIT", True)
    t = _tenant("glive-partial-stop@example.com")
    trade_id = _seed(t["tenant_id"], t["user_id"], sl_order_id="SL-P")
    part = BrokerOrderStatus(order_id="SL-P", symbol="RELIANCE", transaction_type=OrderSide.SELL, quantity=10, filled_quantity=4,
                             order_type="SL", status="CANCELLED", average_price=98.0)

    class B(_BookBroker):
        async def cancel_order(self, order_id):
            return BrokerOrderResponse(order_id=order_id, status="CANCELLED")

    broker = B([part])

    async def go():
        async with _session_factory() as session:
            return await close_position(session, await session.get(TradeRecord, trade_id), 97.0, "Stop Loss", broker=broker)
    outcome = _run(go())
    assert outcome.closed and [(o.order_type, o.quantity) for o in broker.placed] == [("MARKET", 6)]
    assert any("had filled 4 of 10" in w for w in outcome.warnings)
    booked = _get(TradeRecord, trade_id)
    assert booked.exit_price == pytest.approx((4 * 98.0 + 6 * 97.0) / 10, abs=0.01)


def test_the_entry_path_marks_a_definite_stop_rejection():
    from app.risk_engine.risk_manager import TradingDayState
    from tests.test_live_execution import _LiveBroker, _router, _signal
    for error, rejected in ((BrokerOrderRejected("RMS"), True), (ConnectionError("reset"), False)):
        broker = _LiveBroker()

        async def refuse(*args, error=error, **kwargs):
            raise error
        broker.place_stop_loss_order = refuse
        result = _run(_router(broker).execute(_signal(), TradingDayState()))
        assert result.executed and result.sl_failed and result.sl_rejected is rejected


def test_flags_default_off():
    """A fresh process with none of the variables set: every G-LIVE switch is off and the band is today's (unset = 1%)."""
    import os
    import subprocess
    import sys
    env = {k: v for k, v in os.environ.items()
           if k not in ("LIVE_MARKET_PROTECTION", "LIVE_UPSTOX_OPTION_STOP_LIMIT", "LIVE_EXIT_IF_NO_STOP", "STOP_LIMIT_BAND_PCT")}
    out = subprocess.run([sys.executable, "-c", "from app.core import config as c; print(c.LIVE_MARKET_PROTECTION, "
                          "c.LIVE_UPSTOX_OPTION_STOP_LIMIT, c.LIVE_EXIT_IF_NO_STOP, c.STOP_LIMIT_BAND_PCT)"],
                         env=env, capture_output=True, text=True, check=True)
    assert out.stdout.split() == ["False", "False", "False", "None"]
    for raw, parsed in (("", None), ("  ", None), ("abc", None), ("-1", None), ("3", 3.0)):
        assert config._band_pct(raw) == parsed


# --- LIVE_STRICT_WING_FILL (already merged, still off): the four wing outcomes on a NIFTY bull put spread ----------------
def _wing_run(monkeypatch, strict, wing_fill_ratio=1.0, reject_wing=False):
    from app.core.enums import OptionStrategy, SignalDirection
    from app.execution.multileg import _place_live_legs
    from app.instruments.spreads import resolve_structure
    from tests.test_contract_rules import TODAY, _load_master
    from tests.test_multileg import SPOT, _OptionBroker, _rules
    monkeypatch.setattr("app.execution.router.OrderRouter.fill_poll_delay_seconds", 0)
    monkeypatch.setattr(config, "LIVE_STRICT_WING_FILL", strict)
    _load_master()

    class B(_OptionBroker):
        async def place_order(self, order):
            if reject_wing and order.transaction_type == OrderSide.BUY and not self.placed:
                self.placed.append(order)
                return BrokerOrderResponse(order_id="ORD-1", status="REJECTED", message="RMS reject")
            return await super().place_order(order)

        async def get_order_book(self):
            book = await super().get_order_book()
            if book and book[0].transaction_type == OrderSide.BUY and wing_fill_ratio < 1:
                q = book[0].quantity * wing_fill_ratio
                book[0] = book[0].model_copy(update={"filled_quantity": q, "status": "OPEN", "average_price": book[0].average_price if q else 0.0})
            return book

        async def cancel_order(self, order_id):
            return BrokerOrderResponse(order_id=order_id, status="CANCELLED")

    async def go(broker):
        async with _session_factory() as session:
            st = await resolve_structure(session, "NIFTY 50", _rules(), OptionStrategy.BULL_PUT_SPREAD, SignalDirection.LONG,
                                         spread_width=2, spot=SPOT, today=TODAY)
            return await _place_live_legs(broker, st, st.lot_size, "s", None, {}, []), st.lot_size
    broker = B()
    (ok, failure), lot = _run(go(broker))
    return ok, failure, [(o.transaction_type.value, o.quantity) for o in broker.placed], lot


@pytest.mark.parametrize("strict", [False, True])
def test_strict_wing_fill_outcomes(monkeypatch, strict):
    ok, _, orders, lot = _wing_run(monkeypatch, strict)                                   # wing filled in full
    assert ok and orders == [("BUY", lot), ("SELL", lot)]
    ok, failure, orders, lot = _wing_run(monkeypatch, strict, wing_fill_ratio=0.0)        # no fill in the window
    assert not ok and orders == [("BUY", lot)] and "not filled within the confirmation window" in failure
    ok, failure, orders, lot = _wing_run(monkeypatch, strict, reject_wing=True)           # broker rejects the wing
    assert not ok and orders == [("BUY", lot)] and "rejected" in failure
    ok, failure, orders, lot = _wing_run(monkeypatch, strict, wing_fill_ratio=0.5)        # half the wing filled
    if strict:
        assert not ok and "shorts not sent" in failure and orders == [("BUY", lot), ("SELL", lot / 2)]   # unwound
    else:
        assert ok and orders == [("BUY", lot), ("SELL", lot)]                           # today: short at full size
