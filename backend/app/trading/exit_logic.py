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
