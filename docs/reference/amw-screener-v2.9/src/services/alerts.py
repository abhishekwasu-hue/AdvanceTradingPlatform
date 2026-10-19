"""Per-user alert evaluation and delivery.

Tenant rules
* `process_alerts(store, rows, user_id)` evaluates ONLY that user's rules against that user's scan results.
* Messages are rendered from the stored canonical signal record (same data as dashboard and reports).
* Telegram goes to the user's own linked chat (official AMW bot) or their own bot; email goes to the user's own address.
  There is no shared operator chat/address.
Delivery controls: duplicate prevention per (rule, signal, channel) [DB unique], per-rule cooldown, quiet hours
(exchange timezone), per-user hourly rate limit per channel, bounded retries for transient errors, delivery log.
"""
from __future__ import annotations

import logging
import os
import secrets
import smtplib
import time
from datetime import timedelta
from email.message import EmailMessage
from typing import Callable

import pandas as pd

from ..ai.crypto import decrypt, encrypt
from ..notify.telegram import TelegramClient, TelegramError, redact_token
from ..storage.db import Store, utcnow

log = logging.getLogger(__name__)
DEFAULT_NOTIFY = {"telegram": {"mode": None, "chat_id": None, "token_ct": None, "token_kid": None, "token_masked": None,
                               "link_code": None, "verified": False},
                  "email": None, "quiet_hours": None, "max_per_hour": 20, "timezone": "Asia/Kolkata"}
MAX_RETRIES = 2


def notify_settings(store: Store, user_id: int) -> dict:
    s = store.get_pref(user_id, "notify", {}) or {}
    out = {**DEFAULT_NOTIFY, **s}
    out["telegram"] = {**DEFAULT_NOTIFY["telegram"], **(s.get("telegram") or {})}
    return out


def save_notify(store: Store, user_id: int, s: dict) -> None:
    qh = s.get("quiet_hours")
    if qh and (len(qh) != 2 or not all(isinstance(x, str) and len(x) == 5 and x[2] == ":" for x in qh)):
        raise ValueError("quiet_hours must be ['HH:MM', 'HH:MM'] or empty")
    if not isinstance(s.get("max_per_hour"), int) or not 1 <= s["max_per_hour"] <= 500:
        raise ValueError("max_per_hour must be 1..500")
    store.set_pref(user_id, "notify", s)


def public_notify(s: dict) -> dict:
    """Settings safe to show/return: never the bot token or its ciphertext."""
    t = {k: v for k, v in s["telegram"].items() if k not in ("token_ct", "token_kid")}
    return {**s, "telegram": t}


# ---------------------------------------------------------------- Telegram linking
def official_token() -> str | None:
    return os.environ.get("AMW_TELEGRAM_BOT_TOKEN") or None


def start_official_link(store: Store, user_id: int) -> str:
    s = notify_settings(store, user_id)
    code = f"AMW-{secrets.token_hex(4).upper()}"
    s["telegram"] = {**DEFAULT_NOTIFY["telegram"], "mode": "official", "link_code": code}
    save_notify(store, user_id, s)
    return code


def verify_official_link(store: Store, user_id: int, transport=None) -> int:
    tok = official_token()
    if not tok:
        raise TelegramError("not_configured", "The official AMW bot is not configured on this server (AMW_TELEGRAM_BOT_TOKEN).")
    s = notify_settings(store, user_id)
    code = s["telegram"].get("link_code")
    if not code:
        raise TelegramError("no_code", "Start linking first to get a code.")
    chat = TelegramClient(tok, transport).find_chat_by_code(code)
    if chat is None:
        raise TelegramError("not_found", f"No message with code {code} found. Send '/start {code}' to the bot, then verify again.")
    s["telegram"].update({"chat_id": chat, "verified": True, "link_code": None})
    save_notify(store, user_id, s)
    store.audit("telegram_linked", f"user {user_id} official bot chat {chat}", str(user_id))
    return chat


def set_own_bot(store: Store, user_id: int, token: str, chat_id: str, transport=None) -> dict:
    """Verify the user's own bot (getMe + test message) BEFORE storing the encrypted token."""
    cl = TelegramClient(token, transport)
    me = cl.get_me()
    cl.send_message(chat_id, "AMW AI Screener: this chat is now linked for alerts.")
    ct, kid = encrypt(token.strip(), user_id, "telegram")
    s = notify_settings(store, user_id)
    s["telegram"] = {**DEFAULT_NOTIFY["telegram"], "mode": "own", "chat_id": chat_id, "token_ct": ct.hex(), "token_kid": kid,
                     "token_masked": f"…{token.strip()[-4:]}", "verified": True, "bot_username": (me or {}).get("username")}
    save_notify(store, user_id, s)
    store.audit("telegram_linked", f"user {user_id} own bot @{(me or {}).get('username')}", str(user_id))
    return {"bot": (me or {}).get("username"), "chat_id": chat_id}


def unlink_telegram(store: Store, user_id: int) -> None:
    s = notify_settings(store, user_id)
    s["telegram"] = dict(DEFAULT_NOTIFY["telegram"])
    save_notify(store, user_id, s)
    store.audit("telegram_unlinked", f"user {user_id}", str(user_id))


def telegram_client_for(store: Store, user_id: int, transport=None) -> tuple[TelegramClient, str] | None:
    t = notify_settings(store, user_id)["telegram"]
    if not t.get("verified") or not t.get("chat_id"):
        return None
    if t["mode"] == "official":
        tok = official_token()
        return (TelegramClient(tok, transport), t["chat_id"]) if tok else None
    if t["mode"] == "own" and t.get("token_ct"):
        tok = decrypt(bytes.fromhex(t["token_ct"]), user_id, "telegram", t["token_kid"])
        return TelegramClient(tok, transport), t["chat_id"]
    return None


# ---------------------------------------------------------------- senders (per user)
def send_telegram(store: Store, user_id: int, text: str, transport=None, sleeper=time.sleep) -> tuple[str, str | None]:
    try:
        tc = telegram_client_for(store, user_id, transport)
    except Exception as e:  # noqa: BLE001 - e.g. master key changed
        return "failed", f"telegram configuration unusable ({type(e).__name__}); re-link Telegram"
    if tc is None:
        return "skipped", "Telegram not linked for this user"
    cl, chat = tc
    for attempt in range(MAX_RETRIES + 1):
        try:
            cl.send_message(chat, text)
            return "sent", None
        except TelegramError as e:
            if not e.retryable or attempt == MAX_RETRIES:
                return "failed", f"{e.code}: {redact_token(str(e), None)}"
            sleeper(min(5.0, 1.0 * (attempt + 1)))
    return "failed", "unknown"


def send_email(store: Store, user_id: int, text: str, smtp_factory: Callable = smtplib.SMTP) -> tuple[str, str | None]:
    host = os.environ.get("SMTP_HOST")
    to = notify_settings(store, user_id).get("email")
    if not host:
        return "skipped", "Email not configured on this server (SMTP_HOST)"
    if not to:
        return "skipped", "No alert email address set for this user"
    try:
        msg = EmailMessage()
        msg["Subject"], msg["From"], msg["To"] = "AMW AI alert", os.environ.get("SMTP_FROM", os.environ.get("SMTP_USER", "amw@localhost")), to
        msg.set_content(text)
        with smtp_factory(host, int(os.environ.get("SMTP_PORT", "587")), timeout=20) as s:
            if os.environ.get("SMTP_STARTTLS", "1") == "1":
                s.starttls()
            if os.environ.get("SMTP_USER"):
                s.login(os.environ["SMTP_USER"], os.environ.get("SMTP_PASSWORD", ""))
            s.send_message(msg)
        return "sent", None
    except Exception as e:  # noqa: BLE001
        return "failed", type(e).__name__


def default_senders(store: Store, transport=None) -> dict:
    return {"in_app": lambda uid, text: ("sent", None),
            "telegram": lambda uid, text: send_telegram(store, uid, text, transport),
            "email": lambda uid, text: send_email(store, uid, text)}


# ---------------------------------------------------------------- evaluation
def rule_matches(rule: dict, sig: dict) -> bool:
    if sig.get("status") not in ("qualified", "candidate"):
        return False
    if (sig.get("score") or 0) < (rule.get("min_score") or 0):
        return False
    for field, key in (("strategies", "strategy"), ("directions", "direction"), ("timeframes", "timeframe")):
        allowed = rule.get(field) or []
        if allowed and sig.get(key) not in allowed and not (field == "strategies" and str(sig.get(key, "")).split(":")[0] in allowed):
            return False
    syms = rule.get("symbols") or []
    return not syms or sig.get("ticker") in syms or sig.get("symbol") in syms


def format_message(sig: dict) -> str:
    rr = sig.get("rr")
    return (f"AMW AI [{sig['label']}] {sig['symbol']} {sig['direction']} · {sig['setup']} · {sig['timeframe']}\n"
            f"Score {sig['score']} (rule-based, not a probability) · signal bar {sig['signal_time']} · data {sig.get('data_status', 'NA')}\n"
            f"Stop {sig.get('stop')} · Target {sig.get('target')} · R:R {rr if rr is None else round(rr, 2)}\n"
            f"Source {sig.get('provider', 'NA')} ({sig.get('data_kind', 'NA')}) · ref {str(sig.get('key', ''))[:8]}\n"
            "Analysis only — not an order or investment advice.")


def in_quiet_hours(settings: dict, now_utc) -> bool:
    qh = settings.get("quiet_hours")
    if not qh:
        return False
    local = pd.Timestamp(now_utc).tz_localize("UTC").tz_convert(settings.get("timezone") or "Asia/Kolkata").strftime("%H:%M")
    a, b = qh
    return a <= local < b if a <= b else (local >= a or local < b)


def sent_last_hour(store: Store, user_id: int, channel: str, now) -> int:
    return sum(1 for e in store.alert_events(user_id, 500)
               if e["channel"] == channel and e["status"] == "sent" and e["created"] >= now - timedelta(hours=1))


def process_alerts(store: Store, signal_rows: list[dict], user_id: int | None, senders: dict | None = None, now=None) -> list[dict]:
    if user_id is None:
        return []
    senders = senders or default_senders(store)
    now = now or utcnow()
    settings = notify_settings(store, user_id)
    out = []
    for rule in store.alert_rules(user_id, enabled_only=True):
        for sig in signal_rows:
            if not rule_matches(rule, sig):
                continue
            stored = store.get_signal(store.scoped_key(user_id, sig["key"]), user_id)
            canon = {**(stored["payload"] if stored else sig), "key": stored["key"] if stored else sig["key"]}
            last = store.last_alert_time(rule["id"], sig["ticker"])
            cooling = last is not None and now - last < timedelta(minutes=rule.get("cooldown_minutes") or 0)
            for ch in rule.get("channels") or ["in_app"]:
                msg = format_message(canon)
                if cooling:
                    status, err = "suppressed", "cooldown"
                elif ch != "in_app" and in_quiet_hours(settings, now):
                    status, err = "suppressed", "quiet hours"
                elif ch != "in_app" and sent_last_hour(store, user_id, ch, now) >= settings["max_per_hour"]:
                    status, err = "suppressed", f"rate limit ({settings['max_per_hour']}/hour)"
                else:
                    status, err = senders.get(ch, lambda u, t: ("failed", "unknown channel"))(user_id, msg)
                if store.add_alert_event(rule_id=rule["id"], signal_key=canon["key"], ticker=sig["ticker"], message=msg,
                                         channel=ch, status=status, error=err, created=now):
                    out.append({"rule": rule["name"], "ticker": sig["ticker"], "channel": ch, "status": status, "error": err})
    return out
