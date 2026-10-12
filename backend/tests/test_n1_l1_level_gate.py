"""N1 L1: golden parity of the level-gate port with the Trade repo's own code (tests/fixtures/level_gate/golden.json.gz,
made by make_golden.py from Trade at the pinned revision; drift_check.py compares a newer revision).

Every recorded output is reproduced: the gate's state machine (states, events, reasons, entry / SL / target / R:R,
grade, evidence, ticket lines, the one-line description), the trend context and allowed sides, the decision3 functions
(candle read, contraction, power shift, zone study, patterns, reversal forms, commitment with its wait reasons), the
real-break rule (`break_from`, time acceptance) and the settings defaults."""
import ast
import gzip
import subprocess
import sys
import json
import math
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict

import numpy as np
import pandas as pd
import pytest

from app.price_action import breaks as BR
from app.price_action import pa_settings
from app.price_action.level_gate import gate as G
from app.price_action.level_gate import method as M
from app.price_action.level_gate import settings as LS

BACKEND = Path(__file__).resolve().parents[1]
GOLDEN = BACKEND / "tests" / "fixtures" / "level_gate" / "golden.json.gz"
PACKAGE = BACKEND / "app" / "price_action" / "level_gate"


@lru_cache(maxsize=1)
def golden() -> Dict[str, Any]:
    with gzip.open(GOLDEN, "rt", encoding="utf-8") as fh:
        return json.load(fh)


def norm(x: Any) -> Any:
    if isinstance(x, dict):
        return {str(k): norm(v) for k, v in x.items()}
    if isinstance(x, np.ndarray):
        return [norm(v) for v in x.tolist()]
    if isinstance(x, (list, tuple)):
        return [norm(v) for v in x]
    if isinstance(x, (set, frozenset)):
        return sorted(norm(v) for v in x)
    if isinstance(x, (np.bool_, bool)):
        return bool(x)
    if isinstance(x, np.integer):
        return int(x)
    if isinstance(x, (np.floating, float)):
        v = float(x)
        return None if (math.isnan(v) or math.isinf(v)) else v
    return x


def same(a: Any, b: Any, path: str = "") -> None:
    """Exact structure; floats to 1e-9 (relative)."""
    if isinstance(a, float) or isinstance(b, float):
        assert a is not None and b is not None, f"{path}: {a!r} != {b!r}"
        assert math.isclose(float(a), float(b), rel_tol=1e-9, abs_tol=1e-9), f"{path}: {a!r} != {b!r}"
    elif isinstance(a, dict):
        assert isinstance(b, dict) and set(a) == set(b), f"{path}: keys {sorted(a)} != {sorted(b) if isinstance(b, dict) else b!r}"
        for k in a:
            same(a[k], b[k], f"{path}.{k}")
    elif isinstance(a, list):
        assert isinstance(b, list) and len(a) == len(b), f"{path}: {a!r} != {b!r}"
        for i, (x, y) in enumerate(zip(a, b)):
            same(x, y, f"{path}[{i}]")
    else:
        assert a == b, f"{path}: {a!r} != {b!r}"


def frame(bars: Dict[str, list]) -> pd.DataFrame:
    df = pd.DataFrame({k: v for k, v in bars.items() if k != "timestamp"})
    df.insert(0, "timestamp", pd.to_datetime(bars["timestamp"]))
    return df


@lru_cache(maxsize=None)
def series(i: int) -> pd.DataFrame:
    return frame(golden()["series"][i]["bars"])


def arrays(df: pd.DataFrame) -> Dict[str, Any]:
    A: Dict[str, Any] = {k: df[k].to_numpy(float) for k in ("open", "high", "low", "close")}
    A["volume"] = df["volume"].to_numpy(float) if "volume" in df.columns else None
    return A


def daily_state(st: Dict[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(trend=st["trend"], band=None if st["band"] is None else tuple(st["band"]), phase=st["phase"],
                           counter=st["counter"], protected=None if st["protected"] is None else SimpleNamespace(price=st["protected"]),
                           origin_risk=st["origin_risk"], mature=st["mature"])


@lru_cache(maxsize=1)
def states() -> Dict[tuple, SimpleNamespace]:
    return {(c["series"], c["name"]): daily_state(c["state"]) for c in golden()["contexts"] if not c["settings"]}


def ctx_for(sid: int, name: str) -> Dict[str, Any]:
    return G.context_from_state(states()[(sid, name)], {}, asof="2026-09-22")


# --- provenance and settings -------------------------------------------------------------------------------------------

def test_goldens_are_pinned_to_the_ported_trade_revision():
    assert golden()["trade_revision"] == LS.TRADE_REVISION
    counts = golden()["state_counts"]
    assert {"IDLE", "ARMED", "CONFIRMED", "DISARMED"} <= set(counts)


def test_settings_are_trades_defaults():
    same(golden()["settings"]["gate"], norm(LS.GATE_DEFAULTS), "gate")
    same(golden()["settings"]["method"], norm(LS.METHOD_DEFAULTS), "method")
    same(golden()["settings"]["breaks"], norm(LS.BREAK_DEFAULTS), "breaks")
    # The other ATP break callers keep their rule: no time acceptance in their settings.
    assert "break_accept_closes" not in pa_settings.DEFAULTS
    for key in ("median_range_n", "break_buffer_mr", "break_displacement_confirm", "break_close_loc", "strength_min", "break_no_reclaim_bars"):
        assert pa_settings.DEFAULTS[key] == LS.BREAK_DEFAULTS[key], key


def test_gate_settings_cast_like_trade():
    s = LS.gate_settings({"min_rr": "2", "max_armed_bars": 5.0, "wait_next_candle": 1, "unknown": 9, "mr_n": None})
    assert s["min_rr"] == 2.0 and s["max_armed_bars"] == 5 and s["wait_next_candle"] is True and "unknown" not in s
    assert s["mr_n"] == LS.GATE_DEFAULTS["mr_n"]


# --- trend context ------------------------------------------------------------------------------------------------------

def test_context_from_state_parity():
    cases = golden()["contexts"]
    assert len(cases) >= 100
    for i, c in enumerate(cases):
        same(c["ctx"], norm(G.context_from_state(daily_state(c["state"]), c["settings"], asof="2026-09-22")), f"contexts[{i}]")


def test_side_allowed_parity():
    for i, c in enumerate(golden()["side_allowed"]):
        assert G.side_allowed(ctx_for(c["series"], c["ctx"]), c["side"], tuple(c["area"])) is c["out"], f"side_allowed[{i}]"


# --- the state machine ----------------------------------------------------------------------------------------------------

def test_evaluate_parity_states_events_plans_and_descriptions():
    cases = golden()["evaluate"]
    confirmed = 0
    for i, c in enumerate(cases):
        sid = c["series"]
        ctx = ctx_for(sid, c["ctx"])
        same(c["ctx_value"], norm(ctx), f"evaluate[{i}].ctx")
        start = golden()["series"][sid]["session_start"]
        out = G.evaluate(series(sid), tuple(c["area"]), ctx, [tuple(a) for a in c["others"]], start, c["settings"])
        same(c["out"], norm(out), f"evaluate[{i}]")
        assert G.describe(out) == c["describe"], f"evaluate[{i}].describe"
        confirmed += any(e[1] == G.CONFIRMED for e in c["out"]["events"])
    assert confirmed >= 50                     # the goldens exercise real confirmations, not only idle areas


def test_evaluate_covers_every_reason():
    reasons = {e[2].split(" (")[0] for c in golden()["evaluate"] for e in c["out"]["events"]}
    for reason in (G.R_AGAINST, G.R_NO_TREND, G.R_IMPULSIVE, G.R_BROKEN, G.R_TIMEOUT, G.R_RR, G.R_NO_TARGET, G.R_ABSORPTION,
                   G.R_LOW_VOL, "price arrived", "commitment + next candle held"):
        assert reason.split(" (")[0] in reasons, reason


def test_misc_parity():
    misc = golden()["misc"]
    for sid, expected in enumerate(misc["median_ranges"]):
        same(expected, norm(G.median_ranges(arrays(series(sid)), 20)), f"median_ranges[{sid}]")
    cb = misc["closed_bars"]
    df = frame(cb["bars"])
    aware = df.assign(timestamp=pd.to_datetime(df["timestamp"]).dt.tz_localize("Asia/Kolkata").dt.tz_convert("UTC"))
    assert len(G.closed_bars(df, 5, pd.Timestamp("2026-09-22 10:02:00"))) == cb["naive_rows"]
    closed = G.closed_bars(aware, 5, pd.Timestamp(cb["now"]))
    assert len(closed) == cb["aware_rows"] and str(closed["timestamp"].iloc[-1]) == cb["aware_last"]
    assert G.session_start_index(df) == cb["session_start_last"]
    assert G.session_start_index(df, "2026-09-21") == cb["session_start_day1"]
    assert G.session_start_index(df, "2026-10-01") == cb["session_start_missing"]
    same(misc["no_bars"], norm(G.evaluate(df.iloc[:0], (1.0, 2.0), {"ok": True, "sides": {G.BULL}}, (), 0)), "no_bars")
    same(misc["safe_evaluate_error"], norm(G.safe_evaluate(df.drop(columns=["close"]), (1.0, 2.0), {"ok": True}, (), 0)), "safe")


def test_prefix_replays_parity_and_no_look_ahead():
    """The live cycle replays the session's closed bars up to bar m: every prefix matches Trade (immediate entries and
    break_pending included), and a live entry at m happens exactly when the full replay has a CONFIRMED event at m."""
    cases = golden()["prefix"]
    assert golden()["prefix_entries"] >= 10 and golden()["prefix_break_pending"] >= 50
    groups: Dict[tuple, Dict[int, Any]] = {}
    for i, c in enumerate(cases):
        sid = c["series"]
        start = golden()["series"][sid]["session_start"]
        ctx = ctx_for(sid, c["ctx"])
        others = [tuple(a) for a in c["others"]]
        out = G.evaluate(series(sid).iloc[:c["m"] + 1], tuple(c["area"]), ctx, others, start, c["settings"])
        same(c["out"], norm(out), f"prefix[{i}]")
        key = (sid, tuple(c["area"]), c["ctx"], json.dumps(c["settings"], sort_keys=True))
        groups.setdefault(key, {})[c["m"]] = out
    entries = 0
    for (sid, area, cname, over), by_m in groups.items():
        start = golden()["series"][sid]["session_start"]
        others = next([tuple(a) for a in c["others"]] for c in cases if c["series"] == sid and tuple(c["area"]) == area)
        full = G.evaluate(series(sid), area, ctx_for(sid, cname), others, start, json.loads(over))
        confirmed = {e[0] for e in full["events"] if e[1] == G.CONFIRMED}
        live = {m for m, out in by_m.items() if out["entry"]}
        assert live == confirmed & set(by_m), (sid, area, cname, over, sorted(live), sorted(confirmed))
        entries += len(live)
    assert entries == golden()["prefix_entries"]


def test_edge_cases_parity():
    misc = golden()["misc"]
    A = arrays(series(0))
    same(misc["t0"]["commitment"], norm(M.commitment(A, 0, M.UP, {"lo": 0.0, "hi": 1e9}, LS.METHOD_DEFAULTS, None, win=0)), "t0")
    same(misc["t0"]["reversal_form"], norm(M.reversal_form(A, 0, M.DOWN, LS.METHOD_DEFAULTS)), "t0.form")
    df = frame(misc["closed_bars"]["bars"])
    err = G.safe_evaluate(df.drop(columns=["close"]), (1.0, 2.0), {"ok": True}, (), 0)
    assert [G.describe(err), G.describe(err, (100.0, 200.0))] == misc["describe_error"]
    kinds = {s_["volume"] for s_ in golden()["series"]}
    assert {"holes", "gap", "short"} <= {k for k in kinds if isinstance(k, str)}


# --- decision3 functions -------------------------------------------------------------------------------------------------

def test_method_parity():
    s3 = LS.METHOD_DEFAULTS
    cases = golden()["methods"]
    assert len(cases) >= 300
    for i, c in enumerate(cases):
        df = series(c["series"])
        A = arrays(df)
        tss = list(df["timestamp"])
        mr = G.median_ranges(A, 20)
        t, a, d, lv, k = c["t"], c["a"], c["d"], c["lvl"], c["k"]
        p = f"methods[{i}]"
        same(c["candle_read"], norm(M.candle_read(A, t, s3)), p + ".candle_read")
        assert M.contraction(A, t, d, None, s3, lv) is c["contraction"], p
        assert M.contraction(A, t, d, k, s3) is c["contraction_k"], p
        same(c["power_shift"], norm(M.power_shift(A, t, d, None, s3, lv)), p + ".power_shift")
        same(c["power_shift_k"], norm(M.power_shift(A, t, d, k, s3)), p + ".power_shift_k")
        same(c["power_shift_strict3"], norm(M.power_shift(A, t, d, None, dict(s3, power_a_rule="strict3"), lv)), p + ".strict3")
        same(c["zone_study"], norm(M.zone_study(A, a, t, d, s3, mr=mr[t])), p + ".zone_study")
        same(c["zone_study_nomr"], norm(M.zone_study(A, a, t, d, s3)), p + ".zone_study_nomr")
        assert M.zone_pattern(A, a, t, d, s3, mr[t]) == c["zone_pattern"], p
        same(c["reversal_form"], norm(M.reversal_form(A, t, d, s3)), p + ".reversal_form")
        same(c["reversal_form_tss"], norm(M.reversal_form(A, t, d, s3, tss)), p + ".reversal_form_tss")
        same(c["commitment"], norm(M.commitment(A, t, d, lv, s3, None, win=a)), p + ".commitment")
        same(c["commitment_tss"], norm(M.commitment(A, t, d, lv, s3, tss)), p + ".commitment_tss")
        same(c["commitment_extreme"], norm(M.commitment(A, t, d, lv, dict(s3, commit_beyond="extreme"), None, win=a)), p + ".extreme")


def test_commitment_refuses_an_unknown_mode():
    A = arrays(series(0))
    with pytest.raises(ValueError):
        M.commitment(A, 5, M.UP, {"lo": 0.0, "hi": 1.0}, dict(LS.METHOD_DEFAULTS, commit_beyond="wick"))


# --- the real-break rule --------------------------------------------------------------------------------------------------

def test_break_parity():
    es = LS.BREAK_DEFAULTS
    cases = golden()["breaks"]
    assert {c["break_from"] for c in cases if c["break_from"] in (None, -1)} == {None, -1}
    for i, c in enumerate(cases):
        df = series(c["series"]).reset_index(drop=True)
        n = len(df)
        j, level, side = c["j"], c["level"], c["side"]
        p = f"breaks[{i}]"
        assert BR.break_from(df, j, level, side, es) == c["break_from"], p
        assert BR.break_from(df, j, level, side, dict(es, break_accept_closes=0)) == c["break_from_no_accept"], p
        assert BR.break_from(df, j, level, side, dict(es, break_no_reclaim_bars=3)) == c["break_from_k3"], p
        assert BR.first_real_break(df, max(0, j - 10), level, side, es, end=min(n - 1, j + 6)) == c["first_real_break"], p
        assert BR.time_accepted(df["close"].to_numpy(float), j, level, side, 3, max(0, j - 10)) is c["time_accepted"], p


# --- the guard: a pure library -------------------------------------------------------------------------------------------

ALLOWED_IMPORTS = ("typing", "numpy", "pandas", "app.price_action.breaks", "app.price_action.level_gate")


def test_the_level_gate_is_pure():
    """No I/O, orders, brokers or database: an allow-list of imports (review), checked on the source ..."""
    for path in sorted(PACKAGE.glob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                if isinstance(node, ast.ImportFrom):              # "from app.price_action import breaks" -> app.price_action.breaks
                    modules = [f"{node.module}.{alias.name}" for alias in node.names]
                    modules = [m for m in modules if not any(m.split(".")[0] == p_ for p_ in ("typing",))] or [node.module or ""]
                else:
                    modules = [alias.name for alias in node.names]
                for m in modules:
                    assert any(m == a or m.startswith(a + ".") for a in ALLOWED_IMPORTS), (path.name, m)


def test_importing_the_gate_loads_nothing_else_from_the_app():
    """... and at run time: a fresh interpreter importing the gate loads no app module outside app.price_action."""
    code = ("import sys; import app.price_action.level_gate.gate, app.price_action.level_gate.method; "
            "print(sorted(m for m in sys.modules if m.startswith('app.') and not m.startswith('app.price_action')))")
    out = subprocess.run([sys.executable, "-c", code], cwd=BACKEND, capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "[]", out.stdout
