"""N2-7 (P1 spec 3.2, docs/design/N2_P1FGH.md): builder adjustments behind `builder_adjustments` (off by default).

Covers:
- each adjustment by hand: roll up / out, add a hedge, a short strangle to an iron condor, close one side;
- the money: model prices, P&L locked in, cash today;
- before and after: the after curve is the legs held plus the locked-in P&L, and the iron condor caps the loss;
- refusals are 422s;
- nothing is placed (no order rows).
"""
import asyncio
from datetime import date, timedelta

import pytest
from sqlalchemy import func, select

from app.db.models import OrderRecord
from app.options_builder import adjust as adj
from app.options_builder import model as m
from app.options_builder.payoff import build_default_price_range, compute_strategy_payoff_curve
from app.platform import controls
from tests.test_auth_api import _register, _session_factory, client

AS_OF = date(2026, 3, 2)
EXP = AS_OF + timedelta(days=14)
LATER = AS_OF + timedelta(days=42)
SPOT = 22000.0
STEP = 50.0


def _leg(direction, kind, strike, iv=0.15, lots=1, lot_size=50, expiry=EXP, premium=None):
    leg = {"direction": direction, "option_type": kind, "strike": float(strike), "lots": lots, "lot_size": lot_size, "expiry": expiry.isoformat(), "iv": iv}
    leg["premium"] = round(m.leg_theoretical({**leg, "premium": 0}, SPOT, AS_OF), 2) if premium is None else premium
    return leg


STRANGLE = [_leg("SELL", "CE", 22300, premium=60.0), _leg("SELL", "PE", 21700, premium=55.0)]


def _price(leg):
    return round(m.leg_theoretical(leg, SPOT, AS_OF), 2)


# --- pure ------------------------------------------------------------------------------------------------------------

def test_roll_up_and_out_closes_and_reopens_at_model_prices():
    out = adj.adjust(STRANGLE, "roll", spot=SPOT, as_of=AS_OF, strike_step=STEP, r=0.065, index=0, steps=2, expiry=LATER.isoformat())
    new = out["after"][0]
    assert new["strike"] == 22400.0 and new["expiry"] == LATER.isoformat() and new["direction"] == "SELL" and new["iv"] == 0.15
    assert new["premium"] == round(m.leg_theoretical(new, SPOT, AS_OF, r=0.065), 2)
    close_price = round(m.leg_theoretical(STRANGLE[0], SPOT, AS_OF, r=0.065), 2)
    assert out["changes"][0] == {"action": "close", "leg": adj._public(STRANGLE[0]), "price": close_price,
                                 "realised": round((close_price - 60.0) * -50, 2)}
    assert out["realised"] == round((close_price - 60.0) * -50, 2)
    assert out["cash"] == pytest.approx(round(-close_price * 50 + new["premium"] * 50, 2), abs=0.01)   # buy back, sell the new one
    assert out["description"] == f"Roll sell CE 22300 2 strikes up and out to {LATER.isoformat()}"
    with pytest.raises(adj.AdjustError):
        adj.adjust(STRANGLE, "roll", spot=SPOT, as_of=AS_OF, strike_step=STEP, r=0.065, index=0)          # nothing to roll to
    with pytest.raises(adj.AdjustError):
        adj.adjust(STRANGLE, "roll", spot=SPOT, as_of=AS_OF, strike_step=STEP, r=0.065, index=5, steps=1)


def test_hedge_condor_and_close_side():
    hedge = adj.adjust(STRANGLE, "add_hedge", spot=SPOT, as_of=AS_OF, strike_step=STEP, r=0.065, index=1, width_steps=3)
    wing = hedge["after"][-1]
    assert (wing["direction"], wing["option_type"], wing["strike"]) == ("BUY", "PE", 21550.0) and hedge["realised"] == 0.0
    assert hedge["cash"] == pytest.approx(-wing["premium"] * 50)
    with pytest.raises(adj.AdjustError, match="sold leg"):
        adj.adjust([_leg("BUY", "CE", 22000)] + STRANGLE, "add_hedge", spot=SPOT, as_of=AS_OF, strike_step=STEP, r=0.065, index=0)

    ic = adj.adjust(STRANGLE, "iron_condor", spot=SPOT, as_of=AS_OF, strike_step=STEP, r=0.065, width_steps=4)
    wings = [(leg["direction"], leg["option_type"], leg["strike"]) for leg in ic["after"][2:]]
    assert wings == [("BUY", "CE", 22500.0), ("BUY", "PE", 21500.0)] and len(ic["changes"]) == 2
    with pytest.raises(adj.AdjustError, match="iron condor"):
        adj.adjust(ic["after"], "iron_condor", spot=SPOT, as_of=AS_OF, strike_step=STEP, r=0.065)        # already has wings

    closed = adj.adjust(STRANGLE, "close_side", spot=SPOT, as_of=AS_OF, strike_step=STEP, r=0.065, side="CE")
    assert [leg["option_type"] for leg in closed["after"]] == ["PE"] and closed["changes"][0]["action"] == "close"
    with pytest.raises(adj.AdjustError, match="whole strategy"):
        adj.adjust([STRANGLE[0]], "close_side", spot=SPOT, as_of=AS_OF, strike_step=STEP, r=0.065, side="CE")
    with pytest.raises(adj.AdjustError, match="FUT"):
        adj.adjust([{**STRANGLE[0], "option_type": "FUT"}], "roll", spot=SPOT, as_of=AS_OF, strike_step=STEP, r=0.065, index=0, steps=1)


def test_before_and_after_views():
    prices = build_default_price_range(SPOT, num_points=201, range_pct=10)
    ic = adj.adjust(STRANGLE, "iron_condor", spot=SPOT, as_of=AS_OF, strike_step=STEP, r=0.065, width_steps=4)
    v = adj.compare(STRANGLE, ic, prices, spot=SPOT, as_of=AS_OF, r=0.065)
    assert v["before"]["extremes"]["unbounded_loss"] is True and v["after"]["extremes"]["unbounded_loss"] is False
    assert v["after"]["extremes"]["max_loss"] is not None and v["after"]["at_expiry"] == [round(x, 2) for x in compute_strategy_payoff_curve(ic["after"], prices)]
    assert v["greeks_change"]["vega"] == pytest.approx(v["after"]["greeks"]["vega"] - v["before"]["greeks"]["vega"], abs=1e-3)
    assert len(v["before"]["breakevens"]) == 2
    # A close locks P&L in: the after curve is the legs held plus it.
    closed = adj.adjust(STRANGLE, "close_side", spot=SPOT, as_of=AS_OF, strike_step=STEP, r=0.065, side="CE")
    vc = adj.compare(STRANGLE, closed, prices, spot=SPOT, as_of=AS_OF, r=0.065)
    held = compute_strategy_payoff_curve(closed["after"], prices)
    assert vc["after"]["at_expiry"] == [round(x + closed["realised"], 2) for x in held]
    assert vc["after"]["extremes"]["max_profit"] == pytest.approx(55.0 * 50 + closed["realised"], abs=0.01)


# --- API ---------------------------------------------------------------------------------------------------------------

def _headers(email):
    return {"Authorization": f"Bearer {_register(email)}"}


def _flag(on: bool):
    async def go():
        async with _session_factory() as session:
            current = await controls._get(session, controls.KEY_FEATURE_FLAGS)
            flags = dict(current.get("flags") or {})
            flags["builder_adjustments"] = {"on": on, "tenants": []}
            await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": flags}, None)
            await session.commit()
    asyncio.run(go())


@pytest.fixture
def flag_on():
    _flag(True)
    yield
    _flag(False)


def _body(**kw):
    return {"legs": STRANGLE, "spot": SPOT, "as_of": AS_OF.isoformat(), "strike_step": STEP, **kw}


def test_flag_off_is_404():
    assert "builder_adjustments" in controls.FEATURE_FLAGS and "builder_adjustments" in controls.DEFAULT_OFF_FLAGS
    r = client.post("/api/options-builder/adjust", json=_body(kind="close_side", side="CE"), headers=_headers("n2a-off@example.com"))
    assert r.status_code == 404 and r.headers.get("X-Feature-Disabled") == "builder_adjustments"


def test_adjust_route_models_and_places_nothing(flag_on):
    h = _headers("n2a-on@example.com")

    async def orders():
        async with _session_factory() as session:
            return await session.scalar(select(func.count(OrderRecord.id)))
    before = asyncio.run(orders())
    r = client.post("/api/options-builder/adjust", json=_body(kind="iron_condor", width_steps=4), headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["placeable"] is False and "nothing is placed" in body["why_not"] and len(body["after_legs"]) == 4
    assert body["after"]["extremes"]["unbounded_loss"] is False and body["before"]["extremes"]["unbounded_loss"] is True
    assert len(body["prices"]) == len(body["before"]["at_expiry"]) == len(body["after"]["at_expiry"])
    assert client.post("/api/options-builder/adjust", json=_body(kind="roll", index=0), headers=h).status_code == 422
    assert client.post("/api/options-builder/adjust", json=_body(kind="close_side"), headers=h).status_code == 422
    assert client.post("/api/options-builder/adjust", json=_body(kind="roll", index=0, steps=1, strike_step=0), headers=h).status_code == 422
    assert client.post("/api/options-builder/adjust", json=_body(kind="roll", index=0, steps=-500), headers=h).status_code == 422
    assert asyncio.run(orders()) == before



def test_lots_must_be_whole_numbers():
    for bad in (1.5, 0, -1, "2", True, None):
        with pytest.raises(adj.AdjustError, match="whole number"):
            adj.adjust([{**STRANGLE[0], "lots": bad}, STRANGLE[1]], "iron_condor", spot=SPOT, as_of=AS_OF, strike_step=STEP, r=0.065)
    ok = adj.adjust([{**STRANGLE[0], "lots": 2.0}, STRANGLE[1]], "iron_condor", spot=SPOT, as_of=AS_OF, strike_step=STEP, r=0.065)
    assert [leg["lots"] for leg in ok["after"]] == [2, 1, 2, 1] and all(isinstance(leg["lots"], int) for leg in ok["after"])
