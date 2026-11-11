"""M99 P1: the Supply & Demand pack upgrade (app/price_action/sd_zones.py zone kinds and swap level,
app/price_action/sd_odds.py entries / compression / higher-timeframe tag / odds score), behind `sd_pack`.

Generic shape fixtures with our own numbers (positive and negative cases): the swing-extreme zone with its one-candle
confirmation, the two-candle engulfing zone, the swap level after a strong break, compression into a zone, the
higher-timeframe tag, the three entry types and the odds score with its parts. No look-ahead (a cut frame gives the same
values up to the cut), and the scanner, the template (so the bot) and the chart layer read the same evaluation."""
from __future__ import annotations

import warnings

import numpy as np
import pytest

from app.charts import sd_zones_layer as szl
from app.price_action import sd_odds as so
from app.price_action import sd_zones as sd
from app.quality.lookahead import synthetic_bars
from app.screener import compile_screen
from app.screener import sd_scanners as sds
from app.screener.parser import parse
from app.screener.runtime import SymbolData, evaluate, run_screen
from tests.test_m97_sd_zones import AWAY, BASE, LEG_DOWN, OUT_UP, STATE_PATH, _frame, _mirror

RISE = [(100.4, 102.0, 100.2, 101.8), (101.8, 103.0, 101.5, 102.8)]
HH_BULL = (102.8, 105.0, 102.6, 104.2)            # the swing high: a bull candle, body 102.8 .. 104.2
TOUCH = (104.2, 104.6, 102.0, 102.4)              # touches the zone (high >= 102.8), no new high
CONFIRM = (102.4, 102.7, 101.0, 101.3)            # the first candle that does not touch (high 102.7 < 102.8)
BELOW = (101.3, 101.6, 100.4, 100.8)
SWING = dict(kind="swing", swing_left=10)
LOOSE = "leg_mult=1, out_mult=1, leg_body_pct=50, base_body_pct=90, base_mult=0.9"


def _sd(candles, **settings):
    df = _frame(candles)
    s = sd.settings_from(settings)
    return df, sd.track(df, sd.detect(df, s), s)


def _ev(text, df, tf="5m"):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return evaluate(parse(text), SymbolData("S", {tf: df}), base_tf=tf).to_numpy()


# ================================================================================ 1. the swing-extreme zone
def test_swing_supply_is_the_extreme_candle_known_on_the_first_candle_that_does_not_touch():
    df, zones = _sd([*RISE, HH_BULL, TOUCH, CONFIRM, BELOW], **SWING)
    (z,) = zones
    e = 21 + 2
    assert (z.side, z.formation, z.kind) == (sd.SUPPLY, "SWING", "swing")
    assert (z.proximal, z.distal) == (102.8, 105.0)                    # the high down to the far body edge (the open)
    assert (z.base_start, z.base_end, z.formed) == (e, e, e + 2)       # the touching candle does not confirm it
    assert z.depart == e + 1                                           # the widest candle up to the confirming one


def test_swing_demand_is_the_mirror():
    df, zones = _sd(_mirror([*RISE, HH_BULL, TOUCH, CONFIRM, BELOW]), **SWING)
    (z,) = zones
    assert (z.side, z.proximal, z.distal) == (sd.DEMAND, pytest.approx(200 - 102.8), pytest.approx(200 - 105.0))


def test_a_new_extreme_before_the_confirmation_replaces_the_candidate():
    higher = (104.2, 105.5, 103.0, 103.2)                              # a higher high on the next candle
    _, zones = _sd([*RISE, HH_BULL, higher, CONFIRM, BELOW], **SWING)
    (z,) = zones
    assert (z.proximal, z.distal) == (103.2, 105.5)                    # the new extreme's own zone, not the old one


def test_no_confirming_candle_within_the_limit_makes_no_zone():
    _, zones = _sd([*RISE, HH_BULL, TOUCH, TOUCH, TOUCH, CONFIRM, BELOW], **SWING, confirm_within=3)
    assert zones == []
    _, zones = _sd([*RISE, HH_BULL, TOUCH, TOUCH, TOUCH, CONFIRM, BELOW], **SWING, confirm_within=4)
    assert len(zones) == 1


def test_a_swing_is_beyond_the_candles_before_it():
    _, zones = _sd([(100.4, 106.0, 100.2, 105.0), (105.0, 105.2, 101.0, 101.2), *RISE, HH_BULL, TOUCH, CONFIRM, BELOW], **SWING)
    assert all(z.distal != 105.0 for z in zones)                       # 105 is below the 106 high before it


# ================================================================================ 2. the two-candle engulfing zone
PREV_BULL = (102.8, 104.0, 102.6, 103.8)
BEAR_ENGULF = (104.0, 105.0, 102.4, 102.5)                             # opens >= 103.8, closes <= 102.8, high 105


def test_engulfing_supply_is_two_candles_from_the_high_to_the_previous_open():
    df, zones = _sd([*RISE, PREV_BULL, BEAR_ENGULF, CONFIRM, BELOW], **SWING)
    (z,) = zones
    e = 21 + 3
    assert (z.formation, z.kind, z.proximal, z.distal) == ("ENGULF", "engulf", 102.8, 105.0)
    assert (z.base_start, z.base_end, z.formed) == (e - 1, e, e + 1)


def test_engulfing_demand_is_the_mirror_and_off_gives_the_one_candle_zone():
    _, (z,) = _sd(_mirror([*RISE, PREV_BULL, BEAR_ENGULF, CONFIRM, BELOW]), **SWING)
    assert (z.side, z.formation, z.proximal, z.distal) == (sd.DEMAND, "ENGULF", pytest.approx(97.2), pytest.approx(95.0))
    _, (one,) = _sd([*RISE, PREV_BULL, BEAR_ENGULF, CONFIRM, BELOW], **SWING, engulf=False)
    assert (one.formation, one.proximal, one.distal) == ("SWING", 102.5, 105.0)
    assert one.formed == 21 + 3 + 2                                     # 102.7 still touches 102.5: one candle later


def test_kind_both_keeps_both_zone_kinds_apart():
    candles = [*STATE_PATH[:5], *RISE, HH_BULL, TOUCH, CONFIRM, BELOW]
    _, base = _sd(candles)
    _, both = _sd(candles, kind="both", swing_left=5)
    assert [z.kind for z in base] == ["base"]
    assert {z.kind for z in both} >= {"base", "swing"} and len(both) > len(base)
    assert [z.formed for z in both] == sorted(z.formed for z in both)


# ================================================================================ 3. the swap level
BREAK = (101.5, 101.6, 95.0, 95.2)                 # range 6.6: a strong candle closing below the distal line 96.6
AWAY_DOWN = (95.2, 95.6, 94.4, 94.8)
RETEST = (94.8, 96.9, 94.6, 95.8)                  # the first return from below: high 96.9 >= 96.6
SWAP_PATH = [LEG_DOWN, *BASE, OUT_UP, AWAY, BREAK, AWAY_DOWN, RETEST, AWAY_DOWN]


def test_a_strong_break_leaves_the_box_as_a_swap_zone_of_the_other_side():
    df, zones = _sd(SWAP_PATH, swap=True)
    demand, swap = zones
    v = 21 + 5
    assert demand.violated == v and (swap.side, swap.formation, swap.kind, swap.swap_of) == (sd.SUPPLY, "SWAP", "swap", 0)
    assert (swap.proximal, swap.distal, swap.formed) == (96.6, 97.7, v)  # the same box, the near line now its low
    assert swap.visits == [v + 2] and swap.state_at(v + 1) == sd.FRESH and swap.state_at(v + 2) == sd.TESTED


def test_no_swap_when_off_or_when_the_break_is_weak():
    assert len(_sd(SWAP_PATH)[1]) == 1                                  # reference: swap off
    weak = (97.0, 97.2, 96.0, 96.3)                                     # range 1.2 < 1.5 x the average
    _, zones = _sd([LEG_DOWN, *BASE, OUT_UP, AWAY, (101.5, 101.6, 96.9, 97.0), weak, AWAY_DOWN], swap=True)
    assert zones[0].violated is not None and [z.kind for z in zones] == ["base"]
    _, zones = _sd([LEG_DOWN, *BASE, OUT_UP, AWAY, (101.5, 101.6, 96.9, 97.0), weak, AWAY_DOWN], swap=True, swap_mult=0)
    assert [z.kind for z in zones] == ["base", "swap"]                  # 0: any violating close


def test_break_retest_reads_only_swap_zones_and_touch_never_does():
    df, _ = _sd(SWAP_PATH, swap=True)
    v = 21 + 5
    br = _ev('SDZoneEntry("supply", "break_retest", swap=TRUE)', df)
    assert list(np.flatnonzero(br)) == [v + 2]
    assert not _ev('SDZoneEntry("supply", "touch", swap=TRUE)', df).any()
    assert not _ev('SDZoneEntry("supply", "break_retest")', df).any()  # swap off: no swap zone
    ref = _ev('SDZoneEntryRef("supply", "distal", "break_retest", swap=TRUE)', df)
    assert ref[v + 2] == 97.7 and np.isnan(np.delete(ref, v + 2)).all()


# ================================================================================ 4. compression into a zone
SUPPLY_ZONE = _mirror([LEG_DOWN, *BASE, OUT_UP, AWAY])                 # an RBD: near line 102.3, far line 103.4
STAIRS = [(98.5, 99.3, 98.2, 99.1), (99.1, 99.8, 98.6, 99.6), (99.6, 100.4, 99.2, 100.2), (100.2, 100.9, 99.7, 100.7),
          (100.7, 101.6, 100.3, 101.4)]
INTO = (101.4, 102.6, 101.1, 101.9)                                     # touches 102.3


def test_small_steps_up_into_supply_are_compression():
    df = _frame([*SUPPLY_ZONE, *STAIRS, INTO])
    hit = _ev('SDZoneCompression("supply")', df)
    assert list(np.flatnonzero(hit)) == [len(df) - 1]
    assert not _ev('SDZoneCompression("demand")', df).any()


def test_a_wide_approach_candle_or_too_few_steps_is_not_compression():
    wide = list(STAIRS)
    wide[3] = (99.0, 101.9, 98.9, 101.7)                                # range 3 > 1.0 x the average range
    assert not _ev('SDZoneCompression("supply")', _frame([*SUPPLY_ZONE, *wide, INTO])).any()
    flat = [(99.1, 99.8, 98.6, 99.6)] * 5                               # no higher lows
    assert not _ev('SDZoneCompression("supply")', _frame([*SUPPLY_ZONE, *flat, INTO])).any()
    assert _ev('SDZoneCompression("supply", comp_mult=2)', _frame([*SUPPLY_ZONE, *wide, INTO])).any()   # his own setting


def test_compression_into_demand_is_the_mirror():
    df = _frame(_mirror([*SUPPLY_ZONE, *STAIRS, INTO]))
    assert list(np.flatnonzero(_ev('SDZoneCompression("demand")', df))) == [len(df) - 1]


# ================================================================================ 5. the higher-timeframe tag
def test_a_higher_timeframe_zone_counts_from_its_close_until_its_violation():
    ltf = np.arange(1, 11, dtype=np.int64) * 60                         # ten lower bars closing at 60 .. 600
    htf_close = np.array([300, 480], dtype=np.int64)                    # two higher bars closing at 300 and 480
    big = sd.SDZone(sd.DEMAND, "DBR", 100.0, 95.0, 0, 0, 0, 0, violated=1)
    ctx = so.htf_context("1h", [big], htf_close, ltf)
    assert (ctx.known[0], ctx.dead[0]) == (4, 7)                        # known on the bar closing at 300, dead from 480
    small = sd.SDZone(sd.DEMAND, "DBR", 97.0, 96.0, 0, 0, 0, 0)
    with_, against = so.htf_relation_mask(small, ctx, np.arange(10))
    assert list(np.flatnonzero(with_)) == [4, 5, 6] and not against.any()
    other = sd.SDZone(sd.SUPPLY, "RBD", 96.5, 98.0, 0, 0, 0, 0)
    assert list(np.flatnonzero(so.htf_relation_mask(other, ctx, np.arange(10))[1])) == [4, 5, 6]
    apart = sd.SDZone(sd.DEMAND, "DBR", 90.0, 89.0, 0, 0, 0, 0)        # no overlap: no tag (never merged)
    assert not so.htf_relation_mask(apart, ctx, np.arange(10))[0].any()


def test_the_higher_timeframe_read_through_screenql_matches_the_shared_evaluation():
    df = synthetic_bars(900, tf="5m", seed=11)
    d = SymbolData("S", {"5m": df})
    got = evaluate(parse(f'SDZoneHtfWith("demand", "any", {LOOSE}, htf="1h")'), d, base_tf="5m").to_numpy()
    assert got.any() and not evaluate(parse(f'SDZoneHtfWith("demand", "any", {LOOSE})'), d, base_tf="5m").any()
    with pytest.raises(Exception):
        evaluate(parse(f'SDZoneHtfWith("demand", "any", {LOOSE}, htf="3m")'), d, base_tf="5m")   # not higher than 5m


# ================================================================================ 6. the entry types
def test_touch_confirm_and_their_settings():
    df = _frame(STATE_PATH)
    f = 21 + 3
    assert list(np.flatnonzero(_ev('SDZoneEntry("demand", "touch")', df))) == [f + 2]
    assert list(np.flatnonzero(_ev('SDZoneEntry("demand", "confirm")', df))) == [f + 4]          # body 80 %, close above 97.7
    assert list(np.flatnonzero(_ev('SDZoneEntry("demand", "confirm", confirm_body_pct=20)', df))) == [f + 3]
    assert not _ev('SDZoneEntry("demand", "confirm", confirm_bars=2)', df).any()                 # too late for his window
    assert list(np.flatnonzero(_ev('SDZoneEntry("demand")', df))) == list(np.flatnonzero(_ev('SDZoneEnter("demand")', df)))
    ref = _ev('SDZoneEntryRef("demand", "proximal", "confirm")', df)
    assert ref[f + 4] == 97.7 and np.isnan(np.delete(ref, f + 4)).all()


def test_no_confirmation_once_the_zone_is_violated():
    dead = [*STATE_PATH[:5], (101.0, 101.2, 95.8, 96.0), (96.0, 99.0, 95.9, 98.9)]   # visit and violation on one candle
    assert not _ev('SDZoneEntry("demand", "confirm")', _frame(dead)).any()


# ================================================================================ 7. the odds score
def test_the_score_parts_are_listed_and_weighted():
    df = _frame(STATE_PATH)
    f = 21 + 3
    s = sd.SDSettings()
    v = so.view(df, s, so.OddsSettings())
    (z,) = v.zones
    m = v.leg_out_mult(z)
    leg = min(1.0, m / 3.0)
    at_touch = v.breakdown(z, f + 2)
    assert [p["name"] for p in at_touch["parts"]] == list(so.PARTS) and at_touch["htf"] is None
    parts = {p["name"]: p["value"] for p in at_touch["parts"]}
    assert parts == {"fresh": 1.0, "leg_out": pytest.approx(leg, abs=1e-3), "base_time": 0.8, "htf": None, "compression": 0.0}
    assert at_touch["score"] == pytest.approx(round(100 * (1 + leg + 0.8 + 0) / 4, 1))       # htf not counted without htf
    second = v.breakdown(z, f + 5)                                       # one test before this visit
    assert {p["name"]: p["value"] for p in second["parts"]}["fresh"] == 0.5
    only_fresh = so.view(df, s, so.odds_from({"w_leg_out": 0, "w_base_time": 0, "w_compression": 0}))
    assert only_fresh.breakdown(only_fresh.zones[0], f + 5)["score"] == 50.0
    none = so.view(df, s, so.odds_from({k: 0 for k in ("w_fresh", "w_leg_out", "w_base_time", "w_htf", "w_compression")}))
    assert none.breakdown(none.zones[0], f + 2)["score"] is None


def test_the_score_is_information_only_it_never_changes_a_signal():
    df = _frame(STATE_PATH)
    plain = _ev('SDZoneEntry("demand", "touch")', df)
    weighted = _ev('SDZoneEntry("demand", "touch", w_fresh=0, w_leg_out=9, strong_out_mult=10)', df)
    assert (plain == weighted).all()
    score = _ev('SDZoneScore("demand")', df)
    assert np.isfinite(score[21 + 3:21 + 10]).all() and np.isnan(score[:21 + 3]).all()


def test_odds_settings_are_bounded():
    for bad in ({"w_fresh": -1}, {"comp_bars": 2.5}, {"comp_steps": 6, "comp_bars": 5}, {"nope": 1}, {"confirm_body_pct": 101}):
        with pytest.raises(ValueError):
            so.odds_from(bad)
    for bad in ('SDZoneEntry("demand", "early")', 'SDZoneScore("demand", w_fresh=11)', 'SDZoneHtfWith("demand", htf="2d")',
                'SDZoneEntry("demand", kind="all")', 'SDZoneEnter("demand", swap_mult=6)', 'SDZoneEntryRef("demand", "score")'):
        assert not compile_screen(bad, base_tf="5m")[1].ok, bad
    assert compile_screen('SDZoneScore("demand", "fresh", htf="1d", kind="both", swap=TRUE) >= 60', base_tf="15m")[1].ok


# ================================================================================ no look-ahead
@pytest.mark.parametrize("seed", [5, 9])
def test_a_cut_frame_gives_the_same_reads(seed):
    df = synthetic_bars(800, tf="5m", seed=seed)
    s = sd.settings_from({"leg_mult": 1, "out_mult": 1, "leg_body_pct": 50, "base_body_pct": 90, "base_mult": 0.9,
                          "kind": "both", "swing_left": 5, "swap": True, "swap_mult": 0})
    o = so.odds_from({"confirm_body_pct": 0, "comp_bars": 4, "comp_steps": 2, "comp_mult": 3})

    def reads(frame):
        v = so.view(frame, s, o, series=sd.sd_series(frame, s))
        return {"touch": v.entries(sd.DEMAND, "touch")[0], "confirm": v.entries(sd.SUPPLY, "confirm")[0],
                "retest": v.entries(sd.DEMAND, "break_retest")[0], "ref": v.entry_ref(sd.SUPPLY, "touch", "distal"),
                "score": v.nearest_score(sd.DEMAND, "any"), "comp": v.compression_hits(sd.SUPPLY)}
    full = reads(df)
    assert full["touch"].any() and full["confirm"].any() and full["retest"].any() and full["comp"].any()
    for t in range(150, len(df), 97):
        cut = reads(df.iloc[:t + 1])
        for name, value in full.items():
            np.testing.assert_array_equal(cut[name], value[:t + 1], err_msg=f"{name} at {t}")


# ================================================================================ one evaluation everywhere
def test_scanner_template_and_chart_read_the_same_evaluation():
    from tests.test_m97_pr3_templates import VISIT_BAR, frame, run
    df = frame()
    sig = run("I1", df, entry="confirm")
    assert [(t, int(sig.direction[t]), round(float(sig.invalidation[t]), 4)) for t in sig.signal_bars()] == [(VISIT_BAR + 1, 1, 96.6)]
    params = {p.name: p.value for p in (*sds.SETTINGS, *sds.ODDS)}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        scan = evaluate(parse(f'SDZoneEntry("demand", "confirm", {sds.KWARGS}, {sds.ODDS_KWARGS})'), SymbolData("S", {"1d": df}),
                        base_tf="1d", params=params).to_numpy()
    assert list(np.flatnonzero(scan)) == [VISIT_BAR + 1]
    chart = _frame(STATE_PATH[:6])
    rows = szl.zone_rows(chart, "5m")
    (row,) = rows["zones"]
    assert row["kind"] == "base" and row["odds"]["score"] == pytest.approx(round(_ev('SDZoneScore("demand")', chart)[-1], 1))
    assert [p["name"] for p in row["odds"]["parts"]] == list(so.PARTS)


def test_the_chart_tags_against_the_next_higher_drawn_timeframe():
    df = synthetic_bars(900, tf="5m", seed=11)
    from app.screener.runtime import resample
    hdf = resample(df, "5m", "1h")
    rows = szl._compute([("5m", df, None), ("1h", hdf, None)], False, sd.settings_from({"leg_mult": 1, "out_mult": 1,
                        "leg_body_pct": 50, "base_body_pct": 90, "base_mult": 0.9}))
    assert all(r["odds"]["htf"]["timeframe"] == "1h" for r in rows[0]["zones"]) and rows[0]["zones"]
    assert all(r["odds"]["htf"] is None for r in rows[1]["zones"])
    assert {r["odds"]["htf"]["relation"] for r in rows[0]["zones"]} <= {"with", "against", "none"}


# ================================================================================ the five new scanners
def _match(sc, df, tf="5m", **values):
    params = {**sc.defaults(), **values}
    ast, validated = compile_screen(sc.text, base_tf=tf, params=params)
    assert validated.ok, [p.message for p in validated.problems]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return run_screen(ast, validated, [SymbolData("S", {tf: df})], base_tf=tf, params=params, check_history=False)[0].matched


def test_the_p1_scanners_positive_and_negative():
    assert _match(sds.Z6, _frame(STATE_PATH[:6]), htf="none") and not _match(sds.Z6, _frame(STATE_PATH[:5]), htf="none")
    assert _match(sds.Z6, _frame(STATE_PATH[:8]), entry="confirm", htf="none")
    assert not _match(sds.Z6, _frame(STATE_PATH[:7]), entry="confirm", htf="none")
    assert _match(sds.Z7, _frame(SWAP_PATH[:-1]), htf="none") and not _match(sds.Z7, _frame(SWAP_PATH[:-2]), htf="none")
    assert _match(sds.Z8, _frame([*SUPPLY_ZONE, *STAIRS, INTO]), htf="none")
    assert not _match(sds.Z8, _frame([*SUPPLY_ZONE, *STAIRS]), htf="none")
    fresh = _frame(STATE_PATH[:5])
    assert _match(sds.Z9, fresh, min_score=10, htf="none") and not _match(sds.Z9, fresh, min_score=99, htf="none")
    assert sds.Z10.defaults()["htf"] == "1w"
    walk = synthetic_bars(900, tf="5m", seed=11)
    loose = dict(leg_mult=1, out_mult=1, leg_body_pct=50, base_body_pct=90, base_mult=0.9)
    both = [_ev(f'SDZoneHtfWith("{side}", "fresh", {LOOSE}, htf="1h")', walk) for side in ("demand", "supply")]
    hits = np.flatnonzero(both[0] | both[1])
    assert len(hits) and len(hits) < len(walk)
    for t in (int(hits[-1]), int(np.flatnonzero(~(both[0] | both[1]))[-1])):   # the scanner on the last bar = the read
        assert _match(sds.Z10, walk.iloc[:t + 1], htf="1h", **loose) == bool(both[0][t] or both[1][t])


def test_every_new_setting_is_a_parameter_with_a_reference_value():
    for sc in sds.SCANNERS[5:]:
        names = [p.name for p in sc.params]
        for name, *_ in (*sd.SETTINGS_TABLE, *so.ODDS_TABLE):
            assert name in names and f"{name}=${name}" in sc.text, (sc.id, name)
        assert all(p.as_dict()["note"] in (sds.REF_LABEL, "Not set", "ATP default") for p in sc.params)
    assert dict((p.name, p.value) for p in sds.Z7.params)["swap"] is True        # the swap scanner turns swap levels on
    assert [(n, r) for n, _, r, _ in so.ODDS_TABLE] == [(f.name, getattr(so.OddsSettings(), f.name))
                                                       for f in so.OddsSettings.__dataclass_fields__.values()]


def test_the_new_files_are_english_and_carry_no_source_names():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    for rel in ("app/price_action/sd_odds.py", "app/price_action/sd_zones.py", "tests/test_m99_p1_sd.py"):
        text = (root / rel).read_text(encoding="utf-8")
        assert text.isascii(), rel
