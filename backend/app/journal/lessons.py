"""N2-11 (P1 spec 1.6 "learn from your journal", docs/design/N2_P1FGH.md): cards that link a behaviour tag on the
trader's own losing trades to the method-library lesson about it (N2-10).

Only the trader's own data (tenant + user): their counted journal tags over the last `weeks` IST weeks (by trade
entry) and those trades' net P&L. A tag counts as Z6 counts it: CONFIRMED and trader-added always, SUGGESTED while
`count_unconfirmed` is on, DISMISSED never. The tags are the journal's as of its last refresh (opening the journal or
the weekly review refreshes a week); nothing is detected here.

A card is shown for a tag carried by at least one closed losing trade. It states facts:
- how many tagged trades;
- how many closed at a loss;
- their net P&L together;
- the last date;
- the lesson's key.

It never says what to do and promises nothing (the frontend wording is checked like N1 F3's risk text).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import JournalTagRecord, TradeRecord, User
from app.journal import settings as journal_settings
from app.journal.settings import TAGS
from app.market_data.calendar import IST

FLAG = "journal_lessons"
DEFAULT_WEEKS = 8
# The method-library step each tag's lesson is in (frontend src/progression/learn.ts CONCEPT_LINKS lists the same tags
# under these steps; a test keeps the two in step).
LESSON_FOR_TAG: Dict[str, str] = {
    "revenge_trade": "trend", "overtrading": "trend", "no_stop": "level", "fomo_entry": "pullback", "averaging_loser": "pullback",
    "moved_stop_against": "rr", "early_exit": "rr", "held_through_event": "rr",
}


async def lessons(session: AsyncSession, user: User, *, weeks: int = DEFAULT_WEEKS, now: Optional[datetime] = None) -> Dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    s = await journal_settings.load(session, user)
    statuses = {"CONFIRMED"} | ({"SUGGESTED"} if s.count_unconfirmed else set())
    today = now.astimezone(IST).date()
    monday = today - timedelta(days=today.weekday())
    since = datetime.combine(monday - timedelta(weeks=max(1, weeks) - 1), datetime.min.time(), tzinfo=IST).astimezone(timezone.utc)
    rows = await session.execute(select(JournalTagRecord.tag, JournalTagRecord.status, TradeRecord.id, TradeRecord.pnl, TradeRecord.exit_time,
                                        TradeRecord.entry_time).join(TradeRecord, TradeRecord.id == JournalTagRecord.trade_id).where(
        JournalTagRecord.tenant_id == user.tenant_id, JournalTagRecord.user_id == user.id,
        TradeRecord.tenant_id == user.tenant_id, TradeRecord.user_id == user.id, TradeRecord.entry_time >= since))
    by_tag: Dict[str, Dict[str, Any]] = {}
    for tag, status, trade_id, pnl, exit_time, entry_time in rows:
        if status not in statuses or tag not in TAGS:
            continue
        card = by_tag.setdefault(tag, {"tag": tag, "lesson": LESSON_FOR_TAG.get(tag), "trades": set(), "losing": set(), "net": 0.0, "last": None})
        if trade_id in card["trades"]:
            continue
        card["trades"].add(trade_id)
        if exit_time is not None and pnl is not None and float(pnl) < 0:
            card["losing"].add(trade_id)
            card["net"] += float(pnl)
        when = (entry_time if entry_time.tzinfo else entry_time.replace(tzinfo=timezone.utc)).astimezone(IST).date().isoformat()
        card["last"] = max(card["last"] or when, when)
    cards: List[Dict[str, Any]] = [
        {"tag": c["tag"], "lesson": c["lesson"], "trades": len(c["trades"]), "losing_trades": len(c["losing"]),
         "losing_net_pnl": round(c["net"], 2), "last_date": c["last"]}
        for c in by_tag.values() if c["losing"]]
    cards.sort(key=lambda c: (c["losing_net_pnl"], -c["losing_trades"], c["tag"]))       # the costliest first
    return {"cards": cards, "weeks": weeks, "since": since.astimezone(IST).date().isoformat(), "count_unconfirmed": s.count_unconfirmed,
            "basis": "Your own journal tags (as of the journal's last refresh) and those trades' net P&L after charges",
            "as_of": now.isoformat()}
