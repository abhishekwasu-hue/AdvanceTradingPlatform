"""M86 D4 (owner decision): whose broker session fetched a live / intraday value - the ONE scope its caches are keyed by.

No live or intraday market data fetched with one user's broker session (the owner's and an admin's included) is ever
served to another user. A user's live / intraday data comes only from his own connected broker; EOD bhavcopy rows are
shared with everyone (they never pass through here).

The model is per broker CREDENTIAL, i.e. per user: a credential row is linked by one person (`broker_credentials.user_id`,
NOT NULL) and only he logs in to it, so "the session that fetched it" and "the user who fetched it" are the same thing.
The scope string is the bring-your-own-broker session namespace (`fanin.SessionRef.namespace`, `u<user>.c<credential>`),
so a user's own caches are one set whichever path wrote them.

- `holder_scope(adapter)`: the scope of a broker adapter built from a credential row (`token_lifecycle.build_adapter`
  stamps `atp_credential_id` / `atp_user_id` on it; the budget / rate wrappers pass both through). None for an adapter
  that carries no credential (a test double, an un-saved sign-in probe): such an adapter's values are never cached
  where another adapter could read them.
- `record_scope(record)`: the same scope from the credential row itself.
- `scoped_key(scope, *parts)`: a cache key that cannot be built without a scope (raises `UnscopedFeed`).

Nothing here reads a broker, places, changes or blocks an order.
"""
from __future__ import annotations

import uuid
from typing import Any

WRAPPED = ("inner", "_inner", "_adapter", "adapter")   # attributes the wrappers (rate budget, API budget, contract symbols) keep
MAX_DEPTH = 6


class UnscopedFeed(RuntimeError):
    """A cache key for live / intraday broker data was asked for without the fetching session's scope."""


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def _attr(obj: Any, name: str) -> Any:
    try:
        return vars(obj).get(name) if hasattr(obj, "__dict__") else None
    except TypeError:
        return None


def holder_ids(adapter: Any) -> tuple[int | None, int | None]:
    """(credential id, user id) of the session an adapter reads through, looking through its wrappers."""
    credential = user = None
    seen = 0
    node = adapter
    while node is not None and seen < MAX_DEPTH:
        seen += 1
        for name in ("atp_credential_id", "credential_id", "_record_id"):
            credential = credential or _int(_attr(node, name))
        user = user or _int(_attr(node, "atp_user_id"))
        node = next((_attr(node, w) for w in WRAPPED if _attr(node, w) is not None), None)
    return credential, user


def scope_of(credential_id: int | None, user_id: int | None) -> str | None:
    if credential_id is None:
        return None
    return f"u{int(user_id)}.c{int(credential_id)}" if user_id is not None else f"c{int(credential_id)}"


def holder_scope(adapter: Any) -> str | None:
    """`u<user>.c<credential>` of the session `adapter` reads through, or None when it carries no credential."""
    return scope_of(*holder_ids(adapter))


def record_scope(record: Any) -> str | None:
    """The same scope from a broker credential row (None for a row without an id)."""
    return scope_of(_int(getattr(record, "id", None)), _int(getattr(record, "user_id", None)))


def fetch_scope(adapter: Any, fallback: str | None = None) -> str:
    """The scope a cache of what `adapter` fetches is keyed by: its holder's (`holder_scope`), else `fallback` (a
    session namespace the caller already holds), else a scope private to this adapter object (an adapter without a
    credential shares its copies with nobody)."""
    scope = holder_scope(adapter) or fallback
    if scope:
        return scope
    node, seen = adapter, 0                 # the innermost adapter: a wrapper made per request keeps its copies
    while seen < MAX_DEPTH:
        seen += 1
        inner = next((_attr(node, w) for w in WRAPPED if _attr(node, w) is not None), None)
        if inner is None:
            break
        node = inner
    own = _attr(node, "atp_feed_anon")
    if not own:
        own = f"x{uuid.uuid4().hex}"
        try:
            node.atp_feed_anon = own
        except (AttributeError, TypeError):
            pass                      # not stampable: a fresh scope per call - nothing is shared, nothing is reused
    return str(own)


def scoped_key(scope: str | None, *parts: Any) -> str:
    """`md:s:<scope>:<parts...>` - refuses to build a key for live / intraday broker data without the fetching scope."""
    if not scope:
        raise UnscopedFeed("live / intraday broker data is cached only under the fetching user's own session")
    return ":".join(["md", "s", str(scope), *(str(p) for p in parts)])


__all__ = ["UnscopedFeed", "fetch_scope", "holder_ids", "holder_scope", "record_scope", "scope_of", "scoped_key"]
