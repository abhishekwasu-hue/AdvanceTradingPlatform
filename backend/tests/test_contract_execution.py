"""Phase F3: executing on a derived contract - order plan (bought/written option, future),
lot sizing off the master's lot size and the premium at risk, max-lots and margin caps, paper
fills on the contract's own price, LIVE entry + SL-M at the premium floor/ceiling, partial
fills, execution quality, and the worker end to end."""
import asyncio
from datetime import date, datetime, timezone
from typing import List, Optional

import pytest
from sqlalchemy import select

from app.brokers.base import BrokerInterface
from app.brokers.models import BrokerOrderResponse, BrokerOrderStatus, MarginInfo
from app.core.enums import ExpiryRule, InstrumentKind, OptionPosition, OrderSide, SignalDirection, SignalGrade, StrikeRule
from app.core.models import RiskConfig, Signal
from app.db.models import OrderRecord, StrategyDeploymentRecord, TradeRecord, User
from app.execution.contract_execution import ContractExecutionError, build_order_plan, contract_ltp, written_lot_cap
from app.execution.signal_execution import execute_signal_for_user
from app.instruments.contracts import ContractRules, resolve_contract
from app.instruments.master import parse_upstox_master, replace_master
from tests.master_fixture import NIFTY_LOT, build_master
from tests.test_auth_api import _register, _session_factory, client
from tests.test_live_execution import _upgrade_plan

TODAY = date(2026, 9, 28)
BIG = RiskConfig(capital=1_000_000, risk_per_trade_pct=1.0, max_open_positions=10, max_trades_per_day=50)


def _run(coro):
    return asyncio.run(coro)


def _signal(direction=SignalDirection.LONG) -> Signal:
    long = direction == SignalDirection.LONG
    return Signal(
        symbol="NIFTY 50", strategy_id="ema_rsi_scalper_1m", strategy_name="EMA + RSI", direction=direction,
        timestamp=datetime(2026, 9, 28, 4, 30, tzinfo=timezone.utc), entry=24512.0, stop_loss=24460.0 if long else 24564.0,
        target1=24610.0 if long else 24414.0, target2=24700.0 if long else 24324.0, risk_reward=1.9, score=80,
        grade=SignalGrade.HIGH_QUALITY, reasons=["test"], timeframe_combo="1min",
    )


class _FnoBroker(BrokerInterface):
    """Quotes: the underlying at 24512, any contract at `premium`. Orders fill fully unless
    `partial` is set. Margin calculator optional."""
    name = "fakefno"
    max_tag_length = 40

    def __init__(self, premium: float = 120.0, *, margin_per_lot: Optional[float] = 60_000.0, available: float = 100_000.0, partial: Optional[float] = None):
        self.premium = premium
        self.margin_per_lot = margin_per_lot
        self.available = available
        self.partial = partial
        self.placed = []
        self.quoted = []

    async def get_ltp_for_symbol(self, symbol, exchange="NSE"):
        self.quoted.append((symbol, exchange))
        return 24512.0 if symbol in ("NIFTY 50", "NIFTY BANK") else self.premium

    async def get_ltp(self, symbols):
        return {s: (24512.0 if "NIFTY 50" in s else self.premium) for s in symbols}

    async def place_order(self, order):
        self.placed.append(order)
        return BrokerOrderResponse(order_id=f"FNO-{len(self.placed)}", status="OPEN")

    async def get_order_book(self) -> List[BrokerOrderStatus]:
        book = []
        for i, o in enumerate(self.placed, start=1):
            if o.order_type != "MARKET":
                continue
            filled = self.partial if self.partial is not None else o.quantity
            book.append(BrokerOrderStatus(order_id=f"FNO-{i}", symbol=o.symbol, transaction_type=o.transaction_type, quantity=o.quantity,
                                          filled_quantity=filled, order_type="MARKET", status="COMPLETE", average_price=o.price or self.premium + 0.5))
        return book

    async def get_order_margin(self, order):
        return self.margin_per_lot

    async def get_margins(self):
        return MarginInfo(available_cash=self.available, available_margin=self.available)

    async def authenticate(self): raise NotImplementedError
    async def get_profile(self): raise NotImplementedError
    async def get_instruments(self, exchange=None): return []
    async def get_quote(self, symbols): raise NotImplementedError
    async def get_historical_data(self, *a, **k): raise NotImplementedError
    async def get_option_chain(self, underlying, expiry=None): raise NotImplementedError
    async def modify_order(self, *a, **k): raise NotImplementedError
    async def cancel_order(self, order_id): return BrokerOrderResponse(order_id=order_id, status="CANCELLED")
    async def get_trade_book(self): return []
    async def get_positions(self): return []
    async def get_holdings(self): return []


def _load_master():
    rows = parse_upstox_master(build_master(), "NSE")

    async def go():
        async with _session_factory() as session:
            await replace_master(session, "upstox", ["NSE", "NFO"], rows)
    _run(go())


def _resolve(rules: ContractRules, direction=SignalDirection.LONG, spot=24512.0):
    async def go():
        async with _session_factory() as session:
            return await resolve_contract(session, "NIFTY 50", rules, direction, spot=spot, today=TODAY)
    return _run(go())


BUY = ContractRules(kind=InstrumentKind.OPTION, position=OptionPosition.BUY, premium_stop_pct=30.0)
WRITE = ContractRules(kind=InstrumentKind.OPTION, position=OptionPosition.WRITE, premium_stop_pct=50.0)
FUT = ContractRules(kind=InstrumentKind.FUTURE, expiry_rule=ExpiryRule.MONTHLY)


def test_order_plan_for_bought_written_and_future():
    _load_master()
    ce = _resolve(BUY)
    plan = build_order_plan(_signal(), ce, BUY, ltp=120.0)
    assert plan.order_signal.symbol == ce.tradingsymbol and plan.order_signal.direction == SignalDirection.LONG
    assert plan.order_signal.entry == 120.0 and plan.order_signal.stop_loss == 84.0 and plan.order_signal.target1 is None
    assert plan.contract_spec.lot_size == NIFTY_LOT and plan.max_quantity is None
    assert plan.meta["underlying_symbol"] == "NIFTY 50" and plan.meta["underlying_stop_loss"] == 24460.0 and plan.meta["underlying_direction"] == "LONG"

    pe_written = _resolve(WRITE)   # LONG signal -> sell PE
    plan = build_order_plan(_signal(), pe_written, WRITE, ltp=100.0)
    assert pe_written.right == "PE" and plan.order_signal.direction == SignalDirection.SHORT
    assert plan.order_signal.stop_loss == 150.0 and plan.max_quantity == NIFTY_LOT  # one-lot default for writes

    fut = _resolve(FUT, SignalDirection.SHORT, spot=None)
    plan = build_order_plan(_signal(SignalDirection.SHORT), fut, FUT, ltp=24580.0)
    assert plan.order_signal.direction == SignalDirection.SHORT
    assert plan.order_signal.stop_loss == pytest.approx(24580.0 + 52.0) and plan.order_signal.target1 == pytest.approx(24580.0 - 98.0)


def test_contract_ltp_prefers_instrument_key_then_symbol():
    _load_master()
    ce = _resolve(BUY)
    broker = _FnoBroker(premium=95.5)
    assert _run(contract_ltp(broker, ce)) == 95.5
    assert broker.quoted[0] == (ce.instrument_key, "NFO")

    class NoKey(_FnoBroker):
        async def get_ltp_for_symbol(self, symbol, exchange="NSE"):
            if "|" in symbol:
                raise KeyError("unknown key")
            return 77.0
    assert _run(contract_ltp(NoKey(), ce)) == 77.0

    class Dead(_FnoBroker):
        async def get_ltp_for_symbol(self, symbol, exchange="NSE"):
            raise ConnectionError("down")
    with pytest.raises(ContractExecutionError, match="No quote"):
        _run(contract_ltp(Dead(), ce))


def test_written_lot_cap_from_margin():
    _load_master()
    pe = _resolve(WRITE)
    lots, note = _run(written_lot_cap(_FnoBroker(margin_per_lot=60_000, available=200_000), pe))
    assert lots == 2 and "2 lot" in note                      # 0.8 * 200k / 60k
    with pytest.raises(ContractExecutionError, match="Insufficient margin"):
        _run(written_lot_cap(_FnoBroker(margin_per_lot=60_000, available=50_000), pe))
    with pytest.raises(ContractExecutionError, match="did not report"):
        _run(written_lot_cap(_FnoBroker(margin_per_lot=None), pe))


def _user(email: str) -> User:
    _register(email)

    async def load():
        async with _session_factory() as session:
            return await session.scalar(select(User).where(User.email == email))
    user = _run(load())
    _upgrade_plan(user.tenant_id, "pro")
    return user


def _execute(user: User, *, mode: str, contract, rules, broker=None, quote_broker=None, signal=None, risk=BIG):
    async def go():
        async with _session_factory() as session:
            u = await session.get(User, user.id)
            result, order = await execute_signal_for_user(
                session, u, mode=mode, strategy_id="ema_rsi_scalper_1m", signal=signal or _signal(), risk_config=risk,
                broker=broker, quote_broker=quote_broker, contract=contract, rules=rules,
            )
            trade = await session.get(TradeRecord, order.trade_id) if order.trade_id else None
            return result, order, trade
    return _run(go())


def test_paper_option_buy_fills_on_premium_and_sizes_in_lots():
    _load_master()
    user = _user("fno-paper@example.com")
    ce = _resolve(BUY)
    result, order, trade = _execute(user, mode="PAPER", contract=ce, rules=BUY, quote_broker=_FnoBroker(premium=120.0))
    assert result.executed, result.reasons
    # risk 1% of 10L = 10,000; premium at risk 36/unit -> 277 units -> 3 lots of 75
    assert trade.quantity == 3 * NIFTY_LOT and trade.lot_size == NIFTY_LOT
    assert trade.symbol == ce.tradingsymbol and order.symbol == ce.tradingsymbol
    assert 119.0 < trade.entry_price < 121.0 and trade.stop_loss == 84.0 and trade.target1 is None
    assert trade.instrument_kind == "OPTION" and trade.exchange == "NFO" and trade.option_position == "BUY"
    assert trade.underlying_symbol == "NIFTY 50" and trade.underlying_direction == "LONG"
    assert trade.underlying_stop_loss == 24460.0 and trade.underlying_target1 == 24610.0 and trade.premium_stop_pct == 30.0
    assert trade.expiry == date(2026, 10, 1) and trade.expected_price == 120.0 and trade.slippage is not None and trade.entry_latency_ms is not None
    assert trade.mode == "PAPER" and trade.broker_order_id is None


def test_small_account_is_rejected_below_one_lot_with_a_clear_reason():
    _load_master()
    user = _user("fno-small@example.com")
    ce = _resolve(BUY)
    result, order, trade = _execute(user, mode="PAPER", contract=ce, rules=BUY, quote_broker=_FnoBroker(premium=120.0), risk=RiskConfig())
    assert not result.executed and order.status == "REJECTED" and trade is None
    assert any("below one lot" in r for r in result.reasons)


def test_max_lots_caps_the_size():
    _load_master()
    user = _user("fno-cap@example.com")
    rules = ContractRules(kind=InstrumentKind.OPTION, position=OptionPosition.BUY, premium_stop_pct=30.0, max_lots=1)
    ce = _resolve(rules)
    result, order, trade = _execute(user, mode="PAPER", contract=ce, rules=rules, quote_broker=_FnoBroker(premium=120.0))
    assert result.executed and trade.quantity == NIFTY_LOT
    assert any("capped" in r for r in result.reasons)


def test_missing_premium_quote_rejects_on_the_order_trail():
    _load_master()
    user = _user("fno-noquote@example.com")
    ce = _resolve(BUY)

    class Dead(_FnoBroker):
        async def get_ltp_for_symbol(self, symbol, exchange="NSE"):
            raise ConnectionError("quote service down")
    result, order, trade = _execute(user, mode="PAPER", contract=ce, rules=BUY, quote_broker=Dead())
    assert not result.executed and order.status == "REJECTED" and trade is None
    assert order.symbol == ce.tradingsymbol and "No quote" in order.reasons_json


def test_live_option_buy_places_entry_and_premium_floor_stop(monkeypatch):
    from app.execution.router import OrderRouter
    monkeypatch.setattr(OrderRouter, "fill_poll_delay_seconds", 0)
    _load_master()
    user = _user("fno-live@example.com")
    ce = _resolve(BUY)
    broker = _FnoBroker(premium=120.0)
    result, order, trade = _execute(user, mode="LIVE", contract=ce, rules=BUY, broker=broker)
    assert result.executed, result.reasons
    entry, sl = broker.placed[0], broker.placed[1]
    assert entry.symbol == ce.tradingsymbol and entry.exchange == "NFO" and entry.transaction_type == OrderSide.BUY and entry.quantity == 3 * NIFTY_LOT
    assert sl.order_type == "SL-M" and sl.transaction_type == OrderSide.SELL and sl.trigger_price == 84.0 and sl.quantity == entry.quantity
    assert trade.entry_price == 120.5 and trade.broker_order_id == "FNO-1" and trade.sl_order_id == "FNO-2"
    assert trade.expected_price == 120.0 and trade.slippage == pytest.approx(0.5)


def test_live_written_option_sells_and_caps_by_margin(monkeypatch):
    from app.execution.router import OrderRouter
    monkeypatch.setattr(OrderRouter, "fill_poll_delay_seconds", 0)
    _load_master()
    user = _user("fno-write@example.com")
    rules = ContractRules(kind=InstrumentKind.OPTION, position=OptionPosition.WRITE, premium_stop_pct=50.0, max_lots=5)
    pe = _resolve(rules)
    broker = _FnoBroker(premium=100.0, margin_per_lot=60_000, available=100_000)   # margin allows 1 lot
    result, order, trade = _execute(user, mode="LIVE", contract=pe, rules=rules, broker=broker)
    assert result.executed, result.reasons
    entry, sl = broker.placed[0], broker.placed[1]
    assert entry.transaction_type == OrderSide.SELL and entry.quantity == NIFTY_LOT
    assert sl.transaction_type == OrderSide.BUY and sl.trigger_price == 150.0
    assert trade.direction == "SHORT" and trade.option_position == "WRITE" and trade.underlying_direction == "LONG"
    assert any("Margin" in r for r in result.reasons)


def test_live_write_refused_when_margin_unknown_or_insufficient(monkeypatch):
    from app.execution.router import OrderRouter
    monkeypatch.setattr(OrderRouter, "fill_poll_delay_seconds", 0)
    _load_master()
    user = _user("fno-write-refused@example.com")
    pe = _resolve(WRITE)
    unknown = _FnoBroker(premium=100.0, margin_per_lot=None)
    result, order, trade = _execute(user, mode="LIVE", contract=pe, rules=WRITE, broker=unknown)
    assert not result.executed and order.status == "REJECTED" and unknown.placed == [] and "margin" in result.reasons[0].lower()
    poor = _FnoBroker(premium=100.0, margin_per_lot=60_000, available=10_000)
    result, order, trade = _execute(user, mode="LIVE", contract=pe, rules=WRITE, broker=poor)
    assert not result.executed and poor.placed == [] and "Insufficient margin" in result.reasons[0]


def test_partial_fill_sizes_position_and_stop_to_filled_quantity(monkeypatch):
    from app.execution.router import OrderRouter
    monkeypatch.setattr(OrderRouter, "fill_poll_delay_seconds", 0)
    _load_master()
    user = _user("fno-partial@example.com")
    ce = _resolve(BUY)
    broker = _FnoBroker(premium=120.0, partial=float(NIFTY_LOT))   # 1 of 3 lots filled
    result, order, trade = _execute(user, mode="LIVE", contract=ce, rules=BUY, broker=broker)
    assert result.executed
    assert trade.quantity == NIFTY_LOT and broker.placed[1].quantity == NIFTY_LOT
    assert any("Partial fill" in r for r in result.reasons)


def test_paper_future_trades_translated_levels():
    _load_master()
    user = _user("fno-fut@example.com")
    fut = _resolve(FUT, SignalDirection.SHORT, spot=None)
    result, order, trade = _execute(user, mode="PAPER", contract=fut, rules=FUT, quote_broker=_FnoBroker(premium=24580.0), signal=_signal(SignalDirection.SHORT))
    assert result.executed, result.reasons
    assert trade.instrument_kind == "FUTURE" and trade.direction == "SHORT" and trade.lot_size == NIFTY_LOT
    assert trade.stop_loss == pytest.approx(24632.0) and trade.target1 == pytest.approx(24482.0)
    # risk 10,000 / 52 points = 192 units -> 2 lots
    assert trade.quantity == 2 * NIFTY_LOT


def test_worker_trades_option_deployment_end_to_end(monkeypatch):
    from app.workers import trading_worker as tw
    from tests.test_trading_worker import OPEN_NOW, _FakeBroker, _deploy, _force_signal, _get, _tenant, _trades, _worker

    _load_master()
    t = _tenant("fno-worker@example.com")
    dep_id = _deploy(t, symbol="NIFTY 50")

    async def mark():
        async with _session_factory() as session:
            dep = await session.get(StrategyDeploymentRecord, dep_id)
            dep.instrument_kind, dep.option_position, dep.expiry_rule, dep.strike_rule, dep.premium_stop_pct, dep.max_lots = "OPTION", "BUY", "NEAREST", "ATM", 30.0, 2
            await session.commit()
    _run(mark())

    class Broker(_FakeBroker):
        async def get_ltp(self, symbols):
            return {s: (24512.0 if "NIFTY 50" in s else 150.0) for s in symbols}
    broker = Broker()
    # The fake candles close at 100.5 - override the resolver's spot with the worker's own frames
    # is what production does; here the master's strikes start at 24000, so spot must be in range.
    monkeypatch.setattr(tw, "resolve_contract", lambda session, symbol, rules, direction, *, spot, today, broker="upstox", **kw:
                        resolve_contract(session, symbol, rules, direction, spot=24512.0, today=TODAY, broker=broker, **kw))
    worker = _worker(monkeypatch, broker)
    # A generous tenant risk config so one lot is affordable.
    from app.risk_engine import routes as risk_routes
    async def big_risk(tenant_id, session):
        return BIG
    monkeypatch.setattr("app.execution.signal_execution.get_tenant_risk_config", big_risk)
    _force_signal(monkeypatch, lambda: _signal())

    report = _run(worker.run_cycle(now=OPEN_NOW))
    assert report.signals_executed == 1, report.errors
    trades = _trades(t["tenant_id"])
    assert len(trades) == 1
    trade = trades[0]
    assert trade.instrument_kind == "OPTION" and "24500 CE" in trade.symbol and trade.exchange == "NFO"
    assert trade.quantity == 2 * NIFTY_LOT and trade.underlying_symbol == "NIFTY 50"
    assert _get(StrategyDeploymentRecord, dep_id).last_error is None


def test_worker_records_resolution_failure_without_trading(monkeypatch):
    from tests.test_trading_worker import OPEN_NOW, _FakeBroker, _deploy, _force_signal, _get, _tenant, _trades, _worker

    _load_master()
    t = _tenant("fno-worker-fail@example.com")
    dep_id = _deploy(t, symbol="TCS")   # no TCS options in the master

    async def mark():
        async with _session_factory() as session:
            dep = await session.get(StrategyDeploymentRecord, dep_id)
            dep.instrument_kind, dep.option_position = "OPTION", "BUY"
            await session.commit()
    _run(mark())
    worker = _worker(monkeypatch, _FakeBroker())
    _force_signal(monkeypatch, lambda: _signal())
    report = _run(worker.run_cycle(now=OPEN_NOW))
    assert report.signals_executed == 0 and not report.errors and _trades(t["tenant_id"]) == []
    assert "Contract not resolved" in _get(StrategyDeploymentRecord, dep_id).last_error
