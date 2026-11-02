"""M76 MP-5 (W-4): the check-card lines he pressed Place with go on the journal entry (which type, the time of Place),
and the weekly review counts them with the outcomes - numbers only, no fix line. The order itself is unchanged: the ids
are never read by a check, an unknown id is dropped (never a refusal), and nothing is recorded while `trade_warnings`
is off."""
import asyncio
import json
import uuid
from datetime import UTC, date, datetime

from sqlalchemy import select

from app.db.models import JournalEntryRecord, TradeRecord
from app.journal import capture, service
from app.journal.review import ReviewTrade, accepted_summary, advice_in, build_review, lines
from app.journal.settings import DisciplineSettings
from app.paper import fno_ticket
from app.paper import pretrade_warnings as pw
from tests.test_auth_api import _session_factory, client
from tests.test_fno1_chain_ticket import _body, _contract, _expiry, _org_tenant, _place
from tests.test_fno2_basket import _condor_legs, _post, condor  # noqa: F401 - the fixture
from tests.test_fno2_basket import _org_tenant as _basket_tenant
from tests.test_z3_approval_entries import _price
from tests.test_z5_p1d_risk_by_default import _flag


def _entry(trade_id: int) -> JournalEntryRecord | None:
    async def go():
        async with _session_factory() as session:
            return await session.scalar(select(JournalEntryRecord).where(JournalEntryRecord.trade_id == trade_id))
    return asyncio.run(go())


def _plan(trade_id: int) -> dict:
    e = _entry(trade_id)
    assert e is not None
    return json.loads(e.plan_json or "{}")


def _ticket(monkeypatch, *, warnings=True, journal=True):
    t = _org_tenant(f"m76-5-{uuid.uuid4().hex[:4]}")
    if warnings:
        _flag("trade_warnings", t["tenant_id"])
    if journal:
        _flag("discipline_engine", t["tenant_id"])
    capture.reset_flag_cache()
    expiry = _expiry()
    strike = float(30000 + 50 * (uuid.uuid4().int % 100))
    _contract(expiry, strike)
    _price(monkeypatch, 100.0)
    return t, expiry, strike


def test_accepted_types_go_on_the_entry_with_the_time_of_place(monkeypatch):
    t, expiry, strike = _ticket(monkeypatch)
    before = datetime.now(UTC)
    out = _place(t, _body(expiry, strike, accepted_warnings=["duplicate_order", "not_a_type", "margin_short", "duplicate_order"]))
    assert out["status"] == "PLACED" and out["trade_id"], out
    accepted = _plan(out["trade_id"])["accepted_warnings"]
    assert [a["id"] for a in accepted] == ["duplicate_order", "margin_short"]       # known ids, each once, in his order
    assert all(before <= datetime.fromisoformat(a["at"]) <= datetime.now(UTC) for a in accepted)
    week = client.get("/api/journal/week", headers=t["headers"]).json()
    entry = next(e for e in week["entries"] if e["trade_id"] == out["trade_id"])
    assert [(a["id"], a["label"]) for a in entry["plan_view"]["accepted_warnings"]] == [
        ("duplicate_order", pw.LABELS["duplicate_order"]), ("margin_short", pw.LABELS["margin_short"])]
    rows = {a["id"]: a for a in week["accepted_warnings"]}
    assert rows["duplicate_order"] == {"id": "duplicate_order", "label": pw.LABELS["duplicate_order"], "trades": 1, "closed": 0,
                                       "wins": 0, "losses": 0, "net_pnl": 0}


def test_the_order_is_the_same_with_or_without_the_ids(monkeypatch):
    t, expiry, strike = _ticket(monkeypatch)
    plain = _place(t, _body(expiry, strike))
    marked = _place(t, _body(expiry, strike, accepted_warnings=["duplicate_order"]))
    assert plain["status"] == marked["status"] == "PLACED"
    assert marked["filled"] == plain["filled"] and set(marked) == set(plain)
    assert "accepted_warnings" not in _plan(plain["trade_id"])                  # nothing sent: nothing recorded


def test_unknown_ids_only_never_refuse_and_record_nothing(monkeypatch):
    t, expiry, strike = _ticket(monkeypatch)
    out = _place(t, _body(expiry, strike, accepted_warnings=["renamed_later", "x"]))
    assert out["status"] == "PLACED"
    assert "accepted_warnings" not in _plan(out["trade_id"])


def test_nothing_is_recorded_while_trade_warnings_is_off(monkeypatch):
    t, expiry, strike = _ticket(monkeypatch, warnings=False)
    out = _place(t, _body(expiry, strike, accepted_warnings=["duplicate_order"]))
    assert out["status"] == "PLACED"
    assert "accepted_warnings" not in _plan(out["trade_id"])
    assert client.get("/api/journal/week", headers=t["headers"]).json()["accepted_warnings"] == []


def test_without_the_journal_flag_the_order_still_places(monkeypatch):
    t, expiry, strike = _ticket(monkeypatch, journal=False)
    out = _place(t, _body(expiry, strike, accepted_warnings=["duplicate_order"]))
    assert out["status"] == "PLACED" and _entry(out["trade_id"]) is None


def test_a_waiting_order_keeps_the_time_place_was_pressed():
    ask = fno_ticket.OrderAsk(underlying="NIFTY", expiry=date(2030, 1, 31), strike=30000.0, right="CE", side="BUY", lots=1,
                              accepted_warnings=("duplicate_order",), accepted_at="2030-01-02T04:00:00+00:00")
    back = fno_ticket.ask_from_payload(json.loads(json.dumps(fno_ticket.ask_payload(ask))))
    assert back.accepted_warnings == ("duplicate_order",) and back.accepted_at == ask.accepted_at
    old = {k: v for k, v in fno_ticket.ask_payload(ask).items() if k not in ("accepted_warnings", "accepted_at")}
    legacy = fno_ticket.ask_from_payload(old)                               # a payload kept before MP-5
    assert legacy.accepted_warnings == () and legacy.accepted_at is None


def test_basket_legs_that_filled_carry_the_accepted_types(condor):  # noqa: F811 - the fixture
    expiry, _base, legs = condor
    t = _basket_tenant(f"m76-5b-{uuid.uuid4().hex[:4]}")
    _flag("trade_warnings", t["tenant_id"])
    _flag("discipline_engine", t["tenant_id"])
    capture.reset_flag_cache()
    out = _post(t, "execute", {"legs": _condor_legs(expiry, legs), "basket_ref": uuid.uuid4().hex, "product": "NRML",
                               "accepted_warnings": ["expiry_day"]})
    assert out["status"] == "PLACED" and out["placed"] == 4, out
    for row in out["legs"]:
        assert [a["id"] for a in _plan(row["trade_id"])["accepted_warnings"]] == ["expiry_day"]
    week = client.get("/api/journal/week", headers=t["headers"]).json()
    ids = [row["trade_id"] for row in out["legs"]]

    async def units():
        async with _session_factory() as session:
            rows = list(await session.scalars(select(TradeRecord).where(TradeRecord.id.in_(ids))))
            return len(service.review_trades(rows))
    # counted per decision, the unit the discipline score uses (a grouped structure = one, else one per leg)
    assert [(a["id"], a["trades"]) for a in week["accepted_warnings"]] == [("expiry_day", asyncio.run(units()))]


def test_merge_is_idempotent_and_keeps_the_first_time():
    plan = capture.merge_accepted({"entry": 1.0}, [{"id": "event", "at": "t1"}])
    again = capture.merge_accepted(plan, [{"id": "event", "at": "t2"}, {"id": "fo_ban", "at": "t2"}])
    assert again["entry"] == 1.0 and again["accepted_warnings"] == [{"id": "event", "at": "t1"}, {"id": "fo_ban", "at": "t2"}]
    assert capture.merge_accepted(again, [{"id": "event", "at": "t3"}]) == again


def _rt(i, pnl, legs=()):
    return ReviewTrade(id=i, setup="s", unit=f"t:{i}", pnl=pnl, charges=0.0, risk=0.0, holding_minutes=None, trade_ids=tuple(legs))


def test_the_weekly_numbers_and_lines_describe_only():
    trades = [_rt(1, 500.0), _rt(2, -300.0), _rt(3, None), _rt(10, -100.0, legs=(10, 11, 12))]
    accepted = [(1, "margin_short"), (2, "margin_short"), (3, "margin_short"), (10, "fo_ban"), (11, "fo_ban"), (99, "fo_ban")]
    rows = accepted_summary(trades, accepted, pw.LABELS)
    assert rows == [
        {"id": "margin_short", "label": pw.LABELS["margin_short"], "trades": 3, "closed": 2, "wins": 1, "losses": 1, "net_pnl": 200.0},
        {"id": "fo_ban", "label": pw.LABELS["fo_ban"], "trades": 1, "closed": 1, "wins": 0, "losses": 1, "net_pnl": -100.0},
    ]
    score = {"score": None, "components": [], "note": None}
    review = build_review("2030-W01", trades, [], score, [], DisciplineSettings(), accepted=rows)
    said = [s for s in lines(review) if s.startswith("Placed with the line")]
    assert said == [f'Placed with the line "{pw.LABELS["margin_short"]}" shown: 3 trades, 1 won and 1 lost of 2 closed, net ₹200.00.',
                    f'Placed with the line "{pw.LABELS["fo_ban"]}" shown: 1 trade, 0 won and 1 lost of 1 closed, net -₹100.00.']
    assert all(not advice_in(s) for s in said)
    assert not [s for s in lines(build_review("2030-W01", trades, [], score, [], DisciplineSettings())) if s.startswith("Placed")]


def test_clean_accepted():
    assert pw.clean_accepted(None) == () and pw.clean_accepted(["fo_ban", "fo_ban", "zz"]) == ("fo_ban",)
    assert set(pw.LABELS) == set(pw.WARNING_IDS)
