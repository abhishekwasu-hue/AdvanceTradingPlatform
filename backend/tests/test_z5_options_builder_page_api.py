"""Z5 / P1-c: what the builder page reads besides the curves - the metrics card's money lines on `evaluate` (net premium,
reward to risk, a labelled margin estimate, the charges), the basket shown before "Place as basket" (hedge first, the
legs as given, nothing placed), and "Save strategy" (flag off by default; tenant-scoped; archive, not delete)."""
import asyncio
from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.db.models import OptionStrategyRecord, User
from app.execution.india_costs import leg_cost
from app.options_builder import metrics
from app.options_builder import model as m
from app.platform import controls
from tests.test_auth_api import _register, _session_factory, client

AS_OF = date(2026, 3, 2)
EXP = AS_OF + timedelta(days=14)
SPOT = 22000.0


def _headers(email):
    return {"Authorization": f"Bearer {_register(email)}"}


def _set_flag(name: str, on: bool):
    async def go():
        async with _session_factory() as session:
            current = await controls._get(session, controls.KEY_FEATURE_FLAGS)
            flags = dict(current.get("flags") or {})
            flags[name] = {"on": on, "tenants": []}
            await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": flags}, None)
            await session.commit()
    asyncio.run(go())


def _leg(direction, kind, strike, iv=0.15, lots=1, lot_size=50):
    leg = {"direction": direction, "option_type": kind, "strike": strike, "lots": lots, "lot_size": lot_size, "expiry": EXP.isoformat(), "iv": iv}
    leg["premium"] = round(m.leg_theoretical({**leg, "premium": 0}, SPOT, AS_OF), 2)
    return leg


CONDOR = [_leg("BUY", "PE", SPOT - 400), _leg("BUY", "CE", SPOT + 400), _leg("SELL", "PE", SPOT - 200), _leg("SELL", "CE", SPOT + 200)]


def _evaluate(h, legs, **kw):
    r = client.post("/api/options-builder/evaluate", json={"legs": legs, "spot": SPOT, "as_of": AS_OF.isoformat(), **kw}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def test_evaluate_carries_the_metrics_cards_money_lines():
    h = _headers("z5-metrics@example.com")
    body = _evaluate(h, CONDOR)
    credit = sum((1 if leg["direction"] == "SELL" else -1) * leg["premium"] * 50 for leg in CONDOR)
    assert body["net_premium"] == pytest.approx(credit, abs=0.01) and credit > 0
    ext = body["extremes"]
    assert body["reward_to_risk"] == pytest.approx(ext["max_profit"] / -ext["max_loss"])
    # a defined-risk credit structure: at least its max loss, labelled an estimate
    assert body["margin"]["value"] == pytest.approx(-ext["max_loss"], abs=0.01)
    assert body["margin"]["estimated"] is True and "max loss" in body["margin"]["basis"] and "broker" in body["margin"]["note"]
    # the charges: the platform's own table, one order per leg, at the brokerage the trader set
    expected = sum(leg_cost(AS_OF, "buy" if leg["direction"] == "BUY" else "sell", leg["premium"], 50, "OPTION", 15.0)["total"] for leg in CONDOR)
    costs = _evaluate(h, CONDOR, brokerage_per_order=15)["costs"]
    assert costs["to_open"]["total"] == pytest.approx(expected, abs=0.01) and costs["orders"] == 4 and costs["brokerage_per_order"] == 15
    assert costs["to_open"]["brokerage"] == pytest.approx(60.0) and costs["round_trip"] > costs["to_open"]["total"]
    assert costs["to_open"]["stt"] > 0 and costs["as_of"] == AS_OF.isoformat()


def test_margin_is_never_a_made_up_number_for_undefined_risk_calendars_or_futures():
    h = _headers("z5-margin@example.com")
    naked = _evaluate(h, [_leg("SELL", "CE", SPOT)])
    assert naked["margin"]["value"] is None and "Undefined risk" in naked["margin"]["basis"] and naked["reward_to_risk"] is None
    long_call = _evaluate(h, [_leg("BUY", "CE", SPOT)])
    assert long_call["margin"]["value"] == pytest.approx(-long_call["net_premium"]) and "premium paid" in long_call["margin"]["basis"]
    assert long_call["reward_to_risk"] is None                                   # unlimited profit has no ratio
    far = {**_leg("BUY", "CE", SPOT), "expiry": (EXP + timedelta(days=28)).isoformat()}
    cal = _evaluate(h, [far, _leg("SELL", "CE", SPOT)])
    assert cal["margin"]["value"] is None and "different dates" in cal["margin"]["basis"] and cal["reward_to_risk"] is None
    fut = {"direction": "BUY", "option_type": "FUT", "strike": SPOT, "premium": SPOT, "lots": 1, "lot_size": 50, "expiry": EXP.isoformat()}
    covered = _evaluate(h, [fut, _leg("SELL", "CE", SPOT + 200)])
    assert covered["margin"]["value"] is None and "futures" in covered["margin"]["basis"]
    assert covered["net_premium"] == pytest.approx(covered["legs"][1]["premium"] * 50, abs=0.01)   # the future is not premium


def test_metrics_unit_rules():
    assert metrics.reward_to_risk(100.0, -50.0) == 2.0
    assert metrics.reward_to_risk(None, -50.0) is None and metrics.reward_to_risk(100.0, None) is None
    assert metrics.reward_to_risk(float("inf"), -50.0) is None and metrics.reward_to_risk(-10.0, -50.0) is None
    assert metrics.reward_to_risk(100.0, 0.0) is None
    assert metrics.margin_estimate(CONDOR, {"unbounded_loss": False, "max_loss": float("-inf")})["value"] is None


def test_the_basket_is_hedge_first_and_matches_the_legs():
    h = _headers("z5-basket@example.com")
    legs = [CONDOR[2], CONDOR[0], CONDOR[3], {**CONDOR[1], "lots": 2}]           # shorts typed first on purpose
    r = client.post("/api/options-builder/basket", json={"underlying": "NIFTY", "legs": legs}, headers=h)
    assert r.status_code == 200, r.text
    out = r.json()
    assert [o["side"] for o in out["orders"]] == ["BUY", "BUY", "SELL", "SELL"]
    assert [o["leg"] for o in out["orders"]] == [1, 3, 0, 2] and [o["seq"] for o in out["orders"]] == [1, 2, 3, 4]
    for o in out["orders"]:
        leg = legs[o["leg"]]
        assert (o["option_type"], o["strike"], o["expiry"], o["lots"], o["price"]) == (leg["option_type"], leg["strike"], leg["expiry"], leg["lots"], leg["premium"])
        assert o["quantity"] == leg["lots"] * leg["lot_size"]
        assert o["key"] == f"NIFTY|{leg['expiry']}|{leg['strike']:.10g}|{leg['option_type']}"
    assert out["placeable"] is False and "PAPER" in out["why_not"]
    assert out["net_premium"] == pytest.approx(metrics.net_premium(legs), abs=0.01)
    bad = client.post("/api/options-builder/basket", json={"underlying": "NIFTY 50; DROP", "legs": legs}, headers=h)
    assert bad.status_code == 422
    fut = {"direction": "BUY", "option_type": "FUT", "strike": SPOT, "premium": SPOT, "lots": 1, "lot_size": 50, "expiry": EXP.isoformat()}
    order = client.post("/api/options-builder/basket", json={"underlying": "NIFTY", "legs": [fut]}, headers=h).json()["orders"][0]
    assert order["strike"] is None and order["key"] == f"NIFTY|{EXP.isoformat()}|FUT"


def test_the_new_flag_is_off_by_default():
    assert "options_builder_saved" in controls.FEATURE_FLAGS and "options_builder_saved" in controls.DEFAULT_OFF_FLAGS


SAVE = {"name": "Weekly condor", "underlying": "NIFTY", "inputs": {"spot": SPOT, "step": 50, "lot_size": 50, "base_iv": 15, "near_expiry": EXP.isoformat()},
        "legs": [{**leg, "premium_source": "model"} for leg in CONDOR]}


def test_saving_is_404_while_its_flag_is_off():
    h = _headers("z5-save-off@example.com")
    assert client.get("/api/options-builder/strategies", headers=h).status_code == 404
    assert client.post("/api/options-builder/strategies", json=SAVE, headers=h).status_code == 404


def _move_into_tenant(email: str, tenant_id: int, role: str = "USER") -> None:
    async def go():
        async with _session_factory() as session:
            user = await session.scalar(select(User).where(User.email == email))
            user.tenant_id, user.role = tenant_id, role
            await session.commit()
    asyncio.run(go())


def _tenant_of(email: str) -> int:
    async def go():
        async with _session_factory() as session:
            return (await session.scalar(select(User).where(User.email == email))).tenant_id
    return asyncio.run(go())


def test_save_list_load_and_archive_within_the_tenant():
    _set_flag("options_builder_saved", True)
    try:
        owner = _headers("z5-save-owner@example.com")
        mate_token = _register("z5-save-mate@example.com")
        _move_into_tenant("z5-save-mate@example.com", _tenant_of("z5-save-owner@example.com"))
        mate = {"Authorization": f"Bearer {mate_token}"}
        other = _headers("z5-save-other@example.com")

        r = client.post("/api/options-builder/strategies", json=SAVE, headers=owner)
        assert r.status_code == 201, r.text
        sid = r.json()["id"]
        assert r.json()["legs"] == 4 and r.json()["name"] == "Weekly condor"
        assert [s["id"] for s in client.get("/api/options-builder/strategies", headers=mate).json()] == [sid]   # shared in the tenant
        assert client.get("/api/options-builder/strategies", headers=other).json() == []                         # never across tenants
        assert client.get(f"/api/options-builder/strategies/{sid}", headers=other).status_code == 404
        loaded = client.get(f"/api/options-builder/strategies/{sid}", headers=mate).json()
        assert loaded["inputs"]["step"] == 50 and loaded["inputs"]["near_expiry"] == EXP.isoformat()
        assert [(leg["direction"], leg["strike"], leg["premium"], leg["premium_source"]) for leg in loaded["legs"]] == \
               [(leg["direction"], leg["strike"], leg["premium"], "model") for leg in CONDOR]
        assert client.delete(f"/api/options-builder/strategies/{sid}", headers=mate).status_code == 403            # not theirs
        assert client.delete(f"/api/options-builder/strategies/{sid}", headers=other).status_code == 404
        assert client.delete(f"/api/options-builder/strategies/{sid}", headers=owner).json() == {"id": sid, "archived": True}
        assert client.get("/api/options-builder/strategies", headers=owner).json() == []

        async def kept():
            async with _session_factory() as session:
                return (await session.get(OptionStrategyRecord, sid)).archived
        assert asyncio.run(kept()) is True                                                                         # archived, not deleted
    finally:
        _set_flag("options_builder_saved", False)


def test_a_saved_strategy_is_validated():
    _set_flag("options_builder_saved", True)
    try:
        h = _headers("z5-save-bad@example.com")
        post = lambda body: client.post("/api/options-builder/strategies", json=body, headers=h).status_code  # noqa: E731
        assert post({**SAVE, "legs": []}) == 422
        assert post({**SAVE, "name": ""}) == 422
        assert post({**SAVE, "legs": SAVE["legs"] * 4}) == 422                       # 16 legs > 12
        assert post({**SAVE, "extra": 1}) == 422
        assert post({**SAVE, "legs": [{**SAVE["legs"][0], "strike": -1}]}) == 422
        assert post({**SAVE, "inputs": {"spot": -5}}) == 422
        assert post({**SAVE, "inputs": {"lot_size": 0}}) == 422
    finally:
        _set_flag("options_builder_saved", False)


def test_a_switched_off_builder_switches_off_saving_too():
    _set_flag("options_builder_saved", True)
    _set_flag("options_builder", False)
    try:
        h = _headers("z5-save-kill@example.com")
        assert client.get("/api/options-builder/strategies", headers=h).status_code == 503
    finally:
        _set_flag("options_builder", True)
        _set_flag("options_builder_saved", False)
