"""Golden parity harness for the market-zones port (Z7a-1): the same calls run through Trade (read-only checkout) and
through ATP, both normalised to one JSON shape.

  * `trade_outputs(T)` - Trade@eafc872 functions on the fixtures (T = the imported Trade modules, see `import_trade`).
  * `atp_outputs()`    - the ATP port on the same fixtures with the equivalent settings.
  * GOLDEN             - the frozen `trade_outputs` (tests/fixtures/zones/golden_trade_eafc872.json.gz), so CI checks
                         ATP == Trade without the Trade repository on disk.

Normalisation only absorbs the deliberate, documented differences: ATP timeframe labels ("15m" for Trade's "15M" /
"15minute", "1h" for "60M" / "1hour" / "1H" frames), ASCII tags in sr_v3, zone status FILLED/ACTIVE -> state
tested/fresh (compared on ATP's `first_touch` event - its `state` is the ATP lifecycle since Z7a-2), and a dynamic S/R
row's formed time (not compared). Regenerate the golden file (after a deliberate Trade
re-sync) from backend/ with:  python -m tests.zones_golden --write
The golden file is grandfathered in scripts/check_public_repo.py (Trade output, open question PR-2): it may not change
here; a Trade re-sync goes to the private fixtures store (docs/design/TRADE_TAKEOVER.md section 3).
"""
import gzip
import importlib
import importlib.util
import json
import math
import os
import sys
import types
from typing import Any, Dict, List, Optional

import pandas as pd

TRADE_COMMIT = "eafc872"
TRADE_PATH = os.environ.get("TRADE_REPO_PATH", "/home/user/trade")
FIXTURE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "zones")
GOLDEN = os.path.join(FIXTURE_DIR, f"golden_trade_{TRADE_COMMIT}.json.gz")
TRADE_MODULES = ("market_zones", "signals", "sr_dynamic", "sr_levels_v3", "mtf_pullback_strategy")


# --- fixtures -----------------------------------------------------------------------------------------------------------
def load(name: str) -> pd.DataFrame:
    return pd.read_csv(os.path.join(FIXTURE_DIR, f"{name}.csv"), parse_dates=["timestamp"])


def resample(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    """Trade tests' resample (label / closed left) - used the same way for both sides."""
    d = df.set_index("timestamp").resample(rule, label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna(subset=["open"])
    return d.reset_index()


def fake_ohlc(n: int, freq: str = "1h", start_price: float = 24000.0) -> pd.DataFrame:
    """Trade tests/test_market_zones.py::_fake_ohlc (a regular, deterministic oscillation)."""
    dates = pd.date_range("2026-01-01 09:15", periods=n, freq=freq)
    closes = [start_price + (i % 20) * 5 - (i % 7) * 3 for i in range(n)]
    return pd.DataFrame({"timestamp": dates, "open": closes, "high": [c + 8 for c in closes], "low": [c - 8 for c in closes],
                         "close": closes})


def frames() -> Dict[str, pd.DataFrame]:
    m5, s15 = load("market_5m"), load("session_15m")
    return {
        "bn": load("bn_daily"), "w1": load("walk_1m"),
        "m5": m5, "m15": resample(m5, "15min"), "m30": resample(m5, "30min"), "m60": resample(m5, "1h"),
        "s15": s15, "s30": resample(s15, "30min"), "s1h": resample(s15, "1h"),
        "f1h": fake_ohlc(300), "f15": fake_ohlc(300, "15min"), "f15r": fake_ohlc(150, "15min"), "f1m": fake_ohlc(150, "1min"),
        "f5m": fake_ohlc(150, "5min"), "f30": fake_ohlc(150, "30min"), "f60": fake_ohlc(150, "60min"),
    }


# Parameter variants run through both sides (Trade keyword names -> ATP ZoneSettings fields).
ZONE_FRAMES = ("bn", "m5", "w1", "s15", "s1h")
ZONE_VARIANTS = {"default": {}, "fast": {"impulse_mult": 1.2, "avg_window": 10}, "wide_base": {"base_lookback": 2, "base_tightness_mult": 0.9}}
SR_CALLS = [("bn", 3, 0.3, 3), ("bn", 5, 1.0, 5), ("s15", 3, 0.3, 10), ("s1h", 3, 0.3, 5), ("m15", 3, 0.3, 10), ("w1", 4, 0.05, 6)]
DYN_VARIANTS = {"default": {}, "tick": {"dyn_mintick": 0.05},
                "alt": {"dyn_prd": 5, "dyn_maxnumpp": 10, "dyn_channel_w_pct": 5.0, "dyn_maxnumsr": 8, "dyn_min_strength": 1}}
DYN_TRADE_KW = {"dyn_prd": "prd", "dyn_maxnumpp": "maxnumpp", "dyn_channel_w_pct": "channel_w_pct", "dyn_maxnumsr": "maxnumsr",
                "dyn_min_strength": "min_strength", "dyn_mintick": "mintick"}
DYN_FRAMES = ("m5", "m15", "s15", "s30", "s1h", "bn")
V3_TF = {"5minute": "5m", "15minute": "15m", "30minute": "30m", "1hour": "1h", "4hour": "4h", "day": "1d", "week": "1w"}
V3_CASES = {
    "market": ({"5m": "m5", "15m": "m15", "30m": "m30", "1h": "m60"}, {}),
    "session": ({"15m": "s15", "30m": "s30", "1h": "s1h"}, {}),
    "session_chop": ({"15m": "s15", "30m": "s30", "1h": "s1h"},
                     {"chop_filter": True, "reject_redepart": True, "flip_min_break_atr": 0.5, "min_base_points": 20.0}),
    "session_gaps": ({"15m": "s15", "30m": "s30", "1h": "s1h"}, {"gap_max_age_days": 40, "gap_min_pct": 0.1}),
    "daily": ({"1d": "bn"}, {}),
}


# --- normalisation ------------------------------------------------------------------------------------------------------
def _iso(t: Any) -> Optional[str]:
    if t is None or (isinstance(t, float) and math.isnan(t)):
        return None
    return pd.Timestamp(t).isoformat()


def _num(x: Any) -> Any:
    if x is None:
        return None
    if isinstance(x, bool):
        return x
    try:
        f = float(x)
    except (TypeError, ValueError):
        return x
    return None if math.isnan(f) else f


def _zone(z: Dict[str, Any], time_key: str) -> Dict[str, Any]:
    return {"zone_type": z["zone_type"], "zone_low": _num(z["zone_low"]), "zone_high": _num(z["zone_high"]), "formed": _iso(z[time_key])}


def _levels(d: Dict[str, Any]) -> Dict[str, Any]:
    return {side: [{"level": _num(x["level"]), "touches": int(x["touches"])} for x in d.get(side, [])] for side in ("support", "resistance")}


def _rows_sorted(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(rows, key=lambda r: (r["zone_type"], r["timeframe"], r["formed"] or "", r["zone_low"], r["zone_high"], str(r["strength"])))


TRADE_SUFFIX_TF = {"1M": "1m", "5M": "5m", "15M": "15m", "30M": "30m", "60M": "1h"}
# The reference implementation (Trade) writes these reasons in Marathi; ATP is English only, so they are mapped to English.
# The Marathi side is written with \u escapes (no Devanagari in ATP source - scripts/check_english_only.py).
TRADE_TAGS = {"GAP↑": "GAP_UP", "GAP↓": "GAP_DOWN", "FLIP✓": "FLIP_RETESTED", "\u0926\u094b\u0928\u094d\u0939\u0940-\u092c\u093e\u091c\u0942": "BOTH_SIDES"}


def _trade_row(r: Dict[str, Any], zone_tf: str, sr_tf: str, gap_tf: str) -> Dict[str, Any]:
    zt = str(r["zone_type"])
    if zt.startswith("DYNAMIC_SR_"):
        base, suffix = zt.rsplit("_", 1)
        zone_type, tf, formed = base, TRADE_SUFFIX_TF[suffix], None
    else:
        zone_type, formed = zt, _iso(r["formed_date"])
        tf = sr_tf if zt in ("SUPPORT", "RESISTANCE") else gap_tf if zt in ("UP_GAP", "DOWN_GAP") else zone_tf
    strength = r.get("strength")
    strength = None if strength is None or (isinstance(strength, float) and math.isnan(strength)) else int(strength)
    return {"zone_type": zone_type, "timeframe": tf, "zone_low": _num(r["zone_low"]), "zone_high": _num(r["zone_high"]),
            "strength": strength, "formed": formed, "state": "tested" if r["status"] == "FILLED" else "fresh"}


def _atp_row(r: Dict[str, Any]) -> Dict[str, Any]:
    dyn = r["zone_type"].startswith("DYNAMIC_SR_")
    return {"zone_type": r["zone_type"], "timeframe": r["timeframe"], "zone_low": _num(r["zone_low"]), "zone_high": _num(r["zone_high"]),
            "strength": None if r["strength"] is None else int(r["strength"]), "formed": None if dyn else _iso(r["formed_at"]),
            # Trade's ACTIVE / FILLED is ATP's first-touch event (Z7a-2: `state` is the ATP lifecycle, tracked from confirmation)
            "state": "tested" if r["first_touch"] is not None else "fresh"}


def _tag(t: str) -> str:
    if t in TRADE_TAGS:
        return TRADE_TAGS[t]
    return t.replace("×", "x")


def _v3_level(z: Dict[str, Any], tf_map: Dict[str, str], tag_map) -> Dict[str, Any]:
    anchor = z.get("anchor")
    return {
        "level": _num(z["level"]), "low": _num(z["low"]), "high": _num(z["high"]), "mid": _num(z["mid"]),
        "core_low": _num(z["core_low"]), "core_high": _num(z["core_high"]), "role": z["role"], "score": _num(z["score"]),
        "grade": z["grade"], "tags": [tag_map(t) for t in z["tags"]], "tfs": [tf_map.get(t, t) for t in z["tfs"]],
        "pivot_count": int(z["pivot_count"]), "components": {k: _num(v) for k, v in z["components"].items()},
        "distance_pct": _num(z["distance_pct"]), "origin": z["origin"], "flipped": bool(z["flipped"]), "retested": bool(z["retested"]),
        "accept_ratio": _num(z["accept_ratio"]), "rejections": int(z["rejections"]), "rej_strong": int(z["rej_strong"]),
        "keys": list(z["keys"]), "gap_kind": z["gap_kind"], "gap_fill_pct": _num(z["gap"]["fill_pct"]) if z["gap"] else None,
        "anchor": None if anchor is None else {"price": _num(anchor["price"]), "tf": tf_map.get(anchor["tf"], anchor["tf"]),
                                               "kind": anchor["kind"], "ts": _iso(anchor["ts"]), "name": anchor.get("name")},
        "crossings": z.get("crossings"),
    }


def _v3(res: Dict[str, Any], display: List[Dict[str, Any]], tf_map: Dict[str, str], tag_map) -> Dict[str, Any]:
    meta = res["meta"]
    return {"levels": [_v3_level(z, tf_map, tag_map) for z in res["levels"]],
            "display": [_num(z["level"]) for z in display],
            "meta": {"price": _num(meta.get("price")), "tol": _num(meta.get("tol")), "atr_ref": _num(meta.get("atr_ref")),
                     "pivot_count": meta.get("pivot_count"), "ref_tf": tf_map.get(meta.get("ref_tf"), meta.get("ref_tf"))}}


def _gaps(gs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [{"kind": g["kind"], "low": _num(g["low"]), "high": _num(g["high"]), "date": str(g["date"]), "fill_pct": _num(g["fill_pct"])}
            for g in gs]


# --- Trade side ---------------------------------------------------------------------------------------------------------
def import_trade(path: str = TRADE_PATH) -> Optional[types.SimpleNamespace]:
    """The Trade modules from a read-only checkout, or None when it is not on disk. plotly (only used by Trade's chart
    overlays) is stubbed for the import when it is not installed; sys.path is restored afterwards."""
    if not os.path.isfile(os.path.join(path, "market_zones.py")):
        return None
    stubs = []
    if importlib.util.find_spec("plotly") is None:
        for name in ("plotly", "plotly.graph_objects"):
            sys.modules[name] = types.ModuleType(name)
            stubs.append(name)
    sys.path.append(path)
    try:
        mods = {name: importlib.import_module(name) for name in TRADE_MODULES}
    finally:
        sys.path.remove(path)
        for name in stubs:
            sys.modules.pop(name, None)
    return types.SimpleNamespace(**mods)


def trade_outputs(T: types.SimpleNamespace) -> Dict[str, Any]:
    F = frames()
    mz, sig, dyn, v3, mtf = T.market_zones, T.signals, T.sr_dynamic, T.sr_levels_v3, T.mtf_pullback_strategy
    out: Dict[str, Any] = {"trade_commit": TRADE_COMMIT}

    ob, ds, touched = {}, {}, {}
    for f in ZONE_FRAMES:
        df = F[f]
        for v, k in ZONE_VARIANTS.items():
            ob_kw = {x: y for x, y in k.items() if x in ("impulse_mult", "avg_window")}
            blocks = mz.detect_order_blocks(df, **ob_kw)
            zones = mz.detect_demand_supply_zones(df, **k)
            ob[f"{f}/{v}"] = [_zone(z, "formed_date") for z in blocks]
            ds[f"{f}/{v}"] = [_zone(z, "formed_date") for z in zones]
            if v == "default":
                touched[f] = [bool(mz.is_zone_mitigated(z["zone_low"], z["zone_high"], df[df["timestamp"] > z["formed_date"]].iloc[1:]))
                              for z in blocks + zones]
    out["order_blocks"], out["demand_supply"], out["first_touch"] = ob, ds, touched

    sw = {}
    for f in ("bn", "s15", "m15"):
        for order in (3, 5):
            hi, lo = sig.find_swings(F[f], order=order)
            mh, ml = sig.filter_major_swings(F[f], hi, lo, min_move_pct=0.5)
            mh1, ml1 = sig.filter_major_swings(F[f], hi, lo, min_move_pct=1.0)
            sw[f"{f}/{order}"] = {"high": list(map(int, hi)), "low": list(map(int, lo)), "major_0.5": [list(map(int, mh)), list(map(int, ml))],
                                  "major_1.0": [list(map(int, mh1)), list(map(int, ml1))]}
    out["swings"] = sw
    out["sr_levels"] = {f"{f}/{o}/{t}/{n}": _levels(sig.find_support_resistance_levels(F[f], order=o, cluster_tolerance_pct=t, top_n=n))
                        for f, o, t, n in SR_CALLS}

    dy = {}
    for v, k in DYN_VARIANTS.items():
        tk = {DYN_TRADE_KW[x]: y for x, y in k.items()}
        for end in range(400, 900, 25):
            dy[f"w1@{end}/{v}"] = _levels(dyn.compute_dynamic_sr(F["w1"].iloc[:end + 1].reset_index(drop=True), **tk))
        for f in DYN_FRAMES:
            dy[f"{f}/{v}"] = _levels(dyn.compute_dynamic_sr(F[f], **tk))
    out["dynamic_sr"] = dy

    gaps = {}
    for f in ("s15", "bn"):
        std = F[f].rename(columns={"timestamp": "Date", "open": "Open", "high": "High", "low": "Low", "close": "Close"})
        for mg in (0.3, 0.1):
            gaps[f"{f}/{mg}"] = [{"zone_type": g["kind"], "zone_low": _num(g["gap_low"]), "zone_high": _num(g["gap_high"]),
                                  "formed": _iso(g["gap_time"])} for g in mtf.find_overnight_gaps(std, mg)]
    out["overnight_gaps"] = gaps

    obr = {}
    for f in ("s15", "m5"):
        for end in range(30, len(F[f]), 37):
            sub = F[f].iloc[:end + 1].reset_index(drop=True)
            for d in ("BULLISH", "BEARISH"):
                b = sig.find_order_block(sub, d)
                obr[f"{f}@{end}/{d}"] = None if b is None else {"ob_high": _num(b["ob_high"]), "ob_low": _num(b["ob_low"]), "ob_index": int(b["ob_index"]),
                                                                "retest": bool(sig.is_retesting_order_block(sub, b, d))}
    out["order_block_retest"] = obr

    out["role"] = [mz.compute_current_role(lo, hi, p) for lo, hi, p in ROLE_GRID]

    az = {}
    for name, (fr, dyn_fr) in ALL_ZONE_CASES.items():
        kwargs = {TRADE_DYN_ARG[tf]: F[f] for tf, f in dyn_fr.items() if tf in TRADE_DYN_ARG}
        rows = mz.compute_all_zones(F[fr["1h"]], F[fr["15m"]], "X", **kwargs)
        az[name] = _rows_sorted([_trade_row(r, "1h", "1h", "15m") for r in rows.to_dict("records")])
    out["all_zones"] = az
    intr = mz.compute_intraday_sr_zones("X", df_15m_recent=F["s15"], df_30m_recent=F["s30"], df_60m_recent=F["s1h"])
    out["intraday"] = _rows_sorted([_trade_row(r, "1h", "1h", "15m") for r in intr.to_dict("records")])

    conf = {}
    for name, (tfs, price_off) in CONFLUENCE_CASES.items():
        base = F[tfs[0][1]]["close"].iloc[-1]
        table = mz.compute_5m_15m_confluence_table(float(base) + price_off, {lbl: F[f] for lbl, f in tfs})
        conf[name] = [{k: _num(v) if k != "timeframe" and k != "order_block_type" else v for k, v in r.items()} for r in table.to_dict("records")]
    out["confluence"] = conf

    v3o = {}
    for name, (fr, cfg_over) in V3_CASES.items():
        inv = {a: t for t, a in V3_TF.items()}
        cfg = v3.SRConfig(**cfg_over)
        res = v3.compute_sr_v3({inv[tf]: F[f] for tf, f in fr.items()}, cfg=cfg)
        v3o[name] = _v3(res, v3.select_display_levels(res["levels"], res["meta"].get("price"), cfg), V3_TF, _tag)
    out["sr_v3"] = v3o
    s15 = F["s15"]
    out["sr_v3_gaps"] = {f"{mg}/{age}": _gaps(v3.find_unfilled_gaps(s15, mg, age)) for mg, age in ((0.2, 10), (0.1, 40), (0.5, 40))}
    out["sr_v3_flip"] = {}
    for lvl, origin in flip_grid(s15):
        since = s15["timestamp"].iloc[100]
        out["sr_v3_flip"][f"{lvl}/{origin}"] = {
            "reversal": v3.detect_role_reversal(lvl - 10, lvl + 10, origin, s15, since),
            "acceptance": v3.detect_flip_acceptance(lvl, 15.0, origin, s15, since),
            "crossings": v3.count_crossings(s15, lvl, 15.0, since),
            "rejections": v3.count_rejections(s15, lvl, 15.0, since, v3.compute_atr(s15), v3.SRConfig()),
        }
    return out


ROLE_GRID = [(100.0, 110.0, 104.0), (100.0, 110.0, 105.0), (100.0, 110.0, 106.0), (100.0, 100.0, 100.0), (50.0, 60.0, 70.0)]
TRADE_DYN_ARG = {"15m": "df_15m_recent", "30m": "df_30m_recent", "1h": "df_60m_recent", "1m": "df_1m_recent", "5m": "df_5m_recent"}
ALL_ZONE_CASES = {
    # name: ({"1h": history 1h, "15m": history 15m}, {dynamic tf: recent frame})
    "session": ({"1h": "s1h", "15m": "s15"}, {"30m": "s30", "1h": "s1h"}),
    "fake": ({"1h": "f1h", "15m": "f15"}, {"15m": "f15r", "1m": "f1m", "5m": "f5m", "30m": "f30", "1h": "f60"}),
    "market": ({"1h": "m60", "15m": "m15"}, {"5m": "m5", "1m": "w1"}),
    "daily": ({"1h": "bn", "15m": "bn"}, {}),
}
CONFLUENCE_CASES = {"market": ([("5M", "m5"), ("15M", "m15")], 0.0), "market_up": ([("5M", "m5"), ("15M", "m15")], 60.0),
                    "session": ([("15M", "s15"), ("1H", "s1h")], 0.0), "session_down": ([("15M", "s15"), ("1H", "s1h")], -150.0)}


def flip_grid(df: pd.DataFrame) -> List[Any]:
    """Levels across the fixture's own price range (deciles of the closes), each as a former resistance and support."""
    return [(round(float(q), 2), origin) for q in df["close"].quantile([0.1, 0.3, 0.5, 0.7, 0.9]) for origin in ("R", "S")]


# --- ATP side -----------------------------------------------------------------------------------------------------------
def atp_outputs() -> Dict[str, Any]:
    from app.price_action.swings import swing_indices
    from app.price_action.zones import compute as C
    from app.price_action.zones import detect as D
    from app.price_action.zones import levels as L
    from app.price_action.zones import sr_v3 as V
    from app.price_action.zones.settings import resolve_settings

    F = frames()
    out: Dict[str, Any] = {"trade_commit": TRADE_COMMIT}
    ob, ds, touched = {}, {}, {}
    for f in ZONE_FRAMES:
        df = F[f]
        for v, k in ZONE_VARIANTS.items():
            s = resolve_settings(k)
            blocks, zones = D.detect_order_blocks(df, s), D.detect_demand_supply_zones(df, s)
            ob[f"{f}/{v}"] = [_zone(z, "formed_at") for z in blocks]
            ds[f"{f}/{v}"] = [_zone(z, "formed_at") for z in zones]
            if v == "default":
                touched[f] = [D.first_touch_event(z["zone_low"], z["zone_high"], df[df["timestamp"] > z["formed_at"]].iloc[1:]) is not None
                              for z in blocks + zones]
    out["order_blocks"], out["demand_supply"], out["first_touch"] = ob, ds, touched

    sw = {}
    for f in ("bn", "s15", "m15"):
        for order in (3, 5):
            hi, lo = swing_indices(F[f], order)
            mh, ml = L.filter_major_swings(F[f], hi, lo, min_move_pct=0.5)
            mh1, ml1 = L.filter_major_swings(F[f], hi, lo, min_move_pct=1.0)
            sw[f"{f}/{order}"] = {"high": hi, "low": lo, "major_0.5": [mh, ml], "major_1.0": [mh1, ml1]}
    out["swings"] = sw
    out["sr_levels"] = {f"{f}/{o}/{t}/{n}": _levels(L.find_support_resistance_levels(F[f], order=o, cluster_tolerance_pct=t, top_n=n))
                        for f, o, t, n in SR_CALLS}

    dy = {}
    for v, k in DYN_VARIANTS.items():
        s = resolve_settings(k)
        for end in range(400, 900, 25):
            dy[f"w1@{end}/{v}"] = _levels(L.compute_dynamic_sr(F["w1"].iloc[:end + 1].reset_index(drop=True), s))
        for f in DYN_FRAMES:
            dy[f"{f}/{v}"] = _levels(L.compute_dynamic_sr(F[f], s))
    out["dynamic_sr"] = dy

    gaps = {}
    for f in ("s15", "bn"):
        for mg in (0.3, 0.1):
            gaps[f"{f}/{mg}"] = [_zone(g, "formed_at") for g in D.find_overnight_gaps(F[f], resolve_settings({"gap_min_pct": mg}))]
    out["overnight_gaps"] = gaps

    obr = {}
    for f in ("s15", "m5"):
        for end in range(30, len(F[f]), 37):
            sub = F[f].iloc[:end + 1].reset_index(drop=True)
            for d in ("BULLISH", "BEARISH"):
                b = D.find_order_block(sub, d)
                obr[f"{f}@{end}/{d}"] = None if b is None else {"ob_high": b["ob_high"], "ob_low": b["ob_low"], "ob_index": b["ob_index"],
                                                                "retest": D.is_retesting_order_block(sub, b, d)}
    out["order_block_retest"] = obr

    out["role"] = [D.role_by_price(lo, hi, p) for lo, hi, p in ROLE_GRID]

    az = {}
    for name, (fr, dyn_fr) in ALL_ZONE_CASES.items():
        dyn_frames = {tf: F[f] for tf, f in dyn_fr.items()}
        dyn_frames.setdefault("15m", F[fr["15m"]])                      # Trade's fallback: the 15m history
        rows = C.compute_all_zones({"1h": F[fr["1h"]], "15m": F[fr["15m"]]}, dyn_frames)
        az[name] = _rows_sorted([_atp_row(r) for r in rows])
    out["all_zones"] = az
    intr = C.compute_intraday_sr_zones({"15m": F["s15"], "30m": F["s30"], "1h": F["s1h"]})
    out["intraday"] = _rows_sorted([_atp_row(r) for r in intr])

    conf = {}
    lbl_tf = {"5M": "5m", "15M": "15m", "1H": "1h"}
    for name, (tfs, price_off) in CONFLUENCE_CASES.items():
        base = F[tfs[0][1]]["close"].iloc[-1]
        s = resolve_settings({"confluence_timeframes": [lbl_tf[lbl] for lbl, _ in tfs]})
        table = C.compute_confluence_table(float(base) + price_off, {lbl_tf[lbl]: F[f] for lbl, f in tfs}, s)
        inv = {v: k for k, v in lbl_tf.items()}
        conf[name] = [{k: (inv[v] if k == "timeframe" else v if k == "order_block_type" else _num(v)) for k, v in r.items()} for r in table]
    out["confluence"] = conf

    v3o = {}
    for name, (fr, cfg_over) in V3_CASES.items():
        s = resolve_settings({"sr_v3": cfg_over})
        res = V.compute_sr_v3({tf: F[f] for tf, f in fr.items()}, settings=s)
        v3o[name] = _v3(res, V.select_display_levels(res["levels"], s), {}, lambda t: t)
    out["sr_v3"] = v3o
    s15 = F["s15"]
    out["sr_v3_gaps"] = {f"{mg}/{age}": _gaps(V.find_unfilled_gaps(s15, mg, age)) for mg, age in ((0.2, 10), (0.1, 40), (0.5, 40))}
    out["sr_v3_flip"] = {}
    cfg = resolve_settings().sr_v3
    for lvl, origin in flip_grid(s15):
        since = s15["timestamp"].iloc[100]
        out["sr_v3_flip"][f"{lvl}/{origin}"] = {
            "reversal": V.detect_role_reversal(lvl - 10, lvl + 10, origin, s15, since),
            "acceptance": V.detect_flip_acceptance(lvl, 15.0, origin, s15, since),
            "crossings": V.count_crossings(s15, lvl, 15.0, since),
            "rejections": V.count_rejections(s15, lvl, 15.0, since, V.compute_atr(s15), cfg),
        }
    return out


# --- comparison ---------------------------------------------------------------------------------------------------------
def to_json(obj: Any) -> Any:
    """Plain JSON types (numpy scalars -> Python, tuples -> lists), so a fresh output compares like the frozen file."""
    return json.loads(json.dumps(obj, default=lambda o: o.item() if hasattr(o, "item") else str(o)))


def diff(expected: Any, actual: Any, path: str = "", rel: float = 1e-9, out: Optional[List[str]] = None,
         stats: Optional[Dict[str, int]] = None) -> List[str]:
    """Mismatch paths (floats within rel / 1e-9 absolute count as equal). stats counts exact and approximate leaf matches."""
    out = [] if out is None else out
    stats = {"exact": 0, "approx": 0} if stats is None else stats
    if isinstance(expected, dict) and isinstance(actual, dict):
        for k in sorted(set(expected) | set(actual)):
            if k not in expected or k not in actual:
                out.append(f"{path}/{k}: missing on {'ATP' if k not in actual else 'Trade'} side")
            else:
                diff(expected[k], actual[k], f"{path}/{k}", rel, out, stats)
    elif isinstance(expected, list) and isinstance(actual, list):
        if len(expected) != len(actual):
            out.append(f"{path}: length {len(expected)} (Trade) != {len(actual)} (ATP)")
        for i, (a, b) in enumerate(zip(expected, actual)):
            diff(a, b, f"{path}[{i}]", rel, out, stats)
    elif isinstance(expected, float) or isinstance(actual, float):
        if isinstance(expected, bool) or isinstance(actual, bool) or expected is None or actual is None:
            if expected != actual:
                out.append(f"{path}: {expected!r} != {actual!r}")
            else:
                stats["exact"] += 1
        elif expected == actual:
            stats["exact"] += 1
        elif math.isclose(float(expected), float(actual), rel_tol=rel, abs_tol=1e-9):
            stats["approx"] += 1
        else:
            out.append(f"{path}: {expected!r} != {actual!r}")
    elif expected != actual:
        out.append(f"{path}: {expected!r} != {actual!r}")
    else:
        stats["exact"] += 1
    return out


def read_golden(path: str) -> Dict[str, Any]:
    """A golden file: gzip-compressed JSON (working rule: golden fixtures over ~1,000 lines are stored compressed)."""
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return json.load(fh)


def write_golden(path: str, data: Any) -> None:
    """Deterministic gzip JSON: sorted keys, fixed layout, no name or time in the gzip header (mtime 0) - the same
    output gives byte-identical files, so a re-sync that changes nothing shows no diff."""
    body = (json.dumps(data, indent=1, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")
    with open(path, "wb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw, compresslevel=9, mtime=0) as fh:
        fh.write(body)


def load_golden() -> Dict[str, Any]:
    return read_golden(GOLDEN)


if __name__ == "__main__":
    T = import_trade()
    if T is None:
        sys.exit(f"Trade checkout not found at {TRADE_PATH} (set TRADE_REPO_PATH)")
    data = to_json(trade_outputs(T))
    if "--write" in sys.argv:
        write_golden(GOLDEN, data)
        print("wrote", GOLDEN)
    st: Dict[str, int] = {"exact": 0, "approx": 0}
    problems = diff(data, to_json(atp_outputs()), stats=st)
    print(f"leaves: {st}; mismatches: {len(problems)}")
    for p in problems[:50]:
        print(" ", p)
