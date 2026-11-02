"""M77 SX-6: study sections 7 (trade plan), 8 (pre-trade check card), 9 (scenarios), 10 (similar past setups).

- trade plan: his legs as references + his planned prices; every number from the Options Builder engine at the study's own
  reference price (payoff, max gain / loss, break-evens, margin ESTIMATE labelled); no plan -> "Not available";
- scenarios: P&L at both edges of the next areas (each with its own timeframe) and at his saved what-if prices;
- check card: kept only with the server's valid signature (his own, this underlying, not after the study time), with the
  lines he accepted and when; a forged / another member's card is left out and said so;
- similar setups: setup recall with the plan as the subject, each holding on its own, closed strictly before the study;
- PDF and CSV print every section; the words stay clean (no call words); same inputs -> same bytes; the route reads it.
"""
import asyncio
import csv
import io
import json
import re
from datetime import UTC, datetime, timedelta

import pytest

from app.paper import fno_basket, fno_ticket
from app.paper import pretrade_card as PC
from app.paper import pretrade_warnings as pw
from app.signals.language import is_clean
from app.study import csv_export as CSV
from app.study import pages as P
from app.study import pdf as PDF
from app.study import plan_sections as PS
from app.study import snapshot as S
from tests.test_m76_mp2_pretrade_checks import _body as _mp2_body
from tests.test_m76_mp2_pretrade_checks import _checks, on  # noqa: F401 - the M76 check card fixture
from tests.test_m77_sx2_studies import BODY, CUT, _flags, _inputs, _member, client, served  # noqa: F401 - the app client and the fixture

ADVICE = re.compile(r"\b(buy|sell|target|stop[- ]loss|entry|recommend\w*|profit)\b", re.I)
EXPIRY = (CUT + timedelta(days=9)).date()
LOT = 15


def _contract(strike, right):
    return fno_ticket.Contract(underlying="BANKNIFTY", tradingsymbol=f"BANKNIFTY{int(strike or 0)}{right}", exchange="NFO",
                               instrument_key=f"NSE_FO|{int(strike or 0)}{right}", expiry=EXPIRY, strike=strike, right=right,
                               lot_size=LOT, tick_size=0.05)


def _resolved(legs=(("BUY", 46900.0, "CE", 2, 210.0), ("SELL", 46600.0, "PE", 1, 260.0)), **over):
    asks = [fno_basket.LegAsk(underlying="BANKNIFTY", expiry=EXPIRY, strike=k, right=r, side=side, lots=n, price=p) for side, k, r, n, p in legs]
    contracts = [_contract(a.strike, a.right) for a in asks]
    out = {"kind": "OPTIONS", "underlying": "BANKNIFTY", "exits": PS.exits_part({"stop_kind": "POINTS", "stop": 40.0, "time_at": "15:10"}),
           "triggers": [], "triggers_reason": "no trigger plan of yours was waiting on this underlying",
           "legs": [{"label": c.label, "side": a.side, "right": a.right, "strike": a.strike, "expiry": a.expiry.isoformat(), "lots": a.lots,
                     "lot_size": c.lot_size, "price": a.price, "quantity": a.lots * c.lot_size} for a, c in zip(asks, contracts)],
           "builder_legs": [fno_basket._builder_leg(a, c.lot_size, a.price) for a, c in zip(asks, contracts)],
           "rate": 0.065, "rate_source": "platform", "subject_side": "LONG"}
    out.update(over)
    return out


WHAT_IF = [{"name": "Gap up", "spot_move": 300.0, "iv_change": -2.0, "days": 1}, {"name": "IV only", "spot_move": None, "iv_change": 3.0, "days": None}]


def _snap(**over):
    base = {"trade_plan": _resolved(), "what_if": WHAT_IF}
    base.update(over)
    return S.assemble(_inputs(**base))


def _text(snap) -> list[str]:
    return P.all_text(P.render(snap, S.fingerprint(S.canonical(snap))))


# ---------------------------------------------------------------------------------------------------------------------
# Trade plan + scenarios (pure)
# ---------------------------------------------------------------------------------------------------------------------
def test_plan_numbers_are_the_engines_at_the_studys_own_price():
    snap = _snap()
    plan = snap["data"]["trade_plan"]
    assert plan["spot"]["value"] == 46585.26 and plan["spot"]["source"] == "last close (15-minute)"
    assert plan["engine_reason"] is None and plan["legs"][0]["iv"] and plan["legs"][0]["iv_source"] == "solved from the premium"
    want = fno_basket._compute([dict(x) for x in _resolved()["builder_legs"]], 46585.26, CUT, 0.065)
    assert plan["greeks"]["delta"] == pytest.approx(want["summary"]["greeks"]["delta"])
    assert plan["unbounded_profit"] is True and plan["max_profit"] is None and plan["unbounded_loss"] is False
    assert plan["max_loss"] == pytest.approx(-(46600 - 260 + 2 * 210) * LOT)          # at a price of 0
    assert plan["breakevens"] and all(b > 0 for b in plan["breakevens"])
    assert plan["margin"]["source"] == "estimate" and plan["margin"]["words"] == PS.MARGIN_SOURCE
    assert plan["net_premium"] == pytest.approx(260 * LOT - 2 * 210 * LOT)
    assert len(plan["payoff"]["prices"]) <= PS.CURVE_POINTS + 1 and len(plan["payoff"]["expiry"]) == len(plan["payoff"]["prices"])
    assert [x["name"] for x in plan["exits"]] == ["Exit if wrong", "Close at a time"]
    assert S.canonical(_snap()) == S.canonical(snap)                                   # same inputs -> same bytes


def test_scenarios_at_both_edges_of_the_next_areas_and_at_his_what_if_prices():
    snap = _snap()
    rows = snap["data"]["scenarios"]["rows"]
    areas = {a["label"]: a for a in snap["data"]["areas"]["rows"]}
    names = [r["name"] for r in rows]
    assert names[:4] == ["Next area above S1 (15-minute), lower edge", "Next area above S1 (15-minute), upper edge",
                         "Next area below B1 (15-minute), lower edge", "Next area below B1 (15-minute), upper edge"]
    assert rows[0]["price"] == areas["S1"]["low"] and rows[3]["price"] == areas["B1"]["high"]
    assert [r["name"] for r in rows[4:]] == ["Your what-if: Gap up", "Your what-if: IV only"]
    assert rows[4]["price"] == pytest.approx(46885.26) and rows[5]["price"] == 46585.26
    curve = fno_basket._compute([dict(x) for x in _resolved()["builder_legs"]], 46585.26, CUT, 0.065)
    from app.options_builder.payoff import compute_strategy_payoff_curve
    assert rows[0]["pnl_expiry"] == round(compute_strategy_payoff_curve(_resolved()["builder_legs"], [rows[0]["price"]])[0], 2)
    assert all(isinstance(r["pnl_today"], float) for r in rows) and curve["legs"]
    assert snap["data"]["scenarios"]["margin"] == snap["data"]["trade_plan"]["margin"]


def test_no_plan_or_an_unreadable_plan_says_not_available():
    snap = S.assemble(_inputs())
    assert snap["not_available"]["trade_plan"] == PS.NO_PLAN and snap["not_available"]["scenarios"] == PS.NO_SCENARIO_PLAN
    bad = _snap(trade_plan={"reason": "no planned price for BANKNIFTY 02 APR 46900 CE"})
    assert bad["not_available"]["trade_plan"] == "no planned price for BANKNIFTY 02 APR 46900 CE"
    assert bad["not_available"]["scenarios"].endswith("so there are no scenarios")
    none = _snap(what_if=[], zones=S.ZoneRead(reason=S.AREAS_OFF))
    assert none["not_available"]["scenarios"] == PS.NO_LEVELS and "trade_plan" in none["data"]


def test_no_reference_price_keeps_the_plan_but_says_why_the_model_numbers_are_missing():
    import pandas as pd

    from app.charts import price_action_layer as pal
    empty = pd.DataFrame(columns=pal.FRAME_COLS)
    snap = _snap(frames={"15m": (empty, None), "1h": (empty, None)}, what_if=[])
    plan = snap["data"]["trade_plan"]
    assert plan["engine_reason"] == PS.NO_PRICE and plan["payoff"] is None and plan["greeks"] is None
    assert plan["breakevens"] and plan["margin"]["basis"]                              # the expiry payoff needs no price


def test_a_futures_plan_and_an_equity_plan():
    fut = _snap(trade_plan=_resolved(legs=(("SELL", None, "FUT", 1, 46700.0),)))
    plan = fut["data"]["trade_plan"]
    assert plan["breakevens"] == [46700.0] and plan["margin"]["value"] is None and "broker" in plan["margin"]["basis"]
    assert plan["engine_reason"] == PS.FUTURES_ONLY and fut["data"]["scenarios"]["today_reason"] == PS.FUTURES_ONLY
    rows = fut["data"]["scenarios"]["rows"]
    assert rows[0]["pnl_expiry"] == pytest.approx(round((46700.0 - rows[0]["price"]) * LOT, 2))
    eq = _snap(trade_plan={"kind": "EQUITY", "underlying": "NIFTY BANK", "equity": {"side": "BUY", "quantity": 10, "price": 46500.0},
                           "exits": [], "triggers": [], "triggers_reason": "none"})
    plan = eq["data"]["trade_plan"]
    assert plan["order_value"] == 465000.0 and plan["margin"]["basis"] == PS.EQUITY_MARGIN
    assert eq["data"]["scenarios"]["rows"][0]["pnl_today"] == pytest.approx(round((eq["data"]["scenarios"]["rows"][0]["price"] - 46500) * 10, 2))


def test_exits_and_trigger_plans_are_his_own_as_he_set_them():
    assert PS.exits_part(None) == []
    got = PS.exits_part({"stop_kind": "PCT", "stop": 1.5, "target_kind": None, "target": 90.0, "trail_kind": "ATR", "trail": 2.0,
                         "time_minutes": 45, "combined": "MTM_INR"})
    assert [(x["name"], x.get("value"), x.get("measure")) for x in got] == [
        ("Exit if wrong", 1.5, "%"), ("Exit at gain", 90.0, None), ("Trailing exit", 2.0, "ATR"), ("Close after", 45.0, "minutes"),
        ("Combined exit measured on", None, None)]
    t = PS.trigger_part({"name": "Break", "status_words": "Armed", "condition": {"level": 46800.0, "side": "ABOVE", "mode": "CLOSE",
                                                                                 "timeframe_minutes": 15}, "orders": [{}]})
    assert (t["level"], t["side"], t["by"], t["timeframe_minutes"], t["orders"]) == (46800.0, "above", "a closed candle", 15, 1)


# ---------------------------------------------------------------------------------------------------------------------
# Pre-trade check card (signed)
# ---------------------------------------------------------------------------------------------------------------------
class U:
    def __init__(self, uid=7, tid=3):
        self.id, self.tenant_id = uid, tid


INST = pw.instrument_key("BANKNIFTY", EXPIRY.isoformat(), 46900.0, "CE")
FINDINGS = [{"id": "event", "severity": "fact", "text": "RBI policy before expiry.", "facts": {"days": 2.0, "level": 46900.0}},
            {"id": "duplicate_order", "severity": "mistake", "text": "You already hold this contract on the same side.", "facts": {}}]


def _card(user=None, findings=FINDINGS, at=None):
    at = at or (CUT - timedelta(minutes=5)).isoformat()
    sig = PC.sign(user or U(), INST, findings, at)
    return json.loads(json.dumps({"instrument": INST, "findings": findings, "checked_at": at, "signature": sig}))   # as the browser sends it


def test_signature_survives_the_browser_and_refuses_a_change():
    card = _card()
    assert card["findings"][0]["facts"]["level"] == 46900 and PC.verify(U(), card)      # 46900.0 -> 46900 in JSON: still verifies
    assert not PC.verify(U(8), card) and not PC.verify(U(7, 4), card)                   # another member / organisation
    forged = {**card, "findings": [{**card["findings"][0], "text": "Nothing to see."}, card["findings"][1]]}
    assert not PC.verify(U(), forged) and not PC.verify(U(), {**card, "signature": "0" * 64}) and not PC.verify(U(), {"instrument": INST})


def test_check_card_read_keeps_only_his_signed_cards_with_what_he_accepted(monkeypatch):
    async def times(user, instrument):
        return {"event": (CUT - timedelta(minutes=2)).isoformat(), "fo_ban": None}

    monkeypatch.setattr(pw, "accepted_times", times)
    got = asyncio.run(PS.check_card_read(U(), "NIFTY BANK", [_card(), _card(U(9))], CUT))
    [card] = got["cards"]
    assert card["contract"] == fno_ticket.contract_label("BANKNIFTY", EXPIRY, 46900.0, "CE")
    assert [(f["id"], f["accepted"], f["accepted_at"]) for f in card["findings"]] == [
        ("event", True, (CUT - timedelta(minutes=2)).isoformat()), ("duplicate_order", False, None)]
    assert [a["id"] for a in card["accepted"]] == ["event", "fo_ban"] and card["findings"][0]["name"] == pw.LABELS["event"]
    assert asyncio.run(PS.check_card_read(U(), "NIFTY BANK", None, CUT)) == {"reason": PS.NO_CARD}
    assert asyncio.run(PS.check_card_read(U(), "NIFTY BANK", [_card(U(9))], CUT)) == {"reason": PS.CARD_UNVERIFIED}
    later = _card(at=(CUT + timedelta(minutes=1)).isoformat())                          # checked after the study: no look-ahead
    assert asyncio.run(PS.check_card_read(U(), "NIFTY BANK", [later], CUT)) == {"reason": PS.CARD_UNVERIFIED}
    assert asyncio.run(PS.check_card_read(U(), "RELIANCE", [_card()], CUT)) == {"reason": PS.CARD_UNVERIFIED}   # another underlying


def test_accepted_times_reads_the_time_place_was_pressed(monkeypatch):
    store = {}

    class R:
        async def set(self, key, value, ex=None):
            store[key] = value.encode()

        async def mget(self, keys):
            return [store.get(k) for k in keys]

    from app.cache import client as cache
    monkeypatch.setattr(cache, "_get_client", lambda: R())
    when = datetime(2026, 3, 24, 4, 1, 2, 900, tzinfo=UTC)
    assert asyncio.run(pw.accept(U(), ["event"], INST, now=when)) == ["event"]
    store[pw._key(U(), "fo_ban", INST)] = b"1"                                          # a record from before the time was kept
    assert asyncio.run(pw.accepted_times(U(), INST)) == {"event": "2026-03-24T04:01:02+00:00", "fo_ban": None}
    assert asyncio.run(pw.accepted(U(), INST)) == {"event", "fo_ban"}


# ---------------------------------------------------------------------------------------------------------------------
# Similar past setups
# ---------------------------------------------------------------------------------------------------------------------
def test_similar_setups_use_recall_unchanged_each_holding_on_its_own(monkeypatch):
    from app.setup_recall import service as recall
    seen = {}

    async def closed(session, user, setup, cutoff):
        seen["setup"], seen["cutoff"] = setup, cutoff
        return []

    async def tfs(session, tenant_id, ids):
        return {}

    def units(trades, timeframes):
        ctx = recall.Context(setup=fno_ticket.STRATEGY_ID, side="LONG", holding="SWING", timeframe=None)
        mk = lambda i, closed_at, pnl: recall.Unit(trade_id=i, trade_ids=(i,), context=ctx, entered_at=closed_at - timedelta(days=1),
                                                    closed_at=closed_at, pnl=pnl, risk=100.0, exit_reason="target hit", mode="PAPER")
        return [mk(1, CUT - timedelta(days=3), 250.0), mk(2, CUT - timedelta(days=2), -100.0), mk(3, CUT, 50.0)]   # 3: not strictly before

    monkeypatch.setattr(recall, "_closed_before", closed)
    monkeypatch.setattr(recall, "_timeframes", tfs)
    monkeypatch.setattr(recall, "build_units", units)
    got = asyncio.run(PS.similar_read(None, U(), _resolved(), CUT))
    assert seen == {"setup": fno_ticket.STRATEGY_ID, "cutoff": CUT}
    intraday, swing = got["groups"]
    assert intraday["holding"] == "INTRADAY" and intraday["count"] == 0
    assert swing["count"] == 2 and [m["r"] for m in swing["matches"]] == [-1.0, 2.5]
    assert all(is_clean(m["how_closed"]) for m in swing["matches"]) and "trade_id" not in swing["matches"][0]
    assert asyncio.run(PS.similar_read(None, U(), None, CUT)) == {"reason": PS.NO_SETUP_PLAN}
    assert asyncio.run(PS.similar_read(None, U(), {"kind": "EQUITY"}, CUT)) == {"reason": PS.EQUITY_SETUP}


# ---------------------------------------------------------------------------------------------------------------------
# PDF / CSV
# ---------------------------------------------------------------------------------------------------------------------
CARD_PART = PS.card_part([_card()], {INST: {"event": (CUT - timedelta(minutes=2)).isoformat()}})
SIMILAR = {"subject": {"setup_words": "Hand order from the option / futures ticket", "side": "long", "timeframe": "not recorded"},
           "as_of": CUT.isoformat(), "groups": [{"holding": "SWING", "holding_words": "Carry forward (NRML)", "line": "No similar closed trades of yours yet.",
                                                 "count": 0, "min_sample": 5, "enough": False, "wins": None, "losses": None, "flat": None,
                                                 "avg_r": None, "matches": []}]}


def test_pdf_prints_sections_7_to_10_in_clean_words():
    snap = _snap(pre_trade_check=CARD_PART, similar_setups=SIMILAR)
    texts = _text(snap)
    joined = " ".join(texts)
    for marker in ("Trade plan", "Max gain", "Margin (estimate, with the hedge)", "Your exits, as set in the plan", "Exit if wrong",
                   "Pre-trade check", "RBI policy before expiry.", "You accepted", "Scenarios", "Your what-if: Gap up",
                   "Similar past setups", "Carry forward (NRML): No similar closed trades of yours yet."):
        assert marker in joined, marker
    assert not [t for t in texts if ADVICE.search(t) and "You already hold" not in t]
    assert all(is_clean(t) for t in texts if "You already hold" not in t)
    assert PDF.study_pdf(snap) == PDF.study_pdf(_snap(pre_trade_check=CARD_PART, similar_setups=SIMILAR))


@pytest.mark.parametrize("section", ["trade_plan", "pre_trade_check", "scenarios", "similar_setups"])
def test_each_section_on_and_off(section):
    full = [s for s in S.SECTIONS]
    snap = _snap(sections=[s for s in full if s != section], pre_trade_check=CARD_PART, similar_setups=SIMILAR)
    assert section not in snap["data"] and section not in snap["not_available"]
    assert S.SECTION_TITLES[section] not in _text(snap) and PDF.study_pdf(snap)
    only = _snap(sections=[section], pre_trade_check=CARD_PART, similar_setups=SIMILAR)
    assert section in only["data"] and S.SECTION_TITLES[section] in _text(only)


def test_csv_tables_for_the_new_sections():
    snap = _snap(pre_trade_check=CARD_PART, similar_setups=SIMILAR)
    t = CSV.tables(snap)
    assert {"trade_plan", "scenarios", "pre_trade_check", "similar_setups"} <= set(t)
    plan = list(csv.reader(io.StringIO(t["trade_plan"].decode())))
    assert plan[0] == ["part", "leg", "item", "value"] and ["number", "", "margin_basis", snap["data"]["trade_plan"]["margin"]["basis"]] in plan
    assert any(r[2] == "side" and r[3] == "long" for r in plan) and not any("BUY" in v or "SELL" in v for r in plan for v in r)
    scen = list(csv.reader(io.StringIO(t["scenarios"].decode())))
    assert scen[0][:4] == ["scenario", "price", "pnl_now", "pnl_at_expiry"] and len(scen) == 7 and "," not in scen[1][1]
    check = list(csv.reader(io.StringIO(t["pre_trade_check"].decode())))
    assert check[1][4] == "yes" and check[2][4] == "no" and check[1][5].endswith("+05:30")
    assert CSV.zip_all(snap, "s") == CSV.zip_all(_snap(pre_trade_check=CARD_PART, similar_setups=SIMILAR), "s")


def test_chosen_strikes_come_from_the_chain_for_the_plans_legs():
    from app.study import sections as SEC
    from tests.test_m77_sx5_sections_csv import VIEW
    view = {**VIEW, "expiry": EXPIRY.isoformat()}
    legs = _resolved(legs=(("BUY", 46700.0, "CE", 1, 200.0), ("SELL", 46000.0, "PE", 1, 90.0)))["legs"]
    oc = SEC.option_context_part(view, legs)
    assert [(c["strike"], c["right"], c["oi"], c["in_chain"]) for c in oc["chosen_strikes"]] == [(46700.0, "CE", 1500.0, True), (46000.0, "PE", None, False)]
    assert SEC.option_context_part(VIEW, legs)["chosen_reason"].startswith("the trade plan's option legs are on another expiry")
    assert SEC.option_context_part(view)["chosen_reason"] == SEC.NO_PLAN_STRIKES
    assert SEC.option_context_part(view, _resolved(legs=(("SELL", None, "FUT", 1, 46700.0),))["legs"])["chosen_reason"] == SEC.NO_OPTION_LEGS


# ---------------------------------------------------------------------------------------------------------------------
# The reads and the route
# ---------------------------------------------------------------------------------------------------------------------
def _fake_master(monkeypatch):
    async def contracts(session, legs):
        return [_contract(leg.strike, leg.right) for leg in legs], []

    class Own:
        risk_free_rate = None

    async def settings(session, user):
        return Own()

    async def triggers(session, user, symbol, when):
        return [], "trigger plans are not available on this platform yet"

    from app.option_chain import chain_view
    monkeypatch.setattr(fno_basket, "_contracts", contracts)
    monkeypatch.setattr(chain_view, "options_settings", settings)
    monkeypatch.setattr(PS, "_triggers", triggers)


PLAN = {"kind": "OPTIONS", "legs": [{"expiry": EXPIRY.isoformat(), "strike": 46900, "right": "CE", "side": "BUY", "lots": 2, "price": 210},
                                    {"expiry": EXPIRY.isoformat(), "strike": 46600, "right": "PE", "side": "SELL", "lots": 1, "price": 260,
                                     "lot_size": 999, "iv": 0.5}],
        "exits": {"stop_kind": "POINTS", "stop": 40}, "max_loss": 1, "spot": 1}


def test_resolve_plan_reads_the_contracts_and_refuses_missing_inputs(monkeypatch):
    _fake_master(monkeypatch)
    from app.study.routes import PlanRef
    ref = PlanRef.model_validate(PLAN).model_dump()
    got = asyncio.run(PS.resolve_plan(None, U(), "NSE", "NIFTY BANK", ref, CUT))
    assert got["underlying"] == "BANKNIFTY" and [leg["lot_size"] for leg in got["legs"]] == [LOT, LOT]       # never his 999
    assert all(leg.get("iv") is None for leg in got["builder_legs"]) and got["subject_side"] == "LONG"
    assert asyncio.run(PS.resolve_plan(None, U(), "NSE", "NIFTY BANK", None, CUT)) is None
    assert asyncio.run(PS.resolve_plan(None, U(), "NFO", "BANKNIFTY26MARFUT", ref, CUT)) == {"reason": PS.CONTRACT_CHART}
    assert asyncio.run(PS.resolve_plan(None, U(), "NSE", "NIFTY BANK", {"kind": "OPTIONS", "legs": []}, CUT)) == {"reason": PS.NO_LEGS}
    no_price = {**ref, "legs": [{**ref["legs"][0], "price": None}]}
    assert asyncio.run(PS.resolve_plan(None, U(), "NSE", "NIFTY BANK", no_price, CUT))["reason"].startswith("no planned price for BANKNIFTY")
    assert asyncio.run(PS.resolve_plan(None, U(), "NSE", "NIFTY BANK", {"kind": "EQUITY", "equity": {"side": "BUY"}}, CUT)) == {"reason": PS.NO_EQUITY}


def test_route_saves_the_plan_sections_from_references(served, monkeypatch):  # noqa: F811 - the fixture
    _fake_master(monkeypatch)
    headers, _, _ = _member("sx6")
    _flags(ui_v2=True, study_export=True)
    r = client.post("/api/studies", json={**BODY, "plan": PLAN, "check_cards": [{"instrument": INST, "findings": [], "checked_at": "x",
                                                                                  "signature": "f" * 64}]}, headers=headers)
    assert r.status_code == 201, r.text
    snap = client.get(f"/api/studies/{r.json()['id']}", headers=headers).json()["snapshot"]
    plan = snap["data"]["trade_plan"]
    assert [leg["lot_size"] for leg in plan["legs"]] == [LOT, LOT] and plan["margin"]["source"] == "estimate"
    assert "max_loss" in plan and plan["max_loss"] != 1 and snap["data"]["scenarios"]["rows"]
    assert snap["not_available"]["pre_trade_check"] == PS.CARD_UNVERIFIED
    assert "similar_setups" in snap["data"] and "trade_plan" not in snap["not_available"]
    assert client.get(f"/api/studies/{r.json()['id']}/csv?table=trade_plan", headers=headers).status_code == 200
    plain = client.post("/api/studies", json=BODY, headers=headers).json()
    snap = client.get(f"/api/studies/{plain['id']}", headers=headers).json()["snapshot"]
    assert snap["not_available"]["trade_plan"] == PS.NO_PLAN and snap["not_available"]["pre_trade_check"] == PS.NO_CARD


def test_checks_route_signs_its_answer_and_the_card_verifies_for_him_only(on):  # noqa: F811 - the M76 fixture
    from sqlalchemy import select

    from app.db.models import User
    from tests.test_auth_api import _session_factory
    t, expiry, strike = on
    out = _checks(t, _mp2_body(expiry, strike))
    card = json.loads(json.dumps({"instrument": out["instrument"], "findings": out["findings"], "checked_at": out["checked_at"],
                                  "signature": out["signature"]}))

    async def member():
        async with _session_factory() as session:
            return (await session.scalars(select(User).where(User.tenant_id == t["tenant_id"]))).first()

    me = asyncio.run(member())
    assert datetime.fromisoformat(out["checked_at"]).tzinfo is not None
    assert PC.verify(me, card) and not PC.verify(U(me.id + 1, me.tenant_id), card)
