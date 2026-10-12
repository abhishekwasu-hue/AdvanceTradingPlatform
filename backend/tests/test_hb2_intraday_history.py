"""HB2: the owner's intraday (1-minute) history - capture after the close + resumable backfill through the owner's OWN
broker session into the owner's private store (md_tenant_candles), flag `history_intraday` (off by default).

No network: the broker is a fake adapter serving START-labelled 1-minute bars (as every adapter returns them) and
recording each call; the clock is injected. Dates are in 2023 (no holiday calendar in the test database, so every
weekday is a trading day) and every test uses its own symbols, because the in-memory database is shared.
Covered: flag off = no work (job, worker hook); closed bars only + ingest stamp (no look-ahead for as-of reads); START
-> END labels; resume after an interruption and after the time budget; idempotent re-run and `--redo` corrections;
rate-budget sharing (spare tokens only, exits never queued behind it, 429 ends the pass); tenant isolation (never
another tenant's or a teammate's session, bars only in the owner's private store); off-hours only; refused days;
quality gaps recorded for the owner's tenant; the worker hook shares the worker's budget; the admin progress route.
"""
import asyncio
from datetime import date, datetime, time, timedelta, timezone
from typing import Dict, List, Optional

import pytest
from sqlalchemy import func, select, update

from app.brokers.exceptions import BrokerAPIError
from app.brokers.rate_budget import RateBudget, RateLimits
from app.core import config
from app.core.models import OHLCVBar
from app.db.models import (BrokerCredentialRecord, DataQualityEventRecord, HistoryIntradayDayRecord, MdCandleRecord,
                           MdTenantCandleRecord, Tenant, User)
from app.market_data.calendar import IST
from app.market_lake import intraday_history as hb2, tenant_store
from app.platform import controls
from tests.test_auth_api import _register, _session_factory, client
from tests.utils import enable_mfa

D = HistoryIntradayDayRecord
FRI = date(2023, 3, 10)                                 # a Friday; the week before is Mon 6 .. Thu 9
EVENING = datetime(2023, 3, 10, 17, 0, tzinfo=IST)      # after the close: quiet hours, today's bars final


def _run(coro):
    return asyncio.run(coro)


def _flag(on: bool, tenants=()):
    async def go():
        async with _session_factory() as session:
            current = await controls._get(session, controls.KEY_FEATURE_FLAGS)
            flags = current.get("flags") or {}
            flags[hb2.FLAG] = {"on": on, "tenants": list(tenants)}
            await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": flags}, None)
            await session.commit()
    _run(go())


TOKENS: Dict[str, str] = {}


def _user(email: str) -> User:
    TOKENS[email] = _register(email)

    async def go():
        async with _session_factory() as session:
            return await session.scalar(select(User).where(User.email == email))
    return _run(go())


def _tenant_name(tenant_id: int) -> str:
    async def go():
        async with _session_factory() as session:
            return (await session.get(Tenant, tenant_id)).name
    return _run(go())


def _credential(user: User, *, broker="upstox", label="primary", valid=True, tenant_id=None) -> int:
    async def go():
        async with _session_factory() as session:
            rec = BrokerCredentialRecord(tenant_id=tenant_id or user.tenant_id, user_id=user.id, broker_name=broker,
                                         account_label=label, encrypted_payload="not-used-by-the-fake-adapter",
                                         token_status="VALID" if valid else "EXPIRED",
                                         token_expires_at=datetime(2099, 1, 1, tzinfo=timezone.utc))
            session.add(rec)
            await session.commit()
            return rec.id
    return _run(go())


def _teammate(owner: User, email: str, role: str = "USER") -> User:
    from app.auth.security import hash_password

    async def go():
        async with _session_factory() as session:
            user = User(tenant_id=owner.tenant_id, email=email, hashed_password=hash_password("S3cur3Pass!"), role=role,
                        is_active=True)
            session.add(user)
            await session.commit()
            return user
    return _run(go())


def _count(model, *where) -> int:
    async def go():
        async with _session_factory() as session:
            return int(await session.scalar(select(func.count()).select_from(model).where(*where)) or 0)
    return _run(go())


def _states(tenant_id: int, key: str) -> Dict[date, D]:
    async def go():
        async with _session_factory() as session:
            return {s.trade_date: s for s in await session.scalars(select(D).where(D.tenant_id == tenant_id, D.instrument_key == key))}
    return _run(go())


def day_bars(day: date, base: float = 100.0, *, skip=(), minutes: Optional[int] = None) -> List[OHLCVBar]:
    """A full NSE session of START-labelled 1-minute bars (09:15 .. 15:29 IST), as the broker sends them."""
    out = []
    start = datetime.combine(day, time(9, 15), tzinfo=IST)
    for i in range(minutes or 375):
        if i in skip:
            continue
        p = base + i * 0.01
        out.append(OHLCVBar(timestamp=start + timedelta(minutes=i), open=p, high=p + 0.5, low=p - 0.5, close=p + 0.1, volume=100 + i))
    return out


class FakeBroker:
    """START-labelled bars per symbol; records every call. `fail` maps a call number to an exception."""

    def __init__(self, bars: Dict[str, List[OHLCVBar]], *, today: date = FRI, fail: Optional[Dict[int, BaseException]] = None,
                 extra_today: Optional[Dict[str, List[OHLCVBar]]] = None) -> None:
        self.bars, self.today, self.fail = bars, today, fail or {}
        self.extra_today = extra_today or {}
        self.calls: List[tuple] = []

    def _maybe_fail(self):
        exc = self.fail.get(len(self.calls))
        if exc is not None:
            raise exc

    async def get_historical_data(self, symbol, exchange, interval, from_date, to_date):
        self.calls.append(("historical", symbol, exchange, interval, from_date.date(), to_date.date()))
        self._maybe_fail()
        lo, hi = from_date.date(), to_date.date()
        return [b for b in self.bars.get(symbol, []) if lo <= b.timestamp.astimezone(IST).date() <= hi and
                b.timestamp.astimezone(IST).date() != self.today]          # the historical endpoint has no "today"

    async def get_intraday_candles(self, symbol, exchange, interval):
        self.calls.append(("intraday", symbol, exchange, interval))
        self._maybe_fail()
        return [b for b in self.bars.get(symbol, []) if b.timestamp.astimezone(IST).date() == self.today] + \
            self.extra_today.get(symbol, [])

    async def place_order(self, *a, **k):          # HB2 must never reach an order path
        raise AssertionError("HB2 placed an order")


class Owner:
    """An owner tenant (registered OWNER + own Upstox credential) configured as HISTORY_OWNER_TENANT_ID."""

    def __init__(self, email: str, *, credential=True) -> None:
        self.user = _user(email)
        self.tenant_id = self.user.tenant_id
        self.name = _tenant_name(self.tenant_id)
        self.credential_id = _credential(self.user) if credential else None
        self.used: List[int] = []

    def run(self, broker: FakeBroker, *, clock=None, **kw) -> hb2.PassReport:
        def factory(record):
            self.used.append(record.id)
            return broker
        kw.setdefault("start", FRI - timedelta(days=4))
        return _run(hb2.run_pass(_session_factory, adapter_factory=factory, clock=clock or (lambda: EVENING),
                                 owner_tenant_id=self.tenant_id, owner_tenant_name=self.name, **kw))


# --- flag, registration, migration -------------------------------------------------------------------------------------
def test_flag_is_registered_off_by_default_and_flag_off_means_no_work():
    assert hb2.FLAG == "history_intraday" and hb2.FLAG in controls.FEATURE_FLAGS and hb2.FLAG in controls.DEFAULT_OFF_FLAGS
    owner = Owner("hb2-flag@example.com")
    broker = FakeBroker({"HBIF1": day_bars(FRI)})
    _flag(False)
    with pytest.raises(hb2.IntradayConfigError, match="flag is off"):
        owner.run(broker, symbols=["NSE:HBIF1"])
    assert broker.calls == [] and owner.used == [] and _count(D, D.tenant_id == owner.tenant_id) == 0
    assert _count(MdTenantCandleRecord, MdTenantCandleRecord.instrument_key == "NSE:HBIF1") == 0
    _flag(False, tenants=[owner.tenant_id])                  # allow-listed for the owner's tenant only: runs
    report = owner.run(broker, symbols=["NSE:HBIF1"], start=FRI)
    assert report.complete and report.days_done == 1
    _flag(False)


def test_migration_is_rollback_safe_and_the_only_head():
    from pathlib import Path

    from alembic.config import Config
    from alembic.script import ScriptDirectory
    backend = Path(__file__).resolve().parents[1]
    script = ScriptDirectory.from_config(Config(str(backend / "alembic.ini")))
    assert script.get_heads() == ["f3b5d7a9c1e2"]
    text_ = (backend / "alembic" / "versions" / "f3b5d7a9c1e2_hb2_intraday_history.py").read_text()
    assert "ROLLBACK_SAFE = True" in text_ and "purely additive" in text_


# --- capture: closed bars only, labels, ingest stamp ----------------------------------------------------------------
def test_capture_stores_closed_bars_by_end_with_ingest_stamp_and_as_of_hides_them_before(monkeypatch):
    monkeypatch.setattr(config, "HISTORY_INTRADAY_HOUR_IST", 15)            # earliest allowed: 15:45 IST
    owner = Owner("hb2-closed@example.com")
    _flag(True)
    now = datetime(2023, 3, 10, 15, 50, tzinfo=IST)
    forming = [OHLCVBar(timestamp=datetime(2023, 3, 10, 15, 50, tzinfo=IST), open=1, high=1, low=1, close=1, volume=1)]
    broker = FakeBroker({"HBIC1": day_bars(FRI)}, extra_today={"HBIC1": forming})
    report = owner.run(broker, symbols=["NSE:HBIC1"], start=FRI, clock=lambda: now)
    assert report.complete and report.days_done == 1 and report.bars == 375 and report.dropped_forming == 1
    assert broker.calls == [("intraday", "HBIC1", "NSE", "1min")]           # today: the intraday endpoint, alone

    utc_now = now.astimezone(timezone.utc)                                   # SQLite compares stored UTC wall times

    async def read():
        async with _session_factory() as session:
            rows = list(await session.scalars(select(MdTenantCandleRecord).where(
                MdTenantCandleRecord.tenant_id == owner.tenant_id, MdTenantCandleRecord.instrument_key == "NSE:HBIC1")
                .order_by(MdTenantCandleRecord.ts)))
            before = await tenant_store.candles(session, owner.tenant_id, "NSE:HBIC1", "5m", utc_now - timedelta(hours=8), utc_now,
                                                symbol="HBIC1", as_of=utc_now - timedelta(minutes=1), reader_user_id=owner.user.id)
            after = await tenant_store.candles(session, owner.tenant_id, "NSE:HBIC1", "5m", utc_now - timedelta(hours=8), utc_now,
                                               symbol="HBIC1", reader_user_id=owner.user.id)
            return rows, before, after
    rows, before, after = _run(read())
    assert len(rows) == 375 and {r.source for r in rows} == {f"broker:upstox:u{owner.user.id}"} and {r.timeframe for r in rows} == {"1m"}
    first, last = rows[0], rows[-1]
    assert first.ts.replace(tzinfo=timezone.utc).astimezone(IST).time() == time(9, 16)      # START 09:15 -> END 09:16
    assert last.ts.replace(tzinfo=timezone.utc).astimezone(IST).time() == time(15, 30)
    stamps = {r.ingested_at.replace(tzinfo=timezone.utc) if r.ingested_at.tzinfo is None else r.ingested_at for r in rows}
    assert all(s >= now for s in stamps)                                     # stamped at write, after every bar's END
    assert all(r.ts.replace(tzinfo=timezone.utc) <= now for r in rows)       # the forming 15:50 bar was never stored
    assert before == [] and len(after) == 75                                 # as-of before the ingest sees nothing
    state = _states(owner.tenant_id, "NSE:HBIC1")[FRI]
    assert state.status == "done" and state.endpoint == "intraday" and state.dropped_forming == 1 and state.bars == 375
    assert state.credential_id == owner.credential_id
    assert _count(MdCandleRecord, MdCandleRecord.instrument_key == "NSE:HBIC1") == 0           # never the shared lake


def test_backfill_windows_newest_first_and_gaps_are_recorded_for_the_owner_only():
    owner = Owner("hb2-backfill@example.com")
    _flag(True)
    days = [FRI - timedelta(days=n) for n in range(0, 5)]                    # Mon 6 .. Fri 10
    bars = [b for d in days for b in day_bars(d, skip=(10, 11) if d == date(2023, 3, 7) else ())]
    broker = FakeBroker({"HBIB1": bars})
    report = owner.run(broker, symbols=["NSE:HBIB1"], start=date(2023, 3, 4))   # Saturday start: weekends never asked
    assert report.complete and report.days_done == 5 and report.requests == 2
    assert broker.calls[0][0] == "intraday"
    assert broker.calls[1] == ("historical", "HBIB1", "NSE", "1min", date(2023, 3, 6), date(2023, 3, 9))
    states = _states(owner.tenant_id, "NSE:HBIB1")
    assert sorted(states) == days[::-1] and all(s.status == "done" for s in states.values())
    gaps = _count(DataQualityEventRecord, DataQualityEventRecord.instrument_key == "NSE:HBIB1", DataQualityEventRecord.kind == "gap",
                  DataQualityEventRecord.tenant_id == owner.tenant_id)
    shared = _count(DataQualityEventRecord, DataQualityEventRecord.instrument_key == "NSE:HBIB1", DataQualityEventRecord.tenant_id.is_(None))
    assert gaps == 1 and shared == 0 and states[date(2023, 3, 7)].quality_events >= 1


# --- resume and idempotency -----------------------------------------------------------------------------------------
def test_resume_after_an_interrupted_pass_and_after_the_time_budget(monkeypatch):
    monkeypatch.setattr(config, "HISTORY_INTRADAY_WINDOW_DAYS", 2)
    owner = Owner("hb2-resume@example.com")
    _flag(True)
    days = [FRI - timedelta(days=n) for n in range(0, 5)]
    bars = {"HBIR1": [b for d in days for b in day_bars(d)]}
    crash = FakeBroker(bars, fail={3: asyncio.CancelledError()})              # the worker is stopped on the 3rd request
    with pytest.raises(asyncio.CancelledError):
        owner.run(crash, symbols=["NSE:HBIR1"])
    states = _states(owner.tenant_id, "NSE:HBIR1")
    assert {d: s.status for d, s in states.items()} == {FRI: "done", date(2023, 3, 9): "done", date(2023, 3, 8): "done",
                                                         date(2023, 3, 7): "running", date(2023, 3, 6): "running"}
    assert _count(MdTenantCandleRecord, MdTenantCandleRecord.instrument_key == "NSE:HBIR1",
                  MdTenantCandleRecord.ts < datetime(2023, 3, 8, tzinfo=IST)) == 0         # nothing half written
    again = FakeBroker(bars)
    report = owner.run(again, symbols=["NSE:HBIR1"])
    assert report.complete and report.days_done == 2 and report.skipped == 3
    assert again.calls == [("historical", "HBIR1", "NSE", "1min", date(2023, 3, 6), date(2023, 3, 7))]
    assert _states(owner.tenant_id, "NSE:HBIR1")[date(2023, 3, 6)].attempts == 2

    out_of_time = owner.run(FakeBroker({"HBIR2": bars["HBIR1"]}), symbols=["NSE:HBIR2"], budget_seconds=0)
    assert out_of_time.status == "partial" and not out_of_time.complete and out_of_time.pending == 5
    done = owner.run(FakeBroker({"HBIR2": bars["HBIR1"]}), symbols=["NSE:HBIR2"])
    assert done.complete and done.days_done == 5


def test_rerun_is_idempotent_and_redo_writes_only_changed_bars_as_versions():
    owner = Owner("hb2-idem@example.com")
    _flag(True)
    bars = {"HBII1": day_bars(FRI) + day_bars(FRI - timedelta(days=1))}
    first = owner.run(FakeBroker(bars), symbols=["NSE:HBII1"], start=FRI - timedelta(days=1))
    assert first.complete and first.inserted == 750
    rerun = FakeBroker(bars)
    second = owner.run(rerun, symbols=["NSE:HBII1"], start=FRI - timedelta(days=1))
    assert second.complete and rerun.calls == [] and second.skipped == 2 and second.inserted == 0
    changed = [b.model_copy() for b in bars["HBII1"]]
    changed[400] = changed[400].model_copy(update={"close": changed[400].close + 1})       # one corrected bar, Thursday
    redo = owner.run(FakeBroker({"HBII1": changed}), symbols=["NSE:HBII1"], start=FRI - timedelta(days=1), redo=True)
    assert redo.complete and redo.inserted == 0 and redo.corrected == 1
    assert _count(MdTenantCandleRecord, MdTenantCandleRecord.instrument_key == "NSE:HBII1", MdTenantCandleRecord.version == 2) == 1


def test_a_bad_batch_is_refused_and_nothing_is_written():
    owner = Owner("hb2-refused@example.com")
    _flag(True)
    bad = day_bars(FRI - timedelta(days=1))
    bad[3] = bad[3].model_copy(update={"low": 0.0})
    report = owner.run(FakeBroker({"HBIX1": bad}), symbols=["NSE:HBIX1"], start=FRI - timedelta(days=1), end=FRI - timedelta(days=1))
    assert report.refused == 1 and report.complete           # worked through; the refused day is retried next pass
    state = _states(owner.tenant_id, "NSE:HBIX1")[FRI - timedelta(days=1)]
    assert state.status == "refused" and "usable price" in state.error
    assert _count(MdTenantCandleRecord, MdTenantCandleRecord.instrument_key == "NSE:HBIX1") == 0
    empty = owner.run(FakeBroker({}), symbols=["NSE:HBIX2"], start=FRI - timedelta(days=1), end=FRI - timedelta(days=1))
    assert empty.no_data == 1 and _states(owner.tenant_id, "NSE:HBIX2")[FRI - timedelta(days=1)].status == "no_data"


# --- rate budget ----------------------------------------------------------------------------------------------------
class FakeTime:
    def __init__(self) -> None:
        self.t = 0.0
        self.slept: List[float] = []

    def clock(self) -> float:
        return self.t

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.t += seconds


def test_acquire_spare_leaves_the_reserve_for_exits_and_never_queues_them():
    ft = FakeTime()
    budget = RateBudget(RateLimits(per_second=10.0, burst=10, per_minute=100), clock=ft.clock, sleep=ft.sleep)

    async def go():
        for _ in range(5):                                   # burst 10, reserve 50%: a spare token needs 6 in the bucket
            assert await budget.acquire_spare(0.5)
        assert ft.t == 0.0 and ft.slept == []
        # nothing spare now: with a deadline a background read gives up instead of waiting
        assert await budget.acquire_spare(0.5, deadline=ft.t + 0.01) is False
        for _ in range(5):                                   # the worker's exits still find the reserve at once
            await budget.acquire()
        assert ft.t == 0.0
        assert await budget.acquire_spare(0.5)               # waits for the bucket to refill above the reserve again
        assert abs(ft.t - 0.6) < 1e-9
        return budget.spare_calls, budget.calls
    assert _run(go()) == (6, 11)


def test_spare_waiting_does_not_hold_the_lock_for_exits():
    async def go():
        budget = RateBudget(RateLimits(per_second=1.0, burst=2, per_minute=60))
        await budget.acquire()
        await budget.acquire()                               # empty: a spare token needs a refill of the reserve too
        background = asyncio.create_task(budget.acquire_spare(0.5))
        await asyncio.sleep(0.05)
        assert not background.done()
        assert not budget._lock.locked()                     # an exit's acquire() is not queued behind the background read
        background.cancel()
        with pytest.raises(asyncio.CancelledError):
            await background
    _run(go())


def test_the_pass_draws_from_the_given_budget_and_a_429_ends_it(monkeypatch):
    monkeypatch.setattr(config, "HISTORY_INTRADAY_WINDOW_DAYS", 1)
    owner = Owner("hb2-budget@example.com")
    _flag(True)
    budget = RateBudget(RateLimits(per_second=100.0, burst=100, per_minute=1000))
    asked = []

    def budget_for(tenant_id, key):
        asked.append((tenant_id, key))
        return budget
    days = [FRI - timedelta(days=n) for n in range(0, 5)]
    bars = {"HBIL1": [b for d in days for b in day_bars(d)]}
    report = owner.run(FakeBroker(bars), symbols=["NSE:HBIL1"], budget_for=budget_for)
    assert asked == [(owner.tenant_id, "upstox@primary")] and budget.spare_calls == report.requests == 5

    limited = FakeBroker({"HBIL2": bars["HBIL1"]}, fail={2: BrokerAPIError("Too many requests", status_code=429)})
    stopped = owner.run(limited, symbols=["NSE:HBIL2"], budget_for=budget_for)
    assert stopped.status == "rate_limited" and not stopped.complete and len(limited.calls) == 2   # nothing after the 429
    states = _states(owner.tenant_id, "NSE:HBIL2")
    assert states[FRI].status == "done" and states[FRI - timedelta(days=1)].status == "pending"
    assert "Too many requests" in states[FRI - timedelta(days=1)].error and FRI - timedelta(days=2) not in states


# --- tenant isolation -----------------------------------------------------------------------------------------------
def test_only_the_owners_own_session_is_used_and_no_other_tenant_sees_the_bars():
    _flag(True)
    owner = Owner("hb2-iso-owner@example.com", credential=False)
    teammate = _teammate(owner.user, "hb2-iso-mate@example.com")
    _credential(teammate, label="mate")                      # a teammate's Upstox in the owner's tenant: never used
    co_owner = _teammate(owner.user, "hb2-iso-coowner@example.com", role="OWNER")
    _credential(co_owner, label="coowner")                   # a teammate promoted to OWNER (higher id): never used either
    other = _user("hb2-iso-other@example.com")
    other_cred = _credential(other)                          # another tenant's own session: never used
    broker = FakeBroker({"HBIT1": day_bars(FRI)})
    report = owner.run(broker, symbols=["NSE:HBIT1"], start=FRI)
    assert report.status == "no_session" and broker.calls == [] and owner.used == []
    _credential(owner.user, broker="zerodha")                # execution-only terms: not a Tier A session
    assert owner.run(broker, symbols=["NSE:HBIT1"], start=FRI).status == "no_session"

    owner.credential_id = _credential(owner.user, label="own")
    report = owner.run(broker, symbols=["NSE:HBIT1"], start=FRI)
    assert report.complete and owner.used == [owner.credential_id] and other_cred not in owner.used
    assert _count(MdTenantCandleRecord, MdTenantCandleRecord.instrument_key == "NSE:HBIT1",
                  MdTenantCandleRecord.tenant_id != owner.tenant_id) == 0
    assert _count(MdCandleRecord, MdCandleRecord.instrument_key == "NSE:HBIT1") == 0

    async def reads():
        async with _session_factory() as session:
            evening = EVENING.astimezone(timezone.utc)
            window = (evening - timedelta(hours=9), evening)
            mine = await tenant_store.candles(session, owner.tenant_id, "NSE:HBIT1", "1m", *window, symbol="HBIT1",
                                              reader_user_id=owner.user.id)
            theirs = await tenant_store.candles(session, other.tenant_id, "NSE:HBIT1", "1m", *window, symbol="HBIT1",
                                                reader_user_id=other.id)
            with pytest.raises(PermissionError):
                await tenant_store.candles(session, None, "NSE:HBIT1", "1m", *window, symbol="HBIT1")
            return len(mine), len(theirs)
    assert _run(reads()) == (375, 0)

    with pytest.raises(hb2.IntradayConfigError, match="HISTORY_OWNER_TENANT_NAME"):     # the owner must be confirmed
        _run(hb2.run_pass(_session_factory, adapter_factory=lambda r: broker, clock=lambda: EVENING,
                          owner_tenant_id=owner.tenant_id, owner_tenant_name="someone else"))
    with pytest.raises(hb2.IntradayConfigError, match="HISTORY_OWNER_TENANT_ID"):
        _run(hb2.run_pass(_session_factory, adapter_factory=lambda r: broker, clock=lambda: EVENING))


def test_history_api_serves_the_bars_to_the_owner_only():
    _flag(True)
    owner = Owner("hb2-api-owner@example.com")
    owner.run(FakeBroker({"HBIA1": day_bars(FRI)}), symbols=["NSE:HBIA1"], start=FRI)
    params = {"instrument_key": "NSE:HBIA1", "timeframe": "15m", "store": "tenant", "symbol": "HBIA1",
              "start": (EVENING - timedelta(hours=9)).isoformat(), "end": EVENING.isoformat()}
    mine = client.get("/api/market-data/history", params=params, headers={"Authorization": f"Bearer {TOKENS['hb2-api-owner@example.com']}"})
    other = client.get("/api/market-data/history", params=params,
                       headers={"Authorization": f"Bearer {_register('hb2-api-other@example.com')}"})
    assert mine.status_code == 200, mine.text
    assert other.status_code == 200, other.text
    assert len(mine.json()["candles"]) == 25 and other.json()["candles"] == []


# --- hours ----------------------------------------------------------------------------------------------------------
def test_never_during_market_hours_and_a_pass_stops_before_the_morning():
    owner = Owner("hb2-hours@example.com")
    _flag(True)
    broker = FakeBroker({"HBIH1": day_bars(FRI)})
    with pytest.raises(hb2.IntradayConfigError, match="trading hours"):
        owner.run(broker, symbols=["NSE:HBIH1"], start=FRI, clock=lambda: datetime(2023, 3, 10, 11, 0, tzinfo=IST))
    with pytest.raises(hb2.IntradayConfigError, match="trading hours"):
        owner.run(broker, symbols=["NSE:HBIH1"], start=FRI, clock=lambda: datetime(2023, 3, 10, 15, 40, tzinfo=IST))
    assert broker.calls == []
    assert hb2.quiet_hours(datetime(2023, 3, 11, 11, 0, tzinfo=IST), set())             # Saturday: no session
    assert hb2.nightly_due(datetime(2023, 3, 10, 15, 59, tzinfo=IST), None) is None
    assert hb2.nightly_due(EVENING, None) == FRI and hb2.nightly_due(EVENING, FRI) is None

    times = iter([datetime(2023, 3, 13, 7, 50, tzinfo=IST)] * 4 + [datetime(2023, 3, 13, 8, 5, tzinfo=IST)] * 50)
    days = [FRI - timedelta(days=n) for n in range(0, 12)]
    morning = FakeBroker({"HBIH2": [b for d in days for b in day_bars(d)]}, today=date(2023, 3, 13))
    report = owner.run(morning, symbols=["NSE:HBIH2"], start=FRI - timedelta(days=11), clock=lambda: next(times))
    assert report.status == "partial" and "trading hours" in " ".join(report.notes) and report.pending > 0
    assert len(morning.calls) <= 1


# --- the worker -----------------------------------------------------------------------------------------------------
def test_worker_hook_is_gated_and_shares_the_workers_budget(monkeypatch):
    from app.workers.trading_worker import TradingWorker
    owner = Owner("hb2-worker@example.com")
    monkeypatch.setattr(config, "HISTORY_OWNER_TENANT_ID", owner.tenant_id)
    seen = []

    async def fake_pass(factory, *, budget_for=None, **kw):
        seen.append(budget_for)
        return hb2.PassReport(complete=True)
    monkeypatch.setattr(hb2, "run_pass", fake_pass)

    def turn(flag_on, when, last=None):
        _flag(flag_on)

        async def go():
            worker = TradingWorker(_session_factory)
            worker._last_intraday_day = last
            async with _session_factory() as session:
                ok = await worker._maybe_start_intraday_history(session, when)
            if worker._intraday_task is not None:
                await worker._intraday_task
            shared = worker._budget_for(owner.tenant_id, "upstox@primary")
            return ok, worker._last_intraday_day, shared, worker
        return _run(go())
    assert turn(False, EVENING)[:2] == (False, None) and seen == []
    assert turn(True, EVENING.replace(hour=15))[:2] == (False, None) and seen == []
    ok, last, shared, worker = turn(True, EVENING)
    assert ok and last == FRI and len(seen) == 1
    assert seen[0].__self__ is worker and seen[0](owner.tenant_id, "upstox@primary") is shared     # the same RateBudget
    assert turn(True, EVENING, last=FRI)[0] is False and len(seen) == 1
    _flag(False)


def test_stopping_the_worker_cancels_the_pass():
    from app.workers.trading_worker import TradingWorker

    async def go():
        worker = TradingWorker(_session_factory)
        worker._intraday_task = asyncio.get_running_loop().create_task(asyncio.sleep(3600))
        worker.stop()
        with pytest.raises(asyncio.CancelledError):
            await worker._intraday_task
        return worker._intraday_task.cancelled()
    assert _run(go()) is True


# --- admin ----------------------------------------------------------------------------------------------------------
def test_admin_progress_is_super_admin_only_and_returns_counts_not_bars(monkeypatch):
    owner = Owner("hb2-progress@example.com")
    _flag(True)
    owner.run(FakeBroker({"HBIP1": day_bars(FRI)}), symbols=["NSE:HBIP1"], start=FRI)
    monkeypatch.setattr(config, "HISTORY_OWNER_TENANT_ID", owner.tenant_id)
    plain = {"Authorization": f"Bearer {_register('hb2-plain@example.com')}"}
    assert client.get("/api/admin/history-intraday/progress", headers=plain).status_code == 403
    token = _register("hb2-admin@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    async def promote():
        async with _session_factory() as session:
            user = await session.scalar(select(User).where(User.email == "hb2-admin@example.com"))
            user.role = "SUPER_ADMIN"
            await session.commit()
    _run(promote())
    enable_mfa(headers)
    res = client.get("/api/admin/history-intraday/progress", headers=headers)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["tenant_id"] == owner.tenant_id and body["flag_on"] is True
    assert body["instruments"]["NSE:HBIP1"]["by_status"] == {"done": 1} and body["instruments"]["NSE:HBIP1"]["bars"] == 375
    assert "candles" not in res.text
    _flag(False)


def test_instrument_keys_are_checked_not_guessed():
    assert hb2.parse_instrument("reliance") == hb2.Instrument("NSE", "RELIANCE")
    assert hb2.parse_instrument("NSE:Nifty  50").key == "NSE:NIFTY 50"
    for bad in ("MCX:CRUDEOIL", "NFO:NIFTY24JANFUT", "", "NSE:"):
        with pytest.raises(ValueError):
            hb2.parse_instrument(bad)
    assert hb2._windows([date(2023, 3, d) for d in (6, 7, 8, 9, 10)], 3) == [
        [date(2023, 3, 10), date(2023, 3, 9), date(2023, 3, 8)], [date(2023, 3, 7), date(2023, 3, 6)]]


# --- review follow-ups (M1-M3, L1-L5) --------------------------------------------------------------------------------
def test_the_session_user_is_pinned_to_one_user():
    _flag(True)
    owner = Owner("hb2-pin-owner@example.com", credential=False)
    co_owner = _teammate(owner.user, "hb2-pin-coowner@example.com", role="OWNER")
    co_cred = _credential(co_owner, label="coowner")
    broker = FakeBroker({"HBIN1": day_bars(FRI)})
    assert owner.run(broker, symbols=["NSE:HBIN1"], start=FRI).status == "no_session"     # lowest-id OWNER only
    report = owner.run(broker, symbols=["NSE:HBIN1"], start=FRI, owner_user_id=co_owner.id)  # pinned explicitly
    assert report.complete and owner.used == [co_cred]
    assert _states(owner.tenant_id, "NSE:HBIN1")[FRI].user_id == co_owner.id
    other = _user("hb2-pin-other@example.com")
    with pytest.raises(hb2.IntradayConfigError, match="HISTORY_OWNER_USER_ID"):            # checked against the tenant
        owner.run(broker, symbols=["NSE:HBIN1"], start=FRI, owner_user_id=other.id)


def test_broker_rows_are_readable_only_by_the_session_holder():
    _flag(True)
    owner = Owner("hb2-read-owner@example.com")
    owner.run(FakeBroker({"HBIV1": day_bars(FRI, skip=(30, 31))}), symbols=["NSE:HBIV1"], start=FRI)   # one gap event
    _teammate(owner.user, "hb2-read-viewer@example.com", role="VIEWER")
    _teammate(owner.user, "hb2-read-coowner@example.com", role="OWNER")
    params = {"instrument_key": "NSE:HBIV1", "timeframe": "1m", "store": "tenant", "symbol": "HBIV1",
              "start": (EVENING - timedelta(hours=9)).isoformat(), "end": EVENING.isoformat()}

    def read(token):
        res = client.get("/api/market-data/history", params=params, headers={"Authorization": f"Bearer {token}"})
        assert res.status_code == 200, res.text
        return len(res.json()["candles"]), res.json()["quality_events"]

    def login(email):
        res = client.post("/api/auth/login", json={"email": email, "password": "S3cur3Pass!"})
        assert res.status_code == 200, res.text
        return res.json()["access_token"]
    assert read(TOKENS["hb2-read-owner@example.com"]) == (373, 1)
    assert read(login("hb2-read-viewer@example.com")) == (0, 0)        # same organisation, not the session holder: no bars,
    assert read(login("hb2-read-coowner@example.com")) == (0, 0)       # and no quality events about them either


def test_a_pin_change_never_shows_one_holders_bars_to_the_other():
    _flag(True)
    a = Owner("hb2-pinchange-a@example.com")
    a.run(FakeBroker({"HBIQ1": day_bars(FRI)}), symbols=["NSE:HBIQ1"], start=FRI)
    b = _teammate(a.user, "hb2-pinchange-b@example.com", role="OWNER")
    b_cred = _credential(b, label="b")
    b_broker = FakeBroker({"HBIQ1": day_bars(FRI, base=200.0)})
    report = a.run(b_broker, symbols=["NSE:HBIQ1"], start=FRI, owner_user_id=b.id)        # the pin moves to B
    assert report.complete and report.days_done == 1 and b_broker.calls                    # B keeps its own copy
    assert a.used[-1] == b_cred

    async def reads():
        async with _session_factory() as session:
            evening = EVENING.astimezone(timezone.utc)
            window = (evening - timedelta(hours=9), evening)
            as_a = await tenant_store.candles(session, a.tenant_id, "NSE:HBIQ1", "1m", *window, symbol="HBIQ1", reader_user_id=a.user.id)
            as_b = await tenant_store.candles(session, a.tenant_id, "NSE:HBIQ1", "1m", *window, symbol="HBIQ1", reader_user_id=b.id)
            nobody = await tenant_store.candles(session, a.tenant_id, "NSE:HBIQ1", "1m", *window, symbol="HBIQ1")
            return as_a, as_b, nobody
    as_a, as_b, nobody = _run(reads())
    assert len(as_a) == 375 and all(bar.open < 200 for bar in as_a)                       # A sees only A's bars
    assert len(as_b) == 375 and all(bar.open >= 200 for bar in as_b)                      # B sees only B's bars
    assert nobody == []

    def read(token):
        params = {"instrument_key": "NSE:HBIQ1", "timeframe": "1m", "store": "tenant", "symbol": "HBIQ1",
                  "start": (EVENING - timedelta(hours=9)).isoformat(), "end": EVENING.isoformat()}
        res = client.get("/api/market-data/history", params=params, headers={"Authorization": f"Bearer {token}"})
        assert res.status_code == 200, res.text
        return [c["open"] for c in res.json()["candles"]]
    login = client.post("/api/auth/login", json={"email": "hb2-pinchange-b@example.com", "password": "S3cur3Pass!"})
    assert min(read(login.json()["access_token"])) >= 200 and max(read(TOKENS["hb2-pinchange-a@example.com"])) < 200


def test_mcx_session_request_cap_and_blackout_dates(monkeypatch):
    owner = Owner("hb2-mcx@example.com")
    _flag(True)
    days = [FRI - timedelta(days=n) for n in range(0, 12)]
    bars = {"HBIM1": [b for d in days for b in day_bars(d)]}
    capped = FakeBroker(bars)
    report = owner.run(capped, symbols=["NSE:HBIM1"], start=FRI - timedelta(days=11), max_requests=2)
    assert report.status == "request_cap" and not report.complete and report.requests == 2 == len(capped.calls)
    assert report.pending > 0
    again = FakeBroker(bars)                                 # the cap is a rolling 30-minute count from the database:
    report = owner.run(again, symbols=["NSE:HBIM1"], start=FRI - timedelta(days=11), max_requests=2)
    assert report.status == "request_cap" and again.calls == [] and _run(hb2.recent_requests(_session_factory, owner.tenant_id)) == 2

    from app.db.models import StrategyDeploymentRecord

    async def mcx_deployment():
        async with _session_factory() as session:
            session.add(StrategyDeploymentRecord(tenant_id=owner.tenant_id, strategy_id="ema_crossover", symbol="CRUDEOIL",
                                                 exchange="MCX", timeframe="5min", mode="PAPER", status="ACTIVE"))
            await session.commit()
    _run(mcx_deployment())
    held = FakeBroker(bars)
    report = owner.run(held, symbols=["NSE:HBIM1"], start=FRI - timedelta(days=11))       # 17:00 IST: MCX open
    assert report.status == "mcx_session" and held.calls == [] and not report.complete
    late = owner.run(held, symbols=["NSE:HBIM1"], start=FRI - timedelta(days=11),
                     clock=lambda: datetime(2023, 3, 10, 23, 45, tzinfo=IST))           # MCX closed at 23:30
    assert late.complete and held.calls

    monkeypatch.setattr(config, "HISTORY_INTRADAY_BLACKOUT_DATES", frozenset({date(2023, 3, 11)}))   # e.g. a budget Saturday
    assert not hb2.quiet_hours(datetime(2023, 3, 11, 20, 0, tzinfo=IST), set())
    with pytest.raises(hb2.IntradayConfigError, match="blackout"):
        owner.run(held, symbols=["NSE:HBIM1"], clock=lambda: datetime(2023, 3, 11, 20, 0, tzinfo=IST))


def test_a_cds_deployment_in_session_holds_the_pass_until_17_00():
    """BK2-8: currency derivatives trade to 17:00 IST on the same broker account - HB2 waits for their close too."""
    owner = Owner("hb2-cds@example.com")
    _flag(True)
    days = [FRI - timedelta(days=n) for n in range(0, 4)]
    bars = {"HBIC1": [b for d in days for b in day_bars(d)]}
    from app.db.models import StrategyDeploymentRecord

    async def cds_deployment():
        async with _session_factory() as session:
            session.add(StrategyDeploymentRecord(tenant_id=owner.tenant_id, strategy_id="ema_crossover", symbol="USDINR",
                                                 exchange="CDS", timeframe="5min", mode="PAPER", status="ACTIVE"))
            await session.commit()
    _run(cds_deployment())
    held = FakeBroker(bars)
    report = owner.run(held, symbols=["NSE:HBIC1"], start=FRI - timedelta(days=3),
                       clock=lambda: datetime(2023, 3, 10, 16, 30, tzinfo=IST))           # NSE closed, CDS open
    assert report.status == "mcx_session" and held.calls == [] and "CDS" in " ".join(report.notes)
    late = owner.run(held, symbols=["NSE:HBIC1"], start=FRI - timedelta(days=3),
                     clock=lambda: datetime(2023, 3, 10, 17, 5, tzinfo=IST))            # CDS closed at 17:00
    assert late.status != "mcx_session" and held.calls


def test_hours_and_flag_are_checked_again_after_waiting_for_a_token():
    owner = Owner("hb2-recheck@example.com")
    _flag(True)

    class FlagOffWhileWaiting(RateBudget):
        async def acquire_spare(self, reserve, *, deadline=None):
            async with _session_factory() as session:            # the operator turns the flag off during the wait
                current = await controls._get(session, controls.KEY_FEATURE_FLAGS)
                flags = current.get("flags") or {}
                flags[hb2.FLAG] = {"on": False, "tenants": []}
                await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": flags}, None)
                await session.commit()
            return await super().acquire_spare(reserve, deadline=deadline)
    broker = FakeBroker({"HBIW1": day_bars(FRI)})
    budget = FlagOffWhileWaiting(RateLimits(per_second=100.0, burst=100, per_minute=1000))
    report = owner.run(broker, symbols=["NSE:HBIW1"], start=FRI, budget_for=lambda t, k: budget)
    assert report.status == "partial" and broker.calls == [] and report.requests == 0
    assert _states(owner.tenant_id, "NSE:HBIW1")[FRI].status == "pending"


def test_adapter_is_reused_within_an_ist_day():
    owner = Owner("hb2-cache@example.com")
    _flag(True)
    built = []
    broker = FakeBroker({"HBIK1": day_bars(FRI)})

    def factory(record):
        built.append(record.id)
        return broker
    cache: dict = {}
    for when in (EVENING, EVENING + timedelta(minutes=15)):
        _run(hb2.run_pass(_session_factory, adapter_factory=factory, adapter_cache=cache, clock=lambda w=when: w,
                          symbols=["NSE:HBIK1"], start=FRI, owner_tenant_id=owner.tenant_id, owner_tenant_name=owner.name))
    assert built == [owner.credential_id]
    _run(hb2.run_pass(_session_factory, adapter_factory=factory, adapter_cache=cache, clock=lambda: EVENING + timedelta(days=1),
                      symbols=["NSE:HBIK1"], start=FRI, owner_tenant_id=owner.tenant_id, owner_tenant_name=owner.name))
    assert built == [owner.credential_id] * 2 and len(cache) == 1


def test_one_bad_day_refuses_only_that_day_and_progress_marks_stale_running(monkeypatch):
    owner = Owner("hb2-split@example.com")
    _flag(True)
    days = [FRI - timedelta(days=n) for n in range(1, 4)]                     # Tue 7 .. Thu 9, one window
    bars = [b for d in days for b in day_bars(d)]
    bars[400] = bars[400].model_copy(update={"low": 0.0})                    # a bad bar on Wednesday
    report = owner.run(FakeBroker({"HBIS1": bars}), symbols=["NSE:HBIS1"], start=days[-1], end=days[0])
    states = _states(owner.tenant_id, "NSE:HBIS1")
    assert report.requests == 1 and report.refused == 1 and report.days_done == 2
    assert states[date(2023, 3, 8)].status == "refused" and states[date(2023, 3, 9)].status == "done"
    assert states[date(2023, 3, 7)].status == "done"

    async def stale_and_progress():
        async with _session_factory() as session:
            row = states[date(2023, 3, 8)]
            await session.execute(update(D).where(D.id == row.id).values(
                status="running", started_at=datetime(2023, 3, 10, 12, 0, tzinfo=timezone.utc)))
            await session.commit()
            return await hb2.progress(session, tenant_id=owner.tenant_id, now=datetime(2023, 3, 10, 15, 0, tzinfo=timezone.utc))
    body = _run(stale_and_progress())
    entry = next(a for a in body["attention"] if a["instrument_key"] == "NSE:HBIS1")
    assert entry["status"] == "running" and entry["stale"] is True and entry["started_at"].startswith("2023-03-10T12:00")
    assert body["stale_running"] >= 1


def test_blackout_dates_are_parsed_and_a_typo_refuses_the_job(monkeypatch):
    monkeypatch.setenv("HB2_TEST_DATES", "2026-11-08, 2027-02-01,2026-13-01,nov-8")
    dates, bad = config._iso_dates("HB2_TEST_DATES")
    assert dates == frozenset({date(2026, 11, 8), date(2027, 2, 1)}) and bad == ["2026-13-01", "nov-8"]
    owner = Owner("hb2-blackout@example.com")
    _flag(True)
    monkeypatch.setattr(config, "HISTORY_INTRADAY_BLACKOUT_ERRORS", bad)
    broker = FakeBroker({"HBIY1": day_bars(FRI)})
    with pytest.raises(hb2.IntradayConfigError, match="2026-13-01"):
        owner.run(broker, symbols=["NSE:HBIY1"], start=FRI)
    assert broker.calls == []
