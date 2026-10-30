"""Z6: journal auto-capture - the hooks the trade pipeline calls, and the rebuild from the trade row.

Where the hooks sit (every PAPER or LIVE fill goes through them, whatever started it):
* `on_entry`       - `app/trading/persistence.persist_trade`, right after the trade row is committed. Every entry path
                     ends there: the console and webhooks (`execute_signal_for_user`), deployments (the worker),
                     multi-leg structures (`execute_structure`), the public API - and Z3's PAPER approvals, which call
                     `execute_signal_for_user` too. A caller that knows where the trade came from passes `source`
                     (`journal_source`): the TradingView webhook "WEBHOOK", the public API "API"; Z3 approvals will
                     pass "PROPOSAL" (with #173) and a scan's "Then" template "SCAN". Otherwise it is derived (a
                     deployment -> STRATEGY, else MANUAL).
* `on_marks`       - `position_monitor.monitor_open_positions`, after the sweep's marks are committed: the highest and
                     lowest price seen while open (MAE / MFE).
* `on_stop_change` - `position_monitor._apply_trade_exit_rules` after a stop change is committed (and any future
                     stop-edit endpoint): the stop history the "moved stop against position" tag reads.
* `on_exit`        - `position_monitor.close_position`, right after the exit is committed: the exit fill, costs, the
                     exit chart reference and, for an option closed on an underlying quote (the monitor's contract and
                     structure exits, the mark-price endpoint), IV at exit.

A hook never waits on the journal and never takes a second database connection on the trade path:
* the flag is read through the caller's own session (the connection it already holds) and cached for
  `FLAG_TTL_SECONDS`, so with `discipline_engine` off a hook costs one dictionary lookup and nothing else;
* with the flag on, the hook only queues the work (plain values, never the caller's ORM row) and returns. One
  background task per event loop drains the queue in order, one job at a time, each in its own session and bounded by
  `HOOK_TIMEOUT_SECONDS`; so the journal holds at most one pool connection, and only between trades. A full queue
  drops the job (logged). Errors are logged and swallowed: the journal can never fail, slow down, block or roll back a
  trade, an exit or an order (ADR-0004: exits are never blocked).
(On an engine with a single shared connection - StaticPool, the test suite's in-memory SQLite, never production - the
job runs inline instead: a second session there is the caller's own connection.)
What a hook missed (flag off at the time, a dropped job, a trade from before this feature) is rebuilt from the trade
row by `ensure_entries`.

Concurrent writers (the hooks, a weekly refresh, a second browser tab) never race into a duplicate: entries and tags
are created with INSERT ... ON CONFLICT DO NOTHING and re-read, and the price range is widened in one atomic UPDATE.

A chart snapshot is a reference, not an image: the instrument and the instant, which the chart engine redraws from
the market data lake "as of" that time (ADR-0013 keeps every bar's ingest time, so the redraw shows what was known
then). Greeks / IV at entry are solved from the entry premium and the underlying price at the signal when the
caller passes it (`underlying_entry` in the contract meta); without it they stay empty and say why.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional, Tuple, cast

from sqlalchemy import Table, bindparam, func, literal, select, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from sqlalchemy.pool import StaticPool

from app.db.models import JournalEntryRecord, PlatformControlRecord, Price, TradeRecord
from app.journal import FLAG

log = logging.getLogger(__name__)

# Where a trade came from. API = the public REST API (an API key), WEBHOOK = a TradingView alert, PROPOSAL = a Z3
# approval (passed by #173 once it lands), SCAN = a scan's "Then" template; MANUAL / STRATEGY are derived otherwise.
SOURCES = ("MANUAL", "STRATEGY", "SCAN", "PROPOSAL", "WEBHOOK", "API")

FLAG_TTL_SECONDS = 10.0       # how long a read of the flag is trusted (an operator's switch takes effect within this)
HOOK_TIMEOUT_SECONDS = 15.0   # one queued journal job may take this long, then it is cancelled (logged)
QUEUE_LIMIT = 2000            # queued jobs beyond this are dropped (logged); the trade row still has everything

ENTRIES = cast(Table, JournalEntryRecord.__table__)   # Core statements (atomic updates, upserts) target the table


def _utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def derive_source(trade: TradeRecord) -> str:
    return "STRATEGY" if trade.deployment_id is not None else "MANUAL"


def chart_ref(trade: TradeRecord, at: Optional[datetime]) -> Optional[Dict[str, Any]]:
    when = _utc(at)
    if when is None:
        return None
    return {"symbol": trade.symbol, "exchange": trade.exchange, "instrument_key": trade.instrument_key,
            "underlying_symbol": trade.underlying_symbol, "at": when.isoformat(), "redraw": "market_lake_as_of"}


def _strike_and_right(symbol: str) -> tuple:
    """'NIFTY 24500 CE 01 OCT 26' / 'NIFTY26O0124500CE' -> (24500.0, 'CE'); (None, None) when unreadable."""
    m = re.search(r"\b(\d{3,6}(?:\.\d+)?)\s*(CE|PE)\b", symbol)
    if not m:
        m = re.search(r"(\d{3,6}(?:\.\d+)?)(CE|PE)$", symbol.replace(" ", ""))
    return (float(m.group(1)), m.group(2)) if m else (None, None)


def greeks_at_entry(trade: TradeRecord, underlying_price: Optional[float]) -> Dict[str, Any]:
    """IV and Greeks of an option at its entry fill, or {"unavailable": why}."""
    return greeks_at(trade, underlying_price, float(trade.entry_price), _utc(trade.entry_time), "entry")


def greeks_at_exit(trade: TradeRecord, underlying_price: Optional[float]) -> Dict[str, Any]:
    """IV and Greeks of an option at its exit fill (the underlying quote the exit was judged on), or {"unavailable": why}."""
    if trade.exit_price is None:
        return {"unavailable": "no exit fill"}
    return greeks_at(trade, underlying_price, float(trade.exit_price), _utc(trade.exit_time), "exit")


def greeks_at(trade: TradeRecord, underlying_price: Optional[float], premium: float, at: Optional[datetime], leg: str) -> Dict[str, Any]:
    """IV and Greeks of the option at one fill (`premium` at `at`, the underlying at `underlying_price`)."""
    if (trade.instrument_kind or "UNDERLYING") != "OPTION":
        return {"unavailable": "not an option"}
    if not underlying_price or underlying_price <= 0:
        return {"unavailable": f"no underlying price at {leg}"}
    if trade.expiry is None:
        return {"unavailable": "no expiry on the trade"}
    strike, right = _strike_and_right(trade.symbol)
    if strike is None:
        return {"unavailable": f"cannot read strike/right from {trade.symbol}"}
    from app.market_data.calendar import IST
    from app.option_chain.leg_greeks import compute_leg_greeks
    from app.option_chain.models import OptionLegInput, OptionType
    when = at or datetime.now(timezone.utc)
    signed = int(trade.quantity) if trade.direction == "LONG" else -int(trade.quantity)
    try:
        solved = compute_leg_greeks(OptionLegInput(strike=strike, option_type=OptionType.CALL if right == "CE" else OptionType.PUT, quantity=signed,
                                                   underlying_ltp=float(underlying_price), expiry=trade.expiry.isoformat(),
                                                   option_ltp=float(premium), as_of=when.astimezone(IST).date().isoformat()))
    except Exception as exc:  # noqa: BLE001 - a premium outside the no-arbitrage bounds cannot be solved
        return {"unavailable": f"IV not solvable: {str(exc)[:120]}"}
    g = solved.greeks
    return {"underlying_price": round(float(underlying_price), 4), "strike": strike, "right": right,
            "iv": round(g.implied_volatility, 4), "delta": round(g.delta, 4), "gamma": round(g.gamma, 6),
            "theta": round(g.theta, 4), "vega": round(g.vega, 4)}


def _loads(raw: Optional[str], default: Any) -> Any:
    try:
        value = json.loads(raw) if raw else default
    except ValueError:
        return default
    return value if isinstance(value, type(default)) else default


def plan_of(trade: TradeRecord) -> Dict[str, Any]:
    """The levels the trade was taken on: the signal's price (or the fill when the signal had none), the initial stop,
    the targets and the planned reward:risk."""
    entry = float(trade.expected_price) if trade.expected_price else float(trade.entry_price)
    stop = float(trade.initial_stop_loss) if trade.initial_stop_loss is not None else float(trade.stop_loss)
    rr = None
    if trade.target1 is not None and stop and entry != stop:
        rr = round(abs(float(trade.target1) - entry) / abs(entry - stop), 2)
    return {"entry": round(entry, 4), "entry_source": "signal" if trade.expected_price else "fill", "stop": round(stop, 4),
            "target1": trade.target1, "target2": trade.target2, "rr": rr, "setup": trade.strategy_id, "holding": trade.holding,
            "underlying_symbol": trade.underlying_symbol, "underlying_stop_loss": trade.underlying_stop_loss,
            "underlying_target1": trade.underlying_target1, "exit_rules": _loads(trade.exit_rules, {}) or None}


def _side(direction: str, leg: str) -> str:
    opening = "BUY" if direction == "LONG" else "SELL"
    return opening if leg == "ENTRY" else ("SELL" if opening == "BUY" else "BUY")


def apply_trade(entry: JournalEntryRecord, trade: TradeRecord, *, source: Optional[str] = None) -> None:
    """Fills what the trade row can tell: plan (once), entry/exit fills, chart references, the price range seen.
    Idempotent: calling it twice changes nothing the second time."""
    if source and source.upper() in SOURCES:
        entry.source = source.upper()
    elif not entry.source:
        entry.source = derive_source(trade)
    entry.mode = trade.mode
    if not _loads(entry.plan_json, {}):
        entry.plan_json = json.dumps(plan_of(trade), sort_keys=True, default=str)
    fills: List[Dict[str, Any]] = _loads(entry.fills_json, [])
    legs = {f.get("leg") for f in fills}
    entry_time = _utc(trade.entry_time)
    if "ENTRY" not in legs:
        fills.append({"leg": "ENTRY", "side": _side(trade.direction, "ENTRY"), "price": float(trade.entry_price), "quantity": float(trade.quantity),
                      "at": entry_time.isoformat() if entry_time else None, "order_id": trade.broker_order_id,
                      "slippage": trade.slippage, "latency_ms": trade.entry_latency_ms})
    exit_time = _utc(trade.exit_time)
    if trade.exit_price is not None and exit_time is not None and "EXIT" not in legs:
        fills.append({"leg": "EXIT", "side": _side(trade.direction, "EXIT"), "price": float(trade.exit_price), "quantity": float(trade.quantity),
                      "at": exit_time.isoformat(), "order_id": trade.exit_order_id, "reason": trade.exit_reason})
    entry.fills_json = json.dumps(fills, sort_keys=True, default=str)
    if entry.chart_entry_json is None:
        ref = chart_ref(trade, trade.entry_time)
        entry.chart_entry_json = json.dumps(ref, sort_keys=True) if ref else None
    if entry.chart_exit_json is None and exit_time is not None:
        ref = chart_ref(trade, exit_time)
        entry.chart_exit_json = json.dumps(ref, sort_keys=True) if ref else None


def trade_prices(trade: TradeRecord) -> List[float]:
    """The prices the trade row says the position traded at (entry, exit, the best price the monitor recorded)."""
    raw = [trade.entry_price, trade.exit_price, trade.best_price]
    return [float(p) for p in raw if p is not None and float(p) > 0]


# --- writes that are safe under concurrency ---------------------------------------------------------------------------

def dialect_insert(session: AsyncSession, table: Any) -> Any:
    """INSERT for the session's database with ON CONFLICT support (PostgreSQL in production, SQLite in the tests)."""
    name = session.get_bind().dialect.name
    if name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as pg_insert
        return pg_insert(table)
    if name == "sqlite":
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert
        return sqlite_insert(table)
    raise RuntimeError(f"journal upserts are not implemented for {name}")


async def widen(session: AsyncSession, trade_id: int, prices: Iterable[float]) -> None:
    """Widens the highest / lowest price seen in one atomic UPDATE (GREATEST / LEAST over the stored value), so two
    writers never lose each other's extreme."""
    valid = [float(p) for p in prices if p is not None and float(p) > 0]
    if not valid:
        return
    hi, lo = literal(max(valid), Price), literal(min(valid), Price)
    postgres = session.get_bind().dialect.name == "postgresql"
    greatest, least = (func.greatest, func.least) if postgres else (func.max, func.min)   # SQLite: max()/min() with 2 args
    col = ENTRIES.c
    await session.execute(update(ENTRIES).where(col.trade_id == trade_id).values(
        high_price=greatest(func.coalesce(col.high_price, hi), hi), low_price=least(func.coalesce(col.low_price, lo), lo)))


async def widen_many(session: AsyncSession, prices_by_trade: Dict[int, Iterable[float]]) -> None:
    """AUD-G2.1: `widen` for many trades in ONE executemany round trip (the same atomic UPDATE per row, so two writers
    still never lose each other's extreme) - a journal read no longer sends one UPDATE per trade."""
    params = []
    for trade_id, prices in prices_by_trade.items():
        valid = [float(p) for p in prices if p is not None and float(p) > 0]
        if valid:
            params.append({"w_trade_id": trade_id, "w_hi": max(valid), "w_lo": min(valid)})
    if not params:
        return
    postgres = session.get_bind().dialect.name == "postgresql"
    greatest, least = (func.greatest, func.least) if postgres else (func.max, func.min)   # SQLite: max()/min() with 2 args
    col = ENTRIES.c
    hi, lo = bindparam("w_hi", type_=Price), bindparam("w_lo", type_=Price)
    stmt = update(ENTRIES).where(col.trade_id == bindparam("w_trade_id")).values(
        high_price=greatest(func.coalesce(col.high_price, hi), hi), low_price=least(func.coalesce(col.low_price, lo), lo))
    await session.execute(stmt, params)


def new_entry_values(trade: TradeRecord, source: Optional[str] = None) -> Dict[str, Any]:
    """The columns of a fresh journal entry for `trade` (what the trade row alone can tell at creation)."""
    now = datetime.now(timezone.utc)
    chosen = source.upper() if source and source.upper() in SOURCES else derive_source(trade)
    return {"tenant_id": trade.tenant_id, "user_id": trade.user_id, "trade_id": trade.id, "source": chosen, "mode": trade.mode,
            "plan_json": json.dumps(plan_of(trade), sort_keys=True, default=str), "fills_json": "[]", "stop_history_json": "[]",
            "captured_at": now, "updated_at": now}


async def ensure_entries(session: AsyncSession, trades: Iterable[TradeRecord], *, source: Optional[str] = None,
                         marks: Optional[Dict[int, float]] = None) -> Dict[int, JournalEntryRecord]:
    """The journal entry of every trade, created or completed from the trade row (the caller commits).

    Safe against a concurrent writer: missing entries are inserted with ON CONFLICT (trade_id) DO NOTHING, the price
    range is widened atomically, and the rows are re-read fresh; only then are the trade-derived columns filled in
    (the same values whichever writer gets there first)."""
    trades = list(trades)
    if not trades:
        return {}
    ids = [t.id for t in trades]
    have = set(await session.scalars(select(JournalEntryRecord.trade_id).where(JournalEntryRecord.trade_id.in_(ids))))
    missing = [new_entry_values(t, source) for t in trades if t.id not in have]
    if missing:
        stmt = dialect_insert(session, ENTRIES).on_conflict_do_nothing(index_elements=["trade_id"])
        await session.execute(stmt, missing)
    await widen_many(session, {trade.id: trade_prices(trade) + ([float(marks[trade.id])] if marks and trade.id in marks else [])
                               for trade in trades})
    rows = await session.scalars(select(JournalEntryRecord).where(JournalEntryRecord.trade_id.in_(ids))
                                 .execution_options(populate_existing=True))
    existing = {e.trade_id: e for e in rows}
    out: Dict[int, JournalEntryRecord] = {}
    for trade in trades:
        entry = existing.get(trade.id)
        if entry is None:          # the trade was deleted between the insert and the read
            continue
        apply_trade(entry, trade, source=source)
        out[trade.id] = entry
    return out


# --- the flag, read without a second connection -----------------------------------------------------------------------

@dataclass
class _FlagState:
    expires: float
    on: bool
    tenants: frozenset


_flag_state: Optional[_FlagState] = None


def reset_flag_cache() -> None:
    """Forgets the cached flag (tests, and an operator tool that wants the switch to apply at once)."""
    global _flag_state
    _flag_state = None


async def _flag_on(session: AsyncSession, tenant_id: int) -> bool:
    """Whether `discipline_engine` is on for the tenant. Read through the CALLER's session - the connection it already
    holds - at most once per `FLAG_TTL_SECONDS`; never opens a session or takes a pool connection of its own. Same rule
    as `controls.flag_enabled` (absent = the default, which is OFF for this flag). An unreadable flag counts as off."""
    global _flag_state
    now = time.monotonic()
    state = _flag_state
    if state is None or state.expires <= now:
        from app.platform.controls import DEFAULT_OFF_FLAGS, KEY_FEATURE_FLAGS
        with session.no_autoflush:
            raw = await session.scalar(select(PlatformControlRecord.value_json).where(PlatformControlRecord.key == KEY_FEATURE_FLAGS))
        entry = (_loads(raw, {}).get("flags") or {}).get(FLAG) or {}
        state = _FlagState(expires=now + FLAG_TTL_SECONDS, on=bool(entry.get("on", FLAG not in DEFAULT_OFF_FLAGS)),
                           tenants=frozenset(int(t) for t in entry.get("tenants") or []))
        _flag_state = state
    return state.on or tenant_id in state.tenants


# --- the background runner: the trade path only enqueues ---------------------------------------------------------------

Job = Callable[[AsyncSession], Awaitable[None]]


@dataclass
class _Runner:
    loop: asyncio.AbstractEventLoop
    queue: "asyncio.Queue[Tuple[AsyncEngine, int, str, Job]]"
    task: "asyncio.Task[None]"


_runner: Optional[_Runner] = None


async def _execute(bind: AsyncEngine, work: Job) -> None:
    async with AsyncSession(bind=bind, expire_on_commit=False) as own:
        await work(own)
        await own.commit()


async def _drain_forever(queue: "asyncio.Queue[Tuple[AsyncEngine, int, str, Job]]") -> None:
    while True:
        bind, tenant_id, what, work = await queue.get()
        try:
            await asyncio.wait_for(_execute(bind, work), timeout=HOOK_TIMEOUT_SECONDS)
        except Exception as exc:  # noqa: BLE001 - the journal never fails anything (a timeout included)
            log.warning("Journal capture (%s) skipped for tenant %s: %s", what, tenant_id, exc or type(exc).__name__)
        finally:
            queue.task_done()


def _submit(bind: AsyncEngine, tenant_id: int, what: str, work: Job) -> None:
    """Queues one journal job and returns at once. The runner task is held here (a strong reference) and restarted if
    the event loop changed or it stopped."""
    global _runner
    loop = asyncio.get_running_loop()
    runner = _runner
    if runner is None or runner.loop is not loop or runner.task.done():
        queue: "asyncio.Queue[Tuple[AsyncEngine, int, str, Job]]" = asyncio.Queue(maxsize=QUEUE_LIMIT)
        runner = _Runner(loop=loop, queue=queue, task=loop.create_task(_drain_forever(queue), name="journal-capture"))
        _runner = runner
    try:
        runner.queue.put_nowait((bind, tenant_id, what, work))
    except asyncio.QueueFull:
        log.warning("Journal capture (%s) dropped for tenant %s: %d jobs already queued", what, tenant_id, QUEUE_LIMIT)


async def drain(max_wait: float = 10.0) -> None:
    """Waits until every queued journal job of this event loop has run (tests; a graceful shutdown). Never raises."""
    runner = _runner
    if runner is None or runner.loop is not asyncio.get_running_loop() or runner.task.done():
        return
    try:
        await asyncio.wait_for(runner.queue.join(), timeout=max_wait)
    except Exception as exc:  # noqa: BLE001
        log.warning("Journal capture queue not drained: %s", exc or type(exc).__name__)


def _single_connection(bind: AsyncEngine) -> bool:
    """An engine whose pool is ONE shared connection (StaticPool: the in-memory SQLite of the test suite, never
    production). A second session there is the same connection, so a job running beside the caller would interleave
    with - and could roll back or close - the caller's transaction; the job runs inline instead."""
    return isinstance(bind.sync_engine.pool, StaticPool)


async def _run(session: AsyncSession, tenant_id: int, what: str, work: Job) -> None:
    """The guard every hook goes through: flag (no new connection), then enqueue. Never raises, never waits on a job
    (except on a single-connection engine, see `_single_connection`)."""
    try:
        if not await _flag_on(session, tenant_id):
            return
        bind = session.bind
        if not isinstance(bind, AsyncEngine):
            log.warning("Journal capture (%s) skipped for tenant %s: the session has no engine bind", what, tenant_id)
            return
        if _single_connection(bind):
            await asyncio.wait_for(_execute(bind, work), timeout=HOOK_TIMEOUT_SECONDS)
            return
        _submit(bind, tenant_id, what, work)
    except Exception as exc:  # noqa: BLE001 - the journal never fails the trade
        log.warning("Journal capture (%s) skipped for tenant %s: %s", what, tenant_id, exc)


# --- the hooks (flag-gated on the caller's connection, queued, never raise) --------------------------------------------

def _ids(trade: TradeRecord) -> Optional[tuple]:
    """(trade id, tenant id), or None when the caller's row cannot be read (the hook then does nothing)."""
    try:
        return int(trade.id), int(trade.tenant_id)
    except Exception as exc:  # noqa: BLE001 - never raise into the trade path
        log.warning("Journal capture skipped: trade row unreadable (%s)", exc)
        return None


async def _entry_for(own: AsyncSession, trade_id: int, *, source: Optional[str] = None,
                     marks: Optional[Dict[int, float]] = None) -> Tuple[Optional[TradeRecord], Optional[JournalEntryRecord]]:
    row = await own.get(TradeRecord, trade_id)
    if row is None:
        return None, None
    return row, (await ensure_entries(own, [row], source=source, marks=marks)).get(trade_id)


async def _set_greeks(own: AsyncSession, trade_id: int, leg: str, greeks: Dict[str, Any]) -> None:
    """Writes IV (and, at entry, the Greeks) once: `WHERE ... IS NULL`, so a second writer never overwrites the first."""
    col = ENTRIES.c
    if leg == "entry":
        await own.execute(update(ENTRIES).where(col.trade_id == trade_id, col.greeks_entry_json.is_(None))
                          .values(greeks_entry_json=json.dumps(greeks, sort_keys=True), iv_entry=greeks.get("iv")))
    elif greeks.get("iv") is not None:
        await own.execute(update(ENTRIES).where(col.trade_id == trade_id, col.iv_exit.is_(None))
                          .values(iv_exit=greeks.get("iv")))


async def on_entry(session: AsyncSession, trade: TradeRecord, *, source: Optional[str] = None,
                   underlying_price: Optional[float] = None) -> None:
    ids = _ids(trade)
    if ids is None:
        return
    trade_id, tenant_id = ids

    async def work(own: AsyncSession) -> None:
        row, entry = await _entry_for(own, trade_id, source=source)
        if row is not None and entry is not None and (row.instrument_kind or "UNDERLYING") == "OPTION":
            await _set_greeks(own, trade_id, "entry", greeks_at_entry(row, underlying_price))
    await _run(session, tenant_id, "entry", work)


async def on_exit(session: AsyncSession, trade: TradeRecord, *, underlying_price: Optional[float] = None) -> None:
    """`underlying_price`: the underlying quote the exit was judged on (options), for IV at exit."""
    ids = _ids(trade)
    if ids is None:
        return
    trade_id, tenant_id = ids

    async def work(own: AsyncSession) -> None:
        row, entry = await _entry_for(own, trade_id)
        if row is not None and entry is not None and (row.instrument_kind or "UNDERLYING") == "OPTION" and underlying_price:
            await _set_greeks(own, trade_id, "exit", greeks_at_exit(row, underlying_price))
    await _run(session, tenant_id, "exit", work)


async def on_marks(session: AsyncSession, tenant_id: int, prices: Dict[int, float]) -> None:
    if not prices:
        return
    marks = dict(prices)

    async def work(own: AsyncSession) -> None:
        trades = list(await own.scalars(select(TradeRecord).where(TradeRecord.id.in_(list(marks)), TradeRecord.tenant_id == tenant_id)))
        await ensure_entries(own, trades, marks=marks)
    await _run(session, tenant_id, "marks", work)


async def on_stop_change(session: AsyncSession, trade: TradeRecord, old: float, new: float, *, source: str = "rule",
                         at: Optional[datetime] = None) -> None:
    ids = _ids(trade)
    if ids is None:
        return
    trade_id, tenant_id = ids
    when = _utc(at) or datetime.now(timezone.utc)

    async def work(own: AsyncSession) -> None:
        _, entry = await _entry_for(own, trade_id)
        if entry is None:
            return
        # Only the runner appends to the stop history, one job at a time, so this read-modify-write cannot interleave.
        history: List[Dict[str, Any]] = _loads(entry.stop_history_json, [])
        history.append({"at": when.isoformat(), "from": float(old), "to": float(new), "source": source[:40]})
        entry.stop_history_json = json.dumps(history, sort_keys=True)
    await _run(session, tenant_id, "stop change", work)
