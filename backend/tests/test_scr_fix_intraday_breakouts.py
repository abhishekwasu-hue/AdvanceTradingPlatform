"""SCR-FIX intraday breakouts: the six pre-built intraday breakout screens the M93 audit marked BUG, on an index whose
volume comes from the near-month future (app/market_data/index_volume.py: 0 where the future has no bar).

The audit's root cause: that placeholder 0 was averaged as traded volume (the plain 20-bar average of NR7 and the
same-time-of-day average of the session screens), so a break on ordinary volume early in the future's window read as
2-20x and passed. Fixed by M93-Q3 (owner decision 9 Nov: a 0 / missing volume is no volume data - the volume condition
is not applied and the result says so): `runtime.no_index_volume` / `index_volume.screen_frame` and
`breakouts.screens.Bars.allow_missing`. These tests pin it on each of the six screens' own ScreenQL calls, through the
screener's own entry points (`run_screen`, `evaluate`), with synthetic sessions and our own numbers:
positive / negative cases, the early window (too few sessions with volume), the session boundary and no look-ahead.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.screener import compile_screen
from app.screener.breakouts.screens import VOLUME_SKIPPED_NOTE
from app.screener.runtime import SymbolData, evaluate, run_screen
from tests.breakout_fixtures import sessions_frame

Bar = tuple[float, float, float, float, float]

# The six screens' own calls (templates.py / resolved ScreenQL; the up side, without the DowTrend context parts, which
# read no volume): (screen id, base timeframe, ScreenQL, bar of the session at which the test day breaks).
SCREENS = [
    ("intraday_15m_nr7", "15m", 'NRBreakout("up", "nr7", 1.5, 0.2)', 5),
    ("intraday_5m_orb15", "5m", 'ORBreakout("up", 15, 1.5, TRUE, 15, 0.2)', 14),
    ("intraday_15m_orb30", "15m", 'ORBreakout("up", 30, 1.5, TRUE, 15, 0.2)', 5),
    ("intraday_15m_first_hour", "15m", 'DayLevelBreak("up", "first_hour", 1.5, TRUE, 15, 0.2)', 5),
    ("intraday_5m_prev_day", "5m", 'DayLevelBreak("up", "prev_day", 1.5, TRUE, 15, 0.2)', 14),
    ("intraday_5m_day_extreme", "5m", 'DayExtremeBreak("up", 1.5, TRUE, 15, 0.2)', 14),
]
SESSION_SCREENS = [s for s in SCREENS if s[0] != "intraday_15m_nr7"]
BARS = {"5m": 75, "15m": 25}               # bars of a regular 09:15-15:30 session
MINUTES = {"5m": 5, "15m": 15}
INDEX = "NIFTY BANK"                       # an index: its 0 volume is "no volume data" for the screener (M93-Q3)
SESSIONS = 30
FIRST_REAL = 23                            # the future's volume starts here; 0 before (the placeholder)
VOL = 1000.0                               # flat traded volume, so every average (plain or same time of day) is 1000


def _quiet(n: int, volume: float) -> list[Bar]:
    """A flat session: closes alternate 99.9 / 100.1, every high 100.25 and low 99.75 (equal ranges: each bar is an NR7)."""
    return [((99.9, 100.25, 99.75, 100.1, volume) if i % 2 == 0 else (100.1, 100.25, 99.75, 99.9, volume)) for i in range(n)]


def _break_day(n: int, slot: int, volume: float, break_volume: float) -> list[Bar]:
    """A quiet session whose bar `slot` closes at 102.0, far beyond every level the six screens read (the 15 / 30-minute
    range, the first hour, the previous session and the session's high so far are all 100.25), then quiet again."""
    day = _quiet(n, volume)
    day[slot] = (100.2, 102.2, 100.1, 102.0, break_volume)
    return day


def _frame(tf: str, target: int, slot: int, break_volume: float, *, first_real: int = FIRST_REAL, sessions: int = SESSIONS) -> pd.DataFrame:
    """`sessions` sessions of `tf` bars; volume 0 before session `first_real` (the future has no bar there), VOL after;
    session `target` breaks at `slot` on `break_volume`; the frame ends with the target session."""
    days = []
    for k in range(sessions):
        v = VOL if k >= first_real else 0.0
        days.append(_break_day(BARS[tf], slot, v, break_volume) if k == target else _quiet(BARS[tf], v))
    return sessions_frame(days[: target + 1], minutes=MINUTES[tf])


def _series(text: str, tf: str, frame: pd.DataFrame, symbol: str = INDEX) -> np.ndarray:
    ast, validated = compile_screen(text, base_tf=tf)
    assert validated.ok, validated.problems
    v = evaluate(ast, SymbolData(symbol, {tf: frame.copy()}), base_tf=tf)
    return pd.Series(v).reindex(frame.index).fillna(False).astype(bool).to_numpy()


def _last(text: str, tf: str, frame: pd.DataFrame, symbol: str = INDEX):
    ast, validated = compile_screen(text, base_tf=tf)
    [m] = run_screen(ast, validated, [SymbolData(symbol, {tf: frame.copy()})], base_tf=tf, check_history=False)
    return m


def _bar(tf: str, target: int, slot: int) -> int:
    return target * BARS[tf] + slot


# ------------------------------------------------------------------------- the audit's bug: zeros diluting the average
@pytest.mark.parametrize("sid,tf,text,slot", SCREENS)
def test_an_ordinary_volume_break_is_not_confirmed_by_placeholder_zeros(sid, tf, text, slot):
    """NEGATIVE. The break comes on ordinary volume (1.0x). The session screens' break day has 6 earlier sessions with
    volume (>= tod_min_sessions 5); NR7's break is the 6th bar of the first session with volume, so its 20-bar window
    holds 5 real bars and 15 placeholders. Read as traded volume, the zeros made it 3.3x / 4x (the audit's case);
    read as no volume data, the screen does not take that ratio."""
    target = FIRST_REAL + 6 if sid != "intraday_15m_nr7" else SESSIONS - 1
    frame = _frame(tf, target, slot, VOL)
    j = _bar(tf, target, slot)
    as_stock = _series(text, tf, frame, symbol="STOCKX")           # a stock's 0 IS traded volume: the old reading
    as_index = _series(text, tf, frame)
    if sid == "intraday_15m_nr7":
        assert not as_stock[j] and not as_index[j]                  # 20 real bars in the window: 1.0x fails either way
        early = _frame(tf, FIRST_REAL, slot, VOL)
        k = _bar(tf, FIRST_REAL, slot)
        assert _series(text, tf, early, symbol="STOCKX")[k]          # 5 real + 15 zeros averaged: 4x, a false pass
        m = _last(text, tf, early.iloc[: k + 1])
        assert m.matched and m.note == VOLUME_SKIPPED_NOTE           # no volume data in the window: not applied, said so
    else:
        assert as_stock[j], "zeros averaged as traded volume inflate the same-time ratio (the audit's artefact)"
        assert not as_index[j], "6 sessions with volume: the real same-time average (1.0x) decides"
        m = _last(text, tf, frame.iloc[: j + 1])
        assert not m.matched and m.note is None


@pytest.mark.parametrize("sid,tf,text,slot", SESSION_SCREENS)
def test_a_strong_volume_break_still_matches_and_needs_no_note(sid, tf, text, slot):
    """POSITIVE. The same break on 3x the real same-time average: a match, and no "volume not available" note."""
    target = FIRST_REAL + 6
    frame = _frame(tf, target, slot, 3 * VOL)
    j = _bar(tf, target, slot)
    hits = np.flatnonzero(_series(text, tf, frame)[target * BARS[tf]:]) + target * BARS[tf]
    assert list(hits) == [j]
    m = _last(text, tf, frame.iloc[: j + 1])
    assert m.matched and m.note is None


def test_nr7_strong_volume_break_matches_on_the_real_average():
    sid, tf, text, slot = SCREENS[0]
    target = SESSIONS - 1
    frame = _frame(tf, target, slot, 3 * VOL)
    j = _bar(tf, target, slot)
    assert _series(text, tf, frame)[j]
    m = _last(text, tf, frame.iloc[: j + 1])
    assert m.matched and m.note is None


@pytest.mark.parametrize("sid,tf,text,slot", SESSION_SCREENS)
def test_too_few_sessions_with_volume_is_no_volume_data_never_a_ratio(sid, tf, text, slot):
    """The 2nd session with volume (1 earlier session < tod_min_sessions): no same-time average can be formed from real
    volume, so the confirmation is not applied and the result says so (owner decision M93-Q3) - it is never a ratio
    against zeros. On a stock (real volume, too short a history) the same bar has no ratio and does not match."""
    target = FIRST_REAL + 1
    frame = _frame(tf, target, slot, VOL)
    j = _bar(tf, target, slot)
    m = _last(text, tf, frame.iloc[: j + 1])
    assert m.matched and m.note == VOLUME_SKIPPED_NOTE
    stock = frame.iloc[FIRST_REAL * BARS[tf]:]                     # a stock with only 2 sessions of bars, all with volume
    s = _last(text, tf, stock.iloc[: BARS[tf] + slot + 1], symbol="STOCKX")
    assert not s.matched


# ---------------------------------------------------------------------------------------------- the session boundary
def test_previous_day_break_reads_the_previous_session_and_never_signals_on_the_opening_bar():
    """A gap open: the session opens and stays beyond the previous session's high (100.25). The 09:15-09:25 bars are
    inside skip_first_min, so the signal is the first eligible bar (09:30) - never the opening bar."""
    tf, text = "5m", 'DayLevelBreak("up", "prev_day", 1.5, TRUE, 15, 0.2)'
    days = [_quiet(BARS[tf], VOL) for _ in range(SESSIONS - 1)]
    days.append([(102.0, 102.3, 101.9, 102.1, 3 * VOL) for _ in range(BARS[tf])])
    frame = sessions_frame(days, minutes=5)
    hits = np.flatnonzero(_series(text, tf, frame))
    start = (SESSIONS - 1) * BARS[tf]
    assert list(hits) == [start + 3]                                # 09:30 IST, the first bar after the first 15 minutes
    assert pd.Timestamp(frame.index[start + 3]).tz_convert("Asia/Kolkata").strftime("%H:%M") == "09:30"


@pytest.mark.parametrize("sid,tf,text,slot", [s for s in SESSION_SCREENS if s[0] in ("intraday_5m_orb15", "intraday_15m_orb30", "intraday_15m_first_hour")])
def test_the_opening_window_never_signals_inside_itself(sid, tf, text, slot):
    """ORB / first hour: a close beyond the range while the range is still forming is not a break (the level is not
    known yet); the first eligible bar after the window is."""
    target = SESSIONS - 1
    frame = _frame(tf, target, 0, 3 * VOL, first_real=0)         # the session's FIRST bar closes at 102.0
    hits = np.flatnonzero(_series(text, tf, frame)[target * BARS[tf]:])
    assert 0 not in hits


def test_nr7_reads_the_chart_as_one_continuous_series_across_sessions():
    """NR7 has no session clock by design (one continuous chart, owner principle): the opening bar can break the
    previous session's last bar. The audit listed this as a spec gap, not a bug; the screen keeps it."""
    tf, text = "15m", 'NRBreakout("up", "nr7", 1.5, 0.2)'
    target = SESSIONS - 1
    frame = _frame(tf, target, 0, 3 * VOL, first_real=0)
    assert _series(text, tf, frame)[_bar(tf, target, 0)]


# --------------------------------------------------------------------------------------------------------- no look-ahead
@pytest.mark.parametrize("sid,tf,text,slot", SCREENS)
@pytest.mark.parametrize("target,break_volume", [(FIRST_REAL, VOL), (FIRST_REAL + 1, VOL), (FIRST_REAL + 6, 3 * VOL), (FIRST_REAL + 6, VOL)])
def test_the_full_series_equals_the_live_run_on_bars_cut_at_each_bar(sid, tf, text, slot, target, break_volume):
    """scan = history = live: the series at bar j equals run_screen on the frame cut at j, around the break and on the
    session's first bars, in and before the future's volume window."""
    frame = _frame(tf, target, slot, break_volume)
    full = _series(text, tf, frame)
    start = target * BARS[tf]
    for j in sorted({start, start + 1, start + slot - 1, start + slot, start + slot + 1, start - 1}):
        m = _last(text, tf, frame.iloc[: j + 1])
        assert m.matched == bool(full[j]), (sid, target, j)
