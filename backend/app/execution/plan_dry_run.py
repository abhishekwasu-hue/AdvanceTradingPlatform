"""M76 MP-12 (owner order M76 D-2; amendment of 1 Nov: facilities, not enforcement, ONE compact card): a trigger plan's
dry run - "what would have fired on the last N sessions, with the engine's states at those times".

Read-only, end to end. A dry run never places, arms, proposes, notifies or changes anything: no plan row is written (a
saved plan is read, never touched - the replay works on a detached copy of its condition), the live engine's own
re-read timing (`plan_conditions._NEXT`) is not touched, and the route rolls its session back. What it does:

* **Replay** - his plan's condition over the stored CLOSED bars of the last N sessions he asks for (N is his own, at most
  MAX_SESSIONS - an operational bound). The plan is taken as armed at the start of each session and fires at most once
  in it, as the live plan fires once. Every decision is made by the live engine's own code, as of a pass right after
  each candle closed - never a bar after that moment (no look-ahead):
    - price TOUCH: `trigger_rules.touched` on each closed one-minute bar's low / high (the shared rule; the backtest's way
      of reading a bar it cannot see inside), only inside his own time window (`trigger_plans.in_window`);
    - price CLOSE: `trigger_plans.reached` (the live engine's function, on a detached copy of the plan) with that
      session's one-minute bars, as of each close of his timeframe (`trigger_rules.close_trigger`'s armed-after rule);
    - AREA / STRUCTURE: `plan_conditions.read` (the live engine's ONE reading source: the signal engine when it reads
      this chart for him, else ATP's readers; never both) as of each closed candle of his timeframe. Each row names
      the source that met it, as the live trigger note does.
  At most MAX_READS method readings and MAX_SECONDS per dry run (operational bounds): a longer replay is refused with
  plain words before it starts, and a replay cut by time says which sessions were not replayed - never a guess.
* **What would be sent** - each order of the plan through the order ticket's own `assess` (the checks `place` runs at
  fire time, read-only: no entry slot is taken, nothing is stored), at the price now, and whether a fire would place at
  once or ask him first (his plan's choice, his Trade settings, the organisation's approval-only mode).

Nothing here is a trading rule: the condition, the timeframe, the window, the orders and N are his own.
"""
from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast

import pandas as pd

from app.execution import plan_conditions as PC
from app.execution import trigger_plans as tp
from app.trading import trigger_rules

MAX_SESSIONS = 10              # sessions one dry run replays (operational: the stored one-minute window)
MAX_READS = 400                # method readings one dry run may make (operational: each is a full chart reading)
MAX_SECONDS = 25.0             # wall time of one replay (operational); the rest is reported "not replayed"
LOOKBACK_EXTRA_DAYS = 5        # calendar days looked back beyond two per session to find his sessions (operational)
ONE_MINUTE = timedelta(minutes=1)

TRIGGERED, NOT_MET, NO_READING, NOT_REPLAYED = "TRIGGERED", "NOT_MET", "NO_READING", "NOT_REPLAYED"
STATE_WORDS = {TRIGGERED: "Would have triggered", NOT_MET: "Not met", NO_READING: "No reading",
               NOT_REPLAYED: "Not replayed"}
SOURCE_BARS = "bars"
SOURCE_WORDS = {**PC.SOURCE_WORDS, SOURCE_BARS: "the stored one-minute candles"}
NO_BARS = "No stored closed candles for this chart in that span."
TIME_OUT = "The dry run's time ran out before this session."
ONCE = "A plan fires once; each session is replayed as if it was armed when that session began."

PriceFn = Callable[[str, str], Awaitable[float]]


class DryRunError(ValueError):
    """A dry run that cannot be made, in plain words."""


@dataclass(frozen=True)
class Bar:
    """One stored one-minute bar in the shape the shared trigger rules read (`timestamp` = its start)."""
    timestamp: datetime
    low: float
    high: float
    close: float


@dataclass
class SessionRow:
    """One replayed session: the plan's state then (armed from `armed_at`; triggered by the bar that closed at `at`)."""
    session: str                              # the session's date (IST), ISO
    state: str                                # TRIGGERED | NOT_MET | NO_READING | NOT_REPLAYED
    armed_at: str
    at: str | None = None
    price: float | None = None                # the close of the bar that met it (the live trigger price)
    source: str | None = None
    words: str = ""
    candles: int = 0                          # closed candles replayed in this session

    def view(self) -> dict[str, Any]:
        out = asdict(self)
        out["state_words"] = STATE_WORDS[self.state]
        out["source_words"] = SOURCE_WORDS.get(self.source or "", None)
        return out


# --- pure helpers ---------------------------------------------------------------------------------------------------------

def _utc(value: Any) -> datetime:
    return PC._ts(value)  # noqa: SLF001 - the reading's own timestamp normaliser (same conversion as the live read)


def session_of(t: datetime) -> date:
    return t.astimezone(tp.IST).date()


def session_start(d: date) -> datetime:
    """The moment a session's plan is taken as armed: the start of its IST date (before its first bar)."""
    return datetime(d.year, d.month, d.day, tzinfo=tp.IST).astimezone(UTC)


def last_sessions(starts: Sequence[datetime], n: int) -> list[date]:
    """The last `n` sessions that have stored bars (oldest first) - the data says which, never a calendar guess."""
    return sorted({session_of(s) for s in starts})[-n:]


def bars_of(frame: pd.DataFrame) -> list[Bar]:
    return [Bar(_utc(t), float(lo), float(hi), float(cl))
            for t, lo, hi, cl in zip(frame["timestamp"], frame["low"], frame["high"], frame["close"], strict=True)]


def replay_touch(c: tp.Condition, bars: Sequence[Bar], armed_at: datetime, now: datetime) -> tuple[Bar | None, int]:
    """The first closed one-minute bar after `armed_at`, inside his window, whose low / high reached his level (the
    shared touch rule). A bar still forming at `now` is never read. (bar, bars read)."""
    read = 0
    for b in bars:
        end = b.timestamp + ONE_MINUTE
        if end > now:
            break                                   # forming (or later) bar: never read
        if end <= armed_at:
            continue
        read += 1
        if not tp.in_window(c, b.timestamp):
            continue                                # a tick of this minute falls outside his window
        if trigger_rules.touched(c.level, c.side, b.low, b.high):
            return b, read
    return None, read


async def replay_close(c: tp.Condition, bars: Sequence[Bar], armed_at: datetime, now: datetime, symbol: str,
                       exchange: str) -> tuple[trigger_rules.Candle | None, int]:
    """The live engine's candle-close decision (`trigger_plans.reached`, on a detached copy of the plan), made as of each
    close of his timeframe after `armed_at` - the pass right after that candle closed. (candle, candles read)."""
    minutes = int(c.timeframe_minutes or 0)
    ends = [k.end for k in trigger_rules.closed_candles(bars, minutes, now) if k.end > armed_at]
    shadow = SimpleNamespace(watch_symbol=symbol, watch_exchange=exchange, armed_at=armed_at, last_price=None,
                             last_checked_at=None, trigger_price=None)

    async def candles(_symbol: str, _exchange: str) -> Sequence[object]:
        return bars

    async def no_price(_symbol: str, _exchange: str) -> float:
        raise LookupError("a candle-close plan reads candles only")
    for n, t in enumerate(ends, start=1):
        if not tp.in_window(c, t):
            continue
        if await tp.reached(cast(Any, shadow), c, no_price, candles, t):
            return trigger_rules.close_trigger(c.level, c.side, bars, minutes, t, armed_at=armed_at), n
    return None, len(ends)


# --- the dry run ------------------------------------------------------------------------------------------------------------

async def _frame(session: Any, user: Any, key: str, tf: str, n: int, now: datetime) -> pd.DataFrame:
    from app.charts import price_action_layer as pal
    from app.charts import read_chart as RC
    from app.market_lake import own_bars
    days = min(pal.MAX_DAYS[tf], 2 * n + LOOKBACK_EXTRA_DAYS)
    private = await own_bars.scope_for(session, user, key)
    try:
        frame, _source = await pal.load_frame(session, key, tf, now - timedelta(days=days), now, private=private)
    except pal.WindowTooLarge as exc:
        raise DryRunError("This chart cannot be replayed on that timeframe.") from exc
    return RC.closed_only(frame, now)


async def _method_session(session: Any, user: Any, symbol: str, exchange: str, c: tp.Condition, steps: Sequence[datetime],
                          row: SessionRow, armed_at: datetime, deadline: float) -> int:
    """Replays one session with the live engine's reading (`plan_conditions.read`) as of each closed candle. Reads made."""
    reads, seen_source, note = 0, None, None
    for t in steps:
        if not tp.in_window(c, t):
            continue
        if time.monotonic() > deadline:
            row.state, row.words = NOT_REPLAYED, TIME_OUT
            return reads
        try:
            reading = await PC.read(session, user, symbol, exchange, c, armed_at, t)
        except Exception:  # noqa: BLE001 - no reading at this candle, as the live pass waits
            reading = PC.Reading(None)
        reads += 1
        row.candles += 1
        seen_source = seen_source or reading.source
        note = reading.note or note
        if reading.hit is not None:
            hit = reading.hit
            row.state, row.at, row.source, row.words = TRIGGERED, _utc(hit.at).isoformat(), hit.source, hit.words
            row.price = hit.price if hit.price is not None else reading.last_close
            return reads
    row.state = NOT_MET if seen_source else NO_READING
    row.source = seen_source
    row.words = note or ("" if seen_source else "No reading of this chart for those candles.")
    return reads


async def replay(session: Any, user: Any, symbol: str, exchange: str, c: tp.Condition, sessions: int,
                 now: datetime) -> dict[str, Any]:
    """The replay part of the dry run (read-only): one row per session, oldest first."""
    key = PC.instrument_key(symbol, exchange)
    tf = "1m" if c.kind == tp.PRICE else PC.read_tf(c.timeframe_minutes)
    if key is None or tf is None:
        raise DryRunError("This chart cannot be replayed on that timeframe.")
    frame = await _frame(session, user, key, tf, sessions, now)
    out: dict[str, Any] = {"timeframe": tf, "sessions": [], "reads": 0, "cut": False, "note": None}
    if not len(frame):
        out["note"] = NO_BARS
        return out
    starts = [_utc(t) for t in frame["timestamp"]]
    ends = [_utc(t) for t in frame["bar_end"]]
    days = last_sessions(starts, sessions)
    if c.kind != tp.PRICE:
        total = sum(1 for s in starts if session_of(s) in days)
        if total > MAX_READS:
            raise DryRunError(f"That is {total} candles to read; one dry run reads at most {MAX_READS}. "
                              "Choose fewer sessions or a longer candle timeframe.")
    bars = bars_of(frame) if c.kind == tp.PRICE else []
    deadline = time.monotonic() + MAX_SECONDS
    rows: list[SessionRow] = []
    for d in days:
        armed = session_start(d)
        row = SessionRow(session=d.isoformat(), state=NOT_MET, armed_at=armed.isoformat())
        rows.append(row)
        if time.monotonic() > deadline:
            row.state, row.words = NOT_REPLAYED, TIME_OUT
            out["cut"] = True
            continue
        if c.kind == tp.PRICE:
            mine = [b for b in bars if session_of(b.timestamp) == d]     # the live candles: that session's minutes
            row.source = SOURCE_BARS
            if c.mode == trigger_rules.CLOSE:
                candle, row.candles = await replay_close(c, mine, armed, now, symbol, exchange)
                if candle is not None:
                    row.state, row.at, row.price = TRIGGERED, candle.end.isoformat(), candle.close
                    row.words = (f"A {c.timeframe_minutes}-minute candle closed {'at or above' if c.side == trigger_rules.ABOVE else 'at or below'} "
                                 f"{c.level:g}.")
            else:
                bar, row.candles = replay_touch(c, mine, armed, now)
                if bar is not None:
                    row.state, row.at, row.price = TRIGGERED, (bar.timestamp + ONE_MINUTE).isoformat(), bar.close
                    row.words = f"The price touched {c.level:g} in this one-minute candle."
            continue
        steps = [e for s, e in zip(starts, ends, strict=True) if session_of(s) == d and e > armed]
        out["reads"] += await _method_session(session, user, symbol, exchange, c, steps, row, armed, deadline)
        out["cut"] = out["cut"] or row.state == NOT_REPLAYED
    out["sessions"] = [r.view() for r in rows]
    return out


def _compact(view: dict[str, Any]) -> dict[str, Any]:
    """The ticket's own answer, only the lines the card shows (every value as `assess` gave it)."""
    keys = ("label", "side", "lots", "units", "order_type", "product", "price", "price_source", "fill_price", "margin",
            "max_loss", "errors", "refusals", "warnings", "can_place", "missing")
    return {k: view.get(k) for k in keys}


async def would_send(session: Any, user: Any, plan: Any, orders: Sequence[tp.PlanOrder], choice: str | None,
                     price_lookup: PriceFn | None, now: datetime) -> dict[str, Any]:
    """What a fire would send now: each order through the ticket's own `assess` (read-only), and place / ask first."""
    from app.execution import approval
    from app.paper import fno_ticket
    chosen = choice or await tp.plan_approval_setting(session, user)
    ask_first = chosen == tp.APPROVAL or await approval.required(session, user.tenant_id, None, user.id)
    if chosen not in tp.APPROVALS:
        words = "Not set: choose place at once or ask me first before arming."
    elif ask_first:
        words = "Would ask you first (in-app and Telegram); only your Approve places it."
    else:
        words = "Would place at once, through the order ticket, with every check again then."
    views = []
    for n, o in enumerate(orders):
        ask = tp.ask_of(plan, o, n)
        ask.client_ref = None                        # a dry run holds no order reference
        view = await fno_ticket.assess(session, user, ask, price_lookup=price_lookup, now=now, origin=approval.ORIGIN_AUTOMATIC,
                                       margin_reader=fno_ticket.read_broker_margin)
        views.append(_compact(view))
    return {"choice": chosen, "ask_first": bool(ask_first), "words": words, "orders": views}


async def dry_run(session: Any, user: Any, *, underlying: str, symbol: str, exchange: str, condition: tp.Condition,
                  orders: Sequence[tp.PlanOrder], choice: str | None, sessions: int, plan_id: int | None = None,
                  price_lookup: PriceFn | None = None, now: datetime | None = None) -> dict[str, Any]:
    """The whole dry run (see the module text). Read-only; the caller rolls its session back."""
    errors = tp.condition_errors(condition)
    if errors:
        raise DryRunError(" ".join(errors))
    if not 1 <= int(sessions) <= MAX_SESSIONS:
        raise DryRunError(f"Choose how many sessions to replay: 1 to {MAX_SESSIONS}.")
    now = _utc(now or datetime.now(UTC))
    out = await replay(session, user, symbol, exchange, condition, int(sessions), now)
    plan = SimpleNamespace(id=plan_id or 0, underlying=underlying)
    sent = await would_send(session, user, plan, orders, choice, price_lookup, now) if orders else None
    rows = out["sessions"]
    return {"dry_run": True, "mode": "PAPER", "placed": 0, "plan_id": plan_id, "underlying": underlying,
            "condition_words": tp.condition_words(underlying, condition), "timeframe": out["timeframe"],
            "sessions_asked": int(sessions), "sessions": rows, "triggered": sum(1 for r in rows if r["state"] == TRIGGERED),
            "reads": out["reads"], "cut": out["cut"], "note": out["note"], "once": ONCE, "on_trigger": sent,
            "as_of": now.isoformat()}


__all__ = ["MAX_SESSIONS", "MAX_READS", "MAX_SECONDS", "DryRunError", "Bar", "SessionRow", "dry_run", "replay", "replay_touch",
           "replay_close", "would_send", "last_sessions", "session_start", "bars_of"]
