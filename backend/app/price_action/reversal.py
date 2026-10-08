"""Logical price reversal at a level - candlestick logic as ONE rule, not pattern names.

Ported from Trade@0df3e09d0aa5942e3352327d5b998c5f6939ac61, elliott/reversal.py (E2 + C1, the default "composite" mode)
and price_action/candles.py (#241, the "score100" mode), merged behind one API:

    evaluate_reversal(candles, level, direction, settings) -> {valid, score, score_pct, parts, reason, label, n, ...}

The composite rejection candle: the last N = 1..touch_reclaim_window CLOSED candles merged into one (first open, highest
high, lowest low, last close). Hammer (N=1), engulfing (N=2) and star (N=3) are the same idea: price was pushed into the
level and the other side took control. Bullish (support) rules; bearish is the mirror image (prices negated):

  touch      the composite low reaches the level (within touch_tol_mr x median range)
  inv        optional hard invalidation: the low stays above it and no close in the window is beyond it
  touched    the DEEPEST level the low reached (several levels can form a zone)
  reclaim    the composite closes back above the touched level (reclaim_ref = touched_level) or the zone top
  last       N >= 2: the last candle itself closes in the trade direction
  strength   strength_min x MR <= composite range <= strength_max x MR (MR = median range of the bars before the window)
  indecision close location inside indecision_band (0.40-0.60) = indecision: a follow-through candle is needed (N + 1)
  score      weighted wick / close location / body (+ time, divergence, reclaim depth, overlap when enabled) >= rejection_min

The blended label ("hammer-like", "engulfing-like", ...) is for logs and charts only - no decision ever uses a pattern
name. Only closed candles are used and a window always ends on the last closed candle, so a result for bar j depends on
bars <= j only (causal). The caller passes closed candles; `completed_only()` drops a candle still forming.
"""
from typing import Any, Dict, List, Optional, Sequence, Union

import numpy as np
import pandas as pd

from app.price_action import pa_settings
from app.price_action.breaks import median_range

# Reasons. The order decides which failed window is "closest" when none passes.
NO_DATA, NO_TOUCH, INV, NO_RECLAIM, LAST_AGAINST = "no_data", "no_touch", "beyond_inv", "no_reclaim", "last_against"
WEAK, EXHAUSTION, INDECISIVE, LOW_SCORE = "weak", "exhaustion", "indecision", "low_score"
PATH, NO_BODY = "path_retrace", "no_body"
_STAGE = {NO_DATA: -1, NO_TOUCH: 0, INV: 1, NO_RECLAIM: 2, LAST_AGAINST: 3, PATH: 3, WEAK: 4, EXHAUSTION: 4, NO_BODY: 4,
          INDECISIVE: 5, LOW_SCORE: 6}
REASON_TEXT = {
    NO_DATA: "not enough closed candles to measure the market's normal range",
    NO_TOUCH: "price did not reach the level",
    INV: "price went beyond the invalidation level",
    NO_RECLAIM: "price did not close back on the level's side",
    LAST_AGAINST: "the last candle closed against the direction",
    PATH: "the last candle gave back more than half of the move",
    WEAK: "the candles were too small compared with the normal range (no real push)",
    EXHAUSTION: "the candles were too large compared with the normal range (spike / exhaustion)",
    NO_BODY: "no body in the direction and no deep reclaim",
    INDECISIVE: "the close sits in the middle of the range (indecision) - a follow-through candle is needed",
    LOW_SCORE: "the rejection was not strong enough",
}


# ---------------------------------------------------------------------------------------------------------------------
# Composite mode (elliott/reversal.py)
# ---------------------------------------------------------------------------------------------------------------------
class Bars:
    """numpy arrays of one timeframe frame (built once) plus its median range array."""

    def __init__(self, frame: pd.DataFrame, mr: np.ndarray) -> None:
        self.frame = frame
        self.o, self.h, self.l, self.c = (frame[k].to_numpy(float) for k in ("open", "high", "low", "close"))
        self.mr = mr
        self.n = len(self.c)


def _window(b: Bars, a: int, j: int, dirn: int):
    """Composite of bars a..j, mirrored for a bearish read (prices negated, high and low swapped)."""
    if dirn > 0:
        return b.o[a], b.h[a:j + 1].max(), b.l[a:j + 1].min(), b.c[j], b.c[a:j + 1]
    return -b.o[a], -b.l[a:j + 1].min(), -b.h[a:j + 1].max(), -b.c[j], -b.c[a:j + 1]


def _label(n, o, h, l, c, fo, fc, mo, mc, dirn) -> str:
    """Blended classic name - LOG / REPORT ONLY, never used in a decision. Works on mirrored (bullish) prices.
    fo/fc = first candle, mo/mc = second (N=2) or middle (N=3)."""
    rng = h - l
    if rng <= 0:
        return "other"
    wick, cl, body = (min(o, c) - l) / rng, (c - l) / rng, abs(c - o) / rng
    bull = dirn > 0
    if n == 1:
        if wick >= 0.5 and cl >= 0.6:
            return "hammer-like" if bull else "shooting-star-like"
        if body >= 0.6 and cl >= 0.8:
            return "marubozu-like"
        return "other"
    lo1, hi1 = min(fo, fc), max(fo, fc)
    if n == 2:
        if fc < fo and mo <= fc + 1e-12 and mc >= fo:
            return "engulfing-like"
        if fc < fo and hi1 > lo1 and lo1 + 0.5 * (hi1 - lo1) <= c <= hi1:
            return "piercing-like" if bull else "dark-cloud-like"
        return "other"
    if fc < fo and abs(mc - mo) <= 0.3 * max(hi1 - lo1, 1e-12) and c >= lo1 + 0.5 * (hi1 - lo1):
        return "star-like"
    return "other"


def evaluate_window(b: Bars, j: int, n: int, dirn: int, levels: Sequence[float], tol: float, s: Dict[str, Any], inv=None,
                    min_start: int = 0, extreme_idx: Optional[int] = None, bars_last_subleg: Optional[int] = None,
                    div_ok: Optional[bool] = None, reclaim_ref: Optional[str] = None, mr_override=None,
                    rmin: Optional[float] = None) -> Dict[str, Any]:
    """Window = bars [j - n + 1, j]. levels = zone levels (prices); inv = hard invalidation or None.
    mr_override = strength reference (a number or a function of the window start); rmin = rejection_min override."""
    out: Dict[str, Any] = {"ok": False, "reason": NO_DATA, "n": n, "score": 0.0, "touched": None, "comp": None, "close_loc": None,
                           "rng_ratio": None, "parts": {}, "label": None}
    reclaim_ref = reclaim_ref or s["reclaim_ref"]
    a = j - n + 1
    if a < max(min_start, 0) or j >= b.n or not levels:
        return out
    m = b.mr[a] if mr_override is None else (mr_override(a) if callable(mr_override) else mr_override)
    if m is None:
        m = b.mr[a]
    if not np.isfinite(m) or m <= 0:
        return out
    o, h, l, c, closes = _window(b, a, j, dirn)
    out["comp"] = (float(b.o[a]), float(b.h[a:j + 1].max()), float(b.l[a:j + 1].min()), float(b.c[j]))
    lv = sorted(dirn * float(x) for x in levels)                     # mirrored: the touch is always from above
    rng = h - l
    out["close_loc"] = (c - l) / rng if rng > 0 else 0.0
    out["rng_ratio"] = rng / m
    fo, fc = (b.o[a], b.c[a]) if dirn > 0 else (-b.o[a], -b.c[a])
    mo, mc = ((b.o[a + 1], b.c[a + 1]) if dirn > 0 else (-b.o[a + 1], -b.c[a + 1])) if n >= 2 else (fo, fc)
    out["label"] = _label(n, o, h, l, c, fo, fc, mo, mc, dirn)
    if not l <= lv[-1] + tol:
        out["reason"] = NO_TOUCH
        return out
    if inv is not None:
        iv = dirn * float(inv)
        if not l > iv or (closes <= iv).any():
            out["reason"] = INV
            return out
    touched = next(x for x in lv if l <= x + tol)                    # the deepest level touched
    out["touched"] = dirn * touched
    ref = touched if reclaim_ref == "touched_level" else lv[-1]
    if not c > ref:
        out["reason"] = NO_RECLAIM
        return out
    if n >= 2:
        last_o, last_c = (b.o[j], b.c[j]) if dirn > 0 else (-b.o[j], -b.c[j])
        if not last_c > last_o:
            out["reason"] = LAST_AGAINST
            return out
        if s.get("path_checks"):                                     # how much the last candle gave back on its own
            last_h, prev_c = (b.h[j], b.c[j - 1]) if dirn > 0 else (-b.l[j], -b.c[j - 1])
            if (max(last_h, prev_c) - c) / rng > 0.5:
                out["reason"] = PATH
                return out
    if rng < s["strength_min"] * m:
        out["reason"] = WEAK
        return out
    if rng > s["strength_max"] * m:
        if s.get("strength_cap_mode", "fixed") == "fixed" or out["close_loc"] < 0.6:
            out["reason"] = EXHAUSTION
            return out
        guard = s.get("strength_risk_guard_mult", 0.0)
        if guard > 0 and rng > guard * m:
            out["reason"] = EXHAUSTION
            return out
    w = s["rejection_weights"]
    body_bull = abs(c - o) / rng * (c > o)
    hi1, lo1 = max(fo, fc), min(fo, fc)
    depth = float(np.clip((c - lo1) / (hi1 - lo1), 0.0, 1.0)) if n >= 2 and hi1 > lo1 and fc < fo else 0.0
    body_v = body_bull
    if s.get("body_term_mode", "bull_body") == "body_or_reclaim" and depth >= 0.5:
        body_v = max(body_bull, depth)
    if s.get("min_body_or_reclaim") and (c - o) / rng < s.get("min_body_frac", 0.10) and depth < 0.5:
        out["reason"] = NO_BODY
        return out
    comps = [("wick", w[0], (min(o, c) - l) / rng), ("close_loc", w[1], (c - l) / rng), ("body", w[2], body_v)]
    if extreme_idx is not None and bars_last_subleg is not None:
        comps.append(("time", w[3], float(j - extreme_idx <= bars_last_subleg)))
    if div_ok is not None:
        comps.append(("div", w[4], float(div_ok)))
    if s.get("w_reclaim_depth", 0.0) > 0:
        rec, stab = float(np.clip((c - touched) / m, 0.0, 1.0)), float(np.clip((touched - l) / m, 0.0, 1.0))
        comps.append(("reclaim_depth", s["w_reclaim_depth"], 0.5 * (rec + stab)))
    if s.get("w_overlap", 0.0) > 0:
        p0 = max(a - 3, 0)
        if p0 < a:
            ph, pl = (b.h[p0:a].max(), b.l[p0:a].min()) if dirn > 0 else (-b.l[p0:a].min(), -b.h[p0:a].max())
            ov = max(0.0, min(h, ph) - max(l, pl)) / rng
            comps.append(("low_overlap", s["w_overlap"], 1.0 - min(ov, 1.0)))
    tw = sum(x for _, x, _ in comps)
    out["score"] = sum(x * v for _, x, v in comps) / tw if tw > 0 else 0.0
    if n >= 3 and s.get("n3_penalty", 0.0) > 0:
        out["score"] -= s["n3_penalty"]
    out["parts"] = {k: round(float(v), 4) for k, _, v in comps}
    band = s.get("indecision_band", pa_settings.DEFAULTS["indecision_band"])
    if band[0] <= out["close_loc"] <= band[1]:
        out["reason"] = INDECISIVE
        return out
    if out["score"] < (s["rejection_min"] if rmin is None else rmin):
        out["reason"] = LOW_SCORE
        return out
    out.update(ok=True, reason=None)
    return out


def evaluate(b: Bars, j: int, dirn: int, levels: Sequence[float], tol: float, s: Dict[str, Any], **kw) -> Dict[str, Any]:
    """N = 1..touch_reclaim_window (+ indecision follow-through): the best-scoring passing window (a tie goes to the smaller
    N); otherwise the "closest" failing window. The window always ends on bar j.
    Follow-through: legacy - N = window on j - 1 indecisive and bar j closes in the direction => N + 1; addendum - see
    `_followthrough`."""
    nmax = s["touch_reclaim_window"]
    res = [evaluate_window(b, j, n, dirn, levels, tol, s, **kw) for n in range(1, nmax + 1)]
    if 1 <= j < b.n:
        if s.get("followthrough_mode", "legacy") == "legacy":
            prev = evaluate_window(b, j - 1, nmax, dirn, levels, tol, s, **kw)
            if prev["reason"] == INDECISIVE and dirn * (b.c[j] - b.o[j]) > 0:
                res.append(evaluate_window(b, j, nmax + 1, dirn, levels, tol, s, **kw))
        else:
            res += _followthrough(b, j, dirn, levels, tol, s, nmax, kw)
    ok = [r for r in res if r["ok"]]
    if ok:
        return max(ok, key=lambda r: (r["score"], -r["n"]))
    return max(res, key=lambda r: (_STAGE.get(r["reason"], -1), r["score"], -r["n"]))


def _followthrough(b: Bars, j: int, dirn: int, levels, tol, s, nmax, kw) -> List[Dict[str, Any]]:
    """Addendum mode: a composite ending on bar j - k (k <= followthrough_max_bars) was indecisive (close location in the
    band, touch / reclaim / strength passed); no candle in between triggered and none closed beyond the invalidation;
    bar j closes beyond that composite's close in the trade direction => trigger. Score = the indecisive composite's."""
    out: List[Dict[str, Any]] = []
    rmin = kw.get("rmin")
    kw = {k: v for k, v in kw.items() if k != "rmin"}
    inv = kw.get("inv")
    for k in range(1, s.get("followthrough_max_bars", 1) + 1):
        e = j - k
        if e < 0:
            break
        if inv is not None and any(dirn * b.c[x] <= dirn * float(inv) for x in range(e + 1, j + 1)):
            break
        if dirn * (b.c[j] - b.c[e]) <= 0:
            continue
        if any(evaluate_window(b, x, n, dirn, levels, tol, s, rmin=rmin, **kw)["ok"] for x in range(e + 1, j) for n in range(1, nmax + 1)):
            break                                                    # a candle in between would have triggered - that is the signal
        for n in range(1, nmax + 1):
            prev = evaluate_window(b, e, n, dirn, levels, tol, s, rmin=rmin, **kw)
            if prev["reason"] != INDECISIVE:
                continue
            if s.get("followthrough_score_gate"):
                mw = evaluate_window(b, j, n + k, dirn, levels, tol, s, rmin=rmin, **kw)
                if not mw["parts"] or mw["score"] < (s["rejection_min"] if rmin is None else rmin) - s["followthrough_score_relax"]:
                    continue
            a = e - n + 1
            out.append(dict(prev, ok=True, reason=None, n=n + k, followthrough=True,
                            comp=(float(b.o[a]), float(b.h[a:j + 1].max()), float(b.l[a:j + 1].min()), float(b.c[j]))))
    return out


def retest_fn(frame: pd.DataFrame, s: Dict[str, Any], mr: np.ndarray):
    """Failed-retest hook for `breaks.first_real_break`. A break candidate at t (side "below") was reclaimed; within the
    next touch_reclaim_window + 1 bars the level is rejected FROM THE BROKEN SIDE by a logical reversal => confirm on that
    bar. Every window candle is after t, the composite starts beyond the level (first open on the broken side) and the
    rejecting close is beyond level -/+ buffer - price really came back to the level from below and was thrown back.
    No time / divergence parts. Candle experiments are neutralised (`core_candle`) so they never move breaks."""
    s = pa_settings.core_candle(s)
    b = Bars(frame, mr)

    def fn(_frame: pd.DataFrame, t: int, level: float, side: str, end: int) -> Optional[int]:
        dirn = -1 if side == "below" else 1                          # a below-break is retested from below => bearish rejection
        tol = s["break_buffer_mr"] * (mr[t] if np.isfinite(mr[t]) else 0.0)
        last = min(end, t + 1 + s["touch_reclaim_window"])
        for j in range(t + 1, last + 1):
            beyond = b.c[j] < level - tol if side == "below" else b.c[j] > level + tol
            if not beyond:
                continue
            for n in range(1, min(s["touch_reclaim_window"], j - t) + 1):
                a = j - n + 1
                started = b.o[a] < level if side == "below" else b.o[a] > level
                if started and evaluate_window(b, j, n, dirn, [level], tol, s, min_start=t + 1)["ok"]:
                    return j
        return None
    return fn


# ---------------------------------------------------------------------------------------------------------------------
# score100 mode (price_action/candles.py #241)
# ---------------------------------------------------------------------------------------------------------------------
SCORE_REASON = {NO_DATA: "REJECTION_NO_DATA", NO_TOUCH: "REJECTION_NO_TOUCH", NO_RECLAIM: "REJECTION_NO_RECLAIM",
                LAST_AGAINST: "REJECTION_LAST_CANDLE_AGAINST", WEAK: "REJECTION_WEAK_CANDLE", EXHAUSTION: "REJECTION_EXHAUSTION",
                INDECISIVE: "REJECTION_INDECISION", LOW_SCORE: "REJECTION_SCORE"}   # the #241 skip codes, for logs


def completed_only(df: pd.DataFrame, tf_minutes: int, now) -> pd.DataFrame:
    """Candles completed by `now` (timestamp = candle start; timestamp + tf <= now). Mixed naive / aware times are
    compared in IST."""
    if df is None or len(df) == 0:
        return df
    ts = pd.to_datetime(df["timestamp"])
    now_ts = pd.Timestamp(now)
    if getattr(ts.dt, "tz", None) is not None and now_ts.tzinfo is None:
        ts = ts.dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
    elif getattr(ts.dt, "tz", None) is None and now_ts.tzinfo is not None:
        now_ts = now_ts.tz_convert("Asia/Kolkata").tz_localize(None)
    return df[(ts + pd.Timedelta(minutes=tf_minutes) <= now_ts).to_numpy()].reset_index(drop=True)


def composite(window: pd.DataFrame) -> Dict[str, Any]:
    out = {"open": float(window["open"].iloc[0]), "high": float(window["high"].max()), "low": float(window["low"].min()),
           "close": float(window["close"].iloc[-1])}
    if "timestamp" in window:
        out.update(ts_start=pd.Timestamp(window["timestamp"].iloc[0]), ts_end=pd.Timestamp(window["timestamp"].iloc[-1]))
    return out


def _score_label(n: int, sweep: bool, bullish: bool) -> str:
    if sweep:
        return "failed-breakdown-like" if bullish else "failed-breakout-like"
    return {1: "hammer-like" if bullish else "shooting-star-like", 2: "engulfing-like"}.get(n, "star-like")


def score_window(df: pd.DataFrame, n: int, level: float, bullish: bool, s: Dict[str, Any]) -> Dict[str, Any]:
    """#241: the composite of the last `n` closed candles of df, scored 0-100."""
    out: Dict[str, Any] = {"ok": False, "reason": NO_DATA, "n": n, "score": 0.0, "parts": {}, "label": None, "composite": None,
                           "median_range": None, "close_loc": None, "sweep": False}
    if df is None or len(df) < n + s["score_min_median_candles"]:
        return out
    window = df.iloc[len(df) - n:]
    hist = df.iloc[max(0, len(df) - n - s["median_range_n"]): len(df) - n]
    med = float((hist["high"] - hist["low"]).median()) if len(hist) >= s["score_min_median_candles"] else 0.0
    comp = composite(window)
    out.update(composite=comp, median_range=round(med, 6) if med else None)
    if med <= 0:
        return out
    tol = abs(float(level)) * s["score_touch_tol_pct"] / 100.0
    sign = 1 if bullish else -1
    o, h, l, c, lv = sign * comp["open"], sign * (comp["high"] if bullish else comp["low"]), sign * (comp["low"] if bullish else comp["high"]), \
        sign * comp["close"], sign * float(level)
    rng = h - l
    close_loc = (c - l) / rng if rng > 0 else 0.0
    out["close_loc"] = round(close_loc, 3)
    if not l <= lv + tol:
        out["reason"] = NO_TOUCH
        return out
    if not c >= lv:
        out["reason"] = NO_RECLAIM
        return out
    if n >= 2:
        lo_, lc_ = float(window["open"].iloc[-1]), float(window["close"].iloc[-1])
        if not ((lc_ > lo_) if bullish else (lc_ < lo_)):
            out["reason"] = LAST_AGAINST
            return out
    if rng < s["strength_min"] * med:
        out["reason"] = WEAK
        return out
    if rng > s["strength_max"] * med:
        out["reason"] = EXHAUSTION
        return out
    closes = window["close"].to_numpy(float) * sign
    below = closes < lv
    sweep = bool(below.any() and n >= 2 and any(closes[k] >= lv for k in range(int(below.argmax()) + 1, len(closes))))
    w = s["score_weights"]
    speed = s["score_speed"]
    clamp = lambda x: max(0.0, min(1.0, float(x)))              # noqa: E731
    parts = {"wick": round(w["wick"] * clamp((min(o, c) - l) / rng), 1),
             "close_loc": round(w["close_loc"] * clamp(close_loc), 1),
             "bounce": round(w["bounce"] * clamp((c - l) / med / s["score_bounce_full_mr"]), 1),
             "sweep": float(w["sweep"]) if sweep else 0.0,
             "speed": float(speed[n - 1]) if n <= len(speed) else 0.0}
    out.update(parts=parts, score=round(sum(parts.values()), 1), label=_score_label(n, sweep, bullish), sweep=sweep)
    band = s["indecision_band"]
    if band[0] <= close_loc <= band[1]:
        out["reason"] = INDECISIVE
        return out
    if out["score"] < s["score_min"]:
        out["reason"] = LOW_SCORE
        return out
    out.update(ok=True, reason=None)
    return out


def score_rejection(df: pd.DataFrame, level: float, bullish: bool, s: Dict[str, Any]) -> Dict[str, Any]:
    """#241: N = 1..3 (and N = 4 for an indecision follow-through) - the best passing window, else the closest failure."""
    nmax = len(s["score_speed"])
    results = [score_window(df, n, level, bullish, s) for n in range(1, nmax + 1)]
    if df is not None and len(df) >= nmax + 2:
        prev = score_window(df.iloc[:-1], nmax, level, bullish, s)
        last_o, last_c = float(df["open"].iloc[-1]), float(df["close"].iloc[-1])
        if prev["reason"] == INDECISIVE and ((last_c > last_o) if bullish else (last_c < last_o)):
            results.append(score_window(df, nmax + 1, level, bullish, s))
    passed = [r for r in results if r["ok"]]
    if passed:
        return max(passed, key=lambda r: (r["score"], -r["n"]))
    return max(results, key=lambda r: (_STAGE.get(r["reason"], -1), r["score"], -r["n"]))


# ---------------------------------------------------------------------------------------------------------------------
# The one API
# ---------------------------------------------------------------------------------------------------------------------
def _dirn(direction: Union[str, int]) -> int:
    if isinstance(direction, str):
        d = direction.strip().upper()
        if d in ("BULLISH", "LONG", "UP", "SUPPORT"):
            return 1
        if d in ("BEARISH", "SHORT", "DOWN", "RESISTANCE"):
            return -1
        raise ValueError(f"unknown direction {direction!r}")
    return 1 if direction > 0 else -1


def evaluate_reversal(candles: pd.DataFrame, level: Union[float, Sequence[float]], direction: Union[str, int],
                      settings: Optional[Dict[str, Any]] = None, *, inv: Optional[float] = None, extreme_idx: Optional[int] = None,
                      bars_last_subleg: Optional[int] = None, div_ok: Optional[bool] = None) -> Dict[str, Any]:
    """Did price logically reverse at `level` on the last CLOSED candle of `candles`?

    candles: closed candles, oldest first (open/high/low/close; `timestamp` optional). level: one price or the levels of a
    zone. direction: BULLISH (support - expect a bounce up) or BEARISH (resistance). settings: `pa_settings` overrides;
    `reversal_mode` picks composite (default, elliott/reversal.py) or score100 (price_action/candles.py).
    Returns {valid, score, score_pct (0-100), parts, reason, reason_text, label, n, touched, composite, close_loc, mode}.
    The label is descriptive only."""
    s = settings if settings is not None and "reversal_mode" in settings else pa_settings.settings(**(settings or {}))
    dirn = _dirn(direction)
    levels = [float(level)] if np.isscalar(level) else [float(x) for x in level]
    mode = s["reversal_mode"]
    df = candles.reset_index(drop=True) if candles is not None else None
    if df is None or len(df) == 0 or not levels:
        return {"valid": False, "score": 0.0, "score_pct": 0.0, "parts": {}, "reason": NO_DATA, "reason_text": REASON_TEXT[NO_DATA],
                "label": None, "n": 0, "touched": None, "composite": None, "close_loc": None, "mode": mode}
    if mode == "score100":
        lvl = max(levels) if dirn > 0 else min(levels)                       # #241 works on one level: the zone's near edge
        r = score_rejection(df, lvl, dirn > 0, s)
        return {"valid": r["ok"], "score": r["score"], "score_pct": r["score"], "parts": r["parts"], "reason": r["reason"],
                "reason_text": REASON_TEXT.get(r["reason"]) if r["reason"] else None, "reason_code": SCORE_REASON.get(r["reason"]),
                "label": r["label"], "n": r["n"], "touched": lvl if r["reason"] not in (NO_DATA, NO_TOUCH) else None,
                "composite": r["composite"], "close_loc": r["close_loc"], "sweep": r.get("sweep", False), "mode": mode}
    b = Bars(df, median_range(df, s["median_range_n"]))
    j = b.n - 1
    tol = s["touch_tol_mr"] * (b.mr[j] if np.isfinite(b.mr[j]) else 0.0)
    r = evaluate(b, j, dirn, levels, tol, s, inv=inv, extreme_idx=extreme_idx, bars_last_subleg=bars_last_subleg, div_ok=div_ok)
    comp = None
    if r["comp"] is not None:
        o, h, l, c = r["comp"]
        comp = {"open": o, "high": h, "low": l, "close": c}
    return {"valid": r["ok"], "score": round(float(r["score"]), 4), "score_pct": round(100.0 * max(float(r["score"]), 0.0), 1),
            "parts": r["parts"], "reason": r["reason"], "reason_text": REASON_TEXT.get(r["reason"]) if r["reason"] else None,
            "label": r["label"], "n": r["n"], "touched": r["touched"], "composite": comp, "close_loc": r["close_loc"],
            "followthrough": bool(r.get("followthrough")), "mode": mode}


# ---------------------------------------------------------------------------------------------------------------------
# Helpers for entries and charts (price_action/candles.py)
# ---------------------------------------------------------------------------------------------------------------------
def structure_stop(result: Dict[str, Any], level: float, direction: Union[str, int], buffer_mr: float, median_range_value: float) -> float:
    """Stop beyond the composite extreme by `buffer_mr` median ranges - and never on the wrong side of the level."""
    comp, lv, buf = result["composite"], float(level), buffer_mr * median_range_value
    if _dirn(direction) > 0:
        return round(min(comp["low"], lv) - buf, 4)
    return round(max(comp["high"], lv) + buf, 4)


def chase_ok(entry: float, level: float, stop: float, direction: Union[str, int], max_frac: float = 0.5) -> bool:
    """The entry's distance past the level is at most `max_frac` of the entry-to-stop distance (no chasing)."""
    entry, level, stop = float(entry), float(level), float(stop)
    if _dirn(direction) > 0:
        return (entry - level) <= max_frac * (entry - stop)
    return (level - entry) <= max_frac * (stop - entry)


def pullback_price(result: Dict[str, Any]) -> float:
    """The middle of the composite candle's range (a limit entry on a pullback)."""
    comp = result["composite"]
    return round((comp["high"] + comp["low"]) / 2.0, 4)


def describe(result: Dict[str, Any]) -> str:
    parts = " ".join(f"{k}={v:g}" for k, v in (result.get("parts") or {}).items())
    return f"N={result['n']} score={result['score']:g} {result.get('label') or ''} [{parts}]".strip()


def scan_markers(df: pd.DataFrame, levels: Sequence[float], settings: Optional[Dict[str, Any]] = None,
                 max_distance_mr: float = 6.0) -> List[Dict[str, Any]]:
    """Chart annotations: for every closed candle, the best passing reversal at a nearby level (within `max_distance_mr`
    median ranges); the side comes from that bar's close. One marker per reversal (an overlapping window is not repeated).
    Each marker uses only bars <= its own index."""
    s = settings if settings is not None and "reversal_mode" in settings else pa_settings.settings(**(settings or {}))
    out: List[Dict[str, Any]] = []
    if df is None or len(df) < s["median_range_n"] + 2:
        return out
    df = df.reset_index(drop=True)
    mr = median_range(df, s["median_range_n"])
    last_end = -1
    for i in range(s["median_range_n"] + 1, len(df)):
        close = float(df["close"].iloc[i])
        m = mr[i]
        if not np.isfinite(m) or m <= 0:
            continue
        best = None
        for lv in levels:
            if abs(close - float(lv)) > max_distance_mr * m:
                continue
            r = evaluate_reversal(df.iloc[: i + 1], float(lv), 1 if close >= float(lv) else -1, s)
            if r["valid"] and (best is None or r["score_pct"] > best[1]["score_pct"]):
                best = (float(lv), r)
        if best is not None and i - best[1]["n"] + 1 > last_end:
            last_end = i
            out.append({"index": i, "level": best[0], "bullish": close >= best[0], "score_pct": best[1]["score_pct"],
                        "n": best[1]["n"], "label": best[1]["label"]})
    return out
