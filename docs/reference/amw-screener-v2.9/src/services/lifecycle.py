"""Signal lifecycle maintenance driven by NEW completed-bar evidence from a scan.

For each open stored signal of a scanned ticker/timeframe:
  * close beyond the stored stop/invalidation  -> invalidated
  * high/low reached the stored target          -> completed
  * expiry bars elapsed (counted in completed bars since signal bar) -> expired
  * awaiting_confirmation and the same setup is now qualified          -> qualified
Rows from the scan carry the latest close for the timeframe through `data_time`/`close` fields.
"""
from __future__ import annotations

import logging
import re

from ..storage.db import Store

log = logging.getLogger(__name__)


def update_lifecycle(store: Store, scan_rows: list[dict], latest: dict | None = None, user_id: int | None = None) -> list[tuple[str, str]]:
    """latest: {(ticker, tf): {"close", "high", "low", "times": [last completed bar times]}}; derived from rows if None."""
    changes: list[tuple[str, str]] = []
    qualified_now = {(r["ticker"], r["strategy"], r["setup"], r["timeframe"], r["direction"]) for r in scan_rows if r["status"] == "qualified"}
    latest = latest or {}
    for s in store.open_signals(user_id):          # only the scan owner's signals
        info = latest.get((s["ticker"], s["timeframe"]))
        try:
            if s["state"] == "awaiting_confirmation" and (s["ticker"], s["strategy"], s["setup"], s["timeframe"], s["direction"]) in qualified_now:
                if store.transition(s["key"], "qualified", "mandatory conditions now met", user_id):          # False = already applied (idempotent)
                    changes.append((s["key"], "qualified"))
                continue
            if not info:
                continue
            long = s["direction"] == "bullish"
            if s["stop"] is not None and ((long and info["close"] < s["stop"]) or (not long and info["close"] > s["stop"])):
                why = f"close {info['close']:.2f} beyond stop {s['stop']:.2f}"
                if store.transition(s["key"], "invalidated", why, user_id):          # False = already applied (idempotent)
                    changes.append((s["key"], "invalidated"))
                continue
            if s["target"] is not None and s["state"] in ("qualified", "active") and \
                    ((long and info["high"] >= s["target"]) or (not long and info["low"] <= s["target"])):
                if store.transition(s["key"], "completed", f"target {s['target']:.2f} reached", user_id):          # False = already applied (idempotent)
                    changes.append((s["key"], "completed"))
                continue
            m = re.match(r"(\d+) completed", str((s.get("payload") or {}).get("expiry", "")))
            times = info.get("times", [])
            n = (len(times) - 1 - times.index(s["signal_time"])) if s["signal_time"] in times else None
            if m and n is not None and n > int(m.group(1)):
                if store.transition(s["key"], "expired", f"{n} bars elapsed > {m.group(1)}", user_id):          # False = already applied (idempotent)
                    changes.append((s["key"], "expired"))
        except ValueError as e:
            log.warning("lifecycle %s: %s", s["key"], e)
    return changes
