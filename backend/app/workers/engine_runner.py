"""M66 EN-3a: the signal engine runner - SIGNALS ONLY (no order, no deployment type, no approval step; EN-3b adds the
bot side).

Where it runs: inside the trading worker's own scheduling, never as a second resident loop. Every worker cycle starts
`run_due` as one background task with its own database session (`TradingWorker._spawn_engine_runner`; one at a time per
worker, like the Vision drain), so a slow read never holds up the cycle, its exits included (ADR-0004).

What it does, per organisation whose flag `trade_engine` is ON (admin / owner only, OFF by default) and only while the
private engine is installed (`loader.load()` is not None; otherwise a no-op, never a crash):

1. The runner settings (`platform_controls` row `trade_engine_runner`, admin-set, operational only): whose signals
   (`user_id`), the symbols, the signal timeframe, the small delay after a bar's close, the grace window, the bounded
   attempts, the data window and the user's own engine settings handed to the engine as they are.
2. Per symbol: the signal timeframe's last bar closed by (now - delay), from the market calendar. A bar is read once;
   a bar whose data is not complete yet is tried again on a later cycle, at most `max_attempts` times and only within
   `grace_seconds` after (close + delay), then given up with its reason. A per-symbol lock keeps two runs of one symbol
   apart. These are operational settings only: nothing here frames the chart reading by the clock.
3. The input is EN-2's `build_request` at that bar's close (`now` = the bar's END, so no bar that closes later is ever
   handed in - no look-ahead). Any data problem - a gap the adapter found, or one the engine reports - means no signal
   for that bar, and the run record says why (the gap lines in plain words, for the admin).
4. Each signal on the read bar goes into ATP's EXISTING signals table (`signal_history`) through the existing ingest
   path (`app.trading.persistence.persist_signal_history`). Idempotent: one dedupe row per (organisation, symbol,
   timeframe, bar END, engine model) in `runtime_counters` (its unique key), so the same bar never gives a second row,
   also after a restart or a replay.
5. The shown text is written by ATP from the card's token ids (`app.signals.language.format_signal`, the user's own
   surface: his symbol, the area as a price range; structure words only). It must pass `language.check`; a card ATP
   has no phrase for, or one that fails the check, is stored without a text line (never rewritten). No entry, stop,
   target or ratio is stored on the row. The internal references (model, entry zone, invalidation, evidence) stay in
   the dedupe row, which no user surface reads.

PAPER only: nothing here places, blocks or changes an order or an exit (ADR-0004); the AI is never involved
(ADR-0006). No new table and no migration: `signal_history`, `runtime_counters` and `platform_controls` exist.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache.client import cache_release_lock, cache_try_lock
from app.core.enums import SignalDirection, SignalGrade
from app.core.models import Signal as AtpSignal
from app.db.models import PlatformControlRecord, RuntimeCounterRecord, User
from app.engine_provider import frames as F
from app.engine_provider import loader
from app.engine_provider import sources as S
from app.engine_provider.contract import INTERFACE_VERSION, EngineResult, TradeEngineProvider
from app.engine_provider.contract import Signal as EngineSignal
from app.signal_scoring.models import EnrichedSignal
from app.signals import language
from app.trading.persistence import persist_signal_history

logger = logging.getLogger(__name__)

FLAG = "trade_engine"
STRATEGY_ID = "trade_engine"                 # signal_history.strategy_id of an engine signal
DISPLAY_NAME = "Signal engine"               # what the Signals inbox shows for it
SETTINGS_KEY = "trade_engine_runner"         # platform_controls: {"tenants": {"<tenant id>": {...}}}
STATE = "engine_run_state"                   # runtime_counters name: one row per (organisation, timeframe, symbol)
DEDUPE = "engine_signal"                     # runtime_counters name: one row per signal (the dedupe key)
LOCK_PREFIX = "atp:trade_engine:symbol:"
RUNS_KEPT = 20                               # run records kept per symbol (newest last)
LINES_KEPT = 20                              # gap / note lines kept per run record
DUE_LOOKBACK_DAYS = 14                       # how far back the calendar is searched for the last closed bar

# Operational defaults (owner settings; never a reading rule). The delay and the grace follow Trade's resident loop.
DEFAULTS: dict[str, Any] = {"signal_timeframe": "5m", "delay_seconds": 3, "grace_seconds": 120, "max_attempts": 3,
                            "intraday_days": 10, "daily_days": 400, "read_timeout_seconds": 30}
LIMITS: dict[str, tuple[float, float]] = {"delay_seconds": (0, 600), "grace_seconds": (0, 3600), "max_attempts": (1, 10),
                                          "intraday_days": (1, 3650), "daily_days": (1, 3650), "read_timeout_seconds": (1, 600)}
SIGNAL_TIMEFRAMES = (*F.MINUTES, F.DAILY)

# Run statuses (admin view).
SIGNAL = "signal"
NO_SIGNAL = "no_signal"
DATA_GAP = "data_gap"
FAILED = "failed"
MISSED = "missed"

DIRECTION_OF_SIDE = {"buyer": SignalDirection.LONG, "seller": SignalDirection.SHORT}

BrokerFor = Callable[[AsyncSession, int, User, datetime], Awaitable[Any]]
Collect = Callable[..., Awaitable[tuple[F.SymbolData, list[str]]]]


# --- settings ------------------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Watch:
    """One symbol the runner reads: ATP's lake key, its exchange, the broker's name of it and the name shown."""
    key: str
    exchange: str = "NSE"
    broker_symbol: Optional[str] = None
    name: str = ""

    @property
    def shown(self) -> str:
        return (self.name or self.broker_symbol or self.key.split(":")[-1]).strip().upper()[:50]


@dataclass(frozen=True)
class RunnerSettings:
    tenant_id: int
    user_id: int
    symbols: tuple[Watch, ...]
    signal_timeframe: str = DEFAULTS["signal_timeframe"]
    delay_seconds: float = DEFAULTS["delay_seconds"]
    grace_seconds: float = DEFAULTS["grace_seconds"]
    max_attempts: int = DEFAULTS["max_attempts"]
    intraday_days: int = DEFAULTS["intraday_days"]
    daily_days: int = DEFAULTS["daily_days"]
    read_timeout_seconds: float = DEFAULTS["read_timeout_seconds"]
    engine_settings: Mapping[str, Any] = field(default_factory=dict)


class SettingsProblem(ValueError):
    """Runner settings that cannot be used (the message is plain words for the admin)."""


def parse_settings(tenant_id: int, raw: Any) -> RunnerSettings:
    """One organisation's stored runner settings -> RunnerSettings (defaults for what is not set); SettingsProblem when
    they cannot be used."""
    if not isinstance(raw, Mapping):
        raise SettingsProblem("the runner settings are not a set of values")
    user_id = raw.get("user_id")
    if not isinstance(user_id, int) or isinstance(user_id, bool) or user_id <= 0:
        raise SettingsProblem("whose signals these are (user_id) is not set")
    watches: list[Watch] = []
    for item in raw.get("symbols") or []:
        if not isinstance(item, Mapping) or not str(item.get("key") or "").strip():
            raise SettingsProblem("a symbol has no key")
        watches.append(Watch(key=str(item["key"]).strip(), exchange=str(item.get("exchange") or "NSE").strip().upper(),
                             broker_symbol=(str(item["broker_symbol"]).strip() or None) if item.get("broker_symbol") else None,
                             name=str(item.get("name") or "").strip()))
    if len({w.key for w in watches}) != len(watches):
        raise SettingsProblem("a symbol is listed twice")
    tf = str(raw.get("signal_timeframe") or DEFAULTS["signal_timeframe"])
    if tf not in SIGNAL_TIMEFRAMES:
        raise SettingsProblem(f"the signal timeframe {tf!r} is not one ATP builds")
    numbers: dict[str, Any] = {}
    for name, (low, high) in LIMITS.items():
        value = raw.get(name, DEFAULTS[name])
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= value <= high:
            raise SettingsProblem(f"{name} must be a number from {low:g} to {high:g}")
        numbers[name] = int(value) if name in ("max_attempts", "intraday_days", "daily_days") else float(value)
    engine_settings = raw.get("engine_settings") or {}
    if not isinstance(engine_settings, Mapping):
        raise SettingsProblem("the engine settings are not a set of values")
    return RunnerSettings(tenant_id=int(tenant_id), user_id=user_id, symbols=tuple(watches), signal_timeframe=tf,
                          engine_settings=dict(engine_settings), **numbers)


async def _stored_settings(session: AsyncSession) -> dict[str, Any]:
    row = await session.get(PlatformControlRecord, SETTINGS_KEY)
    try:
        value = json.loads(row.value_json or "{}") if row is not None else {}
    except ValueError:
        value = {}
    tenants = value.get("tenants") if isinstance(value, dict) else None
    return dict(tenants) if isinstance(tenants, dict) else {}


async def save_settings(session: AsyncSession, tenant_id: int, values: Mapping[str, Any], user: Optional[User]) -> RunnerSettings:
    """Store one organisation's runner settings (validated first; the caller checks the admin / owner role). Commits."""
    parsed = parse_settings(tenant_id, values)
    tenants = await _stored_settings(session)
    tenants[str(int(tenant_id))] = dict(values)
    row = await session.get(PlatformControlRecord, SETTINGS_KEY)
    if row is None:
        row = PlatformControlRecord(key=SETTINGS_KEY)
        session.add(row)
    row.value_json = json.dumps({"tenants": tenants})
    row.updated_by = user.id if user is not None else None
    await session.commit()
    return parsed


# --- the bar that is due ---------------------------------------------------------------------------------------------------

def bar_end(start: datetime, timeframe: str, cal: Any) -> datetime:
    """The END of the continuous-session bar that starts at `start` (cut short by the day's close; Daily = the close)."""
    close = F.session_close(cal, start.date())
    if timeframe == F.DAILY:
        return close
    return min(start + timedelta(minutes=F.MINUTES[timeframe]), close)


def due_bar(cal: Any, timeframe: str, now: datetime, delay_seconds: float) -> Optional[datetime]:
    """The END of the last `timeframe` bar that closed by (now - delay), from the calendar; None when there is none in
    the look-back. A bar that is still running is never due."""
    cut = now.astimezone(F.IST) - timedelta(seconds=delay_seconds)
    first = F.session_open(cal, (cut - timedelta(days=DUE_LOOKBACK_DAYS)).date())
    starts = F.expected_starts(timeframe, first, cut, cal)
    return bar_end(starts[-1], timeframe, cal) if starts else None


def window_starts(cal: Any, timeframes: Sequence[str], as_of: datetime, cfg: RunnerSettings) -> dict[str, datetime]:
    """{timeframe: the window's first bar START}: the session open `daily_days` (Daily) or `intraday_days` back."""
    day = as_of.astimezone(F.IST).date()
    return {tf: F.session_open(cal, day - timedelta(days=cfg.daily_days if tf == F.DAILY else cfg.intraday_days))
            for tf in timeframes}


# --- records (runtime_counters) --------------------------------------------------------------------------------------------

def _iso(ts: Optional[datetime]) -> Optional[str]:
    return ts.astimezone(timezone.utc).isoformat() if ts is not None else None


def _hash(text: str, size: int = 24) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:size]


def state_key(tenant_id: int, timeframe: str, symbol_key: str) -> str:
    return f"{int(tenant_id)}:{timeframe}:{_hash(symbol_key)}"


def dedupe_key(tenant_id: int, symbol_key: str, timeframe: str, end: datetime, model_id: str) -> str:
    """The signal's idempotency key: organisation + symbol + timeframe + bar END + engine model (hashed; never shown)."""
    return _hash(f"{int(tenant_id)}|{symbol_key}|{timeframe}|{_iso(end)}|{model_id}", 64)


async def _counter(session: AsyncSession, name: str, key: str) -> Optional[RuntimeCounterRecord]:
    return (await session.scalars(select(RuntimeCounterRecord).where(RuntimeCounterRecord.name == name,
                                                                     RuntimeCounterRecord.key == key).limit(1))).first()


def _load(row: Optional[RuntimeCounterRecord]) -> dict[str, Any]:
    if row is None:
        return {}
    try:
        value = json.loads(row.value or "null")
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


async def _save_state(session: AsyncSession, key: str, value: dict[str, Any]) -> None:
    row = await _counter(session, STATE, key)
    if row is None:
        session.add(RuntimeCounterRecord(name=STATE, key=key, value=json.dumps(value)))
    else:
        row.value = json.dumps(value)


async def run_records(session: AsyncSession, tenant_id: int) -> list[dict[str, Any]]:
    """The admin's view: per symbol and timeframe the runner's state and its latest run records (gap lines in plain
    words). Never a user surface."""
    rows = await session.scalars(select(RuntimeCounterRecord).where(RuntimeCounterRecord.name == STATE,
                                                                    RuntimeCounterRecord.key.startswith(f"{int(tenant_id)}:")))
    out = [_load(r) for r in rows]
    return sorted((v for v in out if v), key=lambda v: (str(v.get("symbol")), str(v.get("timeframe"))))


async def internal_signal(session: AsyncSession, key: str) -> dict[str, Any]:
    """The internal references of one engine signal (model, entry zone, invalidation, evidence) - for the server only
    (EN-3b reads them); never shown."""
    return _load(await _counter(session, DEDUPE, key))


# --- the per-symbol lock -----------------------------------------------------------------------------------------------------

_held: set[str] = set()


def lock_name(tenant_id: int, symbol_key: str) -> str:
    return f"{LOCK_PREFIX}{int(tenant_id)}:{_hash(symbol_key)}"


async def _lock(name: str, holder: str, ttl_seconds: int) -> bool:
    """In this process and across replicas (Redis). Redis unreachable: this process's lock only (the worker's replica
    lock already keeps a second replica's cycle out)."""
    if name in _held:
        return False
    acquired, _reachable = await cache_try_lock(name, holder, ttl_seconds)
    if not acquired:
        return False
    _held.add(name)
    return True


async def _unlock(name: str, holder: str) -> None:
    _held.discard(name)
    await cache_release_lock(name, holder)


# --- the card text -------------------------------------------------------------------------------------------------------------

def card_line(signal: EngineSignal, result: EngineResult, shown: str) -> tuple[Optional[str], Optional[str]]:
    """(the text shown on the user's own signal, or None; a note for the admin when no text is shown). ATP writes the
    words from the card's token ids; the area range is the card's own, else the band of the area the signal refers to;
    the invalidation is never part of it."""
    card = signal.card
    area_range = card.area_range
    if area_range is None and signal.area_id is not None:
        band = next((a.band for a in result.areas if a.area_id == signal.area_id), None)
        area_range = (band.low, band.high) if band is not None else None
    timeframe = card.timeframe or (signal.timeframe if signal.timeframe in language.TIMEFRAMES else None)
    card = replace(card, symbol=shown, side=card.side or signal.side, timeframe=timeframe, area_range=area_range)
    try:
        text = language.format_signal(card, "signal_own")
    except ValueError as exc:
        return None, f"card text not shown: {exc}"
    problems = language.check(text, "signal_own", own_symbols=[shown])
    if problems:
        return None, "card text not shown: " + ", ".join(f"{p.kind} {p.detail}" for p in problems)
    return text, None


# --- one run -----------------------------------------------------------------------------------------------------------------------

@dataclass
class Outcome:
    status: str
    reason: str
    problems: tuple[str, ...] = ()
    gaps: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    signals: list[int] = field(default_factory=list)
    duplicates: int = 0
    engine: str = ""

    @property
    def retryable(self) -> bool:
        return self.status in (DATA_GAP, FAILED)


@dataclass(frozen=True)
class SymbolRun:
    """What one symbol did on this cycle: `state` is "idle" (no closed bar in the look-back), "done" (the bar was read),
    "busy" (another run holds the symbol), "missed" (reached after its grace window), "given_up" (attempts or grace used
    up) or the read's status; `outcome` is set when the bar was read on this cycle."""
    state: str
    bar_end: Optional[datetime] = None
    outcome: Optional[Outcome] = None
    final: bool = True


@dataclass
class RunReport:
    """What one `run_due` did (counts; the details are in the run records)."""
    skipped: Optional[str] = None
    tenants: int = 0
    runs: int = 0
    signals: int = 0
    duplicates: int = 0
    busy: int = 0
    problems: list[str] = field(default_factory=list)


async def _emit(session: AsyncSession, cfg: RunnerSettings, watch: Watch, user: User, signal: EngineSignal, result: EngineResult,
                outcome: Outcome) -> None:
    """One engine signal -> one signal_history row, once (the dedupe row commits with it)."""
    key = dedupe_key(cfg.tenant_id, watch.key, signal.timeframe, signal.bar_end, signal.model_id)
    if await _counter(session, DEDUPE, key) is not None:
        outcome.duplicates += 1
        return
    text, note = card_line(signal, result, watch.shown)
    if note:
        outcome.notes.append(note)
    internal = {"tenant_id": cfg.tenant_id, "symbol": watch.key, "timeframe": signal.timeframe, "bar_end": _iso(signal.bar_end),
                "model_id": signal.model_id, "instance_id": signal.instance_id, "side": signal.side, "area_id": signal.area_id,
                "entry_zone": [signal.entry_zone.low, signal.entry_zone.high], "invalidation": signal.invalidation,
                "evidence": [{"token": e.token, "present": bool(e.present)} for e in signal.evidence],
                "engine": result.version, "history_id": None}
    try:
        async with session.begin_nested():
            session.add(RuntimeCounterRecord(name=DEDUPE, key=key, value=json.dumps(internal)))
            await session.flush()
    except IntegrityError:                      # another run wrote the same bar's signal first
        outcome.duplicates += 1
        return
    atp_signal = AtpSignal(symbol=watch.shown, strategy_id=STRATEGY_ID, strategy_name=DISPLAY_NAME,
                           direction=DIRECTION_OF_SIDE[signal.side], timestamp=signal.bar_end.astimezone(timezone.utc), grade=SignalGrade.VALID,
                           reasons=[text] if text else [], timeframe_combo=signal.timeframe)
    enriched = EnrichedSignal(signal=atp_signal, composite_score=0, grade=SignalGrade.VALID, breakdown={}, confirmations=[])
    record = await persist_signal_history(session, user, enriched)       # commits the row and its dedupe row together
    row = await _counter(session, DEDUPE, key)
    if row is not None:
        row.value = json.dumps({**internal, "history_id": record.id})
    outcome.signals.append(record.id)


async def _attempt(session: AsyncSession, cfg: RunnerSettings, watch: Watch, engine: TradeEngineProvider, user: User, broker: Any,
                   due: datetime, now: datetime, cal: Any, collect: Collect) -> Outcome:
    tf = cfg.signal_timeframe
    timeframes = list(dict.fromkeys([tf, *engine.timeframes]))
    if tf not in engine.timeframes:
        return Outcome(FAILED, f"the engine does not read the {tf} timeframe (runner settings)")
    starts = window_starts(cal, timeframes, due, cfg)
    from app.market_lake import own_bars
    scope = await own_bars.scope_for(session, user, watch.key)
    data, notes = await collect(session, key=watch.key, exchange=watch.exchange, timeframes=timeframes, starts=starts, now=now,
                                cal=cal, scope=scope, broker=broker, broker_symbol=watch.broker_symbol)
    # `now` = the due bar's END: every frame holds only bars closed by then (no look-ahead), and a bar the source has not
    # delivered yet is a gap of the signal frame, not a quietly older read.
    built = F.build_request(data, timeframes, tf, due, starts, cal, settings=cfg.engine_settings)
    gaps = [g.text for g in built.gaps][:LINES_KEPT]
    notes = list(notes)[:LINES_KEPT]
    if built.request is None:
        return Outcome(DATA_GAP, "no closed bar of the signal timeframe to read", built.data_problems, gaps, notes)
    if built.data_problems:
        return Outcome(DATA_GAP, "data gaps: no signal is read on this bar", built.data_problems, gaps, notes)
    request = built.request
    try:
        result = await asyncio.wait_for(asyncio.to_thread(engine.read, request), timeout=cfg.read_timeout_seconds)
    except asyncio.TimeoutError:
        return Outcome(FAILED, "the engine did not answer in time", (), gaps, notes)
    except Exception as exc:  # noqa: BLE001 - a failing engine read is this bar's problem only
        logger.warning("Signal engine read failed for %s (%s)", watch.shown, exc.__class__.__name__)
        return Outcome(FAILED, f"the engine read failed ({exc.__class__.__name__})", (), gaps, notes)
    if not isinstance(result, EngineResult) or result.interface != INTERFACE_VERSION:
        return Outcome(FAILED, "the engine's answer does not follow ATP's engine interface", (), gaps, notes)
    merged = F.merge_problems(result, built.gaps)
    if merged.data_problems:
        return Outcome(DATA_GAP, "the engine reported data problems: no signal on this bar", merged.data_problems, gaps, notes,
                       engine=result.version)
    on_bar = [s for s in result.signals if isinstance(s, EngineSignal) and s.timeframe == tf and s.bar_end == request.as_of
              and s.side in DIRECTION_OF_SIDE]
    if len(on_bar) != len(result.signals):
        notes.append(f"{len(result.signals) - len(on_bar)} signal(s) not on the read bar were left out")
    outcome = Outcome(NO_SIGNAL, "no signal on this bar", (), gaps, notes, engine=result.version)
    for signal in on_bar:
        await _emit(session, cfg, watch, user, signal, result, outcome)
    if outcome.signals:
        outcome.status, outcome.reason = SIGNAL, f"{len(outcome.signals)} signal(s) written"
    elif outcome.duplicates:
        outcome.reason = "this bar's signal was already written"
    return outcome


def _record(due: datetime, now: datetime, attempt: int, outcome: Outcome, final: bool) -> dict[str, Any]:
    return {"bar_end": _iso(due), "at": _iso(now), "attempt": attempt, "status": outcome.status, "reason": outcome.reason,
            "problems": list(outcome.problems), "gaps": outcome.gaps[:LINES_KEPT], "notes": outcome.notes[:LINES_KEPT],
            "signals": outcome.signals, "duplicates": outcome.duplicates, "engine": outcome.engine, "final": final}


async def run_symbol(session: AsyncSession, cfg: RunnerSettings, watch: Watch, engine: TradeEngineProvider, user: User, broker: Any,
                     now: datetime, *, collect: Collect = S.collect, holder: Optional[str] = None) -> SymbolRun:
    """One symbol on this cycle: read the due bar when it has not been read (or is being retried). Commits."""
    tf = cfg.signal_timeframe
    first = (now.astimezone(F.IST) - timedelta(days=max(cfg.daily_days, cfg.intraday_days) + DUE_LOOKBACK_DAYS + 7)).date()
    cal = await S.load_calendar(session, watch.exchange, first, now.astimezone(F.IST).date() + timedelta(days=1))
    due = due_bar(cal, tf, now, cfg.delay_seconds)
    if due is None:
        return SymbolRun("idle")
    skey = state_key(cfg.tenant_id, tf, watch.key)
    state = _load(await _counter(session, STATE, skey))
    same = state.get("bar_end") == _iso(due)
    if same and state.get("final"):
        return SymbolRun("done", due)
    runs = list(state.get("runs") or [])
    if not same and runs and not runs[-1].get("final"):      # an earlier bar still being retried: a newer bar closed
        runs[-1] = {**runs[-1], "final": True, "reason": f"{runs[-1].get('reason')} - left: a newer bar closed"}
    base ={"tenant_id": cfg.tenant_id, "symbol": watch.key, "shown": watch.shown, "timeframe": tf}
    late = now - (due + timedelta(seconds=cfg.delay_seconds)) > timedelta(seconds=cfg.grace_seconds)
    attempts = int(state.get("attempts") or 0) if same else 0
    if late or attempts >= cfg.max_attempts:
        if same:                                 # an unfinished bar: given up with its last reason
            if runs:
                runs[-1] = {**runs[-1], "final": True, "reason": f"{runs[-1].get('reason')} - given up after "
                            f"{attempts} attempt(s){' and the grace window' if late else ''}"}
        else:                                    # reached only after its grace window: never read late
            runs.append(_record(due, now, 0, Outcome(MISSED, "this bar was reached after its grace window; it is not read late"), True))
        await _save_state(session, skey, {**base, "bar_end": _iso(due), "attempts": attempts, "final": True,
                                          "status": runs[-1]["status"] if runs else MISSED, "runs": runs[-RUNS_KEPT:]})
        await session.commit()
        return SymbolRun(MISSED if not same else "given_up", due)
    holder = holder or uuid.uuid4().hex
    name = lock_name(cfg.tenant_id, watch.key)
    if not await _lock(name, holder, int(cfg.read_timeout_seconds) + 120):
        return SymbolRun("busy", due)
    try:
        attempt = attempts + 1
        try:
            outcome = await _attempt(session, cfg, watch, engine, user, broker, due, now, cal, collect)
        except Exception as exc:  # noqa: BLE001 - a data read that fails is retried within the grace window
            await session.rollback()
            await session.refresh(user)
            logger.warning("Signal engine run failed for %s (%s)", watch.shown, exc.__class__.__name__)
            outcome = Outcome(FAILED, f"the run failed ({exc.__class__.__name__})")
        final = not outcome.retryable or attempt >= cfg.max_attempts
        runs.append(_record(due, now, attempt, outcome, final))
        await _save_state(session, skey, {**base, "bar_end": _iso(due), "attempts": attempt, "final": final,
                                          "status": outcome.status, "runs": runs[-RUNS_KEPT:]})
        await session.commit()
        return SymbolRun(outcome.status, due, outcome, final)
    finally:
        await _unlock(name, holder)


async def run_due(session_factory: Any, now: Optional[datetime] = None, *, broker_for: Optional[BrokerFor] = None,
                  collect: Collect = S.collect) -> RunReport:
    """Every configured organisation with `trade_engine` ON, every symbol of its runner settings. A no-op (no database
    read) while the engine is not installed. Never raises for one organisation or symbol."""
    engine = loader.load()
    if engine is None:
        return RunReport(skipped="engine not installed")
    from app.platform.controls import flag_enabled
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    report = RunReport()
    holder = uuid.uuid4().hex
    async with session_factory() as session:
        stored = await _stored_settings(session)
        for tenant_key in sorted(stored, key=str):
            try:
                tenant_id = int(tenant_key)
            except ValueError:
                report.problems.append(f"runner settings for {tenant_key!r}: not an organisation id")
                continue
            if not await flag_enabled(session, FLAG, tenant_id):
                continue
            try:
                cfg = parse_settings(tenant_id, stored[tenant_key])
            except SettingsProblem as exc:
                report.problems.append(f"organisation {tenant_id}: {exc}")
                continue
            user = await session.get(User, cfg.user_id)
            if user is None or user.tenant_id != tenant_id or not user.is_active:
                report.problems.append(f"organisation {tenant_id}: the signals' user is not an active member")
                continue
            report.tenants += 1
            broker = None
            if broker_for is not None:
                try:
                    broker = await broker_for(session, tenant_id, user, now)
                except Exception as exc:  # noqa: BLE001 - no broker = the broker's frames are gaps, never a crash
                    await session.rollback()
                    await session.refresh(user)
                    report.problems.append(f"organisation {tenant_id}: broker not available ({exc.__class__.__name__})")
            for watch in cfg.symbols:
                try:
                    status = await run_symbol(session, cfg, watch, engine, user, broker, now, collect=collect, holder=holder)
                except Exception as exc:  # noqa: BLE001 - one symbol's problem stays on it
                    await session.rollback()
                    await session.refresh(user)
                    logger.exception("Signal engine runner failed for organisation %s", tenant_id)
                    report.problems.append(f"organisation {tenant_id} {watch.shown}: {exc.__class__.__name__}")
                    continue
                if status.state == "busy":
                    report.busy += 1
                elif status.outcome is not None:
                    report.runs += 1
                    report.signals += len(status.outcome.signals)
                    report.duplicates += status.outcome.duplicates
    return report


__all__ = ["FLAG", "STRATEGY_ID", "DISPLAY_NAME", "SETTINGS_KEY", "STATE", "DEDUPE", "DEFAULTS", "Watch", "RunnerSettings",
           "SettingsProblem", "parse_settings", "save_settings", "due_bar", "bar_end", "window_starts", "state_key", "dedupe_key",
           "run_records", "internal_signal", "lock_name", "card_line", "Outcome", "SymbolRun", "RunReport", "run_symbol", "run_due"]
