from datetime import UTC, datetime, timedelta

from app.screener.breakouts.amw_breakout import Candidate, Candle, Settings, Snapshot
from app.screener.breakouts.amw_research import evaluate


def test_replay_horizons_use_bars_after_signal_availability(monkeypatch):
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    candidate = Candidate("range", 1, 100.0, ("B1",))
    frames = (
        Snapshot(t0, Candle(100.2, 101.1, 100.1, 101.0, 1.0), (candidate,), zones_ready=True, structure=1, htf=1),
        Snapshot(t0 + timedelta(minutes=15), Candle(101, 103, 100, 102, 1), zones_ready=True),
    )
    monkeypatch.setattr("app.screener.breakouts.amw_research.iter_snapshots", lambda *args, **kwargs: iter(frames))
    result = evaluate({}, Settings())["directions"]
    got = result["LONG"]["horizons"][1]
    assert got == {
        "sample": 1, "mean_return_atr": 1.0, "median_return_atr": 1.0, "mean_mfe_atr": 2.0,
        "mean_mae_atr": 1.0, "failure_frequency": 0, "continuation_frequency": 1,
    }
    assert result["LONG"]["horizons"][3]["sample"] == 0
    assert result["SHORT"]["emitted"] == 0


def test_signal_outcome_does_not_use_holdout_candles():
    from app.screener.breakouts.amw_breakout import Decision
    from app.screener.breakouts.amw_research import signal_outcome

    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    bars = (
        Snapshot(t0, Candle(100, 102, 99, 101, 2)),
        Snapshot(t0 + timedelta(minutes=15), Candle(101, 104, 95, 99, 2)),
    )
    decision = Decision(
        "range", -1, t0, t0, "SIGNAL_GENERATED", "ELIGIBLE",
        evidence={"reference_price": 101, "previous_atr": 2, "level": 103},
    )
    assert signal_outcome(bars, 0, decision, 1, stop=1) is None
    result = signal_outcome(bars, 0, decision, 1)
    assert result["directional_atr"] == 1
    assert result["mfe_atr"] == 3
    assert result["mae_atr"] == 1.5
    assert result["failed_breakout"] is False
    assert result["first_mfe_3_atr_bar"] == 1
