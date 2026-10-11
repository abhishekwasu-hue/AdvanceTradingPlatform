"""Phase BE: Telegram inbound.

The organisation's alert bot (Settings > Alerts > Telegram) can also *listen*: Telegram posts every
message and button press to `POST /api/telegram/webhook/{webhook_token}` (the tenant's TradingView
webhook token, already a per-tenant secret URL) with the `X-Telegram-Bot-Api-Secret-Token` header
the operator set when registering the webhook. Both must match; the chat must be on the
whitelist (`chat_id` plus `allowed_chat_ids`); everything else is ignored, audited, and answered
200 so Telegram stops retrying.

What a whitelisted chat can do:
* commands - `/brief`, `/positions`, `/risk`, `/news`, `/levels NIFTY`, `/thesis NIFTY`,
  `/why <trade_id>`, `/help` - and free questions through the Copilot router. All read-only.
* inline **Approve / Reject** buttons on monitoring-agent proposals: the button carries only a
  one-time nonce (`p:<nonce>`, inside Telegram's 64-byte limit); the server row holds the HMAC
  over (tenant, action, nonce, decision), the expiry (the proposal's own) and the chat it was sent
  to. A press runs the same `monitor.decide()` -> `execute()` path the web uses, as the tenant's
  owner, once. Allowed over Telegram: PAUSE_DEPLOYMENT / REDUCE_RISK / REVIEW_STRATEGY on **PAPER**
  deployments. Anything touching a LIVE deployment, and every EXIT_POSITION, answers "web +
  authenticator only" (the decision by the operator; ADR-0006, Phase C3 step-up).
  PW5: a third one-time button, **Snooze 1h**, on its own row: not a decision - the proposal stays
  PROPOSED, is hidden for an hour (never past its expiry) and is sent again once, with fresh buttons.
* Z3 / BT2: an ENTRY proposal (always PAPER) is decided over Telegram too, and with `approval_only_entries` on so is an
  "Exit now?" on a PAPER position. `/exit <trade id>` closes a PAPER position at once (ADR-0004: an exit is never
  held back); LIVE positions close on the web.

Rate limit 20 messages a minute per chat; every handled update is metered (`telegram_inbound`).
"""
from __future__ import annotations

import hashlib
import hmac
import html
import json
import logging
import re
import secrets
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from typing import Deque, Dict, List, Optional, Tuple

import httpx
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import briefing, market_memory, monitor, sentiment
from app.ai.interview import tr
from app.alerts.channels import TelegramConfig, decrypt_config, decrypt_raw, encrypt_config, parse_config
from app.alerts.links import START_CODE, consume_start
from app.cache.client import cache_incr_window
from app.audit.log import write_audit_log
from app.billing.service import meter
from app.core import config as app_config
from app.core.enums import AlertChannelType, UserRole
from app.db.models import AiActionRecord, AlertChannelRecord, StrategyDeploymentRecord, TelegramCallbackRecord, Tenant, TradeRecord, User
from app.market_data.calendar import IST
from app.risk_engine.routes import get_tenant_risk_config
from app.core.models import RiskConfig

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org"
TIMEOUT_SECONDS = 10.0
RATE_LIMIT = 20
RATE_WINDOW_SECONDS = 60.0
AUDIT_STRANGERS_PER_HOUR = 5     # a stranger's messages are audited this often per chat, then only logged
METRIC = "telegram_inbound"
TELEGRAM_ACTIONS = ("PAUSE_DEPLOYMENT", "REDUCE_RISK", "REVIEW_STRATEGY")      # EXIT_POSITION and anything LIVE: web + authenticator only
CALLBACK_PREFIX = "p:"
SNOOZE_MINUTES = 60                 # PW5: the Telegram Snooze button (the app offers more choices)
SNOOZE_LABEL = "1h"
COMMANDS = ("/help", "/start", "/brief", "/positions", "/risk", "/news", "/levels", "/thesis", "/why", "/exit")
_rate: Dict[Tuple[int, str], Deque[float]] = defaultdict(deque)


def http_client() -> httpx.AsyncClient:
    """Tests replace this with a MockTransport client."""
    return httpx.AsyncClient(timeout=TIMEOUT_SECONDS)


# --- settings on the Telegram channel ------------------------------------------------------------
async def telegram_channel(session: AsyncSession, tenant_id: int) -> Optional[AlertChannelRecord]:
    return await session.scalar(select(AlertChannelRecord).where(AlertChannelRecord.tenant_id == tenant_id,
                                                                  AlertChannelRecord.channel_type == AlertChannelType.TELEGRAM.value))


def webhook_url(tenant: Tenant) -> str:
    base = (app_config.FRONTEND_URL or "").rstrip("/")
    # P0.3 / S13: the TradingView token is no longer stored in plaintext, so the Telegram path carries the stored
    # hash (an identifier, not a credential - the secret header authenticates). Legacy organisations keep the
    # plaintext path until their hash is filled in. Rotating the TradingView URL changes this URL: re-register.
    identifier = tenant.webhook_token_hash or tenant.webhook_token
    return f"{base}/api/telegram/webhook/{identifier}"


def status_of(record: Optional[AlertChannelRecord], tenant: Tenant) -> dict:
    if record is None:
        return {"configured": False, "inbound_enabled": False, "allowed_chat_ids": [], "has_secret": False, "webhook_url": webhook_url(tenant),
                "telegram_actions": list(TELEGRAM_ACTIONS), "note": "Add the Telegram alert channel first (bot token + chat id)."}
    cfg: TelegramConfig = decrypt_config(record)  # type: ignore[assignment]
    return {"configured": True, "inbound_enabled": bool(cfg.inbound_enabled), "allowed_chat_ids": [cfg.chat_id] + [c for c in cfg.allowed_chat_ids if c != cfg.chat_id],
            "has_secret": bool(cfg.inbound_secret), "webhook_url": webhook_url(tenant), "telegram_actions": list(TELEGRAM_ACTIONS),
            "approvers": [{"telegram_user_id": a.telegram_user_id, "user_id": a.user_id, "email": a.email} for a in cfg.approvers],
            "note": "Approvals over Telegram cover PAPER deployments and reduce/pause proposals only; LIVE decisions need the web and your authenticator. "
                    "Without approvers, only a private chat on the whitelist can decide (as the owner); in a group, list who may."}


async def configure(session: AsyncSession, tenant: Tenant, user: User, *, enabled: bool, allowed_chat_ids: List[str],
                    approvers: Optional[List[dict]] = None) -> dict:
    """`approvers` (P0.8 / A6): [{telegram_user_id, email}] - the e-mail names an active user of this organisation and is
    stored as the platform user id, so a decision is recorded against the person who pressed the button."""
    record = await telegram_channel(session, tenant.id)
    if record is None:
        raise LookupError("No Telegram alert channel configured")
    raw = decrypt_raw(record)
    raw["inbound_enabled"] = bool(enabled)
    raw["allowed_chat_ids"] = sorted({str(c).strip() for c in allowed_chat_ids if str(c).strip()})[:10]
    if approvers is not None:
        resolved = []
        for item in approvers[:10]:
            tg_id, email = str(item.get("telegram_user_id") or "").strip(), str(item.get("email") or "").strip().lower()
            if not tg_id:
                continue
            member = await session.scalar(select(User).where(User.tenant_id == tenant.id, User.email == email, User.is_active.is_(True))) if email else None
            if member is None:
                raise ValueError(f"No active user {email or '(missing e-mail)'} in this organisation for Telegram user {tg_id}")
            resolved.append({"telegram_user_id": tg_id, "user_id": member.id, "email": member.email})
        raw["approvers"] = resolved
    if enabled and not raw.get("inbound_secret"):
        raw["inbound_secret"] = secrets.token_urlsafe(32)
    record.encrypted_config = encrypt_config(parse_config(record.channel_type, raw), tenant.id)
    await write_audit_log(session, tenant.id, user.id, "telegram_inbound_configured",
                          f"enabled={enabled} chats={len(raw['allowed_chat_ids']) + 1} approvers={len(raw.get('approvers') or [])}")
    await session.commit()
    return status_of(record, tenant)


async def register_webhook(session: AsyncSession, tenant: Tenant, user: User, *, client: Optional[httpx.AsyncClient] = None) -> dict:
    """Tells Telegram where to post (setWebhook with the secret token). The bot token never leaves the server."""
    record = await telegram_channel(session, tenant.id)
    if record is None:
        raise LookupError("No Telegram alert channel configured")
    cfg: TelegramConfig = decrypt_config(record)  # type: ignore[assignment]
    if not cfg.inbound_enabled or not cfg.inbound_secret:
        raise ValueError("Enable inbound commands first")
    answer = await _call(cfg, "setWebhook", {"url": webhook_url(tenant), "secret_token": cfg.inbound_secret, "allowed_updates": ["message", "callback_query"],
                                            "drop_pending_updates": True}, client)
    await write_audit_log(session, tenant.id, user.id, "telegram_webhook_registered", f"ok={answer.get('ok')} {str(answer.get('description', ''))[:120]}")
    await session.commit()
    return {"ok": bool(answer.get("ok")), "description": answer.get("description"), "webhook_url": webhook_url(tenant)}


# --- Telegram API ----------------------------------------------------------------------------------
async def _call(cfg: TelegramConfig, method: str, payload: dict, client: Optional[httpx.AsyncClient] = None) -> dict:
    own = client is None
    client = client or http_client()
    try:
        try:
            response = await client.post(f"{TELEGRAM_API}/bot{cfg.bot_token}/{method}", json=payload)
        except httpx.HTTPError as exc:                    # the network is not Telegram's answer; never a 500 to Telegram
            return {"ok": False, "description": f"transport: {type(exc).__name__}"}
        try:
            data = response.json()
        except ValueError:
            data = {"ok": False, "description": response.text[:200]}
        if response.status_code >= 400 and "description" not in data:
            data = {"ok": False, "description": f"HTTP {response.status_code}"}
        return data
    finally:
        if own:
            await client.aclose()


async def reply(cfg: TelegramConfig, chat_id: str, text: str, client: Optional[httpx.AsyncClient] = None, reply_markup: Optional[dict] = None,
                parse_mode: Optional[str] = "HTML") -> dict:
    payload = {"chat_id": chat_id, "text": text[:4000], "disable_web_page_preview": True}
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if reply_markup:
        payload["reply_markup"] = reply_markup
    answer = await _call(cfg, "sendMessage", payload, client)
    if not answer.get("ok", False):
        logger.warning("Telegram sendMessage to chat %s failed: %s", chat_id, str(answer.get("description", ""))[:200])
    return answer


# --- guards --------------------------------------------------------------------------------------------
def secret_ok(header_value: Optional[str], cfg: TelegramConfig) -> bool:
    return bool(cfg.inbound_secret) and bool(header_value) and hmac.compare_digest(str(header_value), str(cfg.inbound_secret))


def chat_allowed(cfg: TelegramConfig, chat_id: str) -> bool:
    return str(chat_id) == str(cfg.chat_id) or str(chat_id) in {str(c) for c in cfg.allowed_chat_ids}


_stranger_audits: Dict[Tuple[int, str], Deque[float]] = defaultdict(deque)


def audit_stranger(tenant_id: int, chat_id: str, now: Optional[float] = None) -> bool:
    """True when this rejection should go to the hash-chained audit log (the first few per chat per
    hour); the rest go to the application log so a stranger cannot flood the audit chain."""
    now = now if now is not None else time.monotonic()
    bucket = _stranger_audits[(tenant_id, str(chat_id))]
    while bucket and now - bucket[0] > 3600.0:
        bucket.popleft()
    if len(bucket) >= AUDIT_STRANGERS_PER_HOUR:
        return False
    bucket.append(now)
    return True


def _rate_limited_local(tenant_id: int, chat_id: str, now: Optional[float] = None) -> bool:
    now = now if now is not None else time.monotonic()
    bucket = _rate[(tenant_id, str(chat_id))]
    while bucket and now - bucket[0] > RATE_WINDOW_SECONDS:
        bucket.popleft()
    if len(bucket) >= RATE_LIMIT:
        return True
    bucket.append(now)
    return False


async def rate_limited(tenant_id: int, chat_id: str, now: Optional[float] = None) -> bool:
    """P0.8 / A6: the per-chat limit lives in Redis (shared by every API replica, survives a restart) as a fixed
    one-minute window; when Redis is unreachable the in-process window above still applies."""
    window = int((time.time() if now is None else now) // RATE_WINDOW_SECONDS)
    count = await cache_incr_window(f"tg:rate:{tenant_id}:{chat_id}:{window}", int(RATE_WINDOW_SECONDS * 2))
    if count is None:
        return _rate_limited_local(tenant_id, chat_id, now)
    return count > RATE_LIMIT


async def acting_user(session: AsyncSession, tenant_id: int) -> Optional[User]:
    user = await session.scalar(select(User).where(User.tenant_id == tenant_id, User.is_active.is_(True), User.role == UserRole.OWNER.value).order_by(User.id).limit(1))
    return user or await session.scalar(select(User).where(User.tenant_id == tenant_id, User.is_active.is_(True)).order_by(User.id).limit(1))


async def actor_for_sender(session: AsyncSession, tenant_id: int, cfg: TelegramConfig, from_id: str, chat_id: str = "") -> Optional[User]:
    """P0.8 / A6: the platform user a Telegram sender acts as. With approvers configured, only a listed `from.id` acts,
    as its mapped user. Without approvers (legacy setup) only a *private* chat acts - Telegram gives a private chat the
    user's own id, so `from.id == chat.id` on a whitelisted chat - as the owner; a member of a whitelisted group
    (`from.id != chat.id`) is nobody and is never recorded as the owner."""
    from_id = str(from_id or "")
    if not from_id:
        return None
    if cfg.approvers:
        match = next((a for a in cfg.approvers if str(a.telegram_user_id) == from_id), None)
        if match is None:
            return None
        user = await session.get(User, match.user_id)
        return user if user is not None and user.tenant_id == tenant_id and user.is_active else None
    if from_id == str(chat_id or "") and chat_allowed(cfg, from_id):
        return await acting_user(session, tenant_id)
    return None


# --- callbacks (approve / reject buttons) -------------------------------------------------------------
def _signature(tenant_id: int, action_id: int, nonce: str, decision: str) -> str:
    key = (app_config.JWT_SECRET_KEY or "atp").encode("utf-8")
    return hmac.new(key, f"{tenant_id}:{action_id}:{nonce}:{decision}".encode("utf-8"), hashlib.sha256).hexdigest()


async def telegram_allowed(session: AsyncSession, action: AiActionRecord) -> Tuple[bool, str]:
    """PAPER deployments and the reduce/pause/review actions only. Z3 / BT2: also an ENTRY proposal (it always places a
    PAPER order) and, with `approval_only_entries` on, an "Exit now?" on a PAPER position."""
    if action.action == "ENTRY":
        mode = str(json.loads(action.evidence_json or "{}").get("mode") or "")
        return (True, "") if mode == "PAPER" else (False, "This entry proposal is decided on the web only.")
    if action.action == "EXIT_POSITION" and action.trade_id is not None:
        from app.execution import approval
        trade = await session.get(TradeRecord, action.trade_id)
        if trade is not None and trade.tenant_id == action.tenant_id and trade.mode == "PAPER" and await approval.flag_on(session, action.tenant_id):
            return True, ""
    if action.action not in TELEGRAM_ACTIONS:
        return False, "This kind of proposal is decided on the web only (with your authenticator)."
    if action.deployment_id is None:
        return False, "Proposals without a deployment are decided on the web only."
    dep = await session.get(StrategyDeploymentRecord, action.deployment_id)
    if dep is None or dep.tenant_id != action.tenant_id or dep.mode != "PAPER":
        return False, "LIVE proposals are decided on the web with your authenticator, never over Telegram."
    return True, ""


async def keyboard_for(session: AsyncSession, action: AiActionRecord, chat_id: str, now: Optional[datetime] = None) -> Optional[dict]:
    """One-time Approve / Reject (and, PW5, Snooze 1h) buttons for a proposal, or None when Telegram may not decide it."""
    allowed, _ = await telegram_allowed(session, action)
    if not allowed or action.status != "PROPOSED":
        return None
    now = now or datetime.now(timezone.utc)
    buttons: Dict[str, dict] = {}
    for decision, label in (("approve", "✅ Approve"), ("reject", "❌ Reject"), ("snooze", f"⏰ Snooze {SNOOZE_LABEL}")):
        nonce = secrets.token_urlsafe(18)
        session.add(TelegramCallbackRecord(tenant_id=action.tenant_id, action_id=action.id, nonce=nonce, decision=decision, chat_id=str(chat_id),
                                           signature=_signature(action.tenant_id, action.id, nonce, decision), expires_at=action.expires_at, created_at=now))
        buttons[decision] = {"text": label, "callback_data": f"{CALLBACK_PREFIX}{nonce}"}
    await session.flush()
    # PW5: Snooze sits on its own row under Approve / Reject - the same one-time nonce mechanism; pressing any of the
    # three uses up the other two, and the wake-up reminder arrives with fresh buttons.
    return {"inline_keyboard": [[buttons["approve"], buttons["reject"]], [buttons["snooze"]]]}


async def decide_from_callback(session: AsyncSession, tenant: Tenant, cfg: TelegramConfig, data: str, chat_id: str, *, now: Optional[datetime] = None,
                               from_id: str = "") -> str:
    """Resolves one button press: nonce -> signed row -> the same decide/execute path the web uses. Returns the text to show.
    P0.8 / A6: the sender (`from_id`) must be an authorised approver; the decision is recorded against that person."""
    now = now or datetime.now(timezone.utc)
    nonce = data[len(CALLBACK_PREFIX):] if data.startswith(CALLBACK_PREFIX) else ""
    row = await session.scalar(select(TelegramCallbackRecord).where(TelegramCallbackRecord.nonce == nonce, TelegramCallbackRecord.tenant_id == tenant.id)) if nonce else None
    if row is None:
        await write_audit_log(session, tenant.id, None, "telegram_callback_rejected", f"unknown or foreign nonce from chat {chat_id}")
        await session.commit()
        return "This button is not valid for this organisation."
    if not hmac.compare_digest(row.signature, _signature(row.tenant_id, row.action_id, row.nonce, row.decision)):
        await write_audit_log(session, tenant.id, None, "telegram_callback_rejected", f"bad signature nonce={nonce[:6]}")
        await session.commit()
        return "This button failed verification."
    if row.chat_id != str(chat_id) or not chat_allowed(cfg, chat_id):
        await write_audit_log(session, tenant.id, None, "telegram_callback_rejected", f"chat {chat_id} is not the chat this button was sent to")
        await session.commit()
        return "This button belongs to another chat."
    if row.used_at is not None:
        await write_audit_log(session, tenant.id, None, "telegram_callback_replayed", f"nonce={nonce[:6]} already used at {row.used_at}")
        await session.commit()
        return "Already decided - this button was used before."
    expires = row.expires_at if row.expires_at.tzinfo else row.expires_at.replace(tzinfo=timezone.utc)
    if expires <= now:
        row.used_at = now
        await session.commit()
        return "This proposal has expired."
    action = await session.get(AiActionRecord, row.action_id)
    if action is None or action.tenant_id != tenant.id:
        return "The proposal no longer exists."
    allowed, why = await telegram_allowed(session, action)
    if not allowed:
        row.used_at = now
        await write_audit_log(session, tenant.id, None, "telegram_callback_refused", f"action #{action.id} {action.action}: {why}")
        await session.commit()
        return why
    user = await actor_for_sender(session, tenant.id, cfg, from_id, chat_id)
    if user is None:
        await write_audit_log(session, tenant.id, None, "telegram_callback_refused", f"action #{action.id}: Telegram user {from_id or '?'} in chat {chat_id} is not an authorised approver")
        await session.commit()
        return "You are not an authorised approver for this organisation - ask the owner to add your Telegram user id under Settings."
    # Claim the nonce atomically: two deliveries of the same (or the sibling) button race here, and
    # only the one whose conditional UPDATE lands gets to decide.
    claimed = await session.execute(update(TelegramCallbackRecord).where(TelegramCallbackRecord.id == row.id, TelegramCallbackRecord.used_at.is_(None))
                                    .values(used_at=now, used_by=user.id))
    await session.commit()
    if claimed.rowcount != 1:
        await write_audit_log(session, tenant.id, None, "telegram_callback_replayed", f"nonce={nonce[:6]} claimed concurrently")
        await session.commit()
        return "Already decided - this button was used before."
    siblings = await session.execute(update(TelegramCallbackRecord).where(TelegramCallbackRecord.action_id == row.action_id, TelegramCallbackRecord.tenant_id == tenant.id,
                                                                          TelegramCallbackRecord.id != row.id, TelegramCallbackRecord.used_at.is_(None)).values(used_at=now))
    del siblings
    await session.commit()
    if row.decision == "snooze":
        # PW5: not a decision - the proposal stays PROPOSED and comes back once after SNOOZE_MINUTES (cut at its expiry).
        try:
            action = await monitor.snooze(session, action, user, until=now + timedelta(minutes=SNOOZE_MINUTES), via=f"Telegram chat {chat_id} by user {from_id}", now=now)
        except ValueError as exc:
            return f"Could not snooze: {exc}"
        wake = action.snoozed_until
        at = (wake if wake.tzinfo else wake.replace(tzinfo=timezone.utc)).astimezone(IST).strftime("%H:%M") if wake else "?"
        return f"Proposal #{action.id} snoozed until {at} IST - it will be sent again then (it still expires on time)."
    try:
        action = await monitor.decide(session, action, user, approve=(row.decision == "approve"), note=f"via Telegram chat {chat_id} by user {from_id}", now=now)
    except ValueError as exc:
        return f"Could not decide: {exc}"
    if row.decision == "approve":
        from app.execution.approval import execution_sources
        price_lookup, broker = await execution_sources(session, action)      # Z3: an entry is re-quoted, an exit priced
        action = await monitor.execute(session, action, user, price_lookup=price_lookup, broker=broker, now=now)
    await write_audit_log(session, tenant.id, user.id, "telegram_decision", f"#{action.id} {action.action} {action.status} via chat {chat_id} by Telegram user {from_id}")
    await session.commit()
    return f"Proposal #{action.id} {action.action.replace('_', ' ').lower()}: {action.status}" + (f" - {action.result}" if action.result else "")


# --- commands --------------------------------------------------------------------------------------------
def help_text(lang: str) -> str:
    return tr(lang,
              "Commands: /brief · /positions · /risk · /news · /levels NIFTY · /thesis NIFTY · /why <trade id> · /exit <trade id> · /help. "
              "Anything else is a question to the Copilot. Nothing here places an order; /exit closes a PAPER position now. "
              "Buttons on proposals decide PAPER entries and exits and reduce/pause.",
              "Commands: /brief · /positions · /risk · /news · /levels NIFTY · /thesis NIFTY · /why <trade id> · /exit <trade id> · /help. बाकी काहीही = Copilot ला प्रश्न. "
              "इथून कोणतीही order लागत नाही; /exit PAPER position लगेच बंद करते. Proposal वरची buttons PAPER entries, exits आणि reduce/pause ठरवतात.")


def _lang(text: str) -> str:
    return "mr" if re.search(r"[ऀ-ॿ]", text or "") else "en"


async def _positions_text(session: AsyncSession, tenant_id: int, lang: str) -> str:
    rows = list(await session.scalars(select(TradeRecord).where(TradeRecord.tenant_id == tenant_id, TradeRecord.exit_time.is_(None)).order_by(TradeRecord.entry_time.desc()).limit(15)))
    if not rows:
        return tr(lang, "No open positions.", "कोणतीही open position नाही.")
    lines = [tr(lang, f"Open positions ({len(rows)}):", f"Open positions ({len(rows)}):")]
    for t in rows:
        lines.append(f"#{t.id} {t.mode} {t.direction} {t.symbol} x{t.quantity:g} @ {t.entry_price:,.2f} SL {t.stop_loss:,.2f}"
                     + (f" T1 {t.target1:,.2f}" if t.target1 else ""))
    return "\n".join(lines)


async def _why_text(session: AsyncSession, tenant_id: int, trade_id: int, lang: str) -> str:
    t = await session.get(TradeRecord, trade_id)
    if t is None or t.tenant_id != tenant_id:
        return tr(lang, "No such trade.", "असा trade नाही.")
    parts = [f"#{t.id} {t.mode} {t.direction} {t.symbol} ({t.strategy_id}) entered {t.entry_time:%d %b %H:%M} at {t.entry_price:,.2f}, SL {t.stop_loss:,.2f}"]
    if t.exit_time:
        parts.append(f"exited {t.exit_time:%d %b %H:%M} at {t.exit_price:,.2f} ({t.exit_reason or ''}), P&L {t.pnl:+,.2f}" if t.exit_price is not None else "exited")
    else:
        parts.append(tr(lang, "still open; the worker watches the stop and targets.", "अजून open; worker stop आणि targets पाहतो आहे."))
    return "\n".join(parts)


async def _exit_text(session: AsyncSession, tenant_id: int, user: User, arg: str) -> str:
    """Z3 / BT2: "Exit now" from the chat - `/exit <trade id>` closes that PAPER position at once (the live price when a
    broker session quotes it, else the last price seen). LIVE positions are closed on the web (authenticated session)."""
    from app.execution import approval
    try:
        trade_id = int(arg.lstrip("#"))
    except ValueError:
        return "Usage: /exit <trade id> - /positions lists them."
    trade = await session.get(TradeRecord, trade_id)
    if trade is None or trade.tenant_id != tenant_id:
        return "No such position."
    if trade.exit_time is not None:
        return f"Position #{trade.id} is already closed."
    if trade.mode != "PAPER":
        return "LIVE positions are closed from the web app (Positions page), never over Telegram."
    try:
        outcomes = await approval.exit_now(session, trade, user, via="telegram")
    except ValueError as exc:
        return f"Could not exit #{trade.id}: {exc}"
    closed = [o for o in outcomes if o.closed]
    if not closed:
        return f"Position #{trade.id} not closed: {'; '.join(w for o in outcomes for w in o.warnings) or 'unknown'}"
    return "\n".join(f"Closed #{o.trade_id} at {o.exit_price:,.2f}, P&L {o.pnl:+,.2f} (after charges)" for o in closed)


async def _risk_text(session: AsyncSession, user: User, lang: str) -> str:
    cfg = await get_tenant_risk_config(user.tenant_id, session) or RiskConfig()
    day = await briefing.your_day(session, user, cfg, datetime.now(timezone.utc))
    out = [tr(lang, f"Risk: capital {cfg.capital:,.0f}, {cfg.risk_per_trade_pct}% per trade, daily loss cap {cfg.max_daily_loss_pct}%.",
              f"Risk: भांडवल {cfg.capital:,.0f}, प्रति trade {cfg.risk_per_trade_pct}%, दैनिक तोटा मर्यादा {cfg.max_daily_loss_pct}%.")]
    for mode in ("PAPER", "LIVE"):
        d = day.get(mode) or {}
        if d.get("active") or d.get("trades_today"):
            out.append(f"{mode}: {tr(lang, 'realised today', 'आजचा P&L')} {d.get('realised_pnl', 0):+,.0f}, {tr(lang, 'loss budget left', 'उरलेले तोटा-बजेट')} {d.get('loss_left', 0):,.0f}, "
                       f"{tr(lang, 'open', 'open')} {d.get('open_positions', 0)}")
    return "\n".join(out)


async def _news_text(session: AsyncSession, tenant_id: int, lang: str) -> str:
    from app.news_feed import service as news_feed
    if not await news_feed.enabled(session, tenant_id):
        return tr(lang, "The news feed is off for this platform.", "News feed या platform वर बंद आहे.")
    items = await news_feed.items(session, tenant_id, hours=24, min_severity=3)
    if not items:
        return tr(lang, "No notable feed items in the last 24 hours (unverified feed).", "गेल्या 24 तासांत लक्षणीय बातमी नाही (unverified feed).")
    lines = [tr(lang, "Feed (unverified, severity >= 3):", "Feed (unverified, तीव्रता >= 3):")]
    for it in items[:8]:
        c = it["classification"]
        lines.append(f"[{c.get('severity')}] {it['headline'][:120]} - {it['source']}")
    return "\n".join(lines)


async def _levels_text(session: AsyncSession, tenant_id: int, symbol: str, lang: str) -> str:
    memory = await market_memory.latest(session, tenant_id, symbol=symbol or None)
    snap = next((s for s in memory.get("symbols", []) if not symbol or s["symbol"] == symbol.upper()), None)
    if snap is None:
        return tr(lang, f"No market read for {symbol or 'the watchlist'} yet.", f"{symbol or 'watchlist'} साठी अजून market read नाही.")
    payload = snap.get("payload") or {}
    levels = payload.get("levels") or {}
    parts = [f"{snap['symbol']} {snap.get('last_price')} ({(snap.get('change_pct') or 0):+.2f}%) bias {snap.get('bias')} regime {snap.get('regime')} structure {snap.get('structure')}"]
    if levels:
        parts.append(", ".join(f"{k} {v}" for k, v in list(levels.items())[:8]))
    return "\n".join(parts)


async def _thesis_text(session: AsyncSession, tenant_id: int, symbol: str, lang: str) -> str:
    from app.ai import thesis
    from app.platform.controls import flag_enabled
    flag_on = await flag_enabled(session, thesis.FLAG, tenant_id)
    if symbol and flag_on:                                                       # Phase BD-lite: the full thesis when the flag is on
        built = await thesis.current(session, tenant_id, symbol, lang=lang)
        if built is not None:
            return "\n".join(built["lines"])
    memory = await market_memory.latest(session, tenant_id)
    lines = await _levels_text(session, tenant_id, symbol, lang), *sentiment.view(lang, memory.get("sentiment"))[:2]
    trailer = (tr(lang, "(No market read of this symbol yet - add it to the watchlist.)", "(या symbol चा market read अजून नाही - watchlist मध्ये घाला.)") if flag_on
               else tr(lang, "(Scenarios need the market thesis feature; ask the platform admin.)", "(Scenario साठी market thesis feature लागते; platform admin ला सांगा.)"))
    return "\n".join(lines) + "\n" + trailer


async def answer_text(session: AsyncSession, tenant: Tenant, user: User, text: str) -> str:
    text = (text or "").strip()
    lang = _lang(text)
    cmd, _, arg = text.partition(" ")
    cmd = cmd.lower().split("@")[0]
    arg = arg.strip()
    if cmd in ("/help", "/start"):
        return help_text(lang)
    from app.platform.controls import flag_enabled
    if cmd in ("/brief", "/risk") or not cmd.startswith("/"):
        if not await flag_enabled(session, "ai_copilot", tenant.id):          # the operator's AI kill flag binds Telegram too
            return tr(lang, "The AI Copilot is switched off on this platform right now.", "AI Copilot या platform वर सध्या बंद आहे.")
    if cmd in ("/brief", "/thesis") or not cmd.startswith("/"):
        # P0.8-D: the same first-use acknowledgement as the web app; approve/reject callbacks never depend on it.
        from app.ai import compliance_terms as terms
        if await terms.latest(session, terms.KIND_COPILOT, tenant_id=tenant.id, user_id=user.id) is None:
            return tr(lang, "Open the AI Copilot in the web app once and accept its acknowledgement first (it is not an investment adviser).",
                      "आधी web app मध्ये AI Copilot एकदा उघडा आणि त्याची सूचना स्वीकारा (तो गुंतवणूक सल्लागार नाही).")
    if cmd == "/brief":
        brief = await briefing.build(session, user, lang)
        return "\n".join(briefing.summary_lines(lang, brief))
    if cmd == "/positions":
        return await _positions_text(session, tenant.id, lang)
    if cmd == "/risk":
        return await _risk_text(session, user, lang)
    if cmd == "/news":
        return await _news_text(session, tenant.id, lang)
    if cmd == "/levels":
        return await _levels_text(session, tenant.id, arg.upper(), lang)
    if cmd == "/thesis":
        return await _thesis_text(session, tenant.id, arg.upper(), lang)
    if cmd == "/why":
        try:
            return await _why_text(session, tenant.id, int(arg), lang)
        except ValueError:
            return tr(lang, "Usage: /why <trade id>", "वापर: /why <trade id>")
    if cmd == "/exit":
        return await _exit_text(session, tenant.id, user, arg)
    if cmd.startswith("/"):
        return help_text(lang)
    from app.ai.routes import copilot_answer
    out = await copilot_answer(session, user, text, lang)
    answer = str(out.get("answer") or help_text(lang))
    return f"{answer}\n\n({out['note']})" if out.get("note") else answer      # P0.8-B: the "AI answer not used" note travels too


# --- the update --------------------------------------------------------------------------------------
async def _link_from_start(session: AsyncSession, tenant: Tenant, cfg: TelegramConfig, code: str, chat_id: str, from_id: str, *,
                           client: Optional[httpx.AsyncClient], now: datetime) -> dict:
    """S3b-2: `/start <code>` links this private chat to the user who asked for the code. Open to chats outside the
    whitelist (that is the point), so it is rate limited and gives nothing away: the chat only learns linked / not."""
    if await rate_limited(tenant.id, chat_id):
        return {"handled": "rate_limited"}
    row, why = await consume_start(session, tenant.id, code, chat_id, from_id, now=now)
    if row is not None:
        await write_audit_log(session, tenant.id, row.user_id, "telegram_linked", f"chat {chat_id} linked for screen alerts")
    elif audit_stranger(tenant.id, f"link:{chat_id}"):
        await write_audit_log(session, tenant.id, None, "telegram_link_refused", f"chat {chat_id}: {why}")
    await session.commit()
    if row is not None:
        text = "Linked. Screen alerts from the rules you created will come to this chat. Unlink any time in Settings."
    elif why == "not_private":
        text = "Send the code to the bot in a private chat, not in a group."
    else:
        text = "That code did not work (wrong, used or expired). Make a new one in Settings."
    await reply(cfg, chat_id, text, client, parse_mode=None)
    return {"handled": "link", "result": why}


async def handle_update(session: AsyncSession, tenant: Tenant, cfg: TelegramConfig, update: dict, *, client: Optional[httpx.AsyncClient] = None,
                        now: Optional[datetime] = None) -> dict:
    """One Telegram update (message or callback_query). Always returns a small dict; never raises to Telegram."""
    now = now or datetime.now(timezone.utc)
    message = update.get("message") or update.get("edited_message")
    callback = update.get("callback_query")
    if callback:
        chat_id = str(((callback.get("message") or {}).get("chat") or {}).get("id") or (callback.get("from") or {}).get("id") or "")
        from_id = str((callback.get("from") or {}).get("id") or "")
        if await rate_limited(tenant.id, chat_id):
            await _call(cfg, "answerCallbackQuery", {"callback_query_id": callback.get("id"), "text": "Slow down - try again in a minute."}, client)
            return {"handled": "rate_limited"}
        if not chat_allowed(cfg, chat_id):
            if audit_stranger(tenant.id, chat_id):
                await write_audit_log(session, tenant.id, None, "telegram_inbound_ignored", f"callback from non-whitelisted chat {chat_id}")
                await session.commit()
            else:
                logger.warning("Telegram callback from non-whitelisted chat %s ignored (tenant %s)", chat_id, tenant.id)
            return {"handled": "ignored", "reason": "chat not whitelisted"}
        data = str(callback.get("data") or "")
        if data.startswith("oi:"):                   # OI Banner O4b: snooze / mute - an authorised sender only, never an order
            from app.option_chain import oi_alerts
            actor = await actor_for_sender(session, tenant.id, cfg, from_id, chat_id)
            text = (await oi_alerts.snooze_from_telegram(session, tenant.id, data, actor.id, now) if actor is not None
                    else "Only an authorised user of this bot can snooze alerts.")
        else:
            text = await decide_from_callback(session, tenant, cfg, data, chat_id, now=now, from_id=from_id)
        await meter(session, tenant.id, METRIC, 1, source="telegram", metadata={"kind": "callback"})
        await session.commit()
        await _call(cfg, "answerCallbackQuery", {"callback_query_id": callback.get("id"), "text": text[:190]}, client)
        msg = callback.get("message") or {}
        if msg.get("message_id"):
            await _call(cfg, "editMessageText", {"chat_id": chat_id, "message_id": msg["message_id"], "text": f"{html.escape(msg.get('text') or '')}\n\n<b>{html.escape(text)}</b>",
                                                 "parse_mode": "HTML"}, client)
        return {"handled": "callback", "reply": text}
    if message:
        chat_id = str((message.get("chat") or {}).get("id") or "")
        from_id = str((message.get("from") or {}).get("id") or "")
        text = str(message.get("text") or "")
        if text.startswith("/start ") and START_CODE.fullmatch(text[len("/start "):].strip()):
            return await _link_from_start(session, tenant, cfg, text[len("/start "):].strip(), chat_id, from_id, client=client, now=now)
        if not chat_allowed(cfg, chat_id):
            if not await rate_limited(tenant.id, chat_id) and audit_stranger(tenant.id, chat_id):
                await write_audit_log(session, tenant.id, None, "telegram_inbound_ignored", f"message from non-whitelisted chat {chat_id}: {text[:60]!r}")
                await session.commit()
            else:
                logger.warning("Telegram message from non-whitelisted chat %s ignored (tenant %s)", chat_id, tenant.id)
            return {"handled": "ignored", "reason": "chat not whitelisted"}
        if await rate_limited(tenant.id, chat_id):
            await reply(cfg, chat_id, tr(_lang(text), "Slow down - 20 messages a minute.", "थोडे थांबा - मिनिटाला 20 messages."), client, parse_mode=None)
            return {"handled": "rate_limited"}
        user = await actor_for_sender(session, tenant.id, cfg, from_id, chat_id)
        if user is None:
            # P0.8 / A6: a whitelisted group's other members get no account data and act as nobody.
            if audit_stranger(tenant.id, f"{chat_id}:{from_id}"):
                await write_audit_log(session, tenant.id, None, "telegram_inbound_refused", f"Telegram user {from_id or '?'} in chat {chat_id} is not an authorised user: {text[:60]!r}")
                await session.commit()
            await reply(cfg, chat_id, tr(_lang(text), "You are not an authorised user of this bot - ask the owner to add your Telegram user id under Settings.",
                                         "तुम्ही या bot चे अधिकृत वापरकर्ते नाही - owner ला Settings मध्ये तुमचा Telegram user id जोडायला सांगा."), client, parse_mode=None)
            return {"handled": "refused", "reason": "sender not an approver"}
        try:
            answer = await answer_text(session, tenant, user, text)
        except Exception as exc:  # noqa: BLE001 - the chat gets a plain error, the log the detail
            logger.exception("Telegram command failed")
            answer = tr(_lang(text), f"Could not answer: {type(exc).__name__}", f"उत्तर देता आले नाही: {type(exc).__name__}")
        await meter(session, tenant.id, METRIC, 1, source="telegram", metadata={"kind": "message", "command": text.split(" ")[0][:20] if text.startswith("/") else "text"})
        await session.commit()
        await reply(cfg, chat_id, answer, client, parse_mode=None)          # plain text: command output is never HTML
        return {"handled": "message", "reply": answer}
    return {"handled": "ignored", "reason": "no message"}


async def send_proposal_buttons(session: AsyncSession, channel: AlertChannelRecord, action: AiActionRecord, text_html: str,
                                client: Optional[httpx.AsyncClient] = None, now: Optional[datetime] = None,
                                extra_row: Optional[list] = None) -> bool:
    """Used by the alert dispatcher: an AI_PROPOSAL to a Telegram channel with inbound on gets the two
    buttons when Telegram may decide it. Returns True when the message was sent with buttons."""
    cfg: TelegramConfig = decrypt_config(channel)  # type: ignore[assignment]
    if not cfg.inbound_enabled:
        return False
    keyboard = await keyboard_for(session, action, cfg.chat_id, now)
    if keyboard is None:
        return False
    if extra_row:                                      # PW4: "Open in app" under Approve / Reject
        keyboard = {**keyboard, "inline_keyboard": [*keyboard.get("inline_keyboard", []), extra_row]}
    answer = await reply(cfg, cfg.chat_id, text_html, client, reply_markup=keyboard)
    if not answer.get("ok", False):
        await session.rollback()                      # the nonce rows go with the failed send; the retry mints new ones
        raise RuntimeError(f"Telegram API: {answer.get('description') or 'send failed'}")
    message_id = (answer.get("result") or {}).get("message_id")
    if message_id:                                     # PW4: remembered so an app decision / expiry can update this message
        action.telegram_chat_id, action.telegram_message_id = str(cfg.chat_id), int(message_id)
        await session.flush()
    return True


# --- PW4: Telegram <-> app, ported from the Trade bot (vision/tg.py: edit_caption / send_photo) -------------------

_OUTCOME = {
    "APPROVED": "✅ Approved in the app", "EXECUTED": "✅ Approved in the app", "REJECTED": "❌ Rejected in the app",
    "EXPIRED": "⌛ Expired - no decision in time", "FAILED": "⚠️ Approved in the app; it could not be carried out",
}


def outcome_text(action: AiActionRecord, now: Optional[datetime] = None) -> str:
    when = (action.decided_at or now or datetime.now(timezone.utc))
    when = when if when.tzinfo else when.replace(tzinfo=timezone.utc)
    stamp = when.astimezone(timezone(timedelta(hours=5, minutes=30))).strftime("%H:%M IST")
    return f"{_OUTCOME.get(action.status, action.status)} · {stamp}"


async def sync_proposal_message(session: AsyncSession, action: AiActionRecord, *, client: Optional[httpx.AsyncClient] = None,
                                now: Optional[datetime] = None) -> bool:
    """A proposal decided in the app (or expired) whose Telegram message still shows Approve / Reject: take the buttons
    off and reply under it with what happened, so a stale button is never pressed. Like the Trade bot's tg helpers it
    never raises - Telegram being down must not fail the decision. True when Telegram accepted both calls."""
    if not action.telegram_message_id or not action.telegram_chat_id or action.status == "PROPOSED":
        return False
    try:
        channel = await telegram_channel(session, action.tenant_id)
        if channel is None:
            return False
        cfg: TelegramConfig = decrypt_config(channel)  # type: ignore[assignment]
        chat_id, message_id = action.telegram_chat_id, int(action.telegram_message_id)
        cleared = await _call(cfg, "editMessageReplyMarkup", {"chat_id": chat_id, "message_id": message_id,
                                                              "reply_markup": {"inline_keyboard": []}}, client)
        said = await _call(cfg, "sendMessage", {"chat_id": chat_id, "text": outcome_text(action, now), "reply_to_message_id": message_id,
                                                "disable_notification": True}, client)
        return bool(cleared.get("ok")) and bool(said.get("ok"))
    except Exception as exc:  # noqa: BLE001 - best effort by design
        logger.warning("Telegram proposal message not updated for action %s: %s", action.id, type(exc).__name__)
        return False


def _plain(caption_html: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", caption_html))


async def send_photo(cfg: TelegramConfig, chat_id: str, png: Optional[bytes], caption_html: str, *,
                     reply_markup: Optional[dict] = None, client: Optional[httpx.AsyncClient] = None) -> Optional[int]:
    """A chart with its caption (Telegram allows 1024 characters), optional buttons; the message id or None. As in the
    Trade bot: a caption Telegram cannot parse as HTML is sent once more as plain text, and no image means a text
    message. Never raises; the token never appears in what it logs or returns."""
    own = client is None
    client = client or http_client()
    try:
        for parse_html in (True, False):
            text = caption_html if parse_html else _plain(caption_html)
            data: dict = {"chat_id": str(chat_id)}
            if parse_html:
                data["parse_mode"] = "HTML"
            if reply_markup:
                data["reply_markup"] = json.dumps(reply_markup)
            try:
                if png:
                    data["caption"] = text[:1024]
                    response = await client.post(f"{TELEGRAM_API}/bot{cfg.bot_token}/sendPhoto", data=data,
                                                 files={"photo": ("chart.png", png, "image/png")})
                else:
                    data["text"] = text[:4096]
                    response = await client.post(f"{TELEGRAM_API}/bot{cfg.bot_token}/sendMessage", data=data)
                answer = response.json()
            except (httpx.HTTPError, ValueError) as exc:
                logger.warning("Telegram photo not sent: %s", type(exc).__name__)
                return None
            if answer.get("ok"):
                return (answer.get("result") or {}).get("message_id")
            if response.status_code != 400:            # only a parse error is worth the plain-text retry
                break
        return None
    finally:
        if own:
            await client.aclose()
