"""Deterministic high-resolution chart renderer (matplotlib, headless) for reports, PNG export and Telegram previews.

Every annotation is taken from computed analysis evidence (confirmed swings, BOS/CHoCH events, S/R clusters, zones) or
from the stored signal record (stop/target). Positions are candle indices of the rendered window, so a marker can only
appear on the candle where the event was computed. `render_chart` returns the PNG bytes AND an annotation manifest
(time, price, kind) so tests can verify coordinates against the data.
"""
from __future__ import annotations

import io

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402

UP, DOWN = "#26a69a", "#ef5350"


def render_chart(t, bars: int = 120, signal: dict | None = None, title: str = "", dpi: int = 160,
                 show: dict | None = None) -> tuple[bytes, list[dict]]:
    show = {"swings": True, "events": True, "sr": True, "zones": True, "ema": True, "levels": True, **(show or {})}
    df = t.df.iloc[-bars:]
    s0 = len(t.df) - len(df)
    n = len(df)
    O, H, L, C = (df[c].to_numpy() for c in ("Open", "High", "Low", "Close"))
    manifest: list[dict] = []
    fig, (ax, axv) = plt.subplots(2, 1, figsize=(12, 6.8), dpi=dpi, sharex=True, gridspec_kw={"height_ratios": [4, 1]})
    for i in range(n):
        col = UP if C[i] >= O[i] else DOWN
        ax.vlines(i, L[i], H[i], color=col, lw=0.8)
        ax.add_patch(Rectangle((i - 0.35, min(O[i], C[i])), 0.7, max(abs(C[i] - O[i]), 1e-9), color=col))
    if show["ema"]:
        for nm, colr in (("ema20", "#ff9800"), ("ema50", "#1e88e5"), ("ema200", "#8e24aa")):
            if nm in df and df[nm].notna().any():
                ax.plot(range(n), df[nm].to_numpy(), lw=0.9, color=colr, label=nm.upper())
    lo, hi = float(np.nanmin(L)), float(np.nanmax(H))
    pad = (hi - lo) * 0.06 or 1.0
    if show["zones"]:
        for z in t.zones:
            if z["bottom"] > hi or z["top"] < lo:
                continue
            x0 = max(z["start_i"] - s0, 0)
            ax.add_patch(Rectangle((x0, z["bottom"]), n - x0, z["top"] - z["bottom"], alpha=0.12,
                                   color="green" if z["kind"] == "demand" else "red"))
            manifest.append({"kind": f"zone_{z['kind']}", "time": t.time(z["start_i"]), "price": z["top"], "low": z["bottom"]})
    if show["sr"]:
        for s in t.sr:
            if lo - pad <= s["level"] <= hi + pad:
                ax.axhline(s["level"], color="#ffa000", lw=0.8, ls="--")
                ax.text(0, s["level"], f" {s['role']} {s['level']:.2f} ({s['touches']}x)", fontsize=7, color="#e65100", va="bottom")
                manifest.append({"kind": s["role"], "time": None, "price": s["level"]})
    if show["swings"]:
        for sw in t.swings:
            if sw.i >= s0:
                x = sw.i - s0
                ax.annotate(sw.label, (x, sw.price), xytext=(0, 7 if sw.kind == "H" else -11), textcoords="offset points",
                            ha="center", fontsize=7, color="#b71c1c" if sw.kind == "H" else "#1b5e20")
                manifest.append({"kind": f"swing_{sw.label}", "time": t.time(sw.i), "price": sw.price,
                                 "confirmed_time": t.time(sw.confirmed_at)})
    if show["events"]:
        for e in t.events:
            if e.i >= s0:
                ax.hlines(e.level, max(e.swing_i - s0, 0), e.i - s0, color="#1565c0", lw=1.1)
                ax.text(e.i - s0, e.level, f" {e.type}", fontsize=7, color="#1565c0", va="bottom")
                manifest.append({"kind": e.type, "time": t.time(e.i), "price": e.level, "direction": e.direction})
    if show["levels"] and signal:
        for key, colr in (("stop", "#d32f2f"), ("target", "#2e7d32")):
            v = signal.get(key)
            if v is not None and np.isfinite(v):
                ax.axhline(v, color=colr, lw=1.0, ls=":")
                ax.text(n - 1, v, f"{key} {v:.2f} ", fontsize=7, color=colr, ha="right", va="bottom")
                manifest.append({"kind": key, "time": None, "price": float(v)})
                lo, hi = min(lo, v), max(hi, v)
        st = signal.get("signal_time")
        idx = [str(x)[:16] for x in df.index]
        if st in idx:
            x = idx.index(st)
            ax.annotate("signal", (x, H[x]), xytext=(0, 18), textcoords="offset points", ha="center", fontsize=8,
                        color="#000", arrowprops={"arrowstyle": "->", "lw": 0.8})
            manifest.append({"kind": "signal", "time": st, "price": float(H[x])})
    ax.set_ylim(lo - pad, hi + pad)
    vol = df["Volume"].to_numpy()
    axv.bar(range(n), np.nan_to_num(vol), color=[UP if C[i] >= O[i] else DOWN for i in range(n)], width=0.7)
    axv.set_ylabel("Vol", fontsize=7)
    ticks = list(range(0, n, max(1, n // 8)))
    axv.set_xticks(ticks)
    axv.set_xticklabels([str(df.index[i])[:10] if t.tf in ("1D", "1W") else str(df.index[i])[5:16] for i in ticks], fontsize=7, rotation=20)
    ax.tick_params(labelsize=7)
    ax.grid(alpha=0.15)
    ax.set_title(title, fontsize=10)
    if show["ema"]:
        ax.legend(fontsize=7, loc="upper left")
    fig.text(0.99, 0.005, f"bars to {t.time()} (last completed) · rendered by AMW", fontsize=6, ha="right", color="#666")
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=dpi)
    plt.close(fig)
    return buf.getvalue(), manifest
