"""Passwords and access tokens.

P0.3 / S14: every access token names its issuer and audience, carries no e-mail (PII belongs in the user row,
not in a bearer that crosses logs and proxies), and its header names the signing key (`kid`), so the secret
can be rotated: `JWT_SECRET_KEY` signs, `JWT_PREVIOUS_SECRET_KEYS` still verify. Tokens minted before P0.3 (no
iss/aud) are accepted while `JWT_ACCEPT_LEGACY` is on - they expire within minutes anyway.
"""
import asyncio
import hashlib
import logging
import os
import secrets
import time
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


# M73 SL-2: an account made through a sign-in provider has no password anybody chose. Its hash is marked unusable with
# this prefix (the Django convention; erasure already stores "!erased"): it never verifies, the sign-in still spends one
# bcrypt round (no timing difference), and "Forgot password?" sets a real one later (that hash has no prefix).
UNUSABLE_PASSWORD_PREFIX = "!"


def unusable_password_hash() -> str:
    """A bcrypt hash of a random, discarded password, marked unusable (see UNUSABLE_PASSWORD_PREFIX)."""
    return UNUSABLE_PASSWORD_PREFIX + hash_password(secrets.token_urlsafe(32))


def password_usable(hashed: Optional[str]) -> bool:
    """True when the account has a password its owner chose (a sign-in way of its own)."""
    return bool(hashed) and not str(hashed).startswith(UNUSABLE_PASSWORD_PREFIX)


def verify_password(password: str, hashed: str) -> bool:
    # Hashes made before P0.3 were produced by bcrypt truncating at 72 bytes; verifying the same way keeps those
    # accounts logging in. New passwords longer than that are refused at registration/change (hash_password).
    raw = password.encode("utf-8")[:BCRYPT_MAX_BYTES]
    if not password_usable(hashed):
        rest = hashed[len(UNUSABLE_PASSWORD_PREFIX):] if hashed else ""
        try:   # the same bcrypt work as a real check, never a match
            bcrypt.checkpw(raw, rest.encode())
        except ValueError:
            pass   # intentional: "!erased" (no bcrypt hash behind the mark) - still never a match
        return False
    return bcrypt.checkpw(raw, hashed.encode())


CGROUP_CPU_MAX = "/sys/fs/cgroup/cpu.max"


def _cgroup_cpu_quota(path: str = CGROUP_CPU_MAX) -> Optional[int]:
    """The CPUs a container's CPU quota allows (cgroup v2 `cpu.max`: "<quota> <period>", rounded up), or None when there
    is no quota (`max`) or no such file (not Linux, cgroup v1)."""
    try:
        with open(path, encoding="ascii") as handle:
            quota, period = handle.read().split()[:2]
        if quota == "max":
            return None
        return max(1, -(-int(quota) // int(period)))
    except (OSError, ValueError):
        return None


def _cpu_count() -> int:
    try:
        cpus = max(1, len(os.sched_getaffinity(0)))           # the CPUs this process may run on
    except (AttributeError, OSError):
        cpus = max(1, os.cpu_count() or 1)
    quota = _cgroup_cpu_quota()                               # LD-7: and a container's CPU quota (`docker --cpus`)
    return min(cpus, quota) if quota else cpus


def _bcrypt_concurrency() -> int:
    """The CPUs this process may use, never below one - not divided among the API processes (2 CPUs, 2 processes: 2
    each).

    LD-9: the pool is per process, and the processes do not get the logins evenly: Caddy keeps a few keep-alive
    connections to the API, and every request on one goes to the process that accepted it. With the CPUs divided
    among the processes (before: 2 CPUs, 2 processes, 1 thread each) a login waited for its own process's only check
    while the other process's thread and a CPU stood idle; near the box's capacity that queue is what pushed the
    login p95 past its 2 s (CI runners at 268 ms a check). Sized to the CPUs, a free CPU takes the next check whichever
    process it arrived on; the kernel shares the CPUs between the checks and BCRYPT_NICE keeps them behind the other
    requests. The work is the same: one check per login at the unchanged cost. At most API_WORKERS x CPUs checks run
    at once (4 on the KVM 2), each a little slower only while more of them than CPUs overlap."""
    return max(1, _cpu_count())


BCRYPT_CONCURRENCY = _bcrypt_concurrency()
# LD-7: bcrypt's threads run at a lower CPU priority (nice +BCRYPT_NICE, Linux; 0 = as before). A login wave is up to a
# core of bcrypt on a small host; at equal priority it takes CPU from every other request (the 500-user test's dashboard
# p95 was over its limit only during the login ramp). Lower priority only orders the work: each check is the same cost
# and runs whenever a CPU is free.
BCRYPT_NICE_DEFAULT = 5


def _bcrypt_nice(raw: Optional[str]) -> int:
    """BCRYPT_NICE from the environment, 0-19. LD-7 review N8: a value that is not a whole number falls back to the
    default with a warning instead of stopping the API at import."""
    if raw is None:
        return BCRYPT_NICE_DEFAULT
    if not raw.strip():
        return 0
    try:
        value = int(raw.strip())
    except ValueError:
        logging.getLogger(__name__).warning("BCRYPT_NICE=%r is not a whole number - using %d", raw, BCRYPT_NICE_DEFAULT)
        return BCRYPT_NICE_DEFAULT
    return max(0, min(19, value))


BCRYPT_NICE = _bcrypt_nice(os.environ.get("BCRYPT_NICE"))
_bcrypt_executors: Dict[int, Any] = {}


def _lower_this_thread() -> None:
    if not BCRYPT_NICE or not hasattr(os, "setpriority"):
        return
    try:
        import threading
        os.setpriority(os.PRIO_PROCESS, threading.get_native_id(), BCRYPT_NICE)   # this thread only (Linux)
    except (OSError, AttributeError):
        pass                                                                       # not permitted / not Linux: as before


def _bcrypt_executor() -> Any:
    """bcrypt's own threads, BCRYPT_CONCURRENCY of them, one pool per process: more checks at once than CPUs only make
    every one of them slower, and they must not take the shared thread pools that sync routes, file I/O and chart work
    use - a login wave would otherwise queue all of that behind it."""
    pid = os.getpid()
    executor = _bcrypt_executors.get(pid)
    if executor is None:
        from concurrent.futures import ThreadPoolExecutor
        executor = _bcrypt_executors[pid] = ThreadPoolExecutor(max_workers=BCRYPT_CONCURRENCY, thread_name_prefix="bcrypt",
                                                               initializer=_lower_this_thread)
    return executor


def _timed(queued_at: float, fn: Any, *args: Any) -> Any:
    """Runs `fn` on a bcrypt thread, recording how long it waited for the thread and how long it ran (LD-7)."""
    started = time.perf_counter()
    try:
        return fn(*args)
    finally:
        from app.observability.metrics import BCRYPT_QUEUE_WAIT, BCRYPT_SECONDS
        BCRYPT_QUEUE_WAIT.observe(started - queued_at)
        BCRYPT_SECONDS.observe(time.perf_counter() - started)


async def verify_password_async(password: str, hashed: str) -> bool:
    """`verify_password` on one of bcrypt's own threads. bcrypt is slow on purpose (a quarter of a second or more per
    check); run inline in an async route it holds the event loop, so every other request of that API process waits
    behind each login (Z10-d load test: dashboard p99 about 2 s while users logged in). bcrypt releases the GIL while it
    works."""
    return await asyncio.get_running_loop().run_in_executor(_bcrypt_executor(), _timed, time.perf_counter(), verify_password, password, hashed)


async def hash_password_async(password: str) -> str:
    """`hash_password` on bcrypt's own threads (same reason and the same pool as `verify_password_async`)."""
    return await asyncio.get_running_loop().run_in_executor(_bcrypt_executor(), _timed, time.perf_counter(), hash_password, password)


def create_access_token(user_id: int, email: Optional[str] = None, session_id: Optional[int] = None) -> str:
    """`email` is accepted for call-site compatibility and deliberately not put in the token."""
    now = datetime.now(timezone.utc)
    payload: Dict[str, Any] = {"sub": str(user_id), "iss": config.JWT_ISSUER, "aud": config.JWT_AUDIENCE, "iat": now,
                               "exp": now + timedelta(minutes=JWT_EXPIRE_MINUTES)}
    if session_id is not None:
        payload["sid"] = session_id
    return jwt.encode(payload, config.JWT_SECRET_KEY, algorithm=JWT_ALGORITHM, headers={"kid": key_id(config.JWT_SECRET_KEY)})


def create_view_token(user_id: int, view_session_id: int, admin_id: int, expires_at: datetime) -> str:
    """MO3-A1: the access token of a read-only "View as user" session. It reads as the person (`sub`), names the view
    session (`vs`) and the admin behind it (`imp`), says read-only (`ro`), and carries no login session (`sid`), so no
    two-factor step-up can ever pass on it. It expires with the view session."""
    now = datetime.now(timezone.utc)
    payload: Dict[str, Any] = {"sub": str(user_id), "iss": config.JWT_ISSUER, "aud": config.JWT_AUDIENCE, "iat": now,
                               "exp": expires_at, "vs": view_session_id, "imp": admin_id, "ro": 1}
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
