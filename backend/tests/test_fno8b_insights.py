"""F&O workspace 8b, slice 1 (owner order ATP_SESSION_FNO_WORKSPACE.md, sections 8b and 9): the read-only ATP-only reads.

- the routes exist only behind their flags, are GET only, and the route table classifies them;
- the risk budget is the caller's own risk settings, and says it blocks nothing (the risk line is a warning only);
- the setup-aware chain marks only the strikes the caller's OWN deployment rules work out to, for HIS OWN signal - a
  teammate's signal or deployment, another organisation's signal, an equity deployment or a signal without a side marks
  nothing - in structure words only.

Made-up data only: made-up underlyings with a made-up strike ladder, made-up deployments and proposals."""
import asyncio
import json
import uuid
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select

from app.chart_analyze import signal_language
from app.core.models import RiskConfig
from app.db.models import AiActionRecord, InstrumentRecord, OrderRecord, SignalHistoryRecord, StrategyDeploymentRecord
from app.market_data.calendar import IST
from app.paper import fno_insights
from app.paper.fno_insights_routes import router
from tests.test_auth_api import _session_factory, client
from tests.test_trading_worker import _tenant
from tests.test_z3b_paper_account import _member, _paper_flag
from tests.test_z5_p1d_risk_by_default import _flag

LADDER = [float(k) for k in range(100, 145, 5)]          # 100, 105, ..., 140
URL = "/api/paper/fno/setup-strikes"


def _run(coro):
    return asyncio.run(coro)


def _org(tag: str, *, paper: bool = True) -> dict:
    t = _tenant(f"fno8b-{tag}-{uuid.uuid4().hex[:6]}@example.com")
    _flag("ui_v2", t["tenant_id"])
    if paper:
        _paper_flag(t["tenant_id"])
    return t


def _underlying() -> str:
    return "ZZFO" + uuid.uuid4().hex[:6].upper()


def _ladder(underlying: str) -> date:
    expiry = datetime.now(IST).date() + timedelta(days=3)

    async def go():
        async with _session_factory() as session:
            for right in ("CE", "PE"):
                for k in LADDER:
                    session.add(InstrumentRecord(broker="upstox", exchange="NFO", instrument_key=f"upstox|{underlying}{expiry:%y%m%d}{int(k)}{right}",
                                                 tradingsymbol=f"{underlying}{int(k)}{right}", underlying=underlying, instrument_type=right,
                                                 expiry=expiry, strike=k, lot_size=1))
            await session.commit()
    _run(go())
    return expiry


def _deployment(tenant_id: int, symbol: str, *, created_by=None, strategy: str = "BULL_PUT_SPREAD", kind: str = "OPTION",
                width: int = 2) -> int:
    async def go():
        async with _session_factory() as session:
            dep = StrategyDeploymentRecord(tenant_id=tenant_id, strategy_id=f"made-up-{uuid.uuid4().hex[:6]}", symbol=symbol, exchange="NSE",
                                           timeframe="15min", mode="PAPER", status="PAUSED", instrument_kind=kind, option_position="WRITE",
                                           expiry_rule="NEAREST", strike_rule="OTM", strike_offset=1, option_strategy=strategy,
                                           spread_width=width, created_by=created_by)
            session.add(dep)
            await session.commit()
            return dep.id
    return _run(go())


def _proposal(tenant_id: int, symbol: str, *, owner=None, deployment=None, direction="LONG", price=121.0) -> str:
    async def go():
        async with _session_factory() as session:
            evidence = {"symbol": symbol, "direction": direction, "proposal_price": price, "strategy_id": "made-up",
                        "signal": {"strategy_name": "Made-up setup"}}
            row = AiActionRecord(tenant_id=tenant_id, deployment_id=deployment, action="ENTRY", rule=f"made-up-{uuid.uuid4().hex[:8]}", reason="made up",
                                 evidence_json=json.dumps(evidence), status="PROPOSED", owner_user_id=owner,
                                 expires_at=datetime.now(timezone.utc) + timedelta(minutes=30))
            session.add(row)
            await session.commit()
            return f"action:{row.id}"
    return _run(go())


def _history(tenant_id: int, user_id: int, symbol: str, direction="SHORT", entry=119.0) -> str:
    async def go():
        async with _session_factory() as session:
            row = SignalHistoryRecord(tenant_id=tenant_id, user_id=user_id, strategy_id="made-up", symbol=symbol, direction=direction,
                                      signal_time=datetime.now(timezone.utc), entry=entry, grade="B")
            session.add(row)
            await session.commit()
            return f"history:{row.id}"
    return _run(go())


def _strikes(t: dict, signal: str, spot=None) -> dict:
    params = {"signal": signal} if spot is None else {"signal": signal, "spot": spot}
    out = client.get(URL, headers=t["headers"], params=params)
    assert out.status_code == 200, out.text
    return out.json()


def _texts(value) -> list:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [x for v in value.values() for x in _texts(v)]
    if isinstance(value, list):
        return [x for v in value for x in _texts(v)]
    return []


# --- flags, the route table, GET only ------------------------------------------------------------------------------------

def test_the_routes_are_behind_their_flags_and_read_only():
    t = _tenant(f"fno8b-off-{uuid.uuid4().hex[:6]}@example.com")
    out = client.get("/api/paper/fno/risk-budget", headers=t["headers"])
    assert out.status_code == 404 and out.headers["X-Feature-Disabled"] == "ui_v2"
    out = client.get(URL, headers=t["headers"], params={"signal": "action:1"})
    assert out.status_code == 404 and out.headers["X-Feature-Disabled"] == "ui_v2"
    _flag("ui_v2", t["tenant_id"])
    out = client.get("/api/paper/fno/risk-budget", headers=t["headers"])
    assert out.status_code == 404 and out.headers["X-Feature-Disabled"] == "paper_account"
    assert client.get(URL, headers=t["headers"], params={"signal": "action:1"}).status_code == 200      # the chain's own flag only
    assert client.get("/api/paper/fno/risk-budget").status_code == 401 and client.get(URL, params={"signal": "action:1"}).status_code == 401
    assert client.get(URL, headers=t["headers"], params={"signal": "drop table"}).status_code == 422
    # Nothing here can lead to an order: GET only.
    assert {m for r in router.routes for m in getattr(r, "methods", set())} == {"GET"}


# --- 8b-d: the risk line's budget ----------------------------------------------------------------------------------------

def test_the_risk_budget_is_the_callers_own_settings_and_blocks_nothing():
    t = _org("budget")
    body = RiskConfig(capital=200_000.0, risk_per_trade_pct=1.0, max_daily_loss_pct=2.5).model_dump()
    assert client.put("/api/risk-settings", headers=t["headers"], json=body).status_code == 200
    out = client.get("/api/paper/fno/risk-budget", headers=t["headers"]).json()
    assert out["capital"] == 200_000.0
    assert out["per_trade"] == {"amount": 2_000.0, "pct": 1.0} and out["daily"] == {"amount": 5_000.0, "pct": 2.5}
    assert out["blocks"] is False and out["source"] == "Your risk settings"

    async def orders():
        async with _session_factory() as session:
            return await session.scalar(select(func.count()).select_from(OrderRecord).where(OrderRecord.tenant_id == t["tenant_id"]))
    assert _run(orders()) == 0                                                  # reading it places nothing


# --- 8b-a: the setup-aware chain -----------------------------------------------------------------------------------------

def test_own_proposal_marks_the_strikes_of_its_own_deployment_at_the_chain_price():
    t = _org("own")
    sym = _underlying()
    _ladder(sym)
    dep = _deployment(t["tenant_id"], sym, created_by=t["user_id"])            # bull put spread, OTM 1, wing 2 strikes away
    signal = _proposal(t["tenant_id"], sym, owner=t["user_id"], deployment=dep)
    out = _strikes(t, signal, spot=120.5)
    assert out["source"] == "your_settings" and out["underlying"] == sym and out["side"] == "bull_put" and out["message"] is None
    assert out["reference"] == {"text": "the current price", "price": 120.5}
    [item] = out["items"]
    assert item["id"] == dep and item["settings"].startswith("Bull put spread · main strike 1 strike out of the money")
    # At 120.5 the ATM is 120; one strike out of the money on the put side is 115; the wing two strikes lower, 105.
    assert item["legs"] == [{"label": "Main strike", "right": "PE", "strike": 115.0, "ratio": 1},
                            {"label": "Wing", "right": "PE", "strike": 105.0, "ratio": 1}]
    # Without the chain's price, the signal's own price is the reference (121 -> ATM 120, same strikes).
    out = _strikes(t, signal)
    assert out["reference"] == {"text": "the signal's price", "price": 121.0} and out["items"][0]["legs"] == item["legs"]


def test_own_generated_signal_uses_only_the_callers_own_fitting_deployments():
    t = _org("hist")
    member = _member(t)
    sym = _underlying()
    _ladder(sym)
    _deployment(t["tenant_id"], sym, created_by=member["user_id"], strategy="BEAR_CALL_SPREAD")    # a teammate's: never used
    _deployment(t["tenant_id"], sym, created_by=t["user_id"], strategy="BULL_PUT_SPREAD")         # his, but the other side
    signal = _history(t["tenant_id"], t["user_id"], sym, direction="SHORT")
    out = _strikes(t, signal, spot=120.5)
    assert out["side"] == "bear_call" and out["items"] == [] and out["message"] == fno_insights.NONE_FIT
    mine = _deployment(t["tenant_id"], sym, created_by=t["user_id"], strategy="BEAR_CALL_SPREAD")
    out = _strikes(t, signal, spot=120.5)
    assert [i["id"] for i in out["items"]] == [mine]
    assert out["items"][0]["legs"] == [{"label": "Main strike", "right": "CE", "strike": 125.0, "ratio": 1},
                                       {"label": "Wing", "right": "CE", "strike": 135.0, "ratio": 1}]


def test_a_teammates_or_another_organisations_signal_marks_nothing():
    t = _org("team")
    member = _member(t)
    other = _org("other")
    sym = _underlying()
    _ladder(sym)
    theirs = _deployment(t["tenant_id"], sym, created_by=member["user_id"])
    mine = _deployment(t["tenant_id"], sym, created_by=t["user_id"])
    cases = [
        _proposal(t["tenant_id"], sym, owner=member["user_id"], deployment=mine),        # a teammate's proposal
        _proposal(t["tenant_id"], sym, owner=None, deployment=theirs),                   # organisation-wide, a teammate's deployment
        _proposal(t["tenant_id"], sym, owner=t["user_id"], deployment=theirs),           # his proposal on a teammate's deployment
        _history(t["tenant_id"], member["user_id"], sym),                                 # a teammate's generated signal
        _proposal(other["tenant_id"], sym, owner=other["user_id"]),                      # another organisation's
        "action:999999999", "trade:1", "intent:1",
    ]
    for signal in cases:
        out = _strikes(t, signal, spot=120.5)
        assert out["items"] == [] and out["message"] == fno_insights.NOT_YOURS and out["underlying"] is None, signal


def test_no_side_an_equity_deployment_or_no_price_mark_nothing_and_say_why():
    t = _org("empty")
    sym = _underlying()
    _ladder(sym)
    equity = _deployment(t["tenant_id"], sym, created_by=t["user_id"], kind="EQUITY")
    out = _strikes(t, _proposal(t["tenant_id"], sym, owner=t["user_id"], deployment=equity), spot=120.5)
    assert out["items"] == [] and out["message"] == fno_insights.NOT_OPTION
    out = _strikes(t, _proposal(t["tenant_id"], sym, owner=t["user_id"], direction=None), spot=120.5)
    assert out["items"] == [] and out["message"] == fno_insights.NO_DIRECTION
    _deployment(t["tenant_id"], sym, created_by=t["user_id"])
    out = _strikes(t, _proposal(t["tenant_id"], sym, owner=t["user_id"], price=None))
    assert out["items"] == [] and out["message"] == fno_insights.NO_PRICE


def test_the_setup_answer_uses_structure_words_only():
    t = _org("words")
    sym = _underlying()
    _ladder(sym)
    dep = _deployment(t["tenant_id"], sym, created_by=t["user_id"])
    answers = [_strikes(t, _proposal(t["tenant_id"], sym, owner=t["user_id"], deployment=dep), spot=120.5),
               _strikes(t, "action:999999999")]
    for msg in (fno_insights.NOT_YOURS, fno_insights.NO_DIRECTION, fno_insights.NO_PRICE, fno_insights.NONE_FIT, fno_insights.NOT_OPTION):
        assert signal_language.is_clean(msg), msg
    for text in _texts(answers):
        assert signal_language.is_clean(text), text


def test_parse_signal_id():
    assert fno_insights.parse_signal_id("action:12") == ("action", 12)
    assert fno_insights.parse_signal_id("history:7") == ("history", 7)
    for bad in ("trade:1", "alert:3", "action:", "action:x", "", "action:1234567890123"):
        assert fno_insights.parse_signal_id(bad) is None
