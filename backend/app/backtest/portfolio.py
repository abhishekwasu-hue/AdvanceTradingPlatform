"""N7 C3: the portfolio backtest - one strategy over several symbols on one shared book.

`run_portfolio_backtest` walks the union of the symbols' bar times in order. At each time, for the symbols that have a
bar then:
1. exits first, every open position (sorted by symbol) - the same exit logic as the single-symbol engine (the fill
   model's stop / target touch, the exit rules of Phase J1), so an exit frees its slot and its cash before any entry;
2. then entries (sorted by symbol): the strategy's signal on the windows closed by then (realism C1: no forming
   higher-timeframe bar), sized by the shared RiskManager against ONE TradingDayState - max open positions, trades per
   day, the daily loss limit and the consecutive-loss cool-down hold across the book, not per symbol;
3. the book is marked at the close of every symbol's last bar so far.
With `cash_limit` (default) an entry whose notional is over the cash not already in open positions - or over
`max_weight_pct` of the marked equity - is refused and counted (never resized; OPEN_QUESTIONS C3-2). Positions still
open at the end close at their symbol's last close ("End of backtest"), as in the single engine.

A one-symbol run with `cash_limit` off makes exactly the single engine's trades (tested), so the parts shared with it
mean the same. Fill and latency models that defer an entry (next_open, fixed_ms) and the span_like margin model are not
offered here yet (refused with the reason; OPEN_QUESTIONS C3-1); slippage models are.

Pure: no I/O, no orders - a simulation on the bars it is given. The route (flag `backtest_portfolio`) records each run
as a trial in the organisation's research ledger and deflates it (N7 C5), and holds lake windows to the sealed holdout.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field

from app.backtest.analytics import build_analytics
from app.backtest.models import ModelSet
from app.backtest.options import IST, to_utc
from app.backtest.windows import WindowCursor, decision_time
from app.core.enums import SignalDirection
from app.core.models import RiskConfig, Trade
from app.core.resampling import resample_ohlc
from app.execution.paper_broker import PaperBroker
from app.indicators.prefix_cache import register_frames, with_prefix_cache
from app.instruments.registry import get_contract_spec
from app.risk_engine.risk_manager import RiskManager, TradingDayState
from app.strategy_engine.base import BaseStrategy
from app.trading.exit_rules import ExitRules, apply_exit_rules

FLAG = "backtest_portfolio"
ENGINE_VERSION = "p1"
MAX_SYMBOLS = 50
TRADING_DAYS = 252


class PortfolioError(ValueError):
    """A portfolio run the engine does not offer (the reason is shown as is)."""


class PortfolioSettings(BaseModel):
    cash_limit: bool = Field(default=True, description="refuse an entry the book's free cash cannot pay for (no leverage)")
    max_weight_pct: float = Field(default=100.0, gt=0, le=100, description="one position's notional, % of the marked equity at entry")


class PortfolioResult(BaseModel):
    strategy_id: str
    symbols: List[str]
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0
    net_pnl: float = 0.0
    max_drawdown: float = 0.0                 # on the marked daily equity, in money
    max_drawdown_pct: float = 0.0
    sharpe: Optional[float] = None            # annualised, on the marked daily equity
    final_equity: float = 0.0
    trades: List[Trade] = Field(default_factory=list)
    per_symbol: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    daily: List[Dict[str, Any]] = Field(default_factory=list)       # [{day, equity, open_positions}]
    refused: Dict[str, int] = Field(default_factory=dict)          # entries refused by the book (cash, weight) and the risk rules
    analytics: Optional[Dict[str, Any]] = None
    exit_rules: Optional[str] = None
    settings: Dict[str, Any] = Field(default_factory=dict)
    execution: Optional[Dict[str, Any]] = None
    reproducibility: Optional[Dict[str, Any]] = None
    deflation: Optional[Dict[str, Any]] = None
    run_id: Optional[int] = None


@dataclass
class _Leg:
    """One symbol's state in the book."""
    symbol: str
    frames: Dict[str, pd.DataFrame]
    primary: pd.DataFrame
    cursor: WindowCursor
    pos: Dict[Any, int]
    min_hist: int
    spec: Any
    broker: PaperBroker
    open_trade: Optional[Trade] = None
    initial_stop: float = 0.0
    best_price: Optional[float] = None
    last_close: Optional[float] = None
    trades: List[Trade] = field(default_factory=list)


def _check_models(models: ModelSet) -> None:
    if models.fill_model().defers_entry or models.latency_model().delay() > pd.Timedelta(0):
        raise PortfolioError("execution_models: fills and latency that defer an entry (next_open, fixed_ms) are not offered for "
                             "portfolio backtests yet")
    if models.margin_model().name != "none":
        raise PortfolioError("execution_models: the span_like margin model is not offered for portfolio backtests yet")
    if models.pricing != "recorded_only" or models.settlement != "default":
        raise PortfolioError("execution_models: the pricing and settlement models apply to option backtests only")


def _same_clock(frames: Dict[str, pd.DataFrame]) -> None:
    aware = {pd.DatetimeIndex(df.index).tz is not None for df in frames.values()}
    if len(aware) > 1:
        raise PortfolioError("candles: every symbol's timestamps must carry a timezone, or none may")


@with_prefix_cache
def run_portfolio_backtest(strategy: BaseStrategy, frames_by_symbol: Dict[str, pd.DataFrame], base_tf: str, risk_config: RiskConfig,
                           exit_rules: Optional[ExitRules] = None, models: Optional[ModelSet] = None,
                           settings: Optional[PortfolioSettings] = None) -> PortfolioResult:
    settings = settings or PortfolioSettings()
    models = models or ModelSet()
    _check_models(models)
    if not frames_by_symbol:
        raise PortfolioError("at least one symbol")
    if len(frames_by_symbol) > MAX_SYMBOLS:
        raise PortfolioError(f"at most {MAX_SYMBOLS} symbols in one portfolio run")
    _same_clock(frames_by_symbol)
    primary_tf = strategy.timeframes[0]
    fill = models.fill_model()
    rules = exit_rules if exit_rules is not None and exit_rules.active else None
    legs: List[_Leg] = []
    for symbol in sorted(frames_by_symbol):
        base_df = frames_by_symbol[symbol].sort_index()
        frames = {tf: base_df if tf == base_tf else resample_ohlc(base_df, tf) for tf in strategy.timeframes}
        register_frames(frames.values())
        broker = PaperBroker()
        if not models.is_default:
            broker.slippage_model = models.slippage_model(broker.slippage_pct)
        primary = frames[primary_tf]
        legs.append(_Leg(symbol, frames, primary, WindowCursor(frames, strategy.timeframes),
                         {ts: i for i, ts in enumerate(primary.index)}, strategy.min_history()[primary_tf], get_contract_spec(symbol), broker))

    risk_manager = RiskManager(risk_config)
    state = TradingDayState()
    realised = float(risk_config.capital)
    refused: Dict[str, int] = {}
    current_day = None
    daily: List[Dict[str, Any]] = []
    timeline = sorted(set().union(*(set(leg.primary.index) for leg in legs)))

    def marked() -> float:
        out = realised
        for leg in legs:
            t = leg.open_trade
            if t is not None and leg.last_close is not None:
                sign = 1 if t.direction == SignalDirection.LONG else -1
                out += sign * (leg.last_close - t.entry_price) * t.quantity
        return out

    def in_positions() -> float:
        return sum(leg.open_trade.entry_price * leg.open_trade.quantity for leg in legs if leg.open_trade is not None)

    def close(leg: _Leg, price: float, when: Any, reason: str, counted: bool = True) -> None:
        nonlocal realised
        assert leg.open_trade is not None
        closed = leg.broker.close_trade(leg.open_trade, price, when, reason)
        leg.trades.append(closed)
        pnl = float(closed.pnl or 0.0)
        realised += pnl
        if counted:
            state.daily_pnl += pnl
            state.open_positions = max(0, state.open_positions - 1)
            state.consecutive_losses = 0 if pnl > 0 else state.consecutive_losses + 1
        leg.open_trade = None

    for ts in timeline:
        day = to_utc(ts).astimezone(IST).date()
        if day != current_day:
            if current_day is not None:
                daily.append({"day": str(current_day), "equity": round(marked(), 2), "open_positions": state.open_positions})
            current_day = day
            state.trades_today = 0
            state.daily_pnl = 0.0
        here = [(leg, leg.pos[ts]) for leg in legs if ts in leg.pos]
        # 1. exits first - the same logic as the single engine, on this bar's range
        for leg, i in here:
            bar = leg.primary.iloc[i]
            if leg.open_trade is not None and i >= leg.min_hist:
                t = leg.open_trade
                direction = "LONG" if t.direction == SignalDirection.LONG else "SHORT"
                outcome = None
                update = None
                if rules is not None:
                    bar_time = ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts
                    update = apply_exit_rules(rules, direction=direction, entry_price=t.entry_price, initial_stop=leg.initial_stop,
                                              current_stop=t.stop_loss, best_price=leg.best_price, high=float(bar["high"]),
                                              low=float(bar["low"]), entry_time=t.entry_time, now=bar_time)
                    if update.time_exit_reason:
                        outcome = (update.time_exit_reason, float(bar["close"]))
                if outcome is None:
                    outcome = fill.exit(direction, t.stop_loss, t.target1, t.target2, bar)
                if outcome is None and update is not None:
                    leg.best_price = update.best_price
                    if update.stop_changed:
                        t.stop_loss = update.stop_loss
                if outcome is not None:
                    close(leg, outcome[1], ts, outcome[0])
            leg.last_close = float(bar["close"])
        # 2. entries, on the windows closed by this bar's close
        for leg, i in here:
            if leg.open_trade is not None or i < leg.min_hist:
                continue
            bar = leg.primary.iloc[i]
            signal = strategy.analyze(leg.cursor.at(decision_time(ts, primary_tf)), leg.symbol)
            if not signal.is_tradeable:
                continue
            decision = risk_manager.validate_and_size(signal, state, contract_spec=leg.spec)
            if not decision.approved:
                key = "risk: " + (decision.reasons[0].split(" (")[0] if decision.reasons else "refused")
                refused[key] = refused.get(key, 0) + 1
                continue
            price = float(signal.entry if signal.entry is not None else bar["close"])
            notional = price * float(decision.quantity)
            if settings.cash_limit:
                equity_now = marked()
                if notional > realised - in_positions():
                    refused["cash"] = refused.get("cash", 0) + 1
                    continue
                if notional > equity_now * settings.max_weight_pct / 100.0:
                    refused["weight"] = refused.get("weight", 0) + 1
                    continue
            if not models.is_default:
                leg.broker.fill_context = {"qty": decision.quantity, "volume": float(bar["volume"]) if "volume" in bar else None}
            leg.open_trade = leg.broker.open_trade(signal, decision.quantity, ts)
            leg.open_trade.expected_price = signal.entry
            leg.initial_stop = leg.open_trade.stop_loss
            leg.best_price = None
            state.trades_today += 1
            state.open_positions += 1
    for leg in legs:                                         # the single engine's end: close at the symbol's last close
        if leg.open_trade is not None:
            close(leg, float(leg.primary.iloc[-1]["close"]), leg.primary.index[-1], "End of backtest", counted=False)
    if current_day is not None:
        daily.append({"day": str(current_day), "equity": round(marked(), 2), "open_positions": 0})
    return _result(strategy, legs, daily, refused, risk_config, rules, settings, models)


def daily_returns(daily: List[Dict[str, Any]], capital: float) -> List[float]:
    """The marked equity's day-on-day returns (the first day from the capital)."""
    out, prev = [], float(capital)
    for d in daily:
        out.append(d["equity"] / prev - 1.0 if prev else 0.0)
        prev = d["equity"]
    return out


def _result(strategy: BaseStrategy, legs: List[_Leg], daily: List[Dict[str, Any]], refused: Dict[str, int], risk: RiskConfig,
            rules: Optional[ExitRules], settings: PortfolioSettings, models: ModelSet) -> PortfolioResult:
    trades = sorted([t for leg in legs for t in leg.trades], key=lambda t: (t.exit_time, t.symbol))
    winners = [t for t in trades if t.pnl is not None and t.pnl > 0]
    net = sum(t.pnl for t in trades if t.pnl is not None)
    curve = [float(risk.capital)] + [d["equity"] for d in daily]
    peak, dd, dd_pct = curve[0], 0.0, 0.0
    for e in curve:
        peak = max(peak, e)
        dd = max(dd, peak - e)
        dd_pct = max(dd_pct, (peak - e) / peak if peak > 0 else 0.0)
    r = np.array(daily_returns(daily, risk.capital), dtype=float)
    sd = float(r.std(ddof=1)) if len(r) > 1 else 0.0
    per_symbol = {}
    for leg in legs:
        won = [t for t in leg.trades if t.pnl is not None and t.pnl > 0]
        per_symbol[leg.symbol] = {"trades": len(leg.trades), "net_pnl": round(sum(t.pnl or 0.0 for t in leg.trades), 2),
                                  "win_rate": round(len(won) / len(leg.trades) * 100, 2) if leg.trades else 0.0, "bars": len(leg.primary)}
    return PortfolioResult(
        strategy_id=strategy.id, symbols=[leg.symbol for leg in legs], total_trades=len(trades), winning_trades=len(winners),
        losing_trades=len(trades) - len(winners), win_rate=round(len(winners) / len(trades) * 100, 2) if trades else 0.0,
        net_pnl=round(net, 2), max_drawdown=round(dd, 2), max_drawdown_pct=round(dd_pct * 100, 4),
        sharpe=round(float(r.mean() / sd * math.sqrt(TRADING_DAYS)), 4) if sd > 0 else None,
        final_equity=round(curve[-1], 2), trades=trades, per_symbol=per_symbol, daily=daily, refused=dict(sorted(refused.items())),
        analytics=build_analytics(trades, curve, risk.capital), exit_rules=rules.to_json() if rules is not None else None,
        settings=settings.model_dump(), execution=None if models.is_default else {"models": models.describe()})


def frames_hash(frames_by_symbol: Dict[str, pd.DataFrame]) -> str:
    from app.backtest.repro import config_hash, data_version
    return config_hash(**{s: data_version(df) for s, df in sorted(frames_by_symbol.items())})


__all__ = ["FLAG", "ENGINE_VERSION", "MAX_SYMBOLS", "PortfolioError", "PortfolioSettings", "PortfolioResult", "run_portfolio_backtest",
           "daily_returns", "frames_hash"]
