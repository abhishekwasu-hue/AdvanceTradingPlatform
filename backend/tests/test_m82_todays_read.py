"""M82 / M83: the Copilot "Today's read" card shows the true state - the last stored read with its session date when the
market is closed (R2), the one exact reason when there is none (R3), and "Read now" for this user only, disabled with the
reason without a valid broker session of his own (R4). Server-side: the brief's `read` block and POST
/api/ai/market-memory/refresh (the worker's own read, the existing endpoint and rate limiter)."""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.ai import briefing
from app.ai import market_memory as mm
from app.auth.security import hash_password
from app.db.models import BrokerCredentialRecord, MarketSnapshotRecord, TraderProfileRecord, User, WorkerHeartbeatRecord
from tests.test_auth_api import _register, _session_factory, client
from tests.test_phase_ar_market_memory import _Broker, _FakeMD

UTC = timezone.utc
SUNDAY = datetime(2026, 11, 1, 6, 30, tzinfo=UTC)        # Sun 1 Nov, 12:00 IST - market closed
FRIDAY_CLOSE_READ = datetime(2026, 10, 30, 10, 5, tzinfo=UTC)   # Fri 30 Oct, 15:35 IST - the day's final read
WEDNESDAY_READ = datetime(2026, 10, 28, 9, 0, tzinfo=UTC)       # Wed 28 Oct - older than latest()'s 3-day window
BROKER_WORDS = "needs a broker session"


def _run(coro):
    return asyncio.run(coro)


def _tenant(email):
    headers = {"Authorization": f"Bearer {_register(email)}"}
    me = client.get("/api/auth/me", headers=headers).json()
    return headers, me["tenant_id"], me["id"]


def _teammate(tenant_id: int, email: str, role: str = "USER") -> dict:
    async def go():
        async with _session_factory() as session:
            session.add(User(tenant_id=tenant_id, email=email, hashed_password=hash_password("S3cur3Pass!"), role=role, is_active=True))
            await session.commit()
    _run(go())
    token = client.post("/api/auth/login", json={"email": email, "password": "S3cur3Pass!"}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _add(*rows):
    async def go():
        async with _session_factory() as session:
            session.add_all(rows)
            await session.commit()
    _run(go())


def _symbol(tenant_id, at, symbol="NIFTY 50", regime="TRENDING_UP"):
    return MarketSnapshotRecord(tenant_id=tenant_id, kind="SYMBOL", symbol=symbol, exchange="NSE", timeframe="5min", source="upstox",
                                bias="BULLISH", regime=regime, higher_regime="TRENDING_UP", structure="UPTREND", last_price=25000.0,
                                change_pct=0.4, payload_json="{}", captured_at=at)


def _credential(tenant_id, user_id, status="VALID"):
    return BrokerCredentialRecord(tenant_id=tenant_id, user_id=user_id, broker_name="upstox", account_label="primary", encrypted_payload="x",
                                  token_status=status, token_expires_at=datetime.now(UTC) + (timedelta(hours=12) if status == "VALID"
                                                                                            else -timedelta(hours=12)))


def _profile(tenant_id, user_id):
    return TraderProfileRecord(tenant_id=tenant_id, user_id=user_id, answers_json="{}", preferences_json="{}")


def _brief(tenant_id, user_id, now=SUNDAY):
    async def go():
        async with _session_factory() as session:
            user = await session.get(User, user_id)
            return await briefing.build(session, user, now=now)
    return _run(go())


def _no_broker_words(brief):
    assert BROKER_WORDS not in str(brief)


@pytest.fixture
def worker_ran_friday():
    """The trading worker's heartbeat spans Friday's session (the table holds one row per worker, shared by the tests)."""
    async def go(started, seen):
        async with _session_factory() as session:
            row = await session.scalar(select(WorkerHeartbeatRecord).where(WorkerHeartbeatRecord.worker_name == "trading_worker"))
            old = None if row is None else (row.started_at, row.last_seen_at)
            if row is None:
                row = WorkerHeartbeatRecord(worker_name="trading_worker", cycle_count=1)
                session.add(row)
            row.started_at, row.last_seen_at = started, seen
            await session.commit()
            return old
    old = _run(go(datetime(2026, 10, 29, 0, 0, tzinfo=UTC), SUNDAY))
    yield
    if old is not None:
        _run(go(*old))


# --- R2: the market is closed and a read is stored ----------------------------------------------------------------------

def test_closed_market_shows_the_last_session_read_with_its_date():
    _, tenant_id, user_id = _tenant("m82-closed@example.com")
    _add(_symbol(tenant_id, FRIDAY_CLOSE_READ), _credential(tenant_id, user_id))
    b = _brief(tenant_id, user_id)
    read = b["read"]
    assert read["state"] == "last_session" and read["label"] == "Last session, Fri 30 Oct" and read["read_date"] == "2026-10-30"
    assert read["reason"] is None and b["day_type"]["kind"] == "TREND_UP"
    assert b["plan"]["headline"].startswith("Last session, Fri 30 Oct: A trending-up day")
    _no_broker_words(b)


def test_a_read_older_than_three_days_is_still_shown_never_no_read():
    _, tenant_id, user_id = _tenant("m82-older@example.com")
    _add(_symbol(tenant_id, WEDNESDAY_READ))
    b = _brief(tenant_id, user_id)
    assert b["read"]["state"] == "older" and b["read"]["label"] == "Last read, Wed 28 Oct"
    assert b["day_type"]["kind"] == "TREND_UP" and "No market read" not in b["plan"]["headline"]
    assert b["market_data"]["figures_shown"] is False          # an old read's prices stay hidden (P0.10)


def test_a_read_with_too_few_candles_is_named_not_called_no_read():
    _, tenant_id, user_id = _tenant("m82-unknown-regime@example.com")
    _add(_symbol(tenant_id, FRIDAY_CLOSE_READ, regime="UNKNOWN"))
    b = _brief(tenant_id, user_id)
    assert b["read"]["state"] == "last_session"
    assert b["plan"]["headline"] == "Last session, Fri 30 Oct: The stored read has too few candles to name the day's character."


# --- R3: no read - one exact reason ---------------------------------------------------------------------------------------

def test_none_ever_read_with_a_valid_session_and_the_worker_running(worker_ran_friday):
    _, tenant_id, user_id = _tenant("m82-none@example.com")
    _add(_credential(tenant_id, user_id), _profile(tenant_id, user_id))
    b = _brief(tenant_id, user_id)
    assert b["read"]["state"] == "none" and b["read"]["reason"] == "none_ever"
    assert b["plan"]["headline"] == "No market read stored yet. Press Read now."
    assert b["read"]["read_now"]["enabled"] is True
    _no_broker_words(b)


def test_token_expired_gives_the_reconnect_reason():
    _, tenant_id, user_id = _tenant("m82-expired@example.com")
    _add(_credential(tenant_id, user_id, status="EXPIRED"), _profile(tenant_id, user_id))
    b = _brief(tenant_id, user_id)
    read = b["read"]
    assert read["reason"] == "broker_expired" and read["read_now"]["enabled"] is False
    assert read["read_now"]["reconnect"] == {"broker": "upstox", "account_label": "primary"}
    assert b["plan"]["headline"] == "No market read: Your broker session has expired - reconnect it to read the market."


def test_worker_missed_the_last_session():
    _, tenant_id, user_id = _tenant("m82-worker@example.com")
    _add(_credential(tenant_id, user_id))           # no Copilot profile: the worker's read never covers this organisation
    b = _brief(tenant_id, user_id)
    assert b["read"]["reason"] == "worker_missed"
    assert b["plan"]["headline"] == ("No market read: The automatic market read did not run on the last session (Fri 30 Oct). "
                                     "Press Read now.")
    _no_broker_words(b)


def test_worker_ran_reads_the_heartbeat_window():
    start, end = datetime(2026, 10, 30, 3, 45, tzinfo=UTC), datetime(2026, 10, 30, 11, 0, tzinfo=UTC)
    alive = WorkerHeartbeatRecord(worker_name="trading_worker", started_at=start - timedelta(days=1), last_seen_at=SUNDAY)
    down_all_friday = WorkerHeartbeatRecord(worker_name="trading_worker", started_at=start - timedelta(days=2), last_seen_at=start - timedelta(hours=20))
    started_after = WorkerHeartbeatRecord(worker_name="trading_worker", started_at=end + timedelta(hours=2), last_seen_at=SUNDAY)
    other = WorkerHeartbeatRecord(worker_name="trigger_engine", started_at=start - timedelta(days=1), last_seen_at=SUNDAY)
    assert briefing._worker_ran([alive], start, end)
    assert not briefing._worker_ran([down_all_friday], start, end)
    assert not briefing._worker_ran([started_after], start, end)
    assert not briefing._worker_ran([other], start, end)


def test_valid_session_never_says_it_needs_one(worker_ran_friday):
    _, tenant_id, user_id = _tenant("m82-valid@example.com")
    _add(_credential(tenant_id, user_id), _profile(tenant_id, user_id), _symbol(tenant_id, FRIDAY_CLOSE_READ))
    b = _brief(tenant_id, user_id)
    assert b["read"]["read_now"] == {"enabled": True, "broker": "ok", "reason": None}
    assert {c["id"]: c for c in b["checklist"]}["broker"]["ok"] is True
    _no_broker_words(b)


# --- R4 / M83: "Read now" -------------------------------------------------------------------------------------------------

def _patch_reader(monkeypatch, used):
    from app.ai import global_cues
    from app.brokers import token_lifecycle
    from app.market_data import service

    def adapter(record, client=None):
        used.append((record.tenant_id, record.user_id))
        return _Broker()

    async def no_world():
        return [], []
    monkeypatch.setattr(token_lifecycle, "build_adapter", adapter)
    monkeypatch.setattr(service, "MarketDataService", _FakeMD)
    monkeypatch.setattr(global_cues, "fetch_all", no_world)


def _count(tenant_id, kind="SYMBOL"):
    async def go():
        async with _session_factory() as session:
            return await session.scalar(select(func.count()).select_from(MarketSnapshotRecord).where(
                MarketSnapshotRecord.tenant_id == tenant_id, MarketSnapshotRecord.kind == kind))
    return _run(go())


def test_read_now_reads_through_the_users_own_session(monkeypatch):
    used = []
    _patch_reader(monkeypatch, used)
    headers, tenant_id, user_id = _tenant("m82-readnow@example.com")
    _add(_credential(tenant_id, user_id))
    before = client.get("/api/ai/brief", headers=headers).json()
    assert before["read"]["state"] == "none" and before["read"]["read_now"]["enabled"] is True
    r = client.post("/api/ai/market-memory/refresh", headers=headers, json={})
    assert r.status_code == 200, r.text
    assert r.json()["report"]["symbols"] >= 2 and used == [(tenant_id, user_id)]
    after = client.get("/api/ai/brief", headers=headers).json()
    assert after["read"]["state"] != "none" and after["read"]["reason"] is None and after["day_type"]["symbol"] == "NIFTY 50"


def test_read_now_is_disabled_with_the_reason_without_a_session(monkeypatch):
    used = []
    _patch_reader(monkeypatch, used)
    headers, tenant_id, _ = _tenant("m82-disabled@example.com")
    rn = client.get("/api/ai/brief", headers=headers).json()["read"]["read_now"]
    assert rn["enabled"] is False and rn["broker"] == "none" and "connect your broker" in rn["reason"]
    assert client.post("/api/ai/market-memory/refresh", headers=headers, json={}).status_code == 409   # the server refuses too
    assert used == [] and _count(tenant_id) == 0


def test_read_now_never_uses_another_users_broker_or_organisation(monkeypatch):
    used = []
    _patch_reader(monkeypatch, used)
    owner_headers, tenant_id, owner_id = _tenant("m82-owner@example.com")
    _add(_credential(tenant_id, owner_id))
    mate = _teammate(tenant_id, "m82-mate@example.com")
    mate_id = client.get("/api/auth/me", headers=mate).json()["id"]
    rn = _brief(tenant_id, mate_id)["read"]["read_now"]
    assert rn["enabled"] is False and rn["broker"] == "teammate"
    assert client.post("/api/ai/market-memory/refresh", headers=mate, json={}).status_code == 409
    stranger, other_tenant, _ = _tenant("m82-stranger@example.com")
    assert client.post("/api/ai/market-memory/refresh", headers=stranger, json={}).status_code == 409
    assert used == [] and _count(tenant_id) == 0 and _count(other_tenant) == 0
    viewer = _teammate(tenant_id, "m82-viewer@example.com", role="VIEWER")
    viewer_id = client.get("/api/auth/me", headers=viewer).json()["id"]
    assert _brief(tenant_id, viewer_id)["read"]["read_now"]["broker"] == "role"
    assert client.post("/api/ai/market-memory/refresh", headers=viewer, json={}).status_code == 403


def test_a_read_that_stores_no_symbol_says_why(monkeypatch):
    used = []
    _patch_reader(monkeypatch, used)

    class _Empty(_FakeMD):
        async def get_candles(self, symbol, exchange, interval, now=None):
            return [] if interval == "1min" else await super().get_candles(symbol, exchange, interval, now)
    from app.market_data import service
    monkeypatch.setattr(service, "MarketDataService", _Empty)
    headers, tenant_id, user_id = _tenant("m82-why@example.com")
    _add(_credential(tenant_id, user_id))
    report = client.post("/api/ai/market-memory/refresh", headers=headers, json={}).json()["report"]
    assert report["symbols"] == 0
    assert "NIFTY 50: the broker returned too few candles to read (0 one-minute bars)" in report["errors"]


def test_last_read_takes_the_newest_day_only():
    _, tenant_id, _ = _tenant("m82-lastread@example.com")
    _add(_symbol(tenant_id, WEDNESDAY_READ, symbol="NIFTY BANK"), _symbol(tenant_id, FRIDAY_CLOSE_READ),
         _symbol(tenant_id, FRIDAY_CLOSE_READ - timedelta(minutes=15)))

    async def go():
        async with _session_factory() as session:
            return await mm.last_read(session, tenant_id)
    rows = _run(go())
    assert [r["symbol"] for r in rows] == ["NIFTY 50"] and rows[0]["captured_at"].startswith("2026-10-30T10:05")
