"""P2-8 J4 + J5: the database part of "My proof", his if-then plans and process goals, and the Journal's Today tab.

His own words only: an if-then plan is the text he typed ("If ..., then ..."), shown back to him before the market
opens and, if he chose, after a matching event (a stop hit / a loss); a goal is his own process measure and target.
Nothing here writes a plan for him, judges him or blocks anything; no order path reads these tables.
"""
from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import JournalPlanRecord, JournalProofRecord, User
from app.journal import proof, review_data
from app.journal.entries import EntryError
from app.journal.settings import DisciplineSettings

PROOF_DAYS = 90          # facts are collected from the last 90 days
MAX_PLANS = 20
MAX_OWN_PROOFS = 100
REMIND_HOURS = 24        # an if-then plan is shown again for this long after a matching event


def _text(name: str, value: Any, longest: int, *, required: bool = False) -> str | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            raise EntryError(f"Write the {name} first")
        return None
    if not isinstance(value, str):
        raise EntryError(f"{name} must be text")
    text = " ".join(value.split())
    if len(text) > longest:
        raise EntryError(f"{name} may have at most {longest} characters")
    return text


def clean_plan(body: dict[str, Any]) -> dict[str, Any]:
    """An if-then plan or a goal as he wrote it (pure). Raises EntryError naming the first problem."""
    kind = str(body.get("kind") or "").upper()
    if kind == "IF_THEN":
        trigger = str(body.get("trigger") or "NONE").upper()
        if trigger not in proof.TRIGGERS:
            raise EntryError("trigger must be NONE, STOP_HIT or LOSS")
        return {"kind": kind, "if_text": _text("if part", body.get("if_text"), 200, required=True),
                "then_text": _text("then part", body.get("then_text"), 300, required=True), "trigger": trigger,
                "metric": None, "target_pct": None, "period": None}
    if kind == "GOAL":
        metric = str(body.get("metric") or "").upper()
        if metric not in proof.METRICS:
            raise EntryError(f"metric must be one of {sorted(proof.METRICS)}")
        target = body.get("target_pct")
        if isinstance(target, bool) or not isinstance(target, int) or not 1 <= target <= 100:
            raise EntryError("target_pct must be a whole number from 1 to 100")
        period = str(body.get("period") or "").upper()
        if period not in proof.PERIODS:
            raise EntryError("period must be WEEK or MONTH")
        return {"kind": kind, "if_text": None, "then_text": None, "trigger": "NONE", "metric": metric, "target_pct": target,
                "period": period}
    raise EntryError("kind must be IF_THEN or GOAL")


def plan_dict(row: JournalPlanRecord) -> dict[str, Any]:
    return {"id": row.id, "kind": row.kind, "if_text": row.if_text, "then_text": row.then_text, "trigger": row.trigger,
            "metric": row.metric, "metric_label": proof.METRICS.get(row.metric or "", None), "target_pct": row.target_pct,
            "period": row.period, "active": row.active}


async def plans_of(session: AsyncSession, user: User, *, active_only: bool = False) -> list[JournalPlanRecord]:
    q = select(JournalPlanRecord).where(JournalPlanRecord.user_id == user.id, JournalPlanRecord.tenant_id == user.tenant_id)
    if active_only:
        q = q.where(JournalPlanRecord.active.is_(True))
    return list(await session.scalars(q.order_by(JournalPlanRecord.id)))


async def own_plan(session: AsyncSession, user: User, plan_id: int) -> JournalPlanRecord | None:
    return await session.scalar(select(JournalPlanRecord).where(JournalPlanRecord.id == plan_id, JournalPlanRecord.user_id == user.id,
                                                                JournalPlanRecord.tenant_id == user.tenant_id))


async def add_plan(session: AsyncSession, user: User, body: dict[str, Any]) -> JournalPlanRecord:
    values = clean_plan(body)
    if len(await plans_of(session, user)) >= MAX_PLANS:
        raise EntryError(f"You have {MAX_PLANS} plans. Remove one first.", 409)
    row = JournalPlanRecord(tenant_id=user.tenant_id, user_id=user.id, **values)
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def update_plan(session: AsyncSession, row: JournalPlanRecord, body: dict[str, Any]) -> JournalPlanRecord:
    if set(body) - {"active"}:
        merged = {**plan_dict(row), **body}
        for key, value in clean_plan(merged).items():
            setattr(row, key, value)
    if "active" in body:
        if not isinstance(body["active"], bool):
            raise EntryError("active must be true or false")
        row.active = body["active"]
    await session.commit()
    await session.refresh(row)
    return row


# --- My proof -----------------------------------------------------------------------------------------------------------

async def _units(session: AsyncSession, user: User, days: int, now: datetime) -> list[Any]:
    return await review_data.units(session, user, now - timedelta(days=days), now + timedelta(minutes=1))


async def proof_page(session: AsyncSession, user: User, *, now: datetime | None = None) -> dict[str, Any]:
    """His facts of good process (last PROOF_DAYS days) with his own entries, pinned first, then newest first."""
    now = now or datetime.now(UTC)
    units = await _units(session, user, PROOF_DAYS, now)
    found = proof.facts(units)
    rows = list(await session.scalars(select(JournalProofRecord).where(JournalProofRecord.user_id == user.id,
                                                                       JournalProofRecord.tenant_id == user.tenant_id)))
    pins = {r.fact_key for r in rows if r.pinned}
    items = [{**f.to_dict(), "pinned": f.key in pins} for f in found]
    for r in rows:
        if r.fact_key.startswith("own:"):
            items.append({"key": r.fact_key, "id": r.id, "kind": "own", "text": r.text, "at": r.happened_on.isoformat() if r.happened_on else None,
                          "entry_kind": "trade" if r.trade_id else None, "entry_id": r.trade_id, "chart": None, "own": True, "pinned": r.pinned})
    items.sort(key=lambda x: (x["at"] or "", x["key"]), reverse=True)
    items.sort(key=lambda x: not x["pinned"])
    last = proof.last_good(found)
    return {"items": items, "last_good": last.to_dict() if last else None, "after_loss": proof.after_loss_line(units),
            "days": PROOF_DAYS}


async def add_own_proof(session: AsyncSession, user: User, text: Any, happened_on: Any) -> JournalProofRecord:
    words = _text("entry", text, 300, required=True)
    when: date | None = None
    if happened_on is not None:
        try:
            when = date.fromisoformat(str(happened_on))
        except ValueError:
            raise EntryError("happened_on must be a date (YYYY-MM-DD)") from None
    rows = await session.scalars(select(JournalProofRecord.id).where(JournalProofRecord.user_id == user.id,
                                                                     JournalProofRecord.fact_key.like("own:%")))
    if len(list(rows)) >= MAX_OWN_PROOFS:
        raise EntryError(f"You have {MAX_OWN_PROOFS} entries of your own. Remove one first.", 409)
    row = JournalProofRecord(tenant_id=user.tenant_id, user_id=user.id, fact_key=f"own:{uuid.uuid4().hex[:16]}", text=words,
                             happened_on=when or datetime.now(UTC).date(), pinned=False)
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def pin(session: AsyncSession, user: User, key: str, pinned: bool) -> None:
    if not key or len(key) > 80:
        raise EntryError("key is not valid")
    row = await session.scalar(select(JournalProofRecord).where(JournalProofRecord.user_id == user.id, JournalProofRecord.fact_key == key))
    if row is None:
        if key.startswith("own:"):
            raise EntryError("No such entry", 404)
        row = JournalProofRecord(tenant_id=user.tenant_id, user_id=user.id, fact_key=key, pinned=pinned)
        session.add(row)
    else:
        row.pinned = pinned
    await session.commit()


async def remove_own_proof(session: AsyncSession, user: User, proof_id: int) -> bool:
    row = await session.scalar(select(JournalProofRecord).where(JournalProofRecord.id == proof_id, JournalProofRecord.user_id == user.id,
                                                                JournalProofRecord.tenant_id == user.tenant_id,
                                                                JournalProofRecord.fact_key.like("own:%")))
    if row is None:
        return False
    await session.delete(row)
    await session.commit()
    return True


# --- goals and the Today tab -------------------------------------------------------------------------------------------

def _period_start(period: str, now: datetime) -> datetime:
    from app.market_data.calendar import IST
    ist = now.astimezone(IST)
    day = ist.date() - timedelta(days=ist.weekday()) if period == "WEEK" else ist.date().replace(day=1)
    return review_data.day_bounds(day)[0]


async def goals_progress(session: AsyncSession, user: User, s: DisciplineSettings, *, now: datetime | None = None) -> list[dict[str, Any]]:
    now = now or datetime.now(UTC)
    out = []
    for row in await plans_of(session, user, active_only=True):
        if row.kind != "GOAL" or not row.metric or not row.period:
            continue
        units = await review_data.units(session, user, _period_start(row.period, now), now + timedelta(minutes=1))
        out.append({"id": row.id, "period": row.period, **proof.goal_progress(row.metric, int(row.target_pct or 0), units,
                                                                                int(s.min_closed_for_review))})
    return out


async def today(session: AsyncSession, user: User, s: DisciplineSettings, *, now: datetime | None = None) -> dict[str, Any]:
    """The Journal's Today tab: his if-then plans (the pre-market card), the ones a recent event brings back, his goals'
    progress, his last good decision and, after a loss, the calm fact about his plan."""
    now = now or datetime.now(UTC)
    recent = await _units(session, user, PROOF_DAYS, now)
    plans = [r for r in await plans_of(session, user, active_only=True) if r.kind == "IF_THEN"]
    since = (now - timedelta(hours=REMIND_HOURS)).isoformat()
    reminders = []
    for r in plans:
        hit = proof.triggered(r.trigger, recent) if r.trigger != "NONE" else None
        if hit is not None and (hit.exit_time or "") >= since:
            reminders.append({**plan_dict(r), "because": f"{'Stop hit' if r.trigger == 'STOP_HIT' else 'Loss'} on {hit.symbol}",
                              "entry_kind": hit.kind, "entry_id": hit.id})
    found = proof.facts(recent)
    last = proof.last_good(found)
    return {"plans": [plan_dict(r) for r in plans], "reminders": reminders, "goals": await goals_progress(session, user, s, now=now),
            "last_good": last.to_dict() if last else None, "after_loss": proof.after_loss_line(recent)}


__all__ = ["clean_plan", "plan_dict", "plans_of", "own_plan", "add_plan", "update_plan", "proof_page", "add_own_proof", "pin",
           "remove_own_proof", "goals_progress", "today"]
