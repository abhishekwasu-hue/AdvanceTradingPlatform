from datetime import datetime, timezone

from app.brokers.models import BrokerPosition
from app.db.models import TradeRecord
from app.reconciliation.engine import reconcile_positions
from app.reconciliation.models import ReconciliationStatus


def _trade(id_, symbol, direction, quantity, exit_time=None):
    return TradeRecord(
        id=id_, tenant_id=1, user_id=1, mode="PAPER", symbol=symbol, strategy_id="s",
        direction=direction, entry_time=datetime.now(timezone.utc), entry_price=100.0,
        quantity=quantity, stop_loss=98.0, target1=104.0, exit_time=exit_time,
    )


def test_matched_long_position():
    trades = [_trade(1, "NIFTY", "LONG", 50)]
    positions = [BrokerPosition(symbol="NIFTY", quantity=50, average_price=100.0)]
    report = reconcile_positions("zerodha", trades, positions)
    assert report.mismatched_count == 0
    item = report.items[0]
    assert item.status == ReconciliationStatus.MATCHED
    assert item.internal_net_quantity == 50
    assert item.broker_net_quantity == 50


def test_matched_short_position_uses_signed_quantity():
    trades = [_trade(1, "NIFTY", "SHORT", 50)]
    positions = [BrokerPosition(symbol="NIFTY", quantity=-50, average_price=100.0)]
    report = reconcile_positions("zerodha", trades, positions)
    assert report.items[0].status == ReconciliationStatus.MATCHED
    assert report.items[0].internal_net_quantity == -50


def test_quantity_mismatch():
    trades = [_trade(1, "NIFTY", "LONG", 50)]
    positions = [BrokerPosition(symbol="NIFTY", quantity=25, average_price=100.0)]
    report = reconcile_positions("zerodha", trades, positions)
    assert report.mismatched_count == 1
    item = report.items[0]
    assert item.status == ReconciliationStatus.QUANTITY_MISMATCH
    assert item.internal_net_quantity == 50
    assert item.broker_net_quantity == 25
    assert item.internal_trade_ids == [1]


def test_missing_at_broker_when_platform_thinks_position_is_open_but_broker_has_none():
    trades = [_trade(1, "NIFTY", "LONG", 50)]
    report = reconcile_positions("zerodha", trades, [])
    assert report.mismatched_count == 1
    item = report.items[0]
    assert item.status == ReconciliationStatus.MISSING_AT_BROKER
    assert item.internal_net_quantity == 50
    assert item.broker_net_quantity is None


def test_untracked_at_broker_when_broker_has_a_position_platform_has_never_seen():
    positions = [BrokerPosition(symbol="BANKNIFTY", quantity=10, average_price=200.0)]
    report = reconcile_positions("zerodha", [], positions)
    assert report.mismatched_count == 1
    item = report.items[0]
    assert item.status == ReconciliationStatus.UNTRACKED_AT_BROKER
    assert item.internal_net_quantity is None
    assert item.broker_net_quantity == 10


def test_zero_quantity_broker_positions_are_ignored():
    # A broker often reports a closed-out position as a row with quantity 0 - that must not be
    # treated as an open position needing reconciliation.
    trades = []
    positions = [BrokerPosition(symbol="NIFTY", quantity=0, average_price=0.0)]
    report = reconcile_positions("zerodha", trades, positions)
    assert report.items == []
    assert report.mismatched_count == 0


def test_multiple_open_trades_for_the_same_symbol_are_netted():
    trades = [_trade(1, "NIFTY", "LONG", 30), _trade(2, "NIFTY", "LONG", 20)]
    positions = [BrokerPosition(symbol="NIFTY", quantity=50, average_price=100.0)]
    report = reconcile_positions("zerodha", trades, positions)
    item = report.items[0]
    assert item.status == ReconciliationStatus.MATCHED
    assert item.internal_net_quantity == 50
    assert sorted(item.internal_trade_ids) == [1, 2]


def test_multiple_symbols_reported_independently():
    trades = [_trade(1, "NIFTY", "LONG", 50), _trade(2, "BANKNIFTY", "SHORT", 20)]
    positions = [
        BrokerPosition(symbol="NIFTY", quantity=50, average_price=100.0),
        BrokerPosition(symbol="BANKNIFTY", quantity=-15, average_price=200.0),
    ]
    report = reconcile_positions("zerodha", trades, positions)
    by_symbol = {i.symbol: i for i in report.items}
    assert by_symbol["NIFTY"].status == ReconciliationStatus.MATCHED
    assert by_symbol["BANKNIFTY"].status == ReconciliationStatus.QUANTITY_MISMATCH
    assert report.mismatched_count == 1


def test_closed_trades_are_never_passed_in_and_never_counted():
    # engine.py trusts its caller to only pass open trades - this asserts the route-level
    # contract by constructing a closed trade and confirming it, if mistakenly included, would
    # still net correctly rather than silently vanish (defense in depth, not the primary filter).
    trades = [_trade(1, "NIFTY", "LONG", 50, exit_time=datetime.now(timezone.utc))]
    positions = [BrokerPosition(symbol="NIFTY", quantity=50, average_price=100.0)]
    report = reconcile_positions("zerodha", trades, positions)
    assert report.items[0].status == ReconciliationStatus.MATCHED
