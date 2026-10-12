"""N1 L1: the decision3 functions the level gate calls, ported from Trade `decision3/method.py` (pinned revision in
`settings.TRADE_REVISION`). Logic unchanged; messages in English. `A` is a dict of numpy arrays (open, high, low,
close, and volume or None); `d` is the trade direction (UP = +1, DOWN = -1); `t` a bar index.

* `candle_read`    - who won the candle and how strongly (big = against the previous `candle_avg_n` candles).
* `contraction`    - (4a) the counter side shrinks inside the area: the average range of the pullback candles inside is
                     below both the pullback's earlier candles and the impulse candles (relative, no "exactly 3").
* `power_shift`    - (4) evidence count: contraction, overlapping bodies, no new extreme or a sweep, a rejection wick.
* `zone_study`     - (a2) the handover study from arming to t: trend-side candles growing and counter-side weakening
                     (first half vs second half; body required, then range / close location / volume by majority) =
                     handover; counter side strengthening = absorption. Both directions in one function.
* `zone_pattern`   - (a2) evidence only: falling / rising wedge, channel / flag, double / triple bottom / top.
* `reversal_form`  - (Q16) the reversal candle (engulf, star, inside break, doji confirm, tweezer, pin, strong close,
                     strong close over two candles).
* `commitment`     - (6) the commitment candle: a reversal form, a close beyond the previous close (or extreme) on the
                     trade side of the level; with a study window (c0): body >= `commit_dom_mult` x the window's average
                     body and volume above the window's average (volume NA: allowed, grade B at most).
"""
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from app.price_action.level_gate.settings import METHOD_DEFAULTS

UP, DOWN = 1, -1

Bars = Mapping[str, Any]


def _ohlc(A: Bars, i: int) -> Tuple[float, float, float, float]:
    return float(A["open"][i]), float(A["high"][i]), float(A["low"][i]), float(A["close"][i])


def candle_read(A: Bars, t: int, s: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    s = s or METHOD_DEFAULTS
    n_avg = int(s["candle_avg_n"])
    o, h, lo, c = _ohlc(A, t)
    rng = max(h - lo, 1e-9)
    body = abs(c - o)
    lo_i = max(0, t - n_avg)
    bodies = np.abs(A["close"][lo_i:t] - A["open"][lo_i:t])
    ranges = A["high"][lo_i:t] - A["low"][lo_i:t]
    pos = (c - lo) / rng
    return {"winner": "buyers" if c > o else ("sellers" if c < o else "none"), "body_pct": body / rng, "close_pos": pos,
            "upper_wick": (h - max(o, c)) / rng, "lower_wick": (min(o, c) - lo) / rng,
            "big_body": bool(len(bodies)) and body > float(np.mean(bodies)), "big_range": bool(len(ranges)) and rng > float(np.mean(ranges)),
            "doji": body / rng < float(s["doji_body_max"])}


def _mean(x: Sequence[float]) -> Optional[float]:
    return float(np.mean(x)) if len(x) else None


def _vol(A: Bars) -> Optional[np.ndarray]:
    v = A.get("volume") if hasattr(A, "get") else None
    if v is None:
        return None
    v = np.asarray(v, dtype=float)
    return v if np.isfinite(v).any() and np.nanmax(v) > 0 else None


def contraction(A: Bars, t: int, d: int, k: Optional[Mapping[str, Any]], s: Mapping[str, Any],
                lvl: Optional[Mapping[str, float]] = None) -> bool:
    k0 = k["k_open_bar"] if k and k.get("k_open_bar") is not None else max(0, t - int(s["power_fallback_bars"]))
    idx = [i for i in range(k0, t + 1) if (A["close"][i] - A["open"][i]) * d < 0]
    if lvl is not None:
        inside = [i for i in idx if A["low"][i] <= lvl["hi"] and A["high"][i] >= lvl["lo"]]
    else:
        inside = idx[-max(1, len(idx) // 3):] if idx else []
    earlier = [i for i in idx if inside and i < inside[0]]
    if k and k.get("origin_bar") is not None and k.get("i_end_bar") is not None:
        imp = [i for i in range(int(k["origin_bar"]), int(k["i_end_bar"]) + 1) if (A["close"][i] - A["open"][i]) * d > 0]
    else:
        imp = [i for i in range(max(0, k0 - int(s["power_fallback_bars"])), k0) if (A["close"][i] - A["open"][i]) * d > 0]

    def avg_range(ii: List[int]) -> Optional[float]:
        return _mean([A["high"][i] - A["low"][i] for i in ii])
    a_in, a_er, a_im = avg_range(inside), avg_range(earlier), avg_range(imp)
    return bool(a_in is not None and a_er is not None and a_im is not None and a_in < a_er and a_in < a_im)


def power_shift(A: Bars, t: int, d: int, k: Optional[Mapping[str, Any]], s: Mapping[str, Any],
                lvl: Optional[Mapping[str, float]] = None) -> Tuple[bool, int, Dict[str, Any]]:
    """(a) counter candles shrinking (`power_a_rule`: relative = `contraction`; strict3 = the old last-3 rule); (b) the last
    3 bodies overlap >= `power_overlap`; (c) the last counter push makes no new extreme, or sweeps and closes back;
    (d) trend-side rejection wicks. At least `power_shift_min` of (a)-(d). (e) handover volume: evidence only."""
    items: Dict[str, Any] = {}
    k0 = k["k_open_bar"] if k and k.get("k_open_bar") is not None else max(0, t - int(s["power_fallback_bars"]))
    if s.get("power_a_rule", "relative") == "strict3":
        idx = [i for i in range(k0, t + 1) if (A["close"][i] - A["open"][i]) * d < 0]
        r = [A["high"][i] - A["low"][i] for i in idx[-3:]]
        items["a_shrinking"] = len(r) == 3 and r[0] > r[1] > r[2]
    else:
        items["a_shrinking"] = contraction(A, t, d, k, s, lvl)
    bl = [(min(A["open"][i], A["close"][i]), max(A["open"][i], A["close"][i])) for i in range(max(0, t - 2), t + 1)]
    ov = []
    for (a0, a1), (b0, b1) in zip(bl[:-1], bl[1:]):
        m = min(a1 - a0, b1 - b0)
        ov.append((min(a1, b1) - max(a0, b0)) / m if m > 0 else 0.0)
    items["b_overlap"] = len(ov) == 2 and all(x >= float(s["power_overlap"]) for x in ov)
    ext = k.get("k_ext") if k else None
    eb = k.get("k_ext_bar") if k else None
    if ext is not None and eb is not None:
        no_new = eb < t
        cur = A["low"][t] if d == UP else A["high"][t]
        prev_ext = min(A["low"][k0:t]) if d == UP and t > k0 else (max(A["high"][k0:t]) if t > k0 else cur)
        swept = (cur - prev_ext) * d < 0 and (A["close"][t] - prev_ext) * d > 0
        items["c_no_new_extreme_or_sweep"] = bool(no_new or swept)
        items["sweep"] = bool(swept)
    else:
        items["c_no_new_extreme_or_sweep"] = False
        items["sweep"] = False
    rej = False
    for i in range(max(0, t - 1), t + 1):
        cr = candle_read(A, i, s)
        w = cr["lower_wick"] if d == UP else cr["upper_wick"]
        ow = cr["upper_wick"] if d == UP else cr["lower_wick"]
        rej = rej or (w >= cr["body_pct"] and w > ow)
    items["d_rejection_wick"] = rej
    zs = zone_study(A, k0, t, d, s) if _vol(A) is not None else None
    items["e_handover_volume"] = None if zs is None or zs.get("handover") is None else bool(zs["handover"] and zs.get("vol_ok"))
    items["e_rsi_div"] = None                                            # evidence only; not available
    n = sum(1 for key in ("a_shrinking", "b_overlap", "c_no_new_extreme_or_sweep", "d_rejection_wick") if items[key])
    return n >= int(s["power_shift_min"]), n, items


def _cmp(first: Sequence[Any], second: Sequence[Any]) -> Optional[int]:
    """+1 the second half is larger, -1 smaller, 0 equal, None when a half is empty / NA."""
    a = _mean([x for x in first if x is not None and np.isfinite(x)])
    b = _mean([x for x in second if x is not None and np.isfinite(x)])
    if a is None or b is None:
        return None
    return 1 if b > a else (-1 if b < a else 0)


def _majority(req: Optional[bool], others: Sequence[Optional[bool]]) -> Optional[bool]:
    """`req` (required) true and at least half of the available other checks true."""
    if req is None:
        return None
    av = [x for x in others if x is not None]
    return bool(req and (not av or sum(1 for x in av if x) * 2 >= len(av)))


def zone_study(A: Bars, a: int, t: int, d: int, s: Optional[Mapping[str, Any]], mr: Optional[float] = None) -> Dict[str, Any]:
    s = s or METHOD_DEFAULTS
    w = list(range(max(0, int(a)), int(t) + 1))
    vol = _vol(A)
    out: Dict[str, Any] = {"handover": None, "absorption": None, "trend_growing": None, "counter_weakening": None,
                           "vol_ok": vol is not None, "pattern": None, "line": ""}
    if len(w) < int(s.get("study_min_bars", 4)):
        out["pattern"] = zone_pattern(A, a, t, d, s, mr)
        return out
    m = len(w) // 2
    halves = (w[:m], w[m:])

    def feats(ii: List[int], side: int) -> Dict[str, List[float]]:
        o_, h_, l_, c_ = A["open"], A["high"], A["low"], A["close"]
        sel = [i for i in ii if (c_[i] - o_[i]) * d * side > 0]
        rng = [max(h_[i] - l_[i], 1e-9) for i in sel]
        body = [abs(c_[i] - o_[i]) for i in sel]
        loc = [((c_[i] - l_[i]) if d > 0 else (h_[i] - c_[i])) / r for i, r in zip(sel, rng)]   # close toward the trade end
        opp = [((min(o_[i], c_[i]) - l_[i]) if d > 0 else (h_[i] - max(o_[i], c_[i]))) / r for i, r in zip(sel, rng)]
        v = [float(vol[i]) for i in sel] if vol is not None else []
        return {"body": body, "range": rng, "loc": loc, "opp": opp, "vol": v}
    T = [feats(x, 1) for x in halves]
    C = [feats(x, -1) for x in halves]
    tb, tr, tl = _cmp(T[0]["body"], T[1]["body"]), _cmp(T[0]["range"], T[1]["range"]), _cmp(T[0]["loc"], T[1]["loc"])
    tv = _cmp(T[0]["vol"], T[1]["vol"]) if vol is not None else None
    cb, cr, co = _cmp(C[0]["body"], C[1]["body"]), _cmp(C[0]["range"], C[1]["range"]), _cmp(C[0]["opp"], C[1]["opp"])
    cl = _cmp(C[0]["loc"], C[1]["loc"])
    cv = _cmp(C[0]["vol"], C[1]["vol"]) if vol is not None else None
    out["trend_growing"] = _majority(None if tb is None else tb > 0, [None if x is None else x > 0 for x in (tr, tl, tv)])
    out["counter_weakening"] = _majority(None if cb is None else cb < 0, [None if co is None else co > 0, None if cl is None else cl > 0,
                                                                          None if cv is None else cv < 0])
    out["handover"] = bool(out["trend_growing"] and out["counter_weakening"])
    out["absorption"] = bool(cb is not None and cb > 0 and cr is not None and cr > 0 and (cv is None or cv > 0))
    out["vol_trend_rising"] = None if tv is None else tv > 0
    out["pattern"] = zone_pattern(A, a, t, d, s, mr)
    tw, cw = ("buying", "selling") if d > 0 else ("selling", "buying")
    parts = []
    if out["trend_growing"]:
        parts.append(f"{tw} candles growing")
    if out["counter_weakening"]:
        parts.append(f"{cw} candles weakening")
    if out["absorption"]:
        parts.append(f"{cw} candles strengthening (absorption)")
    if out["vol_trend_rising"]:
        parts.append(f"volume rising on {tw} candles")
    out["line"] = " · ".join(([out["pattern"]] if out["pattern"] else []) + parts)
    return out


def zone_pattern(A: Bars, a: int, t: int, d: int, s: Optional[Mapping[str, Any]], mr: Optional[float] = None) -> Optional[str]:
    """Swings inside the area (a ... t - 1, simple 1-bar pivots). UP (demand): falling wedge / falling channel - bull
    flag / double - triple bottom; DOWN (supply): the mirror. None when there is none."""
    s = s or METHOD_DEFAULTS
    H, L = A["high"], A["low"]
    ii = list(range(max(1, int(a)), int(t) - 1))                           # a pivot needs both neighbours (commitment excluded)
    hs = [(i, float(H[i])) for i in ii if H[i] > H[i - 1] and H[i] >= H[i + 1]]
    ls = [(i, float(L[i])) for i in ii if L[i] < L[i - 1] and L[i] <= L[i + 1]]
    if mr is None or not np.isfinite(mr):
        rr = [H[i] - L[i] for i in range(max(0, int(a)), int(t) + 1)]
        mr = float(np.median(rr)) if rr else 0.0
    tol = float(s.get("pattern_eq_mr", 0.5)) * mr
    if len(hs) >= 2 and len(ls) >= 2:
        sh = (hs[-1][1] - hs[0][1]) / max(hs[-1][0] - hs[0][0], 1)
        sl = (ls[-1][1] - ls[0][1]) / max(ls[-1][0] - ls[0][0], 1)
        if d > 0 and sh < 0 and sl < 0:
            return "falling wedge" if sh < sl else "falling channel / bull flag"
        if d < 0 and sh > 0 and sl > 0:
            return "rising wedge" if sl > sh else "rising channel / bear flag"
    ext = ls if d > 0 else hs
    if len(ext) >= 2 and tol > 0:
        best = min(p for _, p in ext) if d > 0 else max(p for _, p in ext)
        n = sum(1 for _, p in ext if abs(p - best) <= tol)
        if n >= 2:
            return f"{'triple' if n >= 3 else 'double'} {'bottom' if d > 0 else 'top'}"
    return None


def _same_window(tss: Optional[Sequence[Any]], t: int, i: int, s: Mapping[str, Any]) -> bool:
    """Is candle i on t's day and at or after `entry_start`? (No timestamps: yes.)"""
    if tss is None:
        return True
    ti, tt = pd.Timestamp(tss[i]), pd.Timestamp(tss[t])
    return bool(ti.normalize() == tt.normalize() and ti.strftime("%H:%M") >= s["entry_start"])


def reversal_form(A: Bars, t: int, d: int, s: Mapping[str, Any], tss: Optional[Sequence[Any]] = None) -> Optional[Tuple[str, int]]:
    """(form, candles merged) or None. Order: engulf, star, inside_break, doji_confirm, tweezer, pin, strong_close,
    strong_close_2. d = trade direction (UP: rejection below, close up)."""
    if t < 1:
        return None
    o, h, lo, c = _ohlc(A, t)
    po, ph, pl, pc = _ohlc(A, t - 1)
    rng, prng = max(h - lo, 1e-9), max(ph - pl, 1e-9)
    body = (c - o) * d
    pbody = (pc - po) * d
    two = _same_window(tss, t, t - 1, s)                                  # two-candle forms: t - 1 in today's window
    doji_max, cbody, cfrac = float(s["doji_body_max"]), float(s["commit_body"]), float(s["commit_close_frac"])
    pos = (c - lo) / rng if d == UP else (h - c) / rng                     # close near the trade-side end (1 = at it)
    rej = (min(o, c) - lo) / rng if d == UP else (h - max(o, c)) / rng     # the rejection wick at the level
    opp = (h - max(o, c)) / rng if d == UP else (min(o, c) - lo) / rng
    if two and body > 0 and pbody < 0 and max(o, c) >= max(po, pc) and min(o, c) <= min(po, pc):
        return "engulf", 1
    if two and t >= 2 and body > 0 and _same_window(tss, t, t - 2, s):
        qo, qh, ql, qc = _ohlc(A, t - 2)
        q_rng, qbody = max(qh - ql, 1e-9), (qc - qo) * d
        if qbody < 0 and -qbody / q_rng >= cbody and abs(pc - po) / prng <= float(s["star_body_max"]) and \
                (c - (qo + qc) / 2) * d > 0:
            return "star", 2
        inside = ph <= qh and pl >= ql
        harami = max(po, pc) <= max(qo, qc) and min(po, pc) >= min(qo, qc)
        if (inside or harami) and (c - (ph if d == UP else pl)) * d > 0:
            return "inside_break", 2
    if two and body > 0 and abs(pc - po) / prng < doji_max and (c - (ph if d == UP else pl)) * d > 0:
        return "doji_confirm", 2
    if two and body > 0 and pbody <= 0 and \
            abs((lo - pl) if d == UP else (h - ph)) <= float(s["tweezer_tol"]) * (rng + prng) / 2:
        return "tweezer", 2
    if rej >= float(s["pin_wick_min"]) and rej > opp and pos >= cfrac:
        return "pin", 1
    if body > 0 and body / rng >= cbody and pos >= cfrac and opp < body / rng:
        return "strong_close", 1
    if two:                                                                # a strong close over <= 2 merged candles
        mo, mh, ml = po, max(h, ph), min(lo, pl)
        mrng = max(mh - ml, 1e-9)
        mb = (c - mo) * d
        mpos = (c - ml) / mrng if d == UP else (mh - c) / mrng
        mopp = (mh - max(mo, c)) / mrng if d == UP else (min(mo, c) - ml) / mrng
        if mb > 0 and mb / mrng >= cbody and mpos >= cfrac and mopp < mb / mrng:
            return "strong_close_2", 2
    return None


def commitment(A: Bars, t: int, d: int, lvl: Mapping[str, float], s: Mapping[str, Any], tss: Optional[Sequence[Any]] = None,
               win: Optional[int] = None) -> Tuple[bool, str, Dict[str, Any]]:
    """(ok, why, details). Minimum: a close beyond the previous candle's close (`commit_beyond` = close; "extreme" is a
    what-if) - for a merged two-candle form the candle before the pattern - and a close on the trade side of / inside
    the level. Grade A: beyond the previous extreme or an engulf; else B. Wait (no entry on this candle): doji, inside
    bar, a candle still in the pullback direction; with `win`: not dominant, or volume not above the window's."""
    if s["commit_beyond"] not in ("extreme", "close"):
        raise ValueError(f"commit_beyond {s['commit_beyond']!r} - one of extreme / close")
    if tss is not None:
        hm = pd.Timestamp(tss[t]).strftime("%H:%M")
        if not (s["entry_start"] <= hm < s["entry_end"]):
            return False, "outside the entry time window", {}
    if t < 1:
        return False, "no commitment candle", {}
    o, h, lo, c = _ohlc(A, t)
    po, ph, pl, pc = _ohlc(A, t - 1)
    rng = max(h - lo, 1e-9)
    beyond_close = (c - pc) * d > 0
    beyond_ext = (c - (ph if d == UP else pl)) * d > 0
    side = (c >= lvl["lo"]) if d == UP else (c <= lvl["hi"])
    if abs(c - o) / rng < float(s["doji_body_max"]):
        return False, "doji at the level - waiting for the next candle", {"wait": "doji"}
    if h <= ph and lo >= pl:
        return False, "inside bar - waiting for the next candle", {"wait": "inside"}
    if (c - o) * d < 0 and not beyond_close:
        return False, "candle still in the pullback direction - waiting", {"wait": "pullback"}
    fm = reversal_form(A, t, d, s, tss)
    if fm is not None and fm[1] == 2 and t >= 2 and _same_window(tss, t, t - 2, s):
        r = t - 2                                                          # merged form: "previous" = the candle before it
        qo, qh, ql, qc = _ohlc(A, r)
        beyond_close = (c - qc) * d > 0
        beyond_ext = (c - (qh if d == UP else ql)) * d > 0
    need = beyond_close if s["commit_beyond"] == "close" else beyond_ext
    if not (need and side):
        return False, "no commitment candle" + ("" if side else " (close on the wrong side of the level)"), {}
    if fm is None:
        return False, "no commitment candle (no reversal form)", {}
    form, merged = fm
    grade = "A" if (beyond_ext or form == "engulf") else "B"
    cm: Dict[str, Any] = {"form": form, "merged": merged, "grade": grade, "beyond_extreme": bool(beyond_ext), "engulf": form == "engulf",
                          "body_pct": round(abs(c - o) / rng, 2), "big_body": candle_read(A, t, s)["big_body"],
                          "big_range": candle_read(A, t, s)["big_range"]}
    if win is not None:                                                    # c0: dominant candle of the study window + volume
        ww = list(range(max(0, int(win)), t)) or [t]
        mb = _mean([abs(A["close"][i] - A["open"][i]) for i in ww]) or 0.0
        body = max(abs(c - o), abs(c - po) if merged == 2 else 0.0)
        bm = body / mb if mb > 0 else float("inf")
        cm["body_mult"] = round(bm, 2) if np.isfinite(bm) else None
        if bm < float(s.get("commit_dom_mult", 1.5)):
            return False, f"commitment candle not dominant (body {bm:.1f}x the window average)", {"wait": "not_dominant", **cm}
        V = _vol(A)
        wv = [float(V[i]) for i in ww] if V is not None else []
        mv = _mean([x for x in wv if np.isfinite(x) and x > 0]) if wv else None
        if V is not None and np.isfinite(V[t]) and V[t] > 0 and mv:
            vm = float(V[t]) / mv
            cm.update(vol_mult=round(vm, 2), vol_na=False)
            if vm <= 1.0:
                return False, f"commitment on low volume ({vm:.1f}x the window average)", {"wait": "low_volume", **cm}
        else:
            cm.update(vol_mult=None, vol_na=True)                          # volume NA: allowed, grade B at most
            grade = cm["grade"] = "B"
    return True, f"commitment {form} ({merged} candle) · grade {grade}", cm
