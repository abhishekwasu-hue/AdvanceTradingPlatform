"""U8 EV1/EV2: the evidence API - the stored pack of a matched symbol, its image, and "send to my Telegram".

GET  /api/screener/evidence/runs/{run_id}            the run's evidence ids by symbol
GET  /api/screener/evidence/{evidence_id}?debug=      the pack (`evidence/1`); failed conditions' drawings only with debug
GET  /api/screener/evidence/{evidence_id}/image.png   the server-rendered chart (theme=light|dark), the Telegram image
POST /api/screener/evidence/{evidence_id}/telegram    the image + caption to the caller's OWN linked private chat

Behind `screener_v2` and `evidence_charts` (both off by default). With `evidence_charts` off every route answers 404 -
the feature is hidden, not advertised. Reads follow `evidence_store.readable` (organisation; with BT1 `byob_data_tier`
on, the user whose broker session read the bars). Informational: nothing here places, proposes or changes an order.
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.core.rate_limit import user_rate_limit
from app.db.models import ScreenRunRecord, User
from app.db.session import get_session
from app.platform.controls import require_flag
from app.screener import evidence_store

router = APIRouter(prefix="/api/screener/evidence", tags=["screener"])
telegram_rate_limit = user_rate_limit("evidence_telegram", limit=10, window_seconds=60)
image_rate_limit = user_rate_limit("evidence_image", limit=30, window_seconds=60)      # rendering is CPU work
NOT_FOUND = "Evidence not found"


async def _gate(session: AsyncSession, user: User) -> None:
    await require_flag(session, "screener_v2", user.tenant_id)
    if not await evidence_store.enabled(session, user.tenant_id):
        raise HTTPException(status_code=404, detail="Not Found")


async def _row(session: AsyncSession, user: User, evidence_id: int) -> Any:
    await _gate(session, user)
    row = await evidence_store.readable(session, user, evidence_id)
    if row is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    return row


@router.get("/runs/{run_id}")
async def run_evidence(run_id: int, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    await _gate(session, user)
    run = await session.get(ScreenRunRecord, run_id)
    if run is None or run.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404, detail="Run not found")            # another organisation's run: no hint
    if run.user_id != user.id and await evidence_store._byob(session, user):
        raise HTTPException(status_code=404, detail="Run not found")            # BT1b: a run read the runner's own feed
    rows = await evidence_store.for_run(session, user, run_id)
    return {"run_id": run_id, "evidence": {r.symbol: r.id for r in rows}}


@router.get("/{evidence_id}")
async def get_evidence(evidence_id: int, debug: bool = False, user: User = Depends(get_current_user),
                       session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    row = await _row(session, user, evidence_id)
    return {**evidence_store.view(evidence_store.load(row), debug=debug), "evidence_id": row.id, "pack_sha256": row.pack_sha256,
            "can_send": row.user_id == user.id}


@router.get("/{evidence_id}/image.png", dependencies=[Depends(image_rate_limit)])
async def evidence_image(evidence_id: int, theme: Literal["light", "dark"] = "light", user: User = Depends(get_current_user),
                         session: AsyncSession = Depends(get_session)) -> Response:
    row = await _row(session, user, evidence_id)
    try:
        png, _caption = await asyncio.to_thread(evidence_store.image_and_caption, row, theme)       # off the event loop
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return Response(content=png, media_type="image/png", headers={"Cache-Control": "private, no-store"})


@router.post("/{evidence_id}/telegram", dependencies=[Depends(telegram_rate_limit)])
async def send_evidence(evidence_id: int, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    row = await _row(session, user, evidence_id)
    result = await evidence_store.send_to_own_chat(session, row, user)
    if not result["sent"]:
        raise HTTPException(status_code=409, detail=result["reason"])
    return result


__all__ = ["router"]
