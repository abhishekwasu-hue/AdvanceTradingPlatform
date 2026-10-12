"""N1 L2: the Daily trend (degree-aware Dow), ported from Trade `decision3/daily.py` (`fold_impulse`, Q15 FINAL) with
`decision3/daily_swings.prepare` and `bots_common/level_reaction.prepare_daily`, at the pinned revision. Logic unchanged;
the `why` texts are in English (they are not compared with Trade's; every decision field is).

Input: Daily candles (timestamp = the session date, `bar_end` = that day's close time = known_at). One `DState` per
Daily candle, known at its close:
* Swings: pivot (default; high[i] > the N highs to the left and >= the N to the right, lows mirrored; confirmed at
  i + N's close) or directional change (theta = k x sigma_D). Alternating H / L; the same kind twice keeps the more extreme.
* Start: a lower high (H2 < H1 - tol) and a Daily close below the low between them -> DOWN (UP mirrored), only with an
  impulse-natured leg (Q28). Protected = the impulse's ORIGIN.
* A same-degree correction is a counter swing beyond the impulse's largest inner pullback (Q22); after it, a close
  beyond the impulse extreme starts a new impulse and protected advances to the correction's extreme.
* The origin broken by a Daily close -> phase "origin_broken"; the trend changes only with an opposite impulse (flip).
  A counter-move read as a correction while the parent origin holds -> A1 correction (Q49 / ABC_FIX), no flip.
* RANGE: no trend and two about-equal highs and lows (or the origin broken and then two equal highs / lows, Q27).
  UNKNOWN only with <= `daily_min_sessions` candles.
"""
from __future__ import annotations

import bisect
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from app.price_action.daily_trend import legs as DL
from app.price_action.daily_trend import settings as S3


@dataclass(frozen=True)
class DPivot:
    kind: str           # "H" / "L"
    price: float
    bar: int            # Daily candle index
    day: pd.Timestamp
    confirm_bar: int
    known_at: pd.Timestamp


@dataclass
class DState:
    bar: int
    day: Optional[pd.Timestamp]
    known_at: Optional[pd.Timestamp]
    trend: str
    protected: Optional[DPivot] = None
    band: Optional[Tuple[float, float]] = None
    why: str = ""
    pivots: list = field(default_factory=list)
    phase: Optional[str] = None          # "impulse" / "correction" / "origin_broken"
    wave: Optional[str] = None           # mechanical leg index ("L3" ...), debug only
    corr_label: Optional[str] = None     # the running correction leg "a" ... "e"
    imp_end: Optional[DPivot] = None     # the last impulse's extreme
    mature: bool = False                 # the leg is near / beyond its structural target (evidence, not a gate)
    targets: tuple = ()                  # Q44: the first impulse leg's extreme after the parent origin (A1 only)
    parent: Optional[DPivot] = None      # A1: the larger-degree origin (the trend's start, or the holding parent)
    counter: Optional[str] = None        # "pullback" / "candidate" / "correction" / None
    leg_start: Optional[int] = None      # the running impulse leg's origin bar
    corr_bars: tuple = ()                # same-degree correction swings in the running leg (bars)
    origin_risk: bool = False            # B1: the correction is within maturity_sigma_d x sigma_D of the origin
    sharp: Optional[tuple] = None        # ABC_FIX rule 2: (lo, hi, bar) sharp-fall supply (DOWN) / sharp-rise demand (UP)
    evidence: tuple = ()                 # ABC_FIX rule 3: continuation evidence
    corr_zone: Optional[tuple] = None    # the correction before the running leg ended in a sharp zone
    leg_from: Optional[int] = None       # where the counter-move before the running leg started (bar)
    target_passed: bool = False          # Q44: protected is already beyond the structural target
    wave_a: Optional[DPivot] = None      # Q49: the 5-wave first leg of an A1 correction (wave A)
    alt: Optional[str] = None            # Q49: the alternative count of wave_a ("1"), debug only


def prepare(df: pd.DataFrame) -> pd.DataFrame:
    """Daily OHLC -> timestamp (IST, normalised), bar_end (15:30), no duplicates, in order (Trade `daily_swings.prepare`)."""
    d = df.copy()
    d["timestamp"] = pd.to_datetime(d["timestamp"])
    if getattr(d["timestamp"].dt, "tz", None) is not None:
        d["timestamp"] = d["timestamp"].dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
    d["timestamp"] = d["timestamp"].dt.normalize()
    d = d.drop_duplicates("timestamp", keep="last").sort_values("timestamp").reset_index(drop=True)
    d["bar_end"] = d["timestamp"] + pd.Timedelta(hours=15, minutes=30)
    return d[["timestamp", "bar_end", "open", "high", "low", "close"]]


def prepare_daily(df: pd.DataFrame, today: Any = None) -> pd.DataFrame:
    """The gate's Daily frame (Trade `level_reaction.prepare_daily`): today's (incomplete) Daily bar removed - the trend is
    what was known at yesterday's close."""
    d = prepare(df)
    if today is not None:
        d = d[d["timestamp"] < pd.Timestamp(today)].reset_index(drop=True)
    return d


def _arr(d: pd.DataFrame) -> Dict[str, np.ndarray]:
    return {k: d[k].to_numpy(float) for k in ("open", "high", "low", "close")}


def known_at_of(d: pd.DataFrame) -> List[pd.Timestamp]:
    if "bar_end" in d.columns:
        return [pd.Timestamp(x) for x in d["bar_end"]]
    return [pd.Timestamp(x).normalize() + pd.Timedelta(hours=15, minutes=30) for x in d["timestamp"]]


def sigma_d(d: pd.DataFrame, n: int) -> np.ndarray:
    """sigma_D[i] = the median Daily range of the last n candles (i included); NaN while fewer (warm-up)."""
    rng = (d["high"] - d["low"]).to_numpy(float)
    out = np.full(len(d), np.nan)
    for i in range(len(d)):
        if i + 1 >= n:
            out[i] = float(np.median(rng[i + 1 - n:i + 1]))
    return out


def pivots_pivot(d: pd.DataFrame, n: int, kat: Sequence[pd.Timestamp]) -> List[DPivot]:
    """Raw pivots in confirm order - ties: strict on the left, >= on the right."""
    A = _arr(d)
    H, L = A["high"], A["low"]
    out = []
    for i in range(n, len(d) - n):
        if H[i] > H[i - n:i].max() and H[i] >= H[i + 1:i + n + 1].max():
            out.append(DPivot("H", float(H[i]), i, pd.Timestamp(d["timestamp"].iloc[i]), i + n, kat[i + n]))
        if L[i] < L[i - n:i].min() and L[i] <= L[i + 1:i + n + 1].min():
            out.append(DPivot("L", float(L[i]), i, pd.Timestamp(d["timestamp"].iloc[i]), i + n, kat[i + n]))
    return sorted(out, key=lambda p: (p.confirm_bar, p.bar))


def pivots_dc(d: pd.DataFrame, k: float, sig: np.ndarray, kat: Sequence[pd.Timestamp]) -> List[DPivot]:
    """Directional change: a move of theta = k x sigma_D against the extreme (raw high / low) confirms it (at that close)."""
    A = _arr(d)
    H, L = A["high"], A["low"]
    out: List[DPivot] = []
    mode = 0
    hi_i: Optional[int] = None
    lo_i: Optional[int] = None
    for i in range(len(d)):
        if not np.isfinite(sig[i]):
            continue
        th = k * sig[i]
        if hi_i is None or lo_i is None:
            hi_i = lo_i = i
            continue
        if mode >= 0 and H[i] > H[hi_i]:
            hi_i = i
        if mode <= 0 and L[i] < L[lo_i]:
            lo_i = i
        if mode >= 0 and hi_i < i and H[hi_i] - L[i] >= th:
            out.append(DPivot("H", float(H[hi_i]), hi_i, pd.Timestamp(d["timestamp"].iloc[hi_i]), i, kat[i]))
            mode, lo_i = -1, i
        elif mode <= 0 and lo_i < i and H[i] - L[lo_i] >= th:
            out.append(DPivot("L", float(L[lo_i]), lo_i, pd.Timestamp(d["timestamp"].iloc[lo_i]), i, kat[i]))
            mode, hi_i = 1, i
    return out


def _add(seq: List[DPivot], p: DPivot) -> None:
    """Alternating: the same kind twice keeps the more extreme."""
    if seq and seq[-1].kind == p.kind:
        better = p.price > seq[-1].price if p.kind == "H" else p.price < seq[-1].price
        if better:
            seq[-1] = p
        return
    seq.append(p)


def _last2(seq: Sequence[DPivot], kind: str) -> Tuple[Optional[DPivot], Optional[DPivot]]:
    xs = [p for p in seq if p.kind == kind]
    return (xs[-2], xs[-1]) if len(xs) >= 2 else (None, xs[-1] if xs else None)


def fold(d: pd.DataFrame, s: Optional[Mapping[str, Any]] = None) -> List[DState]:
    """One DState per Daily candle (index = candle). Only `daily_trend_mode` = impulse (Q15 FINAL) is ported; Trade's
    "minor" mode is a what-if report."""
    s = S3.load(s)
    if s["daily_trend_mode"] == "impulse":
        return fold_impulse(d, s)
    raise ValueError(f"daily_trend_mode {s['daily_trend_mode']!r} - only 'impulse' is ported (Trade's 'minor' is a what-if)")


def _raw_pivots(d: pd.DataFrame, s: Mapping[str, Any], kat: Sequence[pd.Timestamp], sig: np.ndarray) -> List[DPivot]:
    if s["daily_swing_method"] == "dc":
        return pivots_dc(d, float(s["daily_dc_k"]), sig, kat)
    if s["daily_swing_method"] != "pivot":
        raise ValueError(f"daily_swing_method {s['daily_swing_method']!r} - one of pivot / dc")
    return pivots_pivot(d, int(s["daily_pivot_n"]), kat)


def complete_impulse_start(cands: Sequence[DPivot], seq: Sequence[DPivot], low: Sequence[DPivot], end: DPivot, w_lo: int, w_hi: int,
                           upto: int) -> Optional[DPivot]:
    """Q49 (a): the first candidate P from which the move to `end` reads as a complete 5-wave impulse (trend-degree pivots
    first, then one degree lower, confirmed by `upto`) whose wave 5 starts at a bar within [w_lo, w_hi]. None if none."""
    for q in cands:
        for src in (seq, low):
            pr, br = DL.leg_points(src, q.bar, q.price, end.bar, end.price, upto)
            if DL.read_counter(pr)["family"] != "impulse":
                continue
            segs = DL.motive_segs(pr)
            if segs is not None and w_lo <= br[len(pr) - len(segs[4])] <= w_hi:
                return q
    return None


def fold_impulse(d: pd.DataFrame, s: Optional[Mapping[str, Any]] = None) -> List[DState]:  # noqa: C901 - one ported state machine
    """Q15 degree-aware Dow: the trend = the direction of the last IMPULSE leg; protected = that impulse's ORIGIN."""
    s = S3.load(s)
    d = d.reset_index(drop=True)
    kat = known_at_of(d)
    sig = sigma_d(d, int(s["daily_sigma_sessions"]))
    raw = _raw_pivots(d, s, kat, sig)
    A = _arr(d)
    H, L, C = A["high"], A["low"], A["close"]
    by_conf: Dict[int, List[DPivot]] = {}
    for p in raw:
        by_conf.setdefault(p.confirm_bar, []).append(p)
    # one degree lower (N - 1): inner pullbacks (Q22) and inner structure (Q32); N = 1 / dc -> the same pivots
    nlow = int(s["daily_pivot_n"]) - 1
    low_all = pivots_pivot(d, nlow, kat) if (s["daily_swing_method"] == "pivot" and nlow >= 1) else list(raw)
    low: List[DPivot] = []                                                 # confirmed by bar i (grows in the loop)
    low_by: Dict[int, List[DPivot]] = {}
    for p in low_all:
        low_by.setdefault(p.confirm_bar, []).append(p)
    eq = float(s["range_eq_sigma_d"])
    msig = float(s["maturity_sigma_d"])
    cfrac = float(s["corr_degree_frac"])
    rule = s["corr_degree_rule"]
    if rule not in ("internal_pullback", "ratio"):
        raise ValueError(f"corr_degree_rule {rule!r} - one of internal_pullback / ratio")
    gate, range_after = bool(s["daily_start_nature_gate"]), bool(s["range_after_trend"])
    pgate = bool(s["daily_parent_gate"])
    A2: Dict[str, np.ndarray] = DL.arrays(d)
    mcache: Dict[Any, Any] = {}

    def piv(kind: str, bar: int) -> DPivot:
        price = float(H[bar] if kind == "H" else L[bar])
        return DPivot(kind, price, int(bar), pd.Timestamp(d["timestamp"].iloc[bar]), int(bar), kat[bar])

    def extreme(dirn: int, a: int, b: int) -> int:                         # the impulse-direction extreme in [a, b]
        return a + int(np.argmin(L[a:b + 1]) if dirn < 0 else np.argmax(H[a:b + 1]))

    seq: List[DPivot] = []
    out: List[DState] = []
    trend, dirn, band, brk = "NEUTRAL", 0, None, -1
    origin: Any = None
    imp_end: Any = None
    w1_start: Any = None
    phase: Optional[str] = None
    k_imp, ref_corr = 0, 0.0
    broken_at: Any = None
    leg_start: Any = None
    prev_int = 0.0                                                         # Q22: the previous impulse leg's largest inner pullback
    porg: Any = None
    pcorr = False
    tstart_org: Any = None                                                 # A1 / ABC_FIX: parent = the trend impulse's start
    t_first: Any = None
    t_first_par: Any = None                                                # Q44: the first impulse leg's extreme after the parent
    cm_cache: Dict[Any, str] = {}
    sharp: Any = None
    evid: tuple = ()
    cm0: Any = None                                                        # the running counter-move's start (= the impulse extreme)
    czone: Any = None
    leg_cm: Any = None
    wave_a: Any = None                                                     # Q49: an A1 correction's 5-wave first leg (wave A)

    def counter_kind(i: int) -> Tuple[str, int]:
        """ABC_FIX rule 1: the WHOLE counter-move from the impulse extreme to today's extreme, read once."""
        st0 = cm0 or imp_end
        seg = H[st0.bar:i + 1] if dirn < 0 else L[st0.bar:i + 1]
        tb = st0.bar + int(np.argmax(seg) if dirn < 0 else np.argmin(seg))
        if tb <= st0.bar:
            return "single", tb
        key = (st0.bar, tb, len(low), leg_start, round(prev_int, 9))
        if key not in cm_cache:
            tpx = float(H[tb] if dirn < 0 else L[tb])
            k = DL.classify(A2, seq, st0.bar, st0.price, tb, tpx, i, cache=mcache, inner_seq=low, elliott=True)["structure"]
            if k == "impulse":
                own = int_pull(-dirn, st0.bar, tb, i)
                ref = max(int_pull(dirn, leg_start, st0.bar, i), prev_int)
                if own < ref:                                              # a lower-degree 5 waves (a zigzag's a): no reversal
                    k = "wave_a"
                elif not DL.unfolded_123(DL.leg_prices(seq, st0.bar, st0.price, tb, tpx, i, inner_seq=low)):
                    k = "wave_1a"                                          # Q49: a same-degree 5-wave first leg is wave A by default
            cm_cache[key] = k
        return cm_cache[key], tb

    imp_cache: Dict[Any, bool] = {}
    q49_org = s["q49_impulse_origin"]
    if q49_org not in ("complete_impulse", "leg"):
        raise ValueError(f"q49_impulse_origin {q49_org!r} - one of complete_impulse / leg")

    def imp_origin(i: int) -> Any:
        """Q49 (a): the origin of the impulse the counter-move corrects - the running leg's origin, or, when the running leg
        is wave 5 of a complete 5-wave impulse, that impulse's start."""
        if q49_org == "leg":
            return origin
        ok = "H" if dirn < 0 else "L"
        lo_bar = tstart_org.bar if tstart_org is not None else origin.bar
        pool = [p for p in seq if p.kind == ok and lo_bar <= p.bar < origin.bar and p.confirm_bar <= i]
        if tstart_org is not None and tstart_org.bar < origin.bar and all(p.bar != tstart_org.bar for p in pool):
            pool.append(tstart_org)
        w_lo = leg_cm if (leg_cm is not None and leg_cm <= origin.bar) else origin.bar
        cands = []
        for q in sorted(pool, key=lambda p: -p.bar):
            seg = H[q.bar + 1:imp_end.bar + 1] if dirn < 0 else L[q.bar + 1:imp_end.bar + 1]
            if len(seg) and ((seg.max() > q.price) if dirn < 0 else (seg.min() < q.price)):
                continue
            cands.append(q)
        tail = (len(seq), seq[-1].bar if seq else -1, seq[-1].price if seq else None, len(low))
        for q in cands:
            key = (q.bar, imp_end.bar, origin.bar, w_lo) + tail
            if key not in imp_cache:
                imp_cache[key] = complete_impulse_start([q], seq, low, imp_end, w_lo, origin.bar, i) is not None
            if imp_cache[key]:
                return q
        return origin

    prov: List[Dict[str, Any]] = []                                        # provisional completions (until continuation confirms)
    PROV_KEYS = ("origin", "k_imp", "leg_start", "pcorr", "porg", "tstart_org", "prev_int", "ref_corr", "cm0", "imp_end", "sharp",
                 "leg_cm", "evid", "czone", "t_first", "t_first_par", "wave_a")

    def corr_end(i: int) -> Any:
        """Has the continuation confirmed, and where did the correction end? The counter pivots from the provisional origin
        to today's extreme from which the move to today reads as motive - the most extreme of them; None if not yet."""
        okind = "H" if dirn < 0 else "L"
        b0 = prov[0]["cm0"].bar if prov else origin.bar
        cands = [origin] + [q for q in low if q.kind == okind and b0 < q.bar < imp_end.bar and q.confirm_bar <= i and q.bar != origin.bar]
        best: Any = None
        wait: Any = None
        for q in cands:
            if best is not None and (q.price - best.price) * -dirn <= 0:
                continue
            r = DL.classify(A2, seq, q.bar, q.price, imp_end.bar, imp_end.price, i, cache=mcache, inner_seq=low, elliott=True)
            k = r["structure"]
            if k == "impulse" or (k == "single" and q is origin):
                best = q
            elif r.get("form") == "zigzag" and (wait is None or (q.price - wait.price) * -dirn > 0):
                wait = q                                                   # 5-3-5 may also be an impulse's 1-2-3: undecided
        if best is not None and wait is not None and (wait.price - best.price) * -dirn > 0:
            return None
        return best

    def push_prov() -> None:
        """A correction ended - but whether it was a, b or the real end shows only after the continuation (ABC_FIX 1, Q45).
        Keep the state before it."""
        snap = {k: v for k, v in zip(PROV_KEYS, (origin, k_imp, leg_start, pcorr, porg, tstart_org, prev_int, ref_corr,
                                                    cm0 or imp_end, imp_end, sharp, leg_cm, evid, czone, t_first, t_first_par, wave_a))}
        prov.append(snap)

    def sharp_zone(dn: int, a: int, b: int, i: int) -> Optional[tuple]:
        """ABC_FIX rule 2 (Q55 = B): the sharp-fall point = the FIRST lower-degree swing low (DOWN; UP mirrored: swing high)
        broken by a run of wide-range impulse-direction candles; the zone = that candle's extreme + the base candles'
        bodies, +/- half a band (range_eq_sigma_d x sigma_D). Confirmed pivots and bars <= i only."""
        if b - a < 2:
            return None
        O = d["open"].to_numpy(float)
        R_ = H - L
        wide: Dict[int, bool] = {}

        def wd(k: int) -> bool:                                            # a wide-range, impulse-direction candle?
            if k not in wide:
                ok_ = a + 1 <= k - 1 and k <= i
                wide[k] = bool(ok_ and R_[k] > R_[a + 1:k].mean() and (C[k] - O[k]) * dn > 0)
            return wide[k]

        def in_run(k: int) -> bool:
            return wd(k) and (wd(k - 1) or (k + 1 <= i and wd(k + 1)))

        kind = "L" if dn < 0 else "H"
        best: Any = None
        for p in low:
            if p.kind != kind or p.confirm_bar > i or p.bar >= b:
                continue
            brk_ = (C[p.bar + 1:i + 1] - p.price) * dn > 0
            if not brk_.any():
                continue
            t = p.bar + 1 + int(np.argmax(brk_))
            if t <= a or not in_run(t):
                continue
            key = (t, p.price * dn)
            if best is None or key < best[0]:
                best = (key, p)
        if best is None:
            return None
        q = best[1]
        half = eq * sig[i] if np.isfinite(sig[i]) else 0.0
        k0, k1 = max(q.bar - 1, 0), min(q.bar + 1, len(d) - 1)
        if dn < 0:                                                         # supply: the swing low up to the base bodies
            lo, hi = float(L[q.bar]), float(max(max(O[k], C[k]) for k in range(k0, k1 + 1)))
        else:                                                              # demand: the base bodies up to the swing high
            lo, hi = float(min(min(O[k], C[k]) for k in range(k0, k1 + 1))), float(H[q.bar])
        return (lo - half, hi + half, int(q.bar))

    def int_pull(dn: int, a: int, b: int, i: int) -> float:
        """Q22: the largest inner pullback (price) of the impulse (direction dn) in (a, b) - lower-degree counter pivots
        confirmed by i, each measured from the impulse extreme before it (from a). None -> 0."""
        ok, res = ("H" if dn < 0 else "L"), 0.0
        for q in low:
            if q.kind == ok and a < q.bar < b and q.confirm_bar <= i:
                j = extreme(dn, a, q.bar)
                res = max(res, (q.price - float(L[j] if dn < 0 else H[j])) * -dn)
        return res

    def degree_ref(p_bar: int, i: int) -> float:
        """Q22 reference: the running leg's largest inner pullback, or the previous impulse leg's - whichever is larger."""
        return max(int_pull(dirn, leg_start, extreme(dirn, leg_start, p_bar), i), prev_int)

    def pre_ext(p: DPivot) -> float:
        """The impulse-direction extreme before correction swing p (from leg_start)."""
        j = extreme(dirn, leg_start, p.bar)
        return float(L[j] if dirn < 0 else H[j])

    def corrections(i: int) -> List[DPivot]:
        """Same-degree counter swings in the running leg (confirmed by i): size = p - the extreme before p > the reference."""
        ok = "H" if dirn < 0 else "L"
        cand = [p for p in seq if p.kind == ok and p.bar > leg_start and p.confirm_bar <= i]
        if rule == "ratio":
            return [p for p in cand if (p.price - pre_ext(p)) * -dirn >= cfrac * ref_corr]
        res = []
        for p in cand:
            ref = degree_ref(p.bar, i)
            if ref > 0 and (p.price - pre_ext(p)) * -dirn > ref:
                res.append(p)
        return res

    def beyond_after(p: DPivot, i: int) -> bool:
        """Did a Daily close after p (<= i) pass the extreme before p in the impulse direction?"""
        seg = C[p.bar + 1:i + 1]
        return bool(len(seg)) and bool(((seg - pre_ext(p)) * dirn > 0).any())

    def start(nd: int, org: DPivot, w1s: Any, w1l: float, ref: float, i: int, why_: str, w1e_bar: Optional[int] = None) -> str:
        nonlocal trend, dirn, band, origin, imp_end, w1_start, phase, k_imp, ref_corr, broken_at, leg_start, prev_int, porg, pcorr, \
            tstart_org, sharp, evid, cm0, prov, leg_cm, czone, t_first, t_first_par, wave_a
        trend, dirn, band = ("UP" if nd > 0 else "DOWN"), nd, None
        porg, pcorr, sharp, evid, cm0, prov, leg_cm, czone, t_first, t_first_par = None, False, None, (), None, [], None, None, None, None
        wave_a = None
        tstart_org = w1s if w1s is not None else org
        origin, w1_start, ref_corr, k_imp, phase, broken_at = org, w1s, max(ref, 1e-9), 2, "impulse", None
        leg_start = org.bar
        prev_int = int_pull(nd, w1s.bar, w1e_bar, i) if (w1s is not None and w1e_bar is not None) else 0.0
        imp_end = piv("H" if nd > 0 else "L", extreme(nd, org.bar, i))
        return why_

    def nature_ok(nd: int, w1s: DPivot, w1e: DPivot, org: DPivot, i: int, tag: str = "no trend (Q28)") -> Tuple[bool, str]:
        """Q28: a trend starts only with an impulse-natured leg - (1) = w1s -> w1e or (3) = org -> today's extreme. With no C
        baseline (warm-up) only the structure counts: corrective -> no."""
        if not gate:
            return True, ""
        e = extreme(nd, org.bar, i)
        c1 = DL.classify(A2, seq, w1s.bar, w1s.price, w1e.bar, w1e.price, i, cache=mcache, inner_seq=low)
        c3 = DL.classify(A2, seq, org.bar, org.price, e, float(H[e] if nd > 0 else L[e]), i, cache=mcache, inner_seq=low)
        kinds = (c1["kind"], c3["kind"])
        if "impulse" in kinds:
            return True, f" ((1) {DL.tag(c1)}, (3) {DL.tag(c3)})"
        if c1["C"] is None and c3["C"] is None and "corrective" not in kinds:
            return True, " (no C; structure not corrective)"
        return False, (f"{'UP' if nd > 0 else 'DOWN'} structure but no impulse-natured leg ((1) {DL.tag(c1)}, (3) {DL.tag(c3)}) "
                       f"-> {tag}")

    def flip(i: int) -> str:
        """After origin_broken: an opposite impulse? (Also on the break day.) Q36: the opposite structure needs an
        impulse-natured leg. Returns why ("" = nothing; a refusal gives its reason)."""
        why, nd, ek = "", -dirn, ("L" if dirn < 0 else "H")
        tag = "no flip - origin broken, no opposite impulse (Q36; grade B at most)"
        blocked = ""
        okind = "H" if nd < 0 else "L"
        cands = [p for p in seq if p.kind == okind and p.bar > imp_end.bar and p.confirm_bar <= i
                 and (p.price - imp_end.price) * nd > 0]
        flipped = False
        for h in sorted(cands, key=lambda p: p.price * nd):
            later = H[h.bar:i + 1].max() if nd < 0 else L[h.bar:i + 1].min()
            if (later - h.price) * nd < 0:
                continue
            mids = [p for p in seq if p.kind == ("L" if nd < 0 else "H") and imp_end.bar < p.bar < h.bar]
            if not mids:
                continue
            lm = min(mids, key=lambda p: p.price * -nd)
            if (C[i] - lm.price) * nd > 0:
                ok_n, why_n = nature_ok(nd, imp_end, lm, h, i, tag)
                if not ok_n:
                    blocked = blocked or why_n
                    continue
                why = start(nd, h, imp_end, abs(imp_end.price - lm.price), abs(h.price - lm.price), i,
                            f"origin broken + opposite structure -> {'UP' if nd > 0 else 'DOWN'} (origin {h.price:,.2f})", lm.bar)
                flipped = True
                break
        post = [] if flipped else [p for p in seq if p.kind == ek and p.bar > broken_at and p.confirm_bar <= i
                                   and (p.price - imp_end.price) * -dirn > 0]
        if post:
            hl = post[-1]
            ext_bar = broken_at + int(np.argmax(H[broken_at:hl.bar + 1]) if dirn < 0 else np.argmin(L[broken_at:hl.bar + 1]))
            ext = float(H[ext_bar] if dirn < 0 else L[ext_bar])
            if (C[i] - ext) * -dirn > 0:
                nd = -dirn
                ok_n, why_n = nature_ok(nd, imp_end, piv("H" if dirn < 0 else "L", ext_bar), hl, i, tag)
                if ok_n:
                    why = start(nd, hl, imp_end, abs(ext - imp_end.price), abs(ext - hl.price), i,
                                f"opposite impulse after the origin break -> {'UP' if nd > 0 else 'DOWN'} (origin {hl.price:,.2f})", ext_bar)
                else:
                    blocked = blocked or why_n
        return why or blocked

    for i in range(len(d)):
        why: str = ""
        corr: List[DPivot] = []
        for p in by_conf.get(i, []):
            _add(seq, p)
        low.extend(low_by.get(i, []))
        tol = eq * sig[i] if np.isfinite(sig[i]) else 0.0
        ek = "L" if dirn < 0 else "H"
        if dirn != 0 and phase == "origin_broken":
            why = flip(i) or why
        if dirn != 0 and phase in ("impulse", "correction", "origin_broken"):
            ek = "L" if dirn < 0 else "H"
            corr = corrections(i)
            done = [] if pcorr else [p for p in corr if beyond_after(p, i) and (C[i] - p.price) * dirn > 0]
            if done:                                                       # a close beyond the extreme after a correction: new impulse
                top = max(done, key=lambda p: p.price * -dirn)
                push_prov()
                ref_corr = abs(top.price - pre_ext(top))
                prev_int = int_pull(dirn, leg_start, extreme(dirn, leg_start, top.bar), i)
                leg_cm, czone = (cm0 or imp_end).bar, None
                origin, phase, k_imp, leg_start, pcorr, cm0 = top, "impulse", k_imp + 1, top.bar, False, None
                why = f"new impulse (L{2 * k_imp - 1}) -> protected advances to the correction extreme {origin.price:,.2f}"
                corr = corrections(i)
            if pcorr and imp_end.bar < i:                                  # did the A1 correction end?
                seg = H[imp_end.bar + 1:i + 1] if dirn < 0 else L[imp_end.bar + 1:i + 1]
                tb = imp_end.bar + 1 + int(np.argmax(seg) if dirn < 0 else np.argmin(seg))
                hl = [p for p in seq if p.kind == ek and imp_end.bar < p.bar < tb and p.confirm_bar <= i]
                lvl = hl[-1].price if hl else imp_end.price
                fire = tb < i and (C[i] - lvl) * dirn > 0                  # Q45: a close beyond the counter-move's last HL
                tpx = float(H[tb] if dirn < 0 else L[tb])
                at_sharp = sharp is not None and sharp[0] <= tpx <= sharp[1]
                if not fire and at_sharp and tb < i and (C[i] - (sharp[0] if dirn < 0 else sharp[1])) * dirn > 0:
                    fire, lvl = True, (sharp[0] if dirn < 0 else sharp[1])  # ABC_FIX rule 3: ended in the sharp zone
                if fire:
                    top = piv("H" if dirn < 0 else "L", tb)
                    push_prov()
                    ref_corr = max(abs(top.price - imp_end.price), 1e-9)
                    t0 = tstart_org.bar if tstart_org is not None else leg_start
                    prev_int = int_pull(dirn, t0, imp_end.bar, i)
                    leg_cm = (cm0 or imp_end).bar
                    origin, phase, k_imp, leg_start, pcorr, cm0, wave_a = top, "impulse", k_imp + 1, tb, False, None, None
                    czone = sharp if at_sharp else None
                    if at_sharp:
                        evid = evid + (f"correction ended at sharp-{'fall supply' if dirn < 0 else 'rise demand'} "
                                       f"{sharp[0]:,.0f}-{sharp[1]:,.0f}",)
                    tstart_org, sharp = top, None
                    why = (f"correction (A1) ended - a close beyond the counter-move's last {'HL' if dirn < 0 else 'LH'} {lvl:,.2f} "
                           f"-> new impulse (L{2 * k_imp - 1}), protected = the correction extreme {top.price:,.2f}")
                    corr = corrections(i)
            imp_end = piv(ek, extreme(dirn, leg_start, i))
            if phase == "impulse" and not pcorr and tstart_org is not None and imp_end.bar == i:
                sharp = sharp_zone(dirn, tstart_org.bar, imp_end.bar, i) or sharp
            if prov and imp_end.bar == i and (imp_end.price - prov[0]["cm0"].price) * dirn > 0:
                end = corr_end(i)
                if end is not None:
                    leg_cm = prov[0]["cm0"].bar
                    prov.clear()
                    if end.bar != origin.bar:                              # Rule 4: origin = the correction's end
                        why = (f"continuation confirmed -> origin = the correction's end {end.price:,.2f} "
                               f"(not the extreme {origin.price:,.2f})")
                        if tstart_org is not None and tstart_org.bar == origin.bar:
                            tstart_org = end
                        origin, leg_start = end, end.bar
            while prov and phase != "origin_broken" and (C[i] - origin.price) * dirn < 0:
                snap = prov.pop()                                          # the provisional origin broke: not a completion
                keep = snap["porg"] if snap["pcorr"] else snap["origin"]
                if keep is None or (C[i] - keep.price) * dirn < 0:
                    prov.clear()
                    break
                origin, k_imp, leg_start, pcorr, porg, tstart_org, prev_int, ref_corr, cm0, imp_end, sharp, leg_cm, evid, czone, \
                    t_first, t_first_par, wave_a = (snap[k] for k in PROV_KEYS)
                why = (f"back to origin {origin.price:,.2f} - the earlier correction completion broke before the continuation "
                       "-> the correction continues (ABC_FIX 1)")
                corr = corrections(i)
            if phase != "origin_broken":
                phase = "correction" if (corr or pcorr) else "impulse"
                if phase == "correction" and cm0 is None:
                    cm0 = imp_end
                elif phase == "impulse" and imp_end.bar == i:
                    cm0 = None
                if pgate and (pcorr or (C[i] - origin.price) * dirn < 0):  # ABC_FIX rule 1: read the whole counter-move
                    cmk, _tb = counter_kind(i)
                    par = tstart_org if (tstart_org is not None and (tstart_org.price - origin.price) * dirn < 0) else None
                    ao = imp_origin(i) if cmk == "wave_1a" else origin
                    if cmk == "impulse" or (cmk == "wave_1a" and (C[i] - ao.price) * dirn < 0):   # Q49 (b) / (a): wave 1
                        if cmk == "wave_1a" and ao is not origin:
                            origin = ao
                        pcorr, wave_a = False, None
                    elif par is not None and (C[i] - par.price) * dirn >= 0:
                        if cmk in ("wave_a", "wave_1a"):                   # Q49: a 5-wave first leg -> wave A by default
                            new_a = piv("H" if dirn < 0 else "L", _tb)
                            if wave_a is not None and wave_a.bar == new_a.bar:
                                pass
                            elif cmk == "wave_1a":
                                why = (f"counter-move in 5 waves, but no wave-1 condition ((b) no same-degree 5-wave structure; "
                                       f"(a) the corrected impulse's origin {ao.price:,.2f} held on the close) -> wave A "
                                       f"{new_a.price:,.2f} (alt: 1), a correction - the trend does not reverse (Q49)")
                            else:
                                why = (f"counter-move in 5 waves of a lower degree (Q22) -> wave A {new_a.price:,.2f} (alt: 1); "
                                       f"wave 1 only with a close beyond the parent {par.price:,.2f} - a correction (Q49)")
                            wave_a = new_a
                        elif not pcorr:
                            why = (f"impulse origin {origin.price:,.2f} broken by a Daily close, but the counter-move is {cmk} "
                                   f"(not an impulse) and the impulse start {par.price:,.2f} held -> a correction, the trend "
                                   "does not reverse (ABC_FIX 1)")
                        porg, pcorr, phase = par, True, "correction"
                        if t_first is None or t_first_par is None or t_first_par.bar != par.bar:
                            t_first, t_first_par = cm0 or imp_end, par     # Q44: the first impulse leg after the parent ended here
                    elif pcorr:                                            # Q49 (a): the parent origin broke too -> no wave A
                        origin, pcorr, wave_a = par or porg, False, None
                if not pcorr and (C[i] - origin.price) * dirn < 0:
                    phase, broken_at = "origin_broken", i
                    why = f"protected origin {origin.price:,.2f} broken by a Daily close - waiting for an opposite impulse (trend not over)"
                    w = flip(i)
                    why = w if phase == "impulse" else (why + (f" · {w}" if w else ""))
        if range_after and dirn != 0 and phase == "origin_broken":         # Q27
            hs = [p for p in seq if p.kind == "H" and p.bar > imp_end.bar and p.confirm_bar <= i]
            ls = [p for p in seq if p.kind == "L" and p.bar > imp_end.bar and p.confirm_bar <= i]
            if len(hs) >= 2 and len(ls) >= 2 and abs(hs[-1].price - hs[-2].price) <= tol and abs(ls[-1].price - ls[-2].price) <= tol:
                bd = (min(ls[-1].price, ls[-2].price), max(hs[-1].price, hs[-2].price))
                if bd[0] <= C[i] <= bd[1]:
                    trend, dirn, phase, brk, band = "RANGE", 0, None, broken_at, bd
                    why = "origin broken + two about-equal highs and lows -> RANGE (edges; Q27)"
        if trend in ("NEUTRAL", "RANGE"):
            Hs = [p for p in seq if p.kind == "H" and p.confirm_bar <= i]
            Ls = [p for p in seq if p.kind == "L" and p.confirm_bar <= i]
            if len(Hs) >= 2:                                               # DOWN: a lower high + a close below the low between
                h1, h2 = Hs[-2], Hs[-1]
                mid = [p for p in Ls if h1.bar < p.bar < h2.bar]
                if mid and h2.bar > brk and h2.price < h1.price - tol and C[i] < mid[-1].price:
                    lm = mid[-1]
                    ok_n, why_n = nature_ok(-1, h1, lm, h2, i)
                    if ok_n:
                        why = start(-1, h2, h1, h1.price - lm.price, h2.price - lm.price, i,
                                    "lower high + a close below the previous low -> DOWN impulse" + why_n, lm.bar)
                    else:
                        why = why_n
            if trend in ("NEUTRAL", "RANGE") and len(Ls) >= 2:             # UP mirrored
                l1, l2 = Ls[-2], Ls[-1]
                mid = [p for p in Hs if l1.bar < p.bar < l2.bar]
                if mid and l2.bar > brk and l2.price > l1.price + tol and C[i] > mid[-1].price:
                    hm = mid[-1]
                    ok_n, why_n = nature_ok(1, l1, hm, l2, i)
                    if ok_n:
                        why = start(1, l2, l1, hm.price - l1.price, hm.price - l2.price, i,
                                    "higher low + a close above the previous high -> UP impulse" + why_n, hm.bar)
                    else:
                        why = why_n
            if trend == "NEUTRAL":
                p_h1, p_h2 = _last2(seq, "H")
                p_l1, p_l2 = _last2(seq, "L")
                if p_h1 and p_l1 and p_h2 and p_l2 and p_h2.bar > brk and p_l2.bar > brk and abs(p_h2.price - p_h1.price) <= tol \
                        and abs(p_l2.price - p_l1.price) <= tol:
                    trend, band, why = "RANGE", (min(p_l1.price, p_l2.price), max(p_h1.price, p_h2.price)), "two about-equal highs and lows"
            elif trend == "RANGE" and band and (C[i] > band[1] or C[i] < band[0]):
                trend, why, brk, band = "NEUTRAL", "close outside the range band", i, None
        # wave / correction leg / maturity
        wave = corr_label = counter = None
        targets: tuple = ()
        cbars: tuple = ()
        mature, risk, passed = False, False, False
        if dirn != 0 and phase:
            wave = f"L{2 * k_imp - 1}" if phase == "impulse" else f"L{2 * k_imp}"
            if phase in ("correction", "origin_broken"):
                prev_px, prev_k, n_after = imp_end.price, imp_end.kind, 0
                lref = cfrac * ref_corr if rule == "ratio" else max(int_pull(dirn, leg_start, imp_end.bar, i), prev_int)
                for p in seq:
                    if p.bar > imp_end.bar and p.confirm_bar <= i and p.kind != prev_k and (
                            abs(p.price - prev_px) >= lref if rule == "ratio" else (lref > 0 and abs(p.price - prev_px) > lref)):
                        prev_px, prev_k, n_after = p.price, p.kind, n_after + 1
                corr_label = "abcde"[min(n_after, 4)]
            near = msig * (sig[i] if np.isfinite(sig[i]) else 0.0)
            t_live = t_first is not None and phase in ("impulse", "correction") and (pcorr or imp_end.bar > t_first.bar)
            passed = t_live and ((porg if pcorr else origin).price - t_first.price) * dirn >= 0
            if t_live and not passed:
                tg = [t_first.price]                                       # Q44: the structural target
                mature = (not pcorr) and any((imp_end.price - x) * dirn >= -near for x in tg)
                targets = tuple(round(float(x), 2) for x in tg)
            cbars = tuple(int(p.bar) for p in corr) if phase in ("impulse", "correction") else ()
            if pcorr:
                counter = "correction"
            elif phase == "correction":
                counter = "candidate"
            elif phase == "impulse" and imp_end.bar < i:
                counter = "pullback"
            if phase in ("correction", "origin_broken") or counter == "pullback":
                prot_px = porg.price if pcorr else origin.price            # B1: the correction near the protected origin
                ext_c = H[i] if dirn < 0 else L[i]
                risk = (ext_c - prot_px) * dirn <= near and phase != "origin_broken"
        shown = trend if i + 1 > int(s["daily_min_sessions"]) else "UNKNOWN"
        tr = shown in ("UP", "DOWN")
        prot = (porg if pcorr else origin) if trend in ("UP", "DOWN") else None
        out.append(DState(i, pd.Timestamp(d["timestamp"].iloc[i]), kat[i], shown,
                          prot, band if trend == "RANGE" else None,
                          why if shown != "UNKNOWN" else "Daily data too short", list(seq),
                          phase if tr else None, wave if tr else None, corr_label if tr else None, imp_end if tr else None,
                          bool(mature and tr), targets if tr else (),
                          (porg if pcorr else tstart_org) if tr else None, counter if tr else None, leg_start if tr else None, cbars if tr else (),
                          bool(risk and tr), sharp if tr else None, evid if tr else (), czone if tr else None,
                          leg_cm if tr else None, bool(passed and tr),
                          wave_a if (tr and pcorr) else None, "1" if (tr and pcorr and wave_a) else None))
    return out


_KAT_CACHE: Dict[int, Tuple[List[DState], List[Any]]] = {}


def state_at(states: List[DState], ts: Any) -> DState:
    """The last Daily state known at ts (known_at <= ts); none -> UNKNOWN."""
    ts = pd.Timestamp(ts)
    hit = _KAT_CACHE.get(id(states))
    if hit is None or hit[0] is not states or len(hit[1]) != len(states):
        if len(_KAT_CACHE) > 16:
            _KAT_CACHE.clear()
        hit = _KAT_CACHE[id(states)] = (states, [x.known_at for x in states])
    kats = hit[1]
    j = bisect.bisect_right(kats, ts) - 1
    return states[j] if j >= 0 else DState(-1, None, None, "UNKNOWN", why="no Daily candle yet")
