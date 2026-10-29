"""PW4: a live stream so a decision taken on one surface shows on the other within 2 s.

A proposal approved on Telegram (or in another tab, or by a teammate) must update the Watchtower without a reload, and
the reverse. The app keeps one stream open: `GET /api/live/stream` (Server-Sent Events over fetch, so the bearer token
travels in a header). The server checks a tiny per-organisation signature once a second - the open proposals and the
latest decision or snooze, the newest notification - and sends an event only when it changed. No data rides the stream: the
page reloads what it shows through the normal, authorised endpoints.

Each stream lives at most `STREAM_SECONDS` and the client reconnects; a comment line every 15 s keeps proxies from
closing an idle connection. One database round-trip per second per open app is fine for the Phase 0 cohort (a few
hundred users); a Redis pub/sub feed can replace the polling later without changing the wire format.

TM Part 1 (TM-12; flag `data_hub`, OFF by default): the same stream also carries the data hub's `ticks` topic - prices,
so here data does ride the stream - for the symbols the tab registered (`PUT /api/live/ticks`, at most 50, at most one
update per symbol per second, from the user's own broker connection only) and a `hub` event when that connection's
state changes (live / reconnecting / renew needed), in plain words. `GET /api/live/hub` gives the state on page load.
Flag off: the stream is exactly as before and the two routes answer 404.

AUD-G2.2 (flag `live_push`, with `ui_v2` + `data_hub`; OFF by default): the push. The `ready` event of a tab's stream
then carries `push` - the tick-age limit, the heartbeat and the bar settle time (settings LIVE_PUSH_*) - and the
heartbeat is a `beat` event the tab can hear (a comment line is invisible to it), so the tab knows it may rely on the
ticks and stops its price timers; it goes back to them when it hears nothing for two beats. The ticks are the same
`ticks` events as above: the caller's own broker connection only, the tab's registered symbols only (at most 50). One
user holds at most LIVE_PUSH_MAX_STREAMS_PER_USER push streams at once per process; a further tab gets the stream as
before without `push`. The slot is given back when the stream ends (time up, the tab closed or disconnected).
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any, AsyncIterator, Awaitable, Callable, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.dependencies import get_current_user
from app.charts.drawings import EXCHANGE_RE, SYMBOL_RE
from app.core.rate_limit import user_rate_limit
from app.db.models import AiActionRecord, NotificationRecord, User
from app.db.session import get_session

router = APIRouter(prefix="/api/live", tags=["live"])
logger = logging.getLogger(__name__)

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


# AUD-G2.2: open push streams per user in this process (the bound of LIVE_PUSH_MAX_STREAMS_PER_USER).
_push_streams: Dict[int, int] = {}


def _take_push_slot(user_id: int) -> bool:
    from app.core.config import LIVE_PUSH_MAX_STREAMS_PER_USER
    held = _push_streams.get(user_id, 0)
    if held >= LIVE_PUSH_MAX_STREAMS_PER_USER:
        return False
    _push_streams[user_id] = held + 1
    return True


def _drop_push_slot(user_id: int) -> None:
    left = _push_streams.get(user_id, 0) - 1
    if left > 0:
        _push_streams[user_id] = left
    else:
        _push_streams.pop(user_id, None)


async def events(tenant_id: int, disconnected: Callable[[], Awaitable[bool]], *, max_seconds: float = STREAM_SECONDS,
                 poll_seconds: float = POLL_SECONDS, heartbeat_seconds: float = HEARTBEAT_SECONDS, feed: Any = None,
                 push: Optional[Dict[str, Any]] = None, push_user: Optional[int] = None) -> AsyncIterator[bytes]:
    """The stream body: `ready`, then one event per topic whose signature changed, a heartbeat comment, and `bye`
    when the stream's time is up (the client reconnects). With a data-hub `feed` (TM-12) also its `ticks` / `hub`
    events each second; a failing feed never ends the stream. With `push` (AUD-G2.2) and a free slot for `push_user`:
    `ready` carries it and the heartbeat is a `beat` event; the slot is given back however the stream ends."""
    held = False
    if push is not None and push_user is not None:
        held = _take_push_slot(push_user)
    if not held:
        push = None
    try:
        factory = _factory()
        started = last_beat = time.monotonic()
        previous: Optional[Dict[str, Tuple]] = None
        yield _event("ready", {"retry_ms": 2000, **({"push": push} if push is not None else {})})
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
            if feed is not None:
                try:
                    for name, data in await feed.poll():
                        yield _event(name, data)
                except Exception:  # noqa: BLE001 - prices are extra: the reload signals keep flowing
                    logger.debug("live ticks feed failed", exc_info=True)
            if time.monotonic() - last_beat >= heartbeat_seconds:
                last_beat = time.monotonic()
                yield _event("beat", {}) if push is not None else b": ping\n\n"
            await asyncio.sleep(poll_seconds)
        yield _event("bye", {"reconnect": True})
    finally:
        if held and push_user is not None:
            _drop_push_slot(push_user)


SID_RE = r"^[A-Za-z0-9_-]{8,64}$"


async def _hub_or_404(session: AsyncSession, user: User) -> None:
    from app.market_data.hub import hub_on
    from app.platform.controls import flag_enabled
    if not (await flag_enabled(session, "ui_v2", user.tenant_id) and await hub_on(session, user.tenant_id)):
        raise HTTPException(status_code=404, detail="Not found")


async def _feed_for(user: User, sid: Optional[str]) -> Any:
    """The data hub's browser feed for this tab, or None (no tab id, or the flag off)."""
    if not sid:
        return None
    from app.market_data.hub import BrowserFeed
    factory = _factory()
    async with factory() as session:
        try:
            await _hub_or_404(session, user)
        except HTTPException:
            return None
    return BrowserFeed(factory, user.id, sid)


async def _push_for(user: User) -> Optional[Dict[str, Any]]:
    """AUD-G2.2: the push settings for a tab that has a data-hub feed, or None while `live_push` is off."""
    from app.core import config
    from app.platform.controls import flag_enabled
    async with _factory()() as session:
        if not await flag_enabled(session, "live_push", user.tenant_id):
            return None
    from app.market_data.hub import BROWSER_SYMBOL_CAP
    return {"stale_after_s": config.LIVE_PUSH_STALE_SECONDS, "beat_s": config.LIVE_PUSH_HEARTBEAT_SECONDS,
            "bar_settle_s": config.LIVE_PUSH_BAR_SETTLE_SECONDS, "max_symbols": BROWSER_SYMBOL_CAP}


@router.get("/stream")
async def live_stream(request: Request, sid: Optional[str] = Query(default=None, pattern=SID_RE),
                      user: User = Depends(get_current_user)) -> StreamingResponse:
    """The caller's organisation only (the tenant comes from the token, never from the request). `sid` (TM-12): the
    tab's id for its live ticks, from the caller's own broker connection only."""
    from app.auth import view_as
    left = view_as.stream_seconds(STREAM_SECONDS)       # MO3-A1: a read-only view's stream ends with the view
    extra: Dict[str, Any] = {"max_seconds": left} if left is not None else {}
    feed = await _feed_for(user, sid)
    if feed is not None:
        extra["feed"] = feed
        push = await _push_for(user)
        if push is not None:
            extra.update(push=push, push_user=user.id, heartbeat_seconds=float(push["beat_s"]))
    return StreamingResponse(
        events(user.tenant_id, request.is_disconnected, **extra),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


# --- TM Part 1: the data hub's browser side (flag data_hub; 404 while off) -------------------------------------------

_SYMBOL = re.compile(SYMBOL_RE)
_EXCHANGE = re.compile(EXCHANGE_RE)
ticks_rate_limit = user_rate_limit("live_ticks", limit=60, window_seconds=60)


class TickItem(BaseModel):
    symbol: str = Field(min_length=1, max_length=60)
    exchange: str = Field(default="NSE", min_length=2, max_length=10)

    @field_validator("symbol")
    @classmethod
    def _symbol(cls, v: str) -> str:
        v = v.strip().upper()
        if not _SYMBOL.match(v):
            raise ValueError("not a trading symbol")
        return v

    @field_validator("exchange")
    @classmethod
    def _exchange(cls, v: str) -> str:
        v = v.strip().upper()
        if not _EXCHANGE.match(v):
            raise ValueError("not an exchange")
        return v


class TicksBody(BaseModel):
    sid: str = Field(pattern=SID_RE)
    items: List[TickItem] = Field(default_factory=list, max_length=50)


@router.put("/ticks", dependencies=[Depends(ticks_rate_limit)])
async def put_ticks(body: TicksBody, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict:
    """The symbols this tab shows (replaces the tab's previous set). They are registered on the caller's own broker
    connection, so its one stream carries them; their prices arrive as `ticks` events on the tab's live stream. Stores
    only the tab's list for a few minutes; places nothing."""
    from app.market_data import hub
    await _hub_or_404(session, user)
    pairs = [(i.symbol, i.exchange) for i in body.items]
    await hub.set_browser_symbols(user.id, body.sid, pairs)
    conn = await hub.user_connection(session, user)
    streamed = await hub.admit_for_user(session, user, conn, pairs) if conn is not None else []
    await session.commit()
    status = (await hub.connection_status(conn)).public() if conn is not None else None
    return {"accepted": len(pairs), "streamed": len(streamed), "status": status}


@router.get("/hub")
async def hub_status(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict:
    """The state of the caller's own broker connection for prices, in plain words (no ids). `status` is null when no
    broker is linked (the broker gate covers that)."""
    from app.market_data import hub
    await _hub_or_404(session, user)
    conn = await hub.user_connection(session, user)
    return {"status": (await hub.connection_status(conn)).public() if conn is not None else None}
