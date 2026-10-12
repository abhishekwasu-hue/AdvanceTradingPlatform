"""N1 L1: the level-bot confirmation gate, ported from the Trade repo (docs/design/N1_EXECUTION_CORE.md §2, §5).

A stored level is only a candidate AREA (low-high), never a signal. On closed bars only, per (symbol, area):
IDLE -> ARMED (price arrives from the trade side the Daily trend allows) -> CONFIRMED (a dominant commitment candle on
higher volume, after a handover study inside the area, R:R >= min) or DISARMED (real break of the far edge, timeout,
impulsive approach, absorption until timeout, R:R, no target, against the trend).

* `gate`     - the state machine (Trade `bots_common/level_reaction.py`): `evaluate`, `safe_evaluate`, `describe`,
               `context_from_state`, `side_allowed`, `closed_bars`, `session_start_index`.
* `method`   - the decision3 functions the gate calls (Trade `decision3/method.py`): `zone_study`, `zone_pattern`,
               `commitment`, `reversal_form`, `power_shift`, `contraction`, `candle_read`.
* `settings` - the gate's, decision3's and the real-break defaults at the pinned Trade revision.
* The real-break rule is ATP's existing port, `app/price_action/breaks.py` (`break_from`).

Pure: no I/O, no orders, no broker. The Daily trend fold (Trade `decision3.daily.fold`) is N1 L2; until then the trend
context comes in as a state object. Golden parity: tests/test_n1_l1_level_gate.py, goldens made by Trade's own code
(tests/fixtures/level_gate/make_golden.py), drift check tests/fixtures/level_gate/drift_check.py.
"""
