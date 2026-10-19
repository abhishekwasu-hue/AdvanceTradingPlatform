"""Language-independent strategy IR + host rule engine (Visual No-Code Builder and Pine subset).

No user code is ever executed here: the IR is a JSON tree evaluated by a fixed interpreter.

Expression nodes (evaluate to a numpy array over the context window, NaN = missing):
  {"t":"num","v":1.5}                         constant
  {"t":"param","name":"len"}                  strategy parameter (numeric)
  {"t":"field","name":"close"}                context series (see contract.SERIES); {"t":"field","name":"hl2"} derived
  {"t":"ind","fn":"ema|sma|rsi|atr|highest|lowest|change|stdev","src":<expr>,"n":<int|param>}
  {"t":"shift","a":<expr>,"n":<int>}          value n bars ago (history reference)
  {"t":"bin","op":"+|-|*|/","a":..,"b":..}
  {"t":"cmp","op":">|<|>=|<=|==|!=","a":..,"b":..}
  {"t":"cross","dir":"over|under","a":..,"b":..}
  {"t":"and"|"or","args":[...]}, {"t":"not","a":..}
  {"t":"pred","name":<PREDICATES key>,"args":{...}}   structure/MTF/session/liquidity/data-quality predicate (last bar)
Rule tree: {"type":"group","logic":"AND|OR","children":[group|rule]}, rule = {"type":"rule","name":str,"expr":<expr>}
Strategy IR: {"ir_version":1,"classification":str,"long":<group|null>,"short":<group|null>,
              "stop":{"method":"atr","mult":1.5}|{"method":"swing","buffer_atr":0.2}|null,"target":{"r":2.0}|null}

Semantics: evaluated at the LAST COMPLETED BAR (index n-1). Any comparison involving a missing value is MISSING
(rule passed = null) and a missing rule never qualifies a group (fail-closed). Floating comparisons are exact
except "==" / "!=" which use a relative tolerance of 1e-9.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from ..analysis.indicators import atr as _atr_df, ema as _ema, rsi as _rsi, sma as _sma
from .contract import SERIES

IR_VERSION = 1
MAX_DEPTH = 6
MAX_NODES = 300
CMP = {">": np.greater, "<": np.less, ">=": np.greater_equal, "<=": np.less_equal}
IND_FNS = {"ema", "sma", "rsi", "atr", "highest", "lowest", "change", "stdev"}
DERIVED = {"hl2", "hlc3", "ohlc4"}

# predicate catalogue: name -> (description, args with defaults, data requirement)
PREDICATES: dict[str, dict] = {
    "trend_is": {"desc": "Primary-timeframe trend label equals value", "args": {"label": "Bullish"}},
    "htf_trend_is": {"desc": "Higher-timeframe trend label equals value", "args": {"tf": "1W", "label": "Bullish"}},
    "bos_recent": {"desc": "Close-confirmed BOS in direction within N bars", "args": {"direction": "bullish", "bars": 5}},
    "choch_recent": {"desc": "CHoCH / MSS in direction within N bars", "args": {"direction": "bullish", "bars": 5}},
    "close_above_last_swing_high": {"desc": "Last close above the last CONFIRMED swing high", "args": {}},
    "close_below_last_swing_low": {"desc": "Last close below the last CONFIRMED swing low", "args": {}},
    "near_support": {"desc": "Close within k*ATR of a support level", "args": {"atr_mult": 1.0}},
    "near_resistance": {"desc": "Close within k*ATR of a resistance level", "args": {"atr_mult": 1.0}},
    "in_demand_zone": {"desc": "Low of last bar touched a non-invalidated demand zone", "args": {}},
    "in_supply_zone": {"desc": "High of last bar touched a non-invalidated supply zone", "args": {}},
    "breakout_retest": {"desc": "Broke last swing high within N bars and a later low retested it (held)", "args": {"bars": 10, "atr_tol": 0.25}},
    "pullback_to_ema": {"desc": "Low within k*ATR of EMA(period) during last 3 bars and close above it", "args": {"period": 20, "atr_mult": 0.5}},
    "rally_to_ema": {"desc": "High within k*ATR of EMA(period) during last 3 bars and close below it", "args": {"period": 20, "atr_mult": 0.5}},
    "session_between": {"desc": "Last bar time-of-day within [start,end] (intraday)", "args": {"start": "09:30", "end": "14:45"}},
    "min_avg_traded_value": {"desc": "20-bar avg close*volume >= value (missing volume -> missing)", "args": {"value": 1e8}},
    "data_quality_ok": {"desc": "Data status is OK (not STALE)", "args": {}},
    # ---- v2 (scanner foundation)
    "candle_pattern": {"desc": "Candle pattern (Hammer, Shooting Star, Bullish/Bearish Engulfing, Doji, Inside Bar, Outside Bar, "
                               "Morning/Evening Star, Pin Bar, Strong Body, Rejection, Gap Reversal) with bias within N bars, "
                               "not failed (confirmed=true: next bar confirmed it)",
                       "args": {"pattern": "Pin Bar", "bias": "bullish", "bars": 1, "confirmed": False}},
    "supertrend_dir": {"desc": "Supertrend(10, 3) direction on the last bar", "args": {"direction": "bullish"}},
    "pct_change": {"desc": "Close % change over N bars compared with value (op >= or <=)", "args": {"bars": 1, "op": ">=", "value": 2.0}},
    "rsi_divergence": {"desc": "Regular RSI(14) divergence between the last two CONFIRMED swing lows (bullish) / highs "
                               "(bearish); the latest pivot at most max_age bars old", "args": {"direction": "bullish", "max_age": 15}},
    "gap_recent": {"desc": "Opening gap >= min_atr x ATR in direction within N bars (unfilled=true: not yet filled)",
                   "args": {"direction": "up", "bars": 1, "min_atr": 0.5, "unfilled": False}},
    "sr_role_reversal": {"desc": "Close within k*ATR of a level that flipped role (old resistance now support, or vice versa)",
                         "args": {"role": "support", "atr_mult": 1.0}},
    "last_swing_label": {"desc": "Label of the last CONFIRMED swing high (kind H: HH/LH) or low (kind L: HL/LL)",
                         "args": {"kind": "L", "label": "HL"}},
    "htf_close_above_ema": {"desc": "Higher-timeframe close above (above=true) / below its EMA(20|50)",
                            "args": {"tf": "1D", "period": 50, "above": True}},
}
CANDLE_NAMES = {"Doji", "Hammer", "Shooting Star", "Bullish Engulfing", "Bearish Engulfing", "Inside Bar", "Outside Bar",
                "Morning Star", "Evening Star", "Pin Bar", "Strong Body", "Rejection", "Gap Reversal"}
ARG_CHOICES = {"direction": {"bullish", "bearish", "up", "down"}, "bias": {"bullish", "bearish", "neutral"},
               "op": {">=", "<="}, "role": {"support", "resistance"}, "tf": {"1W", "1D", "1H", "30m", "15m"},
               "period": {20, 50}, "label": {"Bullish", "Bearish", "Mixed", "HH", "LH", "HL", "LL"},
               "pattern": CANDLE_NAMES, "kind": {"H", "L"}}


class IRError(ValueError):
    pass


# ------------------------------------------------------------------ validation
def validate_strategy_ir(ir: dict, param_names: set[str] | None = None) -> list[str]:
    errs: list[str] = []
    if not isinstance(ir, dict):
        return ["IR must be an object"]
    if ir.get("ir_version") != IR_VERSION:
        errs.append(f"ir_version must be {IR_VERSION}")
    if not ir.get("long") and not ir.get("short"):
        errs.append("at least one of long/short rule groups is required")
    count = [0]
    for side in ("long", "short"):
        if ir.get(side):
            _val_group(ir[side], 1, errs, param_names or set(), count, side)
    st = ir.get("stop")
    if st is not None:
        if st.get("method") == "atr" and not (0 < float(st.get("mult", 0)) <= 10):
            errs.append("stop.mult must be in (0,10]")
        elif st.get("method") not in ("atr", "swing"):
            errs.append("stop.method must be atr or swing")
    tg = ir.get("target")
    if tg is not None and not (0 < float(tg.get("r", 0)) <= 20):
        errs.append("target.r must be in (0,20]")
    if count[0] > MAX_NODES:
        errs.append(f"too many nodes ({count[0]} > {MAX_NODES})")
    return errs


def _val_group(g, depth, errs, pn, count, path):
    count[0] += 1
    if depth > MAX_DEPTH:
        errs.append(f"{path}: nesting depth > {MAX_DEPTH}")
        return
    if g.get("type") == "rule":
        if not str(g.get("name", "")).strip():
            errs.append(f"{path}: rule needs a name")
        _val_expr(g.get("expr"), errs, pn, count, f"{path}/{g.get('name')}", want_bool=True)
        return
    if g.get("type") != "group" or g.get("logic") not in ("AND", "OR"):
        errs.append(f"{path}: group needs logic AND/OR")
        return
    if not g.get("children"):
        errs.append(f"{path}: empty group")
    for i, c in enumerate(g.get("children", [])):
        _val_group(c, depth + 1, errs, pn, count, f"{path}[{i}]")


def _val_expr(e, errs, pn, count, path, want_bool=False):
    count[0] += 1
    if not isinstance(e, dict) or "t" not in e:
        errs.append(f"{path}: invalid expression")
        return
    t = e["t"]
    boolean = t in ("cmp", "cross", "and", "or", "not", "pred")
    if want_bool and not boolean:
        errs.append(f"{path}: rule expression must be a condition (comparison/cross/predicate)")
    if t == "num":
        if not isinstance(e.get("v"), (int, float)) or not math.isfinite(e["v"]):
            errs.append(f"{path}: num must be finite")
    elif t == "param":
        if e.get("name") not in pn:
            errs.append(f"{path}: unknown parameter {e.get('name')}")
    elif t == "field":
        if e.get("name") not in SERIES and e.get("name") not in DERIVED:
            errs.append(f"{path}: unknown field {e.get('name')}")
    elif t == "ind":
        if e.get("fn") not in IND_FNS:
            errs.append(f"{path}: unknown indicator {e.get('fn')}")
        if e.get("fn") != "atr":
            _val_expr(e.get("src"), errs, pn, count, path)
        n = e.get("n")
        if isinstance(n, dict):
            _val_expr(n, errs, pn, count, path)
        elif not isinstance(n, int) or not (1 <= n <= 500):
            errs.append(f"{path}: period must be an int in 1..500")
    elif t == "shift":
        if not isinstance(e.get("n"), int) or not (0 <= e["n"] <= 500):
            errs.append(f"{path}: history offset must be int 0..500")
        _val_expr(e.get("a"), errs, pn, count, path)
    elif t in ("bin", "cmp", "cross"):
        ops = {"bin": ("+", "-", "*", "/"), "cmp": (">", "<", ">=", "<=", "==", "!=")}
        if t in ops and e.get("op") not in ops[t]:
            errs.append(f"{path}: bad operator {e.get('op')}")
        if t == "cross" and e.get("dir") not in ("over", "under"):
            errs.append(f"{path}: cross dir must be over/under")
        _val_expr(e.get("a"), errs, pn, count, path)
        _val_expr(e.get("b"), errs, pn, count, path)
    elif t in ("and", "or"):
        if not e.get("args"):
            errs.append(f"{path}: {t} needs args")
        for a in e.get("args", []):
            _val_expr(a, errs, pn, count, path, want_bool=True)
    elif t == "not":
        _val_expr(e.get("a"), errs, pn, count, path, want_bool=True)
    elif t == "pred":
        if e.get("name") not in PREDICATES:
            errs.append(f"{path}: unknown predicate {e.get('name')}")
            return
        spec = PREDICATES[e["name"]]["args"]
        for k, v in (e.get("args") or {}).items():
            if k not in spec:
                errs.append(f"{path}: predicate {e['name']} has no argument '{k}' (allowed: {sorted(spec)})")
                continue
            d = spec[k]
            if isinstance(d, bool):
                ok = isinstance(v, bool)
            elif isinstance(d, (int, float)):
                ok = isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
            else:
                ok = isinstance(v, str)
            if not ok:
                errs.append(f"{path}: {e['name']}.{k} must be {type(d).__name__}")
            elif k in ARG_CHOICES and v not in ARG_CHOICES[k]:
                errs.append(f"{path}: {e['name']}.{k}={v!r} not supported (allowed: {sorted(map(str, ARG_CHOICES[k]))})")
    else:
        errs.append(f"{path}: unknown node type {t}")


def lookback_required(ir: dict) -> int:
    """Bars needed incl. indicator warm-up (3x period for EMA-type smoothing)."""
    need = [1]

    def walk(e, extra=0):
        if not isinstance(e, dict):
            return
        if e.get("t") == "ind":
            n = e["n"] if isinstance(e.get("n"), int) else 50
            need.append(extra + 3 * n + 1)
            walk(e.get("src"), extra + n)
        elif e.get("t") == "shift":
            walk(e["a"], extra + e["n"])
            need.append(extra + e["n"] + 1)
        elif e.get("t") == "cross":
            walk(e["a"], extra + 1)
            walk(e["b"], extra + 1)
        else:
            for k in ("a", "b", "src"):
                walk(e.get(k), extra)
            for a in e.get("args", []) if isinstance(e.get("args"), list) else []:
                walk(a, extra)
            if e.get("type") == "rule":
                walk(e.get("expr"), extra)
            for c in e.get("children", []):
                walk(c, extra)
    for side in ("long", "short"):
        walk(ir.get(side))
    return max(need)


# ------------------------------------------------------------------ evaluation
class _Ev:
    def __init__(self, ctx: dict):
        self.ctx = ctx
        self.s = ctx["series"]
        self.n = len(self.s["close"])
        self.p = ctx.get("params", {})

    def arr(self, name: str) -> np.ndarray:
        if name in DERIVED:
            h, l_, c, o = (self.arr(x) for x in ("high", "low", "close", "open"))
            return {"hl2": (h + l_) / 2, "hlc3": (h + l_ + c) / 3, "ohlc4": (o + h + l_ + c) / 4}[name]
        return np.array([np.nan if v is None else float(v) for v in self.s[name]], dtype=float)

    def num(self, v) -> int:
        if isinstance(v, dict):
            x = self.p.get(v["name"]) if v.get("t") == "param" else None
            if x is None:
                raise IRError(f"parameter {v.get('name')} not set")
            return int(x)
        return int(v)

    def ev(self, e) -> np.ndarray:
        t = e["t"]
        if t == "num":
            return np.full(self.n, float(e["v"]))
        if t == "param":
            v = self.p.get(e["name"])
            if v is None:
                raise IRError(f"parameter {e['name']} not set")
            return np.full(self.n, float(v))
        if t == "field":
            return self.arr(e["name"])
        if t == "shift":
            a = self.ev(e["a"])
            k = e["n"]
            return np.concatenate([np.full(min(k, self.n), np.nan), a[:self.n - k]]) if k else a
        if t == "ind":
            n = self.num(e["n"])
            if e["fn"] == "atr":
                df = pd.DataFrame({"High": self.arr("high"), "Low": self.arr("low"), "Close": self.arr("close")})
                return _atr_df(df, n).to_numpy()
            src = pd.Series(self.ev(e["src"]))
            f = {"ema": lambda: _ema(src, n), "sma": lambda: _sma(src, n), "rsi": lambda: _rsi(src, n),
                 "highest": lambda: src.rolling(n, min_periods=n).max(), "lowest": lambda: src.rolling(n, min_periods=n).min(),
                 "change": lambda: src - src.shift(n), "stdev": lambda: src.rolling(n, min_periods=n).std(ddof=0)}[e["fn"]]
            return f().to_numpy(dtype=float)
        if t == "bin":
            a, b = self.ev(e["a"]), self.ev(e["b"])
            with np.errstate(divide="ignore", invalid="ignore"):
                r = {"+": a + b, "-": a - b, "*": a * b, "/": np.where(b == 0, np.nan, a / np.where(b == 0, 1, b))}[e["op"]]
            return r
        if t == "cmp":
            a, b = self.ev(e["a"]), self.ev(e["b"])
            miss = np.isnan(a) | np.isnan(b)
            if e["op"] in ("==", "!="):
                eq = np.isclose(a, b, rtol=1e-9, atol=1e-12)
                r = eq if e["op"] == "==" else ~eq
            else:
                with np.errstate(invalid="ignore"):
                    r = CMP[e["op"]](a, b)
            return np.where(miss, np.nan, r.astype(float))
        if t == "cross":
            a, b = self.ev(e["a"]), self.ev(e["b"])
            pa, pb = np.r_[np.nan, a[:-1]], np.r_[np.nan, b[:-1]]
            miss = np.isnan(a) | np.isnan(b) | np.isnan(pa) | np.isnan(pb)
            with np.errstate(invalid="ignore"):
                r = (a > b) & (pa <= pb) if e["dir"] == "over" else (a < b) & (pa >= pb)
            return np.where(miss, np.nan, r.astype(float))
        if t in ("and", "or"):
            vals = [self.ev(a) for a in e["args"]]
            out = vals[0]
            for v in vals[1:]:
                out = _and3(out, v) if t == "and" else _or3(out, v)
            return out
        if t == "not":
            a = self.ev(e["a"])
            return np.where(np.isnan(a), np.nan, 1.0 - a)
        if t == "pred":
            r = np.full(self.n, np.nan)
            v = _predicate(self.ctx, e["name"], {**PREDICATES[e["name"]]["args"], **(e.get("args") or {})})
            r[-1] = np.nan if v is None else float(v)
            return r
        raise IRError(f"unknown node {t}")


def _and3(a, b):
    out = np.where((a == 0) | (b == 0), 0.0, np.where(np.isnan(a) | np.isnan(b), np.nan, 1.0))
    return out


def _or3(a, b):
    return np.where((a == 1) | (b == 1), 1.0, np.where(np.isnan(a) | np.isnan(b), np.nan, 0.0))


def _last(ctx, k):
    v = ctx["series"][k][-1]
    return None if v is None else float(v)


def _predicate(ctx: dict, name: str, a: dict) -> bool | None:
    st, s = ctx["structure"], ctx["series"]
    n = len(s["close"])
    c, atr = _last(ctx, "close"), _last(ctx, "atr14")
    if name == "trend_is":
        return None if st.get("trend_label") in (None, "NA") else st["trend_label"] == a["label"]
    if name == "htf_trend_is":
        h = (ctx.get("htf") or {}).get(a["tf"])
        return None if not h else h.get("trend_label") == a["label"]
    if name in ("bos_recent", "choch_recent"):
        typ = "BOS" if name == "bos_recent" else "CHoCH"
        return any(e["type"] == typ and e["direction"] == a["direction"] and e["index"] >= n - int(a["bars"]) for e in st["events"])
    if name in ("close_above_last_swing_high", "close_below_last_swing_low"):
        kind = "H" if name.endswith("high") else "L"
        sw = [x for x in st["swings"] if x["kind"] == kind and x["confirmed_index"] <= n - 1]
        if not sw or c is None:
            return None
        return c > sw[-1]["price"] if kind == "H" else c < sw[-1]["price"]
    if name in ("near_support", "near_resistance"):
        if c is None or atr is None:
            return None
        role = "support" if name == "near_support" else "resistance"
        return any(x["role"] == role and abs(c - x["level"]) <= float(a["atr_mult"]) * atr for x in st["sr"])
    if name in ("in_demand_zone", "in_supply_zone"):
        kind = "demand" if name == "in_demand_zone" else "supply"
        lo, hi = _last(ctx, "low"), _last(ctx, "high")
        if lo is None:
            return None
        return any(z["kind"] == kind and z["status"] != "invalidated" and
                   ((lo <= z["top"] and c >= z["bottom"]) if kind == "demand" else (hi >= z["bottom"] and c <= z["top"]))
                   for z in st["zones"])
    if name == "breakout_retest":
        sw = [x for x in st["swings"] if x["kind"] == "H"]
        if not sw or atr is None:
            return None
        lvl, ci = sw[-1]["price"], sw[-1]["confirmed_index"]
        cl, lw = s["close"], s["low"]
        brk = [j for j in range(max(ci + 1, n - int(a["bars"])), n) if cl[j] is not None and cl[j] > lvl]
        if not brk:
            return False
        later = [lw[j] for j in range(brk[0] + 1, n) if lw[j] is not None]
        return bool(later) and min(later) <= lvl + float(a["atr_tol"]) * atr and c > lvl
    if name in ("pullback_to_ema", "rally_to_ema"):
        key = f"ema{int(a['period'])}"
        if key not in s or atr is None:
            arr = _ema(pd.Series([np.nan if v is None else v for v in s["close"]], dtype=float), int(a["period"])).to_numpy()
        else:
            arr = np.array([np.nan if v is None else v for v in s[key]], dtype=float)
        e = arr[-1]
        if not np.isfinite(e) or atr is None or c is None:
            return None
        if name == "pullback_to_ema":
            lows = [v for v in s["low"][-3:] if v is not None]
            return bool(lows) and min(lows) <= e + float(a["atr_mult"]) * atr and c > e
        highs = [v for v in s["high"][-3:] if v is not None]
        return bool(highs) and max(highs) >= e - float(a["atr_mult"]) * atr and c < e
    if name == "session_between":
        if ctx["meta"].get("timeframe") in ("1D", "1W"):
            return None
        hm = str(s["time"][-1])[11:16]
        return a["start"] <= hm <= a["end"]
    if name == "min_avg_traded_value":
        cl, vo = s["close"][-20:], s["volume"][-20:]
        if any(v is None for v in vo) or len(vo) < 20:
            return None
        return float(np.mean([x * y for x, y in zip(cl, vo)])) >= float(a["value"])
    if name == "data_quality_ok":
        return ctx["meta"].get("data_status") == "OK"
    if name == "candle_pattern":
        hits = [x for x in st.get("candles", []) if x["pattern"] == a["pattern"] and x["bias"] == a["bias"]
                and x["index"] >= n - int(a["bars"]) and x["status"] != "failed"]
        if a["confirmed"]:
            hits = [x for x in hits if x["status"] == "confirmed"]
        return bool(hits)
    if name == "supertrend_dir":
        v = _last(ctx, "st_dir")
        return None if v is None else (v > 0) == (a["direction"] in ("bullish", "up"))
    if name == "pct_change":
        k = int(a["bars"])
        cl = s["close"]
        if k < 1 or len(cl) <= k or cl[-1] is None or cl[-1 - k] in (None, 0):
            return None
        ch = (cl[-1] / cl[-1 - k] - 1) * 100
        return ch >= float(a["value"]) if a["op"] == ">=" else ch <= float(a["value"])
    if name == "rsi_divergence":
        bull = a["direction"] in ("bullish", "up")
        sw = [x for x in st["swings"] if x["kind"] == ("L" if bull else "H") and x["confirmed_index"] <= n - 1][-2:]
        r = s.get("rsi14") or []
        if len(sw) < 2 or sw[1]["index"] < n - int(a["max_age"]) or sw[0]["index"] < 0:
            return False if len(sw) >= 2 else None
        r0, r1 = r[sw[0]["index"]], r[sw[1]["index"]]
        if r0 is None or r1 is None:
            return None
        if bull:
            return sw[1]["price"] < sw[0]["price"] and r1 > r0
        return sw[1]["price"] > sw[0]["price"] and r1 < r0
    if name == "gap_recent":
        want = "up" if a["direction"] in ("up", "bullish") else "down"
        return any(g["direction"] == want and g["index"] >= n - int(a["bars"]) and g["size_atr"] >= float(a["min_atr"])
                   and (not a["unfilled"] or not g["filled"]) for g in st.get("gaps", []))
    if name == "sr_role_reversal":
        if c is None or atr is None:
            return None
        return any(x.get("role_reversal") and x["role"] == a["role"] and abs(c - x["level"]) <= float(a["atr_mult"]) * atr
                   for x in st["sr"])
    if name == "last_swing_label":
        sw = [x for x in st["swings"] if x["kind"] == a["kind"] and x["confirmed_index"] <= n - 1]
        return None if not sw else sw[-1]["label"] == a["label"]
    if name == "htf_close_above_ema":
        h = (ctx.get("htf") or {}).get(a["tf"])
        e = (h or {}).get(f"ema{int(a['period'])}")
        if not h or e is None or h.get("close") is None:
            return None
        return (h["close"] > e) == bool(a["above"])
    raise IRError(f"unknown predicate {name}")


def _tri(v: float) -> bool | None:
    return None if np.isnan(v) else bool(v >= 0.5)


def eval_group(ev: _Ev, g: dict, rules_out: list) -> bool | None:
    if g["type"] == "rule":
        v = _tri(ev.ev(g["expr"])[-1])
        rules_out.append({"name": g["name"], "passed": v, "detail": render(g["expr"]) + ("" if v is not None else "  [missing data]")})
        return v
    vals = [eval_group(ev, c, rules_out) for c in g["children"]]
    if g["logic"] == "AND":
        if any(v is False for v in vals):
            return False
        return None if any(v is None for v in vals) else True
    if any(v is True for v in vals):
        return True
    return None if any(v is None for v in vals) else False


def evaluate_ir(ir: dict, ctx: dict) -> dict:
    """Evaluate a strategy IR on one context; returns a contract result dict (validated by the caller)."""
    ev = _Ev(ctx)
    rules: list[dict] = []
    res = {"long": None, "short": None}
    for side in ("long", "short"):
        if ir.get(side):
            sub: list[dict] = []
            res[side] = eval_group(ev, ir[side], sub)
            rules += [{**r, "name": f"{side}: {r['name']}"[:200]} for r in sub]
    c, atr = _last(ctx, "close"), _last(ctx, "atr14")
    bias = "long" if res["long"] is True else "short" if res["short"] is True else "neutral"
    out = {"status": "qualified" if bias != "neutral" else "rejected", "bias": bias,
           "classification": ir.get("classification", "visual"), "rules": rules,
           "explanation": ("long rules all passed" if bias == "long" else "short rules all passed" if bias == "short"
                           else "no side qualified (failed or missing rules)")}
    if bias != "neutral" and c is not None:
        d = 1 if bias == "long" else -1
        stop = None
        st = ir.get("stop")
        if st and st["method"] == "atr" and atr is not None:
            stop = c - d * float(st["mult"]) * atr
        elif st and st["method"] == "swing":
            sw = [x for x in ctx["structure"]["swings"] if x["kind"] == ("L" if d > 0 else "H")]
            if sw and atr is not None and (c - sw[-1]["price"]) * d > 0:
                stop = sw[-1]["price"] - d * float(st.get("buffer_atr", 0.2)) * atr
        out["stop"] = None if stop is None else round(stop, 4)
        out["invalidation"] = out["stop"]
        tg = ir.get("target")
        if tg and stop is not None:
            out["target"] = round(c + d * float(tg["r"]) * abs(c - stop), 4)
        out["entry"] = f"next bar after completed-bar close {c:.2f}"
    return out


# ------------------------------------------------------------------ readable rendering + code export
def render(e) -> str:
    t = e.get("t")
    if t == "num":
        return f"{e['v']:g}"
    if t == "param":
        return f"{{{e['name']}}}"
    if t == "field":
        return e["name"]
    if t == "shift":
        return f"{render(e['a'])}[{e['n']}]"
    if t == "ind":
        n = render(e["n"]) if isinstance(e["n"], dict) else e["n"]
        return f"ATR({n})" if e["fn"] == "atr" else f"{e['fn'].upper()}({render(e['src'])}, {n})"
    if t in ("bin", "cmp"):
        return f"({render(e['a'])} {e['op']} {render(e['b'])})" if t == "bin" else f"{render(e['a'])} {e['op']} {render(e['b'])}"
    if t == "cross":
        return f"{render(e['a'])} crosses {e['dir']} {render(e['b'])}"
    if t in ("and", "or"):
        return "(" + f" {t.upper()} ".join(render(a) for a in e["args"]) + ")"
    if t == "not":
        return f"NOT {render(e['a'])}"
    if t == "pred":
        args = {**PREDICATES[e["name"]]["args"], **(e.get("args") or {})}
        return f"{e['name']}({', '.join(f'{k}={v}' for k, v in args.items())})"
    return "?"


def render_group(g: dict, indent: int = 0) -> str:
    pad = "  " * indent
    if g["type"] == "rule":
        return f"{pad}• {g['name']}: {render(g['expr'])}"
    return f"{pad}{g['logic']} of:\n" + "\n".join(render_group(c, indent + 1) for c in g["children"])


def render_strategy(ir: dict) -> str:
    parts = []
    for side in ("long", "short"):
        if ir.get(side):
            parts.append(f"{side.upper()} when\n{render_group(ir[side], 1)}")
    if ir.get("stop"):
        parts.append(f"STOP: {ir['stop']}")
    if ir.get("target"):
        parts.append(f"TARGET: {ir['target']['r']}R")
    return "\n".join(parts)


# ------------------------------------------------------------------ templates
def _f(n): return {"t": "field", "name": n}
def _ema_(n): return {"t": "ind", "fn": "ema", "src": _f("close"), "n": n}
def _cmp(a, op, b): return {"t": "cmp", "op": op, "a": a, "b": b}
def _num(v): return {"t": "num", "v": v}
def _rule(name, e): return {"type": "rule", "name": name, "expr": e}
def _and(*c): return {"type": "group", "logic": "AND", "children": list(c)}
def _p(name, **a): return {"t": "pred", "name": name, "args": a}


TEMPLATES: dict[str, dict] = {
    "Bullish trend": {"ir_version": 1, "classification": "trend", "long": _and(
        _rule("close > EMA20", _cmp(_f("close"), ">", _f("ema20"))), _rule("EMA20 > EMA50", _cmp(_f("ema20"), ">", _f("ema50"))),
        _rule("EMA50 > EMA200", _cmp(_f("ema50"), ">", _f("ema200"))), _rule("ADX >= 20", _cmp(_f("adx"), ">=", _num(20)))),
        "short": None, "stop": {"method": "swing", "buffer_atr": 0.2}, "target": {"r": 2.0}},
    "Bearish trend": {"ir_version": 1, "classification": "trend", "long": None, "short": _and(
        _rule("close < EMA20", _cmp(_f("close"), "<", _f("ema20"))), _rule("EMA20 < EMA50", _cmp(_f("ema20"), "<", _f("ema50"))),
        _rule("EMA50 < EMA200", _cmp(_f("ema50"), "<", _f("ema200"))), _rule("ADX >= 20", _cmp(_f("adx"), ">=", _num(20)))),
        "stop": {"method": "swing", "buffer_atr": 0.2}, "target": {"r": 2.0}},
    "Breakout and retest": {"ir_version": 1, "classification": "breakout", "long": _and(
        _rule("broke & retested last swing high", _p("breakout_retest", bars=10, atr_tol=0.25)),
        _rule("RVOL >= 1.2", _cmp(_f("rvol"), ">=", _num(1.2)))), "short": None,
        "stop": {"method": "atr", "mult": 1.5}, "target": {"r": 2.0}},
    "Trend-continuation pullback": {"ir_version": 1, "classification": "pullback", "long": _and(
        _rule("uptrend", _p("trend_is", label="Bullish")), _rule("pullback to EMA20 held", _p("pullback_to_ema", period=20, atr_mult=0.5)),
        _rule("RSI > 40", _cmp(_f("rsi14"), ">", _num(40)))), "short": None,
        "stop": {"method": "swing", "buffer_atr": 0.2}, "target": {"r": 2.0}},
    "Support reversal": {"ir_version": 1, "classification": "reversal", "long": _and(
        _rule("near support", _p("near_support", atr_mult=0.5)), _rule("bullish CHoCH recent", _p("choch_recent", direction="bullish", bars=10)),
        _rule("close > prior high", _cmp(_f("close"), ">", {"t": "shift", "a": _f("high"), "n": 1}))), "short": None,
        "stop": {"method": "atr", "mult": 1.0}, "target": {"r": 2.0}},
    "Resistance reversal": {"ir_version": 1, "classification": "reversal", "long": None, "short": _and(
        _rule("near resistance", _p("near_resistance", atr_mult=0.5)), _rule("bearish CHoCH recent", _p("choch_recent", direction="bearish", bars=10)),
        _rule("close < prior low", _cmp(_f("close"), "<", {"t": "shift", "a": _f("low"), "n": 1}))),
        "stop": {"method": "atr", "mult": 1.0}, "target": {"r": 2.0}},
    "Volume expansion": {"ir_version": 1, "classification": "volume", "long": _and(
        _rule("RVOL >= 2", _cmp(_f("rvol"), ">=", _num(2))), _rule("green bar", _cmp(_f("close"), ">", _f("open"))),
        _rule("close > 20-bar high (prior)", _cmp(_f("close"), ">", {"t": "shift", "a": {"t": "ind", "fn": "highest", "src": _f("high"), "n": 20}, "n": 1}))),
        "short": None, "stop": {"method": "atr", "mult": 1.5}, "target": {"r": 2.0}},
    "Multi-timeframe confluence": {"ir_version": 1, "classification": "mtf", "long": _and(
        _rule("weekly bullish", _p("htf_trend_is", tf="1W", label="Bullish")), _rule("daily bullish", _p("trend_is", label="Bullish")),
        _rule("bullish BOS in 10 bars", _p("bos_recent", direction="bullish", bars=10))), "short": None,
        "stop": {"method": "swing", "buffer_atr": 0.2}, "target": {"r": 2.0}},
}


# ------------------------------------------------------------------ Python export (expression subset; equivalence tested)
class NotExportable(ValueError):
    pass


def _py(e) -> str:
    t = e["t"]
    if t == "num":
        return f"amw.num({float(e['v'])!r}, N)"
    if t == "param":
        return f"amw.num(float(P[{e['name']!r}]), N)"
    if t == "field":
        if e["name"] in DERIVED:
            parts = {"hl2": ("high", "low"), "hlc3": ("high", "low", "close"), "ohlc4": ("open", "high", "low", "close")}[e["name"]]
            acc = f"S[{parts[0]!r}]"
            for p_ in parts[1:]:
                acc = f"amw.binop({acc}, '+', S[{p_!r}])"
            return f"amw.binop({acc}, '/', amw.num({float(len(parts))}, N))"
        return f"S[{e['name']!r}]"
    if t == "shift":
        return f"amw.shift({_py(e['a'])}, {e['n']})"
    if t == "ind":
        if isinstance(e["n"], dict):
            raise NotExportable("parameterised indicator periods are not exportable")
        n = e["n"]
        if e["fn"] == "atr":
            return f"amw.atr(S['high'], S['low'], S['close'], {n})"
        src = _py(e["src"])
        if e["fn"] in ("ema", "sma", "rsi"):
            return f"amw.{e['fn']}({src}, {n})"
        if e["fn"] in ("highest", "lowest"):
            return f"amw.rolling({src}, {n}, {'max' if e['fn'] == 'highest' else 'min'})"
        if e["fn"] == "change":
            return f"amw.binop({src}, '-', amw.shift({src}, {n}))"
        raise NotExportable(f"indicator {e['fn']} is not exportable")
    if t == "bin":
        return f"amw.binop({_py(e['a'])}, {e['op']!r}, {_py(e['b'])})"
    if t == "cmp":
        return f"amw.cmp({_py(e['a'])}, {e['op']!r}, {_py(e['b'])})"
    if t == "cross":
        return f"amw.cross({_py(e['a'])}, {_py(e['b'])}, {e['dir']!r})"
    if t in ("and", "or"):
        return f"amw.{t}3(" + ", ".join(_py(a) for a in e["args"]) + ")"
    if t == "not":
        return f"amw.not3({_py(e['a'])})"
    raise NotExportable(f"node '{t}' (e.g. structure predicates) cannot be exported to code; use the visual engine")


def _py_group(g, out: list, side: str, ind: str = "    ") -> str:
    if g["type"] == "rule":
        var = f"r{len(out)}"
        out.append(f"{ind}{var} = amw.last({_py(g['expr'])})\n"
                   f"{ind}rules.append({{'name': {(side + ': ' + g['name'])[:200]!r}, 'passed': {var}, 'detail': {render(g['expr'])!r}}})")
        return var
    vs = [_py_group(c, out, side, ind) for c in g["children"]]
    lst = "[" + ", ".join(vs) + "]"
    if g["logic"] == "AND":
        return f"(False if any(v is False for v in {lst}) else (None if any(v is None for v in {lst}) else True))"
    return f"(True if any(v is True for v in {lst}) else (None if any(v is None for v in {lst}) else False))"


def export_python(ir: dict) -> str:
    """Translate an IR (expression subset, ATR stop / R target) into a Python Lab strategy with identical results."""
    if (ir.get("stop") or {}).get("method") == "swing":
        raise NotExportable("swing-based stop is not exportable; use an ATR stop")
    body: list[str] = []
    sides = {}
    for side in ("long", "short"):
        if ir.get(side):
            lines: list[str] = []
            v = _py_group(ir[side], lines, side)
            body += lines
            body.append(f"    {side} = {v}")
            sides[side] = True
        else:
            body.append(f"    {side} = None")
    st, tg = ir.get("stop"), ir.get("target")
    return "\n".join([
        f"# Exported from the AMW visual builder (IR v{IR_VERSION}); semantics verified equal to the host engine in tests.",
        "def evaluate(ctx):",
        "    S, P = ctx['series'], ctx.get('params', {})",
        "    N = len(S['close'])",
        "    rules = []",
        *body,
        "    bias = 'long' if long is True else 'short' if short is True else 'neutral'",
        "    out = {'status': 'qualified' if bias != 'neutral' else 'rejected', 'bias': bias,",
        f"           'classification': {ir.get('classification', 'visual')!r}, 'rules': rules,",
        "           'explanation': 'long rules all passed' if bias == 'long' else 'short rules all passed' if bias == 'short'",
        "           else 'no side qualified (failed or missing rules)'}",
        "    c, a = S['close'][-1], S['atr14'][-1]",
        "    if bias != 'neutral' and c is not None:",
        "        d = 1 if bias == 'long' else -1",
        (f"        stop = c - d * {float(st['mult'])!r} * a if a is not None else None" if st else "        stop = None"),
        "        out['stop'] = None if stop is None else round(stop, 4)",
        "        out['invalidation'] = out['stop']",
        (f"        if stop is not None:\n            out['target'] = round(c + d * {float(tg['r'])!r} * abs(c - stop), 4)" if tg else ""),
        "        out['entry'] = 'next bar after completed-bar close %.2f' % c",
        "    return out",
        ""])
