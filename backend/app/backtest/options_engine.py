"""Phase W: historical option backtests - the same structures a deployment trades live
(app/instruments/spreads.py), priced bar by bar (app/backtest/options.py), exited by the rules
the position monitor applies (app/trading/position_monitor.py), sized by the risk engine.

The strategy still signals on the underlying's bars. On a tradeable signal the engine:

1. picks the expiry from the synthetic listing calendar and the strikes from a ladder around
   spot with the deployment's own rules (`plan_structure`, shared with the live resolver);
2. prices every leg (recorded chain quotes or Black-Scholes) and derives the economics with
   `structure_metrics` - the credit, max loss, breakevens and the target/stop levels;
3. sizes lots exactly as `execute_structure` does: risk per trade over the risk per unit, capped
   by max_lots;
4. then, every bar: repriced legs at the close -> `structure_exit_reason`; the underlying levels
   (short strikes, breakevens) on the bar's low/high, filled at that level; time exits and the
   intraday square-off; settlement at intrinsic value on expiry.

A SINGLE option mirrors Phase F4: premium floor/ceiling on the option, stop/targets on the
underlying. One `Trade` per structure (entry/exit price per unit, P&L net of per-leg option
charges) keeps the analytics, Monte Carlo and walk-forward views working unchanged; the per-leg
detail travels in `BacktestResult.options["structures"]`.
"""
from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Dict, List, Optional, Sequence, Set

import pandas as pd

from app.backtest.analytics import build_analytics
from app.backtest.models import ModelError, ModelSet
from app.backtest.options import (
    Black76Pricer,
    ExpiryCalendar,
    OptionPricer,
    PricingUnavailable,
    SnapshotPricer,
    SyntheticPricer,
    VolatilityModel,
    bars_per_year,
    default_lot_size,
    default_strike_step,
    lot_size_for,
    strike_ladder,
    to_utc,
    underlying_name,
)
from app.backtest.windows import WindowCursor, decision_time
from app.core.enums import AssetClass, ExpiryRule, InstrumentKind, OptionPosition, OptionStrategy, OrderSide, SignalDirection, StrikeRule
from app.core.models import BacktestResult, RiskConfig, Signal, Trade
from app.core.resampling import resample_ohlc
from app.execution.paper_broker import PaperBroker
from app.execution.paper_charges import STATUTORY_ONLY, PaperCharges
from app.indicators.prefix_cache import register_frames, with_prefix_cache
from app.instruments import expiry_data
from app.instruments.contracts import (
    ContractResolutionError,
    ContractRules,
    ResolvedContract,
    option_right,
    select_strike,
)
from app.instruments.expiry_data import ExpiryDataMissing, ExpiryDataStale
from app.instruments.models import ContractSpec
from app.instruments.spreads import CustomLeg, PlannedLeg, ResolvedLeg, ResolvedStructure, can_hold_for_days, plan_structure, structure_metrics
from app.market_data.calendar import IST
from app.risk_engine.loss_streak import BacktestLossStreak
from app.risk_engine.risk_manager import RiskManager, TradingDayState, nonfinite_levels
from app.strategy_engine.base import BaseStrategy
from app.trading.exit_logic import underlying_exit
from app.trading.exit_rules import ExitRules
from app.trading.position_monitor import structure_exit_reason, underlying_exit_reason
from app.trading.trigger_rules import ABOVE, BELOW, closed_beyond

logger = logging.getLogger(__name__)

ENGINE_VERSION = "9-options"   # P1-2b: a single contract keeps the signal's reward:risk, so an organisation's own minimum filters it as live does (8: CP6-15: the losses-in-a-row pause ends at the next session or holds to the end (7: realism 1: closed HTF bars only (6: NIFTY expiries from NSE data too; 5: BANKNIFTY from NSE data; 4: P0.6 dated lots))
NO_NEW_ENTRIES_AFTER = time(15, 0)    # the worker's cut-off for intraday deployments
SQUARE_OFF_AT = time(15, 15)
SETTLEMENT_FROM = time(15, 15)        # a bar at/after this on expiry day settles the structure


@dataclass
class OptionBacktestConfig:
    """What the deployment would carry (ContractRulesRequest fields) plus the simulation's own
    knobs: contract conventions, the pricing model and the intraday behaviour."""
    option_strategy: OptionStrategy = OptionStrategy.SINGLE
    option_position: OptionPosition = OptionPosition.BUY      # SINGLE only
    expiry_rule: ExpiryRule = ExpiryRule.NEAREST
    strike_rule: StrikeRule = StrikeRule.ATM
    strike_offset: int = 0
    spread_width: int = 2
    target_credit_pct: Optional[float] = None
    stop_credit_pct: Optional[float] = None
    underlying_breach_exit: bool = False                      # DEF-0: the trader's own underlying-level exit (off by default)
    premium_stop_pct: Optional[float] = None                  # SINGLE: floor (bought) / ceiling (written)
    custom_legs: List[CustomLeg] = field(default_factory=list)
    max_lots: Optional[int] = None
    # Conventions (None = the exchange's current one for the underlying).
    lot_size: Optional[int] = None
    strike_step: Optional[float] = None
    expiry_weekday: Optional[int] = None                      # 0 = Monday ... 4 = Friday
    weekly_expiry: Optional[bool] = None
    holidays: Set[date] = field(default_factory=set)
    # Pricing (synthetic): fixed IV as a fraction (0.14 = 14%), else realised vol of the trailing closes.
    implied_volatility: Optional[float] = None
    realised_vol_window: int = 20
    risk_free_rate: float = 0.07
    # Intraday behaviour, as the worker enforces it for deployments; False holds to exit/expiry.
    intraday: bool = True
    # M55 T1: the deployment's "Hold for days" (holding SWING) on a defined-risk credit structure - no square-off at the
    # close (it exits by its own rules or settles at expiry); the entry cut-off stays, as in the worker. Ignored otherwise.
    hold_for_days: bool = False
    # Strategy templates section 5: a single option's stop source - None / TEMPLATE_INVALIDATION (the strategy's stop on
    # the underlying, as before) or PREMIUM_PCT (only the premium floor / ceiling; no underlying-level stop).
    stop_source: Optional[str] = None

    @property
    def is_single(self) -> bool:
        return self.option_strategy == OptionStrategy.SINGLE

    def rules(self) -> ContractRules:
        position = self.option_position if self.is_single else OptionPosition.WRITE
        stop_pct = self.premium_stop_pct          # DEF-0 D3: None = no premium stop
        return ContractRules(kind=InstrumentKind.OPTION, position=position, expiry_rule=self.expiry_rule, strike_rule=self.strike_rule,
                             strike_offset=self.strike_offset, premium_stop_pct=stop_pct, max_lots=self.max_lots)


@dataclass
class _Leg:
    right: str
    role: str                 # SHORT / LONG
    strike: float
    expiry: date
    ratio: int
    entry_price: float        # filled premium per unit
    quantity: float           # units (lots x lot x ratio)
    exit_price: Optional[float] = None

    @property
    def direction(self) -> SignalDirection:
        return SignalDirection.SHORT if self.role == "SHORT" else SignalDirection.LONG

    def as_dict(self) -> dict:
        return {"right": self.right, "role": self.role, "strike": self.strike, "expiry": self.expiry.isoformat(), "ratio": self.ratio,
                "quantity": self.quantity, "entry_price": self.entry_price, "exit_price": self.exit_price}


@dataclass
class _Open:
    label: str
    legs: List[_Leg]
    meta: dict                          # group_meta as the live executor writes it (SINGLE: premium levels)
    lots: int
    quantity: float                     # the 1x leg's units
    entry_time: datetime                # UTC
    entry_time_bar: object              # the bar index value, for the Trade
    near_expiry: date
    direction: SignalDirection          # SHORT = credit received, LONG = debit paid
    entry_unit: float                   # |net credit| (or the single premium) at the fills
    single: bool = False
    # SINGLE: the underlying levels the strategy set and the premium safety net.
    underlying_direction: Optional[str] = None
    underlying_stop: Optional[float] = None
    underlying_t1: Optional[float] = None
    underlying_t2: Optional[float] = None
    premium_level: Optional[float] = None
    # Strategy templates R5: the signal's (underlying) direction, for the template's exit signal
    signal_direction: Optional[str] = None


class OptionBacktestError(ValueError):
    """The configuration cannot be simulated at all (nothing to do with one bar)."""


def _resolved_structure(strategy: OptionStrategy, planned: Sequence[PlannedLeg], *, underlying: str, underlying_symbol: str,
                        near: date, far: Optional[date], lot: int) -> ResolvedStructure:
    """A ResolvedStructure with synthetic contracts so `structure_metrics` runs unchanged."""
    legs: List[ResolvedLeg] = []
    for leg in planned:
        expiry = far if leg.far_expiry else near
        assert expiry is not None
        symbol = f"{underlying}{expiry.strftime('%d%b%y').upper()}{int(leg.strike)}{leg.right}"
        long = leg.role == "LONG"
        contract = ResolvedContract(
            kind=InstrumentKind.OPTION, underlying=underlying, underlying_symbol=underlying_symbol, tradingsymbol=symbol,
            exchange="NFO", instrument_key=f"BACKTEST|{symbol}", lot_size=lot, tick_size=0.05, expiry=expiry, strike=leg.strike,
            right=leg.right, entry_side=OrderSide.BUY if long else OrderSide.SELL,
            trade_direction=SignalDirection.LONG if long else SignalDirection.SHORT,
            position=OptionPosition.BUY if long else OptionPosition.WRITE,
        )
        legs.append(ResolvedLeg(leg.role, contract, leg.ratio))
    # Width as the live resolver measures it: per right, the distance from the primary strike to
    # the wing/short leg (half the span for a butterfly's two wings); a custom set, the whole span.
    width = 0.0
    if strategy == OptionStrategy.CUSTOM:
        strikes = [l.strike for l in planned]
        width = max(strikes) - min(strikes) if len(strikes) > 1 else 0.0
    elif strategy != OptionStrategy.CALENDAR_SPREAD:
        for right in {l.right for l in planned}:
            strikes = [l.strike for l in planned if l.right == right]
            span = max(strikes) - min(strikes)
            width = max(width, span / 2 if strategy == OptionStrategy.LONG_BUTTERFLY else span)
    return ResolvedStructure(strategy=strategy, legs=legs, underlying_symbol=underlying_symbol, lot_size=lot, expiry=near,
                             width_points=round(width, 2), notes=[])


@with_prefix_cache
def run_option_backtest(
    strategy: BaseStrategy,
    base_df: pd.DataFrame,
    symbol: str,
    base_tf: str,
    risk_config: RiskConfig,
    config: OptionBacktestConfig,
    exit_rules: Optional[ExitRules] = None,
    pricer: Optional[OptionPricer] = None,
    models: Optional[ModelSet] = None,
    charges: Optional[PaperCharges] = None,
) -> BacktestResult:
    """`charges` (M68 PR-7): the trader's own paper charges (None = statutory charges only); see app.backtest.engine."""
    frames: Dict[str, pd.DataFrame] = {}
    for tf in strategy.timeframes:
        frames[tf] = base_df if tf == base_tf else resample_ohlc(base_df, tf)
    primary_tf = strategy.timeframes[0]
    primary_df = frames[primary_tf]
    cursor = WindowCursor(frames, strategy.timeframes)
    register_frames(frames.values())   # causal indicators on the windows: computed once per run (realism 3)
    min_hist = strategy.min_history()[primary_tf]
    closes = primary_df["close"].astype(float).tolist()
    is_daily = bars_per_year(primary_tf) == 250.0

    underlying, underlying_symbol = underlying_name(symbol)
    # P0.6 / B5: the lot size is the exchange's lot for the contract traded (lot_size_for: by expiry where the dated
    # table has one, else by entry day), unless the run pins one.
    lot = int(config.lot_size or default_lot_size(underlying))
    lots_used: set = set()
    expiries_used: set = set()            # the report's calendar label comes from the expiries actually traded
    vol = VolatilityModel(fixed_iv=config.implied_volatility, window=config.realised_vol_window, annualisation=bars_per_year(primary_tf))
    synthetic = SyntheticPricer(vol, config.risk_free_rate)
    if pricer is None:
        pricer = synthetic
    # A snapshot pricer's fallback (if any) shares this run's volatility model.
    if isinstance(pricer, SnapshotPricer) and pricer.fallback is not None:
        pricer.fallback.vol = vol
    calendar = ExpiryCalendar.for_underlying(underlying, config.holidays, weekday=config.expiry_weekday, weekly=config.weekly_expiry)
    rules = config.rules()
    held_for_days = config.hold_for_days and can_hold_for_days(config.option_strategy)   # M55 T1: credit structures only
    if config.option_strategy == OptionStrategy.CUSTOM and not config.custom_legs:
        raise OptionBacktestError("CUSTOM structure has no legs")

    # N7 C2c: execution models - slippage (fixed_pct / spread_half), span_like margin, black76 for missing strikes. A
    # fill or latency model, or a volume-based slippage, has nothing to act on here (premiums are priced, not traded
    # bars): refused, never ignored. The default set is exactly the engine before (golden test).
    models = models or ModelSet()
    if models.fill != "touch" or models.latency != "zero" or models.slippage == "volume_share":
        raise ModelError("fill / latency models and volume_share slippage apply to single-leg backtests only")
    broker = (charges or STATUTORY_ONLY).broker()
    if not models.is_default:
        broker.slippage_model = models.slippage_model(broker.slippage_pct)
    margin = models.margin_model()
    margin_on = margin.name != "none"
    margin_refused = 0
    margin_calls = 0
    b76: Optional[Black76Pricer] = None
    if models.pricing == "black76":
        if not isinstance(pricer, SnapshotPricer):
            raise ModelError("black76 prices strikes missing from a recorded or uploaded chain; this run has no chain (synthetic pricing)")
        b76 = Black76Pricer(pricer.rows, vol, config.risk_free_rate, **{"max_age_minutes": pricer.max_age_minutes, **models.pricing_params()})
        pricer.fallback = b76  # type: ignore[assignment]
    # DEF-0 / M68 PR-7 (TS1-066): a backtest's starting equity is the trader's own capital (the caller passes his capital,
    # else his paper account's - app/paper/paper_settings.py capital_of); none set: the run starts from 0 and every figure
    # measured as a share of the capital is left out - never a capital ATP chose.
    capital = float(risk_config.capital or 0.0)
    risk_manager = RiskManager(risk_config)
    state = TradingDayState()
    streak = BacktestLossStreak(risk_config.loss_streak_pause, risk_config.max_consecutive_losses)   # CP6-15
    time_rules = exit_rules if exit_rules is not None and exit_rules.active else None
    exit_at = time_rules.exit_time_of_day() if time_rules else None
    # Strategy templates R5 / section 5: the template's exit signal (the user's TEMPLATE_EXIT choice), read on a bar close
    # of an open position for the signal's own side, closes it at the next bar's open, reason "Template exit".
    template_exit = bool(getattr(strategy, "acts_on_exit_signal", False))
    exit_due = False

    open_pos: Optional[_Open] = None
    trades: List[Trade] = []
    structures: List[dict] = []
    skipped: Counter = Counter()
    equity = capital
    equity_curve = [equity]
    settlements = 0
    current_day: Optional[date] = None
    step_used: Optional[float] = None

    def close_position(pos: _Open, exit_prices: Dict[int, float], when, reason: str) -> None:
        nonlocal equity, settlements, open_pos
        gross = 0.0
        charges = 0.0
        settled = reason.startswith("Expiry")
        for idx, leg in enumerate(pos.legs):
            price = exit_prices[idx]
            leg.exit_price = price
            sign = -1 if leg.role == "SHORT" else 1
            gross += sign * (price - leg.entry_price) * leg.quantity
            # P0.6 / B2: a written leg sold first (STT on its entry premium). A leg of the expiring series is settled, not
            # traded: entry-side charges only, and a bought leg in the money is exercised (STT on the intrinsic value
            # instead of a sell-side premium STT). A far-expiry leg (calendar spread) is closed at market as usual.
            if settled and leg.expiry == pos.near_expiry:
                charges += broker.estimate_round_trip_costs(leg.entry_price, 0.0, leg.quantity, "OPTION", sold_first=leg.role == "SHORT", settled=True,
                                                            entry_date=pos.entry_time, exit_date=when)
                if leg.role == "LONG":
                    charges += broker.exercise_charges(price, leg.quantity, trade_date=when)
            else:
                charges += broker.estimate_round_trip_costs(leg.entry_price, price, leg.quantity, "OPTION", sold_first=leg.role == "SHORT",
                                                            entry_date=pos.entry_time, exit_date=when)
        value = sum((leg.exit_price if leg.role == "SHORT" else -leg.exit_price) * leg.ratio for leg in pos.legs)
        exit_unit = value if pos.direction == SignalDirection.SHORT else -value
        pnl = round(gross - charges, 2)
        trade = Trade(symbol=pos.label, strategy_id=strategy.id, direction=pos.direction, entry_time=pos.entry_time_bar,
                      entry_price=round(pos.entry_unit, 2), quantity=pos.quantity, stop_loss=float(pos.meta.get("stop_value") or pos.premium_level or 0.0),
                      exit_time=when, exit_price=round(exit_unit, 2), exit_reason=reason, pnl=pnl, charges=round(charges, 2))
        trades.append(trade)
        equity += pnl
        equity_curve.append(equity)
        state.daily_pnl += pnl
        state.open_positions = max(0, state.open_positions - 1)
        state.consecutive_losses = streak.closed(pnl, when)
        state.last_loss_at = streak.last_loss_at
        if reason.startswith("Expiry"):
            settlements += 1
        structures.append({
            "label": pos.label, "entry_time": pd.Timestamp(pos.entry_time_bar).isoformat(), "exit_time": pd.Timestamp(when).isoformat(),
            "expiry": pos.near_expiry.isoformat(), "lots": pos.lots, "direction": pos.direction.value, "entry_unit": round(pos.entry_unit, 2),
            "exit_unit": round(exit_unit, 2), "exit_reason": reason, "pnl": pnl, "charges": round(charges, 2),
            "net_credit": pos.meta.get("net_credit"), "max_loss": pos.meta.get("max_loss"), "max_profit": pos.meta.get("max_profit"),
            "breakevens": pos.meta.get("breakevens"), "legs": [l.as_dict() for l in pos.legs],
        })
        open_pos = None

    def leg_prices(pos: _Open, spot: float, at: datetime, *, settle: bool = False) -> Dict[int, float]:
        out: Dict[int, float] = {}
        for idx, leg in enumerate(pos.legs):
            if settle and leg.expiry == pos.near_expiry:
                intrinsic = max(spot - leg.strike, 0.0) if leg.right == "CE" else max(leg.strike - spot, 0.0)
                out[idx] = round(intrinsic, 2)
            else:
                out[idx] = pricer.price(leg.right, leg.strike, leg.expiry, spot=spot, at=at)
        return out

    def structure_value(prices: Dict[int, float], pos: _Open) -> float:
        return sum((prices[i] if leg.role == "SHORT" else -prices[i]) * leg.ratio for i, leg in enumerate(pos.legs))

    def margin_required(pos: _Open, spot: float) -> float:
        """N7 C2c: span_like on a structure - its defined maximum loss, or the short legs' underlying notional x the
        short-option %; bought options are paid for (no extra margin)."""
        shorts = [leg for leg in pos.legs if leg.role == "SHORT"]
        if not shorts:
            return margin.required("OPTION_LONG", 0.0)
        max_loss = pos.meta.get("max_loss")
        if not pos.single and max_loss is not None:
            return margin.required("OPTION_DEFINED", 0.0, max_loss=float(max_loss) * pos.quantity)
        return margin.required("OPTION_SHORT", spot * sum(leg.quantity for leg in shorts))

    def unrealised(pos: _Open, prices: Dict[int, float]) -> float:
        return sum((leg.entry_price - prices[i] if leg.role == "SHORT" else prices[i] - leg.entry_price) * leg.quantity
                   for i, leg in enumerate(pos.legs))

    for i in range(min_hist, len(primary_df)):
        bar = primary_df.iloc[i]
        ts = primary_df.index[i]
        at = to_utc(ts)
        at_ist = at.astimezone(IST)
        bar_day = at_ist.date()
        if bar_day != current_day:
            current_day = bar_day
            state.daily_pnl = 0.0
            state.trades_today = 0
            state.consecutive_losses = streak.new_session()   # CP6-15: as app/backtest/engine.py
        vol.update(closes[max(0, i - config.realised_vol_window): i + 1])
        high, low, close = float(bar["high"]), float(bar["low"]), float(bar["close"])

        if open_pos is not None:
            pos = open_pos
            reason: Optional[str] = None
            exit_spot = close
            settle = False
            try:
                # 1. Expiry: the near legs settle at intrinsic value on expiry day's last bars (or
                #    on a later bar when the data skips the close).
                past_close = bar_day > pos.near_expiry or (bar_day == pos.near_expiry and (is_daily or at_ist.time() >= SETTLEMENT_FROM))
                if past_close:
                    reason, settle = f"Expiry settlement ({pos.near_expiry.isoformat()})", True
                    exit_spot = float(bar["open"]) if bar_day > pos.near_expiry else close
                # R5: the template's exit signal on the previous bar's close - out at this bar's open
                if reason is None and exit_due:
                    reason, exit_spot = "Template exit", float(bar["open"])
                exit_due = False
                # 2. Underlying levels on the bar's range, filled at the level itself.
                if reason is None:
                    if pos.single:
                        hit_low = underlying_exit(pos.underlying_direction, pos.underlying_stop, pos.underlying_t1, pos.underlying_t2, low)
                        hit_high = underlying_exit(pos.underlying_direction, pos.underlying_stop, pos.underlying_t1, pos.underlying_t2, high)
                        # A bar that crosses both is resolved the pessimistic way: the stop first.
                        for hit, level in ((hit_low, low), (hit_high, high)):
                            if hit == "Stop Loss":
                                reason, exit_spot = f"{hit} (underlying)", pos.underlying_stop
                                break
                        if reason is None:
                            for hit, level in ((hit_high, high), (hit_low, low)):
                                if hit is not None:
                                    reason = f"{hit} (underlying)"
                                    exit_spot = pos.underlying_t2 if hit == "Target 2" else pos.underlying_t1
                                    break
                    else:
                        for level in (low, high):
                            hit = underlying_exit_reason(pos.meta, level)
                            if hit is not None:
                                reason, exit_spot = hit, _breach_level(pos.meta, level)
                                break
                # 3. Time rules and the intraday square-off, at the close - after the levels (REV-S2: a stop inside this bar
                #    exits at the stop, not at the close).
                if reason is None and time_rules is not None and time_rules.time_exit_minutes and at - pos.entry_time >= timedelta(minutes=time_rules.time_exit_minutes):
                    reason = f"Time exit ({time_rules.time_exit_minutes}m in trade)"
                if reason is None and exit_at is not None and at_ist.time() >= exit_at:
                    reason = f"Time exit (flat at {time_rules.time_exit_at} IST)"
                if reason is None and config.intraday and not is_daily and not held_for_days and at_ist.time() >= SQUARE_OFF_AT:
                    reason = f"Square-off ({SQUARE_OFF_AT.strftime('%H:%M')} IST)"
                # 4. The structure's own value at the close.
                prices = leg_prices(pos, exit_spot, at, settle=settle)
                if reason is None:
                    if pos.single:
                        price = prices[0]
                        leg = pos.legs[0]
                        # TT-7: the shared close rule (app/trading/trigger_rules.py) - the premium at this bar's close.
                        if pos.premium_level is None:
                            pass                                  # DEF-0: no premium stop set
                        elif leg.role == "LONG" and closed_beyond(pos.premium_level, BELOW, price):
                            reason = f"Premium floor ({pos.premium_level:g})"
                        elif leg.role == "SHORT" and closed_beyond(pos.premium_level, ABOVE, price):
                            reason = f"Premium ceiling ({pos.premium_level:g})"
                    else:
                        reason = structure_exit_reason(pos.meta, structure_value(prices, pos), None)
                if reason is None and margin_on and margin.call(equity + unrealised(pos, prices), margin_required(pos, exit_spot)):
                    reason = "Margin call"                  # N7 C2c: marked at this bar's close against the requirement then
                    margin_calls += 1
                if reason is None and template_exit and pos.signal_direction and i + 1 < len(primary_df):
                    window_now = cursor.at(decision_time(ts, primary_tf))
                    exit_due = bool(strategy.exit_signal(window_now, pos.signal_direction))   # type: ignore[attr-defined]
                if reason is not None:
                    fills = {idx: (p if settle else round(broker._slip(p, SignalDirection.LONG if pos.legs[idx].role == "SHORT" else SignalDirection.SHORT), 2))
                             for idx, p in prices.items()}
                    close_position(pos, fills, ts, reason)
            except PricingUnavailable as exc:
                skipped[f"open position not repriced: {exc}"] += 1

        if open_pos is None:
            if config.intraday and not is_daily and at_ist.time() >= NO_NEW_ENTRIES_AFTER:
                continue
            window = cursor.at(decision_time(ts, primary_tf))   # only bars that had closed (no forming HTF bar)
            signal = strategy.analyze(window, symbol)
            if not signal.is_tradeable:
                continue
            spot = close
            step = config.strike_step or default_strike_step(underlying, spot)
            step_used = step
            ladder = strike_ladder(spot, step)
            try:
                expiry = calendar.select(config.expiry_rule, bar_day)
            except ExpiryDataStale:
                # The file is out of date for this bar: no trade, counted, said in the result - never a guessed expiry.
                skipped[f"stale expiry data (read through {expiry_data.stale_after()})"] += 1
                continue
            except ExpiryDataMissing as exc:
                raise OptionBacktestError(str(exc)) from exc
            if expiry is None:
                skipped["no expiry"] += 1
                continue
            if expiry == bar_day and at_ist.time() >= SETTLEMENT_FROM and not is_daily:
                skipped["expiry day too late to enter"] += 1
                continue
            lot = int(config.lot_size or lot_size_for(underlying, bar_day, expiry))
            expiries_used.add(expiry)
            lots_used.add(lot)
            try:
                if config.is_single:
                    open_pos = _open_single(signal, rules, config, spot=spot, ladder=ladder, expiry=expiry, at=at, ts=ts, lot=lot,
                                            underlying=underlying, pricer=pricer, risk_manager=risk_manager, state=state, broker=broker)
                else:
                    open_pos = _open_structure(signal, rules, config, spot=spot, ladder=ladder, expiry=expiry, calendar=calendar, at=at, ts=ts,
                                               lot=lot, underlying=underlying, underlying_symbol=underlying_symbol, pricer=pricer,
                                               risk_manager=risk_manager, state=state, broker=broker)
            except (ContractResolutionError, PricingUnavailable, _Skip) as exc:
                skipped[_skip_key(str(exc))] += 1
                continue
            if margin_on and open_pos is not None and margin_required(open_pos, spot) > equity:
                open_pos = None                             # N7 C2c: the equity cannot margin it - no entry, counted
                margin_refused += 1
                skipped["margin: equity below the span_like requirement"] += 1
                continue
            if open_pos is not None:
                open_pos.signal_direction = signal.direction.value
            state.trades_today += 1
            state.open_positions += 1

    if open_pos is not None:
        last = primary_df.iloc[-1]
        try:
            prices = leg_prices(open_pos, float(last["close"]), to_utc(primary_df.index[-1]))
            close_position(open_pos, prices, primary_df.index[-1], "End of backtest")
        except PricingUnavailable:
            # No quote to close on: the position is dropped from the tally and said so.
            skipped["open position at end without a quote"] += 1

    winners = [t for t in trades if t.pnl is not None and t.pnl > 0]
    losers = [t for t in trades if t.pnl is not None and t.pnl <= 0]
    total = len(trades)
    net_pnl = sum(t.pnl for t in trades if t.pnl is not None)
    gross_profit = sum(t.pnl for t in winners)
    gross_loss = sum(t.pnl for t in losers)
    peak, max_dd = equity_curve[0], 0.0
    for e in equity_curve:
        peak = max(peak, e)
        max_dd = max(max_dd, peak - e)
    options = {
        "engine_version": ENGINE_VERSION, "pricing_model": pricer.name, "pricing": pricer.describe(), "underlying": underlying,
        "structure": config.option_strategy.value, "position": config.option_position.value if config.is_single else None,
        "lot_size": lot if len(lots_used) <= 1 else sorted(lots_used), "strike_step": step_used or config.strike_step or default_strike_step(underlying, float(primary_df["close"].iloc[-1])),
        "expiry_calendar": (f"NSE listed expiries ({'all listed' if calendar.weekly else 'monthly only'}; data through "
                            f"{expiry_data.stale_after()}), " if calendar.data_symbol
                            else f"{'weekly' if calendar.weekly else 'monthly'}, ")
                           + "/".join(sorted({["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][e.weekday()] for e in expiries_used},
                                             key=["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].index)
                                      or [["Mon", "Tue", "Wed", "Thu", "Fri"][calendar.weekday]]),
        "intraday": config.intraday, **({"held_for_days": True} if held_for_days else {}), "structures_opened": len(structures), "expiry_settlements": settlements,
        "signals_skipped": dict(skipped), "structures": structures,
        "disclaimer": ("Synthetic premiums (Black-Scholes) approximate what the market would have quoted; they carry no smile, "
                       "no bid/ask and no liquidity. Use this to study structure mechanics, not to claim an edge.")
        if pricer.name == "synthetic" else "Premiums from recorded chain quotes; legs without a fresh quote were priced synthetically if a fallback was allowed.",
    }
    if isinstance(pricer, SnapshotPricer):
        options.update({"snapshot_hits": pricer.hits, "synthetic_fallbacks": pricer.fallbacks, "snapshot_contracts": pricer.contracts})
    if b76 is not None:
        # N7 C2c: every model price flagged - the count, the contracts, and per leg whether any of its prices was a model's
        priced = {f"{int(k[1])} {k[2]} {k[0]}" for k in b76.priced}
        options.update({"model_prices": b76.count, "model_priced_contracts": sorted(priced)[:100], "model_pricing": b76.describe()})
        for st in structures:
            for leg in st["legs"]:
                leg["model_priced"] = f"{int(leg['strike'])} {leg['right']} {leg['expiry']}" in priced
    return BacktestResult(
        strategy_id=strategy.id, symbol=symbol, total_trades=total, winning_trades=len(winners), losing_trades=len(losers),
        win_rate=round(len(winners) / total * 100, 2) if total else 0.0, net_pnl=round(net_pnl, 2),
        gross_profit=round(gross_profit, 2), gross_loss=round(gross_loss, 2),
        profit_factor=round(gross_profit / abs(gross_loss), 2) if gross_loss else None, max_drawdown=round(max_dd, 2),
        avg_win=round(gross_profit / len(winners), 2) if winners else 0.0, avg_loss=round(gross_loss / len(losers), 2) if losers else 0.0,
        expectancy=round(net_pnl / total, 2) if total else 0.0, trades=trades, equity_curve=equity_curve,
        analytics=build_analytics(trades, equity_curve, capital),
        exit_rules=time_rules.to_json() if time_rules is not None else None, options=options,
        execution=None if models.is_default else {"models": models.describe(), "margin_refused": margin_refused, "margin_calls": margin_calls,
                                                  "model_prices": b76.count if b76 is not None else 0},
        loss_streak=streak.report(),
    )


class _Skip(Exception):
    """A signal the engine does not trade, with the reason counted."""


def _skip_key(text: str) -> str:
    """Skip reasons are counted by kind, so numbers in the message don't fragment the tally."""
    for marker in ("Risk per lot", "would be a net", "cannot profit", "shows no loss", "not entered on", "No premium quote",
                   "no recorded quote", "No CE strike", "No PE strike", "no expiry", "no listed far expiry"):
        if marker.lower() in text.lower():
            return marker.lower()
    return text[:80]


def _breach_level(meta: dict, level: float) -> float:
    """The underlying level that fired (short strike or breakeven) - the fill is at the level,
    not at the bar's extreme."""
    exits = meta.get("underlying_exits") or {}
    candidates = [v for v in exits.values()] + [float(s) for s in (meta.get("short_strikes") or {}).values()]
    if not candidates:
        return level
    return min(candidates, key=lambda c: abs(c - level))


def _size_lots(risk_manager: RiskManager, state: TradingDayState, signal: Signal, *, basis: float, risk_per_unit: float, lot: int,
               underlying_symbol: str, max_lots: Optional[int], direction: SignalDirection = SignalDirection.SHORT,
               keep_rr: bool = False) -> int:
    """P1-2b (TT12-4) parity: a single contract keeps the strategy's reward:risk, as the live / paper contract order does
    (app/execution/contract_execution.py copies the signal), so an organisation's own minimum filters both alike; a
    multi-leg structure has none, as live (app/execution/multileg.py). With no minimum set nothing is filtered."""
    spec = ContractSpec(symbol=underlying_symbol, exchange="NFO", asset_class=AssetClass.INDEX_OPTION, description="option backtest",
                        lot_size=float(lot), tick_size=0.05)
    sizing = signal.model_copy(update={"symbol": underlying_symbol, "direction": direction, "entry": basis,
                                       # M64: a short with no stop (no limit to its loss) is the "no stop" 0, as live
                                       "stop_loss": ((round(basis + risk_per_unit, 2) if risk_per_unit > 0 else 0.0)
                                                     if direction == SignalDirection.SHORT else round(basis - risk_per_unit, 2)),
                                       "target1": None, "target2": None, "risk_reward": signal.risk_reward if keep_rr else None})
    # DEF-0: with no risk per trade set the size is the trader's max lots (else one lot), as live
    decision = risk_manager.validate_and_size(sizing, state, contract_spec=spec, requested_quantity=float(max_lots * lot) if max_lots else None)
    if not decision.approved:
        raise _Skip("; ".join(decision.reasons) or "risk engine refused")
    lots = int(decision.quantity // lot)
    if lots < 1:
        raise _Skip(f"Risk per lot ({risk_per_unit * lot:,.0f}) exceeds risk per trade")
    if max_lots:
        lots = min(lots, max_lots)
    return lots


def _open_structure(signal: Signal, rules: ContractRules, config: OptionBacktestConfig, *, spot: float, ladder: List[float], expiry: date,
                    calendar: ExpiryCalendar, at: datetime, ts, lot: int, underlying: str, underlying_symbol: str, pricer: OptionPricer,
                    risk_manager: RiskManager, state: TradingDayState, broker: PaperBroker) -> _Open:
    strategy = config.option_strategy
    planned = plan_structure(strategy, signal.direction, rules, spot=spot, strikes_by_right={"CE": ladder, "PE": ladder},
                             spread_width=config.spread_width, custom_legs=config.custom_legs)
    try:
        far = calendar.far_expiry(expiry, as_of=at.astimezone(IST).date()) if any(l.far_expiry for l in planned) else None
    except (ExpiryDataMissing, StopIteration) as exc:
        raise _Skip(f"no listed far expiry after {expiry}") from exc
    structure = _resolved_structure(strategy, planned, underlying=underlying, underlying_symbol=underlying_symbol, near=expiry, far=far, lot=lot)
    premiums = {leg.contract.tradingsymbol: pricer.price(leg.contract.right, leg.contract.strike, leg.contract.expiry, spot=spot, at=at)
                for leg in structure.legs}
    metrics = structure_metrics(structure, premiums, target_credit_pct=config.target_credit_pct, stop_credit_pct=config.stop_credit_pct,
                                underlying_exit=config.underlying_breach_exit)
    basis = abs(metrics.net_credit) or 0.05
    lots = _size_lots(risk_manager, state, signal, basis=basis, risk_per_unit=metrics.risk_per_unit, lot=lot,
                      underlying_symbol=underlying_symbol, max_lots=config.max_lots)
    quantity = lots * lot
    legs: List[_Leg] = []
    credit = 0.0
    for leg in structure.legs:
        quote = premiums[leg.contract.tradingsymbol]
        fill = round(broker._slip(quote, leg.contract.trade_direction), 2)
        credit += (fill if leg.role == "SHORT" else -fill) * leg.ratio
        legs.append(_Leg(leg.contract.right, leg.role, float(leg.contract.strike), leg.contract.expiry, leg.ratio, fill, quantity * leg.ratio))
    meta = metrics.as_dict()
    meta.update({"lots": lots, "quantity": quantity, "underlying_symbol": underlying_symbol})
    strikes = "/".join(f"{int(l.strike)}{l.right}" for l in legs)
    label = f"{underlying} {strategy.value} {strikes} {expiry.isoformat()}"
    return _Open(label=label, legs=legs, meta=meta, lots=lots, quantity=quantity, entry_time=at, entry_time_bar=ts, near_expiry=expiry,
                 direction=SignalDirection.SHORT if credit >= 0 else SignalDirection.LONG, entry_unit=abs(credit))


def _open_single(signal: Signal, rules: ContractRules, config: OptionBacktestConfig, *, spot: float, ladder: List[float], expiry: date,
                 at: datetime, ts, lot: int, underlying: str, pricer: OptionPricer, risk_manager: RiskManager, state: TradingDayState,
                 broker: PaperBroker) -> _Open:
    if signal.entry is None or signal.stop_loss is None:
        raise _Skip("signal has no entry/stop to derive option levels from")
    bad = nonfinite_levels(signal)
    if bad:
        raise _Skip(f"signal {bad} is not a finite number")
    position = rules.position or OptionPosition.BUY
    right = option_right(signal.direction, position)
    strike = select_strike(ladder, spot, right, rules.strike_rule, rules.strike_offset)
    if strike is None:
        raise ContractResolutionError(f"No {right} strike for the rule")
    quote = pricer.price(right, strike, expiry, spot=spot, at=at)
    # DEF-0 D3: the premium stop is the trader's own (None = no premium exit), as live (contract_execution)
    pct = rules.premium_stop_pct / 100.0 if rules.premium_stop_pct is not None else None
    bought = position == OptionPosition.BUY
    trade_direction = SignalDirection.LONG if bought else SignalDirection.SHORT
    fill = round(broker._slip(quote, trade_direction), 2)
    level = (round(fill * (1 - pct), 2) if bought else round(fill * (1 + pct), 2)) if pct is not None else None
    max_lots = config.max_lots if config.max_lots else None
    per_unit = round(fill * pct, 2) if pct is not None else (fill if bought else 0.0)
    # M64 RM-2: as live - a written option with no stop has no limit to its loss; risk-based sizing falls back to the
    # trader's own lots (RiskManager's warning), never a refusal.
    lots = _size_lots(risk_manager, state, signal, basis=fill, risk_per_unit=per_unit, lot=lot,
                      underlying_symbol=underlying, max_lots=max_lots, direction=trade_direction, keep_rr=True)
    quantity = lots * lot
    leg = _Leg(right, "LONG" if bought else "SHORT", float(strike), expiry, 1, fill, quantity)
    meta = {"stop_value": level, "premium_stop_pct": rules.premium_stop_pct, "net_credit": fill if not bought else -fill}
    label = f"{underlying} {'BUY' if bought else 'WRITE'} {int(strike)}{right} {expiry.isoformat()}"
    # Strategy templates section 5: PREMIUM_PCT - the premium floor / ceiling is the only stop (no underlying-level stop)
    underlying_stop = None if config.stop_source == "PREMIUM_PCT" else signal.stop_loss
    return _Open(label=label, legs=[leg], meta=meta, lots=lots, quantity=quantity, entry_time=at, entry_time_bar=ts, near_expiry=expiry,
                 direction=trade_direction, entry_unit=fill, single=True, underlying_direction=signal.direction.value,
                 underlying_stop=underlying_stop, underlying_t1=signal.target1, underlying_t2=signal.target2, premium_level=level)
