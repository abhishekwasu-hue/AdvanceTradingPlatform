"""v2 scanner predicates in the shared IR engine: candle_pattern, supertrend_dir, pct_change, rsi_divergence,
gap_recent, sr_role_reversal, htf_close_above_ema; argument validation (unknown/invalid args are rejected)."""
import numpy as np
import pandas as pd

from src.analysis.context import analyze_tf
from src.config import default_settings
from src.lab.contract import build_context
from src.lab.ir import evaluate_ir, validate_strategy_ir

CFG = default_settings()


def ctx_from(df, htf=None, status="OK"):
    t = analyze_tf(df, "1D", CFG)
    return build_context(t, {}, {"timeframe": "1D", "data_status": status, "symbol": "T"}, htf=htf), t


def run(pred, args, ctx):
    ir = {"ir_version": 1, "long": {"type": "group", "logic": "AND", "children": [
        {"type": "rule", "name": "x", "expr": {"t": "pred", "name": pred, "args": args}}]}, "short": None}
    assert validate_strategy_ir(ir) == [], validate_strategy_ir(ir)
    return evaluate_ir(ir, ctx)["rules"][0]["passed"]


def trend_df(n=260, slope=0.5, last=None):
    c = 100 + slope * np.arange(n) + np.sin(np.arange(n) / 3)
    df = pd.DataFrame({"Open": c - 0.2, "High": c + 1, "Low": c - 1, "Close": c, "Volume": 1e6},
                      index=pd.date_range("2025-01-01", periods=n, freq="B", tz="Asia/Kolkata"))
    if last:
        df.iloc[-1] = last
    return df


def test_supertrend_and_pct_change():
    ctx, _ = ctx_from(trend_df())
    assert run("supertrend_dir", {"direction": "bullish"}, ctx) is True
    assert run("supertrend_dir", {"direction": "bearish"}, ctx) is False
    base = trend_df()
    p = base.Close.iloc[-2]
    up = base.copy()
    up.iloc[-1] = (p, p * 1.075, p * 0.999, p * 1.07, 1e6)                          # last close +7 % vs prior close
    c2, _ = ctx_from(up)
    assert run("pct_change", {"bars": 1, "op": ">=", "value": 5.0}, c2) is True
    assert run("pct_change", {"bars": 1, "op": "<=", "value": 0.0}, c2) is False


def test_candle_pattern_and_gap_predicates():
    base = trend_df()
    prev = base.Close.iloc[-2]
    df = base.copy()
    df.iloc[-1] = (prev + 3.0, prev + 3.3, prev - 1.5, prev - 1.2, 1e6)             # gap up, closes below prior close
    ctx, t = ctx_from(df)
    assert any(c["pattern"] == "Gap Reversal" for c in t.candles if c["i"] == len(df) - 1)
    assert run("candle_pattern", {"pattern": "Gap Reversal", "bias": "bearish", "bars": 1}, ctx) is True
    assert run("candle_pattern", {"pattern": "Gap Reversal", "bias": "bullish", "bars": 1}, ctx) is False
    assert run("gap_recent", {"direction": "up", "bars": 1, "min_atr": 0.5}, ctx) is True
    assert run("gap_recent", {"direction": "up", "bars": 1, "min_atr": 0.5, "unfilled": True}, ctx) is False  # traded back


def test_rsi_divergence_predicate_matches_analysis_module():
    from src.analysis.context import rsi_divergence
    rng = np.random.default_rng(3)
    c = 100 + np.cumsum(rng.normal(0, 1, 300))
    df = pd.DataFrame({"Open": c, "High": c + 1, "Low": c - 1, "Close": c, "Volume": 1e6},
                      index=pd.date_range("2025-01-01", periods=300, freq="B", tz="Asia/Kolkata"))
    for end in range(220, 300, 7):                                                  # same answer as the strategy module
        ctx, t = ctx_from(df.iloc[:end])
        for d in ("bullish", "bearish"):
            got = run("rsi_divergence", {"direction": d, "max_age": 15}, ctx)
            ref = rsi_divergence(t, d, 15) is not None
            assert (got is True) == ref, (end, d, got, ref)


def test_htf_and_role_reversal_predicates():
    ctx, t = ctx_from(trend_df(), htf={"1W": analyze_tf(trend_df(400, 1.0), "1W", CFG)})
    assert run("htf_close_above_ema", {"tf": "1W", "period": 20, "above": True}, ctx) is True
    assert run("htf_close_above_ema", {"tf": "1D", "period": 20, "above": True}, ctx) is None   # no 1D HTF -> UNKNOWN
    assert run("sr_role_reversal", {"role": "support", "atr_mult": 50.0}, ctx) in (True, False)


def test_unknown_or_invalid_arguments_are_rejected():
    def errs(args, name="candle_pattern"):
        return validate_strategy_ir({"ir_version": 1, "long": {"type": "group", "logic": "AND", "children": [
            {"type": "rule", "name": "x", "expr": {"t": "pred", "name": name, "args": args}}]}, "short": None})
    assert any("no argument 'colour'" in e for e in errs({"colour": "red"}))
    assert any("not supported" in e for e in errs({"pattern": "Abandoned Baby"}))           # unsupported pattern
    assert any("must be int" in e for e in errs({"bars": "two"}))
    assert any("not supported" in e for e in errs({"op": ">"}, "pct_change"))
    assert any("unknown predicate" in e for e in errs({}, "elliott_wave_3"))
