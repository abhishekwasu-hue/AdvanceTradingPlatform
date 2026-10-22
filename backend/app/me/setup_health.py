"""Settings redesign part D ("Setup health", docs/design/SETTINGS_REDESIGN_REVIEW.md R8): what in the caller's setup
needs an action now, carried by `GET /api/me/setup` as `health` and shown as rows of the Dashboard's Needs attention
block (frontend src/dashboard/model.ts attentionRows; no second strip). Behind `ui_v2`: with it off `health` is null.

`health` is a list with one item per thing that needs the trader, and an empty list when all is fine:
- `analytics_token` (`state` "expired" / "expiring"): the organisation's Upstox Analytics Token has run out, or runs out
  before the end of today (IST). The daily broker login is the Dashboard's own "Log in" row (reused, not here).
- `ai_key` (`state` "no_credit" / "failing", `message` the plain sentence): the caller's own AI key (`user_ai_keys`, the
  Connect AI panel) failed its last test with a reason the trader must fix (out of credit, refused, cannot be opened).
  A passing break (rate limit, service unreachable) is not shown. Only while `ai_one_key` is on (the panel's flag) and
  only when the caller has a key: someone without a key sees nothing here.
- `telegram` (`state` "not_connected"): the organisation has no Telegram alert channel that is on, and this server can
  connect one. Read from `platform_bot.telegram_channel` only - nothing here calls Telegram (the Connect screen's GET
  polls Telegram for new messages; it is never called from here). The app shows it only once the Get started card is
  hidden (that card has its own Telegram step).
- `data_feed` (`state` "delayed"): the platform's market data is behind (`status_page._data_feed` says degraded: the
  latest session's end-of-day file is not loaded yet). Platform-wide, so it is cached once per process.

The answer is cached `CACHE_SECONDS` per (tenant, user) and the data feed `FEED_CACHE_SECONDS` per process, so a Dashboard
opened again and again costs no extra reads; `forget()` drops one caller's entry after they change their AI key. Reads
only: nothing here writes, and nothing here touches a broker session, a deployment or an order.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import BrokerCredentialRecord

logger = logging.getLogger(__name__)

PAGE_FLAG = "ui_v2"
CACHE_SECONDS = 45.0
FEED_CACHE_SECONDS = 60.0
IST = timezone(timedelta(hours=5, minutes=30))
# The AI key's last test failed for a reason only the trader can fix (app/ai/byok.py PLAIN_ERRORS keys).
AI_ACTION_KINDS = ("no_credit", "auth", "binding")

@dataclass(frozen=True)
class Caller:
    """The caller as two plain ids (a failed save rolls back and expires the ORM user)."""
    id: int
    tenant_id: int


_cache: Dict[Tuple[int, int], Tuple[float, List[Dict[str, Any]]]] = {}
_feed: Dict[str, Tuple[float, bool]] = {}


def reset_cache() -> None:
    """Forget every cached answer (tests)."""
    _cache.clear()
    _feed.clear()


def forget(tenant_id: int, user_id: int) -> None:
    """Drop one caller's cached answer (after they connect, test or remove their AI key)."""
    _cache.pop((int(tenant_id), int(user_id)), None)


def _end_of_today(now: datetime) -> datetime:
    local = now.astimezone(IST)
    return (local.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)).astimezone(timezone.utc)


async def _analytics_token(session: AsyncSession, tenant_id: int, now: datetime) -> Optional[Dict[str, Any]]:
    from app.brokers import upstox_analytics
    rows = await session.scalars(select(BrokerCredentialRecord).where(
        BrokerCredentialRecord.tenant_id == tenant_id, BrokerCredentialRecord.broker_name == upstox_analytics.BROKER))
    worst: Optional[str] = None
    for record in rows:
        # Decrypted in memory only to read the token's expiry; the value is never returned or logged.
        view = upstox_analytics.record_view(record, now)
        if not view.get("analytics_token_set"):
            continue
        if view.get("analytics_token_expired"):
            return {"key": "analytics_token", "state": "expired"}
        expires = view.get("analytics_token_expires_at")
        try:
            at = datetime.fromisoformat(str(expires)) if expires else None
        except ValueError:
            at = None
        if at is not None:
            at = at if at.tzinfo else at.replace(tzinfo=timezone.utc)
            if at <= _end_of_today(now):
                worst = "expiring"
    return {"key": "analytics_token", "state": worst} if worst else None


async def _ai_key(session: AsyncSession, user: Any) -> Optional[Dict[str, Any]]:
    from app.ai import byok, resolve
    if not await resolve.one_key_on(session, user.tenant_id):
        return None
    record = await byok.connected_key(session, user)
    if record is None or record.last_test_ok is not False:
        return None
    kind = next((k for k in AI_ACTION_KINDS if record.last_test_error == byok.PLAIN_ERRORS[k]), None)
    if kind is None:
        return None
    return {"key": "ai_key", "state": "no_credit" if kind == "no_credit" else "failing", "message": byok.PLAIN_ERRORS[kind]}


async def _telegram(session: AsyncSession, tenant_id: int) -> Optional[Dict[str, Any]]:
    from app.alerts import platform_bot
    record = await platform_bot.telegram_channel(session, tenant_id)          # one row read; never a call to Telegram
    if record is not None and record.enabled:
        return None
    if not platform_bot.configured():
        return None                                                            # nothing to connect to on this server
    return {"key": "telegram", "state": "not_connected"}


async def _feed_delayed(session: AsyncSession, now: datetime) -> bool:
    entry = _feed.get("feed")
    if entry is not None and time.monotonic() - entry[0] < FEED_CACHE_SECONDS:
        return entry[1]
    from app.observability import status_page
    try:
        feed = await status_page._data_feed(session, now, None)
        delayed = feed.get("state") == status_page.DEGRADED
    except Exception as exc:  # noqa: BLE001 - an unknown feed is never shown as delayed, and never fails the page
        logger.warning("Setup health: data feed not read (%s)", exc.__class__.__name__)
        delayed = False
    _feed["feed"] = (time.monotonic(), delayed)
    return delayed


async def compute(session: AsyncSession, user: Any, now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """The items that need the trader now (see the module docstring), uncached."""
    now = now or datetime.now(timezone.utc)
    items: List[Dict[str, Any]] = []
    for item in (await _analytics_token(session, user.tenant_id, now), await _ai_key(session, user),
                 await _telegram(session, user.tenant_id)):
        if item is not None:
            items.append(item)
    if await _feed_delayed(session, now):
        items.append({"key": "data_feed", "state": "delayed"})
    return items


async def health(session: AsyncSession, user: Any, now: Optional[datetime] = None) -> Optional[List[Dict[str, Any]]]:
    """`GET /api/me/setup`'s `health`: null while `ui_v2` is off, else the cached items for this (tenant, user). `user`
    is the caller or any object with its `id` and `tenant_id` (only those two are read)."""
    from app.platform.controls import flag_enabled
    tenant_id, user_id = int(user.tenant_id), int(user.id)
    if not await flag_enabled(session, PAGE_FLAG, tenant_id):
        return None
    entry = _cache.get((tenant_id, user_id))
    if entry is not None and time.monotonic() - entry[0] < CACHE_SECONDS:
        return [dict(item) for item in entry[1]]
    items = await compute(session, user, now)
    _cache[(tenant_id, user_id)] = (time.monotonic(), items)
    return [dict(item) for item in items]


__all__ = ["CACHE_SECONDS", "Caller", "FEED_CACHE_SECONDS", "compute", "forget", "health", "reset_cache"]
