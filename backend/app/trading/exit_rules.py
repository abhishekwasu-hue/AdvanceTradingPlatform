"""Phase J1: dynamic exit rules (master prompt section 29) shared by the backtest engine and the
live/paper position monitor, so a rule behaves identically in both (section 13's "same logic
everywhere").

A deployment (or a backtest request) carries an `ExitRules` JSON:

* `trailing_stop_pct`  - once in profit, the stop follows the best price seen at this distance
  (LONG: best * (1 - pct/100); SHORT: best * (1 + pct/100)). The stop only ever tightens.
* `break_even_at_r`    - after the trade has moved this many R (initial risk multiples) in its
  favour, the stop moves to the entry price (plus nothing: a scratch, not a profit lock).
* `time_exit_minutes`  - close after this long in the trade, whatever the price.
* `time_exit_at`       - close at this IST wall time ("14:45"), for strategies that must be flat
  before an event or the square-off scramble.
* `scale_out_at_r` + `scale_out_pct` (M99 P7, flag `risk_options`) - once the price is this many R in the trade's favour,
  this % of the open lots closes (whole lots, rounded down, at least one; the rest stays open with its own stop and
  target). Once per position, never on a loss: it is judged on the price now, not on the best price seen. The level and
  the share are the trader's own (no default). A single position on the traded price (cash / futures); an option
  position, whose levels are on its underlying, is not scaled out. The worker closes the part through the one partial-exit path
  (app/paper/fno_position_actions.exit_part); the backtest closes the same lots at the level (`scale_out_lots`).

`apply_exit_rules` is a pure function of the trade's state and the latest bar/quote: it returns
the (possibly tightened) stop, the new best price, and a time-exit reason when one fired. The
callers own persistence (the monitor updates `stop_loss`/`best_price` on the row and, LIVE,
modifies the broker-side SL-M to the new trigger).
"""
import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from datetime import time as dtime
from typing import Optional
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExitRules:
    trailing_stop_pct: Optional[float] = None
    break_even_at_r: Optional[float] = None
    time_exit_minutes: Optional[int] = None
    time_exit_at: Optional[str] = None   # "HH:MM" IST
    scale_out_at_r: float | None = None   # M99 P7: the user's own level, in R
    scale_out_pct: float | None = None    # M99 P7: the user's own share of the open lots, %

    @property
    def active(self) -> bool:
        return any(v is not None for v in asdict(self).values())

    def to_json(self) -> Optional[str]:
        data = {k: v for k, v in asdict(self).items() if v is not None}
        return json.dumps(data, sort_keys=True) if data else None

    @classmethod
    def from_json(cls, text: Optional[str]) -> "ExitRules":
        if not text:
            return cls()
        try:
            data = json.loads(text)
        except (TypeError, ValueError):
            return cls()
        return cls(**{k: data[k] for k in cls.__dataclass_fields__ if k in data})

    def describe(self) -> str:
        parts = []
        if self.trailing_stop_pct is not None:
            parts.append(f"trail {self.trailing_stop_pct:g}%")
        if self.break_even_at_r is not None:
            parts.append(f"break-even at {self.break_even_at_r:g}R")
        if self.time_exit_minutes is not None:
            parts.append(f"time exit {self.time_exit_minutes}m")
        if self.time_exit_at:
            parts.append(f"flat at {self.time_exit_at} IST")
        if self.scale_out_ready:
            parts.append(f"scale out {self.scale_out_pct:g}% at {self.scale_out_at_r:g}R")
        return ", ".join(parts) if parts else "no dynamic exits"

    @property
    def scale_out_ready(self) -> bool:
        """M99 P7: both the level and the share are set (one without the other is not a rule)."""
        return (self.scale_out_at_r is not None and self.scale_out_at_r > 0 and self.scale_out_pct is not None
                and 0 < self.scale_out_pct < 100)

    def exit_time_of_day(self) -> Optional[dtime]:
        if not self.time_exit_at:
            return None
        try:
            hh, mm = self.time_exit_at.split(":")
            return dtime(int(hh), int(mm))
        except (ValueError, AttributeError):
            return None


@dataclass
class ExitUpdate:
    stop_loss: float
    best_price: float
    stop_changed: bool
    stop_reason: Optional[str]       # why the stop moved ("trailing", "break-even")
    time_exit_reason: Optional[str]  # set when a time rule says close now
    scale_out_level: float | None = None   # M99 P7: the price the scale-out level is at, when the price reached it now


def apply_exit_rules(
    rules: ExitRules, *, direction: str, entry_price: float, initial_stop: float, current_stop: float,
    best_price: Optional[float], high: float, low: float, entry_time: datetime, now: datetime,
) -> ExitUpdate:
    """One evaluation against the latest bar (or a single quote passed as high == low == price).
    Stops only tighten; a time exit is reported, never applied here."""
    is_long = direction == "LONG"
    best = best_price if best_price is not None else entry_price
    best = max(best, high) if is_long else min(best, low)
    stop = current_stop
    reason: Optional[str] = None

    risk = abs(entry_price - initial_stop)
    if rules.break_even_at_r is not None and risk > 0:
        moved = (best - entry_price) if is_long else (entry_price - best)
        if moved >= rules.break_even_at_r * risk:
            candidate = entry_price
            if (is_long and candidate > stop) or (not is_long and candidate < stop):
                stop, reason = candidate, "break-even"

    if rules.trailing_stop_pct is not None and rules.trailing_stop_pct > 0:
        pct = rules.trailing_stop_pct / 100.0
        candidate = round(best * (1 - pct), 2) if is_long else round(best * (1 + pct), 2)
        in_profit = (best > entry_price) if is_long else (best < entry_price)
        if in_profit and ((is_long and candidate > stop) or (not is_long and candidate < stop)):
            stop, reason = candidate, "trailing"

    time_reason: Optional[str] = None
    # ADR-0004: a time rule that cannot be computed (a stored value too large for a timedelta, a malformed value) is
    # skipped for this evaluation; it never raises, so the stop / target / other exits of this position still run.
    try:
        if rules.time_exit_minutes is not None and rules.time_exit_minutes > 0:
            entry_aware = entry_time if entry_time.tzinfo else entry_time.replace(tzinfo=IST)
            now_aware = now if now.tzinfo else now.replace(tzinfo=IST)
            if now_aware - entry_aware >= timedelta(minutes=rules.time_exit_minutes):
                time_reason = f"Time exit ({rules.time_exit_minutes}m in trade)"
    except Exception as exc:  # noqa: BLE001 - OverflowError / TypeError / ValueError: skip the time rule only
        logger.warning("Time exit after %r minutes could not be computed (%s) - time rule skipped", rules.time_exit_minutes, exc)
    try:
        cutoff = rules.exit_time_of_day()
        if time_reason is None and cutoff is not None:
            now_ist = (now if now.tzinfo else now.replace(tzinfo=IST)).astimezone(IST)
            if now_ist.time() >= cutoff:
                time_reason = f"Time exit (flat at {rules.time_exit_at} IST)"
    except Exception as exc:  # noqa: BLE001 - skip the time-of-day rule only
        logger.warning("Time exit at %r could not be computed (%s) - time rule skipped", rules.time_exit_at, exc)

    return ExitUpdate(stop_loss=round(stop, 2), best_price=round(best, 2), stop_changed=stop != current_stop,
                      stop_reason=reason, time_exit_reason=time_reason,
                      scale_out_level=scale_out_level(rules, direction=direction, entry_price=entry_price,
                                                      initial_stop=initial_stop, high=high, low=low))


def scale_out_level(rules: ExitRules, *, direction: str, entry_price: float, initial_stop: float, high: float,
                    low: float) -> float | None:
    """M99 P7: the scale-out level's price when the price reached it now (this bar's range, or one quote as high == low),
    else None. In the trade's favour only, so a part is never scaled out of a loss. Pure."""
    if not rules.scale_out_ready:
        return None
    risk = abs(entry_price - initial_stop)
    if risk <= 0:
        return None
    is_long = direction == "LONG"
    level = entry_price + float(rules.scale_out_at_r or 0) * risk if is_long else entry_price - float(rules.scale_out_at_r or 0) * risk
    reached = high >= level if is_long else low <= level
    return round(level, 2) if reached else None


def scale_out_lots(rules: ExitRules, held_lots: int) -> int:
    """M99 P7: how many of `held_lots` the scale-out closes - the trader's % of them, rounded down, at least one; 0 when
    fewer than two lots are held (a part needs a rest). Pure."""
    most = held_lots - 1                 # a part always leaves a rest
    if not rules.scale_out_ready or most < 1:
        return 0
    lots = int(held_lots * float(rules.scale_out_pct or 0) / 100.0 + 1e-9)
    return min(max(1, lots), most)
