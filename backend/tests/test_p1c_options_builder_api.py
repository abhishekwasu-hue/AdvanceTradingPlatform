"""P1-c: the Options Strategy Builder API - catalog, template, evaluate, suggest. Research only: checked against the
model functions (themselves checked against textbook values and Monte Carlo in P1-b), the flag, auth, and that the
package imports nothing that could place an order."""
import asyncio
import math
import pathlib
import re
from datetime import date, timedelta

from app.brokers.models import OptionChain, OptionChainRow
from app.options_builder import model as m
from app.platform import controls
from tests.test_auth_api import _register, _session_factory, client

AS_OF = date(2026, 3, 2)
EXP = AS_OF + timedelta(days=14)
SPOT = 22000.0


def _headers(email):
    return {"Authorization": f"Bearer {_register(email)}"}


def _flag(on: bool):
    async def go():
        async with _session_factory() as session:
            current = await controls._get(session, controls.KEY_FEATURE_FLAGS)
            flags = dict(current.get("flags") or {})
            flags["options_builder"] = {"on": on, "tenants": []}
            await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": flags}, None)
            await session.commit()
    asyncio.run(go())


def _priced(legs, iv=0.15):
    out = []
    for leg in legs:
        leg = {**leg, "lot_size": 50, "iv": iv}
        leg["premium"] = round(m.leg_theoretical({**leg, "premium": 0}, SPOT, AS_OF), 4)
        out.append(leg)
    return out


def _template(h, name, **kw):
    body = {"name": name, "atm_strike": SPOT, "width": 200, "near_expiry": EXP.isoformat(), **kw}
    return client.post("/api/options-builder/template", json=body, headers=h)


def _evaluate(h, legs, **kw):
    return client.post("/api/options-builder/evaluate", json={"legs": legs, "spot": SPOT, "as_of": AS_OF.isoformat(), **kw}, headers=h)


def test_needs_a_login_and_the_flag():
    assert client.get("/api/options-builder/catalog").status_code == 401
    h = _headers("p1c-flag@example.com")
    assert client.get("/api/options-builder/catalog", headers=h).status_code == 200      # a kill flag: on by default
    _flag(False)
    try:
        assert client.get("/api/options-builder/catalog", headers=h).status_code == 503
    finally:
        _flag(True)


def test_the_catalog_and_a_template_hedge_first():
    h = _headers("p1c-cat@example.com")
    cat = client.get("/api/options-builder/catalog", headers=h).json()
    assert len(cat["templates"]) == 38 and set(cat["families"]) == {"Bullish", "Bearish", "Neutral", "Volatility", "Stock"}
    assert cat["templates"]["Call Calendar"]["two_expiries"] is True and cat["templates"]["Iron Condor"]["two_expiries"] is False
    legs = _template(h, "Iron Condor").json()["legs"]
    assert [leg["direction"] for leg in legs] == ["BUY", "BUY", "SELL", "SELL"]
    assert sorted(leg["strike"] for leg in legs) == [SPOT - 400, SPOT - 200, SPOT + 200, SPOT + 400]
    assert _template(h, "Call Calendar").status_code == 422
    assert _template(h, "No Such").status_code == 404


def test_evaluate_gives_the_models_numbers():
    h = _headers("p1c-eval@example.com")
    legs = _priced(_template(h, "Iron Condor").json()["legs"])
    out = _evaluate(h, legs, points=101, range_pct=6)
    assert out.status_code == 200, out.text
    body = out.json()
    mid = len(body["prices"]) // 2
    assert body["prices"][mid] == SPOT and abs(body["today"][mid]) < 1.0                       # priced by the model: flat today
    assert len(body["at_expiry"]) == len(body["prices"]) == 101
    ext = body["extremes"]
    assert ext["unbounded_loss"] is False and ext["max_loss"] == m.payoff_extremes(legs)["max_loss"]
    assert body["breakevens"] == sorted(x for pair in m.profitable_intervals(legs) for x in pair if 0 < x < math.inf)
    assert body["summary"]["pop"] == m.pop_at_expiry(legs, SPOT, 0.15, 14 / 365) and body["summary"]["method"].startswith("exact")
    net = m.net_greeks(legs, SPOT, AS_OF)
    assert abs(sum(leg["greeks"]["delta"] for leg in body["legs"]) - net["delta"]) < 1e-9
    assert "not a forecast" in body["disclaimer"]


def test_an_unbounded_loss_is_null_with_its_flag_and_a_calendar_has_no_single_payoff():
    h = _headers("p1c-unb@example.com")
    short_call = _priced([{"direction": "SELL", "option_type": "CE", "strike": SPOT, "lots": 1, "expiry": EXP.isoformat()}])
    ext = _evaluate(h, short_call).json()["extremes"]
    assert ext["max_loss"] is None and ext["unbounded_loss"] is True and ext["max_profit"] > 0
    cal = _priced(_template(h, "Call Calendar", next_expiry=(EXP + timedelta(days=28)).isoformat()).json()["legs"])
    body = _evaluate(h, cal, days_forward=14).json()
    assert body["single_expiry"] is False and body["at_expiry"] is None and body["extremes"] is None
    assert body["summary"]["method"].startswith("numerical") and body["on_date"][len(body["prices"]) // 2] > 0


def test_iv_is_solved_from_the_premium_or_refused_and_the_clock_must_be_explicit():
    h = _headers("p1c-iv@example.com")
    legs = _priced([{"direction": "BUY", "option_type": "CE", "strike": SPOT, "lots": 1, "expiry": EXP.isoformat()}])
    no_iv = [{k: v for k, v in legs[0].items() if k != "iv"}]
    body = _evaluate(h, no_iv).json()
    assert body["legs"][0]["iv_source"] == "solved from the premium" and abs(body["legs"][0]["iv"] - 0.15) < 1e-3
    assert _evaluate(h, [{**no_iv[0], "premium": 0}]).status_code == 422
    naive = client.post("/api/options-builder/evaluate", json={"legs": legs, "spot": SPOT, "as_of": "2026-03-02T10:00:00"}, headers=h)
    assert naive.status_code == 422
    intraday = client.post("/api/options-builder/evaluate", json={"legs": legs, "spot": SPOT, "as_of": f"{EXP.isoformat()}T10:00:00+05:30"}, headers=h)
    assert intraday.status_code == 200 and intraday.json()["legs"][0]["greeks"]["gamma"] > 0  # expiry day still has hours


def _chain():
    rows = []
    for i in range(-15, 16):
        k = SPOT + i * 100
        c = m.leg_theoretical({"option_type": "CE", "strike": k, "expiry": EXP.isoformat(), "iv": 0.15}, SPOT, AS_OF)
        p = m.leg_theoretical({"option_type": "PE", "strike": k, "expiry": EXP.isoformat(), "iv": 0.15}, SPOT, AS_OF)
        rows.append(OptionChainRow(strike=k, call_ltp=round(c, 2), put_ltp=round(p, 2), call_iv=0.15, put_iv=0.15).model_dump())
    return OptionChain(underlying="NIFTY", expiry=EXP.isoformat(), underlying_ltp=SPOT, rows=rows).model_dump()


def test_suggest_runs_a_ported_rule_on_a_chain_and_says_how_pop_was_made():
    h = _headers("p1c-sug@example.com")
    body = {"chain": _chain(), "rule": "iron_condor", "step": 100, "hedge_width_points": 200, "pop_threshold_pct": 50, "as_of": AS_OF.isoformat()}
    out = client.post("/api/options-builder/suggest", json=body, headers=h).json()
    assert out["found"] and out["atm_strike"] == SPOT and out["result"]["combined_pop_pct"] >= 50 and "model" in out["pop_source"]
    assert client.post("/api/options-builder/suggest", json={**body, "pop_threshold_pct": 99.9}, headers=h).json()["found"] is False
    spread = {**body, "rule": "credit_spread"}
    assert client.post("/api/options-builder/suggest", json=spread, headers=h).status_code == 422          # needs a direction
    found = client.post("/api/options-builder/suggest", json={**spread, "direction": "BEARISH", "pop_threshold_pct": 80}, headers=h).json()
    assert found["found"] and found["result"]["short_pop_pct"] >= 80


def test_the_builder_package_cannot_place_an_order():
    """Research only (ADR-0006 / P1-c): nothing in app/options_builder imports execution, brokers' order calls or risk."""
    pkg = pathlib.Path(__file__).resolve().parents[1] / "app" / "options_builder"
    forbidden = re.compile(r"^\s*(from|import)\s+app\.(execution|trading|orders|risk|deployments|kill_switch)\b", re.M)
    for f in pkg.glob("*.py"):
        assert not forbidden.search(f.read_text()), f.name
        assert "place_order" not in f.read_text(), f.name
