"""OI Banner O1: the ported OI-regime math. Golden cases come from the Trade repo's own functions
(fixtures/oi_regime/golden.json, captured by capture_from_trade.py); the named cases are Trade's tests/test_oi_analysis.py
in English."""
import json
import pathlib
import re
from datetime import date, datetime, timedelta

import pytest

from app.brokers.models import OptionChain, OptionChainRow
from app.option_chain import oi_regime as oi
from app.option_chain.oi_regime import Direction, OIClass, OISignal, SnapshotPoint, Strength

GOLDEN = json.loads((pathlib.Path(__file__).resolve().parent / "fixtures" / "oi_regime" / "golden.json").read_text(encoding="utf-8"))
SRC = pathlib.Path(__file__).resolve().parents[1] / "app" / "option_chain" / "oi_regime.py"


def _dir(value):
    return None if value is None else Direction(value)


def _chain(items, underlying_ltp=None):
    rows = [OptionChainRow(strike=i["strike_price"], call_oi=i["call_options"]["market_data"]["oi"], call_ltp=i["call_options"]["market_data"]["ltp"],
                           put_oi=i["put_options"]["market_data"]["oi"], put_ltp=i["put_options"]["market_data"]["ltp"]) for i in items]
    return OptionChain(underlying="X", expiry="", underlying_ltp=underlying_ltp, rows=rows)


def _history(rows):
    return [SnapshotPoint(r[0], r[1], r[2], OISignal.parse(r[3])) for r in rows]


# ---------------------------------------------------------------- golden: same inputs, same outputs as Trade

def test_golden_classification_and_rotation():
    for case in GOLDEN["classify"]:
        assert oi.classify_oi_price_action(*case["in"]).value == case["out"], case
    for case in GOLDEN["rotation"]:
        assert oi.is_genuine_rotation(*case["in"]) == _dir(case["out"]), case
    for case in GOLDEN["rotation2"]:
        assert oi.rotation_confirmed_for_2_snapshots([tuple(r) for r in case["in"]]) == _dir(case["out"]), case
    seen = {c["out"] for c in GOLDEN["classify"]}
    assert seen == {c.value for c in OIClass}                                  # every class is exercised


def test_golden_hysteresis_sequences_replay_identically():
    flips = 0
    for steps in GOLDEN["hysteresis"]:
        held = []
        for step in steps:
            got = oi.compute_oi_signal_with_hysteresis(step["diff"], step["put"], step["call"], held[-5:])
            want = OISignal.parse(step["signal"])
            assert got == want, (steps, step)
            if held and got.direction != held[-1].signal.direction:
                flips += 1
            held.append(SnapshotPoint(step["diff"], step["put"], step["call"], got))
    assert flips > 0                                                           # the fixture contains confirmed flips


def test_golden_pcr_matrix_levels_and_gates():
    for case in GOLDEN["pcr"]:
        pcr, bias = oi.compute_pcr_signal(*case["in"])
        assert (pcr, bias.value) == (case["pcr"], case["bias"]), case
    for case in GOLDEN["matrix"]:
        got = oi.compute_oi_price_matrix(*case["in"])
        assert (got.category, got.bias.value, got.strength) == (case["out"]["category"], case["out"]["bias"], case["out"]["strength"]), case
    for case in GOLDEN["psych"]:
        assert oi.find_psychological_level(*case["in"]) == case["out"], case
    for case in GOLDEN["gate"]:
        direction, label = case["in"]
        assert oi.check_oi_diff_entry_gate(direction, OISignal.parse(label)) == case["diff_gate"], case
        if label is not None:                                                  # missing data: ATP fails closed (see below)
            assert oi.check_oi_confirmation(direction, OISignal.parse(label), "A")[0] == case["confirm_a"], case


def test_golden_swing_gate_rollover_and_max_pain():
    for case in GOLDEN["swing"]:
        d, matrix, pcr_bias, mp, price, roll, max_opposing = case["in"]
        m = oi.OIPriceMatrix(matrix["category"], Direction(matrix["bias"]), matrix["strength"]) if matrix else None
        r = oi.RolloverProxy(None, None, Direction(roll["bias"]), 0, 0) if roll else None
        ok, detail = oi.swing_oi_gate(d, m, _dir(pcr_bias), mp, price, r, max_opposing)
        assert (ok, len(detail.supporting), len(detail.opposing), detail.total_signals) == (case["ok"], case["supporting"], case["opposing"], case["total"]), case
    for case in GOLDEN["rollover"]:
        got = oi.compute_rollover_proxy(_chain(case["near"]), _chain(case["next"]), case["atm"])
        want = case["out"]
        assert (got.rollover_pct, got.cost_of_carry, got.bias.value, got.near_expiry_total_oi, got.next_expiry_total_oi) == (
            want["rollover_pct"], want["cost_of_carry"], want["bias"], want["near_expiry_total_oi"], want["next_expiry_total_oi"]), case
    for case in GOLDEN["max_pain"]:                                            # ATP's existing max pain, Trade's answers
        assert oi.max_pain(_chain(case["chain"]).rows) == case["out"]


# ---------------------------------------------------------------- Trade's named cases, in English

def test_hysteresis_named_cases():
    assert oi.compute_oi_signal_with_hysteresis(5_000_000, 100, 90, []) == OISignal(Direction.BULLISH, Strength.WEAKENING)
    bearish = _history([(-100, 500, 600, "BEARISH (Strong)"), (-80, 505, 620, "BEARISH (Strong)"), (-50, 508, 615, "BEARISH (Strong)")])
    assert oi.compute_oi_signal_with_hysteresis(20, 520, 610, bearish).direction == Direction.BEARISH        # one reversal does not flip
    turning = _history([(-50, 508, 615, "BEARISH (Strong)"), (10, 520, 600, "BEARISH (Strong)"), (15, 545, 580, "BEARISH (Strong)")])
    assert oi.compute_oi_signal_with_hysteresis(25, 570, 555, turning) == OISignal(Direction.BULLISH, Strength.STRONG)
    no_rotation = _history([(-50, 508, 615, "BEARISH (Strong)"), (10, 509, 614, "BEARISH (Strong)"), (15, 510, 613, "BEARISH (Strong)")])
    assert oi.compute_oi_signal_with_hysteresis(25, 511, 612, no_rotation) == OISignal(Direction.BEARISH, Strength.WEAKENING)
    strong = _history([(26.90e5, 134.13e5, 107.24e5, "BULLISH (Strong)"), (14.96e5, 111.32e5, 96.36e5, "BULLISH (Strong)"),
                       (3.13e5, 152.58e5, 149.45e5, "BULLISH (Strong)")])
    # a deep opposite diff while unconfirmed: the old direction, Weakening - never a false Strong
    assert oi.compute_oi_signal_with_hysteresis(-45.44e5, 135.40e5, 180.85e5, strong) == OISignal(Direction.BULLISH, Strength.WEAKENING)


def test_a_real_sequence_never_shows_strong_against_the_diff_sign():
    held = []
    for diff, put, call in [(-15.10e5, 285.56e5, 300.67e5), (-15.10e5, 285.56e5, 300.67e5), (44.33e5, 405.41e5, 361.07e5),
                            (-28.25e5, 385.24e5, 413.49e5), (11.18e5, 457.15e5, 445.97e5), (70.71e5, 511.57e5, 440.85e5)]:
        sig = oi.compute_oi_signal_with_hysteresis(diff, put, call, held)
        if sig.strength == Strength.STRONG:
            assert (diff > 0) == (sig.direction == Direction.BULLISH)
        held.append(SnapshotPoint(diff, put, call, sig))


def test_confirm_count_one_and_settings_are_honoured():
    prev = _history([(-10, 100, 110, "BEARISH (Strong)")])
    # confirm_count=1 needs no earlier same-sign diffs, but still the two-comparison rotation (three rows)
    assert oi.compute_oi_signal_with_hysteresis(10, 120, 110, prev, confirm_count=1).direction == Direction.BEARISH
    two = _history([(-10, 100, 120, "BEARISH (Strong)"), (-5, 110, 110, "BEARISH (Strong)")])
    assert oi.compute_oi_signal_with_hysteresis(15, 125, 100, two, confirm_count=1) == OISignal(Direction.BULLISH, Strength.STRONG)
    # a looser rotation threshold lets a smaller move confirm
    assert oi.compute_oi_signal_with_hysteresis(25, 511, 612, no_rotation_history(), rotation_min_change_pct=0.1).direction == Direction.BULLISH


def no_rotation_history():
    return _history([(-50, 508, 615, "BEARISH (Strong)"), (10, 509, 614, "BEARISH (Strong)"), (15, 510, 613, "BEARISH (Strong)")])


@pytest.mark.parametrize("label,bullish,bearish", [
    ("BULLISH (Strong)", True, False), ("BULLISH (Weakening)", False, True), ("BEARISH (Strong)", False, True),
    ("BEARISH (Weakening)", True, False), ("NEUTRAL", False, False), (None, False, False)])
def test_entry_gate(label, bullish, bearish):
    sig = OISignal.parse(label)
    assert oi.check_oi_diff_entry_gate("BULLISH", sig) is bullish and oi.check_oi_diff_entry_gate("BEARISH", sig) is bearish


def test_confirmation_gate_fails_closed_and_strict_mode_can_pass():
    assert oi.check_oi_confirmation("BULLISH", None) == (False, "OI data unavailable or stale")         # Trade skipped the gate
    assert oi.check_oi_confirmation("BULLISH", OISignal(Direction.BULLISH, Strength.STRONG), "B")[0] is True
    assert oi.check_oi_confirmation("BULLISH", OISignal(Direction.BULLISH, Strength.WEAKENING), "B")[0] is False
    assert oi.check_oi_confirmation("BULLISH", OISignal(Direction.BULLISH, Strength.WEAKENING), "A")[0] is True
    assert oi.check_oi_confirmation("BEARISH", OISignal(Direction.BULLISH, Strength.WEAKENING), "A")[0] is False
    assert oi.check_oi_confirmation("BULLISH", oi.NEUTRAL_SIGNAL, "A")[0] is True


def test_banner_messages_and_classification():
    put = oi.classify_oi_price_action(90_000_000, 85_000_000, 4000, 4200)
    call = oi.classify_oi_price_action(70_000_000, 75_000_000, 3200, 3000)
    assert (put, call) == (OIClass.WRITING, OIClass.SHORT_COVERING)
    sig = oi.generate_oi_price_signal(put, call, "BANKNIFTY")
    assert sig.direction == Direction.BULLISH
    assert sig.message == "Put writing rising + Call short covering → avoid shorting calls; BANKNIFTY bias bullish"
    bear = oi.generate_oi_price_signal(OIClass.BUYING, OIClass.INSUFFICIENT_DATA, "SENSEX")
    assert bear.direction == Direction.BEARISH and bear.message == "Put buying rising → avoid shorting puts; SENSEX bias bearish"
    assert oi.generate_oi_price_signal(OIClass.WRITING, OIClass.WRITING, "X").message == oi.MIXED_MESSAGE
    assert oi.generate_oi_price_signal(OIClass.FLAT, OIClass.INSUFFICIENT_DATA, "X").message == oi.NO_ACTIVITY_MESSAGE
    assert oi.classify_oi_price_action(102, 100, 101, 100, oi_threshold_pct=5) == OIClass.FLAT        # thresholds are parameters
    assert oi.classify_oi_price_action(None, 100, 1, 1) == OIClass.INSUFFICIENT_DATA


def test_reconcile_named_cases():
    bear = oi.generate_oi_price_signal(OIClass.INSUFFICIENT_DATA, OIClass.WRITING, "NIFTY")
    mixed = oi.reconcile_with_diff_level(bear, 71_246_000, 182_521_000, 253_767_000)
    assert mixed.direction == Direction.MIXED and "Put OI higher, +71,246,000" in mixed.message
    assert oi.reconcile_with_diff_level(bear, -50_000_000, 200_000_000, 150_000_000) == bear                  # consistent
    assert oi.reconcile_with_diff_level(bear, 5_000_000, 200_000_000, 210_000_000) == bear                    # trivial
    neutral = oi.generate_oi_price_signal(OIClass.FLAT, OIClass.FLAT, "NIFTY")
    assert oi.reconcile_with_diff_level(neutral, 100_000_000, 100_000_000, 300_000_000) == neutral
    assert oi.reconcile_with_diff_level(bear, 0, 0, 0) == bear
    bull = oi.generate_oi_price_signal(OIClass.LONG_UNWINDING, OIClass.SHORT_COVERING, "NIFTY")
    vs_stable = oi.reconcile_with_diff_level(bull, -50_000_000, 200_000_000, 150_000_000, OISignal(Direction.BEARISH, Strength.STRONG))
    assert vs_stable.direction == Direction.MIXED and "confirmed trend (BEARISH)" in vs_stable.message
    agree = oi.reconcile_with_diff_level(bull, 50_000_000, 150_000_000, 200_000_000, OISignal(Direction.BULLISH, Strength.STRONG))
    assert agree == bull
    assert oi.reconcile_with_diff_level(bear, 71_246_000, 182_521_000, 253_767_000, significance_pct=50) == bear


def test_pcr_bands_edges_and_configuration():
    edges = {0.6: oi.PcrBand.OVERSOLD, 0.7: oi.PcrBand.MILD_BEARISH, 0.89: oi.PcrBand.MILD_BEARISH, 0.9: oi.PcrBand.SIDEWAYS,
             1.0: oi.PcrBand.SIDEWAYS, 1.01: oi.PcrBand.MILD_BULLISH, 1.3: oi.PcrBand.MILD_BULLISH, 1.31: oi.PcrBand.OVERBOUGHT}
    for pcr, band in edges.items():
        assert oi.pcr_band(pcr) == band, pcr
    assert "oversold" in oi.compute_pcr_zone_label(0.88).lower() and "overbought" not in oi.compute_pcr_zone_label(0.88).lower()
    assert "overbought" in oi.compute_pcr_zone_label(1.15).lower() and "oversold" not in oi.compute_pcr_zone_label(1.15).lower()
    assert "Bullish" not in oi.compute_pcr_zone_label(0.6) and "Bearish" not in oi.compute_pcr_zone_label(1.5)
    assert oi.compute_pcr_zone_label(None) == "Insufficient data"
    wide = oi.PcrBands(oversold_below=0.5, mild_bearish_below=0.8, sideways_max=1.1, mild_bullish_max=1.6)
    assert oi.pcr_band(0.6, wide) == oi.PcrBand.MILD_BEARISH and oi.compute_pcr_signal(1500, 1000, wide)[1] == Direction.BULLISH
    with pytest.raises(ValueError):
        oi.PcrBands(oversold_below=0.9, mild_bearish_below=0.7)


def test_settings_layering_and_validation():
    s = oi.resolve_settings(None, {"confirm_count": 4, "pcr_bands": {"oversold_below": 0.6}}, {"strike_step": 25})
    assert s.confirm_count == 4 and s.strike_step == 25 and s.pcr_bands.oversold_below == 0.6 and s.pcr_bands.mild_bearish_below == 0.9
    assert oi.resolve_settings() == oi.OIRegimeSettings()
    with pytest.raises(ValueError):
        oi.resolve_settings({"no_such_setting": 1})
    with pytest.raises(ValueError):
        oi.resolve_settings({"atm_range": 0})


def _rows(step, n=11, centre=None, call=1000, put=2000, ltp=1.0):
    centre = centre if centre is not None else step * 400
    return [OptionChainRow(strike=centre + (i - n // 2) * step, call_oi=call, put_oi=put, call_ltp=ltp, put_ltp=ltp) for i in range(n)]


@pytest.mark.parametrize("step,spot", [(50, 24510.0), (100, 72910.0), (25, 12990.0), (2.5, 401.0)])
def test_strike_step_per_underlying_gives_the_same_window(step, spot):
    """Underlyings with different steps: the inferred step and the ATM window agree (ATM +- 6 strikes = 13 strikes)."""
    rows = _rows(step, n=41, centre=round(spot / step) * step)
    chain = OptionChain(underlying="U", expiry="", underlying_ltp=spot, rows=rows)
    settings = oi.OIRegimeSettings()
    assert oi.resolve_strike_step(settings, chain) == step
    totals = oi.summarise_chain(chain, oi.resolve_strike_step(settings, chain), settings.atm_range)
    assert totals.atm_strike == round(spot / step) * step and len(totals.strikes) == 13
    assert totals.total_call_oi == 13 * 1000 and totals.total_put_oi == 13 * 2000 and totals.diff == 13 * 1000
    explicit = oi.summarise_chain(chain.model_copy(update={"underlying_ltp": totals.atm_strike}), step / 2, 6)            # an explicit (smaller) step narrows the window, as in the source
    assert len(explicit.strikes) == 7
    assert oi.summarise_chain(OptionChain(underlying="U", expiry="", rows=rows), step, 6) is None
    assert oi.infer_strike_step([]) is None


def test_wall_confirmation_and_psychological_level():
    rows = [OptionChainRow(strike=24500, call_oi=1200, put_oi=800), OptionChainRow(strike=24550, call_oi=10, put_oi=10)]
    ok, detail = oi.check_oi_wall_confirmation(rows, 24480, "BEARISH", 50, baseline_oi=1000)
    assert ok and detail.target_strike == 24500 and detail.side == "CE" and detail.chg_oi == 200
    ok, detail = oi.check_oi_wall_confirmation(rows, 24500, "BULLISH", 50, baseline_oi=900)
    assert not ok and detail.side == "PE" and detail.chg_oi == -100
    assert oi.check_oi_wall_confirmation(rows, 24500, "BULLISH", 50, baseline_oi=None)[0] is False           # no baseline yet
    assert oi.check_oi_wall_confirmation(rows, 30000, "BULLISH", 50, 0)[1].reason == "strike not in the chain"
    assert oi.find_psychological_level(24500, "BEARISH", 500) == 25000 and oi.find_psychological_level(24500, "BULLISH", 500) == 24000
    s = oi.OIRegimeSettings()
    assert oi.psychological_round_to(s, 50) == 500 and oi.psychological_round_to(oi.OIRegimeSettings(psychological_round_to=200), 50) == 200


def test_pcr_and_iv_gates_fail_closed_on_stale_or_missing_data():
    now = datetime(2026, 1, 15, 11, 0)
    assert oi.is_stale(None, now, 15) and oi.is_stale(now - timedelta(minutes=45), now, 15) and not oi.is_stale(now - timedelta(minutes=2), now, 15)
    assert oi.check_pcr_gate(None, "BULLISH", 0.8, 1.1)[0] is False
    assert oi.check_pcr_gate(0.75, "BULLISH", 0.8, 1.1)[:2] == (False, 0.75)
    assert oi.check_pcr_gate(0.95, "BULLISH", 0.8, 1.1)[0] is True and oi.check_pcr_gate(0.95, "BEARISH", 0.8, 1.1)[0] is True
    assert oi.check_pcr_gate(1.2, "BEARISH", 0.8, 1.1)[:2] == (False, 1.2)
    assert oi.check_iv_change_gate(None, 15)[0] is False
    assert oi.check_iv_change_gate(oi.IvChange(11, 10, 10.0, 5), 15) == (True, 10.0, "IV gate passed")
    assert oi.check_iv_change_gate(oi.IvChange(11.5, 10, 15.0, 5), 15)[0] is False                          # at the limit blocks
    assert "breakout" in oi.check_iv_change_gate(oi.IvChange(13, 10, 30.0, 5), 15)[2]


def test_iv_baseline_uses_only_sideways_sessions_before_today():
    today = date(2026, 3, 10)
    sideways = dict(open=100, high=110, low=90, close=101)                     # body ratio 0.05
    trending = dict(open=90, high=110, low=90, close=110)                      # marubozu
    history = [oi.DayIv(today - timedelta(days=i), iv, **(trending if i == 2 else sideways))
               for i, iv in [(1, 10.0), (2, 30.0), (3, 12.0), (4, None), (5, 14.0)]] + [oi.DayIv(today, 99.0, **sideways)]
    got = oi.iv_change_from_average(13.0, history, today, lookback_days=3)
    assert got.days_in_baseline == 2 and got.baseline_avg_iv == 11.0 and round(got.change_pct, 4) == round(2 / 11 * 100, 4)
    assert oi.iv_change_from_average(13.0, history, today, lookback_days=10).baseline_avg_iv == 12.0
    assert oi.iv_change_from_average(13.0, [d for d in history if d.day == today], today) is None
    assert oi.body_ratio(5, 5, 5, 5) == 0.0
    chain = OptionChain(underlying="U", expiry="", underlying_ltp=24510, rows=[OptionChainRow(strike=24500, call_iv=12.0, put_iv=14.0),
                                                                              OptionChainRow(strike=24550, call_iv=99.0)])
    assert oi.atm_iv(chain) == 13.0


def test_aggregation_takes_the_last_value_per_bucket():
    base = datetime(2026, 1, 15, 10, 0)
    points = [(base + timedelta(minutes=m), 5000 + m * 20) for m in (0, 5, 10, 15)]
    ten = oi.aggregate_history(points, 10, lambda p: p[0])
    assert [(b.strftime("%H:%M"), p[1]) for b, p in ten] == [("10:10", 5300), ("10:00", 5100)]
    assert len(oi.aggregate_history(points, 15, lambda p: p[0])) == 2 and len(oi.aggregate_history(points, 5, lambda p: p[0])) == 4
    assert oi.aggregate_history([], 10, lambda p: p[0]) == []
    assert oi.slot_start(datetime(2026, 1, 15, 10, 7, 31), 5) == datetime(2026, 1, 15, 10, 5)


def test_evaluate_snapshot_builds_the_banner():
    settings = oi.OIRegimeSettings()
    chain = OptionChain(underlying="U", expiry="2026-01-29", underlying_ltp=24510.0, rows=_rows(50, n=21, centre=24500))
    first = oi.summarise_chain(chain, 50, settings.atm_range)
    state = oi.evaluate_snapshot("UNDERLYING", first, None, [], settings, max_pain_strike=24500, expiry=chain.expiry, today=date(2026, 1, 15))
    assert state.first_of_day and state.message == oi.INSUFFICIENT_HISTORY_MESSAGE and state.direction == Direction.NEUTRAL
    assert state.pcr == 2.0 and state.pcr_band == oi.PcrBand.OVERBOUGHT and state.dte == 14 and state.expiry == date(2026, 1, 29)
    later = oi.summarise_chain(chain.model_copy(update={"rows": _rows(50, n=21, centre=24500, put=2200, ltp=0.95)}), 50, settings.atm_range)
    hist = [SnapshotPoint(first.diff, first.total_put_oi, first.total_call_oi, state.stable_signal)]
    nxt = oi.evaluate_snapshot("UNDERLYING", later, first, hist, settings)
    assert nxt.put_class == OIClass.WRITING and nxt.direction == Direction.BULLISH and "UNDERLYING bias bullish" in nxt.message
    assert nxt.delta_diff == later.diff - first.diff and not nxt.first_of_day and nxt.dte is None


def test_compute_dte():
    assert oi.compute_dte("2026-01-29", date(2026, 1, 29)) == (date(2026, 1, 29), 0)
    assert oi.compute_dte(None, date(2026, 1, 1)) == (None, None) and oi.compute_dte("29-Jan", date(2026, 1, 1)) == (None, None)


def test_no_marathi_and_no_instrument_constants_in_the_module():
    text = SRC.read_text(encoding="utf-8")
    assert not re.search(r"[ऀ-ॿ]", text)
    for name in ("NIFTY", "BANKNIFTY", "SENSEX", "FINNIFTY"):
        assert name not in text
    assert not re.search(r"\b20\d\d-\d\d-\d\d\b", text)


def test_max_pain_tie_resolves_to_the_lowest_strike_whatever_the_row_order():
    rows = [OptionChainRow(strike=200, call_oi=1, put_oi=1), OptionChainRow(strike=100, call_oi=1, put_oi=1)]
    assert oi.max_pain(rows) == 100 and oi.max_pain(list(reversed(rows))) == 100
    assert oi.max_pain([]) is None
