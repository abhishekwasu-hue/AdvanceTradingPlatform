"""M76 MP-13 (owner order M76 D-3; docs/design/M76_REVIEW.md §4; flag `copilot_workspace`): the close summary inside the
Copilot workspace - his own trading day in numbers only.

`GET /api/charts/workspace/close-summary` answers, per mode he traded (PAPER and LIVE read by the same code; a mode with
nothing is left out), from his OWN trade rows and journal entries since the start of today (IST):
* trades   - decisions entered today and closed today (a structure's legs count once, the journal's own unit), the net
             P&L of the legs closed today and their charges, how many closed with a gain / a loss;
* accepted - the check-card lines he pressed Place with on today's trades (M76 MP-5 `plan_json.accepted_warnings`), per
             type with its count;
* plan     - his own checklist (journal settings `checklist_rules`): his answers on today's trades (followed / not
             followed / not answered; none saved = no checklist row); and plan vs fill: on trades whose plan had its own
             entry price (a signal's), the gap between that price and the fill in points (average and largest);
* open     - what stays open now: positions, their marks' unrealised P&L, the rupees at risk to his own stops and how
             many have no stop of his.
`market_open` says whether the exchange is open now (the page titles the card "So far today" until the close).

Numbers only: no judgement, no advice, no buy / sell / target / stop wording. Self-scoped (tenant AND user). Read-only:
SELECTs only, nothing here places, changes or blocks an order or an exit. Behind `ui_v2` + `copilot_workspace` (404 while
either is off). No migration.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.db.models import JournalEntryRecord, TradeRecord, User
from app.db.session import get_session

router = APIRouter(prefix="/api/charts/workspace", tags=["charts"])
FLAG = "copilot_workspace"
UI_V2 = "ui_v2"
MODES = ("PAPER", "LIVE")


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _sign(direction: str) -> int:
    return -1 if str(direction).upper() == "SHORT" else 1


def mode_summary(rows: Sequence[TradeRecord], plans: dict[int, dict[str, Any]], checklists: dict[int, list[dict[str, Any]]],
                 start: datetime, labels: dict[str, str], rules: Sequence[str]) -> dict[str, Any]:
    """One mode's numbers from his rows (pure). `plans` / `checklists`: per trade id, the journal entry's plan and his
    answers; `start`: the start of today (UTC); `labels`: the check-card type names; `rules`: his own checklist."""
    from app.journal.entries import _risk
    from app.journal.service import review_trades
    entered_ids = {t.id for t in rows if (_utc(t.entry_time) or start) >= start and t.entry_time is not None}
    units = review_trades(rows)
    entered = [u for u in units if set(u.rows) & entered_ids]
    by_id = {t.id: t for t in rows}

    def closed_today(u: Any) -> bool:
        exits = [_utc(by_id[i].exit_time) for i in u.rows if i in by_id]
        known = [x for x in exits if x is not None]
        return u.pnl is not None and bool(exits) and len(known) == len(exits) and max(known) >= start
    closed = [u for u in units if closed_today(u)]
    legs_closed = [t for t in rows if t.pnl is not None and (x := _utc(t.exit_time)) is not None and x >= start]
    net = round(sum(float(t.pnl or 0.0) for t in legs_closed), 2)
    charges = round(sum(float(t.charges or 0.0) for t in legs_closed), 2)

    # per decision (a structure once): a type accepted on any of its legs, his answers on any of its legs
    accepted: Counter[str] = Counter()
    for u in entered:
        accepted.update({str(i["id"]) for tid in u.rows for i in (plans.get(tid) or {}).get("accepted_warnings") or []
                         if isinstance(i, dict) and i.get("id")})

    checklist: dict[str, int] | None = None
    if rules:
        followed = missed = 0
        for u in entered:
            answers: dict[str, Any] = {}
            for tid in u.rows:
                for a in checklists.get(tid) or []:
                    if isinstance(a, dict) and a.get("followed") is not None:
                        answers.setdefault(str(a.get("rule")), a.get("followed"))
            followed += sum(1 for rule in rules if answers.get(rule) is True)
            missed += sum(1 for rule in rules if answers.get(rule) is False)
        asked = len(rules) * len(entered)
        checklist = {"rules": len(rules), "trades": len(entered), "followed": followed, "not_followed": missed,
                     "not_answered": asked - followed - missed}

    gaps = []
    for tid in sorted(entered_ids):
        plan, t = plans.get(tid) or {}, by_id[tid]
        if plan.get("entry_source") == "signal" and isinstance(plan.get("entry"), (int, float)) and t.entry_price:
            gaps.append(abs(float(t.entry_price) - float(plan["entry"])))
    fill = {"trades": len(gaps), "average_points": round(sum(gaps) / len(gaps), 2) if gaps else None,
            "largest_points": round(max(gaps), 2) if gaps else None}

    open_ = [t for t in rows if t.exit_time is None]
    unrealised = round(sum(_sign(t.direction) * (float(t.mark_price) - float(t.entry_price)) * float(t.quantity)
                           for t in open_ if t.mark_price is not None), 2)
    at_risk = round(sum(_risk(t.direction, float(t.entry_price), float(t.stop_loss) if t.stop_loss else None, float(t.quantity))
                        for t in open_), 2)
    return {
        "trades": {"entered": len(entered), "closed": len(closed), "gains": sum(1 for u in closed if (u.pnl or 0) > 0),
                   "losses": sum(1 for u in closed if (u.pnl or 0) < 0), "net_pnl": net, "charges": charges},
        "accepted": [{"id": wid, "label": labels.get(wid, wid), "count": n} for wid, n in sorted(accepted.items(), key=lambda kv: (-kv[1], kv[0]))],
        "checklist": checklist,
        "fill_vs_plan": fill,
        "open": {"positions": len(open_), "unrealised_pnl": unrealised, "at_risk_to_your_stops": at_risk,
                 "without_your_stop": sum(1 for t in open_ if not t.stop_loss),
                 "unmarked": sum(1 for t in open_ if t.mark_price is None)},
    }


async def _on(session: AsyncSession, user: User) -> None:
    from app.platform.controls import flag_enabled
    for flag in (UI_V2, FLAG):
        if not await flag_enabled(session, flag, user.tenant_id):
            raise HTTPException(status_code=404, detail="Not found", headers={"X-Feature-Disabled": flag})


async def build(session: AsyncSession, user: User, now: datetime) -> dict[str, Any]:
    from app.journal import capture
    from app.journal import settings as journal_settings
    from app.market_data.calendar import IST, market_session_status
    from app.paper.pretrade_warnings import LABELS
    start = now.astimezone(IST).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)
    rows = list(await session.scalars(select(TradeRecord).where(
        TradeRecord.tenant_id == user.tenant_id, TradeRecord.user_id == user.id,
        or_(TradeRecord.entry_time >= start, TradeRecord.exit_time >= start, TradeRecord.exit_time.is_(None)))))
    ids = [t.id for t in rows]
    plans: dict[int, dict[str, Any]] = {}
    checklists: dict[int, list[dict[str, Any]]] = {}
    if ids:
        for e in await session.scalars(select(JournalEntryRecord).where(JournalEntryRecord.tenant_id == user.tenant_id,
                                                                         JournalEntryRecord.user_id == user.id,
                                                                         JournalEntryRecord.trade_id.in_(ids))):
            plans[e.trade_id] = capture._loads(e.plan_json, {})  # noqa: SLF001 - the journal's own reader
            checklists[e.trade_id] = capture._loads(e.checklist_json, [])  # noqa: SLF001
    rules = list((await journal_settings.load(session, user)).checklist_rules)
    modes = {}
    for mode in MODES:
        mine = [t for t in rows if (t.mode or "PAPER") == mode]
        if mine:
            modes[mode] = mode_summary(mine, plans, checklists, start, LABELS, rules)
    status = await market_session_status(session, now)
    return {"as_of": now.isoformat(), "from": start.isoformat(), "market_open": bool(status.is_open), "modes": modes,
            "checklist_saved": bool(rules), "blocks": False}


@router.get("/close-summary")
async def close_summary(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    """His own day in numbers (see the module docstring). Read-only."""
    await _on(session, user)
    return await build(session, user, datetime.now(UTC))


__all__ = ["router", "mode_summary", "build"]
