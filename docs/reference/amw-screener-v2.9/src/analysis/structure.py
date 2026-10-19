"""Market structure: confirmed swings, HH/HL/LH/LL, BOS/CHoCH, S/R, zones, liquidity, compression.

Non-repainting rules
--------------------
* A swing at bar i is only CONFIRMED at bar i+k (k bars on the right). `confirmed_at` stores that index.
* BOS/CHoCH at bar j only uses swings with confirmed_at <= j and requires a CLOSE beyond the swing.
  A wick beyond the level with the close back inside is a sweep, not a break.
* BOS  = close breaks the latest swing in the direction of the current structure trend (or first break).
* CHoCH = close breaks the latest swing AGAINST the current structure trend (= MSS here).
"""
from __future__ import annotations

from dataclasses import dataclass, asdict

import numpy as np
import pandas as pd


@dataclass
class Swing:
    i: int
    kind: str          # "H" or "L"
    price: float
    confirmed_at: int
    label: str = ""


@dataclass
class StructEvent:
    i: int
    type: str          # BOS / CHoCH
    direction: str     # bullish / bearish
    level: float
    swing_i: int


def find_swings(df: pd.DataFrame, k: int = 3) -> list[Swing]:
    H, L = df.High.to_numpy(), df.Low.to_numpy()
    n, out = len(df), []
    for i in range(k, n - k):
        # strictly above the left side, >= the right side (first bar of an equal-high plateau wins)
        if H[i] > H[i - k:i].max() and H[i] >= H[i + 1:i + k + 1].max():
            out.append(Swing(i, "H", float(H[i]), i + k))
        if L[i] < L[i - k:i].min() and L[i] <= L[i + 1:i + k + 1].min():
            out.append(Swing(i, "L", float(L[i]), i + k))
    lh = ll = None
    for s in out:
        if s.kind == "H":
            s.label = "SH" if lh is None else ("HH" if s.price > lh else "LH")
            lh = s.price
        else:
            s.label = "SL" if ll is None else ("HL" if s.price > ll else "LL")
            ll = s.price
    return out


def structure_events(df: pd.DataFrame, swings: list[Swing]) -> tuple[list[StructEvent], str | None]:
    C = df.Close.to_numpy()
    events: list[StructEvent] = []
    trend: str | None = None
    used: set[int] = set()
    by_conf = sorted(swings, key=lambda s: s.confirmed_at)
    last_h: Swing | None = None
    last_l: Swing | None = None
    p = 0
    for j in range(len(df)):
        while p < len(by_conf) and by_conf[p].confirmed_at <= j:
            s = by_conf[p]
            if s.kind == "H": last_h = s
            else: last_l = s
            p += 1
        if last_h and last_h.i not in used and C[j] > last_h.price:
            used.add(last_h.i)
            events.append(StructEvent(j, "BOS" if trend in (None, "up") else "CHoCH", "bullish", last_h.price, last_h.i))
            trend = "up"
        if last_l and last_l.i not in used and C[j] < last_l.price:
            used.add(last_l.i)
            events.append(StructEvent(j, "BOS" if trend in (None, "down") else "CHoCH", "bearish", last_l.price, last_l.i))
            trend = "down"
    return events, trend


def confirmed_swings(swings: list[Swing], upto: int) -> list[Swing]:
    return [s for s in swings if s.confirmed_at <= upto]


def structure_bias(swings: list[Swing], trend: str | None, n_last: int = 4) -> float:
    """-1..+1 combining recent swing labels and last structural break."""
    lab = [s.label for s in swings[-n_last:]]
    if not lab and trend is None:
        return 0.0
    pos = sum(l in ("HH", "HL") for l in lab)
    neg = sum(l in ("LH", "LL") for l in lab)
    s = (pos - neg) / max(1, len(lab))
    t = 1.0 if trend == "up" else -1.0 if trend == "down" else 0.0
    return float(np.clip(0.5 * s + 0.5 * t, -1, 1))


def gaps(df: pd.DataFrame, min_atr: float = 0.5, lookback: int = 60) -> list[dict]:
    """Opening gaps of at least `min_atr` x ATR(14) (prior bar's ATR) within the last `lookback` bars.
    `filled` = a LATER bar traded back to the prior close (evaluated only on bars inside the frame)."""
    if "atr14" not in df or len(df) < 2:
        return []
    O, H, L, C = (df[c].to_numpy() for c in ("Open", "High", "Low", "Close"))
    A = df["atr14"].to_numpy()
    n, out = len(df), []
    for i in range(max(1, n - lookback), n):
        a = A[i - 1]
        if not np.isfinite(a) or a <= 0:
            continue
        g = O[i] - C[i - 1]
        if abs(g) < min_atr * a:
            continue
        up = g > 0
        later = range(i, n)
        filled_at = next((j for j in later if (L[j] <= C[i - 1] if up else H[j] >= C[i - 1])), None)
        out.append({"i": i, "direction": "up" if up else "down", "size": float(g), "size_atr": float(abs(g) / a),
                    "prior_close": float(C[i - 1]), "open": float(O[i]), "filled": filled_at is not None,
                    "filled_i": filled_at})
    return out


def sr_levels(swings: list[Swing], atr_last: float, last_close: float, tol_atr: float = 0.5,
              df: pd.DataFrame | None = None) -> list[dict]:
    """Swing clusters (>= 2 touches). Strength = touches. With `df`: origin role (majority of swing kinds),
    role reversal (origin differs from the current side of price), closes crossing the level after it formed, and
    freshness = bars since the last touch."""
    if not swings or not np.isfinite(atr_last):
        return []
    tol = tol_atr * atr_last
    pts = sorted(swings, key=lambda s: s.price)
    clusters: list[list[Swing]] = []
    for s in pts:
        if clusters and s.price - clusters[-1][-1].price <= tol:
            clusters[-1].append(s)
        else:
            clusters.append([s])
    out = []
    for cl in clusters:
        if len(cl) < 2:
            continue
        lvl = float(np.mean([s.price for s in cl]))
        d = {"level": lvl, "touches": len(cl), "low": min(s.price for s in cl), "high": max(s.price for s in cl),
             "role": "support" if lvl < last_close else "resistance", "first_i": min(s.i for s in cl),
             "last_i": max(s.i for s in cl)}
        if df is not None and len(df):
            origin = "resistance" if sum(s.kind == "H" for s in cl) >= sum(s.kind == "L" for s in cl) else "support"
            c = df.Close.to_numpy()[d["first_i"]:]
            side = np.sign(c - lvl)
            side = side[side != 0]
            d.update(origin=origin, role_reversal=origin != d["role"],
                     crosses=int((np.diff(side) != 0).sum()) if len(side) > 1 else 0,
                     bars_since_touch=int(len(df) - 1 - d["last_i"]))
        out.append(d)
    return out


def zones(df: pd.DataFrame, atr_s: pd.Series, departure_atr: float = 1.3) -> list[dict]:
    """RBR/DBR demand and RBD/DBD supply zones from 1-3 base candles + impulsive departure."""
    O, H, L, C = (df[c].to_numpy() for c in ("Open", "High", "Low", "Close"))
    A = atr_s.to_numpy()
    n, res = len(df), []
    for b0 in range(1, n - 1):
        for blen in (1, 2, 3):
            b1 = b0 + blen - 1
            lo = b1 + 1
            if lo >= n or not np.isfinite(A[lo]):
                continue
            base = range(b0, b1 + 1)
            if not all(abs(C[j] - O[j]) <= 0.5 * (H[j] - L[j] + 1e-12) for j in base):
                continue
            rng = H[lo] - L[lo]
            if not (rng >= departure_atr * A[lo] and abs(C[lo] - O[lo]) >= 0.6 * rng):
                continue
            leg_in_up = C[b0 - 1] > O[b0 - 1]
            if C[lo] > O[lo] and C[lo] > max(H[j] for j in base):
                kind, name = "demand", "RBR" if leg_in_up else "DBR"
                top, bot = max(max(O[j], C[j]) for j in base), min(L[j] for j in base)
            elif C[lo] < O[lo] and C[lo] < min(L[j] for j in base):
                kind, name = "supply", "RBD" if leg_in_up else "DBD"
                top, bot = max(H[j] for j in base), min(min(O[j], C[j]) for j in base)
            else:
                continue
            tests, status, inside = 0, "fresh", False
            for j in range(lo + 1, n):
                touch = L[j] <= top if kind == "demand" else H[j] >= bot
                broke = C[j] < bot if kind == "demand" else C[j] > top
                if broke:
                    status = "invalidated"
                    break
                if touch and not inside:
                    tests += 1
                inside = touch
            if tests and status != "invalidated":
                status = "tested"
            res.append({"kind": kind, "name": name, "start_i": b0, "end_i": b1, "departure_i": lo,
                        "top": float(top), "bottom": float(bot), "tests": tests, "status": status,
                        "departure_atr": float(rng / A[lo])})
            break
    keep: list[dict] = []
    for z in sorted([z for z in res if z["status"] != "invalidated"], key=lambda z: -z["departure_i"]):
        if any(k["kind"] == z["kind"] and not (z["top"] < k["bottom"] or z["bottom"] > k["top"]) for k in keep):
            continue
        keep.append(z)
    return keep


def liquidity(df: pd.DataFrame, swings: list[Swing], atr_last: float, eq_tol_atr: float = 0.15) -> dict:
    """Liquidity pools are HYPOTHESES from untaken swing points; OHLC cannot reveal real stop orders."""
    H, L, C = df.High.to_numpy(), df.Low.to_numpy(), df.Close.to_numpy()
    n = len(df)
    tol = eq_tol_atr * atr_last if np.isfinite(atr_last) else 0
    hs = [s for s in swings if s.kind == "H"]
    ls = [s for s in swings if s.kind == "L"]
    eq_h = [(a.price, b.price, a.i, b.i) for x, a in enumerate(hs) for b in hs[x + 1:] if abs(a.price - b.price) <= tol and b.i - a.i >= 3]
    eq_l = [(a.price, b.price, a.i, b.i) for x, a in enumerate(ls) for b in ls[x + 1:] if abs(a.price - b.price) <= tol and b.i - a.i >= 3]
    bsl = [s for s in hs if s.confirmed_at < n and (s.i + 1 >= n or H[s.i + 1:].max() <= s.price)]
    ssl = [s for s in ls if s.confirmed_at < n and (s.i + 1 >= n or L[s.i + 1:].min() >= s.price)]
    sweeps = []
    for s in swings:
        for j in range(s.confirmed_at + 1, n):          # only after the swing is known
            if s.kind == "H" and H[j] > s.price:
                if C[j] < s.price:
                    sweeps.append({"i": j, "side": "BSL", "bias": "bearish", "level": s.price, "swing_i": s.i})
                break
            if s.kind == "L" and L[j] < s.price:
                if C[j] > s.price:
                    sweeps.append({"i": j, "side": "SSL", "bias": "bullish", "level": s.price, "swing_i": s.i})
                break
    sweeps.sort(key=lambda d: d["i"])
    return {"equal_highs": eq_h, "equal_lows": eq_l, "bsl": [asdict(s) for s in bsl],
            "ssl": [asdict(s) for s in ssl], "sweeps": sweeps}


def compression_state(df: pd.DataFrame, ratio: float = 0.75) -> str | None:
    a14, a50 = df["atr14"].iloc[-1], df["atr50"].iloc[-1]
    if not (np.isfinite(a14) and np.isfinite(a50)) or a50 == 0:
        return None
    r = a14 / a50
    return "compression" if r < ratio else "expansion" if r > 1 / ratio else "normal"
