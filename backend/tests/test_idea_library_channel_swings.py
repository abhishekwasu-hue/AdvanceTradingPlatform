"""Idea library slice 4 (OPEN_QUESTIONS IDEA-12, IDEA-14): the two registry functions the T6 and T7 scans needed, and those
scans.

* ChannelLine(side, touches, edge_touches): the B5 channel's trend-side line on EVERY bar - the rising support of a rising
  channel (up), the falling resistance of a falling one (down) - read from the bars before the bar (closed bars only) and
  projected to it. Checked: it is exactly the line of a hand-built channel; it is the very line B5 reads on its own
  channel breakout bars (LineBreakoutRef's "the line's other side"); it ends when the channel breaks; it never changes
  when later bars are added (truncation) and its down side is the up side on reflected bars (mirror).
* PriorSwingHigh / PriorSwingLow(degree, back): the confirmed swing before the latest one (back one), or further back -
  equal to a brute-force reading of the causal swings known at each bar.
* T6 "rising" / "falling" and T7's head and shoulders scans: must fire / must not fire, no look-ahead.
* Pins: the older functions' series and the older scans' text are byte-identical to main before this slice.
"""
import hashlib
import json

import numpy as np
import pandas as pd
import pytest

from app.idea_library import content as c
from app.quality.lookahead import synthetic_bars
from app.screener import mirror as mr
from app.screener.parser import parse
from app.screener.runtime import SymbolData, evaluate
from app.screener.validator import validate

LIB = c.load()
TF = "15m"


def _ev(text, df, **params):
    return evaluate(parse(text), SymbolData("S", {TF: df}), base_tf=TF, params=params)


def _bool(series):
    return np.asarray(series.to_numpy(), dtype=bool)


def channel_bars(n=400, slope=0.05, width=6.0, period=20):
    """A clean rising channel: the lows sit on 100 + slope x bar, the highs about `width` above it. Every `period` bars
    price touches the lower line with a long lower wick and closes near its high (a rejection at the line)."""
    x = np.arange(n, dtype=float)
    base = 100 + slope * x
    phase = (x % period) / period
    tri = np.where(phase < 0.5, phase * 2, 2 - phase * 2)
    mid = base + tri * width
    rising = phase < 0.5
    o = np.where(rising, mid - 0.3, mid + 0.3)
    cl = np.where(rising, mid + 0.3, mid - 0.3)
    h, lo = np.maximum(o, cl) + 0.2, np.minimum(o, cl) - 0.2
    at_line = (x % period) == 0
    lo = np.where(at_line, base, lo)
    o, cl, h = np.where(at_line, base + 0.6, o), np.where(at_line, base + 1.2, cl), np.where(at_line, base + 1.3, h)
    idx = pd.date_range("2026-01-05 03:45", periods=n, freq="15min", tz="UTC")
    return pd.DataFrame({"open": o, "high": h, "low": lo, "close": cl, "volume": 1000.0}, index=idx)


def _lower_line(df):
    return 100 + 0.05 * np.arange(len(df))


# ------------------------------------------------------------------------------------------------------- ChannelLine
def test_channel_line_is_the_lower_line_of_a_rising_channel_and_there_is_no_falling_one():
    df = channel_bars()
    up = _ev('ChannelLine("up")', df).to_numpy(float)
    have = np.isfinite(up)
    assert have.sum() > 300                                               # from the first channel on, every bar
    assert np.allclose(up[have], _lower_line(df)[have])                   # projected to each bar, exactly the line
    assert not have[:40].any()                                           # not before two lows and the far high are known
    assert _ev('ChannelLine("down")', df).isna().all()                    # no falling channel in a rising market


def test_channel_line_is_the_line_b5_itself_reads_on_its_channel_breakouts():
    """The same channel as B5: on each channel breakout bar the line equals LineBreakoutRef's other side of the channel
    (up: base_low, the rising support; down: base_high, the falling resistance)."""
    seen = 0
    for seed in range(6):
        df = synthetic_bars(900, tf=TF, seed=seed)
        for side, what in (("up", "base_low"), ("down", "base_high")):
            fired = _bool(_ev(f'TrendlineBreakout("{side}", "channel", 2, 0)', df))
            ref = _ev(f'LineBreakoutRef("{what}", "{side}", "channel", 2, 0)', df).to_numpy(float)
            line = _ev(f'ChannelLine("{side}")', df).to_numpy(float)
            at = np.flatnonzero(fired)
            assert np.allclose(line[at], ref[at]), (seed, side)
            seen += len(at)
    assert seen > 20


def test_channel_line_ends_when_the_channel_breaks_beyond_its_far_edge_or_through_the_line():
    """A close beyond the far edge (B5's channel breakout) or through the line ends the channel: missing from the next bar
    until a new channel has formed from swings after the break (the next lows here, on the same line)."""
    df = channel_bars()
    up = _ev('ChannelLine("up")', df).to_numpy(float)
    t0 = 310                                                             # a bar at the channel's middle, not a swing low
    for value, gone in ((20.0, 12), (-20.0, 32)):
        hit = df.copy()
        hit.iloc[t0, hit.columns.get_loc("close")] = _lower_line(df)[t0] + value
        hit.iloc[t0, hit.columns.get_loc("high" if value > 0 else "low")] = _lower_line(df)[t0] + value
        after = _ev('ChannelLine("up")', hit).to_numpy(float)
        assert np.array_equal(after[:t0 + 1], up[:t0 + 1], equal_nan=True)   # nothing before or on the bar changes
        assert np.isnan(after[t0 + 1:t0 + 1 + gone]).all(), value          # the broken channel is gone
        assert np.isfinite(after[t0 + 1 + gone:]).any()                    # a new one forms from the later swings


def test_edge_touches_only_removes_channels_and_zero_is_the_b5_channel():
    for seed in (1, 3, 7):
        df = synthetic_bars(900, tf=TF, seed=seed)
        for side in ("up", "down"):
            base = _ev(f'ChannelLine("{side}")', df).to_numpy(float)
            assert np.array_equal(_ev(f'ChannelLine("{side}", 2, 0)', df).to_numpy(float), base, equal_nan=True)
            prev = base
            for k in (1, 2, 3):
                cur = _ev(f'ChannelLine("{side}", 2, {k})', df).to_numpy(float)
                kept = np.isfinite(cur)
                assert not (kept & ~np.isfinite(prev)).any() and np.array_equal(cur[kept], prev[kept]), (seed, side, k)
                prev = cur
    assert np.isfinite(_ev('ChannelLine("up", 2, 2)', channel_bars()).to_numpy(float)).sum() > 300   # both lines touched


@pytest.mark.parametrize("text", ['close > ChannelLine("up", 1)', 'close > ChannelLine("up", 2, 11)', 'close > ChannelLine("flat")',
                                  'close > ChannelLine("up", 2, close)'])
def test_channel_line_arguments_are_checked(text):
    assert validate(parse(text), base_tf=TF).problems


@pytest.mark.parametrize("seed", [2, 5])
def test_channel_line_has_no_look_ahead(seed):
    df = synthetic_bars(900, tf=TF, seed=seed)
    for text in ('ChannelLine("up")', 'ChannelLine("down", 3, 1)', 'ReversalAt(ChannelLine("up", 2, 2), "bullish")'):
        full = _ev(text, df).to_numpy(float)
        assert np.isfinite(full).any()
        for cut in (260, 455, 701):
            part = _ev(text, df.iloc[:cut]).to_numpy(float)
            assert np.array_equal(part, full[:cut], equal_nan=True), (text, cut)


def test_channel_line_mirrors_on_reflected_bars():
    df = channel_bars()
    level = float(df["high"].max() + df["low"].min())
    up = _ev('ChannelLine("up", 2, 2)', df).to_numpy(float)
    down = _ev('ChannelLine("down", 2, 2)', mr.reflect_bars(df, level)).to_numpy(float)
    assert np.array_equal(np.isfinite(up), np.isfinite(down)) and np.allclose(level - down[np.isfinite(down)], up[np.isfinite(up)])
    assert mr.mirror(parse('ReversalAt(ChannelLine("up", 2, 1), "bullish")')).text == 'ReversalAt(ChannelLine("down", 2, 1), "bearish")'


# ---------------------------------------------------------------------------------------- the swing before the latest one
def _pivots(df, degree):
    from app.price_action import causal_swings as cs
    from app.price_action import pa_settings
    raw = pd.DataFrame({k: df[k].astype(float).to_numpy() for k in ("open", "high", "low", "close")})
    raw["timestamp"] = raw["bar_end"] = df.index
    return list(cs.degree_pivots(raw, degree, pa_settings.settings()))


@pytest.mark.parametrize("seed, degree", [(0, 0), (4, 1), (9, 0)])
def test_prior_swing_is_the_swing_before_the_latest_one_known_at_each_bar(seed, degree):
    df = synthetic_bars(900, tf=TF, seed=seed)
    piv = _pivots(df, degree)
    for kind, name in (("H", "PriorSwingHigh"), ("L", "PriorSwingLow")):
        for back, word in ((1, "one"), (2, "two"), (3, "three")):
            got = _ev(f'{name}({degree}, "{word}")', df).to_numpy(float)
            for t in range(0, len(df), 7):                               # brute force: the swings confirmed by bar t
                known = [p.price for p in piv if p.kind == kind and p.confirmed_idx <= t]
                want = known[-1 - back] if len(known) > back else np.nan
                assert (np.isnan(want) and np.isnan(got[t])) or got[t] == want, (name, word, t)
        latest = _ev(f'{"SwingHigh" if kind == "H" else "SwingLow"}({degree})', df)
        assert np.array_equal(_ev(f"{name}({degree})", df).to_numpy(float), _ev(f'{name}({degree}, "one")', df).to_numpy(float),
                              equal_nan=True)
        assert latest.notna().any()


def test_prior_swing_has_no_look_ahead_and_refuses_an_unknown_position():
    df = synthetic_bars(900, tf=TF, seed=6)
    for text in ('PriorSwingHigh(0, "two")', "PriorSwingLow(1)"):
        full = _ev(text, df).to_numpy(float)
        for cut in (300, 640):
            assert np.array_equal(_ev(text, df.iloc[:cut]).to_numpy(float), full[:cut], equal_nan=True)
    assert validate(parse('close > PriorSwingHigh(0, "four")'), base_tf=TF).problems
    assert validate(parse("close > PriorSwingHigh(0, 2)"), base_tf=TF).problems


# ------------------------------------------------------------------------------------------------------------- T6 scans
def test_t6_reads_a_rejection_at_the_trend_side_channel_line_only():
    t6 = LIB.idea("T6")
    assert t6 is not None and [s.id for s in t6.scans] == ["rising", "falling"]
    rising, falling = t6.scans
    df = channel_bars()
    fired = _bool(_ev(rising.text, df, **c.scan_params(t6, rising)))
    assert list(np.flatnonzero(fired)) == list(range(60, 400, 20))      # each touch of the lower line, once the channel is known
    assert not _bool(_ev(falling.text, df, **c.scan_params(t6, falling))).any()   # no falling channel: never the other side
    level = float(df["high"].max() + df["low"].min())                   # the falling channel: the mirror fires on the same bars
    mirrored = _bool(_ev(falling.text, mr.reflect_bars(df, level), **c.scan_params(t6, falling)))
    assert np.array_equal(mirrored, fired)
    # must not fire: the same rejection candle far away from the line (inside the channel)
    inside = df.copy()
    for t in range(60, 400, 20):
        inside.iloc[t, [inside.columns.get_loc(k) for k in ("open", "high", "low", "close")]] = np.array([2.6, 3.3, 2.0, 3.2]) + _lower_line(df)[t]
    assert not _bool(_ev(rising.text, inside, **c.scan_params(t6, rising)))[60:].any()


def test_t6_scans_have_no_look_ahead():
    t6 = LIB.idea("T6")
    assert t6 is not None
    df = synthetic_bars(900, tf=TF, seed=1)
    for scan in t6.scans:
        values = {**c.scan_params(t6, scan), "edge_touches": 1}
        full = _bool(_ev(scan.text, df, **values))
        assert full.any(), scan.id
        for cut in (333, 512, 777):
            assert np.array_equal(_bool(_ev(scan.text, df.iloc[:cut], **values)), full[:cut]), (scan.id, cut)


def test_t6_scans_need_only_the_breakout_flags():
    t6 = LIB.idea("T6")
    assert t6 is not None
    for scan in t6.scans:
        assert c.scan_flags(scan) == ["screener_v2", "screener_builder_u2", "screener_breakouts", "screener_breakouts_bk4"]
        assert c.scan_payload(t6, scan, {"screener_v2", "screener_builder_u2", "screener_breakouts"})["available"] is False
        assert c.scan_params(t6, scan) == {"line_touches": 2, "edge_touches": 2}


# ---------------------------------------------------------------------------------------------- T7 head and shoulders
def _scan(idea_id, scan_id):
    idea = LIB.idea(idea_id)
    assert idea is not None
    return idea, next(s for s in idea.scans if s.id == scan_id)


def _no_zone(text):
    head, _, tail = text.rpartition(" AND NearZone(")
    assert head and tail
    return head


@pytest.mark.parametrize("scan_id, own", [("hs_top", "H"), ("hs_bottom", "L")])
def test_t7_right_shoulder_is_rejected_at_the_left_shoulders_height_below_the_head(scan_id, own):
    idea, scan = _scan("T7", scan_id)
    sign = 1 if own == "H" else -1
    seen = 0
    for seed in range(8):
        df = synthetic_bars(900, tf=TF, seed=seed)
        fired = np.flatnonzero(_bool(_ev(_no_zone(scan.text), df, swing_size=0)))
        piv = _pivots(df, 0)
        for t in fired:
            known = [p for p in piv if p.confirmed_idx <= t]
            assert known[-1].kind != own                                  # price is coming back up from the neckline low
            shoulder, head = [p.price for p in known if p.kind == own][-2:]
            assert sign * (head - shoulder) > 0                           # the head is beyond the left shoulder
            extreme = df["high"].iloc[t] if own == "H" else df["low"].iloc[t]
            assert sign * (head - extreme) > 0                            # the right shoulder stays below the head
        seen += len(fired)
    assert seen, "the fixtures must carry a right shoulder"


@pytest.mark.parametrize("scan_id, own", [("hs_top_retest", "H"), ("hs_bottom_retest", "L")])
def test_t7_neckline_retest_is_the_retest_of_a_low_between_the_shoulders(scan_id, own):
    """Must fire / must not fire: the retest of the broken neckline after left shoulder, head and right shoulder; the
    broken level is one of the swing lows between the shoulders. The same swing retest without the pattern is no signal."""
    idea, scan = _scan("T7", scan_id)
    side, other = ("down", "L") if own == "H" else ("up", "H")
    sign = 1 if own == "H" else -1
    seen = misses = 0
    for degree in (0, 1):
        for seed in range(16):
            df = synthetic_bars(900, tf=TF, seed=seed)
            values = {"swing_size": degree, "retest_max_bars": 10}
            fired = _bool(_ev(scan.text, df, **values))
            plain = _bool(_ev(f'RetestEntry("{side}", "swing", $retest_max_bars, TRUE, swing=$swing_size)', df, **values))
            assert not (fired & ~plain).any()
            misses += int((plain & ~fired).sum())
            level = _ev(f'RetestEntryRef("level", "{side}", "swing", $retest_max_bars, TRUE, swing=$swing_size)', df, **values).to_numpy(float)
            piv = _pivots(df, degree)
            for t in np.flatnonzero(fired):
                known = [p for p in piv if p.confirmed_idx <= t]
                ls, head, rs = [p for p in known if p.kind == own][-3:]
                assert sign * (head.price - ls.price) > 0 and sign * (head.price - rs.price) > 0
                lows = [p.price for p in known if p.kind == other and ls.bar_idx < p.bar_idx < rs.bar_idx]
                assert any(abs(lv - level[t]) < 1e-9 for lv in lows), (seed, t)
            seen += int(fired.sum())
    assert seen and misses, "the fixtures must carry a neckline retest and a plain swing retest"


def test_t7_head_and_shoulders_scans_have_no_look_ahead():
    t7 = LIB.idea("T7")
    assert t7 is not None
    for scan in t7.scans[2:]:
        text = scan.text if "NearZone" not in scan.text else _no_zone(scan.text)
        values = {k: v for k, v in {"swing_size": 0, "retest_max_bars": 10}.items() if f"${k}" in text}
        df, full = next((d, f) for d in (synthetic_bars(900, tf=TF, seed=seed) for seed in range(12))
                        for f in [_bool(_ev(text, d, **values))] if f.any())
        for cut in (400, 615, 820):
            assert np.array_equal(_bool(_ev(text, df.iloc[:cut], **values)), full[:cut]), (scan.id, cut)


def test_t7_head_and_shoulders_flags():
    t7 = LIB.idea("T7")
    assert t7 is not None
    flags = {s.id: c.scan_flags(s) for s in t7.scans}
    assert flags["hs_top"] == flags["hs_bottom"] == ["screener_v2", "screener_builder_u2", "market_zones"]
    assert flags["hs_top_retest"] == flags["hs_bottom_retest"] == ["screener_v2", "screener_builder_u2", "screener_breakouts",
                                                                 "screener_breakouts_bk4"]


# ------------------------------------------------------------------------------------------- the adopt re-check (slice 4)
def test_no_template_reads_an_area_a_channel_line_or_a_swing_pattern_so_the_reasons_stand():
    """T1, T3, T4, T6, T7 and T8 stay without a template: none of the built templates reads a market zone, the channel
    line or the swings before the latest one. A template that starts to must trigger the adopt review again."""
    from app.strategy_templates import library
    text = json.dumps([json.loads(t.model_dump_json()) for t in library.templates() + library.archived()])
    for name in ("NearZone", "ZoneRole", "ChannelLine", "PriorSwingHigh", "PriorSwingLow"):
        assert name not in text, name
    for idea_id in ("T1", "T3", "T4", "T6", "T7", "T8"):
        idea = LIB.idea(idea_id)
        assert idea is not None and idea.adopt is None and idea_id in c.ADOPT_REVIEW
    assert "IDEA-12" in c.ADOPT_REVIEW["T6"] and "C2" in c.ADOPT_REVIEW["T7"]


# ------------------------------------------------------------------------------------------------ byte-identical pins
def _digest(series):
    a = np.asarray(series.to_numpy(), dtype=float)
    nan = np.isnan(a)
    return hashlib.sha256(nan.tobytes() + np.round(np.where(nan, 0.0, a), 6).tobytes()).hexdigest()[:16]


# computed on main before this slice (the functions ChannelLine reuses, the swings and the retest the H&S scans read)
PINS = {
    '1|TrendlineBreakout("up", "channel", 2, 0)': "9bf018601512d4f7",
    '1|TrendlineBreakout("down", "channel", 2, 0)': "153fbcf17bbab08a",
    '1|TrendlineBreakout("up", "line", 2, 0)': "63adabe61e2735f1",
    '1|LineBreakoutRef("level", "up", "channel", 2, 0)': "287ab8d88ae6e66a",
    '1|LineBreakoutRef("base_low", "down", "channel", 2, 0)': "9be79e40dad905a7",
    '1|RetestEntry("down", "channel", 20, FALSE, k=0)': "a14501d3d332865c",
    '1|RetestEntry("down", "swing", 10, TRUE, swing=0)': "8eec8a8e5eae4da2",
    '1|SwingHigh(0)': "f191a40be571121c",
    '1|SwingLow(1)': "ce289a239041e614",
    '1|ReversalAt(SwingHigh(0), "bearish")': "f2503fc25bc93acc",
    '7|TrendlineBreakout("up", "channel", 2, 0)': "61d9db3e9872caa4",
    '7|TrendlineBreakout("down", "channel", 2, 0)': "a2d11fef3e738860",
    '7|TrendlineBreakout("up", "line", 2, 0)': "c03cb6d91186dd3e",
    '7|LineBreakoutRef("level", "up", "channel", 2, 0)': "35ffcf494d2662f0",
    '7|LineBreakoutRef("base_low", "down", "channel", 2, 0)': "87f1accfc33049e1",
    '7|RetestEntry("down", "channel", 20, FALSE, k=0)': "40762940f1c3641c",
    '7|RetestEntry("down", "swing", 10, TRUE, swing=0)': "5b409452c5491c03",
    '7|SwingHigh(0)': "8e2a4f01de9776ac",
    '7|SwingLow(1)': "e3a358b2c508411b",
    '7|ReversalAt(SwingHigh(0), "bearish")': "a45805dafe94d630",
}
OLD_SCANS = {"T1": ("up", "down"), "T2": ("upper", "lower"), "T3": ("bearish", "bullish"), "T4": ("highs", "lows"),
             "T5": ("above", "below"), "T7": ("top", "bottom"), "T8": ("bull", "bear", "upper", "lower")}
OLD_SCANS_SHA = "f11725ead7e153cc216ab05499104620b6974df863ee6211e1c24b0319224d2b"


def test_the_older_series_are_byte_identical():
    frames = {seed: synthetic_bars(900, tf=TF, seed=seed) for seed in (1, 7)}
    got = {key: _digest(_ev(key.split("|", 1)[1], frames[int(key.split("|", 1)[0])])) for key in PINS}
    assert got == PINS


def test_the_older_scans_are_byte_identical():
    old = [[i.id, s.id, s.text] for i in LIB.ideas for s in i.scans if s.id in OLD_SCANS.get(i.id, ())]
    assert [(i, s) for i, s, _ in old] == [(i, s) for i, ids in OLD_SCANS.items() for s in ids]
    assert hashlib.sha256(json.dumps(old).encode()).hexdigest() == OLD_SCANS_SHA
