"""SFC P1: what the chart draws for one of his own strategies - read from the SAME evaluation the backtest and the bot use.

`plot(strategy, df, ...)` returns:
* `series`: only the indicators his conditions read (each operand that is a number series), one entry per distinct
  formula, with its pane: "price" for a price-unit line (EMA, VWAP, a band ...), else its own pane (RSI, MACD ...);
* `markers`: every signal bar (OQ-1: the bar a side's rule TURNS true) - "entry" when the walk took it (flat, inside his
  window), "skipped" when it was already in a trade; every exit with the rule that closed it;
* `why`: for each signal / exit marker, the conditions that held with their values (the hover, P3);
* `trades`, `state`, `last_signal`, `warmup_bars`;
* P4: `stats` (trades, win rate, points, max drawdown, average R, money P&L) of the same walk;
* P2: `levels` (a fixed number his condition compares a series with, in that series' pane), `pa` (the price-action
  shapes his conditions read on the last closed bar - a breakout's box, a zone band - from the screener's own evidence
  builder), `position` (the open trade: entry / his stop / his target, never a made-up one) and `pending` (the forming
  bar the chart sent: would a side's rule turn true if it closed now - display only, never stored or acted on).
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
END_OF_DATA = "End of backtest"           # the backtest closes a trade still open on its last bar - on the chart it is open
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


def _pane(strategy: StrategyV2Strategy, ast: Any, label: str) -> str:
    unit = validate_study(ast, base_tf=strategy.strategy.timeframe).unit
    return "price" if unit == "price" else f"own:{label.split('(')[0]}"


def levels_of(strategy: StrategyV2Strategy) -> list[dict[str, Any]]:
    """A fixed number compared with a series ("RSI(14) below 30") is a flat line in that series' pane, once each."""
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, float]] = set()
    base = strategy.strategy.timeframe
    for leaf in strategy.compiled.leaves.values():
        ops = leaf.operands
        series = [(role, ast) for role, ast in ops.items() if any(isinstance(x, n.Call) for x in n.walk(ast))]
        if not series:
            continue
        role, ast = series[0]
        label = leaf.operand_text.get(role, n.to_text(ast))
        if leaf.timeframe != base:
            label += f" ({leaf.timeframe})"
        pane = _pane(strategy, ast, leaf.operand_text.get(role, n.to_text(ast)))
        for other, op in ops.items():
            if other == role or not isinstance(op, n.Num) or not math.isfinite(float(op.value)):
                continue
            key = (pane, float(op.value))
            if key in seen:
                continue
            seen.add(key)
            out.append({"id": f"l{len(out) + 1}", "value": float(op.value), "pane": pane, "label": f"{label} {float(op.value):g}"})
    return out


def pa_of(strategy: StrategyV2Strategy, df: pd.DataFrame, symbol: str) -> list[dict[str, Any]]:
    """The price-action shapes his conditions read on the last closed bar (a breakout's box, a zone band), drawn by the
    screener's own evidence builder from the same bars - only the shapes, nothing else of the pack."""
    from app.screener import evidence as E
    from app.screener.runtime import ScreenRuntimeError, SymbolData
    base = strategy.strategy.timeframe
    leaves = [leaf.ast for leaf in strategy.compiled.leaves.values() if leaf.timeframe == base]
    if not leaves or df.empty:
        return []
    ast = n.Logic("ANY", leaves) if len(leaves) > 1 else leaves[0]
    try:
        from app.screener.validator import validate
        validated = validate(ast, base_tf=base)
        pack = E.build_pack(ast, validated, SymbolData(symbol, {base: df}), base_tf=base, window=E.MAX_WINDOW, debug=True)
    except (ScreenRuntimeError, ValueError, KeyError):
        return []
    times = pack["bars"]["t"]
    last = len(times) - 1

    def at(i: Any, fallback: int) -> str:
        return times[max(0, min(last, int(i) if i is not None else fallback))]

    out: list[dict[str, Any]] = []
    for d in pack.get("drawables") or []:
        low, high = d.get("low"), d.get("high")
        if d.get("kind") not in ("band", "box") or low is None or high is None:
            continue
        out.append({"id": f"p{len(out) + 1}", "kind": d["kind"], "low": float(low), "high": float(high), "label": str(d.get("label") or ""),
                    "from": at(d.get("from"), 0), "to": at(d.get("to"), last), "role": d.get("role"),
                    "level": float(d["level"]) if d.get("level") is not None else None})
    return out


def position_of(trades: list[Any]) -> dict[str, Any] | None:
    """The trade still open on the last bar, with his own stop / target (OQ-10: no target set - none is drawn)."""
    tr = next((t for t in reversed(trades) if t.exit_time is None or t.exit_reason == END_OF_DATA), None)
    if tr is None or tr.entry_price is None:
        return None
    return {"side": "long" if tr.direction == SignalDirection.LONG else "short", "entry_time": _iso(tr.entry_time), "entry": _round(tr.entry_price),
            "stop": _round(tr.stop_loss) if tr.stop_loss is not None else None,
            "target": _round(tr.target1) if tr.target1 is not None else None}


def pending_of(strategy: StrategyV2Strategy, df: pd.DataFrame, symbol: str, forming: dict[str, Any] | None) -> dict[str, Any] | None:
    """The forming bar the chart shows, read as if it closed now: the side whose rule would turn true on it. Display only:
    computed apart from the closed-bar evaluation, never stored, never sent to paper / live, never counted."""
    if not forming or df.empty:
        return None
    t = pd.Timestamp(forming["t"])
    t = t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")
    idx = df.index if df.index.tz is not None else df.index.tz_localize("UTC")
    if t <= idx[-1]:
        return None                                            # not newer than the last closed bar: nothing is forming
    row = pd.DataFrame({k: [float(forming[k[0]])] for k in ("open", "high", "low", "close")} | {"volume": [float(forming.get("v") or 0.0)]},
                       index=pd.DatetimeIndex([t if df.index.tz is not None else t.tz_localize(None)], name=df.index.name))
    from app.strategy_v2.evaluate import evaluate
    ev = evaluate(strategy.compiled, {strategy.strategy.timeframe: pd.concat([df, row])}, symbol)
    side = "long" if ev.signal_long[-1] else "short" if ev.signal_short[-1] else None
    return {"t": _iso(t), "side": side} if side else None


CMP_WORDS = {"crosses_above": "crossed above", "crosses_below": "crossed below", "gt": ">", "lt": "<", "gte": "≥", "lte": "≤"}


def leaves_of(node: Any) -> list[str]:
    """The condition ids an AND / OR tree reads, in order."""
    if isinstance(node, str):
        return [node]
    return [x for item in node.items for x in leaves_of(item)]


def stats_of(trades: list[Any]) -> dict[str, Any]:
    """P4: the quick stats of the walk on the chart's bars, in price points (independent of his size) plus the money
    P&L the backtest gives with his own paper settings. A trade still open on the last bar counts as a trade (it has an
    entry marker, T8) but not in the results. R = the move in units of the entry-to-his-stop distance (no stop: no R)."""
    closed = [t for t in trades if t.exit_time is not None and t.exit_reason != END_OF_DATA and t.exit_price is not None]
    points: list[float] = []
    rs: list[float] = []
    for t in closed:
        sign = 1.0 if t.direction == SignalDirection.LONG else -1.0
        move = sign * (float(t.exit_price) - float(t.entry_price))
        points.append(move)
        risk = abs(float(t.entry_price) - float(t.stop_loss)) if t.stop_loss is not None else 0.0
        if risk > 0:
            rs.append(move / risk)
    curve = np.cumsum(points) if points else np.zeros(0)
    peak = np.maximum.accumulate(np.concatenate([[0.0], curve])) if points else np.zeros(1)
    drawdown = float(np.min(np.concatenate([[0.0], curve]) - peak)) if points else 0.0
    wins = sum(p > 0 for p in points)
    money = [float(t.pnl) for t in closed if t.pnl is not None]
    return {"trades": len(trades), "closed": len(closed), "open": len(trades) - len(closed), "wins": int(wins),
            "win_rate": _round(100.0 * wins / len(closed)) if closed else None, "points": _round(float(sum(points))),
            "max_drawdown": _round(drawdown), "avg_r": _round(float(np.mean(rs))) if rs else None,
            "pnl": _round(sum(money)) if money else None}


def why_at(strategy: StrategyV2Strategy, ev: Evaluation, t: int, role: str | None = None) -> list[dict[str, Any]]:
    """P3: the conditions of the rule that fired on bar t (`role`: entry_long / entry_short; None: every condition), each
    with whether it held and its values. A condition that did not hold while the rule fired sits in an OR group the bar
    met another way (`optional`)."""
    tree = strategy.compiled.trees.get(role) if role else None
    ids = list(dict.fromkeys(leaves_of(tree))) if tree is not None else list(strategy.compiled.leaves)
    fired = bool(ev.raw[role][t]) if role and role in ev.raw else False
    rows: list[dict[str, Any]] = []
    for lid in ids:
        leaf = strategy.compiled.leaves[lid]
        values = {r: _round(ev.values[leaf.id][r][t]) for r in leaf.operands}
        held = bool(ev.truth[leaf.id][t])
        rows.append({"id": leaf.id, "held": held, "optional": fired and not held, "text": _words(leaf), "values": values,
                     "timeframe": leaf.timeframe})
    return rows


def _words(leaf: Any) -> str:
    """The condition in its own operands' words (no ScreenQL syntax for the trader)."""
    def word(text: str) -> str:
        try:
            return f"{float(text):g}"                         # a fixed number: "45", never "45.0"
        except ValueError:
            return text

    parts = {k: word(v) for k, v in leaf.operand_text.items()}
    left = parts.get("left", "")
    cmp = getattr(leaf, "cmp", "")
    if "right" in parts:
        text = f"{left} {CMP_WORDS.get(cmp, 'vs')} {parts['right']}"
    elif "low" in parts:
        text = f"{left} between {parts['low']} and {parts['high']}"
    elif cmp in ("rising", "falling"):
        text = f"{left} {cmp} for {leaf.bars} bars"
    else:
        text = left
    if getattr(leaf, "within_bars", None):
        text += f" (within {leaf.within_bars} bars)"
    return text


def plot(strategy: StrategyV2Strategy, df: pd.DataFrame, symbol: str, risk: RiskConfig, charges: Any = None,
         forming: dict[str, Any] | None = None) -> dict[str, Any]:
    from app.backtest.engine import run_backtest
    df = df.iloc[-MAX_BARS:]
    ev, _ = strategy.evaluation(df, symbol)
    signals = [(int(t), "long") for t in np.flatnonzero(ev.signal_long)] + [(int(t), "short") for t in np.flatnonzero(ev.signal_short)]
    signals.sort()
    try:
        result = run_backtest(strategy, df, symbol, strategy.label, risk, charges=charges) if charges is not None else \
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
        why[mid] = why_at(strategy, ev, t, f"entry_{side}")
    for tr in trades:
        if tr.exit_time is None or tr.exit_reason == END_OF_DATA:
            continue                                           # still open on the last bar: no exit is drawn
        mid = f"m{len(markers) + 1}"
        side = "long" if tr.direction == SignalDirection.LONG else "short"
        markers.append({"id": mid, "t": _iso(tr.exit_time), "kind": "exit", "side": side, "rule": tr.exit_reason or "",
                        "pnl": _round(float(tr.pnl)) if tr.pnl is not None else None})
    markers.sort(key=lambda m: m["t"])
    open_trade = next((tr for tr in reversed(trades) if tr.exit_time is None or tr.exit_reason == END_OF_DATA), None)
    last = trades[-1] if trades else None
    state = "warming" if not ev.warm.any() else "in_trade" if open_trade is not None else "exited" if last is not None else "waiting"
    last_signal = None
    if signals:
        t, side = signals[-1]
        last_signal = {"t": _iso(ev.index[t]), "side": side}
    return {
        "series": series_of(strategy, ev), "markers": markers, "why": why, "state": state, "last_signal": last_signal,
        "warmup_bars": ev.warmup_bars, "bars": len(df),
        "stats": stats_of(trades), "levels": levels_of(strategy), "pa": pa_of(strategy, df, symbol), "position": position_of(trades),
        "pending": pending_of(strategy, df, symbol, forming),
        "trades": [{"entry_time": _iso(tr.entry_time), "entry": _round(tr.entry_price), "side": "long" if tr.direction == SignalDirection.LONG else "short",
                    "stop": _round(tr.stop_loss) if tr.stop_loss is not None else None, "target": _round(tr.target1) if tr.target1 is not None else None,
                    "exit_time": _iso(tr.exit_time) if tr.exit_time and tr.exit_reason != END_OF_DATA else None,
                    "exit": _round(tr.exit_price) if tr.exit_price is not None and tr.exit_reason != END_OF_DATA else None,
                    "exit_rule": None if tr.exit_reason == END_OF_DATA else tr.exit_reason,
                    "pnl": _round(tr.pnl) if tr.pnl is not None and tr.exit_reason != END_OF_DATA else None} for tr in trades],
    }


__all__ = ["plot", "series_of", "why_at", "stats_of", "levels_of", "pa_of", "position_of", "pending_of", "MAX_BARS"]
