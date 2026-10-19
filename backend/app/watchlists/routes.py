"""AMW-2: `GET/POST /api/watchlists`, `PUT/DELETE /api/watchlists/{watchlist_id}` - the caller's own named
watchlists. Behind the `watchlists` flag (404 while off). Every route reads and writes only the caller's own rows
(tenant and user); another member's or organisation's list answers 404 as if it did not exist.
"""
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.db.models import User
from app.db.session import get_session
from app.watchlists import service

router = APIRouter(prefix="/api/watchlists", tags=["watchlists"])


async def _enabled(session: AsyncSession, user: User) -> None:
    from app.platform.controls import flag_enabled
    if not await flag_enabled(session, service.FLAG, user.tenant_id):
        raise HTTPException(status_code=404, detail="Not found", headers={"X-Feature-Disabled": service.FLAG})


class WatchlistBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=service.NAME_MAX)
    symbols: List[str] = Field(default_factory=list, max_length=service.MAX_SYMBOLS)

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        return service.clean_name(v)

    @field_validator("symbols")
    @classmethod
    def _symbols(cls, v: List[str]) -> List[str]:
        return service.clean_symbols(v)


def _duplicate(name: str) -> HTTPException:
    return HTTPException(status_code=409, detail=f"you already have a watchlist named {name!r}")


async def _mine(session: AsyncSession, user: User, watchlist_id: int):
    row = await service.get_own(session, user.tenant_id, user.id, watchlist_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Watchlist not found")
    return row


@router.get("")
async def list_watchlists(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> List[Dict[str, Any]]:
    """The caller's own watchlists, by name."""
    await _enabled(session, user)
    return [service.as_dict(r) for r in await service.list_own(session, user.tenant_id, user.id)]


@router.post("", status_code=201)
async def create_watchlist(body: WatchlistBody, user: User = Depends(get_current_user),
                           session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    await _enabled(session, user)
    tenant_id, user_id = user.tenant_id, user.id
    # One create at a time per user: the user's row is locked (Postgres SELECT ... FOR UPDATE, held to the commit), so
    # two racing creates are counted one after the other.
    await session.execute(select(User.id).where(User.id == user_id).with_for_update())
    current = await service.count_own(session, tenant_id, user_id)
    if current >= service.MAX_PER_USER:
        raise HTTPException(status_code=409, detail=f"at most {service.MAX_PER_USER} watchlists per user - delete one first")
    await service.check_plan_limit(session, tenant_id, current)
    if await service.name_taken(session, tenant_id, user_id, body.name):
        raise _duplicate(body.name)
    row = service.new_row(tenant_id, user_id, body.name, body.symbols)
    session.add(row)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise _duplicate(body.name) from None
    await session.refresh(row)
    return service.as_dict(row)


@router.put("/{watchlist_id}")
async def update_watchlist(watchlist_id: int, body: WatchlistBody, user: User = Depends(get_current_user),
                           session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """Replaces the list's name and symbols."""
    await _enabled(session, user)
    row = await _mine(session, user, watchlist_id)
    # The same per-user lock as a create, so the case-insensitive name check cannot race another save.
    await session.execute(select(User.id).where(User.id == user.id).with_for_update())
    if await service.name_taken(session, user.tenant_id, user.id, body.name, except_id=row.id):
        raise _duplicate(body.name)
    service.apply(row, body.name, body.symbols)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise _duplicate(body.name) from None
    await session.refresh(row)
    return service.as_dict(row)


@router.delete("/{watchlist_id}")
async def delete_watchlist(watchlist_id: int, user: User = Depends(get_current_user),
                           session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    await _enabled(session, user)
    row = await _mine(session, user, watchlist_id)
    await session.delete(row)
    await session.commit()
    return {"deleted": watchlist_id}
