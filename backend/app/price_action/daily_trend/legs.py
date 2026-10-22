"""N1 L2: is a Daily leg impulse or corrective? Ported from Trade `decision3/daily_legs.py` (the legs2 / patterns2 adapter on
Daily pivots) with the parts of `legs2/measure.py` it uses, at the pinned revision. Logic unchanged; codes in English.

* Nature C (legs2 layer 2): the leg's Daily candles - efficiency ratio, (1 - overlap), body, candles in the leg's
  direction - as a percentile against up to `baseline_legs` earlier confirmed legs of the same Daily series; >= c_hi ->
  IMP, <= c_lo -> COR. No volume on Daily. Fewer than `baseline_min` baseline legs -> NA (warm-up).
* Structure (patterns2 rules): the confirmed pivots inside the leg (extreme to extreme), reduced from the smallest
  inner pair to 5 / 3 waves; the impulse rules -> "impulse", else zigzag / flat -> "corrective"; no inner pivots ->
  "single". With `elliott=True` the full recursive reading (`read_counter`) decides.
* kind: "impulse" = structure impulse or C IMP; "corrective" = C COR or structure corrective; else "unclear".
"""
import threading
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np

from app.price_action.daily_trend import rules as PR
from app.price_action.daily_trend.settings import LEGS_DEFAULTS, PATTERN_DEFAULTS

IMP, NEU, COR = "IMP", "NEU", "COR"
METRICS = ("er", "overlap", "body", "dirc")
MOTIVE = ("impulse", "diagonal")
CORRECTIVE = ("zigzag", "flat", "triangle", "combination", "3-wave")
COMPLETE = ("zigzag", "flat", "triangle", "combination")      # combination = W and Y themselves complete corrections

# read_counter's memo (a pure function of the prices; Trade's own cache). Per thread (review of #213): the fold runs in
# worker threads (X1 `asyncio.to_thread`), and a shared dict with the recursion guard below is not safe across threads.
_LOCAL = threading.local()


def _read_cache() -> Dict[Tuple[Any, ...], Dict[str, Any]]:
    cache = getattr(_LOCAL, "read", None)
    if cache is None:
        cache = _LOCAL.read = {}
    return cache


# --- legs2 measures -----------------------------------------------------------------------------------------------------

def nature_class(x: Optional[float], hi: float, lo: float) -> str:
    """legs2 `measure.classify`."""
    if x is None or not np.isfinite(x):
        return NEU
    return IMP if x >= hi else (COR if x <= lo else NEU)


def candle_metrics(A: Mapping[str, np.ndarray], i0: int, i1: int, dirn: int) -> Tuple[Dict[str, Optional[float]], Dict[int, float]]:
    """legs2: over the leg bars (i0, i1] - ER, overlap, body, direction candles; a session's first-bar gap left out."""
    o, h, low, c, first = A["o"], A["h"], A["l"], A["c"], A["first"]
    idx = np.arange(i0 + 1, i1 + 1)
    step = c[idx] - c[idx - 1]
    gap = np.where(first[idx], o[idx] - c[idx - 1], 0.0)
    adj = step - gap
    path = float(np.abs(adj).sum())
    er = abs(float(adj.sum())) / path if path > 0 else 0.0
    er = min(max(er, 0.0), 1.0)
    ovs = []
    for a, b in zip(idx[:-1], idx[1:]):
        if first[b]:
            continue
        rng = h[b] - low[b]
        if rng <= 0:
            continue
        ovs.append(max(0.0, min(h[a], h[b]) - max(low[a], low[b])) / rng)
    rngs = h[idx] - low[idx]
    ok = rngs > 0
    body = float(np.mean(np.abs(c[idx] - o[idx])[ok] / rngs[ok])) if ok.any() else None
    dirc = float(np.mean((c[idx] - o[idx]) * dirn > 0)) if len(idx) else None
    return {"er": er, "overlap": float(np.mean(ovs)) if ovs else None, "body": body, "dirc": dirc}, \
        {int(b): float(g) for b, g in zip(idx, gap) if g != 0.0}


def pct_rank(x: float, base: Sequence[float]) -> float:
    """Average rank on ties: (below + half of equal) / N."""
    b = np.asarray(base, float)
    return float(((b < x).sum() + 0.5 * (b == x).sum()) / len(b))


# --- daily_legs ---------------------------------------------------------------------------------------------------------

def arrays(d: Any) -> Dict[str, np.ndarray]:
    """Daily OHLC -> the candle-metrics arrays; `first` False everywhere (a Daily gap is part of the leg)."""
    return {"o": d["open"].to_numpy(float), "h": d["high"].to_numpy(float), "l": d["low"].to_numpy(float),
            "c": d["close"].to_numpy(float), "first": np.zeros(len(d), bool)}


def metrics(A: Mapping[str, np.ndarray], i0: int, i1: int, dirn: int) -> Optional[Dict[str, Optional[float]]]:
    if i1 <= i0:
        return None
    return candle_metrics(A, int(i0), int(i1), int(dirn))[0]


def c_class(raw: Optional[Mapping[str, Any]], base_raws: Sequence[Optional[Mapping[str, Any]]],
            s: Optional[Mapping[str, Any]] = None) -> Tuple[Optional[float], str]:
    """legs2 C: the mean percentile (overlap reversed) -> (C, class). Too small a baseline -> (None, "NA")."""
    s = s or LEGS_DEFAULTS
    base = [b for b in base_raws if b is not None][-int(s["baseline_legs"]):]
    if raw is None or len(base) < int(s["baseline_min"]):
        return None, "NA"
    pct = []
    for m in METRICS:
        x = raw.get(m)
        vals = [b[m] for b in base if b.get(m) is not None]
        if x is None or not vals:
            continue
        p = pct_rank(x, vals)
        pct.append(1.0 - p if m == "overlap" else p)
    if not pct:
        return None, "NA"
    C = float(np.mean(pct))
    return C, nature_class(C, s["c_hi"], s["c_lo"])


def reduce_waves(x: Sequence[float]) -> Dict[int, List[float]]:
    """Alternating extreme prices x[0..n] -> {n: x_n} (5 / 3 / 1 waves): removes the smallest INNER pair each time, only
    when the remaining points stay extremes."""
    xs = list(map(float, x))
    out = {len(xs) - 1: list(xs)}
    d = 1 if xs[-1] > xs[0] else -1
    while len(xs) - 1 > 1:
        best: Optional[Tuple[float, int]] = None
        for i in range(1, len(xs) - 2):
            a, b = xs[i], xs[i + 1]
            sgn = d if i % 2 == 1 else -d
            ok = (xs[i + 2] - a) * sgn >= 0 and (b - xs[i - 1]) * sgn >= 0
            size = abs(b - a)
            if ok and (best is None or size < best[0]):
                best = (size, i)
        if best is None:
            break
        i = best[1]
        xs = xs[:i] + xs[i + 2:]
        out.setdefault(len(xs) - 1, list(xs))
    return out


def reduce_counted(x: Sequence[float]) -> Dict[int, Tuple[List[float], List[int]]]:
    """As `reduce_waves`, with how many original legs each remaining wave holds: {n: (prices, counts)}."""
    xs = list(map(float, x))
    cnt = [1] * (len(xs) - 1)
    out = {len(xs) - 1: (list(xs), list(cnt))}
    d = 1 if xs[-1] > xs[0] else -1
    while len(xs) - 1 > 1:
        best: Optional[Tuple[float, int]] = None
        for i in range(1, len(xs) - 2):
            a, b = xs[i], xs[i + 1]
            sgn = d if i % 2 == 1 else -d
            ok = (xs[i + 2] - a) * sgn >= 0 and (b - xs[i - 1]) * sgn >= 0
            if ok and (best is None or abs(b - a) < best[0]):
                best = (abs(b - a), i)
        if best is None:
            break
        i = best[1]
        merged = cnt[i - 1] + cnt[i] + cnt[i + 1]
        xs = xs[:i] + xs[i + 2:]
        cnt = cnt[:i - 1] + [merged] + cnt[i + 2:]
        out.setdefault(len(xs) - 1, (list(xs), list(cnt)))
    return out


def _segments(prices: Sequence[float], counts: Sequence[int]) -> List[List[float]]:
    out, k = [], 0
    for c in counts:
        out.append(list(prices[k:k + c + 1]))
        k += c
    return out


def _is_motive(seg: Sequence[float], s: Optional[Mapping[str, Any]] = None) -> bool:
    return len(seg) - 1 == 1 or read_counter(seg, s)["family"] in MOTIVE


def _is_corrective(seg: Sequence[float], s: Optional[Mapping[str, Any]] = None) -> bool:
    return len(seg) - 1 == 1 or read_counter(seg, s)["family"] in CORRECTIVE


def _settings_key(s: Optional[Mapping[str, Any]]) -> Tuple[Any, ...]:
    """The memo key part of the type ratios: empty for the defaults (the Daily trend's key stays the prices alone)."""
    if s is None or all(s.get(k) == v for k, v in PATTERN_DEFAULTS.items()):
        return ()
    return ("ratios",) + tuple((k, float(s[k])) for k in sorted(PATTERN_DEFAULTS))


def read_counter(prices: Sequence[float], s: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """One whole move (extreme to extreme, one degree lower pivots) read by the Elliott rules, every wave's subdivision
    read recursively: impulse 5-3-5-3-5, diagonal, triangle, combination, zigzag 5-3-5, flat 3-3-5. A one-leg wave fits
    both. The zigzag / flat type reads the B/A ratios of `s` (the `PATTERN_DEFAULTS` keys; default: those values, as the
    Daily trend reads them - WAVE-6 makes them settings of the wave reading). Returns {"family", "n"}: impulse / diagonal
    / triangle / combination / zigzag / flat / 3-wave / single."""
    key = tuple(round(float(v), 6) for v in prices) + _settings_key(s)
    cache = _read_cache()
    if key in cache:
        return cache[key]
    if len(cache) > 20000:
        cache.clear()
    n = len(prices) - 1
    if n <= 1:
        return {"family": "single", "n": max(n, 0)}
    cache[key] = {"family": "3-wave", "n": n}                             # recursion guard
    red = reduce_counted(prices)
    s2 = PATTERN_DEFAULTS if s is None else s
    fam: Optional[str] = None
    p5 = red.get(5)
    if p5 is not None and n >= 5:
        x5, c5 = p5
        seg5 = _segments(prices, c5)
        corr_all = all(_is_corrective(g, s2) for g in seg5)
        if PR.impulse(x5, done=True) and all(_is_motive(seg5[k], s2) for k in (0, 2, 4)) and all(_is_corrective(seg5[k], s2) for k in (1, 3)):
            fam = "impulse"
        elif (corr_all or (all(_is_motive(seg5[k], s2) for k in (0, 2, 4)) and all(_is_corrective(seg5[k], s2) for k in (1, 3)))) and \
                (PR.wedge(x5, done=True, kind="contracting") or PR.wedge(x5, done=True, kind="expanding")):
            fam = "diagonal"
        elif corr_all and any(PR.triangle(x5, done=True, kind=k)[0] for k in ("contracting", "expanding")) and not PR.impulse(x5, True):
            fam = "triangle"
        elif all(len(seg5[k]) > 2 and read_counter(seg5[k], s2)["family"] in COMPLETE for k in (0, 2, 4)) and \
                all(_is_corrective(seg5[k], s2) for k in (1, 3)):
            fam = "combination"
    p9 = red.get(9)
    if fam is None and p9 is not None and n >= 9:                          # an extended wave: 9 waves
        x9, c9 = p9
        seg9 = _segments(prices, c9)
        for ext in (0, 2, 4):
            grp = [[k] for k in range(9)]
            j = ext
            waves = grp[:j] + [list(range(j, j + 5))] + grp[j + 5:]
            if len(waves) != 5:
                continue
            x5e = [x9[0]] + [x9[w[-1] + 1] for w in waves]
            segs = [sum((seg9[k][1:] if t else seg9[k] for t, k in enumerate(w)), []) for w in waves]
            if PR.impulse(x5e, done=True) and all(_is_motive(segs[k], s2) for k in (0, 2, 4)) and \
                    all(_is_corrective(segs[k], s2) for k in (1, 3)):
                fam = "impulse"
                break
    p3 = red.get(3)
    if fam is None and p3 is not None:
        x3, c3 = p3
        a_seg, _, c_seg = _segments(prices, c3)
        if len(a_seg) > 2 and len(c_seg) > 2 and read_counter(a_seg, s2)["family"] in COMPLETE and \
                read_counter(c_seg, s2)["family"] in COMPLETE:
            fam = "combination"
        elif PR.zigzag(x3, True, s2)[0] and _is_motive(a_seg, s2) and _is_motive(c_seg, s2):
            fam = "zigzag"
        elif PR.flat(x3, True, s2)[0] and _is_corrective(a_seg, s2) and _is_motive(c_seg, s2):
            fam = "flat"
        if fam is None:                                                    # B does not end at the most extreme point
            ia = c3[0]
            for ib in range(ia + 1, n, 2):
                xb = [prices[0], prices[ia], prices[ib], prices[n]]
                b_seg, c2 = list(prices[ia:ib + 1]), list(prices[ib:])
                if (prices[ib] - prices[0]) * (prices[n] - prices[0]) < 0 or len(c2) < 2:
                    continue
                if not (_is_corrective(b_seg, s2) and _is_motive(c2, s2)):
                    continue
                if PR.zigzag(xb, True, s2)[0] and _is_motive(a_seg, s2):
                    fam = "zigzag"
                    break
                if PR.flat(xb, True, s2)[0] and _is_corrective(a_seg, s2):
                    fam = "flat"
                    break
    out = {"family": fam or "3-wave", "n": n}
    cache[key] = out
    return out


def structure(prices: Sequence[float], elliott: bool = False) -> Dict[str, Any]:
    """{"structure": impulse / corrective / single, "n", "form"}. Default: the impulse rule on the 5-wave reduction, else
    zigzag / flat / 3-wave. elliott=True: `read_counter`'s recursive reading."""
    n = len(prices) - 1
    if n <= 1:
        return {"structure": "single", "n": max(n, 0), "form": None}
    if elliott:
        fam = read_counter(prices)["family"]
        if fam in ("impulse", "diagonal"):
            return {"structure": "impulse", "n": n, "form": fam}
        return {"structure": "corrective", "n": n, "form": fam}
    red = reduce_waves(prices)
    x5 = red.get(5)
    if x5 is not None and PR.impulse(x5, done=True):
        return {"structure": "impulse", "n": n, "form": "impulse"}
    x3 = red.get(3)
    form = None
    if x3 is not None:
        s = PATTERN_DEFAULTS
        form = "zigzag" if PR.zigzag(x3, True, s)[0] else ("flat" if PR.flat(x3, True, s)[0] else None)
    return {"structure": "corrective", "n": n, "form": form or "3-wave"}


def _m(A: Mapping[str, np.ndarray], i0: int, i1: int, dirn: int, cache: Optional[Dict[Any, Any]]) -> Optional[Dict[str, Optional[float]]]:
    if cache is None:
        return metrics(A, i0, i1, dirn)
    k = (int(i0), int(i1), int(dirn))
    if k not in cache:
        cache[k] = metrics(A, i0, i1, dirn)
    return cache[k]


def leg_points(seq: Sequence[Any], a_bar: int, a_price: float, b_bar: int, b_price: float, upto_confirm: int,
               inner_seq: Optional[Sequence[Any]] = None) -> Tuple[List[float], List[int]]:
    """The leg a -> b as alternating extreme points (prices, bars): inner pivots (inner_seq = one degree lower, else seq)
    confirmed by `upto_confirm` only (no look-ahead). Same kind twice -> the more extreme; a last inner pivot in the leg's
    direction merges with b."""
    dirn = 1 if b_price > a_price else -1
    inner_src = seq if inner_seq is None else inner_seq
    inner = sorted((p for p in inner_src if p.confirm_bar <= upto_confirm and a_bar < p.bar < b_bar), key=lambda p: p.bar)
    prices, kinds, bars = [a_price], ["L" if dirn > 0 else "H"], [int(a_bar)]
    for p in inner:
        if p.kind != kinds[-1]:
            prices.append(p.price)
            kinds.append(p.kind)
            bars.append(int(p.bar))
        elif len(prices) > 1 and (p.price - prices[-1]) * (1 if p.kind == "H" else -1) > 0:
            prices[-1], bars[-1] = p.price, int(p.bar)
    if len(prices) > 1 and kinds[-1] == ("H" if dirn > 0 else "L"):
        prices, bars = prices[:-1], bars[:-1]
    prices.append(b_price)
    bars.append(int(b_bar))
    return prices, bars


def leg_prices(seq: Sequence[Any], a_bar: int, a_price: float, b_bar: int, b_price: float, upto_confirm: int,
               inner_seq: Optional[Sequence[Any]] = None) -> List[float]:
    return leg_points(seq, a_bar, a_price, b_bar, b_price, upto_confirm, inner_seq)[0]


def motive_segs(prices: Sequence[float]) -> Optional[List[List[float]]]:
    """`read_counter`'s motive reading (5-wave impulse / wedge, or a 9-wave extension) -> the five waves' points, or None."""
    n = len(prices) - 1
    red = reduce_counted(prices)
    p5 = red.get(5)
    if p5 is not None and n >= 5:
        x5, c5 = p5
        seg5 = _segments(prices, c5)
        sub = all(_is_motive(seg5[k]) for k in (0, 2, 4)) and all(_is_corrective(seg5[k]) for k in (1, 3))
        if PR.impulse(x5, done=True) and sub:
            return seg5
        if (sub or all(_is_corrective(g) for g in seg5)) and \
                (PR.wedge(x5, done=True, kind="contracting") or PR.wedge(x5, done=True, kind="expanding")):
            return seg5
    p9 = red.get(9)
    if p9 is not None and n >= 9:
        x9, c9 = p9
        seg9 = _segments(prices, c9)
        for j in (0, 2, 4):
            waves = [[k] for k in range(j)] + [list(range(j, j + 5))] + [[k] for k in range(j + 5, 9)]
            if len(waves) != 5:
                continue
            x5e = [x9[0]] + [x9[w[-1] + 1] for w in waves]
            segs = [sum((seg9[k][1:] if t else seg9[k] for t, k in enumerate(w)), []) for w in waves]
            if PR.impulse(x5e, done=True) and all(_is_motive(segs[k]) for k in (0, 2, 4)) and \
                    all(_is_corrective(segs[k]) for k in (1, 3)):
                return segs
    return None


def unfolded_123(prices: Sequence[float]) -> bool:
    """Q49 (b): a 5-wave counter-move completes its own same-degree structure - its "1" in 5 waves, a 3-wave "2", and a
    "3" beyond "1"'s end that unfolds in 5 waves itself. Structure only. No -> False (the 5-wave leg is wave A)."""
    if read_counter(prices)["family"] not in MOTIVE:
        return False
    segs = motive_segs(prices)
    if segs is None:
        return False
    w1, w2, w3 = segs[0], segs[1], segs[2]
    d = 1 if prices[-1] > prices[0] else -1
    return (len(w1) - 1 >= 5 and read_counter(w1)["family"] in MOTIVE
            and len(w2) - 1 >= 3 and read_counter(w2)["family"] in CORRECTIVE
            and len(w3) - 1 >= 5 and read_counter(w3)["family"] in MOTIVE
            and (w3[-1] - w1[-1]) * d > 0)


def classify(A: Mapping[str, np.ndarray], seq: Sequence[Any], a_bar: int, a_price: float, b_bar: int, b_price: float,
             upto_confirm: int, s: Optional[Mapping[str, Any]] = None, cache: Optional[Dict[Any, Any]] = None,
             inner_seq: Optional[Sequence[Any]] = None, elliott: bool = False) -> Dict[str, Any]:
    """One leg (a -> b; b may be the running extreme). seq = confirmed pivots of the same degree (the C baseline from the
    legs completed by a); inner_seq = one degree lower (the inner structure). A leg of <= short_leg_bars bars -> C
    neutral. Returns kind / structure / form / n / C / c_cls."""
    s = s or LEGS_DEFAULTS
    dirn = 1 if b_price > a_price else -1
    conf = [p for p in seq if p.confirm_bar <= upto_confirm]
    prices = leg_prices(seq, a_bar, a_price, b_bar, b_price, upto_confirm, inner_seq)
    st = structure(prices, elliott)
    short = int(s["short_leg_bars"])
    done = sorted((p for p in conf if p.bar <= a_bar), key=lambda p: p.bar)
    pairs = [(p, q) for p, q in zip(done, done[1:]) if p.kind != q.kind and q.bar - p.bar > short][-int(s["baseline_legs"]):]
    base = [_m(A, p.bar, q.bar, 1 if q.price > p.price else -1, cache) for p, q in pairs]
    C: Optional[float]
    if b_bar - a_bar <= short:
        C, cls = None, NEU
    else:
        C, cls = c_class(_m(A, a_bar, b_bar, dirn, cache), base, s)
    if st["structure"] == "impulse" or cls == IMP:
        kind = "impulse"
    elif cls == COR or st["structure"] == "corrective":
        kind = "corrective"
    else:
        kind = "unclear"
    return {"kind": kind, "structure": st["structure"], "form": st["form"], "n": st["n"], "C": C, "c_cls": cls}


def tag(c: Optional[Mapping[str, Any]]) -> str:
    """A short note, e.g. "impulse (5-wave, C IMP)"."""
    if not c:
        return "-"
    return f"{c['kind']} ({c['form'] or c['structure']}, C {c['c_cls']})"
