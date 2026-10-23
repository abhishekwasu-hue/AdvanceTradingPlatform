#!/usr/bin/env python
"""TPL-F6: how many signal bars change when an equal swing low stops reading as a lower low (the swing structure made
mirror-symmetric: DowTrend, SwingBreakType, SwingLabel). Read-only; synthetic bars only, never market data.

    cd backend && PYTHONPATH=. python tests/tools/tpl_f6_signal_delta.py [--count 300] [--bars 600] [--markdown]

Each item is run twice on the same deterministic series: with the rule before the fix (tests/mirror_tie_fixtures.py
`legacy_dow` / `legacy_swing_label` patched in) and with today's rule. A "signal bar" is a (series, bar, direction) where
the item fires: a template's signal, a screener condition that is true, or a classifier value (one row per value). The
series (tests/mirror_tie_fixtures.series): random walks and three kinds built to hold equal highs and lows, cycled, on
the 15m / 1h / 1d clocks; family G runs on 1h bars only, longer (its wave and higher-timeframe reads need the history).
Items that do not read the swing structure are run as controls (they must not change)."""
from __future__ import annotations

import argparse
import contextlib
import json
import sys
import warnings
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Set, Tuple

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))          # backend/

from app.screener import runtime                                        # noqa: E402
from app.screener.breakouts import screens                              # noqa: E402
from app.screener.breakouts import templates as bt                      # noqa: E402
from app.screener.parser import parse                                   # noqa: E402
from app.screener.runtime import SymbolData, evaluate                   # noqa: E402
from app.strategy_templates import compile as C                        # noqa: E402
from app.strategy_templates import engine, library                     # noqa: E402
from tests.mirror_tie_fixtures import legacy_dow, legacy_swing_label, series   # noqa: E402

TFS = ("15m", "1h", "1d")
Hits = Set[Tuple[Any, ...]]


@contextlib.contextmanager
def legacy_rule() -> Iterator[None]:
    """The structure functions as they were before TPL-F6."""
    old_dow, old_label = screens.Bars.dow, runtime._swing_label
    screens.Bars.dow = lambda self, degree: legacy_dow(self, degree)   # type: ignore[method-assign]
    runtime._swing_label = legacy_swing_label                           # type: ignore[assignment]
    try:
        yield
    finally:
        screens.Bars.dow, runtime._swing_label = old_dow, old_label    # type: ignore[method-assign, assignment]


# ------------------------------------------------------------------------------------------------------------- items
def _template(tid: str, params: Dict[str, Any]) -> Callable[[pd.DataFrame, str], Hits]:
    def run(df: pd.DataFrame, tf: str) -> Hits:
        t = library.get(tid)
        try:
            settings = C.resolve_settings(t, tf, params=params)
        except C.TemplateError:
            return set()
        s = engine.compute(C.compile_recipe(t, settings), {tf: df})
        return {(int(i), int(s.direction[i])) for i in s.signal_bars()}
    return run


def _condition(text: str) -> Callable[[pd.DataFrame, str], Hits]:
    ast = parse(text)

    def run(df: pd.DataFrame, tf: str) -> Hits:
        try:
            v = evaluate(ast, SymbolData("S", {tf: df}), base_tf=tf)
        except runtime.ScreenRuntimeError:
            return {("error",)}                    # e.g. a context timeframe with no bar yet; counted, never hidden
        if not isinstance(v, pd.Series):
            return set()
        return {(int(i),) for i in np.flatnonzero(v.fillna(False).astype(bool).to_numpy())}
    return run


def _classifier(text: str) -> Callable[[pd.DataFrame, str], Hits]:
    ast = parse(text)

    def run(df: pd.DataFrame, tf: str) -> Hits:
        v = evaluate(ast, SymbolData("S", {tf: df}), base_tf=tf)
        return {(int(i), str(x)) for i, x in enumerate(v.tolist()) if isinstance(x, str) and x}
    return run


Item = Tuple[str, str, Callable[[pd.DataFrame, str], Hits], Tuple[str, ...]]     # (group, label, run, timeframes)


def items() -> List[Item]:
    out: List[Item] = []
    for d in (0, 1):
        out.append(("screener function", f"DowTrend({d}) value", _classifier(f"DowTrend({d})"), TFS))
        out.append(("screener function", f'SwingLabel("low", {d}) value', _classifier(f'SwingLabel("low", {d})'), TFS))
        out.append(("screener function", f'SwingLabel("high", {d}) value', _classifier(f'SwingLabel("high", {d})'), TFS))
        for side in ("up", "down"):
            out.append(("screener function", f'SwingBreakType("{side}", {d}, 0.2) value',
                        _classifier(f'SwingBreakType("{side}", {d}, 0.2)'), TFS))
    for edge, direction in (("SwingLow(0)", "bullish"), ("SwingHigh(0)", "bearish")):
        out.append(("control", f'ReversalAt({edge}, "{direction}")', _condition(f'ReversalAt({edge}, "{direction}")'), TFS))
    # the pre-built breakout templates whose ScreenQL text reads the higher-timeframe DowTrend (or B2's break type)
    for t in bt.TEMPLATES:
        text, _ = bt.screenql(t.screen, t.signal_tf, bt.template_params(t))
        if text and t.signal_tf in TFS and ("DowTrend" in text or "SwingBreakType" in text):
            out.append(("breakout template", f"{t.id} ({t.screen} @{t.signal_tf})", _condition(text), (t.signal_tf + "-long",)))
    tpl: List[Tuple[str, str, Dict[str, Any]]] = [
        ("A2", "A2 range edges, swing size 0", {"swing_degree": 0}), ("A2", "A2 range edges (defaults)", {}),
        ("F2", "F2 range edges, swing size 0", {"swing_degree": 0}), ("F2", "F2 range edges (defaults)", {}),
        ("F2", "F2 band reversion, structure regime", {"source": "band_reversion", "regime": "structure", "swing_degree": 0}),
        ("E3", "E3 structure regime, swing size 0", {"regime": "structure", "swing_degree": 0}),
        ("E3", "E3 structure regime (defaults)", {"regime": "structure"}),
        ("E2", "E2 RSI divergence, CHoCH trigger (defaults)", {}),
        ("B2", "B2 swing break BOS (defaults)", {}), ("B2", "B2 swing break, swing size 0", {"swing_degree": 0}),
    ]
    out += [(f"template {tid}", label, _template(tid, p), TFS) for tid, label, p in tpl]
    g = {"wave_tf": "1h", "higher_tf": "1d", "trend_degree": 0}
    for tid in ("G1", "G2", "G3", "G4", "G5", "G6", "G7", "G8", "G9", "G10", "G11"):
        out.append(("template G", f"{tid} (wave 1h, trend 1d at swing size 0)", _template(tid, g), ("1h-long",)))
    for tid in ("C1", "C3", "D1", "D2", "D3", "B1", "A5"):                # read no structure function: must not change
        out.append(("control", f"{tid} (defaults)", _template(tid, {}), ("15m", "1d")))
    return out


# ------------------------------------------------------------------------------------------------------------- report
def measure(count: int, bars: int) -> List[Dict[str, Any]]:
    warnings.simplefilter("ignore")
    frames: Dict[str, List[Tuple[str, int, pd.DataFrame]]] = {tf: list(series(count, bars, tf)) for tf in TFS}
    for tf in TFS:                                  # longer series: the higher-timeframe trend needs its own history
        frames[tf + "-long"] = list(series(max(count // 3, 1), bars * 3, tf, seed0=50_000))
    rows: List[Dict[str, Any]] = []
    for group, label, run, tfs in items():
        tot: Dict[str, int] = defaultdict(int)
        for key in tfs:
            tf = key.split("-")[0]
            for kind, seed, df in frames[key]:
                with legacy_rule():
                    before = run(df, tf)
                after = run(df, tf)
                tot["series"] += 1
                if ("error",) in before or ("error",) in after:
                    tot["could_not_run"] += 1
                    continue
                tot["before"] += len(before)
                tot["after"] += len(after)
                tot["added"] += len(after - before)
                tot["removed"] += len(before - after)
                tot["series_changed"] += before != after
        rows.append({"group": group, "item": label, "timeframes": ", ".join(k.replace("-long", " (long)") for k in tfs), **tot})
    return rows


def markdown(rows: List[Dict[str, Any]], count: int, bars: int) -> str:
    head = (f"Sample: {count} series per timeframe x {bars} bars (15m / 1h / 1d); \"(long)\": {max(count // 3, 1)} series x "
            f"{bars * 3} bars (family G and the breakout templates, whose higher-timeframe trend needs the history); "
            "deterministic synthetic bars (tests/mirror_tie_fixtures.py). A classifier row counts one signal bar per bar with "
            "a value, so a changed value is one added and one removed.\n\n"
            "| Group | Item | Timeframes | Series | Could not run | Series changed | Signal bars before | After | Added | Removed |\n"
            "|---|---|---|---|---|---|---|---|---|---|\n")
    body = "".join(f"| {r['group']} | {r['item']} | {r['timeframes']} | {r['series']} | {r.get('could_not_run', 0)} | {r['series_changed']} | {r['before']} | "
                   f"{r['after']} | {r['added']} | {r['removed']} |\n" for r in rows)
    return head + body


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--count", type=int, default=300, help="series per timeframe")
    parser.add_argument("--bars", type=int, default=600, help="bars per series")
    parser.add_argument("--markdown", action="store_true", help="print a Markdown table")
    args = parser.parse_args(argv)
    rows = measure(args.count, args.bars)
    print(markdown(rows, args.count, args.bars) if args.markdown else json.dumps(rows, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
