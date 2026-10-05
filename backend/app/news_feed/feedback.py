"""Phase BD-2: members' verdicts on feed items and the organisation's "news trust".

A feed item is unverified by construction (Phase BB). The people reading it know whether it was
useful, noise, or classified in the wrong direction; recording that gives two things: a per-source
and per-type precision table the operator can act on (turn a noisy source off), and a deterministic
`news_trust` (0.25..1.0) that scales the strength of the thesis news factor for that organisation
once enough verdicts exist (`MIN_RATINGS`). Nothing here places orders or changes sizing.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import NewsEventRecord, NewsFeedbackRecord, User

VERDICTS = ("useful", "noise", "wrong_direction")
MIN_RATINGS = 10            # below this the trust is 1.0 (no evidence either way)
TRUST_FLOOR = 0.25          # even a badly rated feed keeps a quarter of its weight: it is still information
WINDOW_DAYS = 90


async def record(session: AsyncSession, tenant_id: int, user: User, news_event_id: int, verdict: str, note: Optional[str] = None,
                 *, now: Optional[datetime] = None, commit: bool = True) -> NewsFeedbackRecord:
    """One verdict per member per item; a second vote replaces the first."""
    if verdict not in VERDICTS:
        raise ValueError(f"verdict must be one of {', '.join(VERDICTS)}")
    item = await session.get(NewsEventRecord, news_event_id)
    if item is None or item.origin != "FEED":
        raise LookupError("Only feed items take feedback")
    now = now or datetime.now(timezone.utc)
    row = await session.scalar(select(NewsFeedbackRecord).where(
        NewsFeedbackRecord.tenant_id == tenant_id, NewsFeedbackRecord.news_event_id == news_event_id, NewsFeedbackRecord.user_id == user.id))
    if row is None:
        row = NewsFeedbackRecord(tenant_id=tenant_id, news_event_id=news_event_id, user_id=user.id, verdict=verdict, note=(note or "")[:300] or None, created_at=now)
        session.add(row)
        try:
            await session.flush()
        except IntegrityError:                       # the same member voted twice at once: the second press updates
            await session.rollback()
            row = await session.scalar(select(NewsFeedbackRecord).where(
                NewsFeedbackRecord.tenant_id == tenant_id, NewsFeedbackRecord.news_event_id == news_event_id, NewsFeedbackRecord.user_id == user.id))
            if row is None:
                raise
            row.verdict, row.note, row.created_at = verdict, (note or "")[:300] or None, now
    else:
        row.verdict, row.note, row.created_at = verdict, (note or "")[:300] or None, now
    if commit:
        await session.commit()
    return row


async def mine(session: AsyncSession, tenant_id: int, user_id: int, item_ids: List[int]) -> Dict[int, str]:
    """This member's verdicts on the given items (for the UI to show which thumb is pressed)."""
    if not item_ids:
        return {}
    rows = await session.scalars(select(NewsFeedbackRecord).where(
        NewsFeedbackRecord.tenant_id == tenant_id, NewsFeedbackRecord.user_id == user_id, NewsFeedbackRecord.news_event_id.in_(item_ids)))
    return {r.news_event_id: r.verdict for r in rows}


def trust_from_counts(useful: int, noise: int, wrong: int) -> dict:
    """The organisation's news trust: the useful share over the window, floored, 1.0 until MIN_RATINGS."""
    total = useful + noise + wrong
    if total < MIN_RATINGS:
        return {"ratings": total, "trust": 1.0, "applied": False, "useful": useful, "noise": noise, "wrong_direction": wrong,
                "note": f"{MIN_RATINGS - total} more verdict(s) before the trust applies"}
    share = useful / total
    return {"ratings": total, "trust": round(max(TRUST_FLOOR, share), 2), "applied": True, "useful": useful, "noise": noise, "wrong_direction": wrong, "note": None}


async def trust(session: AsyncSession, tenant_id: int, *, now: Optional[datetime] = None) -> dict:
    now = now or datetime.now(timezone.utc)
    rows = list(await session.execute(select(NewsFeedbackRecord.verdict, func.count()).where(
        NewsFeedbackRecord.tenant_id == tenant_id, NewsFeedbackRecord.created_at >= now - timedelta(days=WINDOW_DAYS)).group_by(NewsFeedbackRecord.verdict)))
    counts = {verdict: int(n) for verdict, n in rows}
    return trust_from_counts(counts.get("useful", 0), counts.get("noise", 0), counts.get("wrong_direction", 0))


async def summary(session: AsyncSession, tenant_id: int, *, now: Optional[datetime] = None) -> dict:
    """Trust plus precision per feed source and per classified type (keyword classification: the
    shared one every organisation sees), over the window."""
    now = now or datetime.now(timezone.utc)
    rows = list(await session.execute(select(NewsFeedbackRecord.verdict, NewsEventRecord.feed_id, NewsEventRecord.category)
                                      .join(NewsEventRecord, NewsEventRecord.id == NewsFeedbackRecord.news_event_id)
                                      .where(NewsFeedbackRecord.tenant_id == tenant_id, NewsFeedbackRecord.created_at >= now - timedelta(days=WINDOW_DAYS))))
    by_source: Dict[str, Dict[str, int]] = {}
    by_category: Dict[str, Dict[str, int]] = {}
    for verdict, feed_id, category in rows:
        for table, key in ((by_source, feed_id or "unknown"), (by_category, category or "OTHER")):
            bucket = table.setdefault(key, {"useful": 0, "noise": 0, "wrong_direction": 0})
            bucket[verdict] = bucket.get(verdict, 0) + 1

    def rows_of(table):
        out = []
        for key, c in sorted(table.items()):
            total = sum(c.values())
            out.append({"key": key, **c, "total": total, "useful_share": round(c["useful"] / total, 2) if total else None})
        return out
    return {"window_days": WINDOW_DAYS, "trust": await trust(session, tenant_id, now=now), "by_source": rows_of(by_source), "by_category": rows_of(by_category)}
