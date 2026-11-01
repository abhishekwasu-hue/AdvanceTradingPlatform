"""Z10-c (G6, Phase 0 exit criterion "no API order path reachable"): the PAPER-only lock, flag `phase0_paper_only`.

Off by default and never turned on by code. While on, every new LIVE entry is refused - at the entry checks and at the
broker-order chokepoint - and exits of an existing LIVE position still reach the broker (ADR-0004)."""
import asyncio
from datetime import timedelta
from typing import List, Optional

import pytest

from app.brokers.models import ORDER_INTENT_ENTRY, BrokerOrderRequest, BrokerOrderResponse
from app.brokers.rate_budget import RateBudget, RateLimitedBroker, RateLimits
from app.core.enums import OrderSide
from app.db.models import StrategyDeploymentRecord, User
from app.execution.tagging import LEG_ENTRY, LEG_EXIT, LEG_STOP, build_order_tag
from app.platform import controls, phase0_lock
from tests.test_auth_api import _session_factory
from tests.test_trading_worker import (
    BAR_TS,
    LATE_NOW,
    OPEN_NOW,
    _deploy,
    _FakeBroker,
    _force_signal,
    _get,
    _signal,
    _tenant,
    _trades,
    _worker,
)

pytestmark = pytest.mark.usefixtures("no_open_positions_from_earlier_tests")   # counts are per cycle (ADR-0004 sweep)


def _run(coro):
    return asyncio.run(coro)


async def _store_lock(session, on: bool, tenants: Optional[List[int]] = None) -> None:
    """What Admin Console -> Feature flags stores (the admin route's own audit is covered by the Phase N tests)."""
    flags = dict((await controls._get(session, controls.KEY_FEATURE_FLAGS)).get("flags") or {})
    flags[phase0_lock.FLAG] = {"on": on, "tenants": sorted(tenants or [])}
    await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": flags}, None)
    await session.commit()


def _set_lock(on: bool, tenants: Optional[List[int]] = None) -> None:
    async def go():
        async with _session_factory() as session:
            await _store_lock(session, on, tenants)
    _run(go())


def _flags() -> dict:
    async def go():
        async with _session_factory() as session:
            return await controls.feature_flags(session)
    return _run(go())


def _order(leg: Optional[str], *, algo: Optional[str] = None, broker: Optional[str] = None) -> BrokerOrderRequest:
    """An order as the platform builds it: the leg's tag, and intent ENTRY only on an entry (OrderRouter, multi-leg)."""
    tag = build_order_tag(strategy_id="ema_rsi", leg=leg, algo_id=algo, broker=broker) if leg else None
    return BrokerOrderRequest(symbol="RELIANCE", exchange="NSE", transaction_type=OrderSide.BUY, quantity=1, tag=tag,
                              intent=ORDER_INTENT_ENTRY if leg == LEG_ENTRY else None)


class _Inner:
    name = "upstox"
    access_token = None

    def __init__(self) -> None:
        self.calls: List[str] = []

    async def place_order(self, order):
        self.calls.append(f"place:{order.tag}")
        return BrokerOrderResponse(order_id="B1", status="OPEN")

    async def modify_order(self, order_id, quantity=None, price=None, trigger_price=None, order_type=None):
        self.calls.append(f"modify:{order_id}")
        return BrokerOrderResponse(order_id=order_id, status="OPEN")

    async def cancel_order(self, order_id):
        self.calls.append(f"cancel:{order_id}")
        return BrokerOrderResponse(order_id=order_id, status="CANCELLED")


def _wrapped(tenant_id: Optional[int] = 7):
    inner = _Inner()
    return RateLimitedBroker(inner, RateBudget(RateLimits(100.0, 100, 1000)), tenant_id=tenant_id), inner  # type: ignore[arg-type]


def test_flag_is_registered_on_by_default_and_live_trading_is_off_by_default():
    flags = _flags()
    assert flags[phase0_lock.FLAG]["on"] is True             # M55 S1 (owner order): the lock ON by default, every mode
    assert phase0_lock.FLAG in controls.DEFAULT_ON_FLAGS and phase0_lock.FLAG not in controls.DEFAULT_OFF_FLAGS
    assert flags["live_trading"]["on"] is False                # M55 S1: LIVE OFF by default; only the owner switches it on
    assert "live_trading" in controls.DEFAULT_OFF_FLAGS


def test_only_a_stated_entry_intent_counts_as_an_entry():
    assert phase0_lock.is_entry_order(_order(LEG_ENTRY))
    assert phase0_lock.is_entry_order(_order(LEG_ENTRY, algo="ALGO42"))
    for leg in (LEG_EXIT, LEG_STOP, None):
        assert not phase0_lock.is_entry_order(_order(leg))
        assert not phase0_lock.is_entry_order(_order(leg, algo="ALGO42"))
    # The tag alone never makes an entry (review M1): an exit tagged like an entry still passes.
    looks_like_entry = BrokerOrderRequest(symbol="RELIANCE", transaction_type=OrderSide.SELL, quantity=1, tag="X-ENT")
    assert not phase0_lock.is_entry_order(looks_like_entry)


@pytest.mark.parametrize("algo", ["NSEALGO1234567890ENT", "NSEALGO1234567890ENTABCDEFGHIJKL"])   # 20 and 32 characters
def test_a_long_algo_id_never_turns_an_exit_into_a_refused_entry(algo):
    """Review M1: the tag used to be cut at 20 characters, so an algo id whose first 20 characters end in "ENT" made
    every EXIT / SL tag read as an entry and the chokepoint refused the square-off (ADR-0004)."""
    assert len(algo) in (20, 32)
    for leg in (LEG_EXIT, LEG_STOP, LEG_ENTRY):
        tag = build_order_tag(strategy_id="ema_rsi", leg=leg, algo_id=algo)
        assert tag.endswith("-" + leg) and len(tag) <= 20        # the leg stays whole; the algo id is cut

    async def go():
        broker, inner = _wrapped()
        phase0_lock.remember(True, ())
        statuses = {leg: (await broker.place_order(_order(leg, algo=algo))).status for leg in (LEG_EXIT, LEG_STOP, LEG_ENTRY)}
        return statuses, inner.calls
    try:
        statuses, calls = _run(go())
    finally:
        phase0_lock.reset_cache()
    assert statuses == {LEG_EXIT: "OPEN", LEG_STOP: "OPEN", LEG_ENTRY: "REJECTED"}
    assert len(calls) == 2 and all(not c.endswith("-ENT") for c in calls)


def test_chokepoint_refuses_entries_only_while_locked():
    async def go():
        broker, inner = _wrapped()
        phase0_lock.remember(False, ())
        assert (await broker.place_order(_order(LEG_ENTRY))).status == "OPEN"        # off: unchanged
        phase0_lock.remember(True, ())
        refused = await broker.place_order(_order(LEG_ENTRY))
        assert refused.status == "REJECTED" and refused.message.startswith(phase0_lock.REFUSAL)
        # Exits, stops, untagged orders, modifies and cancels always reach the broker (ADR-0004).
        for leg in (LEG_EXIT, LEG_STOP, None):
            assert (await broker.place_order(_order(leg))).status == "OPEN"
        assert (await broker.modify_order("SL1", trigger_price=97.5)).status == "OPEN"
        assert (await broker.cancel_order("SL1")).status == "CANCELLED"
        return inner.calls
    try:
        calls = _run(go())
    finally:
        phase0_lock.reset_cache()
    assert calls == ["place:ema_rsi-ENT", "place:ema_rsi-EXIT", "place:ema_rsi-SL", "place:None", "modify:SL1", "cancel:SL1"]


def test_chokepoint_honours_the_tenant_allow_list():
    async def go():
        phase0_lock.remember(False, [7])
        locked, _ = _wrapped(7)
        other, _ = _wrapped(8)
        unknown, _ = _wrapped(None)
        return [(await b.place_order(_order(LEG_ENTRY))).status for b in (locked, other, unknown)]
    try:
        assert _run(go()) == ["REJECTED", "OPEN", "REJECTED"]   # an unknown tenant is refused while anyone is locked
    finally:
        phase0_lock.reset_cache()


def test_entry_blocks_name_the_lock_for_live_only():
    async def go(on: bool, mode: str):
        async with _session_factory() as session:
            await _store_lock(session, on)
            user = User(id=0, tenant_id=1, email="x@example.com", hashed_password="x", role="OWNER")
            return await controls.entry_blocks(session, user=user, mode=mode, broker_name="upstox")
    try:
        assert phase0_lock.ENTRY_REASON in _run(go(True, "LIVE"))
        assert phase0_lock.ENTRY_REASON not in _run(go(True, "PAPER"))
        assert phase0_lock.ENTRY_REASON not in _run(go(False, "LIVE"))
    finally:
        _set_lock(False)


def test_worker_refuses_a_live_entry_but_paper_still_trades(monkeypatch):
    monkeypatch.setattr("app.execution.router.OrderRouter.fill_poll_delay_seconds", 0)
    t = _tenant("z10c-lock-entry@example.com")
    live_id = _deploy(t, mode="LIVE")
    paper_id = _deploy(t, mode="PAPER")
    broker = _FakeBroker(ltp=101.0)
    worker = _worker(monkeypatch, broker)
    _force_signal(monkeypatch, _signal)
    _set_lock(True)
    try:
        report = _run(worker.run_cycle(now=OPEN_NOW))
    finally:
        _set_lock(False)
    assert broker.placed == []                                            # nothing reached the broker
    assert phase0_lock.ENTRY_REASON in (_get(StrategyDeploymentRecord, live_id).last_error or "")
    assert report.signals_executed == 1 and [tr.mode for tr in _trades(t["tenant_id"])] == ["PAPER"]
    assert _get(StrategyDeploymentRecord, paper_id).last_error is None


class _StopStandingBroker(_FakeBroker):
    """The protective stop stays working at the broker (not filled) until cancelled, so an exit needs a market order."""

    def __init__(self, ltp: float = 101.0) -> None:
        super().__init__(ltp=ltp, intraday_bars=360)
        self.cancelled: List[str] = []

    async def cancel_order(self, order_id):
        self.cancelled.append(order_id)
        return BrokerOrderResponse(order_id=order_id, status="CANCELLED")

    async def get_order_book(self):
        book = await super().get_order_book()
        for row in book:
            if row.order_type == "SL-M":
                row.filled_quantity, row.average_price = 0, None
                row.status = "CANCELLED" if row.order_id in self.cancelled else "TRIGGER PENDING"
        return book


@pytest.mark.usefixtures("owner_switched_live_on")   # M55 S1: the LIVE posture the owner switches on
def test_worker_exits_an_existing_live_position_while_locked(monkeypatch):
    """ADR-0004: the LIVE position opened before the lock went on is squared off at the broker, through the chokepoint."""
    monkeypatch.setattr("app.execution.router.OrderRouter.fill_poll_delay_seconds", 0)
    monkeypatch.setattr("app.trading.position_monitor.FILL_POLL_DELAY_SECONDS", 0)
    t = _tenant("z10c-lock-exit@example.com")
    _deploy(t, mode="LIVE")
    broker = _StopStandingBroker(ltp=101.0)
    worker = _worker(monkeypatch, broker)
    _force_signal(monkeypatch, _signal)
    _run(worker.run_cycle(now=OPEN_NOW))                                  # lock off: entry + protective stop
    trade = _trades(t["tenant_id"])[0]
    assert trade.mode == "LIVE" and trade.exit_time is None and len(broker.placed) == 2

    _set_lock(True)
    try:
        broker.ltp = 100.7
        _force_signal(monkeypatch, lambda: _signal(ts=BAR_TS + timedelta(hours=4, minutes=46)))
        report = _run(worker.run_cycle(now=LATE_NOW))
    finally:
        _set_lock(False)
    closed = _trades(t["tenant_id"])
    assert report.positions_closed == 1 and closed[0].exit_reason == "Intraday square-off"
    assert len(closed) == 1                                               # no new entry
    exit_order = broker.placed[-1]
    assert exit_order.tag is not None and exit_order.tag.endswith(LEG_EXIT)
    assert exit_order.transaction_type == OrderSide.SELL and exit_order.order_type == "MARKET"
    assert broker.cancelled == ["ORD-2"]                                  # the standing stop was cancelled first


def test_a_live_option_structure_is_refused_while_locked(monkeypatch):
    """Multi-leg: layer 1 refuses the structure before any leg is sent; layer 2 refuses each leg on its own."""
    from app.core.enums import OptionStrategy, SignalDirection
    from app.core.models import RiskConfig
    from app.execution.multileg import _place_live_legs, execute_structure
    from app.instruments.spreads import resolve_structure
    from tests.test_contract_rules import TODAY, _load_master
    from tests.test_multileg import SPOT, _OptionBroker, _rules

    monkeypatch.setattr("app.execution.router.OrderRouter.fill_poll_delay_seconds", 0)
    _load_master()
    t = _tenant("z10c-lock-structure@example.com")

    async def structure(session):
        return await resolve_structure(session, "NIFTY 50", _rules(), OptionStrategy.BULL_PUT_SPREAD, SignalDirection.LONG,
                                       spread_width=2, spot=SPOT, today=TODAY)

    async def layer1(broker):
        async with _session_factory() as session:
            user = await session.get(User, t["user_id"])
            return await execute_structure(session, user, mode="LIVE", strategy_id="s", signal=_signal(), structure=await structure(session),
                                           rules=_rules(), target_credit_pct=None, stop_credit_pct=None, idempotency_key="z10c-lock-ml",
                                           broker=broker, quote_broker=broker, risk_config=RiskConfig(capital=1_000_000, risk_per_trade_pct=1.0))

    async def layer2(broker):
        async with _session_factory() as session:
            wrapped = RateLimitedBroker(broker, RateBudget(RateLimits(100.0, 100, 1000)), tenant_id=t["tenant_id"])
            phase0_lock.remember(True, ())
            return await _place_live_legs(wrapped, await structure(session), 1, "s", None, {}, [], product="MIS")

    broker = _OptionBroker()
    _set_lock(True)
    try:
        result = _run(layer1(broker))
    finally:
        _set_lock(False)
    assert not result.executed and phase0_lock.ENTRY_REASON in result.reasons and broker.placed == []
    try:
        ok, failure = _run(layer2(broker))
    finally:
        phase0_lock.reset_cache()
    assert not ok and phase0_lock.REFUSAL in failure and broker.placed == []
