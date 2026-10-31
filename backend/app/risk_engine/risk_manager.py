import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Optional

from app.core.enums import SignalDirection
from app.core.models import RiskConfig, RiskDecision, Signal
from app.instruments.models import ContractSpec
from app.risk_engine import risk_limits


def nonfinite_levels(signal: Signal) -> str:
    """The signal's price levels that are set but not finite numbers (NaN or infinite), as "entry nan, target1 inf";
    empty when every set level is usable. Shared by the risk check and the contract / option plans, which derive their
    own levels from these and store the underlying ones on the trade."""
    levels = (("entry", signal.entry), ("stop loss", signal.stop_loss), ("target1", signal.target1), ("target2", signal.target2))
    return ", ".join(f"{name} {value!r}" for name, value in levels if value is not None and not math.isfinite(value))


@dataclass
class TradingDayState:
    """Mutable counters the risk engine checks before approving every order.

    A real deployment persists this per trading day in Redis/Postgres and updates it
    as fills and closes come back from the broker; this in-memory version is enough
    to drive paper trading and backtests.

    M64: `open_positions` and `trades_today` count a multi-leg structure once (its legs share one group); `last_loss_at`
    is when the newest loss of the current streak closed and `as_of` the time the check runs (the cool-down in minutes).
    """

    trades_today: int = 0
    daily_pnl: float = 0.0
    consecutive_losses: int = 0
    open_positions: int = 0
    last_loss_at: datetime | None = None
    as_of: datetime | None = None


def _aware(value: datetime | None) -> datetime | None:
    return value.replace(tzinfo=UTC) if value is not None and value.tzinfo is None else value


def cooldown_until(cfg: RiskConfig, state: TradingDayState) -> str | None:
    """Why the losses-in-a-row pause holds now, as the end of the sentence ("for 30 minutes, until 14:05 IST"), or None
    when it does not hold (not on, not reached, or its minutes have passed)."""
    limit = cfg.max_consecutive_losses
    if limit is None or state.consecutive_losses < limit:
        return None
    minutes = cfg.loss_cooldown_minutes
    if minutes is not None and minutes > 0:
        last, now = _aware(state.last_loss_at), _aware(state.as_of)
        if last is None or now is None:
            return f"for {minutes} minutes after the last loss"
        ends = last + timedelta(minutes=minutes)
        if now >= ends:
            return None
        from app.market_data.calendar import IST
        return f"for {minutes} minutes, until {ends.astimezone(IST).strftime('%H:%M')} IST"
    return "until you resume" if cfg.loss_streak_pause == "until_resume" else "until the next session"


class RiskManager:
    """Every order, paper or live, must pass through here before it reaches execution.

    M64 (Settings > Risk limits): each check here runs only when the trader switched it on - a switched-off check reaches
    this class as "not set" (app/risk_engine/risk_limits.apply_switches). With every check off the size is the lots the
    trader typed (`requested_quantity`), else one lot. Every refusal is one plain sentence with its numbers (`reasons`)
    and the same as data (`blocks`) for the block message. Exits never come here (ADR-0004)."""

    def __init__(self, config: RiskConfig) -> None:
        self.config = config

    def validate_and_size(
        self, signal: Signal, state: TradingDayState, contract_spec: Optional[ContractSpec] = None,
        requested_quantity: Optional[float] = None,
    ) -> RiskDecision:
        """`contract_spec` is only supplied for symbols the instrument registry recognizes
        (MCX commodities, crypto pairs) - see app/instruments/registry.py. Every plain NSE/BSE
        equity or index-option symbol passes None here and sizes exactly as before, off the
        tenant's own configured `RiskConfig.lot_size`, so this stays fully backward compatible.

        DEF-0 (owner, 28 Oct): each limit applies only when the trader set it. With no risk per trade set the size is
        not risk-based: it is `requested_quantity` (the trader's own lots, or the caller's cap) when given, else one lot.
        """
        if not signal.is_tradeable:
            return RiskDecision(approved=False, reasons=["This signal says no trade."])
        if signal.entry is None or signal.stop_loss is None:
            return RiskDecision(approved=False, reasons=["This signal has no entry or stop-loss level."])
        # A NaN level (an alert's "nan", a broken quote) would size to NaN and crash the int() below, leaving the order
        # at RISK_CHECK; an infinite target would fill and then fail to store. Refuse it like any other unusable signal.
        bad = nonfinite_levels(signal)
        if bad:
            return RiskDecision(approved=False, reasons=[f"Signal {bad} is not a finite number"])
        cfg = self.config
        blocks: list[dict[str, Any]] = []
        # TT12-4: a minimum reward:risk applies only when the trader set one (0 = not set: no R:R filter at all).
        # Entries only: exits never pass through here (ADR-0004).
        if cfg.min_risk_reward > 0 and signal.risk_reward is not None and signal.risk_reward < cfg.min_risk_reward:
            blocks.append(risk_limits.reward_risk_block(signal.risk_reward, cfg.min_risk_reward))
        if cfg.max_trades_per_day is not None and state.trades_today >= cfg.max_trades_per_day:
            blocks.append(risk_limits.entries_block(state.trades_today, cfg.max_trades_per_day))
        if cfg.max_open_positions is not None and state.open_positions >= cfg.max_open_positions:
            blocks.append(risk_limits.open_positions_block(state.open_positions, cfg.max_open_positions))
        until = cooldown_until(cfg, state)
        if until is not None and cfg.max_consecutive_losses is not None:
            blocks.append(risk_limits.cooldown_block(state.consecutive_losses, cfg.max_consecutive_losses, until))

        daily_limit = cfg.daily_loss_amount()
        if daily_limit is not None and state.daily_pnl <= -daily_limit:
            blocks.append(risk_limits.daily_loss_block(-state.daily_pnl, daily_limit))

        if blocks:
            return self._refused(blocks)

        risk_amount = cfg.risk_amount()
        unit = contract_spec.lot_size if contract_spec is not None else cfg.lot_size
        typed = float(requested_quantity) if requested_quantity is not None and requested_quantity > 0 else None
        warnings: list[str] = []
        if risk_amount is None:
            # DEF-0 / M64: no risk-based sizing - the trader's own size (or one lot); the size does not depend on the stop
            # distance (an undefined-risk structure with no stop set sizes too).
            quantity = typed if typed is not None else float(unit)
            return self._size_checked(quantity, unit, warnings, typed=True)

        if signal.direction == SignalDirection.SHORT and signal.stop_loss <= 0:
            # M64 RM-2: a short with no stop has no limit to its loss, so risk-based sizing cannot size it - the trader's
            # own lots (or one lot) are used with a WARNING chip; never a refusal.
            quantity = typed if typed is not None else float(unit)
            warnings.append(risk_limits.NO_STOP_WARNING.format(lots=risk_limits.lots_text(quantity / unit if unit else quantity)))
            return self._size_checked(quantity, unit, warnings, typed=True)
        risk_per_unit = abs(signal.entry - signal.stop_loss)
        if risk_per_unit <= 0:
            return self._refused([risk_limits.no_distance_block()])

        raw_qty = risk_amount / risk_per_unit

        if contract_spec is not None and contract_spec.fractional:
            # No such thing as "one lot" of a spot crypto pair - round down to the instrument's
            # own smallest quantity increment instead of flooring to a whole multiple of it.
            increments = int(raw_qty / contract_spec.lot_size)
            quantity = round(increments * contract_spec.lot_size, 8)
        else:
            lots = int(raw_qty // unit)
            quantity = lots * unit

        if quantity <= 0:
            return self._refused([risk_limits.below_one_lot_block(risk_amount, risk_per_unit * float(unit))])

        return self._size_checked(quantity, unit, warnings, typed=False)

    def _size_checked(self, quantity: float, unit: float, warnings: list[str], *, typed: bool) -> RiskDecision:
        """M64: the max lots per order. A size the trader typed above it is refused with the smaller size offered ("place
        N?"); a size risk-based sizing worked out is lowered to it, said in a note (both limits are his own)."""
        limit = self.config.max_lots_per_order
        lot = float(unit) if unit and unit > 0 else 1.0
        if limit is not None and limit > 0 and quantity > limit * lot + 1e-9:
            if typed:
                return self._refused([risk_limits.max_lots_block(int(limit), quantity / lot)], warnings)
            warnings.append(f"Risk-based sizing gave {risk_limits.lots_text(quantity / lot)}; your max lots per order is {limit}, "
                            f"so {risk_limits.lots_text(limit)} {'is' if limit == 1 else 'are'} placed.")
            quantity = float(limit) * lot
        return RiskDecision(approved=True, quantity=quantity, reasons=["Risk checks passed"], warnings=warnings)

    @staticmethod
    def _refused(blocks: list[dict[str, Any]], warnings: list[str] | None = None) -> RiskDecision:
        return RiskDecision(approved=False, reasons=[b["text"] for b in blocks], blocks=blocks, warnings=list(warnings or []))
