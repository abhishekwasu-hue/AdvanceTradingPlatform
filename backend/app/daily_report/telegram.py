"""M89 (owner order M89, addendum to M88-R): the stored daily report on Telegram.

* TG1: one short HTML message (the NIFTY read, breadth, the conclusion and the information line, inside Telegram's
  4096-character limit), the full report as a PDF document, and a button that opens the report in ATP.
* TG2: ON by default for every user who connected Telegram (the chat `rhythm.deliver.destination` finds as his own);
  he turns it off in Settings > Alerts ("Daily market report on Telegram", prefs namespace `daily_report`) or with the
  Stop button under the message (a signed link: a page with one button, so a link preview stops nothing). A report goes
  to a user ONCE: `daily_report_sends` is unique per (report, user).
* TG3: through the existing bot (the organisation's Telegram channel; the platform bot's token filled in by
  `channels.decrypt_config`), queued and paced (DAILY_REPORT_TELEGRAM_PACE_MS between users, a 429's retry_after obeyed),
  a failed send tried again up to DAILY_REPORT_TELEGRAM_ATTEMPTS times; a chat that blocked the bot is marked and
  skipped. The admin card shows the sent / failed counts.
* TG4: behind `shared_daily_report` - nothing is queued or sent for an organisation while the flag is off for it.
Information only: no order button, nothing here places, blocks or changes an order. The bot token is never logged.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import html
import json
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import config
from app.daily_report import FLAG, PAGE_FLAG
from app.db.models import DailyReportSendRecord, SharedDailyReportRecord, User, UserPrefsRecord

logger = logging.getLogger(__name__)

PREFS = "daily_report"
MAX_CHARS = 4096                      # Telegram's message limit
TELEGRAM_API = "https://api.telegram.org"
TIMEOUT = 30.0
BLOCKED_HINTS = ("blocked", "deactivated", "chat not found", "kicked", "user is deactivated")
RETRY_STATES = ("queued", "failed")
Sleep = Callable[[float], Awaitable[None]]


# --- the Stop link (no sign-in: the signed token is the authority) ------------------------------------------------------
def _key() -> bytes:
    return hashlib.sha256(b"atp-daily-report-stop|" + (config.JWT_SECRET_KEY or "atp").encode()).digest()


def stop_token(tenant_id: int, user_id: int) -> str:
    payload = base64.urlsafe_b64encode(f"{tenant_id}:{user_id}".encode()).decode().rstrip("=")
    return f"{payload}.{hmac.new(_key(), payload.encode(), hashlib.sha256).hexdigest()[:32]}"


def read_stop_token(token: str) -> tuple[int, int] | None:
    try:
        payload, mac = token.split(".", 1)
        if not hmac.compare_digest(hmac.new(_key(), payload.encode(), hashlib.sha256).hexdigest()[:32], mac):
            return None
        tenant, user = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)).decode().split(":", 1)
        return int(tenant), int(user)
    except (ValueError, UnicodeDecodeError):
        return None


# --- his choice ----------------------------------------------------------------------------------------------------------
async def wants(session: AsyncSession, tenant_id: int, user_id: int) -> bool:
    from app.me.prefs import stored_value
    raw = await session.scalar(select(UserPrefsRecord.value_json).where(UserPrefsRecord.tenant_id == tenant_id, UserPrefsRecord.user_id == user_id,
                                                                      UserPrefsRecord.namespace == PREFS))
    return bool(stored_value(PREFS, raw).get("telegram"))


async def set_wants(session: AsyncSession, tenant_id: int, user_id: int, on: bool) -> None:
    row = await session.scalar(select(UserPrefsRecord).where(UserPrefsRecord.tenant_id == tenant_id, UserPrefsRecord.user_id == user_id,
                                                             UserPrefsRecord.namespace == PREFS))
    value, now = json.dumps({"telegram": bool(on)}), datetime.now(UTC)
    if row is None:
        session.add(UserPrefsRecord(tenant_id=tenant_id, user_id=user_id, namespace=PREFS, value_json=value, version=1, updated_at=now))
    else:
        row.value_json, row.version, row.updated_at = value, row.version + 1, now
    await session.commit()


async def flag_on(session: AsyncSession, tenant_id: int, memo: dict[int, bool] | None = None) -> bool:
    from app.platform.controls import flag_enabled
    memo = memo if memo is not None else {}
    if tenant_id not in memo:
        memo[tenant_id] = await flag_enabled(session, PAGE_FLAG, tenant_id) and await flag_enabled(session, FLAG, tenant_id)
    return memo[tenant_id]


async def own_chat(session: AsyncSession, tenant_id: int, user_id: int) -> tuple[Any, str] | None:
    """(the Telegram channel config, his own chat id) or None (not connected, or the chat is another member's)."""
    from app.alerts.channels import decrypt_config
    from app.rhythm.build import Me
    from app.rhythm.deliver import destination
    target = await destination(session, Me(id=user_id, tenant_id=tenant_id))
    if target is None:
        return None
    channel, chat = target
    return decrypt_config(channel), chat


# --- the message -------------------------------------------------------------------------------------------------------
def message_html(report: dict[str, Any]) -> str:
    """The short summary (TG1), always inside Telegram's limit."""
    sections = report.get("sections") or {}
    idx = next((r for r in report.get("index") or [] if not r.get("missing")), None)
    head = ["<b>Daily market report</b>", f"<i>{html.escape(str(report.get('info_line') or ''))}</i>", ""]
    if idx is not None and idx.get("close") is not None:
        chg = idx.get("change_pct")
        head.append(f"<b>{html.escape(str(idx['symbol']))}</b> {idx.get('close'):,.2f}" + (f" ({chg:+.2f}%)" if chg is not None else ""))
    b = report.get("breadth") or {}
    head.append(f"Advances {b.get('advances', 0)} · Declines {b.get('declines', 0)}")
    tail = ["", "The full report is attached as a PDF. Turn these off in Settings › Alerts or with Stop below."]
    parts = [(title, str(sections[key])) for key, title in (("index_read", "Index"), ("breadth_read", "Breadth"), ("conclusion", "Conclusion"))
             if sections.get(key)]

    def render(items: list[tuple[str, str]]) -> str:
        body: list[str] = []
        for title, words in items:
            body += ["", f"<b>{title}</b>", html.escape(words)]
        return "\n".join(head + body + tail)
    text = render(parts)
    while len(text) > MAX_CHARS and parts:                # shorten the longest section's words (never cut inside a tag)
        i = max(range(len(parts)), key=lambda k: len(parts[k][1]))
        title, words = parts[i]
        cut = max(0, len(words) - (len(text) - MAX_CHARS) - 1)
        parts[i] = (title, words[:cut].rstrip() + "…") if cut > 0 else (title, "…")
        text = render(parts)
    return text[:MAX_CHARS]


def buttons(base: str | None, tenant_id: int, user_id: int) -> dict[str, Any] | None:
    if not base:
        return None
    return {"inline_keyboard": [[{"text": "Open the report", "url": f"{base}/copilot/read?report=open"}],
                                [{"text": "Stop these reports", "url": f"{base}/api/daily-report/telegram/stop?t={stop_token(tenant_id, user_id)}"}]]}


# --- the queue -----------------------------------------------------------------------------------------------------------
async def queue(session: AsyncSession, report_id: int) -> int:
    """Queue the report for every user it should reach (flag on for his organisation, his choice on, his own Telegram
    chat). Never a second row for the same user: once per report."""
    have = set(await session.scalars(select(DailyReportSendRecord.user_id).where(DailyReportSendRecord.report_id == report_id)))
    users = [(int(u.id), int(u.tenant_id)) for u in await session.scalars(select(User).where(User.is_active.is_(True)).order_by(User.id))]
    memo: dict[int, bool] = {}
    added = 0
    for user_id, tenant_id in users:
        if user_id in have or not await flag_on(session, tenant_id, memo) or not await wants(session, tenant_id, user_id):
            continue
        if await own_chat(session, tenant_id, user_id) is None:
            continue
        session.add(DailyReportSendRecord(report_id=report_id, tenant_id=tenant_id, user_id=user_id, status="queued", attempts=0))
        added += 1
    await session.commit()
    return added


class TelegramRefused(RuntimeError):
    def __init__(self, status: int, description: str, retry_after: float | None = None):
        super().__init__(f"Telegram API {status}: {description}")
        self.status, self.description, self.retry_after = status, description, retry_after

    @property
    def blocked(self) -> bool:
        return self.status in (400, 403) and any(h in self.description.lower() for h in BLOCKED_HINTS)


async def _call(client: httpx.AsyncClient, token: str, method: str, **kwargs: Any) -> dict[str, Any]:
    try:
        response = await client.post(f"{TELEGRAM_API}/bot{token}/{method}", **kwargs)
    except httpx.HTTPError as exc:
        raise TelegramRefused(0, f"transport: {type(exc).__name__}") from None
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    if response.status_code >= 400 or not payload.get("ok", False):
        retry = (payload.get("parameters") or {}).get("retry_after")
        raise TelegramRefused(response.status_code, str(payload.get("description") or "")[:150].replace(token, "[redacted]"),
                              float(retry) if retry is not None else None)
    return payload


async def _call_paced(client: httpx.AsyncClient, token: str, method: str, sleep: Sleep, **kwargs: Any) -> dict[str, Any]:
    """One call; on 429 (Telegram's flood limit) wait the retry_after it names and try once more."""
    try:
        return await _call(client, token, method, **kwargs)
    except TelegramRefused as exc:
        if exc.status != 429:
            raise
        await sleep(min(float(exc.retry_after or 1.0), 60.0))
        return await _call(client, token, method, **kwargs)


async def send_pending(session: AsyncSession, report_id: int, *, client: httpx.AsyncClient | None = None,
                       sleep: Sleep = asyncio.sleep, pdf: bytes | None = None) -> dict[str, int]:
    """Send the queued (and retryable failed) rows of one report, paced. Returns this pass's counts."""
    from app.alerts.deeplink import app_base_url
    from app.daily_report.pdf import report_pdf
    report_row = await session.get(SharedDailyReportRecord, report_id)
    out = {"sent": 0, "failed": 0, "blocked": 0, "skipped": 0}
    if report_row is None or report_row.status != "ok" or not report_row.content_json:
        return out
    report = json.loads(report_row.content_json)
    rows = [(int(r.id), int(r.tenant_id), int(r.user_id)) for r in await session.scalars(
        select(DailyReportSendRecord).where(DailyReportSendRecord.report_id == report_id, DailyReportSendRecord.status.in_(RETRY_STATES),
                                            DailyReportSendRecord.attempts < config.DAILY_REPORT_TELEGRAM_ATTEMPTS)
        .order_by(DailyReportSendRecord.id))]
    if not rows:
        return out
    document = pdf if pdf is not None else await asyncio.to_thread(report_pdf, report)
    name = f"daily-market-report-{report.get('trade_date')}.pdf"
    text, base = message_html(report), app_base_url()
    own = client is None
    client = client or httpx.AsyncClient(timeout=TIMEOUT)
    memo: dict[int, bool] = {}
    pace = config.DAILY_REPORT_TELEGRAM_PACE_MS / 1000.0
    try:
        for i, (row_id, tenant_id, user_id) in enumerate(rows):
            if i:
                await sleep(pace)                                    # paced: inside Telegram's broadcast limit
            row = await session.get(DailyReportSendRecord, row_id)
            if row is None:
                continue
            target = None
            if await flag_on(session, tenant_id, memo) and await wants(session, tenant_id, user_id):
                target = await own_chat(session, tenant_id, user_id)
            if target is None:
                row.status, row.error = "skipped", "turned off, flag off or no Telegram chat"
                out["skipped"] += 1
                await session.commit()
                continue
            cfg, chat = target
            token = str(cfg.bot_token or "")
            row.attempts += 1
            try:
                if not token:
                    raise TelegramRefused(0, "Telegram is not set up on this server")
                markup = buttons(base, tenant_id, user_id)
                await _call_paced(client, token, "sendMessage", sleep, json={
                    "chat_id": chat, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True, **({"reply_markup": markup} if markup else {})})
                await _call_paced(client, token, "sendDocument", sleep, data={"chat_id": chat, "caption": str(report.get("info_line") or "")[:1024]},
                                  files={"document": (name, document, "application/pdf")})
                row.status, row.error, row.sent_at = "sent", None, datetime.now(UTC)
                out["sent"] += 1
            except TelegramRefused as exc:
                row.status = "blocked" if exc.blocked else "failed"
                row.error = str(exc)[:200]
                out["blocked" if exc.blocked else "failed"] += 1
                logger.info("Daily report to user %s not sent: %s", user_id, row.status)
            finally:
                token = ""
            await session.commit()
    finally:
        if own:
            await client.aclose()
    return out


async def deliver(session: AsyncSession, report_id: int, **kwargs: Any) -> dict[str, int]:
    """Queue, then send (the hook after a report is stored, and the worker's retry pass)."""
    await queue(session, report_id)
    return await send_pending(session, report_id, **kwargs)


async def pending_report(session: AsyncSession) -> int | None:
    """The report with rows still to send (queued, or failed under the attempt cap), if any."""
    return await session.scalar(select(DailyReportSendRecord.report_id).where(
        DailyReportSendRecord.status.in_(RETRY_STATES), DailyReportSendRecord.attempts < config.DAILY_REPORT_TELEGRAM_ATTEMPTS)
        .order_by(DailyReportSendRecord.report_id.desc()).limit(1))


async def counts(session: AsyncSession, report_ids: list[int]) -> dict[int, dict[str, int]]:
    out: dict[int, dict[str, int]] = {i: {"queued": 0, "sent": 0, "failed": 0, "blocked": 0, "skipped": 0} for i in report_ids}
    if not report_ids:
        return out
    for rid, status, n in (await session.execute(select(DailyReportSendRecord.report_id, DailyReportSendRecord.status, func.count())
                                                 .where(DailyReportSendRecord.report_id.in_(report_ids))
                                                 .group_by(DailyReportSendRecord.report_id, DailyReportSendRecord.status))).all():
        out[int(rid)][str(status)] = int(n)
    return out
