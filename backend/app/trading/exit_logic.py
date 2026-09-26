from typing import Optional, Tuple

from app.db.models import TradeRecord


def determine_exit_price(
    direction: str, stop_loss: float, target1: float, target2: Optional[float], low: float, high: float,
) -> Optional[Tuple[str, float]]:
    """The single source of truth for exit priority (stop loss, then target2 - the more
    ambitious level, so it wins if somehow both are already crossed within the same bar/tick -
    then target1), shared by the backtest engine (which knows a bar's full low/high range) and
    `check_exit` below (which only ever has one current price, passed as `low == high ==
    current_price`). Before this was extracted, the backtest engine had its own independently
    written copy of this exact priority order - a comment claimed it "matched" this function, but
    nothing enforced that, so a future change to one could silently drift from the other. Kept as
    one function so that's no longer possible.
    """
    is_long = direction == "LONG"

    if is_long:
        if low <= stop_loss:
            return "Stop Loss", stop_loss
        if target2 is not None and high >= target2:
            return "Target 2", target2
        if high >= target1:
            return "Target 1", target1
    else:
        if high >= stop_loss:
            return "Stop Loss", stop_loss
        if target2 is not None and low <= target2:
            return "Target 2", target2
        if low <= target1:
            return "Target 1", target1

    return None


def check_exit(trade: TradeRecord, current_price: float) -> Optional[Tuple[str, float]]:
    """Decides whether `current_price` would close this open position, and at what price/reason.
    A single live/paper price point has no low/high range, so it's passed as both bounds.
    """
    return determine_exit_price(
        trade.direction, trade.stop_loss, trade.target1, trade.target2, current_price, current_price,
    )


def underlying_exit(direction: str, stop_loss: Optional[float], target1: Optional[float], target2: Optional[float], price: float) -> Optional[str]:
    """Which of the strategy's *underlying* levels `price` has crossed, if any - the same
    priority as `determine_exit_price` (stop, then the more ambitious target, then target 1),
    tolerant of missing targets (an option deployment's strategy may only have a stop)."""
    if stop_loss is None:
        return None
    is_long = direction == "LONG"
    if (is_long and price <= stop_loss) or (not is_long and price >= stop_loss):
        return "Stop Loss"
    if target2 is not None and ((is_long and price >= target2) or (not is_long and price <= target2)):
        return "Target 2"
    if target1 is not None and ((is_long and price >= target1) or (not is_long and price <= target1)):
        return "Target 1"
    return None


def check_contract_exit(trade: TradeRecord, contract_price: float, underlying_price: Optional[float]) -> Optional[Tuple[str, float]]:
    """Exit decision for a derived-contract trade (Phase F4).

    * OPTION: the strategy's levels are on the underlying, so a stop/target crossed by
      `underlying_price` exits at the *contract's* current price (reason suffixed "(underlying)").
      Independently, the premium safety net: a bought option whose premium fell to its floor
      (`trade.stop_loss`), or a written option whose premium rose to its ceiling, exits at the
      contract price. With no underlying quote the premium check still runs.
    * FUTURE / UNDERLYING: the transplanted levels are on the contract's own price - plain
      `check_exit`.
    """
    kind = (trade.instrument_kind or "UNDERLYING").upper()
    if kind != "OPTION":
        return check_exit(trade, contract_price)
    bought = trade.direction == "LONG"
    if (bought and contract_price <= trade.stop_loss) or (not bought and contract_price >= trade.stop_loss):
        return ("Premium floor" if bought else "Premium ceiling"), contract_price
    if underlying_price is not None and trade.underlying_direction:
        hit = underlying_exit(trade.underlying_direction, trade.underlying_stop_loss, trade.underlying_target1,
                              trade.underlying_target2, underlying_price)
        if hit is not None:
            return f"{hit} (underlying)", contract_price
    return None
