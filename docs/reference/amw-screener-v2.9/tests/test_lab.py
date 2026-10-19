"""Strategy Lab tests: all eight authoring modes, sandbox security, schema validation, cross-language consistency,
look-ahead prevention, cancellation, quotas, scanner + backtest integration. SYNTHETIC fixtures only; no network."""
import os
import threading
import time

import numpy as np
import pandas as pd
import pytest

from src.analysis.context import analyze_tf
from src.config import default_settings
from src.lab import engine, sandbox
from src.lab.contract import StrategySpec, build_context, capabilities, resolve_params, validate_result
from src.lab.ir import TEMPLATES as IR_T, NotExportable, export_python, lookback_required, render_strategy, validate_strategy_ir
from src.lab.pine import TEMPLATES as PINE_T, translate_pine
from src.lab.sqlcheck import TEMPLATES as SQL_T, check_sql
from src.lab.templates import CODE_TEMPLATES
from tests.conftest import make_ohlc

SB = sandbox.sandbox_status()["verified"]


@pytest.fixture(autouse=True)
def _all_languages(monkeypatch):
    monkeypatch.delenv("AMW_LAB_LANGUAGES", raising=False)
need_sb = pytest.mark.skipif(not SB, reason="process sandbox not available on this platform (execution disabled by design)")
CFG = default_settings()
META = {"symbol": "T", "ticker": "T.NS", "exchange": "NSE", "timeframe": "1D", "timezone": "Asia/Kolkata",
        "provider": "fixture", "data_kind": "simulated", "data_status": "OK"}


def _ctx(closes, params=None, vol=None, htf=None):
    t = analyze_tf(make_ohlc(closes, spread=0.5, vol=1000.0 if vol is None else vol), "1D", CFG)
    return build_context(t, params or {}, META, htf, 200)


UP = list(np.linspace(100, 200, 260) + np.sin(np.arange(260)) * 1.5)
DN = list(np.linspace(200, 100, 260) + np.sin(np.arange(260)) * 1.5)

# the same "EMA stack" rule expressed in every authoring mode (cross-language consistency fixture v1)
EQUIV = {
    "python": CODE_TEMPLATES["python"]["Trend (EMA stack)"],
    "javascript": CODE_TEMPLATES["javascript"]["Trend (EMA stack)"],
    "typescript": CODE_TEMPLATES["typescript"]["Trend (EMA stack)"],
    "lua": CODE_TEMPLATES["lua"]["Trend (EMA stack)"],
    "r": CODE_TEMPLATES["r"]["Trend (EMA stack)"],
    "sql": "SELECT symbol, 'long' AS bias FROM latest WHERE close > ema20 AND ema20 > ema50 AND ema50 > ema200",
    "pine": None,
    "visual": None,
}
VIS = {"ir_version": 1, "classification": "trend", "long": {"type": "group", "logic": "AND", "children": [
    {"type": "rule", "name": "c>e20", "expr": {"t": "cmp", "op": ">", "a": {"t": "field", "name": "close"}, "b": {"t": "field", "name": "ema20"}}},
    {"type": "rule", "name": "e20>e50", "expr": {"t": "cmp", "op": ">", "a": {"t": "field", "name": "ema20"}, "b": {"t": "field", "name": "ema50"}}},
    {"type": "rule", "name": "e50>e200", "expr": {"t": "cmp", "op": ">", "a": {"t": "field", "name": "ema50"}, "b": {"t": "field", "name": "ema200"}}}]},
    "short": None, "stop": {"method": "atr", "mult": 2.0}, "target": {"r": 2.0}}
PINE_EQ = "//@version=5\nindicator(\"x\")\nlong_signal = close > ta.ema(close, 20) and ta.ema(close, 20) > ta.ema(close, 50) and ta.ema(close, 50) > ta.ema(close, 200)\n"


def run(lang, src=None, ir=None, ctxs=None, **spec):
    sp = StrategySpec(language=lang, **spec)
    return engine.execute(lang, src, ir, sp, ctxs)


# ---------------------------------------------------------------- contract
def test_result_schema_validation():
    ok, e, _ = validate_result({"status": "qualified", "bias": "long", "rules": [{"name": "a", "passed": True}], "stop": 90}, 100)
    assert ok and not e
    assert validate_result({"status": "maybe"}, 100)[0] is None
    assert validate_result({"status": "qualified", "stop": float("nan")}, 100)[0] is None
    assert validate_result({"status": "qualified", "bias": "long", "stop": 110}, 100)[0] is None   # stop on wrong side
    assert validate_result("qualified", 100)[0] is None
    assert validate_result({"status": "rejected", "rules": [{"name": "x" * 300, "passed": True}]}, 100)[0] is None


def test_spec_and_params():
    with pytest.raises(Exception):
        StrategySpec(language="python", permissions=["network"])
    sp = StrategySpec(language="python", params=[{"name": "n", "type": "int", "default": 20, "min": 5, "max": 50}])
    assert resolve_params(sp, {"n": "30"}) == {"n": 30}
    with pytest.raises(ValueError):
        resolve_params(sp, {"n": 99})


def test_context_is_completed_bars_and_missing_is_null():
    c = _ctx(UP)
    assert c["meta"]["last_completed_bar"] == c["series"]["time"][-1]
    assert c["series"]["ema200"][0] is None and c["series"]["close"][0] is not None
    assert all(s["confirmed_index"] <= len(c["series"]["close"]) - 1 for s in c["structure"]["swings"])


def test_capability_matrix_is_detected():
    cap = capabilities()
    assert set(cap) >= {"python", "pine", "javascript", "typescript", "sql", "lua", "r", "visual"}
    assert cap["visual"]["native_execution"] is True
    assert "subset" in str(cap["pine"]["native_execution"])
    if not SB:
        assert cap["python"]["native_execution"] is False


# ---------------------------------------------------------------- visual + pine (host engine; run everywhere)
def test_visual_ir_validation_depth_and_render():
    assert validate_strategy_ir(VIS) == []
    deep = {"type": "rule", "name": "x", "expr": VIS["long"]["children"][0]["expr"]}
    for _ in range(8):
        deep = {"type": "group", "logic": "AND", "children": [deep]}
    assert any("depth" in e for e in validate_strategy_ir({**VIS, "long": deep}))
    assert any("unknown field" in e for e in validate_strategy_ir({**VIS, "long": {"type": "group", "logic": "AND", "children": [
        {"type": "rule", "name": "x", "expr": {"t": "cmp", "op": ">", "a": {"t": "field", "name": "secret"}, "b": {"t": "num", "v": 1}}}]}}))
    assert "LONG when" in render_strategy(VIS)
    assert lookback_required(IR_T["Volume expansion"]) >= 60


def test_visual_templates_all_valid_and_missing_fails_closed():
    for name, ir in IR_T.items():
        assert validate_strategy_ir(ir) == [], name
    short = _ctx(UP[:30])          # EMA200 missing -> rule missing -> not qualified
    r = run("visual", ir=VIS, ctxs=[short])[0]
    assert r.ok and r.result["status"] == "rejected"
    assert any(x["passed"] is None for x in r.result["rules"])


def test_pine_subset_translation_and_rejections():
    for name, src in PINE_T.items():
        ir, errs, _ = translate_pine(src)
        assert ir is not None and not errs, (name, errs)
    for bad, frag in [("indicator('x')\nlong_signal = close > open", "version"),
                      ("//@version=5\nstrategy.entry('L', strategy.long)", "strategy.entry"),
                      ("//@version=5\nx = request.security(syminfo.tickerid, 'D', close)\nlong_signal = close > x", "request"),
                      ("//@version=5\nif close > open\n    x = 1", "not supported"),
                      ("//@version=5\nlong_signal = close > open ? 1 : 0", "ternary"),
                      ("//@version=5\nlong_signal = ta.vwma(close, 10) > close", "not in the supported"),
                      ("//@version=4\nlong_signal = close > open", "v4")]:
        ir, errs, _ = translate_pine(bad)
        assert ir is None and any(frag in e for e in errs), (bad, errs)


def test_pine_history_reference_semantics():
    src = "//@version=5\nindicator('x')\nlong_signal = close > close[1] and close[1] > close[2]\n"
    ir, _, _ = translate_pine(src)
    c_up = _ctx(list(np.linspace(100, 120, 80)))
    c_dn = _ctx(list(np.linspace(120, 100, 80)))
    assert run("pine", src, ctxs=[c_up])[0].result["status"] == "qualified"
    assert run("pine", src, ctxs=[c_dn])[0].result["status"] == "rejected"


# ---------------------------------------------------------------- SQL validation (host)
def test_sql_allowlist():
    for name, q in SQL_T.items():
        assert check_sql(q) == [], name
    for bad in ["DROP TABLE bars", "SELECT symbol FROM latest; DELETE FROM bars", "SELECT * FROM sqlite_master", "PRAGMA table_info(bars)",
                "SELECT load_extension('x') AS symbol FROM latest", "ATTACH DATABASE 'x' AS y", "SELECT symbol FROM _bars",
                "INSERT INTO bars VALUES (1)", "SELECT symbol FROM main.bars"]:
        assert check_sql(bad), bad


# ---------------------------------------------------------------- code runtimes (sandboxed)
@need_sb
@pytest.mark.parametrize("lang", ["python", "javascript", "typescript", "lua", "r", "sql"])
def test_runtime_executes_and_validates(lang):
    v = engine.validate(lang, EQUIV[lang], None, StrategySpec(language=lang))
    assert v["ok"], v
    rs = run(lang, EQUIV[lang], ctxs=[_ctx(UP), _ctx(DN)])
    assert [r.ok for r in rs] == [True, True], [r.errors for r in rs]
    assert rs[0].result["status"] == "qualified" and rs[1].result["status"] == "rejected"
    assert rs[0].signal_time == _ctx(UP)["meta"]["last_completed_bar"]


@need_sb
def test_cross_language_consistency():
    fixtures = {"up": _ctx(UP), "down": _ctx(DN), "short_history": _ctx(UP[:40]),
                "flat": _ctx(list(100 + np.sin(np.arange(260) / 5)))}
    expected = {k: run("visual", ir=VIS, ctxs=[c])[0].result["status"] for k, c in fixtures.items()}
    assert expected["up"] == "qualified" and expected["short_history"] == "rejected"
    for lang in ["python", "javascript", "typescript", "lua", "r", "sql", "pine"]:
        src = PINE_EQ if lang == "pine" else EQUIV[lang]
        got = {k: r.result["status"] for k, r in zip(fixtures, run(lang, src, ctxs=list(fixtures.values())))}
        assert got == expected, (lang, got, expected)


@need_sb
def test_python_export_equivalence():
    ir = {"ir_version": 1, "classification": "x", "long": {"type": "group", "logic": "AND", "children": [
        {"type": "rule", "name": "ema cross", "expr": {"t": "cross", "dir": "over", "a": {"t": "ind", "fn": "ema", "src": {"t": "field", "name": "close"}, "n": 5},
                                                       "b": {"t": "ind", "fn": "sma", "src": {"t": "field", "name": "close"}, "n": 10}}},
        {"type": "group", "logic": "OR", "children": [
            {"type": "rule", "name": "rsi", "expr": {"t": "cmp", "op": ">", "a": {"t": "ind", "fn": "rsi", "src": {"t": "field", "name": "close"}, "n": 14}, "b": {"t": "num", "v": 50}}},
            {"type": "rule", "name": "hh", "expr": {"t": "cmp", "op": ">", "a": {"t": "field", "name": "close"},
                                                    "b": {"t": "shift", "a": {"t": "ind", "fn": "highest", "src": {"t": "field", "name": "high"}, "n": 10}, "n": 1}}}]}]},
        "short": {"type": "group", "logic": "AND", "children": [
            {"type": "rule", "name": "below", "expr": {"t": "cmp", "op": "<", "a": {"t": "field", "name": "hl2"}, "b": {"t": "ind", "fn": "atr", "src": None, "n": 14}}}]},
        "stop": {"method": "atr", "mult": 1.5}, "target": {"r": 2.0}}
    code = export_python(ir)
    rng = np.random.default_rng(3)
    ctxs = [_ctx(list(100 + np.cumsum(rng.normal(0, 1, 120)))) for _ in range(12)]
    a = run("visual", ir=ir, ctxs=ctxs)
    b = run("python", code, ctxs=ctxs)
    for x, y in zip(a, b):
        assert y.ok, y.errors
        assert x.result["status"] == y.result["status"] and x.result["bias"] == y.result["bias"]
        assert [r["passed"] for r in x.result["rules"]] == [r["passed"] for r in y.result["rules"]]
        if x.result.get("stop") is not None:
            assert x.result["stop"] == pytest.approx(y.result["stop"], rel=1e-6)
    with pytest.raises(NotExportable):
        export_python(IR_T["Breakout and retest"])


@need_sb
def test_all_code_templates_execute():
    c = _ctx(UP, params={"min_adx": 20, "rsi_lo": 40, "rsi_hi": 70, "min_rvol": 1.5, "lookback": 20})
    for lang, d in CODE_TEMPLATES.items():
        for name, src in d.items():
            r = run(lang, src, ctxs=[c])[0]
            assert r.ok, (lang, name, r.errors)
    for name, src in SQL_T.items():
        assert run("sql", src, ctxs=[c])[0].ok, name


# ---------------------------------------------------------------- security
PY_ATTACKS = {
    "import os": "import os\ndef evaluate(ctx):\n    return {'status':'rejected'}\n",
    "open file": "def evaluate(ctx):\n    open('/etc/passwd').read()\n    return {'status':'rejected'}\n",
    "dunder escape": "def evaluate(ctx):\n    return ().__class__.__bases__[0].__subclasses__()\n",
    "eval": "def evaluate(ctx):\n    return eval('1')\n",
}


def test_python_static_rejections():
    for name, src in PY_ATTACKS.items():
        assert engine.static_check("python", src), name


@need_sb
def test_python_runtime_blocks_secrets_network_files():
    os.environ["AMW_TEST_SECRET"] = "s3cret"
    probe = ("def evaluate(ctx):\n"
             "    out = []\n"
             "    for name in ('socket', 'os', 'subprocess', 'ctypes', 'importlib'):\n"
             "        try:\n            __import__(name)\n            out.append(name)\n"
             "        except Exception:\n            pass\n"
             "    return {'status':'rejected','explanation': ','.join(out)}\n")
    assert engine.static_check("python", probe)             # '__import__' is rejected statically
    # direct runtime test (skip host static checks) - audit hook + restricted builtins must block imports and files
    w = engine._call("python", "run", "import socket\ndef evaluate(ctx):\n    return {'status':'rejected'}\n", [_ctx(UP)],
                     StrategySpec(language="python"), "t")
    assert w["results"][0]["error"] and "not allowed" in w["results"][0]["error"]
    w = engine._call("python", "run", "def evaluate(ctx):\n    return {'status':'rejected','explanation': str(open)}\n", [_ctx(UP)],
                     StrategySpec(language="python"), "t")
    assert "NameError" in (w["results"][0]["error"] or "")


@need_sb
def test_process_sandbox_isolation():
    st = sandbox.self_test()
    assert st["verified"] and st["checks"]["network"] is False and st["checks"]["secret_visible"] is False
    assert st["checks"]["write"] is False and st["checks"]["read_app_data"] is False and st["checks"]["uid"] == 65534
    assert st["checks"]["host_file_escape"] is False


@need_sb
def test_js_blocks_require_process_eval_and_random():
    for src in ["function evaluate(ctx){ return require('fs'); }", "function evaluate(ctx){ return process.env; }",
                "function evaluate(ctx){ return eval('1'); }"]:
        assert engine.static_check("javascript", src)
    w = engine._call("javascript", "run", "function evaluate(ctx){ return {status: 'rejected', explanation: typeof require + typeof process}; }",
                     [_ctx(UP)], StrategySpec(language="javascript"), "t")
    assert w["results"][0]["raw"]["explanation"] == "undefinedundefined"
    w = engine._call("javascript", "run", "function evaluate(ctx){ Math.random(); return {status:'rejected'}; }", [_ctx(UP)],
                     StrategySpec(language="javascript"), "t")
    assert "disabled" in w["results"][0]["error"]
    escapes = ["function evaluate(ctx){ return (new this.constructor.constructor('return 1'))(); }",
               "function evaluate(ctx){ return ctx.constructor.constructor('return typeof process')(); }",
               "function evaluate(ctx){ return console.log.constructor('return typeof process')(); }",
               "function evaluate(ctx){ return amw.last.constructor('return 1')(); }",
               "function evaluate(ctx){ return (function(){ return this })().constructor.constructor('return 1')(); }"]
    for code in escapes:      # realm escape / code generation attempts must all fail
        w = engine._call("javascript", "run", code, [_ctx(UP)], StrategySpec(language="javascript"), "t")
        assert w["results"][0]["error"], (code, w["results"][0])


@need_sb
def test_lua_removed_libraries_and_instruction_limit():
    for code in ["function evaluate(ctx) return {status=tostring(os.execute)} end", "function evaluate(ctx) return {status=tostring(io)} end",
                 "function evaluate(ctx) return {status=tostring(require)} end"]:
        w = engine._call("lua", "run", code, [_ctx(UP)], StrategySpec(language="lua"), "t")
        r = w["results"][0]
        assert r["error"] or r["raw"]["status"] == "nil", r
    w = engine._call("lua", "run", "function evaluate(ctx) while true do end end", [_ctx(UP)], StrategySpec(language="lua"), "t")
    assert "instruction limit" in w["results"][0]["error"]


@need_sb
def test_r_forbidden_and_package_allowlist():
    assert engine.static_check("r", "evaluate <- function(ctx) system('id')")
    assert engine.static_check("r", "library(httr)\nevaluate <- function(ctx) list(status='rejected')")
    assert engine.static_check("r", "evaluate <- function(ctx) base::system('id')")
    assert not engine.static_check("r", "library(stats)\nevaluate <- function(ctx) list(status='rejected')")


@need_sb
def test_sql_runtime_authorizer_blocks_internal_table():
    w = engine._call("sql", "run", "SELECT symbol FROM _bars", [_ctx(UP)], StrategySpec(language="sql"), "t")
    assert "prohibited" in (w["results"][0]["error"] or "") or "not authorized" in (w["results"][0]["error"] or "")


@need_sb
def test_timeout_and_memory_limits():
    t0 = time.time()
    r = run("python", "def evaluate(ctx):\n    while True:\n        pass\n", ctxs=[_ctx(UP)], limits={"timeout_s": 2, "memory_mb": 128})
    assert not r[0].ok and ("timed out" in r[0].errors[0] or "crashed" in r[0].errors[0]) and time.time() - t0 < 10
    r = run("python", "def evaluate(ctx):\n    x = [0] * (10**9)\n    return {'status':'rejected'}\n", ctxs=[_ctx(UP)],
            limits={"timeout_s": 10, "memory_mb": 64})
    assert not r[0].ok and "MemoryError" in " ".join(r[0].errors)
    r = run("javascript", "function evaluate(ctx){ while(true){} }", ctxs=[_ctx(UP)], limits={"timeout_s": 2})
    assert not r[0].ok


@need_sb
def test_cancellation_and_quota():
    ev = threading.Event()
    threading.Timer(0.5, ev.set).start()
    t0 = time.time()
    r = engine.execute("python", "def evaluate(ctx):\n    while True:\n        pass\n", None,
                       StrategySpec(language="python", limits={"timeout_s": 30}), [_ctx(UP)], "q1", ev)
    assert not r[0].ok and "cancelled" in r[0].errors[0] and time.time() - t0 < 5
    errors = []

    def slow():
        errors.append(engine.execute("python", "def evaluate(ctx):\n    while True:\n        pass\n", None,
                                     StrategySpec(language="python", limits={"timeout_s": 2}), [_ctx(UP)], "same-user")[0].errors)
    th = [threading.Thread(target=slow) for _ in range(2)]
    [t.start() for t in th]
    [t.join() for t in th]
    assert any("concurrency quota" in " ".join(e) for e in errors)


@need_sb
def test_output_limit():
    r = run("python", "def evaluate(ctx):\n    return {'status':'rejected','explanation':'x'*10, 'rules':[{'name':'a'*150,'passed':True}]*99}\n",
            ctxs=[_ctx(UP)], limits={"timeout_s": 5, "max_output_kb": 1})
    assert not r[0].ok


# ---------------------------------------------------------------- look-ahead prevention
@need_sb
def test_runtime_cannot_see_future_bars():
    """Contexts are truncated by the host: appending future bars never changes earlier evaluations."""
    src = "def evaluate(ctx):\n    s = ctx['series']['close']\n    return {'status':'rejected','explanation': str(len(s)) + ':' + str(s[-1])}\n"
    full = make_ohlc(UP, spread=0.5)
    t_past = analyze_tf(full.iloc[:200], "1D", CFG)
    c_past = build_context(t_past, {}, META, None, 150)
    r = run("python", src, ctxs=[c_past])[0]
    assert r.result["explanation"] == f"150:{round(float(full.Close.iloc[199]), 6)}"
    assert r.signal_time == str(full.index[199])


# ---------------------------------------------------------------- storage, scanner and backtest integration
def test_storage_versions_immutable_and_tenant_isolated(tmp_path):
    from src.storage.db import Store
    s = Store(f"sqlite:///{tmp_path}/t.db")
    a, _ = s.create_user("a")
    b, _ = s.create_user("b")
    sid = s.lab_create(a, "x", "visual", ir=VIS, spec=StrategySpec(language="visual").model_dump())
    v1 = s.lab_publish(a, sid)
    s.lab_save_draft(a, sid, None, IR_T["Bearish trend"], StrategySpec(language="visual").model_dump())
    v2 = s.lab_publish(a, sid)
    assert (v1, v2) == (1, 2)
    assert s.lab_version(a, sid, 1)["ir"] == VIS                 # v1 unchanged after later edits
    assert s.lab_get(b, sid) is None and s.lab_versions(b, sid) == [] and not s.lab_save_draft(b, sid, None, {}, {})
    with pytest.raises(KeyError):
        s.lab_publish(b, sid)


def _svc(tmp_path):
    from src.data.fixture_provider import FixtureProvider
    from src.services.app_services import Services
    return Services(db_url=f"sqlite:///{tmp_path}/s.db", provider=FixtureProvider(end="2026-10-16"))


def test_lab_scan_job_statuses_and_dedup(tmp_path):
    from src.lab.service import start_lab_scan
    svc = _svc(tmp_path)
    uid = svc.local_user
    sid = svc.store.lab_create(uid, "vis", "visual", ir=VIS, spec=StrategySpec(language="visual", data_quality="OK_or_STALE").model_dump())
    uni = pd.DataFrame({"symbol": ["A", "B", "ZZZ"], "ticker": ["A.NS", "B.NS", "ZZZ.NS"], "company": "", "industry": ""})
    with pytest.raises(ValueError):
        start_lab_scan(svc, uid, sid, None, uni, "u")              # drafts never scanned
    svc.store.lab_publish(uid, sid)
    job, rid = start_lab_scan(svc, uid, sid, None, uni, "u")
    while job.status in ("queued", "running"):
        time.sleep(0.05)
    assert job.status == "done", job.error
    run_row = svc.store.lab_runs(uid, sid)[0]
    assert run_row["status"] in ("COMPLETED", "PARTIALLY_COMPLETED") and run_row["stats"]["symbols_done"] == 3
    sig = pd.DataFrame(job.result.signals)
    assert set(sig.strategy) == {f"lab:{sid}@v1"} and "ZZZ.NS" not in set(sig.ticker)
    assert run_row["config"]["spec"]["language"] == "visual" and "provider" in run_row["config"]


def test_lab_backtest_uses_shared_engine_and_costs(tmp_path):
    from src.lab.service import lab_backtest
    svc = _svc(tmp_path)
    uid = svc.local_user
    sid = svc.store.lab_create(uid, "vis", "visual", ir=VIS, spec=StrategySpec(language="visual").model_dump())
    svc.store.lab_publish(uid, sid)
    clean = make_ohlc(UP, spread=0.5)
    r = lab_backtest(svc, uid, sid, None, clean, "1D", warmup=210)
    assert r["run_status"] == "COMPLETED" and r["version"] == 1 and r["all"]["trades"] >= 1
    tr = r["trades"]
    assert (pd.to_datetime(tr.entry_time) > pd.to_datetime(tr.signal_time)).all()
    svc.save_override({"costs": {"brokerage_bps": 50, "exchange_tax_bps": 50, "slippage_bps": 50}})
    r2 = lab_backtest(svc, uid, sid, None, clean, "1D", warmup=210)
    assert r2["all"]["avg_trade_ret_pct"] < r["all"]["avg_trade_ret_pct"]


@need_sb
def test_code_strategy_backtest_matches_visual(tmp_path):
    from src.lab.service import lab_backtest
    svc = _svc(tmp_path)
    uid = svc.local_user
    a = svc.store.lab_create(uid, "vis", "visual", ir=VIS, spec=StrategySpec(language="visual").model_dump())
    b = svc.store.lab_create(uid, "py", "python", source=export_python(VIS), spec=StrategySpec(language="python").model_dump())
    svc.store.lab_publish(uid, a)
    svc.store.lab_publish(uid, b)
    clean = make_ohlc(UP, spread=0.5)
    ra = lab_backtest(svc, uid, a, None, clean, "1D", warmup=210)
    rb = lab_backtest(svc, uid, b, None, clean, "1D", warmup=210)
    assert ra["trades"].drop(columns=["setup"]).to_dict("records") == rb["trades"].drop(columns=["setup"]).to_dict("records")


@need_sb
def test_r_large_batch_is_chunked():
    """Backtests send hundreds of contexts; R must handle them (chunked) without crashing."""
    ctxs = [_ctx(UP)] * 95
    r = run("r", EQUIV["r"], ctxs=ctxs)
    assert len(r) == 95 and all(x.ok for x in r) and {x.result["status"] for x in r} == {"qualified"}
