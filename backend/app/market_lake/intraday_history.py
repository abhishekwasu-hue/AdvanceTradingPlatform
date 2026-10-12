"""HB2: the owner's intraday (1-minute) history on the server - a capture after the close plus a resumable backfill,
read through the OWNER's own broker session into the owner's PRIVATE store. Design note: docs/design/HB2_INTRADAY_HISTORY.md.

Why: the Trade-repo collector that was to fill HB0's R2 store was cancelled, so HB0's importer stays idle and HB1 covers
only daily bars. HB2 fills the same tenant-private table (`md_tenant_candles`, HB0) in the same lake format: timeframe
`1m`, bars labelled by their END in UTC, B1 versions (identical bar skipped, changed bar -> next version), `ingested_at`
= the moment the bar was written. 3/5/15/30/60-minute bars are built on read (`tenant_store.resample`).

Rules (hard):
- Read-only market data. Only the adapter's candle endpoints are called (`get_historical_data` for past days,
  `get_intraday_candles` for today after the close); no order path is touched (Phase 0: PAPER only).
- One owner, one session: bars go only to HISTORY_OWNER_TENANT_ID, confirmed by HISTORY_OWNER_TENANT_NAME (HB0's rule),
  and are read only through a credential that ONE pinned user of THAT tenant (HISTORY_OWNER_USER_ID, else the lowest-id
  active OWNER) linked themselves (BT1 "own session") at a broker whose data terms are confirmed (Tier A), with a usable
  token today. Another tenant's, a teammate's or a co-owner's session is never used, the bars never reach the shared
  lake, and the holder is in each row's source (`broker:<name>:u<id>`): within the tenant only that user reads them
  (BYOK / BT1b), whoever is pinned later.
- Volume: at most HISTORY_INTRADAY_MAX_REQUESTS requests in any rolling 30 minutes (counted from the database), and no
  pass while the owner's MCX deployments are in session (the same account's exits may need the API until 23:30 IST).
- Rate budget: when the trading worker runs the job it hands over the SAME RateBudget its exits draw from, and every
  request takes a token through `acquire_spare` - only while HISTORY_INTRADAY_RESERVE of both buckets stays free for
  the worker, never holding the budget's lock while waiting. A 429 ends the pass. Broker errors here are not reported
  to the trading circuit breaker.
- Off-hours only: nothing runs on an NSE trading day between HISTORY_INTRADAY_STOP_HOUR_IST (08:00) and
  HISTORY_INTRADAY_HOUR_IST (16:00, never before 15:45), checked before every request - live reads keep the API.
- No look-ahead: only bars whose END is at or before the fetch time are stored (a forming bar is dropped and counted),
  and each is stamped with the time it was written (`ingested_at`), so an as-of replay never sees it earlier.
- Resumable and idempotent: one `history_intraday_days` row per (tenant, instrument, trade date). A day is written and
  marked `done` in one transaction; claimed days are `running` until then, so an interrupted pass leaves nothing half
  written and the next pass retries them. `done` days are never asked for again (`--redo` re-asks; identical bars are
  skipped, changed ones become versions). One pass at a time per database (advisory lock). A pass has a time budget.
- Flag `history_intraday` (off by default): off -> the worker hook, the CLI and `run_pass` do nothing (checked again
  before every request, so turning it off stops a pass).

CLI (inside the backend container, off-hours; it has no worker to share a budget with, so it uses its own RateBudget
at the broker's conservative limits and still leaves HISTORY_INTRADAY_RESERVE of it unused):
    python -m app.market_lake.intraday_history run [--from 2022-01-03] [--to 2026-10-09] [--symbols NSE:RELIANCE,...]
        [--redo] [--budget-seconds 1800]
    python -m app.market_lake.intraday_history progress
    python -m app.market_lake.intraday_history purge --tenant ID --confirm-name NAME
"""
import argparse
import asyncio
import json
import logging
import math
import sys
import time as monotonic_clock
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

from sqlalchemy import delete, distinct, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.brokers.exceptions import BrokerAPIError, BrokerAuthenticationError
from app.brokers.rate_budget import RateBudget, limits_for
from app.core import config
from app.db.models import (BrokerCredentialRecord, DataQualityEventRecord, HistoryIntradayDayRecord, MdTenantCandleRecord,
                           StrategyDeploymentRecord, Tenant, User)
from app.market_data.calendar import EXCHANGE_SESSIONS, IST, is_trading_day, load_holidays
from app.market_lake import quality
from app.market_lake.ingest import LakeBar, trade_date, write_candles_bulk
from app.market_lake.r2_import import ImportRefused, _dedupe, check_labels

logger = logging.getLogger(__name__)

FLAG = "history_intraday"
LOCK_KEY = 0x48420003                       # pg advisory lock id ("HB" 0003); HB0 uses 0001, HB1 0002
TIMEFRAME = "1m"
PERIOD = timedelta(minutes=1)
# Brokers whose adapter serves BOTH 1-minute endpoints (past days and today's session). Also must be Tier A confirmed.
SUPPORTED_BROKERS = ("upstox",)
OWNER_ROLE = "OWNER"                        # without HISTORY_OWNER_USER_ID: the lowest-id active OWNER, and only them
BROKER_SOURCE_PREFIX = "broker:"            # HB2's sources: broker:<name>:u<holder user id>, readable only by that user
CAP_WINDOW = timedelta(minutes=30)          # HISTORY_INTRADAY_MAX_REQUESTS counts the requests of this rolling window


def holder_source(broker: str, user_id: int) -> str:
    """The lake source of bars read through `user_id`'s own session at `broker` - the holder is IN the row, so a read
    can show each member exactly their own (BT1b), whoever is pinned later."""
    return f"{BROKER_SOURCE_PREFIX}{broker}:u{int(user_id)}"
EXCHANGES = ("NSE", "BSE")                  # cash and indices on the NSE clock (F&O / MCX: open question HB2-6)
MCX_SESSION = EXCHANGE_SESSIONS["MCX"]
STALE_RUNNING = timedelta(hours=2)          # a `running` day older than this belongs to a pass that died (progress)
RECHECK_NO_DATA_DAYS = 3                    # a `no_data` day this recent is asked again (the broker may publish late)
SESSION = EXCHANGE_SESSIONS["NSE"]
BARS_FINAL = time(15, 45)                   # every bar of the day has closed and the broker has it
D = HistoryIntradayDayRecord

Clock = Callable[[], datetime]
BudgetFor = Callable[[int, str], RateBudget]
AdapterFactory = Callable[[BrokerCredentialRecord], Any]


class IntradayConfigError(RuntimeError):
    """The pass cannot start (flag off, owner tenant not set or not confirmed, outside hours, bad arguments)."""


@dataclass(frozen=True)
class Instrument:
    exchange: str
    symbol: str

    @property
    def key(self) -> str:
        return f"{self.exchange}:{self.symbol}"


def parse_instrument(raw: str) -> Instrument:
    """'NSE:RELIANCE' / 'RELIANCE' (NSE) / 'NSE:NIFTY 50' -> Instrument; other exchanges are refused (ValueError)."""
    text_ = " ".join((raw or "").split()).upper()
    exchange, _, symbol = text_.rpartition(":") if ":" in text_ else ("NSE", "", text_)
    exchange, symbol = exchange.strip() or "NSE", symbol.strip()
    if not symbol or len(f"{exchange}:{symbol}") > 64:
        raise ValueError(f"{raw!r} is not an instrument (EXCHANGE:SYMBOL)")
    if exchange not in EXCHANGES:
        raise ValueError(f"{raw!r}: HB2 captures {', '.join(EXCHANGES)} instruments only")
    return Instrument(exchange, symbol)


@dataclass
class PassReport:
    # complete | partial | no_session | locked | rate_limited | auth_failed | mcx_session | request_cap
    status: str = "complete"
    complete: bool = False
    tenant_id: Optional[int] = None
    broker: Optional[str] = None
    credential_id: Optional[int] = None
    instruments: int = 0
    requests: int = 0
    days_done: int = 0
    no_data: int = 0
    refused: int = 0
    failed: int = 0
    skipped: int = 0
    pending: int = 0
    bars: int = 0
    inserted: int = 0
    corrected: int = 0
    dropped_forming: int = 0
    quality_events: int = 0
    notes: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        out = asdict(self)
        out["errors"] = self.errors[:200]
        out["notes"] = self.notes[:200]
        return out


def _utc(ts: datetime) -> datetime:
    return ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts.astimezone(timezone.utc)


def _wall() -> datetime:
    return datetime.now(timezone.utc)


# --- when ------------------------------------------------------------------------------------------------------------
def quiet_hours(now: datetime, holidays: Set[date]) -> bool:
    """True when HB2 may call the broker: a day without an NSE session, or a trading day after the bars are final
    (HISTORY_INTRADAY_HOUR_IST, never before 15:45 IST) or before HISTORY_INTRADAY_STOP_HOUR_IST (never after 09:00).
    Never on a HISTORY_INTRADAY_BLACKOUT_DATES date (a special session the calendar does not know: Muhurat, a budget-day
    Saturday)."""
    ist = now.astimezone(IST)
    if ist.date() in config.HISTORY_INTRADAY_BLACKOUT_DATES:
        return False
    if not is_trading_day(ist.date(), holidays):
        return True
    start = max(time(min(max(config.HISTORY_INTRADAY_HOUR_IST, 0), 23)), BARS_FINAL)
    stop = min(time(min(max(config.HISTORY_INTRADAY_STOP_HOUR_IST, 0), 23)), time(9, 0))
    return ist.time() >= start or ist.time() < stop


def last_closed_day(now: datetime, holidays: Set[date]) -> date:
    """The newest trade date whose 1-minute bars are all final: today after 15:45 IST on a trading day, else earlier."""
    ist = now.astimezone(IST)
    day = ist.date() if ist.time() >= BARS_FINAL else ist.date() - timedelta(days=1)
    while not is_trading_day(day, holidays):
        day -= timedelta(days=1)
    return day


def nightly_due(now: datetime, last_day: Optional[date]) -> Optional[date]:
    """The IST date the worker should run a pass for, or None (before HISTORY_INTRADAY_HOUR_IST, or that day is done)."""
    ist = now.astimezone(IST)
    if ist.time() < max(time(min(max(config.HISTORY_INTRADAY_HOUR_IST, 0), 23)), BARS_FINAL) or last_day == ist.date():
        return None
    return ist.date()


async def _holidays(session: AsyncSession, years: Sequence[int]) -> Tuple[Set[date], Set[int]]:
    """NSE holidays of the years that have a calendar, and the years without one (their weekdays are all asked for)."""
    days: Set[date] = set()
    unknown: Set[int] = set()
    for year in sorted(set(years)):
        loaded = await load_holidays(session, SESSION.holiday_calendar, year)
        if loaded:
            days |= set(loaded)
        else:
            unknown.add(year)
    return days, unknown


# --- who -------------------------------------------------------------------------------------------------------------
async def owner_tenant(session: AsyncSession, tenant_id: Optional[int] = None, name: Optional[str] = None) -> int:
    """HB0's owner rule: HISTORY_OWNER_TENANT_ID must name a tenant and HISTORY_OWNER_TENANT_NAME must equal its name."""
    tenant_id = config.HISTORY_OWNER_TENANT_ID if tenant_id is None else tenant_id
    if tenant_id is None:
        raise IntradayConfigError("HISTORY_OWNER_TENANT_ID is not set: refusing to capture the owner's 1-minute history")
    tenant = await session.get(Tenant, tenant_id)
    if tenant is None:
        raise IntradayConfigError(f"HISTORY_OWNER_TENANT_ID={tenant_id} is not a tenant of this database")
    expected = (config.HISTORY_OWNER_TENANT_NAME if name is None else name or "").strip()
    if not expected or expected != (tenant.name or "").strip():
        raise IntradayConfigError(f"HISTORY_OWNER_TENANT_NAME must equal the name of tenant {tenant_id} - a confirmation "
                                  "that the id is the owner's (refusing to capture licensed 1-minute data)")
    return int(tenant_id)


async def enabled(session: AsyncSession) -> bool:
    """The worker's gate: an owner tenant is configured and the flag is on for it (on for everyone or allow-listed)."""
    from app.platform.controls import flag_enabled
    owner = config.HISTORY_OWNER_TENANT_ID
    return owner is not None and await flag_enabled(session, FLAG, owner)


async def owner_user(session: AsyncSession, tenant_id: int, user_id: Optional[int] = None) -> Optional[User]:
    """The ONE user whose own session HB2 reads: HISTORY_OWNER_USER_ID (refused unless it is an active user of the owner
    tenant), else the lowest-id active OWNER of that tenant. Never another co-owner or teammate."""
    user_id = config.HISTORY_OWNER_USER_ID if user_id is None else user_id
    if user_id is not None:
        user = await session.get(User, user_id)
        if user is None or user.tenant_id != tenant_id or not user.is_active:
            raise IntradayConfigError(f"HISTORY_OWNER_USER_ID={user_id} is not an active user of the owner tenant {tenant_id}")
        return user
    return await session.scalar(select(User).where(User.tenant_id == tenant_id, User.is_active.is_(True),
                                                   User.role == OWNER_ROLE).order_by(User.id).limit(1))


async def owner_session(session: AsyncSession, tenant_id: int, now: datetime,
                        user_id: Optional[int] = None) -> Optional[Tuple[User, BrokerCredentialRecord]]:
    """The owner's OWN broker session: a credential the pinned owner user (`owner_user`) linked themselves in the owner's
    tenant (BT1: never a teammate's, never another tenant's), at a supported broker whose data terms allow Tier A, with a
    usable token now. None when that user has none (the owner has not logged in today) - no other user is tried."""
    from app.market_data.entitlement import own_records, pick_session
    user = await owner_user(session, tenant_id, user_id)
    if user is None:
        return None
    records = [r for r in await own_records(session, user) if r.broker_name in SUPPORTED_BROKERS]
    record = pick_session(records, now)
    if record is not None and record.tenant_id == tenant_id and record.user_id == user.id:
        return user, record
    return None


async def mcx_in_session(session: AsyncSession, tenant_id: int, now: datetime) -> bool:
    """True while the owner tenant has a non-stopped MCX deployment and the MCX session is open (it trades to 23:30 IST):
    its exits may need the same broker account's rate limit, so HB2 neither starts nor continues a pass then."""
    from app.market_data.calendar import session_family, session_status
    rows = await session.scalars(select(StrategyDeploymentRecord.exchange).where(
        StrategyDeploymentRecord.tenant_id == tenant_id, StrategyDeploymentRecord.status != "STOPPED").distinct())
    if not any(session_family(exchange) == "MCX" for exchange in rows):
        return False
    status = session_status(now, await load_holidays(session, MCX_SESSION.holiday_calendar), "MCX")
    return bool(status.is_open)


# --- what ------------------------------------------------------------------------------------------------------------
async def instruments(session: AsyncSession, tenant_id: int, symbols: Optional[Sequence[str]] = None) -> Tuple[List[Instrument], List[str]]:
    """The instruments to keep: `symbols` when given, else HISTORY_INTRADAY_SYMBOLS plus (HISTORY_INTRADAY_INCLUDE_
    DEPLOYMENTS) the owner tenant's own deployments. Unusable entries are returned as notes, never guessed."""
    raw: List[str] = list(symbols) if symbols else list(config.HISTORY_INTRADAY_SYMBOLS)
    if not symbols and config.HISTORY_INTRADAY_INCLUDE_DEPLOYMENTS:
        rows = await session.execute(select(StrategyDeploymentRecord.exchange, StrategyDeploymentRecord.symbol).where(
            StrategyDeploymentRecord.tenant_id == tenant_id, StrategyDeploymentRecord.status != "STOPPED").distinct())
        raw += [f"{exchange or 'NSE'}:{symbol}" for exchange, symbol in rows]
    out: Dict[str, Instrument] = {}
    notes: List[str] = []
    for entry in raw:
        try:
            inst = parse_instrument(entry)
        except ValueError as exc:
            notes.append(f"skipped: {exc}")
            continue
        out.setdefault(inst.key, inst)
    return [out[k] for k in sorted(out)], notes


def _windows(days: Sequence[date], span: int) -> List[List[date]]:
    """Newest first; consecutive pending days grouped while they fit `span` calendar days (one request each)."""
    out: List[List[date]] = []
    for day in sorted(days, reverse=True):
        if out and (out[-1][0] - day).days < span:
            out[-1].append(day)
        else:
            out.append([day])
    return out


async def _pending_days(session: AsyncSession, tenant_id: int, user_id: int, inst: Instrument, first: date, last: date,
                        holidays: Set[date],
                        *, redo: bool) -> Tuple[List[date], int]:
    """Trading days in [first, last] not done yet (and `no_data` days only while recent) -> (days, skipped count)."""
    states = {d: s for d, s in await session.execute(select(D.trade_date, D.status).where(
        D.tenant_id == tenant_id, D.user_id == user_id, D.instrument_key == inst.key, D.trade_date >= first,
        D.trade_date <= last))}
    days, skipped, day = [], 0, first
    while day <= last:
        if is_trading_day(day, holidays):
            status = states.get(day)
            settled = status == "done" or (status == "no_data" and day < last - timedelta(days=RECHECK_NO_DATA_DAYS))
            if settled and not redo:
                skipped += 1
            else:
                days.append(day)
        day += timedelta(days=1)
    return days, skipped


# --- one day into the private store ----------------------------------------------------------------------------------
def to_bars(inst: Instrument, raw: Sequence[Any]) -> List[LakeBar]:
    """Broker bars (labelled by START, as every adapter returns them; a naive time is IST) -> lake bars labelled by
    END, UTC. A price that is not a positive number or a volume that is not a count refuses the batch (ImportRefused):
    nothing is filled in."""
    from app.brokers.timestamps import parse_broker_timestamp
    out: List[LakeBar] = []
    for b in raw:
        start = parse_broker_timestamp(getattr(b, "timestamp", None))
        if start is None:
            raise ImportRefused(f"a bar without a readable time ({getattr(b, 'timestamp', None)!r})")
        prices: List[Any] = [getattr(b, name, None) for name in ("open", "high", "low", "close")]
        try:
            values = [float(p) for p in prices]       # None -> TypeError, handled below
            volume = float(getattr(b, "volume", 0) or 0)
        except (TypeError, ValueError):
            raise ImportRefused(f"unreadable price or volume at {start.isoformat()}") from None
        if any(not math.isfinite(v) or v <= 0 for v in values):
            raise ImportRefused(f"a bar without a usable price at {start.isoformat()} - nothing is filled in")
        if not math.isfinite(volume) or volume < 0:
            raise ImportRefused(f"volume {volume!r} at {start.isoformat()} is not a count")
        out.append(LakeBar(inst.key, TIMEFRAME, start + PERIOD, values[0], values[1], values[2], values[3], int(volume)))
    return out


async def _store_day(session: AsyncSession, tenant_id: int, inst: Instrument, day: date, bars: List[LakeBar], source: str, *,
                     stamp: datetime, complete_day: bool) -> Dict[str, Any]:
    """Checks, writes and verifies one day's closed bars in the tenant's private store. Does not commit."""
    check_labels("m1", bars, inst.exchange)                      # END labels inside the session, whole minutes
    bars, dupes = _dedupe(bars, source)                          # identical repeats dropped; two different bars refused
    written = await write_candles_bulk(session, bars, source, tenant_id=tenant_id, ingested_at=stamp)
    await session.flush()
    first, last = _utc(bars[0].ts), _utc(bars[-1].ts)
    in_store = int(await session.scalar(select(func.count(distinct(MdTenantCandleRecord.ts))).where(
        MdTenantCandleRecord.tenant_id == tenant_id, MdTenantCandleRecord.instrument_key == inst.key,
        MdTenantCandleRecord.timeframe == TIMEFRAME, MdTenantCandleRecord.source == source,
        MdTenantCandleRecord.ts >= first, MdTenantCandleRecord.ts <= last)) or 0)
    if in_store < len(bars):
        raise RuntimeError(f"verification failed: the private store holds {in_store} of the day's {len(bars)} bars")
    events = list(dupes) + quality.find_bar_problems(bars, source)       # no `late` check: a backfill is late by nature
    if complete_day:
        events += quality.find_gaps(bars, day, source)                   # reported, never filled
    written_events = await quality.record(session, events, tenant_id=tenant_id, commit=False)
    return {"bars": len(bars), "inserted": written.inserted, "corrected": written.corrected, "skipped": written.skipped,
            "quality_events": written_events, "first_ts": first, "last_ts": last}


# --- state -----------------------------------------------------------------------------------------------------------
async def _claim(session_factory: Any, tenant_id: int, inst: Instrument, days: Sequence[date], *, source: str,
                 credential_id: int, user_id: int, endpoint: str) -> None:
    now = _wall()
    async with session_factory() as session:
        existing = {s.trade_date: s for s in await session.scalars(select(D).where(
            D.tenant_id == tenant_id, D.user_id == user_id, D.instrument_key == inst.key, D.trade_date.in_(list(days))))}
        for day in days:
            state = existing.get(day)
            if state is None:
                state = D(tenant_id=tenant_id, user_id=user_id, instrument_key=inst.key, trade_date=day, status="pending", attempts=0,
                          inserted=0, corrected=0, skipped=0, dropped_forming=0, quality_events=0)
                session.add(state)
            state.status, state.error, state.started_at = "running", None, now
            state.attempts = (state.attempts or 0) + 1
            state.source, state.credential_id, state.user_id, state.endpoint = source, credential_id, user_id, endpoint
        await session.commit()


async def _set(session_factory: Any, tenant_id: int, user_id: int, inst: Instrument, days: Sequence[date], **values: Any) -> None:
    async with session_factory() as session:
        await session.execute(update(D).where(D.tenant_id == tenant_id, D.user_id == user_id, D.instrument_key == inst.key,
                                              D.trade_date.in_(list(days))).values(updated_at=_wall(), **values))
        await session.commit()


# --- the pass --------------------------------------------------------------------------------------------------------
async def _flag_on(session_factory: Any, tenant_id: int) -> bool:
    from app.platform.controls import flag_enabled
    async with session_factory() as session:
        return bool(await flag_enabled(session, FLAG, tenant_id))


async def _try_lock(session: AsyncSession) -> bool:
    """Transaction-scoped advisory lock (released by the lock session's rollback, or when its connection goes away)."""
    if session.get_bind().dialect.name != "postgresql":
        return True
    return bool(await session.scalar(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": LOCK_KEY}))


def _default_adapter(record: BrokerCredentialRecord) -> Any:
    from app.brokers.token_lifecycle import build_adapter
    return build_adapter(record)


def _adapter(record: BrokerCredentialRecord, factory: AdapterFactory, cache: Optional[Dict[Any, Any]], day: date) -> Any:
    """One adapter per (credential, its last update, IST day) when the caller keeps a cache (the worker does): the
    retries of one evening reuse it (and the instrument master it has loaded); a re-login or a new day builds a new one."""
    if cache is None:
        return factory(record)
    key = (record.id, record.updated_at, day)
    if key not in cache:
        cache.clear()                                    # one slot: yesterday's or a stale token's adapter is dropped
        cache[key] = factory(record)
    return cache[key]


async def run_pass(session_factory: Any, *, budget_for: Optional[BudgetFor] = None, adapter_factory: Optional[AdapterFactory] = None,
                   symbols: Optional[Sequence[str]] = None, start: Optional[date] = None, end: Optional[date] = None,
                   redo: bool = False, budget_seconds: Optional[float] = None, clock: Optional[Clock] = None,
                   owner_tenant_id: Optional[int] = None, owner_tenant_name: Optional[str] = None,
                   owner_user_id: Optional[int] = None, reserve: Optional[float] = None, max_requests: Optional[int] = None,
                   adapter_cache: Optional[Dict[Any, Any]] = None) -> PassReport:
    """One pass: today's capture (once the bars are final) and the backfill, newest days first, until everything in
    range is done or the time budget, the quiet hours, the rate budget or the flag ends it. See the module docstring.
    `budget_for(tenant_id, "<broker>@<account>")` returns the trading worker's own RateBudget for that session; without
    it the pass makes its own (CLI). At most `max_requests` (HISTORY_INTRADAY_MAX_REQUESTS) broker requests in any
    rolling 30 minutes.
    `adapter_cache` (a dict the caller keeps) reuses one adapter across the passes of one IST day. `complete` is True
    only when nothing in range is left to ask for."""
    clock = clock or _wall
    reserve = config.HISTORY_INTRADAY_RESERVE if reserve is None else reserve
    seconds = float(config.HISTORY_INTRADAY_BUDGET_SECONDS if budget_seconds is None else budget_seconds)
    deadline = monotonic_clock.monotonic() + seconds
    report = PassReport()
    if config.HISTORY_INTRADAY_BLACKOUT_ERRORS:
        raise IntradayConfigError("HISTORY_INTRADAY_BLACKOUT_DATES has entries that are not YYYY-MM-DD dates: "
                                  f"{', '.join(config.HISTORY_INTRADAY_BLACKOUT_ERRORS)} - fix them (a typo would let HB2 "
                                  "run during a special session)")
    async with session_factory() as session:
        tenant_id = await owner_tenant(session, owner_tenant_id, owner_tenant_name)
        report.tenant_id = tenant_id
    if not await _flag_on(session_factory, tenant_id):
        raise IntradayConfigError(f"The '{FLAG}' feature flag is off for the owner's tenant - turn it on in Admin > Controls")
    first_day = start or date.fromisoformat(config.HISTORY_INTRADAY_START)
    now = clock()
    async with session_factory() as session:
        holidays, unknown_years = await _holidays(session, range(first_day.year - 1, now.astimezone(IST).year + 1))
        if not quiet_hours(now, holidays):
            raise IntradayConfigError("NSE trading hours or a blackout date: HB2 runs only after the bars are final "
                                      f"({max(config.HISTORY_INTRADAY_HOUR_IST, 15)}:45 IST at the earliest) or before "
                                      f"{min(config.HISTORY_INTRADAY_STOP_HOUR_IST, 9):02d}:00 IST, and on days without a session")
        found = await owner_session(session, tenant_id, now, owner_user_id)
        if found is None:
            report.status = "no_session"
            report.notes.append("no usable broker session of the owner's own (log in to the broker today); nothing asked")
            return report
        if await mcx_in_session(session, tenant_id, now):
            report.status = "mcx_session"
            report.notes.append("the owner's MCX deployments are in session - nothing asked until MCX closes")
            return report
        session_user, record = found
        targets, notes = await instruments(session, tenant_id, symbols)
    report.notes += notes
    report.broker, report.credential_id, report.instruments = record.broker_name, record.id, len(targets)
    last_day = min(end or last_closed_day(now, holidays), last_closed_day(now, holidays))
    if unknown_years:
        report.notes.append(f"no NSE holiday calendar for {', '.join(map(str, sorted(unknown_years)))}: every weekday is asked "
                            "for (a holiday comes back as no_data)")
    if not targets or last_day < first_day:
        report.complete = True
        return report
    budget = budget_for(tenant_id, f"{record.broker_name}@{record.account_label or 'primary'}") if budget_for else \
        RateBudget(limits_for(record.broker_name))
    adapter = _adapter(record, adapter_factory or _default_adapter, adapter_cache, now.astimezone(IST).date())
    source = holder_source(record.broker_name, session_user.id)
    cap = max(1, int(config.HISTORY_INTRADAY_MAX_REQUESTS if max_requests is None else max_requests))
    async with session_factory() as lock_session:
        if not await _try_lock(lock_session):
            report.status = "locked"
            return report
        try:
            await _run_targets(session_factory, report, tenant_id, session_user.id, record, adapter, budget, targets, first_day,
                               last_day, holidays, source=source, redo=redo, reserve=reserve, deadline=deadline, clock=clock,
                               cap=cap)
        finally:
            await lock_session.rollback()                        # ends the lock's transaction: the lock is released
    # Complete = the pass worked through everything in range. Failed / refused days are retried by the next day's pass
    # (and by any manual run), so they do not keep the worker re-asking all night.
    report.complete = report.status == "complete" and report.pending == 0
    logger.info("HB2 intraday history pass: %s", {k: v for k, v in report.as_dict().items() if k not in ("notes", "errors")})
    return report


async def _run_targets(session_factory: Any, report: PassReport, tenant_id: int, user_id: int, record: BrokerCredentialRecord,
                       adapter: Any, budget: RateBudget, targets: Sequence[Instrument], first_day: date, last_day: date,
                       holidays: Set[date], *, source: str, redo: bool, reserve: float, deadline: float, clock: Clock,
                       cap: int) -> None:
    plan: List[Tuple[Instrument, List[date]]] = []
    today_ist = clock().astimezone(IST).date()
    async with session_factory() as session:
        for inst in targets:
            days, skipped = await _pending_days(session, tenant_id, user_id, inst, first_day, last_day, holidays, redo=redo)
            report.skipped += skipped
            if today_ist in days:                        # today comes from the intraday endpoint, alone in its request
                plan.append((inst, [today_ist]))
            for window in _windows([d for d in days if d != today_ist], config.HISTORY_INTRADAY_WINDOW_DAYS):
                plan.append((inst, window))
    # Today's capture first (every instrument), then the backfill newest first, instrument by instrument.
    plan.sort(key=lambda item: (0 if item[1][0] == today_ist else 1, item[0].key if item[1][0] != today_ist else "", -item[1][0].toordinal()))
    broken: Set[str] = set()
    for i, (inst, window) in enumerate(plan):
        if inst.key in broken:
            report.pending += len(window)
            continue
        stop = await _should_stop(session_factory, report, tenant_id, clock, holidays, deadline, cap)
        if stop:
            report.status, why = stop
            report.pending += sum(len(w) for _, w in plan[i:])
            report.notes.append(why)
            return
        endpoint = "intraday" if window[0] == today_ist else "historical"
        await _claim(session_factory, tenant_id, inst, window, source=source, credential_id=record.id, user_id=user_id,
                     endpoint=endpoint)
        remaining = deadline - monotonic_clock.monotonic()
        got = await budget.acquire_spare(reserve, deadline=budget.now() + max(0.0, remaining))
        # The wait may have been long: hours, the flag and MCX are looked at again before the request goes out.
        stop = (None if not got else await _should_stop(session_factory, report, tenant_id, clock, holidays, None, None)) or \
            (None if got else ("partial", "time budget used while the trading worker held the rate budget"))
        if stop:
            await _set(session_factory, tenant_id, user_id, inst, window, status="pending", note=f"{stop[1]} - asked again next pass")
            report.status = stop[0]
            report.pending += sum(len(w) for _, w in plan[i:])
            report.notes.append(stop[1])
            return
        report.requests += 1
        try:
            if endpoint == "intraday":
                raw = await adapter.get_intraday_candles(inst.symbol, inst.exchange, "1min")
            else:
                lo, hi = min(window), max(window)
                raw = await adapter.get_historical_data(inst.symbol, inst.exchange, "1min",
                                                        datetime.combine(lo, time(0), tzinfo=IST), datetime.combine(hi, time(0), tzinfo=IST))
        except Exception as exc:  # noqa: BLE001 - classified below; one instrument's failure never stops the others
            fetched_status = _classify(exc)
            why = f"{type(exc).__name__}: {exc}"[:2000]
            if fetched_status in ("rate_limited", "auth_failed"):
                await _set(session_factory, tenant_id, user_id, inst, window, status="pending", error=why)
                report.status = fetched_status
                report.pending += sum(len(w) for _, w in plan[i:])
                report.errors.append(f"{inst.key}: {why}"[:500])
                return
            logger.warning("HB2: %s %s..%s failed: %s", inst.key, min(window), max(window), why)
            await _set(session_factory, tenant_id, user_id, inst, window, status="failed", error=why)
            report.failed += len(window)
            report.errors.append(f"{inst.key} {min(window)}..{max(window)}: {why}"[:500])
            broken.add(inst.key)                         # its other windows wait for the next pass (no wasted requests)
            continue
        fetched_at = clock()
        await _store_window(session_factory, report, tenant_id, user_id, inst, window, list(raw or []), source=source,
                            fetched_at=fetched_at, clock=clock, last_day=last_day)


def _classify(exc: BaseException) -> str:
    if isinstance(exc, BrokerAuthenticationError):
        return "auth_failed"
    if isinstance(exc, BrokerAPIError) and exc.status_code == 429:
        return "rate_limited"
    if isinstance(exc, BrokerAPIError) and exc.status_code in (401, 403):
        return "auth_failed"
    return "failed"


async def recent_requests(session_factory: Any, tenant_id: int) -> int:
    """Broker requests HB2 made for the tenant in the last 30 minutes, from the database (each request claims its
    window's days with one `started_at`), so the cap holds across passes, worker restarts, the CLI and replicas."""
    async with session_factory() as session:
        claims = select(D.instrument_key, D.user_id, D.started_at).where(
            D.tenant_id == tenant_id, D.started_at >= _wall() - CAP_WINDOW).distinct().subquery()
        return int(await session.scalar(select(func.count()).select_from(claims)) or 0)


async def _should_stop(session_factory: Any, report: PassReport, tenant_id: int, clock: Clock, holidays: Set[date],
                       deadline: Optional[float], cap: Optional[int]) -> Optional[Tuple[str, str]]:
    """(status, why) when the pass must stop before its next request; None to go on. `deadline` / `cap` None skip
    those checks (the re-check after the token wait: the window is already claimed and counted)."""
    if deadline is not None and monotonic_clock.monotonic() >= deadline:
        return "partial", "time budget used - the rest is asked for on the next pass"
    made = 0 if cap is None else max(report.requests, await recent_requests(session_factory, tenant_id))
    if cap is not None and made >= cap:
        return "request_cap", (f"{made} requests in the last 30 minutes (HISTORY_INTRADAY_MAX_REQUESTS={cap}) - the rest is "
                               "asked for on a later pass")
    now = clock()
    if not quiet_hours(now, holidays):
        return "partial", "NSE trading hours (or a blackout date) reached - stopped so live reads keep the broker API"
    if not await _flag_on(session_factory, tenant_id):
        return "partial", f"the '{FLAG}' flag was turned off - stopped"
    async with session_factory() as session:
        if await mcx_in_session(session, tenant_id, now):
            return "mcx_session", "the owner's MCX deployments are in session - stopped until MCX closes"
    return None


async def _store_window(session_factory: Any, report: PassReport, tenant_id: int, user_id: int, inst: Instrument,
                        window: Sequence[date], raw: List[Any], *, source: str, fetched_at: datetime, clock: Clock, last_day: date) -> None:
    from app.brokers.timestamps import parse_broker_timestamp
    # One refused bar refuses only ITS day: the window's bars are split by their IST date before they are checked (a bar
    # whose time cannot be read cannot be placed in a day, so it still refuses the whole window).
    raw_by_day: Dict[date, List[Any]] = {}
    for b in raw:
        start = parse_broker_timestamp(getattr(b, "timestamp", None))
        if start is None:
            why = f"a bar without a readable time ({getattr(b, 'timestamp', None)!r})"
            await _set(session_factory, tenant_id, user_id, inst, window, status="refused", error=why[:2000])
            report.refused += len(window)
            report.errors.append(f"{inst.key} {min(window)}..{max(window)}: refused - {why}"[:500])
            return
        raw_by_day.setdefault(start.astimezone(IST).date(), []).append(b)
    bars: List[LakeBar] = []
    refused_days: Dict[date, str] = {}
    for day, day_raw in raw_by_day.items():
        try:
            bars += to_bars(inst, day_raw)
        except ImportRefused as exc:
            refused_days[day] = str(exc)
    for day, why in sorted(refused_days.items()):
        if day in window:
            await _set(session_factory, tenant_id, user_id, inst, [day], status="refused", error=why[:2000])
            report.refused += 1
            report.errors.append(f"{inst.key} {day}: refused - {why}"[:500])
    closed = [b for b in bars if _utc(b.ts) <= _utc(fetched_at)]     # no look-ahead: a forming bar is never stored
    forming = len(bars) - len(closed)
    by_day: Dict[date, List[LakeBar]] = {}
    for bar in closed:
        by_day.setdefault(trade_date(bar.ts), []).append(bar)
    outside = sum(len(v) for d, v in by_day.items() if d not in window)
    report.dropped_forming += forming
    for day in sorted(window):
        if day in refused_days:
            continue
        day_bars = by_day.get(day, [])
        extra = {"dropped_forming": forming if day == max(window) else 0}
        if not day_bars:
            await _set(session_factory, tenant_id, user_id, inst, [day], status="no_data", bars=0, error=None, done_at=None,
                       note="the broker returned no closed 1-minute bar for this day (beyond its depth, not traded, or a "
                            "holiday)", **extra)
            report.no_data += 1
            continue
        async with session_factory() as session:
            try:
                out = await _store_day(session, tenant_id, inst, day, day_bars, source, stamp=max(clock(), _utc(fetched_at)),
                                       complete_day=day < last_day or _utc(fetched_at) >= _utc(datetime.combine(day, BARS_FINAL, tzinfo=IST)))
                note = f"{outside} bar(s) outside the asked days ignored" if outside and day == max(window) else None
                await session.execute(update(D).where(D.tenant_id == tenant_id, D.user_id == user_id, D.instrument_key == inst.key,
                                                             D.trade_date == day)
                                      .values(status="done", error=None, note=note, done_at=_wall(), updated_at=_wall(), **out, **extra))
                await session.commit()
            except ImportRefused as exc:
                await session.rollback()
                await _set(session_factory, tenant_id, user_id, inst, [day], status="refused", error=str(exc)[:2000])
                report.refused += 1
                report.errors.append(f"{inst.key} {day}: refused - {exc}"[:500])
                continue
            except Exception as exc:  # noqa: BLE001 - one bad day must not stop the pass; it stays retryable
                await session.rollback()
                logger.exception("HB2: storing %s %s failed", inst.key, day)
                await _set(session_factory, tenant_id, user_id, inst, [day], status="failed", error=f"{type(exc).__name__}: {exc}"[:2000])
                report.failed += 1
                report.errors.append(f"{inst.key} {day}: failed - {exc}"[:500])
                continue
        report.days_done += 1
        report.bars += out["bars"]
        report.inserted += out["inserted"]
        report.corrected += out["corrected"]
        report.quality_events += out["quality_events"]


# --- reporting and clean-up -------------------------------------------------------------------------------------------
async def progress(session: AsyncSession, *, tenant_id: Optional[int] = None, limit: int = 200,
                   now: Optional[datetime] = None) -> Dict[str, Any]:
    """Per instrument: days by status, first / last day done, bars stored; then the days that need attention (failed,
    refused, running - with when they were claimed, and `stale` for a `running` day older than STALE_RUNNING, whose pass
    died) - newest first. Counts and names only, never bars."""
    tenant = config.HISTORY_OWNER_TENANT_ID if tenant_id is None else tenant_id
    if tenant is None:
        return {"tenant_id": None, "instruments": {}, "attention": []}
    per: Dict[str, Dict[str, Any]] = {}
    for key, status, n, first, last, bars in await session.execute(
            select(D.instrument_key, D.status, func.count(), func.min(D.trade_date), func.max(D.trade_date), func.sum(D.bars))
            .where(D.tenant_id == tenant).group_by(D.instrument_key, D.status)):
        entry = per.setdefault(key, {"by_status": {}, "first_done": None, "last_done": None, "bars": 0})
        entry["by_status"][status] = int(n)
        if status == "done":
            entry["first_done"], entry["last_done"] = first.isoformat() if first else None, last.isoformat() if last else None
            entry["bars"] = int(bars or 0)
    attention = list(await session.scalars(select(D).where(D.tenant_id == tenant, D.status.in_(("failed", "refused", "running")))
                                           .order_by(D.trade_date.desc()).limit(max(1, limit))))
    stale_before = (now or _wall()) - STALE_RUNNING
    stale_running = int(await session.scalar(select(func.count()).select_from(D).where(
        D.tenant_id == tenant, D.status == "running", (D.started_at.is_(None)) | (D.started_at < stale_before))) or 0)

    def stale(s: Any) -> bool:
        """A `running` day claimed longer ago than any pass lasts: its pass died (it is asked again by the next one)."""
        return s.status == "running" and (s.started_at is None or _utc(s.started_at) < stale_before)
    return {"tenant_id": tenant, "instruments": per,
            "stale_running": stale_running,
            "attention": [{"instrument_key": s.instrument_key, "user_id": s.user_id, "trade_date": s.trade_date.isoformat(),
                           "status": s.status,
                           "started_at": _utc(s.started_at).isoformat() if s.started_at else None, "stale": stale(s),
                           "error": s.error, "attempts": s.attempts} for s in attention]}


async def purge_tenant(session: AsyncSession, tenant_id: int, *, confirm_name: str) -> Dict[str, int]:
    """Removes what HB2 put in ONE tenant's private store (1-minute rows from `broker:*`), the quality events about them
    and the tenant's HB2 day rows. The name must match the tenant's (the same confirmation as the capture)."""
    tenant = await session.get(Tenant, tenant_id)
    if tenant is None or not confirm_name.strip() or confirm_name.strip() != (tenant.name or "").strip():
        raise IntradayConfigError(f"--confirm-name must equal the name of tenant {tenant_id}")
    bars = await session.execute(delete(MdTenantCandleRecord).where(
        MdTenantCandleRecord.tenant_id == tenant_id, MdTenantCandleRecord.timeframe == TIMEFRAME,
        MdTenantCandleRecord.source.like(f"{BROKER_SOURCE_PREFIX}%")))
    events = await session.execute(delete(DataQualityEventRecord).where(
        DataQualityEventRecord.tenant_id == tenant_id, DataQualityEventRecord.source.like(f"{BROKER_SOURCE_PREFIX}%")))
    states = await session.execute(delete(D).where(D.tenant_id == tenant_id))
    await session.commit()
    return {"bars": int(getattr(bars, "rowcount", 0) or 0), "quality_events": int(getattr(events, "rowcount", 0) or 0),
            "state_rows": int(getattr(states, "rowcount", 0) or 0)}


# --- CLI ---------------------------------------------------------------------------------------------------------------
def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m app.market_lake.intraday_history", description="HB2: owner intraday history")
    sub = p.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="capture + backfill now (off-hours only; resumable)")
    run.add_argument("--from", dest="start", type=date.fromisoformat, default=None)
    run.add_argument("--to", dest="end", type=date.fromisoformat, default=None)
    run.add_argument("--symbols", default="", help="EXCHANGE:SYMBOL,... (default: HISTORY_INTRADAY_SYMBOLS + deployments)")
    run.add_argument("--redo", action="store_true", help="ask again for days already done (changed bars become versions)")
    run.add_argument("--budget-seconds", type=float, default=None)
    sub.add_parser("progress", help="per instrument: days by status")
    purge = sub.add_parser("purge", help="remove HB2's rows of one tenant (a wrongly configured owner)")
    purge.add_argument("--tenant", type=int, required=True)
    purge.add_argument("--confirm-name", required=True)
    return p


async def _main(argv: Optional[Sequence[str]] = None) -> int:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    args = _parser().parse_args(argv)
    engine = create_async_engine(config.DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        if args.command == "run":
            report = await run_pass(factory, symbols=[s for s in args.symbols.split(",") if s.strip()] or None,
                                    start=args.start, end=args.end, redo=args.redo, budget_seconds=args.budget_seconds)
            print(json.dumps(report.as_dict(), indent=2, default=str))
            return 0 if report.complete else 1
        async with factory() as session:
            if args.command == "progress":
                result = await progress(session)
            else:
                result = await purge_tenant(session, args.tenant, confirm_name=args.confirm_name)
        print(json.dumps(result, indent=2, default=str))
        return 0
    except IntradayConfigError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    finally:
        await engine.dispose()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sys.exit(asyncio.run(_main()))
