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

`apply_exit_rules` is a pure function of the trade's state and the latest bar/quote: it returns
the (possibly tightened) stop, the new best price, and a time-exit reason when one fired. The
callers own persistence (the monitor updates `stop_loss`/`best_price` on the row and, LIVE,
modifies the broker-side SL-M to the new trigger).
"""
import json
from dataclasses import asdict, dataclass
from datetime import datetime, time as dtime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


@dataclass(frozen=True)
class ExitRules:
    trailing_stop_pct: Optional[float] = None
    break_even_at_r: Optional[float] = None
    time_exit_minutes: Optional[int] = None
    time_exit_at: Optional[str] = None   # "HH:MM" IST

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
        return ", ".join(parts) if parts else "no dynamic exits"

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
    if rules.time_exit_minutes is not None and rules.time_exit_minutes > 0:
        entry_aware = entry_time if entry_time.tzinfo else entry_time.replace(tzinfo=IST)
        now_aware = now if now.tzinfo else now.replace(tzinfo=IST)
        if now_aware - entry_aware >= timedelta(minutes=rules.time_exit_minutes):
            time_reason = f"Time exit ({rules.time_exit_minutes}m in trade)"
    cutoff = rules.exit_time_of_day()
    if time_reason is None and cutoff is not None:
        now_ist = (now if now.tzinfo else now.replace(tzinfo=IST)).astimezone(IST)
        if now_ist.time() >= cutoff:
            time_reason = f"Time exit (flat at {rules.time_exit_at} IST)"

    return ExitUpdate(stop_loss=round(stop, 2), best_price=round(best, 2), stop_changed=stop != current_stop,
                      stop_reason=reason, time_exit_reason=time_reason)
