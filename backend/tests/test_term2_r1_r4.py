"""TERM-2 step 4 (owner order docs/owner-orders/ATP_ORDER_TERM2.md, "Beyond the best"), each behind its own flag, OFF by
default, PAPER first:

- R1 Roll: the preview of the exit of the caller's own open legs plus the same strikes on the next listed expiry, with the
  new legs' numbers from the basket's engine; reads only (no order, no exit), the caller's own legs only;
- R2 What-if: the basket's evaluate adds the combined payoff of his own open legs on the same underlying + expiry;
- R3 the My day read answers for the Terminal's strike-to-area mark and names each option's strike and right;
- R4 Margin pair: required (the estimate, labelled; the broker's number only through his own session) and available
  (the Terminal status funds);
- every one of them is ignored / 404 while its flag is off, so the answers are exactly as before.
"""
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app.db.models import OrderRecord, TradeRecord
from app.paper import fno_term
from app.platform import controls
from app.platform.flag_catalog import FLAG_GROUP, FLAG_INFO
from tests.test_auth_api import _session_factory, client
from tests.test_fno2_basket import LOT, _contract, _expiry, _leg, _org_tenant, _post, _prices, _run
from tests.test_z5_p1d_risk_by_default import _flag

FLAGS = ("term_roll", "term_whatif", "term_strike_area", "term_margin_pair")


@pytest.fixture
def spread(monkeypatch):
    """A put credit spread on one expiry and the same strikes on the next one: (expiry, next, strikes, {symbol: price})."""
    expiry = _expiry()
    nxt = expiry + timedelta(days=7)
    base = 30000.0 + 50 * (uuid.uuid4().int % 100)
    table = {}
    names = {}
    for day, (short_p, long_p) in ((expiry, (80.0, 40.0)), (nxt, (95.0, 50.0))):
        s = _contract(day, base - 100, "PE")
        b = _contract(day, base - 200, "PE")
        table[s], table[b] = short_p, long_p
        names[(day, "short")], names[(day, "long")] = s, b
    table["NIFTY 50"] = base
    _prices(monkeypatch, table)
    return expiry, nxt, base, names


def _on(t, *names):
    for name in ("terminal", *names):
        _flag(name, t["tenant_id"])


def _open_spread(t, expiry, base) -> list:
    legs = [_leg(expiry, base - 100, "PE", "SELL", 80.0, stop=120.0), _leg(expiry, base - 200, "PE", "BUY", 40.0)]
    out = _post(t, "execute", {"legs": legs, "basket_ref": uuid.uuid4().hex, "product": "NRML"})
    assert out["status"] == "PLACED", out
    return [r["trade_id"] for r in out["legs"]]


def _count_orders(tenant_id) -> int:
    async def go():
        async with _session_factory() as session:
            return int(await session.scalar(select(func.count()).select_from(OrderRecord).where(OrderRecord.tenant_id == tenant_id)) or 0)
    return _run(go())


def _open_trades(tenant_id) -> list:
    async def go():
        async with _session_factory() as session:
            return list(await session.scalars(select(TradeRecord).where(TradeRecord.tenant_id == tenant_id, TradeRecord.exit_time.is_(None))))
    return _run(go())


# --- the four flags --------------------------------------------------------------------------------------------------------

def test_the_four_flags_are_registered_off_by_default_in_the_orders_group():
    for name in FLAGS:
        assert name in controls.FEATURE_FLAGS and name in controls.DEFAULT_OFF_FLAGS, name
        assert FLAG_GROUP[name] == "orders"
        assert {"ui_v2", "terminal"} <= set(FLAG_INFO[name].depends_on)
    assert fno_term.FLAGS == FLAGS


# --- R2 what-if and R4 margin pair on the one evaluate call --------------------------------------------------------------

def test_evaluate_is_unchanged_while_the_flags_are_off(spread):
    expiry, _, base, _ = spread
    t = _org_tenant("t2off")
    _open_spread(t, expiry, base)
    body = {"legs": [{**_leg(expiry, base - 100, "PE", "SELL", 80.0), "iv": 0.15}], "spot": base, "with_open": True, "margin_pair": True}
    out = _post(t, "evaluate", body)
    assert out["ok"] is True and "whatif" not in out and "margin_pair" not in out
    # The preview / execute bodies do not take the what-if fields (extra fields are refused there, as before).
    bad = client.post("/api/paper/fno/basket/preview", headers=t["headers"], json=body)
    assert bad.status_code == 422


def test_whatif_combines_the_open_legs_on_the_same_underlying_and_expiry(spread):
    expiry, nxt, base, _ = spread
    t = _org_tenant("t2wi")
    _on(t, "term_whatif")
    _open_spread(t, expiry, base)
    new = [{**_leg(expiry, base - 100, "PE", "SELL", 80.0), "iv": 0.15}]
    out = _post(t, "evaluate", {"legs": new, "spot": base, "with_open": True})
    wi = out["whatif"]
    assert wi["ok"] is True and len(wi["open"]) == 2 and {o["side"] for o in wi["open"]} == {"BUY", "SELL"}
    assert [leg["id"] for leg in wi["open_legs"]] == [f"open-{o['trade_id']}" for o in wi["open"]]
    # One more short put on the open spread: the combined credit is the basket's plus the spread's own.
    assert out["net_premium"] == pytest.approx(80 * LOT)
    assert wi["net_premium"] == pytest.approx((80 + 80 - 40) * LOT, rel=1e-3)        # the open legs at their filled entry price
    assert out["extremes"]["max_loss"] != wi["extremes"]["max_loss"]
    assert "order" not in wi and "labels" not in wi               # the payoff only; the basket's own figures stay the basket's
    # Another expiry: nothing open there, so no combined payoff (the basket's own is the answer).
    other = _post(t, "evaluate", {"legs": [{**_leg(nxt, base - 100, "PE", "SELL", 95.0), "iv": 0.15}], "spot": base, "with_open": True})
    assert other["whatif"]["ok"] is False and other["whatif"]["open"] == []


def test_margin_pair_is_the_estimate_labelled_and_the_status_funds(spread):
    expiry, _, base, _ = spread
    t = _org_tenant("t2mp")
    _on(t, "term_margin_pair")
    legs = [{**_leg(expiry, base - 100, "PE", "SELL", 80.0, stop=120.0), "iv": 0.15}, {**_leg(expiry, base - 200, "PE", "BUY", 40.0), "iv": 0.15}]
    one = _post(t, "evaluate", {"legs": legs, "spot": base, "margin_pair": True})["margin_pair"]
    assert one["source"] == "estimate" and one["estimate"] is True and "not your broker's number" in one["label"]
    assert one["required"] == pytest.approx((100 - 40) * LOT)
    status = client.get("/api/terminal/status", headers=t["headers"]).json()
    assert one["available"] == pytest.approx(status["funds"]["available"])
    assert one["free_after"] == pytest.approx(one["available"] - one["required"])
    # Every lot change is a new read: twice the lots, twice the margin.
    two = _post(t, "evaluate", {"legs": [{**leg, "lots": 2} for leg in legs], "spot": base, "margin_pair": True})["margin_pair"]
    assert two["required"] == pytest.approx(2 * one["required"])


# --- R1 roll -----------------------------------------------------------------------------------------------------------

def test_roll_preview_is_404_without_its_flag(spread):
    expiry, _, base, _ = spread
    t = _org_tenant("t2r0")
    ids = _open_spread(t, expiry, base)
    _flag("terminal", t["tenant_id"])
    out = client.post("/api/paper/fno/roll/preview", headers=t["headers"], json={"trade_ids": ids})
    assert out.status_code == 404 and out.headers["X-Feature-Disabled"] == "term_roll"


def test_roll_preview_moves_the_same_strikes_to_the_next_expiry_and_places_nothing(spread):
    expiry, nxt, base, names = spread
    t = _org_tenant("t2r1")
    _on(t, "term_roll")
    ids = _open_spread(t, expiry, base)
    before = _count_orders(t["tenant_id"])
    out = client.post("/api/paper/fno/roll/preview", headers=t["headers"], json={"trade_ids": ids})
    assert out.status_code == 200, out.text
    p = out.json()
    assert p["ok"] is True and p["mode"] == "PAPER" and p["atomic"] is False and "Step 1" in p["steps"] and "Step 2" in p["steps"]
    assert p["expiry"] == nxt.isoformat() and p["underlying"] == "NIFTY"
    assert sorted((e["side"], e["lots"]) for e in p["exit"]) == [("BUY", 1), ("SELL", 1)]
    assert sorted((leg["side"], leg["strike"], leg["right"], leg["expiry"]) for leg in p["legs"]) == [
        ("BUY", base - 200, "PE", nxt.isoformat()), ("SELL", base - 100, "PE", nxt.isoformat())]
    assert all(leg["stop_loss"] is None for leg in p["legs"])          # DEF-0: his own stop, blank until he sets one
    # The roll's money: closing the spread pays 80 and receives 40; the new spread receives 95 and pays 50.
    assert p["exit_value"] == pytest.approx((40 - 80) * LOT)
    assert p["numbers"]["net_premium"] == pytest.approx((95 - 50) * LOT)
    assert p["net"] == pytest.approx(((40 - 80) + (95 - 50)) * LOT)
    assert p["numbers"]["extremes"]["max_loss"] == pytest.approx((95 - 50) * LOT - 100 * LOT)
    # A preview only: no order and no exit.
    assert _count_orders(t["tenant_id"]) == before and len(_open_trades(t["tenant_id"])) == 2


def test_roll_preview_reads_only_the_callers_own_legs(spread):
    expiry, _, base, _ = spread
    owner = _org_tenant("t2r2")
    ids = _open_spread(owner, expiry, base)
    other = _org_tenant("t2r3")
    _on(other, "term_roll")
    out = client.post("/api/paper/fno/roll/preview", headers=other["headers"], json={"trade_ids": ids})
    assert out.status_code == 404


def test_exits_stay_open_while_a_roll_is_previewed(spread):
    """ADR-0004: the roll's step 1 is the ordinary Exit now - never blocked by the roll or its flag."""
    expiry, _, base, _ = spread
    t = _org_tenant("t2r4")
    _on(t, "term_roll")
    ids = _open_spread(t, expiry, base)
    assert client.post("/api/paper/fno/roll/preview", headers=t["headers"], json={"trade_ids": ids}).status_code == 200
    for trade_id in ids:
        out = client.post(f"/api/positions/{trade_id}/exit-now", headers=t["headers"])
        assert out.status_code == 200, out.text
    assert _open_trades(t["tenant_id"]) == []


# --- R3 the My day read for the strike-to-area mark ---------------------------------------------------------------------

def test_my_day_answers_for_the_strike_area_mark_and_names_the_strike(spread):
    expiry, _, base, _ = spread
    t = _org_tenant("t2r3a")
    _open_spread(t, expiry, base)
    assert client.get("/api/today/my-day", headers=t["headers"]).status_code == 404
    _flag("terminal", t["tenant_id"])
    assert client.get("/api/today/my-day", headers=t["headers"]).status_code == 404
    _flag("term_strike_area", t["tenant_id"])
    out = client.get("/api/today/my-day", headers=t["headers"])
    assert out.status_code == 200, out.text
    rows = out.json()["positions"]
    assert sorted((r["side"], r["strike"], r["right"]) for r in rows) == [("LONG", base - 200, "PE"), ("SHORT", base - 100, "PE")]
