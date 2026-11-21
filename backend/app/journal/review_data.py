"""P2-8 J3: reads one period of his journal into `reviews.Unit`s (the database part of the reviews).

A unit is one decision: a single ATP trade, a whole structure (its legs summed, `service.review_trades`), or an
outside-ATP entry. His check-in is the one on the unit's first leg (the entry sheet opens there). Tags: the behaviour
tags he did not dismiss; `confirmed` = the ones he confirmed. Only his own rows (user + organisation).
"""
from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    JournalCheckinRecord,
    JournalEntryRecord,
    JournalManualTradeRecord,
    JournalReflectionRecord,
    JournalTagRecord,
    TradeRecord,
    User,
)
from app.journal import capture, entries, service
from app.journal.detectors import is_systematic_exit
from app.journal.reviews import Gate, Unit
from app.journal.settings import DisciplineSettings
from app.market_data.calendar import IST


def day_bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time(0, 0), tzinfo=IST).astimezone(UTC)
    return start, start + timedelta(days=1)


def month_bounds(label: str) -> tuple[datetime, datetime]:
    year, month = (int(x) for x in label.split("-"))
    start = datetime(year, month, 1, tzinfo=IST)
    end = datetime(year + (month == 12), month % 12 + 1, 1, tzinfo=IST)
    return start.astimezone(UTC), end.astimezone(UTC)


def month_weeks(label: str) -> list[str]:
    """The ISO weeks whose Monday falls in the month (each week counted in one month only)."""
    year, month = (int(x) for x in label.split("-"))
    day = date(year, month, 1)
    out: list[str] = []
    while day.month == month:
        if day.weekday() == 0:
            iso = day.isocalendar()
            out.append(f"{iso[0]}-W{iso[1]:02d}")
        day += timedelta(days=1)
    return out


async def closed_total(session: AsyncSession, user: User) -> int:
    """His closed decisions in all (a structure once; outside-ATP entries too): the first-20 gate's count."""
    rows = (await session.execute(select(TradeRecord.id, TradeRecord.leg_group_id).where(
        TradeRecord.user_id == user.id, TradeRecord.tenant_id == user.tenant_id, TradeRecord.exit_time.is_not(None)))).all()
    units = {f"g:{g}" if g else f"t:{i}" for i, g in rows}
    manual = await session.scalar(select(func.count()).select_from(JournalManualTradeRecord).where(
        JournalManualTradeRecord.user_id == user.id, JournalManualTradeRecord.tenant_id == user.tenant_id,
        JournalManualTradeRecord.exit_time.is_not(None))) or 0
    return len(units) + int(manual)


async def gate(session: AsyncSession, user: User, s: DisciplineSettings) -> Gate:
    return Gate(await closed_total(session, user), int(s.min_closed_for_review))


async def units(session: AsyncSession, user: User, start: datetime, end: datetime) -> list[Unit]:
    trades = await service.own_trades(session, user, start, end)
    manual = await entries.manual_in(session, user, start, end)
    ids = [t.id for t in trades] or [-1]
    checkins = {(c.trade_id, c.manual_trade_id): c for c in await session.scalars(select(JournalCheckinRecord).where(
        JournalCheckinRecord.user_id == user.id, JournalCheckinRecord.tenant_id == user.tenant_id))}
    tags: dict[int, list[JournalTagRecord]] = {}
    for tag in await session.scalars(select(JournalTagRecord).where(JournalTagRecord.user_id == user.id, JournalTagRecord.trade_id.in_(ids))):
        tags.setdefault(tag.trade_id, []).append(tag)
    journal = {e.trade_id: e for e in await session.scalars(select(JournalEntryRecord).where(
        JournalEntryRecord.user_id == user.id, JournalEntryRecord.trade_id.in_(ids)))}
    by_id = {t.id: t for t in trades}
    out: list[tuple[str, Unit]] = []
    for rt in service.review_trades(trades):
        legs = [by_id[i] for i in rt.trade_ids if i in by_id]
        first = legs[0]
        c = checkins.get((first.id, None))
        live = [x for leg in legs for x in tags.get(leg.id, []) if x.status != "DISMISSED"]
        mae = mfe = None
        if len(legs) == 1 and not first.leg_group_id:
            exc = service.excursions(first, journal.get(first.id))
            qty = abs(float(first.quantity))
            mae = exc["mae"] * qty if exc["mae"] is not None else None
            mfe = exc["mfe"] * qty if exc["mfe"] is not None else None
            stop_set = bool(first.initial_stop_loss if first.initial_stop_loss is not None else first.stop_loss)
        else:
            stop_set = service._has_group_stop(first)
        when = service._utc(first.entry_time)
        ended = [x for x in (service._utc(leg.exit_time) for leg in legs) if x is not None]
        plan = capture._loads(journal[first.id].plan_json, {}) if first.id in journal else capture.plan_of(first)
        out.append((when.isoformat() if when else "", Unit(
            id=first.id, kind="trade", symbol=first.symbol, setup=first.strategy_id, entry_time=when.isoformat() if when else None,
            pnl=rt.pnl, stop_set=stop_set, exit_by_rule=rt.pnl is not None and all(is_systematic_exit(leg.exit_reason) for leg in legs),
            from_signal=plan.get("entry_source") == "signal",
            exit_reason=next((leg.exit_reason for leg in legs if leg.exit_reason), None), exit_time=max(ended).isoformat() if ended and rt.pnl is not None else None, tags=tuple(sorted({x.tag for x in live})),
            confirmed=tuple(sorted({x.tag for x in live if x.status == "CONFIRMED"})), mae_amount=mae, mfe_amount=mfe,
            chart=f"/api/journal/trades/{first.id}/chart?at=exit" if first.exit_time else f"/api/journal/trades/{first.id}/chart?at=entry",
            **_answers(c))))
    for m in manual:
        c = checkins.get((None, m.id))
        when = entries._utc(m.entry_time)
        out.append((when.isoformat() if when else "", Unit(
            id=m.id, kind="manual", symbol=m.symbol, setup=m.setup, entry_time=when.isoformat() if when else None,
            pnl=entries.manual_pnl(m), stop_set=m.stop_loss is not None,
            exit_time=entries._iso(entries._utc(m.exit_time)) if m.exit_time else None, exit_reason=m.exit_reason, chart=f"/api/journal/manual/{m.id}/chart?at=entry", **_answers(c))))
    out.sort(key=lambda x: x[0])
    return [Unit(**{**u.__dict__, "entry_order": i}) for i, (_, u) in enumerate(out)]


def _answers(c: JournalCheckinRecord | None) -> dict[str, Any]:
    if c is None:
        return {}
    return {"feeling_before": c.feeling_before, "feeling_after": c.feeling_after, "confidence": c.confidence,
            "plan_followed": c.plan_followed, "mistake": c.mistake, "lesson": c.lesson}


async def answer_of(session: AsyncSession, user: User, period: str) -> str | None:
    return await session.scalar(select(JournalReflectionRecord.answer).where(
        JournalReflectionRecord.user_id == user.id, JournalReflectionRecord.tenant_id == user.tenant_id,
        JournalReflectionRecord.period == period))


async def save_answer(session: AsyncSession, user: User, period: str, answer: str) -> str | None:
    row = await session.scalar(select(JournalReflectionRecord).where(
        JournalReflectionRecord.user_id == user.id, JournalReflectionRecord.tenant_id == user.tenant_id,
        JournalReflectionRecord.period == period))
    text = answer.strip()
    if not text:
        if row is not None:
            await session.delete(row)
            await session.commit()
        return None
    if row is None:
        session.add(JournalReflectionRecord(tenant_id=user.tenant_id, user_id=user.id, period=period, answer=text))
    else:
        row.answer = text
    await session.commit()
    return text


__all__ = ["day_bounds", "month_bounds", "month_weeks", "closed_total", "gate", "units", "answer_of", "save_answer"]
