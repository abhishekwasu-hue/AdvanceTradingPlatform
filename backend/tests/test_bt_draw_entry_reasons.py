"""BT-DRAW (owner 18 Nov): every test trade says why it entered - the signal's own sentences, never its provenance."""
from __future__ import annotations

from datetime import UTC, datetime

from app.core.models import Signal, SignalDirection
from app.execution.paper_broker import PaperBroker


def _signal(reasons: list[str]) -> Signal:
    return Signal(symbol="DEMO", strategy_id="template:DEMO", strategy_name="Demo", direction=SignalDirection.LONG,
                  timestamp=datetime(2026, 10, 5, 4, 0, tzinfo=UTC), entry=100.0, stop_loss=98.0, target1=104.0,
                  reasons=reasons, provenance={"template": "internal-id"})


def test_open_trade_keeps_the_signal_sentences():
    trade = PaperBroker().open_trade(_signal(["The price came back into a demand area and a candle committed.", " ", "Evidence: a, b"]),
                                     1, datetime(2026, 10, 5, 4, 15, tzinfo=UTC))
    assert trade.entry_reasons == ["The price came back into a demand area and a candle committed.", "Evidence: a, b"]
    dumped = trade.model_dump(mode="json")
    assert dumped["entry_reasons"][0].startswith("The price came back")
    assert "internal-id" not in str(dumped)                                  # the provenance never reaches the trade


def test_a_signal_without_reasons_gives_an_empty_list():
    trade = PaperBroker().open_trade(_signal([]), 1, datetime(2026, 10, 5, 4, 15, tzinfo=UTC))
    assert trade.entry_reasons == []
