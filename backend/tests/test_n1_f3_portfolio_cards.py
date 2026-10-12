"""N1 F3: the portfolio risk cards (flag portfolio_risk, off by default) - the Telegram `/risk` lines, the proposal
card's "portfolio impact" line, and the server payload the Positions card renders (frontend/src/portfolio/riskSample.json
is checked against it here). Numbers of the user's own PAPER book and their own limits: never advice, and the AI never
places an order (none of this reaches an order path)."""
import asyncio
import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select

from app.db.models import AiActionRecord, NotificationRecord, Tenant, TradeRecord, User
from app.execution import approval
from app.portfolio import caps as C
from app.portfolio import risk as R
from app.portfolio import risk_text as T
from tests.order_path_graph import CallGraph
from tests.test_auth_api import _session_factory, client
from tests.test_n1_e1_execution_core import _flag
from tests.test_trading_worker import _tenant
from tests.test_z10c_order_paths import GATED
from tests.test_z10c_tenancy_fixes import _member
from tests.test_z3_approval_entries import _sig

UTC = timezone.utc
BACKEND = Path(__file__).resolve().parents[1]
SAMPLE = BACKEND.parent / "frontend" / "src" / "portfolio" / "riskSample.json"


def _run(coro):
    return asyncio.run(coro)


def _email(tag):
    return f"n1f3-{tag}-{uuid.uuid4().hex[:6]}@example.com"


def _pos(underlying, risk=0.0, gross=0.0, direction=R.BULLISH):
    return R.Position(key=uuid.uuid4().hex, symbol=underlying, underlying=underlying, kind="UNDERLYING", direction=direction,
                      long_notional=gross, short_notional=0.0, risk=risk, priced_at_entry=False)


def _no_advice(lines):
    for line in lines:
        assert not T.has_advice(line), line


# --- the text (pure) ------------------------------------------------------------------------------------------------------

def test_telegram_lines_state_numbers_and_limits_never_advice():
    positions = [_pos("INFY", 900, 30_000), _pos("TCS", 600, 20_000), _pos("NIFTY", 500, 10_000)]
    view = R.view(positions, {"INFY": "Information Technology", "TCS": "Information Technology", "NIFTY": R.INDEX_BUCKET},
                  {"INFY": R.Beta(0.8, 250, True, "lake"), "NIFTY": R.Beta(1.0, 0, True, "definition")}, 100_000.0)
    lines = T.telegram_lines(view, "en", C.Limits(30.0, 2.0, 5))
    assert lines[0] == "Portfolio (your PAPER book): 3 positions, exposure 60,000 (60% of capital), net +60,000."
    assert lines[1] == "Open risk at the stops: 2,000 (2% of capital); largest bucket Information Technology 50,000 (50%)."
    assert "beta unknown for TCS" in lines[2]
    assert "All 3 directional positions are bullish" in lines[3]
    assert lines[4] == "Your limits on new entries: one bucket's exposure 30%, one bucket's open risk 2%, 5 open positions."
    mr = T.telegram_lines(view, "mr", C.Limits(30.0, 2.0, 5))
    assert len(mr) == 5 and any(re.search(r"[ऀ-ॿ]", line) for line in mr)
    # The check itself catches advice in either language (review of #219: Marathi imperatives too).
    for bad in ("You should cut this.", "हे शेअर्स विका.", "जोखीम कमी करा.", "आता खरेदी करा.", "तुम्ही थांबले पाहिजे."):
        assert T.has_advice(bad), bad
    assert all(w for w in T.ADVICE_WORDS_MR) and not T.has_advice("Open risk at the stops: 2,000 (2% of capital).")
    _no_advice(lines + mr)
    assert T.telegram_lines(R.view([], {}, {}, 100_000.0)) == ["Portfolio (your PAPER book): no open positions."]
    assert len(T.telegram_lines(view, "en", None)) == 4                                     # caps off: no limits line


def test_the_impact_line_takes_the_entry_at_its_most_against_the_user_s_limits():
    inputs = C.BookInputs(positions=[_pos("BANKNIFTY", 400, 20_000), _pos("INFY", 900, 30_000)],
                          buckets={"BANKNIFTY": R.INDEX_BUCKET, "INFY": "Information Technology", "NIFTY": R.INDEX_BUCKET},
                          capital=100_000.0, risk_cap_amount=1_000.0, limits=C.Limits(25.0, 2.0, 3))
    out = T.impact(inputs, C.Candidate("NIFTYFUT", "NIFTY", 100.0, 90.0))                    # unsized: 100 x 10 at the cap
    assert out["bucket"] == R.INDEX_BUCKET and out["bucket_gross_pct"] == 30.0 and out["bucket_risk_pct"] == 1.4
    assert out["total_risk_pct"] == 2.3 and out["positions"] == 3 and out["entry_risk"] == 1000.0
    assert out["line"] == ("Portfolio after this entry (at most): INDEX exposure 30% of capital (your limit 25%), open risk in INDEX "
                           "1.4% (your limit 2%), all open risk 2.3%, open positions 3 (your limit 3).")
    plain = T.impact(C.BookInputs([], {}, 100_000.0, 500.0, C.Limits()), C.Candidate("X", "X", 50.0, 45.0))
    assert plain["line"].endswith("open positions 1.") and "your limit" not in plain["line"]
    _no_advice([out["line"], plain["line"]])


# --- the server pieces --------------------------------------------------------------------------------------------------------

def _trade(t, symbol, qty=10.0, entry=100.0, stop=95.0):
    return TradeRecord(tenant_id=t["tenant_id"], user_id=t["user_id"], mode="PAPER", symbol=symbol, strategy_id="f3", direction="LONG",
                       entry_time=datetime.now(UTC) - timedelta(minutes=30), entry_price=entry, quantity=qty, stop_loss=stop)


def _seed(*rows):
    async def go():
        async with _session_factory() as session:
            session.add_all(rows)
            await session.commit()
    _run(go())


def test_the_route_payload_matches_the_frontend_sample():
    t = _tenant(_email("sample"), with_credentials=False)
    for name in (C.BASE_FLAG, C.FLAG):
        _flag(name, t["tenant_id"])
    _seed(_trade(t, f"F3S{uuid.uuid4().hex[:4].upper()}"))
    live = client.get("/api/portfolio/risk", headers=t["headers"]).json()
    sample = json.loads(SAMPLE.read_text())
    sample.pop("_doc")
    assert set(sample) == set(live)
    for key in ("exposure", "open_risk", "beta", "direction", "limits"):
        assert set(sample[key]) == set(live[key]), key
    assert set(sample["buckets"][0]) == set(live["buckets"][0])
    assert set(next(iter(sample["beta"]["per_underlying"].values()))) == set(next(iter(live["beta"]["per_underlying"].values())))
    _flag(C.FLAG, t["tenant_id"], on=False)
    assert client.get("/api/portfolio/risk", headers=t["headers"]).json()["limits"] is None            # caps off


def test_telegram_risk_carries_the_book_only_with_the_flag_on():
    from app.telegram_inbound.service import answer_text
    t = _tenant(_email("tg"), with_credentials=False)
    _seed(_trade(t, f"F3T{uuid.uuid4().hex[:4].upper()}"))

    async def ask():
        async with _session_factory() as session:
            tenant = await session.get(Tenant, t["tenant_id"])
            user = await session.get(User, t["user_id"])
            return await answer_text(session, tenant, user, "/risk")
    assert "Portfolio (your PAPER book)" not in _run(ask())
    _flag(C.BASE_FLAG, t["tenant_id"])
    out = _run(ask())
    assert "Portfolio (your PAPER book): 1 positions, exposure 1,000" in out and "Open risk at the stops: 50" in out
    _no_advice([line for line in out.split("\n") if "Portfolio" in line or "Open risk" in line or "beta" in line])


def test_the_proposal_card_and_message_carry_the_owner_s_impact_line():
    t = _tenant(_email("card"), with_credentials=False)
    member_headers = _member(_email("card-member"), t["tenant_id"], "USER")
    _flag(C.BASE_FLAG, t["tenant_id"])
    _seed(_trade(t, f"F3C{uuid.uuid4().hex[:4].upper()}"))

    async def propose():
        async with _session_factory() as session:
            row = await approval.propose_entry(session, t["tenant_id"], signal=_sig(symbol="RELIANCE"), strategy_id="f3", source="tradingview",
                                               idempotency_key=f"f3:{uuid.uuid4().hex}", user_id=t["user_id"])
            note = await session.scalar(select(NotificationRecord).where(NotificationRecord.ai_action_id == row.id))
            return row.id, note.message
    action_id, message = _run(propose())
    assert "Portfolio after this entry (at most): " in message and "open positions 2." in message
    cards = client.get("/api/ai/actions?status=PROPOSED", headers=t["headers"]).json()
    card = next(c for c in cards if c["id"] == action_id)
    assert card["portfolio_impact"]["positions"] == 2 and card["portfolio_impact"]["line"].startswith("Portfolio after this entry")
    _no_advice([card["portfolio_impact"]["line"]])
    theirs = client.get("/api/ai/actions?status=PROPOSED", headers=member_headers).json()
    assert all(c.get("portfolio_impact") is None for c in theirs)                    # another member never sees the owner's book

    async def raw():
        async with _session_factory() as session:
            return (await session.get(AiActionRecord, action_id)).status
    assert _run(raw()) == "PROPOSED"                                                 # information only: nothing placed


def test_the_cards_are_not_an_order_path():
    graph = CallGraph(gated=GATED)
    mine = {name for name in graph.functions if name.startswith(("app.portfolio.risk_text.", "app.telegram_inbound.service._portfolio_lines",
                                                                  "app.ai.proposal_card."))}
    assert "app.portfolio.risk_text.proposal_impact" in mine and "app.telegram_inbound.service._portfolio_lines" in mine
    assert not {n for n in mine if n.startswith(("app.portfolio.", "app.telegram_inbound.service._portfolio"))} & graph.reaching()



def test_the_org_owner_never_sees_a_member_s_portfolio_impact():
    """Review of #219: the OWNER lists a member's waiting proposal - the impact is the member's own book, so none."""
    t = _tenant(_email("owner"), with_credentials=False)
    _member(_email("owner-member"), t["tenant_id"], "USER")
    _flag(C.BASE_FLAG, t["tenant_id"])

    async def propose():
        async with _session_factory() as session:
            member = await session.scalar(select(User).where(User.tenant_id == t["tenant_id"], User.id != t["user_id"]))
            row = await approval.propose_entry(session, t["tenant_id"], signal=_sig(symbol="RELIANCE"), strategy_id="f3", source="tradingview",
                                               idempotency_key=f"f3:{uuid.uuid4().hex}", user_id=member.id)
            return row.id, row.owner_user_id, member.id
    action_id, owner_id, member_id = _run(propose())
    assert owner_id == member_id != t["user_id"]
    me = client.get("/api/auth/me", headers=t["headers"]).json()
    assert me["role"] == "OWNER"
    cards = client.get("/api/ai/actions?status=PROPOSED", headers=t["headers"]).json()
    card = next(c for c in cards if c["id"] == action_id)
    assert card["portfolio_impact"] is None


def test_the_book_is_read_once_per_request(monkeypatch):
    """Review of #219: propose_entry reads the owner's book once for the caps and the impact line; the cards read the
    caller's book once for every proposal listed."""
    from app.portfolio import risk_data
    t = _tenant(_email("once"), with_credentials=False)
    for name in (C.BASE_FLAG, C.FLAG):
        _flag(name, t["tenant_id"])

    async def limits():
        async with _session_factory() as session:
            from app.paper.ticket import save_settings
            await save_settings(session, await session.get(User, t["user_id"]), {"max_open_positions": 10})
    _run(limits())
    reads = []
    real = risk_data.open_book

    async def counting(session, user):
        reads.append(user.id)
        return await real(session, user)
    monkeypatch.setattr(risk_data, "open_book", counting)

    async def propose():
        async with _session_factory() as session:
            row = await approval.propose_entry(session, t["tenant_id"], signal=_sig(symbol="RELIANCE"), strategy_id="f3", source="tradingview",
                                               idempotency_key=f"f3:{uuid.uuid4().hex}", user_id=t["user_id"])
            note = await session.scalar(select(NotificationRecord).where(NotificationRecord.ai_action_id == row.id))
            return note.message
    assert "Portfolio after this entry" in _run(propose()) and reads == [t["user_id"]]
    assert "Portfolio after this entry" in _run(propose()) and len(reads) == 2
    reads.clear()
    cards = [c for c in client.get("/api/ai/actions?status=PROPOSED", headers=t["headers"]).json() if c.get("portfolio_impact")]
    assert len(cards) == 2 and reads == [t["user_id"]]
