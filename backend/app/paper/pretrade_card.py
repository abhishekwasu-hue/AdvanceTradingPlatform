"""M77 SX-6 (OPEN_QUESTIONS M77Q-6 default): the check card the ticket shows (M76 MP-2) is signed by the server when it
returns it, so a saved study keeps a card only when it is ATP's own answer to this member - never a card a page made up.

The signature is an HMAC (as app/chart_analyze/share.py) over the member, the contract, the findings exactly as
returned and the time they were checked. Read-only: signing and verifying never hold back, change or place an order.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Mapping, Sequence
from typing import Any

from app.core import config as app_config
from app.db.models import User


def _key() -> bytes:
    return hashlib.sha256(b"atp-pretrade-card|" + (app_config.JWT_SECRET_KEY or "atp").encode()).digest()


def _norm(value: Any) -> Any:
    """The same value after a trip through the browser's JSON: a whole float is an integer there (24500.0 -> 24500)."""
    if isinstance(value, bool) or value is None or isinstance(value, (int, str)):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else value
    if isinstance(value, Mapping):
        return {str(k): _norm(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_norm(v) for v in value]
    return str(value)


def _payload(user_id: int, tenant_id: int, instrument: str, findings: Sequence[Mapping[str, Any]], checked_at: str) -> bytes:
    body = {"u": int(user_id), "t": int(tenant_id), "i": str(instrument), "at": str(checked_at), "f": _norm(list(findings))}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sign(user: User, instrument: str, findings: Sequence[Mapping[str, Any]], checked_at: str) -> str:
    return hmac.new(_key(), _payload(user.id, user.tenant_id, instrument, findings, checked_at), hashlib.sha256).hexdigest()


def verify(user: User, card: Mapping[str, Any]) -> bool:
    """True when `card` ({instrument, findings, checked_at, signature}) is the server's own answer to this member."""
    try:
        want = sign(user, str(card["instrument"]), list(card["findings"]), str(card["checked_at"]))
    except (KeyError, TypeError, ValueError):
        return False
    return hmac.compare_digest(want, str(card.get("signature") or ""))


__all__ = ["sign", "verify"]
