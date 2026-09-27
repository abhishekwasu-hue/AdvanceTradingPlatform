from typing import Dict, Optional

import pandas as pd

from app.core.enums import SignalDirection
from app.core.models import BacktestResult, RiskConfig, Trade
from app.core.resampling import resample_ohlc
from app.execution.paper_broker import PaperBroker
from app.instruments.registry import get_contract_spec
from app.risk_engine.risk_manager import RiskManager, TradingDayState
from app.strategy_engine.base import BaseStrategy
from app.trading.exit_logic import determine_exit_price
from app.trading.exit_rules import ExitRules, apply_exit_rules
from app.backtest.analytics import build_analytics

__all__ = ["resample_ohlc", "run_backtest"]


ENGINE_VERSION = "2"   # Phase J: exit rules + analytics


def run_backtest(
    strategy: BaseStrategy,
    base_df: pd.DataFrame,
    symbol: str,
    base_tf: str,
    risk_config: RiskConfig,
    exit_rules: Optional[ExitRules] = None,
) -> BacktestResult:
    """Event-driven backtest over historical OHLCV bars.

    Higher timeframes are resampled from base_tf data. This is a simplification: a higher-timeframe
    bar is treated as available as soon as its window closes at-or-before the current base-timeframe
    timestamp, which is safe for completed HTF bars but does not model partial-bar intrabar ticks.
    """
    frames: Dict[str, pd.DataFrame] = {}
    for tf in strategy.timeframes:
        frames[tf] = base_df if tf == base_tf else resample_ohlc(base_df, tf)

    primary_tf = strategy.timeframes[0]
    primary_df = frames[primary_tf]
    min_hist = strategy.min_history()[primary_tf]

    broker = PaperBroker()
    risk_manager = RiskManager(risk_config)
    state = TradingDayState()
    contract_spec = get_contract_spec(symbol)

    open_trade: Trade | None = None
    trades: list[Trade] = []
    equity = risk_config.capital
    equity_curve = [equity]
    rules = exit_rules if exit_rules is not None and exit_rules.active else None
    initial_stop = 0.0
    best_price: Optional[float] = None

    for i in range(min_hist, len(primary_df)):
        bar = primary_df.iloc[i]
        current_time = primary_df.index[i]

        if open_trade is not None:
            direction = "LONG" if open_trade.direction == SignalDirection.LONG else "SHORT"
            outcome = None
            if rules is not None:
                # Phase J1: the same rules the position monitor applies live, on this bar's range.
                # The stop tightened on the previous bar is what this bar is judged against, so
                # a bar cannot trail its own stop into itself.
                bar_time = current_time.to_pydatetime() if hasattr(current_time, "to_pydatetime") else current_time
                update = apply_exit_rules(
                    rules, direction=direction, entry_price=open_trade.entry_price, initial_stop=initial_stop,
                    current_stop=open_trade.stop_loss, best_price=best_price, high=float(bar["high"]), low=float(bar["low"]),
                    entry_time=open_trade.entry_time, now=bar_time,
                )
                if update.time_exit_reason:
                    outcome = (update.time_exit_reason, float(bar["close"]))
            if outcome is None:
                outcome = determine_exit_price(
                    direction, open_trade.stop_loss, open_trade.target1, open_trade.target2,
                    bar["low"], bar["high"],
                )
            if outcome is None and rules is not None:
                best_price = update.best_price
                if update.stop_changed:
                    open_trade.stop_loss = update.stop_loss

            if outcome is not None:
                reason, exit_price = outcome
                closed = broker.close_trade(open_trade, exit_price, current_time, reason)
                trades.append(closed)
                equity += closed.pnl
                equity_curve.append(equity)
                state.daily_pnl += closed.pnl
                state.open_positions = max(0, state.open_positions - 1)
                state.consecutive_losses = 0 if closed.pnl > 0 else state.consecutive_losses + 1
                open_trade = None

        if open_trade is None:
            window = {tf: frames[tf][frames[tf].index <= current_time] for tf in strategy.timeframes}
            signal = strategy.analyze(window, symbol)
            if signal.is_tradeable:
                decision = risk_manager.validate_and_size(signal, state, contract_spec=contract_spec)
                if decision.approved:
                    open_trade = broker.open_trade(signal, decision.quantity, current_time)
                    open_trade.expected_price = signal.entry
                    initial_stop = open_trade.stop_loss
                    best_price = None
                    state.trades_today += 1
                    state.open_positions += 1

    if open_trade is not None:
        last_bar = primary_df.iloc[-1]
        closed = broker.close_trade(open_trade, last_bar["close"], primary_df.index[-1], "End of backtest")
        trades.append(closed)
        equity += closed.pnl
        equity_curve.append(equity)

    winners = [t for t in trades if t.pnl is not None and t.pnl > 0]
    losers = [t for t in trades if t.pnl is not None and t.pnl <= 0]
    total_trades = len(trades)
    net_pnl = sum(t.pnl for t in trades if t.pnl is not None)
    gross_profit = sum(t.pnl for t in winners)
    gross_loss = sum(t.pnl for t in losers)

    peak = equity_curve[0]
    max_dd = 0.0
    for e in equity_curve:
        peak = max(peak, e)
        max_dd = max(max_dd, peak - e)

    return BacktestResult(
        strategy_id=strategy.id,
        symbol=symbol,
        total_trades=total_trades,
        winning_trades=len(winners),
        losing_trades=len(losers),
        win_rate=round(len(winners) / total_trades * 100, 2) if total_trades else 0.0,
        net_pnl=round(net_pnl, 2),
        gross_profit=round(gross_profit, 2),
        gross_loss=round(gross_loss, 2),
        profit_factor=round(gross_profit / abs(gross_loss), 2) if gross_loss else None,
        max_drawdown=round(max_dd, 2),
        avg_win=round(gross_profit / len(winners), 2) if winners else 0.0,
        avg_loss=round(gross_loss / len(losers), 2) if losers else 0.0,
        expectancy=round(net_pnl / total_trades, 2) if total_trades else 0.0,
        trades=trades,
        equity_curve=equity_curve,
        analytics=build_analytics(trades, equity_curve, risk_config.capital),
        exit_rules=rules.to_json() if rules is not None else None,
    )
