"""Release tests for the Strategy Lab: all eight authoring modes are released; operators can restrict them.
Covers release gating, user workflow (create -> validate -> run -> publish -> scan -> backtest) for EVERY mode, Python
helper parity with host indicators, Pine coverage, the local trusted (unsandboxed) mode used on Windows for every code
runtime, and the API.
SYNTHETIC fixtures only; no network."""
import os
import time

import numpy as np
import pandas as pd
import pytest

from src.analysis.context import analyze_tf
from src.config import default_settings
from src.lab import engine, sandbox
from src.lab.contract import StrategySpec, build_context, capabilities, enabled_languages
from src.lab.pine import TEMPLATES as PINE_T, translate_pine
from src.lab.templates import CODE_TEMPLATES
from tests.conftest import make_ohlc

CFG = default_settings()
META = {"symbol": "T", "ticker": "T.NS", "exchange": "NSE", "timeframe": "1D", "timezone": "Asia/Kolkata",
        "provider": "fixture", "data_kind": "simulated", "data_status": "OK"}
UP = list(np.linspace(100, 200, 260) + np.sin(np.arange(260)) * 1.5)
DN = list(np.linspace(200, 100, 260) + np.sin(np.arange(260)) * 1.5)
SB = sandbox.sandbox_status()["verified"]
need_exec = pytest.mark.skipif(not SB, reason="no process sandbox on this platform")


ALL = ["python", "pine", "visual", "sql", "javascript", "typescript", "lua", "r"]


@pytest.fixture(autouse=True)
def _release_mode(monkeypatch):
    monkeypatch.delenv("AMW_LAB_LANGUAGES", raising=False)


def ctx(closes, params=None, vol=1000.0):
    t = analyze_tf(make_ohlc(closes, spread=0.5, vol=vol), "1D", CFG)
    return build_context(t, params or {}, META, None, 200)


# ---------------------------------------------------------------- release gating
def test_all_modes_released():
    assert sorted(enabled_languages()) == sorted(ALL)
    assert sorted(capabilities()["_enabled"]) == sorted(ALL)


@pytest.mark.parametrize("lang", ["javascript", "typescript", "sql", "lua", "r", "visual"])
def test_operator_can_disable_modes(lang, monkeypatch):
    monkeypatch.setenv("AMW_LAB_LANGUAGES", "python,pine")
    assert enabled_languages() == ["python", "pine"]
    v = engine.validate(lang, "x", {"ir_version": 1}, StrategySpec(language=lang))
    assert not v["ok"] and "disabled by the operator" in v["errors"][0]
    r = engine.execute(lang, "x", {"ir_version": 1}, StrategySpec(language=lang), [ctx(UP)])
    assert not r[0].ok and "disabled by the operator" in r[0].errors[0]


# ---------------------------------------------------------------- Pine Script
def test_pine_templates_all_translate_and_run():
    for name, src in PINE_T.items():
        ir, errs, _ = translate_pine(src)
        assert ir is not None and not errs, (name, errs)
        r = engine.execute("pine", src, None, StrategySpec(language="pine"), [ctx(UP), ctx(DN)])
        assert all(x.ok for x in r), (name, [x.errors for x in r])


def test_pine_inputs_set_stop_and_target():
    src = "//@version=5\nindicator('x')\nstop_atr = input.float(3.0)\ntarget_r = input.float(1.5)\nlong_signal = close > ta.ema(close, 20)\n"
    c = ctx(UP)
    r = engine.execute("pine", src, None, StrategySpec(language="pine"), [c])[0]
    close, atr = c["series"]["close"][-1], c["series"]["atr14"][-1]
    assert r.result["stop"] == pytest.approx(close - 3.0 * atr, rel=1e-6)
    assert r.result["target"] == pytest.approx(close + 1.5 * 3.0 * atr, rel=1e-6)
    bad = "//@version=5\nindicator('x')\nstop_atr = input.float(50)\nlong_signal = close > open\n"
    assert not engine.validate("pine", bad, None, StrategySpec(language="pine"))["ok"]


def test_pine_indicators_match_host_values():
    """ta.ema / ta.rsi / ta.atr in the Pine subset equal the host indicator engine on the last completed bar."""
    c = ctx(UP)
    s = c["series"]
    for expr, key in (("ta.ema(close, 20)", "ema20"), ("ta.rsi(close, 14)", "rsi14"), ("ta.atr(14)", "atr14")):
        v = s[key][-1]
        # the context is rounded to 6 dp; a +/-1e-4 window must contain the Pine-computed value
        lo = f"//@version=5\nindicator('x')\nlong_signal = {expr} > {v - 1e-4!r} and {expr} < {v + 1e-4!r}\n"
        assert engine.execute("pine", lo, None, StrategySpec(language="pine"), [c])[0].result["status"] == "qualified", expr


def test_pine_crossover_semantics():
    closes = list(np.linspace(110, 100, 60)) + [100.5, 101.5, 104]
    c = ctx(closes)
    src = "//@version=5\nindicator('x')\nlong_signal = ta.crossover(close, ta.sma(close, 5))\n"
    r = engine.execute("pine", src, None, StrategySpec(language="pine"), [c])[0]
    s = c["series"]["close"]
    sma_now, sma_prev = np.mean(s[-5:]), np.mean(s[-6:-1])
    expected = s[-1] > sma_now and s[-2] <= sma_prev
    assert (r.result["status"] == "qualified") == expected


def test_pine_diagnostics_have_line_numbers():
    src = "//@version=5\nindicator('x')\nx = ta.ema(close, 20)\nlong_signal = foo > x\n"
    v = engine.validate("pine", src, None, StrategySpec(language="pine"))
    assert not v["ok"] and v["errors"][0].startswith("line 4") and "foo" in v["errors"][0]


# ---------------------------------------------------------------- Python
@need_exec
def test_python_templates_all_valid_and_executable():
    c = ctx(UP, params={"lookback": 20, "min_rvol": 1.5})
    for name, src in CODE_TEMPLATES["python"].items():
        v = engine.validate("python", src, None, StrategySpec(language="python"))
        assert v["ok"], (name, v)
        r = engine.execute("python", src, None, StrategySpec(language="python"), [c])[0]
        assert r.ok, (name, r.errors)


@need_exec
def test_python_helpers_match_host_indicators():
    c = ctx(UP)
    src = ("def evaluate(ctx):\n    s = ctx['series']\n"
           "    e = amw.ema(s['close'], 20)[-1]; r = amw.rsi(s['close'], 14)[-1]; a = amw.atr(s['high'], s['low'], s['close'], 14)[-1]\n"
           "    return {'status': 'rejected', 'explanation': str([e, r, a])}\n")
    out = engine.execute("python", src, None, StrategySpec(language="python"), [c])[0]
    e, r, a = eval(out.result["explanation"])
    assert e == pytest.approx(c["series"]["ema20"][-1], rel=1e-6)
    assert r == pytest.approx(c["series"]["rsi14"][-1], rel=1e-5)
    assert a == pytest.approx(c["series"]["atr14"][-1], rel=1e-6)


@need_exec
def test_python_user_errors_are_reported_not_crashing():
    cases = {"syntax": "def evaluate(ctx)\n    return 1\n",
             "runtime": "def evaluate(ctx):\n    return 1 / 0\n",
             "bad output": "def evaluate(ctx):\n    return {'status': 'yes'}\n",
             "no function": "x = 1\n"}
    for name, src in cases.items():
        r = engine.execute("python", src, None, StrategySpec(language="python"), [ctx(UP)])[0]
        assert not r.ok and r.errors, name
    r = engine.execute("python", cases["runtime"], None, StrategySpec(language="python"), [ctx(UP)])[0]
    assert "ZeroDivisionError" in r.errors[0]


@need_exec
def test_python_print_logs_captured():
    src = "def evaluate(ctx):\n    print('close', ctx['series']['close'][-1])\n    return {'status': 'rejected'}\n"
    r = engine.execute("python", src, None, StrategySpec(language="python"), [ctx(UP)])[0]
    assert r.ok and r.logs.startswith("close ")


def test_python_local_trusted_mode_without_os_sandbox(monkeypatch):
    """Windows desktop path: no OS sandbox; AMW_ALLOW_UNSANDBOXED=1 enables execution in a separate process with the
    in-process guards (restricted builtins, import allowlist, audit hook, timeout, empty environment)."""
    monkeypatch.setattr(sandbox, "_linux_tools", lambda: False)
    monkeypatch.delenv("AMW_ALLOW_UNSANDBOXED", raising=False)
    r = engine.execute("python", CODE_TEMPLATES["python"]["Trend (EMA stack)"], None, StrategySpec(language="python"), [ctx(UP)])[0]
    assert not r.ok and "sandbox unavailable" in r.errors[0]
    monkeypatch.setenv("AMW_ALLOW_UNSANDBOXED", "1")
    r = engine.execute("python", CODE_TEMPLATES["python"]["Trend (EMA stack)"], None, StrategySpec(language="python"), [ctx(UP)])[0]
    assert r.ok and r.result["status"] == "qualified"
    os.environ["AMW_TEST_SECRET2"] = "x"
    w = engine._call("python", "run", "import socket\ndef evaluate(ctx):\n    return {'status':'rejected'}\n", [ctx(UP)],
                     StrategySpec(language="python"), "t")
    assert "not allowed" in w["results"][0]["error"]
    t0 = time.time()
    r = engine.execute("python", "def evaluate(ctx):\n    while True:\n        pass\n", None,
                       StrategySpec(language="python", limits={"timeout_s": 2}), [ctx(UP)])[0]
    assert not r.ok and time.time() - t0 < 10


@pytest.mark.parametrize("lang", ["javascript", "typescript", "lua", "sql", "r"])
def test_local_trusted_mode_other_runtimes(lang, monkeypatch):
    """Windows path for every code runtime: disabled without the opt-in, runs with it (own code only)."""
    from tests.test_lab import EQUIV
    monkeypatch.setattr(sandbox, "_linux_tools", lambda: False)
    monkeypatch.delenv("AMW_ALLOW_UNSANDBOXED", raising=False)
    assert "sandbox unavailable" in engine.execute(lang, EQUIV[lang], None, StrategySpec(language=lang), [ctx(UP)])[0].errors[0]
    monkeypatch.setenv("AMW_ALLOW_UNSANDBOXED", "1")
    r = engine.execute(lang, EQUIV[lang], None, StrategySpec(language=lang), [ctx(UP), ctx(DN)])
    assert [x.ok for x in r] == [True, True], [x.errors for x in r]
    assert [x.result["status"] for x in r] == ["qualified", "rejected"]


# ---------------------------------------------------------------- end-to-end workflow + API
def _svc(tmp_path):
    from src.data.fixture_provider import FixtureProvider
    from src.services.app_services import Services
    return Services(db_url=f"sqlite:///{tmp_path}/p1.db", provider=FixtureProvider(end="2026-10-16"))


@pytest.mark.parametrize("lang", ["pine", "visual"] + [pytest.param(x, marks=need_exec) for x in ("python", "sql", "javascript", "typescript", "lua", "r")])
def test_user_workflow_end_to_end(tmp_path, lang):
    from src.lab.ir import TEMPLATES as IR_T
    from src.lab.service import lab_backtest, run_on_symbols, start_lab_scan
    from tests.test_lab import EQUIV
    svc = _svc(tmp_path)
    uid = svc.local_user
    src = {"pine": PINE_T["EMA trend (v5)"], "visual": None}.get(lang, EQUIV.get(lang))
    ir = IR_T["Bullish trend"] if lang == "visual" else None
    spec = StrategySpec(language=lang, data_quality="OK_or_STALE")
    sid = svc.store.lab_create(uid, f"my {lang}", lang, source=src, ir=ir, spec=spec.model_dump())
    assert engine.validate(lang, src, ir, spec)["ok"]
    rows = run_on_symbols(svc, uid, lang, src, ir, spec, ["A.NS", "ZZZ.NS"])
    assert {r["ticker"] for r in rows} == {"A.NS", "ZZZ.NS"}
    assert [r for r in rows if r["ticker"] == "ZZZ.NS"][0]["status"].startswith(("INSUFFICIENT", "DATA_"))
    assert [r for r in rows if r["ticker"] == "A.NS"][0]["status"] in ("qualified", "rejected")
    svc.store.lab_publish(uid, sid)
    uni = pd.DataFrame({"symbol": list("ABCD"), "ticker": [f"{x}.NS" for x in "ABCD"], "company": "", "industry": ""})
    job, rid = start_lab_scan(svc, uid, sid, None, uni, "test")
    while job.status in ("queued", "running"):
        time.sleep(0.05)
    assert job.status == "done", job.error
    run = svc.store.lab_runs(uid, sid)[0]
    assert run["status"] == "COMPLETED" and run["stats"]["symbols_done"] == 4 and run["language"] == lang
    bt = lab_backtest(svc, uid, sid, None, make_ohlc(UP, spread=0.5), "1D", warmup=210)
    assert bt["run_status"] == "COMPLETED" and bt["language"] == lang and bt["version"] == 1


def test_api_release_and_gate(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from src.api.main import create_app
    monkeypatch.setenv("AMW_AUTH_MODE", "token")
    svc = _svc(tmp_path)
    _, tok = svc.store.create_user("alice", "analyst")
    _, tok2 = svc.store.create_user("bob", "analyst")
    c = TestClient(create_app(svc))
    H, H2 = {"Authorization": f"Bearer {tok}"}, {"Authorization": f"Bearer {tok2}"}
    assert c.get("/api/v1/lab/strategies").status_code == 401
    r = c.post("/api/v1/lab/strategies", json={"name": "js", "language": "javascript", "source": "x"}, headers=H)
    assert r.status_code == 201                                                       # released
    monkeypatch.setenv("AMW_LAB_LANGUAGES", "python,pine")
    r = c.post("/api/v1/lab/strategies", json={"name": "js2", "language": "javascript", "source": "x"}, headers=H)
    assert r.status_code == 403 and "disabled" in r.text                              # operator restriction
    monkeypatch.delenv("AMW_LAB_LANGUAGES")
    r = c.post("/api/v1/lab/strategies", json={"name": "p", "language": "pine", "source": PINE_T["EMA trend (v5)"]}, headers=H)
    assert r.status_code == 201
    sid = r.json()["id"]
    assert c.get(f"/api/v1/lab/strategies/{sid}", headers=H2).status_code == 404      # tenant isolation
    assert c.post("/api/v1/lab/validate", json={"language": "pine", "source": "//@version=5\nlong_signal = foo"}, headers=H).json()["ok"] is False
    assert c.post(f"/api/v1/lab/strategies/{sid}/publish", headers=H).json()["version"] == 1
    caps = c.get("/api/v1/lab/capabilities", headers=H).json()
    assert sorted(caps["_enabled"]) == sorted(ALL) and "verified" in caps["sandbox_status"]
