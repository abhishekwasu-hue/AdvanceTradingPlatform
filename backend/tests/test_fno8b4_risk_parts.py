"""F&O workspace 8b, slice 4 (owner order ATP_SESSION_FNO_WORKSPACE.md, sections 8b-d and 9): the risk line's events and
margin parts - reads only, information only.

- `GET /api/paper/fno/event-risk` lists the events calendar's entries (the market events and the company's calendar)
  from today to the expiry that apply to this underlying: global ones and the caller's own organisation's, never another
  organisation's; each market event says what his own event rule does that day; an empty calendar is said plainly;
- the risk budget carries the PAPER balance (starting capital + closed, counted PAPER P&L) and says the open positions'
  margin is not taken off; reading it creates no PAPER account;
- both are behind `ui_v2` + the PAPER account, GET only, classified in the route table, and place nothing.

Made-up data only: made-up underlyings, made-up events and trades."""
import asyncio
import uuid
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select

from app.db.models import CompanyRecord, EarningsCalendarEventRecord, MarketEventRecord, OrderRecord, PaperAccountRecord, TradeRecord
from app.market_data.calendar import IST
from app.paper import fno_insights
from app.paper.fno_insights_routes import router
from tests.tenancy_route_table import ROUTE_TABLE
from tests.test_auth_api import _session_factory, client
from tests.test_fno8b_insights import _org
from tests.test_trading_worker import _tenant
from tests.test_z5_p1d_risk_by_default import _flag

URL = "/api/paper/fno/event-risk"


def _run(coro):
    return asyncio.run(coro)


def _today() -> date:
    return datetime.now(IST).date()


def _name() -> str:
    return "ZZEV" + uuid.uuid4().hex[:6].upper()


def _event(tenant_id, day: date, *, underlying=None, kind="RBI_POLICY", action="SIZE_CUT", cut=None) -> int:
    async def go():
        async with _session_factory() as session:
            row = MarketEventRecord(tenant_id=tenant_id, underlying=underlying, event_date=day, kind=kind, action=action, size_cut_pct=cut,
                                    description="made up")
            session.add(row)
            await session.commit()
            return row.id
    return _run(go())


def _company_event(tenant_id, symbol: str, day: date, kind="RESULTS") -> None:
    async def go():
        async with _session_factory() as session:
            company = CompanyRecord(tenant_id=tenant_id, symbol=symbol, name=f"{symbol} made up", sector="Made up", industry="Made up")
            session.add(company)
            await session.flush()
            session.add(EarningsCalendarEventRecord(tenant_id=tenant_id, company_id=company.id, event_type=kind, event_date=day))
            await session.commit()
    _run(go())


def _get(t, underlying: str, until: date) -> dict:
    out = client.get(URL, headers=t["headers"], params={"underlying": underlying, "until": until.isoformat()})
    assert out.status_code == 200, out.text
    return out.json()


# --- flags, the route table, GET only ------------------------------------------------------------------------------------

def test_event_risk_is_behind_the_ticket_flags_and_read_only():
    t = _tenant(f"fno8b4-off-{uuid.uuid4().hex[:6]}@example.com")
    params = {"underlying": "NIFTY", "until": _today().isoformat()}
    out = client.get(URL, headers=t["headers"], params=params)
    assert out.status_code == 404 and out.headers["X-Feature-Disabled"] == "ui_v2"
    _flag("ui_v2", t["tenant_id"])
    out = client.get(URL, headers=t["headers"], params=params)
    assert out.status_code == 404 and out.headers["X-Feature-Disabled"] == "paper_account"
    assert client.get(URL, params=params).status_code == 401
    t2 = _org("ev-shape")
    assert client.get(URL, headers=t2["headers"], params={"underlying": "drop;table", "until": _today().isoformat()}).status_code == 422
    assert client.get(URL, headers=t2["headers"], params={"underlying": "NIFTY"}).status_code == 422
    assert {m for r in router.routes for m in getattr(r, "methods", set())} == {"GET"}
    assert ("GET", URL) in ROUTE_TABLE


# --- the events ----------------------------------------------------------------------------------------------------------

def test_events_up_to_the_expiry_for_this_underlying_with_his_own_event_rule():
    t = _org("ev")
    other = _org("ev-other")
    name = _name()
    today = _today()
    expiry = today + timedelta(days=6)
    _event(t["tenant_id"], today + timedelta(days=2), underlying=name, kind="RESULTS", action="BLOCK")
    _event(t["tenant_id"], today + timedelta(days=4), underlying=name, kind="OTHER", cut=40.0)
    _event(t["tenant_id"], today + timedelta(days=9), underlying=name, kind="BUDGET")             # after the expiry
    _event(t["tenant_id"], today - timedelta(days=1), underlying=name, kind="BUDGET")             # already past
    _event(t["tenant_id"], today + timedelta(days=1), underlying=_name(), kind="RESULTS")         # another symbol
    _event(other["tenant_id"], today + timedelta(days=3), underlying=name, kind="ELECTION")       # another organisation's
    full = _get(t, name, expiry)
    out = {**full, "events": [e for e in full["events"] if e["scope"] == name]}       # (another test's "all symbols" rows aside)
    assert out["blocks"] is False and out["calendar_empty"] is False and out["message"] is None
    assert out["from"] == today.isoformat() and out["until"] == expiry.isoformat()
    assert [(e["date"], e["name"], e["scope"]) for e in out["events"]] == [
        ((today + timedelta(days=2)).isoformat(), "Results", name), ((today + timedelta(days=4)).isoformat(), "Market event", name)]
    assert out["events"][0]["rule"] == "Your event rule that day: no new entries."
    assert out["events"][1]["rule"] == "Your event rule that day: a smaller size (40% cut)."
    # The other organisation sees its own entry, never this one's.
    seen = _get(other, name, expiry)
    assert [e["name"] for e in seen["events"] if e["scope"] == name] == ["Election"]


def test_an_index_bucket_event_applies_to_indices_and_a_company_event_to_its_stock():
    t = _org("ev-scope")
    today = _today()
    _event(t["tenant_id"], today + timedelta(days=1), underlying="INDEX", kind="RBI_POLICY")
    _event(t["tenant_id"], today + timedelta(days=2), underlying="*", kind="FED")
    stock = _name()
    _company_event(t["tenant_id"], stock, today + timedelta(days=3))
    index = _get(t, "NIFTY", today + timedelta(days=5))
    names = [e["name"] for e in index["events"] if e["source"] == "market"]
    assert "RBI policy" in names and "US Fed decision" in names
    assert next(e for e in index["events"] if e["name"] == "RBI policy")["scope"] == "all indices"
    single = _get(t, stock, today + timedelta(days=5))
    assert "RBI policy" not in [e["name"] for e in single["events"]]                         # an index event, not a stock's
    company = [e for e in single["events"] if e["source"] == "company"]
    assert [(e["name"], e["scope"], e["rule"]) for e in company] == [("Results", stock, None)]
    # Another organisation's company entry is not read.
    assert [e for e in _get(_org("ev-scope2"), stock, today + timedelta(days=5))["events"] if e["source"] == "company"] == []


def _direct(t, underlying: str, until: date, today: date) -> dict:
    """The read itself on a made-up far-future "today" (no other test's events exist that far out)."""
    async def go():
        async with _session_factory() as session:
            from app.db.models import User
            user = await session.get(User, t["user_id"])
            return await fno_insights.event_risk(session, user, underlying, until, today=today)
    return _run(go())


def test_an_empty_calendar_is_said_plainly_and_a_far_expiry_is_clipped():
    t = _org("ev-empty")
    today = date(2097, 1, 5) + timedelta(days=uuid.uuid4().int % 300)
    out = _direct(t, "NIFTY", today + timedelta(days=3), today)
    assert out["events"] == [] and out["calendar_empty"] is True
    assert out["message"] == fno_insights.EMPTY_CALENDAR
    far = _direct(t, "NIFTY", today + timedelta(days=400), today)
    assert far["clipped"] is True and far["until"] == (today + timedelta(days=fno_insights.EVENT_DAYS_MAX)).isoformat()
    # An event only after the expiry: the calendar is not empty, there is simply nothing before the expiry.
    _event(t["tenant_id"], today + timedelta(days=10), underlying="NIFTY", kind="BUDGET")
    later = _direct(t, "NIFTY", today + timedelta(days=3), today)
    assert later["events"] == [] and later["calendar_empty"] is False and later["message"] is None
    # ... and another organisation's far-future entry never makes his calendar look filled.
    assert _direct(_org("ev-empty2"), "NIFTY", today + timedelta(days=3), today)["calendar_empty"] is True


# --- the PAPER balance ---------------------------------------------------------------------------------------------------

def _trade(t, *, pnl, closed=True, price_source=None, mode="PAPER") -> None:
    async def go():
        async with _session_factory() as session:
            now = datetime.now(timezone.utc)
            session.add(TradeRecord(tenant_id=t["tenant_id"], user_id=t["user_id"], mode=mode, symbol="MADEUP", strategy_id="made-up",
                                    direction="LONG", entry_time=now - timedelta(hours=2), entry_price=100.0, quantity=1.0, stop_loss=90.0,
                                    exchange="NSE", pnl=pnl, charges=0.0, price_source=price_source,
                                    exit_time=now - timedelta(hours=1) if closed else None, exit_price=101.0 if closed else None))
            await session.commit()
    _run(go())


def test_the_budget_carries_the_paper_balance_without_creating_an_account():
    t = _org("bal")

    async def accounts():
        async with _session_factory() as session:
            return await session.scalar(select(func.count()).select_from(PaperAccountRecord).where(PaperAccountRecord.user_id == t["user_id"]))
    start = 1_000_000.0          # M68 PR-7: the capital he saved (tests/conftest.py _saved_paper_capital); ATP sets none
    out = client.get("/api/paper/fno/risk-budget", headers=t["headers"]).json()
    assert out["paper_balance"]["amount"] == round(start, 2) and out["paper_balance"]["open_margin_counted"] is False
    assert _run(accounts()) == 0                                                   # read only: no account created
    _trade(t, pnl=1500.0)
    _trade(t, pnl=-400.0)
    _trade(t, pnl=999.0, closed=False)                                             # open: not booked
    _trade(t, pnl=777.0, price_source="client")                                    # priced from his own inputs: left out
    _trade(t, pnl=555.0, mode="LIVE")                                              # not PAPER
    out = client.get("/api/paper/fno/risk-budget", headers=t["headers"]).json()
    assert out["paper_balance"]["amount"] == round(start + 1100.0, 2)
    assert out["blocks"] is False

    async def orders():
        async with _session_factory() as session:
            return await session.scalar(select(func.count()).select_from(OrderRecord).where(OrderRecord.tenant_id == t["tenant_id"]))
    assert _run(orders()) == 0
