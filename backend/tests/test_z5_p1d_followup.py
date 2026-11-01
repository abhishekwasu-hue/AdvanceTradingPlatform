"""Z5 / P1-d follow-up (verification findings on #179):

1. The VIX gate's live reading is not Copilot market memory (no "INDIA VIX" symbol snapshot, no history, no freshness),
   and under BT1b its raw values reach only the member whose own session took it; others get the state and reason.
2. With byob on, the reading tries each active member's own Tier A session until one reads.
3. An approved ticket proposal whose time stop is no longer ahead of now + the lead is not placed.
4. A capture that raises before reading stores a failed reading (or retries next cycle), never silently waits 15 minutes.
6. The ticket's cash-underlying check follows the instrument master when it knows the symbol.
"""
import json
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.ai import market_memory
from app.db.models import AiActionRecord, InstrumentRecord, MarketSnapshotRecord, TradePlanRecord, User
from app.execution import approval
from app.market_data.calendar import IST
from app.risk_engine import by_default, vix_gate
from tests.def0_legacy import own_vix
from tests.test_auth_api import _session_factory, client
from tests.test_trading_worker import _FakeBroker, _worker
from tests.test_z3_approval_entries import _flag_for as _approval_flag
from tests.test_z3_approval_entries import _price
from tests.test_z3b_paper_account import _member
from tests.test_z5_p1d_risk_by_default import VIX_NOW, _flag, _org, _place, _preview, _run, _tenant_t, _vix_clock

UTC = timezone.utc
NOW = VIX_NOW.astimezone(UTC)


def _store(t, *, last=14.0, prev=13.0, reader=None, error=None, minutes_ago=5):
    async def go():
        async with _session_factory() as session:
            reading = {"last": last, "prev_close": prev, "change_pct": round((last / prev - 1) * 100, 2) if last and prev else None,
                       "date": VIX_NOW.date().isoformat(), "error": error}
            await vix_gate.capture(session, t["tenant_id"], None, now=NOW - timedelta(minutes=minutes_ago), source="upstox",
                                   reader_user_id=reader, reading=reading)
    _run(go())


# --- 1. not market memory; BT1b on the raw values ----------------------------------------------------------------------

def test_vix_readings_are_not_copilot_market_memory():
    t = _tenant_t("fu-memory")
    _store(t)

    async def go():
        async with _session_factory() as session:
            return await market_memory.latest(session, t["tenant_id"], now=NOW)
    memory = _run(go())
    assert all(s["symbol"] != "INDIA VIX" for s in memory["symbols"]) and "INDIA VIX" not in memory["history"]
    assert memory["updated_at"] is None                              # a VIX reading never makes the memory look fresh


def test_under_bt1b_only_the_reader_sees_india_vix_values(monkeypatch):
    owner = _tenant_t("fu-bt1b")
    _org(owner)
    member = _member(owner)
    for flag in (by_default.FLAG, vix_gate.FLAG, "byob_data_tier"):
        _flag(flag, owner["tenant_id"])
    own_vix(client, owner["headers"])                                     # M68 PR-5: each user's own India VIX check
    own_vix(client, member["headers"])
    _vix_clock(monkeypatch)
    _store(owner, last=14.0, prev=13.0, reader=owner["user_id"])         # taken through the owner's own session

    mine = client.get("/api/paper/risk", headers=owner["headers"]).json()["vix_gate"]
    theirs = client.get("/api/paper/risk", headers=member["headers"]).json()["vix_gate"]
    assert mine["latest"]["last"] == 14.0 and any("+7.7%" in m for m in mine["messages"])
    assert theirs["latest"] is None and theirs["bullish_paused"] is True and theirs["state"] == "spike"
    assert theirs["messages"] and not any("7.7" in m or "14" in m for m in theirs["messages"])
    refusal = next(r for r in _preview(member, symbol="NIFTY")["refusals"] if "India VIX" in r)
    assert "7.7" not in refusal and "at least 5%" in refusal                 # the same verdict, without the values
    assert any("+7.7%" in r for r in _preview(owner, symbol="NIFTY")["refusals"])


# --- 2. byob: each member's own session in turn -------------------------------------------------------------------------

def test_with_byob_the_reading_falls_back_to_another_members_own_session(monkeypatch):
    from app.brokers import token_lifecycle
    from app.market_data import entitlement
    owner = _tenant_t("fu-members")
    member = _member(owner)
    _flag("byob_data_tier", owner["tenant_id"])

    class _Record:
        broker_name = "upstox"

    async def resolve(session, user, symbol, exchange="NSE", segment=None, *, register=True):
        if user.id == member["user_id"]:
            return entitlement.Entitlement(tier=entitlement.DataTier.LIVE_OWN_BROKER, symbol=symbol, exchange=exchange, record=_Record())
        return entitlement.Entitlement(tier=entitlement.DataTier.EOD, symbol=symbol, exchange=exchange, reason="owner has no own broker")
    monkeypatch.setattr(entitlement, "resolve", resolve)
    monkeypatch.setattr(token_lifecycle, "build_adapter", lambda record, client=None: "member-session")

    class _Service:
        def __init__(self, adapter, **kw):
            assert adapter == "member-session"

        async def get_ltp(self, *a, **k):
            return 14.0

        async def get_candles(self, *a, **k):
            from app.core.models import OHLCVBar
            day = (VIX_NOW - timedelta(days=1)).date()
            return [OHLCVBar(timestamp=datetime(day.year, day.month, day.day, tzinfo=IST), open=13, high=13, low=13, close=13.0, volume=0)]

    async def go():
        async with _session_factory() as session:
            owner_user = await session.get(User, owner["user_id"])
            members = list(await session.scalars(select(User).where(User.tenant_id == owner["tenant_id"])))
            row = await vix_gate.capture_for_tenant(session, owner["tenant_id"], owner_user, org_adapter=None, market_data_factory=_Service,
                                                    now=NOW, members=members)
            return json.loads(row.payload_json)
    payload = _run(go())
    assert payload["error"] is None and payload["last"] == 14.0 and payload["reader_user_id"] == member["user_id"]


# --- 3. a stale time stop at approval -----------------------------------------------------------------------------------

def test_an_approved_ticket_whose_time_stop_has_passed_is_not_placed(monkeypatch):
    from app.paper import ticket
    t = _tenant_t("fu-stale")
    _org(t)
    _flag(by_default.FLAG, t["tenant_id"])
    _approval_flag(t["tenant_id"])
    _price(monkeypatch, 100.0)
    monkeypatch.setattr(ticket, "wall_clock", lambda: datetime(2026, 10, 9, 10, 0, tzinfo=IST))
    out = _place(t, time_stop="11:00")
    assert out["status"] == "PROPOSED"

    async def approve_late():
        async with _session_factory() as session:
            action = await session.get(AiActionRecord, out["proposal"]["id"])
            action.status = "APPROVED"
            await session.commit()
            user = await session.get(User, t["user_id"])

            async def lookup(symbol, exchange="NSE"):
                return 100.0
            done = await approval.execute_entry(session, action, user, price_lookup=lookup,
                                                now=datetime(2026, 10, 9, 11, 0, tzinfo=IST).astimezone(UTC))   # M68 PR-3: not later than now
            plan = await session.get(TradePlanRecord, out["id"])
            return done.status, done.result, plan.status
    status, result, plan_status = _run(approve_late())
    assert status == "FAILED" and "time stop 11:00" in result and plan_status == "FAILED"
    early = {"ticket": {"plan_id": 1}, "exit_rules": json.dumps({"time_exit_at": "11:00"})}
    assert approval.ticket_time_stop_passed(early, datetime(2026, 10, 9, 10, 40, tzinfo=IST)) is None
    assert approval.ticket_time_stop_passed(early, datetime(2026, 10, 9, 10, 58, tzinfo=IST)) is None   # M68 PR-3: no 5-minute lead
    assert approval.ticket_time_stop_passed({"exit_rules": json.dumps({"time_exit_at": "09:30"})}, datetime(2026, 10, 9, 10, 40, tzinfo=IST)) is None


# --- 4. a capture that raises -----------------------------------------------------------------------------------------

def test_a_capture_that_raises_stores_a_failed_reading_or_retries_next_cycle(monkeypatch):
    t = _tenant_t("fu-raise")
    _flag(vix_gate.FLAG, t["tenant_id"])
    worker = _worker(monkeypatch, _FakeBroker())

    async def boom(*a, **k):
        raise RuntimeError("acting user lookup failed")
    monkeypatch.setattr(worker, "_acting_user", boom)

    async def run():
        async with _session_factory() as session:
            await worker._vix_readings(session, NOW)
            return list(await session.scalars(select(MarketSnapshotRecord).where(MarketSnapshotRecord.tenant_id == t["tenant_id"],
                                                                                 MarketSnapshotRecord.kind == vix_gate.KIND)))
    rows = _run(run())
    assert len(rows) == 1 and "acting user lookup failed" in json.loads(rows[0].payload_json)["error"]
    assert worker._last_vix.get(t["tenant_id"]) == NOW

    # When even the failed reading cannot be stored, nothing marks it taken: the next cycle tries again.
    u = _tenant_t("fu-raise2")
    _flag(vix_gate.FLAG, u["tenant_id"])

    async def no_store(*a, **k):
        raise RuntimeError("database unavailable")
    monkeypatch.setattr(vix_gate, "capture", no_store)
    _run(run())
    assert u["tenant_id"] not in worker._last_vix


# --- 6. the instrument master decides what is a cash underlying -------------------------------------------------------

def test_the_ticket_follows_the_instrument_master_for_cash_underlyings():
    t = _tenant_t("fu-master")
    _org(t)
    _flag(by_default.FLAG, t["tenant_id"])
    tag = uuid.uuid4().hex[:6].upper()
    equity, future = f"EQ{tag}", f"FN{tag}"

    # A broker name of its own, removed afterwards: the shared test database's real masters (and the worker tests that
    # read them) must never see these rows.
    broker = f"z5test{tag.lower()}"

    async def seed():
        async with _session_factory() as session:
            session.add(InstrumentRecord(broker=broker, exchange="NSE", instrument_key=f"NSE_EQ|{equity}", tradingsymbol=equity, instrument_type="EQ"))
            session.add(InstrumentRecord(broker=broker, exchange="NFO", instrument_key=f"NSE_FO|{future}", tradingsymbol=future, instrument_type="FUT"))
            await session.commit()

    async def clean():
        from sqlalchemy import delete
        async with _session_factory() as session:
            await session.execute(delete(InstrumentRecord).where(InstrumentRecord.broker == broker))
            await session.commit()
    _run(seed())
    try:
        assert _preview(t, symbol=equity)["can_place"]
        assert any("cash underlyings" in e for e in _preview(t, symbol=future)["errors"])      # the master says FUT, no regex needed
        assert _preview(t, symbol="RELIANCE")["can_place"]                                       # not in this master: the fallback check
        assert any("cash underlyings" in e for e in _preview(t, symbol="NIFTY24OCT25000CE")["errors"])
    finally:
        _run(clean())


def test_the_readers_own_refused_order_stores_and_notifies_no_vix_values(monkeypatch):
    """BT1b: an order's reasons go on the order trail (GET /api/orders, tenant-wide) and into the org-wide RISK_REJECTION
    notification, so even when the entering user took the reading they are worded without India VIX's values."""
    from app.db.models import NotificationRecord, OrderRecord
    from tests.test_z5_p1d_risk_by_default import _enter
    owner = _tenant_t("fu-trail")
    _org(owner)
    for flag in (by_default.FLAG, vix_gate.FLAG, "byob_data_tier"):
        _flag(flag, owner["tenant_id"])
    own_vix(client, owner["headers"])
    _vix_clock(monkeypatch)
    _store(owner, last=14.0, prev=13.0, reader=owner["user_id"])
    executed, reasons, _ = _enter(owner["user_id"], symbol="NIFTY")
    assert not executed and any("India VIX gate" in r for r in reasons)

    async def trail():
        async with _session_factory() as session:
            orders = list(await session.scalars(select(OrderRecord).where(OrderRecord.tenant_id == owner["tenant_id"])))
            notes = list(await session.scalars(select(NotificationRecord).where(NotificationRecord.tenant_id == owner["tenant_id"],
                                                                                 NotificationRecord.event_type == "RISK_REJECTION")))
            return [o.reasons_json for o in orders], [n.message for n in notes]
    stored, notified = _run(trail())
    texts = [t for t in stored + notified if t and "India VIX" in t]
    assert texts and all("7.7" not in t and "14.0" not in t and "13.0" not in t for t in texts)
    # The reader still sees the values where only they look: their own status strip and ticket preview.
    assert any("+7.7%" in m for m in client.get("/api/paper/risk", headers=owner["headers"]).json()["vix_gate"]["messages"])
