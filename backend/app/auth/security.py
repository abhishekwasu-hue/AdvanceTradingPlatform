"""Passwords and access tokens.

P0.3 / S14: every access token names its issuer and audience, carries no e-mail (PII belongs in the user row,
not in a bearer that crosses logs and proxies), and its header names the signing key (`kid`), so the secret
can be rotated: `JWT_SECRET_KEY` signs, `JWT_PREVIOUS_SECRET_KEYS` still verify. Tokens minted before P0.3 (no
iss/aud) are accepted while `JWT_ACCEPT_LEGACY` is on - they expire within minutes anyway.
"""
import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import bcrypt
import jwt

from app.core import config
from app.core.config import JWT_ALGORITHM, JWT_EXPIRE_MINUTES

BCRYPT_MAX_BYTES = 72


def key_id(secret: str) -> str:
    """A short, non-reversible name for a signing secret (what `kid` carries)."""
    return hashlib.sha256(secret.encode()).hexdigest()[:12]


def _signing_keys() -> List[str]:
    return [config.JWT_SECRET_KEY, *config.JWT_PREVIOUS_SECRET_KEYS]


def hash_password(password: str) -> str:
    if len(password.encode("utf-8")) > BCRYPT_MAX_BYTES:
        raise ValueError(f"Password must be at most {BCRYPT_MAX_BYTES} bytes")       # bcrypt silently truncates beyond this
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    raw = password.encode("utf-8")
    if len(raw) > BCRYPT_MAX_BYTES:
        return False
    return bcrypt.checkpw(raw, hashed.encode())


def create_access_token(user_id: int, email: Optional[str] = None, session_id: Optional[int] = None) -> str:
    """`email` is accepted for call-site compatibility and deliberately not put in the token."""
    now = datetime.now(timezone.utc)
    payload: Dict[str, Any] = {"sub": str(user_id), "iss": config.JWT_ISSUER, "aud": config.JWT_AUDIENCE, "iat": now,
                               "exp": now + timedelta(minutes=JWT_EXPIRE_MINUTES)}
    if session_id is not None:
        payload["sid"] = session_id
    return jwt.encode(payload, config.JWT_SECRET_KEY, algorithm=JWT_ALGORITHM, headers={"kid": key_id(config.JWT_SECRET_KEY)})


def decode_access_token(token: str) -> Dict[str, Any]:
    """Verifies signature (current or a previous secret, picked by `kid` when present), expiry, issuer and
    audience. Raises jwt.PyJWTError on any failure."""
    keys = _signing_keys()
    try:
        kid = jwt.get_unverified_header(token).get("kid")
    except jwt.PyJWTError:
        kid = None
    ordered = sorted(keys, key=lambda k: 0 if kid and key_id(k) == kid else 1)
    last_error: Optional[jwt.PyJWTError] = None
    for secret in ordered:
        try:
            return jwt.decode(token, secret, algorithms=[JWT_ALGORITHM], issuer=config.JWT_ISSUER, audience=config.JWT_AUDIENCE)
        except jwt.MissingRequiredClaimError as exc:
            if config.JWT_ACCEPT_LEGACY:
                try:
                    return jwt.decode(token, secret, algorithms=[JWT_ALGORITHM])    # pre-P0.3 token: no iss/aud
                except jwt.PyJWTError as legacy_exc:
                    last_error = legacy_exc
                    continue
            last_error = exc
        except jwt.InvalidSignatureError as exc:
            last_error = exc
            continue
    raise last_error or jwt.InvalidTokenError("token could not be verified")
