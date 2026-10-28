"""UI-28 U2.4 (owner, 28 Oct): a position says which Paper bot opened it (read-only, the column already exists), so
the Paper bot page can show the bot's own open positions; a trade entered by hand says null."""
from datetime import datetime, timezone

from app.db.models import TradeRecord
from app.trading.routes import TradeRecordResponse


def _record(deployment_id):
    return TradeRecord(id=7, mode="PAPER", symbol="NIFTY 50", strategy_id="orb", direction="LONG",
                       entry_time=datetime(2026, 10, 28, 4, 0, tzinfo=timezone.utc), entry_price=100.0, quantity=10,
                       stop_loss=95.0, target1=110.0, target2=None, charges=0.0, deployment_id=deployment_id)


def test_the_bot_that_opened_a_position_is_in_the_answer():
    assert TradeRecordResponse.from_record(_record(42)).deployment_id == 42


def test_a_hand_entered_position_has_no_bot():
    body = TradeRecordResponse.from_record(_record(None)).model_dump()
    assert "deployment_id" in body and body["deployment_id"] is None
