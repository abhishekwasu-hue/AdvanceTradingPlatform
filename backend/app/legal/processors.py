"""P3-4-B7 / B22 (DPDP s.8(2), s.11(1)): the register of third parties that process users' personal data.

One list, used twice: `docs/legal/PROCESSORS.md` (the owner's register, with contract status) and the "who received your
data" file in the user's own data export (`GET /api/me/export` -> `processors.json`). The names and purposes here are
the same words as in the register; a test keeps the two in step.

`used_by` says which of the user's own records show they use the service (a connected broker, their own AI key, a linked
Telegram chat, a social sign-in, a push device). It reads only the user's own rows and never a key or a token. A service
the code cannot see per user (the e-mail sender, the hosting, the back-up store) is marked "all accounts".
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    AlertChannelRecord,
    BrokerAccountRecord,
    BrokerCredentialRecord,
    NotificationLinkRecord,
    User,
    UserAiKeyRecord,
    UserIdentityRecord,
)


@dataclass(frozen=True)
class Processor:
    key: str
    name: str
    purpose: str
    data: str          # the kinds of personal data that reach it
    scope: str         # "all accounts" | "if you use it"


PROCESSORS: tuple[Processor, ...] = (
    Processor("hosting", "Hosting provider (server and network)", "Runs the platform and stores its database.",
              "Everything you give or create on the platform", "all accounts"),
    Processor("email", "E-mail sending service (SMTP)", "Sends verification, password-reset and alert e-mails.",
              "Your e-mail address and the text of the e-mail", "all accounts"),
    Processor("backup_store", "Off-site back-up storage", "Keeps encrypted copies of the database for recovery.",
              "An encrypted copy of everything stored about you", "all accounts"),
    Processor("payments", "Payment gateway (Razorpay)", "Takes subscription payments.",
              "Your name, e-mail and the payment (card or UPI details go to the gateway, not to us)", "if you use it"),
    Processor("broker", "Your broker (Upstox, Zerodha, Dhan, Angel One, Fyers, Groww, Shoonya, Kotak Neo, CoinDCX)",
              "Reads your account and sends the orders you set up, with your own credentials.",
              "Your broker credentials and tokens, orders, positions and balances", "if you use it"),
    Processor("ai", "AI providers (Anthropic, OpenAI, Google Gemini, OpenRouter, Groq, Mistral, Z.ai, Cloudflare)",
              "Answers your questions and reviews charts when you ask or switch it on, with the key you pasted.",
              "The text of your question, the chart or data you sent for review, and your own AI key", "if you use it"),
    Processor("telegram", "Telegram", "Delivers alerts and approval messages to your linked chat.",
              "Your Telegram chat id and the text of each message", "if you use it"),
    Processor("push", "Browser and phone push services", "Delivers alerts to your browser or phone.",
              "A push address of your device and the text of each alert", "if you use it"),
    Processor("sms_webhook", "SMS, WhatsApp and webhook gateways you add", "Deliver alerts to a phone number or address you enter.",
              "The phone number or address you enter and the text of each alert", "if you use it"),
    Processor("social_signin", "Google and Facebook sign-in", "Lets you sign in with an account you already have.",
              "Your account id and e-mail at that provider", "if you use it"),
)


async def used_keys(session: AsyncSession, user: User) -> dict[str, str]:
    """Key -> a short note, for the services the user's own rows show. Nothing secret is read."""
    out: dict[str, str] = {}
    brokers = sorted({b for b in await session.scalars(select(BrokerCredentialRecord.broker_name).where(
        BrokerCredentialRecord.user_id == user.id, BrokerCredentialRecord.tenant_id == user.tenant_id))}
        | {b for b in await session.scalars(select(BrokerAccountRecord.broker_name).where(
            BrokerAccountRecord.user_id == user.id, BrokerAccountRecord.tenant_id == user.tenant_id))})
    if brokers:
        out["broker"] = ", ".join(brokers)
    ai = sorted(set(await session.scalars(select(UserAiKeyRecord.provider).where(
        UserAiKeyRecord.user_id == user.id, UserAiKeyRecord.tenant_id == user.tenant_id))))
    if ai:
        out["ai"] = ", ".join(ai)
    if await session.scalar(select(NotificationLinkRecord.id).where(
            NotificationLinkRecord.user_id == user.id, NotificationLinkRecord.tenant_id == user.tenant_id,
            NotificationLinkRecord.status == "linked").limit(1)):
        out["telegram"] = "linked"
    social = sorted(set(await session.scalars(select(UserIdentityRecord.provider).where(UserIdentityRecord.user_id == user.id))))
    if social:
        out["social_signin"] = ", ".join(social)
    # Alert channels the user set up themselves (the organisation's other members' channels are not theirs).
    kinds = set(await session.scalars(select(AlertChannelRecord.channel_type).where(
        AlertChannelRecord.created_by == user.id, AlertChannelRecord.tenant_id == user.tenant_id)))
    if "TELEGRAM" in {k.upper() for k in kinds}:
        out["telegram"] = out.get("telegram") or "an alert channel"
    if {k.upper() for k in kinds} & {"PUSH", "FCM"}:
        out["push"] = "an alert channel"
    if {k.upper() for k in kinds} & {"SMS", "WHATSAPP", "WEBHOOK", "SIGNED_WEBHOOK"}:
        out["sms_webhook"] = ", ".join(sorted(k.lower() for k in kinds if k.upper() in {"SMS", "WHATSAPP", "WEBHOOK", "SIGNED_WEBHOOK"}))
    return out


async def summary(session: AsyncSession, user: User) -> list[dict[str, Any]]:
    """The `processors.json` rows: every third party by name and purpose, and whether this user's records show its use."""
    used = await used_keys(session, user)
    return [{"name": p.name, "purpose": p.purpose, "data_shared": p.data,
             "applies_to": p.scope,
             "you_use_it": True if p.scope == "all accounts" else (p.key in used),
             "detail": used.get(p.key, "")} for p in PROCESSORS]
