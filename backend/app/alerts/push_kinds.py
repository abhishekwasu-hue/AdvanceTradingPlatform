"""B8: the four kinds of phone notification, where each one opens, and the short text it carries.

The owner's list (readiness order B8): signals, approvals, exits and renew reminders. Each kind maps the existing
notification types (`app/notifications/service.notify` is still the only place a notification is raised) and opens
one screen of the app:

| Kind        | Notifications                                                    | Opens                                   |
|-------------|------------------------------------------------------------------|-----------------------------------------|
| `signals`   | AI_PROPOSAL of an entry (the strategy engine's own signal)       | `/signals?proposal=<id>` (Signals item) |
| `approvals` | every other AI_PROPOSAL (exit now?, reduce, pause, ...)          | `/copilot/watchtower?action=<id>`       |
| `exits`     | EXIT, EMERGENCY_EXIT                                             | `/positions?trade=<id>` (Positions)     |
| `renew`     | TOKEN_EXPIRED, BROKER_DISCONNECT (the pre-open login reminder)   | `/broker` (Broker)                      |

Anything at EMERGENCY severity (the global kill switch, an emergency exit) always goes, whatever the phone's choices,
and opens Positions. Other notification types are not sent to phones (they stay in the app, Telegram and e-mail).

Privacy (owner decision MA-7): an FCM payload is readable by Google's servers, so a phone notification carries only a
fixed sentence per kind - no symbol, price, quantity or P&L, in the text or in the data block - and the path carries
only opaque ids. The details show inside the app. The sentences use structure words only (no buy / sell / target /
stop-loss wording; CLAUDE.md "Signal language").
"""
from __future__ import annotations

import hashlib
from typing import Dict, Iterable, Optional, Tuple
from urllib.parse import urlencode

from app.core.enums import NotificationSeverity, NotificationType

PHONE_KINDS: Dict[str, str] = {
    "signals": "Signals from your own strategies",
    "approvals": "Decisions waiting for you",
    "exits": "Positions closed",
    "renew": "Broker login reminders",
}

# The fixed text of each kind: (title, body). Never anything from the notification itself (MA-7).
PHONE_TEXT: Dict[str, Tuple[str, str]] = {
    "signals": ("A signal is waiting for you", "Your strategy found a setup. Open AiAlgoDesk to look at it - nothing is placed until you decide."),
    "approvals": ("A decision is waiting for you", "Open AiAlgoDesk to review it. Nothing changes until you decide."),
    "exits": ("A position was closed", "Open AiAlgoDesk to see what happened."),
    "renew": ("Broker login needed", "Renew your broker login in AiAlgoDesk to keep live prices and paper trading running."),
}
EMERGENCY_TEXT = ("Important: please open AiAlgoDesk", "Something needs your attention now.")

ACTION_ENTRY = "ENTRY"                                 # app/execution/approval.ACTION_ENTRY (not imported: no cycle)

_EXIT_TYPES = {NotificationType.EXIT.value, NotificationType.EMERGENCY_EXIT.value}
_RENEW_TYPES = {NotificationType.TOKEN_EXPIRED.value, NotificationType.BROKER_DISCONNECT.value}


def device_id(token: str) -> str:
    """The opaque id of a phone: the first 24 hex characters of the token's SHA-256 (never the token itself)."""
    return hashlib.sha256(f"atp-fcm-device|{token.strip()}".encode()).hexdigest()[:24]


def is_emergency(notification) -> bool:
    return getattr(notification, "severity", None) == NotificationSeverity.EMERGENCY.value


def phone_kind(notification, action: Optional[str] = None) -> Optional[str]:
    """The kind of a notification for phones, or None when it is not sent to phones. `action` is the AI action's
    `action` (ENTRY, EXIT_NOW, ...) when the notification names one."""
    event = getattr(notification, "event_type", "")
    if event == NotificationType.AI_PROPOSAL.value:
        return "signals" if action == ACTION_ENTRY else "approvals"
    if event in _EXIT_TYPES:
        return "exits"
    if event in _RENEW_TYPES:
        return "renew"
    if is_emergency(notification):
        return "exits"
    return None


def phone_path(kind: str, notification) -> str:
    """The in-app path the phone opens (a bare path with opaque ids only; frontend/src/native/deepLinks.ts checks it)."""
    action_id = getattr(notification, "ai_action_id", None)
    if kind == "signals":
        return "/signals?" + urlencode({"proposal": action_id}) if action_id else "/signals"
    if kind == "approvals":
        return "/copilot/watchtower?" + urlencode({"action": action_id}) if action_id else "/copilot/watchtower"
    if kind == "exits":
        trade = getattr(notification, "related_trade_id", None)
        return "/positions?" + urlencode({"trade": trade}) if trade else "/positions"
    if kind == "renew":
        return "/broker"
    return "/notifications"


def phone_text(kind: str, notification) -> Tuple[str, str]:
    if is_emergency(notification) and getattr(notification, "event_type", "") not in _EXIT_TYPES:
        return EMERGENCY_TEXT
    return PHONE_TEXT.get(kind, EMERGENCY_TEXT)


def device_wants(kinds: Optional[Iterable[str]], kind: str, notification) -> bool:
    """None = every kind. EMERGENCY always goes."""
    if is_emergency(notification) or kinds is None:
        return True
    return kind in set(kinds)


def addressed_to(device_user_id: int, notification) -> bool:
    """A notification addressed to one member (`user_id`) reaches only that member's phones; an organisation-wide one
    (no `user_id`) reaches every member's phones."""
    target = getattr(notification, "user_id", None)
    return target is None or int(target) == int(device_user_id)


def urgent(notification) -> bool:
    """Sent with sound even inside the organisation's quiet hours."""
    return getattr(notification, "severity", None) in (NotificationSeverity.CRITICAL.value, NotificationSeverity.EMERGENCY.value)


__all__ = ["PHONE_KINDS", "PHONE_TEXT", "device_id", "phone_kind", "phone_path", "phone_text", "device_wants", "addressed_to",
           "urgent", "is_emergency"]
