"""Per-trade memory the stop guard keeps between worker cycles (worker process only; a restart starts it again).

Kept apart from stop_guard so close_position (position_monitor, which stop_guard imports) can drop a closed trade's
entries without an import cycle - a closed position must not leave counters behind.
"""
from typing import Dict

# trade id -> monotonic time of the last CRITICAL "no stop" alert; absent = never alerted
last_failure_alert: Dict[int, float] = {}
# trade id -> monotonic time of the last "stopped re-arming" CRITICAL (its own cooldown: it must not be swallowed by
# the per-rejection alerts that came before it)
gave_up_alert: Dict[int, float] = {}
# trade id -> immediate exits tried (LIVE_EXIT_IF_NO_STOP)
exit_attempts: Dict[int, int] = {}
# trade id -> consecutive stops the guard re-armed that the broker accepted and then REJECTED
rearm_rejects: Dict[int, int] = {}
# trade id -> the stop order id the guard last re-armed (only its rejection counts against the trade)
rearmed_order: Dict[int, str] = {}


def forget(trade_id: int) -> None:
    """The position is closed: nothing more to count or alert about."""
    for table in (last_failure_alert, gave_up_alert, exit_attempts, rearm_rejects, rearmed_order):
        table.pop(trade_id, None)
