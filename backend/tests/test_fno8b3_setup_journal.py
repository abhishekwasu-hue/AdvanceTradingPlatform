"""F&O workspace 8b, slice 3 (owner order ATP_SESSION_FNO_WORKSPACE.md, sections 8b b, k and 9).

- "Trade this setup" (8b-b): the setup-strikes answer carries each leg's role in the structure (short / long) and the
  deployment's own lots and sold-leg premium stop (None when he set none), so the page opens the EXISTING PAPER ticket /
  basket pre-filled; the read places nothing, and the placing itself is the existing PAPER path (asserted at the
  Phase 0 chokepoint);
- the auto journal (8b-k): a closed PAPER F&O trade's journal entry is the draft, from facts only (fills, P&L, the exit
  as recorded) plus the structure words of HIS OWN signal it came from; a line with a value or a call word is left out;
  the link writes only the journal rows, never a trade or an order; a teammate's signal links nothing; another member
  sees none of it; exits stay unblocked;
- the routes: behind `ui_v2` + the PAPER account, login needed, the link needs a trading role.

Made-up data only: made-up underlyings, strikes, deployments, proposals and trades."""
import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from app.chart_analyze import signal_language
from app.db.models import AiActionRecord, JournalEntryRecord, OrderRecord, TradeRecord
from app.paper import fno_journal
from app.platform import phase0_lock
from tests.test_auth_api import _session_factory, client
from tests.test_fno2_basket import _condor_legs, _lock, _org_tenant, _post, condor  # noqa: F401 - the fixture is used by name
from tests.test_fno8b_insights import _deployment, _ladder, _org, _strikes, _underlying
from tests.test_trading_worker import _tenant
from tests.test_z3b_paper_account import _member
from tests.test_z5_p1d_risk_by_default import _flag

DRAFTS = "/api/paper/fno/journal/drafts"
LINK = "/api/paper/fno/journal/link"
WORDS = ["The pullback reached a seller area", "rsi: 41.2", "Buy above the level", "A handover is forming inside the area"]


def _run(coro):
    return asyncio.run(coro)


def _proposal(tenant_id: int, symbol: str, owner, *, reasons=WORDS) -> str:
    async def go():
        async with _session_factory() as session:
            evidence = {"symbol": symbol, "direction": "LONG", "proposal_price": 30000.0, "strategy_id": "made-up",
                        "signal": {"strategy_name": "Made-up setup", "reasons": reasons}}
            row = AiActionRecord(tenant_id=tenant_id, action="ENTRY", rule=f"made-up-{uuid.uuid4().hex[:8]}", reason="made up",
                                 evidence_json=json.dumps(evidence), status="PROPOSED", owner_user_id=owner,
                                 expires_at=datetime.now(timezone.utc) + timedelta(minutes=30))
            session.add(row)
            await session.commit()
            return f"action:{row.id}"
    return _run(go())


def _count(model, tenant_id: int) -> int:
    async def go():
        async with _session_factory() as session:
            return await session.scalar(select(func.count()).select_from(model).where(model.tenant_id == tenant_id))
    return _run(go())


def _trades(ids) -> list:
    async def go():
        async with _session_factory() as session:
            rows = await session.scalars(select(TradeRecord).where(TradeRecord.id.in_(list(ids))).order_by(TradeRecord.id))
            return [(r.id, r.exit_time, r.stop_loss, r.notes, r.quantity) for r in rows]
    return _run(go())


def _drafts(t) -> list:
    out = client.get(DRAFTS, headers=t["headers"])
    assert out.status_code == 200, out.text
    body = out.json()
    assert body["mode"] == "PAPER"
    return body["items"]


# --- the routes ----------------------------------------------------------------------------------------------------------

def test_the_journal_routes_are_behind_ui_v2_and_the_paper_account():
    t = _tenant(f"fno8b3-off-{uuid.uuid4().hex[:6]}@example.com")
    body = {"signal": "action:1", "trade_ids": [1]}
    for call in (lambda: client.get(DRAFTS, headers=t["headers"]), lambda: client.post(LINK, headers=t["headers"], json=body)):
        out = call()
        assert out.status_code == 404 and out.headers["X-Feature-Disabled"] == "ui_v2"
    _flag("ui_v2", t["tenant_id"])
    out = client.get(DRAFTS, headers=t["headers"])
    assert out.status_code == 404 and out.headers["X-Feature-Disabled"] == "paper_account"
    assert client.get(DRAFTS).status_code == 401 and client.post(LINK, json=body).status_code == 401
    ok = _org("journal-ok")
    assert client.post(LINK, headers=ok["headers"], json={"signal": "drop table", "trade_ids": [1]}).status_code == 422
    assert client.post(LINK, headers=ok["headers"], json={"signal": "action:1", "trade_ids": []}).status_code == 422
    assert client.post(LINK, headers=ok["headers"], json={**body, "side": "BUY"}).status_code == 422     # no order fields at all
    assert client.get(DRAFTS, headers=ok["headers"]).json() == {"mode": "PAPER", "days": fno_journal.DRAFT_DAYS, "items": []}


# --- 8b-b: the pre-fill comes from his own deployment -----------------------------------------------------------------

def test_setup_legs_carry_their_role_and_his_own_lots_and_stop_and_the_read_places_nothing():
    t = _org("prefill")
    sym = _underlying()
    _ladder(sym)
    dep = _deployment(t["tenant_id"], sym, created_by=t["user_id"])           # bull put spread written: main short, wing long

    async def settings():
        async with _session_factory() as session:
            from app.db.models import StrategyDeploymentRecord
            row = await session.get(StrategyDeploymentRecord, dep)
            row.max_lots, row.premium_stop_pct = 2, 40.0
            await session.commit()
    signal = _proposal(t["tenant_id"], sym, t["user_id"])

    async def attach():
        async with _session_factory() as session:
            row = await session.get(AiActionRecord, int(signal.split(":")[1]))
            row.deployment_id = dep
            await session.commit()
    _run(attach())
    [item] = _strikes(t, signal, spot=120.5)["items"]
    assert [(l["label"], l["role"]) for l in item["legs"]] == [("Main strike", "short"), ("Wing", "long")]
    assert item["lots"] is None and item["stop_pct"] is None                  # nothing set: nothing made up
    _run(settings())
    [item] = _strikes(t, signal, spot=120.5)["items"]
    assert item["lots"] == 2 and item["stop_pct"] == 40.0
    assert _count(OrderRecord, t["tenant_id"]) == 0 and _count(TradeRecord, t["tenant_id"]) == 0


# --- 8b-k: the auto journal ------------------------------------------------------------------------------------------------

def test_a_closed_basket_becomes_one_draft_from_facts_with_his_signals_structure_words(condor, monkeypatch):  # noqa: F811
    expiry, base, legs = condor
    t = _org_tenant("journal")
    member = _member(t)
    signal = _proposal(t["tenant_id"], "NIFTY", t["user_id"])
    theirs = _proposal(t["tenant_id"], "NIFTY", member["user_id"])
    seen: list = []
    real = phase0_lock.assert_paper

    def spy(*a, **kw):
        seen.append(str(kw.get("mode", a[0] if a else "")).upper())
        return real(*a, **kw)
    _lock(t["tenant_id"], True)
    try:
        # The pre-filled basket is placed by the EXISTING basket path, PAPER under the Phase 0 lock.
        monkeypatch.setattr(phase0_lock, "assert_paper", spy)
        placed = _post(t, "execute", {"legs": _condor_legs(expiry, legs), "basket_ref": uuid.uuid4().hex, "product": "NRML"})
        assert placed["placed"] == 4 and all(r["mode"] == "PAPER" for r in placed["legs"])
        ids = [r["trade_id"] for r in placed["legs"]]
        before = _trades(ids)
        orders = _count(OrderRecord, t["tenant_id"])

        # The link and the drafts never reach an order path.
        def no_order(*a, **kw):
            raise AssertionError("an order path was reached")
        monkeypatch.setattr(phase0_lock, "assert_paper", no_order)
        out = client.post(LINK, headers=t["headers"], json={"signal": theirs, "trade_ids": ids})
        assert out.status_code == 200 and out.json() == {"linked": [], "message": fno_journal.NOT_YOURS}
        out = client.post(LINK, headers=member["headers"], json={"signal": signal, "trade_ids": ids})
        assert out.status_code == 200 and out.json()["linked"] == []             # not his signal, not his trades
        out = client.post(LINK, headers=t["headers"], json={"signal": signal, "trade_ids": ids})
        assert out.status_code == 200 and sorted(out.json()["linked"]) == sorted(ids)
        assert client.post(LINK, headers=t["headers"], json={"signal": signal, "trade_ids": ids}).json()["linked"] == []   # once
        assert _trades(ids) == before and _count(OrderRecord, t["tenant_id"]) == orders

        # Open positions are not drafts yet.
        assert _drafts(t) == []

        # Exits are never blocked, under the lock too (ADR-0004).
        monkeypatch.setattr(phase0_lock, "assert_paper", real)
        for trade_id in ids:
            assert client.post(f"/api/positions/{trade_id}/exit-now", headers=t["headers"]).status_code == 200
        monkeypatch.setattr(phase0_lock, "assert_paper", no_order)
        orders = _count(OrderRecord, t["tenant_id"])
        closed = _trades(ids)

        [draft] = _drafts(t)
        assert draft["kind"] == "basket" and draft["title"] == "Basket · 4 legs" and sorted(draft["trade_ids"]) == sorted(ids)
        assert draft["status"] == "draft" and draft["notes"] is None and draft["placed_by"] == "you"
        assert len(draft["legs"]) == 4 and all(l["exit_price"] is not None and l["entry_price"] > 0 for l in draft["legs"])
        assert draft["pnl"] == round(sum(l["pnl"] for l in draft["legs"]), 2)
        # Only the structure-word lines of his signal: the value line and the call-word line are left out.
        assert draft["signal"] == {"setup": "Made-up setup", "words": [WORDS[0], WORDS[3]]}
        for text in [draft["title"], draft["signal"]["setup"], *draft["signal"]["words"]]:
            assert signal_language.is_clean(text), text
        # Reading the drafts changed no trade and placed no order; the journal rows exist.
        assert _trades(ids) == closed and _count(OrderRecord, t["tenant_id"]) == orders
        assert _count(JournalEntryRecord, t["tenant_id"]) == 4
        assert len(seen) == 4 and set(seen) == {"PAPER"}                       # every leg asserted PAPER at the chokepoint

        # Another member sees none of it.
        assert _drafts(member) == []

        # His own words go on the existing notes field; the draft then reads as noted.
        monkeypatch.setattr(phase0_lock, "assert_paper", real)
        for trade_id in ids:
            assert client.patch(f"/api/trades/{trade_id}/journal", headers=t["headers"], json={"notes": "Waited for the area."}).status_code == 200
        [draft] = _drafts(t)
        assert draft["status"] == "noted" and draft["notes"] == "Waited for the area."
    finally:
        _lock(t["tenant_id"], False)


def test_a_closed_trade_without_a_signal_is_a_draft_with_facts_only(condor):  # noqa: F811
    expiry, base, legs = condor
    t = _org_tenant("plain")
    lp = legs["long_put"]
    body = {"underlying": "NIFTY", "expiry": expiry.isoformat(), "strike": lp[0], "right": "PE", "side": "BUY", "lots": 1,
            "order_type": "MARKET", "product": "NRML", "chain_ltp": lp[3], "client_ref": uuid.uuid4().hex}
    out = client.post("/api/paper/fno/order", headers=t["headers"], json=body)
    assert out.status_code == 200 and out.json()["status"] == "PLACED", out.text
    trade_id = out.json()["trade_id"]
    assert client.post(f"/api/positions/{trade_id}/exit-now", headers=t["headers"]).status_code == 200
    [draft] = _drafts(t)
    assert draft["kind"] == "trade" and draft["trade_ids"] == [trade_id] and draft["signal"] is None
    assert draft["placed_by"] == "you" and draft["strategy"] is None and draft["status"] == "draft"
    # 8b slice 4: the snapshot's candles are the underlying's own chart (drawn on the page; nothing is stored).
    assert (draft["spot_symbol"], draft["spot_exchange"]) == ("NIFTY 50", "NSE")
    [leg] = draft["legs"]
    assert leg["side"] == "long" and leg["exit_at"] is not None and leg["pnl"] is not None


def test_signal_words_keep_only_the_signal_language():
    assert fno_journal.signal_words(WORDS, "NIFTY") == [WORDS[0], WORDS[3]]
    assert fno_journal.signal_words("not a list", "NIFTY") == []
    assert fno_journal.signal_words(["Target 23,100", "Seller area: 23,000–23,100", "SL hit"], "NIFTY") == ["Seller area: 23,000–23,100"]
