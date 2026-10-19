"""Z10-d (ATP_SCREENER_SPEC §1.1, §1.5): the screen result cache - "cached < 200 ms".

A screen's matches depend only on the screen (its AST and parameters), the base timeframe, the universe and the bars.
The key is hash(namespace, AST, params, timeframe, universe, as_of) as the spec words it:

* `namespace` is required: whose data the bars are - (tenant id, user id when the data tier is per user (BT1
  `byob_data_tier`) else None, data source / tier), see `namespace()`. One organisation's (or one user's) result is
  never served to another, even for the same screen and symbols.
* `as_of` given: the symbols and that stamp. Valid ONLY for the shared EOD lake, whose bars for a closed session never
  change; bars read from a broker session must not pass `as_of`.
* `as_of` not given: a fingerprint of each symbol's bars - count, first and last bar time, the columns and a hash of
  every value - so a new bar or any revised value is a different key.

Entries live for a short TTL (spec: live 15-30 s; EOD until the next bar - the caller says which) and the cache is
bounded (least recently used out). It holds results only, never bars, and lives in the process (one per worker).
Matches are returned as copies, so a caller cannot change what the next caller reads.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from collections import OrderedDict
from dataclasses import replace
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from app.screener import nodes
from app.screener.runtime import Match, SymbolData, run_screen
from app.screener.validator import Validated

LIVE_TTL_SECONDS = 30.0
MAX_ENTRIES = 64


def _stamp(value: Any) -> str:
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def namespace(tenant_id: int, *, user_id: Optional[int] = None, per_user: bool = False, source: str) -> Tuple[Any, ...]:
    """The cache namespace of a run: the organisation, the user when the data tier is per user, and the data source /
    tier (e.g. "lake:eod", "broker:upstox")."""
    if per_user and user_id is None:
        raise ValueError("a per-user data tier needs the user id in the cache namespace")
    return (int(tenant_id), int(user_id) if per_user and user_id is not None else None, str(source))


def _fingerprint(data: SymbolData, base_tf: str) -> Tuple[Any, ...]:
    frame = data.frames.get(base_tf)
    if frame is None or frame.empty:
        return (data.symbol, 0)
    index = frame.index
    ends = (int(index.asi8[0]), int(index.asi8[-1])) if isinstance(index, pd.DatetimeIndex) else (_stamp(index[0]), _stamp(index[-1]))
    values = frame.to_numpy()                                  # one float block: a view, not a copy
    if values.dtype.kind == "f":                               # every value, hashed in memory order (no copy when contiguous)
        by_column = bool(values.T.flags.c_contiguous)
        block = values.T if by_column else np.ascontiguousarray(values)
        digest = ("col" if by_column else "row") + hashlib.blake2b(np.ascontiguousarray(block).data, digest_size=16).hexdigest()
    else:
        digest = "repr" + hashlib.blake2b(repr(values.tolist()).encode(), digest_size=16).hexdigest()
    return (data.symbol, len(frame), ends, tuple(frame.columns), digest)


def cache_key(namespace: Tuple[Any, ...], ast: Any, base_tf: str, params: Optional[Dict[str, Any]], universe: List[SymbolData], *,
              as_of: Optional[Any] = None) -> str:
    if not namespace:
        raise ValueError("a result cache key needs a namespace (tenant, user for a per-user tier, data source)")
    from app.screener import ranking
    # N3 S7a review (LOW): a RankScore's result depends on the system's tree too (the run's systems, `ranking.ACTIVE`)
    active = ranking.ACTIVE.get()
    trees = {name: (active[name].tree() if name in active else None) for name in ranking.names_in(ast, params)}
    head = json.dumps({"ns": list(namespace), "ast": nodes.to_json(ast), "version": nodes.VERSION, "tf": base_tf, "params": params or {},
                       **({"rankings": trees} if trees else {})}, sort_keys=True, default=str)
    body: Any = ([d.symbol for d in universe], _stamp(as_of)) if as_of is not None else [_fingerprint(d, base_tf) for d in universe]
    digest = hashlib.sha256(head.encode())
    digest.update(repr(body).encode())
    return digest.hexdigest()


class ResultCache:
    """Bounded, TTL'd memo of `run_screen` results (thread-safe; the computation runs outside the lock)."""

    def __init__(self, max_entries: int = MAX_ENTRIES) -> None:
        self.max_entries = max_entries
        self._entries: "OrderedDict[str, Tuple[float, List[Match]]]" = OrderedDict()
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def get(self, key: str, now: Optional[float] = None) -> Optional[List[Match]]:
        now = time.monotonic() if now is None else now
        with self._lock:
            entry = self._entries.get(key)
            if entry is None or entry[0] <= now:
                if entry is not None:
                    del self._entries[key]
                return None
            self._entries.move_to_end(key)
            return [replace(m) for m in entry[1]]

    def put(self, key: str, matches: List[Match], ttl_seconds: float, now: Optional[float] = None) -> None:
        now = time.monotonic() if now is None else now
        with self._lock:
            self._entries[key] = (now + ttl_seconds, [replace(m) for m in matches])
            self._entries.move_to_end(key)
            while len(self._entries) > self.max_entries:
                self._entries.popitem(last=False)

    def run(self, ast: Any, validated: Validated, universe: List[SymbolData], *, namespace: Tuple[Any, ...], base_tf: str,
            params: Optional[Dict[str, Any]] = None, as_of: Optional[Any] = None, ttl_seconds: float = LIVE_TTL_SECONDS,
            vectorized: bool = False) -> Tuple[List[Match], bool]:
        """(matches, served from the cache). `namespace` is required (see `namespace()`); `as_of` only for the shared
        EOD lake."""
        key = cache_key(namespace, ast, base_tf, params, universe, as_of=as_of)
        cached = self.get(key)
        with self._lock:
            if cached is not None:
                self.hits += 1
            else:
                self.misses += 1
        if cached is not None:
            return cached, True
        matches = run_screen(ast, validated, universe, base_tf=base_tf, params=params, vectorized=vectorized)
        self.put(key, matches, ttl_seconds)
        return matches, False

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


__all__ = ["ResultCache", "cache_key", "namespace", "LIVE_TTL_SECONDS"]
