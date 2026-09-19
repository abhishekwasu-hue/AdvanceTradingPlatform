from collections import defaultdict
from datetime import datetime, timezone
from typing import Dict, List, Tuple

from app.brokers.models import BrokerPosition
from app.db.models import TradeRecord
from app.reconciliation.models import ReconciliationItem, ReconciliationReport, ReconciliationStatus


def _internal_signed_quantity(trade: TradeRecord) -> int:
    return trade.quantity if trade.direction == "LONG" else -trade.quantity


def reconcile_positions(
    broker_name: str, internal_open_trades: List[TradeRecord], broker_positions: List[BrokerPosition],
) -> ReconciliationReport:
    """Compares this tenant's internally-tracked open positions (TradeRecord rows with no
    exit_time, netted by symbol - LONG contributes +quantity, SHORT contributes -quantity, the
    same sign convention every broker adapter's own net position quantity already uses) against
    what the broker itself reports (BrokerInterface.get_positions()), one row per symbol. A real
    deployment runs this periodically (or on demand) to catch drift between what the platform
    believes it holds and what actually sits at the broker: a manual fill/exit made outside the
    platform, a missed webhook, a broker-side correction, a live order that filled without the
    platform's own trade record being written.

    Symbols are matched by exact string equality - the same trading symbol the strategy/order
    router used going in is what's expected to come back from the broker; this doesn't attempt
    fuzzy matching across different symbol-naming conventions.
    """
    internal_by_symbol: Dict[str, Tuple[int, List[int]]] = defaultdict(lambda: (0, []))
    for trade in internal_open_trades:
        qty, ids = internal_by_symbol[trade.symbol]
        internal_by_symbol[trade.symbol] = (qty + _internal_signed_quantity(trade), ids + [trade.id])

    broker_by_symbol: Dict[str, int] = defaultdict(int)
    for position in broker_positions:
        if position.quantity != 0:
            broker_by_symbol[position.symbol] += position.quantity

    all_symbols = set(internal_by_symbol) | set(broker_by_symbol)
    items: List[ReconciliationItem] = []
    for symbol in sorted(all_symbols):
        has_internal = symbol in internal_by_symbol
        has_broker = symbol in broker_by_symbol
        internal_qty, trade_ids = internal_by_symbol.get(symbol, (None, []))
        broker_qty = broker_by_symbol.get(symbol)

        if has_internal and not has_broker:
            items.append(ReconciliationItem(
                symbol=symbol, internal_net_quantity=internal_qty, broker_net_quantity=None,
                status=ReconciliationStatus.MISSING_AT_BROKER, internal_trade_ids=trade_ids,
                detail=f"Platform shows {internal_qty} open at {symbol}, but {broker_name} reports no position",
            ))
        elif has_broker and not has_internal:
            items.append(ReconciliationItem(
                symbol=symbol, internal_net_quantity=None, broker_net_quantity=broker_qty,
                status=ReconciliationStatus.UNTRACKED_AT_BROKER, internal_trade_ids=[],
                detail=f"{broker_name} reports {broker_qty} at {symbol} with no matching platform position",
            ))
        elif internal_qty != broker_qty:
            items.append(ReconciliationItem(
                symbol=symbol, internal_net_quantity=internal_qty, broker_net_quantity=broker_qty,
                status=ReconciliationStatus.QUANTITY_MISMATCH, internal_trade_ids=trade_ids,
                detail=f"Platform shows {internal_qty}, {broker_name} shows {broker_qty} at {symbol}",
            ))
        else:
            items.append(ReconciliationItem(
                symbol=symbol, internal_net_quantity=internal_qty, broker_net_quantity=broker_qty,
                status=ReconciliationStatus.MATCHED, internal_trade_ids=trade_ids,
            ))

    mismatched_count = sum(1 for item in items if item.status != ReconciliationStatus.MATCHED)
    return ReconciliationReport(
        broker_name=broker_name, checked_at=datetime.now(timezone.utc).isoformat(),
        items=items, mismatched_count=mismatched_count,
    )
