"""Signals day one (owner order 23 Oct): the setup card and the adopted watch on Signals, behind `ui_v2` and
`strategy_templates` (404 while either is off). See app/signals_feed/day_one.py for the design.

* `GET    /api/signals/day-one`          - the card's choices (the ready strategies with every default setting, the lists to
                                           watch, NIFTY 50 first) and, once adopted, his watch: one row per symbol in
                                           structure words. An evaluation older than STALE_SECONDS is run again here.
* `POST   /api/signals/day-one/adopt`    - adopts a ready strategy on a list for HIM: PAPER, approval-only entries (server
                                           constants, no request field), then runs the logic once over the closed Daily bars.
* `POST   /api/signals/day-one/refresh`  - evaluates his watch again (at most once a MIN_REFRESH_SECONDS).
* `DELETE /api/signals/day-one`          - stops the watch (his row only).

Per user and tenant: the row is the caller's own; another member's or organisation's watch is never read or changed, and
his own list is looked up by tenant and user. Nothing here creates a deployment, a proposal or an order (ADR-0006), and
nothing is in the way of an exit (ADR-0004).
"""
from datetime import datetime, timezone
from typing import Any, Dict, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.log import write_audit_log
from app.auth.dependencies import TRADING_ROLES, get_current_user, require_trader
from app.db.models import User
from app.db.session import get_session
from app.signals_feed import day_one as D

router = APIRouter(prefix="/api/signals/day-one", tags=["signals"])
FLAGS = ("ui_v2", D.FLAG)


class ListRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["system", "own"]
    key: Optional[str] = Field(default=None, max_length=80, pattern=r"^[a-z0-9-]{1,80}$")
    id: Optional[int] = Field(default=None, ge=1)


class AdoptBody(BaseModel):
    """Only the template and the list: the settings are the template's own defaults (shown before he confirms), the mode
    is PAPER and every entry waits for his approval - nothing in the body can change that."""
    model_config = ConfigDict(extra="forbid")
    template_id: str = Field(min_length=1, max_length=8, pattern=r"^[A-G][0-9]{1,2}$")
    list: ListRef


async def _enabled(session: AsyncSession, user: User) -> None:
    from app.platform.controls import flag_enabled
    for flag in FLAGS:
        if not await flag_enabled(session, flag, user.tenant_id):
            raise HTTPException(status_code=404, detail="Not found", headers={"X-Feature-Disabled": flag})


async def _offers(session: AsyncSession, user: User) -> list:
    from app.strategy_templates import library
    from app.strategy_templates.routes import _available, _listed
    out = []
    for tid in D.OFFERS:
        try:
            template = library.get(tid)
        except KeyError:
            continue
        if await _listed(session, user, template) and await _available(session, user, template):
            out.append(D.offer(template))
    return out


async def _choices(session: AsyncSession, user: User) -> Dict[str, Any]:
    lists = await D.list_choices(session, user)
    return {"offers": await _offers(session, user), "lists": lists,
            "default_list": next(({"kind": "system", "key": c["key"]} for c in lists if c.get("default")), None),
            "can_adopt": user.role in TRADING_ROLES, "max_watch": D.MAX_WATCH}


async def _watch(session: AsyncSession, user: User, value: Dict[str, Any], *, force: bool = False,
                 min_age: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """His adopted watch, evaluated again when stale (or `force`, at most once a `min_age`). None: no usable adoption."""
    try:
        template, strategy = D.strategy_of(str(value.get("strategy_id") or ""))
    except KeyError:
        return None
    now = datetime.now(timezone.utc)
    snapshot = value.get("snapshot")
    due = D.stale(snapshot, now, min_age) if force and min_age is not None else D.stale(snapshot, now)
    if due:
        value = {**value, "snapshot": await D.run(session, strategy, value.get("items") or [], now)}
        await D.write_row(session, user, value, now)
    return D.answer(value, template, strategy)


@router.get("")
async def day_one(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """The setup card's choices and, once he adopted one, his watch."""
    await _enabled(session, user)
    _, value = await D.read_row(session, user)
    watch = await _watch(session, user, value) if value else None
    return {**await _choices(session, user), "adopted": watch,
            "outdated": bool(value) and watch is None}       # the template changed since he adopted it: choose again


@router.post("/adopt")
async def adopt(body: AdoptBody, user: User = Depends(require_trader), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """Adopts the ready strategy on the list as HIS paper strategy (approval-only entries) and reads the list once."""
    from app.strategy_templates import ids, library
    from app.strategy_templates.compile import TemplateError
    await _enabled(session, user)
    if body.template_id not in D.OFFERS:
        raise HTTPException(status_code=404, detail="No such ready strategy")
    offered = {o["id"] for o in await _offers(session, user)}
    if body.template_id not in offered:
        raise HTTPException(status_code=409, detail="This ready strategy is not switched on for your account yet")
    template = library.get(body.template_id)
    try:
        strategy_id, _ = ids.adopt(template, D.TIMEFRAME)
    except TemplateError as exc:
        raise HTTPException(status_code=422, detail={"problems": exc.problems}) from exc
    found = await D.resolve_list(session, user, body.list.kind, body.list.key, body.list.id)
    if found is None:
        raise HTTPException(status_code=404, detail="No such list")
    list_ref, items = found
    if not items:
        raise HTTPException(status_code=422, detail="This list has no symbols yet. Pick another list.")
    now = datetime.now(timezone.utc)
    value = D.adoption_value(template, strategy_id, list_ref, items[:D.MAX_WATCH], len(items) > D.MAX_WATCH, now)
    await write_audit_log(session, user.tenant_id, user.id, "signals_day_one_adopted",
                          f"{template.id} on {list_ref['name']} ({min(len(items), D.MAX_WATCH)} symbols), {D.MODE}, entries {D.ENTRIES}")
    await D.write_row(session, user, value, now)
    watch = await _watch(session, user, value, force=True, min_age=0)
    return {**await _choices(session, user), "adopted": watch, "outdated": False}


@router.post("/refresh")
async def refresh(user: User = Depends(require_trader), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """Reads his watch again on the latest closed Daily bars (a second tap within MIN_REFRESH_SECONDS answers the last)."""
    await _enabled(session, user)
    _, value = await D.read_row(session, user)
    watch = await _watch(session, user, value, force=True, min_age=D.MIN_REFRESH_SECONDS) if value else None
    if watch is None:
        raise HTTPException(status_code=404, detail="Pick a ready strategy and a list first")
    return {**await _choices(session, user), "adopted": watch, "outdated": False}


@router.delete("")
async def stop(user: User = Depends(require_trader), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """Stops his watch (the ready strategy and the list); the card offers the choices again."""
    await _enabled(session, user)
    if await D.clear_row(session, user):
        await write_audit_log(session, user.tenant_id, user.id, "signals_day_one_stopped", "")
        await session.commit()
    return {**await _choices(session, user), "adopted": None, "outdated": False}
