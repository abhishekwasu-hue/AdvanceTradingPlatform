"""N1 L2: golden parity of the Daily trend port with the Trade repo's own code (tests/fixtures/daily_trend/golden.json.gz,
made by make_golden.py from Trade at the pinned revision; drift_check.py compares a newer revision).

Reproduced: every Daily state the fold produces (trend, protected origin, band, phase, wave, correction leg, impulse
extreme, maturity, targets, parent, counter, leg start, correction bars, origin risk, sharp zone, evidence, correction
zone, leg origin, target passed, wave A, alt count, pivots) for the default settings and setting variants; `state_at`;
the pivots and sigma_D; the leg reader (classify, read_counter, structure, reductions, unfolded 1-2-3, leg points, C
class); the wave rules; `prepare` / `prepare_daily`; the settings defaults. The free-text `why` is English in ATP and
only its presence is compared."""
import ast
import gzip
import subprocess
import sys
import json
import math
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict

import pandas as pd
import pytest

from app.price_action.daily_trend import fold as DD
from app.price_action.daily_trend import legs as DL
from app.price_action.daily_trend import rules as PR
from app.price_action.daily_trend import settings as S3

BACKEND = Path(__file__).resolve().parents[1]
GOLDEN = BACKEND / "tests" / "fixtures" / "daily_trend" / "golden.json.gz"
PACKAGE = BACKEND / "app" / "price_action" / "daily_trend"


@lru_cache(maxsize=1)
def golden() -> Dict[str, Any]:
    with gzip.open(GOLDEN, "rt", encoding="utf-8") as fh:
        return json.load(fh)


def num(x: Any) -> Any:
    if x is None:
        return None
    v = float(x)
    return None if (math.isnan(v) or math.isinf(v)) else v


def same(a: Any, b: Any, path: str = "") -> None:
    if isinstance(a, float) or isinstance(b, float):
        assert a is not None and b is not None, f"{path}: {a!r} != {b!r}"
        assert math.isclose(float(a), float(b), rel_tol=1e-9, abs_tol=1e-9), f"{path}: {a!r} != {b!r}"
    elif isinstance(a, dict):
        assert isinstance(b, dict) and set(a) == set(b), f"{path}: keys {sorted(a)} != {sorted(b) if isinstance(b, dict) else b!r}"
        for k in a:
            same(a[k], b[k], f"{path}.{k}")
    elif isinstance(a, (list, tuple)):
        assert isinstance(b, (list, tuple)) and len(a) == len(b), f"{path}: {a!r} != {b!r}"
        for i, (x, y) in enumerate(zip(a, b)):
            same(x, y, f"{path}[{i}]")
    else:
        assert a == b, f"{path}: {a!r} != {b!r}"


def pivot(p: Any) -> Any:
    return None if p is None else [p.kind, num(p.price), int(p.bar), int(p.confirm_bar)]


def state(st: DD.DState) -> Dict[str, Any]:
    return {"bar": int(st.bar), "trend": st.trend, "protected": pivot(st.protected),
            "band": None if st.band is None else [num(x) for x in st.band], "why": bool(st.why), "phase": st.phase,
            "wave": st.wave, "corr_label": st.corr_label, "imp_end": pivot(st.imp_end), "mature": bool(st.mature),
            "targets": [num(x) for x in st.targets], "parent": pivot(st.parent), "counter": st.counter,
            "leg_start": None if st.leg_start is None else int(st.leg_start), "corr_bars": [int(x) for x in st.corr_bars],
            "origin_risk": bool(st.origin_risk), "sharp": None if st.sharp is None else [num(st.sharp[0]), num(st.sharp[1]), int(st.sharp[2])],
            "evidence": list(st.evidence), "corr_zone": None if st.corr_zone is None else [num(st.corr_zone[0]), num(st.corr_zone[1]),
                                                                                          int(st.corr_zone[2])],
            "leg_from": None if st.leg_from is None else int(st.leg_from), "target_passed": bool(st.target_passed),
            "wave_a": pivot(st.wave_a), "alt": st.alt, "n_pivots": len(st.pivots)}


def frame(bars: Dict[str, list]) -> pd.DataFrame:
    df = pd.DataFrame({k: v for k, v in bars.items() if k != "timestamp"})
    df.insert(0, "timestamp", pd.to_datetime(bars["timestamp"]))
    return df


@lru_cache(maxsize=None)
def daily(sid: int) -> pd.DataFrame:
    return DD.prepare(frame(golden()["series"][sid]["bars"]))


@lru_cache(maxsize=None)
def folded(sid: int, variant: str) -> tuple:
    over = next(f["settings"] for f in golden()["folds"] if f["series"] == sid and f["variant"] == variant)
    return tuple(DD.fold(daily(sid), over))


# --- provenance and settings ---------------------------------------------------------------------------------------------

def test_goldens_are_pinned_to_the_ported_trade_revision():
    assert golden()["trade_revision"] == S3.TRADE_REVISION


def test_settings_are_trades_defaults():
    same(golden()["settings"]["fold"], S3.DEFAULTS, "fold")
    same(golden()["settings"]["legs"], S3.LEGS_DEFAULTS, "legs")
    same(golden()["settings"]["patterns"], S3.PATTERN_DEFAULTS, "patterns")
    assert S3.load({"daily_pivot_n": 3})["daily_pivot_n"] == 3 and S3.DEFAULTS["daily_pivot_n"] == 2


def test_the_goldens_cover_the_fold_s_branches():
    cov = golden()["coverage"]
    for key in ("trend:UP", "trend:DOWN", "trend:RANGE", "trend:NEUTRAL", "trend:UNKNOWN", "phase:impulse", "phase:correction",
                "phase:origin_broken", "counter:pullback", "counter:candidate", "counter:correction", "wave_a", "sharp", "corr_zone",
                "mature", "origin_risk", "target_passed"):
        assert cov.get(key, 0) > 0, key


# --- the fold ------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("index", range(16))
def test_fold_parity_every_state(index):
    folds = golden()["folds"]
    if index >= len(folds):
        pytest.skip("no such fold case")
    case = folds[index]
    sid, variant = case["series"], case["variant"]
    states = folded(sid, variant)
    assert len(states) == len(case["states"])
    for expected, st in zip(case["states"], states):
        same(expected, state(st), f"{variant}[{sid}].bar{expected['bar']}")
    same(case["final_pivots"], [pivot(p) for p in states[-1].pivots], f"{variant}[{sid}].pivots")
    d = daily(sid)
    for st in states[::97]:                                    # day / known_at are the bar's date and its close
        assert st.day == d["timestamp"].iloc[st.bar] and st.known_at == d["bar_end"].iloc[st.bar]
        if st.protected is not None:
            assert st.protected.known_at == d["bar_end"].iloc[st.protected.confirm_bar]


def test_truncated_runs_match_trade_and_the_full_run():
    """A live day sees only the candles so far: the fold on the first m + 1 candles matches Trade's, and equals state m of
    the full run (causal - no state depends on a later candle)."""
    for case in golden()["truncated"]:
        sid, m = case["series"], case["m"]
        short = DD.fold(daily(sid).iloc[:m + 1])
        same(case["state"], state(short[-1]), f"truncated[{sid}].{m}")
        same(state(folded(sid, "default")[m]), state(short[-1]), f"causal[{sid}].{m}")


def test_short_series_parity():
    for case in golden()["short"]:
        states = DD.fold(daily(0).iloc[:case["m"] + 1])
        same(case["states"], [state(st) for st in states], f"short.{case['m']}")
    assert all(st.trend == "UNKNOWN" for st in DD.fold(daily(0).iloc[:S3.DEFAULTS["daily_min_sessions"]]))


def test_state_at_parity():
    for case in golden()["state_at"]:
        st = DD.state_at(list(folded(case["series"], "default")), pd.Timestamp(case["ts"]))
        assert (st.bar, st.trend) == (case["bar"], case["trend"]), case


def test_only_the_impulse_mode_is_ported():
    with pytest.raises(ValueError):
        DD.fold(daily(0).iloc[:30], {"daily_trend_mode": "minor"})


def test_pivots_and_sigma_parity():
    for case in golden()["pivots"]:
        d = daily(case["series"])
        kat = DD.known_at_of(d)
        sig = DD.sigma_d(d, 10)
        same(case["sigma"], [num(x) for x in sig], "sigma")
        same(case["pivot2"], [pivot(p) for p in DD.pivots_pivot(d, 2, kat)], "pivot2")
        same(case["pivot1"], [pivot(p) for p in DD.pivots_pivot(d, 1, kat)], "pivot1")
        same(case["dc"], [pivot(p) for p in DD.pivots_dc(d, 1.0, sig, kat)], "dc")


def test_prepare_and_prepare_daily_parity():
    case = golden()["misc"]["prepare"]
    messy = frame(case["input"])
    prep = DD.prepare(messy)
    assert len(prep) == case["rows"] and list(prep.columns) == case["columns"]
    assert str(prep["timestamp"].iloc[0]) == case["first"] and str(prep["bar_end"].iloc[-1]) == case["last_end"]
    assert math.isclose(float(prep["close"].sum()), case["close_sum"], rel_tol=1e-12)
    gate = DD.prepare_daily(messy, "2010-02-15")
    assert len(gate) == case["daily_rows"] and str(gate["timestamp"].iloc[-1]) == case["daily_last"]


# --- the leg reader and the wave rules -----------------------------------------------------------------------------------

def test_classify_and_leg_points_parity():
    lows: Dict[int, Any] = {}
    for i, case in enumerate(golden()["legs"]):
        sid = case["series"]
        d = daily(sid)
        final = folded(sid, "default")[-1].pivots
        if sid not in lows:
            lows[sid] = DD.pivots_pivot(d, 1, DD.known_at_of(d))
        inner = lows[sid] if case["inner"] else None
        pa, pb = final[case["a"]], final[case["b"]]
        c = DL.classify(DL.arrays(d), final, pa.bar, pa.price, pb.bar, pb.price, case["upto"], inner_seq=inner, elliott=case["elliott"])
        same(case["out"], {k: (num(v) if k == "C" else v) for k, v in c.items()}, f"legs[{i}]")
        assert DL.tag(c) == case["tag"].replace("—", "-"), i
        prices, bars = DL.leg_points(final, pa.bar, pa.price, pb.bar, pb.price, case["upto"], inner)
        same(case["points"], [[num(x) for x in prices], bars], f"legs[{i}].points")


def test_read_counter_structure_reductions_parity():
    for i, case in enumerate(golden()["read_counter"]):
        x = case["x"]
        p = f"read_counter[{i}]"
        same(case["read"], DL.read_counter(x), p)
        same(case["structure"], DL.structure(x), p + ".structure")
        same(case["structure_e"], DL.structure(x, elliott=True), p + ".structure_e")
        same(case["reduce"], {str(k): v for k, v in DL.reduce_waves(x).items()}, p + ".reduce")
        same(case["counted"], {str(k): [v[0], v[1]] for k, v in DL.reduce_counted(x).items()}, p + ".counted")
        assert DL.unfolded_123(x) is case["unfolded_123"], p
        same(case["motive"], DL.motive_segs(x), p + ".motive")


def test_wave_rules_parity():
    s2 = S3.PATTERN_DEFAULTS
    for i, case in enumerate(golden()["rules"]):
        x, done = case["x"], case["done"]
        assert PR.impulse(x, done) is case["impulse"], i
        assert PR.wedge(x, done, "contracting") is case["wedge_c"], i
        assert PR.wedge(x, done, "expanding") is case["wedge_e"], i
        assert list(PR.triangle(x, done, "contracting", 0.5)) == case["tri_c"], i
        assert list(PR.triangle(x, done, "expanding")) == case["tri_e"], i
        assert PR.zigzag(x, done, s2)[0] is case["zigzag"], i
        assert PR.flat(x, done, s2)[0] is case["flat"][0], i
        assert PR.flat(x, done, s2)[1] == case["flat"][1], i


def test_c_class_parity():
    data = golden()["misc"]["c_class"]
    for case in data["cases"]:
        C, cls = DL.c_class(case["raw"], data["base"][:case["n_base"]])
        same(case["out"], [num(C), cls], str(case["n_base"]))


# --- purity --------------------------------------------------------------------------------------------------------------

ALLOWED_IMPORTS = ("__future__", "bisect", "dataclasses", "typing", "numpy", "pandas", "app.price_action.daily_trend")


def test_the_daily_trend_is_pure():
    """No I/O, orders, brokers or database: an allow-list of imports, checked on the source ..."""
    for path in sorted(PACKAGE.glob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                modules = [f"{node.module}.{alias.name}" if (node.module or "").startswith("app") else (node.module or "")
                           for alias in node.names]
            elif isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            else:
                continue
            for m in modules:
                assert any(m == a or m.startswith(a + ".") for a in ALLOWED_IMPORTS), (path.name, m)


def test_importing_the_daily_trend_loads_nothing_else_from_the_app():
    """... and at run time: a fresh interpreter importing it loads no app module outside app.price_action."""
    code = ("import sys; import app.price_action.daily_trend.fold; "
            "print(sorted(m for m in sys.modules if m.startswith('app.') and not m.startswith('app.price_action')))")
    out = subprocess.run([sys.executable, "-c", code], cwd=BACKEND, capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "[]", out.stdout
