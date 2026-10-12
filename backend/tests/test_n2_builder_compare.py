"""N2-8 (P1 spec 3.2, docs/design/N2_P1FGH.md): builder compare behind `builder_compare` (off by default).

The comparison is client-side over the existing `POST /api/options-builder/evaluate`, so there is no new route. This
checks:
- the flag is registered off and reaches the page through `/api/system/features`, per organisation;
- the evaluate answer carries every number the compare table shows, on one price grid when the same spot, range and
  points are sent.
"""
import asyncio
import uuid
from datetime import date, timedelta

from app.options_builder import model as m
from app.platform import controls
from tests.test_auth_api import _session_factory, client
from tests.test_trading_worker import _tenant

AS_OF = date(2026, 3, 2)
EXP = AS_OF + timedelta(days=14)
SPOT = 22000.0


def _flag(tenant_id: int, on: bool = True) -> None:
    async def go():
        async with _session_factory() as session:
            current = await controls._get(session, controls.KEY_FEATURE_FLAGS)
            flags = dict(current.get("flags") or {})
            entry = flags.get("builder_compare") or {"on": False, "tenants": []}
            tenants = set(entry.get("tenants") or [])
            (tenants.add if on else tenants.discard)(tenant_id)
            flags["builder_compare"] = {"on": False, "tenants": sorted(tenants)}
            await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": flags}, None)
            await session.commit()
    asyncio.run(go())


def _leg(direction, kind, strike, iv=0.15):
    leg = {"direction": direction, "option_type": kind, "strike": strike, "lots": 1, "lot_size": 50, "expiry": EXP.isoformat(), "iv": iv}
    leg["premium"] = round(m.leg_theoretical({**leg, "premium": 0}, SPOT, AS_OF), 2)
    return leg


def test_flag_registered_off_and_per_organisation():
    assert "builder_compare" in controls.FEATURE_FLAGS and "builder_compare" in controls.DEFAULT_OFF_FLAGS
    t = _tenant(f"n2cmp-{uuid.uuid4().hex[:6]}@example.com")
    other = _tenant(f"n2cmp-o-{uuid.uuid4().hex[:6]}@example.com")
    assert client.get("/api/system/features", headers=t["headers"]).json()["builder_compare"] is False
    _flag(t["tenant_id"])
    assert client.get("/api/system/features", headers=t["headers"]).json()["builder_compare"] is True
    assert client.get("/api/system/features", headers=other["headers"]).json()["builder_compare"] is False


def test_evaluate_gives_the_compare_numbers_on_one_grid():
    t = _tenant(f"n2cmp-e-{uuid.uuid4().hex[:6]}@example.com")
    strategies = {
        "condor": [_leg("BUY", "PE", SPOT - 400), _leg("BUY", "CE", SPOT + 400), _leg("SELL", "PE", SPOT - 200), _leg("SELL", "CE", SPOT + 200)],
        "strangle": [_leg("SELL", "PE", SPOT - 200), _leg("SELL", "CE", SPOT + 200)],
        "bull call": [_leg("BUY", "CE", SPOT), _leg("SELL", "CE", SPOT + 300)],
    }
    grids = []
    for legs in strategies.values():
        r = client.post("/api/options-builder/evaluate", headers=t["headers"],
                        json={"legs": legs, "spot": SPOT, "as_of": AS_OF.isoformat(), "range_pct": 10, "points": 121})
        assert r.status_code == 200, r.text
        body = r.json()
        for key in ("net_premium", "extremes", "breakevens", "margin", "reward_to_risk", "at_expiry", "prices"):
            assert key in body, key
        assert "pop" in body["summary"] and "theta" in body["summary"]["greeks"] and "expected_move" in body["summary"]
        grids.append(body["prices"])
    assert grids[0] == grids[1] == grids[2] and len(grids[0]) == 121
