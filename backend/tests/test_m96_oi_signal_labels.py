"""Owner bug report 13 Nov (M96, Trade AS IS): the chain view's stable OI Diff signal uses Trade's five labels and Trade's
History "View Interval" buckets (5 / 10 / 15 minutes), and no reading looks ahead.

This runs in CI without the Trade checkout; tests/test_m96_trade_golden.py compares the same functions with Trade's own
code when ATP_TRADE_REPO names a read-only checkout.
"""
from __future__ import annotations

import random
from datetime import UTC, date, datetime, timedelta, timezone

import pytest

from app.brokers.models import OptionChain, OptionChainRow
from app.option_chain import oi_regime, snapshots
from app.option_chain.oi_regime import Direction, OIRegimeSettings, OISignal, SnapshotPoint, Strength

IST = timezone(timedelta(hours=5, minutes=30))

# Trade oi_analysis.compute_oi_signal_with_hysteresis, its five return strings with the leading emoji left out.
TRADE_LABELS = ["BULLISH (Strong)", "BULLISH (Weakening)", "BEARISH (Strong)", "BEARISH (Weakening)", "NEUTRAL"]


def _p(diff: float, put: float, call: float, signal: OISignal) -> SnapshotPoint:
    return SnapshotPoint(diff, put, call, signal)


def test_the_five_labels_are_trades_words():
    signals = [OISignal(Direction.BULLISH, Strength.STRONG), OISignal(Direction.BULLISH, Strength.WEAKENING),
               OISignal(Direction.BEARISH, Strength.STRONG), OISignal(Direction.BEARISH, Strength.WEAKENING), OISignal(Direction.NEUTRAL)]
    assert [s.label for s in signals] == TRADE_LABELS
    # Trade's emoji labels read back to the same signal (stored readings, golden fixtures)
    for emoji, label in zip(["\U0001f7e2", "\U0001f7e1", "\U0001f534", "\U0001f7e0", "⚪"], TRADE_LABELS):
        assert OISignal.parse(f"{emoji} {label}").label == label


def test_each_label_by_trades_rule():
    """Strength against the reading `lookback_for_strength` (3) back; a new side needs 3 diffs of its sign and a rotation."""
    bull_s = OISignal(Direction.BULLISH, Strength.STRONG)
    hist = [_p(100, 1000, 900, bull_s), _p(120, 1010, 890, bull_s), _p(130, 1020, 890, bull_s)]
    rule = dict(structural_flip_min_rise_pct=None)
    assert oi_regime.compute_oi_signal_with_hysteresis(150, 1050, 900, hist, **rule).label == "BULLISH (Strong)"      # puts up
    assert oi_regime.compute_oi_signal_with_hysteresis(50, 950, 900, hist, **rule).label == "BULLISH (Weakening)"     # puts not up
    # one reading on the other side: Trade holds the old side as Weakening (never a false Strong)
    assert oi_regime.compute_oi_signal_with_hysteresis(-50, 1000, 1050, hist, **rule).label == "BULLISH (Weakening)"
    bear_s = OISignal(Direction.BEARISH, Strength.STRONG)
    bears = [_p(-100, 900, 1000, bear_s), _p(-120, 890, 1010, bear_s), _p(-130, 890, 1020, bear_s)]
    assert oi_regime.compute_oi_signal_with_hysteresis(-150, 900, 1050, bears, **rule).label == "BEARISH (Strong)"
    assert oi_regime.compute_oi_signal_with_hysteresis(-50, 900, 950, bears, **rule).label == "BEARISH (Weakening)"
    assert oi_regime.compute_oi_signal_with_hysteresis(0, 1000, 1000, [], **rule).label == "NEUTRAL"                  # first reading


def test_view_interval_buckets_as_trade():
    """Trade aggregate_oi_history: floor each reading to the interval, keep the bucket's LAST reading, newest first."""
    start = datetime(2026, 11, 11, 9, 15, tzinfo=IST)
    points = [start + timedelta(minutes=5 * i) for i in range(7)]               # 09:15 .. 09:45
    def view(n: int) -> list[tuple[str, str]]:
        return [(floor.strftime("%H:%M"), p.strftime("%H:%M")) for floor, p in oi_regime.aggregate_history(points, n, lambda p: p)]
    assert view(5) == [(t, t) for t in ["09:45", "09:40", "09:35", "09:30", "09:25", "09:20", "09:15"]]
    assert view(10) == [("09:40", "09:45"), ("09:30", "09:35"), ("09:20", "09:25"), ("09:10", "09:15")]
    assert view(15) == [("09:45", "09:45"), ("09:30", "09:40"), ("09:15", "09:25")]


def _day(seed: int, steps: int = 20) -> list[snapshots.SlotChain]:
    rng = random.Random(seed)
    strikes = list(range(24_000, 25_001, 50))
    call = {k: rng.randint(50_000, 300_000) for k in strikes}
    put = {k: rng.randint(50_000, 300_000) for k in strikes}
    out = []
    for i in range(steps):
        cd, pd_ = rng.uniform(-5, 5), rng.uniform(-5, 5)
        call = {k: max(0, round(v * (1 + cd / 100))) for k, v in call.items()}
        put = {k: max(0, round(v * (1 + pd_ / 100))) for k, v in put.items()}
        chain = OptionChain(underlying="NIFTY", expiry="2026-11-13", underlying_ltp=24_500 + rng.uniform(-60, 60),
                            rows=[OptionChainRow(strike=float(k), call_oi=call[k], put_oi=put[k], call_ltp=50.0, put_ltp=50.0) for k in strikes])
        at = (datetime(2026, 11, 11, 9, 20, tzinfo=IST) + timedelta(minutes=5 * i)).astimezone(UTC)
        out.append(snapshots.SlotChain(at, at, chain))
    return out


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_no_look_ahead_each_reading_uses_only_earlier_readings(seed):
    """The signal shown for a reading is the same whether or not later readings exist (replayed day = live day)."""
    slots = _day(seed)
    full = [e.state.stable_signal.label for e in snapshots.replay("NIFTY", slots, OIRegimeSettings(), date(2026, 11, 11))]
    for k in range(1, len(slots) + 1):
        part = [e.state.stable_signal.label for e in snapshots.replay("NIFTY", slots[:k], OIRegimeSettings(), date(2026, 11, 11))]
        assert part == full[:k], (seed, k)
    assert set(full) <= set(TRADE_LABELS)
