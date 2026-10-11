"""Z7a-1 market zones - behaviour beyond the Trade parity: settings, role by price, the lifecycle (the deliberate change:
a first touch is fresh -> tested, never death), the gap-through event, no look-ahead and multi-timeframe consistency."""
import pandas as pd
import pytest
from pydantic import ValidationError

from app.price_action.zones import compute as C
from app.price_action.zones import detect as D
from app.price_action.zones import levels as L
from app.price_action.zones import sr_v3 as V
from app.price_action.zones.lifecycle import ZoneEvent, ZoneState, is_live, replay, transition
from app.price_action.zones.settings import ZoneSettings, resolve_settings
from tests import zones_golden as G

T0 = pd.Timestamp("2026-03-02 09:15")


def bars(rows, start=T0, freq="1h"):
    """rows [(o, h, l, c)] -> a bar frame stamped every `freq` (or explicit stamps when rows carry 5 items)."""
    if rows and len(rows[0]) == 5:
        return pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close"])
    df = pd.DataFrame([tuple(map(float, r)) for r in rows], columns=["open", "high", "low", "close"])
    df.insert(0, "timestamp", pd.date_range(start, periods=len(df), freq=freq))
    return df


def ob_market():
    """25 calm bars (range 10), a bearish candle, an impulsive up candle (body 40), a rally, then an impulsive pullback
    into the bearish candle - a bullish order block touched later (the pullback also makes a bearish block of the rally top)."""
    calm = [(100, 105, 95, 100)] * 25
    return bars(calm + [(101, 103, 96, 97), (97, 140, 96, 137), (137, 150, 135, 148), (148, 152, 140, 142), (142, 143, 102, 104),
                        (104, 120, 103, 118)])


# --- settings -------------------------------------------------------------------------------------------------------------
def test_settings_defaults_are_the_trade_values():
    s = ZoneSettings()
    assert (s.impulse_mult, s.avg_window, s.avg_min_periods, s.base_lookback, s.base_tightness_mult) == (1.5, 20, 5, 4, 0.7)
    assert (s.sr_swing_order, s.sr_cluster_tolerance_pct, s.sr_top_n, s.major_swing_min_move_pct) == (3, 0.3, 5, 0.5)
    assert (s.dyn_prd, s.dyn_maxnumpp, s.dyn_channel_w_pct, s.dyn_maxnumsr, s.dyn_min_strength, s.dyn_min_bars) == (10, 20, 10, 5, 2, 100)
    assert (s.gap_min_pct, s.sr_timeframe, s.zone_timeframe, s.gap_timeframe) == (0.30, "1h", "1h", "15m")
    assert s.dynamic_sr_timeframes == ["1m", "5m", "15m", "30m", "1h"]
    assert (s.memory_retire_days, s.memory_max_distance_pct, s.memory_max_levels) == (30, 15.0, 12)
    assert s.memory_retire_days_by_tf == {"1m": 2, "5m": 5, "15m": 15, "30m": 30, "1h": 30}
    assert (s.memory_atr_mult, s.memory_tol_min_pct, s.memory_tol_max_pct) == (0.5, 0.08, 0.40)
    v = s.sr_v3
    assert (v.recency_half_life_days, v.tol_atr_mult, v.flip_accept_ratio, v.grade_a, v.grade_b, v.max_levels, v.session_end) == \
        (2.5, 0.5, 0.6, 65.0, 45.0, 12, "15:15")
    assert v.prd == {"5m": 10, "15m": 10, "30m": 10, "1h": 10, "4h": 5, "1d": 5, "1w": 3}


def test_settings_layer_platform_tenant_symbol_and_refuse_bad_input():
    s = resolve_settings({"impulse_mult": 2.0, "sr_v3": {"grade_a": 70}}, {"sr_v3": {"chop_filter": True}}, {"impulse_mult": 1.8})
    assert s.impulse_mult == 1.8 and s.sr_v3.grade_a == 70 and s.sr_v3.chop_filter is True and s.avg_window == 20
    with pytest.raises(ValueError, match="unknown zone setting"):
        resolve_settings({"impulse": 2})
    with pytest.raises(ValueError, match="unknown sr_v3"):
        resolve_settings({"sr_v3": {"grade_z": 1}})
    with pytest.raises(ValidationError):
        resolve_settings({"impulse_mult": -1})
    with pytest.raises(ValidationError):
        resolve_settings({"zone_timeframe": "7m"})
    with pytest.raises(ValidationError):
        resolve_settings({"dynamic_sr_timeframes": ["15m", "2h"]})


# --- role by price ----------------------------------------------------------------------------------------------------------
def test_role_comes_from_the_price_whatever_the_origin():
    assert D.role_by_price(110, 120, 100) == D.ROLE_RESISTANCE        # e.g. a "bullish" order block above the price
    assert D.role_by_price(80, 90, 100) == D.ROLE_SUPPORT             # e.g. a supply zone below the price
    assert D.role_by_price(95, 105, 100) == D.ROLE_SUPPORT            # mid == price -> support (Trade)
    df = ob_market()
    for price in (90.0, 200.0):
        rows = C.compute_all_zones({"1h": df}, settings=resolve_settings({"sr_min_bars": 1000}), current_price=price)
        assert rows and {r["role"] for r in rows} == {D.ROLE_SUPPORT if price > 150 else D.ROLE_RESISTANCE}


# --- lifecycle (the deliberate change) ----------------------------------------------------------------------------------------
def test_lifecycle_transitions():
    F, T, FL, X = ZoneState.FRESH, ZoneState.TESTED, ZoneState.FLIPPED, ZoneState.INVALIDATED
    assert transition(F, ZoneEvent.TOUCH) is T and transition(F, ZoneEvent.GAP_THROUGH) is T
    assert transition(T, ZoneEvent.TOUCH) is T and transition(T, ZoneEvent.FLIP) is FL and transition(F, ZoneEvent.FLIP) is FL
    assert transition(FL, ZoneEvent.TOUCH) is FL and transition(FL, ZoneEvent.GAP_THROUGH) is FL
    for st in (F, T, FL):
        assert transition(st, ZoneEvent.STRUCTURAL_BREAK) is X
    for ev in ZoneEvent:
        assert transition(X, ev) is X                                    # terminal
    assert transition("fresh", "TOUCH") is T
    assert replay(["TOUCH", "TOUCH", "FLIP", "TOUCH"]) is FL and replay([]) is F
    assert is_live(FL) and not is_live(X)
    with pytest.raises(ValueError):
        transition(F, "FILLED")


def test_first_touch_is_an_event_and_the_zone_is_kept_as_tested():
    df = ob_market()
    obs = D.detect_order_blocks(df)
    assert [(z["zone_type"], z["zone_low"], z["zone_high"], z["formed_bar"], z["confirmed_bar"]) for z in obs] == [("BULLISH_OB", 96.0, 103.0, 25, 26),
                                                                                                          ("BEARISH_OB", 135.0, 150.0, 27, 29)]
    rows = [r for r in C.compute_all_zones({"1h": df}) if r["zone_type"] == "BULLISH_OB"]
    assert len(rows) == 1                                                 # Trade would mark it FILLED; ATP keeps it
    r = rows[0]
    assert r["state"] == "tested" and r["first_touch"]["event"] == D.TOUCH and r["first_touch"]["bar"] == 29
    assert r["first_touch"]["time"] == df["timestamp"].iloc[29]
    # as of the bar before the pullback it is still fresh
    early = [r for r in C.compute_all_zones({"1h": df}, as_of=df["timestamp"].iloc[28]) if r["zone_type"] == "BULLISH_OB"]
    assert early[0]["state"] == "fresh" and early[0]["first_touch"] is None


def test_first_touch_event_touch_gap_through_and_none():
    zone = (100.0, 105.0)
    above = bars([(110, 112, 108, 111), (111, 113, 109, 112)])
    assert D.first_touch_event(*zone, above) is None
    assert D.first_touch_event(*zone, above.iloc[:0]) is None
    dip = bars([(110, 112, 108, 111), (111, 111, 104, 106), (106, 107, 99, 100)])
    assert D.first_touch_event(*zone, dip)["event"] == D.TOUCH and D.first_touch_event(*zone, dip)["bar"] == 1
    # close 111 -> next open 97: price jumped across the whole zone without a bar overlapping it
    jump = bars([(110, 112, 108, 111), (97, 98, 95, 96), (96, 99, 94, 98)])
    ev = D.first_touch_event(*zone, jump)
    assert ev["event"] == D.GAP_THROUGH and ev["bar"] == 1
    assert D.first_touch_event(*zone, jump[["timestamp", "high", "low"]]) is None    # no open / close -> overlap only (Trade)


def test_gap_zone_jumped_through_is_tested_by_gap_not_removed():
    day = lambda d, rows: [(pd.Timestamp(f"2026-03-0{d} 09:15") + pd.Timedelta(minutes=15 * i), *map(float, r)) for i, r in enumerate(rows)]
    df = bars(day(2, [(100, 101, 99, 100), (100, 101, 99, 100.5)]) + day(3, [(110, 112, 109, 111), (111, 113, 110, 112)])
              + day(4, [(95, 96, 94, 95), (95, 95.5, 94, 95)]))
    gaps = D.find_overnight_gaps(df)
    assert [(g["zone_type"], g["zone_low"], g["zone_high"]) for g in gaps] == [("UP_GAP", 101.0, 109.0), ("DOWN_GAP", 96.0, 110.0)]
    rows = {r["zone_type"]: r for r in C.compute_all_zones({"15m": df}, settings=resolve_settings({"sr_min_bars": 1000}))}
    up = rows["UP_GAP"]
    assert up["state"] == "tested" and up["first_touch"]["event"] == D.GAP_THROUGH
    assert up["first_touch"]["time"] == pd.Timestamp("2026-03-04 09:15") and up["role"] == D.ROLE_RESISTANCE
    assert rows["DOWN_GAP"]["state"] == "fresh"


def test_session_dates_use_the_exchange_time_zone():
    # 18:30 UTC on the 2nd is 00:00 IST on the 3rd - a new session in exchange time
    ts = pd.Series(pd.to_datetime(["2026-03-02 09:00", "2026-03-02 18:30"]).tz_localize("UTC"))
    assert [str(d) for d in D.session_dates(ts, "Asia/Kolkata")] == ["2026-03-02", "2026-03-03"]
    assert [str(d) for d in D.session_dates(ts, "UTC")] == ["2026-03-02", "2026-03-02"]


# --- no look-ahead ----------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("name", ["s15", "s1h", "bn"])
def test_zones_as_of_t_use_only_bars_up_to_t(name):
    df = G.frames()[name]
    full_ob, full_ds = D.detect_order_blocks(df), D.detect_demand_supply_zones(df)
    key = lambda z: (z["zone_type"], z["zone_low"], z["zone_high"], z["formed_bar"], z["confirmed_bar"])
    for t in range(60, len(df), 47):
        cut = df.iloc[:t + 1]
        assert [key(z) for z in D.detect_order_blocks(cut)] == [key(z) for z in full_ob if z["confirmed_bar"] <= t]
        assert [key(z) for z in D.detect_demand_supply_zones(cut)] == [key(z) for z in full_ds if z["confirmed_bar"] <= t]
        for z in D.detect_order_blocks(cut) + D.detect_demand_supply_zones(cut):
            assert z["formed_bar"] < z["confirmed_bar"] <= t


def test_compute_all_zones_as_of_equals_the_series_cut_at_t():
    F = G.frames()
    frames, dyn = {"1h": F["s1h"], "15m": F["s15"]}, {"15m": F["s15"], "30m": F["s30"], "1h": F["s1h"]}
    for t in F["s15"]["timestamp"].iloc[[300, 450, 600, 749]]:
        got = C.compute_all_zones(frames, dyn, as_of=t)
        cut = C.compute_all_zones({k: v[v["timestamp"] <= t] for k, v in frames.items()}, {k: v[v["timestamp"] <= t] for k, v in dyn.items()})
        assert got == cut
        assert all(pd.Timestamp(r["formed_at"]) <= t and pd.Timestamp(r["confirmed_at"]) <= t for r in got)
        assert all(r["first_touch"] is None or pd.Timestamp(r["first_touch"]["time"]) <= t for r in got)
        # appending future bars never changes a zone known at t (only its later state)
        later = {(r["zone_type"], r["timeframe"], r["zone_low"], r["zone_high"], str(r["formed_at"])) for r in C.compute_all_zones(frames, dyn)
                 if r["zone_type"] not in ("SUPPORT", "RESISTANCE") and not r["zone_type"].startswith("DYNAMIC")}
        assert {(r["zone_type"], r["timeframe"], r["zone_low"], r["zone_high"], str(r["formed_at"])) for r in got
                if r["zone_type"] not in ("SUPPORT", "RESISTANCE") and not r["zone_type"].startswith("DYNAMIC")} <= later


def test_dynamic_sr_and_sr_v3_ignore_bars_after_t():
    F = G.frames()
    w = F["w1"]
    for t in (450, 600, 750):
        cut = w.iloc[:t + 1]
        assert L.compute_dynamic_sr(cut) == L.compute_dynamic_sr(pd.concat([cut, cut.iloc[-1:]]).iloc[:t + 1])
    t = F["s15"]["timestamp"].iloc[500]
    frames = {"15m": F["s15"], "30m": F["s30"], "1h": F["s1h"]}
    res = V.compute_sr_v3({k: v[v["timestamp"] <= t] for k, v in frames.items()})
    assert res["meta"]["now"] <= t
    assert all(z["anchor"] is None or z["anchor"]["ts"] is None or z["anchor"]["ts"] <= t for z in res["levels"])


# --- multi-timeframe consistency ----------------------------------------------------------------------------------------------
def test_intraday_rows_equal_the_dynamic_rows_of_the_full_computation():
    F = G.frames()
    dyn = {"15m": F["s15"], "30m": F["s30"], "1h": F["s1h"]}
    full = [r for r in C.compute_all_zones({"1h": F["s1h"], "15m": F["s15"]}, dyn) if r["zone_type"].startswith("DYNAMIC_SR_")]
    intra = C.compute_intraday_sr_zones(dyn)
    strip = lambda rows: sorted((r["zone_type"], r["timeframe"], r["zone_low"], r["strength"], str(r["formed_at"])) for r in rows)
    assert strip(full) == strip(intra) and {r["timeframe"] for r in intra} == {"15m", "30m", "1h"}


def test_each_timeframe_family_reads_its_own_frame_and_sides_agree_with_price():
    F = G.frames()
    s = resolve_settings({"zone_timeframe": "15m", "sr_timeframe": "30m", "gap_timeframe": "15m"})
    rows = C.compute_all_zones({"15m": F["s15"], "30m": F["s30"]}, {"5m": F["m5"]}, settings=s)
    by = lambda typ: [r for r in rows if r["zone_type"] == typ]
    assert {r["timeframe"] for r in by("BULLISH_OB") + by("DEMAND_ZONE")} == {"15m"}
    assert len(by("BULLISH_OB") + by("BEARISH_OB")) == len(D.detect_order_blocks(F["s15"]))
    assert {r["timeframe"] for r in by("SUPPORT") + by("RESISTANCE")} == {"30m"}
    assert {r["timeframe"] for r in rows if r["zone_type"].startswith("DYNAMIC")} == {"5m"}
    # classical / dynamic levels are split by their own frame's close: support below, resistance at / above
    for df, levels in ((F["s30"], L.find_support_resistance_levels(F["s30"], settings=s)), (F["m5"], L.compute_dynamic_sr(F["m5"], s))):
        last = float(df["close"].iloc[-1])
        assert all(x["level"] < last for x in levels["support"]) and all(x["level"] >= last for x in levels["resistance"])


def test_too_little_data_is_empty_not_an_error():
    tiny = bars([(100, 101, 99, 100)] * 3)
    assert D.detect_order_blocks(tiny) == [] and D.detect_demand_supply_zones(tiny) == [] and D.find_overnight_gaps(tiny.iloc[:1]) == []
    assert L.compute_dynamic_sr(tiny) == {"support": [], "resistance": []}
    assert C.compute_all_zones({"1h": tiny, "15m": tiny}) == []
    assert V.compute_sr_v3({})["levels"] == [] and V.compute_sr_v3({"15m": tiny.drop(columns=["open"])})["levels"] == []
    row = C.compute_confluence_row("5m", None, 100.0)
    assert row["support_level"] is None and row["order_block_type"] is None and "resistance_level_3" in row
