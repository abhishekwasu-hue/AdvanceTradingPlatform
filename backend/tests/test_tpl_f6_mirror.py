"""TPL-F6 (OPEN_QUESTIONS; owner-approved fix): the swing structure and the reversal check are mirror-symmetric.

A series and its mirror (every price p -> axis - p, highs and lows swapped, on whole ticks so equal prices stay equal)
give exactly mirrored outputs on every bar: DowTrend UP <-> DOWN (RANGE stays), SwingLabel HH <-> LL and LH <-> HL,
SwingBreakType("up") <-> SwingBreakType("down"), ReversalAt bullish <-> bearish at the mirrored level, and the A2 / F2
range-edge templates long <-> short. Before the fix an equal swing low read as LL (lower) while an equal swing high read
as LH (lower): a tie read "lower" on both sides, so the two sides disagreed wherever a series held equal swings - the
test of the old rule below shows it, on the same series.

The series: random walks (app/quality/lookahead.synthetic_bars) and series built to hold equal highs and equal lows
(tests/mirror_tie_fixtures.py) - no market data. One documented exception, older than this fix and outside it: the
zigzag's opening swing (app/price_action/causal_swings.zigzag_pivots tests a high before a low, so when both confirm on
the same first bar the two sides open with different swings; app/screener/mirror.py SWING_WARMUP). There the structure is
compared from the bar its swings agree again (two highs and two lows of the common swings), and the test checks that the
difference is only that opening."""
from typing import List, Optional, Tuple

import numpy as np
import pandas as pd
import pytest

from app.screener.breakouts.screens import Bars
from app.screener.parser import parse
from app.screener.runtime import SymbolData, _breakout_params, evaluate
from app.strategy_templates import compile as C
from app.strategy_templates import engine, library
from tests.mirror_tie_fixtures import KINDS, legacy_dow, mirror, series, tie_bars

TREND = {"UP": "DOWN", "DOWN": "UP", "RANGE": "RANGE", None: None}
LABEL = {"HH": "LL", "LL": "HH", "LH": "HL", "HL": "LH", None: None}
COUNT = 60                                   # series per timeframe (cycling through the four kinds)


def ev(text: str, df: pd.DataFrame, tf: str) -> pd.Series:
    return evaluate(parse(text), SymbolData("S", {tf: df}), base_tf=tf)


def _key(p) -> Tuple[str, int, int]:
    return p.kind, int(p.bar_idx), int(p.confirmed_idx)


def _mirrored_key(p) -> Tuple[str, int, int]:
    return ("L" if p.kind == "H" else "H"), int(p.bar_idx), int(p.confirmed_idx)


def settled_bar(a: Bars, b: Bars, degree: int) -> Optional[int]:
    """The first bar from which the structure of `a` and of its mirror `b` must agree: 0 when every swing mirrors; after a
    different opening swing, the bar confirming the second high and the second low of the common swings (each label then
    compares two common swings). None when the swings differ anywhere but at the opening."""
    pa, pb = [_key(p) for p in a.pivots(degree)], [_mirrored_key(p) for p in b.pivots(degree)]
    if pa == pb:
        return 0
    first = min(x[2] for x in pa[:1] + pb[:1])
    for i, j in sorted(((i, j) for i in range(5) for j in range(5)), key=sum):
        if pa[i:] == pb[j:] and pa[i:]:
            common = pa[i:]
            opening = max([x[2] for x in pa[:i]] + [x[2] for x in pb[:j]])
            assert opening <= first + 10, "only the opening swings differ"
            highs, lows = [x[2] for x in common if x[0] == "H"], [x[2] for x in common if x[0] == "L"]
            if len(highs) < 2 or len(lows) < 2:
                return a.n
            return max(highs[1], lows[1])
    return None


def mismatches(da, db, start: int) -> List[int]:
    return [t for t in range(start, len(da)) if TREND[da[t]] != db[t]]


CASES = [(tf, d) for tf in ("15m", "1d") for d in (0, 1)]


@pytest.mark.parametrize("tf, degree", CASES, ids=[f"{tf}-D{d}" for tf, d in CASES])
def test_dow_trend_is_mirrored_on_every_bar(tf, degree):
    p = _breakout_params({})
    full = opening = 0
    for kind, seed, df in series(COUNT, 600, tf):
        a, b = Bars(df, p), Bars(mirror(df), p)
        start = settled_bar(a, b, degree)
        assert start is not None, (kind, seed)
        full += start == 0
        opening += start > 0
        assert mismatches(a.dow(degree), b.dow(degree), start) == [], (kind, seed, degree)
    assert full >= COUNT * 2 // 3                      # the opening exception stays an exception


@pytest.mark.parametrize("tf, degree", CASES, ids=[f"{tf}-D{d}" for tf, d in CASES])
def test_the_old_rule_was_not_mirrored_on_the_same_series(tf, degree):
    """The pre-fix labelling (an equal low read LL) on the very series above: many of them disagree with their mirror."""
    p = _breakout_params({})
    broken = 0
    for kind, seed, df in series(COUNT, 600, tf):
        a, b = Bars(df, p), Bars(mirror(df), p)
        start = settled_bar(a, b, degree)
        broken += bool(mismatches(legacy_dow(a, degree), legacy_dow(b, degree), start or 0))
    assert broken >= COUNT // 4


def test_the_fixtures_hold_equal_swing_highs_and_equal_swing_lows():
    p = _breakout_params({})
    for kind in KINDS:
        eq = {"H": 0, "L": 0}
        for seed in range(8):
            last = {"H": None, "L": None}
            for pv in Bars(tie_bars(600, "15m", seed, kind), p).pivots(0):
                eq[pv.kind] += last[pv.kind] == pv.price
                last[pv.kind] = pv.price
        assert eq["H"] and eq["L"], kind


def test_swing_labels_and_break_types_are_mirrored():
    for kind, seed, df in series(24, 600, "15m"):
        md = mirror(df)
        a, b = Bars(df, _breakout_params({})), Bars(md, _breakout_params({}))
        start = settled_bar(a, b, 0)
        assert start is not None
        for x, y in (("high", "low"), ("low", "high")):
            mine = ev(f'SwingLabel("{x}", 0)', df, "15m").iloc[start:]
            theirs = ev(f'SwingLabel("{y}", 0)', md, "15m").iloc[start:]
            assert [LABEL[v if isinstance(v, str) else None] for v in mine] == [v if isinstance(v, str) else None for v in theirs], (kind, seed)
        for side, other in (("up", "down"), ("down", "up")):
            mine = ev(f'SwingBreakType("{side}", 0, 0.2)', df, "15m").iloc[start:]
            theirs = ev(f'SwingBreakType("{other}", 0, 0.2)', md, "15m").iloc[start:]
            assert [v if isinstance(v, str) else None for v in mine] == [v if isinstance(v, str) else None for v in theirs], (kind, seed)


def test_swing_labels_agree_with_the_dow_trend_on_ties():
    """SwingLabel and DowTrend read an equal swing the same way (UP = HH + HL, DOWN = LH + LL)."""
    for kind, seed, df in series(16, 600, "15m"):
        high, low = ev('SwingLabel("high", 0)', df, "15m"), ev('SwingLabel("low", 0)', df, "15m")
        dow = Bars(df, _breakout_params({})).dow(0)
        for t in range(len(df)):
            if dow[t] is None or pd.isna(high.iloc[t]) or pd.isna(low.iloc[t]):
                continue
            pair = (high.iloc[t], low.iloc[t])
            assert dow[t] == ("UP" if pair == ("HH", "HL") else "DOWN" if pair == ("LH", "LL") else "RANGE"), (kind, seed, t)


@pytest.mark.parametrize("level, mirrored", [("Min(low, 20)[1]", "Max(high, 20)[1]"), ("SMA(close, 20)", "SMA(close, 20)")])
def test_reversal_at_is_mirrored_on_every_bar(level, mirrored):
    """The reversal check was already exact (its bearish read is the bullish rule on negated prices); kept under test.
    Rolling extremes as the level hold many equal values on these series."""
    hits = 0
    for kind, seed, df in series(24, 600, "15m"):
        md = mirror(df)
        for d, o in (("bullish", "bearish"), ("bearish", "bullish")):
            lv, lvm = (level, mirrored) if d == "bullish" else (mirrored, level)
            mine = ev(f'ReversalAt({lv}, "{d}")', df, "15m").to_numpy()
            theirs = ev(f'ReversalAt({lvm}, "{o}")', md, "15m").to_numpy()
            assert (mine == theirs).all(), (kind, seed, d, np.flatnonzero(mine != theirs)[:5])
            hits += int(mine.sum())
    assert hits > 0


def _run(tid, df, **params):
    t = library.get(tid)
    return engine.compute(C.compile_recipe(t, C.resolve_settings(t, "15m", params=params)), {"15m": df})


def _signals(s):
    return {t: int(s.direction[t]) for t in s.signal_bars()}


# Seeds 2, 4, 5 of the 800-bar fixture at swing size 0 were the ones OPEN_QUESTIONS TPL-F6 recorded for F2 / A2.
@pytest.mark.parametrize("tid, params", [("A2", {"swing_degree": 0}), ("F2", {"swing_degree": 0}), ("A2", {}),
                                         ("E3", {"regime": "structure", "swing_degree": 0})])
def test_range_templates_are_mirrored_on_the_series_that_broke(tid, params):
    from app.quality.lookahead import synthetic_bars
    from tests.mirror_tie_fixtures import whole_ticks
    carried = 0
    for seed in (2, 3, 4, 5, 9, 12):
        df = whole_ticks(synthetic_bars(800, tf="15m", seed=seed))
        axis = float(df["high"].max() + df["low"].min())
        s, m = _run(tid, df, **params), _run(tid, mirror(df, axis), **params)
        assert _signals(m) == {t: -d for t, d in _signals(s).items()}, (tid, seed)
        for t in s.signal_bars():
            assert axis - m.invalidation[t] == pytest.approx(s.invalidation[t])
        carried += len(s.signal_bars())
    assert carried, "the fixtures must carry signals"


def test_the_params_hash_records_the_structure_version_only_where_it_is_read():
    """A template that reads DowTrend / SwingBreakType / SwingLabel records STRUCTURE_VERSION (its signals changed on
    equal swings); every other template's hash is what it was before TPL-F6."""
    import hashlib
    import json
    reading = {t.id for t in library.templates() if C.reads_structure(t)}
    assert {"A2", "B2", "E2", "E3", "F2", "G1", "G11"} <= reading
    assert not reading & {"A5", "B1", "C1", "C3", "D1", "D2", "D3", "E1", "F1"}
    d1 = library.get("D1")
    s = C.resolve_settings(d1, "15m")
    body = {"template": "D1", "version": 1, "schema": "atp.template/1", "engine": C.ENGINE_VERSION, "timeframe": "15m",
            "sides": s.sides, "params": s.params, "pa_settings": C._pa_settings_fingerprint()}
    assert C.params_hash(d1, s) == hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()[:20]
    a2 = library.get("A2")
    s = C.resolve_settings(a2, "15m")
    body = {"template": "A2", "version": a2.version, "schema": "atp.template/1", "engine": C.ENGINE_VERSION, "timeframe": "15m",
            "sides": s.sides, "params": s.params, "pa_settings": C._pa_settings_fingerprint()}
    old = hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()[:20]
    new = hashlib.sha256(json.dumps({**body, "structure": C.STRUCTURE_VERSION}, sort_keys=True, default=str).encode()).hexdigest()[:20]
    assert C.params_hash(a2, s) == new != old
