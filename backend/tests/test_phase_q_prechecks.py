"""Phase Q / master prompt section 17: pre-placement checks - instrument and expiry validity,
margin available for every LIVE entry (not only written options), and the PARTIAL_FILL status on
the order trail."""
import asyncio
from dataclasses import replace
from datetime import date, datetime, timezone
from typing import List, Optional

import pytest
from sqlalchemy import select

from app.brokers.base import BrokerInterface
from app.brokers.models import BrokerOrderResponse, BrokerOrderStatus, MarginInfo
from app.core.enums import OrderSide, SignalDirection, SignalGrade
from app.core.models import RiskConfig, Signal
from app.db.models import OrderEventRecord, TradeRecord, User
from app.execution.prechecks import PreCheckRefusal, is_index_symbol, live_margin_cap, validate_instrument
from app.execution.router import OrderRouter
from app.execution.signal_execution import execute_signal_for_user
from app.instruments.master import MasterRow, replace_master
from tests.test_auth_api import _register, _session_factory, client  # noqa: F401 - fixtures
from tests.test_contract_execution import BUY, _load_master, _resolve
from tests.test_live_execution import _upgrade_plan

TODAY = date(2026, 9, 28)
RISK = RiskConfig(capital=1_000_000, risk_per_trade_pct=1.0, max_open_positions=10, max_trades_per_day=50)


def _run(coro):
    return asyncio.run(coro)


def _signal(symbol="RELIANCE", entry=100.0, stop=98.0) -> Signal:
    return Signal(
        symbol=symbol, strategy_id="ema_rsi_scalper_1m", strategy_name="EMA + RSI", direction=SignalDirection.LONG,
        timestamp=datetime.now(timezone.utc), entry=entry, stop_loss=stop, target1=entry + 4, target2=entry + 8,
        risk_reward=2.0, score=85, grade=SignalGrade.HIGH_QUALITY, reasons=["test"], timeframe_combo="1min",
    )


class _Broker(BrokerInterface):
    """Equity broker with a configurable margin calculator, funds and fill behaviour."""
    name = "fakeq"

    def __init__(self, *, per_share: Optional[float] = None, available: Optional[float] = 10_000.0,
                 calculator_raises: bool = False, partial: Optional[float] = None):
        self.per_share = per_share
        self.available = available
        self.calculator_raises = calculator_raises
        self.partial = partial
        self.placed = []

    async def get_order_margin(self, order):
        if self.calculator_raises:
            raise ConnectionError("margin calculator down")
        return None if self.per_share is None else self.per_share * order.quantity

    async def get_margins(self):
        if self.available is None:
            raise ConnectionError("funds endpoint down")
        return MarginInfo(available_cash=self.available, available_margin=self.available)

    async def place_order(self, order):
        self.placed.append(order)
        return BrokerOrderResponse(order_id=f"Q-{len(self.placed)}", status="OPEN")

    async def get_order_book(self) -> List[BrokerOrderStatus]:
        return [BrokerOrderStatus(
            order_id=f"Q-{i}", symbol=o.symbol, transaction_type=o.transaction_type, quantity=o.quantity,
            filled_quantity=self.partial if self.partial is not None else o.quantity, order_type="MARKET",
            status="COMPLETE", average_price=100.2,
        ) for i, o in enumerate(self.placed, start=1) if o.order_type == "MARKET"]

    async def authenticate(self): raise NotImplementedError
    async def get_profile(self): raise NotImplementedError
    async def get_instruments(self, exchange=None): return []
    async def get_ltp(self, symbols): return {s: 100.0 for s in symbols}
    async def get_quote(self, symbols): raise NotImplementedError
    async def get_historical_data(self, *a, **k): raise NotImplementedError
    async def get_option_chain(self, underlying, expiry=None): raise NotImplementedError
    async def modify_order(self, *a, **k): raise NotImplementedError
    async def cancel_order(self, order_id): return BrokerOrderResponse(order_id=order_id, status="CANCELLED")
    async def get_trade_book(self): return []
    async def get_positions(self): return []
    async def get_holdings(self): return []


def _user(email: str) -> User:
    _register(email)

    async def load():
        async with _session_factory() as session:
            return await session.scalar(select(User).where(User.email == email))
    user = _run(load())
    _upgrade_plan(user.tenant_id, "pro")
    return user


def _execute(user: User, broker, *, signal=None, mode="LIVE", contract=None, rules=None):
    async def go():
        async with _session_factory() as session:
            u = await session.get(User, user.id)
            result, order = await execute_signal_for_user(
                session, u, mode=mode, strategy_id="ema_rsi_scalper_1m", signal=signal or _signal(), risk_config=RISK,
                broker=broker, quote_broker=broker, contract=contract, rules=rules,
            )
            trade = await session.get(TradeRecord, order.trade_id) if order.trade_id else None
            events = [e.to_status for e in (await session.scalars(
                select(OrderEventRecord).where(OrderEventRecord.order_id == order.id).order_by(OrderEventRecord.id))).all()]
            return result, order, trade, events
    return _run(go())


def _seed_master(broker: str, symbols):
    rows = [MasterRow(broker=broker, exchange="NSE", segment="NSE_EQ", instrument_key=f"NSE_EQ|{s}", tradingsymbol=s,
                      name=s, underlying=s, instrument_type="EQ", expiry=None, strike=None, lot_size=1, tick_size=0.05)
            for s in symbols]
    rows.append(MasterRow(broker=broker, exchange="NSE", segment="NSE_INDEX", instrument_key="NSE_INDEX|Nifty 50", tradingsymbol="NIFTY 50",
                          name="NIFTY 50", underlying="NIFTY", instrument_type="INDEX", expiry=None, strike=None, lot_size=1, tick_size=0.05))

    async def go():
        async with _session_factory() as session:
            await replace_master(session, broker, ["NSE"], rows)
    _run(go())


# --- validate_instrument -------------------------------------------------------------------------

def test_index_symbols_are_recognised():
    assert is_index_symbol("NIFTY 50") and is_index_symbol("NIFTY BANK") and is_index_symbol("SENSEX") and is_index_symbol("finnifty")
    assert not is_index_symbol("RELIANCE") and not is_index_symbol("NIFTYBEES")


def test_live_index_spot_refused_paper_allowed():
    async def go():
        async with _session_factory() as session:
            live = await validate_instrument(session, symbol="NIFTY 50", exchange="NSE", mode="LIVE", broker_name="fakeq", today=TODAY)
            paper = await validate_instrument(session, symbol="NIFTY 50", exchange="NSE", mode="PAPER", broker_name=None, today=TODAY)
            return live, paper
    live, paper = _run(go())
    assert live[0] and "index" in live[0][0].lower() and "contract rules" in live[0][0]
    assert paper == ([], [])


def test_master_lookup_refuses_unknown_symbol_and_notes_unsynced_master():
    _seed_master("fakeq", ["RELIANCE", "TCS"])

    async def go():
        async with _session_factory() as session:
            known = await validate_instrument(session, symbol="RELIANCE", exchange="NSE", mode="LIVE", broker_name="fakeq", today=TODAY)
            unknown = await validate_instrument(session, symbol="NOSUCHSTOCK", exchange="NSE", mode="LIVE", broker_name="fakeq", today=TODAY)
            index_row = await validate_instrument(session, symbol="NIFTYBEES", exchange="NSE", mode="LIVE", broker_name="fakeq", today=TODAY)
            unsynced = await validate_instrument(session, symbol="RELIANCE", exchange="NSE", mode="LIVE", broker_name="neverSynced", today=TODAY)
            mcx = await validate_instrument(session, symbol="CRUDEOIL", exchange="MCX", mode="LIVE", broker_name="neverSynced", today=TODAY)
            return known, unknown, index_row, unsynced, mcx
    known, unknown, index_row, unsynced, mcx = _run(go())
    assert known[0] == [] and "verified against fakeq" in known[1][0]
    assert unknown[0] and "not in fakeq's NSE instrument master" in unknown[0][0]
    assert index_row[0] and "not in fakeq's NSE instrument master" in index_row[0][0]   # ETF not seeded -> unknown
    assert unsynced[0] == [] and "not synced" in unsynced[1][0]
    assert mcx == ([], [])   # registry instrument: master lookup does not apply


def test_expired_contract_refused_in_every_mode_and_today_noted():
    _load_master()
    ce = _resolve(BUY)

    async def go():
        async with _session_factory() as session:
            expired = await validate_instrument(session, symbol=ce.tradingsymbol, exchange="NFO", mode="PAPER", broker_name=None,
                                                contract=replace(ce, expiry=date(2026, 9, 1)), today=TODAY)
            today = await validate_instrument(session, symbol=ce.tradingsymbol, exchange="NFO", mode="LIVE", broker_name="fakeq",
                                              contract=replace(ce, expiry=TODAY), today=TODAY)
            fine = await validate_instrument(session, symbol=ce.tradingsymbol, exchange="NFO", mode="LIVE", broker_name="fakeq",
                                             contract=ce, today=TODAY)
            return expired, today, fine
    expired, today, fine = _run(go())
    assert expired[0] and "expired on 2026-09-01" in expired[0][0]
    assert today[0] == [] and "expires today" in today[1][0]
    assert fine == ([], [])


# --- live_margin_cap -----------------------------------------------------------------------------

def test_margin_cap_from_calculator_and_refusals():
    cap, note = _run(live_margin_cap(_Broker(per_share=500.0, available=10_000.0), symbol="RELIANCE", exchange="NSE",
                                     side=OrderSide.BUY, unit=1.0))
    assert cap == 16 and "broker margin calculator" in note   # 0.8 * 10,000 / 500

    # Lots: the cap is a whole number of lots.
    cap, _ = _run(live_margin_cap(_Broker(per_share=100.0, available=100_000.0), symbol="X", exchange="NFO", side=OrderSide.BUY, unit=75.0))
    assert cap == 750   # 80,000 / 7,500 per lot = 10 lots

    with pytest.raises(PreCheckRefusal, match="Insufficient margin"):
        _run(live_margin_cap(_Broker(per_share=500.0, available=300.0), symbol="RELIANCE", exchange="NSE", side=OrderSide.BUY, unit=1.0))
    with pytest.raises(PreCheckRefusal, match="Could not read available margin"):
        _run(live_margin_cap(_Broker(per_share=500.0, available=None), symbol="RELIANCE", exchange="NSE", side=OrderSide.BUY, unit=1.0))


def test_margin_unknown_is_noted_not_guessed_except_bought_option_premium():
    # No calculator, equity: not verifiable -> no cap, broker enforces (as before Phase Q).
    cap, note = _run(live_margin_cap(_Broker(per_share=None), symbol="RELIANCE", exchange="NSE", side=OrderSide.BUY, unit=1.0, unit_price=100.0))
    assert cap is None and "not verifiable" in note
    # Calculator errors count as unknown too - never a refusal on their own.
    cap, note = _run(live_margin_cap(_Broker(per_share=None, calculator_raises=True), symbol="RELIANCE", exchange="NSE", side=OrderSide.BUY, unit=1.0))
    assert cap is None and "not verifiable" in note
    # Bought option: premium x lot is the exact cash, so it is checked and capped.
    cap, note = _run(live_margin_cap(_Broker(per_share=None, available=30_000.0), symbol="NIFTY CE", exchange="NFO", side=OrderSide.BUY,
                                     unit=75.0, unit_price=120.0, exact_cash=True))
    assert cap == 150 and "premium x lot" in note   # 24,000 / 9,000 per lot = 2 lots
    with pytest.raises(PreCheckRefusal, match="Insufficient margin"):
        _run(live_margin_cap(_Broker(per_share=None, available=5_000.0), symbol="NIFTY CE", exchange="NFO", side=OrderSide.BUY,
                             unit=75.0, unit_price=120.0, exact_cash=True))


# --- through execute_signal_for_user -------------------------------------------------------------

def test_live_equity_entry_capped_by_margin_and_noted_on_trail(monkeypatch):
    monkeypatch.setattr(OrderRouter, "fill_poll_delay_seconds", 0)
    user = _user("q-margin@example.com")
    broker = _Broker(per_share=500.0, available=10_000.0)   # risk would allow 5,000 shares; margin allows 16
    result, order, trade, events = _execute(user, broker)
    assert result.executed, result.reasons
    assert trade.quantity == 16 and broker.placed[0].quantity == 16 and broker.placed[1].quantity == 16
    assert any("Margin 500/share" in r for r in result.reasons) and any("Size capped" in r for r in result.reasons)
    assert "PARTIAL_FILL" not in events and events[-2:] == ["FILLED", "POSITION_OPEN"]


def test_live_equity_entry_refused_when_margin_short():
    user = _user("q-short@example.com")
    result, order, trade, events = _execute(user, _Broker(per_share=500.0, available=200.0))
    assert not result.executed and order.status == "REJECTED" and trade is None
    assert "Insufficient margin" in order.reasons_json and events[-1] == "REJECTED"


def test_live_index_spot_entry_refused_before_broker():
    user = _user("q-index@example.com")
    broker = _Broker(per_share=500.0)
    result, order, trade, events = _execute(user, broker, signal=_signal(symbol="NIFTY 50", entry=24512.0, stop=24460.0))
    assert not result.executed and order.status == "REJECTED"
    assert "is an index" in order.reasons_json and broker.placed == []


def test_live_unknown_symbol_refused_when_master_synced():
    _seed_master("fakeq", ["RELIANCE"])
    user = _user("q-unknown@example.com")
    broker = _Broker(per_share=500.0)
    result, order, trade, events = _execute(user, broker, signal=_signal(symbol="NOSUCHSTOCK"))
    assert not result.executed and "instrument master" in order.reasons_json and broker.placed == []


def test_partial_fill_recorded_on_order_trail(monkeypatch):
    monkeypatch.setattr(OrderRouter, "fill_poll_delay_seconds", 0)
    user = _user("q-partial@example.com")
    broker = _Broker(per_share=None, partial=7.0)   # no margin cap; the broker fills 7 of the sized quantity
    result, order, trade, events = _execute(user, broker)
    assert result.executed and result.partial_fill and trade.quantity == 7 and result.requested_quantity > 7
    assert events[-4:] == ["PENDING", "PARTIAL_FILL", "FILLED", "POSITION_OPEN"]
    assert order.quantity == 7 and broker.placed[1].quantity == 7   # stop sized to the fill


def test_paper_entries_unchanged_by_margin_checks():
    user = _user("q-paper@example.com")
    result, order, trade, events = _execute(user, None, mode="PAPER", signal=_signal(symbol="NIFTY 50", entry=24512.0, stop=24460.0))
    assert result.executed and trade is not None and "Margin" not in " ".join(result.reasons)
