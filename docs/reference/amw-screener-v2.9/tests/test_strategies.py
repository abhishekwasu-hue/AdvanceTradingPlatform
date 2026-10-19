"""Built-in strategy tests (Breakout / Pullback / Reversal / Trend / Intraday / Swing) on SYNTHETIC fixtures."""
import numpy as np
import pandas as pd

from src.analysis.context import analyze_tf
from src.data.validation import QualityReport
from src.strategies.base import Condition, StrategyContext
from src.strategies.registry import REGISTRY, enabled_strategies, evaluate_all, required_timeframes
from tests.conftest import intraday_session, make_ohlc


def ctx_of(frames: dict, cfg, status="OK", bench=None):
    an = {tf: analyze_tf(df, tf, cfg) for tf, df in frames.items()}
    return StrategyContext("T", "T.NS", an, {tf: QualityReport("T", tf, status) for tf in frames}, bench or {}, cfg)


def breakout_frame():
    base = list(np.linspace(80, 100, 140)) + list(100 + 2 * np.sin(np.linspace(0, 8 * np.pi, 120))) + [103.5, 105]
    vol = [1000.0] * (len(base) - 2) + [3000.0, 2500.0]
    return make_ohlc(base, spread=0.4, vol=vol)


def test_condition_normalises_numpy_bools():
    c = Condition("x", np.True_, np.False_)
    assert c.passed is True and c.mandatory is False


def test_breakout_qualifies_with_volume_and_fails_without(cfg):
    sig = [s for s in REGISTRY["breakout"](cfg).evaluate(ctx_of({"1D": breakout_frame()}, cfg)) if s.direction == "bullish"]
    assert any(s.status == "qualified" for s in sig)
    q = [s for s in sig if s.status == "qualified"][0]
    assert q.stop < q.target and q.rr >= cfg["strategies"]["breakout"]["params"]["min_rr"]
    lowvol = breakout_frame()
    lowvol["Volume"] = 1000.0
    sig2 = REGISTRY["breakout"](cfg).evaluate(ctx_of({"1D": lowvol}, cfg))
    assert not any(s.status == "qualified" for s in sig2)
    assert any("volume confirmation" in " ".join(c.name for c in s.failed) for s in sig2)


def test_breakout_touch_is_not_breakout(cfg):
    f = breakout_frame().iloc[:-2]          # price near but not closed beyond the level
    sigs = REGISTRY["breakout"](cfg).evaluate(ctx_of({"1D": f}, cfg))
    assert not any(s.status == "qualified" for s in sigs)


def test_failed_breakout_rejected(cfg):
    f = breakout_frame()
    extra = make_ohlc([101.0, 99.5], spread=0.4, start=f.index[-1] + pd.Timedelta(days=1))
    sigs = REGISTRY["breakout"](cfg).evaluate(ctx_of({"1D": pd.concat([f, extra])}, cfg))
    assert not any(s.status == "qualified" and s.direction == "bullish" for s in sigs)


def test_pullback_requires_trend(cfg):
    up = make_ohlc(list(np.linspace(100, 200, 250)) + list(np.linspace(200, 192, 6)), spread=0.6)
    s_up = REGISTRY["pullback"](cfg).evaluate(ctx_of({"1D": up}, cfg))
    assert all(s.direction == "bullish" for s in s_up)
    flat = make_ohlc(100 + np.sin(np.arange(256) / 4), spread=0.6)
    assert REGISTRY["pullback"](cfg).evaluate(ctx_of({"1D": flat}, cfg)) == []


def test_reversal_needs_triggers_and_marks_candidates(cfg):
    c = list(np.linspace(200, 120, 200)) + list(np.linspace(120, 128, 5)) + list(np.linspace(128, 118, 5)) + [123, 127, 131]
    sigs = REGISTRY["reversal"](cfg).evaluate(ctx_of({"1D": make_ohlc(c, spread=0.8)}, cfg))
    bull = [s for s in sigs if s.direction == "bullish"][0]
    bear = [s for s in sigs if s.direction == "bearish"]
    assert bull.status in ("qualified", "candidate")
    if bull.status == "candidate":
        assert any("CHoCH" in c.name for c in bull.conditions)
    assert all(s.status == "rejected" for s in bear)


def test_trend_continuation_and_rejects_downtrend(cfg):
    up = make_ohlc(list(np.linspace(100, 200, 260) + np.sin(np.arange(260)) * 1.5), spread=0.5)
    assert any(s.status == "qualified" for s in REGISTRY["trend_continuation"](cfg).evaluate(ctx_of({"1D": up}, cfg)))
    dn = make_ohlc(list(np.linspace(200, 100, 260) + np.sin(np.arange(260)) * 1.5), spread=0.5)
    assert not any(s.direction == "bullish" for s in REGISTRY["trend_continuation"](cfg).evaluate(ctx_of({"1D": dn}, cfg)))


def test_intraday_orb_session_filter(cfg):
    prev = intraday_session(list(np.linspace(99.5, 100.2, 25)), day="2026-10-15", spread=0.05)
    closes = [100.2, 100.1, 100.15, 100.1, 100.12, 100.18, 100.1, 100.15, 100.6, 100.9]
    vol = [1000.0] * 8 + [4000.0, 3000.0]
    today = intraday_session(closes, day="2026-10-16", vol=vol, spread=0.05)
    df = pd.concat([make_ohlc(list(np.linspace(95, 99.5, 120)), tf="15m", start="2026-10-01 09:15", spread=0.05), prev, today])
    df = df[~df.index.duplicated()].sort_index()
    sigs = REGISTRY["intraday"](cfg).evaluate(ctx_of({"15m": df}, cfg))
    orb = [s for s in sigs if "Opening Range" in s.setup and s.direction == "bullish"]
    assert orb, "ORB evaluated"
    late = df[df.index.time <= pd.Timestamp("09:20").time()]
    assert REGISTRY["intraday"](cfg).evaluate(ctx_of({"15m": late}, cfg)) == [] or all(
        any(c.name == "session time filter" and c.passed is False for c in s.conditions) for s in REGISTRY["intraday"](cfg).evaluate(ctx_of({"15m": late}, cfg)))


def test_swing_weekly_gate(cfg):
    up = make_ohlc(list(np.linspace(100, 200, 260) + np.sin(np.arange(260)) * 1.5), spread=0.5)
    wk_dn = make_ohlc(list(np.linspace(300, 150, 100)), tf="1W", spread=1.0)
    sigs = REGISTRY["swing"](cfg).evaluate(ctx_of({"1D": up, "1W": wk_dn}, cfg))
    assert all(s.status != "qualified" for s in sigs if s.direction == "bullish")


def test_stale_and_insufficient_never_qualify(cfg):
    sigs = REGISTRY["breakout"](cfg).evaluate(ctx_of({"1D": breakout_frame()}, cfg, status="INSUFFICIENT"))
    assert all(s.status == "rejected" and s.label.startswith("Rejected") for s in sigs)
    stale = REGISTRY["breakout"](cfg).evaluate(ctx_of({"1D": breakout_frame()}, cfg, status="STALE"))
    ok = REGISTRY["breakout"](cfg).evaluate(ctx_of({"1D": breakout_frame()}, cfg, status="OK"))
    assert max(s.score for s in stale) <= max(s.score for s in ok) - 4.9


def test_score_never_overrides_mandatory_and_is_reproducible(cfg):
    a = [s.to_row() for s in evaluate_all(ctx_of({"1D": breakout_frame()}, cfg), enabled_strategies(cfg, ["breakout", "pullback"]))]
    b = [s.to_row() for s in evaluate_all(ctx_of({"1D": breakout_frame()}, cfg), enabled_strategies(cfg, ["breakout", "pullback"]))]
    assert [(x["key"], x["score"], x["status"]) for x in a] == [(x["key"], x["score"], x["status"]) for x in b]
    for r in a:
        if "[M]" in (r["conditions_failed"] or ""):
            assert r["status"] == "rejected"


def test_strategies_independently_enabled(cfg):
    cfg["strategies"]["breakout"]["enabled"] = False
    names = [s.name for s in enabled_strategies(cfg)]
    assert "breakout" not in names and "pullback" in names
    assert "1W" in required_timeframes(enabled_strategies(cfg, ["swing"]))


def test_mtf_conflict_reported(cfg):
    up = make_ohlc(list(np.linspace(100, 200, 260)), spread=0.5)
    wk_dn = make_ohlc(list(np.linspace(300, 150, 100)), tf="1W", spread=1.0)
    c = ctx_of({"1D": up, "1W": wk_dn}, cfg)
    assert c.get("1D").trend_label == "Bullish" and c.get("1W").trend_label == "Bearish"
    from src.strategies.base import mtf_alignment
    v, txt = mtf_alignment(c, "1D", "bullish")
    assert 0 < v < 1 and "1W:Bearish" in txt


def test_bearish_trend_condition_text_matches_logic(cfg):
    """Regression: bearish EMA-alignment detail used to read 'close<EMA20<EMA50>EMA200'."""
    dn = make_ohlc(list(np.linspace(200, 100, 260) + np.sin(np.arange(260)) * 1.5), spread=0.5)
    sigs = [s for s in REGISTRY["trend_continuation"](cfg).evaluate(ctx_of({"1D": dn}, cfg)) if s.direction == "bearish"]
    det = [c.detail for c in sigs[0].conditions if c.name == "EMA alignment"][0]
    assert det == "close<EMA20<EMA50<EMA200"


def test_backtest_weekly_context_excludes_forming_week():
    """Regression A-001: the swing backtest must not treat the current partial week as a completed weekly bar."""
    import pandas as pd
    from src.backtest.engine import _weekly
    idx = pd.bdate_range("2026-10-05", "2026-10-14", tz="Asia/Kolkata")            # ends on a Wednesday
    df = pd.DataFrame({"Open": 1.0, "High": 2.0, "Low": 0.5, "Close": 1.5, "Volume": 1.0}, index=idx)
    w = _weekly(df)
    assert [d.date().isoformat() for d in w.index] == ["2026-10-09"]               # only the completed week
    full = _weekly(df[df.index <= "2026-10-09 23:59"])
    assert full.index[-1].date().isoformat() == "2026-10-09" and len(full) == 1     # a Friday close completes it


def test_backtest_htf_context_is_point_in_time():
    """A-007: intraday backtests get a daily context from COMPLETED sessions only; 1D gets completed weeks."""
    import pandas as pd
    from src.backtest.engine import _htf_frames
    from src.config import default_settings
    m = default_settings()["market"]
    idx = pd.date_range("2026-10-14 09:15", "2026-10-14 15:15", freq="15min", tz="Asia/Kolkata")
    idx = idx.append(pd.date_range("2026-10-15 09:15", "2026-10-15 11:00", freq="15min", tz="Asia/Kolkata"))
    df = pd.DataFrame({"Open": 1.0, "High": 2.0, "Low": 0.5, "Close": 1.5, "Volume": 1.0}, index=idx)
    d = _htf_frames(df, "15m", m)["1D"]
    assert [x.date().isoformat() for x in d.index] == ["2026-10-14"]                 # 15 Oct session still forming
    full = _htf_frames(df[df.index.date == pd.Timestamp("2026-10-14").date()], "15m", m)["1D"]
    assert len(full) == 1                                                            # 15:15 bar closes the session
    assert "1W" in _htf_frames(df, "1D", m) and _htf_frames(df, "1W", m) == {}


def test_backtest_intermediate_htf_frames_are_completed_and_aligned():
    """A-007b: 15m backtests also get completed 1H bars aligned to 09:15; 5m gets 15m + 1H + 1D."""
    import pandas as pd
    from src.backtest.engine import _htf_frames
    from src.config import default_settings
    m = default_settings()["market"]
    idx = pd.date_range("2026-10-15 09:15", "2026-10-15 11:15", freq="15min", tz="Asia/Kolkata")   # last bar 11:15-11:30
    df = pd.DataFrame({"Open": 1.0, "High": 2.0, "Low": 0.5, "Close": 1.5, "Volume": 1.0}, index=idx)
    h = _htf_frames(df, "15m", m)
    assert [t.strftime("%H:%M") for t in h["1H"].index] == ["09:15", "10:15"]       # 11:15 hour still forming
    assert h["1H"].Volume.tolist() == [4.0, 4.0]
    assert set(_htf_frames(df.resample("5min").ffill(), "5m", m)) == {"15m", "1H", "1D"}
