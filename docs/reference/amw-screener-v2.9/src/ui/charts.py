"""Plotly chart rendering for one symbol/timeframe analysis (no analysis logic here)."""
from __future__ import annotations

import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from ..analysis.context import TFAnalysis


def build_chart(t: TFAnalysis, bars: int = 200, show: dict | None = None, signals: list[dict] | None = None,
                dark: bool = False) -> go.Figure:
    show = show or {}
    signals = signals or []
    df = t.df.iloc[-bars:]
    s0 = len(t.df) - len(df)
    x = [str(i)[:16] for i in df.index]
    X = lambda i: x[i - s0] if s0 <= i < len(t.df) else None  # noqa: E731
    fig = make_subplots(rows=4, cols=1, shared_xaxes=True, row_heights=[0.58, 0.14, 0.14, 0.14], vertical_spacing=0.02,
                        subplot_titles=("", "Volume", "RSI 14", "MACD"))
    fig.add_trace(go.Candlestick(x=x, open=df.Open, high=df.High, low=df.Low, close=df.Close, name="OHLC",
                                 increasing_line_color="#26a69a", decreasing_line_color="#ef5350"), 1, 1)
    for col, color in (("ema20", "#ff9800"), ("ema50", "#2196f3"), ("ema200", "#9c27b0")):
        if show.get("ema", True) and df[col].notna().any():
            fig.add_trace(go.Scatter(x=x, y=df[col], name=col.upper(), line=dict(width=1, color=color)), 1, 1)
    if show.get("bb", False):
        for col in ("bb_up", "bb_low"):
            fig.add_trace(go.Scatter(x=x, y=df[col], name=col, line=dict(width=0.8, dash="dot", color="grey")), 1, 1)
    if show.get("vwap", False) and df.vwap.notna().any():
        fig.add_trace(go.Scatter(x=x, y=df.vwap, name="VWAP", line=dict(width=1, color="black", dash="dash")), 1, 1)

    if show.get("zones", True):
        for z in t.zones:
            if z["departure_i"] < s0 - 300:
                continue
            fig.add_shape(type="rect", x0=X(max(z["start_i"], s0)), x1=x[-1], y0=z["bottom"], y1=z["top"], row=1, col=1,
                          fillcolor="rgba(76,175,80,0.15)" if z["kind"] == "demand" else "rgba(244,67,54,0.15)", line_width=0)
            fig.add_annotation(x=x[-1], y=z["top"], text=f"{z['kind']} {z['name']} ({z['status']})", showarrow=False,
                               xanchor="right", yanchor="bottom", font=dict(size=9), row=1, col=1)
    if show.get("sr", True):
        for s in t.sr:
            fig.add_hline(y=s["level"], line=dict(color="orange", width=1, dash="dash"), row=1, col=1,
                          annotation_text=f"{s['role']} {s['level']:.2f} ({s['touches']}x)", annotation_font_size=9)
    if show.get("swings", True):
        sw = [s for s in t.swings if s.i >= s0]
        fig.add_trace(go.Scatter(x=[X(s.i) for s in sw], y=[s.price for s in sw], mode="text", text=[s.label for s in sw],
                                 textposition=["top center" if s.kind == "H" else "bottom center" for s in sw],
                                 textfont=dict(size=9, color=["#c62828" if s.kind == "H" else "#2e7d32" for s in sw]),
                                 name="Swings", showlegend=False), 1, 1)
        for e in t.events:
            if e.i >= s0:
                fig.add_shape(type="line", x0=X(max(e.swing_i, s0)), x1=X(e.i), y0=e.level, y1=e.level, row=1, col=1,
                              line=dict(color="#1565c0", width=1.2))
                fig.add_annotation(x=X(e.i), y=e.level, text=e.type, showarrow=False, font=dict(size=9, color="#1565c0"),
                                   xanchor="left", row=1, col=1)
        sws = [s for s in t.liquidity["sweeps"] if s["i"] >= s0]
        if sws:
            fig.add_trace(go.Scatter(x=[X(s["i"]) for s in sws], y=[s["level"] for s in sws], mode="markers",
                                     marker=dict(symbol=["triangle-down" if s["side"] == "BSL" else "triangle-up" for s in sws],
                                                 size=10, color="purple"), name="Liquidity sweep"), 1, 1)
    if show.get("patterns", True):
        for p in t.patterns:
            if p.get("upper"):
                for side in ("upper", "lower"):
                    a0, y0, a1, y1 = p[side]
                    if a1 >= s0:
                        fig.add_shape(type="line", x0=X(max(a0, s0)), x1=X(a1), y0=y0 if a0 >= s0 else y0 + (y1 - y0) * (s0 - a0) / max(1, a1 - a0),
                                      y1=y1, line=dict(color="teal", width=1.5), row=1, col=1)
            if p.get("neckline") is not None:
                fig.add_hline(y=p["neckline"], line=dict(color="teal", width=1.2), row=1, col=1,
                              annotation_text=f"{p['pattern']} [{p['status']}]", annotation_font_size=9,
                              annotation_position="bottom left")
            elif p.get("upper"):
                fig.add_annotation(x=x[-1], y=p["upper"][3], text=f"{p['pattern']} [{p['status']}]", showarrow=False,
                                   font=dict(size=9, color="teal"), xanchor="right", row=1, col=1)
    if show.get("refs", True):
        for s in signals:
            for key, color, nm in (("stop", "#d32f2f", "stop/invalidation"), ("target", "#388e3c", "target ref")):
                v = s.get(key)
                if v is not None and np.isfinite(v):
                    fig.add_hline(y=v, line=dict(color=color, width=0.8, dash="dot"), row=1, col=1,
                                  annotation_text=f"{s['strategy']}: {nm} {v:.2f}", annotation_font_size=8,
                                  annotation_position="top left")

    colors = np.where(df.Close >= df.Open, "#26a69a", "#ef5350")
    fig.add_trace(go.Bar(x=x, y=df.Volume, marker_color=colors, name="Volume", showlegend=False), 2, 1)
    fig.add_trace(go.Scatter(x=x, y=df.rsi14, name="RSI", line=dict(width=1, color="#673ab7"), showlegend=False), 3, 1)
    for lvl in (30, 70):
        fig.add_hline(y=lvl, line=dict(color="grey", width=0.6, dash="dot"), row=3, col=1)
    fig.add_trace(go.Bar(x=x, y=df.macd_hist, name="MACD hist", marker_color="grey", showlegend=False), 4, 1)
    fig.add_trace(go.Scatter(x=x, y=df.macd, name="MACD", line=dict(width=1, color="#1e88e5"), showlegend=False), 4, 1)
    fig.add_trace(go.Scatter(x=x, y=df.macd_signal, name="Signal", line=dict(width=1, color="#fb8c00"), showlegend=False), 4, 1)
    fig.update_xaxes(type="category", nticks=12, rangeslider_visible=False)
    fig.update_layout(template="plotly_dark" if dark else "plotly_white", height=900, margin=dict(l=10, r=10, t=30, b=10), legend=dict(orientation="h", y=1.02),
                      title=f"{t.tf} · analysis only (completed candles) · last {t.last.get('time')}")
    return fig
