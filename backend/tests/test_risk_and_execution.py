import pytest

from app.core.enums import ExecutionMode, SignalDirection
from app.core.models import RiskConfig, Signal
from app.execution.router import LiveTradingNotConfigured, OrderRouter
from app.risk_engine.risk_manager import RiskManager, TradingDayState


def _sample_signal(entry=100.0, sl=98.0, rr=2.0) -> Signal:
    return Signal(
        symbol="TESTSYM",
        strategy_id="test",
        strategy_name="Test",
        direction=SignalDirection.LONG,
        timestamp="2024-01-02T09:20:00",
        entry=entry,
        stop_loss=sl,
        target1=entry + rr * (entry - sl),
        target2=entry + (rr + 1) * (entry - sl),
        risk_reward=rr,
        score=85,
    )


def test_risk_manager_approves_valid_signal_and_sizes_position():
    config = RiskConfig(capital=100_000, risk_per_trade_pct=1.0, lot_size=1)
    manager = RiskManager(config)
    state = TradingDayState()
    decision = manager.validate_and_size(_sample_signal(), state)
    assert decision.approved
    # risk amount = 1000, risk per unit = 2.0 -> 500 shares
    assert decision.quantity == 500


def test_risk_manager_rejects_when_daily_loss_limit_breached():
    config = RiskConfig(capital=100_000, max_daily_loss_pct=2.0)
    manager = RiskManager(config)
    state = TradingDayState(daily_pnl=-2500.0)
    decision = manager.validate_and_size(_sample_signal(), state)
    assert not decision.approved
    assert any("Daily loss limit" in r for r in decision.reasons)


def test_risk_manager_rejects_when_max_trades_reached():
    config = RiskConfig(max_trades_per_day=3)
    manager = RiskManager(config)
    state = TradingDayState(trades_today=3)
    decision = manager.validate_and_size(_sample_signal(), state)
    assert not decision.approved


def test_risk_manager_rejects_no_trade_signal():
    config = RiskConfig()
    manager = RiskManager(config)
    state = TradingDayState()
    no_trade = Signal(
        symbol="X", strategy_id="s", strategy_name="s",
        direction=SignalDirection.NO_TRADE, timestamp="2024-01-02T09:20:00",
    )
    decision = manager.validate_and_size(no_trade, state)
    assert not decision.approved


def test_order_router_fills_paper_trade():
    router = OrderRouter(mode=ExecutionMode.PAPER, risk_config=RiskConfig(risk_per_trade_pct=1.0))
    state = TradingDayState()
    result = router.execute(_sample_signal(), state)
    assert result.executed
    assert result.trade is not None
    assert result.trade.quantity > 0
    assert state.trades_today == 1
    assert state.open_positions == 1


def test_order_router_blocks_live_mode_without_broker_adapter():
    router = OrderRouter(mode=ExecutionMode.LIVE, risk_config=RiskConfig())
    state = TradingDayState()
    with pytest.raises(LiveTradingNotConfigured):
        router.execute(_sample_signal(), state)
