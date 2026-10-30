"""N1 L2: golden parity of the Daily trend port with the Trade repo's own code (tests/fixtures/daily_trend/golden.json.gz,
made by make_golden.py from Trade at the pinned revision; drift_check.py compares a newer revision).

Reproduced: every Daily state the fold produces (trend, protected origin, band, phase, wave, correction leg, impulse
extreme, maturity, targets, parent, counter, leg start, correction bars, origin risk, sharp zone, evidence, correction
zone, leg origin, target passed, wave A, alt count, pivots) for the default settings and setting variants; `state_at`;
the pivots and sigma_D; the leg reader (classify, read_counter, structure, reductions, unfolded 1-2-3, leg points, C
class); the wave rules; `prepare` / `prepare_daily`; the settings defaults. The free-text `why` is English in ATP and
only its presence is compared.

The golden file is Trade output, read from the private fixtures store (tests/private_fixtures.py): the public CI skips
the parity tests with the reason. The series are built in code from the golden's synthetic inputs
(fixtures/daily_trend/synthetic_inputs.py; test_the_series_built_in_code_are_the_golden_s ties the two), so the
no-look-ahead tests always run."""
import ast
import math
import subprocess
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict

import pandas as pd
import pytest

from app.price_action.daily_trend import context as CTX
from app.price_action.daily_trend import fold as DD
from app.price_action.daily_trend import legs as DL
from app.price_action.daily_trend import rules as PR
from app.price_action.daily_trend import settings as S3
from tests import private_fixtures as PF
from tests.fixtures.daily_trend import synthetic_inputs as DTI

BACKEND = Path(__file__).resolve().parents[1]
GOLDEN = "backend/tests/fixtures/daily_trend/golden.json.gz"      # a private fixture (manifest path)
PACKAGE = BACKEND / "app" / "price_action" / "daily_trend"


@lru_cache(maxsize=1)
def golden() -> Dict[str, Any]:
    """The golden file from the private fixtures store; skips the calling test when the store is not mounted."""
    return PF.load(GOLDEN)


@lru_cache(maxsize=1)
def synthetic() -> list:
    """The golden's series, built in code: [(label, raw bars)] in golden order."""
    return DTI.all_series()


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


def bars_json(df: pd.DataFrame) -> Dict[str, list]:
    return {"timestamp": [str(x) for x in df["timestamp"]], **{k: [float(x) for x in df[k]] for k in ("open", "high", "low", "close")}}


@lru_cache(maxsize=None)
def daily(sid: int) -> pd.DataFrame:
    return DD.prepare(frame(bars_json(synthetic()[sid][1])))


@lru_cache(maxsize=None)
def folded(sid: int, variant: str) -> tuple:
    return tuple(DD.fold(daily(sid), DTI.VARIANTS[variant]))


def test_the_series_built_in_code_are_the_golden_s():
    """The bars, labels and setting variants built in code are the ones Trade's outputs were recorded on."""
    g = golden()
    assert [s["seed"] for s in g["series"]] == [label for label, _ in synthetic()]
    for sid, (entry, (_, raw)) in enumerate(zip(g["series"], synthetic())):
        assert entry["bars"] == bars_json(raw), sid
    for f in g["folds"]:
        assert f["settings"] == DTI.VARIANTS[f["variant"]], f["variant"]


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
                "mature", "origin_risk", "target_passed", "q27_range_after_break", "q49_origin_moved_on_break", "flip",
                "q49_pool_trend_start"):
        assert cov.get(key, 0) > 0, key


BRANCH_LINES = {"q49_pool_trend_start": "pool.append(tstart_org)"}


def port_branch_bars(sid: int) -> Dict[str, list]:
    """The fold bars `i` where the port runs each recorded branch line (a line tracer on fold.py)."""
    import inspect
    path = DD.__file__
    lines = inspect.getsource(DD).split("\n")
    where = {}
    for name, text in BRANCH_LINES.items():
        hits = [n + 1 for n, line in enumerate(lines) if line.strip() == text]
        assert len(hits) == 1, (name, hits)
        where[hits[0]] = name
    seen: Dict[str, set] = {name: set() for name in BRANCH_LINES}

    def tracer(frame: Any, event: str, arg: Any) -> Any:
        if frame.f_code.co_filename != path:
            return None

        def local(fr: Any, ev: str, a: Any) -> Any:
            if ev == "line" and fr.f_lineno in where:
                seen[where[fr.f_lineno]].add(int(fr.f_locals.get("i", -1)))
            return local
        return local
    old = sys.gettrace()
    sys.settrace(tracer)
    try:
        DD.fold(daily(sid))
    finally:
        sys.settrace(old)
    return {name: sorted(bars) for name, bars in seen.items()}


def test_the_port_runs_the_recorded_branches_on_the_same_bars():
    """Review of #213 (L2-6): branches a state does not show (Q49's pool getting the trend start appended) are recorded
    from Trade's own fold by a line tracer; the port runs the same line on the same bars of the same series."""
    recorded = golden()["branches"]
    assert {b["branch"] for b in recorded} == set(BRANCH_LINES)
    for b in recorded:
        assert b["bars"] and port_branch_bars(b["series"])[b["branch"]] == b["bars"], b


# --- the fold ------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("index", range(30))
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


def series_of(kind: str) -> list:
    return [i for i, (label, _) in enumerate(synthetic()) if str(label).startswith(kind)]


def causal_bars(states: Any, every: int = 0, cap: int = 0) -> list:
    """Bars to re-fold on their own: every bar where flip() runs (the day after an origin break) or the trend / phase /
    protected origin changes, plus every k-th bar (`every`). `cap` keeps the first ones."""
    picks = set(range(0, len(states), every)) if every else set()
    for m in range(1, len(states)):
        a, b = states[m - 1], states[m]
        if a.phase == "origin_broken" or (a.trend, a.phase, pivot(a.protected)) != (b.trend, b.phase, pivot(b.protected)):
            picks.add(m)
    out = sorted(picks)
    return out[:cap] if cap else out


def _evenly(xs: list, k: int) -> list:
    if len(xs) <= k:
        return list(xs)
    return [xs[round(i * (len(xs) - 1) / (k - 1))] for i in range(k)]


DENSE_FLIPS, DENSE_CHANGES, DENSE_EVERY, DENSE_SHORT = 8, 8, 125, 200


def dense_bars(states: Any) -> list:
    """The CI dense subset (fits the lookahead job: about 1.5 minutes for every series): a short series (at most
    DENSE_SHORT bars) is re-folded at every bar; a long one at DENSE_FLIPS bars where flip() runs and DENSE_CHANGES bars
    where the state changes (both spread evenly over the series), plus every DENSE_EVERY-th bar. Deterministic: the same
    bars on every run."""
    if len(states) <= DENSE_SHORT:
        return list(range(len(states)))
    flips, changes = [], []
    for m in range(1, len(states)):
        a, b = states[m - 1], states[m]
        if a.phase == "origin_broken":
            flips.append(m)
        elif (a.trend, a.phase, pivot(a.protected)) != (b.trend, b.phase, pivot(b.protected)):
            changes.append(m)
    return sorted(set(_evenly(flips, DENSE_FLIPS)) | set(_evenly(changes, DENSE_CHANGES))
                  | set(range(DENSE_EVERY, len(states), DENSE_EVERY)))


def assert_causal(sid: int, bars: list) -> None:
    full = folded(sid, "default")
    for m in bars:
        same(state(full[m]), state(DD.fold(daily(sid).iloc[:m + 1])[-1]), f"causal[{sid}].{m}")


def test_no_state_depends_on_a_later_candle_fast():
    """Review of #213: the full run's state m equals the fold on the first m + 1 candles - checked on every bar where
    flip() runs or the state changes (series 0; a one-bar look-ahead in flip() fails here)."""
    bars = causal_bars(folded(0, "default"), cap=40)
    assert len(bars) >= 20
    assert_causal(0, bars)
    for sid in series_of("flip_spike"):                        # the hand-built flip() cases: every bar after the break
        assert_causal(sid, causal_bars(folded(sid, "default")))


@pytest.mark.lookahead
@pytest.mark.parametrize("sid", range(16))
def test_no_state_depends_on_a_later_candle_dense(sid):
    """The dense version (the Z10-a no-look-ahead CI job), on every golden series: `dense_bars` (every bar of a short
    series; flips, state changes and a stride on a long one - a prefix fold costs ~1 ms per bar, so every bar of a
    1,000-bar series would take minutes)."""
    if sid >= len(synthetic()):
        pytest.skip("no such series")
    bars = dense_bars(folded(sid, "default"))
    assert len(bars) >= 4
    assert_causal(sid, bars)


def test_short_series_parity():
    for case in golden()["short"]:
        states = DD.fold(daily(0).iloc[:case["m"] + 1])
        same(case["states"], [state(st) for st in states], f"short.{case['m']}")
    assert all(st.trend == "UNKNOWN" for st in DD.fold(daily(0).iloc[:S3.DEFAULTS["daily_min_sessions"]]))


def test_state_at_parity():
    for case in golden()["state_at"]:
        st = DD.state_at(list(folded(case["series"], "default")), pd.Timestamp(case["ts"]))
        assert (st.bar, st.trend) == (case["bar"], case["trend"]), case


def test_only_the_impulse_mode_is_ported_and_bad_settings_are_refused():
    for over in ({"daily_trend_mode": "minor"}, {"daily_swing_method": "zigzag"}, {"corr_degree_rule": "fib"},
                 {"q49_impulse_origin": "nearest"}):
        with pytest.raises(ValueError):
            DD.fold(daily(0).iloc[:30], over)


def test_known_at_without_bar_end_is_the_session_close():
    d = daily(0).iloc[:5].drop(columns=["bar_end"])
    assert DD.known_at_of(d) == [pd.Timestamp(t).normalize() + pd.Timedelta(hours=15, minutes=30) for t in d["timestamp"]]


def test_trend_context_parity():
    """Trade `level_reaction.trend_context` through the typed bridge (context.trend_context -> level_gate.context_from_state)."""
    cases = golden()["trend_context"]
    assert any(not c["ctx"]["ok"] for c in cases) and any(c["ctx"]["ok"] for c in cases)
    for i, c in enumerate(cases):
        raw = frame(golden()["series"][c["series"]]["bars"])
        ctx = CTX.trend_context(raw, None if c["today"] is None else pd.Timestamp(c["today"]), c["settings"])
        got = {k: (sorted(v) if k == "sides" else (None if v is None else [num(x) for x in v]) if k == "band" else v) for k, v in ctx.items()}
        same(c["ctx"], got, f"trend_context[{i}]")


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

ALLOWED_IMPORTS = ("__future__", "bisect", "dataclasses", "threading", "typing", "numpy", "pandas", "app.price_action.daily_trend",
                   "app.price_action.level_gate.gate")


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
    code = ("import sys; import app.price_action.daily_trend.fold, app.price_action.daily_trend.context; "
            "print(sorted(m for m in sys.modules if m.startswith('app.') and not m.startswith('app.price_action')))")
    out = subprocess.run([sys.executable, "-c", code], cwd=BACKEND, capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "[]", out.stdout
