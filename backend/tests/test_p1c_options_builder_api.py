"""P1-c: the Options Strategy Builder API - catalog, template, evaluate, suggest. Research only: checked against the
model functions (themselves checked against textbook values and Monte Carlo in P1-b), the flag, auth, and that the
package imports nothing that could place an order."""
import ast
import asyncio
import json
import math
import pathlib
import subprocess
import sys
from datetime import date, timedelta

from app.brokers.models import OptionChain, OptionChainRow
from app.core.config import RISK_FREE_RATE
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
    for leg, out_leg in zip(legs, body["legs"]):                                                # priced by the model: unchanged
        assert abs(out_leg["theoretical"] - leg["premium"]) < 0.01


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


# Every `app.*` module the builder may import (second review P1-c: an allowlist, because a denylist misses real paths
# such as app.risk_engine or app.public_api). Anything else under app is refused.
ALLOWED_APP_IMPORTS = ("app.options_builder", "app.option_chain", "app.brokers.models", "app.core.config", "app.core.rate_limit",
                       "app.auth.dependencies", "app.db.models", "app.db.session", "app.platform.controls")
ORDER_LAYERS = ("app.execution", "app.trading", "app.risk_engine", "app.deployments", "app.kill_switch", "app.public_api", "app.workers")


def _allowed(mod: str) -> bool:
    return not mod.startswith("app.") or any(mod == ok or mod.startswith(ok + ".") for ok in ALLOWED_APP_IMPORTS)


def test_the_builder_package_cannot_place_an_order():
    """Research only (ADR-0006 / P1-c): every module under app/options_builder imports only allowlisted `app.*` modules
    (absolute, `from app import x`, relative), uses no dynamic import, and never names an order call."""
    pkg = pathlib.Path(__file__).resolve().parents[1] / "app" / "options_builder"
    order_calls = {"place_order", "modify_order", "cancel_order", "exit_position", "place_stop_loss_order"}
    files = list(pkg.rglob("*.py"))
    assert len(files) >= 6
    for f in files:
        for node in ast.walk(ast.parse(f.read_text())):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                assert node.level <= 1, f"{f.name}: a relative import leaves the package"
                if node.level:
                    names = [f"app.options_builder.{node.module}" if node.module else "app.options_builder"]
                elif node.module == "app":
                    names = [f"app.{a.name}" for a in node.names]
                else:
                    names = [node.module or ""]
            else:
                names = []
            for mod in names:
                assert _allowed(mod), f"{f.name} imports {mod}"
                assert mod.split(".")[0] != "importlib", f"{f.name} imports importlib (dynamic imports bypass this check)"
            if isinstance(node, (ast.Name, ast.Attribute)):
                name = node.id if isinstance(node, ast.Name) else node.attr
                assert name not in order_calls | {"importlib", "import_module", "__import__"}, f"{f.name} names {name}"


def test_importing_the_builder_loads_no_order_layer():
    """The same rule at runtime, transitively: a fresh interpreter imports the builder's routes, and nothing from the
    execution, trading, risk, deployment or kill-switch layers (nor a broker adapter) ends up loaded."""
    code = ("import sys, json; import app.options_builder.routes; "
            "print(json.dumps(sorted(m for m in sys.modules if m.startswith('app.'))))")
    out = subprocess.run([sys.executable, "-c", code], cwd=pathlib.Path(__file__).resolve().parents[1], capture_output=True, text=True, check=True)
    loaded = json.loads(out.stdout.strip().splitlines()[-1])
    assert "app.options_builder.routes" in loaded
    assert not [m for m in loaded if m.startswith(ORDER_LAYERS)], loaded
    assert not [m for m in loaded if m.startswith("app.brokers.") and m != "app.brokers.models" and not m.startswith("app.brokers.models.")], loaded


def test_bad_numbers_are_refused_not_crashed_on():
    """Review P1-c: a wing below zero, a negative spot, a zero strike, Infinity and an oversized chain are each a 422."""
    h = _headers("p1c-bounds@example.com")
    wide = {"atm_strike": 300, "width": 200}
    assert _template(h, "Iron Condor", **wide).status_code == 422                                     # 300 - 2*200 < 0
    assert _template(h, "Iron Condor", **wide, spot=300, iv=0.3, as_of=AS_OF.isoformat()).status_code == 422
    assert _template(h, "Iron Condor", width=9_999_999).status_code == 422
    leg = _priced([{"direction": "BUY", "option_type": "CE", "strike": SPOT, "lots": 1, "expiry": EXP.isoformat()}])[0]
    raw = json.dumps({"legs": [leg], "spot": SPOT, "as_of": AS_OF.isoformat()}).replace(f'"premium": {leg["premium"]}', '"premium": Infinity')
    assert "Infinity" in raw
    r = client.post("/api/options-builder/evaluate", content=raw, headers={**h, "Content-Type": "application/json"})
    assert r.status_code == 422
    body = {"chain": _chain(), "rule": "iron_condor", "step": 100, "hedge_width_points": 200, "as_of": AS_OF.isoformat()}
    neg = client.post("/api/options-builder/suggest", json={**body, "chain": {**body["chain"], "underlying_ltp": -5}}, headers=h)
    assert neg.status_code == 422 and "underlying price" in neg.json()["detail"]
    zero = {**body["chain"], "rows": [{**body["chain"]["rows"][0], "strike": 0}] + body["chain"]["rows"][1:]}
    assert client.post("/api/options-builder/suggest", json={**body, "chain": zero}, headers=h).status_code == 422
    many = {**body["chain"], "rows": body["chain"]["rows"] * 40}                                     # 31 * 40 > 1000
    assert client.post("/api/options-builder/suggest", json={**body, "chain": many}, headers=h).status_code == 422


def test_the_iv_is_solved_at_the_rate_the_caller_gave():
    """Review P1-c: solving at the default rate and repricing at another moved a leg off its own premium."""
    h = _headers("p1c-rate@example.com")
    leg = {"direction": "BUY", "option_type": "CE", "strike": SPOT, "premium": 300.0, "lots": 1, "lot_size": 50, "expiry": EXP.isoformat()}
    for rate in (0.0, RISK_FREE_RATE, 0.25):
        out = _evaluate(h, [leg], rate=rate).json()
        priced = {**leg, "iv": out["legs"][0]["iv"]}
        assert abs(m.leg_theoretical(priced, SPOT, AS_OF, r=rate) - 300.0) < 0.01, rate
    assert _evaluate(h, [leg], rate=0.25).json()["legs"][0]["iv"] < _evaluate(h, [leg], rate=0.0).json()["legs"][0]["iv"]


def test_a_switched_off_builder_answers_503_before_reading_the_body():
    h = _headers("p1c-flag-body@example.com")
    _flag(False)
    try:
        assert client.post("/api/options-builder/evaluate", json={"legs": "not a list"}, headers=h).status_code == 503
    finally:
        _flag(True)


def test_a_template_can_be_priced_by_the_model_as_a_labelled_starting_point():
    h = _headers("p1c-price@example.com")
    out = _template(h, "Covered Call", lot_size=75, spot=SPOT, iv=0.15, as_of=AS_OF.isoformat()).json()
    assert out["priced_by_model"] is True and all(leg["premium_source"] == "model" and leg["lot_size"] == 75 for leg in out["legs"])
    fut = next(leg for leg in out["legs"] if leg["option_type"] == "FUT")
    assert fut["premium"] == round(SPOT * math.exp(RISK_FREE_RATE * 14 / 365), 2) and fut["iv"] is None       # the fair forward
    call = next(leg for leg in out["legs"] if leg["option_type"] == "CE")
    assert call["iv"] == 0.15 and call["premium"] > 0
    assert _template(h, "Covered Call", spot=SPOT).status_code == 422                                 # spot without iv
    plain = _template(h, "Iron Condor").json()
    assert plain["priced_by_model"] is False and "premium" not in plain["legs"][0]
