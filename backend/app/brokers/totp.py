"""The current one-time code for a broker login, from whatever the tenant stored as `totp_secret`.

Brokers that log in with a TOTP (Angel One's `totp`, Shoonya's `factor2`) want the six-digit code of the
moment, while what a person keeps in Settings is usually the base32 secret from the broker's TOTP
enrolment. A stored base32 secret is turned into the current code here; a value that is already a 6- or
8-digit code is sent as-is (a one-off login), and anything else (Shoonya accounts set up with a date of
birth or PAN as the second factor) is passed through unchanged. Spaces are dropped, so a secret copied
in groups of four still works.

The secret never appears in an error: a value that looks like a base32 secret but does not decode raises
a fixed message, with no chained exception carrying it.
"""
from __future__ import annotations

import re
from typing import Optional

from app.brokers.exceptions import BrokerAuthenticationError

_BASE32 = re.compile(r"^[A-Z2-7]{16,}=*$")


def totp_code(secret_or_code: Optional[str]) -> str:
    value = (secret_or_code or "").strip().replace(" ", "")
    if value.isdigit() and len(value) in (6, 8):
        return value
    if _BASE32.match(value.upper()):
        import pyotp
        try:
            return pyotp.TOTP(value.upper()).now()
        except Exception:  # noqa: BLE001 - a malformed secret; the message must not carry it
            raise BrokerAuthenticationError("The stored TOTP secret is not a valid base32 key; paste it again from the broker's TOTP setup") from None
    return value
