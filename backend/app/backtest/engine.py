from typing import Dict, Optional

import pandas as pd

from app.core.enums import SignalDirection
from app.core.models import BacktestResult, RiskConfig, Trade
from app.core.resampling import resample_ohlc
from app.backtest.windows import WindowCursor, bar_length, decision_time
from app.indicators.prefix_cache import register_frames, with_prefix_cache
from app.execution.paper_broker import PaperBroker
from app.instruments.registry import get_contract_spec
from app.risk_engine.loss_streak import BacktestLossStreak
from app.risk_engine.risk_manager import RiskManager, TradingDayState
from app.strategy_engine.base import BaseStrategy
from app.backtest.models import ModelSet
from app.trading.exit_rules import ExitRules, apply_exit_rules
from app.backtest.analytics import build_analytics
from app.backtest.options import IST, to_utc

__all__ = ["resample_ohlc", "run_backtest"]


ENGINE_VERSION = "5"   # CP6-15: the losses-in-a-row pause ends at the next session or holds to the end (4: realism 1 closed HTF bars; 3: P0.6 daily counters, gap fills)


@with_prefix_cache
def run_backtest(
    strategy: BaseStrategy,
    base_df: pd.DataFrame,
    symbol: str,
    base_tf: str,
    risk_config: RiskConfig,
    exit_rules: Optional[ExitRules] = None,
    models: Optional[ModelSet] = None,
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
    cursor = WindowCursor(frames, strategy.timeframes)
    register_frames(frames.values())   # causal indicators on the windows: computed once per run (realism 3)
    min_hist = strategy.min_history()[primary_tf]

    # Realism C2: execution models; the default set is exactly the engine before C2 (golden test).
    models = models or ModelSet()
    broker = PaperBroker()
    if not models.is_default:
        broker.slippage_model = models.slippage_model(broker.slippage_pct)
    fill = models.fill_model()
    delay = models.latency_model().delay()
    deferred = fill.defers_entry or delay > pd.Timedelta(0)     # N7 C2b: the entry fills at a later bar's open
    bar_len = bar_length(primary_tf)
    starts = pd.DatetimeIndex(primary_df.index)
    pending: Optional[tuple] = None          # (signal, quantity, fill bar index) of an entry waiting for its bar
    margin = models.margin_model()            # N7 C2c: span_like - refuse what equity cannot margin; margin call at a close
    margin_on = margin.name != "none"
    margin_kind = _margin_kind(get_contract_spec(symbol), symbol)
    margin_refused = 0
    margin_calls = 0
    risk_manager = RiskManager(risk_config)
    state = TradingDayState()
    streak = BacktestLossStreak(risk_config.loss_streak_pause, risk_config.max_consecutive_losses)   # CP6-15
    contract_spec = get_contract_spec(symbol)

    open_trade: Trade | None = None
    trades: list[Trade] = []
    equity = risk_config.capital
    equity_curve = [equity]
    rules = exit_rules if exit_rules is not None and exit_rules.active else None
    initial_stop = 0.0
    best_price: Optional[float] = None
    current_day = None
    # Strategy templates R5 / section 5: with the user's TEMPLATE_EXIT choice the template's exit signal on a bar close of
    # an open trade closes it at the next price (the next bar's open), reason "Template exit". Off for every other strategy.
    template_exit = bool(getattr(strategy, "acts_on_exit_signal", False))
    exit_due = False

    for i in range(min_hist, len(primary_df)):
        bar = primary_df.iloc[i]
        current_time = primary_df.index[i]
        # P0.6 / B1: the risk engine's "today" counters (trades, daily P&L) are per Indian trading day, as the
        # worker keeps them; never reset, the daily loss and trades-per-day limits capped the whole backtest.
        bar_day = to_utc(current_time).astimezone(IST).date()
        if bar_day != current_day:
            current_day = bar_day
            state.trades_today = 0
            state.daily_pnl = 0.0
            # CP6-15: "Pause until the next session" starts the losses in a row again here; "until I resume" keeps them
            # (nobody resumes a test, so that pause holds to the end of the run).
            state.consecutive_losses = streak.new_session()

        if pending is not None and pending[2] == i:
            # N7 C2b: the deferred entry fills at this bar's OPEN (the only number of this bar it reads); this bar's
            # range after the open then counts for its exits, as on any later bar.
            signal, quantity, _ = pending
            pending = None
            if margin_on and margin.required(margin_kind, float(bar["open"]) * quantity) > equity:
                margin_refused += 1                     # the equity cannot margin it at the fill price: no entry
            else:
                open_trade = broker.open_trade(signal.model_copy(update={"entry": float(bar["open"])}), quantity, current_time)
                open_trade.expected_price = signal.entry
                initial_stop = open_trade.stop_loss
                best_price = None
                state.trades_today += 1
                state.open_positions += 1

        if open_trade is not None:
            direction = "LONG" if open_trade.direction == SignalDirection.LONG else "SHORT"
            outcome = None
            if exit_due:
                # R5: the exit signal came on the previous bar's close - out at this bar's open, before anything else
                outcome, exit_due = ("Template exit", float(bar["open"])), False
            if outcome is None and rules is not None:
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
                outcome = fill.exit(direction, open_trade.stop_loss, open_trade.target1, open_trade.target2, bar)
            if outcome is None and rules is not None:
                best_price = update.best_price
                if update.stop_changed:
                    open_trade.stop_loss = update.stop_loss
            if outcome is None and margin_on:
                # N7 C2c: the equity marked at this bar's close against the requirement at that close
                close_px = float(bar["close"])
                sign = 1 if open_trade.direction == SignalDirection.LONG else -1
                marked = equity + sign * (close_px - open_trade.entry_price) * open_trade.quantity
                if margin.call(marked, margin.required(margin_kind, close_px * open_trade.quantity)):
                    outcome = ("Margin call", close_px)
                    margin_calls += 1

            if outcome is None and template_exit and i + 1 < len(primary_df):
                # R5 / S10: read on this bar's close for the open trade's own side; acted on at the next bar's open
                exit_due = bool(strategy.exit_signal(cursor.at(decision_time(current_time, primary_tf)), direction))   # type: ignore[attr-defined]

            if outcome is not None:
                reason, exit_price = outcome
                closed = broker.close_trade(open_trade, exit_price, current_time, reason)
                trades.append(closed)
                equity += closed.pnl
                equity_curve.append(equity)
                state.daily_pnl += closed.pnl
                state.open_positions = max(0, state.open_positions - 1)
                state.consecutive_losses = streak.closed(closed.pnl, current_time)
                open_trade = None

        if open_trade is None and pending is None:
            window = cursor.at(decision_time(current_time, primary_tf))   # only bars that had closed (no forming HTF bar)
            signal = strategy.analyze(window, symbol)
            if signal.is_tradeable:
                decision = risk_manager.validate_and_size(signal, state, contract_spec=contract_spec)
                if decision.approved and deferred:
                    # the order reaches the market `delay` after this bar closed: the bar trading then (or the next one
                    # after a gap) fills it at its open; none left -> no entry
                    arrives = decision_time(current_time, primary_tf) + delay
                    j = int((starts + bar_len).searchsorted(arrives, side="right"))
                    if j <= i:
                        j = i + 1
                    broker.fill_context = {"qty": decision.quantity, "volume": float(bar["volume"]) if "volume" in bar else None}
                    if j < len(primary_df):
                        pending = (signal, decision.quantity, j)
                elif decision.approved and margin_on and margin.required(margin_kind, float(signal.entry or bar["close"]) * decision.quantity) > equity:
                    margin_refused += 1
                elif decision.approved:
                    if not models.is_default:
                        broker.fill_context = {"qty": decision.quantity, "volume": float(bar["volume"]) if "volume" in bar else None}
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
        execution=None if models.is_default else {"models": models.describe(), "margin_refused": margin_refused, "margin_calls": margin_calls,
                                                  "margin_kind": margin_kind if margin_on else None},
        loss_streak=streak.report(),
    )


def _margin_kind(spec, symbol: str) -> str:
    """The span_like bucket of the traded instrument: its contract spec's asset class, a futures symbol ("...FUT"), else
    cash equity (OPEN_QUESTIONS C2-6)."""
    asset = getattr(getattr(spec, "asset_class", None), "value", None)
    if str(asset) in ("COMMODITY", "CRYPTO"):
        return str(asset)
    return "FUTURES" if "FUT" in (symbol or "").upper() else "EQUITY"
