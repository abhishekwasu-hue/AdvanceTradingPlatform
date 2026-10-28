"""AUD-V1 (owner audit 28 Oct; AP-FIX AP1): the Autopilot decides on closed bars only.

A broker's intraday feed ends with the bar still forming; the worker drops every bar whose start + timeframe is after
the cycle's clock before the strategy reads it - the same bars the backtest and the screener decide on."""
from datetime import datetime, timedelta, timezone

import pandas as pd

from app.market_data.calendar import IST
from app.market_data.freshness import closed_frames
from app.strategy_engine.registry import registry
from tests.test_trading_worker import STRATEGY, _deploy, _FakeBroker, _run, _signal, _tenant, _worker


def _frame(start, n, step, tz=None):
    idx = pd.date_range(start, periods=n, freq=step, tz=tz)
    return pd.DataFrame({"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0}, index=idx)


def test_the_forming_bar_is_dropped_on_every_timeframe():
    now = datetime(2026, 9, 25, 10, 32, 30, tzinfo=IST)
    frames = {
        "1min": _frame("2026-09-25 09:15", 78, "1min", tz=IST),        # last bar 10:32 is forming at 10:32:30
        "5min": _frame("2026-09-25 09:15", 16, "5min", tz=IST),        # last bar 10:30 closes 10:35
        "day": _frame("2026-09-21", 5, "1D", tz=IST),                  # today's daily bar is forming
        "1w": _frame("2026-09-07", 3, "7D", tz=IST),                   # the week of 21 Sep is forming
    }
    out = closed_frames(frames, now)
    assert out["1min"].index[-1] == pd.Timestamp("2026-09-25 10:31", tz=IST)
    assert out["5min"].index[-1] == pd.Timestamp("2026-09-25 10:25", tz=IST)
    assert out["day"].index[-1] == pd.Timestamp("2026-09-24", tz=IST)
    assert out["1w"].index[-1] == pd.Timestamp("2026-09-14", tz=IST)
    naive = {"1min": _frame("2026-09-25 03:45", 78, "1min")}           # a naive index is UTC
    assert closed_frames(naive, now)["1min"].index[-1] == pd.Timestamp("2026-09-25 05:01")
    closed = {"1min": _frame("2026-09-25 09:15", 77, "1min", tz=IST)}  # nothing forming: nothing dropped
    assert len(closed_frames(closed, now)["1min"]) == 77


def test_the_worker_hands_the_strategy_closed_bars_only(monkeypatch):
    t = _tenant("aud-v1@example.com")
    _deploy(t)
    seen = []

    def analyze(data, symbol):
        seen.append({tf: df.index[-1] for tf, df in data.items()})
        return _signal()
    monkeypatch.setattr(registry.get(STRATEGY), "analyze", analyze)
    # Today's bars 09:15 .. 10:32 (78); the cycle runs at 10:32:30, so 10:32 is still forming.
    worker = _worker(monkeypatch, _FakeBroker(intraday_bars=78))
    now = datetime(2026, 9, 25, 10, 32, 30, tzinfo=IST)
    _run(worker.run_cycle(now=now))
    assert seen, "the strategy was not evaluated"
    for last in seen[0].values():
        stamp = last.to_pydatetime()
        stamp = stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)
        assert stamp + timedelta(minutes=1) <= now, seen
