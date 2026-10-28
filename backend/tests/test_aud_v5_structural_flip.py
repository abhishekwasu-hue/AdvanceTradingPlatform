"""AUD-V5 (owner audit 28 Oct): a stale "BULLISH (Weakening)" must not persist all day while calls pile up and puts sit
flat. The owner's 28 Oct numbers: Call 488.61 L, Put 369.53 L, delta -57.38 L.

Trade's rotation rule needs puts to FALL >= 2 % (twice); the structural flip (settings.structural_flip_min_rise_pct, on by
default in ATP's banner) flips on the diff moving towards the new side on each of the last `confirm_count` snapshots with
the new side's OI up >= that %. The pure function keeps Trade's rule when the parameter is None (golden parity)."""
from app.option_chain import oi_regime as oi
from app.option_chain.oi_regime import Direction, OIRegimeSettings, OISignal, SnapshotPoint, Strength

L = 1e5
PUT = 369.53 * L
WEAK_BULL = OISignal(Direction.BULLISH, Strength.WEAKENING)
# Calls climb, puts flat; diff = put - call falls each step to the owner's -119.08 L (last step -57.38 L).
CALLS = [379.53 * L, 404.53 * L, 431.23 * L]
HISTORY = [SnapshotPoint(diff=PUT - c, total_put_oi=PUT, total_call_oi=c, signal=WEAK_BULL) for c in CALLS]
NOW_CALL = 488.61 * L
NOW_DIFF = PUT - NOW_CALL


def test_owner_numbers():
    assert round((NOW_DIFF - HISTORY[-1].diff) / L, 2) == -57.38


def test_trades_rule_alone_holds_the_stale_bullish():
    got = oi.compute_oi_signal_with_hysteresis(NOW_DIFF, PUT, NOW_CALL, HISTORY)
    assert got == WEAK_BULL


def test_the_structural_flip_turns_it_bearish():
    got = oi.compute_oi_signal_with_hysteresis(NOW_DIFF, PUT, NOW_CALL, HISTORY, structural_flip_min_rise_pct=2.0)
    assert got.direction == Direction.BEARISH
    assert OIRegimeSettings().structural_flip_min_rise_pct == 2.0                         # on in the banner by default


def test_no_flip_when_the_new_side_did_not_rise_enough_or_the_diff_did_not_keep_moving():
    flat_calls = [SnapshotPoint(diff=PUT - 480 * L, total_put_oi=PUT, total_call_oi=480 * L, signal=WEAK_BULL)] * 3
    assert oi.compute_oi_signal_with_hysteresis(PUT - 481 * L, PUT, 481 * L, flat_calls, structural_flip_min_rise_pct=2.0) == WEAK_BULL
    zigzag = [HISTORY[0], HISTORY[2], HISTORY[1]]                                            # diff went back up once
    assert oi.compute_oi_signal_with_hysteresis(NOW_DIFF, PUT, NOW_CALL, zigzag, structural_flip_min_rise_pct=2.0) == WEAK_BULL


def test_the_mirror_flip_to_bullish():
    call = 400 * L
    puts = [390 * L, 405 * L, 420 * L]
    weak_bear = OISignal(Direction.BEARISH, Strength.WEAKENING)
    hist = [SnapshotPoint(diff=p - call, total_put_oi=p, total_call_oi=call, signal=weak_bear) for p in puts]
    got = oi.compute_oi_signal_with_hysteresis(450 * L - call, 450 * L, call, hist, structural_flip_min_rise_pct=2.0)
    assert got.direction == Direction.BULLISH
