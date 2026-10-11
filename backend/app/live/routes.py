"""PW4: a live stream so a decision taken on one surface shows on the other within 2 s.

A proposal approved on Telegram (or in another tab, or by a teammate) must update the Watchtower without a reload, and
the reverse. The app keeps one stream open: `GET /api/live/stream` (Server-Sent Events over fetch, so the bearer token
travels in a header). The server checks a tiny per-organisation signature once a second - the open proposals and the
latest decision or snooze, the newest notification - and sends an event only when it changed. No data rides the stream: the
page reloads what it shows through the normal, authorised endpoints.

Each stream lives at most `STREAM_SECONDS` and the client reconnects; a comment line every 15 s keeps proxies from
closing an idle connection. One database round-trip per second per open app is fine for the Phase 0 cohort (a few
hundred users); a Redis pub/sub feed can replace the polling later without changing the wire format.
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import AsyncIterator, Awaitable, Callable, Dict, Optional, Tuple

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.dependencies import get_current_user
from app.db.models import AiActionRecord, NotificationRecord, User

router = APIRouter(prefix="/api/live", tags=["live"])

POLL_SECONDS = 1.0
HEARTBEAT_SECONDS = 15.0
STREAM_SECONDS = 300.0

# Tests point this at their own database; the app uses its engine's session factory.
session_factory: Optional[async_sessionmaker] = None


def _factory() -> async_sessionmaker:
    if session_factory is not None:
        return session_factory
    from app.db.session import _session_factory
    return _session_factory


async def signature(session: AsyncSession, tenant_id: int) -> Dict[str, Tuple]:
    """What the pages care about, per topic. Equal signatures = nothing to reload."""
    actions = (await session.execute(
        select(func.count(AiActionRecord.id).filter(AiActionRecord.status == "PROPOSED"), func.max(AiActionRecord.id),
               func.max(AiActionRecord.decided_at), func.max(AiActionRecord.executed_at),
               # PW5: a snooze (or its wake) hides or shows a proposal, so it is a change too.
               func.count(AiActionRecord.id).filter(AiActionRecord.status == "PROPOSED", AiActionRecord.snoozed_until.is_not(None)),
               func.max(AiActionRecord.snoozed_until))
        .where(AiActionRecord.tenant_id == tenant_id)
    )).one()
    notes = (await session.execute(
        select(func.max(NotificationRecord.id), func.count(NotificationRecord.id).filter(NotificationRecord.read_at.is_(None)))
        .where(NotificationRecord.tenant_id == tenant_id)
    )).one()
    return {"ai_actions": tuple(str(v) for v in actions), "notifications": tuple(str(v) for v in notes)}


def _event(name: str, data: dict) -> bytes:
    return f"event: {name}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n".encode()


async def events(tenant_id: int, disconnected: Callable[[], Awaitable[bool]], *, max_seconds: float = STREAM_SECONDS,
                 poll_seconds: float = POLL_SECONDS, heartbeat_seconds: float = HEARTBEAT_SECONDS) -> AsyncIterator[bytes]:
    """The stream body: `ready`, then one event per topic whose signature changed, a heartbeat comment, and `bye`
    when the stream's time is up (the client reconnects)."""
    factory = _factory()
    started = last_beat = time.monotonic()
    previous: Optional[Dict[str, Tuple]] = None
    yield _event("ready", {"retry_ms": 2000})
    while time.monotonic() - started < max_seconds:
        if await disconnected():
            return
        async with factory() as session:
            current = await signature(session, tenant_id)
        if previous is not None:
            for topic, value in current.items():
                if value != previous.get(topic):
                    yield _event(topic, {"changed": True})
        previous = current
        if time.monotonic() - last_beat >= heartbeat_seconds:
            last_beat = time.monotonic()
            yield b": ping\n\n"
        await asyncio.sleep(poll_seconds)
    yield _event("bye", {"reconnect": True})


@router.get("/stream")
async def live_stream(request: Request, user: User = Depends(get_current_user)) -> StreamingResponse:
    """The caller's organisation only (the tenant comes from the token, never from the request)."""
    return StreamingResponse(
        events(user.tenant_id, request.is_disconnected),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )
