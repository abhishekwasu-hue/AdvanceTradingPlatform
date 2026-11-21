"""P2-8 J1-4: plan vs actual, one short line per difference.

The plan is what the trade was taken on, as recorded by the capture hooks (`capture.plan_of`: the signal's price, the
initial stop, his own exit levels from his deployment / ticket settings); the actual is what the fills, the stop
history and the exit say. Only differences are listed: a trade that went as planned has no line. Nothing is added to
the plan (no default exit level, no reward:risk rule). Every line describes; none advises (`review.advice_in`).
"""
from __future__ import annotations

from typing import Any, Sequence

from app.journal.detectors import is_trader_exit
from app.journal.review import advice_in

TICK = 1e-6


def _p(value: float) -> str:
    return f"{value:,.2f}"


def _money(value: float) -> str:
    return f"{'-' if value < 0 else ''}₹{abs(value):,.2f}"


def _f(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _by_own_rules(history: Sequence[dict[str, Any]], now: float) -> bool:
    """Every recorded move came from his own exit rules (a trailing / breakeven rule he set) and ends at the stop the
    trade has now: part of his plan, so not a difference."""
    if not history:
        return False
    last = _f(history[-1].get("to"))
    return (last is not None and abs(last - now) <= TICK
            and all(str(h.get("source") or "").lower().startswith("exit rule") for h in history))


def plan_vs_actual(*, direction: str, plan: dict[str, Any], entry_price: float, quantity: float, stop_now: float | None,
                   stop_history: Sequence[dict[str, Any]], exit_price: float | None, exit_reason: str | None,
                   exit_filled_quantity: float | None) -> list[str]:
    """Pure: the same recorded trade gives the same lines."""
    lines: list[str] = []
    planned_entry = _f(plan.get("entry"))
    if plan.get("entry_source") == "signal" and planned_entry is not None and abs(float(entry_price) - planned_entry) > TICK:
        gap = float(entry_price) - planned_entry
        lines.append(f"Filled at {_p(entry_price)}; your signal's price was {_p(planned_entry)} ({gap:+,.2f} per unit).")
    planned_stop = _f(plan.get("stop"))
    now = _f(stop_now)
    if planned_stop is not None and now is not None and abs(now - planned_stop) > TICK and not _by_own_rules(stop_history, now):
        times = f", {len(stop_history)} changes" if len(stop_history) > 1 else ""
        lines.append(f"Stop moved from {_p(planned_stop)} to {_p(now)}{times}.")
    level = _f(plan.get("target1"))
    if exit_price is not None and is_trader_exit(exit_reason):
        exit_at = float(exit_price)
        sign = 1 if direction == "LONG" else -1
        stop = now or planned_stop
        inside_stop = stop is None or sign * (exit_at - stop) > 0
        inside_level = level is None or sign * (level - exit_at) > 0
        if inside_stop and inside_level:
            parts = [f"your stop ({_p(stop)})"] if stop is not None else []
            parts += [f"your exit level ({_p(level)})"] if level is not None else []
            before = f", before {' and '.join(parts)}" if parts else ""
            lines.append(f"Closed by hand at {_p(exit_at)}{before}.")
    if exit_filled_quantity is not None and exit_price is not None and abs(float(exit_filled_quantity)) + TICK < abs(float(quantity)):
        lines.append(f"Exit filled {abs(float(exit_filled_quantity)):g} of {abs(float(quantity)):g}.")
    return [line for line in lines if not advice_in(line)]


def for_trade(trade: Any, plan: dict[str, Any], stop_history: Sequence[dict[str, Any]]) -> list[str]:
    """The lines of one ATP trade row (a leg of a structure is described by `for_spread`)."""
    return plan_vs_actual(direction=trade.direction, plan=plan, entry_price=float(trade.entry_price), quantity=float(trade.quantity),
                          stop_now=trade.stop_loss, stop_history=list(stop_history), exit_price=trade.exit_price,
                          exit_reason=trade.exit_reason, exit_filled_quantity=trade.exit_filled_quantity)


def for_spread(summary: dict[str, Any]) -> list[str]:
    """A closed structure whose loss went past the max loss it was placed with."""
    planned, actual = summary.get("planned_max_loss"), summary.get("actual_loss")
    if planned is not None and actual is not None and actual > planned + 0.005:
        line = f"Loss {_money(-actual)}; the structure's planned max loss was {_money(-planned)}."
        return [] if advice_in(line) else [line]
    return []


__all__ = ["plan_vs_actual", "for_trade", "for_spread"]
