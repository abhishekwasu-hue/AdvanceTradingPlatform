"""M99 B1-a: the per-bar classifier (app/price_action/bar_class.py), its ScreenQL functions behind the flag
`m99_primitives`, and the bot worker's index volume from the near-month future (app/workers/strategy_bars.py).

Synthetic shape fixtures with our own numbers: positive and negative cases per class, the class limits as settings,
missing / 0 volume, cut bars = the full run (no look-ahead), the registry mirror of the settings, the flag (hidden /
refused while off) and the worker's futures volume."""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, date, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from app.market_data import index_volume
from app.platform import controls, flag_catalog, flag_state, trader_pack
from app.price_action import bar_class as bc
from app.price_action.zones.detect import average_range
from app.quality.lookahead import synthetic_bars
from app.screener import m99_gate, parse
from app.screener.registry import BAR_CLASS_KWARGS, FUNCTIONS, M99_FLAG, M99_FUNCTIONS
from app.screener.runtime import SymbolData, evaluate
from app.workers import strategy_bars

AVG = 4                                          # a short average keeps the fixtures readable


def _frame(rows: list[tuple[float, float, float, float]], volume: list[float] | None = None) -> pd.DataFrame:
    """Bars from (open, high, low, close) rows, 5-minute stamps, volume 100 unless given."""
    o, h, lo, c = (np.array(col, dtype=float) for col in zip(*rows))
    idx = pd.date_range("2026-03-02 03:45", periods=len(rows), freq="5min", tz="UTC")
    return pd.DataFrame({"open": o, "high": h, "low": lo, "close": c,
                         "volume": np.array(volume if volume is not None else [100.0] * len(rows), dtype=float)}, index=idx)


def _ranged(ranges: list[float], close_at: float = 0.5) -> pd.DataFrame:
    """One bar per range, low 0 (so every range and ratio is exact), closing at `close_at` of its own range."""
    return _frame([(0.0, float(r), 0.0, close_at * r) for r in ranges])


def _s(**kw) -> bc.BarClassSettings:
    return bc.settings_from({"avg_n": AVG, **kw})


# --------------------------------------------------------------------------------------------- relative range / volume
def test_relative_range_reads_the_bars_before_never_the_bar_itself():
    f = _ranged([2, 2, 2, 2, 4, 1])
    rel = bc.relative_range(f, AVG)
    assert rel.iloc[:AVG].isna().all()                                   # warm-up: fewer than avg_n bars before
    assert rel.iloc[4] == pytest.approx(4 / 2)                           # 4 against the four 2s before it
    assert rel.iloc[5] == pytest.approx(1 / ((2 + 2 + 2 + 4) / 4))       # the 4 is now in the average
    before = average_range(f, AVG, AVG).shift(1)                         # the same average as the zone detector
    assert np.allclose(rel.iloc[AVG:], (f["high"] - f["low"]).iloc[AVG:] / before.iloc[AVG:])


def test_relative_volume_equals_the_screener_rel_volume_on_positive_volume():
    from app.screener.breakouts import params as bp
    from app.screener.breakouts.screens import Bars
    f = synthetic_bars(300, seed=3)
    f = f.assign(volume=f["volume"].astype(float) + 1.0)                # positive everywhere
    ours = bc.relative_volume(f, 20).to_numpy(float)
    theirs = Bars(f, bp.settings(vol_avg_n=20)).rvol
    assert np.allclose(ours, theirs, equal_nan=True)
    on_screen = index_volume.screen_frame(synthetic_bars(300, seed=3))  # with 0 as no data (M93-Q3): the same again
    assert np.allclose(bc.relative_volume(on_screen, 20), Bars(on_screen, bp.settings(vol_avg_n=20)).rvol, equal_nan=True)


def test_missing_or_zero_volume_is_never_a_value_and_never_averaged_in():
    f = _ranged([2] * 10)
    f["volume"] = [100.0, 100.0, 100.0, 100.0, 300.0, 0.0, 100.0, 100.0, 100.0, 100.0]
    rel = bc.relative_volume(f, AVG)
    assert rel.iloc[4] == pytest.approx(3.0)
    assert rel.iloc[5:10].isna().all()                                   # the 0 bar, then every window holding it
    cls = bc.volume_class(f, _s())
    assert cls.iloc[5] == bc.NO_VOLUME and cls.iloc[6:10].isna().all()   # its own volume is there: no class, no guess
    f.loc[f.index[7], "volume"] = np.nan
    assert bc.volume_class(f, _s()).iloc[7] == bc.NO_VOLUME
    assert (bc.volume_class(f.drop(columns="volume"), _s()) == bc.NO_VOLUME).all()


# --------------------------------------------------------------------------------------------- the classes
@pytest.mark.parametrize("last, want", [(0.5, bc.NARROW), (1.2, bc.NARROW), (1.4, bc.AVERAGE), (2.6, bc.AVERAGE), (3.0, bc.WIDE),
                                        (4.0, bc.WIDE), (4.2, bc.ULTRA_WIDE)])
def test_spread_class_each_class_and_its_neighbours(last, want):
    # average range 2: narrow below 1.4 (0.7), wide above 2.6 (1.3), ultra-wide above 4.0 (2.0); a limit takes the lower class
    assert bc.spread_class(_ranged([2, 2, 2, 2, last]), _s()).iloc[-1] == want


def test_spread_class_limits_are_settings():
    f = _ranged([2, 2, 2, 2, 3.0])                                       # relative range 1.5
    assert bc.spread_class(f, _s()).iloc[-1] == bc.WIDE
    assert bc.spread_class(f, _s(wide=1.6, ultra_wide=3.0)).iloc[-1] == bc.AVERAGE
    assert bc.spread_class(f, _s(narrow=1.55, wide=1.6, ultra_wide=3.0)).iloc[-1] == bc.NARROW
    assert bc.spread_class(f, _s(wide=1.2, ultra_wide=1.4)).iloc[-1] == bc.ULTRA_WIDE
    assert bc.spread_class(f, _s()).iloc[:AVG].isna().all()


@pytest.mark.parametrize("at, want", [(0.0, bc.LOW), (0.3, bc.LOW), (1 / 3, bc.MIDDLE), (0.5, bc.MIDDLE), (2 / 3, bc.MIDDLE),
                                      (0.7, bc.HIGH), (1.0, bc.HIGH)])
def test_close_position_thirds(at, want):
    assert bc.close_position(_ranged([3.0], close_at=at)).iloc[0] == want


def test_close_position_part_is_a_setting_and_a_bar_without_range_has_none():
    f = _ranged([4.0], close_at=0.3)
    assert bc.close_position(f).iloc[0] == bc.LOW
    assert bc.close_position(f, bc.settings_from({"close_part": 0.25})).iloc[0] == bc.MIDDLE
    assert bc.close_position(f, bc.settings_from({"close_part": 0.5})).iloc[0] == bc.LOW
    assert bc.close_position(_frame([(100.0, 100.0, 100.0, 100.0)])).iloc[0] is None


def test_bar_direction_against_the_previous_close():
    f = _frame([(100, 101, 99, 100), (100, 102, 99, 101), (101, 101.5, 99, 100), (100, 101, 99.5, 100), (100, 103, 99, 100.05)])
    assert bc.bar_direction(f).tolist() == [None, bc.UP, bc.DOWN, bc.LEVEL, bc.UP]


@pytest.mark.parametrize("last, want", [(50, bc.LOW), (70, bc.AVERAGE), (100, bc.AVERAGE), (130, bc.AVERAGE), (140, bc.HIGH),
                                        (200, bc.HIGH), (210, bc.ULTRA)])
def test_volume_class_each_class_and_its_neighbours(last, want):
    f = _ranged([2] * 5)
    f["volume"] = [100.0, 100.0, 100.0, 100.0, float(last)]
    assert bc.volume_class(f, _s()).iloc[-1] == want


def test_volume_class_limits_are_settings():
    f = _ranged([2] * 5)
    f["volume"] = [100.0, 100.0, 100.0, 100.0, 150.0]
    assert bc.volume_class(f, _s()).iloc[-1] == bc.HIGH
    assert bc.volume_class(f, _s(vol_high=1.6, vol_ultra=3.0)).iloc[-1] == bc.AVERAGE
    assert bc.volume_class(f, _s(vol_low=1.55, vol_high=1.6, vol_ultra=3.0)).iloc[-1] == bc.LOW
    assert bc.volume_class(f, _s(vol_high=1.2, vol_ultra=1.4)).iloc[-1] == bc.ULTRA


def test_classify_returns_every_series_on_the_same_index():
    f = synthetic_bars(120, seed=1)
    out = bc.classify(f)
    assert list(out.columns) == ["rel_range", "rel_volume", "spread", "close_position", "direction", "volume"]
    assert out.index.equals(f.index)
    assert set(out["spread"].dropna()) <= set(bc.SPREAD_CLASSES) and set(out["volume"].dropna()) <= set(bc.VOLUME_CLASSES)


# --------------------------------------------------------------------------------------------- no look-ahead
@pytest.mark.parametrize("seed", [0, 5])
def test_cut_bars_equal_the_full_run(seed):
    f = synthetic_bars(260, seed=seed)
    s = bc.settings_from({"avg_n": 10})
    full = bc.classify(f, s)
    for cut in (11, 57, 140, 259):
        part = bc.classify(f.iloc[:cut], s)
        pd.testing.assert_frame_equal(part, full.iloc[:cut], check_freq=False)


# --------------------------------------------------------------------------------------------- settings and registry
def test_settings_reference_values_bounds_and_order():
    s = bc.BarClassSettings()
    assert (s.avg_n, s.narrow, s.wide, s.ultra_wide, s.vol_low, s.vol_high, s.vol_ultra) == (20, 0.7, 1.3, 2.0, 0.7, 1.3, 2.0)
    assert s.close_part == pytest.approx(1 / 3)
    assert [r[2] for r in bc.SETTINGS_TABLE] == [getattr(s, r[0]) for r in bc.SETTINGS_TABLE]
    assert all(row["label"] == "reference - edit before use" for row in bc.reference_settings())
    for bad, why in (({"spread": 1}, "unknown"), ({"narrow": 0}, "between"), ({"avg_n": 2.5}, "whole"),
                     ({"narrow": 1.5}, "below"), ({"vol_high": 2.5}, "below"), ({"close_part": True}, "number")):
        with pytest.raises(ValueError, match=why):
            bc.settings_from(bad)


def test_the_registry_mirrors_the_settings():
    table = {name: (ref, bounds) for name, _, ref, bounds in bc.SETTINGS_TABLE}
    for fn, pairs in BAR_CLASS_KWARGS.items():
        kwargs = {a.name: a for a in FUNCTIONS[fn].kwargs}
        for arg, setting in pairs:
            assert kwargs[arg].default == table[setting][0] and kwargs[arg].bounds == table[setting][1], (fn, arg)
            assert kwargs[arg].literal_only
    for fn in ("RelRange", "SpreadClass", "VolumeClass"):
        assert FUNCTIONS[fn].args[0].default == table["avg_n"][0] and FUNCTIONS[fn].extra_bars == 1
    assert FUNCTIONS["SpreadClass"].values == bc.SPREAD_CLASSES and FUNCTIONS["VolumeClass"].values == bc.VOLUME_CLASSES
    assert FUNCTIONS["ClosePosition"].values == bc.CLOSE_POSITIONS and FUNCTIONS["BarDirection"].values == bc.DIRECTIONS


def test_screenql_reads_the_module():
    f = _ranged([2, 2, 2, 2, 3.0, 2.0])
    f["volume"] = [100.0, 100.0, 100.0, 100.0, 150.0, 0.0]
    d = SymbolData("S", {"5m": f})
    assert evaluate(parse("RelRange(4)"), d, base_tf="5m").iloc[4] == pytest.approx(1.5)
    assert evaluate(parse("SpreadClass(4)"), d, base_tf="5m").iloc[4] == bc.WIDE
    assert evaluate(parse("SpreadClass(4, wide=1.6, ultra=3)"), d, base_tf="5m").iloc[4] == bc.AVERAGE
    assert evaluate(parse("VolumeClass(4)"), d, base_tf="5m").iloc[4] == bc.HIGH
    assert evaluate(parse("VolumeClass(4)"), d, base_tf="5m").iloc[5] == bc.NO_VOLUME
    assert evaluate(parse("ClosePosition(part=0.5)"), d, base_tf="5m").iloc[4] == bc.MIDDLE
    assert evaluate(parse('BarDirection() == "DOWN"'), d, base_tf="5m").iloc[5]


# --------------------------------------------------------------------------------------------- the flag
def test_the_flag_is_registered_off_with_catalog_state_and_pack_entries():
    assert M99_FLAG == "m99_primitives" == m99_gate.FLAG
    assert M99_FLAG in controls.FEATURE_FLAGS and M99_FLAG in controls.DEFAULT_OFF_FLAGS
    assert "places no orders" in controls.FEATURE_FLAGS[M99_FLAG]
    assert flag_catalog.FLAG_INFO[M99_FLAG].status == "unfinished" and flag_catalog.FLAG_GROUP[M99_FLAG] == "screen_library"
    assert "M99" in flag_catalog.FINISH[M99_FLAG] and "M99" in str(flag_state.FLAG_STATUS[M99_FLAG])
    assert M99_FLAG in trader_pack.EXCLUDED_REASONS
    # every M99 B1 function sits behind the one m99_primitives flag (B1-a bar classes, B1-b Fibonacci / clusters, B1-c structure)
    assert set(M99_FUNCTIONS) == {"RelRange", "SpreadClass", "ClosePosition", "BarDirection", "VolumeClass", "StructureEventSeries", "RegimeTag",
                                  "FibLevel", "FibDistancePct", "ClusterDistancePct"}
    ast = parse('SpreadClass(20) == "WIDE" AND VolumeClass(20) == "HIGH" AND close > open')
    assert m99_gate.flagged_calls(ast) == ["SpreadClass", "VolumeClass"]
    assert M99_FLAG in m99_gate.refusal(["SpreadClass"]) and "bar close only" in m99_gate.intrabar_refusal(["SpreadClass"])


def _org(tag, on):
    from tests.test_n1_e1_execution_core import _flag
    from tests.test_trading_worker import _tenant
    t = _tenant(f"m99-{tag}-{uuid.uuid4().hex[:6]}@example.com", with_credentials=False)
    for name in ("screener_v2", "screener_intrabar") + ((M99_FLAG,) if on else ()):
        _flag(name, t["tenant_id"])
    return t


def test_flag_off_hides_and_refuses_the_bar_class_functions():
    from tests.test_auth_api import client
    off, on = _org("off", False), _org("on", True)
    source = 'SpreadClass(20) == "WIDE"'
    entries_off = client.get("/api/screener/registry", headers=off["headers"]).json()["entries"]
    entries_on = client.get("/api/screener/registry", headers=on["headers"]).json()["entries"]
    assert not set(M99_FUNCTIONS) & set(entries_off) and set(M99_FUNCTIONS) <= set(entries_on)
    refused = client.post("/api/screener/validate", headers=off["headers"], json={"source": source, "base_tf": "15m"}).json()
    assert not refused["ok"] and M99_FLAG in json.dumps(refused["problems"])
    assert client.post("/api/screener/validate", headers=on["headers"], json={"source": source, "base_tf": "15m"}).json()["ok"]
    rule = {"name": "bars", "kind": "instrument", "symbol": "ABC", "condition": source, "base_tf": "15m"}
    r = client.post("/api/alerts/rules", headers=off["headers"], json=rule)
    assert r.status_code == 422 and M99_FLAG in r.text
    r = client.post("/api/alerts/rules", headers=on["headers"], json={**rule, "fire_on": "intrabar"})
    assert r.status_code == 422 and "bar close only" in r.text
    r = client.post("/api/alerts/rules", headers=on["headers"], json=rule)
    assert r.status_code in (200, 201), r.text


# --------------------------------------------------------------------------------------------- the worker's index volume
class _Market:
    """get_frames per symbol from a dict; records the symbols read."""

    def __init__(self, frames: dict[str, dict[str, pd.DataFrame]]) -> None:
        self.frames, self.read = frames, []

    async def get_frames(self, symbol, exchange, base_timeframe, timeframes):
        self.read.append((symbol, exchange))
        if symbol not in self.frames:
            raise RuntimeError("no bars")
        return {tf: f.copy() for tf, f in self.frames[symbol].items()}


def _index_and_future():
    idx = pd.date_range("2026-03-02 03:45", periods=4, freq="5min", tz="UTC")
    index_frame = pd.DataFrame({"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 0.0}, index=idx)
    fut_frame = pd.DataFrame({"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5,
                              "volume": [500.0, 700.0, 900.0]}, index=idx[1:])                    # no bar at the first stamp
    return index_frame, fut_frame


def _near(monkeypatch, fut: index_volume.NearFuture | None):
    async def near(session, symbol, exchange, on):
        assert isinstance(on, date)
        return fut
    monkeypatch.setattr(index_volume, "near_future", near)


def _run(market, symbol):
    now = datetime(2026, 3, 2, 4, 10, tzinfo=UTC)
    return asyncio.run(strategy_bars.frames(None, None, market, symbol, "NSE", "5m", ["5m"], now=now, store=False))


def test_an_index_frame_in_the_worker_gets_its_near_month_future_volume(monkeypatch):
    index_frame, fut_frame = _index_and_future()
    _near(monkeypatch, index_volume.NearFuture("NIFTY", "NIFTY26MARFUT", date(2026, 3, 26), "NFO"))
    market = _Market({"NIFTY 50": {"5m": index_frame}, "NIFTY26MARFUT": {"5m": fut_frame}})
    got = _run(market, "NIFTY 50")
    assert got["5m"]["volume"].tolist() == [0.0, 500.0, 700.0, 900.0]          # 0 where the future has no bar
    assert got["5m"][["open", "high", "low", "close"]].equals(index_frame[["open", "high", "low", "close"]])
    assert market.read == [("NIFTY 50", "NSE"), ("NIFTY26MARFUT", "NFO")]
    # read as the screener reads it: the 0 is no volume data, the futures volume is volume
    assert bc.volume_class(index_volume.screen_frame(got["5m"]), bc.settings_from({"avg_n": 1})).tolist() == [
        bc.NO_VOLUME, None, bc.HIGH, bc.AVERAGE]


def test_a_stock_or_an_unread_future_keeps_its_own_bars(monkeypatch):
    index_frame, _ = _index_and_future()
    stock = index_frame.assign(volume=123.0)
    _near(monkeypatch, index_volume.NearFuture("NIFTY", "NIFTY26MARFUT", date(2026, 3, 26), "NFO"))
    market = _Market({"RELIANCE": {"5m": stock}, "NIFTY 50": {"5m": index_frame}})
    assert _run(market, "RELIANCE")["5m"].equals(stock) and market.read == [("RELIANCE", "NSE")]
    assert _run(market, "NIFTY 50")["5m"].equals(index_frame)                  # the future's read failed: as before
    _near(monkeypatch, None)
    assert _run(market, "NIFTY 50")["5m"].equals(index_frame)                  # no contract in the master: as before


def test_replace_frame_volume_matches_replace_volume():
    from app.core.models import OHLCVBar
    index_frame, fut_frame = _index_and_future()
    bars = [OHLCVBar(timestamp=t.to_pydatetime(), open=1, high=1, low=1, close=1, volume=0) for t in index_frame.index]
    fut = [OHLCVBar(timestamp=t.to_pydatetime(), open=1, high=1, low=1, close=1, volume=v) for t, v in fut_frame["volume"].items()]
    assert [b.volume for b in index_volume.replace_volume(bars, fut)] == index_volume.replace_frame_volume(index_frame, fut_frame)[
        "volume"].tolist()
    assert (index_volume.replace_frame_volume(index_frame, None)["volume"] == 0).all()


def test_the_new_modules_are_english_and_public_safe():
    root = Path(__file__).resolve().parents[1]
    for rel in ("app/price_action/bar_class.py", "app/screener/m99_gate.py"):
        assert (root / rel).read_text(encoding="utf-8").isascii(), rel
