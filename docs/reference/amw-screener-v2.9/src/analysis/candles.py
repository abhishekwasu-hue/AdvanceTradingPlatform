"""Candlestick patterns on COMPLETED candles. Each hit is a candidate; confirmation needs the next bar."""
from __future__ import annotations

import numpy as np
import pandas as pd


def _ctx(close: np.ndarray, i: int, n: int = 5) -> str:
    """Prior context using closes BEFORE bar i only."""
    if i < n + 1:
        return "unknown"
    seg = close[i - n - 1:i]
    ch = seg[-1] - seg[0]
    rng = np.ptp(seg)
    if rng == 0:
        return "sideways"
    if ch > 0.5 * rng:
        return "uptrend"
    if ch < -0.5 * rng:
        return "downtrend"
    return "sideways"


# Tolerances (documented in docs/STRATEGY_RULES.md, "Candlestick definitions v2")
CANDLE_TOL = {"pin_wick": 2 / 3, "pin_body": 1 / 3, "strong_body": 0.7, "strong_atr": 1.0, "reject_wick": 0.5,
              "gap_atr": 0.5}


def detect_candles(df: pd.DataFrame, lookback: int = 10, tol: dict | None = None) -> list[dict]:
    T = {**CANDLE_TOL, **(tol or {})}
    O, H, L, C = (df[c].to_numpy() for c in ("Open", "High", "Low", "Close"))
    atr = df["atr14"].to_numpy() if "atr14" in df else np.full(len(df), np.nan)
    n, out = len(df), []

    def add(i, name, bias, confirm, invalid):
        status = "candidate"
        if i + 1 < n and bias != "neutral":
            ok = C[i + 1] > H[i] if bias == "bullish" else C[i + 1] < L[i]
            bad = C[i + 1] < L[i] if bias == "bullish" else C[i + 1] > H[i]
            status = "confirmed" if ok else "failed" if bad else "unconfirmed"
        out.append({"i": i, "pattern": name, "bias": bias, "context": _ctx(C, i), "confirm": confirm,
                    "invalid": invalid, "status": status})

    for i in range(max(2, n - lookback), n):
        rng = H[i] - L[i]
        if rng <= 0:
            continue
        body = abs(C[i] - O[i])
        up, lo = H[i] - max(O[i], C[i]), min(O[i], C[i]) - L[i]
        ctx = _ctx(C, i)
        if body <= 0.1 * rng:
            add(i, "Doji", "neutral", "direction of the next candle", "-")
        elif lo >= 2 * body and up <= max(0.3 * body, 0.1 * rng) and ctx == "downtrend":
            add(i, "Hammer", "bullish", f"next close > {H[i]:.2f}", f"close < {L[i]:.2f}")
        elif up >= 2 * body and lo <= max(0.3 * body, 0.1 * rng) and ctx == "uptrend":
            add(i, "Shooting Star", "bearish", f"next close < {L[i]:.2f}", f"close > {H[i]:.2f}")
        p = i - 1
        pb = abs(C[p] - O[p])
        if C[p] < O[p] and C[i] > O[i] and O[i] <= C[p] and C[i] >= O[p] and body > pb and ctx != "uptrend":
            add(i, "Bullish Engulfing", "bullish", f"next close > {H[i]:.2f}", f"close < {min(L[i], L[p]):.2f}")
        if C[p] > O[p] and C[i] < O[i] and O[i] >= C[p] and C[i] <= O[p] and body > pb and ctx != "downtrend":
            add(i, "Bearish Engulfing", "bearish", f"next close < {L[i]:.2f}", f"close > {max(H[i], H[p]):.2f}")
        if H[i] < H[p] and L[i] > L[p]:
            add(i, "Inside Bar", "neutral", f"breakout > {H[p]:.2f} or < {L[p]:.2f}", "-")
        if H[i] > H[p] and L[i] < L[p]:
            add(i, "Outside Bar", "bullish" if C[i] > O[i] else "bearish", "follow-through", "-")
        # ---- v2 patterns (explicit definitions; no predictive value assumed)
        if lo >= T["pin_wick"] * rng and body <= T["pin_body"] * rng and min(O[i], C[i]) >= L[i] + T["pin_wick"] * rng:
            add(i, "Pin Bar", "bullish", f"next close > {H[i]:.2f}", f"close < {L[i]:.2f}")
        elif up >= T["pin_wick"] * rng and body <= T["pin_body"] * rng and max(O[i], C[i]) <= H[i] - T["pin_wick"] * rng:
            add(i, "Pin Bar", "bearish", f"next close < {L[i]:.2f}", f"close > {H[i]:.2f}")
        if body >= T["strong_body"] * rng and np.isfinite(atr[i]) and rng >= T["strong_atr"] * atr[i]:
            add(i, "Strong Body", "bullish" if C[i] > O[i] else "bearish", "follow-through", "close back inside the body")
        if lo >= T["reject_wick"] * rng and C[i] >= L[i] + 0.5 * rng and not (lo >= T["pin_wick"] * rng and body <= T["pin_body"] * rng):
            add(i, "Rejection", "bullish", f"next close > {H[i]:.2f}", f"close < {L[i]:.2f}")
        elif up >= T["reject_wick"] * rng and C[i] <= H[i] - 0.5 * rng and not (up >= T["pin_wick"] * rng and body <= T["pin_body"] * rng):
            add(i, "Rejection", "bearish", f"next close < {L[i]:.2f}", f"close > {H[i]:.2f}")
        if np.isfinite(atr[i]) and i >= 1:
            g = O[i] - C[i - 1]
            if g >= T["gap_atr"] * atr[i] and C[i] < C[i - 1]:
                add(i, "Gap Reversal", "bearish", f"next close < {L[i]:.2f}", f"close > {H[i]:.2f}")
            elif -g >= T["gap_atr"] * atr[i] and C[i] > C[i - 1]:
                add(i, "Gap Reversal", "bullish", f"next close > {H[i]:.2f}", f"close < {L[i]:.2f}")
        q = i - 2
        if q >= 0 and np.isfinite(atr[q]):
            b0, b1 = abs(C[q] - O[q]), abs(C[p] - O[p])
            if C[q] < O[q] and b0 > 0.8 * atr[q] and b1 < 0.4 * b0 and C[i] > O[i] and C[i] > (O[q] + C[q]) / 2:
                add(i, "Morning Star", "bullish", f"close > {H[i]:.2f}", f"close < {min(L[q], L[p], L[i]):.2f}")
            if C[q] > O[q] and b0 > 0.8 * atr[q] and b1 < 0.4 * b0 and C[i] < O[i] and C[i] < (O[q] + C[q]) / 2:
                add(i, "Evening Star", "bearish", f"close < {L[i]:.2f}", f"close > {max(H[q], H[p], H[i]):.2f}")
    return out
