"""N4 CH3b: the price-action layer's further concepts, each from a ported engine (Trade@1e6c9ac, parity-tested in
tests/test_ch3b_trade_parity.py), applied to the closed bars the layer already cut at `at`:

  * leg classes - impulse vs corrective (`app.price_action.legs`: strong / weak impulse, healthy / mixed / dangerous
    pullback, reversal, range) for the legs between the chart's own causal swings; CHoCH inside a pullback uses the next
    lower degree's swings as the internal swings;
  * structure - BOS / CHoCH / reversal / recovery / range events and the state with its protected level
    (`app.price_action.structure_tracker`, a bar-by-bar state machine);
  * liquidity - buy-side / sell-side pools (swing highs / lows of the chart's degree, equal highs / lows, previous day's
    and week's high / low) and their sweeps (`app.price_action.liquidity`);
  * trendlines - the newest falling resistance and rising support line and the channel parallel to each
    (`app.price_action.trendlines`);
  * the top-down panel - the structure state on W / D / 1H / 15m, each computed from its own bars as of the same `at`.
Every function here is pure over bars that ended by `at`, so nothing later can reach it. Reference only - no order.
"""
from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from app.price_action import causal_swings as cs
from app.price_action import legs as LG
from app.price_action import liquidity as LQ
from app.price_action import structure_tracker as ST
from app.price_action import trendlines as TL

MAX_STRUCTURE_EVENTS = 12            # default view: the newest BOS / CHoCH / reversal events (debug: all)
SWEEP_LOOKBACK = 30                  # bars searched for sweeps of the pools known before them
MTF_TFS = ("1w", "1d", "1h", "15m")
STRUCTURE_EVENTS = ("BOS", "CHOCH", "REVERSAL_CONFIRMED", "RECOVERY", "RANGE_START", "RANGE_EXIT_UP", "RANGE_EXIT_DOWN", "INIT_TREND", "INIT_RANGE")
DEBUG_EVENTS = ("PULLBACK_START", "PULLBACK_END", "SWEEP")
EVENT_NAME = {"BOS": "BOS", "CHOCH": "CHoCH", "REVERSAL_CONFIRMED": "reversal", "RECOVERY": "recovery", "RANGE_START": "range",
              "RANGE_EXIT_UP": "range exit", "RANGE_EXIT_DOWN": "range exit", "INIT_TREND": "trend", "INIT_RANGE": "range",
              "PULLBACK_START": "pullback", "PULLBACK_END": "pullback end", "SWEEP": "sweep"}
STATE_DIRECTION = {ST.UP: "up", ST.UP_PB: "up", ST.UP_WEAK: "up (weak)", ST.DN: "down", ST.DN_PB: "down", ST.DN_WEAK: "down (weak)",
                   ST.RANGE: "range", ST.INIT: "none"}
SRC_TEXT = {"swing": "swing", "equal": "equal", "PDH": "previous day high", "PDL": "previous day low", "PWH": "previous week high",
            "PWL": "previous week low"}


def _utc(t: Any) -> pd.Timestamp:
    ts = pd.Timestamp(t)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


def _epoch(t: Any) -> int:
    return int(_utc(t).timestamp())


def _num(x: float, digits: int = 6) -> float:
    return float(round(float(x), digits))


def price_text(x: float) -> str:
    return f"{x:,.2f}".replace(",", "")


def _arrow(direction: int) -> str:
    return "↑" if direction > 0 else "↓"


# ---------------------------------------------------------------------------------------------------------------------
# Leg classes
# ---------------------------------------------------------------------------------------------------------------------
def _leg_pivots(pivots: Sequence[cs.Pivot]) -> List[LG.Pivot]:
    return [LG.Pivot(p.kind, p.bar_idx, int(p.confirmed_idx if p.confirmed_idx is not None else p.bar_idx), p.price, "swing") for p in pivots]


def _class_row(lg: LG.Leg) -> Dict[str, Any]:
    f = lg.features
    return {"role": lg.role.lower(), "class": lg.label, "class_text": LG.LABEL_TEXT.get(lg.label, lg.label.lower()), "score": float(lg.score),
            "depth": f.get("depth"), "choch": bool(f.get("choch", False)), "efficiency": f.get("eff_range"), "overlap": f.get("overlap"),
            "reasons": "; ".join(lg.reasons)}


def leg_classes(frame: pd.DataFrame, confirmed: Sequence[cs.Pivot], internal: Optional[Sequence[cs.Pivot]],
                tentative: Optional[cs.Pivot] = None) -> Tuple[List[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """The class of each leg between consecutive `confirmed` swings (in order), and of the tentative leg (last confirmed ->
    the running extreme) when given - classified with the confirmed legs before it, as Trade's `current_leg` does."""
    df = frame.reset_index(drop=True)
    mr = LG.median_range(df, LG.LegConfig().n_median)
    swing = _leg_pivots(confirmed)
    inner = _leg_pivots(internal) if internal else None
    legs = LG.legs_between(df, swing, inner, mr)
    rows = [_class_row(lg) for lg in legs]
    tent = None
    if tentative is not None and swing and tentative.bar_idx > swing[-1].pivot_bar:
        last, cfg = swing[-1], LG.LegConfig()
        d = 1 if tentative.kind == "H" else -1
        b = tentative.bar_idx
        f = LG.leg_features(df, last.pivot_bar, b, d, last.price, tentative.price, mr[b], cfg)
        lg = LG.Leg(last.pivot_bar, b, len(df) - 1, d, last.price, tentative.price, f, provisional=True,
                    disp_mask=LG._disp_mask(df, last.pivot_bar, b, d, mr[b], cfg))
        LG.classify(copy.deepcopy(legs) + [lg], df, inner, cfg)
        tent = _class_row(lg)
    return rows, tent


# ---------------------------------------------------------------------------------------------------------------------
# Structure (BOS / CHoCH)
# ---------------------------------------------------------------------------------------------------------------------
def _direction_of(ev: Dict[str, Any]) -> int:
    to = ev.get("to_state") or ev.get("state") or ""
    if ev["type"] == "SWEEP":
        return 1 if ev.get("side") == "LOW" else -1
    if ev["type"] == "CHOCH":                     # a CHoCH breaks against the trend it weakens
        return -1 if to.startswith("UPTREND") else 1
    return 1 if to.startswith("UPTREND") else -1 if to.startswith("DOWNTREND") else 0


def structure(frame: pd.DataFrame, tf: str, debug: bool = False) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """(events, state) from the structure tracker fed the bars one by one. Events: BOS / CHoCH / reversal / recovery / range
    (debug adds pullbacks and the tracker's own sweeps); the newest MAX_STRUCTURE_EVENTS unless debug."""
    df = frame.reset_index(drop=True)
    tr = ST.run(df, tf)
    ts = df["timestamp"].to_numpy()
    kinds = STRUCTURE_EVENTS + (DEBUG_EVENTS if debug else ())
    rows: List[Dict[str, Any]] = []
    for ev in tr.events:
        if ev["type"] not in kinds or ev.get("price") is None:
            continue
        d = _direction_of(ev)
        name = EVENT_NAME[ev["type"]]
        swing_idx = ev.get("broke_swing_idx", ev.get("swing_idx"))
        rows.append({"type": ev["type"], "name": name, "direction": "up" if d > 0 else "down" if d < 0 else "flat",
                     "price": _num(ev["price"]), "price_text": price_text(ev["price"]), "time": _epoch(ts[ev["bar_idx"]]),
                     "confirmed_time": _epoch(ev["time"]), "state": ev.get("to_state", ev.get("state")),
                     "swing_time": _epoch(ts[swing_idx]) if isinstance(swing_idx, (int, np.integer)) and 0 <= swing_idx < len(ts) else None,
                     "text": f"{name} {_arrow(d) if d else ''} {price_text(ev['price'])}".replace("  ", " ")})
    if not debug:
        rows = rows[-MAX_STRUCTURE_EVENTS:]
    snap = tr.snapshot()
    prot = snap["protected_level"]
    state = {"tf": tf, "state": snap["trend_state"], "direction": STATE_DIRECTION.get(snap["trend_state"], "none"),
             "protected": None if prot is None else _num(prot), "protected_text": None if prot is None else price_text(prot),
             "protected_label": None if prot is None else f"protected {'low' if tr.s > 0 else 'high'} {price_text(prot)}",
             "range_high": None if snap["range_high"] is None else _num(snap["range_high"]),
             "range_low": None if snap["range_low"] is None else _num(snap["range_low"]),
             "ref_range": None if snap["ref_range"] is None else _num(snap["ref_range"]), "bars": int(snap["bars"])}
    last = next((r for r in reversed(rows) if r["type"] in ("BOS", "CHOCH", "REVERSAL_CONFIRMED", "RECOVERY")), None)
    state["last_event"] = None if last is None else {k: last[k] for k in ("type", "name", "direction", "price", "time", "text")}
    state["text"] = f"{tf} {state['direction']}" + (f" · {last['name']} {price_text(last['price'])}" if last else "")
    return rows, state


# ---------------------------------------------------------------------------------------------------------------------
# Liquidity
# ---------------------------------------------------------------------------------------------------------------------
def liquidity(frame: pd.DataFrame, pivots: Sequence[cs.Pivot], debug: bool = False, tz: str = "Asia/Kolkata") -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """(pools, sweeps) at the last bar: the pools near the price (debug: every pool known) and the sweeps in the last
    SWEEP_LOOKBACK bars. Pivots: the chart degree's confirmed swings; noise scale: the chart's median range."""
    df = frame.reset_index(drop=True)
    n = len(df)
    if n < 2:
        return [], []
    ts = pd.to_datetime(df["timestamp"], utc=True).dt.tz_convert(tz).dt.tz_localize(None).reset_index(drop=True)
    sigma = LG.median_range(df, LG.LegConfig().n_median)
    V = LQ.View(ts=ts, high=df["high"].to_numpy(float), low=df["low"].to_numpy(float), close=df["close"].to_numpy(float), sigma=sigma,
                pivots=[LQ.KnownPivot(p.kind, p.price, int(p.confirmed_idx)) for p in pivots if p.confirmed_idx is not None])
    t = n - 1
    raw = LQ.pools(V, t) if debug else LQ.pool_marks(V, t)
    close = float(df["close"].iloc[-1])
    pools: List[Dict[str, Any]] = []
    for p in raw:
        side = p["side"]
        label = p.get("label") or f"{LQ.SRC_EN.get(p['src'], p['src'])} {'BSL' if side == 'buy' else 'SSL'}"
        pools.append({"side": side, "src": p["src"], "price": _num(p["price"]), "price_text": price_text(p["price"]), "label": label,
                      "beyond": bool((p["price"] < close) if side == "buy" else (p["price"] > close)),
                      "text": f"{'buy-side' if side == 'buy' else 'sell-side'} liquidity · {SRC_TEXT.get(p['src'], p['src'])} {price_text(p['price'])}"})
    pools.sort(key=lambda r: (-r["price"], r["src"]))
    tsu = df["timestamp"].to_numpy()
    sweeps = [{"side": s["side"], "src": s["src"], "price": _num(s["price"]), "price_text": price_text(s["price"]), "time": _epoch(tsu[s["bar"]]),
               "extreme": _num(s["extreme"]),
               "text": f"sweep of {SRC_TEXT.get(s['src'], s['src'])} {price_text(s['price'])}"} for s in LQ.sweeps(V, t, SWEEP_LOOKBACK)]
    sweeps.sort(key=lambda r: (r["time"], -r["price"]))
    return pools, sweeps


# ---------------------------------------------------------------------------------------------------------------------
# Trendlines and channels
# ---------------------------------------------------------------------------------------------------------------------
def trendlines(frame: pd.DataFrame) -> List[Dict[str, Any]]:
    """The newest falling resistance and rising support line over the bars (with its channel): every point in time and
    price, drawn from the first anchor to the last closed bar."""
    df = frame.reset_index(drop=True)
    out: List[Dict[str, Any]] = []
    ts = df["timestamp"].to_numpy()
    n = len(df)
    for ln in TL.detect_trendlines(df):
        tl = ln["line"]
        ch = TL.channel_of(tl, df)
        word = "falling resistance" if ln["side"] == "resistance" else "rising support"
        out.append({"kind": ln["kind"], "side": ln["side"], "slope": _num(tl.m), "touches": int(ln["touches"]),
                    "a": {"time": _epoch(ts[tl.a]), "price": _num(tl.at(tl.a))}, "b": {"time": _epoch(ts[tl.b_idx]), "price": _num(tl.at(tl.b_idx))},
                    "end": {"time": _epoch(ts[n - 1]), "price": _num(tl.at(n - 1))}, "next_price": _num(tl.at(n)),
                    "next_price_text": price_text(tl.at(n)),
                    "touch_times": [_epoch(t) for t, _p in ln["touch_points"]],
                    "channel": {"offset": _num(ch["offset"]), "from": {"time": _epoch(ts[tl.a]), "price": _num(tl.at(tl.a) + ch["offset"])},
                                "to": {"time": _epoch(ts[n - 1]), "price": _num(tl.at(n - 1) + ch["offset"])}, "anchor_time": _epoch(ts[ch["at_bar"]])},
                    "text": f"{word} · {ln['touches']} touches · next {price_text(tl.at(n))}"})
    return out


# ---------------------------------------------------------------------------------------------------------------------
# Top-down panel
# ---------------------------------------------------------------------------------------------------------------------
def weekly_from_daily(daily: pd.DataFrame, tz: str = "Asia/Kolkata") -> pd.DataFrame:
    """Daily closed bars -> weekly bars (weeks end Friday, session-local). A week is closed once its last daily bar's
    end is its Friday's - or once a later week has started; the forming week is left out. bar_end = the last daily bar's."""
    if daily is None or not len(daily):
        return pd.DataFrame(columns=["timestamp", "bar_end", "open", "high", "low", "close"])
    d = daily.reset_index(drop=True)
    local = pd.to_datetime(d["timestamp"], utc=True).dt.tz_convert(tz)
    wk = local.dt.tz_localize(None).dt.to_period("W-FRI")
    g = d.groupby(wk.to_numpy(), sort=True)
    out = pd.DataFrame({"timestamp": g["timestamp"].first(), "bar_end": g["bar_end"].last(), "open": g["open"].first(), "high": g["high"].max(),
                        "low": g["low"].min(), "close": g["close"].last()}).reset_index(drop=True)
    last_local = pd.to_datetime(out["bar_end"], utc=True).dt.tz_convert(tz)
    friday = last_local.dt.weekday == 4
    closed = friday | (out.index < len(out) - 1)
    return out[closed].reset_index(drop=True)


def mtf_row(frame: Optional[pd.DataFrame], tf: str, source: Optional[str]) -> Dict[str, Any]:
    """One row of the top-down panel: the structure state on that timeframe's own bars as of the same time."""
    if frame is None or not len(frame):
        return {"tf": tf, "state": None, "direction": "none", "protected": None, "protected_text": None, "last_event": None, "bars": 0,
                "as_of": None, "source": source, "text": f"{tf} no bars"}
    _rows, state = structure(frame, tf)
    be = frame["bar_end"].iloc[-1]
    return {"tf": tf, "state": state["state"], "direction": state["direction"], "protected": state["protected"],
            "protected_text": state["protected_text"], "last_event": state["last_event"], "bars": state["bars"],
            "as_of": _utc(be).to_pydatetime().isoformat(), "source": source, "text": state["text"]}
