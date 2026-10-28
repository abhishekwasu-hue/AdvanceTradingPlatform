"""DEF-0 PR 3 (owner order, 28 Oct): D8 - the ticket shows the broker's own margin for the order as an estimate (PAPER
too), else one honest line; never a "-" beside "comes with the order ticket"."""
import uuid

from app.paper import fno_ticket
from tests.test_fno1_chain_ticket import _body, _contract, _expiry, _preview
from tests.test_trading_worker import _tenant
from tests.test_z3_approval_entries import _price
from tests.test_z3b_paper_account import _paper_flag
from tests.test_z5_p1d_risk_by_default import _flag


def _fresh(monkeypatch):
    t = _tenant(f"def0c-{uuid.uuid4().hex[:6]}@example.com")
    _flag("ui_v2", t["tenant_id"])
    _paper_flag(t["tenant_id"])
    expiry = _expiry()
    _contract(expiry, 22700.0)
    _price(monkeypatch, 131.5)
    return t, expiry


def test_the_brokers_margin_is_shown_as_an_estimate(monkeypatch):
    t, expiry = _fresh(monkeypatch)

    async def answer(_session, _user, _contract, side, units, product):
        assert side == "SELL" and units > 0 and product == "NRML"
        return 118_250.0, "upstox"
    monkeypatch.setattr(fno_ticket, "read_broker_margin", answer)
    m = _preview(t, _body(expiry, 22700.0, side="SELL", product="NRML"))["margin"]
    assert m["value"] == 118_250.0 and m["estimated"] is True and m["source"] == "broker" and "upstox" in m["basis"]


def test_without_a_broker_answer_one_honest_line(monkeypatch):
    t, expiry = _fresh(monkeypatch)
    m = _preview(t, _body(expiry, 22700.0, side="SELL", product="NRML"))["margin"]
    assert m["value"] is None and m["basis"] == fno_ticket.NO_MARGIN
    assert not (m.get("note") or "").count("comes with the order ticket")
    bought = _preview(t, _body(expiry, 22700.0, side="BUY", product="NRML"))["margin"]
    assert bought["value"] is not None and bought["source"] == "estimate"            # the Builder's labelled estimate
