"""AMW Strategy Lab worker for Python, Lua and SQL. Runs INSIDE the process sandbox (see sandbox.py).

stdin : {"language": "python"|"lua"|"sql", "mode": "check"|"run", "source": str, "contexts": [ctx...],
         "limits": {"lua_mem_mb": int, "lua_instructions": int, "sql_steps": int, "sql_rows": int}}
stdout: one line "@@AMW@@" + JSON {"runtime_version": str, "results": [{"raw": obj|null, "error": str|null, "logs": str}],
                                    "check": {"ok": bool, "errors": [str]}}
"""
import io
import json
import math
import sys

ALLOWED_IMPORTS = {"math", "statistics"}


# ------------------------------------------------------------------ helper library exposed as `amw`
class _AMW:
    """Series helpers. Series are lists (oldest -> newest); None = missing. Index -1 = last completed bar."""

    @staticmethod
    def isna(x):
        return x is None or (isinstance(x, float) and math.isnan(x))

    @staticmethod
    def last(series, n=0):
        """Value n bars before the last completed bar (n=0 -> last)."""
        return series[-1 - n] if len(series) > n else None

    prev = last

    @staticmethod
    def highest(series, n, offset=0):
        w = series[len(series) - offset - n: len(series) - offset] if offset else series[-n:]
        w = [v for v in w if v is not None]
        return max(w) if len(w) == n else None

    @staticmethod
    def lowest(series, n, offset=0):
        w = series[len(series) - offset - n: len(series) - offset] if offset else series[-n:]
        w = [v for v in w if v is not None]
        return min(w) if len(w) == n else None

    @staticmethod
    def crossover(a, b):
        if len(a) < 2 or len(b) < 2 or None in (a[-1], a[-2], b[-1], b[-2]):
            return None
        return a[-1] > b[-1] and a[-2] <= b[-2]

    @staticmethod
    def crossunder(a, b):
        if len(a) < 2 or len(b) < 2 or None in (a[-1], a[-2], b[-1], b[-2]):
            return None
        return a[-1] < b[-1] and a[-2] >= b[-2]

    # full-series indicators matching the host (pandas) definitions; leading missing values are skipped
    @staticmethod
    def ema(src, n):
        k, out, val, cnt = 2.0 / (n + 1), [], None, 0
        for v in src:
            if v is None:
                out.append(val if (val is not None and cnt >= n) else None)
                continue
            val = v if val is None else val + k * (v - val)
            cnt += 1
            out.append(val if cnt >= n else None)
        return out

    @staticmethod
    def _wilder(src, n):
        a, out, val, cnt = 1.0 / n, [], None, 0
        for v in src:
            if v is None:
                out.append(val if (val is not None and cnt >= n) else None)
                continue
            val = v if val is None else val + a * (v - val)
            cnt += 1
            out.append(val if cnt >= n else None)
        return out

    @staticmethod
    def sma(src, n):
        out = []
        for i in range(len(src)):
            w = src[max(0, i - n + 1): i + 1]
            out.append(sum(w) / n if len(w) == n and None not in w else None)
        return out

    @staticmethod
    def rsi(src, n=14):
        up, dn = [None], [None]
        for i in range(1, len(src)):
            if src[i] is None or src[i - 1] is None:
                up.append(None), dn.append(None)
            else:
                d = src[i] - src[i - 1]
                up.append(max(d, 0.0)), dn.append(max(-d, 0.0))
        au, ad = _AMW._wilder(up, n), _AMW._wilder(dn, n)
        out = []
        for u, d in zip(au, ad):
            if u is None or d is None:
                out.append(None)
            elif u == 0 and d == 0:
                out.append(50.0)
            elif d == 0:
                out.append(100.0)
            else:
                out.append(100 - 100 / (1 + u / d))
        return out

    @staticmethod
    def atr(high, low, close, n=14):
        tr = []
        for i in range(len(close)):
            if None in (high[i], low[i]) or (i > 0 and close[i - 1] is None):
                tr.append(None)
            elif i == 0:
                tr.append(high[i] - low[i])
            else:
                tr.append(max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1])))
        return _AMW._wilder(tr, n)

    # element-wise tri-state helpers used by code exported from the visual builder
    @staticmethod
    def num(v, n):
        return [float(v)] * n

    @staticmethod
    def shift(a, k):
        return ([None] * min(k, len(a)) + a[:len(a) - k]) if k else a

    @staticmethod
    def binop(a, op, b):
        out = []
        for x, y in zip(a, b):
            if x is None or y is None or (op == "/" and y == 0):
                out.append(None)
            else:
                out.append(x + y if op == "+" else x - y if op == "-" else x * y if op == "*" else x / y)
        return out

    @staticmethod
    def cmp(a, op, b):
        out = []
        for x, y in zip(a, b):
            if x is None or y is None:
                out.append(None)
            elif op in ("==", "!="):
                eq = abs(x - y) <= max(1e-12, 1e-9 * abs(y))
                out.append(eq if op == "==" else not eq)
            else:
                out.append(x > y if op == ">" else x < y if op == "<" else x >= y if op == ">=" else x <= y)
        return out

    @staticmethod
    def cross(a, b, d):
        out = [None]
        for i in range(1, len(a)):
            if None in (a[i], b[i], a[i - 1], b[i - 1]):
                out.append(None)
            else:
                out.append(a[i] > b[i] and a[i - 1] <= b[i - 1] if d == "over" else a[i] < b[i] and a[i - 1] >= b[i - 1])
        return out

    @staticmethod
    def and3(*xs):
        return [False if any(v is False for v in c) else (None if any(v is None for v in c) else True) for c in zip(*xs)]

    @staticmethod
    def or3(*xs):
        return [True if any(v is True for v in c) else (None if any(v is None for v in c) else False) for c in zip(*xs)]

    @staticmethod
    def not3(a):
        return [None if v is None else (not v) for v in a]

    @staticmethod
    def rolling(src, n, fn):
        out = []
        for i in range(len(src)):
            w = [v for v in src[max(0, i - n + 1): i + 1] if v is not None]
            out.append(fn(w) if len(w) == n else None)
        return out


def _safe_import(name, globals=None, locals=None, fromlist=(), level=0):
    root = name.split(".")[0]
    if root not in ALLOWED_IMPORTS:
        raise ImportError(f"import of '{name}' is not allowed (allowed: {sorted(ALLOWED_IMPORTS)})")
    return __import__(name, globals, locals, fromlist, level)


def _audit_hook(event, args):
    blocked_prefix = ("socket.", "subprocess.", "os.system", "os.exec", "os.spawn", "os.posix_spawn", "os.fork", "os.kill",
                      "ctypes.", "os.listdir", "os.scandir", "os.remove", "os.rename", "os.mkdir", "shutil.", "urllib.",
                      "http.", "sys._getframe", "gc.get_", "builtins.input", "code.", "pty.", "winreg.", "msvcrt.")
    if event == "open" or event.startswith(blocked_prefix):
        raise PermissionError(f"blocked by AMW sandbox: {event}")
    if event == "import" and args[0].split(".")[0] not in ALLOWED_IMPORTS | {"encodings", "_strptime"}:
        raise PermissionError(f"blocked import: {args[0]}")
    if event in ("exec", "compile") and _HOOK_STRICT[0]:
        if event == "exec" and args and id(args[0]) in _ALLOWED_CODE:
            return
        raise PermissionError(f"blocked by AMW sandbox: {event}")


_HOOK_STRICT = [False]
_ALLOWED_CODE = set()
SAFE_BUILTINS = {k: __builtins__[k] if isinstance(__builtins__, dict) else getattr(__builtins__, k) for k in (
    "abs", "all", "any", "bool", "dict", "enumerate", "filter", "float", "int", "isinstance", "len", "list", "map", "max",
    "min", "pow", "range", "reversed", "round", "set", "sorted", "str", "sum", "tuple", "zip", "ValueError", "TypeError",
    "KeyError", "IndexError", "ZeroDivisionError", "Exception", "None", "True", "False", "print")}


def run_python(source, contexts, mode):
    try:
        code = compile(source, "<strategy>", "exec")
    except SyntaxError as e:
        return {"ok": False, "errors": [f"line {e.lineno}: {e.msg}"]}, []
    _ALLOWED_CODE.add(id(code))
    builtins = dict(SAFE_BUILTINS)
    builtins["__import__"] = _safe_import
    results = []
    _HOOK_STRICT[0] = True
    for ctx in ([{}] if mode == "check" else contexts):
        buf = io.StringIO()
        g = {"__builtins__": {**builtins, "print": lambda *a, **k: print(*a, file=buf)}, "amw": _AMW, "__name__": "strategy"}
        try:
            exec(code, g)
            fn = g.get("evaluate")
            if not callable(fn):
                return {"ok": False, "errors": ["define a function evaluate(ctx)"]}, []
            if mode == "check":
                return {"ok": True, "errors": []}, []
            raw = fn(ctx)
            json.dumps(raw)
            results.append({"raw": raw, "error": None, "logs": buf.getvalue()[-4000:]})
        except BaseException as e:  # noqa: BLE001 - report user errors, never crash the batch
            results.append({"raw": None, "error": f"{type(e).__name__}: {str(e)[:300]}", "logs": buf.getvalue()[-4000:]})
    return {"ok": True, "errors": []}, results


# ------------------------------------------------------------------ Lua
def run_lua(source, contexts, mode, limits):
    import lupa
    from lupa import lua54
    lua = lua54.LuaRuntime(register_eval=False, register_builtins=False, unpack_returned_tuples=False,
                           max_memory=int(limits.get("lua_mem_mb", 64)) * 2**20)
    n_instr = int(limits.get("lua_instructions", 50_000_000))
    lua.execute(f"local sh=debug.sethook; sh(function() error('instruction limit exceeded') end, '', {n_instr})")
    lua.execute("""
      os=nil; io=nil; package=nil; require=nil; debug=nil; dofile=nil; loadfile=nil; load=nil; loadstring=nil;
      collectgarbage=nil; coroutine=nil; string.dump=nil; rawset=nil; rawget=nil; rawequal=nil; setmetatable=nil; getmetatable=nil
      NA = 0/0
      amw = {}
      function amw.isna(x) return x == nil or x ~= x end
      function amw.last(s, n) n = n or 0; return s[#s - n] end
      amw.prev = amw.last
      function amw.highest(s, n) local m=nil; for i=#s-n+1,#s do local v=s[i]; if v==nil or v~=v then return NA end; if m==nil or v>m then m=v end end; return m end
      function amw.lowest(s, n) local m=nil; for i=#s-n+1,#s do local v=s[i]; if v==nil or v~=v then return NA end; if m==nil or v<m then m=v end end; return m end
      function amw.crossover(a, b) local x1,x0,y1,y0=a[#a],a[#a-1],b[#b],b[#b-1]; if amw.isna(x1) or amw.isna(x0) or amw.isna(y1) or amw.isna(y0) then return nil end; return x1>y1 and x0<=y0 end
      function amw.crossunder(a, b) local x1,x0,y1,y0=a[#a],a[#a-1],b[#b],b[#b-1]; if amw.isna(x1) or amw.isna(x0) or amw.isna(y1) or amw.isna(y0) then return nil end; return x1<y1 and x0>=y0 end
    """)
    try:
        lua.execute(source)
    except lupa.LuaError as e:
        return {"ok": False, "errors": [str(e)[:300]]}, []
    fn = lua.globals().evaluate
    if fn is None:
        return {"ok": False, "errors": ["define a function evaluate(ctx)"]}, []
    if mode == "check":
        return {"ok": True, "errors": []}, []
    nan = float("nan")

    def to_lua(o):
        if isinstance(o, dict):
            return lua.table_from({k: to_lua(v) for k, v in o.items()})
        if isinstance(o, list):
            return lua.table_from([to_lua(v) for v in o])
        return nan if o is None else o

    def from_lua(o):
        if lua54.lua_type(o) == "table":
            keys = list(o.keys())
            if keys and all(isinstance(k, int) for k in keys):
                return [from_lua(o[i]) for i in range(1, len(keys) + 1)]
            return {str(k): from_lua(o[k]) for k in keys}
        if isinstance(o, float) and math.isnan(o):
            return None
        return o
    results = []
    for ctx in contexts:
        try:
            raw = from_lua(fn(to_lua(ctx)))
            results.append({"raw": raw, "error": None, "logs": ""})
        except Exception as e:  # noqa: BLE001
            results.append({"raw": None, "error": f"LuaError: {str(e)[:300]}", "logs": ""})
    return {"ok": True, "errors": []}, results


# ------------------------------------------------------------------ SQL (read-only, approved views only)
SQL_COLS = ["open", "high", "low", "close", "volume", "ema20", "ema50", "ema200", "sma20", "sma50", "rsi14", "macd_hist",
            "atr14", "adx", "rvol"]
SQL_FUNCS = {"abs", "min", "max", "avg", "sum", "count", "round", "coalesce", "ifnull", "nullif", "lag", "lead",
             "row_number", "first_value", "last_value", "upper", "lower", "length", "substr", "iif"}


def run_sql(source, contexts, mode, limits, params):
    import sqlite3
    results = []
    for ctx in ([None] if mode == "check" else contexts):
        con = sqlite3.connect(":memory:")
        cols = ", ".join(f"{c} REAL" for c in SQL_COLS)
        con.execute(f"CREATE TABLE _bars (symbol TEXT, timeframe TEXT, bar_index INTEGER, ts TEXT, {cols})")
        if ctx is not None:
            s, m = ctx["series"], ctx["meta"]
            rows = [(m.get("symbol"), m.get("timeframe"), i, s["time"][i], *[s[c][i] for c in SQL_COLS]) for i in range(len(s["close"]))]
            con.executemany(f"INSERT INTO _bars VALUES ({','.join('?' * (4 + len(SQL_COLS)))})", rows)
        con.execute("CREATE VIEW bars AS SELECT * FROM _bars")
        con.execute("CREATE VIEW latest AS SELECT * FROM _bars WHERE bar_index = (SELECT MAX(bar_index) FROM _bars)")

        def auth(action, a1, a2, db, src):
            if action == sqlite3.SQLITE_SELECT:
                return sqlite3.SQLITE_OK
            if action == sqlite3.SQLITE_READ and a1 in ("bars", "latest"):
                return sqlite3.SQLITE_OK
            if action == sqlite3.SQLITE_READ and a1 == "_bars":       # only through the approved views
                return sqlite3.SQLITE_OK if src in ("bars", "latest") else sqlite3.SQLITE_DENY
            if action == sqlite3.SQLITE_FUNCTION and (a2 or "").lower() in SQL_FUNCS:
                return sqlite3.SQLITE_OK
            return sqlite3.SQLITE_DENY
        con.set_authorizer(auth)
        steps = [0]
        max_steps = int(limits.get("sql_steps", 5_000_000))

        def prog():
            steps[0] += 1000
            return 1 if steps[0] > max_steps else 0
        con.set_progress_handler(prog, 1000)
        try:
            cur = con.execute(source, params or {})
            if mode == "check":
                con.close()
                return {"ok": True, "errors": []}, []
            names = [d[0].lower() for d in cur.description or []]
            rows = cur.fetchmany(int(limits.get("sql_rows", 1000)))
            if "symbol" not in names:
                results.append({"raw": None, "error": "query must return a 'symbol' column", "logs": ""})
                continue
            recs = [dict(zip(names, r)) for r in rows]
            if recs:
                bias = str(recs[0].get("bias") or "long").lower()
                raw = {"status": "qualified", "bias": bias if bias in ("long", "short") else "neutral", "classification": "sql",
                       "rules": [{"name": "query returned the symbol", "passed": True, "detail": f"{len(recs)} row(s)"}],
                       "explanation": str(recs[0].get("note") or "returned by SQL screen")[:500]}
            else:
                raw = {"status": "rejected", "bias": "neutral", "classification": "sql",
                       "rules": [{"name": "query returned the symbol", "passed": False, "detail": "0 rows"}],
                       "explanation": "not returned by SQL screen"}
            results.append({"raw": raw, "error": None, "logs": ""})
        except sqlite3.Error as e:
            msg = "query interrupted: step limit exceeded" if "interrupted" in str(e) else f"SQL error: {e}"
            if mode == "check":
                return {"ok": False, "errors": [msg]}, []
            results.append({"raw": None, "error": msg, "logs": ""})
        finally:
            con.close()
    return {"ok": True, "errors": []}, results


def main():
    req = json.loads(sys.stdin.read())
    lang, mode = req["language"], req.get("mode", "run")
    limits = req.get("limits", {})
    if lang == "lua":
        import lupa
        from lupa import lua54  # preload + warm up before the audit hook (lupa imports helpers lazily)
        _w = lua54.LuaRuntime(register_eval=False)
        _f = _w.eval("function(t) return {a=t[1]} end")
        _r = _f(_w.table_from([1.0]))
        lua54.lua_type(_r), list(_r.keys())
        ver = f"Lua 5.4 via lupa {lupa.__version__}"
    elif lang == "sql":
        import sqlite3
        ver = f"SQLite {sqlite3.sqlite_version}"
    else:
        ver = f"Python {sys.version.split()[0]}"
    import statistics  # noqa: F401  - preload allow-listed modules before the hook blocks file access
    sys.addaudithook(_audit_hook)
    if lang == "python":
        check, res = run_python(req["source"], req.get("contexts", []), mode)
    elif lang == "lua":
        check, res = run_lua(req["source"], req.get("contexts", []), mode, limits)
    elif lang == "sql":
        check, res = run_sql(req["source"], req.get("contexts", []), mode, limits, req.get("sql_params"))
    else:
        check, res = {"ok": False, "errors": [f"unsupported language {lang}"]}, []
    sys.__stdout__.write("@@AMW@@" + json.dumps({"runtime_version": ver, "check": check, "results": res}, default=str) + "\n")


if __name__ == "__main__":
    main()
