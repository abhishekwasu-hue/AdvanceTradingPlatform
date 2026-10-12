"""U3 G3 (SCREENER_SPEC §5 "Reproducibility"): a stored screen run can be replayed.

A run stores the AST (checked against `ast_sha256`), its parameters, the universe, the base timeframe, the data source,
`data_as_of` (the newest bar close it read) and each symbol's last closed bar (`bar_times_json`). A replay fetches the
same symbols from the same kind of source, keeps only the bars that had closed by `data_as_of`, evaluates the same AST
with the same parameters, and compares symbol by symbol:

* same last bar and same answer -> reproduced;
* a different last bar -> the source now returns other bars for that moment (a gap filled, a bar missing);
* the same last bar but another answer -> the bars before it changed (a correction or an adjustment).

A replay stores nothing, never changes the stored run and never places, proposes or ranks an order. Runs stored before
U3 have no AST or `data_as_of` and are refused as "not replayable".
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple

import pandas as pd

from app.screener.runtime import Match, SymbolData, bar_close, closed_only

REPLAY_FLAG = "screener_run_replay"
MAX_DIFFERENCES = 50


class NotReplayable(ValueError):
    """The stored run lacks what a replay needs, or its AST does not hash to the stored hash."""


def bar_times(universe: Iterable[SymbolData], base_tf: str) -> Dict[str, str]:
    """{symbol: ISO close time of the last base-timeframe bar the run evaluated}."""
    out: Dict[str, str] = {}
    for data in universe:
        frame = data.frames.get(base_tf)
        if frame is None or frame.empty:
            continue
        out[data.symbol] = pd.Timestamp(bar_close(frame.index[-1:], base_tf)[0]).isoformat()
    return out


def data_as_of(times: Dict[str, str]) -> Optional[datetime]:
    """The newest bar close the run read (None when it read no bars)."""
    stamps = [pd.Timestamp(t) for t in times.values()]
    return max(stamps).to_pydatetime() if stamps else None


def ast_digest(ast_json: str) -> str:
    return hashlib.sha256(ast_json.encode()).hexdigest()


def inputs(row: Any) -> Tuple[Dict[str, Any], Dict[str, Any], List[str], str, datetime]:
    """(AST JSON, params, symbols, base_tf, data_as_of) of a stored run; NotReplayable when something is missing."""
    if not row.ast_json or row.data_as_of is None:
        raise NotReplayable("this run was stored before runs kept their AST and data time (U3), so it cannot be replayed")
    if ast_digest(row.ast_json) != row.ast_sha256:
        raise NotReplayable("the stored AST does not match the run's AST hash")
    as_of = row.data_as_of
    if as_of.tzinfo is None:                       # SQLite hands back naive datetimes; the column is UTC
        as_of = pd.Timestamp(as_of).tz_localize("UTC").to_pydatetime()
    return json.loads(row.ast_json), json.loads(row.params_json or "{}"), list(json.loads(row.universe_json)), row.base_tf, as_of


def trim(universe: List[SymbolData], as_of: datetime) -> List[SymbolData]:
    """Every frame cut to the bars that had closed by `as_of` (a symbol left with no base bars keeps an empty frame)."""
    for data in universe:
        data.frames = {tf: closed_only(frame, tf, as_of) for tf, frame in data.frames.items()}
    return universe


def compare(stored_results: List[Dict[str, Any]], stored_times: Dict[str, str], matches: List[Match],
            replayed_times: Dict[str, str], problems: Dict[str, str]) -> Dict[str, Any]:
    """Symbol-by-symbol comparison of a stored run with its replay."""
    replayed = {m.symbol: m for m in matches}
    differences: List[Dict[str, Any]] = []
    same = 0
    for row in stored_results:
        symbol = str(row["symbol"])
        then = {"matched": bool(row.get("matched")), "bar_time": stored_times.get(symbol)}
        match = replayed.get(symbol)
        now = {"matched": bool(match.matched) if match else False, "bar_time": replayed_times.get(symbol),
               "reason": (match.reason if match else problems.get(symbol))}
        if then["matched"] == now["matched"] and then["bar_time"] == now["bar_time"]:
            same += 1
            continue
        if then["bar_time"] != now["bar_time"]:
            why = "the source now returns a different last bar for that time"
        else:
            why = "same last bar, different answer: earlier bars changed (a correction or an adjustment)"
        differences.append({"symbol": symbol, "stored": then, "replayed": now, "why": why})
    return {"reproduced": not differences, "same": same, "differences": differences[:MAX_DIFFERENCES],
            "more_differences": max(0, len(differences) - MAX_DIFFERENCES)}


__all__ = ["REPLAY_FLAG", "NotReplayable", "bar_times", "data_as_of", "ast_digest", "inputs", "trim", "compare"]
