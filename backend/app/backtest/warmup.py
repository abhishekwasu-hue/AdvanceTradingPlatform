"""REV-S3 (owner order 30 Oct): a template backtest gets the history its recipe reads before the user's test window (R6),
or the result says plainly that it did not.

Why: a template's recipe reads its timeframes' swings and trends (a wave count on the 1h chart needs 250 hourly bars, the
Dow trend of the daily chart at swing size 1 needs 250 daily bars). On a test window shorter than that, the engine's
warm-up (`app.strategy_templates.engine.compute`: "warmup") covers part or all of the window and no bar can signal - the
G templates on three months of 15-minute bars gave 0 trades for this reason.

What this module does (the route / data layer; the backtest engines are unchanged):

* `fetch_history` - when the window's candles came from a server source (the organisation's broker, ATP's stored Daily
  history), the same source is asked again for `history_days` (R6) more calendar days before the window;
* `prepare` - the warm-up bars (strictly before the window's first bar) are put in front of the window and the strategy
  is wrapped so it gives NO signal on a bar before the window's first bar (`WindowStart`): warm-up bars are read, never
  traded, so every trade of the run opens inside the user's window. Causal: the recipe's series at a bar reads that bar
  and the ones before it only (the look-ahead harness, `app.quality.lookahead`), so the bars before the window change
  nothing but how much history each window bar sees - as a deployment, which fetches the same `history_days`;
* `Prepared.note` - when the recipe was still in its warm-up on a bar of the window, one plain sentence that says how
  many trading days of history it needs and how many this test had (`BacktestResult.history`).

Signals only: nothing here sizes, places or exits an order, and no rule of the recipe changes.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import pandas as pd

from app.core.models import Signal

SERVER_SOURCES = ("broker:", "lake:")         # candles the server itself fetched (DataSource labels): fetched again with history
MAX_FETCH_DAYS = 730                          # the candles route's own Daily cap (market_data.candles_routes)
WARMUP_TEXT = "History before your test window: read only"
IST = "Asia/Kolkata"


def _utc(index: pd.Index) -> pd.DatetimeIndex:
    idx = pd.DatetimeIndex(index)
    return idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")


def _utc_frame(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out.index = _utc(df.index)
    return out


def sessions(index: pd.Index) -> int:
    """Trading sessions in `index`: the India dates its bars fall on (a Daily bar is one session)."""
    if not len(index):
        return 0
    return int(_utc(index).tz_convert(IST).normalize().nunique())


def recipe_of(strategy: Any) -> Any:
    """The template recipe of a strategy (None for a built-in or Strategy Builder strategy)."""
    return getattr(strategy, "recipe", None) if callable(getattr(strategy, "history_days", None)) else None


class WindowStart:
    """The strategy as it is, except that a bar before `start` (the user's window start) never gives a signal: the warm-up
    bars are read, never traded. Everything else (timeframes, history, exits, ids) is the inner strategy's."""

    def __init__(self, inner: Any, start: pd.Timestamp) -> None:
        self.__dict__["inner"] = inner
        self.__dict__["start"] = pd.Timestamp(start)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.__dict__["inner"], name)

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(self.__dict__["inner"], name, value)

    def analyze(self, data: Dict[str, pd.DataFrame], symbol: str) -> Signal:
        inner = self.__dict__["inner"]
        df = data.get(inner.timeframes[0])
        if df is not None and len(df):
            last = pd.Timestamp(df.index[-1])
            last = last.tz_localize("UTC") if last.tzinfo is None else last
            if last < self.__dict__["start"]:
                return inner.no_trade(symbol, df.index[-1], [WARMUP_TEXT])
        return inner.analyze(data, symbol)


@dataclass
class Prepared:
    """What a run is given: the frame (warm-up + window), the strategy (guarded when warm-up was added), the window's first
    bar, and what the user is told about the history."""
    frame: pd.DataFrame
    strategy: Any
    window_start: Optional[pd.Timestamp] = None
    warmup_sessions: int = 0
    needed_sessions: Optional[int] = None
    note: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    def history(self) -> Optional[Dict[str, Any]]:
        """`BacktestResult.history`: None for a strategy that is not a template (its result hashes as before)."""
        if self.needed_sessions is None:
            return None
        return {"note": self.note, "needed_days": self.needed_sessions, "warmup_days": self.warmup_sessions}


def prepare(strategy: Any, window: pd.DataFrame, history: Optional[pd.DataFrame] = None) -> Prepared:
    """The run's frame and strategy. `history`: bars from the same source, fetched for the warm-up; only its bars strictly
    before the window's first bar are used. Not a template: the window and the strategy unchanged."""
    recipe = recipe_of(strategy)
    if recipe is None or not len(window):
        return Prepared(window, strategy)
    from app.strategy_templates import engine
    from app.strategy_templates.parity import need_sessions
    win = _utc_frame(window)
    start = pd.Timestamp(win.index[0])
    frame, guarded = window, strategy
    warm = None
    if history is not None and len(history):
        h = _utc_frame(history)
        warm = h[h.index < start]
        warm = warm[~warm.index.duplicated(keep="last")].sort_index()
        if len(warm):
            frame = pd.concat([warm[list(win.columns)] if set(win.columns) <= set(warm.columns) else warm, win])
            guarded = WindowStart(strategy, start)
    warmup = sessions(warm.index) if warm is not None and len(warm) else 0
    needed = need_sessions(recipe)
    prepared = Prepared(frame, guarded, start, warmup, needed)
    # what the engine itself says: is a bar of the window still in the recipe's warm-up?
    series = engine.compute(recipe, {recipe.settings.timeframe: _utc_frame(frame)})
    first_ready = int(series.warmup_bars)
    offset = len(frame) - len(win)
    if first_ready > offset:
        if first_ready < len(frame):
            needed = max(needed, sessions(_utc(frame.index[:first_ready])))
        prepared.needed_sessions = needed
        prepared.note = note(needed, warmup, sessions(win.index), sessions(_utc(frame.index[offset:first_ready])))
    return prepared


def note(needed: int, had: int, window_days: int, blocked_days: int) -> str:
    """One plain sentence: how much history the template needs before the window, how much this test had, and what that
    did to the window."""
    days = lambda k: f"{k} trading day" + ("" if k == 1 else "s")           # noqa: E731
    head = f"This template needs about {days(needed)} of candles before your test window; this test had {had}"
    if blocked_days >= window_days:
        return head + ", so no day of this test could give a signal."
    return head + f", so its first {days(blocked_days)} could not give a signal."


async def fetch_history(session: Any, user: Any, data_source: Optional[str], symbol: str, timeframe: str,
                        window: pd.DataFrame, strategy: Any, now: Optional[datetime] = None) -> Optional[pd.DataFrame]:
    """R6: the template's `history_days` of candles before the window, from the source the window came from (the
    organisation's broker, or ATP's stored Daily history). None when the window was uploaded / sample candles, the
    strategy is not a template, or the source cannot serve it (the result's note then says what the test lacked)."""
    source = str(data_source or "")
    if recipe_of(strategy) is None or not len(window) or not source.startswith(SERVER_SOURCES) or user is None:
        return None
    from app.backtest import bar_source
    start = pd.Timestamp(_utc(window.index[:1])[0])
    now = now or datetime.now(timezone.utc)
    span = max(0, math.ceil((pd.Timestamp(now) - start).total_seconds() / 86400))
    lookback = min(MAX_FETCH_DAYS, span + int(strategy.history_days()))
    broker = source.split(":", 1)[1] if source.startswith("broker:") else None
    try:
        bars = await bar_source.resolve(session, user.tenant_id, symbol, "NSE", timeframe, lookback_days=lookback, user=user,
                                        broker=None if broker in (None, "", "auto") else broker)
    except Exception:  # noqa: BLE001 - the warm-up is extra history: a source that cannot serve it never fails the run
        return None
    return bars.frame


__all__ = ["WindowStart", "Prepared", "prepare", "note", "fetch_history", "sessions", "recipe_of", "WARMUP_TEXT"]
