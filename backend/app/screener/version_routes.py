"""LAB-18 (AMW-7): the versions of a saved screen - list them, read one, and the one upload of a device's history.

* Behind `screener_v2` and `screener_copilot` (off by default); the Screener page adds `ui_v2`.
* Tenant-scoped: a screen of another organisation is "not found", like the screens routes.
* No restore route: "open an older version" reads it here and loads it into the builder; saving writes a new version
  through the usual `PUT /api/screener/screens/{id}`.
"""
from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, require_trader
from app.db.models import User
from app.db.session import get_session
from app.platform.controls import require_flag
from app.screener import versions
from app.screener.routes import FLAG as SCREENER_FLAG
from app.screener.routes import _compiled, _owned

router = APIRouter(prefix="/api/screener", tags=["screener"])


class ImportBody(BaseModel):
    versions: List[Dict[str, Any]] = Field(default_factory=list, max_length=versions.MAX_IMPORT)


async def _flags(session: AsyncSession, user: User) -> None:
    await require_flag(session, SCREENER_FLAG, user.tenant_id)
    await require_flag(session, versions.FLAG, user.tenant_id)


def _numbered(rows: List[Any]) -> List[tuple]:
    return [(v, i + 1, i == len(rows) - 1) for i, v in enumerate(rows)]


@router.get("/screens/{screen_id}/versions")
async def list_versions(screen_id: int, user: User = Depends(get_current_user),
                        session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """The screen's versions, oldest first, each with what changed from the one before (plain lines)."""
    await _flags(session, user)
    row = await _owned(session, user, screen_id)
    rows = await versions.listing(session, row.id)
    return {"screen_id": row.id, "imported": row.versions_imported_at is not None, "max": versions.MAX_VERSIONS,
            "versions": [versions.summary(v, n, last) for v, n, last in _numbered(rows)]}


@router.get("/screens/{screen_id}/versions/{version}")
async def read_version(screen_id: int, version: int, user: User = Depends(get_current_user),
                       session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """One version in full - its text, timeframe, settings and builder blocks, to load into the builder."""
    await _flags(session, user)
    row = await _owned(session, user, screen_id)
    rows = await versions.listing(session, row.id)
    for v, n, last in _numbered(rows):
        if v.version == version:
            return versions.detail(v, n, last)
    raise HTTPException(status_code=404, detail="Version not found")


@router.post("/screens/{screen_id}/versions/import")
async def import_versions(screen_id: int, body: ImportBody, user: User = Depends(require_trader),
                          session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """The one upload of the history this device kept for the screen (frontend versions.ts). Validated and capped; a
    repeat is a no-op (`already`). The page clears its device copy once this answers."""
    await _flags(session, user)
    row = await _owned(session, user, screen_id)
    result = await versions.import_device(session, row, user.id, body.versions,
                                          lambda text, base_tf: _compiled(text, base_tf, {}))
    await session.commit()
    return {"imported": result.imported, "skipped": result.skipped, "already": result.already}
