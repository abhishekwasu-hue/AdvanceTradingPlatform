"""Leg classifier (OHLC only, no indicator): impulse vs corrective (pullback) legs between confirmed swings.

Ported from Trade@1e6c9ac, price_action/legs.py (T2.1-T2.3): the scale, the swing cores, the leg features, the
classifier and the provisional current leg. Logic unchanged; texts in English; the Elliott Tier-A check and Trade's
plotting helpers are not ported. Parity with Trade's own output on the frozen fixtures: tests/test_ch3b_trade_parity.py.

A leg runs from one confirmed swing to the next. Labels: STRONG_IMPULSE / WEAK_IMPULSE / HEALTHY_PULLBACK /
DANGEROUS_PULLBACK / MIXED_PULLBACK / REVERSAL / RANGE.
Scale: median_range(N) = the median (H - L) of the last N complete candles (not ATR); every size is "x median_range", so
the same rules hold on any instrument or timeframe.
Swings (both carry pivot_bar and confirm_bar):
  * fractal(R): high[j] above the R bars left and right -> pivot; confirm_bar = j + R (not known before).
  * ZigZag (range-normalised): a move back from the extreme >= k x median_range -> the extreme is a pivot; confirm_bar = the
    bar that completed that move. Two scales: internal (small k) and swing (large k). Legs = moves between swing-scale
    pivots; internal pivots are for CHoCH.
No look-ahead: a leg (pivot a -> pivot b) is known only from b's confirm_bar (`known_at`). The current leg uses past data.
Label rules (thresholds in LegConfig):
  impulse / pullback role: depth = the leg's length / the previous leg's. depth <= 1 -> PULLBACK. depth > 1 after a pullback
  -> IMPULSE (the trend goes on). depth > 1 after an impulse -> REVERSAL (100 %+ retrace), then taken as the new impulse.
  STRONG_IMPULSE: efficiency >= e_hi, overlap <= o_lo, >= 1 displacement or FVG; else WEAK_IMPULSE.
  DANGEROUS_PULLBACK: depth > r_warn, or a displacement / FVG in the pullback's direction, or speed >= the impulse's, or
  CHoCH + displacement.
  HEALTHY_PULLBACK: depth <= r_ok, speed < impulse speed x s_ratio, spread < 1, no displacement, mixed colours.
  MIXED_PULLBACK: neither healthy nor dangerous. RANGE: efficiency <= e_range, overlap >= o_range, alternation >= alt_range
  (overrides the others, except REVERSAL).
No Fibonacci gate - the retracement depth is a continuous feature. Reference only - nothing here places an order.
"""
import copy
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

STRONG_IMPULSE, WEAK_IMPULSE = "STRONG_IMPULSE", "WEAK_IMPULSE"
HEALTHY_PULLBACK, DANGEROUS_PULLBACK, MIXED_PULLBACK = "HEALTHY_PULLBACK", "DANGEROUS_PULLBACK", "MIXED_PULLBACK"
REVERSAL, RANGE = "REVERSAL", "RANGE"
LABELS = (STRONG_IMPULSE, WEAK_IMPULSE, HEALTHY_PULLBACK, DANGEROUS_PULLBACK, MIXED_PULLBACK, REVERSAL, RANGE)
ROLE_IMPULSE, ROLE_PULLBACK = "IMPULSE", "PULLBACK"

LABEL_TEXT = {
    STRONG_IMPULSE: "strong impulse",
    WEAK_IMPULSE: "weak impulse",
    HEALTHY_PULLBACK: "healthy pullback",
    DANGEROUS_PULLBACK: "dangerous pullback",
    MIXED_PULLBACK: "mixed pullback",
    REVERSAL: "reversal",
    RANGE: "range",
}


@dataclass
class LegConfig:
    n_median: int = 20                 # median_range window (complete candles)
    fractal_r: int = 2
    k_internal: float = 1.5            # ZigZag internal scale (x median_range)
    k_swing: float = 3.0               # ZigZag swing scale
    e_hi: float = 0.45                 # STRONG: minimum efficiency (net / sum of ranges)
    o_lo: float = 0.35                 # STRONG: maximum overlap
    d_k: float = 1.5                   # displacement: body >= d_k x median_range
    d_body: float = 0.6                # displacement: body % >= d_body
    fvg_min: float = 0.1               # smallest FVG / imbalance counted (x median_range)
    r_ok: float = 0.5                  # HEALTHY: maximum depth
    r_warn: float = 0.80               # DANGEROUS: depth > r_warn (valid pullback 38.2-80 %)
    s_ratio: float = 0.8               # HEALTHY: speed < impulse speed x s_ratio
    healthy_max_dir: float = 0.8       # HEALTHY: "mixed colours" -> candles in the leg's direction <= 80 %
    e_range: float = 0.15              # RANGE
    o_range: float = 0.55
    alt_range: float = 0.5


# ---------------------------------------------------------------------------------------------------------------------
# Scale and swings
# ---------------------------------------------------------------------------------------------------------------------
def _arr(df: pd.DataFrame) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    return (df["open"].to_numpy(float), df["high"].to_numpy(float), df["low"].to_numpy(float), df["close"].to_numpy(float))


def median_range(df: pd.DataFrame, n: int = 20) -> np.ndarray:
    """At bar i: the median (H - L) of bars [i - n + 1 .. i] (all complete). At least n // 2 bars at the start; NaN before."""
    rng = (df["high"].astype(float) - df["low"].astype(float))
    return rng.rolling(n, min_periods=max(n // 2, 1)).median().to_numpy(float)


@dataclass
class Pivot:
    kind: str            # "H" / "L"
    pivot_bar: int
    confirm_bar: int
    price: float
    scale: str = "swing"


def fractal_pivots(df: pd.DataFrame, r: int = 2) -> List[Pivot]:
    """Fractal swings: pivot j confirmed at j + r. Same-kind pivots may follow each other (not alternating like ZigZag)."""
    _, h, l, _ = _arr(df)
    n, out = len(h), []
    for j in range(r, n - r):
        if h[j] > h[j - r:j].max() and h[j] >= h[j + 1:j + r + 1].max():
            out.append(Pivot("H", j, j + r, float(h[j]), "fractal"))
        if l[j] < l[j - r:j].min() and l[j] <= l[j + 1:j + r + 1].min():
            out.append(Pivot("L", j, j + r, float(l[j]), "fractal"))
    return sorted(out, key=lambda p: (p.confirm_bar, p.pivot_bar))


def zigzag_pivots(df: pd.DataFrame, k: float = 3.0, n_median: int = 20, scale: str = "swing", mr: Optional[np.ndarray] = None) -> List[Pivot]:
    """Range-normalised ZigZag: a move back from the extreme >= k x median_range (at that bar) confirms the extreme.
    Pivots alternate H / L. The threshold at bar i uses bars up to i only."""
    _, h, l, _ = _arr(df)
    mr = median_range(df, n_median) if mr is None else mr
    n = len(h)
    out: List[Pivot] = []
    if n < 2:
        return out
    trend = 0
    hi_i, lo_i = 0, 0
    for i in range(1, n):
        thr = k * mr[i] if np.isfinite(mr[i]) else np.inf
        if trend >= 0 and h[i] >= h[hi_i]:
            hi_i = i
        if trend <= 0 and l[i] <= l[lo_i]:
            lo_i = i
        if trend >= 0 and hi_i < i and h[hi_i] - l[i] >= thr:
            out.append(Pivot("H", hi_i, i, float(h[hi_i]), scale))
            lo_i = hi_i + 1 + int(np.argmin(l[hi_i + 1:i + 1]))             # the lowest bar after the pivot (up to i only)
            trend = -1
            continue
        if trend <= 0 and lo_i < i and h[i] - l[lo_i] >= thr:
            out.append(Pivot("L", lo_i, i, float(l[lo_i]), scale))
            hi_i = lo_i + 1 + int(np.argmax(h[lo_i + 1:i + 1]))
            trend = 1
    return out


# ---------------------------------------------------------------------------------------------------------------------
# Leg features (T2.2)
# ---------------------------------------------------------------------------------------------------------------------
@dataclass
class Leg:
    start_bar: int
    end_bar: int
    known_at: int                 # the end pivot's confirm_bar (the current leg: the current bar)
    direction: int                # +1 up, -1 down
    start_price: float
    end_price: float
    features: Dict[str, Any] = field(default_factory=dict)
    role: str = ""
    label: str = ""
    score: float = 0.0
    reasons: List[str] = field(default_factory=list)
    provisional: bool = False
    disp_mask: Optional[np.ndarray] = field(default=None, repr=False)     # displacement bars in the leg's direction (for CHoCH)

    def as_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d.pop("disp_mask", None)
        d.update(d.pop("features"))
        d["reasons"] = " · ".join(self.reasons)
        return d


def _max_run(mask: Sequence[bool]) -> int:
    best = cur = 0
    for m in mask:
        cur = cur + 1 if m else 0
        best = max(best, cur)
    return best


def _fallback_mr(h: np.ndarray, l: np.ndarray) -> float:
    m = float(np.nanmedian(h - l)) if len(h) else float("nan")
    return m if np.isfinite(m) and m > 0 else 1.0


def leg_features(df: pd.DataFrame, a: int, b: int, direction: int, start_price: float, end_price: float, mr_ref: Any,
                 cfg: Optional[LegConfig] = None) -> Dict[str, Any]:
    """Features over bars a..b (inclusive). `mr_ref` = the median_range known at the leg's end; every size / mr_ref."""
    cfg = cfg or LegConfig()
    o, h, l, c = (x[a:b + 1] for x in _arr(df))
    s = direction
    m = max(int(b - a + 1), 1)
    mr_ref = float(mr_ref) if mr_ref and np.isfinite(mr_ref) and mr_ref > 0 else _fallback_mr(h, l)
    net = abs(end_price - start_price)
    rng = np.maximum(h - l, 1e-12)
    pc = np.concatenate([[c[0]], c[:-1]])                       # the previous close in the leg (the first bar: its own)
    tr = np.maximum(h, pc) - np.minimum(l, pc)                  # range with the overnight gap - keeps efficiency <= 1
    body = np.abs(c - o)
    sum_dc = float(np.abs(np.diff(c)).sum()) if m > 1 else 0.0
    colour = np.sign(c - o)
    with_dir = colour == s
    against = colour == -s
    alt = float((colour[1:] * colour[:-1] < 0).mean()) if m > 1 else 0.0
    ov = []
    for i in range(1, m):
        inter = min(h[i], h[i - 1]) - max(l[i], l[i - 1])
        ov.append(max(inter, 0.0) / rng[i])
    clv = (((c - l) - (h - c)) / rng) * s
    # FVG (3-candle) and body gaps - in the leg's direction and against it
    fvg_n = fvg_sz = fvg_opp = 0
    for i in range(2, m):
        gap_up, gap_dn = l[i] - h[i - 2], l[i - 2] - h[i]
        g, go = (gap_up, gap_dn) if s > 0 else (gap_dn, gap_up)
        if g >= cfg.fvg_min * mr_ref:
            fvg_n += 1
            fvg_sz += g
        if go >= cfg.fvg_min * mr_ref:
            fvg_opp += 1
    bgap = 0
    for i in range(1, m):
        lo_i, hi_i = min(o[i], c[i]), max(o[i], c[i])
        lo_p, hi_p = min(o[i - 1], c[i - 1]), max(o[i - 1], c[i - 1])
        if (s > 0 and lo_i > hi_p) or (s < 0 and hi_i < lo_p):
            bgap += 1
    disp = (body >= cfg.d_k * mr_ref) & (body / rng >= cfg.d_body)
    disp_with = int((disp & with_dir).sum())
    disp_against = int((disp & against).sum())
    return {
        "bars": m,
        "net_mr": round(net / mr_ref, 3),
        "eff_close": round(abs(c[-1] - c[0]) / sum_dc, 3) if sum_dc > 0 else 0.0,
        "eff_range": round(min(net / float(tr.sum()), 1.0), 3),
        "dir_pct": round(float(with_dir.mean()), 3),
        "max_consec": int(_max_run(with_dir)),
        "alternation": round(alt, 3),
        "body_pct": round(float((body / rng).mean()), 3),
        "clv": round(float(clv.mean()), 3),
        "overlap": round(float(np.mean(ov)), 3) if ov else 0.0,
        "fvg_n": fvg_n, "fvg_mr": round(fvg_sz / mr_ref, 3), "fvg_against": fvg_opp,
        "body_gaps": bgap,
        "disp_n": disp_with, "disp_against": disp_against,
        "speed": round(net / mr_ref / m, 3),
        "spread": round(float(rng.mean()) / mr_ref, 3),
        "mr": round(mr_ref, 4),
    }


# ---------------------------------------------------------------------------------------------------------------------
# Legs + labels (T2.3)
# ---------------------------------------------------------------------------------------------------------------------
def _is_range(f: Dict[str, Any], cfg: LegConfig) -> bool:
    return bool(f["eff_range"] <= cfg.e_range and f["overlap"] >= cfg.o_range and f["alternation"] >= cfg.alt_range)


def _impulse_score(f: Dict[str, Any], cfg: LegConfig) -> float:
    return round(30 * min(f["eff_range"] / max(cfg.e_hi, 1e-9), 1.0) + 25 * max(0.0, 1 - f["overlap"]) + 25 * min(f["disp_n"] + f["fvg_n"], 2) / 2
                 + 10 * f["body_pct"] + 10 * f["dir_pct"], 1)


def _choch(df: pd.DataFrame, leg: Leg, internal: Optional[List[Pivot]], impulse: Optional[Leg]) -> Tuple[bool, bool]:
    """In a pullback leg: did a close break the impulse's last internal pivot on the pullback's side (the protected internal
    swing), and was the breaking candle a displacement? Only internal pivots confirmed before that bar."""
    if impulse is None or not internal:
        return False, False
    _, _, _, c = _arr(df)
    s = leg.direction                                   # the pullback's direction (against the impulse)
    want = "L" if s < 0 else "H"                        # after an up impulse a down pullback -> the impulse's last internal LOW
    pool = [p for p in internal if p.kind == want and impulse.start_bar <= p.pivot_bar <= impulse.end_bar and p.confirm_bar <= leg.end_bar]
    broke = disp = False
    if not pool:
        return False, False
    for i in range(leg.start_bar + 1, leg.end_bar + 1):
        cands = [p for p in pool if p.confirm_bar <= i]
        if not cands:
            continue
        lvl = cands[-1].price
        if (c[i] < lvl) if s < 0 else (c[i] > lvl):
            broke = True
            disp = bool(leg.disp_mask is not None and leg.disp_mask[i - leg.start_bar])
            break
    return broke, disp


def classify(legs: List[Leg], df: Optional[pd.DataFrame] = None, internal: Optional[List[Pivot]] = None,
             cfg: Optional[LegConfig] = None) -> List[Leg]:
    """Sets role / label / score / reasons on the legs (in time order, in place) and returns them. Each leg uses only the
    legs before it."""
    cfg = cfg or LegConfig()
    prev_role: Optional[str] = None
    impulse: Optional[Leg] = None
    prev: Optional[Leg] = None
    for lg in legs:
        f = lg.features
        prev_move = abs(prev.end_price - prev.start_price) if prev is not None else 0.0
        depth = (abs(lg.end_price - lg.start_price) / prev_move) if prev_move > 0 else None    # the true price retrace
        f["depth"] = None if depth is None else round(depth, 3)
        if depth is None or (depth > 1.0 and prev_role == ROLE_PULLBACK):
            role = ROLE_IMPULSE
            reversal = False
        elif depth > 1.0:
            role, reversal = ROLE_IMPULSE, True
        else:
            role, reversal = ROLE_PULLBACK, False
        lg.role = role
        reasons: List[str] = []
        if role == ROLE_IMPULSE:
            lg.score = _impulse_score(f, cfg)
            strong = f["eff_range"] >= cfg.e_hi and f["overlap"] <= cfg.o_lo and (f["disp_n"] + f["fvg_n"]) >= 1
            if reversal:
                assert depth is not None
                lg.label = REVERSAL
                reasons.append(f"{depth * 100:.0f}% retrace of the previous impulse (> 100%)")
            else:
                lg.label = STRONG_IMPULSE if strong else WEAK_IMPULSE
            reasons.append(f"efficiency {f['eff_range']:.2f}, overlap {f['overlap']:.2f}, displacement {f['disp_n']}, FVG {f['fvg_n']}")
            impulse = lg
        else:
            assert depth is not None
            imp_speed = impulse.features["speed"] if impulse is not None else None
            f["speed_ratio"] = round(f["speed"] / imp_speed, 3) if imp_speed else None
            broke, ch_disp = _choch(df, lg, internal, impulse) if df is not None else (False, False)
            f["choch"], f["choch_disp"] = bool(broke), bool(ch_disp)
            danger = []
            if depth > cfg.r_warn:
                danger.append(f"deep ({depth * 100:.0f}% > {cfg.r_warn * 100:.0f}%)")
            if f["disp_n"] > 0 or f["fvg_n"] > 0:
                danger.append(f"displacement {f['disp_n']} / FVG {f['fvg_n']} in the pullback's direction")
            if imp_speed and f["speed"] >= imp_speed:
                danger.append("faster than the impulse")
            if broke and ch_disp:
                danger.append("CHoCH + displacement")
            f["spread_ratio"] = round(f["spread"] / impulse.features["spread"], 3) if impulse is not None and impulse.features.get("spread") else None
            healthy = (depth <= cfg.r_ok and (not imp_speed or f["speed"] < imp_speed * cfg.s_ratio) and (f["spread_ratio"] or 0.0) < 1.0 and f["disp_n"] == 0
                       and f["dir_pct"] <= cfg.healthy_max_dir and depth < 1.0)
            lg.score = round(100 * max(0.0, 1 - depth) * (0.5 + 0.5 * max(0.0, 1 - (f.get("speed_ratio") or 0))), 1)
            if danger:
                lg.label = DANGEROUS_PULLBACK
                reasons += danger
            elif healthy:
                lg.label = HEALTHY_PULLBACK
                reasons.append(f"depth {depth * 100:.0f}%, speed ratio {f.get('speed_ratio')}, spread ratio {f.get('spread_ratio')}")
            else:
                lg.label = MIXED_PULLBACK
                reasons.append(f"depth {depth * 100:.0f}% (r_ok {cfg.r_ok:.2f} - r_warn {cfg.r_warn:.2f}) / other conditions not met")
        if _is_range(f, cfg) and lg.label != REVERSAL:
            lg.label = RANGE
            reasons = [f"efficiency {f['eff_range']:.2f} <= {cfg.e_range}, overlap {f['overlap']:.2f}, alternation {f['alternation']:.2f}"]
        lg.reasons = reasons
        prev_role = role
        prev = lg
    return legs


def _disp_mask(df: pd.DataFrame, a: int, b: int, direction: int, mr_ref: float, cfg: LegConfig) -> np.ndarray:
    o, h, l, c = (x[a:b + 1] for x in _arr(df))
    rng = np.maximum(h - l, 1e-12)
    body = np.abs(c - o)
    return np.asarray((body >= cfg.d_k * mr_ref) & (body / rng >= cfg.d_body) & (np.sign(c - o) == direction))


def pivots_for(df: pd.DataFrame, cfg: Optional[LegConfig] = None) -> Tuple[np.ndarray, List[Pivot], List[Pivot]]:
    """(median_range, swing pivots, internal pivots) - independent of the label thresholds."""
    cfg = cfg or LegConfig()
    mr = median_range(df, cfg.n_median)
    return mr, zigzag_pivots(df, cfg.k_swing, cfg.n_median, "swing", mr), zigzag_pivots(df, cfg.k_internal, cfg.n_median, "internal", mr)


def legs_between(df: pd.DataFrame, swing: Sequence[Pivot], internal: Optional[List[Pivot]], mr: np.ndarray,
                 cfg: Optional[LegConfig] = None) -> List[Leg]:
    """Classified legs between consecutive swing pivots (any pivot source with pivot_bar / confirm_bar) - the loop of
    Trade's `build_legs`, shared so a chart can classify the swings it draws."""
    cfg = cfg or LegConfig()
    legs = []
    for p0, p1 in zip(swing, swing[1:]):
        d = 1 if p1.kind == "H" else -1
        ref = mr[p1.pivot_bar]
        f = leg_features(df, p0.pivot_bar, p1.pivot_bar, d, p0.price, p1.price, ref, cfg)
        legs.append(Leg(p0.pivot_bar, p1.pivot_bar, p1.confirm_bar, d, p0.price, p1.price, f, disp_mask=_disp_mask(df, p0.pivot_bar, p1.pivot_bar, d, ref, cfg)))
    classify(legs, df, internal, cfg)
    return legs


def build_legs(df: pd.DataFrame, cfg: Optional[LegConfig] = None, upto: Optional[int] = None,
               pivots: Optional[Tuple[np.ndarray, List[Pivot], List[Pivot]]] = None) -> Tuple[List[Leg], List[Pivot], List[Pivot]]:
    """df (complete candles, oldest first) -> (legs, swing pivots, internal pivots). `upto` = bar index: only pivots with
    confirm_bar <= upto (the legs known then). Leg features use bars up to the end pivot only."""
    cfg = cfg or LegConfig()
    df = df.reset_index(drop=True)
    if upto is not None:
        df = df.iloc[:upto + 1]
    mr, swing, internal = pivots if (pivots is not None and upto is None) else pivots_for(df, cfg)
    return legs_between(df, swing, internal, mr, cfg), swing, internal


def current_leg(df: pd.DataFrame, cfg: Optional[LegConfig] = None, legs: Optional[List[Leg]] = None, swing: Optional[List[Pivot]] = None,
                internal: Optional[List[Pivot]] = None) -> Optional[Leg]:
    """The current (provisional) leg from the last confirmed swing pivot to the last complete bar - past data only."""
    cfg = cfg or LegConfig()
    df = df.reset_index(drop=True)
    if legs is None:
        legs, swing, internal = build_legs(df, cfg)
    if not swing:
        return None
    last = swing[-1]
    t = len(df) - 1
    if t <= last.pivot_bar:
        return None
    d = -1 if last.kind == "H" else 1
    _, _, _, c = _arr(df)
    end = float(c[t])
    mr = median_range(df, cfg.n_median)
    f = leg_features(df, last.pivot_bar, t, d, last.price, end, mr[t], cfg)
    lg = Leg(last.pivot_bar, t, t, d, last.price, end, f, provisional=True, disp_mask=_disp_mask(df, last.pivot_bar, t, d, mr[t], cfg))
    classify(copy.deepcopy(list(legs)) + [lg], df, internal, cfg)   # a copy - the caller's legs stay as they were
    return lg
