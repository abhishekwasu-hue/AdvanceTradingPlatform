"""M97 PR-1 (docs/design/M97_SUPPLY_DEMAND.md): the generic Supply & Demand zone detector, its states and the ScreenQL
functions behind the flag `sd_pack`.

Hand-built bars per formation (positive and negative), the proximal / distal lines per `edge` setting, the state
transitions Fresh -> Tested(1) -> Tested(2) -> Violated, cut bars = the full run (no look-ahead), the settings' bounds,
the registry mirror of the settings, and the flag (hidden / refused while off)."""
from __future__ import annotations

import json
import uuid
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from app.platform import controls, flag_catalog, flag_state, trader_pack
from app.price_action import sd_zones as sd
from app.quality.lookahead import synthetic_bars
from app.screener import compile_screen, parse, sd_gate
from app.screener.registry import FUNCTIONS, SD_FLAG, SD_FUNCTIONS, SD_KWARGS
from app.screener.runtime import SymbolData, evaluate

PRELUDE = 21                                    # neutral candles: range 2, body 60 % (neither a leg nor a base candle)


def _neutral(level: float = 100.0) -> tuple[float, float, float, float]:
    """Range 2, body 1.2 (60 %): not a leg (range < 1.5 x 2), not a base (body > 50 %)."""
    return (level - 0.6, level + 1.0, level - 1.0, level + 0.6)


def _frame(candles: list[tuple[float, float, float, float]]) -> pd.DataFrame:
    rows = [_neutral() for _ in range(PRELUDE)] + list(candles)
    idx = pd.date_range("2026-03-02 03:45", periods=len(rows), freq="5min", tz="UTC")
    o, h, lo, c = (np.array(col, dtype=float) for col in zip(*rows))
    return pd.DataFrame({"open": o, "high": h, "low": lo, "close": c, "volume": 100.0}, index=idx)


def _mirror(candles: list[tuple[float, float, float, float]], axis: float = 200.0) -> list[tuple[float, float, float, float]]:
    """The same candles upside down (a demand formation becomes the matching supply one)."""
    return [(axis - o, axis - lo, axis - h, axis - c) for o, h, lo, c in candles]


LEG_DOWN = (100.0, 100.2, 96.8, 97.0)          # range 3.4 (>= 1.5 x ~2.0), body 3.0 = 88 %
LEG_UP = (97.0, 100.2, 96.8, 100.0)            # the up leg-in of an RBR (body 88 %)
BASE = [(97.0, 97.6, 96.6, 97.3), (97.3, 97.7, 96.9, 97.1)]     # range 1.0 / 0.8, body 0.3 / 0.2 (<= 50 %)
OUT_UP = (97.1, 101.0, 97.0, 100.8)            # range 4.0 (>= 1.5 x ~2.1); a real candle: low <= open (M98)
AWAY = (100.8, 101.7, 100.5, 101.5)            # wholly above the demand zone (body 58 %: not a base candle)


def _zones(candles, **settings):
    df = _frame(candles)
    return df, sd.track(df, sd.detect(df, sd.settings_from(settings)))


# --------------------------------------------------------------------------------------------------- the formations
def _rbr() -> list[tuple[float, float, float, float]]:
    """An up leg-in (93.8 -> 97.0, body 89 %) with the same base and leg-out above it."""
    return [(93.8, 97.2, 93.6, 97.0)] + BASE + [OUT_UP]


@pytest.mark.parametrize("candles, formation, side", [
    ([LEG_DOWN, *BASE, OUT_UP], "DBR", sd.DEMAND),
    (_rbr(), "RBR", sd.DEMAND),
    (_mirror([LEG_DOWN, *BASE, OUT_UP]), "RBD", sd.SUPPLY),
    (_mirror(_rbr()), "DBD", sd.SUPPLY),
])
def test_each_formation_is_found_on_its_leg_out_bar(candles, formation, side):
    df, zones = _zones(candles)
    assert [(z.formation, z.side) for z in zones] == [(formation, side)]
    z = zones[0]
    last = len(df) - 1
    assert (z.leg_in, z.base_start, z.base_end, z.formed) == (last - 3, last - 2, last - 1, last)
    assert FUNCTIONS["SDZoneFormed"].flag == SD_FLAG
    got = evaluate(parse(f'SDZoneFormed("{formation}")'), SymbolData("S", {"5m": df}), base_tf="5m")
    assert list(np.flatnonzero(got.to_numpy())) == [last]
    for other in {"DBR", "RBR", "RBD", "DBD"} - {formation}:
        assert not evaluate(parse(f'SDZoneFormed("{other}")'), SymbolData("S", {"5m": df}), base_tf="5m").any()
    assert evaluate(parse(f'SDZoneFormed("{side}")'), SymbolData("S", {"5m": df}), base_tf="5m").iloc[-1]


def test_a_base_longer_than_base_max_is_not_a_base():
    six = [BASE[i % 2] for i in range(6)]
    assert _zones([LEG_DOWN, *six, OUT_UP])[1] == []
    assert len(_zones([LEG_DOWN, *six, OUT_UP], base_max=6)[1]) == 1           # the setting decides
    assert len(_zones([LEG_DOWN, *six[:5], OUT_UP])[1]) == 1                   # five (the reference limit) is a base


def test_a_weak_leg_out_makes_no_zone():
    weak = (97.1, 99.4, 97.0, 99.2)            # range 2.4 < 1.5 x ~2.1
    assert _zones([LEG_DOWN, *BASE, weak])[1] == []
    assert len(_zones([LEG_DOWN, *BASE, weak], out_mult=1.0)[1]) == 1


def test_a_big_body_base_candle_is_not_a_base_candle():
    big_body = (96.7, 97.6, 96.6, 97.5)        # range 1.0, body 0.8 = 80 % > 50 %
    assert _zones([LEG_DOWN, big_body, OUT_UP])[1] == []
    assert _zones([LEG_DOWN, big_body, BASE[1], OUT_UP])[1] == []             # the run starts after it: no leg-in before
    assert len(_zones([LEG_DOWN, big_body, OUT_UP], base_body_pct=85)[1]) == 1


def test_a_small_body_leg_in_is_not_a_leg():
    small_body = (98.5, 100.2, 96.8, 97.0)     # range 3.4, body 1.5 = 44 % < 75 %
    assert _zones([small_body, *BASE, OUT_UP])[1] == []
    assert len(_zones([small_body, *BASE, OUT_UP], leg_body_pct=50)[1]) == 0         # 44 % is below the lowest bound too
    short_leg = (99.4, 99.6, 97.0, 97.2)       # body 2.2 = 85 %, but range 2.6 < 1.5 x ~2.0
    assert _zones([short_leg, *BASE, OUT_UP])[1] == []


# ---------------------------------------------------------------------------------------------------- the lines
def test_proximal_and_distal_per_edge_setting():
    _, (wick,) = _zones([LEG_DOWN, *BASE, OUT_UP])
    _, (body,) = _zones([LEG_DOWN, *BASE, OUT_UP], edge="body")
    assert (wick.proximal, wick.distal) == (97.7, 96.6)         # base top wick; lowest low of base + leg-out
    assert (body.proximal, body.distal) == (97.3, 96.6)         # the highest body top of the base
    _, (s_wick,) = _zones(_mirror([LEG_DOWN, *BASE, OUT_UP]))
    _, (s_body,) = _zones(_mirror([LEG_DOWN, *BASE, OUT_UP]), edge="body")
    assert (s_wick.proximal, s_wick.distal) == pytest.approx((200 - 97.7, 200 - 96.6))
    assert (s_body.proximal, s_body.distal) == pytest.approx((200 - 97.3, 200 - 96.6))
    low_out = (97.1, 101.0, 96.2, 100.8)       # a leg-out wick below the base: the distal line follows it
    assert _zones([LEG_DOWN, *BASE, low_out])[1][0].distal == 96.2


# M98 F2: per formation, hand-computed lines from explicit bars. The leg-in and leg-out extremes are chosen so that
# counting either of them in the proximal line (or the leg-in in the distal line) would give a different number.
#   demand: proximal = the highest high (wick) / highest body top (body) of the BASE candles only;
#           distal   = the lowest low of the base AND the leg-out candle (never the leg-in), whatever the edge setting;
#   supply: the mirror (proximal = the lowest low / lowest body bottom of the base; distal = the highest high of the
#           base and the leg-out).
HAND_LINES = {
    # formation: (leg-in, [base candles], leg-out, (wick proximal, body proximal, distal))
    "DBR": ((100.0, 100.2, 96.8, 97.0), [(97.0, 97.6, 96.6, 97.3), (97.2, 97.7, 96.9, 97.4)], (97.1, 101.0, 96.4, 100.8),
            (97.7, 97.4, 96.4)),         # leg-out low 96.4 < base lows 96.6 / 96.9: the distal line is the leg-out's
    "RBR": ((93.8, 97.2, 93.6, 97.0), [(97.0, 97.6, 96.6, 97.3), (97.2, 97.7, 96.9, 97.4)], (97.1, 101.0, 96.8, 100.8),
            (97.7, 97.4, 96.6)),         # leg-in low 93.6 is not in the distal line; the base low 96.6 is
    "RBD": ((100.0, 103.2, 99.8, 103.0), [(103.0, 103.4, 102.4, 102.7), (102.8, 103.1, 102.3, 102.6)], (102.9, 103.6, 99.0, 99.2),
            (102.3, 102.6, 103.6)),      # leg-out high 103.6 > base highs 103.4 / 103.1: the distal line is the leg-out's
    "DBD": ((106.2, 106.4, 103.0, 103.2), [(103.0, 103.4, 102.4, 102.7), (102.8, 103.1, 102.3, 102.6)], (102.9, 103.3, 99.0, 99.2),
            (102.3, 102.6, 103.4)),      # leg-in high 106.4 is not in the distal line; the base high 103.4 is
}


@pytest.mark.parametrize("formation", list(HAND_LINES))
@pytest.mark.parametrize("edge", ["wick", "body"])
def test_hand_computed_proximal_and_distal_per_formation_and_edge(formation, edge):
    leg_in, base, leg_out, (wick_prox, body_prox, distal) = HAND_LINES[formation]
    df, zones = _zones([leg_in, *base, leg_out], edge=edge)
    assert [z.formation for z in zones] == [formation]
    z = zones[0]
    last = len(df) - 1
    # the exact bars: leg-in, the two base candles, the leg-out (the last bar)
    assert (z.leg_in, z.base_start, z.base_end, z.formed) == (last - 3, last - 2, last - 1, last)
    assert tuple(df.iloc[z.leg_in][["open", "high", "low", "close"]]) == leg_in
    assert [tuple(r) for r in df.iloc[z.base_start:z.base_end + 1][["open", "high", "low", "close"]].to_numpy()] == base
    assert tuple(df.iloc[z.formed][["open", "high", "low", "close"]]) == leg_out
    assert z.side == sd.FORMATION_SIDE[formation]
    assert z.proximal == pytest.approx(wick_prox if edge == "wick" else body_prox)
    assert z.distal == pytest.approx(distal)
    # the proximal line is never beyond the base candles' own extremes (the owner's check on his chart)
    if z.side == sd.DEMAND:
        assert z.proximal <= max(h for _, h, _, _ in base)
    else:
        assert z.proximal >= min(lo for _, _, lo, _ in base)


# --------------------------------------------------------------------------------------------------- the states
STATE_PATH = [LEG_DOWN, *BASE, OUT_UP,
              AWAY,                              # +1 fresh
              (101.0, 101.2, 97.5, 98.5),        # +2 visit 1 starts (wick 97.5 <= 97.7)
              (98.5, 99.0, 97.6, 98.8),          # +3 still in the same visit
              (98.8, 101.6, 98.6, 101.2),        # +4 left the zone
              (101.2, 101.4, 97.0, 98.0),        # +5 visit 2
              AWAY,                              # +6 left
              (101.0, 101.2, 95.8, 96.0)]        # +7 close below the distal line 96.6: violated


def test_fresh_tested_tested_violated_bar_by_bar():
    df, (z,) = _zones(STATE_PATH)
    f = z.formed
    assert [z.state_at(t) for t in range(f - 1, f + 8)] == [None, "fresh", "fresh", "tested", "tested", "tested", "tested",
                                                             "tested", "violated"]
    assert [z.visits_by(t) for t in range(f, f + 8)] == [0, 0, 1, 1, 1, 2, 2, 3]   # the violating bar touched again too
    assert z.visits[:2] == [f + 2, f + 5] and z.violated == f + 7
    d = SymbolData("S", {"5m": df})
    enter = evaluate(parse('SDZoneEnter("demand")'), d, base_tf="5m")
    assert list(np.flatnonzero(enter.to_numpy())) == [f + 2]                       # the first visit of a fresh zone only
    viol = evaluate(parse('SDZoneViolated("demand")'), d, base_tf="5m")
    assert list(np.flatnonzero(viol.to_numpy())) == [f + 7]
    assert not evaluate(parse('SDZoneViolated("supply")'), d, base_tf="5m").any()
    fresh_edge = evaluate(parse('SDZoneEdge("demand", "proximal")'), d, base_tf="5m").to_numpy()
    distal = evaluate(parse('SDZoneEdge("demand", "distal")'), d, base_tf="5m").to_numpy()
    assert np.isnan(fresh_edge[:f]).all() and list(fresh_edge[f:f + 2]) == [97.7, 97.7] and np.isnan(fresh_edge[f + 2:]).all()
    assert list(distal[f:f + 2]) == [96.6, 96.6]
    c = df["close"].to_numpy()
    any_d = evaluate(parse('SDZoneDistancePct("demand")'), d, base_tf="5m").to_numpy()
    want = (c[f:f + 7] - 97.7) / c[f:f + 7] * 100
    assert np.allclose(any_d[f:f + 7], want) and np.isnan(any_d[f + 7:]).all() and np.isnan(any_d[:f]).all()
    tested = evaluate(parse('SDZoneDistancePct("demand", "tested")'), d, base_tf="5m").to_numpy()
    assert np.isnan(tested[:f + 2]).all() and np.allclose(tested[f + 2:f + 7], want[2:])
    fresh = evaluate(parse('SDZoneDistancePct("demand", "fresh")'), d, base_tf="5m").to_numpy()
    assert np.allclose(fresh[f:f + 2], want[:2]) and np.isnan(fresh[f + 2:]).all()
    assert any_d[f + 3] > 0 and any_d[f + 2] > 0                                   # closes above the line
    inside = _frame(STATE_PATH[:5] + [(101.0, 101.2, 96.9, 97.2)])                 # a close inside the zone: negative
    assert evaluate(parse('SDZoneDistancePct("demand")'), SymbolData("S", {"5m": inside}), base_tf="5m").iloc[-1] < 0


def test_the_supply_mirror_has_the_same_states():
    df, (z,) = _zones(_mirror(STATE_PATH))
    assert z.side == sd.SUPPLY and z.visits[:2] == [z.formed + 2, z.formed + 5] and z.violated == z.formed + 7
    d = SymbolData("S", {"5m": df})
    assert list(np.flatnonzero(evaluate(parse('SDZoneEnter("supply")'), d, base_tf="5m").to_numpy())) == [z.formed + 2]
    assert not evaluate(parse('SDZoneEnter("demand")'), d, base_tf="5m").any()


def test_the_nearest_zone_is_the_closest_proximal_line():
    s = sd.SDSeries([sd.SDZone(sd.DEMAND, "DBR", 90.0, 88.0, 0, 1, 1, 2), sd.SDZone(sd.DEMAND, "RBR", 98.0, 95.0, 3, 4, 4, 5)],
                    8, np.full(8, 100.0))
    dist, prox, distal = s.nearest(sd.DEMAND, "any")
    assert np.isnan(dist[:2]).all() and list(prox[2:5]) == [90.0] * 3 and list(prox[5:]) == [98.0] * 3
    assert list(distal[5:]) == [95.0] * 3 and dist[6] == pytest.approx(2.0)


# --------------------------------------------------------------------------------------------- no look-ahead
@pytest.mark.parametrize("seed", [3, 7])
def test_cut_bars_equal_the_full_run(seed):
    df = synthetic_bars(700, tf="5m", seed=seed)
    s = sd.settings_from({"leg_mult": 1, "out_mult": 1, "leg_body_pct": 50, "base_body_pct": 90, "base_mult": 0.9})
    full = sd.sd_series(df, s)
    assert len(full.zones) > 10 and any(z.violated is not None for z in full.zones) and any(z.visits for z in full.zones)
    reads = {"formed": lambda x: x.formed("any"), "enter_d": lambda x: x.entered(sd.DEMAND), "viol_s": lambda x: x.violated(sd.SUPPLY),
             "near_d": lambda x: x.nearest(sd.DEMAND, "any")[0], "near_s_t": lambda x: x.nearest(sd.SUPPLY, sd.TESTED)[0],
             "edge_d": lambda x: x.nearest(sd.DEMAND, sd.FRESH)[2]}
    want = {k: fn(full) for k, fn in reads.items()}
    for cut in (60, 150, 333, 501, 699):
        part = sd.sd_series(df.iloc[:cut], s)
        for k, fn in reads.items():
            np.testing.assert_array_equal(fn(part), want[k][:cut], err_msg=f"{k} @ {cut}")
    _, (z,) = _zones(STATE_PATH)                                                   # the hand-built path, cut bar by bar
    df_h = _frame(STATE_PATH)
    for cut in range(z.formed + 1, len(df_h) + 1):
        zs = sd.track(df_h.iloc[:cut], sd.detect(df_h.iloc[:cut]))
        assert [zz.state_at(t) for zz in zs for t in range(cut)] == [z.state_at(t) for t in range(cut)]
    assert sd.detect(df_h.iloc[:z.formed]) == []                                   # before the leg-out closes: no zone


def test_the_cache_returns_the_same_series_and_keys_on_settings():
    df = synthetic_bars(400, seed=5)
    a = sd.cached_sd_series(df, sd.SDSettings())
    assert sd.cached_sd_series(df.copy(), sd.SDSettings()) is a
    assert sd.cached_sd_series(df, sd.settings_from({"edge": "body"})) is not a


# ----------------------------------------------------------------------------------------------- the settings
def test_settings_reference_values_bounds_and_the_registry_mirror():
    ref = sd.SDSettings()
    assert (ref.avg_n, ref.leg_mult, ref.base_mult, ref.out_mult, ref.leg_body_pct, ref.base_body_pct, ref.base_max) == \
        (20, 1.5, 0.7, 1.5, 75.0, 50.0, 5)
    assert (ref.out_ref, ref.edge) == ("average", "wick")
    kwargs = {a.name: a for a in SD_KWARGS}
    assert list(kwargs) == [row[0] for row in sd.SETTINGS_TABLE]
    for name, kind, default, rule in sd.SETTINGS_TABLE:
        assert kwargs[name].default == default == getattr(ref, name), name
        if kind == "choice":
            assert tuple(kwargs[name].choices) == rule
        else:
            assert kwargs[name].bounds == rule
    for name in SD_FUNCTIONS:
        assert FUNCTIONS[name].kwargs == SD_KWARGS and FUNCTIONS[name].timeframed
    for bad in ({"leg_mult": 9}, {"base_max": 2.5}, {"edge": "close"}, {"nope": 1}, {"base_mult": 1.0, "leg_mult": 1.0},
                {"avg_n": True}):
        with pytest.raises(ValueError):
            sd.settings_from(bad)
    for bad in ('SDZoneEnter("demand", leg_mult=9)', 'SDZoneEnter("demand", base_max=11)', 'SDZoneEnter("demand", edge="close")',
                'SDZoneEnter("demand", base_mult=1, leg_mult=1)', 'SDZoneEnter("up")', 'SDZoneFormed("XYZ")',
                'SDZoneDistancePct("demand", "violated")', 'SDZoneEdge("demand", "middle")', 'SDZoneEnter("demand", leg_mult=close)'):
        assert not compile_screen(bad, base_tf="5m")[1].ok, bad
    for good in ('SDZoneEnter("demand", leg_mult=2, base_max=3, edge="body", out_ref="atr")', 'SDZoneDistancePct("supply") < 1',
                 'close < SDZoneEdge("demand", "distal")@1h', 'SDZoneFormed("RBR", avg_n=$n)'):
        assert compile_screen(good, base_tf="5m", params={"n": 30} if "$n" in good else {})[1].ok, good


def test_higher_timeframe_read_is_aligned_to_its_close():
    df = synthetic_bars(900, tf="5m", seed=7)
    d = SymbolData("S", {"5m": df})
    loose = "leg_mult=1, out_mult=1, leg_body_pct=50, base_body_pct=90, base_mult=0.9"
    v = evaluate(parse(f'SDZoneDistancePct("demand", "any", {loose})@15m'), d, base_tf="5m")
    assert len(v) == len(df) and v.notna().any()


# --------------------------------------------------------------------------------------------------- the flag
def test_the_flag_is_registered_off_with_catalog_state_and_pack_entries():
    assert SD_FLAG == "sd_pack" == sd_gate.FLAG
    assert SD_FLAG in controls.FEATURE_FLAGS and SD_FLAG in controls.DEFAULT_OFF_FLAGS
    assert "places no orders" in controls.FEATURE_FLAGS[SD_FLAG]
    assert flag_catalog.FLAG_INFO[SD_FLAG].status == "unfinished" and flag_catalog.FLAG_GROUP[SD_FLAG] == "screen_library"
    assert "M97" in flag_catalog.FINISH[SD_FLAG] and "M97" in str(flag_state.FLAG_STATUS[SD_FLAG])
    assert SD_FLAG in trader_pack.EXCLUDED_REASONS
    assert set(SD_FUNCTIONS) == {"SDZoneDistancePct", "SDZoneEnter", "SDZoneFormed", "SDZoneViolated", "SDZoneEdge"}
    ast = parse('SDZoneEnter("demand") AND SDZoneDistancePct("demand") < 1 AND close > open')
    assert sd_gate.flagged_calls(ast) == ["SDZoneEnter", "SDZoneDistancePct"]
    assert SD_FLAG in sd_gate.refusal(["SDZoneEnter"]) and "bar close only" in sd_gate.intrabar_refusal(["SDZoneEnter"])


def _org(tag, on):
    from tests.test_n1_e1_execution_core import _flag
    from tests.test_trading_worker import _tenant
    t = _tenant(f"m97-{tag}-{uuid.uuid4().hex[:6]}@example.com", with_credentials=False)
    for name in ("screener_v2", "screener_intrabar") + ((SD_FLAG,) if on else ()):
        _flag(name, t["tenant_id"])
    return t


def test_flag_off_hides_and_refuses_the_sd_functions():
    from tests.test_auth_api import client
    off, on = _org("off", False), _org("on", True)
    source = 'SDZoneEnter("demand")'
    entries_off = client.get("/api/screener/registry", headers=off["headers"]).json()["entries"]
    entries_on = client.get("/api/screener/registry", headers=on["headers"]).json()["entries"]
    assert not set(SD_FUNCTIONS) & set(entries_off) and set(SD_FUNCTIONS) <= set(entries_on)
    assert entries_on["SDZoneEnter"]["kwargs"] == [a.name for a in SD_KWARGS]
    refused = client.post("/api/screener/validate", headers=off["headers"], json={"source": source, "base_tf": "15m"}).json()
    assert not refused["ok"] and SD_FLAG in json.dumps(refused["problems"])
    assert client.post("/api/screener/validate", headers=on["headers"], json={"source": source, "base_tf": "15m"}).json()["ok"]
    rule = {"name": "sd", "kind": "instrument", "symbol": "ABC", "condition": source, "base_tf": "15m"}
    r = client.post("/api/alerts/rules", headers=off["headers"], json=rule)
    assert r.status_code == 422 and SD_FLAG in r.text
    r = client.post("/api/alerts/rules", headers=on["headers"], json={**rule, "fire_on": "intrabar"})
    assert r.status_code == 422 and "bar close only" in r.text
    r = client.post("/api/alerts/rules", headers=on["headers"], json=rule)
    assert r.status_code in (200, 201), r.text


def test_the_new_modules_are_english_and_public_safe():
    root = Path(__file__).resolve().parents[1]
    for rel in ("app/price_action/sd_zones.py", "app/screener/sd_gate.py"):
        assert (root / rel).read_text(encoding="utf-8").isascii(), rel
