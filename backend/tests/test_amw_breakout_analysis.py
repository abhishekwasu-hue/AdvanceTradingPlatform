"""Integration checks against the actual ATP analysis primitives used by AMW."""
from dataclasses import replace

import pandas as pd
import pytest

from app.screener.breakouts.amw_analysis import iter_snapshots
from app.screener.breakouts.amw_breakout import Settings


def frame(rows, tf="15min"):
    starts = pd.date_range("2026-05-12 03:45", periods=len(rows), freq=tf, tz="UTC")
    bars = pd.DataFrame(rows, index=starts, columns=["open", "high", "low", "close", "volume"])
    return bars.assign(bar_end=starts + pd.Timedelta(tf))


def b1_frame(side=1):
    box = [(100.5, 102, 100, 101.5, 1000)] * 25
    path = box + [
        (101.5, 104, 101.2, 103.8, 3000),  # breakout
        (103.8, 105, 103.4, 104.7, 1000),  # leaves the breakout level
        (103.2, 104.5, 102.1, 104, 1000),  # returns and closes beyond it
        (104, 104.1, 100.7, 101.2, 1000),  # later reclaim
    ]
    if side < 0:
        path = [(220 - o, 220 - lo, 220 - h, 220 - c, v) for o, h, lo, c, v in path]
    return frame(path)


def zone_frame():
    lead = [(99.9, 101, 99, 100.1, 1000)] * 5
    return frame(lead + [
        (101, 101.2, 97.4, 97.6, 1000),
        (97.5, 98.2, 97.3, 97.6, 1000),
        (97.6, 98.1, 97.4, 97.7, 1000),
        (97.7, 103, 97.3, 102.8, 1000),
        (102.8, 103, 96.9, 97, 1000),
    ], "1h")


@pytest.mark.parametrize("side", [1, -1])
def test_real_b1_breakout_and_retest_have_distinct_close_times(side):
    prices = b1_frame(side)
    cfg = Settings(sources=("B1",), acceptance="RETEST_BREAKOUT")
    snapshots = list(iter_snapshots({"15m": prices}, cfg))
    events = [(i, c) for i, s in enumerate(snapshots) for c in s.candidates if c.side == side]
    assert events[0][0] == 25
    candidate = events[0][1]
    assert candidate.level == (102 if side == 1 else 118)
    assert candidate.sources == ("B1",)
    assert snapshots[25].retested_keys == ()
    assert snapshots[26].retested_keys == ()
    assert snapshots[27].retested_keys == (candidate.key,)
    assert snapshots[25].candle.volume_source == "genuine"
    assert snapshots[25].candle.relative_volume == 3.0


def test_prefix_parity_and_no_future_zone_before_its_actual_close():
    settings = Settings(sources=("B1",))
    price = frame([(100, 101, 99, 100, 10)] * 46)
    htf = zone_frame()
    inputs = {"15m": price, "1h": htf}
    full = list(iter_snapshots(inputs, settings, zone_settings={"avg_n": 5}))
    before_formation = htf.iloc[8].bar_end - pd.Timedelta("15min")
    cutoff = htf.iloc[8].bar_end
    assert next(s for s in full if s.at == before_formation).zones == ()
    formation = next(s for s in full if s.at == cutoff)
    assert formation.zones_ready
    assert [(z.side, z.low, z.high, z.tf) for z in formation.zones] == [(1, 97.3, 98.2, "1h")]
    assert not next(s for s in full if s.at == htf.iloc[9].bar_end).zones
    for until in (before_formation, cutoff, htf.iloc[9].bar_end):
        assert list(iter_snapshots(inputs, settings, as_of=until, zone_settings={"avg_n": 5})) == [s for s in full if s.at <= until]


def test_source_horizon_makes_required_zone_unavailable_without_reusing_stale_geometry():
    config = Settings(sources=("B1",), supply_tfs=("30m",))
    price = frame([(100, 101, 99, 100, 10)] * 46)
    htf = zone_frame()
    cutoff = htf.iloc[8].bar_end
    htf.attrs["available_through"] = cutoff
    cfg = replace(config, supply_tfs=("1h",))
    snapshots = list(iter_snapshots({"15m": price, "1h": htf}, cfg, zone_settings={"avg_n": 5}))
    assert next(s for s in snapshots if s.at == cutoff).zones_ready
    after = next(s for s in snapshots if s.at > cutoff)
    assert not after.zones_ready
    assert after.zones == ()
    assert not any(s.zones_ready for s in iter_snapshots({"15m": price, "1h": htf}, config, zone_settings={"avg_n": 5}))


def test_index_volume_requires_an_explicit_aligned_proxy():
    price = b1_frame()
    cfg = Settings(sources=("B1",))

    def read():
        return list(iter_snapshots({"15m": price}, cfg, instrument_is_index=True))[25].candle

    assert read().volume_source == "unavailable"
    price["futures_volume"] = price["volume"]
    assert read().volume_source == "proxy"
    assert read().relative_volume == 3.0
    price.loc[price.index[25], "futures_volume"] = 0
    assert read().volume_source == "invalid"
    assert read().relative_volume is None


def test_divergence_confirmation_age_and_completed_close_lifecycle():
    import numpy as np

    from app.screener.breakouts.amw_analysis import _divergences_at, _DivergenceSeries, _normalize
    from app.screener.runtime import _Divergence

    price = frame([(100, 102, 90, close, 1000) for close in (100, 99, 94, 100, 96, 95, 94)])
    ready = _normalize(price, "15m", None)
    reads = {(1, "regular"): _DivergenceSeries(
        np.array([3, 4]), [_Divergence(3, 1, 95, True), _Divergence(4, 4, 96, False)]
    )}

    def has(j, age):
        return bool(list(_divergences_at(ready, reads, "15m", j, age)))

    assert not has(2, 6)
    assert has(3, 0)
    assert has(4, 1)  # A wick and a newer non-divergent observation leave the old reading alive.
    assert has(5, 2)  # Equality holds, including at the last allowed age.
    assert not has(5, 1)
    assert not has(6, 6)


def test_delayed_zone_metadata_never_exposes_a_zone_before_its_observation():
    price = frame([(100, 101, 99, 100, 10)] * 46)
    hourly = zone_frame()
    end = hourly.iloc[8].bar_end
    hourly["known_at"] = hourly["bar_end"]
    hourly.loc[hourly.index[8], "known_at"] = end + pd.Timedelta("15min")
    full = list(iter_snapshots({"15m": price, "1h": hourly}, Settings(sources=("B1",)),
                               zone_settings={"avg_n": 5}))
    assert not next(s for s in full if s.at == end).zones_ready
    assert next(s for s in full if s.at == end + pd.Timedelta("15min")).zones


def test_rsi_off_does_not_validate_or_load_the_rsi_only_frame():
    cfg = Settings(sources=("B1",), rsi_tfs=("3m",))
    snapshots = list(iter_snapshots({"15m": b1_frame(), "3m": pd.DataFrame()}, cfg))
    assert snapshots and not any(s.rsi_ready or s.divergences for s in snapshots)


def test_zone_source_waits_for_distal_violation_at_higher_frame_close():
    hourly = zone_frame()
    violation = hourly.iloc[-1].bar_end
    prices = frame([(98, 100, 97.9, 98, 10)] * 39 + [(98, 98.1, 95, 95.5, 10)])
    assert prices.iloc[-1].bar_end == violation
    cfg = Settings(sources=("ZONE",))
    full = list(iter_snapshots({"15m": prices, "1h": hourly}, cfg, zone_settings={"avg_n": 5}))
    assert not any(s.candidates for s in full[:-1])
    assert [(c.side, c.level, c.sources) for c in full[-1].candidates] == [(-1, 97.3, ("ZONE",))]


def test_horizontal_requires_two_confirmed_prior_pivots(monkeypatch):
    from types import SimpleNamespace

    from app.screener.breakouts import screens

    price = b1_frame()
    pivots = [
        SimpleNamespace(kind="H", price=102.0, bar_idx=15, confirmed_idx=18),
        SimpleNamespace(kind="H", price=102.04, bar_idx=21, confirmed_idx=25),
    ]
    monkeypatch.setattr(screens.Bars, "pivots", lambda *_: pivots)
    cfg = Settings(sources=("HORIZONTAL",))
    pairs = {"15m": price}
    assert not list(iter_snapshots(pairs, cfg))[25].candidates
    pivots[-1].confirmed_idx = 24
    wanted = list(iter_snapshots(pairs, cfg))[25].candidates
    assert [(c.side, c.level, c.sources) for c in wanted] == [(1, 102.02, ("HORIZONTAL",))]


def test_previous_session_break_requires_verified_metadata_and_complete_prior_day():
    from app.screener.breakouts.session import calendar_for

    first = frame([(100, 102, 99, 100, 1000)] * 25)
    later = frame([(101, 105, 100, 104, 1000), (104, 106, 103, 105, 1000)])
    later.index += pd.Timedelta(days=1)
    later["bar_end"] += pd.Timedelta(days=1)
    prices = pd.concat([first, later])
    cfg = Settings(sources=("SESSION",))
    assert not any(s.candidates for s in iter_snapshots({"15m": prices}, cfg))
    prices.attrs["session_calendar"] = calendar_for("NSE")
    prices.attrs["previous_sessions"] = {later.index[0].date(): first.index[0].date()}
    got = list(iter_snapshots({"15m": prices}, cfg))
    assert [(c.side, c.level, c.sources) for c in got[25].candidates] == [(1, 102, ("SESSION",))]
    incomplete = prices.drop(prices.index[5])
    assert not any(s.candidates for s in iter_snapshots({"15m": incomplete}, cfg))
