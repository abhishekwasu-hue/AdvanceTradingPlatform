from typing import Optional, Tuple

from app.db.models import TradeRecord


def check_exit(trade: TradeRecord, current_price: float) -> Optional[Tuple[str, float]]:
    """Decides whether `current_price` would close this open position, and at what price/reason.
    Priority matches the backtest engine: stop loss first, then target2 (the more ambitious
    level, so it wins if somehow both are already crossed), then target1. Returns None if the
    position should stay open.
    """
    is_long = trade.direction == "LONG"

    if is_long:
        if current_price <= trade.stop_loss:
            return "Stop Loss", trade.stop_loss
        if trade.target2 is not None and current_price >= trade.target2:
            return "Target 2", trade.target2
        if current_price >= trade.target1:
            return "Target 1", trade.target1
    else:
        if current_price >= trade.stop_loss:
            return "Stop Loss", trade.stop_loss
        if trade.target2 is not None and current_price <= trade.target2:
            return "Target 2", trade.target2
        if current_price <= trade.target1:
            return "Target 1", trade.target1

    return None
