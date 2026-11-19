"""SFC P1: what the chart draws for one of his own strategies - read from the SAME evaluation the backtest and the bot use.

`plot(strategy, df, ...)` returns:
* `series`: only the indicators his conditions read (each operand that is a number series), one entry per distinct
  formula, with its pane: "price" for a price-unit line (EMA, VWAP, a band ...), else its own pane (RSI, MACD ...);
* `markers`: every signal bar (OQ-1: the bar a side's rule TURNS true) - "entry" when the walk took it (flat, inside his
  window), "skipped" when it was already in a trade; every exit with the rule that closed it;
* `why`: for each signal / exit marker, the conditions that held with their values (the hover, P3);
* `trades`, `state`, `last_signal`, `warmup_bars`.
The trades are the backtest engine's own walk (`run_backtest`) on the same bars, so a marker here is a trade there.
Nothing is stored, nothing is ordered.
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from app.core.enums import SignalDirection
from app.core.models import RiskConfig
from app.screener import nodes as n
from app.screener.validator import validate_study
from app.strategy_v2.adapter import StrategyV2Strategy
from app.strategy_v2.evaluate import Evaluation

MAX_BARS = 3000
MAX_SERIES = 12


def _iso(ts: Any) -> str:
    return pd.Timestamp(ts).isoformat()


def _round(v: float) -> float | None:
    return None if v is None or not math.isfinite(v) else round(float(v), 4)


def series_of(strategy: StrategyV2Strategy, ev: Evaluation) -> list[dict[str, Any]]:
    """The number series his conditions read, once each (the same formula in two conditions is one line)."""
    out: list[dict[str, Any]] = []
    seen: dict[str, int] = {}
    base = strategy.strategy.timeframe
    for leaf in strategy.compiled.leaves.values():
        for role, ast in leaf.operands.items():
            text = n.to_text(ast)
            if text in seen or not any(isinstance(x, n.Call) for x in n.walk(ast)):
                continue                                       # a plain price or a fixed number is not a study
            unit = validate_study(ast, base_tf=base).unit
            values = ev.values[leaf.id][role]
            seen[text] = len(out)
            label = leaf.operand_text.get(role, text)
            out.append({"id": f"s{len(out) + 1}", "label": label + (f" ({leaf.timeframe})" if leaf.timeframe != base else ""),
                        "pane": "price" if unit == "price" else f"own:{label.split('(')[0]}",
                        "points": [{"t": _iso(ev.index[i]), "v": _round(values[i])} for i in range(len(values)) if math.isfinite(values[i])]})
            if len(out) >= MAX_SERIES:
                return out
    return out


def why_at(strategy: StrategyV2Strategy, ev: Evaluation, t: int) -> list[dict[str, Any]]:
    """Each condition on bar t: held or not, with its values (the marker's hover)."""
    rows: list[dict[str, Any]] = []
    for leaf in strategy.compiled.leaves.values():
        values = {role: _round(ev.values[leaf.id][role][t]) for role in leaf.operands}
        rows.append({"id": leaf.id, "held": bool(ev.truth[leaf.id][t]), "text": _words(leaf), "values": values,
                     "timeframe": leaf.timeframe})
    return rows


def _words(leaf: Any) -> str:
    """The condition in its own operands' words (no ScreenQL syntax for the trader)."""
    parts = leaf.operand_text
    left = parts.get("left", "")
    if "right" in parts:
        return f"{left} vs {parts['right']}"
    if "low" in parts:
        return f"{left} between {parts['low']} and {parts['high']}"
    return left


def plot(strategy: StrategyV2Strategy, df: pd.DataFrame, symbol: str, risk: RiskConfig, charges: Any = None) -> dict[str, Any]:
    from app.backtest.engine import run_backtest
    df = df.iloc[-MAX_BARS:]
    ev, _ = strategy.evaluation(df, symbol)
    signals = [(int(t), "long") for t in np.flatnonzero(ev.signal_long)] + [(int(t), "short") for t in np.flatnonzero(ev.signal_short)]
    signals.sort()
    try:
        result = run_backtest(strategy, df, symbol, strategy.label, risk, charges) if charges is not None else \
            run_backtest(strategy, df, symbol, strategy.label, risk)
        trades = list(result.trades)
    except (ValueError, ZeroDivisionError):
        trades = []
    taken = {pd.Timestamp(tr.entry_time) for tr in trades}
    markers: list[dict[str, Any]] = []
    why: dict[str, Any] = {}
    for t, side in signals:
        ts = pd.Timestamp(ev.index[t])
        kind = "entry" if ts in taken else "skipped"
        mid = f"m{len(markers) + 1}"
        markers.append({"id": mid, "t": _iso(ts), "kind": kind, "side": side})
        why[mid] = why_at(strategy, ev, t)
    for tr in trades:
        if tr.exit_time is None:
            continue
        mid = f"m{len(markers) + 1}"
        side = "long" if tr.direction == SignalDirection.LONG else "short"
        markers.append({"id": mid, "t": _iso(tr.exit_time), "kind": "exit", "side": side, "rule": tr.exit_reason or "",
                        "pnl": _round(float(tr.pnl)) if tr.pnl is not None else None})
    markers.sort(key=lambda m: m["t"])
    open_trade = next((tr for tr in reversed(trades) if tr.exit_time is None), None)
    last = trades[-1] if trades else None
    state = "warming" if not ev.warm.any() else "in_trade" if open_trade is not None else "exited" if last is not None else "waiting"
    last_signal = None
    if signals:
        t, side = signals[-1]
        last_signal = {"t": _iso(ev.index[t]), "side": side}
    return {
        "series": series_of(strategy, ev), "markers": markers, "why": why, "state": state, "last_signal": last_signal,
        "warmup_bars": ev.warmup_bars, "bars": len(df),
        "trades": [{"entry_time": _iso(tr.entry_time), "entry": _round(tr.entry_price), "side": "long" if tr.direction == SignalDirection.LONG else "short",
                    "stop": _round(tr.stop_loss) if tr.stop_loss is not None else None, "target": _round(tr.target1) if tr.target1 is not None else None,
                    "exit_time": _iso(tr.exit_time) if tr.exit_time else None, "exit": _round(tr.exit_price) if tr.exit_price is not None else None,
                    "exit_rule": tr.exit_reason, "pnl": _round(tr.pnl) if tr.pnl is not None else None} for tr in trades],
    }


__all__ = ["plot", "series_of", "why_at", "MAX_BARS"]
