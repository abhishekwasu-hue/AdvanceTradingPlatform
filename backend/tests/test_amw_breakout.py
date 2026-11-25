import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from app.screener.breakouts.amw_breakout import (
    WEIGHTS,
    Candidate,
    Candle,
    Divergence,
    Settings,
    Snapshot,
    Zone,
    candle_metrics,
    replay,
    zone_gate,
)

BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)


def snapshot(i=0, side=1, *, close=101.0, candidate=False, zones=(), divs=(), **kwargs):
    flip = lambda value: value if side == 1 else 200 - value
    candle = Candle(flip(close - 0.8), max(flip(close + 0.1), flip(close - 0.9)),
                    min(flip(close + 0.1), flip(close - 0.9)), flip(close), 1.0, 1.5, "genuine")
    return Snapshot(
        BASE + timedelta(minutes=15 * i), candle,
        (Candidate(f"test/{side}", side, 100.0, ("B1",)),) if candidate else (),
        zones, divs, kwargs.get("zones_ready", True), kwargs.get("rsi_ready", True), side, float(side),
    )


def run(*snaps, **cfg):
    return list(replay(snaps, Settings(**cfg)))


class TestBreakout(unittest.TestCase):
    def test_mirror_default_and_direction_selector(self):
        for side in (1, -1):
            with self.subTest(side=side):
                s = snapshot(side=side, candidate=True)
                d, = run(s)
                self.assertEqual((d.side, d.score, d.grade, d.state), (side, 100, "A1", "SIGNAL_GENERATED"))
                self.assertEqual(run(s, direction="LONG_ONLY" if side == -1 else "SHORT_ONLY"), [])
                self.assertEqual(len(run(s, direction="LONG_ONLY" if side == 1 else "SHORT_ONLY")), 1)

    def test_strict_buffer_and_invalid_atr(self):
        base = snapshot(candidate=True)
        self.assertEqual(run(replace(base, candle=replace(base.candle, close=100.25)), buffer=0.25), [])
        for atr in (None, 0, float("nan"), -1):
            self.assertEqual(run(replace(base, candle=replace(base.candle, atr=atr))), [])

    def test_zone_boundaries_mirror_and_missing(self):
        for side in (1, -1):
            s = snapshot(side=side)
            c = s.candle.close
            for distance, status in ((0, "BLOCK"), (0.5, "BLOCK"), (1, "WAIT"), (2, "WAIT"), (2.01, "PASS")):
                edge = c + side * distance
                lo, hi = sorted((edge, edge + side))
                z = Zone("z", -side, lo, hi, "1h")
                self.assertEqual(zone_gate(replace(s, zones=(z,)), side, Settings())[0], status)
            self.assertEqual(zone_gate(replace(s, zones_ready=False), side, Settings())[0], "DATA_UNAVAILABLE")
            self.assertEqual(zone_gate(s, side, Settings())[1], "NO_OPPOSING_ZONE_IN_SCOPE")

    def test_rechecks_clearance_and_extension(self):
        z = Zone("contained", -1, 100.5, 103, "1h")
        first = snapshot(candidate=True, zones=(z,))
        later = snapshot(1, close=102)
        last = snapshot(2)
        checks = run(first, later, last)
        self.assertEqual([d.reason for d in checks], ["INSIDE_OPPOSING_ZONE", "EXCESSIVE_EXTENSION", "ELIGIBLE"])
        self.assertEqual(checks[-1].detected_at, BASE)
        self.assertEqual(checks[-1].at, last.at)

    def test_reclaim_expiry_and_no_repeat(self):
        first = snapshot(candidate=True, zones_ready=False)
        self.assertEqual(run(first, snapshot(1, close=99.9))[-1].state, "INVALIDATED")
        self.assertEqual(run(first, *(snapshot(i, zones_ready=False) for i in range(1, 7)))[-1].state, "EXPIRED")
        self.assertEqual(run(first, *(snapshot(i, zones_ready=i == 5) for i in range(1, 6)))[-1].state, "SIGNAL_GENERATED")
        self.assertEqual(len(run(snapshot(candidate=True), snapshot(1, candidate=True))), 1)

    def test_divergence_profiles_and_conflict(self):
        for side in (1, -1):
            for kind in ("regular", "hidden"):
                compatible = Divergence(side, kind, True)
                opposite = Divergence(-side, kind, True)
                base = snapshot(side=side, candidate=True, divs=(compatible,))
                self.assertEqual(run(base, profile="BO-02")[-1].state, "SIGNAL_GENERATED")
                mixed = replace(base, divergences=(compatible, opposite))
                self.assertEqual(run(mixed, profile="BO-02")[-1].reason, "OPPOSITE_DIVERGENCE")
                self.assertEqual(run(replace(base, divergences=(replace(compatible, active=False),)),
                                     profile="BO-02")[-1].reason, "COMPATIBLE_DIVERGENCE_REQUIRED")
                self.assertEqual(run(mixed, profile="BO-04")[-1].reason, "OPPOSITE_DIVERGENCE")
                self.assertEqual(run(mixed, profile="BO-01")[-1].state, "SIGNAL_GENERATED")

    def test_confirmation_modes_prefix_parity(self):
        for side in (1, -1):
            first = snapshot(side=side, candidate=True)
            second = replace(snapshot(1, side=side), retested_keys=(first.candidates[0].key,))
            for config in ({"profile": "BO-03"}, {"acceptance": "FOLLOW_THROUGH"}):
                full = run(first, second, **config)
                self.assertEqual(full[:1], run(first, **config))
                self.assertEqual(full[-1].at, second.at)
                self.assertEqual(full[-1].state, "SIGNAL_GENERATED")

    def test_zero_range_and_settings(self):
        self.assertIsNone(candle_metrics(Candle(1, 1, 1, 1, 1), 1))
        self.assertTrue(all(sum(weights) == 100 for weights in WEIGHTS.values()))
        self.assertEqual(Settings(demand_tfs=("1H",)).version, Settings().version)
        for kwargs in ({"acceptance": "AUTO_SELECT"}, {"supply_tfs": ("4h",)}, {"zone_block": 2}, {"buffer": float("nan")}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                Settings(**kwargs)


if __name__ == "__main__":
    unittest.main()


def test_confluence_does_not_merge_distinct_breakout_levels():
    bar = snapshot(candidate=True)
    bar = replace(bar, candidates=(
        Candidate("b2", 1, 100.0, ("B2",)),
        Candidate("same", 1, 100.05, ("HORIZONTAL",)),
        Candidate("distinct", 1, 100.4, ("ZONE",)),
    ))
    signals = [item for item in run(bar) if item.state == "SIGNAL_GENERATED"]
    assert {item.key: item.evidence["sources"] for item in signals} == {
        "b2": ("B2", "HORIZONTAL"), "distinct": ("ZONE",),
    }
