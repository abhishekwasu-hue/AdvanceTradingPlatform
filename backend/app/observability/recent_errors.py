"""MO3 admin console: the last server errors this API process answered, for the admin Diagnostics drawer.

Only what an operator needs to find the matching log lines: the request id (the same string the response carried in
`X-Request-ID` and the structured log bound), the method, the *route template* (never the query string, a body or a
header), the status and when. In memory and per process (a restart or another worker process starts empty) - the logs
stay the record; this is the shortcut to the right request id. Nothing here is ever served to a non-admin.
"""
from collections import deque
from datetime import datetime, timezone
from threading import Lock
from typing import Deque, Dict, List

MAX_KEPT = 100
_ERRORS: Deque[Dict[str, object]] = deque(maxlen=MAX_KEPT)
_LOCK = Lock()


def record(request_id: str, method: str, route: str, status: int, duration_ms: float) -> None:
    entry = {"request_id": request_id, "method": method, "route": route[:200], "status": int(status),
             "duration_ms": round(duration_ms, 1), "at": datetime.now(timezone.utc).isoformat()}
    with _LOCK:
        _ERRORS.append(entry)


def recent(limit: int = 50) -> List[Dict[str, object]]:
    """Newest first."""
    with _LOCK:
        items = list(_ERRORS)
    return list(reversed(items))[:max(0, limit)]


def clear() -> None:
    with _LOCK:
        _ERRORS.clear()
