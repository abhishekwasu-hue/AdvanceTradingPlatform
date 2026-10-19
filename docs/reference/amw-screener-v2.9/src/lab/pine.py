"""Pine Script SUBSET -> AMW strategy IR translator. Pine is never executed; it is parsed and translated.

SUPPORTED (PINE_SUBSET):
  //@version=5 or //@version=6 (required)          indicator(...) / strategy(...) header (arguments ignored)
  name = <expr>            (single assignment; `:=` reassignment NOT supported)
  name = input.int(def, ...) / input.float(def, ...) / input(def, ...)   -> strategy parameter
  long_signal = <bool expr>  and/or  short_signal = <bool expr>     (REQUIRED: defines the screener condition)
  alertcondition(<bool expr>, ...)  -> treated as long_signal if long_signal is absent
  OPTIONAL risk inputs: stop_atr = input.float(1.5)  (stop = close -/+ stop_atr * ATR14; default 1.5)
                        target_r = input.float(2.0)  (target = entry +/- target_r * risk; default 2.0; 0 = none)
  series: open high low close volume hl2 hlc3 ohlc4 ; history reference x[n] with integer literal n
  ta.ema ta.sma ta.rsi ta.atr(n) ta.highest ta.lowest ta.change ta.stdev ta.crossover ta.crossunder
  operators: + - * /  > < >= <= == !=  and or not  ( )   numeric literals, true/false
  comments //...
NOT SUPPORTED (rejected with a diagnostic): strategy.* orders, request.security, var/varip, if/for/while blocks,
  user functions (=>), arrays/matrices/maps, ternary ?:, plot*/label*/line*/box* (ignored with a warning),
  barstate.*, timeframe.*, na()/nz(), math.*, string functions, tuple assignment.
SEMANTICS: evaluated on the LAST COMPLETED BAR (equivalent to Pine with `barstate.isconfirmed`); `na` comparisons
are false in Pine and MISSING here (both mean "not qualified"). Indicator values converge after warm-up but are
not guaranteed bit-identical to TradingView. This is NOT a general TradingView-compatible runtime.
"""
from __future__ import annotations

import re

TOKEN = re.compile(r"\s*(?:(\d+\.\d*|\.\d+|\d+)|([A-Za-z_][A-Za-z0-9_.]*)|(>=|<=|==|!=|:=|=>|[-+*/()<>\[\],=?:]))")
SERIES = {"open", "high", "low", "close", "volume", "hl2", "hlc3", "ohlc4"}
TA1 = {"ta.ema": "ema", "ta.sma": "sma", "ta.rsi": "rsi", "ta.highest": "highest", "ta.lowest": "lowest", "ta.change": "change",
       "ta.stdev": "stdev"}
UNSUPPORTED = ["strategy.entry", "strategy.exit", "strategy.close", "strategy.order", "request.security", "request.", "barstate.",
               "timeframe.", "math.", "array.", "matrix.", "map.", "str.", "nz(", "na(", "var ", "varip ", "=>"]
IGNORED_CALLS = ("plot", "plotshape", "plotchar", "bgcolor", "label.", "line.", "box.", "hline", "fill", "table.")


class PineError(ValueError):
    pass


class _Parser:
    def __init__(self, text: str, env: dict, params: dict, line: int):
        self.toks = self._lex(text, line)
        self.i, self.env, self.params, self.line = 0, env, params, line

    @staticmethod
    def _lex(text, line):
        out, pos = [], 0
        while pos < len(text):
            if text[pos:].strip() == "":
                break
            m = TOKEN.match(text, pos)
            if not m or m.end() == pos:
                raise PineError(f"line {line}: unexpected character '{text[pos:].strip()[:1]}'")
            out.append(m.group(1) and ("num", float(m.group(1))) or m.group(2) and ("id", m.group(2)) or ("op", m.group(3)))
            pos = m.end()
        return out

    def peek(self):
        return self.toks[self.i] if self.i < len(self.toks) else (None, None)

    def take(self, val=None):
        t = self.peek()
        if val is not None and t[1] != val:
            raise PineError(f"line {self.line}: expected '{val}' but found '{t[1]}'")
        self.i += 1
        return t

    def parse(self):
        e = self.expr(0)
        if self.i != len(self.toks):
            raise PineError(f"line {self.line}: unexpected '{self.peek()[1]}'")
        return e

    PREC = {"or": 1, "and": 2, "==": 3, "!=": 3, ">": 4, "<": 4, ">=": 4, "<=": 4, "+": 5, "-": 5, "*": 6, "/": 6}

    def expr(self, minp):
        left = self.unary()
        while True:
            k, v = self.peek()
            if v == "?":
                raise PineError(f"line {self.line}: ternary '?:' is not supported")
            if v not in self.PREC or self.PREC[v] <= minp - 1 or self.PREC[v] < minp:
                return left
            self.take()
            right = self.expr(self.PREC[v] + 1)
            if v in ("and", "or"):
                left = {"t": v, "args": [left, right]}
            elif v in ("+", "-", "*", "/"):
                left = {"t": "bin", "op": v, "a": left, "b": right}
            else:
                left = {"t": "cmp", "op": v, "a": left, "b": right}

    def unary(self):
        k, v = self.peek()
        if v == "not":
            self.take()
            return {"t": "not", "a": self.unary()}
        if v == "-":
            self.take()
            return {"t": "bin", "op": "-", "a": {"t": "num", "v": 0.0}, "b": self.unary()}
        return self.postfix(self.primary())

    def postfix(self, e):
        while self.peek()[1] == "[":
            self.take("[")
            k, v = self.take()
            if k != "num" or v != int(v):
                raise PineError(f"line {self.line}: history reference must be an integer literal")
            self.take("]")
            e = {"t": "shift", "a": e, "n": int(v)}
        return e

    def args(self):
        self.take("(")
        out = []
        if self.peek()[1] != ")":
            while True:
                if self.peek()[0] == "id" and self.i + 1 < len(self.toks) and self.toks[self.i + 1][1] == "=":
                    raise PineError(f"line {self.line}: named arguments are not supported in expressions")
                out.append(self.expr(0))
                if self.peek()[1] == ",":
                    self.take(",")
                    continue
                break
        self.take(")")
        return out

    def period(self, e):
        if e.get("t") == "num" and e["v"] == int(e["v"]) and e["v"] >= 1:
            return int(e["v"])
        if e.get("t") == "param":
            return e
        raise PineError(f"line {self.line}: length must be an integer literal or input")

    def primary(self):
        k, v = self.take()
        if k == "num":
            return {"t": "num", "v": float(v)}
        if v == "(":
            e = self.expr(0)
            self.take(")")
            return e
        if k != "id":
            raise PineError(f"line {self.line}: unexpected '{v}'")
        if v in ("true", "false"):
            return {"t": "cmp", "op": "==", "a": {"t": "num", "v": 1.0}, "b": {"t": "num", "v": 1.0 if v == "true" else 0.0}}
        if self.peek()[1] == "(":
            a = self.args()
            if v in TA1:
                if len(a) != 2:
                    raise PineError(f"line {self.line}: {v}(source, length) expects 2 arguments")
                return {"t": "ind", "fn": TA1[v], "src": a[0], "n": self.period(a[1])}
            if v == "ta.atr":
                if len(a) != 1:
                    raise PineError(f"line {self.line}: ta.atr(length) expects 1 argument")
                return {"t": "ind", "fn": "atr", "src": None, "n": self.period(a[0])}
            if v in ("ta.crossover", "ta.crossunder"):
                return {"t": "cross", "dir": "over" if v.endswith("over") else "under", "a": a[0], "b": a[1]}
            raise PineError(f"line {self.line}: function '{v}' is not in the supported Pine subset")
        if v in SERIES:
            return {"t": "field", "name": v}
        if v in self.env:
            return self.env[v]
        if v in self.params:
            return {"t": "param", "name": v}
        raise PineError(f"line {self.line}: unknown identifier '{v}'")


def translate_pine(src: str) -> tuple[dict | None, list[str], list[str]]:
    errs, warns = [], []
    lines = src.splitlines()
    ver = next((re.match(r"\s*//\s*@version\s*=\s*(\d+)", l_) for l_ in lines if "@version" in l_), None)
    if not ver:
        return None, ["missing '//@version=5' (or 6) declaration"], []
    if ver.group(1) not in ("5", "6"):
        return None, [f"Pine v{ver.group(1)} not supported (v5/v6 subset only)"], []
    env, params, signals = {}, {}, {}
    for no, raw in enumerate(lines, 1):
        code = re.sub(r"//.*$", "", raw).rstrip()
        if not code.strip():
            continue
        if raw[:1] in (" ", "\t") and code.strip():
            errs.append(f"line {no}: indented blocks (if/for/functions) are not supported")
            continue
        for u in UNSUPPORTED:
            if u in code:
                errs.append(f"line {no}: '{u.strip()}' is not supported in the Pine subset")
                break
        else:
            s = code.strip()
            if re.match(r"(indicator|strategy|library)\s*\(", s):
                continue
            if s.startswith(IGNORED_CALLS):
                warns.append(f"line {no}: '{s.split('(')[0]}' ignored (visual output has no screener meaning)")
                continue
            if re.match(r"(if|for|while|switch)\b", s):
                errs.append(f"line {no}: control flow '{s.split()[0]}' is not supported")
                continue
            if ":=" in s:
                errs.append(f"line {no}: reassignment ':=' is not supported")
                continue
            m = re.match(r"alertcondition\s*\((.*)\)\s*$", s)
            try:
                if m:
                    first = _split_top(m.group(1))[0]
                    signals.setdefault("long_signal", _Parser(first, env, params, no).parse())
                    continue
                m = re.match(r"([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.+)$", s)
                if not m:
                    errs.append(f"line {no}: unsupported statement")
                    continue
                name, rhs = m.group(1), m.group(2).strip()
                mi = re.match(r"input(?:\.(int|float|bool))?\s*\((.*)\)$", rhs)
                if mi:
                    first = _split_top(mi.group(2))[0].strip()
                    first = re.sub(r"^defval\s*=\s*", "", first)
                    try:
                        val = float(first)
                    except ValueError:
                        errs.append(f"line {no}: input default must be a numeric literal")
                        continue
                    params[name] = int(val) if mi.group(1) == "int" else val
                    continue
                e = _Parser(rhs, env, params, no).parse()
                if name in ("long_signal", "short_signal"):
                    signals[name] = e
                else:
                    env[name] = e
            except PineError as pe:
                errs.append(str(pe))
    if not signals and not errs:
        errs.append("define long_signal = <condition> and/or short_signal = <condition> (or alertcondition(...))")
    if errs:
        return None, errs, warns
    ir = {"ir_version": 1, "classification": "pine", "_params": params,
          "long": {"type": "group", "logic": "AND", "children": [{"type": "rule", "name": "long_signal", "expr": signals["long_signal"]}]}
          if "long_signal" in signals else None,
          "short": {"type": "group", "logic": "AND", "children": [{"type": "rule", "name": "short_signal", "expr": signals["short_signal"]}]}
          if "short_signal" in signals else None,
          "stop": {"method": "atr", "mult": float(params.get("stop_atr", 1.5))},
          "target": {"r": float(params.get("target_r", 2.0))} if float(params.get("target_r", 2.0)) > 0 else None}
    if not (0 < ir["stop"]["mult"] <= 10):
        return None, ["stop_atr must be in (0, 10]"], warns
    for side in ("long", "short"):
        if ir[side]:
            ex = ir[side]["children"][0]["expr"]
            if ex.get("t") not in ("cmp", "cross", "and", "or", "not"):
                return None, [f"{side}_signal must be a boolean condition"], warns
    return ir, [], warns


def _split_top(s: str) -> list[str]:
    out, depth, cur = [], 0, ""
    for ch in s:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            out.append(cur)
            cur = ""
        else:
            cur += ch
    out.append(cur)
    return out


TEMPLATES = {
    "EMA trend (v5)": """//@version=5
indicator("AMW EMA trend")
fast = input.int(20)
slow = input.int(50)
stop_atr = input.float(2.0)
target_r = input.float(2.0)
ef = ta.ema(close, fast)
es = ta.ema(close, slow)
long_signal = close > ef and ef > es
short_signal = close < ef and ef < es
""",
    "EMA crossover (v5)": """//@version=5
indicator("AMW EMA crossover")
fast = input.int(9)
slow = input.int(21)
long_signal = ta.crossover(ta.ema(close, fast), ta.ema(close, slow))
short_signal = ta.crossunder(ta.ema(close, fast), ta.ema(close, slow))
""",
    "Donchian breakout + volume (v6)": """//@version=6
indicator("AMW breakout")
len = input.int(20)
long_signal = close > ta.highest(high, len)[1] and volume > ta.sma(volume, 20) * 1.5
""",
    "RSI pullback in uptrend (v5)": """//@version=5
indicator("AMW RSI pullback")
r = ta.rsi(close, 14)
long_signal = close > ta.ema(close, 50) and r[1] < 40 and r > r[1]
""",
    "RSI oversold reversal (v5)": """//@version=5
indicator("AMW RSI reversal")
r = ta.rsi(close, 14)
long_signal = ta.crossover(r, 30) and close > open
short_signal = ta.crossunder(r, 70) and close < open
""",
    "Volume spike (v5)": """//@version=5
indicator("AMW volume spike")
mult = input.float(2.0)
long_signal = volume > ta.sma(volume, 20) * mult and close > open and close > high[1]
""",
}
