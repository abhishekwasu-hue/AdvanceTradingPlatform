"""N2-6 (P1 spec 2.4, docs/design/N2_P1FGH.md): the positions board behind the `positions_board` flag (off by default).

Covers:
- the figures by hand: P&L in ₹ and R, stop and target status, a widened or trailed stop against the plan, time in
  trade, and a position without a mark or a stop;
- only the caller's own positions;
- the exit is one click under a daily loss lockout, and Exit now still closes the position: exits are never gated
  (ADR-0004).
"""
import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.db.models import RiskLockoutRecord, TradePlanRecord, TradeRecord
from app.journal.review import advice_in
from app.market_data.calendar import IST
from app.platform import controls
from app.positions_board import service
from tests.test_auth_api import _session_factory, client
from tests.test_trading_worker import _tenant
from tests.test_z3b_paper_account import _member, _paper_flag

UTC = timezone.utc
NOW = datetime(2026, 10, 12, 6, 0, tzinfo=UTC)


def _run(coro):
    return asyncio.run(coro)


def _email(tag: str) -> str:
    return f"n2p-{tag}-{uuid.uuid4().hex[:6]}@example.com"


def _flag(name: str, tenant_id: int, on: bool = True) -> None:
    async def go():
        async with _session_factory() as session:
            current = await controls._get(session, controls.KEY_FEATURE_FLAGS)
            flags = dict(current.get("flags") or {})
            entry = flags.get(name) or {"on": False, "tenants": []}
            tenants = set(entry.get("tenants") or [])
            (tenants.add if on else tenants.discard)(tenant_id)
            flags[name] = {"on": False, "tenants": sorted(tenants)}
            await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": flags}, None)
            await session.commit()
    _run(go())


def _t(**kw):
    base = dict(id=1, symbol="RELIANCE", mode="PAPER", direction="LONG", quantity=10.0, instrument_kind="UNDERLYING", underlying_symbol=None,
                leg_group_id=None, entry_price=100.0, entry_time=NOW - timedelta(minutes=95), mark_price=104.0, mark_time=NOW - timedelta(minutes=2),
                stop_loss=96.0, initial_stop_loss=96.0, target1=110.0)
    base.update(kw)
    return SimpleNamespace(**base)


# --- pure -----------------------------------------------------------------------------------------------------------

def test_figures_by_hand():
    r = service.row(_t(), None, now=NOW, exit_how="one_click")
    assert r["pnl"] == 40.0 and r["initial_risk"] == 40.0 and r["r_multiple"] == 1.0 and r["risk_basis"] == "initial stop"
    assert r["stop_status"] == "ok" and r["to_stop_r"] == 2.0 and r["target_status"] == "ok" and r["to_target_r"] == 1.5
    assert r["time_in_trade_minutes"] == 95 and r["mark_age_minutes"] == 2 and r["price_basis"] == "mark" and r["stop_drift"] == "unchanged"
    assert service.row(_t(mark_price=96.5), None, now=NOW, exit_how="x")["stop_status"] == "near"          # 0.125 R left
    assert service.row(_t(mark_price=95.0), None, now=NOW, exit_how="x")["stop_status"] == "through"
    assert service.row(_t(mark_price=110.0), None, now=NOW, exit_how="x")["target_status"] == "reached"
    assert service.row(_t(mark_price=109.5), None, now=NOW, exit_how="x")["target_status"] == "near"
    # Short: the same arithmetic mirrored.
    s = service.row(_t(direction="SHORT", mark_price=97.0, stop_loss=104.0, initial_stop_loss=104.0, target1=90.0), None, now=NOW, exit_how="x")
    assert s["pnl"] == 30.0 and s["r_multiple"] == 0.75 and s["to_stop_r"] == 1.75 and s["to_target_r"] == 1.75


def test_stop_drift_against_the_plan_or_the_initial_stop():
    plan = SimpleNamespace(id=7, setup="PULLBACK_AT_LEVEL", entry=100.0, stop_loss=96.0, target=110.0, reward_risk=2.5)
    widened = service.row(_t(stop_loss=94.0, initial_stop_loss=None), plan, now=NOW, exit_how="x")
    assert widened["stop_drift"] == "widened" and widened["risk_basis"] == "plan stop" and widened["original_stop"] == 96.0
    assert widened["plan"]["plan_id"] == 7 and widened["initial_risk"] == 40.0
    trailed = service.row(_t(stop_loss=101.0), None, now=NOW, exit_how="x")
    assert trailed["stop_drift"] == "trailed"
    short_widened = service.row(_t(direction="SHORT", stop_loss=106.0, initial_stop_loss=104.0, target1=90.0, mark_price=99.0), None, now=NOW, exit_how="x")
    assert short_widened["stop_drift"] == "widened"
    bare = service.row(_t(mark_price=None, mark_time=None, stop_loss=0.0, initial_stop_loss=None, target1=None), None, now=NOW, exit_how="automatic")
    assert bare["price_basis"] == "entry" and bare["pnl"] == 0.0 and bare["stop"] is None and bare["r_multiple"] is None
    assert bare["risk_basis"] == "no stop" and bare["stop_status"] is None and bare["stop_drift"] is None and bare["exit"] == "automatic"


# --- API -----------------------------------------------------------------------------------------------------------------

def _open(t, symbol, *, direction="LONG", qty=10.0, entry=100.0, stop=96.0, mark=104.0, target=110.0):
    async def go():
        async with _session_factory() as session:
            now = datetime.now(UTC)
            row = TradeRecord(tenant_id=t["tenant_id"], user_id=t["user_id"], mode="PAPER", symbol=symbol, strategy_id="manual",
                              direction=direction, entry_time=now - timedelta(minutes=30), entry_price=entry, quantity=qty, stop_loss=stop,
                              initial_stop_loss=stop, target1=target, exchange="NSE", mark_price=mark, mark_time=now - timedelta(minutes=1))
            session.add(row)
            await session.commit()
            return row.id
    return _run(go())


def test_flag_off_is_404():
    assert service.FLAG in controls.FEATURE_FLAGS and service.FLAG in controls.DEFAULT_OFF_FLAGS
    t = _tenant(_email("off"))
    r = client.get("/api/positions/board", headers=t["headers"])
    assert r.status_code == 404 and r.headers.get("X-Feature-Disabled") == "positions_board"


def test_board_is_the_callers_own_with_the_plan():
    owner = _tenant(_email("owner"))
    _flag(service.FLAG, owner["tenant_id"])
    member = _member(owner)
    mine = _open(owner, "RELIANCE")
    _open(member, "TCS", mark=50.0)

    async def plan():
        async with _session_factory() as session:
            session.add(TradePlanRecord(tenant_id=owner["tenant_id"], user_id=owner["user_id"], mode="PAPER", symbol="RELIANCE", direction="LONG",
                                        setup="PULLBACK_AT_LEVEL", entry=100.0, stop_loss=97.0, target=110.0, reward_risk=3.33, quantity=10.0,
                                        risk_amount=30.0, risk_cap=50.0, status="PLACED", trade_id=mine))
            await session.commit()
    _run(plan())
    body = client.get("/api/positions/board", headers=owner["headers"]).json()
    assert [p["symbol"] for p in body["positions"]] == ["RELIANCE"]
    p = body["positions"][0]
    assert p["plan"]["setup"] == "PULLBACK_AT_LEVEL" and p["risk_basis"] == "initial stop" and p["pnl"] == 40.0 and p["r_multiple"] == 1.0
    assert p["exit"] == "automatic"                                        # no Exit now without approval-only / PAPER account
    assert body["totals"]["positions"] == 1 and body["totals"]["to_stops_from_marks"] == 80.0 and body["lockout"] is None
    assert [q["symbol"] for q in client.get("/api/positions/board", headers=member["headers"]).json()["positions"]] == ["TCS"]
    for text in (body["note"], body["exits"]):
        assert advice_in(text) == []


def test_exit_stays_one_click_under_a_lockout_and_closes():
    t = _tenant(_email("lock"))
    _flag(service.FLAG, t["tenant_id"])
    _flag("risk_by_default", t["tenant_id"])
    _paper_flag(t["tenant_id"])                                            # Exit now exists with the PAPER account
    trade_id = _open(t, "INFY", mark=99.0)

    async def lock():
        async with _session_factory() as session:
            session.add(RiskLockoutRecord(tenant_id=t["tenant_id"], user_id=t["user_id"], mode="PAPER",
                                          trading_day=datetime.now(UTC).astimezone(IST).date(), kind="DAILY_LOSS", pnl=-5000.0,
                                          limit_amount=2000.0, reason="Daily loss limit reached: new PAPER entries stop for today",
                                          locked_at=datetime.now(UTC)))
            await session.commit()
    _run(lock())
    body = client.get("/api/positions/board", headers=t["headers"]).json()
    assert body["lockout"] is not None and "Daily loss" in body["lockout"]["reason"]
    assert body["positions"][0]["exit"] == "one_click" and "never locked" in body["exits"]
    r = client.post(f"/api/positions/{trade_id}/exit-now", headers=t["headers"])
    assert r.status_code == 200, r.text
    assert r.json()["closed"][0]["exit_price"] == pytest.approx(99.0)
    assert client.get("/api/positions/board", headers=t["headers"]).json()["positions"] == []



def test_a_viewer_sees_automatic_not_a_button_that_would_403():
    """Review: Exit now needs a trading role and scope; a VIEWER's board says the exits run automatically."""
    from urllib.parse import parse_qs, urlparse
    owner = _tenant(_email("viewer-owner"))
    _flag(service.FLAG, owner["tenant_id"])
    _paper_flag(owner["tenant_id"])
    email = _email("viewer")
    invite = client.post("/api/team/invites", headers=owner["headers"], json={"email": email, "role": "VIEWER"})
    assert invite.status_code in (200, 201), invite.text
    token = parse_qs(urlparse(invite.json()["invite_url"]).query)["invite"][0]
    access = client.post(f"/api/auth/invite/{token}/accept", json={"password": "Viewer-Pass-2026"}).json()["access_token"]
    headers = {"Authorization": f"Bearer {access}"}
    me = client.get("/api/auth/me", headers=headers).json()
    trade_id = _open({"tenant_id": owner["tenant_id"], "user_id": me["id"]}, "WIPRO")
    body = client.get("/api/positions/board", headers=headers).json()
    assert body["positions"][0]["exit"] == "automatic"
    assert body["exits"] == service.EXITS_NOTE_AUTOMATIC and "Exit now" not in body["exits"]     # no promise it cannot keep
    assert client.post(f"/api/positions/{trade_id}/exit-now", headers=headers).status_code == 403
    # The owner (a trader) still gets the one-click exit on their own position.
    own = _open(owner, "HCLTECH")
    rows = {p["trade_id"]: p for p in client.get("/api/positions/board", headers=owner["headers"]).json()["positions"]}
    assert rows[own]["exit"] == "one_click"


def test_the_board_s_exit_now_check_is_require_trader_s_own():
    """Review LOW: the board shows Exit now exactly when `require_trader` would let the user through - one check."""
    from app.auth import dependencies as deps
    from app.core.enums import UserRole
    import inspect
    assert "may_trade" in inspect.getsource(service.may_use_exit_now)
    assert deps.TRADER_SCOPE == "trading:write" and set(deps.TRADING_ROLES) == {UserRole.OWNER, UserRole.USER, UserRole.STRATEGY_CREATOR}
