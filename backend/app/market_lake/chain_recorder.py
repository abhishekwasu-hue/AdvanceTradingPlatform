"""TM 9.4: chain history - record option-chain snapshots NOW, through the owner's own session, into the owner's store.

Design note: docs/design/CHAIN_HISTORY.md. What one slot does (`record_slot`, called by the trading worker once per
CHAIN_HISTORY_SLOT_MINUTES while a venue is open):

- **Whose session (TM-7, owner 23 Oct; RDY-6).** The owner is HB2's owner: HISTORY_OWNER_TENANT_ID confirmed by
  HISTORY_OWNER_TENANT_NAME; the session is the one HISTORY_OWNER_USER_ID (else the lowest-id active OWNER of that
  tenant) linked themselves at a broker whose data terms are confirmed (Tier A), with a usable token now
  (`intraday_history.owner_session`). No other user's or organisation's session is ever tried: no owner session = a gap.
- **Where.** `chain_store.write` under the owner's tenant only; the holder is in the row's source (BT1b).
- **What.** CHAIN_HISTORY_UNDERLYINGS (NIFTY, BANKNIFTY, SENSEX) and - only when CHAIN_HISTORY_STOCKS_ENABLED -
  CHAIN_HISTORY_STOCKS, the nearest CHAIN_HISTORY_EXPIRIES expiries of each from the instrument master, every strike the
  broker returns. At most CHAIN_HISTORY_MAX_READS_PER_SLOT reads per slot, the indices first.
- **When.** Only while that underlying's venue is open by the exchange calendar (holidays included,
  `market_session_status`); one slot per period; a slot already stored (another replica) is not read again.
- **Rate budget.** Each read takes a token from the trading worker's own RateBudget of that session through
  `acquire_spare` (CHAIN_HISTORY_RESERVE of both buckets stays free for exits; the budget's lock is never held while
  waiting). No token within CHAIN_HISTORY_TOKEN_WAIT_SECONDS = the slot's remaining reads are skipped (a gap), never
  queued. A 429 or an expired login ends the slot.
- **Read-only.** Only `get_option_chain` is called; no order path is reachable from here (Phase 0: PAPER only).
- Flag `chain_history` (off by default) for the owner's tenant; off = nothing is read.
"""
from __future__ import annotations

import logging
import time as monotonic_clock
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from app.brokers.exceptions import BrokerAPIError, BrokerAuthenticationError
from app.brokers.rate_budget import RateBudget, limits_for
from app.core import config
from app.market_data.calendar import IST, market_session_status, session_family
from app.market_lake import chain_store, intraday_history

logger = logging.getLogger(__name__)

FLAG = "chain_history"
BudgetFor = Callable[[int, str], RateBudget]
AdapterFactory = Callable[[Any], Any]


@dataclass
class SlotReport:
    status: str = "ok"            # ok / off / not_configured / no_session / closed / rate_budget / rate_limited / auth
    slot: Optional[str] = None
    tenant_id: Optional[int] = None
    broker: Optional[str] = None
    planned: int = 0
    reads: int = 0
    stored: int = 0
    already: int = 0
    skipped: int = 0
    raw_bytes: int = 0
    stored_bytes: int = 0
    notes: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


def underlyings() -> List[str]:
    """What is recorded, in read order: the indices first, then the F&O stocks when that setting is on."""
    names = list(config.CHAIN_HISTORY_UNDERLYINGS) + (list(config.CHAIN_HISTORY_STOCKS) if config.CHAIN_HISTORY_STOCKS_ENABLED else [])
    out: List[str] = []
    for raw in names:
        name = " ".join(raw.upper().split())
        if name and name not in out:
            out.append(name)
    return out


def exchange_of(underlying: str) -> str:
    from app.instruments import master
    return master.INDEX_EXCHANGE.get(underlying, "NSE")


async def enabled(session: Any) -> bool:
    """The worker's gate: an owner tenant is configured and the flag is on for it."""
    from app.platform.controls import flag_enabled
    owner = config.HISTORY_OWNER_TENANT_ID
    return owner is not None and await flag_enabled(session, FLAG, owner)


def due(now: datetime, last_slot: Optional[datetime]) -> Optional[datetime]:
    """This slot's start when it has not been attempted yet, else None."""
    slot = chain_store.slot_start(now, config.CHAIN_HISTORY_SLOT_MINUTES)
    return None if last_slot == slot else slot


async def _plan(session: Any, now: datetime, report: SlotReport) -> List[Tuple[str, date]]:
    """(underlying, expiry) to read this slot: venue open, the nearest expiries from the instrument master, capped."""
    from app.instruments import master
    today = now.astimezone(IST).date()
    open_by_family: Dict[str, bool] = {}
    plan: List[Tuple[str, date]] = []
    for underlying in underlyings():
        family = session_family(exchange_of(underlying))
        if family not in open_by_family:
            open_by_family[family] = (await market_session_status(session, now, exchange_of(underlying))).is_open
        if not open_by_family[family]:
            continue
        expiries = (await master.expiries(session, underlying, on_or_after=today))[:config.CHAIN_HISTORY_EXPIRIES]
        if not expiries:
            report.notes.append(f"{underlying}: no option expiry in the instrument master")
            continue
        plan += [(underlying, e) for e in expiries]
    cap = config.CHAIN_HISTORY_MAX_READS_PER_SLOT
    if len(plan) > cap:
        report.notes.append(f"{len(plan) - cap} reads above CHAIN_HISTORY_MAX_READS_PER_SLOT ({cap}) not made")
        report.skipped += len(plan) - cap
        plan = plan[:cap]
    if not open_by_family or not any(open_by_family.values()):
        report.status = "closed"
    return plan


async def record_slot(session_factory: Any, now: Optional[datetime] = None, *, budget_for: Optional[BudgetFor] = None,
                      adapter_factory: Optional[AdapterFactory] = None, adapter_cache: Optional[Dict[Any, Any]] = None,
                      reserve: Optional[float] = None, token_wait_seconds: Optional[float] = None) -> SlotReport:
    """One slot of the chain history (see the module docstring). Never raises for a broker problem; returns what it did."""
    now = now or datetime.now(timezone.utc)
    began = monotonic_clock.monotonic()
    report = SlotReport()
    minutes = config.CHAIN_HISTORY_SLOT_MINUTES
    slot = chain_store.slot_start(now, minutes)
    report.slot = slot.astimezone(IST).isoformat()
    reserve = config.CHAIN_HISTORY_RESERVE if reserve is None else reserve
    wait = float(config.CHAIN_HISTORY_TOKEN_WAIT_SECONDS if token_wait_seconds is None else token_wait_seconds)
    async with session_factory() as session:
        try:
            tenant_id = await intraday_history.owner_tenant(session)
        except intraday_history.IntradayConfigError as exc:
            report.status = "not_configured"
            report.notes.append(str(exc))
            return report
        report.tenant_id = tenant_id
        if not await enabled(session):
            report.status = "off"
            return report
        plan = await _plan(session, now, report)
        if not plan:
            return report
        try:
            found = await intraday_history.owner_session(session, tenant_id, now)
        except intraday_history.IntradayConfigError as exc:
            report.status = "not_configured"
            report.notes.append(str(exc))
            return report
        if found is None:
            report.status = "no_session"
            report.skipped += len(plan)
            report.notes.append("no usable broker session of the owner's own today: nothing read (never another user's)")
            return report
        user, record = found
        todo: List[Tuple[str, date]] = []
        for underlying, expiry in plan:
            if await chain_store.exists(session, tenant_id, underlying, expiry, slot):
                report.already += 1
            else:
                todo.append((underlying, expiry))
    report.planned = len(plan)
    report.broker = record.broker_name
    source = intraday_history.holder_source(record.broker_name, user.id)
    budget = budget_for(tenant_id, f"{record.broker_name}@{record.account_label or 'primary'}") if budget_for else \
        RateBudget(limits_for(record.broker_name))
    adapter = intraday_history._adapter(record, adapter_factory or intraday_history._default_adapter, adapter_cache,
                                        now.astimezone(IST).date())
    from app.instruments import master
    for i, (underlying, expiry) in enumerate(todo):
        if not await budget.acquire_spare(reserve, deadline=budget.now() + wait):
            report.status = "rate_budget"
            report.skipped += len(todo) - i
            report.notes.append("the trading worker held the rate budget: the rest of this slot is a gap")
            break
        report.reads += 1
        started = monotonic_clock.monotonic()
        try:
            chain = await adapter.get_option_chain(master.INDEX_SYMBOLS.get(underlying, underlying), expiry)
        except BrokerAuthenticationError:
            report.status = "auth"
            report.skipped += len(todo) - i - 1
            report.notes.append("the owner's broker login was refused: log in to the broker again")
            break
        except BrokerAPIError as exc:
            if exc.status_code == 429:
                report.status = "rate_limited"
                report.skipped += len(todo) - i - 1
                report.notes.append("the broker answered 429: the rest of this slot is a gap")
                break
            report.notes.append(f"{underlying} {expiry}: the broker refused the read ({exc.status_code})")
            continue
        except Exception as exc:  # noqa: BLE001 - one chain's failure never stops the others (market data only)
            report.notes.append(f"{underlying} {expiry}: {type(exc).__name__}")
            continue
        async with session_factory() as own:
            try:
                captured = now + timedelta(seconds=monotonic_clock.monotonic() - began)      # the read's own time (data time)
                result = await chain_store.write(own, tenant_id, underlying, expiry, chain, captured, slot_minutes=minutes,
                                                 source=source, slot=slot)
                await own.commit()
            except Exception as exc:  # noqa: BLE001 - a bad chain is noted, the others are still stored
                await own.rollback()
                report.notes.append(f"{underlying} {expiry}: not stored ({type(exc).__name__})")
                continue
        if result.status == "OK":
            report.stored += 1
            report.raw_bytes += result.raw_bytes
            report.stored_bytes += result.stored_bytes
        elif result.status == "ALREADY_EXISTS":
            report.already += 1
        else:
            report.notes.append(f"{underlying} {expiry}: the broker returned no strikes")
        logger.debug("Chain history %s %s read in %.2fs", underlying, expiry, monotonic_clock.monotonic() - started)
    return report


async def status(session: Any, now: Optional[datetime] = None) -> Dict[str, Any]:
    """The admin card: what is recorded, today's chains per underlying, the last snapshot, today's gaps (counts only)."""
    from app.market_data.calendar import family_holidays
    now = now or datetime.now(timezone.utc)
    minutes = config.CHAIN_HISTORY_SLOT_MINUTES
    out: Dict[str, Any] = {
        "flag_on": False, "owner_configured": False, "owner_session": None, "slot_minutes": minutes,
        "expiries_per_underlying": config.CHAIN_HISTORY_EXPIRIES, "max_reads_per_slot": config.CHAIN_HISTORY_MAX_READS_PER_SLOT,
        "stocks_enabled": config.CHAIN_HISTORY_STOCKS_ENABLED, "underlyings": [], "totals": None, "problem": None,
        "checked_at": now.isoformat(),
    }
    try:
        tenant_id = await intraday_history.owner_tenant(session)
    except intraday_history.IntradayConfigError:
        out["problem"] = "The owner's organisation is not set (HISTORY_OWNER_TENANT_ID and HISTORY_OWNER_TENANT_NAME)."
        out["underlyings"] = [{"underlying": u, "exchange": exchange_of(u)} for u in underlyings()]
        return out
    out["owner_configured"] = True
    out["flag_on"] = await enabled(session)
    try:
        found = await intraday_history.owner_session(session, tenant_id, now)
    except intraday_history.IntradayConfigError:
        found = None
    out["owner_session"] = {"broker": found[1].broker_name} if found else None
    if not out["flag_on"]:
        out["problem"] = "Recording is off: turn on 'chain_history' for the owner's organisation in Controls."
    elif found is None:
        out["problem"] = "No usable broker session of the owner's own today: nothing is recorded until the owner logs in."
    day = now.astimezone(IST).date()
    stored = await chain_store.day_slots(session, tenant_id, day)
    rows: List[Dict[str, Any]] = []
    holidays: Dict[str, Any] = {}
    for underlying in underlyings():
        exchange = exchange_of(underlying)
        family = session_family(exchange)
        if family not in holidays:
            holidays[family] = await family_holidays(session, family)
        entry = stored.get(underlying) or {"chains": 0, "slots": set(), "raw_bytes": 0, "stored_bytes": 0, "last_captured_at": None}
        trading_day = day.weekday() < 5 and day not in holidays[family]
        expected = chain_store.expected_slots(day, now, minutes, exchange) if trading_day else []
        gaps = chain_store.gap_ranges(expected, entry["slots"], minutes)
        rows.append({"underlying": underlying, "exchange": exchange, "trading_day": trading_day, "chains_today": entry["chains"],
                     "slots_today": len(entry["slots"]), "slots_expected": len(expected),
                     "missing_slots": len([s for s in expected if s not in entry["slots"]]), "gaps": gaps[-10:],
                     "stored_bytes_today": entry["stored_bytes"], "raw_bytes_today": entry["raw_bytes"],
                     "last_captured_at": entry["last_captured_at"].isoformat() if entry["last_captured_at"] else None})
    out["underlyings"] = rows
    out["totals"] = await chain_store.totals(session, tenant_id)
    return out


__all__ = ["FLAG", "SlotReport", "underlyings", "enabled", "due", "record_slot", "status"]
