"""Telegram Bot API adapter (official AMW bot or a user-owned bot). Never uses Telegram account credentials/OTPs.

* Official bot: token from the operator's environment (`AMW_TELEGRAM_BOT_TOKEN`). Users link their chat by sending
  `/start <code>` to the bot; AMW verifies the code via `getUpdates` and stores only the chat id.
* User-owned bot: the user's bot token (from @BotFather) is encrypted like AI keys; the user provides the chat id;
  AMW verifies with `getMe` and a test `sendMessage`.
HTTP goes through an injectable transport; tests use a mock. Tokens are redacted from every error.
"""
from __future__ import annotations

import re
from typing import Any, Callable

import requests

from ..ai.redact import redact

API = "https://api.telegram.org"
# transport(method, url, json_body, files, timeout) -> (status, json)
Transport = Callable[[str, str, dict | None, dict | None, float], tuple[int, Any]]
TOKEN_RE = re.compile(r"^\d{5,12}:[A-Za-z0-9_\-]{30,60}$")


def requests_transport(method, url, body, files, timeout):
    if files:
        r = requests.post(url, data=body, files=files, timeout=timeout)
    else:
        r = requests.request(method, url, json=body, timeout=timeout)
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, None


class TelegramError(RuntimeError):
    def __init__(self, code: str, message: str, retryable: bool = False):
        super().__init__(message)
        self.code, self.retryable = code, retryable


def redact_token(text: str, token: str | None) -> str:
    s = redact(str(text), token)
    return re.sub(r"\d{5,12}:[A-Za-z0-9_\-]{30,60}", "[REDACTED]", s)


class TelegramClient:
    def __init__(self, token: str, transport: Transport | None = None, timeout: float = 15):
        if not token or not TOKEN_RE.match(token.strip()):
            raise TelegramError("invalid_token", "Telegram bot token format is invalid (expected '<digits>:<secret>' from @BotFather)")
        self._t = token.strip()
        self.transport = transport or requests_transport
        self.timeout = timeout

    def __repr__(self) -> str:
        return "<TelegramClient token=[REDACTED]>"

    def _call(self, method: str, body: dict | None = None, files: dict | None = None) -> Any:
        url = f"{API}/bot{self._t}/{method}"
        try:
            st, js = self.transport("POST" if body or files else "GET", url, body, files, self.timeout)
        except requests.Timeout as e:
            raise TelegramError("timeout", "Telegram did not respond in time", True) from e
        except requests.RequestException as e:
            raise TelegramError("network", redact_token(f"network error ({type(e).__name__})", self._t), True) from e
        desc = redact_token((js or {}).get("description", f"HTTP {st}") if isinstance(js, dict) else f"HTTP {st}", self._t)
        if st == 401 or st == 404 and "Not Found" in desc:
            raise TelegramError("invalid_token", "Telegram rejected the bot token")
        if st == 403:
            raise TelegramError("blocked", f"The bot cannot message this chat (user blocked the bot or never started it): {desc}")
        if st == 400:
            raise TelegramError("bad_request", f"Telegram rejected the request: {desc}")
        if st == 429:
            raise TelegramError("rate_limited", f"Telegram rate limit: {desc}", True)
        if st >= 500:
            raise TelegramError("unavailable", f"Telegram unavailable: {desc}", True)
        if not isinstance(js, dict) or not js.get("ok"):
            raise TelegramError("unknown", f"Unexpected Telegram response: {desc}")
        return js.get("result")

    def get_me(self) -> dict:
        return self._call("getMe")

    def find_chat_by_code(self, code: str) -> int | None:
        """Look for '/start <code>' (or just the code) in recent updates sent to the bot."""
        for u in self._call("getUpdates", {"timeout": 0, "allowed_updates": ["message"]}) or []:
            m = (u or {}).get("message") or {}
            if code and code in str(m.get("text", "")):
                return (m.get("chat") or {}).get("id")
        return None

    def send_message(self, chat_id: int | str, text: str) -> dict:
        return self._call("sendMessage", {"chat_id": chat_id, "text": text[:4000], "disable_web_page_preview": True})

    def send_document(self, chat_id: int | str, filename: str, content: bytes, caption: str = "") -> dict:
        return self._call("sendDocument", {"chat_id": chat_id, "caption": caption[:1000]}, {"document": (filename, content)})
