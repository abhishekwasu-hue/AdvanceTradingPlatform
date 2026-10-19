"""Deterministic natural-language scanner builder (works without any AI key).

Pipeline: split the request into clauses -> match each clause against the SUPPORTED condition registry -> build the
shared IR + scanner envelope -> validate -> return a readable preview with every interpretation and assumption.
A clause that matches nothing is reported as UNSUPPORTED; it is never dropped silently or replaced by something
"similar". An LLM may propose text, but only this parser + the validator produce the executable specification, and
the deterministic engine decides which symbols qualify.
"""
from __future__ import annotations

import re

from ..lab.ir import render_strategy
from .spec import TIMEFRAMES, build

TF_WORDS = [(r"\b(weekly|week)\b", "1W"), (r"\b(daily|day|1d)\b", "1D"), (r"\b(hourly|1\s*h(our)?|60\s*-?\s*min(ute)?s?)\b", "1H"),
            (r"\b30\s*-?\s*min(ute)?s?\b", "30m"), (r"\b15\s*-?\s*min(ute)?s?\b", "15m"), (r"\b5\s*-?\s*min(ute)?s?\b", "5m")]
UNIVERSES = [(r"nifty\s*500", "Nifty 500"), (r"nifty\s*200", "Nifty 200"), (r"nifty\s*50\b", "Nifty 50"),
             (r"all\s+nse|nse\s+(stocks|equities)|indian\s+stocks", "NSE equities (list)")]
CANDLES = {"bullish engulfing": ("Bullish Engulfing", "bullish"), "bearish engulfing": ("Bearish Engulfing", "bearish"),
           "hammer": ("Hammer", "bullish"), "shooting star": ("Shooting Star", "bearish"),
           "morning star": ("Morning Star", "bullish"), "evening star": ("Evening Star", "bearish"),
           "pin bar": ("Pin Bar", None), "rejection candle": ("Rejection", None), "strong body": ("Strong Body", None),
           "strong candle": ("Strong Body", None), "gap reversal": ("Gap Reversal", None), "inside bar": ("Inside Bar", "neutral"),
           "doji": ("Doji", "neutral")}
EMA_FIELDS = {20: "ema20", 50: "ema50", 200: "ema200"}


def _f(n):
    return {"t": "field", "name": n}


def _num(v):
    return {"t": "num", "v": float(v)}


def _pred(name, **a):
    return {"t": "pred", "name": name, "args": a}


def _ema(n):
    return _f(EMA_FIELDS[n]) if n in EMA_FIELDS else {"t": "ind", "fn": "ema", "src": _f("close"), "n": int(n)}


def _cmp_word(w):
    return ">" if w in ("above", "over", "greater than", ">", "more than") else "<"


def _clauses(text: str) -> list[str]:
    t = re.sub(r"^\s*(find|show|scan|screen|list|give me)\b.*?\b(with|where|having|that have)\b", "", text, flags=re.I)
    parts = re.split(r",|;|\band\b|\bwith\b|\bwhere\b", t, flags=re.I)
    return [p.strip(" .") for p in parts if p and p.strip(" .")]


def parse(text: str) -> dict:
    """Return {"ir", "envelope", "interpretations", "unsupported", "errors", "readable", "valid"}."""
    low = text.lower()
    tfs = [tf for pat, tf in TF_WORDS if re.search(pat, low)]
    primary = max(tfs, key=TIMEFRAMES.index) if tfs else "1D"           # finest timeframe mentioned
    side = "short" if re.search(r"\b(bearish|short|downtrend|sell)\b", low) and not re.search(r"\b(bullish|long|uptrend|buy)\b", low) else "long"
    universe = next((u for pat, u in UNIVERSES if re.search(pat, low)), "Nifty 50")
    rules, notes, unsupported, htf = [], [], [], set()
    notes.append(f"timeframe: {primary} (finest timeframe mentioned{'' if tfs else '; none mentioned -> daily'})")
    notes.append(f"universe: {universe}" + ("" if any(re.search(p, low) for p, _ in UNIVERSES) else " (default; none mentioned)"))
    notes.append(f"direction: {side} side")

    def add(name, expr, note):
        rules.append({"type": "rule", "name": name, "expr": expr})
        notes.append(f"'{name}' -> {note}")

    for cl in _clauses(text):
        c = cl.lower()
        ctf = next((tf for pat, tf in TF_WORDS if re.search(pat, c)), None)
        bull = not re.search(r"\b(bearish|down|lower high|lower low|below|sell)\b", c) or re.search(r"\bbullish\b", c)
        matched = False
        m = re.search(r"(price|close)?\s*(above|below|over|under)\s*(the\s*)?ema\s*\(?(\d{1,3})\)?", c) or \
            re.search(r"ema\s*\(?(\d{1,3})\)?\s*(above|below)", c)
        if m and "cross" not in c:
            n = int(m.group(4)) if m.lastindex and m.lastindex >= 4 else int(m.group(1))
            op = _cmp_word(m.group(2))
            if ctf and ctf != primary and TIMEFRAMES.index(ctf) < TIMEFRAMES.index(primary) and n in (20, 50):
                htf.add(ctf)
                add(cl, _pred("htf_close_above_ema", tf=ctf, period=n, above=op == ">"), f"{ctf} close {op} EMA{n} (higher timeframe)")
            elif ctf and ctf != primary:
                unsupported.append(f"{cl} (EMA on {ctf} is only supported for EMA 20/50 on a HIGHER timeframe than {primary})")
                continue
            else:
                add(cl, {"t": "cmp", "op": op, "a": _f("close"), "b": _ema(n)}, f"close {op} EMA({n}) on {primary}")
            matched = True
        m = re.search(r"ema\s*\(?(\d{1,3})\)?\s*cross(es|ed)?\s*(above|over|below|under)\s*ema\s*\(?(\d{1,3})\)?", c)
        if not matched and (m or "golden cross" in c or "death cross" in c):
            a, d, b = (int(m.group(1)), m.group(3), int(m.group(4))) if m else ((50, "above", 200) if "golden" in c else (50, "below", 200))
            add(cl, {"t": "cross", "dir": "over" if d in ("above", "over") else "under", "a": _ema(a), "b": _ema(b)},
                f"EMA({a}) crosses {'above' if d in ('above', 'over') else 'below'} EMA({b}) on the last completed bar")
            matched = True
        m = re.search(r"rsi\s*(\(?14\)?)?\s*(above|below|over|under|>|<)\s*(\d+(\.\d+)?)", c)
        if not matched and m:
            op = _cmp_word(m.group(2))
            add(cl, {"t": "cmp", "op": op, "a": _f("rsi14"), "b": _num(m.group(3))}, f"RSI(14) {op} {m.group(3)}")
            matched = True
        m = re.search(r"(relative volume|rvol)\s*(above|over|>|greater than|at least)\s*(\d+(\.\d+)?)", c)
        if not matched and m:
            add(cl, {"t": "cmp", "op": ">=" if "least" in m.group(2) else ">", "a": _f("rvol"), "b": _num(m.group(3))},
                f"relative volume (volume / mean of the PRIOR 20 bars) > {m.group(3)}")
            matched = True
        if not matched and "divergence" in c:
            if "rsi" not in c:
                notes.append(f"'{cl}': oscillator not named -> RSI assumed")
            add(cl, _pred("rsi_divergence", direction="bullish" if bull else "bearish", max_age=15),
                "regular RSI(14) divergence between the last two CONFIRMED swing pivots (k=3 bars each side), latest pivot "
                "at most 15 bars old; confirmation = the pivot itself is confirmed (assumption)")
            matched = True
        if not matched and re.search(r"\btrend\b|\buptrend\b|\bdowntrend\b", c) and "supertrend" not in c:
            label = "Bullish" if bull else "Bearish"
            if ctf and ctf != primary and TIMEFRAMES.index(ctf) < TIMEFRAMES.index(primary):
                htf.add(ctf)
                add(cl, _pred("htf_trend_is", tf=ctf, label=label), f"{ctf} trend label = {label} (EMA alignment + structure)")
            elif ctf and ctf != primary:
                unsupported.append(f"{cl} (trend on a LOWER timeframe than {primary} is not supported)")
                continue
            else:
                add(cl, _pred("trend_is", label=label), f"{primary} trend label = {label} (EMA alignment + structure)")
            matched = True
        if not matched and "supertrend" in c:
            add(cl, _pred("supertrend_dir", direction="bullish" if bull else "bearish"), "Supertrend(10, 3) direction")
            matched = True
        if not matched and re.search(r"higher low|\bhl\b|lower high|\blh\b|higher high|\bhh\b|lower low|\bll\b", c):
            lab = "HL" if re.search(r"higher low|\bhl\b", c) else "LH" if re.search(r"lower high|\blh\b", c) else \
                "HH" if re.search(r"higher high|\bhh\b", c) else "LL"
            if ctf and ctf != primary:
                unsupported.append(f"{cl} (swing labels are evaluated on the scanner timeframe {primary} only)")
                continue
            add(cl, _pred("last_swing_label", kind="L" if lab in ("HL", "LL") else "H", label=lab),
                f"last CONFIRMED swing {'low' if lab in ('HL', 'LL') else 'high'} is a {lab}")
            matched = True
        if not matched and re.search(r"\bbos\b|break of structure", c):
            add(cl, _pred("bos_recent", direction="bullish" if bull else "bearish", bars=5), "close-confirmed BOS within 5 bars")
            matched = True
        if not matched and re.search(r"choch|change of character|\bmss\b", c):
            add(cl, _pred("choch_recent", direction="bullish" if bull else "bearish", bars=5), "CHoCH within 5 bars")
            matched = True
        if not matched:
            for k, (pname, bias) in CANDLES.items():
                if k in c:
                    b = bias or ("bullish" if bull else "bearish")
                    add(cl, _pred("candle_pattern", pattern=pname, bias=b, bars=1, confirmed="confirm" in c),
                        f"{pname} ({b}) on the last completed bar{' confirmed by the next bar' if 'confirm' in c else ''}")
                    matched = True
                    break
        if not matched and re.search(r"near (support|resistance)", c):
            role = "support" if "support" in c else "resistance"
            add(cl, _pred("near_support" if role == "support" else "near_resistance", atr_mult=1.0), f"close within 1 ATR of a {role} level")
            matched = True
        if not matched and re.search(r"(demand|supply) zone", c):
            add(cl, _pred("in_demand_zone" if "demand" in c else "in_supply_zone"), "last bar inside a non-invalidated zone")
            matched = True
        if not matched and re.search(r"gap\s*(up|down)", c):
            d = "up" if "up" in c else "down"
            add(cl, _pred("gap_recent", direction=d, bars=1, min_atr=0.5, unfilled="unfilled" in c), f"gap {d} >= 0.5 ATR on the last bar")
            matched = True
        m = re.search(r"(up|down|gain(ed)?|fell|change)\s*(of\s*)?(more than|at least|above|over)?\s*(\d+(\.\d+)?)\s*%", c)
        if not matched and m:
            v = float(m.group(5))
            down = m.group(1) in ("down", "fell")
            add(cl, _pred("pct_change", bars=1, op="<=" if down else ">=", value=-v if down else v),
                f"close-to-close change over 1 bar {'<=' if down else '>='} {'-' if down else ''}{v}%")
            matched = True
        if not matched and re.search(r"(liquid|turnover|traded value)", c):
            add(cl, _pred("min_avg_traded_value", value=1e8), "20-bar average traded value >= 10 crore (assumption)")
            matched = True
        if not matched and not re.fullmatch(r"(indian|nse|nifty\s*\d*)?\s*(stocks?|equities|shares)?", c):
            unsupported.append(cl)
    ir = {"ir_version": 1, "classification": "nl-builder", "long": None, "short": None,
          "stop": {"method": "swing", "buffer_atr": 0.2}, "target": {"r": 2.0}}
    ir[side] = {"type": "group", "logic": "AND", "children": rules} if rules else None
    notes.append("risk reference levels: stop = last confirmed swing ± 0.2 ATR, target = 2R (defaults, editable)")
    envelope = {"timeframe": primary, "htf": sorted(htf, key=TIMEFRAMES.index), "universe": universe}
    b = build("NL scanner", ir, envelope)
    errors = b["errors"] + ([] if rules else ["no supported condition recognised"])
    return {"ir": ir, "envelope": envelope, "interpretations": notes, "unsupported": unsupported, "errors": errors,
            "valid": not errors and not unsupported, "readable": render_strategy(ir) if rules else ""}
