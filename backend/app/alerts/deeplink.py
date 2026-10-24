"""PW3: where a notification opens in the app, and which push category it belongs to.

A push (and, in PW4, a Telegram message) carries the app path of the exact screen the event is about: the proposal
in the Copilot Watchtower, the trade in Positions, the order in Orders, the option chain for an OI banner, the
notification itself otherwise. Paths only - the service worker opens them on the app's own origin, so a payload can
never send the user to another site.

Categories are the user-facing push preferences ("Proposals", "Trade fills", ...). A device subscription carries the
categories it wants; EMERGENCY notifications (global kill switch, emergency exit) always go through.
"""
from __future__ import annotations

from typing import Dict, Iterable, Optional
from urllib.parse import urlencode

from app.core.enums import NotificationSeverity, NotificationType

PUSH_CATEGORIES: Dict[str, str] = {
    "proposals": "Proposals waiting for your decision",
    "trades": "Entries, exits and rejected orders",
    "alerts": "Screen, OI banner and news alerts",
    "risk": "Risk limits and the daily loss limit",
    "account": "Broker login, connection, security and system problems",
    "reports": "End-of-day and weekly reports",
}

_CATEGORY_OF: Dict[str, str] = {
    NotificationType.AI_PROPOSAL.value: "proposals",
    NotificationType.ENTRY.value: "trades",
    NotificationType.EXIT.value: "trades",
    NotificationType.REJECTION.value: "trades",
    NotificationType.EMERGENCY_EXIT.value: "trades",
    NotificationType.SCREEN_ALERT.value: "alerts",
    NotificationType.OI_BANNER.value: "alerts",
    NotificationType.NEWS_ALERT.value: "alerts",
    NotificationType.RISK_REJECTION.value: "risk",
    NotificationType.DAILY_LOSS_LIMIT.value: "risk",
    NotificationType.SETTLEMENT_WARNING.value: "risk",
    NotificationType.TOKEN_EXPIRED.value: "account",
    NotificationType.BROKER_DISCONNECT.value: "account",
    NotificationType.SECURITY.value: "account",
    NotificationType.SYSTEM_FAILURE.value: "account",
    NotificationType.OI_COLLECTOR.value: "account",
    NotificationType.EOD_SUMMARY.value: "reports",
    NotificationType.THESIS_REPORT.value: "reports",
    NotificationType.MARKETPLACE.value: "reports",
}


def category_of(event_type: str) -> str:
    """Unknown (future) event types count as account/system messages, so they are never silently dropped by a
    preference that predates them unless the user also turned those off."""
    return _CATEGORY_OF.get(event_type, "account")


def push_wanted(categories: Optional[Iterable[str]], event_type: str, severity: str) -> bool:
    """None = every category (the default for a device registered before PW3)."""
    if severity == NotificationSeverity.EMERGENCY.value or categories is None:
        return True
    return category_of(event_type) in set(categories)


def app_path(notification) -> str:
    """The in-app path for a notification (`NotificationRecord`-like: event_type, id, ai_action_id, related_*)."""
    event = notification.event_type
    if getattr(notification, "ai_action_id", None):
        return "/copilot/watchtower?" + urlencode({"action": notification.ai_action_id})
    if event in (NotificationType.OI_BANNER.value, NotificationType.OI_COLLECTOR.value):
        return "/option-chain"
    if getattr(notification, "related_trade_id", None):
        return "/positions?" + urlencode({"trade": notification.related_trade_id})
    if getattr(notification, "related_order_id", None):
        return "/orders?" + urlencode({"order": notification.related_order_id})
    if event == NotificationType.TOKEN_EXPIRED.value or event == NotificationType.BROKER_DISCONNECT.value:
        return "/broker"                                                # Settings redesign B: the Broker page
    if getattr(notification, "id", None):
        return "/notifications?" + urlencode({"n": notification.id})
    return "/notifications"


def app_base_url() -> Optional[str]:
    """PW4: the app's absolute address for links outside the app (Telegram refuses relative URLs): PUBLIC_BASE_URL,
    else an absolute FRONTEND_URL; None when neither is set (the link is then left out, never guessed)."""
    from app.core import config as app_config
    for candidate in (getattr(app_config, "PUBLIC_BASE_URL", ""), getattr(app_config, "FRONTEND_URL", "")):
        base = (candidate or "").strip().rstrip("/")
        if base.startswith(("https://", "http://")):
            return base
    return None


def app_url(notification) -> Optional[str]:
    """The absolute link to the screen a notification is about (for Telegram's "Open in app" button)."""
    base = app_base_url()
    return f"{base}{app_path(notification)}" if base else None


def open_in_app_row(notification) -> Optional[list]:
    """One inline-keyboard row with the "Open in app" link, or None without an absolute app address."""
    url = app_url(notification)
    return [{"text": "Open in app", "url": url}] if url else None
