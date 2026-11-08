"""M88-R S4: when the report runs - once the day's bhavcopy is in the store (an operational schedule, not a reading
rule). Asked every worker cycle; cheap reads only:

* the flag is on for anyone and the Shared report symbols setting is not empty;
* the day = the newest close stored for the setting's first symbol in the licensed NSE daily files, and HB1 is not
  still loading that day's cash / F&O files (`history_backfill_files` pending / running);
* that day has no stored report yet, no run is going and a failed run's retry time has come (at most
  DAILY_REPORT_MAX_ATTEMPTS a day; the admin's Run now is always allowed).
"""
from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import config
from app.daily_report import FLAG
from app.daily_report import settings as S
from app.daily_report.build import RUNNING_STALE, _aware
from app.db.models import HistoryBackfillFileRecord, MdCandleRecord, SharedDailyReportRecord
from app.market_data.calendar import IST

logger = logging.getLogger(__name__)

WAIT_STATES = ("pending", "running")


async def any_enabled(session: AsyncSession) -> bool:
    from app.platform.controls import feature_flags
    entry = (await feature_flags(session)).get(FLAG) or {}
    return bool(entry.get("on") or entry.get("tenants"))


def _probe_symbol(entries: list[str]) -> str:
    from app.daily_report.universe import SUFFIX
    first = entries[0]
    return first[:-len(SUFFIX)].strip() if first.endswith(SUFFIX) else first


async def newest_close_day(session: AsyncSession, symbol: str) -> date | None:
    from app.ai.tools.copilot import lake_key
    newest = await session.scalar(select(func.max(MdCandleRecord.ts)).where(
        MdCandleRecord.instrument_key == lake_key(symbol), MdCandleRecord.timeframe == "day",
        MdCandleRecord.source.startswith("nse_bhav:")))
    if newest is None:
        return None
    return _aware(newest).astimezone(IST).date()  # type: ignore[union-attr]


async def files_loading(session: AsyncSession, day: date) -> bool:
    status = await session.scalars(select(HistoryBackfillFileRecord.status).where(
        HistoryBackfillFileRecord.trade_date == day, HistoryBackfillFileRecord.segment.in_(("cm", "fo"))))
    return any(s in WAIT_STATES for s in status)


async def due_day(session: AsyncSession, now: datetime | None = None) -> date | None:
    now = now or datetime.now(UTC)
    if not await any_enabled(session):
        return None
    cfg = await S.read(session)
    if not cfg["symbols"] or cfg["key_user_id"] is None:
        return None
    day = await newest_close_day(session, _probe_symbol(cfg["symbols"]))
    if day is None or await files_loading(session, day):
        return None
    row = await session.scalar(select(SharedDailyReportRecord).where(SharedDailyReportRecord.trade_date == day))
    if row is None:
        return day
    if row.status == "ok":
        return None
    if row.status == "running" and now - (_aware(row.updated_at) or now) < RUNNING_STALE:
        return None
    if row.attempts >= config.DAILY_REPORT_MAX_ATTEMPTS:
        return None
    next_try = _aware(row.next_try_at)
    return day if next_try is None or next_try <= now else None


async def run_due(session_factory: Any, now: datetime | None = None, *, provider: Any = None, source: Any = None) -> dict[str, Any]:
    """The worker's pass: run the due day's report (if any) on the key of the admin who saved the setting."""
    from app.daily_report import build
    from app.db.models import User
    now = now or datetime.now(UTC)
    async with session_factory() as session:
        day = await due_day(session, now)
        if day is None:
            return {"ran": False, "telegram": await send_pending_any(session)}
        cfg = await S.read(session)
        user = await session.get(User, cfg["key_user_id"])
        if user is None or not user.is_active or str(getattr(user.role, "value", user.role)) != "SUPER_ADMIN":
            return {"ran": False, "reason": "the admin who saved the setting is not an active admin"}
        try:
            row = await build.run(session, day, user=user, trigger="schedule", provider=provider, source=source, now=now,
                                  on_stored=after_stored)
        except build.NothingToRun as exc:
            return {"ran": False, "reason": str(exc)}
        return {"ran": True, "day": day.isoformat(), "status": row.status}


async def after_stored(session: AsyncSession, row: SharedDailyReportRecord) -> None:
    """M89: a stored report goes to every user's own Telegram chat once (queued, paced; nothing while the flag is off)."""
    from app.daily_report import telegram
    try:
        result = await telegram.deliver(session, int(row.id))
        logger.info("Daily report %s on Telegram: %s", row.trade_date, result)
    except Exception:  # noqa: BLE001 - the stored report stands; the worker's next pass sends what is still queued
        logger.exception("Daily report Telegram send failed")
        await session.rollback()


async def send_pending_any(session: AsyncSession) -> dict[str, int] | None:
    """M89: the retry pass - the newest report with sends still queued or retryable."""
    from app.daily_report import telegram
    if not await any_enabled(session):
        return None
    report_id = await telegram.pending_report(session)
    return None if report_id is None else await telegram.send_pending(session, report_id)


async def work_waiting(session: AsyncSession, now: datetime | None = None) -> bool:
    """A report is due, or sends are waiting (the worker starts its task only then)."""
    from app.daily_report import telegram
    if await due_day(session, now) is not None:
        return True
    return await any_enabled(session) and await telegram.pending_report(session) is not None


__all__ = ["due_day", "run_due", "any_enabled", "newest_close_day", "files_loading", "after_stored", "send_pending_any", "work_waiting"]
