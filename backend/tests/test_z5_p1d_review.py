"""Z5 / P1-d review fixes (PR #179), with the reviewer's adversarial cases ported:

- H1: the India VIX gate reads a live reading (LTP + the last completed session's close, stamped today) captured
  through the organisation's data path, end to end from the worker's capture to the gate; Trade timing (open before
  09:20, no valid reading after the grace = spike for bullish entries).
- M1: the console paper-execute answers a locked-out user under approval-only with the reason, not a 500.
- M2: loosening a limit takes effect the next IST day; the lockout is recorded when a close or the worker's sweep
  reaches it, not only when something reads it.
- M3: a time stop already past is refused.
- L1-L9: atomic entry slots, owner-only risk check, overrides through approval counted, plan rows always carry a reason,
  cash underlyings only, exits never touched by any entry check raising.
"""
import asyncio
import json
import os
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.core.models import OHLCVBar
from app.db.models import MarketSnapshotRecord, PaperAccountRecord, RiskLockoutRecord, TradePlanRecord, TradeRecord, User
from app.execution import approval
from app.market_data.calendar import IST
from app.risk_engine import by_default, guardian, vix_gate
from tests.test_auth_api import _session_factory, client
from tests.test_trading_worker import _FakeBroker, _deploy, _get, _worker
from tests.test_z3_approval_entries import _flag_for as _approval_flag
from tests.test_z3_approval_entries import _price, _sig
from tests.test_z3b_paper_account import _member, _paper_flag
from tests.test_z5_p1d_risk_by_default import (VIX_NOW, _closed, _enter, _flag, _open, _org, _place, _plan, _preview, _run, _tenant_t, _vix,
                                               _vix_clock)

UTC = timezone.utc


def _attach(trade_id: int, dep_id: int, **fields) -> None:
    async def go():
        async with _session_factory() as session:
            trade = await session.get(TradeRecord, trade_id)
            trade.deployment_id = dep_id
            for k, v in fields.items():
                setattr(trade, k, v)
            await session.commit()
    _run(go())


def _lockouts(tenant_id: int):
    async def go():
        async with _session_factory() as session:
            return list(await session.scalars(select(RiskLockoutRecord).where(RiskLockoutRecord.tenant_id == tenant_id)))
    return _run(go())


# --- H1: the India VIX gate end to end ----------------------------------------------------------------------------------

class _VixBroker(_FakeBroker):
    """A broker session that quotes India VIX live and serves its completed daily candles (up to yesterday)."""

    def __init__(self, vix: float, prev_close: float, **kw):
        super().__init__(**kw)
        self.vix, self.prev_close = vix, prev_close

    async def get_ltp(self, symbols):
        return {s: (self.vix if "INDIA VIX" in s else self.ltp) for s in symbols}

    async def get_historical_data(self, symbol, exchange, interval, from_date, to_date):
        if symbol == "INDIA VIX" and interval == "day":
            last = to_date.astimezone(IST).date()
            return [OHLCVBar(timestamp=datetime(d.year, d.month, d.day, tzinfo=IST), open=c, high=c, low=c, close=c, volume=0)
                    for d, c in ((last - timedelta(days=1), 12.0), (last, self.prev_close))]
        return await super().get_historical_data(symbol, exchange, interval, from_date, to_date)


def test_the_worker_captures_a_live_vix_reading_and_the_gate_acts_on_it(monkeypatch):
    t = _tenant_t("vix-e2e")
    _org(t)
    _flag(by_default.FLAG, t["tenant_id"])
    _flag(vix_gate.FLAG, t["tenant_id"])
    worker = _worker(monkeypatch, _VixBroker(vix=14.0, prev_close=13.0))            # +7.7% against yesterday's close
    _run(worker.run_cycle(now=VIX_NOW.astimezone(UTC)))

    async def rows():
        async with _session_factory() as session:
            return list(await session.scalars(select(MarketSnapshotRecord).where(MarketSnapshotRecord.tenant_id == t["tenant_id"],
                                                                                 MarketSnapshotRecord.kind == vix_gate.KIND)))
    taken = _run(rows())
    assert len(taken) == 1
    payload = json.loads(taken[0].payload_json)
    assert payload == {"last": 14.0, "prev_close": 13.0, "change_pct": 7.69, "date": VIX_NOW.date().isoformat(), "error": None}

    async def verdicts():
        async with _session_factory() as session:
            long = await vix_gate.check(session, t["tenant_id"], symbol="NIFTY 50", direction="LONG", now=VIX_NOW.astimezone(UTC))
            short = await vix_gate.check(session, t["tenant_id"], symbol="NIFTY", direction="SHORT", now=VIX_NOW.astimezone(UTC))
            return long, short
    long, short = _run(verdicts())
    assert long.state == "spike" and long.reasons and short.reasons == []
    # The next cycle inside the capture interval takes no second reading.
    _run(worker.run_cycle(now=(VIX_NOW + timedelta(minutes=2)).astimezone(UTC)))
    assert len(_run(rows())) == 1


def test_a_failed_capture_is_stored_and_after_the_grace_it_pauses_bullish_entries_only(monkeypatch):
    t = _tenant_t("vix-fail")
    _flag(vix_gate.FLAG, t["tenant_id"])

    class _Down:
        async def get_ltp(self, *a, **k):
            raise ConnectionError("feed down")

        async def get_candles(self, *a, **k):
            return []

    async def go():
        async with _session_factory() as session:
            at = (VIX_NOW.replace(hour=9, minute=21)).astimezone(UTC)
            await vix_gate.capture(session, t["tenant_id"], _Down(), now=at)
            readings = await vix_gate.readings_today(session, t["tenant_id"], VIX_NOW.astimezone(UTC))
            before = await vix_gate.check(session, t["tenant_id"], symbol="NIFTY", direction="LONG", now=VIX_NOW.replace(hour=9, minute=19).astimezone(UTC))
            after = await vix_gate.check(session, t["tenant_id"], symbol="NIFTY", direction="LONG", now=VIX_NOW.astimezone(UTC))
            bear = await vix_gate.check(session, t["tenant_id"], symbol="NIFTY", direction="SHORT", now=VIX_NOW.astimezone(UTC))
            return readings, before, after, bear
    readings, before, after, bear = _run(go())
    assert readings[0]["last"] is None and "feed down" in readings[0]["error"]
    assert before.reasons == [] and before.state == "before_check"
    assert after.state == "no_reading" and "feed down" in after.reasons[0]
    assert bear.reasons == []


def test_with_byob_on_the_reading_uses_only_the_users_own_entitlement(monkeypatch):
    """BT1: with byob_data_tier on, India VIX comes only through the acting user's own Tier A session; without one the
    reading is stored as failed (never through the organisation's or a teammate's session)."""
    from app.market_data import entitlement
    t = _tenant_t("vix-byob")
    _flag("byob_data_tier", t["tenant_id"])

    async def no_tier_a(session, user, symbol, exchange="NSE", segment=None, *, register=True):
        return entitlement.Entitlement(tier=entitlement.DataTier.EOD, symbol=symbol, exchange=exchange, reason="Link your own broker for live data")
    monkeypatch.setattr(entitlement, "resolve", no_tier_a)

    class _OrgSession:
        name = "org"

        async def get_ltp(self, *a, **k):
            raise AssertionError("the organisation's session must not be used under BT1")

    async def go():
        async with _session_factory() as session:
            user = await session.get(User, t["user_id"])
            row = await vix_gate.capture_for_tenant(session, t["tenant_id"], user, org_adapter=_OrgSession(), market_data_factory=lambda b, **k: b,
                                                    now=VIX_NOW.astimezone(UTC))
            return json.loads(row.payload_json)
    payload = _run(go())
    assert payload["last"] is None and "own broker" in payload["error"]


def test_reading_from_takes_the_last_completed_session_before_today():
    now = VIX_NOW.astimezone(UTC)
    bars = [OHLCVBar(timestamp=datetime(2026, 10, d, tzinfo=IST), open=1, high=1, low=1, close=c, volume=0) for d, c in ((7, 12.0), (8, 13.0), (9, 99.0))]
    assert vix_gate.reading_from(14.0, bars, now) == {"last": 14.0, "prev_close": 13.0, "change_pct": 7.69, "date": "2026-10-09"}
    assert vix_gate.reading_from(None, bars, now)["change_pct"] is None
    assert vix_gate.due(None, VIX_NOW.replace(hour=9, minute=19).astimezone(UTC)) is False
    assert vix_gate.due(None, VIX_NOW.astimezone(UTC)) is True
    assert vix_gate.due(VIX_NOW.astimezone(UTC), (VIX_NOW + timedelta(minutes=5)).astimezone(UTC)) is False


def test_market_memory_cue_rows_are_not_gate_readings():
    """The reviewer's finding: a CUE row comes from completed daily bars; the gate reads only live readings now."""
    from app.ai import market_memory
    t = _tenant_t("vix-cue")
    now = VIX_NOW.astimezone(UTC)
    bars = [OHLCVBar(timestamp=datetime(2026, 10, d, tzinfo=IST), open=c, high=c, low=c, close=c, volume=0) for d, c in ((7, 13.0), (8, 20.0))]
    row = market_memory.cue_snapshot(t["tenant_id"], "INDIA VIX", "NSE", bars, "upstox", now)

    async def go():
        async with _session_factory() as session:
            session.add(row)
            await session.commit()
            return await vix_gate.readings_today(session, t["tenant_id"], now)
    assert _run(go()) == []


# --- exits are never touched ---------------------------------------------------------------------------------------------

async def _aboom(*a, **k):
    raise RuntimeError("entry check exploded")


def test_exit_now_and_stop_survive_every_entry_check_raising(monkeypatch):
    """ADR-0004 (reviewer case): with the lockout, the entries limit, the VIX gate and the guardian all raising, exits close."""
    t = _tenant_t("adv-raise")
    _org(t)
    _flag(by_default.FLAG, t["tenant_id"])
    _flag(vix_gate.FLAG, t["tenant_id"])
    _vix_clock(monkeypatch)
    _vix(t, 30.0, 20.0)
    _closed(t, -25_000.0)
    a = _open(t, symbol="NIFTY")
    b = _open(t, stop=95.0)
    _attach(b, _deploy(t))
    for name in ("lockout", "entry_refusals", "proposal_refusal", "reserve_entry", "after_close", "sweep"):
        monkeypatch.setattr(by_default, name, _aboom)
    monkeypatch.setattr(vix_gate, "check", _aboom)
    monkeypatch.setattr(guardian, "guard_entry", _aboom)
    _paper_flag(t["tenant_id"])
    _price(monkeypatch, 99.0)
    assert client.post(f"/api/positions/{a}/exit-now", headers=t["headers"]).status_code == 200
    worker = _worker(monkeypatch, _FakeBroker(ltp=94.0))
    _run(worker.run_cycle(now=datetime(2026, 9, 25, 10, 30, tzinfo=IST)))
    assert _get(TradeRecord, b).exit_reason == "Stop Loss"


def test_time_exit_of_a_locked_user_still_runs(monkeypatch):
    t = _tenant_t("adv-time")
    _org(t)
    _flag(by_default.FLAG, t["tenant_id"])
    _closed(t, -25_000.0)
    assert _preview(t)["can_place"] is False
    trade_id = _open(t, stop=50.0)
    cycle_at = datetime(2026, 9, 25, 10, 30, tzinfo=IST)
    # The position was opened before the cycle on the cycle's own day: the time exit is judged on the cycle's clock
    # (`monitor_open_positions(now=...)`), never on the wall clock the suite happens to run at.
    _attach(trade_id, _deploy(t), exit_rules=json.dumps({"time_exit_at": "10:00"}), entry_time=datetime(2026, 9, 25, 9, 30, tzinfo=IST))
    worker = _worker(monkeypatch, _FakeBroker(ltp=100.0))
    _run(worker.run_cycle(now=cycle_at))
    assert (_get(TradeRecord, trade_id).exit_reason or "").startswith("Time exit")


# --- M1: console under approval-only for a locked user -----------------------------------------------------------------

def test_console_paper_execute_for_a_locked_user_answers_with_the_reason(monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.strategy_engine.registry import registry
    from tests.test_trading_api import _fake_long_signal, _sample_candles_payload
    monkeypatch.setattr(registry.get("ema_rsi_scalper_1m"), "analyze", lambda data, symbol: _fake_long_signal())
    t = _tenant_t("adv-console")
    _org(t)
    _flag(by_default.FLAG, t["tenant_id"])
    _approval_flag(t["tenant_id"])
    _closed(t, -25_000.0)
    local = TestClient(app, raise_server_exceptions=False)
    out = local.post("/api/strategies/ema_rsi_scalper_1m/paper-execute", headers=t["headers"],
                     json={"symbol": "TESTSYM", "candles": {"1min": _sample_candles_payload()}})
    assert out.status_code == 200, out.text
    assert out.json()["executed"] is False and any("Daily loss limit" in r for r in out.json()["reasons"])


# --- M2: no loosening mid-day; the lockout recorded eagerly ------------------------------------------------------------

def test_loosening_waits_for_the_next_day_and_tightening_applies_at_once():
    today = date(2026, 10, 9)
    base = by_default.RiskSettings.defaults().to_json()
    text, applied, deferred, start = by_default.plan_change(base, {"daily_loss_pct": 5.0, "min_rr": 2.0}, today)
    assert applied == {"min_rr": 2.0} and deferred == {"daily_loss_pct": 5.0} and start == "2026-10-10"
    assert by_default.settings_on(text, today).daily_loss_pct == 2.0 and by_default.settings_on(text, today).min_rr == 2.0
    assert by_default.settings_on(text, date(2026, 10, 10)).daily_loss_pct == 5.0
    # A tightening of the same key cancels a loosening still waiting.
    text2, applied2, deferred2, _ = by_default.plan_change(text, {"daily_loss_pct": 1.0}, today)
    assert applied2 == {"daily_loss_pct": 1.0} and deferred2 == {} and by_default.settings_on(text2, date(2026, 10, 12)).daily_loss_pct == 1.0
    for key, old, new, loose in (("max_trades_per_day", 10, 0, True), ("max_trades_per_day", 0, 5, False), ("max_trades_per_day", 5, 8, True),
                                 ("risk_per_trade_amount", 5000, 0, True), ("risk_per_trade_amount", 0, 5000, False),
                                 ("risk_per_trade_pct", 1.0, 1.5, True), ("min_rr", 1.5, 1.0, True), ("min_rr", 1.5, 2.0, False)):
        assert by_default.loosens(key, old, new) is loose, (key, old, new)


def test_user_cannot_raise_the_daily_limit_or_clear_the_entries_limit_mid_day():
    t = _tenant_t("adv-loosen")
    _org(t)
    _flag(by_default.FLAG, t["tenant_id"])
    _closed(t, -25_000.0)                                          # 2.5% of 10 lakh: past the 2% default
    out = client.put("/api/paper/risk", headers=t["headers"], json={"daily_loss_pct": 5.0})
    assert out.status_code == 200 and out.json()["change"]["deferred"] == {"daily_loss_pct": 5.0}
    assert out.json()["settings"]["daily_loss_pct"] == 2.0 and out.json()["pending"]["values"] == {"daily_loss_pct": 5.0}
    assert client.get("/api/paper/risk", headers=t["headers"]).json()["lockout"] is not None
    assert _enter(t["user_id"])[0] is False

    u = _tenant_t("adv-perday")
    _org(u)
    _flag(by_default.FLAG, u["tenant_id"])
    assert client.put("/api/paper/risk", headers=u["headers"], json={"max_trades_per_day": 1}).json()["change"]["applied"] == {"max_trades_per_day": 1}
    assert _enter(u["user_id"])[0] is True
    assert _enter(u["user_id"])[0] is False
    assert client.put("/api/paper/risk", headers=u["headers"], json={"max_trades_per_day": 0}).json()["change"]["deferred"] == {"max_trades_per_day": 0}
    assert _enter(u["user_id"])[0] is False                       # the cleared limit starts tomorrow


def test_recorded_lockout_survives_a_settings_change():
    t = _tenant_t("adv-keep")
    _org(t)
    _flag(by_default.FLAG, t["tenant_id"])
    _closed(t, -25_000.0)
    assert client.get("/api/paper/risk", headers=t["headers"]).json()["lockout"] is not None
    client.put("/api/paper/risk", headers=t["headers"], json={"daily_loss_pct": 5.0})
    assert client.get("/api/paper/risk", headers=t["headers"]).json()["lockout"] is not None
    assert _enter(t["user_id"])[0] is False


def test_a_close_that_reaches_the_limit_records_the_lockout_without_any_read(monkeypatch):
    t = _tenant_t("eager-close")
    _org(t)
    _flag(by_default.FLAG, t["tenant_id"])
    _closed(t, -15_000.0)                                          # 1.5%: under the 2% limit
    trade_id = _open(t, entry=100.0, stop=50.0)

    async def big():
        async with _session_factory() as session:
            trade = await session.get(TradeRecord, trade_id)
            trade.quantity = 2000.0                                # -6,000 at 97: -21,000 for the day
            await session.commit()
    _run(big())
    _paper_flag(t["tenant_id"])
    _price(monkeypatch, 97.0)
    assert _lockouts(t["tenant_id"]) == []
    assert client.post(f"/api/positions/{trade_id}/exit-now", headers=t["headers"]).status_code == 200
    rows = _lockouts(t["tenant_id"])
    assert len(rows) == 1 and rows[0].user_id == t["user_id"]


def test_the_worker_sweep_records_a_lockout_reached_through_open_marks():
    t = _tenant_t("eager-sweep")
    _org(t)
    _flag(by_default.FLAG, t["tenant_id"])
    trade_id = _open(t, entry=100.0, stop=50.0)

    async def marked():
        async with _session_factory() as session:
            trade = await session.get(TradeRecord, trade_id)
            trade.quantity, trade.mark_price, trade.mark_time = 2000.0, 88.0, datetime.now(UTC)   # -24,000 marked today
            await session.commit()
            return await by_default.sweep(session)
    assert _run(marked()) >= 1
    assert len(_lockouts(t["tenant_id"])) == 1
    assert _enter(t["user_id"])[0] is False


# --- M3, L7: the ticket's own checks -----------------------------------------------------------------------------------

def test_a_time_stop_already_past_is_refused(monkeypatch):
    from app.paper import ticket
    t = _tenant_t("adv-ts")
    _org(t)
    _flag(by_default.FLAG, t["tenant_id"])
    monkeypatch.setattr(ticket, "wall_clock", lambda: datetime(2026, 10, 9, 14, 0, tzinfo=IST))
    view = _preview(t, time_stop="09:20")
    assert not view["can_place"] and any("already past" in e for e in view["errors"])
    assert any("already past" in e for e in _preview(t, time_stop="14:03")["errors"])       # inside the 5-minute lead
    assert _preview(t, time_stop="14:30")["can_place"]


def test_the_ticket_takes_cash_underlyings_only_and_rejects_bad_input():
    t = _tenant_t("adv-iso")
    _org(t)
    _flag(by_default.FLAG, t["tenant_id"])
    for symbol in ("NIFTY24OCT25000CE", "BANKNIFTY24OCTFUT", "GOLD"):
        view = _preview(t, symbol=symbol)
        assert not view["can_place"] and any("cash underlyings" in e for e in view["errors"]), symbol
    assert client.post("/api/paper/ticket/preview", headers=t["headers"], json={**_plan(), "mode": "LIVE"}).status_code == 422
    assert client.post("/api/paper/ticket/preview", headers=t["headers"], json={**_plan(), "user_id": 1}).status_code == 422
    for field, value in (("entry", -1), ("quantity", 0), ("stop_loss", 1e8)):
        assert client.post("/api/paper/ticket/preview", headers=t["headers"], json={**_plan(), field: value}).status_code == 422, field
    nan = client.post("/api/paper/ticket/preview", headers=t["headers"], content=json.dumps({**_plan(), "entry": float("nan")}))
    assert nan.status_code == 422


def test_sizing_edges():
    assert by_default.size(100.0, 100.0, 10_000.0, 1)["quantity"] == 0
    assert by_default.size(1e7 - 1, 1.0, 10_000.0, 50)["lots"] == 0
    assert by_default.size(100.0, 98.0, 10_000.0, 0)["quantity"] == 5000
    s = by_default.size(100.0, 98.0, 10_000.0, 75)
    assert s["lots"] == 66 and s["quantity"] == 4950 and s["risk_amount"] <= 10_000.0
    assert by_default.size(100.0, 98.0, -5.0, 1)["quantity"] == 0


# --- L1: atomic entry slots ---------------------------------------------------------------------------------------------

@pytest.fixture(params=["sqlite", "postgresql"])
def slot_db_url(request, tmp_path):
    """A private database with a real connection pool: concurrent sessions must not share the suite's single in-memory
    connection (that would break it for every later test)."""
    if request.param == "sqlite":
        return f"sqlite+aiosqlite:///{tmp_path / 'slots.db'}"
    url = os.environ.get("Z5_TEST_PG_URL")
    if not url:
        pytest.skip("Z5_TEST_PG_URL not set")
    return url


def test_entries_per_day_slot_is_atomic(slot_db_url):
    """Two entries of one user at the same moment with one slot left: exactly one takes it (the conditional UPDATE on the
    PAPER account row is the serialisation point; the second waits and then sees the slot gone)."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import AsyncAdaptedQueuePool
    from app.db.base import Base
    from app.db.models import Tenant

    async def go():
        engine = create_async_engine(slot_db_url, poolclass=AsyncAdaptedQueuePool, pool_size=2, max_overflow=0)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with factory() as s:
                tenant = Tenant(name="z5 slots")
                s.add(tenant)
                await s.flush()
                user = User(tenant_id=tenant.id, email=f"z5-slots-{uuid.uuid4().hex[:10]}@example.com", hashed_password="x")
                s.add(user)
                await s.flush()
                s.add(PaperAccountRecord(tenant_id=tenant.id, user_id=user.id, starting_capital=1_000_000, paused=False))
                await s.commit()
            gate = asyncio.Event()

            async def one(hold: bool):
                async with factory() as s:
                    me = await s.get(User, user.id)
                    if not hold:
                        await gate.wait()                       # the second starts while the first still holds its slot
                    refused = await by_default.reserve_entry(s, me, 1)
                    if hold:
                        gate.set()
                        await asyncio.sleep(0.3)                # keep the transaction open: the other must wait for it
                    await s.commit()
                    return refused
            first, second = await asyncio.gather(one(True), one(False))
            assert first is None and second is not None and "entries per day" in second
            async with factory() as s:
                assert await s.scalar(select(PaperAccountRecord.entries_count).where(PaperAccountRecord.user_id == user.id)) == 1
        finally:
            await engine.dispose()
    asyncio.run(go())


def test_an_entry_refused_later_gives_its_slot_back():
    u = _tenant_t("slots-release")
    _org(u)
    _flag(by_default.FLAG, u["tenant_id"])
    client.put("/api/paper/risk", headers=u["headers"], json={"max_trades_per_day": 1})
    async def refused_later():
        from app.execution.signal_execution import execute_signal_for_user
        async with _session_factory() as session:
            user = await session.get(User, u["user_id"])
            low_rr = _sig().model_copy(update={"risk_reward": 0.5})   # under the organisation's minimum: refused after the slot
            result, _ = await execute_signal_for_user(session, user, mode="PAPER", strategy_id="ema_rsi_scalper_1m", signal=low_rr,
                                                      idempotency_key=f"slot:{uuid.uuid4().hex}")
            return result.executed, result.reasons
    executed, reasons = _run(refused_later())
    assert executed is False and any("Risk/Reward" in r for r in reasons)

    async def used():
        async with _session_factory() as session:
            return await session.scalar(select(PaperAccountRecord.entries_count).where(PaperAccountRecord.user_id == u["user_id"]))
    assert _run(used()) == 0                                      # the slot came back
    assert _enter(u["user_id"])[0] is True


# --- L2, L3, L4: proposal card owner, approvals counted, plans always say why ---------------------------------------

def test_risk_check_is_shown_to_the_proposals_owner_only(monkeypatch):
    t = _tenant_t("card-owner")
    _org(t)
    _flag(by_default.FLAG, t["tenant_id"])
    member = _member(t)

    async def propose():
        async with _session_factory() as session:
            row = await approval.propose_entry(session, t["tenant_id"], signal=_sig(), strategy_id="x", source="tradingview",
                                               idempotency_key=f"own:{uuid.uuid4().hex}", user_id=t["user_id"])
            return row.id
    action_id = _run(propose())

    def card(headers):
        return next(r for r in client.get("/api/ai/actions", headers=headers).json() if r["id"] == action_id)
    assert card(t["headers"])["risk_check"]["sizing"]["suggested_quantity"] > 0
    assert card(member["headers"])["risk_check"] is None


def test_an_override_through_approval_is_placed_and_counted(monkeypatch):
    t = _tenant_t("adv-ovr")
    _org(t, risk_pct=2.0)
    _flag(by_default.FLAG, t["tenant_id"])
    _approval_flag(t["tenant_id"])
    _price(monkeypatch, 100.0)
    out = _place(t, quantity=6000, override=True, override_reason="High conviction setup, documented")
    assert out["status"] == "PROPOSED" and out["reasons"][0].startswith("Sent for approval: proposal #")
    assert client.post(f"/api/ai/actions/{out['proposal']['id']}/approve", headers=t["headers"], json={}).status_code == 200
    plan = client.get("/api/paper/plans", headers=t["headers"]).json()[0]
    assert plan["status"] == "PLACED" and plan["filled"]["quantity"] == 6000 and plan["order_id"]
    assert client.get("/api/paper/risk", headers=t["headers"]).json()["overrides"] == {"today": 1, "total": 1}


def test_plan_rows_always_carry_a_reason(monkeypatch):
    t = _tenant_t("plan-why")
    _org(t)
    _flag(by_default.FLAG, t["tenant_id"])
    _price(monkeypatch, 100.0)

    async def boom(*a, **k):
        raise RuntimeError("pipeline exploded")
    import app.execution.signal_execution as pipeline
    monkeypatch.setattr(pipeline, "execute_signal_for_user", boom)
    out = _place(t)
    assert out["status"] == "FAILED" and "pipeline exploded" in out["reasons"][0]
    monkeypatch.undo()

    # A duplicate proposal (the same plan id raised twice) says so.
    u = _tenant_t("plan-dup")
    _org(u)
    _flag(by_default.FLAG, u["tenant_id"])
    _approval_flag(u["tenant_id"])
    _price(monkeypatch, 100.0)

    async def none(*a, **k):
        return None
    monkeypatch.setattr(approval, "propose_entry", none)
    out = _place(u)
    assert out["status"] == "REFUSED" and "already proposed" in out["reasons"][0]

    async def plans():
        async with _session_factory() as session:
            return list(await session.scalars(select(TradePlanRecord).where(TradePlanRecord.tenant_id.in_((t["tenant_id"], u["tenant_id"])))))
    assert all(json.loads(p.reasons_json) for p in _run(plans()))
