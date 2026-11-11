"""M99 B1-c: per-bar causal structure series - the one facade the M99 packs read swings, BOS / CHoCH and the
trend-or-range regime from.

Built on the existing causal code, which it never changes:
  * `structure_tracker.StructureTracker` (the Trade port: swings with HH / LH / EQH / HL / LL / EQL labels, BOS, CHoCH,
    reversal, recovery, range) - fed one closed bar at a time; after each bar the facade records what the tracker knows
    at that bar's close. A swing therefore appears from its confirm bar (pivot bar + pivot_n), never from its own bar.
  * `causal_swings.degree_pivots` (the multi-degree engine the screener's SwingHigh / SwingLabel read) - the same per-bar
    view of its confirmed pivots (`causal_pivot_series`).

Structure is read as one continuous unit: there is no day, session or clock in anything here, and one call reads one
frame of one timeframe (timeframes are never merged).

Per bar t (index of the frame, oldest first):
  swing_high / swing_low          the last CONFIRMED swing price known at t's close (NaN before the first)
  swing_high_bar / _low_bar       its pivot bar; swing_*_confirm its confirm bar (<= t); -1 before the first
  swing_high_label / _low_label   the tracker's label against the swing of its kind before it (HH / LH / EQH, HL / LL /
                                  EQL; "" for the first one)
  event                           BOS_BULLISH / BOS_BEARISH / CHOCH_BULLISH / CHOCH_BEARISH on the bar where the tracker
                                  emitted it (a close beyond the level), "" elsewhere
  state                           the tracker state at t's close (INIT, RANGE, UPTREND, UPTREND_PULLBACK, UPTREND_WEAK and
                                  the DOWN mirror)
  regime                          UP / DOWN / UP_WEAK / DOWN_WEAK / RANGE ("" in INIT and warm-up); with `range_width`
                                  set, a state whose last `range_swings` confirmed swings span at most range_width x the
                                  reference range is tagged RANGE (off by default: no rule nobody set)

Every number is a parameter (PARAMS), labelled "reference - edit before use"; the defaults are the tracker's own
(`StructureConfig`), read from it so they never drift. Reference only - nothing here places an order.
"""
from __future__ import annotations

from dataclasses import dataclass, field, fields, replace
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from app.price_action import structure_tracker as st

REFERENCE = "reference - edit before use"

UP, DOWN, UP_WEAK, DOWN_WEAK, RANGE = "UP", "DOWN", "UP_WEAK", "DOWN_WEAK", "RANGE"
REGIMES = (UP, DOWN, UP_WEAK, DOWN_WEAK, RANGE)
EVENTS = ("BOS_BULLISH", "BOS_BEARISH", "CHOCH_BULLISH", "CHOCH_BEARISH")
# the tracker's states -> the regime tag (a pullback is still the trend; a broken protected level is WEAK, not merged
# into RANGE or the opposite trend)
REGIME_OF_STATE: Dict[str, str] = {
    st.UP: UP, st.UP_PB: UP, st.UP_WEAK: UP_WEAK,
    st.DN: DOWN, st.DN_PB: DOWN, st.DN_WEAK: DOWN_WEAK,
    st.RANGE: RANGE, st.INIT: "",
}
# the tracker's event + the state it moved to -> the direction of the close that made it
_EVENT_OF: Dict[tuple, str] = {
    ("BOS", st.UP): "BOS_BULLISH", ("BOS", st.DN): "BOS_BEARISH",
    ("CHOCH", st.UP_WEAK): "CHOCH_BEARISH", ("CHOCH", st.DN_WEAK): "CHOCH_BULLISH",
}

_CFG = st.StructureConfig()


@dataclass(frozen=True)
class Param:
    default: Any
    doc: str
    label: str = REFERENCE


# Every number the facade reads. pivot_n / swing_k: None = the tracker's value for the frame's timeframe.
PARAMS: Dict[str, Param] = {
    "pivot_n": Param(None, "a pivot needs this many bars on each side; it is confirmed this many bars after its own bar"
                           f" (None: the tracker's value by timeframe, {dict(_CFG.pivot_n)})"),
    "swing_k": Param(None, "a new swing must be at least this many reference ranges from the last swing"
                           f" (None: the tracker's value by timeframe, {dict(_CFG.swing_k)})"),
    "ref_range_bars": Param(_CFG.ref_range_bars, "reference range: the median high - low of the last this many full bars"),
    "ref_range_min_bars": Param(_CFG.ref_range_min_bars, "fewer full bars than this: no reference range yet (warm-up, nothing read)"),
    "eq_tol": Param(_CFG.eq_tol, "two swings within this many reference ranges are equal (EQH / EQL)"),
    "pullback_k": Param(_CFG.pullback_k, "a close this many reference ranges back from the trend extreme starts a pullback"),
    "lh_tol": Param(_CFG.lh_tol, "after a CHoCH, a lower high must be this many reference ranges under the last high"),
    "weak_to_range_swings": Param(_CFG.weak_to_range_swings, "this many new swings after a CHoCH with nothing decided -> RANGE"),
    "range_exit_k": Param(_CFG.range_exit_k, "a close this many reference ranges beyond a range edge (with a confirmed swing) leaves it"),
    "init_swings": Param(_CFG.init_swings, "the first trend / range is read from this many confirmed swings"),
    "spike_wick_k": Param(_CFG.spike_wick_k, "a spike: a wick of at least this many reference ranges"),
    "spike_body_max": Param(_CFG.spike_body_max, "a spike: a body of at most this share of the bar's range"),
    "spike_return_bars": Param(_CFG.spike_return_bars, "a spike: price back at the wick's root within this many bars"),
    "spike_body_edge": Param(_CFG.spike_body_edge, "a spike's swing price is its body edge, not its wick"),
    "range_width": Param(None, "optional: a state whose last range_swings swings span at most this many reference ranges"
                               " is tagged RANGE (None: off, the tracker's own states only)"),
    "range_swings": Param(_CFG.init_swings, "the swings range_width measures"),
}


def settings(**overrides: Any) -> Dict[str, Any]:
    """The facade's parameters with `overrides` applied (an unknown name is refused)."""
    bad = sorted(set(overrides) - set(PARAMS))
    if bad:
        raise ValueError(f"unknown structure setting(s): {', '.join(bad)}")
    out = {k: p.default for k, p in PARAMS.items()}
    out.update({k: v for k, v in overrides.items() if v is not None})          # None keeps the default
    return out


def tracker_config(tf: str, s: Optional[Dict[str, Any]] = None) -> st.StructureConfig:
    """The tracker's config for one timeframe from the facade's parameters (pivot_n / swing_k set for `tf` only)."""
    s = settings() if s is None else s
    names = {f.name for f in fields(st.StructureConfig)} - {"pivot_n", "swing_k"}
    cfg = replace(_CFG, **{k: s[k] for k in names})
    cfg = replace(cfg, pivot_n=dict(cfg.pivot_n), swing_k=dict(cfg.swing_k))
    if s.get("pivot_n") is not None:
        cfg.pivot_n[tf] = int(s["pivot_n"])
    if s.get("swing_k") is not None:
        cfg.swing_k[tf] = float(s["swing_k"])
    return cfg


@dataclass
class StructureSeries:
    """The per-bar arrays (one value per bar, as known at that bar's close) plus the tracker the frame went through."""
    tf: str
    index: Any
    swing_high: np.ndarray
    swing_high_bar: np.ndarray
    swing_high_confirm: np.ndarray
    swing_high_label: np.ndarray
    swing_low: np.ndarray
    swing_low_bar: np.ndarray
    swing_low_confirm: np.ndarray
    swing_low_label: np.ndarray
    event: np.ndarray
    state: np.ndarray
    regime: np.ndarray
    protected: np.ndarray
    ref_range: np.ndarray
    tracker: st.StructureTracker = field(repr=False)

    def __len__(self) -> int:
        return len(self.event)

    def frame(self) -> pd.DataFrame:
        """The arrays as one frame on the bars' index."""
        cols = ("swing_high", "swing_high_bar", "swing_high_confirm", "swing_high_label", "swing_low", "swing_low_bar",
                "swing_low_confirm", "swing_low_label", "event", "state", "regime", "protected", "ref_range")
        return pd.DataFrame({c: getattr(self, c) for c in cols}, index=self.index)

    def events_at(self, t: int) -> List[Dict[str, Any]]:
        """Every tracker event (SWING, BOS, CHOCH, REVERSAL_CONFIRMED, RECOVERY, RANGE_*, SWEEP ...) known on bar t."""
        return [e for e in self.tracker.events if e["bar_idx"] == t]


def _last_of(swings: List[st.Swing], kind: str) -> Optional[st.Swing]:
    return next((sw for sw in reversed(swings) if sw.kind == kind), None)


def structure_series(frame: pd.DataFrame, tf: str, s: Optional[Dict[str, Any]] = None, time_col: Optional[str] = None) -> StructureSeries:
    """Feed `frame` (closed bars, oldest first, open / high / low / close; optional `bar_is_full`) through a fresh
    StructureTracker, one bar at a time, and record what is known after each bar. `time_col`: the bar-end column (else
    the index). Only bars <= t decide the values at t (the tracker is incremental), so a frame cut at t gives the same
    values up to t (tested)."""
    s = settings() if s is None else s
    cfg = tracker_config(tf, s)
    tr = st.StructureTracker(tf, cfg)
    n = len(frame)
    o, h, lo, c = (frame[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    times = frame[time_col].to_numpy() if time_col else np.asarray(frame.index)
    full = frame["bar_is_full"].to_numpy(bool) if "bar_is_full" in frame.columns else np.ones(n, dtype=bool)
    out: Dict[str, np.ndarray] = {
        "swing_high": np.full(n, np.nan), "swing_low": np.full(n, np.nan), "protected": np.full(n, np.nan), "ref_range": np.full(n, np.nan),
        **{k: np.full(n, -1, dtype=int) for k in ("swing_high_bar", "swing_high_confirm", "swing_low_bar", "swing_low_confirm")},
        **{k: np.full(n, "", dtype=object) for k in ("swing_high_label", "swing_low_label", "event", "state", "regime")},
    }
    width, back = s.get("range_width"), int(s.get("range_swings") or cfg.init_swings)
    for t in range(n):
        evs = tr.on_bar(times[t], o[t], h[t], lo[t], c[t], bool(full[t]))
        for e in evs:
            tag = _EVENT_OF.get((e["type"], e.get("to_state")))
            if tag:
                out["event"][t] = tag
        for kind, key in (("H", "swing_high"), ("L", "swing_low")):
            sw = _last_of(tr.swings, kind)
            if sw is not None:
                out[key][t] = sw.price
                out[key + "_bar"][t] = sw.idx
                out[key + "_confirm"][t] = sw.confirmed_idx
                out[key + "_label"][t] = sw.label
        out["state"][t] = tr.state
        regime = REGIME_OF_STATE.get(tr.state, "")
        rr = tr.rr
        if width is not None and regime and np.isfinite(rr) and rr > 0 and len(tr.swings) >= back:
            recent = [sw.price for sw in tr.swings[-back:]]
            if max(recent) - min(recent) <= float(width) * rr:
                regime = RANGE
        out["regime"][t] = regime
        prot = tr.protected_level()
        out["protected"][t] = np.nan if prot is None else prot
        out["ref_range"][t] = rr
    return StructureSeries(tf=tf, index=frame.index, tracker=tr, **out)


# ------------------------------------------------------------------------------------------------ causal_swings view
@dataclass
class PivotSeries:
    """The causal swing engine's confirmed pivots of one degree as per-bar arrays (as known at each bar's close)."""
    swing_high: np.ndarray
    swing_high_bar: np.ndarray
    swing_high_confirm: np.ndarray
    swing_high_label: np.ndarray
    swing_low: np.ndarray
    swing_low_bar: np.ndarray
    swing_low_confirm: np.ndarray
    swing_low_label: np.ndarray


def causal_pivot_series(frame: pd.DataFrame, degree: int, s: Optional[Dict[str, Any]] = None) -> PivotSeries:
    """`causal_swings.degree_pivots` (degree's settings from `s`, else pa_settings) as per-bar arrays: from each pivot's
    confirm bar on, the last confirmed high / low, its pivot bar, its confirm bar and its label against the pivot of its
    kind before it (HH / LH, HL / LL; an equal high is not higher (LH), an equal low not lower (HL) - the screener's
    SwingLabel rule). `frame`: closed bars with open / high / low / close (timestamp / bar_end: else the index)."""
    from app.price_action import causal_swings as cs
    from app.price_action import pa_settings
    s = pa_settings.settings() if s is None else s
    fr = pd.DataFrame({k: frame[k].astype(float).to_numpy() for k in ("open", "high", "low", "close")})
    fr["timestamp"] = frame["timestamp"].to_numpy() if "timestamp" in frame.columns else np.asarray(frame.index)
    fr["bar_end"] = frame["bar_end"].to_numpy() if "bar_end" in frame.columns else fr["timestamp"].to_numpy()
    n = len(fr)
    arr: Dict[str, np.ndarray] = {
        "swing_high": np.full(n, np.nan), "swing_low": np.full(n, np.nan),
        **{k: np.full(n, -1, dtype=int) for k in ("swing_high_bar", "swing_high_confirm", "swing_low_bar", "swing_low_confirm")},
        **{k: np.full(n, "", dtype=object) for k in ("swing_high_label", "swing_low_label")},
    }
    prev: Dict[str, Optional[float]] = {"H": None, "L": None}
    for p in cs.degree_pivots(fr, degree, s):
        if p.confirmed_idx is None:
            continue
        key = "swing_high" if p.kind == "H" else "swing_low"
        before = prev[p.kind]
        label = "" if before is None else (("HH" if p.price > before else "LH") if p.kind == "H" else ("LL" if p.price < before else "HL"))
        k = int(p.confirmed_idx)
        arr[key][k:] = p.price
        arr[key + "_bar"][k:] = p.bar_idx
        arr[key + "_confirm"][k:] = k
        arr[key + "_label"][k:] = label
        prev[p.kind] = p.price
    return PivotSeries(**arr)


__all__ = ["REFERENCE", "PARAMS", "REGIMES", "EVENTS", "REGIME_OF_STATE", "Param", "StructureSeries", "PivotSeries", "settings",
           "tracker_config", "structure_series", "causal_pivot_series"]
