"""Z7a-2 zone lifecycle (ATP rule, deliberate change from Trade) and the snapshot with level memory - pure, no database.

Lifecycle over real bar sequences: a touch is not a death; closed through and held = flipped; invalidated only beyond
the structural swing; tested by gap; tracking starts at confirmation (not at formation, where Trade's first touch looks).
Snapshot: level memory across days (carry, keep formed time, advance the tracker), no look-ahead, multi-timeframe
consistency. Fixtures: tests/fixtures/zones (synthetic, no NIFTY data).
"""
import pandas as pd
import pytest

from app.price_action.zones import compute as C
from app.price_action.zones import detect as D
from app.price_action.zones.lifecycle import (RESISTANCE_SIDE, SUPPORT_SIDE, ZoneEvent, ZoneState, ZoneTracker, frame_swings, new_tracker,
                                              origin_side, replay, track_zone, transition)
from app.price_action.zones.settings import resolve_settings
from app.price_action.zones.snapshot import build_snapshot, is_line, live_rows, summary, zone_key
from tests import zones_golden as G

T0 = pd.Timestamp("2026-03-02 09:15")


def path(closes, start=T0, freq="1h", wick=0.5):
    """Bars from a close path: open = previous close, high / low = the body +/- wick."""
    rows, prev = [], closes[0]
    for c in closes:
        o = prev
        rows.append((o, max(o, c) + wick, min(o, c) - wick, c))
        prev = c
    df = pd.DataFrame([tuple(map(float, r)) for r in rows], columns=["open", "high", "low", "close"])
    df.insert(0, "timestamp", pd.date_range(start, periods=len(df), freq=freq))
    return df


# a demand zone 100..105 below a market at 120; a swing low at 89.5 (bars 2-3) is the structure beyond it
BASE = [95, 92, 90, 93, 97, 101, 108, 115, 120]
START = 8                                   # tracking starts after bar 8 (the zone is known there)


def run(closes, settings=None, zone=(100.0, 105.0), side=SUPPORT_SIDE, start=START):
    df = path(closes)
    tr = new_tracker(*zone, side)
    track_zone(tr, df, settings, start_after=df["timestamp"].iloc[start])
    return tr, df


def events(tr):
    return [e["event"] for e in tr.events]


def test_touch_is_not_death_and_counts_visits():
    tr, _ = run(BASE + [118, 112, 106, 103.5, 108, 116, 121, 110, 104, 113, 119])
    assert tr.state == "tested" and events(tr) == ["TOUCH", "TOUCH"] and tr.touches == 2 and tr.flips == 0
    assert tr.structure == 89.5 and tr.invalidated_at is None and tr.side == SUPPORT_SIDE
    # a deep wick below the zone that closes back inside is still only a touch
    tr2, _ = run(BASE + [118, 104, 103, 110])
    assert tr2.state == "tested" and tr2.flips == 0


def test_one_close_through_is_not_a_flip_two_held_closes_are():
    one, _ = run(BASE + [110, 99, 103, 110])
    assert one.state == "tested" and one.flips == 0 and one.beyond == 0
    tr, df = run(BASE + [118, 110, 103, 99, 98, 101, 104.5, 97])
    assert events(tr) == ["TOUCH", "FLIP", "TOUCH"] and tr.state == "flipped" and tr.side == RESISTANCE_SIDE
    assert tr.last_event["event"] == "TOUCH"                         # retested from below: still flipped, not dead
    flip = [e for e in tr.events if e["event"] == "FLIP"][0]
    assert pd.Timestamp(flip["time"]) == df["timestamp"].iloc[13]    # known on the second held close, not before
    assert tr.structure == 120.5                                     # the structure beyond it is now the swing high above


def test_flipped_zone_closed_back_through_and_held_returns_to_tested():
    tr, _ = run(BASE + [118, 110, 103, 99, 98, 101, 106, 107, 111])
    assert events(tr)[:2] == ["TOUCH", "FLIP"] and tr.flips == 2 and tr.state == "tested" and tr.side == SUPPORT_SIDE
    assert transition("flipped", "FLIP") is ZoneState.TESTED and replay(["TOUCH", "FLIP", "FLIP"]) is ZoneState.TESTED


def test_invalidated_only_beyond_the_structural_swing():
    # closes below the zone but above the swing low (89.5): flipped, alive
    alive, _ = run(BASE + [110, 99, 95, 92])
    assert alive.state == "flipped" and alive.invalidated_at is None
    # a close below the swing low: the structure broke - terminal, later bars change nothing
    dead, df = run(BASE + [110, 103, 88, 101, 104, 120])
    assert dead.state == "invalidated" and events(dead) == ["TOUCH", "STRUCTURAL_BREAK"]
    assert pd.Timestamp(dead.invalidated_at) == df["timestamp"].iloc[11]
    again = track_zone(dead, path(BASE + [110, 103, 88, 101, 104, 120, 102, 101]))
    assert again.state == "invalidated" and len(again.events) == 2
    # a flipped zone dies when price closes above the swing high beyond it (120.5 + buffer)
    flipped_dead, _ = run(BASE + [118, 110, 103, 99, 98, 104, 112, 121])
    assert events(flipped_dead) == ["TOUCH", "FLIP", "TOUCH", "STRUCTURAL_BREAK"] and flipped_dead.state == "invalidated"


def test_structure_buffer_and_adoption_of_a_later_swing():
    # no swing low below the zone when tracking starts: the first one confirmed later is adopted, then broken
    s = resolve_settings({"flip_confirm_closes": 5})
    closes = [104, 108, 115, 120, 112, 103, 97, 99, 102, 106, 103, 96, 101, 93]
    tr, df = run(closes, settings=s, start=3)
    assert tr.state == "invalidated" and tr.structure == 96.5 and events(tr)[-1] == "STRUCTURAL_BREAK"
    assert pd.Timestamp(tr.structure_at) == df["timestamp"].iloc[6]
    # the structure is only adopted once confirmed (bar 6 + order 3 = bar 9): the dip at bar 11 is earlier than its break
    early = new_tracker(100, 105, SUPPORT_SIDE)
    track_zone(early, df.iloc[:9], s, start_after=df["timestamp"].iloc[3])
    assert early.structure is None
    # a close just under the swing low but inside the buffer does not break it
    s2 = resolve_settings({"structure_buffer_pct": 5.0})
    tr2, _ = run(BASE + [110, 103, 88, 101], settings=s2)
    assert tr2.state != "invalidated"


def test_gap_through_is_tested_by_gap():
    df = path(BASE + [118, 120, 96, 106])
    df.loc[11, ["open", "high", "low", "close"]] = [96.0, 97.0, 93.0, 96.0]   # close 120 -> open 96: jumped across 100..105
    tr = new_tracker(100.0, 105.0, SUPPORT_SIDE)
    track_zone(tr, df, start_after=df["timestamp"].iloc[START])
    assert events(tr) == ["GAP_THROUGH", "TOUCH"] and tr.first_touch["event"] == "GAP_THROUGH" and tr.state == "tested"


def test_tracking_starts_at_confirmation_not_formation():
    # bearish candle (the order block) at 25, two small bullish candles dipping into it, the impulse at 28
    calm = [(100, 105, 95, 100)] * 25
    rows = calm + [(101, 103, 96, 97), (97, 99, 96.5, 98), (98, 100, 97, 99.5), (99.5, 145, 99, 142), (142, 150, 138, 148), (148, 152, 141, 146)]
    df = pd.DataFrame([tuple(map(float, r)) for r in rows], columns=["open", "high", "low", "close"])
    df.insert(0, "timestamp", pd.date_range(T0, periods=len(df), freq="1h"))
    rows = [r for r in C.compute_all_zones({"1h": df}) if r["zone_type"] == "BULLISH_OB"]
    assert len(rows) == 1
    r = rows[0]
    assert pd.Timestamp(r["formed_at"]) == df["timestamp"].iloc[25] and pd.Timestamp(r["confirmed_at"]) == df["timestamp"].iloc[28]
    assert r["first_touch"] is not None and r["first_touch"]["bar"] == 27       # Trade's rule: FILLED before it was even confirmed
    assert r["state"] == "fresh" and r["touches"] == 0                          # ATP: tracked from confirmation only


def test_line_levels_get_a_lifecycle_band_and_origin_side_by_price():
    assert origin_side("DEMAND_ZONE", 1, 2, None) == SUPPORT_SIDE and origin_side("DOWN_GAP", 1, 2, None) == RESISTANCE_SIDE
    assert origin_side("SUPPORT", 100, 100, 105) == SUPPORT_SIDE and origin_side("DYNAMIC_SR_SUPPORT", 100, 100, 95) == RESISTANCE_SIDE
    with pytest.raises(ValueError):
        origin_side("SUPPORT", 100, 100, None)
    F = G.frames()
    rows = C.compute_all_zones({"1h": F["s1h"], "15m": F["s15"]}, {"15m": F["s15"]})
    lines = [r for r in rows if is_line(r["zone_type"])]
    assert lines and all(r["state"] == "fresh" and r["tracker"]["band_low"] < r["zone_low"] < r["tracker"]["band_high"] for r in lines)


@pytest.mark.parametrize("name", ["s1h", "s15", "bn"])
def test_lifecycle_no_look_ahead_and_resume_equals_one_pass(name):
    df = G.frames()[name]
    s = resolve_settings({})
    zones = D.detect_order_blocks(df, s) + D.detect_demand_supply_zones(df, s)
    assert zones
    full_sw = frame_swings(df, s)
    for z in zones[:25]:
        side = origin_side(z["zone_type"], z["zone_low"], z["zone_high"], None)
        one = track_zone(new_tracker(z["zone_low"], z["zone_high"], side), df, s, start_after=z["confirmed_at"], swings=full_sw)
        for t in range(z["confirmed_bar"] + 5, len(df), 61):
            cut = df.iloc[:t + 1]
            part = track_zone(new_tracker(z["zone_low"], z["zone_high"], side), cut, s, start_after=z["confirmed_at"])
            # every event known at t is the same as in the full pass, and none is later than t
            known = [e for e in one.events if pd.Timestamp(e["time"]) <= cut["timestamp"].iloc[-1]]
            assert part.events == known[-s.max_events_kept:]
            # resuming the stored tracker over the later bars gives the one-pass answer
            resumed = track_zone(ZoneTracker.from_dict(part.to_dict()), df, s)
            assert resumed.to_dict() == one.to_dict()


def test_multi_timeframe_a_touch_on_1h_is_seen_on_15m_within_that_hour():
    F = G.frames()
    s15, s1h = F["s15"], F["s1h"]
    s = resolve_settings({})
    checked = 0
    for z in D.detect_order_blocks(s1h, s) + D.detect_demand_supply_zones(s1h, s):
        side = origin_side(z["zone_type"], z["zone_low"], z["zone_high"], None)
        hour = track_zone(new_tracker(z["zone_low"], z["zone_high"], side), s1h, s, start_after=z["confirmed_at"])
        quarter = track_zone(new_tracker(z["zone_low"], z["zone_high"], side), s15, s,
                             start_after=pd.Timestamp(z["confirmed_at"]) + pd.Timedelta(minutes=45))
        if hour.first_touch is not None:
            checked += 1
            assert quarter.first_touch is not None
            assert pd.Timestamp(quarter.first_touch["time"]) <= pd.Timestamp(hour.first_touch["time"]) + pd.Timedelta(minutes=45)
    assert checked >= 10


# --- snapshot: level memory across days ----------------------------------------------------------------------------------
def _sessions(df):
    dates = pd.to_datetime(df["timestamp"]).dt.date
    return sorted(dates.unique())


def _cut(F, day):
    stop = pd.Timestamp(day) + pd.Timedelta(days=1)
    return {k: F[f][F[f]["timestamp"] < stop].reset_index(drop=True) for k, f in (("15m", "s15"), ("30m", "s30"), ("1h", "s1h"))}


SNAP = {"dynamic_sr_timeframes": ["15m", "30m", "1h"], "dyn_min_bars": 60}


def test_snapshot_remembers_levels_across_days_and_advances_their_lifecycle():
    F = G.frames()
    s = resolve_settings(SNAP)
    prev = None
    history = []
    for day in _sessions(F["s15"])[4:]:
        cut = _cut(F, day)
        rows = build_snapshot({"1h": cut["1h"], "15m": cut["15m"]}, cut, s, previous=prev)
        assert len({r["zone_key"] for r in rows}) == len(rows)          # one row per identity
        history.append({r["zone_key"]: r for r in rows})
        prev = rows
    lines = [r for snap in history for r in snap.values() if is_line(r["zone_type"])]
    remembered = [r for r in lines if r["remembered"]]
    assert remembered, "some levels are carried although not found that day"
    # a carried level keeps its price and its first formation time, and its tracker only moves forward
    carried = 0
    for a, b in zip(history, history[1:]):
        for key, r in b.items():
            if is_line(r["zone_type"]) and key in a:
                carried += 1
                assert r["formed_at"] == a[key]["formed_at"] and r["zone_low"] == a[key]["zone_low"]
                assert r["touches"] >= a[key]["touches"] and pd.Timestamp(r["tracker"]["last_bar_at"]) >= pd.Timestamp(a[key]["tracker"]["last_bar_at"])
            if key in a and a[key]["state"] == "invalidated" and is_line(r["zone_type"]):
                pytest.fail("an invalidated level was remembered")
    assert carried > 50
    states = {r["state"] for r in lines}
    assert {"fresh", "tested"} <= states                                    # S/R rows get a lifecycle (not always fresh)
    assert summary(prev) and len(live_rows(prev)) <= len(prev)


def test_snapshot_without_memory_is_todays_levels_only():
    F = G.frames()
    days = _sessions(F["s15"])
    first = _cut(F, days[10])
    second = _cut(F, days[11])
    s_off = resolve_settings({**SNAP, "memory_enabled": False})
    prev = build_snapshot({"1h": first["1h"], "15m": first["15m"]}, first, s_off)
    rows = build_snapshot({"1h": second["1h"], "15m": second["15m"]}, second, s_off, previous=prev)
    assert not any(r["remembered"] for r in rows)
    assert all(pd.Timestamp(r["formed_at"]) == second[r["timeframe"]]["timestamp"].iloc[-1] for r in rows if is_line(r["zone_type"]))


def test_snapshot_as_of_t_uses_only_bars_up_to_t():
    F = G.frames()
    days = _sessions(F["s15"])
    s = resolve_settings(SNAP)
    cut = _cut(F, days[15])
    t = cut["15m"]["timestamp"].iloc[-1]
    rows = build_snapshot({"1h": cut["1h"], "15m": cut["15m"]}, cut, s)
    for r in rows:
        for when in (r["formed_at"], r["confirmed_at"], r["invalidated_at"], (r["last_event"] or {}).get("time"),
                     r["tracker"]["last_bar_at"], r["tracker"]["structure_at"]):
            assert when is None or pd.Timestamp(when) <= t
    # the same snapshot from frames that also hold later bars, cut first, is identical
    later = {k: F[f] for k, f in (("15m", "s15"), ("30m", "s30"), ("1h", "s1h"))}
    recut = {k: v[v["timestamp"] <= t].reset_index(drop=True) for k, v in later.items()}
    assert build_snapshot({"1h": recut["1h"], "15m": recut["15m"]}, recut, s) == rows


def test_zone_key_is_stable_identity():
    line = {"zone_type": "DYNAMIC_SR_SUPPORT", "timeframe": "15m", "zone_low": 101.25, "zone_high": 101.25, "formed_at": T0}
    assert zone_key(line) == zone_key({**line, "zone_type": "DYNAMIC_SR_RESISTANCE"})         # its name follows the price
    assert zone_key(line) != zone_key({**line, "formed_at": T0 + pd.Timedelta(days=3)})         # found again later = new level
    assert zone_key(line) != zone_key({**line, "zone_type": "SUPPORT"})
    ob = {"zone_type": "BULLISH_OB", "timeframe": "1h", "zone_low": 1.0, "zone_high": 2.0, "formed_at": T0}
    assert zone_key(ob) != zone_key({**ob, "formed_at": T0 + pd.Timedelta(hours=1)})
    assert ZoneEvent("TOUCH") is ZoneEvent.TOUCH


def test_memory_keeps_remembered_levels_on_a_night_with_no_level_found():
    from app.price_action.zones.memory import apply_memory
    df = path([100 + (i % 7) for i in range(60)], freq="15min")
    prev = [{"zone_type": "DYNAMIC_SR_SUPPORT", "timeframe": "15m", "zone_low": 101.0, "zone_high": 101.0, "strength": 3,
             "formed_at": df["timestamp"].iloc[10], "state": "tested"},
            {"zone_type": "DYNAMIC_SR_SUPPORT", "timeframe": "15m", "zone_low": 102.0, "zone_high": 102.0, "strength": 3,
             "formed_at": df["timestamp"].iloc[10], "state": "invalidated"}]
    out = apply_memory([], prev, {"15m": df}, df["timestamp"].iloc[-1])
    assert [(r["zone_low"], r["remembered"], r["formed_at"]) for r in out] == [(101.0, True, df["timestamp"].iloc[10])]   # never the dead one
    assert apply_memory([], prev, {"15m": df}, df["timestamp"].iloc[-1], resolve_settings({"memory_enabled": False})) == []


def test_leaving_the_zone_with_a_gap_is_not_a_new_visit():
    df = path(BASE + [118, 110, 104])
    df = pd.concat([df, pd.DataFrame([{"timestamp": df["timestamp"].iloc[-1] + pd.Timedelta(hours=1), "open": 112.0, "high": 114.0,
                                       "low": 111.0, "close": 113.0}])], ignore_index=True)       # inside at 104, opens above at 112
    tr = new_tracker(100.0, 105.0, SUPPORT_SIDE)
    track_zone(tr, df, start_after=df["timestamp"].iloc[START])
    assert events(tr) == ["TOUCH"] and tr.touches == 1


def test_snapshot_memory_families_carry_forward_and_unseen_bars():
    F = G.frames()
    days = _sessions(F["s15"])
    first, second = _cut(F, days[10]), _cut(F, days[11])
    s = resolve_settings(SNAP)                                           # default memory_families: dynamic S/R only (Trade)
    prev = build_snapshot({"1h": first["1h"], "15m": first["15m"]}, first, s)
    rows = build_snapshot({"1h": second["1h"], "15m": second["15m"]}, second, s, previous=prev)
    classic = [r for r in rows if r["zone_type"] in ("SUPPORT", "RESISTANCE")]
    assert classic and not any(r["remembered"] for r in classic)
    assert all(pd.Timestamp(r["formed_at"]) == second["1h"]["timestamp"].iloc[-1] for r in classic)    # today's, as Trade
    both = resolve_settings({**SNAP, "memory_families": ["SR", "DYNAMIC_SR"]})
    prev2 = build_snapshot({"1h": first["1h"], "15m": first["15m"]}, first, both)
    rows2 = build_snapshot({"1h": second["1h"], "15m": second["15m"]}, second, both, previous=prev2)
    kept = {zone_key(r) for r in prev2 if r["zone_type"] in ("SUPPORT", "RESISTANCE")} & \
        {zone_key(r) for r in rows2 if r["zone_type"] in ("SUPPORT", "RESISTANCE")}
    assert kept                                                          # classical levels carried when asked for
    # tonight's 30m frame is missing: yesterday's live 30m levels are carried forward unchanged, not dropped
    no30 = {k: v for k, v in second.items() if k != "30m"}
    rows3 = build_snapshot({"1h": second["1h"], "15m": second["15m"]}, no30, s, previous=prev)
    carried = [r for r in rows3 if r["timeframe"] == "30m"]
    old30 = [r for r in prev if r["timeframe"] == "30m" and is_line(r["zone_type"]) and r["state"] != "invalidated"]
    assert old30 and {zone_key(r) for r in carried} == {zone_key(r) for r in old30} and all(r["remembered"] for r in carried)
    # a frame that starts after the last bar a remembered level saw: the stretch is recorded, never skipped silently
    late = {k: v.iloc[-20:].reset_index(drop=True) for k, v in second.items()}         # tonight only, from 10:30
    stale = [r for r in prev if r["timeframe"] == "15m" and is_line(r["zone_type"])]
    tr = ZoneTracker.from_dict(stale[0]["tracker"])
    track_zone(tr, late["15m"], s)
    assert tr.unseen and tr.unseen[-1]["after"] == stale[0]["tracker"]["last_bar_at"]
