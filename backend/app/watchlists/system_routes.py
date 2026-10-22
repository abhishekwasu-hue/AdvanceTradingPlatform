"""WL1 (handover item 4): the read-only system watchlists (app/watchlists/system.py) and the admin's view of their refresh.

User routes (behind the `watchlists` flag like the user's own lists; 404 while it is off):
- `GET  /api/watchlists/system`              the groups (Indices, Index stocks, Sectors, F&O) and their lists, without rows;
- `GET  /api/watchlists/system/{key}`        one list with its rows;
- `POST /api/watchlists/system/{key}/copy`   "Copy to my list": a new list of the caller's own (tenant and user), under the
                                             same limits as creating one; the system list is not changed;
- `PUT` / `DELETE /api/watchlists/system/{key}`  always 403: system lists are read-only for everyone.

Admin routes (SUPER_ADMIN only; a tenant owner gets 403):
- `GET  /api/admin/system-watchlists`          the last refresh (time, the added / removed symbols per list, the symbols that
                                               did not map to the instrument master) and the recent runs;
- `POST /api/admin/system-watchlists/refresh`  rebuilds the lists now from the tables already held (no download).

Information only: nothing here places, changes or proposes an order.
"""
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, require_mfa_session, require_role
from app.db.models import User
from app.db.session import get_session
from app.watchlists import service, system
from app.watchlists.routes import _duplicate, _enabled, _lock_user

router = APIRouter(prefix="/api/watchlists/system", tags=["watchlists"])
# MO3: SUPER_ADMIN with a two-factor-verified session, like every admin router.
admin_router = APIRouter(prefix="/api/admin/system-watchlists", tags=["admin"], dependencies=[Depends(require_role()), Depends(require_mfa_session)])

READ_ONLY = 'System lists are read-only. Use "Copy to my list" to make your own copy you can change.'


class CopyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Optional[str] = Field(default=None, min_length=1, max_length=service.NAME_MAX)

    @field_validator("name")
    @classmethod
    def _name(cls, v: Optional[str]) -> Optional[str]:
        return service.clean_name(v) if v is not None else None


async def _system(session: AsyncSession, key: str):
    row = await system.get(session, key)
    if row is None:
        raise HTTPException(status_code=404, detail="Watchlist not found")
    return row


@router.get("")
async def list_system(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """The system lists by group, in display order (rows are read per list). Empty groups are left out."""
    await _enabled(session, user)
    rows = await system.list_all(session)
    groups = [{"group": g, "label": system.GROUP_LABELS[g], "lists": [system.summary(r) for r in rows if r.group == g]}
              for g in system.GROUPS]
    refreshed = max((r.refreshed_at for r in rows if r.refreshed_at), default=None)
    return {"groups": [g for g in groups if g["lists"]], "refreshed_at": refreshed.isoformat() if refreshed else None}


@router.get("/{key}")
async def get_system(key: str, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    await _enabled(session, user)
    return system.as_dict(await _system(session, key))


@router.put("/{key}")
async def update_system(key: str, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """System lists are read-only for every user, the admin included (they are rebuilt from the exchange's files)."""
    await _enabled(session, user)
    raise HTTPException(status_code=403, detail=READ_ONLY)


@router.delete("/{key}")
async def delete_system(key: str, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    await _enabled(session, user)
    raise HTTPException(status_code=403, detail=READ_ONLY)


def _base_name(name: str) -> str:
    """A copy's default name: the system name without its count ("NIFTY 50 stocks (50)" -> "NIFTY 50 stocks")."""
    base = name.rsplit(" (", 1)[0] if name.endswith(")") else name
    return service.clean_name(base.rstrip("…")[:service.NAME_MAX - 4] or "My list")


@router.post("/{key}/copy", status_code=201)
async def copy_system(key: str, body: Optional[CopyBody] = None, user: User = Depends(get_current_user),
                      session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """A new list of the caller's own with the system list's rows (the first MAX_SYMBOLS when it holds more; the answer
    says so). Without a name: the system list's name, numbered when the caller already has a list by that name."""
    await _enabled(session, user)
    row = await _system(session, key)
    tenant_id, user_id = user.tenant_id, user.id
    await _lock_user(session, user)
    current = await service.count_own(session, tenant_id, user_id)
    if current >= service.MAX_PER_USER:
        raise HTTPException(status_code=409, detail=f"at most {service.MAX_PER_USER} watchlists per user - delete one first")
    await service.check_plan_limit(session, tenant_id, current)
    if body is not None and body.name is not None:
        name = body.name
        if await service.name_taken(session, tenant_id, user_id, name):
            raise _duplicate(name)
    else:
        base = _base_name(row.name)
        name, n = base, 2
        while await service.name_taken(session, tenant_id, user_id, name):
            name, n = f"{base} {n}", n + 1
    rows = system.watch_items(row)
    items = service.clean_items(rows[:service.MAX_SYMBOLS])
    made = service.new_row(tenant_id, user_id, name, items)
    session.add(made)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise _duplicate(name) from None
    await session.refresh(made)
    return {**service.as_dict(made), "copied_from": row.key, "truncated": len(rows) > len(items), "source_count": len(rows)}


@admin_router.get("")
async def system_status(user: User = Depends(require_role()), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    return await system.status(session)


@admin_router.post("/refresh")
async def system_refresh(user: User = Depends(require_role()), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """Rebuilds the system lists now from the universe tables and the instrument master already held (no download)."""
    report = await system.refresh(session)
    return {"refresh_id": report.refresh_id, "lists_total": report.lists_total, "lists_changed": report.lists_changed,
            "unmapped": report.unmapped, "note": report.note, "changes": report.changes}
