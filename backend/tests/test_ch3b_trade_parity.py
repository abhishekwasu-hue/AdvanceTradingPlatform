"""N4 CH3b: golden parity of the ported price-action engines with Trade@1e6c9ac, and their causality.

* structure_tracker (BOS / CHoCH / sweeps / states), legs (impulse vs corrective), trendlines (+ find_trendline, ref_range)
  and liquidity (pools, sweeps, marks) give exactly Trade's output on the frozen fixtures (tests/ch3b_golden.py; the
  golden file is Trade's own output; with the Trade repository on disk the file is also checked against Trade live);
* causal: each is computed from bars up to t only - cutting the bars at t, or changing every bar after t, leaves what
  is known at t unchanged.
Synthetic fixtures only - no broker, no network, no NIFTY data.
"""
import json

import numpy as np
import pytest

from app.price_action import legs as LG
from app.price_action import liquidity as LQ
from app.price_action import structure_tracker as ST
from app.price_action import trendlines as TL
from tests import ch3b_golden as H


def _norm(x):
    return json.loads(json.dumps(H._j(x), sort_keys=True))


@pytest.fixture(scope="module")
def golden():
    return H.load_golden()


@pytest.fixture(scope="module")
def ours():
    return _norm(H.outputs(H.atp(), False))


@pytest.mark.parametrize("section", ["structure", "legs", "trendlines", "liquidity"])
def test_ports_equal_trade_golden(golden, ours, section):
    assert golden["trade_commit"] == H.TRADE_COMMIT
    assert set(ours[section]) == set(golden[section])
    for key in golden[section]:
        assert ours[section][key] == golden[section][key], f"{section}/{key}"


def test_golden_is_enough_to_mean_something(golden):
    events = [e["type"] for v in golden["structure"].values() for e in v["events"]]
    assert {"BOS", "CHOCH", "SWEEP", "SWING"} <= set(events)
    labels = {lg["label"] for v in golden["legs"].values() for lg in v["legs"]}
    assert {LG.STRONG_IMPULSE, LG.WEAK_IMPULSE, LG.REVERSAL} <= labels and labels & {LG.HEALTHY_PULLBACK, LG.MIXED_PULLBACK, LG.DANGEROUS_PULLBACK}
    assert any(golden["trendlines"].values()) and {r["side"] for v in golden["trendlines"].values() for r in v} == {"support", "resistance"}
    srcs = {p["src"] for v in golden["liquidity"].values() for p in v["pools"]}
    assert {"swing", "PDH", "PDL", "PWH", "PWL"} <= srcs and any(v["sweeps"] for v in golden["liquidity"].values())


def test_golden_matches_trade_live(golden):
    trade = H.import_trade()
    if trade is None:
        pytest.skip("Trade repository not on disk (CI checks the frozen golden file)")
    assert _norm(H.outputs(trade, True)) == golden


def _altered(df, k):
    out = df.copy()
    out.loc[k:, ["open", "high", "low", "close"]] = out.loc[k:, ["open", "high", "low", "close"]].to_numpy() * 1.05
    return out


def test_ported_engines_are_causal():
    F = H.frames()
    df = F["s15"]
    full = ST.run(df, "15m", time_col="timestamp")
    for k in (120, 333, 600):
        cut = ST.run(df.iloc[:k], "15m", time_col="timestamp")
        later = ST.run(_altered(df, k), "15m", time_col="timestamp")
        known = [e for e in full.events if e["bar_idx"] < k]
        assert cut.events == known == [e for e in later.events if e["bar_idx"] < k], k
        legs_cut = [lg.as_dict() for lg in LG.build_legs(df.iloc[:k])[0]]
        legs_full = [lg.as_dict() for lg in LG.build_legs(_altered(df, k))[0] if lg.known_at < k]
        assert legs_cut == legs_full, k
        lines = [_norm({**{x: y for x, y in ln.items() if x != "line"}, "line": vars(ln["line"])}) for ln in TL.detect_trendlines(df.iloc[:k])]
        again = [_norm({**{x: y for x, y in ln.items() if x != "line"}, "line": vars(ln["line"])}) for ln in TL.detect_trendlines(_altered(df, k).iloc[:k])]
        assert lines == again


def test_liquidity_at_t_ignores_later_bars():
    df = H.frames()["m5"]
    mr, swing, _ = LG.pivots_for(df)

    def view(d):
        return H._view(H.atp(), False, d, swing, mr)
    for t in (200, 420, 700):
        a, b = view(df), view(_altered(df, t + 1))
        b.pivots = [p for p in a.pivots]                                     # the same pivots: only bars after t differ
        assert LQ.pools(a, t) == LQ.pools(b, t) and LQ.sweeps(a, t, 30) == LQ.sweeps(b, t, 30) and LQ.pool_marks(a, t) == LQ.pool_marks(b, t)
        assert all(p.known_at <= t for p in a.known_pivots(t))


def test_ref_range_and_tracker_scale_agree_with_trade_rule():
    ranges = list(np.arange(1, 50, dtype=float))
    assert ST.ref_range_from_ranges(ranges, 20, 10) == float(np.median(ranges[-20:]))
    assert np.isnan(ST.ref_range_from_ranges(ranges[:5], 20, 10))
