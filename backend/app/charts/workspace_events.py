"""M76 MP-7 (owner order M76 S-3; flag `copilot_workspace`): event awareness on the Copilot workspace's chart.

`GET /api/charts/workspace/events?symbol=` answers, for the chart's underlying, from today up to its next monthly expiry:
- the listed expiries in that window (facts from the instrument master): a monthly expiry is the last listed expiry of
  its calendar month, every other one is a weekly expiry (the last one listed is just "Expiry");
- the events calendar's entries that apply to it (market events - RBI policy, Fed, budget ... - and the company's own
  calendar), the same read as the ticket's event line (app/paper/fno_insights.py `event_risk`);
- his own event list (prefs `own_events`), marked "own".

Information only (ADR-0006): nothing here places, blocks or changes an order or an exit, and the Guardian's event rules
(M8 / M64 `event_rules`, his own switch) are not read or changed here. Behind `ui_v2` + `copilot_workspace`: 404 while
either is off. Reads only; no migration (the list lives in his prefs).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.core.rate_limit import user_rate_limit
from app.db.models import User
from app.db.session import get_session

router = APIRouter(prefix="/api/charts/workspace", tags=["charts"])
FLAG = "copilot_workspace"
UI_V2 = "ui_v2"
events_rate_limit = user_rate_limit("workspace_events", limit=60, window_seconds=60)
SYMBOL = r"^[A-Za-z0-9&_\- ]{1,40}$"


def expiry_rows(listed: list[date], start: date) -> list[dict[str, Any]]:
    """The listed expiries from `start` up to and including the first monthly one (the last listed expiry of its month).
    Pure. No expiry listed: an empty list (nothing is guessed)."""
    out: list[dict[str, Any]] = []
    days = sorted({d for d in listed if d >= start})
    for i, d in enumerate(days):
        if i + 1 == len(days):                       # the last one listed: whether more follow in its month is not known
            out.append({"date": d.isoformat(), "kind": "expiry", "name": "Expiry"})
            break
        monthly = (days[i + 1].year, days[i + 1].month) != (d.year, d.month)
        out.append({"date": d.isoformat(), "kind": "monthly" if monthly else "weekly",
                    "name": "Monthly expiry" if monthly else "Weekly expiry"})
        if monthly:
            break
    return out


async def _on(session: AsyncSession, user: User) -> None:
    from app.platform.controls import flag_enabled
    for flag in (UI_V2, FLAG):
        if not await flag_enabled(session, flag, user.tenant_id):
            raise HTTPException(status_code=404, detail="Not found", headers={"X-Feature-Disabled": flag})


@router.get("/events", dependencies=[Depends(events_rate_limit)])
async def workspace_events(symbol: str = Query(min_length=1, max_length=40, pattern=SYMBOL),
                           user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    await _on(session, user)
    from app.instruments import master
    from app.market_data.calendar import IST
    from app.me.prefs import read_one
    from app.paper import fno_insights
    today = datetime.now(IST).date()
    name = master.underlying_of(symbol)
    listed = await master.expiries(session, name, on_or_after=today)
    if not listed:
        listed = await master.expiries(session, name, instrument_type="FUT", on_or_after=today)
    expiries = expiry_rows(listed, today)
    until = date.fromisoformat(expiries[-1]["date"]) if expiries else today + timedelta(days=fno_insights.EVENT_DAYS_MAX)
    own, _version = await read_one("own_events", user, session)
    calendar = await fno_insights.event_risk(session, user, name, until, today=today, own_events=list(own.get("events") or []))
    return {"underlying": calendar["underlying"], "from": calendar["from"], "until": calendar["until"], "expiries": expiries,
            "events": calendar["events"], "more": calendar["more"], "calendar_empty": calendar["calendar_empty"],
            "message": calendar["message"], "source": calendar["source"], "blocks": False}
