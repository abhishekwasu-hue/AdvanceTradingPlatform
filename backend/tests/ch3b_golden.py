"""N4 CH3b golden parity harness: the same calls run through Trade@1e6c9ac (read-only) and through the ATP ports, both
normalised to one JSON shape.

Ports checked: opportunity_engine/structure.py -> app/price_action/structure_tracker.py (BOS / CHoCH / sweeps / states),
price_action/legs.py -> app/price_action/legs.py (impulse vs corrective legs), price_action/trendlines.py +
detectors/patterns.find_trendline -> app/price_action/trendlines.py, decision3/liquidity.py (pools, sweeps, marks) ->
app/price_action/liquidity.py.

Fixtures: the frozen synthetic bars of tests/fixtures/zones (seeded, BANKNIFTY-like; no real data, nothing from the
sealed NIFTY holdout). GOLDEN (tests/fixtures/price_action/golden_trade_1e6c9ac_ch3b.json.gz) is Trade's output, so CI
checks ATP == Trade without Trade on disk. Normalisation only drops Trade's Marathi `reasons` texts (ATP's are English).

Regenerate (only on a deliberate Trade re-sync), from backend/:
    TRADE_REPO_PATH=/path/to/trade python -m tests.ch3b_golden --write
The golden file is grandfathered in scripts/check_public_repo.py (Trade output, open question PR-2): it may not change
here; a Trade re-sync goes to the private fixtures store (docs/design/TRADE_TAKEOVER.md section 3).
The Trade files are read with `git archive` at TRADE_COMMIT into a temporary directory - the checkout is never changed.
"""
import importlib
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import types
from io import BytesIO
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from tests import zones_golden as G

TRADE_COMMIT = "1e6c9ac"
TRADE_PATH = os.environ.get("TRADE_REPO_PATH", "/home/user/trade")
GOLDEN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "price_action", f"golden_trade_{TRADE_COMMIT}_ch3b.json.gz")
TRADE_FILES = ("signals.py", "htf_alignment.py", "sr_dynamic.py", "opportunity_engine/__init__.py", "opportunity_engine/config.py",
               "opportunity_engine/measures.py", "opportunity_engine/structure.py", "opportunity_engine/detectors/__init__.py",
               "opportunity_engine/detectors/base.py", "opportunity_engine/detectors/patterns.py", "price_action/__init__.py",
               "price_action/legs.py", "price_action/trendlines.py", "decision3/liquidity.py")
STRUCTURE_FRAMES = {"s15": "15m", "m5": "5m", "bn": "1d", "s1h": "1h"}
LEG_FRAMES = ("s15", "m5", "bn", "s1h")
TRENDLINE_FRAMES = ("s15", "m5", "bn", "s1h", "m60")
CUTS = (0.35, 0.6, 0.85, 1.0)                 # each frame also cut at these fractions (the trendline at earlier bars)
LIQ_FRAMES = ("s15", "m5")
LIQ_BARS = (0.3, 0.5, 0.7, 0.9, 1.0)
SWEEP_LOOKBACK = 30


def frames() -> Dict[str, pd.DataFrame]:
    f = G.frames()
    return {k: f[k].reset_index(drop=True) for k in ("s15", "m5", "bn", "s1h", "m60")}


# --- loading ------------------------------------------------------------------------------------------------------------
def import_trade(path: str = TRADE_PATH) -> Optional[types.SimpleNamespace]:
    """Trade's modules at TRADE_COMMIT, from `git archive` into a temporary directory (None when the repository is not on
    disk). plotly (only for Trade's chart overlays) is stubbed; sys.path / sys.modules are restored afterwards."""
    if not os.path.isdir(os.path.join(path, ".git")):
        return None
    blob = subprocess.run(["git", "-C", path, "archive", TRADE_COMMIT, *TRADE_FILES], check=True, capture_output=True).stdout
    tmp = tempfile.mkdtemp(prefix="trade-ch3b-")
    with tarfile.open(fileobj=BytesIO(blob)) as tar:
        tar.extractall(tmp)  # noqa: S202 - our own git archive of named files
    names = ("signals", "htf_alignment", "sr_dynamic", "opportunity_engine", "price_action", "decision3")
    saved = {k: v for k, v in sys.modules.items() if k.split(".")[0] in names or k.startswith("plotly")}
    for k in saved:
        sys.modules.pop(k)
    for name in ("plotly", "plotly.graph_objects"):
        sys.modules[name] = types.ModuleType(name)
    sys.path.insert(0, tmp)
    try:
        ns = types.SimpleNamespace(S=importlib.import_module("opportunity_engine.structure"), L=importlib.import_module("price_action.legs"),
                                   T=importlib.import_module("price_action.trendlines"), Q=importlib.import_module("decision3.liquidity"))
    finally:
        sys.path.remove(tmp)
        for k in [k for k in sys.modules if k.split(".")[0] in names or k.startswith("plotly")]:
            sys.modules.pop(k)
        sys.modules.update(saved)
        shutil.rmtree(tmp, ignore_errors=True)                         # the modules are loaded; the copy is not needed
    return ns


def atp() -> types.SimpleNamespace:
    from app.price_action import legs, liquidity, structure_tracker, trendlines
    return types.SimpleNamespace(S=structure_tracker, L=legs, T=trendlines, Q=liquidity)


# --- normalisation ------------------------------------------------------------------------------------------------------
def _j(x: Any) -> Any:
    if isinstance(x, dict):
        return {str(k): _j(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_j(v) for v in x]
    if isinstance(x, (pd.Timestamp, np.datetime64)):
        return str(pd.Timestamp(x))
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (np.floating, float)):
        v = float(x)
        return None if not np.isfinite(v) else v
    if isinstance(x, np.bool_):
        return bool(x)
    return x


def _leg(d: Dict[str, Any]) -> Dict[str, Any]:
    d = dict(d)
    d.pop("reasons", None)
    return _j(d)


def _view(side: types.SimpleNamespace, is_trade: bool, df: pd.DataFrame, pivots: List[Any], sigma: np.ndarray) -> Any:
    ts = pd.to_datetime(df["timestamp"]).reset_index(drop=True)
    h, l, c = (df[k].to_numpy(float) for k in ("high", "low", "close"))
    if is_trade:
        order = sorted(pivots, key=lambda p: p.confirm_bar)
        known_by = [sum(1 for p in order if p.confirm_bar <= t) for t in range(len(df))]
        lv = types.SimpleNamespace(ts=ts, A={"high": h, "low": l, "close": c}, sig1h=sigma,
                                   known_pivots=lambda t: order[:known_by[t]] if t < len(known_by) else list(order))
        s = {"liquidity_eq_sigma": 0.15, "accept_closes": 3, "show_distance_sigma": 12.0, "liq_marks_per_side": 3}
        return types.SimpleNamespace(levels=lv, s=s)
    Q = side.Q
    return Q.View(ts=ts, high=h, low=l, close=c, sigma=sigma, pivots=[Q.KnownPivot(p.kind, p.price, p.confirm_bar) for p in pivots])


def outputs(side: types.SimpleNamespace, is_trade: bool) -> Dict[str, Any]:
    F = frames()
    S, L, T, Q = side.S, side.L, side.T, side.Q
    out: Dict[str, Any] = {"trade_commit": TRADE_COMMIT}

    structure = {}
    for name, tf in STRUCTURE_FRAMES.items():
        df = F[name]
        tr = S.StructureTracker(tf)
        for be, o, h, l, c in zip(df["timestamp"], df["open"], df["high"], df["low"], df["close"]):
            tr.on_bar(pd.Timestamp(be), float(o), float(h), float(l), float(c))
        structure[name] = {"events": _j(tr.events), "changes": _j(tr.changes), "snapshot": _j(tr.snapshot()),
                           "swings": [_j(vars(sw)) for sw in tr.swings], "rr_hist": _j(tr.rr_hist)}
    out["structure"] = structure

    legs = {}
    for name in LEG_FRAMES:
        df = F[name]
        lg, swing, internal = L.build_legs(df)
        cur = L.current_leg(df)
        cut = L.build_legs(df, upto=int(len(df) * 0.6))[0]
        legs[name] = {"legs": [_leg(x.as_dict()) for x in lg], "swing": [_j(vars(p)) for p in swing], "internal": [_j(vars(p)) for p in internal],
                      "current": None if cur is None else _leg(cur.as_dict()), "cut60": [_leg(x.as_dict()) for x in cut]}
    out["legs"] = legs

    lines = {}
    for name in TRENDLINE_FRAMES:
        df = F[name]
        for frac in CUTS:
            n = int(len(df) * frac)
            got = T.detect_trendlines(df.iloc[:n])
            rows = []
            for ln in got:
                tl = ln["line"]
                rows.append(_j({**{k: v for k, v in ln.items() if k != "line"},
                                "line": {"m": tl.m, "b": tl.b, "a": tl.a, "b_idx": tl.b_idx, "touches": tl.touches, "after_b": tl.after_b,
                                         "between_ext": tl.between_ext}}))
            lines[f"{name}@{frac}"] = rows
    out["trendlines"] = lines

    liq = {}
    for name in LIQ_FRAMES:
        df = F[name]
        mr, swing, _internal = L.pivots_for(df)
        V = _view(side, is_trade, df, swing, mr)
        for frac in LIQ_BARS:
            t = int(len(df) * frac) - 1
            sw = Q.sweeps_and_traps(V, t, SWEEP_LOOKBACK)[0] if is_trade else Q.sweeps(V, t, SWEEP_LOOKBACK)
            liq[f"{name}@{t}"] = {"pools": _j(Q.pools(V, t)), "sweeps": _j(sw), "marks": _j(Q.pool_marks(V, t))}
    out["liquidity"] = liq
    return out


def load_golden() -> Dict[str, Any]:
    return G.read_golden(GOLDEN)


if __name__ == "__main__":
    if "--write" in sys.argv:
        trade = import_trade()
        if trade is None:
            raise SystemExit(f"no Trade repository at {TRADE_PATH}")
        os.makedirs(os.path.dirname(GOLDEN), exist_ok=True)
        G.write_golden(GOLDEN, outputs(trade, True))
        print(f"wrote {GOLDEN}")
