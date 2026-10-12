"""N2-3 (P1 spec 1.5, docs/design/N2_P1FGH.md): decision tools behind the `decision_tools` flag (off by default).

Covers:
- the risk-of-ruin / Kelly calculator: gambler's-ruin parity, Kelly, growth per trade, and the effect of the risk per
  trade;
- the what-if for the caller's own open positions: subjects by underlying, a model that matches the builder's, curves
  that meet the expiry payoff, and validation;
- user scope, and no advice wording.
"""
import asyncio
import math
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest

from app.db.models import TradeRecord
from app.decision_tools import calculators, own_stats, whatif
from app.journal.review import advice_in
from app.options_builder import model
from app.options_builder.payoff import compute_strategy_payoff_curve
from app.platform import controls
from tests.test_auth_api import _session_factory, client
from tests.test_trading_worker import _tenant
from tests.test_z3b_paper_account import _member

UTC = timezone.utc


def _run(coro):
    return asyncio.run(coro)


def _email(tag: str) -> str:
    return f"n2d-{tag}-{uuid.uuid4().hex[:6]}@example.com"


def _flag(tenant_id: int, on: bool = True) -> None:
    async def go():
        async with _session_factory() as session:
            current = await controls._get(session, controls.KEY_FEATURE_FLAGS)
            flags = dict(current.get("flags") or {})
            entry = flags.get("decision_tools") or {"on": False, "tenants": []}
            tenants = set(entry.get("tenants") or [])
            (tenants.add if on else tenants.discard)(tenant_id)
            flags["decision_tools"] = {"on": False, "tenants": sorted(tenants)}
            await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": flags}, None)
            await session.commit()
    _run(go())


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from _strings(v)


# --- calculator ------------------------------------------------------------------------------------------------------

def test_ruin_matches_gamblers_ruin_for_even_payoffs_and_kelly_and_growth_by_hand():
    out = calculators.ruin_probability(0.6, 1.0, 2.0, 20.0)
    assert out["status"] == "ok" and out["probability"] == pytest.approx((0.4 / 0.6) ** 10, rel=1e-9)
    calc = calculators.calculate(0.6, 1.0, 2.0, 20.0)
    assert calc["kelly"]["fraction"] == pytest.approx(0.2) and calc["expectancy_r"] == pytest.approx(0.2)
    row = next(r for r in calc["rows"] if r["chosen"])
    assert row["risk_pct"] == 2.0 and row["losses_to_ruin"] == 10
    assert row["growth_per_trade_pct"] == pytest.approx(100 * (0.6 * math.log(1.02) + 0.4 * math.log(0.98)), abs=1e-4)
    assert [r["risk_pct"] for r in calc["rows"]] == [0.5, 1.0, 2.0, 5.0]
    assert calculators.growth_per_trade(0.6, 1.0, 100.0) is None


def test_more_risk_per_trade_means_more_ruin_and_past_double_kelly_negative_growth():
    calc = calculators.calculate(0.55, 1.5, 3.0, 50.0, compare=[0.5, 1, 2, 5, 10, 40, 60])
    rows = calc["rows"]
    assert [r["risk_pct"] for r in rows] == [0.5, 1.0, 2.0, 3.0, 5.0, 10.0, 40.0, 60.0]
    probs = [r["ruin_probability"] for r in rows]
    assert probs == sorted(probs) and probs[0] < 1e-6
    kelly = calc["kelly"]["fraction"] * 100                           # 25 %
    assert kelly == pytest.approx(25.0)
    for r in rows:
        if r["risk_pct"] > 2 * kelly:
            assert r["growth_per_trade_pct"] < 0
        if r["risk_pct"] < kelly:
            assert r["growth_per_trade_pct"] > 0


def test_a_negative_expectancy_is_certain_ruin_and_bad_inputs_are_refused():
    calc = calculators.calculate(0.3, 1.5, 1.0, 50.0)
    assert calc["kelly"]["status"] == "negative" and all(r["ruin_status"] == "certain" and r["ruin_probability"] == 1.0 for r in calc["rows"])
    for bad in ((0, 1, 1, 10), (1, 1, 1, 10), (0.5, 0, 1, 10), (0.5, 1, 0, 10), (0.5, 1, 100, 10), (0.5, 1, 1, 0), (0.5, 1, 1, 101)):
        with pytest.raises(calculators.CalculatorError):
            calculators.calculate(*bad)
    for text in _strings(calculators.calculate(0.6, 2.0, 1.0, 30.0)):
        assert advice_in(text) == [], text


def test_weighted_coefficient_agrees_with_the_trade_list_version():
    pnls = [200.0] * 12 + [-100.0] * 8
    assert own_stats.weighted_adjustment_coefficient([(200.0, 0.6), (-100.0, 0.4)]) == pytest.approx(own_stats.adjustment_coefficient(pnls), rel=1e-9)


# --- what-if (pure) -----------------------------------------------------------------------------------------------------

class _T:
    """A TradeRecord-shaped row for the pure functions."""
    def __init__(self, id, symbol, kind, direction, qty, entry, expiry=None, underlying=None, mode="PAPER"):
        self.id, self.symbol, self.instrument_kind, self.direction, self.quantity, self.entry_price = id, symbol, kind, direction, qty, entry
        self.expiry, self.underlying_symbol, self.mode = expiry, underlying, mode


def test_symbols_legs_and_subjects():
    assert whatif.strike_and_right("NIFTY 24500 CE 01 OCT 26") == (24500.0, "CE")
    assert whatif.strike_and_right("NIFTY24OCT24500CE") == (24500.0, "CE")
    assert whatif.strike_and_right("NIFTY26O0124500PE") == (None, "PE")          # weekly: the day runs into the strike
    assert whatif.strike_and_right("RELIANCE") == (None, None)
    exp = date.today() + timedelta(days=10)
    rows = [_T(1, "NIFTY26O0124500PE", "OPTION", "SHORT", 75, 120.0, exp, "NIFTY 50"),
            _T(2, "NIFTY26O0124400PE", "OPTION", "LONG", 75, 80.0, exp, "NIFTY 50"),
            _T(3, "NIFTY26OCTFUT", "FUTURE", "LONG", 75, 24550.0, exp, "NIFTY 50"),
            _T(4, "RELIANCE", "UNDERLYING", "LONG", 10, 2400.0),
            _T(5, "NIFTY WEIRD", "OPTION", "LONG", 75, 10.0, exp, "NIFTY 50")]
    masters = {"NIFTY26O0124500PE": (24500.0, "PE"), "NIFTY26O0124400PE": (24400.0, "PE")}
    subs = {s["underlying"]: s for s in whatif.subjects(rows, masters)}
    assert set(subs) == {"NIFTY 50", "RELIANCE"}
    nifty = subs["NIFTY 50"]
    assert [leg["trade_id"] for leg in nifty["legs"]] == [1, 2, 3] and nifty["skipped"][0]["trade_id"] == 5
    assert nifty["legs"][0] == {"trade_id": 1, "direction": "SELL", "lots": 75.0, "lot_size": 1.0, "premium": 120.0, "symbol": "NIFTY26O0124500PE",
                                "option_type": "PE", "strike": 24500.0, "expiry": exp.isoformat()}
    assert nifty["has_options"] and nifty["expiries"] == [exp.isoformat()]
    assert subs["RELIANCE"]["legs"][0]["option_type"] == "FUT" and "expiry" not in subs["RELIANCE"]["legs"][0]


def test_what_if_curves_match_the_builder_model_and_meet_the_expiry_payoff():
    now = datetime(2026, 10, 12, 5, 0, tzinfo=UTC)
    exp = date(2026, 10, 20)
    legs = whatif.subjects([_T(1, "NIFTY 24500 PE 20 OCT 26", "OPTION", "SHORT", 75, 120.0, exp, "NIFTY 50"),
                            _T(2, "NIFTY 24400 PE 20 OCT 26", "OPTION", "LONG", 75, 80.0, exp, "NIFTY 50")])[0]["legs"]
    out = whatif.what_if(legs, spot=24512.0, iv=0.14, ivs={2: 0.16}, days_forward=3, iv_shift=0.02, range_pct=5, now=now)
    assert len(out["prices"]) == whatif.POINTS and out["prices"][0] == pytest.approx(24512 * 0.95, abs=0.01)
    priced = [{**legs[0], "iv": 0.14}, {**legs[1], "iv": 0.16}]
    assert out["curves"]["now"] == [round(v, 2) for v in model.pnl_on(priced, out["prices"], now)]
    assert out["curves"]["scenario"] == [round(v, 2) for v in model.pnl_on(priced, out["prices"], now + timedelta(days=3), iv_shift=0.02)]
    # At the expiry close every leg is worth its intrinsic value: the curve is the builder's expiry payoff.
    payoff = compute_strategy_payoff_curve([{k: leg[k] for k in ("direction", "option_type", "strike", "premium", "lots", "lot_size")} for leg in legs],
                                           out["prices"])
    assert out["curves"]["expiry"] == pytest.approx([round(v, 2) for v in payoff], abs=0.01)
    assert out["expiry"] == "2026-10-20" and out["max_days_forward"] == pytest.approx(8 + 5.0 / 24, abs=0.01)
    assert set(out["at_spot"]) == {"now", "scenario", "expiry"} and set(out["greeks"]) == {"delta", "gamma", "theta", "vega", "rho"}
    assert advice_in(out["note"]) == []


def test_what_if_validation_and_a_linear_cash_position():
    now = datetime(2026, 10, 12, 5, 0, tzinfo=UTC)
    exp = date(2026, 10, 20)
    opt = whatif.subjects([_T(1, "NIFTY24OCT24500CE", "OPTION", "LONG", 75, 100.0, exp, "NIFTY 50")])[0]["legs"]
    with pytest.raises(whatif.WhatIfError, match="IV"):
        whatif.what_if(opt, spot=24500, now=now)
    with pytest.raises(whatif.WhatIfError, match="Days forward"):
        whatif.what_if(opt, spot=24500, iv=0.14, days_forward=9, now=now)
    with pytest.raises(whatif.WhatIfError, match="Spot"):
        whatif.what_if(opt, spot=0, iv=0.14, now=now)
    with pytest.raises(whatif.WhatIfError):
        whatif.what_if([], spot=100, now=now)
    cash = whatif.subjects([_T(4, "RELIANCE", "UNDERLYING", "SHORT", 10, 2400.0)])[0]["legs"]
    out = whatif.what_if(cash, spot=2400.0, range_pct=10, now=now)
    assert "expiry" not in out["curves"] and out["curves"]["now"][0] == pytest.approx(2400.0, abs=0.01)   # short: +240 x 10 at -10 %
    assert out["curves"]["now"] == out["curves"]["scenario"] and out["greeks"]["delta"] == -10.0


# --- API -----------------------------------------------------------------------------------------------------------------

def _open_trade(t, symbol, kind, direction, qty, entry, expiry=None, underlying=None):
    async def go():
        async with _session_factory() as session:
            row = TradeRecord(tenant_id=t["tenant_id"], user_id=t["user_id"], mode="PAPER", symbol=symbol, strategy_id="manual", direction=direction,
                              entry_time=datetime.now(UTC) - timedelta(hours=1), entry_price=entry, quantity=qty, stop_loss=1.0, exchange="NFO",
                              instrument_kind=kind, expiry=expiry, underlying_symbol=underlying, lot_size=75 if kind != "UNDERLYING" else None)
            session.add(row)
            await session.commit()
            return row.id
    return _run(go())


def test_flag_off_hides_the_routes():
    assert "decision_tools" in controls.FEATURE_FLAGS and "decision_tools" in controls.DEFAULT_OFF_FLAGS
    t = _tenant(_email("off"))
    for method, path, body in (("post", "/api/decision/ruin", {"win_rate": 0.5, "payoff_ratio": 2, "risk_pct": 1, "ruin_pct": 50}),
                               ("get", "/api/decision/positions", None), ("post", "/api/decision/what-if", {"underlying": "X", "spot": 1})):
        r = client.request(method.upper(), path, headers=t["headers"], json=body)
        assert r.status_code == 404 and r.headers.get("X-Feature-Disabled") == "decision_tools", path


def test_ruin_route_and_validation():
    t = _tenant(_email("ruin"))
    _flag(t["tenant_id"])
    r = client.post("/api/decision/ruin", headers=t["headers"], json={"win_rate": 0.6, "payoff_ratio": 1, "risk_pct": 2, "ruin_pct": 20})
    assert r.status_code == 200 and r.json() == calculators.calculate(0.6, 1.0, 2.0, 20.0)
    assert client.post("/api/decision/ruin", headers=t["headers"], json={"win_rate": 1.2, "payoff_ratio": 1, "risk_pct": 2, "ruin_pct": 20}).status_code == 422


def test_positions_and_what_if_are_the_callers_own():
    owner = _tenant(_email("owner"))
    _flag(owner["tenant_id"])
    member = _member(owner)
    exp = date.today() + timedelta(days=20)
    _open_trade(owner, "NIFTY26OCT24500PE", "OPTION", "SHORT", 75, 120.0, exp, "NIFTY 50")
    _open_trade(owner, "NIFTY26OCT24400PE", "OPTION", "LONG", 75, 80.0, exp, "NIFTY 50")
    _open_trade(member, "BANKNIFTY26OCT52000CE", "OPTION", "LONG", 30, 300.0, exp, "NIFTY BANK")

    mine = client.get("/api/decision/positions", headers=owner["headers"]).json()["subjects"]
    assert [s["underlying"] for s in mine] == ["NIFTY 50"] and len(mine[0]["legs"]) == 2
    theirs = client.get("/api/decision/positions", headers=member["headers"]).json()["subjects"]
    assert [s["underlying"] for s in theirs] == ["NIFTY BANK"]

    body = {"underlying": "NIFTY 50", "spot": 24512.0, "iv": 0.14, "days_forward": 2, "iv_shift": 0.01}
    r = client.post("/api/decision/what-if", headers=owner["headers"], json=body)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["underlying"] == "NIFTY 50" and len(out["curves"]["scenario"]) == whatif.POINTS and out["skipped"] == []
    # The member cannot model the owner's position, and a missing IV is a clear 422.
    assert client.post("/api/decision/what-if", headers=member["headers"], json=body).status_code == 404
    assert client.post("/api/decision/what-if", headers=owner["headers"], json={**body, "iv": None}).status_code == 422
