"""Rule-based classical chart patterns on CONFIRMED swings.

Status vocabulary: candidate | confirmed | completed | failed.
A pattern is only 'confirmed' after a completed candle CLOSES beyond its breakout boundary.
Cup-and-handle is intentionally NOT implemented (no reliable, testable rule set yet) and is reported as such.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .structure import Swing

NOT_IMPLEMENTED = ["Cup and Handle"]


def _status(C, H, L, start: int, neck: float, bearish: bool, target: float | None, invalid: float) -> tuple[str, int | None]:
    n = len(C)
    for j in range(start + 1, n):
        if (C[j] > invalid) if bearish else (C[j] < invalid):
            return "failed", j
        if (C[j] < neck) if bearish else (C[j] > neck):
            if target is not None:
                seg = L[j:] if bearish else H[j:]
                if (seg.min() <= target) if bearish else (seg.max() >= target):
                    return "completed", j
            # failed after confirmation if price closes back beyond invalidation
            after = C[j + 1:]
            if len(after) and ((after > invalid).any() if bearish else (after < invalid).any()):
                return "failed", j
            return "confirmed", j
    return "candidate", None


def _pat(name, bias, status, conf_i, points, neckline=None, target=None, invalid=None, upper=None, lower=None, note=""):
    return {"pattern": name, "bias": bias, "status": status, "confirmed_i": conf_i,
            "points": [(s.i, s.price, s.label) for s in points], "neckline": neckline, "target": target,
            "invalidation": invalid, "upper": upper, "lower": lower, "note": note}


def _fit(points: list[Swing]):
    x = np.array([p.i for p in points], float)
    y = np.array([p.price for p in points], float)
    m, b = np.polyfit(x, y, 1)
    return m, b, float(np.abs(y - (m * x + b)).max())


def detect_patterns(df: pd.DataFrame, swings: list[Swing], atr_last: float, tol_atr: float = 0.6) -> list[dict]:
    if not np.isfinite(atr_last) or atr_last <= 0:
        return []
    C, H, L = df.Close.to_numpy(), df.High.to_numpy(), df.Low.to_numpy()
    n, A = len(df), atr_last
    tol = tol_atr * A
    hs = [s for s in swings if s.kind == "H"]
    ls = [s for s in swings if s.kind == "L"]
    out: list[dict] = []

    # ---- Double / triple tops & bottoms
    for cnt, nm in ((3, "Triple"), (2, "Double")):
        if len(hs) >= cnt:
            pk = hs[-cnt:]
            if max(p.price for p in pk) - min(p.price for p in pk) <= tol and pk[-1].i - pk[0].i >= 5 * (cnt - 1):
                troughs = [s for s in ls if pk[0].i < s.i < pk[-1].i]
                if troughs:
                    neck = min(t.price for t in troughs)
                    top = max(p.price for p in pk)
                    h = top - neck
                    if h >= 1.5 * A:
                        st, ci = _status(C, H, L, pk[-1].confirmed_at - 1, neck, True, neck - h, top)
                        out.append(_pat(f"{nm} Top", "bearish", st, ci, pk + troughs, neck, neck - h, top))
                        break
    for cnt, nm in ((3, "Triple"), (2, "Double")):
        if len(ls) >= cnt:
            tr = ls[-cnt:]
            if max(p.price for p in tr) - min(p.price for p in tr) <= tol and tr[-1].i - tr[0].i >= 5 * (cnt - 1):
                peaks = [s for s in hs if tr[0].i < s.i < tr[-1].i]
                if peaks:
                    neck = max(p.price for p in peaks)
                    bot = min(p.price for p in tr)
                    h = neck - bot
                    if h >= 1.5 * A:
                        st, ci = _status(C, H, L, tr[-1].confirmed_at - 1, neck, False, neck + h, bot)
                        out.append(_pat(f"{nm} Bottom", "bullish", st, ci, tr + peaks, neck, neck + h, bot))
                        break

    # ---- Head & shoulders
    if len(hs) >= 3:
        l, hd, r = hs[-3:]
        if hd.price > max(l.price, r.price) + A and abs(l.price - r.price) <= 2 * tol:
            t = [s for s in ls if l.i < s.i < r.i]
            if len(t) >= 2:
                neck = min(t[0].price, t[-1].price)
                h = hd.price - neck
                st, ci = _status(C, H, L, r.confirmed_at - 1, neck, True, neck - h, r.price)
                out.append(_pat("Head and Shoulders", "bearish", st, ci, [l, hd, r] + t, neck, neck - h, r.price,
                                note="invalidation = right shoulder high"))
    if len(ls) >= 3:
        l, hd, r = ls[-3:]
        if hd.price < min(l.price, r.price) - A and abs(l.price - r.price) <= 2 * tol:
            t = [s for s in hs if l.i < s.i < r.i]
            if len(t) >= 2:
                neck = max(t[0].price, t[-1].price)
                h = neck - hd.price
                st, ci = _status(C, H, L, r.confirmed_at - 1, neck, False, neck + h, r.price)
                out.append(_pat("Inverse Head and Shoulders", "bullish", st, ci, [l, hd, r] + t, neck, neck + h, r.price,
                                note="invalidation = right shoulder low"))

    # ---- Triangles / wedges / broadening / rectangle (last 3 highs and 3 lows)
    if len(hs) >= 3 and len(ls) >= 3:
        ph, pl = hs[-3:], ls[-3:]
        start = min(ph[0].i, pl[0].i)
        last_pt = max(s.confirmed_at for s in ph + pl)
        span = (n - 1) - start
        mh, bh, eh = _fit(ph)
        ml, bl, el = _fit(pl)
        if eh <= tol and el <= tol and span >= 10 and last_pt < n:
            dh, dl = mh * span, ml * span
            name, bias = None, "neutral"
            flat_h, flat_l = abs(dh) < A, abs(dl) < A
            if flat_h and flat_l: name = "Rectangle"
            elif flat_h and dl > A: name, bias = "Ascending Triangle", "bullish"
            elif dh < -A and flat_l: name, bias = "Descending Triangle", "bearish"
            elif dh < -A and dl > A: name = "Symmetrical Triangle"
            elif dh > A and dl > A and ml > mh: name, bias = "Rising Wedge", "bearish"
            elif dh < -A and dl < -A and mh > ml: name, bias = "Falling Wedge", "bullish"
            elif dh > A and dl < -A: name = "Broadening Formation"
            if name:
                up_at = lambda j: mh * j + bh  # noqa: E731
                lo_at = lambda j: ml * j + bl  # noqa: E731
                st, ci = "candidate", None
                for j in range(last_pt + 1, n):
                    if C[j] > up_at(j):
                        st, ci = "breakout_up", j
                        break
                    if C[j] < lo_at(j):
                        st, ci = "breakdown", j
                        break
                if ci is not None:
                    follow = C[ci + 1:ci + 3]
                    beyond = (follow > up_at(ci)) if st == "breakout_up" else (follow < lo_at(ci))
                    if len(follow) == 0:
                        st = st + " (unconfirmed: 1 close)"
                    elif beyond.all():
                        st = "confirmed " + st
                    else:
                        st = "failed " + st
                height = up_at(start) - lo_at(start)
                out.append(_pat(name, bias, st, ci, ph + pl,
                                upper=(start, float(up_at(start)), n - 1, float(up_at(n - 1))),
                                lower=(start, float(lo_at(start)), n - 1, float(lo_at(n - 1))),
                                note=f"formation height at start {height:.2f}; boundaries from linear fit of last 3 swings"))

    # ---- Flags & pennants: pole (>=3 ATR within <=10 bars) then 5-20 bar consolidation < 50% of pole
    for cons in range(5, 21):
        e = n - 1 - cons          # pole end index
        if e - 10 < 0:
            break
        seg = slice(e + 1, n)
        cons_hi, cons_lo = H[seg].max(), L[seg].min()
        for direction in ("up", "down"):
            s0 = int(np.argmin(L[e - 10:e + 1])) + e - 10 if direction == "up" else int(np.argmax(H[e - 10:e + 1])) + e - 10
            pole = (H[e] - L[s0]) if direction == "up" else (H[s0] - L[e])
            if pole < 3 * A or s0 >= e:
                continue
            if cons_hi - cons_lo > 0.5 * pole:
                continue
            if direction == "up" and (cons_lo < L[s0] + 0.5 * pole or H[e] < H[s0:e + 1].max()):
                continue
            if direction == "down" and (cons_hi > H[s0] - 0.5 * pole or L[e] > L[s0:e + 1].min()):
                continue
            x = np.arange(e + 1, n)
            mh, _ = np.polyfit(x, H[seg], 1)
            ml, _ = np.polyfit(x, L[seg], 1)
            converging = mh < 0 < ml
            name = ("Bull " if direction == "up" else "Bear ") + ("Pennant" if converging else "Flag")
            last = C[-1]
            if direction == "up":
                st = "confirmed breakout" if last > H[e + 1:n - 1].max(initial=-np.inf) and last > H[e] else "candidate"
                tgt = H[e] + pole if st != "candidate" else None
                inv = float(cons_lo)
            else:
                st = "confirmed breakdown" if last < L[e + 1:n - 1].min(initial=np.inf) and last < L[e] else "candidate"
                tgt = L[e] - pole if st != "candidate" else None
                inv = float(cons_hi)
            out.append({"pattern": name, "bias": "bullish" if direction == "up" else "bearish", "status": st,
                        "confirmed_i": n - 1 if "confirmed" in st else None, "points": [(s0, float(L[s0] if direction == "up" else H[s0]), "pole start"), (e, float(H[e] if direction == "up" else L[e]), "pole end")],
                        "neckline": float(H[e] if direction == "up" else L[e]), "target": float(tgt) if tgt else None,
                        "invalidation": inv, "upper": (e + 1, float(cons_hi), n - 1, float(cons_hi)),
                        "lower": (e + 1, float(cons_lo), n - 1, float(cons_lo)),
                        "note": f"pole {pole:.2f} ({pole / A:.1f} ATR), consolidation {cons} bars; target = pole length projected (only after breakout)"})
            return out  # one flag/pennant (most recent pole) is enough
    return out
