"""Phase AW: the Copilot strategist - studies the live market and builds today's strategy.

The pipeline a professional desk follows, in code:

1. **Study** the symbol (`market_study.study`): multi-timeframe trend, levels, bias, character.
2. **Shortlist setup families that fit the character** - trend pullback, opening-range breakout,
   previous-day-level breakout, VWAP reclaim, Supertrend with a higher-timeframe filter for trend
   days; Bollinger / RSI reversion and VWAP reclaim for range days; breakouts and reversion with
   wider stops for volatile days - each in the direction of the bias when the bias is clear.
3. **Write each as rules** in the platform's declarative strategy language (the same engine the
   Strategy Builder, backtests, the worker and LIVE trading use), with today's levels as operands
   (VWAP, opening range, previous-day high / low) and higher-timeframe filters.
4. **Tune and validate**: every parameter set of every template is simulated on the recent
   candles (vectorised, causal, one position at a time, stop first when a bar touches both, costs,
   intraday square-off at 15:15 IST, at most `MAX_TRADES_PER_DAY`); parameters are chosen on the
   earlier sessions only (in-sample) and judged on the later sessions they never saw
   (out-of-sample, walk-forward). A strategy that only worked in-sample is flagged as over-fitted.
5. **Rank** by out-of-sample expectancy (in R) with in-sample support, and return the best three
   with their rules in plain words, why they fit today, today's trigger levels, the risk per trade
   in points and rupees for the trader's capital, and the evidence.
6. With an external AI provider, the AI may propose up to two more rule sets from the same study;
   they go through the identical simulation and validation - the AI never bypasses the evidence.

Nothing here places an order: the trader adopts a strategy (saved as a custom strategy) and
deploys it in PAPER first.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from itertools import product
from typing import Callable, Dict, List, Optional, Tuple

import pandas as pd

from app.ai.interview import tr
from app.core.models import RiskConfig
from app.core.resampling import resample_ohlc
from app.indicators.volatility import atr as atr_indicator
from app.strategy_engine.declarative import Condition, CustomStrategyConfig, Operand

IST = "Asia/Kolkata"
STYLES = {"intraday": ("5min", "15min"), "scalping": ("1min", "5min")}
COST_PCT = 0.03            # round-trip costs + slippage, % of price
MAX_TRADES_PER_DAY = 3
LAST_ENTRY = (14, 45)      # IST
SQUARE_OFF = (15, 15)
IS_FRACTION = 0.7
MIN_IS_TRADES = 4


def ind(name: str, period: int = 14, *, tf: Optional[str] = None, mult: float = 3.0) -> Operand:
    return Operand(type="indicator", indicator=name, period=period, multiplier=mult, timeframe=tf)


def val(v: float) -> Operand:
    return Operand(type="value", value=v)


def cond(left: Operand, op: str, right: Operand) -> Condition:
    return Condition(left=left, operator=op, right=right)


@dataclass(frozen=True)
class Template:
    id: str
    en: str
    mr: str
    family: str
    characters: Tuple[str, ...]
    grid: Dict[str, Tuple]
    build: Callable[[dict, str, str, str], Tuple[List[Condition], List[Condition]]]   # (params, base, htf, side) -> (long, short)
    why_en: str
    why_mr: str


def _sides(side: str, long_c: List[Condition], short_c: List[Condition]) -> Tuple[List[Condition], List[Condition]]:
    return (long_c if side in ("both", "long") else [], short_c if side in ("both", "short") else [])


def _trend_pullback(p, base, htf, side):
    return _sides(side,
                  [cond(ind("EMA", p["fast"]), "GT", ind("EMA", p["slow"])), cond(ind("CLOSE"), "GT", ind("EMA", 50, tf=htf)),
                   cond(ind("RSI", 14), "CROSSES_ABOVE", val(p["rsi"])), cond(ind("CLOSE"), "GT", ind("VWAP"))],
                  [cond(ind("EMA", p["fast"]), "LT", ind("EMA", p["slow"])), cond(ind("CLOSE"), "LT", ind("EMA", 50, tf=htf)),
                   cond(ind("RSI", 14), "CROSSES_BELOW", val(100 - p["rsi"])), cond(ind("CLOSE"), "LT", ind("VWAP"))])


def _orb(p, base, htf, side):
    return _sides(side,
                  [cond(ind("CLOSE"), "CROSSES_ABOVE", ind("OR_HIGH", p["or_min"])), cond(ind("CLOSE"), "GT", ind("VWAP")),
                   cond(ind("ADX", 14), "GT", val(p["adx"]))],
                  [cond(ind("CLOSE"), "CROSSES_BELOW", ind("OR_LOW", p["or_min"])), cond(ind("CLOSE"), "LT", ind("VWAP")),
                   cond(ind("ADX", 14), "GT", val(p["adx"]))])


def _pd_breakout(p, base, htf, side):
    return _sides(side,
                  [cond(ind("CLOSE"), "CROSSES_ABOVE", ind("PDH")), cond(ind("CLOSE"), "GT", ind("EMA", p["htf_ema"], tf=htf))],
                  [cond(ind("CLOSE"), "CROSSES_BELOW", ind("PDL")), cond(ind("CLOSE"), "LT", ind("EMA", p["htf_ema"], tf=htf))])


def _vwap_reclaim(p, base, htf, side):
    return _sides(side,
                  [cond(ind("CLOSE"), "CROSSES_ABOVE", ind("VWAP")), cond(ind("EMA", 20, tf=htf), "GT", ind("EMA", 50, tf=htf)),
                   cond(ind("RSI", 14), "GT", val(p["rsi"]))],
                  [cond(ind("CLOSE"), "CROSSES_BELOW", ind("VWAP")), cond(ind("EMA", 20, tf=htf), "LT", ind("EMA", 50, tf=htf)),
                   cond(ind("RSI", 14), "LT", val(100 - p["rsi"]))])


def _supertrend(p, base, htf, side):
    return _sides(side,
                  [cond(ind("CLOSE"), "CROSSES_ABOVE", ind("SUPERTREND", 10, mult=p["st_mult"])), cond(ind("CLOSE"), "GT", ind("EMA", 50, tf=htf))],
                  [cond(ind("CLOSE"), "CROSSES_BELOW", ind("SUPERTREND", 10, mult=p["st_mult"])), cond(ind("CLOSE"), "LT", ind("EMA", 50, tf=htf))])


def _reversion(p, base, htf, side):
    return _sides(side,
                  [cond(ind("LOW"), "LT", ind("BB_LOWER", 20, mult=p["bb_k"])), cond(ind("RSI", 14), "LT", val(p["rsi_lo"])),
                   cond(ind("ADX", 14), "LT", val(25))],
                  [cond(ind("HIGH"), "GT", ind("BB_UPPER", 20, mult=p["bb_k"])), cond(ind("RSI", 14), "GT", val(100 - p["rsi_lo"])),
                   cond(ind("ADX", 14), "LT", val(25))])


RR = ((1.5, 2.5), (2.0, 3.0))
TEMPLATES: Tuple[Template, ...] = (
    Template("trend_pullback", "Trend pullback", "Trend pullback", "trend", ("TREND",),
             {"fast": (9, 20), "slow": (21, 50), "rsi": (40, 45), "stop": (1.0, 1.5), "rr": RR}, _trend_pullback,
             "Joins the trend after a pause: fast EMA above slow, price above the higher-timeframe EMA50 and VWAP, RSI turning back up.",
             "थांब्यानंतर trend मध्ये सामील: fast EMA slow च्या वर, भाव मोठ्या timeframe च्या EMA50 आणि VWAP च्या वर, RSI पुन्हा वर वळतो."),
    Template("orb_breakout", "Opening-range breakout", "Opening range breakout", "breakout", ("TREND", "VOLATILE", "RANGE"),
             {"or_min": (15, 30), "adx": (15, 20), "stop": (1.0, 1.5), "rr": RR}, _orb,
             "Trades the first close outside the opening range on the VWAP side, only with ADX showing direction.",
             "Opening range बाहेरचा पहिला close, VWAP च्या बाजूने, आणि ADX दिशा दाखवत असेल तरच."),
    Template("pd_breakout", "Previous-day level breakout", "आधीच्या दिवसाच्या high/low चा breakout", "breakout", ("TREND",),
             {"htf_ema": (20, 50), "stop": (1.0, 1.5), "rr": RR}, _pd_breakout,
             "A close through yesterday's high (or low) with the higher timeframe agreeing - where trapped traders fuel the move.",
             "कालच्या high (किंवा low) च्या पलीकडचा close, मोठा timeframe सहमत असताना - अडकलेले traders move ला इंधन देतात."),
    Template("vwap_reclaim", "VWAP reclaim", "VWAP reclaim", "momentum", ("TREND", "RANGE"),
             {"rsi": (50, 55), "stop": (1.0, 1.5), "rr": RR}, _vwap_reclaim,
             "Price crosses back over VWAP in the direction of the higher-timeframe EMAs, with RSI confirming.",
             "भाव मोठ्या timeframe च्या EMA च्या दिशेने पुन्हा VWAP ओलांडतो, RSI पुष्टी देतो."),
    Template("supertrend_htf", "Supertrend with HTF filter", "Supertrend + मोठ्या timeframe चा filter", "trend", ("TREND",),
             {"st_mult": (2.0, 3.0), "stop": (1.0, 1.5), "rr": RR}, _supertrend,
             "Supertrend flips in the direction of the higher-timeframe EMA50 - rides the trend legs.",
             "मोठ्या timeframe च्या EMA50 च्या दिशेने Supertrend बदलतो - trend च्या लाटेवर स्वार."),
    Template("range_reversion", "Range reversion", "Range reversion (सरासरीकडे परत)", "reversion", ("RANGE", "VOLATILE"),
             {"bb_k": (2.0, 2.5), "rsi_lo": (30, 35), "stop": (1.0, 1.5), "rr": ((1.2, 2.0), (1.5, 2.5))}, _reversion,
             "Fades stretched moves outside the Bollinger band with RSI extreme, only while ADX says there is no trend.",
             "Bollinger band बाहेर ताणलेली move उलटवतो, RSI टोकाला, आणि ADX trend नाही सांगत असतानाच."),
)
BY_ID = {t.id: t for t in TEMPLATES}


def config_for(t: Template, params: dict, base: str, htf: str, side: str, name: Optional[str] = None) -> CustomStrategyConfig:
    long_c, short_c = t.build(params, base, htf, side)
    rr = tuple(params["rr"])
    return CustomStrategyConfig(name=name or f"{t.en} ({side})", timeframe=base, long_conditions=long_c, short_conditions=short_c,
                                stop_loss_atr_mult=float(params["stop"]), atr_period=14, target_rr=rr, min_rr=min(1.2, rr[0]))


# --- the simulator --------------------------------------------------------------------------------------

def _ist_index(df: pd.DataFrame) -> pd.DatetimeIndex:
    idx = pd.DatetimeIndex(df.index)
    return (idx.tz_localize("UTC") if idx.tz is None else idx).tz_convert(IST)


def signals(config: CustomStrategyConfig, df: pd.DataFrame) -> Tuple[pd.Series, pd.Series]:
    def side(conds: List[Condition]) -> pd.Series:
        if not conds:
            return pd.Series(False, index=df.index)
        out = pd.Series(True, index=df.index)
        for c in conds:
            out &= c.holds_series(df, config.timeframe)
        return out
    return side(config.long_conditions), side(config.short_conditions)


def simulate(config: CustomStrategyConfig, df: pd.DataFrame, *, warmup: int = 0) -> List[dict]:
    """Trades the config bar by bar like the engine: entry at the signal bar's close, stop at
    ATR x mult, exit at the stop first (pessimistic), then target 2, then target 1; flat by 15:15
    IST; no entries after 14:45 or beyond MAX_TRADES_PER_DAY. Returns trades with their R."""
    if len(df) < 30:
        return []
    long_s, short_s = signals(config, df)
    atr = atr_indicator(df, config.atr_period)
    ist = _ist_index(df)
    dates = ist.date
    minutes = ist.hour * 60 + ist.minute
    o, h, l, c = (df[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close"))
    ls, ss, a = long_s.to_numpy(), short_s.to_numpy(), atr.to_numpy(dtype=float)
    rr1, rr2 = config.target_rr
    trades, pos, per_day = [], None, {}
    last_entry, square = LAST_ENTRY[0] * 60 + LAST_ENTRY[1], SQUARE_OFF[0] * 60 + SQUARE_OFF[1]
    n = len(df)
    for i in range(max(1, warmup), n):
        if pos is not None:
            d = pos["dir"]
            exit_px = reason = None
            if d == 1:
                if l[i] <= pos["stop"]:
                    exit_px, reason = pos["stop"], "stop"
                elif h[i] >= pos["t2"]:
                    exit_px, reason = pos["t2"], "t2"
                elif h[i] >= pos["t1"]:
                    exit_px, reason = pos["t1"], "t1"
            else:
                if h[i] >= pos["stop"]:
                    exit_px, reason = pos["stop"], "stop"
                elif l[i] <= pos["t2"]:
                    exit_px, reason = pos["t2"], "t2"
                elif l[i] <= pos["t1"]:
                    exit_px, reason = pos["t1"], "t1"
            new_day = i + 1 >= n or dates[i + 1] != dates[i]
            if exit_px is None and (minutes[i] >= square or new_day):
                exit_px, reason = c[i], "square_off"
            if exit_px is not None:
                pts = (exit_px - pos["entry"]) * d - pos["entry"] * COST_PCT / 100.0
                trades.append({"date": str(pos["date"]), "dir": "LONG" if d == 1 else "SHORT", "entry": round(pos["entry"], 2),
                               "exit": round(float(exit_px), 2), "reason": reason, "pts": round(pts, 2), "r": round(pts / pos["risk"], 3)})
                pos = None
                continue
        if pos is None and minutes[i] < last_entry and not math.isnan(a[i]) and a[i] > 0:
            if per_day.get(dates[i], 0) >= MAX_TRADES_PER_DAY:
                continue
            d = 1 if ls[i] else -1 if ss[i] else 0
            if d:
                risk = a[i] * config.stop_loss_atr_mult
                entry = c[i]
                pos = {"dir": d, "entry": entry, "risk": risk, "stop": entry - d * risk, "t1": entry + d * rr1 * risk,
                       "t2": entry + d * rr2 * risk, "date": dates[i]}
                per_day[dates[i]] = per_day.get(dates[i], 0) + 1
    return trades


def metrics(trades: List[dict]) -> dict:
    n = len(trades)
    if not n:
        return {"trades": 0, "win_rate": 0.0, "expectancy_r": 0.0, "profit_factor": None, "total_r": 0.0, "max_dd_r": 0.0, "avg_win_r": None, "avg_loss_r": None}
    rs = [t["r"] for t in trades]
    wins, losses = [r for r in rs if r > 0], [r for r in rs if r <= 0]
    cum = peak = dd = 0.0
    for r in rs:
        cum += r
        peak = max(peak, cum)
        dd = max(dd, peak - cum)
    gl = -sum(losses)
    return {"trades": n, "win_rate": round(100 * len(wins) / n, 1), "expectancy_r": round(sum(rs) / n, 3),
            "profit_factor": round(sum(wins) / gl, 2) if gl > 0 else None, "total_r": round(sum(rs), 2), "max_dd_r": round(dd, 2),
            "avg_win_r": round(sum(wins) / len(wins), 2) if wins else None, "avg_loss_r": round(sum(losses) / len(losses), 2) if losses else None}


def split_sessions(df: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame, List[str]]:
    """In-sample = the earlier IS_FRACTION of the sessions, out-of-sample = the rest (at least one).
    The out-of-sample frame keeps the in-sample bars before it for indicator warm-up; trades are
    counted from its first session on."""
    dates = pd.Series(_ist_index(df).date, index=df.index)
    sessions = sorted(set(dates))
    if len(sessions) < 3:
        return df, df.iloc[0:0], [str(s) for s in sessions]
    cut = max(1, min(len(sessions) - 1, int(round(len(sessions) * IS_FRACTION))))
    oos_start = sessions[cut]
    return df[dates < oos_start], df, [str(s) for s in sessions[cut:]]


def _score(m: dict) -> float:
    if m["trades"] < MIN_IS_TRADES:
        return -9.0
    return m["expectancy_r"] * min(1.0, m["trades"] / 10.0) + (0.05 if (m["profit_factor"] or 0) > 1.2 else 0.0)


def evaluate_template(t: Template, df: pd.DataFrame, base: str, htf: str, side: str) -> Optional[dict]:
    is_df, full_df, oos_sessions = split_sessions(df)
    keys = list(t.grid)
    best = None
    for combo in product(*(t.grid[k] for k in keys)):
        params = dict(zip(keys, combo))
        if "fast" in params and params["fast"] >= params["slow"]:
            continue
        cfg = config_for(t, params, base, htf, side)
        m_is = metrics(simulate(cfg, is_df, warmup=30))
        s = _score(m_is)
        if best is None or s > best[0]:
            best = (s, params, cfg, m_is)
    if best is None:
        return None
    _, params, cfg, m_is = best
    oos_trades = [tr_ for tr_ in simulate(cfg, full_df, warmup=30) if tr_["date"] in set(oos_sessions)] if oos_sessions else []
    m_oos = metrics(oos_trades)
    all_trades = simulate(cfg, full_df, warmup=30)
    return {"template": t, "params": params, "config": cfg, "in_sample": m_is, "out_of_sample": m_oos, "all": metrics(all_trades),
            "trades": all_trades[-60:], "oos_sessions": oos_sessions}


def _verdict(lang: str, m_is: dict, m_oos: dict) -> Tuple[str, str]:
    if m_is["trades"] + m_oos["trades"] < MIN_IS_TRADES:
        return "thin", tr(lang, "Too few trades on these sessions to judge - treat it as an idea to paper-trade, not as evidence.",
                          "या सत्रांत निर्णयासाठी trades खूप कमी - याला पुरावा नव्हे तर PAPER वर तपासायची कल्पना माना.")
    if m_oos["trades"] == 0:
        return "untested", tr(lang, "No trades in the later sessions - not yet validated on unseen data.", "नंतरच्या सत्रांत trade नाही - नवीन data वर अजून तपासलेली नाही.")
    if m_is["expectancy_r"] > 0 and m_oos["expectancy_r"] > 0:
        return "robust", tr(lang, "Positive on the sessions used to tune it and on the later sessions it never saw.",
                            "Tune केलेल्या सत्रांवर आणि कधीही न पाहिलेल्या नंतरच्या सत्रांवरही फायद्याची.")
    if m_is["expectancy_r"] > 0 >= m_oos["expectancy_r"]:
        return "overfit", tr(lang, "Worked only on the tuning sessions, lost on the later ones - likely over-fitted; paper only.",
                             "फक्त tune केलेल्या सत्रांवर चालली, नंतरच्या सत्रांत तोटा - over-fit असण्याची शक्यता; फक्त PAPER.")
    return "weak", tr(lang, "No edge on these candles.", "या candles वर फायदा दिसत नाही.")


def _rule_text(lang: str, c: Condition) -> str:
    return c.label()


def plan_for(lang: str, r: dict, study: dict, risk: RiskConfig, symbol: str) -> dict:
    t: Template = r["template"]
    cfg: CustomStrategyConfig = r["config"]
    atr_pts = study.get("atr_5m") if cfg.timeframe == "5min" else None
    if atr_pts is None:
        atr_pts = study.get("atr_5m")
    stop_pts = round((atr_pts or 0) * cfg.stop_loss_atr_mult, 2) if atr_pts else None
    risk_amount = round(risk.capital * risk.risk_per_trade_pct / 100.0, 2)
    qty = int(risk_amount // stop_pts) if stop_pts else None
    lv = study["levels"]
    triggers = []
    for key, label_en, label_mr in (("or_high", "Opening-range high", "Opening range high"), ("or_low", "Opening-range low", "Opening range low"),
                                    ("pdh", "Previous-day high", "कालचा high"), ("pdl", "Previous-day low", "कालचा low"), ("vwap", "VWAP", "VWAP")):
        if lv.get(key) is not None and any(key.upper().replace("_", "") in c.label().replace("_", "") for c in cfg.long_conditions + cfg.short_conditions):
            triggers.append({"name": tr(lang, label_en, label_mr), "price": lv[key]})
    verdict, verdict_text = _verdict(lang, r["in_sample"], r["out_of_sample"])
    side = "LONG" if cfg.long_conditions and not cfg.short_conditions else "SHORT" if cfg.short_conditions and not cfg.long_conditions else "BOTH"
    return {
        "id": t.id, "name": tr(lang, t.en, t.mr), "family": t.family, "direction": side, "timeframe": cfg.timeframe,
        "why": tr(lang, t.why_en, t.why_mr), "params": r["params"],
        "rules": {"long": [c.label() for c in cfg.long_conditions], "short": [c.label() for c in cfg.short_conditions]},
        "exits": tr(lang, f"Stop {cfg.stop_loss_atr_mult:g} x ATR(14); targets {cfg.target_rr[0]:g}R / {cfg.target_rr[1]:g}R; flat by 15:15.",
                    f"Stop {cfg.stop_loss_atr_mult:g} x ATR(14); target {cfg.target_rr[0]:g}R / {cfg.target_rr[1]:g}R; 15:15 पर्यंत सगळे बंद."),
        "triggers": triggers, "stop_points": stop_pts, "risk_amount": risk_amount, "quantity_hint": qty,
        "in_sample": r["in_sample"], "out_of_sample": r["out_of_sample"], "all": r["all"], "oos_sessions": r["oos_sessions"],
        "verdict": verdict, "verdict_text": verdict_text, "trades": r["trades"],
        "config": json.loads(cfg.model_dump_json()),
        "deployment": {"strategy_id": None, "symbol": symbol.upper(), "exchange": "NSE", "timeframe": cfg.timeframe, "mode": "PAPER",
                       "holding": "INTRADAY", "exit_rules": {"time_exit_at": "15:10", "break_even_at_r": 1.0}},
        "source": r.get("source", "template"),
    }


def _side_for(study: dict, direction: str) -> List[str]:
    if direction in ("long", "short", "both"):
        return [direction]
    if study["bias"] == "BULLISH" and study["confidence"] >= 50:
        return ["long"]
    if study["bias"] == "BEARISH" and study["confidence"] >= 50:
        return ["short"]
    return ["both"]


def build(df_1m: pd.DataFrame, study: dict, lang: str = "mr", *, style: str = "intraday", direction: str = "auto",
          risk: Optional[RiskConfig] = None, extra_configs: Optional[List[Tuple[str, CustomStrategyConfig]]] = None) -> dict:
    """Shortlist, tune, validate and rank. `extra_configs` (name, config) - e.g. the AI's proposals -
    are validated the same way (no tuning: they are taken as written)."""
    lang = "mr" if lang == "mr" else "en"
    risk = risk or RiskConfig()
    base, htf = STYLES.get(style, STYLES["intraday"])
    df = df_1m if base == "1min" else resample_ohlc(df_1m, base)
    sides = _side_for(study, direction)
    chosen = [t for t in TEMPLATES if study["character"] in t.characters] or list(TEMPLATES)
    results = []
    for t in chosen:
        for side in sides:
            r = evaluate_template(t, df, base, htf, side)
            if r is not None:
                results.append(r)
    for name, cfg in extra_configs or []:
        cfg = cfg.model_copy(update={"timeframe": base})
        is_df, full_df, oos = split_sessions(df)
        everything = simulate(cfg, full_df, warmup=30)
        ai_t = Template("ai_" + re.sub(r"\W+", "_", name.lower())[:30], name, name, "ai", (), {}, lambda *a: ([], []),
                        "Proposed by your AI provider from today's study, then validated like every other candidate.",
                        "आजच्या अभ्यासावरून तुमच्या AI ने सुचवलेली, आणि इतरांसारखीच तपासलेली.")
        results.append({"template": ai_t, "params": {}, "config": cfg, "in_sample": metrics(simulate(cfg, is_df, warmup=30)),
                        "out_of_sample": metrics([x for x in everything if x["date"] in set(oos)]), "all": metrics(everything),
                        "trades": everything[-60:], "oos_sessions": oos, "source": "ai"})

    def rank(r):
        m_is, m_oos = r["in_sample"], r["out_of_sample"]
        if m_oos["trades"] == 0:
            return m_is["expectancy_r"] * 0.3 - 0.2
        return 0.6 * m_oos["expectancy_r"] + 0.4 * m_is["expectancy_r"] + (0.1 if m_is["expectancy_r"] > 0 and m_oos["expectancy_r"] > 0 else 0.0)
    results.sort(key=rank, reverse=True)
    traded = [r for r in results if r["all"]["trades"] > 0] or results
    plans = [plan_for(lang, r, study, risk, study["symbol"]) for r in traded[:3]]
    tested = len(results)
    notes = [tr(lang, f"{tested} candidate set(s) tuned on {len(set(pd.Series(_ist_index(df).date)))} sessions; "
                      f"parameters chosen on the first {int(IS_FRACTION * 100)}% and judged on the rest.",
                f"{tested} उमेदवार strategies {len(set(pd.Series(_ist_index(df).date)))} सत्रांवर tune केल्या; parameters पहिल्या "
                f"{int(IS_FRACTION * 100)}% सत्रांवर निवडले आणि उरलेल्या सत्रांवर तपासले.")]
    if plans and plans[0]["verdict"] != "robust":
        notes.append(tr(lang, "No candidate held up on the unseen sessions - today, the professional choice may be to wait or paper-trade only.",
                        "कोणतीच strategy नवीन सत्रांवर टिकली नाही - आज थांबणे किंवा फक्त PAPER हाच professional निर्णय असू शकतो."))
    return {"style": style, "base_timeframe": base, "higher_timeframe": htf, "sides": sides, "candidates": plans,
            "best": plans[0]["id"] if plans else None, "notes": notes, "tested": tested}


AI_PROMPT = """You are a professional Indian intraday strategist. From the MARKET STUDY below, write up to two rule sets that fit
today's market for {symbol} on {base} candles (higher-timeframe filter {htf}). Output JSON only:
{{"strategies": [{{"name": str, "config": CustomStrategyConfig}}]}}
CustomStrategyConfig = {{"name": str, "timeframe": "{base}", "long_conditions": [Condition], "short_conditions": [Condition],
 "stop_loss_atr_mult": number 0.5-3, "atr_period": 14, "target_rr": [number, number], "min_rr": number}}
Condition = {{"left": Operand, "operator": "GT"|"LT"|"GTE"|"LTE"|"CROSSES_ABOVE"|"CROSSES_BELOW", "right": Operand}}
Operand = {{"type": "value", "value": number}} or {{"type": "indicator", "indicator": one of EMA, SMA, RSI, ADX, PLUS_DI, MINUS_DI,
 ATR, SUPERTREND, CLOSE, OPEN, HIGH, LOW, VWAP, DAY_OPEN, OR_HIGH, OR_LOW, PDH, PDL, PDC, BB_UPPER, BB_MID, BB_LOWER, VOLUME,
 VOLUME_SMA, "period": int, "multiplier": number, "timeframe": null or "{htf}"}}
Rules: at most 4 conditions per side; trade with the study's bias when it is clear; use the levels (VWAP, OR_HIGH/OR_LOW = opening
range with period in minutes, PDH/PDL) the study names; no promises of profit. Every rule set will be backtested on recent sessions and
judged on sessions it never saw before anyone sees it.
=== MARKET STUDY ===
{study}"""


def study_text(study: dict) -> str:
    keep = {k: study[k] for k in ("symbol", "last_price", "bias", "confidence", "character", "regime", "higher_regime", "levels", "atr_5m", "vix")}
    keep["timeframes"] = [{k: r[k] for k in ("timeframe", "trend", "regime", "rsi", "adx")} for r in study["timeframes"]]
    keep["scenarios"] = [s["text"] for s in study["scenarios"]]
    return json.dumps(keep, default=str)


def parse_ai(text: str) -> List[Tuple[str, CustomStrategyConfig]]:
    """The AI's rule sets that validate against the strategy schema; anything else is dropped."""
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
    except ValueError:
        return []
    out = []
    for item in (data.get("strategies") or [])[:2]:
        try:
            cfg = CustomStrategyConfig.model_validate(item.get("config") or {})
        except Exception:  # noqa: BLE001 - an invalid proposal is simply not a candidate
            continue
        if sum(len(x) for x in (cfg.long_conditions, cfg.short_conditions)) > 8:
            continue
        out.append(((item.get("name") or cfg.name or "AI strategy")[:60], cfg))
    return out


async def ai_proposals(provider, study: dict, style: str = "intraday") -> List[Tuple[str, CustomStrategyConfig]]:
    base, htf = STYLES.get(style, STYLES["intraday"])
    try:
        text = await provider.complete(AI_PROMPT.format(symbol=study["symbol"], base=base, htf=htf, study=study_text(study)),
                                       "Write the rule sets now.", max_tokens=2000)
    except Exception:  # noqa: BLE001 - the templates still give a full answer
        return []
    return parse_ai(text)


__all__ = ["TEMPLATES", "build", "simulate", "metrics", "config_for", "split_sessions", "parse_ai", "ai_proposals", "STYLES"]
