"""AMW-2: `GET/POST /api/watchlists`, `PUT/DELETE /api/watchlists/{watchlist_id}` - the caller's own named
watchlists. Behind the `watchlists` flag (404 while off). Every route reads and writes only the caller's own rows
(tenant and user); another member's or organisation's list answers 404 as if it did not exist.

UI2-4a1: a body may send `items` (`[{symbol, exchange}]`, spaces allowed in a symbol) instead of `symbols`; a PUT may
name the `version` it edits (a stale one gets 409 with the current list; without it the save replaces as before);
`PUT /api/watchlists/order` sets the order of the lists. Old clients that send `symbols` only work unchanged.
"""
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.db.models import User, WatchlistRecord
from app.db.session import get_session
from app.watchlists import service

router = APIRouter(prefix="/api/watchlists", tags=["watchlists"])


async def _enabled(session: AsyncSession, user: User) -> None:
    from app.platform.controls import flag_enabled
    if not await flag_enabled(session, service.FLAG, user.tenant_id):
        raise HTTPException(status_code=404, detail="Not found", headers={"X-Feature-Disabled": service.FLAG})


class WatchlistItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str = Field(min_length=1, max_length=60)
    exchange: str = Field(default=service.DEFAULT_EXCHANGE, min_length=2, max_length=12)


class WatchlistBody(BaseModel):
    """`symbols` (AMW-2: a pasted list, split on spaces, commas and semicolons) or `items` (UI2-4a1: one row per
    symbol with its exchange, spaces kept). Not both."""
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=service.NAME_MAX)
    symbols: Optional[List[str]] = Field(default=None, max_length=service.MAX_SYMBOLS)
    items: Optional[List[WatchlistItem]] = Field(default=None, max_length=service.MAX_SYMBOLS)

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        return service.clean_name(v)

    @field_validator("symbols")
    @classmethod
    def _symbols(cls, v: Optional[List[str]]) -> Optional[List[str]]:
        if v is not None:
            service.clean_symbols(v)          # refuses a bad symbol here; the route re-cleans with the list's own items
        return v

    @model_validator(mode="after")
    def _one_shape(self) -> "WatchlistBody":
        if self.symbols is not None and self.items is not None:
            raise ValueError("send symbols or items, not both")
        if self.items is not None:
            service.clean_items([i.model_dump() for i in self.items])
        return self

    def rows(self, previous: List[Dict[str, str]]) -> List[Dict[str, str]]:
        """The list's new items. From `symbols`, a symbol the list already held keeps its exchange (and a spaced one
        such as "NIFTY 50" stays whole), so an older client's save loses nothing it did not mean to drop."""
        if self.items is not None:
            return service.clean_items([i.model_dump() for i in self.items])
        symbols = service.clean_symbols(self.symbols or [], keep=[i["symbol"] for i in previous])
        return service.items_from_symbols(symbols, previous)


class WatchlistUpdateBody(WatchlistBody):
    version: Optional[int] = Field(default=None, ge=1, description="the version this edit was made on; omitted = replace")


class OrderBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ids: List[int] = Field(max_length=service.MAX_PER_USER, description="the caller's list ids, first to last")

    @field_validator("ids")
    @classmethod
    def _unique(cls, v: List[int]) -> List[int]:
        if len(set(v)) != len(v):
            raise ValueError("each list id once")
        return v


def _duplicate(name: str) -> HTTPException:
    return HTTPException(status_code=409, detail=f"you already have a watchlist named {name!r}")


async def _mine(session: AsyncSession, user: User, watchlist_id: int) -> WatchlistRecord:
    row = await service.get_own(session, user.tenant_id, user.id, watchlist_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Watchlist not found")
    return row


async def _lock_user(session: AsyncSession, user: User) -> None:
    """The per-user lock (Postgres SELECT ... FOR UPDATE on the user's row, held to the commit): one write at a time
    per user, so counts, name checks and version checks cannot race another device's save."""
    await session.execute(select(User.id).where(User.id == user.id).with_for_update())


@router.get("")
async def list_watchlists(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> List[Dict[str, Any]]:
    """The caller's own watchlists: in the caller's order (PUT /order), then the lists never placed, by name."""
    await _enabled(session, user)
    return [service.as_dict(r) for r in await service.list_own(session, user.tenant_id, user.id)]


@router.post("", status_code=201)
async def create_watchlist(body: WatchlistBody, user: User = Depends(get_current_user),
                           session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    await _enabled(session, user)
    tenant_id, user_id = user.tenant_id, user.id
    # One create at a time per user, so two racing creates are counted one after the other.
    await _lock_user(session, user)
    current = await service.count_own(session, tenant_id, user_id)
    if current >= service.MAX_PER_USER:
        raise HTTPException(status_code=409, detail=f"at most {service.MAX_PER_USER} watchlists per user - delete one first")
    await service.check_plan_limit(session, tenant_id, current)
    if await service.name_taken(session, tenant_id, user_id, body.name):
        raise _duplicate(body.name)
    row = service.new_row(tenant_id, user_id, body.name, body.rows([]))
    session.add(row)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise _duplicate(body.name) from None
    await session.refresh(row)
    return service.as_dict(row)


@router.put("/order")
async def order_watchlists(body: OrderBody, user: User = Depends(get_current_user),
                           session: AsyncSession = Depends(get_session)) -> List[Dict[str, Any]]:
    """Sets the order of the caller's lists: `ids` first to last; a list not named keeps its place after them. An id
    that is not one of the caller's lists is a 404 (another user's list is never touched). Returns the lists in order."""
    await _enabled(session, user)
    await _lock_user(session, user)
    rows = await service.list_own(session, user.tenant_id, user.id)
    if set(body.ids) - {r.id for r in rows}:
        raise HTTPException(status_code=404, detail="Watchlist not found")
    service.reorder(rows, body.ids)
    await session.commit()
    return [service.as_dict(r) for r in await service.list_own(session, user.tenant_id, user.id)]


@router.put("/{watchlist_id}")
async def update_watchlist(watchlist_id: int, body: WatchlistUpdateBody, user: User = Depends(get_current_user),
                           session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """Replaces the list's name and rows. With `version`, a list changed since (on another device) is refused with 409
    and the current list, instead of losing that change."""
    await _enabled(session, user)
    # The same per-user lock as a create, taken before the row is read, so the version and the case-insensitive name
    # checks cannot race another save.
    await _lock_user(session, user)
    row = await _mine(session, user, watchlist_id)
    if body.version is not None and body.version != (row.version or 1):
        raise HTTPException(status_code=409, detail={"message": "the watchlist changed since this edit was made",
                                                     "current": service.as_dict(row)})
    if await service.name_taken(session, user.tenant_id, user.id, body.name, except_id=row.id):
        raise _duplicate(body.name)
    service.apply(row, body.name, body.rows(service.items_of(row)))
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
