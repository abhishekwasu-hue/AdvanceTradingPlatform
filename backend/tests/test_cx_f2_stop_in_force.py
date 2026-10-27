"""F2 (owner review of CX2 / SX): the chip shows the stop actually in force and a distance consistent with it.

For every trailing kind (TRAIL, LOCK, LOCK_TRAIL, BREAK_EVEN) on a combined ₹ stop of a loss of ₹2,500: before the
trailing starts the stop in force is the user's own (-2,500) and the distance is measured to it; at activation and after
N steps it is the trailed stop. The numbers are checked through `view()` - what the chip renders. And a pass that read
the exit before the user edited it never writes a stop worked out from the old trailing onto the edited exit.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from app.db.models import GroupExitRecord
from app.execution import group_exits as gx
from app.execution import trigger_engine
from tests.test_auth_api import _session_factory, client  # noqa: F401 - the fixture
from tests.test_cx_group_exits import NOW, _arm, _get, _org, _spread


def _row(kind: str, spec: dict) -> GroupExitRecord:
    return GroupExitRecord(measure=gx.MTM_INR, sl_value=2500.0, target_value=None, trail_kind=kind,
                           trail_json=json.dumps(spec), legs_json="[]", sl_side=None, target_side=None, basis_value=None,
                           best_value=None, current_sl=None, locked_at=None, trail_log=None, spot_symbol=None,
                           check_mode=gx.TOUCH, status=gx.ARMED, version=1)


def _read(row: GroupExitRecord, value: float) -> tuple:
    """One engine reading, then what the chip is given (view: the stop in force and the distance to it)."""
    gx.apply_trail(row, value, NOW)
    row.last_value = value
    v = gx.view(row)
    return v["stop_now"], v["distance"]["sl"]


# kind, numbers, [(reading, stop in force, distance to it)] - before activation, at activation, after N steps
CASES = [
    (gx.TRAIL, {"step": 500, "by": 400}, [(300.0, -2500.0, 2800.0), (500.0, -2100.0, 2600.0), (1600.0, -1300.0, 2900.0)]),
    (gx.LOCK, {"after": 1000, "lock": 300}, [(600.0, -2500.0, 3100.0), (1000.0, 300.0, 700.0), (3000.0, 300.0, 2700.0)]),
    (gx.LOCK_TRAIL, {"after": 1000, "lock": 300, "step": 500, "by": 400},
     [(600.0, -2500.0, 3100.0), (1000.0, 300.0, 700.0), (2000.0, 1100.0, 900.0)]),
    (gx.BREAK_EVEN, {"after": 1000}, [(600.0, -2500.0, 3100.0), (1000.0, 0.0, 1000.0), (3000.0, 0.0, 3000.0)]),
]


@pytest.mark.parametrize("kind,spec,points", CASES, ids=[c[0] for c in CASES])
def test_the_stop_in_force_and_its_distance_before_at_and_after_activation(kind, spec, points):
    row = _row(kind, spec)
    seen = [(_read(row, value), (stop, gap)) for value, stop, gap in points]
    for got, want in seen:
        assert got == want
    assert seen[0][0][0] == -2500.0                                              # the user's own stop until it starts
    assert gx.view(row)["stop_moved"] is True


def test_before_activation_the_view_says_the_stop_did_not_move():
    row = _row(gx.LOCK_TRAIL, {"after": 1000, "lock": 300, "step": 500, "by": 400})
    _read(row, 600.0)
    v = gx.view(row)
    assert (v["stop_now"], v["stop_moved"], v["locked"]) == (-2500.0, False, False)


def test_a_pass_that_read_the_exit_before_an_edit_never_writes_the_old_trailings_stop():
    t = _org()
    short, hedge = _spread(t, "CXF2")
    row = _arm(t, [short, hedge], measure=gx.MTM_INR, sl_value=2500.0, trail_kind=gx.TRAIL, trail={"step": 500, "by": 500})

    async def go():
        async with _session_factory() as session:
            stale = await session.get(GroupExitRecord, row.id)                   # the pass reads version 1 (TRAIL)
            async with _session_factory() as other:                              # the user edits it meanwhile
                fresh = await other.get(GroupExitRecord, row.id)
                fresh.trail_kind, fresh.trail_json = gx.LOCK_TRAIL, json.dumps({"after": 1000, "lock": 300, "step": 500, "by": 400})
                fresh.current_sl, fresh.best_value, fresh.version = None, None, 2
                await other.commit()
            before = (None, None, None)
            stale.best_value, stale.current_sl = 1000.0, -1500.0                 # the old TRAIL's stop
            await trigger_engine._save_trail(session, stale, before)
            await session.commit()
    asyncio.run(go())
    stored = _get(GroupExitRecord, row.id)
    assert stored.current_sl is None and stored.best_value is None
    assert gx.stop_now(stored) == -2500.0                                        # the user's own stop stays in force
