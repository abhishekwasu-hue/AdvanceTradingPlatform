"""FNO-2 (owner order ATP_SESSION_FNO_WORKSPACE.md, sections 5 and 9): the basket of option legs, PAPER only.

- the routes exist only with `ui_v2` and the PAPER account on, and the table classifies them;
- the numbers: the combined payoff, max profit / loss, breakevens and the margin WITH the hedge (an estimate, labelled)
  beside the short legs alone (the broker's margin, never invented);
- Execute basket: every leg one PAPER order through the ticket, hedge legs first; nothing placed when a check refuses;
  partial fills reported per leg with what to do, never retried; a sold leg is never placed after its hedge failed;
- under the Phase 0 lock every leg is PAPER, asserted at the chokepoint; exits still run (ADR-0004);
- saved baskets are the caller's own (prefs namespace `fno_baskets`), refused when malformed, and fit the size cap;
- the ready strategies read the caller's own options settings, else a neutral default said so."""
import asyncio
import json
import random
import uuid
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import select

from app.ai import worker_bridge
from app.core.models import RiskConfig
from app.db.models import InstrumentRecord, OrderRecord, StrategyDeploymentRecord, TradeRecord
from app.market_data.calendar import IST
from app.me import prefs as P
from app.paper import fno_basket, fno_ticket
from app.platform import phase0_lock
from tests.test_auth_api import _session_factory, client
from tests.test_trading_worker import _tenant
from tests.test_z3b_paper_account import _member, _paper_flag
from tests.test_z5_p1d_risk_by_default import _flag
from tests.test_z10c_phase0_lock import _store_lock

LOT = 75
SPOT = 30000.0


def _run(coro):
    return asyncio.run(coro)


def _expiry() -> date:
    return datetime.now(IST).date() + timedelta(days=60 + random.randint(0, 2000))


def _contract(expiry: date, strike: float, right: str) -> str:
    symbol = f"NIFTY{uuid.uuid4().hex[:6].upper()}{int(strike)}{right}"

    async def go():
        async with _session_factory() as session:
            session.add(InstrumentRecord(broker="upstox", exchange="NFO", segment="NSE_FO", instrument_key=f"NSE_FO|{uuid.uuid4().hex[:10]}",
                                         tradingsymbol=symbol, name="NIFTY", underlying="NIFTY", instrument_type=right, expiry=expiry,
                                         strike=strike, lot_size=LOT, tick_size=0.05))
            await session.commit()
    _run(go())
    return symbol


def _risk(t: dict, *, max_open=10) -> None:
    body = RiskConfig(capital=1_000_000.0, risk_per_trade_pct=1.0, max_daily_loss_pct=5.0, max_trades_per_day=50, max_open_positions=max_open,
                      dd_level_1_pct=20.0, dd_level_2_pct=25.0, stop_cooldown_minutes=0).model_dump()
    out = client.put("/api/risk-settings", headers=t["headers"], json=body)
    assert out.status_code == 200, out.text


def _org_tenant(tag: str, *, max_open=10) -> dict:
    t = _tenant(f"fno2-{tag}-{uuid.uuid4().hex[:6]}@example.com")
    _flag("ui_v2", t["tenant_id"])
    _paper_flag(t["tenant_id"])
    _risk(t, max_open=max_open)
    return t


def _prices(monkeypatch, table: dict) -> None:
    """The live price on the caller's own session, by trading symbol (a symbol not in `table` has no price)."""
    async def factory(session, who):
        async def lookup(symbol, exchange="NSE"):
            return float(table[symbol])
        return lookup
    monkeypatch.setattr(worker_bridge, "price_lookup_for_user", factory)


@pytest.fixture
def condor(monkeypatch):
    """An iron condor's four contracts around a unique expiry: (expiry, {name: (strike, right, symbol, price)})."""
    expiry = _expiry()
    base = float(random.randrange(30000, 40000, 50))
    spec = {"long_put": (base - 200, "PE", 40.0), "short_put": (base - 100, "PE", 80.0),
            "short_call": (base + 100, "CE", 80.0), "long_call": (base + 200, "CE", 40.0)}
    legs = {k: (s, r, _contract(expiry, s, r), p) for k, (s, r, p) in spec.items()}
    _prices(monkeypatch, {sym: p for _, (_, _, sym, p) in legs.items()})
    return expiry, base, legs


def _leg(expiry, strike, right, side, price, *, lots=1, stop=None) -> dict:
    out = {"underlying": "NIFTY", "expiry": expiry.isoformat(), "strike": strike, "right": right, "side": side, "lots": lots, "price": price}
    if stop is not None:
        out["stop_loss"] = stop
    return out


def _condor_legs(expiry, legs) -> list:
    """The user's own order of entry: the shorts first - the basket still places the hedges first."""
    sp, sc, lp, lc = legs["short_put"], legs["short_call"], legs["long_put"], legs["long_call"]
    return [_leg(expiry, sp[0], "PE", "SELL", sp[3], stop=120.0), _leg(expiry, sc[0], "CE", "SELL", sc[3], stop=120.0),
            _leg(expiry, lp[0], "PE", "BUY", lp[3]), _leg(expiry, lc[0], "CE", "BUY", lc[3])]


def _post(t, path, body) -> dict:
    out = client.post(f"/api/paper/fno/basket/{path}", headers=t["headers"], json=body)
    assert out.status_code == 200, out.text
    return out.json()


def _orders(tenant_id):
    async def go():
        async with _session_factory() as session:
            return list(await session.scalars(select(OrderRecord).where(OrderRecord.tenant_id == tenant_id).order_by(OrderRecord.id)))
    return _run(go())


# --- flags, the route table ----------------------------------------------------------------------------------------------

def test_basket_routes_are_off_without_ui_v2_and_the_paper_account():
    t = _tenant(f"fno2-off-{uuid.uuid4().hex[:6]}@example.com")
    body = {"legs": [_leg(_expiry(), 30000.0, "CE", "BUY", 100.0)], "spot": SPOT}
    for flags in ((), ("ui_v2",)):
        for name in flags:
            _flag(name, t["tenant_id"])
        for path in ("evaluate", "preview", "broker-link"):
            out = client.post(f"/api/paper/fno/basket/{path}", headers=t["headers"], json=body)
            assert out.status_code == 404 and out.headers["X-Feature-Disabled"] == ("ui_v2" if not flags else "paper_account")
        out = client.post("/api/paper/fno/basket/execute", headers=t["headers"], json={**body, "basket_ref": uuid.uuid4().hex})
        assert out.status_code == 404
    assert client.post("/api/paper/fno/basket/execute", json={**body, "basket_ref": uuid.uuid4().hex}).status_code == 401


def test_the_basket_has_no_mode_and_one_underlying():
    from app.paper.fno_routes import BasketBody, ExecuteBody
    assert "mode" not in BasketBody.model_fields and "mode" not in ExecuteBody.model_fields
    t = _org_tenant("one")
    e = _expiry()
    two = {"legs": [_leg(e, 30000.0, "CE", "BUY", 10.0), {**_leg(e, 30000.0, "CE", "BUY", 10.0), "underlying": "BANKNIFTY"}], "spot": SPOT}
    out = client.post("/api/paper/fno/basket/evaluate", headers=t["headers"], json=two)
    assert out.status_code == 422 and "one underlying" in out.json()["detail"]
    too_many = {"legs": [_leg(e, 30000.0 + 50 * i, "CE", "BUY", 10.0) for i in range(fno_basket.MAX_LEGS + 1)], "spot": SPOT}
    assert client.post("/api/paper/fno/basket/evaluate", headers=t["headers"], json=too_many).status_code == 422


# --- the numbers: payoff and margin with the hedge -------------------------------------------------------------------------

def test_hedge_first_keeps_each_group_in_order():
    e = date(2030, 1, 1)
    legs = [fno_basket.LegAsk("NIFTY", e, 100, "PE", "SELL", 1), fno_basket.LegAsk("NIFTY", e, 90, "PE", "BUY", 1),
            fno_basket.LegAsk("NIFTY", e, 110, "CE", "SELL", 1), fno_basket.LegAsk("NIFTY", e, 120, "CE", "BUY", 1)]
    assert [i for i, _ in fno_basket.hedge_first(legs)] == [1, 3, 0, 2]


def test_margin_with_the_hedge_and_without_it():
    e = "2030-01-31"
    short = {"direction": "SELL", "option_type": "PE", "strike": 30000.0, "premium": 100.0, "lots": 1, "lot_size": LOT, "expiry": e}
    hedge = {"direction": "BUY", "option_type": "PE", "strike": 29900.0, "premium": 60.0, "lots": 1, "lot_size": LOT, "expiry": e}
    with_hedge = fno_basket.hedge_margin([short, hedge])
    assert with_hedge["hedged"] is True and with_hedge["source"] == "estimate" and "not your broker's number" in with_hedge["note"]
    assert with_hedge["with_hedge"]["value"] == pytest.approx((100 - 40) * LOT) and with_hedge["with_hedge"]["estimated"] is True
    assert with_hedge["without_hedge"]["value"] is None and "broker's margin" in with_hedge["without_hedge"]["basis"]
    assert with_hedge["benefit"] is None                                   # nothing is made up for the naked short
    naked = fno_basket.hedge_margin([short])
    assert naked["hedged"] is False and naked["without_hedge"] is None and naked["with_hedge"]["value"] is None
    longs = fno_basket.hedge_margin([hedge])
    assert longs["with_hedge"]["value"] == pytest.approx(60.0 * LOT) and longs["without_hedge"] is None


def test_evaluate_gives_the_combined_payoff_from_the_builder_engine(condor):
    expiry, base, legs = condor
    t = _org_tenant("eval")
    body = {"legs": [{**leg, "iv": 0.15} for leg in _condor_legs(expiry, legs)], "spot": base}
    out = _post(t, "evaluate", body)
    assert out["ok"] is True and out["lot_sizes"] == [LOT] * 4
    assert out["labels"][0] == fno_ticket.contract_label("NIFTY", expiry, base - 100, "PE")
    assert out["order"] == [2, 3, 0, 1]                                          # hedges first
    credit = (80 + 80 - 40 - 40) * LOT
    assert out["net_premium"] == pytest.approx(credit)
    assert out["extremes"]["max_profit"] == pytest.approx(credit) and out["extremes"]["max_loss"] == pytest.approx(credit - 100 * LOT)
    assert out["breakevens"] == pytest.approx([base - 100 - 80, base + 100 + 80])     # each short strike -/+ the net credit per unit
    assert len(out["prices"]) == len(out["at_expiry"]) == len(out["today"]) == 241
    assert 0 < out["summary"]["pop"] < 1 and set(out["summary"]["greeks"]) >= {"delta", "gamma", "theta", "vega"}
    hedge = out["margin"]["hedge"]
    assert hedge["hedged"] and hedge["with_hedge"]["value"] == pytest.approx(100 * LOT - credit)
    # A leg without a price: said so, no numbers made up.
    no_price = _post(t, "evaluate", {"legs": [{**_condor_legs(expiry, legs)[0], "price": None}], "spot": base})
    assert no_price["ok"] is False and "No price" in no_price["errors"][0]


# --- the confirm step and Execute basket ----------------------------------------------------------------------------------

def test_the_confirm_step_checks_every_leg_hedges_first_with_the_totals(condor):
    expiry, base, legs = condor
    t = _org_tenant("prev")
    view = _post(t, "preview", {"legs": [{**leg, "iv": 0.15} for leg in _condor_legs(expiry, legs)], "spot": base, "product": "NRML"})
    assert view["mode"] == "PAPER" and "Practice mode (PAPER)" in view["practice"]
    assert [(r["role"], r["side"]) for r in view["legs"]] == [("hedge", "BUY"), ("hedge", "BUY"), ("short", "SELL"), ("short", "SELL")]
    assert all(r["can_place"] and r["price_source"] == "live" for r in view["legs"]), view["legs"]
    assert view["can_execute"] is True and view["confirm_label"] == "Place 4 PAPER orders, hedge legs first"
    totals = view["totals"]
    assert view["totals_errors"] == [] and totals is not None
    assert totals["extremes"]["max_loss"] == pytest.approx((80 + 80 - 40 - 40 - 100) * LOT)
    assert totals["margin"]["hedge"]["with_hedge"]["value"] == pytest.approx((100 - 80) * LOT)
    assert _orders(t["tenant_id"]) == []                                            # the confirm step places nothing


def test_no_room_for_every_leg_places_nothing(condor):
    expiry, base, legs = condor
    t = _org_tenant("room", max_open=3)
    view = _post(t, "preview", {"legs": _condor_legs(expiry, legs), "spot": base})
    assert not view["can_execute"] and "opens 4 positions" in view["refusals"][0] and "limit of 3 open positions" in view["refusals"][0]
    out = _post(t, "execute", {"legs": _condor_legs(expiry, legs), "basket_ref": uuid.uuid4().hex})
    assert out["status"] == "REFUSED" and out["placed"] == 0 and "Nothing was placed" in out["next_steps"][0]
    assert _orders(t["tenant_id"]) == []


def test_execute_places_every_leg_in_paper_hedges_first(condor, monkeypatch):
    expiry, base, legs = condor
    t = _org_tenant("exec")
    _lock(t["tenant_id"], True)
    try:
        asserted = []
        real = phase0_lock.assert_paper
        monkeypatch.setattr(phase0_lock, "assert_paper", lambda mode, tenant_id: asserted.append((mode, tenant_id)) or real(mode, tenant_id))
        ref = uuid.uuid4().hex
        out = _post(t, "execute", {"legs": _condor_legs(expiry, legs), "basket_ref": ref, "product": "NRML"})
        assert out["status"] == "PLACED" and out["placed"] == 4 and out["next_steps"] == [], out
        assert asserted == [("PAPER", t["tenant_id"])] * 4                    # every leg asserted at the chokepoint
        orders = _orders(t["tenant_id"])
        assert [o.symbol for o in orders] == [legs["long_put"][2], legs["long_call"][2], legs["short_put"][2], legs["short_call"][2]]
        assert all(o.mode == "PAPER" and o.strategy_id == "manual_fno" for o in orders)
        assert [r["leg"] for r in out["legs"]] == [2, 3, 0, 1] and all(r["mode"] == "PAPER" for r in out["legs"])
        # A second tap on the same basket never places twice.
        again = client.post("/api/paper/fno/basket/execute", headers=t["headers"],
                            json={"legs": _condor_legs(expiry, legs), "basket_ref": ref, "product": "NRML"}).json()
        assert len(_orders(t["tenant_id"])) == 4 and again["placed"] == 4, again       # the same four orders, found by their keys

        async def trades():
            async with _session_factory() as session:
                return list(await session.scalars(select(TradeRecord).where(TradeRecord.tenant_id == t["tenant_id"])))
        held = _run(trades())
        assert len(held) == 4 and all(x.mode == "PAPER" and x.holding == "SWING" for x in held)
        assert sorted(x.stop_loss for x in held if x.option_position == "WRITE") == [120.0, 120.0]
        # Exits are never blocked, under the lock too.
        out = client.post(f"/api/positions/{held[0].id}/exit-now", headers=t["headers"])
        assert out.status_code == 200, out.text
    finally:
        _lock(t["tenant_id"], False)


def test_a_partial_fill_is_shown_per_leg_and_never_retried(condor, monkeypatch):
    expiry, base, legs = condor
    t = _org_tenant("part")
    calls = []
    real = fno_ticket.place

    async def flaky(session, user, ask, **kw):
        calls.append((ask.side, ask.strike, ask.right))
        if ask.side == "SELL" and ask.right == "CE":                          # the broker-side failure after the check passed
            return {"status": "REFUSED", "reasons": ["Max open positions reached (3)"], "label": "x", "order_id": None, "trade_id": None,
                    "filled": None}
        return await real(session, user, ask, **kw)
    monkeypatch.setattr(fno_ticket, "place", flaky)
    out = _post(t, "execute", {"legs": _condor_legs(expiry, legs), "basket_ref": uuid.uuid4().hex})
    assert out["status"] == "PARTIAL" and out["placed"] == 3
    assert [r["status"] for r in out["legs"]] == ["PLACED", "PLACED", "PLACED", "REFUSED"]
    assert out["legs"][3]["reasons"] == ["Max open positions reached (3)"]
    assert len(calls) == 4                                                    # one try per leg, nothing retried
    assert "Exit now" in out["next_steps"][0] and "Place the remaining legs" in out["next_steps"][1]
    assert len(_orders(t["tenant_id"])) == 3                                  # nothing that filled is undone


def test_a_sold_leg_is_never_placed_after_its_hedge_failed(condor, monkeypatch):
    expiry, base, legs = condor
    t = _org_tenant("hedge")
    calls = []

    async def no_hedge(session, user, ask, **kw):
        calls.append(ask.side)
        return {"status": "FAILED", "reasons": ["Not placed: TimeoutError"], "label": "x", "order_id": None, "trade_id": None, "filled": None}
    monkeypatch.setattr(fno_ticket, "place", no_hedge)
    out = _post(t, "execute", {"legs": _condor_legs(expiry, legs), "basket_ref": uuid.uuid4().hex})
    assert out["status"] == "REFUSED" and out["placed"] == 0
    assert calls == ["BUY", "BUY"]                                              # the shorts were never sent
    assert [r["status"] for r in out["legs"]] == ["FAILED", "FAILED", "NOT_PLACED", "NOT_PLACED"]
    assert "uncovered" in out["legs"][2]["reasons"][0]


def test_a_sold_leg_without_a_stop_refuses_the_whole_basket(condor):
    expiry, base, legs = condor
    t = _org_tenant("stop")
    body = _condor_legs(expiry, legs)
    body[0].pop("stop_loss")
    out = _post(t, "execute", {"legs": body, "basket_ref": uuid.uuid4().hex})
    assert out["status"] == "REFUSED" and any("needs a stop-loss" in r for r in out["reasons"])
    assert _orders(t["tenant_id"]) == []


def _lock(tenant_id, on: bool) -> None:
    async def go():
        async with _session_factory() as session:
            await _store_lock(session, False, [tenant_id] if on else [])
    _run(go())
    if not on:
        phase0_lock.reset_cache()


# --- saved baskets: the caller's own -----------------------------------------------------------------------------------

def _saved(expiry: str, n_legs=2, name="Bull put 22800") -> dict:
    return {"name": name, "underlying": "NIFTY", "product": "MIS",
            "legs": [{"expiry": expiry, "strike": 22800.0 - 100 * i, "right": "PE", "side": "SELL" if i == 0 else "BUY", "lots": 1,
                      "price": 100.5, **({"stop_loss": 150.0} if i == 0 else {})} for i in range(n_legs)]}


def test_saved_baskets_are_the_callers_own():
    a = _tenant(f"fno2-save-{uuid.uuid4().hex[:6]}@example.com")
    member = _member(a)
    other = _tenant(f"fno2-other-{uuid.uuid4().hex[:6]}@example.com")
    out = client.put("/api/me/prefs/fno_baskets?version=0", headers=a["headers"], json={"items": {"abcd1234": _saved("2026-10-28")}})
    assert out.status_code == 200, out.text
    assert out.json()["value"]["items"]["abcd1234"]["legs"][0]["stop_loss"] == 150.0
    got = client.get("/api/me/prefs", headers=a["headers"]).json()
    assert list(got["prefs"]["fno_baskets"]["items"]) == ["abcd1234"] and got["versions"]["fno_baskets"] == 1
    for who in (member, other):
        assert client.get("/api/me/prefs", headers=who["headers"]).json()["prefs"]["fno_baskets"] == {"items": {}}
    # A stale version is a conflict, not an overwrite.
    stale = client.put("/api/me/prefs/fno_baskets?version=0", headers=a["headers"], json={"items": {}})
    assert stale.status_code == 409


@pytest.mark.parametrize("bad", [
    {"items": {"abcd1234": {**_saved("2026-10-28"), "mode": "LIVE"}}},
    {"items": {"abcd1234": {**_saved("2026-10-28"), "underlying": "nifty"}}},
    {"items": {"abcd1234": _saved("2026-10-28", n_legs=P.MAX_BASKET_LEGS + 1)}},
    {"items": {f"b{i:07d}": _saved("2026-10-28") for i in range(P.MAX_SAVED_BASKETS + 1)}},
    {"items": {"abcd1234": {**_saved("2026-10-28"), "legs": [{**_saved("2026-10-28")["legs"][0], "right": "FUT"}]}}},
])
def test_a_malformed_saved_basket_is_refused(bad):
    a = _tenant(f"fno2-bad-{uuid.uuid4().hex[:6]}@example.com")
    assert client.put("/api/me/prefs/fno_baskets", headers=a["headers"], json=bad).status_code == 422


def test_the_most_a_user_can_save_fits_the_size_cap():
    biggest = {"items": {f"{i:016d}": {**_saved("2026-10-28", n_legs=P.MAX_BASKET_LEGS, name="A" * 40), "underlying": "B" * 30,
                                       "saved_at": "2026-10-22T10:00:00.123456+05:30",
                                       "legs": [{"expiry": "2026-10-28", "strike": 9999999.99, "right": "PE", "side": "SELL", "lots": 500,
                                                 "price": 9999999.99, "stop_loss": 9999999.99}] * P.MAX_BASKET_LEGS}
                       for i in range(P.MAX_SAVED_BASKETS)}}
    raw = json.dumps(biggest).encode()
    P.FnoBasketsPrefs.model_validate_json(raw)
    assert len(raw) < P.MAX_BODY_BYTES, len(raw)


# --- the user's own settings for the ready strategies ---------------------------------------------------------------------

def test_ready_strategy_settings_are_the_users_own_else_a_neutral_default():
    t = _org_tenant("set")
    out = client.get("/api/paper/fno/basket/settings", headers=t["headers"], params={"underlying": "NIFTY"}).json()
    assert out["source"] is None and out["width_steps"] == fno_basket.NEUTRAL_WIDTH_STEPS and out["lots"] == 1
    assert "neutral start" in out["note"] and "Edit any leg" in out["note"]
    member = _member(t)

    async def deploy(user_id, symbol, width, lots, stop, sid):
        async with _session_factory() as session:
            session.add(StrategyDeploymentRecord(tenant_id=t["tenant_id"], strategy_id=sid, symbol=symbol, mode="PAPER", instrument_kind="OPTION",
                                                 option_position="WRITE", option_strategy="BULL_PUT_SPREAD", spread_width=width, max_lots=lots,
                                                 premium_stop_pct=stop, status="PAUSED", created_by=user_id))
            await session.commit()
    _run(deploy(member["user_id"], "NIFTY", 5, 4, 70.0, "theirs"))            # a teammate's settings are not the caller's
    assert client.get("/api/paper/fno/basket/settings", headers=t["headers"], params={"underlying": "NIFTY"}).json()["source"] is None
    _run(deploy(t["user_id"], "BANKNIFTY", 3, 2, None, "other_symbol"))
    _run(deploy(t["user_id"], "NIFTY 50", 4, 2, 60.0, "mine"))
    out = client.get("/api/paper/fno/basket/settings", headers=t["headers"], params={"underlying": "NIFTY"}).json()
    assert out["source"] == "mine on NIFTY 50" and out["width_steps"] == 4 and out["lots"] == 2 and out["stop_pct"] == 60.0
    out = client.get("/api/paper/fno/basket/settings", headers=t["headers"], params={"underlying": "FINNIFTY"}).json()
    assert out["source"] is not None                                          # their latest options settings when none on it


def test_broker_link_says_why_not_without_the_flag(condor):
    expiry, base, legs = condor
    t = _org_tenant("link")
    out = _post(t, "broker-link", {"legs": _condor_legs(expiry, legs)})
    assert out["available"] is False and "not turned on" in out["why"]
