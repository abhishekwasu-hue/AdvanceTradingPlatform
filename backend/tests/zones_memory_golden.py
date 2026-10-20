"""Golden parity harness for the level-memory port (Z7a-2): Trade@eafc872 level_memory.py vs
app.price_action.zones.memory, on the Z7a-1 zone fixtures (tests/fixtures/zones), same style as tests/zones_golden.py.

  * `trade_outputs(LM, SD)` - Trade's level_memory (LM) and sr_dynamic (SD) on the fixtures;
  * `atp_outputs()`         - the ATP port on the same fixtures with the equivalent settings;
  * GOLDEN                  - the frozen Trade output (tests/fixtures/zones/golden_level_memory_eafc872.json.gz in the private
                              fixtures store, tests/private_fixtures.py), so ATP == Trade is checked without the Trade
                              repository on disk; the public CI skips that check with the reason.

Sections: zone_tolerance (ATR-scaled, bounded), last_touch_time, merge walks (day after day: today's dynamic S/R merged
with yesterday's remembered levels - carry, retire, distance, cap, role by price or kept), remember_dyn_sr and the
nightly apply over zone rows (Trade DataFrames vs ATP row dicts, other zone types untouched). Normalisation absorbs only
the documented differences: ATP timeframe labels and zone rows without the timeframe suffix in the type.
Regenerate (after a deliberate Trade re-sync) from backend/ with:
    ATP_PRIVATE_FIXTURES=<store> python -m tests.zones_memory_golden --write
The golden file is Trade output, so it lives only in the private fixtures store (owner decision PR-2,
docs/PUBLIC_REPO_POLICY.md section 5): --write writes it there; its new sha256 goes into
tests/private_fixtures_manifest.json in a reviewed change.
"""
import importlib
import os
import sys
import types
from typing import Any, Callable, Dict, List, Optional

import pandas as pd

from tests import private_fixtures as PF
from tests import zones_golden as G

GOLDEN = f"backend/tests/fixtures/zones/golden_level_memory_{G.TRADE_COMMIT}.json.gz"      # a private fixture (manifest path)

TOL_FRAMES = ("s15", "m5", "bn", "w1")
TOL_PARAMS = [(0.5, 0.08, 0.40), (1.0, 0.01, 2.0), (0.2, 0.5, 0.6)]
# name: (frame, grouping, merge kwargs, dynamic S/R variant of zones_golden.DYN_VARIANTS)
WALKS: Dict[str, Any] = {
    "s15/default": ("s15", "day", {}, "default"),
    "s15/retire2_cap5": ("s15", "day", {"retire_days": 2, "max_levels": 5}, "alt"),
    "s15/fixed_role": ("s15", "day", {"role_by_price": False, "retire_days": 5}, "default"),
    "m5/alt": ("m5", "day", {}, "alt"),
    "w1/hourly": ("w1", 60, {"retire_days": 1, "max_distance_pct": 0.5}, "alt"),
    "bn/weekly": ("bn", 5, {"max_distance_pct": 5.0}, "default"),
    "bn/near_cap3": ("bn", 10, {"max_distance_pct": 3.0, "max_levels": 3, "retire_days": 20}, "alt"),
}
APPLY_DAYS = (12, 20, 29)          # s15 session indices where the nightly apply is checked
TF_SUFFIX = {"15m": "15M", "30m": "30M", "1h": "60M"}


def import_trade(path: str = G.TRADE_PATH) -> Optional[types.SimpleNamespace]:
    """Trade's level_memory and sr_dynamic from the read-only checkout, or None when it is not on disk."""
    if not os.path.isfile(os.path.join(path, "level_memory.py")):
        return None
    sys.path.append(path)
    try:
        return types.SimpleNamespace(LM=importlib.import_module("level_memory"), SD=importlib.import_module("sr_dynamic"))
    finally:
        sys.path.remove(path)


# --- shared drivers (harness code, identical for both sides) -----------------------------------------------------------
def _ends(df: pd.DataFrame, grouping: Any) -> List[int]:
    """Indices where a walk stops: the last bar of each session date, or every `grouping` bars."""
    if grouping == "day":
        dates = pd.to_datetime(df["timestamp"]).dt.date.to_numpy()
        return [i for i in range(len(df)) if i == len(df) - 1 or dates[i + 1] != dates[i]]
    return list(range(grouping - 1, len(df), grouping))


def _mem(levels: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [{"level": G._num(z["level"]), "strength": G._num(z["strength"]), "formed": G._iso(z["formed_date"]), "fresh": bool(z["fresh"]),
             "role": z["role"]} for z in levels]


def _fresh(dyn: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [{"level": z["level"], "touches": z["touches"], "role": role}
            for key, role in (("support", "SUPPORT"), ("resistance", "RESISTANCE")) for z in dyn.get(key, [])]


def walk(df: pd.DataFrame, grouping: Any, dyn: Callable[[pd.DataFrame], Dict[str, Any]], merge: Callable[..., List[Dict[str, Any]]],
         kwargs: Dict[str, Any]) -> List[Any]:
    """Night after night: today's dynamic S/R merged with the levels remembered yesterday."""
    existing: List[Dict[str, Any]] = []
    out = []
    for end in _ends(df, grouping):
        sub = df.iloc[:end + 1].reset_index(drop=True)
        if len(sub) < 40:
            continue
        merged = merge(existing, _fresh(dyn(sub)), sub, float(sub["close"].iloc[-1]), sub["timestamp"].iloc[-1], **kwargs)
        out.append({"end": end, "levels": _mem(merged)})
        existing = [{"level": z["level"], "strength": z["strength"], "formed_date": z["formed_date"], "role": z["role"]} for z in merged]
    return out


def _session_cut(F: Dict[str, pd.DataFrame], day: int) -> Dict[str, pd.DataFrame]:
    """s15 and its 30m / 1h resamples cut at the end of the `day`-th session (bars stamped before the next session)."""
    s15 = F["s15"]
    dates = sorted(pd.to_datetime(s15["timestamp"]).dt.date.unique())
    stop = pd.Timestamp(dates[day]) + pd.Timedelta(days=1)
    return {tf: F[f][pd.to_datetime(F[f]["timestamp"]) < stop].reset_index(drop=True) for tf, f in (("15m", "s15"), ("30m", "s30"), ("1h", "s1h"))}


def _rows_norm(rows: List[Dict[str, Any]]) -> List[Any]:
    return sorted([[r["zone_type"], r["timeframe"], G._num(r["zone_low"]), G._num(r["zone_high"]), G._num(r["strength"]), G._iso(r["formed"])]
                   for r in rows], key=lambda x: (x[0], x[1], x[2], x[4] or 0, x[5] or ""))


# --- Trade side ---------------------------------------------------------------------------------------------------------
def trade_outputs(T: types.SimpleNamespace) -> Dict[str, Any]:
    LM, SD = T.LM, T.SD
    F = G.frames()
    out: Dict[str, Any] = {"trade_commit": G.TRADE_COMMIT}
    tol = {}
    for f in TOL_FRAMES:
        df = F[f]
        for end in (10, 14, 15, 16, 50, len(df) - 1):
            sub = df.iloc[:end + 1]
            close = float(sub["close"].iloc[-1])
            for k, (m, lo, hi) in enumerate(TOL_PARAMS):
                for p in (close, close * 1.5, close * 0.2):
                    tol[f"{f}@{end}/{k}/{round(p / close, 1)}"] = G._num(LM.zone_tolerance(sub, p, atr_mult=m, min_pct=lo, max_pct=hi))
    tol["none"] = G._num(LM.zone_tolerance(None, 100.0))
    out["zone_tolerance"] = tol

    touch = {}
    for f in ("s15", "bn"):
        df = F[f]
        for q in (0.05, 0.3, 0.5, 0.9, 0.99):
            lv = float(df["close"].quantile(q))
            for t in (0.0, 5.0, 50.0):
                touch[f"{f}/{q}/{t}"] = G._iso(LM.last_touch_time(df, lv, t))
    touch["empty"] = G._iso(LM.last_touch_time(F["s15"].iloc[:0], 100.0, 1.0))
    out["last_touch_time"] = touch

    walks = {}
    for name, (f, grouping, kw, variant) in WALKS.items():
        dkw = {G.DYN_TRADE_KW[x]: y for x, y in G.DYN_VARIANTS[variant].items()}
        walks[name] = walk(F[f], grouping, lambda d, dkw=dkw: SD.compute_dynamic_sr(d, **dkw), LM.merge_levels, kw)
    out["merge_walks"] = walks

    rem, app = {}, {}
    for day in APPLY_DAYS:
        today, before = _session_cut(F, day), _session_cut(F, day - 3)
        now = today["15m"]["timestamp"].iloc[-1]
        prev_rows, today_rows = [], []
        for tf, suffix in TF_SUFFIX.items():
            for rows, frames, stamp in ((prev_rows, before, before["15m"]["timestamp"].iloc[-1]), (today_rows, today, now)):
                d = SD.compute_dynamic_sr(frames[tf])
                for key, role in (("support", "SUPPORT"), ("resistance", "RESISTANCE")):
                    for z in d[key]:
                        rows.append({"symbol": "X", "zone_type": f"DYNAMIC_SR_{role}_{suffix}", "zone_low": z["level"], "zone_high": z["level"],
                                     "strength": z["touches"], "formed_date": stamp, "status": "ACTIVE"})
        today_rows.append({"symbol": "X", "zone_type": "BULLISH_OB", "zone_low": 100.0, "zone_high": 110.0, "strength": None, "formed_date": now,
                           "status": "ACTIVE"})
        zones_df, existing = pd.DataFrame(today_rows), pd.DataFrame(prev_rows)
        for tf, suffix in TF_SUFFIX.items():
            r = LM.remember_dyn_sr(SD.compute_dynamic_sr(today[tf]), existing, suffix, today[tf], now)
            rem[f"{day}/{tf}"] = G._levels(r)
        res = LM.apply_memory_to_zone_rows(zones_df, existing, {s: today[tf] for tf, s in TF_SUFFIX.items()}, now, lambda s: True, symbol="X")
        inv = {v: k for k, v in TF_SUFFIX.items()}
        rows_out = []
        for r in res.to_dict("records"):
            zt = str(r["zone_type"])
            if zt.startswith("DYNAMIC_SR_"):
                base, suffix = zt.rsplit("_", 1)
                rows_out.append({"zone_type": base, "timeframe": inv[suffix], "zone_low": r["zone_low"], "zone_high": r["zone_high"],
                                 "strength": r["strength"], "formed": r["formed_date"]})
            else:
                rows_out.append({"zone_type": zt, "timeframe": "1h", "zone_low": r["zone_low"], "zone_high": r["zone_high"],
                                 "strength": r["strength"], "formed": r["formed_date"]})
        app[str(day)] = _rows_norm(rows_out)
    out["remember_dyn_sr"], out["apply_memory"] = rem, app
    return out


# --- ATP side -----------------------------------------------------------------------------------------------------------
def atp_outputs() -> Dict[str, Any]:
    from app.price_action.zones import levels as L
    from app.price_action.zones import memory as M
    from app.price_action.zones.settings import resolve_settings

    F = G.frames()
    out: Dict[str, Any] = {"trade_commit": G.TRADE_COMMIT}
    tol = {}
    for f in TOL_FRAMES:
        df = F[f]
        for end in (10, 14, 15, 16, 50, len(df) - 1):
            sub = df.iloc[:end + 1]
            close = float(sub["close"].iloc[-1])
            for k, (m, lo, hi) in enumerate(TOL_PARAMS):
                s = resolve_settings({"memory_atr_mult": m, "memory_tol_min_pct": lo, "memory_tol_max_pct": hi})
                for p in (close, close * 1.5, close * 0.2):
                    tol[f"{f}@{end}/{k}/{round(p / close, 1)}"] = G._num(M.zone_tolerance(sub, p, s))
    tol["none"] = G._num(M.zone_tolerance(None, 100.0))
    out["zone_tolerance"] = tol

    touch = {}
    for f in ("s15", "bn"):
        df = F[f]
        for q in (0.05, 0.3, 0.5, 0.9, 0.99):
            lv = float(df["close"].quantile(q))
            for t in (0.0, 5.0, 50.0):
                touch[f"{f}/{q}/{t}"] = G._iso(M.last_touch_time(df, lv, t))
    touch["empty"] = G._iso(M.last_touch_time(F["s15"].iloc[:0], 100.0, 1.0))
    out["last_touch_time"] = touch

    walks = {}
    for name, (f, grouping, kw, variant) in WALKS.items():
        s = resolve_settings(G.DYN_VARIANTS[variant])
        walks[name] = walk(F[f], grouping, lambda d, s=s: L.compute_dynamic_sr(d, s), M.merge_levels, kw)
    out["merge_walks"] = walks

    rem, app = {}, {}
    for day in APPLY_DAYS:
        today, before = _session_cut(F, day), _session_cut(F, day - 3)
        now = today["15m"]["timestamp"].iloc[-1]
        prev_rows: List[Dict[str, Any]] = []
        today_rows: List[Dict[str, Any]] = []
        for tf in TF_SUFFIX:
            for rows, frames, stamp in ((prev_rows, before, before["15m"]["timestamp"].iloc[-1]), (today_rows, today, now)):
                d = L.compute_dynamic_sr(frames[tf])
                for key, role in (("support", "SUPPORT"), ("resistance", "RESISTANCE")):
                    for z in d[key]:
                        rows.append({"zone_type": f"DYNAMIC_SR_{role}", "timeframe": tf, "zone_low": z["level"], "zone_high": z["level"],
                                     "strength": z["touches"], "formed_at": stamp, "state": "fresh"})
        today_rows.append({"zone_type": "BULLISH_OB", "timeframe": "1h", "zone_low": 100.0, "zone_high": 110.0, "strength": None, "formed_at": now,
                           "state": "fresh"})
        for tf in TF_SUFFIX:
            r = M.remember_dyn_sr(L.compute_dynamic_sr(today[tf]), prev_rows, tf, today[tf], now, role_by_price=False)
            rem[f"{day}/{tf}"] = G._levels(r)
        res = M.apply_memory(today_rows, prev_rows, dict(today), now, role_by_price=False)
        app[str(day)] = _rows_norm([{**r, "formed": r["formed_at"]} for r in res])
    out["remember_dyn_sr"], out["apply_memory"] = rem, app
    return out


def load_golden() -> Dict[str, Any]:
    """The frozen Trade output from the private fixtures store; skips the calling test when the store is not mounted."""
    return G.read_golden(str(PF.path(GOLDEN)))


if __name__ == "__main__":
    T = import_trade()
    if T is None:
        sys.exit(f"Trade checkout not found at {G.TRADE_PATH} (set TRADE_REPO_PATH)")
    data = G.to_json(trade_outputs(T))
    if "--write" in sys.argv:
        out = PF.write_path(GOLDEN)
        G.write_golden(str(out), data)
        print("wrote", out, "- put its sha256 in tests/private_fixtures_manifest.json")
    st: Dict[str, int] = {"exact": 0, "approx": 0}
    problems = G.diff(data, G.to_json(atp_outputs()), stats=st)
    print(f"leaves: {st}; mismatches: {len(problems)}")
    for p in problems[:50]:
        print(" ", p)
