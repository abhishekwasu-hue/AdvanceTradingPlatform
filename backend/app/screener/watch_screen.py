"""M91 (owner order, research R40): a watchlist's "Screen" - the user's own saved screen read on the list's symbols.

C2: `screen_marks` is the ONE evaluation the watchlist rows (W2 / W3, `POST /api/screener/watchlist-screen/check`) and
the chart overlay (C1, `POST /api/screener/watchlist-screen/overlay`) read. It runs the screener's own evaluator
(`runtime._Eval`, the code `run_screen` runs) on the same bars and parameters, so the list and the chart can never
disagree: both give the same match and the same met / not met mark per condition on the same bars (tested).

* `matched` - the whole screen on the last closed bar, exactly as `run_screen` reads it (`symbol_match`; too little history
  and a value that could not be computed are "not checked" with their reason, never "did not match").
* `rows` - one per condition, in the screen's reading order and with the evidence pack's own ids (`b1.c1`, ...), the
  evidence pack's state rules (`evidence.condition_state`: an OR met by another branch is "not needed") and its value
  line for the last closed bar ("close 1,842.00 > SMA(close, 20) 1,830.40").
* `hits` (the chart only, `bars` > 0) - for each of the newest `bars` bars: was the whole screen true on that bar, on
  bars closed by then? Every ScreenQL function is causal (Z10-a), so one series read bar by bar equals the screen run
  on bars cut at each bar (`history.py`); a screen with a function answered for the last bar only (registry
  LAST_BAR_ONLY) is evaluated on cut bars instead, for at most CUT_BARS bars.

Nothing here adds a rule, a default screen or a default parameter: the screen, its timeframe and its parameters are the
user's own saved screen. Informational only - it places, proposes and changes no order.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any, Iterable

import numpy as np
import pandas as pd

from app.screener import evidence as E
from app.screener import nodes as n
from app.screener import reasons
from app.screener.breakouts.screens import skip_missing_volume
from app.screener.runtime import ScreenRuntimeError, SymbolData, _Eval, _last, bar_close, no_index_volume, symbol_match

FLAG = "watchlist_screen"
SETTINGS = "watchlist_screens"          # internal user_prefs row: each list's screen and alert choice (W1 / W4)
SEEN = "watchlist_screen_seen"          # internal user_prefs row: each list's matches at its last check (W4)
OVERLAY_BARS = E.DEFAULT_WINDOW         # bars the chart overlay draws (the evidence pack's own window)
CUT_BARS = 60                           # a last-bar-only screen's markers: cut evaluations of at most this many bars
CHECK_GRACE_SECONDS = 20                # operational: the re-check waits this long after a bar closes for its data


def _ctx(ev: _Eval, base_tf: str, params: dict[str, Any], frame: pd.DataFrame) -> E._Ctx:
    return E._Ctx(ev=ev, base_tf=base_tf, params=params, frame=frame, window=1, debug=False)


def condition_rows(ev: _Eval, ast: Any, base_tf: str, params: dict[str, Any], frame: pd.DataFrame) -> list[dict[str, Any]]:
    """Every condition of the screen on the last closed bar: id, text, state (met / failed / not_needed / unknown) and the
    value line - the evidence pack's ids, state rules and numbers (evidence.build_pack's rows, without the drawing)."""
    ctx = _ctx(ev, base_tf, params, frame)
    out: list[dict[str, Any]] = []
    for bi, block in enumerate(E.blocks_of(ast), start=1):
        bid = f"b{bi}"
        found = E.conditions_in(block, prefix=f"{bid}.g")
        group_met = E._groups_met(ev, base_tf, found)
        for ci, item in enumerate(found, start=1):
            if len(out) >= E.MAX_ROWS:
                return out
            cond = item.node
            shown_node = cond if item.required else n.Not(cond)
            text = n.to_text(cond) if item.required else f"NOT ({n.to_text(cond)})"
            row: dict[str, Any] = {"id": f"{bid}.c{ci}", "block": bid, "text": text}
            try:
                raw = E._truth(ev.ev(cond, base_tf))
            except ScreenRuntimeError:
                out.append({**row, "state": E.UNKNOWN, "value": "n/a"})
                continue
            terms = [E._term(ctx, op) for op in E._operands(cond)]
            call = E._inner_call(cond)
            if call is not None and not terms:
                terms = [E._term(ctx, a) for a in call.args if not isinstance(a, (n.Str, n.Bool))][:3]
            value = E._verify_text(shown_node, terms)
            if not item.required and not value.startswith("NOT "):
                value = f"NOT ({value})"
            out.append({**row, "state": E.condition_state(raw, item, group_met), "value": value})
    return out


def _short(validated: Any, data: SymbolData, ev: _Eval, base_tf: str) -> list[str]:
    """The timeframes with less history than the screen reads - run_screen's own check (S5-A review)."""
    return [tf for tf, bars in validated.lookback.items()
            if (tf in data.frames or n.TF_MINUTES.get(tf, 0) > n.TF_MINUTES[base_tf]) and len(ev.frame(tf)) < bars]


def _true(v: Any) -> bool:
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return False
    return bool(v)


def bar_hits(ast: Any, validated: Any, data: SymbolData, ev: _Eval, *, base_tf: str, params: dict[str, Any],
             constants: dict[int, float], bars: int) -> list[bool]:
    """For each of the newest `bars` base bars: the whole screen held on that bar (on bars closed by then)."""
    from app.screener.history import last_bar_only_calls
    base = data.frames[base_tf]
    w = max(1, min(int(bars), len(base)))
    lookback = int((validated.lookback or {}).get(base_tf, 1) or 1)
    first = len(base) - w
    if not last_bar_only_calls(ast) and not validated.cross_sectional:
        try:
            series = ev.ev(ast, base_tf)
        except ScreenRuntimeError:
            return [False] * w
        if isinstance(series, pd.Series):
            arr = series.reindex(base.index).to_numpy() if len(series) != len(base) else series.to_numpy()
            return [(first + i + 1 >= lookback) and _true(arr[first + i]) for i in range(w)]
        return [False] * (w - 1) + [_true(_last(series))]
    out = [False] * w
    for i in range(max(0, w - CUT_BARS), w):
        at = bar_close(pd.DatetimeIndex([base.index[first + i]]), base_tf)[0].to_pydatetime()
        cut = E._trim(data, base_tf, at)
        if cut.frames.get(base_tf) is None or len(cut.frames[base_tf]) < max(1, lookback):
            continue
        try:
            out[i] = _true(_last(_Eval(cut, base_tf, params, constants).ev(ast, base_tf)))
        except ScreenRuntimeError:
            continue
    return out


def screen_marks(ast: Any, validated: Any, data: SymbolData, *, base_tf: str, params: dict[str, Any] | None = None,
                 constants: dict[int, float] | None = None, bars: int = 0) -> dict[str, Any]:
    """C2: the one shared evaluation of a screen on one symbol (module docstring) -> {matched, reason, code, rows, hits}."""
    params = dict(params or {})
    constants = dict(constants or {})
    base = data.frames.get(base_tf)
    if base is None or base.empty:
        return {"matched": False, "reason": reasons.Reason(f"no {base_tf} bars", reasons.NO_BARS), "rows": [], "hits": [], "bar": None}
    no_index_volume([data])                                   # M93-Q3: as run_screen (an index's 0 volume is no volume data)
    ev = _Eval(data, base_tf, params, constants)
    why: reasons.Reason | None = None
    matched = False
    note: str | None = None
    short = _short(validated, data, ev, base_tf)
    if short:
        why = reasons.short_history(short)
    else:
        try:
            m = symbol_match(ev, ast, base_tf)                # run_screen's own answer (M93-Q3 no-data rules included)
            matched, why, note = m.matched, (reasons.as_reason(m.reason) if m.reason else None), m.note
        except ScreenRuntimeError as exc:
            why = reasons.Reason(str(exc), reasons.NOT_CHECKED)
        except Exception as exc:  # noqa: BLE001 - one symbol's bad data never fails the list (run_screen's rule)
            why = reasons.not_checked(exc, data.symbol)
    try:
        with skip_missing_volume():             # M93-Q3: each condition read as the list reads it
            rows = condition_rows(ev, ast, base_tf, params, base)
    except Exception as exc:  # noqa: BLE001 - the marks of a symbol whose data broke the screen: none, with its reason
        rows = []
        why = why or reasons.not_checked(exc, data.symbol)
    hits: list[bool] = []
    if bars > 0:
        with skip_missing_volume():             # M93-Q3: the bars read as the list reads them
            hits = bar_hits(ast, validated, data, ev, base_tf=base_tf, params=params, constants=constants, bars=bars)
        if hits:
            hits[-1] = bool(matched)            # the newest bar is the list's own answer, by construction
    out = {"matched": bool(matched), "reason": why, "rows": rows, "hits": hits, "bar": base.index[-1]}
    if note:
        out["note"] = note                      # M93-Q3: a volume condition not applied (no volume data)
    return out


def marks_of(rows: Iterable[dict[str, Any]]) -> list[str]:
    """The per-condition marks alone (the list's compact form; the parity test compares them with the overlay's)."""
    return [str(r["state"]) for r in rows]


# --------------------------------------------------------------------------------------------- W3: the next bar close
def next_close(now: datetime, tf: str, holidays: Iterable[date] = ()) -> datetime | None:
    """The first moment after `now` at which a newer bar of `tf` has closed on the exchange clock (the bar-close
    engine's own `expected_bar`), or None for a timeframe it does not know. The page re-checks the list then."""
    from app.alerts import barclose as bc
    from app.market_data.calendar import IST, is_trading_day
    if tf not in bc.SUPPORTED:
        return None
    hol = set(holidays)
    at = now.astimezone(UTC) if now.tzinfo else now.replace(tzinfo=UTC)
    current = bc.expected_bar(at, tf, hol)
    day = at.astimezone(IST).date()
    for _ in range(45):
        if is_trading_day(day, hol):
            open_, close = bc._session_bounds(day)
            if tf in bc.INTRADAY:
                step = timedelta(minutes=bc.INTRADAY[tf])
                candidates, t = [], open_ + step
                while t < close:
                    candidates.append(t)
                    t += step
                candidates.append(close)
            else:
                candidates = [close]
            for c in candidates:
                if c > at and bc.expected_bar(c, tf, hol) != current:
                    return c.astimezone(UTC)
        day += timedelta(days=1)
    return None


__all__ = ["FLAG", "SETTINGS", "SEEN", "OVERLAY_BARS", "CUT_BARS", "screen_marks", "condition_rows", "bar_hits", "marks_of", "next_close"]
