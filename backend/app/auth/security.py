"""Passwords and access tokens.

P0.3 / S14: every access token names its issuer and audience, carries no e-mail (PII belongs in the user row,
not in a bearer that crosses logs and proxies), and its header names the signing key (`kid`), so the secret
can be rotated: `JWT_SECRET_KEY` signs, `JWT_PREVIOUS_SECRET_KEYS` still verify. Tokens minted before P0.3 (no
iss/aud) are accepted while `JWT_ACCEPT_LEGACY` is on - they expire within minutes anyway.
"""
import asyncio
import hashlib
import os
import weakref
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
    # Hashes made before P0.3 were produced by bcrypt truncating at 72 bytes; verifying the same way keeps those
    # accounts logging in. New passwords longer than that are refused at registration/change (hash_password).
    raw = password.encode("utf-8")[:BCRYPT_MAX_BYTES]
    return bcrypt.checkpw(raw, hashed.encode())


def _cpu_count() -> int:
    try:
        return max(1, len(os.sched_getaffinity(0)))           # the CPUs this process may use (a container's share)
    except (AttributeError, OSError):
        return max(1, os.cpu_count() or 1)


def _bcrypt_concurrency() -> int:
    """The CPUs this process may use, shared among the API processes of the host (LD-7: with API_WORKERS processes, each
    takes its share, so the host as a whole still runs about one bcrypt check per CPU at a time; never below one)."""
    return max(1, _cpu_count() // max(1, config.API_WORKERS))


BCRYPT_CONCURRENCY = _bcrypt_concurrency()
_bcrypt_limiters: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, Any]" = weakref.WeakKeyDictionary()


def _bcrypt_limiter() -> Any:
    """bcrypt's own thread limit, one per event loop, sized to the CPU count: more checks at once than CPUs only make
    every one of them slower, and they must not take the shared thread pool (40 threads) that sync routes and file I/O
    use - a login wave would otherwise queue every sync endpoint behind it."""
    import anyio
    loop = asyncio.get_running_loop()
    limiter = _bcrypt_limiters.get(loop)
    if limiter is None:
        limiter = _bcrypt_limiters[loop] = anyio.CapacityLimiter(BCRYPT_CONCURRENCY)
    return limiter


async def verify_password_async(password: str, hashed: str) -> bool:
    """`verify_password` on a worker thread. bcrypt is slow on purpose (a quarter of a second or more per check); run
    inline in an async route it holds the event loop, so every other request of that API process waits behind each
    login (Z10-d load test: dashboard p99 about 2 s while users logged in). bcrypt releases the GIL while it works."""
    import anyio.to_thread
    return await anyio.to_thread.run_sync(verify_password, password, hashed, limiter=_bcrypt_limiter())


async def hash_password_async(password: str) -> str:
    """`hash_password` on a worker thread (same reason and the same limiter as `verify_password_async`)."""
    import anyio.to_thread
    return await anyio.to_thread.run_sync(hash_password, password, limiter=_bcrypt_limiter())


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
