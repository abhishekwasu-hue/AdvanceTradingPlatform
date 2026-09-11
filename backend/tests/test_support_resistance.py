import pandas as pd

from app.support_resistance.engine import SupportResistanceEngine
from app.support_resistance.levels import (
    fibonacci_levels,
    opening_range_zone,
    pivot_levels,
    previous_day_levels,
    vwap_zone,
)
from app.support_resistance.zones import build_swing_zones
from app.price_action.swings import alternate_swings, find_swings
from tests.utils import make_series, noisy_uptrend


def _two_day_series() -> pd.DataFrame:
    day1 = make_series([100 + i * 0.1 for i in range(200)], start="2024-01-02 09:15")
    day2 = make_series([120 + i * 0.1 for i in range(200)], start="2024-01-03 09:15")
    return pd.concat([day1, day2]).sort_index()


def test_build_swing_zones_clusters_repeated_touches():
    # three touches near price 95, three touches near price 130 (resistance)
    prices = [100, 95.1, 105, 94.9, 115, 95.0, 125, 130.2, 118, 129.8, 122, 130.1, 119]
    df = make_series(prices * 3)  # repeat to give the fractal window enough bars either side
    swings = find_swings(df, window=3)
    zones = build_swing_zones(df, swings, timeframe="1min", tolerance_pct=1.0)
    assert any(z.kind == "SUPPORT" and z.touches >= 2 for z in zones)


def test_previous_day_levels_uses_prior_session_high_low():
    df = _two_day_series()
    zones = previous_day_levels(df)
    assert len(zones) == 2
    kinds = {z.kind for z in zones}
    assert kinds == {"SUPPORT", "RESISTANCE"}
    resistance = next(z for z in zones if z.kind == "RESISTANCE")
    # day 1 high should be close to 100 + 199*0.1 = 119.9
    assert 119 < resistance.mid < 121


def test_previous_day_levels_returns_empty_with_single_day():
    df = make_series([100 + i * 0.1 for i in range(50)])
    assert previous_day_levels(df) == []


def test_opening_range_zone_uses_first_n_minutes_of_latest_session():
    df = _two_day_series()
    zones = opening_range_zone(df, minutes=15)
    assert len(zones) == 2
    for z in zones:
        assert z.timeframe == "opening_range"


def test_pivot_levels_returns_seven_levels():
    df = _two_day_series()
    zones = pivot_levels(df)
    sources = {z.source for z in zones}
    assert sources == {"pivot_PP", "pivot_R1", "pivot_R2", "pivot_R3", "pivot_S1", "pivot_S2", "pivot_S3"}


def test_fibonacci_levels_returns_five_levels_for_valid_swing():
    prices = [100] * 4 + list(range(100, 140)) + [140] * 4 + list(range(140, 100, -1))
    df = make_series(prices)
    swings = alternate_swings(find_swings(df, window=3))
    zones = fibonacci_levels(df, swings)
    assert len(zones) == 5


def test_vwap_zone_returns_single_zone_near_latest_price():
    df = make_series([100 + i * 0.05 for i in range(100)])
    zones = vwap_zone(df)
    assert len(zones) == 1
    assert zones[0].source == "vwap"


def test_support_resistance_engine_builds_sorted_zones():
    df = make_series(noisy_uptrend(n=400))
    engine = SupportResistanceEngine()
    zones = engine.build_zones(df, timeframe="1min")
    assert len(zones) > 0
    mids = [z.mid for z in zones]
    assert mids == sorted(mids)
    for zone in zones:
        assert zone.lower <= zone.mid <= zone.upper
