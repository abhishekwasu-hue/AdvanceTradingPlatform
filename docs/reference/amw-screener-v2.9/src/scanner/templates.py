"""Scanner template library. Every template is a validated scanner spec (shared IR + envelope) with category, rules,
parameters, timeframes, data requirements, version and known limitations. Templates are starting points: NONE of
them has validated predictive value - run a backtest / forward test before relying on one."""
from __future__ import annotations

from .spec import build


def _f(n): return {"t": "field", "name": n}
def _num(v): return {"t": "num", "v": float(v)}
def _cmp(a, op, b): return {"t": "cmp", "op": op, "a": a, "b": b}
def _pred(name, **a): return {"t": "pred", "name": name, "args": a}
def _r(name, e): return {"type": "rule", "name": name, "expr": e}


def _ir(long=None, short=None, stop=None, target=2.0):
    g = lambda rules: {"type": "group", "logic": "AND", "children": rules} if rules else None  # noqa: E731
    return {"ir_version": 1, "classification": "template", "long": g(long), "short": g(short),
            "stop": stop or {"method": "swing", "buffer_atr": 0.2}, "target": {"r": target}}


LIQ = _r("liquidity: 20-bar avg traded value >= 10 crore", _pred("min_avg_traded_value", value=1e8))
DQ = _r("data quality OK", _pred("data_quality_ok"))
COMMON_LIMITS = ["no validated predictive value", "Yahoo data is delayed/unadjusted; corporate actions are flagged, not adjusted"]

TEMPLATES: list[dict] = [
    # ---------------- Price action
    {"id": "pa_hh_hl_trend", "category": "Price Action", "name": "HH/HL uptrend",
     "ir": _ir([_r("last swing high is HH", _pred("last_swing_label", kind="H", label="HH")),
                _r("last swing low is HL", _pred("last_swing_label", kind="L", label="HL")), LIQ, DQ]),
     "limits": ["swings confirm 3 bars late (non-repainting), so signals lag the turn"]},
    {"id": "pa_lh_ll_downtrend", "category": "Price Action", "name": "LH/LL downtrend",
     "ir": _ir(short=[_r("last swing high is LH", _pred("last_swing_label", kind="H", label="LH")),
                      _r("last swing low is LL", _pred("last_swing_label", kind="L", label="LL")), LIQ, DQ]),
     "limits": ["swings confirm 3 bars late"]},
    {"id": "pa_bos_bull", "category": "Price Action", "name": "Bullish BOS (close-confirmed)",
     "ir": _ir([_r("bullish BOS within 3 bars", _pred("bos_recent", direction="bullish", bars=3)), LIQ, DQ]),
     "limits": ["a wick through the level is a sweep, not a BOS"]},
    {"id": "pa_choch_bull", "category": "Price Action", "name": "Bullish CHoCH",
     "ir": _ir([_r("bullish CHoCH within 3 bars", _pred("choch_recent", direction="bullish", bars=3)), LIQ, DQ]),
     "limits": ["early reversal evidence; many CHoCHs fail"]},
    {"id": "pa_reversal_candle", "category": "Price Action", "name": "Reversal candle at support",
     "ir": _ir([_r("near support", _pred("near_support", atr_mult=1.0)),
                _r("bullish pin bar", _pred("candle_pattern", pattern="Pin Bar", bias="bullish", bars=1)), LIQ, DQ]),
     "limits": ["pattern definitions in docs/STRATEGY_RULES.md; confirmation needs the next bar"]},
    {"id": "pa_support_rejection", "category": "Price Action", "name": "Support rejection",
     "ir": _ir([_r("near support", _pred("near_support", atr_mult=0.5)),
                _r("bullish rejection candle", _pred("candle_pattern", pattern="Rejection", bias="bullish", bars=1)), LIQ, DQ]),
     "limits": []},
    {"id": "pa_resistance_rejection", "category": "Price Action", "name": "Resistance rejection",
     "ir": _ir(short=[_r("near resistance", _pred("near_resistance", atr_mult=0.5)),
                      _r("bearish rejection candle", _pred("candle_pattern", pattern="Rejection", bias="bearish", bars=1)), LIQ, DQ]),
     "limits": []},
    # ---------------- Trend
    {"id": "tr_ema_alignment", "category": "Trend", "name": "EMA alignment (20 > 50 > 200)",
     "ir": _ir([_r("close > EMA20", _cmp(_f("close"), ">", _f("ema20"))), _r("EMA20 > EMA50", _cmp(_f("ema20"), ">", _f("ema50"))),
                _r("EMA50 > EMA200", _cmp(_f("ema50"), ">", _f("ema200"))), LIQ, DQ]),
     "limits": ["EMA200 needs >= 200 bars; missing -> UNKNOWN (never qualifies)"]},
    {"id": "tr_mtf_trend", "category": "Trend", "name": "Multi-timeframe trend (daily + weekly)",
     "envelope": {"timeframe": "1D", "htf": ["1W"]},
     "ir": _ir([_r("daily trend bullish", _pred("trend_is", label="Bullish")),
                _r("weekly trend bullish", _pred("htf_trend_is", tf="1W", label="Bullish")), LIQ, DQ]),
     "limits": ["weekly uses COMPLETED weeks only"]},
    {"id": "tr_supertrend_rsi", "category": "Trend", "name": "Supertrend + RSI",
     "ir": _ir([_r("Supertrend(10,3) bullish", _pred("supertrend_dir", direction="bullish")),
                _r("RSI(14) > 55", _cmp(_f("rsi14"), ">", _num(55))), LIQ, DQ], stop={"method": "atr", "mult": 2.0}),
     "limits": ["Supertrend whipsaws in ranges"]},
    {"id": "tr_continuation", "category": "Trend", "name": "Trend continuation pullback to EMA20",
     "ir": _ir([_r("trend bullish", _pred("trend_is", label="Bullish")),
                _r("pullback to EMA20 held", _pred("pullback_to_ema", period=20, atr_mult=0.5)), LIQ, DQ]),
     "limits": []},
    # ---------------- Volume
    {"id": "vol_rvol_expansion", "category": "Volume", "name": "Relative volume expansion",
     "ir": _ir([_r("RVOL > 2", _cmp(_f("rvol"), ">", _num(2))), _r("close > EMA20", _cmp(_f("close"), ">", _f("ema20"))), LIQ, DQ]),
     "limits": ["RVOL uses the PRIOR 20 bars; missing volume -> UNKNOWN"]},
    {"id": "vol_price_volume", "category": "Volume", "name": "Price-volume confirmation",
     "ir": _ir([_r("up >= 2% on the bar", _pred("pct_change", bars=1, op=">=", value=2.0)),
                _r("RVOL > 1.5", _cmp(_f("rvol"), ">", _num(1.5))), LIQ, DQ]),
     "limits": []},
    {"id": "vol_anomaly", "category": "Volume", "name": "Volume anomaly (RVOL > 3)",
     "ir": _ir([_r("RVOL > 3", _cmp(_f("rvol"), ">", _num(3))), LIQ, DQ]),
     "limits": ["direction-neutral research list (long side used only as a container)"]},
    # ---------------- Reversal
    {"id": "rev_rsi_divergence", "category": "Reversal", "name": "Bullish RSI divergence",
     "ir": _ir([_r("bullish RSI divergence", _pred("rsi_divergence", direction="bullish", max_age=15)), LIQ, DQ]),
     "limits": ["divergence = last two confirmed swing lows; can persist for long periods in downtrends"]},
    {"id": "rev_failed_breakdown", "category": "Reversal", "name": "Failed breakdown (bullish CHoCH after LL)",
     "ir": _ir([_r("last swing low is LL", _pred("last_swing_label", kind="L", label="LL")),
                _r("bullish CHoCH within 5 bars", _pred("choch_recent", direction="bullish", bars=5)), LIQ, DQ]),
     "limits": []},
    {"id": "rev_gap_reversal", "category": "Reversal", "name": "Gap-down reversal",
     "ir": _ir([_r("bullish gap reversal", _pred("candle_pattern", pattern="Gap Reversal", bias="bullish", bars=1)), LIQ, DQ]),
     "limits": ["needs a real opening gap; adjusted/unadjusted data affects gap detection"]},
    # ---------------- Research
    {"id": "res_volatility", "category": "Research", "name": "Volatility candidates (ATR% > 3)",
     "ir": _ir([_r("ATR(14) > 3% of close", _cmp({"t": "bin", "op": "/", "a": _f("atr14"), "b": _f("close")}, ">", _num(0.03))), LIQ, DQ]),
     "limits": ["research list, not a trade setup"]},
    {"id": "res_liquidity", "category": "Research", "name": "Liquidity filter (avg traded value >= 50 crore)",
     "ir": _ir([_r("avg traded value >= 50 crore", _pred("min_avg_traded_value", value=5e8)), DQ]),
     "limits": ["needs complete volume for 20 bars"]},
]
CATEGORIES = ["Price Action", "Trend", "Volume", "Reversal", "Research"]
VERSION = 1


def listing() -> list[dict]:
    out = []
    for t in TEMPLATES:
        b = build(t["name"], t["ir"], t.get("envelope"))
        env = (b["spec"] or {}).get("scanner", {})
        out.append({"id": t["id"], "category": t["category"], "name": t["name"], "version": VERSION,
                    "timeframes": [env.get("timeframe", "1D")] + list(env.get("htf", [])),
                    "data_requirements": "validated OHLCV (completed bars); volume for liquidity/RVOL rules",
                    "limitations": t["limits"] + COMMON_LIMITS, "valid": not b["errors"], "errors": b["errors"],
                    "evidence": "NOT VALIDATED - run a backtest and forward test"})
    return out


def get(tid: str) -> dict:
    t = next((x for x in TEMPLATES if x["id"] == tid), None)
    if t is None:
        raise KeyError(f"unknown template {tid}")
    return {"name": t["name"], "ir": t["ir"], "envelope": t.get("envelope") or {}}
