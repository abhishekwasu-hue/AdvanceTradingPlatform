"""Owner order (3 Nov): the day's Daily bar of the indices is readable soon after the close (DAILY_PROVISIONAL_AT_IST,
default 15:45 IST), not only after NSE's evening index file has been loaded (HB1, app/market_lake/bhavcopy_backfill.py).

What is written: per index the lake keeps history of (LAKE_BACKFILL_INDICES, the DATA-H targets) ONE Daily bar of TODAY's
closed session - only on a trading day (the NSE holiday calendar), only after the session has closed, never when NSE's
index file for the day is already stored, and never twice (a day that already has a provisional bar is skipped: an
idempotent re-run). Nothing is invented: no data -> no bar.

Where it comes from (labelled in the row's source, `how_of`):
1. the history owner's OWN usable broker session (HB2's owner, `intraday_history.owner_session`; never another user's)
   through the read-only candle endpoints DATA-H already uses: the broker's Daily candle of today (FROM_BROKER_DAY) or,
   when its historical endpoint has no "today" (Upstox), today's 1-minute bars from its intraday endpoint
   (FROM_BROKER_MINUTES); every request takes the credential's shared API budget;
2. else his STORED 1-minute bars of today (FROM_STORED_MINUTES), closing-auction flags applied (DH3).
A bar built from 1-minute bars reads the continuous session only: O the first open, H the max, L the min, C the last
close, V the sum - and only when the bars cover the session from its open to its close (else no bar).

Where it lives (licence; docs/COMPLIANCE_IN.md "Data licences", OPEN_QUESTIONS M55Q-1): the shared lake (`md_candles`)
serves only licensed sources (NSE bhavcopy, vendor, the operator's import - price_action_layer.SHARED_SOURCES,
bar_source.LICENSED_PREFIXES), and the stance is "never the owner's broker Daily rows to another organisation". So the
bar goes to the history owner's PRIVATE store (`md_tenant_candles`), timeframe own_bars.PROVISIONAL_LABEL, source
`broker:<how>:u<holder>` - read only by that holder (tenant_store.holder_visible), never through a SharedScope.

How it is read (own_bars.provisional_rows): the chart layer / Copilot Daily frame appends it only AFTER the frame's own
last bar - so once NSE's index file of the day is stored (it ends the frame on that day) it wins automatically; the
Copilot's last close says "provisional close" for it, never "official close" (own_bars.official_close).
"""
from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import config
from app.db.models import MdCandleRecord, MdTenantCandleRecord
from app.market_data.calendar import IST, is_trading_day
from app.market_lake import index_history as dh
from app.market_lake.ingest import LakeBar, write_candles_bulk
from app.market_lake.own_bars import PROVISIONAL_LABEL

logger = logging.getLogger(__name__)

LOCK_KEY = 0x48420073                      # pg advisory lock id ("HB" 0073): one provisional pass at a time
TIMEFRAME = PROVISIONAL_LABEL
FROM_BROKER_DAY, FROM_BROKER_MINUTES, FROM_STORED_MINUTES = "day", "1m", "store1m"
NSE_PREFIX = "nse_bhav:"
Clock = Callable[[], datetime]


def source_for(how: str, holder: int, broker: str | None = None) -> str:
    """broker:<broker>[-1m]:u<holder> (from his session) or broker:store1m:u<holder> (his stored 1-minute bars) - the
    holder is IN the row (BT1b: only he reads it)."""
    if how == FROM_STORED_MINUTES:
        return f"broker:{FROM_STORED_MINUTES}:u{int(holder)}"
    name = (broker or "broker").strip().lower()
    return f"broker:{name}-1m:u{int(holder)}" if how == FROM_BROKER_MINUTES else f"broker:{name}:u{int(holder)}"


def how_of(source: str) -> str:
    """Which of the three ways a provisional row's source says it was made."""
    if source.startswith(f"broker:{FROM_STORED_MINUTES}:"):
        return FROM_STORED_MINUTES
    return FROM_BROKER_MINUTES if source.rsplit(":u", 1)[0].endswith("-1m") else FROM_BROKER_DAY


def due(now: datetime, last_day: date | None) -> date | None:
    """The IST date a pass is due for: from DAILY_PROVISIONAL_AT_IST (and never before the session close), once per
    finished day; None when the setting is off."""
    at = config.DAILY_PROVISIONAL_AT_IST
    if at is None:
        return None
    ist = now.astimezone(IST)
    if ist.time() < max(at, dh.SESSION.close) or last_day == ist.date():
        return None
    return ist.date()


def bar_from_minutes(key: str, day: date, minutes: Sequence[Any]) -> LakeBar | None:
    """Today's Daily bar from END-labelled 1-minute bars (LakeBar or the readers' row tuples (ts, version, o, h, l, c,
    v)): the continuous session only (END in (open, close]). None unless the bars run from the open to the close."""
    open_end = datetime.combine(day, dh.SESSION.open, tzinfo=IST).astimezone(UTC) + timedelta(minutes=1)
    close_end = dh.session_close(day)
    rows = []
    for m in minutes:
        if isinstance(m, LakeBar):
            ts, o, h, lo, c, v = m.ts, m.open, m.high, m.low, m.close, m.volume
        else:
            ts, o, h, lo, c, v = m[0], m[2], m[3], m[4], m[5], m[6]
        ts = dh._utc(ts)                                       # noqa: SLF001 - the same UTC rule as the backfill
        if open_end <= ts <= close_end:
            rows.append((ts, float(o), float(h), float(lo), float(c), int(v or 0)))
    if not rows:
        return None
    rows.sort(key=lambda r: r[0])
    if rows[0][0] != open_end or rows[-1][0] != close_end:
        return None                                            # a part of the session only: never a made-up Daily bar
    return LakeBar(key, TIMEFRAME, close_end, rows[0][1], max(r[2] for r in rows), min(r[3] for r in rows), rows[-1][4],
                   sum(r[5] for r in rows))


@dataclass
class ProvisionalReport:
    status: str = "ok"          # ok | not_due | holiday | not_configured | locked
    day: date | None = None
    written: dict[str, str] = field(default_factory=dict)     # key -> how
    nse_in: list[str] = field(default_factory=list)            # NSE's index file of the day is already stored
    already: list[str] = field(default_factory=list)           # a provisional bar of the day is already stored
    no_data: list[str] = field(default_factory=list)           # no usable bar (no session, the bars do not cover the day)
    notes: list[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        """The day needs no other try: nothing missing (or nothing to do on this day)."""
        return self.status in ("holiday", "not_configured") or (self.status == "ok" and not self.no_data)

    def lines(self) -> list[str]:
        out = [f"provisional-daily {self.day}: {self.status}; written {len(self.written)} "
               f"({', '.join(f'{k} {h}' for k, h in sorted(self.written.items())) or '-'}), NSE file in {len(self.nse_in)}, "
               f"already {len(self.already)}, no data {len(self.no_data)}"]
        return out + [f"  note: {n}" for n in self.notes[:6]]


async def _try_lock(session: AsyncSession) -> bool:
    if session.get_bind().dialect.name != "postgresql":
        return True
    return bool(await session.scalar(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": LOCK_KEY}))


async def _nse_in(session: AsyncSession, key: str, day: date) -> bool:
    return (await session.scalar(select(MdCandleRecord.ts).where(
        MdCandleRecord.instrument_key == key, MdCandleRecord.timeframe.in_(("day", "1d")), MdCandleRecord.ts == dh.session_close(day),
        MdCandleRecord.source.like(f"{NSE_PREFIX}%")).limit(1))) is not None


async def _already(session: AsyncSession, tenant_id: int, holder: int, key: str, day: date) -> bool:
    from app.market_lake.tenant_store import holder_visible
    return (await session.scalar(select(MdTenantCandleRecord.ts).where(
        MdTenantCandleRecord.tenant_id == tenant_id, MdTenantCandleRecord.instrument_key == key, MdTenantCandleRecord.timeframe == TIMEFRAME,
        MdTenantCandleRecord.ts == dh.session_close(day), holder_visible(MdTenantCandleRecord.source, holder)).limit(1))) is not None


async def _from_broker(adapter: Any, t: dh.Target, day: date, fetched_at: datetime) -> tuple[LakeBar | None, str]:
    """(bar, how) from the owner's session: the broker's Daily candle of `day`, else its 1-minute bars of `day`."""
    lo, hi = datetime.combine(day, time(0, 0), tzinfo=IST), datetime.combine(day, time(23, 59), tzinfo=IST)
    raw = list(await adapter.get_historical_data(t.symbol, t.exchange, dh.BROKER_INTERVAL[dh.DAILY], lo, hi) or [])
    bars, _forming = dh.to_lake_bars(t, dh.DAILY, raw, fetched_at)
    end = dh.session_close(day)
    hit = next((b for b in bars if dh._utc(b.ts) == end), None)          # noqa: SLF001
    if hit is not None:
        return LakeBar(t.key, TIMEFRAME, end, hit.open, hit.high, hit.low, hit.close, hit.volume), FROM_BROKER_DAY
    if not hasattr(adapter, "get_intraday_candles"):
        return None, FROM_BROKER_MINUTES
    raw = list(await adapter.get_intraday_candles(t.symbol, t.exchange, dh.BROKER_INTERVAL[dh.MINUTE]) or [])
    minutes, _forming = dh.to_lake_bars(t, dh.MINUTE, raw, fetched_at)
    return bar_from_minutes(t.key, day, minutes), FROM_BROKER_MINUTES


async def _from_store(session: AsyncSession, scope: tuple[int, int], t: dh.Target, day: date) -> LakeBar | None:
    from app.market_lake import own_bars
    lo = datetime.combine(day, dh.SESSION.open, tzinfo=IST).astimezone(UTC)
    rows = await own_bars.fetch_rows(session, scope, t.key, dh.MINUTE, lo, dh.session_close(day), limit=2000)
    return bar_from_minutes(t.key, day, rows)


async def run_pass(session_factory: Any, *, now: datetime | None = None, names: Sequence[str] | None = None,
                   adapter_factory: Callable[[Any], Any] | None = None, wrap: Callable[[Any, Any], Any] | None = None,
                   clock: Clock | None = None) -> ProvisionalReport:
    """One pass for today's IST date (see the module docstring). Read-only towards the broker; writes only the owner's
    private store; one pass at a time."""
    now = now or datetime.now(UTC)
    clock = clock or (lambda: datetime.now(UTC))
    day = now.astimezone(IST).date()
    report = ProvisionalReport(day=day)
    if now < dh.session_close(day):
        report.status = "not_due"
        report.notes.append("the session has not closed yet")
        return report
    tgts, notes = dh.targets(names)
    report.notes += notes
    async with session_factory() as lock_session:
        if not await _try_lock(lock_session):
            report.status = "locked"
            return report
        try:
            async with session_factory() as session:
                if not is_trading_day(day, await dh._holidays(session, day.year, day.year)):   # noqa: SLF001
                    report.status = "holiday"
                    return report
                from app.market_lake import intraday_history
                try:
                    tenant_id = await intraday_history.owner_tenant(session)
                    user = await intraday_history.owner_user(session, tenant_id)
                except intraday_history.IntradayConfigError as exc:
                    report.status = "not_configured"
                    report.notes.append(str(exc))
                    return report
                if user is None:
                    report.status = "not_configured"
                    report.notes.append("the history owner has no active user")
                    return report
                holder = int(user.id)
                found = await intraday_history.owner_session(session, tenant_id, now)
            adapter = None
            record = found[1] if found is not None else None
            if record is not None:
                inner = (adapter_factory or dh._default_adapter)(record)               # noqa: SLF001
                adapter = (wrap or dh._budgeted)(inner, record)                       # noqa: SLF001
            else:
                report.notes.append("no usable broker session of the owner's own now - built from his stored 1-minute bars")
            for t in tgts:
                async with session_factory() as session:
                    if await _nse_in(session, t.key, day):
                        report.nse_in.append(t.key)
                        continue
                    if await _already(session, tenant_id, holder, t.key, day):
                        report.already.append(t.key)
                        continue
                bar, how = None, FROM_STORED_MINUTES
                if adapter is not None:
                    try:
                        bar, how = await _from_broker(adapter, t, day, clock())
                    except Exception as exc:  # noqa: BLE001 - a broker refusal falls back to the stored bars, never a guess
                        report.notes.append(f"{t.name}: the broker did not answer ({dh.scrub(type(exc).__name__)})")
                        bar = None
                if bar is None:
                    async with session_factory() as session:
                        bar, how = await _from_store(session, (tenant_id, holder), t, day), FROM_STORED_MINUTES
                if bar is None:
                    report.no_data.append(t.key)
                    continue
                async with session_factory() as session:
                    await write_candles_bulk(session, [bar], source_for(how, holder, record.broker_name if record is not None else None),
                                             tenant_id=tenant_id, ingested_at=dh._utc(clock()))   # noqa: SLF001
                    await session.commit()
                report.written[t.key] = how
            logger.info("Provisional Daily: %s", " | ".join(report.lines()))
            return report
        finally:
            await lock_session.rollback()


__all__ = ["TIMEFRAME", "FROM_BROKER_DAY", "FROM_BROKER_MINUTES", "FROM_STORED_MINUTES", "source_for", "how_of", "due",
           "bar_from_minutes", "ProvisionalReport", "run_pass"]
