"""C12: the scrubber for what a "Report a problem" (and a "Send feedback") message carries into the support inbox.

The inbox must never hold a secret, a token, somebody else's e-mail address or what the user typed into a form. The
context the app attaches is a closed list of labelled fields (the screen path, the times, the app version, the last
error reference); the server refuses any other field. What the user typed is free text, so it goes through `scrub`,
which replaces, in this order:

* web addresses: the query string and fragment are cut off (reset, invite and verification links carry tokens there);
* JSON Web Tokens and `Bearer ...` values;
* `password: ...`, `token=...`, `api key is ...`, `otp: ...` and similar "label: value" pairs - the label is kept, the
  value removed ("my password reset link failed" is left alone: only `:`, `=` or "is" make a value);
* long opaque strings (24 characters or more with letters and digits mixed, the shape of keys and session ids);
* e-mail addresses other than the reporter's own;
* long digit runs (10 digits or more, spaces and dashes allowed between groups: card, account, Aadhaar and phone
  numbers) and PAN-shaped ids.

It is deliberately greedy: a reference such as an order number may be cut too, and support asks for it again. It never
raises; non-text reads as "".
"""
from __future__ import annotations

import re
from typing import Optional

REMOVED_TOKEN = "[removed: key or token]"
REMOVED_EMAIL = "[removed: e-mail]"
REMOVED_NUMBER = "[removed: number]"
REMOVED_VALUE = "[removed]"

_URL = re.compile(r"\b(https?://[^\s?#<>\"']+)[?#][^\s<>\"']*", re.I)
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\b")
_BEARER = re.compile(r"\b(bearer)\s+[A-Za-z0-9._~+/=-]{8,}", re.I)
_LABELLED = re.compile(
    r"\b(pass(?:word|wd|code)?|pwd|m?pin|otp|totp|2fa code|verification code|backup code|token|access[ _-]?token|"
    r"refresh[ _-]?token|api[ _-]?key|api[ _-]?secret|secret(?:[ _-]?key)?|client[ _-]?secret|private[ _-]?key|session[ _-]?id|cookie)"
    r"(\s*[=:]\s*|\s+is\s+)(\"[^\"]*\"|'[^']*'|\S+)",
    re.I,
)
_OPAQUE = re.compile(r"(?<![A-Za-z0-9_-])(?=[A-Za-z0-9_-]{24,}(?![A-Za-z0-9_-]))(?=[A-Za-z0-9_-]*[0-9])(?=[A-Za-z0-9_-]*[A-Za-z])[A-Za-z0-9_-]+")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_DIGITS = re.compile(r"(?<![\d])\+?\d(?:[ -]?\d){9,}(?![\d])")
_PAN = re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b")


def _labelled(match: "re.Match[str]") -> str:
    value = match.group(3)
    if value in (REMOVED_TOKEN, REMOVED_VALUE):
        return match.group(0)
    return f"{match.group(1)}{match.group(2)}{REMOVED_VALUE}"


def scrub(text: object, *, own_email: Optional[str] = None) -> str:
    """The text with every secret-shaped part replaced (see the module docstring). `own_email` is kept as written."""
    if not isinstance(text, str):
        return ""
    out = _URL.sub(lambda m: m.group(1), text)
    out = _JWT.sub(REMOVED_TOKEN, out)
    out = _BEARER.sub(lambda m: f"{m.group(1)} {REMOVED_TOKEN}", out)
    out = _LABELLED.sub(_labelled, out)
    own = (own_email or "").strip().lower()
    out = _EMAIL.sub(lambda m: m.group(0) if own and m.group(0).lower() == own else REMOVED_EMAIL, out)
    out = _OPAQUE.sub(REMOVED_TOKEN, out)
    out = _DIGITS.sub(REMOVED_NUMBER, out)
    out = _PAN.sub(REMOVED_NUMBER, out)
    return out


__all__ = ["REMOVED_EMAIL", "REMOVED_NUMBER", "REMOVED_TOKEN", "REMOVED_VALUE", "scrub"]
