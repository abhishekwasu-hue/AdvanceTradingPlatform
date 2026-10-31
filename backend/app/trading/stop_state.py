"""Per-trade memory the stop guard keeps between worker cycles.

Kept apart from stop_guard so close_position (position_monitor, which stop_guard imports) can drop a closed trade's
entries without an import cycle - a closed position must not leave counters behind.

M55 S4: the tables survive a restart. Each is an in-process dict (read and written exactly as before) that remembers which
keys changed; `save` writes the changed keys to `runtime_counters` (the database, so a Redis flush cannot reset them
either) at the end of every stop-guard and monitor pass, and `load` reads them back once at worker start. Alert times stay
`time.monotonic()` values in memory and are stored as wall-clock seconds, so a cool-down keeps running across a restart.
A failed save or load is logged and never stops a pass (ADR-0004: nothing here may block an exit).
"""
import json
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)


class _Tracked(dict):
    """A dict that remembers the keys set or removed since the last `save`."""

    def __init__(self) -> None:
        super().__init__()
        self.dirty: set = set()

    def __setitem__(self, key, value) -> None:
        super().__setitem__(key, value)
        self.dirty.add(key)

    def __delitem__(self, key) -> None:
        super().__delitem__(key)
        self.dirty.add(key)

    def pop(self, key, *default):
        if key in self:
            self.dirty.add(key)
        return super().pop(key, *default)

    def clear(self) -> None:
        self.dirty.update(self.keys())
        super().clear()


# trade id -> monotonic time of the last CRITICAL "no stop" alert; absent = never alerted
last_failure_alert: dict[int, float] = _Tracked()
# trade id -> monotonic time of the last "stopped re-arming" CRITICAL (its own cooldown: it must not be swallowed by
# the per-rejection alerts that came before it)
gave_up_alert: dict[int, float] = _Tracked()
# trade id -> immediate exits tried (LIVE_EXIT_IF_NO_STOP)
exit_attempts: dict[int, int] = _Tracked()
# trade id -> consecutive stops the guard re-armed that the broker accepted and then REJECTED
rearm_rejects: dict[int, int] = _Tracked()
# trade id -> the stop order id the guard last re-armed (only its rejection counts against the trade)
rearmed_order: dict[int, str] = _Tracked()
# Re-exit guard: trade id -> consecutive checks that found its earlier exit order still working (or unreadable)
exit_waits: dict[int, int] = _Tracked()
# trade id -> monotonic time of the last "exit still working" CRITICAL
exit_wait_alert: dict[int, float] = _Tracked()
# Naked-short guard: leg group id -> monotonic time of the last "wings held" CRITICAL (and its audit row)
wings_held_alert: dict[str, float] = _Tracked()

# name -> (table, holds a monotonic time, keyed by trade id)
_TABLES: dict[str, tuple[Any, bool, bool]] = {
    "last_failure_alert": (last_failure_alert, True, True),
    "gave_up_alert": (gave_up_alert, True, True),
    "exit_attempts": (exit_attempts, False, True),
    "rearm_rejects": (rearm_rejects, False, True),
    "rearmed_order": (rearmed_order, False, True),
    "exit_waits": (exit_waits, False, True),
    "exit_wait_alert": (exit_wait_alert, True, True),
    "wings_held_alert": (wings_held_alert, True, False),
}


def _to_wall(monotonic_value: float) -> float:
    return time.time() - (time.monotonic() - float(monotonic_value))


def _to_monotonic(wall_value: float) -> float:
    return time.monotonic() - (time.time() - float(wall_value))


async def save(session) -> int:
    """Writes every key changed since the last save (set: its row; removed: its row deleted) and commits. Returns rows
    written or deleted. Never raises."""
    from sqlalchemy import delete

    from app.db.models import RuntimeCounterRecord
    changes = [(name, table, is_time, key) for name, (table, is_time, _) in _TABLES.items() for key in list(table.dirty)]
    if not changes:
        return 0
    try:
        for name, table, is_time, key in changes:
            await session.execute(delete(RuntimeCounterRecord).where(RuntimeCounterRecord.name == name,
                                                                     RuntimeCounterRecord.key == str(key)))
            if key in table:
                value = _to_wall(table[key]) if is_time else table[key]
                session.add(RuntimeCounterRecord(name=name, key=str(key), value=json.dumps(value)))
        await session.commit()
    except Exception as exc:  # noqa: BLE001 - kept dirty: written on the next pass; never stops a pass
        logger.warning("Could not save the stop guard's counters: %s", exc)
        try:
            await session.rollback()
        except Exception:  # noqa: BLE001
            pass
        return 0
    for _, table, _, key in changes:
        table.dirty.discard(key)
    return len(changes)


async def load(session) -> int:
    """Worker start: every stored counter of a position still open (and every leg group cool-down) back into memory;
    rows of closed positions are deleted. Returns entries loaded. Never raises."""
    from sqlalchemy import delete, select

    from app.db.models import RuntimeCounterRecord, TradeRecord
    try:
        rows = list(await session.scalars(select(RuntimeCounterRecord)))
        trade_ids = {int(r.key) for r in rows if _TABLES.get(r.name, (None, False, False))[2] and r.key.isdigit()}
        open_ids = set(await session.scalars(select(TradeRecord.id).where(TradeRecord.id.in_(trade_ids),
                                                                          TradeRecord.exit_time.is_(None)))) if trade_ids else set()
        loaded, stale = 0, []
        for row in rows:
            spec = _TABLES.get(row.name)
            if spec is None:
                continue
            table, is_time, by_trade = spec
            if by_trade and (not row.key.isdigit() or int(row.key) not in open_ids):
                stale.append(row.id)
                continue
            key: Any = int(row.key) if by_trade else row.key
            value = json.loads(row.value)
            dict.__setitem__(table, key, _to_monotonic(value) if is_time else value)   # loaded, not changed
            loaded += 1
        if stale:
            await session.execute(delete(RuntimeCounterRecord).where(RuntimeCounterRecord.id.in_(stale)))
        await session.commit()
        return loaded
    except Exception as exc:  # noqa: BLE001 - the worker starts with fresh counters, as before
        logger.warning("Could not load the stop guard's counters: %s", exc)
        try:
            await session.rollback()
        except Exception:  # noqa: BLE001
            pass
        return 0


def forget(trade_id: int) -> None:
    """The position is closed: nothing more to count or alert about."""
    for table in (last_failure_alert, gave_up_alert, exit_attempts, rearm_rejects, rearmed_order, exit_waits, exit_wait_alert):
        table.pop(trade_id, None)
