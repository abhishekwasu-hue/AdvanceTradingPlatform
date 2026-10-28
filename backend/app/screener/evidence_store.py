"""U8 EV1/EV2: evidence packs kept with screen runs and alert events, read back with the BT1 rules, and sent as images to
the user's OWN linked Telegram chat.

* Built in the same pass as the match (`/api/screener/run`, the bar-close alert engine): the same SymbolData, parameters
  and cross-sectional constants the screen ran on; only for MATCHED symbols, at most MAX_PER_RUN per run and
  MAX_PER_RULE per alert evaluation; a pack that cannot be built is skipped (logged) and never fails the run or the alert.
* Stored as canonical JSON (sorted keys) with its SHA-256; the full pack (with failed conditions, flagged) is stored and
  `view()` hides the failed ones unless debug is asked for.
* Read: the record's organisation only (another organisation gets 404, never a hint); with BT1 `byob_data_tier` on,
  only the user whose run / alert rule read the bars (a broker entitlement is personal - BROKER_DATA_TERMS.md).
* Telegram: only to the record's user's own linked PRIVATE chat (S3b-2 `notification_links`), through the
  organisation's bot; never to the organisation's group chat (the bars may come from that user's own broker session).
  The image is rendered on demand from the stored pack and never stored.
Flag `evidence_charts` (off by default): off -> nothing is built or stored, reads answer 404, alerts stay text only.
Informational only: nothing here places, proposes or changes an order.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AlertChannelRecord, AlertEventRecord, ScreenEvidenceRecord, User
from app.screener import evidence as ev

logger = logging.getLogger(__name__)

FLAG = "evidence_charts"
MAX_PER_RUN = 25
MAX_PER_RULE = 10
MAX_PER_TENANT_DAY = 500           # evidence rows an organisation stores per IST day (runs and alerts together)
IMAGES_PER_ALERT = 3


CONTEXT_FLAG = "evidence_context"     # U8 EV3: option-chain drawables, the multi-timeframe strip, replay


async def context_enabled(session: AsyncSession, tenant_id: Optional[int]) -> bool:
    """EV3 for this organisation (off by default): packs built with the chain drawables and the trend strip."""
    from app.platform.controls import flag_enabled
    return bool(await flag_enabled(session, CONTEXT_FLAG, tenant_id))


async def enabled(session: AsyncSession, tenant_id: Optional[int]) -> bool:
    from app.platform.controls import flag_enabled
    return await flag_enabled(session, FLAG, tenant_id)


def canonical(pack: Dict[str, Any]) -> Tuple[str, str]:
    text = json.dumps(pack, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return text, hashlib.sha256(text.encode("utf-8")).hexdigest()


def view(pack: Dict[str, Any], debug: bool = False) -> Dict[str, Any]:
    """What a reader sees: failed conditions' drawables (and caption lines) only with debug."""
    out = dict(pack)
    out["summary"] = {**pack.get("summary", {}), "debug": debug}
    if not debug:
        out["drawables"] = [d for d in pack.get("drawables") or [] if not d.get("failed")]
        kept = {d["id"] for d in out["drawables"]}
        out["rows"] = [{**r, "drawable": r.get("drawable") if r.get("drawable") in kept else None} for r in pack.get("rows") or []]
    out["summary"]["drawn"] = len(out["drawables"])
    out["caption"] = ev.caption_lines(out)
    return out


def _record(pack: Dict[str, Any], *, tenant_id: int, user_id: Optional[int], run_id: Optional[int] = None,
            alert_event_id: Optional[int] = None, now: Optional[datetime] = None, per_user: bool = False) -> ScreenEvidenceRecord:
    text, sha = canonical(pack)
    return ScreenEvidenceRecord(tenant_id=tenant_id, user_id=user_id, run_id=run_id, alert_event_id=alert_event_id, symbol=pack["symbol"],
                                base_tf=pack["base_tf"], bar_time=datetime.fromisoformat(pack["as_of"]["bar_start"]), schema=pack["schema"],
                                pack_json=text, pack_sha256=sha, per_user=per_user, created_at=now or datetime.now(timezone.utc))


async def daily_room(session: AsyncSession, tenant_id: int, now: Optional[datetime] = None) -> int:
    """How many more evidence rows the organisation may store today (IST day; MAX_PER_TENANT_DAY across runs and alerts)."""
    from app.market_data.calendar import IST
    now = now or datetime.now(timezone.utc)
    day_start = datetime.combine(now.astimezone(IST).date(), datetime.min.time(), IST).astimezone(timezone.utc)
    used = await session.scalar(select(func.count()).select_from(ScreenEvidenceRecord).where(
        ScreenEvidenceRecord.tenant_id == tenant_id, ScreenEvidenceRecord.created_at >= day_start))
    return max(0, MAX_PER_TENANT_DAY - int(used or 0))


async def store_for_run(session: AsyncSession, *, tenant_id: int, user_id: Optional[int], run_id: int, ast: Any, validated: Any,
                        universe: List[Any], matched: List[str], base_tf: str, params: Dict[str, Any], exchange: str,
                        data_source: Optional[str], screen_name: Optional[str] = None, per_user: bool = False) -> Dict[str, int]:
    """Packs for the run's matched symbols (the first MAX_PER_RUN, within the organisation's daily room) -> {symbol:
    evidence id}. `per_user`: the bars came through the runner's own broker session (BT1b). Never raises."""
    try:
        wanted = set(matched)
        chosen = [d for d in universe if d.symbol in wanted]
        limit = min(MAX_PER_RUN, await daily_room(session, tenant_id))
        if not chosen or limit <= 0:
            return {}
        # the cross-sectional ranks need the whole universe; the packs are built for the matched symbols only
        context = await context_enabled(session, tenant_id)
        packs = await asyncio.to_thread(_build_for, ast, validated, universe, chosen, base_tf, params, exchange, data_source, run_id,
                                        screen_name, limit, context)
        rows = [_record(p, tenant_id=tenant_id, user_id=user_id, run_id=run_id, per_user=per_user) for p in packs.values()]
        async with session.begin_nested():               # a failed insert rolls back the packs only, never the run
            for row in rows:
                session.add(row)
            await session.flush()
        return {row.symbol: row.id for row in rows}
    except Exception as exc:  # noqa: BLE001 - evidence never fails a run
        logger.warning("Evidence not stored for run %s: %s", run_id, exc.__class__.__name__)
        return {}


def _build_for(ast: Any, validated: Any, universe: List[Any], chosen: List[Any], base_tf: str, params: Dict[str, Any], exchange: str,
               data_source: Optional[str], run_id: Optional[int], screen_name: Optional[str], limit: int,
               context: bool = False) -> Dict[str, Dict[str, Any]]:
    from app.screener.runtime import _cross_sectional
    constants = _cross_sectional(ast, universe, base_tf, params) if getattr(validated, "cross_sectional", False) else {}
    out: Dict[str, Dict[str, Any]] = {}
    for data in chosen[:limit]:
        try:
            out[data.symbol] = ev.build_pack(ast, validated, data, base_tf=base_tf, params=params, debug=True,
                                             constants=constants.get(data.symbol), exchange=exchange, data_source=data_source,
                                             run_id=run_id, screen_name=screen_name, context=context)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Evidence pack not built for %s: %s", data.symbol, exc.__class__.__name__)
    return out


async def store_for_events(session: AsyncSession, rule: Any, events: List[Tuple[Any, Any]], *, ast: Any, validated: Any,
                           universe: List[Any], params: Dict[str, Any], data_source: Optional[str] = None,
                           per_user: bool = False) -> int:
    """Bar-close alerts: one pack per fired event that will be delivered (`events` = [(AlertEventRecord, SymbolData)];
    status "pending" only - a cooldown-suppressed event is never sent, so its pack would be unreachable), at most
    MAX_PER_RULE and within the organisation's daily room, from the frames the rule was just evaluated on. Never raises;
    returns how many were stored."""
    try:
        events = [(e, d) for e, d in events if getattr(e, "status", None) == "pending"]
        if not events:
            return 0
        limit = min(MAX_PER_RULE, await daily_room(session, rule.tenant_id))
        if limit <= 0:
            return 0
        events = events[:limit]
        chosen = [data for _e, data in events]
        name = None
        if getattr(rule, "name", None):
            name = str(rule.name)
        context = await context_enabled(session, rule.tenant_id)
        packs = await asyncio.to_thread(_build_for, ast, validated, universe, chosen, rule.base_tf, params, rule.exchange or "NSE",
                                        data_source, None, name, limit, context)
        rows = []
        for event, data in events:
            pack = packs.get(data.symbol)
            if pack is None:
                continue
            row = _record(pack, tenant_id=rule.tenant_id, user_id=rule.created_by, alert_event_id=event.id, per_user=per_user)
            row.bar_time = event.bar_time                 # the alert's own bar label (the session open for a daily bar)
            rows.append(row)
        if rows:
            async with session.begin_nested():           # a failed insert rolls back the packs only, never the events
                session.add_all(rows)
                await session.flush()
        return len(rows)
    except Exception as exc:  # noqa: BLE001 - evidence never stops an alert
        logger.warning("Evidence not stored for alert rule %s: %s", getattr(rule, "id", "?"), exc.__class__.__name__)
        return 0


# --------------------------------------------------------------------------------------------------------------- reads
async def readable(session: AsyncSession, user: User, evidence_id: int) -> Optional[ScreenEvidenceRecord]:
    """The record when this user may read it: same organisation; with BT1 `byob_data_tier` on, their own only."""
    row = await session.get(ScreenEvidenceRecord, evidence_id)
    if row is None or row.tenant_id != user.tenant_id:
        return None
    if row.user_id != user.id and (row.per_user or await _byob(session, user)):
        return None                                   # per-user data stays its owner's, whatever the flag says now
    return row


async def _byob(session: AsyncSession, user: User) -> bool:
    from app.platform.controls import flag_enabled
    return await flag_enabled(session, "byob_data_tier", user.tenant_id)


async def for_run(session: AsyncSession, user: User, run_id: int) -> List[ScreenEvidenceRecord]:
    rows = list(await session.scalars(select(ScreenEvidenceRecord).where(ScreenEvidenceRecord.run_id == run_id,
                                                                          ScreenEvidenceRecord.tenant_id == user.tenant_id)
                                      .order_by(ScreenEvidenceRecord.symbol)))
    byob = await _byob(session, user)
    return [r for r in rows if r.user_id == user.id or not (r.per_user or byob)]


def load(row: ScreenEvidenceRecord) -> Dict[str, Any]:
    pack = json.loads(row.pack_json)
    pack["evidence_id"] = row.id
    return pack


# ------------------------------------------------------------------------------------------------------------ Telegram
async def _bot(session: AsyncSession, tenant_id: int) -> Optional[str]:
    from app.alerts.channels import decrypt_config
    from app.secrets_store.envelope import ensure_tenant_key
    channel = await session.scalar(select(AlertChannelRecord).where(AlertChannelRecord.tenant_id == tenant_id,
                                                                   AlertChannelRecord.channel_type == "TELEGRAM")
                                   .order_by(AlertChannelRecord.id).limit(1))
    if channel is None or not channel.enabled:
        return None
    await ensure_tenant_key(session, tenant_id)
    return str(getattr(decrypt_config(channel), "bot_token", "") or "") or None


async def own_chat(session: AsyncSession, tenant_id: int, user_id: Optional[int]) -> Optional[str]:
    if user_id is None:
        return None
    from app.alerts.links import linked_chat
    link = await linked_chat(session, tenant_id, user_id)
    return str(link.chat_id) if link is not None and link.chat_id else None


# SCR-FIX C4: why "Send to my Telegram" cannot go (a machine code for the page, a plain sentence for the person).
NOT_LINKED, BOT_NOT_CONFIGURED, BLOCKED_BY_USER, SEND_FAILED, NOT_OWNER = (
    "not_linked", "bot_not_configured", "blocked_by_user", "send_failed", "not_owner")
REASON_TEXT = {
    NOT_LINKED: "Connect your Telegram to get this chart there.",
    BOT_NOT_CONFIGURED: "Telegram is not set up on this server yet.",
    BLOCKED_BY_USER: "Telegram says the chat with the bot is blocked or closed. Open the chat with the bot, press Start, then try again.",
    SEND_FAILED: "Telegram did not take the image just now. Try again in a minute.",
    NOT_OWNER: "Only the person who ran the screen can send its evidence to their own chat.",
}
# Telegram's answers when the person blocked the bot, deleted the chat or the account is gone (sendPhoto 403 / 400).
_BLOCKED_HINTS = ("api 403", "blocked", "deactivated", "chat not found", "kicked")


async def _platform_chat(session: AsyncSession, tenant_id: int, user_id: int) -> Optional[str]:
    """The private chat this user linked through the platform bot ("Connect Telegram") while it is still the
    organisation's Telegram channel - the chat it sends to is then this user's own."""
    from app.alerts import platform_bot
    from app.alerts.channels import decrypt_raw
    from app.db.models import NotificationLinkRecord
    from app.secrets_store.envelope import ensure_tenant_key
    link = await session.scalar(select(NotificationLinkRecord).where(
        NotificationLinkRecord.tenant_id == tenant_id, NotificationLinkRecord.user_id == user_id,
        NotificationLinkRecord.channel == platform_bot.CHANNEL, NotificationLinkRecord.status == "used",
        NotificationLinkRecord.chat_id.is_not(None)).order_by(NotificationLinkRecord.id.desc()).limit(1))
    if link is None:
        return None
    record = await platform_bot.telegram_channel(session, tenant_id)
    if record is None or not record.enabled:
        return None
    await ensure_tenant_key(session, tenant_id)
    try:
        raw = decrypt_raw(record)
    except ValueError:
        return None
    return str(link.chat_id) if raw.get("platform_bot") and str(raw.get("chat_id") or "") == str(link.chat_id) else None


async def _telegram_target(session: AsyncSession, user: User) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """(chat, bot token, None) when the user's evidence can go to their own chat, else (None, None, reason code).
    The bot is the organisation's own (its Telegram channel) for a chat linked to it, the platform's for a chat linked
    with "Connect Telegram". No bot on the server at all -> bot_not_configured (the button is hidden then)."""
    from app.alerts import platform_bot
    org_token = await _bot(session, user.tenant_id)
    if not org_token and not platform_bot.configured():
        return None, None, BOT_NOT_CONFIGURED
    chat = await own_chat(session, user.tenant_id, user.id)
    if chat is not None:
        return (chat, org_token, None) if org_token else (None, None, BOT_NOT_CONFIGURED)
    chat = await _platform_chat(session, user.tenant_id, user.id)
    if chat is not None:
        return (chat, platform_bot.bot_token(), None) if platform_bot.configured() else (None, None, BOT_NOT_CONFIGURED)
    return None, None, NOT_LINKED


async def telegram_state(session: AsyncSession, row: ScreenEvidenceRecord, user: User) -> Dict[str, Any]:
    """What the evidence page shows by "Send to my Telegram": `telegram_linked`, and when it cannot send the reason code
    and its sentence. Never the token or the chat id."""
    if row.user_id != user.id:
        return {"telegram_linked": False, "telegram_reason": NOT_OWNER, "telegram_message": REASON_TEXT[NOT_OWNER]}
    chat, _token, reason = await _telegram_target(session, user)
    return {"telegram_linked": chat is not None, "telegram_reason": reason, "telegram_message": REASON_TEXT[reason] if reason else None}


def image_and_caption(row: ScreenEvidenceRecord, theme: str = "light") -> Tuple[bytes, str]:
    from app.screener import evidence_png
    pack = view(load(row), debug=False)
    return evidence_png.render(pack, theme=theme), ev.caption_text(pack)


def _failure(code: str) -> Dict[str, Any]:
    return {"sent": False, "reason": code, "message": REASON_TEXT[code]}


async def send_to_own_chat(session: AsyncSession, row: ScreenEvidenceRecord, user: User, client: Optional[httpx.AsyncClient] = None) -> Dict[str, Any]:
    """The image + caption to the user's own linked private chat. {"sent": bool, "reason": code | None, "message": ...};
    never the bot token. Reasons: not_owner, bot_not_configured, not_linked, blocked_by_user, send_failed."""
    if row.user_id != user.id:
        return _failure(NOT_OWNER)
    chat, token, reason = await _telegram_target(session, user)
    if reason is not None or chat is None or not token:
        return _failure(reason or BOT_NOT_CONFIGURED)
    from app.ai.vision_audit import send_photo
    png, caption = await asyncio.to_thread(image_and_caption, row)
    try:
        await send_photo(token, chat, png, caption, client)
    except RuntimeError as exc:
        why = str(exc).replace(token, "[redacted]").lower()
        code = BLOCKED_BY_USER if why.startswith("telegram api") and any(h in why for h in _BLOCKED_HINTS) else SEND_FAILED
        logger.info("Evidence %s not sent to Telegram: %s", row.id, code)
        return _failure(code)
    finally:
        token = ""
    return {"sent": True, "reason": None, "message": None, "bytes": len(png)}


async def alert_photos(session: AsyncSession, channel: AlertChannelRecord, bot_token: str, notification: Any, address: Optional[str],
                       client: Optional[httpx.AsyncClient] = None) -> int:
    """After a screen alert's text went to a user's linked chat (`address`): the evidence images of that notification's
    events (at most IMAGES_PER_ALERT), only when the flag is on and the evidence belongs to the user whose chat it is.
    Never raises (the text is already delivered; an image problem is logged). Returns the images sent."""
    sent = 0
    try:
        if not address or notification.event_type != "SCREEN_ALERT" or not await enabled(session, channel.tenant_id):
            return 0
        events = list(await session.scalars(select(AlertEventRecord.id).where(AlertEventRecord.notification_id == notification.id,
                                                                              AlertEventRecord.tenant_id == channel.tenant_id)
                                            .order_by(AlertEventRecord.symbol)))
        if not events:
            return 0
        rows = list(await session.scalars(select(ScreenEvidenceRecord).where(ScreenEvidenceRecord.alert_event_id.in_(events),
                                                                             ScreenEvidenceRecord.tenant_id == channel.tenant_id)
                                          .order_by(ScreenEvidenceRecord.symbol, ScreenEvidenceRecord.id)))
        from app.ai.vision_audit import send_photo
        chats: Dict[Optional[int], Optional[str]] = {}
        owned = []
        for row in rows:                                  # only the evidence owner's own chat - filtered before the cap
            if row.user_id not in chats:
                chats[row.user_id] = await own_chat(session, row.tenant_id, row.user_id)
            if chats[row.user_id] == str(address):
                owned.append(row)
        for row in owned[:IMAGES_PER_ALERT]:
            png, caption = await asyncio.to_thread(image_and_caption, row)
            await send_photo(bot_token, str(address), png, caption, client)
            sent += 1
    except Exception as exc:  # noqa: BLE001 - the alert text is delivered; images are best effort
        logger.warning("Evidence image not sent for notification %s: %s", getattr(notification, "id", "?"), exc.__class__.__name__)
    return sent


__all__ = ["FLAG", "enabled", "canonical", "view", "store_for_run", "store_for_events", "readable", "for_run", "load", "send_to_own_chat",
           "alert_photos", "image_and_caption", "telegram_state", "REASON_TEXT", "daily_room", "MAX_PER_RUN", "MAX_PER_RULE", "MAX_PER_TENANT_DAY", "IMAGES_PER_ALERT"]
