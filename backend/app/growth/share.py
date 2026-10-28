"""The "my discipline this week" share card: discipline facts from the user's own PAPER record, nothing else.

A share card is a PUBLIC surface (CLAUDE.md): no security name, no price, no money, no buy / sell / target / stop
wording, and it never rewards trading more. So the card holds only:

* days inside his own daily limits (trades and loss, from his saved risk settings) out of the market days of the week
  so far - a day without a trade counts as a day inside them, so trading less never scores worse;
* his journal streak: weeks in a row in which every closed PAPER trade carries his own note (a week without a closed
  trade neither breaks nor extends it);
* the journal's weekly discipline score (following his own plan), only while the journal (`discipline_engine`) is on
  (its own refresh of the week runs then, as on the Journal page and in the daily rhythm);
* the days he traded out of the market days, said neutrally, with the fixed line that a day without a trade is fine.

`guard` is the content check every card passes before it is returned: a line or fact with a security name (his own
symbols and strategy ids included), a currency, a price-like number (a decimal, a thousands separator, four or more
digits), a trade-call word or a money / outcome word is dropped, never rewritten. tests/test_mo4_growth.py runs it over
cards built from fixtures that hold symbols and prices.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import TradeRecord, User
from app.market_data.calendar import IST
from app.rhythm import schedule
from app.signals import language

TITLE = "My discipline this week"
FOOTER = "Paper trading on AiAlgoDesk. Discipline facts only: no stocks, no prices, no advice."
NO_TRADE_LINE = "A day without a trade is a good day when nothing met my plan."
STREAK_WEEKS = 12

# Words a share card may never carry beyond the signal language's own checks: money and outcome words.
OUTCOME_WORDS = re.compile(r"\b(?:profit|profits|loss|losses|p&l|pnl|gain|gains|won|lost|wins?|return|returns|rupees?|lakh|crore)\b",
                           re.IGNORECASE)
PRICE_LIKE = re.compile(r"\d+[.,]\d+|\d{4,}")


def problems(text: str, names: Iterable[str] = ()) -> List[str]:
    """Why `text` may not be on a share card (empty = it may)."""
    text = text or ""
    found = [f"{p.kind}:{p.detail}" for p in language.check(text, "education", names=list(names))]
    found += [f"call_word:{w}" for w in language.call_words(text)]
    found += [f"outcome:{m.group(0)}" for m in OUTCOME_WORDS.finditer(text)]
    found += [f"price:{m.group(0)}" for m in PRICE_LIKE.finditer(text)]
    return found


def guard(card: Dict[str, Any], names: Iterable[str] = ()) -> Dict[str, Any]:
    """The card with every line / fact that has a problem left out (and the fixed texts checked too)."""
    wanted = [n for n in names if n]
    out = dict(card)
    out["facts"] = [f for f in card.get("facts", []) if not problems(f"{f['label']} {f['value']}", wanted)]
    out["lines"] = [line for line in card.get("lines", []) if not problems(line, wanted)]
    for key in ("title", "week_label", "footer"):
        if problems(str(out.get(key) or ""), wanted):
            out[key] = ""
    return out


def _utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _ist_day(value: Optional[datetime]) -> Optional[date]:
    aware = _utc(value)
    return aware.astimezone(IST).date() if aware else None


def week_label(monday: date) -> str:
    return f"Week of {monday.day} {monday.strftime('%b')}"


def inside_limits(trades: Sequence[TradeRecord], days: Sequence[date], max_trades: int, loss_limit: float) -> int:
    """Market days on which he entered no more trades than his own daily number and lost less than his own daily loss
    limit. A day without a trade is inside them."""
    inside = 0
    for d in days:
        entered = sum(1 for t in trades if _ist_day(t.entry_time) == d)
        lost = -sum(float(t.pnl or 0) for t in trades if t.exit_time is not None and _ist_day(t.exit_time) == d)
        inside += int(entered <= max_trades and lost < loss_limit)
    return inside


def journal_streak(closed: Sequence[TradeRecord], this_monday: date, weeks: int = STREAK_WEEKS) -> int:
    """Weeks in a row (this week back) in which every closed trade has his own note. A week without a closed trade is
    skipped: it neither breaks nor extends the streak."""
    streak = 0
    for i in range(weeks):
        monday = this_monday - timedelta(days=7 * i)
        week = [t for t in closed if (d := _ist_day(t.exit_time)) is not None and monday <= d < monday + timedelta(days=7)]
        if not week:
            continue
        if all((t.notes or "").strip() for t in week):
            streak += 1
        else:
            break
    return streak


async def _trades(session: AsyncSession, user: User, since: datetime) -> List[TradeRecord]:
    return list(await session.scalars(select(TradeRecord).where(
        TradeRecord.tenant_id == user.tenant_id, TradeRecord.user_id == user.id, TradeRecord.mode == "PAPER",
        (TradeRecord.entry_time >= since) | (TradeRecord.exit_time >= since)).order_by(TradeRecord.id).limit(5000)))


async def _names(session: AsyncSession, user: User, trades: Sequence[TradeRecord]) -> List[str]:
    """Every security name the card must not carry: his own traded symbols and strategy ids (and their underlyings)."""
    names = set()
    for t in trades:
        for value in (t.symbol, t.underlying_symbol, t.strategy_id):
            if value:
                names.add(str(value).strip())
                names.add(str(value).strip().upper())
    return sorted(n for n in names if n)


async def _discipline_score(session: AsyncSession, user: User, now: datetime) -> Optional[int]:
    from app.platform.controls import flag_enabled
    if not await flag_enabled(session, "discipline_engine", user.tenant_id):
        return None
    from app.journal import service as journal
    data = await journal.refresh_week(session, user, now=now)
    score = (data.get("score") or {}).get("score")
    return int(score) if score is not None else None


async def discipline_week(session: AsyncSession, user: User, now: Optional[datetime] = None) -> Dict[str, Any]:
    from app.market_data.calendar import family_holidays
    from app.risk_engine.routes import get_tenant_risk_config

    now = now or datetime.now(timezone.utc)
    today = now.astimezone(IST).date()
    monday = today - timedelta(days=today.weekday())
    holidays = set(await family_holidays(session, "NSE"))
    days = [d for d in schedule.week_trading_days(monday, holidays) if d <= today]
    since = schedule.day_start(monday - timedelta(days=7 * (STREAK_WEEKS - 1))).astimezone(timezone.utc)
    trades = await _trades(session, user, since)
    week_start = schedule.day_start(monday).astimezone(timezone.utc)
    week = [t for t in trades if (_utc(t.entry_time) or since) >= week_start or (_utc(t.exit_time) or since) >= week_start]
    closed = [t for t in trades if t.exit_time is not None]

    facts: List[Dict[str, str]] = []
    lines: List[str] = []
    hints: List[str] = []
    cfg = await get_tenant_risk_config(user.tenant_id, session)
    if cfg is not None and days and cfg.daily_loss_amount() is not None and cfg.max_trades_per_day is not None:
        loss_limit = float(cfg.daily_loss_amount() or 0.0)
        inside = inside_limits(week, days, int(cfg.max_trades_per_day), loss_limit)
        facts.append({"key": "inside_limits", "label": "Days inside my own limits", "value": f"{inside} of {len(days)}"})
        lines.append(f"I stayed inside my own daily limits on {inside} of {len(days)} market day{'s' if len(days) != 1 else ''}.")
    elif cfg is None or cfg.daily_loss_amount() is None or cfg.max_trades_per_day is None:
        hints.append("Save your own daily limits on the Risk limits page and the card shows the days you stayed inside them.")

    streak = journal_streak(closed, monday)
    facts.append({"key": "journal_streak", "label": "Journal streak", "value": f"{streak} week{'s' if streak != 1 else ''}"})
    if streak:
        lines.append(f"My own note on every closed trade, {streak} week{'s' if streak != 1 else ''} in a row.")
    else:
        hints.append("Write a short note on each closed trade (My trades) and your journal streak starts.")

    score = await _discipline_score(session, user, now)
    if score is not None:
        facts.append({"key": "discipline_score", "label": "Discipline score", "value": f"{score} / 100"})
        lines.append("The discipline score is about following my own plan, not about how much I trade.")

    if days:
        traded = len({d for t in week if (d := _ist_day(t.entry_time)) is not None and d in days})
        facts.append({"key": "days_traded", "label": "Days I traded", "value": f"{traded} of {len(days)}"})
    lines.append(NO_TRADE_LINE)

    card = {"title": TITLE, "week_label": week_label(monday), "facts": facts, "lines": lines, "footer": FOOTER,
            "empty": not week and streak == 0 and score is None}
    shown = guard(card, await _names(session, user, trades))
    return {**shown, "hints": hints}
