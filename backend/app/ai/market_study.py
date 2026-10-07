"""Phase AW: the strategist's market study - what a professional reads before writing today's plan.

`study()` turns one symbol's candles (1-minute bars for the last couple of weeks, optional daily
bars, optional cues) into a structured read:

* **Multi-timeframe trend** - for 5m, 15m, 60m and day: close against EMA 20 / 50 / 200, the regime
  (ADX trend strength, EMA direction, ATR volatility), RSI, and a trend label (UP / DOWN / MIXED).
* **Levels** - today's open / high / low and VWAP, the 15-minute opening range, the previous
  session's high / low / close, classic floor pivots (P, R1, R2, S1, S2), the nearest support and
  resistance zones, the 5-minute and daily ATR.
* **Thesis** - a weighted bias (higher timeframes count more) with a confidence, the day's character
  (trend / range / volatile) and three scenarios with their trigger levels (bull above X towards Y,
  bear below Z towards W, range between them) - the levels the strategies are then built around.
* **Context** - India VIX and the global mood from the market memory, when given.

Pure and deterministic. The strategist (`strategist.py`) builds and validates strategies on top.
"""
from __future__ import annotations

from typing import Dict, List, Optional

import pandas as pd

from app.ai.interview import _frame_at, analyse_market, classify_regime, tr
from app.indicators.momentum import rsi as rsi_indicator
from app.indicators.trend import ema
from app.indicators.volatility import atr as atr_indicator

IST = "Asia/Kolkata"
STUDY_TIMEFRAMES = ("5min", "15min", "60min")
TF_WEIGHT = {"5min": 1.0, "15min": 1.5, "60min": 2.0, "day": 2.5}
MIN_STUDY_BARS = 120   # one-minute bars


def _ist(df: pd.DataFrame) -> pd.DatetimeIndex:
    idx = pd.DatetimeIndex(df.index)
    return (idx.tz_localize("UTC") if idx.tz is None else idx).tz_convert(IST)


def _r(v) -> Optional[float]:
    return None if v is None or pd.isna(v) else round(float(v), 2)


def tf_read(df: pd.DataFrame, tf: str, lang: str) -> dict:
    """One timeframe's trend read."""
    close = df["close"].astype(float)
    last = float(close.iloc[-1])
    e20, e50 = ema(close, 20).iloc[-1], ema(close, 50).iloc[-1]
    e200 = ema(close, 200).iloc[-1] if len(df) >= 200 else float("nan")
    regime = classify_regime(df)
    rsi_v = rsi_indicator(close, 14).iloc[-1] if len(df) > 15 else float("nan")
    if not pd.isna(e50) and last > e20 > e50:
        trend = "UP"
    elif not pd.isna(e50) and last < e20 < e50:
        trend = "DOWN"
    else:
        trend = "MIXED"
    words = {"UP": tr(lang, "uptrend", "वरचा trend"), "DOWN": tr(lang, "downtrend", "खालचा trend"), "MIXED": tr(lang, "mixed", "मिश्र")}
    return {"timeframe": tf, "bars": int(len(df)), "close": _r(last), "ema20": _r(e20), "ema50": _r(e50), "ema200": _r(e200),
            "rsi": _r(rsi_v), "adx": regime.adx, "regime": regime.kind, "trend": trend, "trend_text": words[trend],
            "above_ema200": None if pd.isna(e200) else bool(last > e200)}


def levels(df_1m: pd.DataFrame, day: Optional[pd.DataFrame]) -> dict:
    """Today's levels and the previous session's, from one-minute bars (daily bars when given)."""
    idx = _ist(df_1m)
    dates = pd.Series(idx.date, index=df_1m.index)
    today = idx[-1].date()
    session = df_1m[dates == today]
    prior_days = sorted(d for d in set(dates) if d < today)
    prev = df_1m[dates == prior_days[-1]] if prior_days else None
    out: Dict[str, Optional[float]] = {}
    if len(session):
        out.update(day_open=_r(session["open"].iloc[0]), day_high=_r(session["high"].max()), day_low=_r(session["low"].min()))
        # Minutes since the session's first bar, as plain floats: no Timedelta arithmetic on the
        # index, which NumPy 2 / pandas 3 deprecate for generic (unit-less) timedeltas.
        today_idx = idx[dates == today]
        first = today_idx[0]
        minutes_in = (today_idx - first).total_seconds().to_numpy() / 60.0
        orng = session[minutes_in < 15]
        if len(orng) and (idx[-1] - first).total_seconds() >= 14 * 60:
            out.update(or_high=_r(orng["high"].max()), or_low=_r(orng["low"].min()))
        tp = (session["high"] + session["low"] + session["close"]) / 3.0
        vol = session["volume"].astype(float)
        out["vwap"] = _r((tp * vol).sum() / vol.sum()) if vol.sum() > 0 else _r(tp.mean())
    pdh = pdl = pdc = None
    if day is not None and len(day) >= 2:
        dday = _ist(day)
        prev_rows = day[pd.Series(dday.date, index=day.index) < today]
        if len(prev_rows):
            pdh, pdl, pdc = prev_rows["high"].iloc[-1], prev_rows["low"].iloc[-1], prev_rows["close"].iloc[-1]
    if pdh is None and prev is not None and len(prev):
        pdh, pdl, pdc = prev["high"].max(), prev["low"].min(), prev["close"].iloc[-1]
    if pdh is not None:
        p = (pdh + pdl + pdc) / 3.0
        out.update(pdh=_r(pdh), pdl=_r(pdl), pdc=_r(pdc), pivot=_r(p), r1=_r(2 * p - pdl), s1=_r(2 * p - pdh),
                   r2=_r(p + (pdh - pdl)), s2=_r(p - (pdh - pdl)))
    return out


def _ladder(last: float, lv: dict, market: dict) -> List[dict]:
    """Every named level, sorted from the highest down, with its distance from the price."""
    names = {"pdh": "PDH", "pdl": "PDL", "pdc": "PDC", "pivot": "Pivot", "r1": "R1", "r2": "R2", "s1": "S1", "s2": "S2",
             "or_high": "OR high", "or_low": "OR low", "vwap": "VWAP", "day_high": "Day high", "day_low": "Day low", "day_open": "Day open"}
    rows = [{"name": names[k], "key": k, "price": v} for k, v in lv.items() if k in names and v is not None]
    if market.get("support"):
        rows.append({"name": "Support zone", "key": "support", "price": round((market["support"]["low"] + market["support"]["high"]) / 2, 2)})
    if market.get("resistance"):
        rows.append({"name": "Resistance zone", "key": "resistance", "price": round((market["resistance"]["low"] + market["resistance"]["high"]) / 2, 2)})
    for r in rows:
        r["distance_pct"] = round((r["price"] / last - 1) * 100, 2) if last else None
    return sorted(rows, key=lambda r: -r["price"])


def _nearest(ladder: List[dict], last: float, above: bool, skip: tuple = ()) -> Optional[dict]:
    pool = [r for r in ladder if r["key"] not in skip and ((r["price"] > last * 1.0005) if above else (r["price"] < last * 0.9995))]
    if not pool:
        return None
    return min(pool, key=lambda r: abs(r["price"] - last))


def study(df_1m: pd.DataFrame, symbol: str, lang: str = "mr", *, day: Optional[pd.DataFrame] = None,
          memory: Optional[dict] = None) -> dict:
    lang = "mr" if lang == "mr" else "en"
    df_1m = df_1m.sort_index()
    if len(df_1m) < MIN_STUDY_BARS:
        raise ValueError(f"Need at least {MIN_STUDY_BARS} one-minute candles to study {symbol}; got {len(df_1m)}")
    last = float(df_1m["close"].iloc[-1])
    market = analyse_market(df_1m, "1min", lang)
    frames = {tf: _frame_at(df_1m, "1min", tf) for tf in STUDY_TIMEFRAMES}
    reads = [tf_read(frames[tf], tf, lang) for tf in STUDY_TIMEFRAMES if len(frames[tf]) >= 30]
    if day is not None and len(day) >= 30:
        reads.append(tf_read(day, "day", lang))
    lv = levels(df_1m, day)
    atr5 = atr_indicator(frames["5min"], 14).iloc[-1] if len(frames["5min"]) > 15 else float("nan")
    atr_day = atr_indicator(day, 14).iloc[-1] if day is not None and len(day) > 15 else float("nan")

    score, weight, reasons = 0.0, 0.0, []
    for rd in reads:
        w = TF_WEIGHT.get(rd["timeframe"], 1.0)
        weight += w
        if rd["trend"] == "UP":
            score += w
            reasons.append(tr(lang, f"{rd['timeframe']}: uptrend (price above EMA20 above EMA50)", f"{rd['timeframe']}: वरचा trend (भाव EMA20 च्या वर, EMA20 > EMA50)"))
        elif rd["trend"] == "DOWN":
            score -= w
            reasons.append(tr(lang, f"{rd['timeframe']}: downtrend (price below EMA20 below EMA50)", f"{rd['timeframe']}: खालचा trend (भाव EMA20 च्या खाली, EMA20 < EMA50)"))
    vwap = lv.get("vwap")
    if vwap is not None:
        weight += 1.0
        if last > vwap:
            score += 1.0
            reasons.append(tr(lang, "price above today's VWAP - buyers in control", "भाव आजच्या VWAP च्या वर - खरेदीदारांचे वर्चस्व"))
        else:
            score -= 1.0
            reasons.append(tr(lang, "price below today's VWAP - sellers in control", "भाव आजच्या VWAP च्या खाली - विक्रेत्यांचे वर्चस्व"))
    if market["structure"]["trend"] in ("UPTREND", "DOWNTREND"):
        weight += 1.0
        score += 1.0 if market["structure"]["trend"] == "UPTREND" else -1.0
    net = score / weight if weight else 0.0
    bias = "BULLISH" if net >= 0.35 else "BEARISH" if net <= -0.35 else "NEUTRAL"
    confidence = int(round(min(1.0, abs(net)) * 100))

    regime = market["regime"]["kind"]
    vix = None
    if memory:
        vix_row = next((c for c in memory.get("cues", []) if c.get("symbol") == "INDIA VIX"), None)
        vix = float(vix_row["last_price"]) if vix_row and vix_row.get("last_price") else None
    if (vix is not None and vix >= 20) or regime == "VOLATILE":
        character = "VOLATILE"
    elif bias != "NEUTRAL" and (confidence >= 60 or regime in ("TRENDING_UP", "TRENDING_DOWN")):
        character = "TREND"   # timeframes lined up: a slow grind is still a trend even when the 5-minute ADX is low
    elif regime in ("RANGING", "QUIET") or bias == "NEUTRAL":
        character = "RANGE"
    else:
        character = "TREND"

    ladder = _ladder(last, lv, market)
    up = _nearest(ladder, last, True, skip=("day_open",))
    down = _nearest(ladder, last, False, skip=("day_open",))
    up2 = _nearest([r for r in ladder if up and r["price"] > up["price"] * 1.0005], up["price"], True) if up else None
    down2 = _nearest([r for r in ladder if down and r["price"] < down["price"] * 0.9995], down["price"], False) if down else None
    scenarios = []
    if up:
        tgt = up2["price"] if up2 else round(up["price"] + (atr5 * 3 if not pd.isna(atr5) else up["price"] * 0.004), 2)
        scenarios.append({"id": "bull", "trigger": up["price"], "trigger_name": up["name"], "target": tgt,
                          "text": tr(lang, f"Bullish read while it holds above {up['name']} {up['price']:,.2f}; the next reference level above is {tgt:,.2f}.",
                                     f"{up['name']} {up['price']:,.2f} च्या वर टिकला तर तेजीचे वाचन; वरची पुढची संदर्भ पातळी {tgt:,.2f}.")})
    if down:
        tgt = down2["price"] if down2 else round(down["price"] - (atr5 * 3 if not pd.isna(atr5) else down["price"] * 0.004), 2)
        scenarios.append({"id": "bear", "trigger": down["price"], "trigger_name": down["name"], "target": tgt,
                          "text": tr(lang, f"Bearish read below {down['name']} {down['price']:,.2f}; the next reference level below is {tgt:,.2f}.",
                                     f"{down['name']} {down['price']:,.2f} च्या खाली गेला तर मंदीचे वाचन; खालची पुढची संदर्भ पातळी {tgt:,.2f}.")})
    if up and down:
        scenarios.append({"id": "range", "trigger": None, "low": down["price"], "high": up["price"],
                          "text": tr(lang, f"Between {down['price']:,.2f} and {up['price']:,.2f} the data reads as a range.",
                                     f"{down['price']:,.2f} ते {up['price']:,.2f} दरम्यान data range दाखवतो.")})

    # P0.8-D: the headline describes the session; it does not tell the trader what to trade.
    headline = {
        ("TREND", "BULLISH"): ("Trend day, buyers in control on the data: higher highs above VWAP.", "Trend चा दिवस, data नुसार खरेदीदारांचे वर्चस्व: VWAP च्या वर वाढते highs."),
        ("TREND", "BEARISH"): ("Trend day, sellers in control on the data: lower lows below VWAP.", "Trend चा दिवस, data नुसार विक्रेत्यांचे वर्चस्व: VWAP च्या खाली घटते lows."),
        ("VOLATILE", None): ("Volatile session: wide swings, so ATR-based stops are wider in rupees.", "अस्थिर सत्र: मोठे चढ-उतार, त्यामुळे ATR वर आधारित stop रुपयांत मोठे."),
        ("RANGE", None): ("Range-bound session: price is moving between its nearest levels without follow-through.", "Range मधले सत्र: भाव जवळच्या levels दरम्यान फिरतो, पुढे जात नाही."),
    }
    key = (character, bias) if (character, bias) in headline else (character, None)
    if key not in headline:
        key = ("RANGE", None)
    lines = [tr(lang, *headline[key])] + reasons[:5]
    if vix is not None:
        lines.append(tr(lang, f"India VIX {vix:.1f}" + (" - high (above 20)." if vix >= 20 else "."),
                        f"India VIX {vix:.1f}" + (" - जास्त (20 पेक्षा वर)." if vix >= 20 else ".")))
    if memory and memory.get("globals"):
        from app.ai import global_cues
        g = global_cues.view(lang, memory["globals"])
        if g:
            lines.append(g[0])
    return {
        "symbol": symbol.upper(), "last_price": round(last, 2), "as_of": df_1m.index[-1].isoformat(),
        "timeframes": reads, "levels": lv, "ladder": ladder,
        "atr_5m": _r(atr5), "atr_day": _r(atr_day), "atr_5m_pct": _r(atr5 / last * 100) if last and not pd.isna(atr5) else None,
        "regime": regime, "higher_regime": market["higher_regime"]["kind"], "structure": market["structure"],
        "support": market["support"], "resistance": market["resistance"],
        "bias": bias, "bias_score": round(net, 2), "confidence": confidence, "character": character,
        "scenarios": scenarios, "lines": lines, "vix": vix, "today": market["today"],
    }


__all__ = ["study", "levels", "tf_read", "STUDY_TIMEFRAMES"]
