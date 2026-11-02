"""M76 MP-2: the check service behind the ONE check card - every type with a fixture, his switches, "accepted today",
the 300 ms budget (a slow check is pending, Place never waits), and Place unchanged with lines shown (accept and
continue). Read-only: no check places, refuses, changes or delays an order or an exit."""
import asyncio
import time
import uuid
from dataclasses import replace
from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest

from app.main import app
from app.paper import pretrade_checks as pc
from app.paper import pretrade_routes
from app.paper import pretrade_warnings as pw
from tests.test_auth_api import _session_factory, client
from tests.test_fno1_chain_ticket import _body, _contract, _expiry, _org_tenant, _place, _preview
from tests.test_z3_approval_entries import _price
from tests.test_z5_p1d_risk_by_default import _flag

app.dependency_overrides[pretrade_routes.session_factory] = lambda: _session_factory
app.dependency_overrides[pretrade_routes.checks_rate_limit] = lambda: None
URL = "/api/paper/fno/order/checks"


@pytest.fixture
def on(monkeypatch):
    t = _org_tenant(f"m76-{uuid.uuid4().hex[:4]}")
    _flag("trade_warnings", t["tenant_id"])
    expiry = _expiry()
    strike = float(30000 + 50 * (uuid.uuid4().int % 100))
    _contract(expiry, strike)
    _price(monkeypatch, 100.0)
    return t, expiry, strike


def _checks(t, body, **kw):
    r = client.post(URL, headers=t["headers"], json={**body, **kw})
    assert r.status_code == 200, r.text
    return r.json()


def _ids(out) -> list:
    return [f["id"] for f in out["findings"]]


def _save(t, values):
    r = client.put("/api/me/trade-settings", headers=t["headers"], json={"values": values})
    assert r.status_code == 200, r.text


def test_off_without_the_flag():
    t = _org_tenant(f"m76-off-{uuid.uuid4().hex[:4]}")
    r = client.post(URL, headers=t["headers"], json=_body(_expiry(), 30000.0))
    assert r.status_code == 404 and r.headers["X-Feature-Disabled"] == "trade_warnings"


def test_the_answer_carries_his_switches_and_the_budget(on):
    t, expiry, strike = on
    out = _checks(t, _body(expiry, strike))
    assert out["budget_ms"] == 300 and out["pending"] == [] and out["ms"] < 300
    assert out["switches"]["duplicate_order"] == {"on": True, "ask": False}          # the owner's start states
    assert set(out["switches"]) == set(pw.WARNING_IDS)


def test_duplicate_order_is_a_fact_line_and_place_is_unchanged(on):
    t, expiry, strike = on
    assert "duplicate_order" not in _ids(_checks(t, _body(expiry, strike)))
    assert _place(t, _body(expiry, strike))["status"] == "PLACED"
    out = _checks(t, _body(expiry, strike))
    line = next(f for f in out["findings"] if f["id"] == "duplicate_order")
    assert line["severity"] == "mistake" and line["text"].startswith("Open: NIFTY") and line["text"].endswith("· this order: buy 1 lot")
    assert " buy 1 lot ·" in line["text"] and line["facts"]["open_lots"] == 1
    assert "duplicate_order" not in _ids(_checks(t, _body(expiry, strike, side="SELL")))   # other side: not a duplicate
    # Accept and continue: with the line shown, Place goes through exactly as before (a warning never refuses).
    assert _place(t, _body(expiry, strike))["status"] == "PLACED"


def test_limit_far_from_ltp_exists_only_with_his_own_band(on):
    t, expiry, strike = on
    body = _body(expiry, strike, order_type="LIMIT", price=120.0, ltp=100.0)
    assert "limit_far_from_ltp" not in _ids(_checks(t, body))                        # no band saved: no line (M76Q-1)
    _save(t, {"warnings.collar_pct": 5})
    line = next(f for f in _checks(t, body)["findings"] if f["id"] == "limit_far_from_ltp")
    assert line["text"] == "Limit 120.00 · LTP 100.00 · 20.0% away · your band 5%"
    assert "limit_far_from_ltp" not in _ids(_checks(t, {**body, "price": 104.0}))  # inside his band
    assert "limit_far_from_ltp" not in _ids(_checks(t, {**body, "order_type": "MARKET"}))


def test_a_switch_off_removes_its_line(on):
    t, expiry, strike = on
    _save(t, {"warnings.collar_pct": 5, "warnings.limit_far_from_ltp": False})
    out = _checks(t, _body(expiry, strike, order_type="LIMIT", price=120.0, ltp=100.0))
    assert "limit_far_from_ltp" not in _ids(out) and out["switches"]["limit_far_from_ltp"]["on"] is False


def test_accepted_today_hides_the_line_for_that_contract_only(on):
    t, expiry, strike = on
    _save(t, {"warnings.collar_pct": 5})
    body = _body(expiry, strike, order_type="LIMIT", price=120.0, ltp=100.0)
    r = client.post(URL + "/accept", headers=t["headers"],
                    json={"underlying": "NIFTY", "expiry": expiry.isoformat(), "strike": strike, "right": "CE", "types": ["limit_far_from_ltp"]})
    assert r.status_code == 204
    out = _checks(t, body)
    assert "limit_far_from_ltp" not in _ids(out) and "limit_far_from_ltp" in out["accepted"]
    assert "limit_far_from_ltp" in _ids(_checks(t, {**body, "right": "PE"}))         # another contract: shown


def test_the_preview_says_which_kind_each_warning_is(on):
    t, expiry, strike = on
    p = _preview(t, _body(expiry, strike))
    assert len(p["warning_ids"]) == len(p["warnings"])
    assert set(p["warning_ids"]) <= set(pw.WARNING_IDS) | {"status"}


# --- each server check with a fixture (no clock in the reading: the time is the order's own operational fact) ----------

USER = SimpleNamespace(id=1, tenant_id=1)
DRAFT = pc.Draft("NIFTY", date(2026, 11, 3), 24500.0, "CE", "BUY", 1, "MARKET", "MIS", None, 100.0)


def test_market_state_pre_open_and_closed():
    s = pw.settings_from({})
    pre = asyncio.run(pc.check_market_state(_session_factory, USER, DRAFT, s, datetime(2026, 10, 27, 3, 35, tzinfo=UTC)))   # 09:05 IST
    assert pre["id"] == "market_state" and pre["text"] == "Market: pre-open (09:00-09:15)" and pre["severity"] == "fact"
    weekend = asyncio.run(pc.check_market_state(_session_factory, USER, DRAFT, s, datetime(2026, 11, 1, 5, 0, tzinfo=UTC)))  # a Sunday
    assert weekend["text"].startswith("Market: closed (weekend) · opens Mon 02 Nov 09:15")
    open_ = asyncio.run(pc.check_market_state(_session_factory, USER, DRAFT, s, datetime(2026, 10, 27, 5, 0, tzinfo=UTC)))
    assert open_ is None


def test_mis_cutoff_needs_his_time_and_his_minutes():
    now = datetime(2026, 10, 27, 9, 28, tzinfo=UTC)                                     # 14:58 IST
    assert asyncio.run(pc.check_mis_cutoff(_session_factory, USER, DRAFT, pw.settings_from({}), now)) is None
    only_time = pw.settings_from({"exits.square_off_time": "15:10"})
    assert asyncio.run(pc.check_mis_cutoff(_session_factory, USER, DRAFT, only_time, now)) is None
    s = pw.settings_from({"exits.square_off_time": "15:10", "warnings.mis_minutes": 15})
    line = asyncio.run(pc.check_mis_cutoff(_session_factory, USER, DRAFT, s, now))
    assert line["text"] == "MIS · your square-off 15:10 · 12 min left"
    nrml = replace(DRAFT, product="NRML")
    assert asyncio.run(pc.check_mis_cutoff(_session_factory, USER, nrml, s, now)) is None
    late = asyncio.run(pc.check_mis_cutoff(_session_factory, USER, DRAFT, s, datetime(2026, 10, 27, 10, 0, tzinfo=UTC)))
    assert late["text"] == "MIS · your square-off 15:10 has passed"


def test_wording_is_facts_with_numbers_no_advice():
    s = pw.settings_from({"warnings.collar_pct": 5.0})
    d = pc.Draft("NIFTY", date(2026, 11, 3), 24500.0, "CE", "BUY", 1, "LIMIT", "NRML", 182.0, 160.4)
    line = asyncio.run(pc.check_limit_far(_session_factory, USER, d, s, datetime.now(UTC)))
    assert line["text"] == "Limit 182.00 · LTP 160.40 · 13.5% away · your band 5%"
    for word in ("risky", "should", "avoid", "recommend", "buy now", "sell now", "target", "stop-loss"):
        assert word not in line["text"].lower()


# --- the 300 ms budget ---------------------------------------------------------------------------------------------------

def test_a_slow_check_is_pending_the_rest_come_back_within_the_budget_and_a_follow_up_collects_it(monkeypatch):
    async def slow(factory, user, d, s, now):
        await asyncio.sleep(0.6)
        return pc.finding("market_state", "Market: closed (holiday) · made-up")

    async def boom(factory, user, d, s, now):
        raise RuntimeError("feed down")

    async def quick(factory, user, d, s, now):
        return pc.finding("duplicate_order", "Open: X buy 1 lot · this order: buy 1 lot")
    monkeypatch.setattr(pc, "CHECKS", {"duplicate_order": quick, "limit_far_from_ltp": boom, "market_state": slow})
    s = pw.settings_from({})
    user = SimpleNamespace(id=4242, tenant_id=1)

    async def go():
        t0 = time.perf_counter()
        first = await pc.run(_session_factory, user, DRAFT, s, accepted=set())
        took = time.perf_counter() - t0
        await asyncio.sleep(0.4)                                          # the slow one finishes in the background
        second = await pc.run(_session_factory, user, DRAFT, s, accepted=set(), only=first["pending"])
        return first, took, second
    first, took, second = asyncio.run(go())
    assert took < 0.4 and first["ms"] < 400                               # 300 ms budget (+ scheduling slack)
    assert [f["id"] for f in first["findings"]] == ["duplicate_order"]    # a failing check says nothing
    assert first["pending"] == ["market_state"]
    assert [f["id"] for f in second["findings"]] == ["market_state"] and second["pending"] == []


def test_worst_first_mistakes_before_facts(monkeypatch):
    async def fact(factory, user, d, s, now):
        return pc.finding("market_state", "Market: pre-open (09:00-09:15)")

    async def mistake(factory, user, d, s, now):
        return pc.finding("limit_far_from_ltp", "Limit 1.00 · LTP 2.00 · 50.0% away · your band 5%")
    monkeypatch.setattr(pc, "CHECKS", {"market_state": fact, "limit_far_from_ltp": mistake})
    out = asyncio.run(pc.run(_session_factory, SimpleNamespace(id=4343, tenant_id=1), DRAFT, pw.settings_from({}), accepted=set()))
    assert [f["id"] for f in out["findings"]] == ["limit_far_from_ltp", "market_state"]


def test_the_check_service_never_touches_orders():
    import inspect
    src = inspect.getsource(pc) + inspect.getsource(pretrade_routes)
    for word in ("fno_ticket.place", "execute_signal", "exit_now", ".add(", "delete(", "update("):
        assert word not in src, word
