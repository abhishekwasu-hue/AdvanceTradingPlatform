"""M71 PL-1(c): the one-time paced 1-minute backfill, then gaps only (app/market_lake/backfill.py).

No network: the broker is DATA-H's fake adapter (START-labelled bars, no "today" on the historical endpoint) recording
every call; the clock is fixed after the close on a weekday (no holiday calendar in the test database). Covered: the
bookkeeping and resume, chunking per broker range, pacing through the RateBudget (spy), the gap queue's dedupe and drain,
a second run making ZERO history requests, Daily never calling the broker, and the owner-only universe (data terms).
"""
import asyncio
import json
from datetime import date, datetime, time, timedelta

import pytest
from sqlalchemy import select

from app.core import config
from app.db.models import DataQualityEventRecord, LakeBackfillWindowRecord, MdTenantCandleRecord, WatchlistRecord
from app.market_data.calendar import IST
from app.market_lake import backfill as bf
from tests.test_auth_api import _session_factory
from tests.test_data_h_lake_backfill import FRI, NOW, THU, FakeBroker, _count, _credential, _tenant_name, _user, minute_bars

W = LakeBackfillWindowRecord


def _run(coro):
    return asyncio.run(coro)


class SpyBudget:
    """The RateBudget API the backfill uses (`acquire_spare`, `now`), counting every token."""

    def __init__(self, allow: bool = True) -> None:
        self.allow, self.taken, self.reserves = allow, 0, []

    def now(self) -> float:
        return 0.0

    async def acquire_spare(self, reserve, *, deadline=None):
        self.reserves.append(reserve)
        if self.allow:
            self.taken += 1
        return self.allow


class Owner:
    def __init__(self, email: str, monkeypatch, *, days: int = 45) -> None:
        self.user = _user(email)
        self.tenant_id = self.user.tenant_id
        self.credential_id = _credential(self.user)
        monkeypatch.setattr(config, "HISTORY_OWNER_TENANT_ID", self.tenant_id)
        monkeypatch.setattr(config, "HISTORY_OWNER_TENANT_NAME", _tenant_name(self.tenant_id))
        monkeypatch.setattr(config, "HISTORY_OWNER_USER_ID", None)
        monkeypatch.setattr(config, "LAKE_BACKFILL_INDICES", ["NIFTY"])
        monkeypatch.setattr(config, "MARKET_BACKFILL_ENABLED", True)
        monkeypatch.setattr(config, "MARKET_BACKFILL_INTRADAY_DAYS", days)
        monkeypatch.setattr(config, "MARKET_BACKFILL_MAX_REQUESTS", 10_000)
        self.budget = SpyBudget()
        self.budget_keys: list = []

    def _budget_for(self, tenant_id, key):
        self.budget_keys.append((tenant_id, key))
        return self.budget

    def run(self, broker: FakeBroker, **kw) -> bf.PassReport:
        kw.setdefault("symbols", ["NIFTY", "NSE:RELIANCE"])
        return _run(bf.run_backfill(_session_factory, now=NOW, clock=lambda: NOW, budget_for=self._budget_for,
                                    adapter_factory=lambda record: broker, **kw))

    def drain(self, broker: FakeBroker, **kw) -> bf.PassReport:
        return _run(bf.drain_gaps(_session_factory, now=NOW, clock=lambda: NOW, budget_for=self._budget_for,
                                  adapter_factory=lambda record: broker, **kw))


def _broker() -> FakeBroker:
    minutes = {s: minute_bars(THU, base) + minute_bars(FRI, base) for s, base in (("NIFTY 50", 17000), ("RELIANCE", 2400))}
    return FakeBroker({}, minutes)


def _historical(broker: FakeBroker) -> list:
    return [c for c in broker.calls if c[0] == "historical"]


def test_backfill_chunks_per_broker_range_paces_every_call_and_a_second_run_makes_zero_calls(monkeypatch):
    owner = Owner("m71-fill@example.com", monkeypatch)
    broker = _broker()
    report = owner.run(broker)
    assert report.status == "ok" and report.complete and report.failed == 0, report.as_dict()
    hist = _historical(broker)
    windows = report.planned
    assert hist and all(c[3] == "1min" for c in hist)                         # never a Daily request
    assert all((c[5] - c[4]).days + 1 <= bf.minute_days_per_request("upstox") for c in hist)
    assert len(hist) < windows                                                # several grid windows per request
    assert any(c[0] == "intraday" for c in broker.calls)                     # the session that closed today
    assert owner.budget.taken == len(broker.calls) == report.requests       # every request took a rate-budget token
    assert set(owner.budget.reserves) == {config.MARKET_BACKFILL_RESERVE}
    assert owner.budget_keys and owner.budget_keys[0] == (owner.tenant_id, "upstox@primary")
    source = f"broker:upstox:u{owner.user.id}"
    assert _count(MdTenantCandleRecord, MdTenantCandleRecord.tenant_id == owner.tenant_id, MdTenantCandleRecord.source == source,
                  MdTenantCandleRecord.instrument_key == "NSE:RELIANCE", MdTenantCandleRecord.timeframe == "1m") == 750
    # bookkeeping: every window of the range recorded; first / last bar and the complete flag per symbol
    assert _count(W, W.tenant_id == owner.tenant_id, W.timeframe == "1m") == windows

    async def state():
        async with _session_factory() as session:
            return await bf.series_state(session, owner.tenant_id, owner.user.id, [bf.to_symbol("RELIANCE")], FRI)
    row = _run(state())[0]
    assert row["backfill_complete"] and row["last_error"] is None
    assert row["first_bar"].startswith(THU.isoformat()) and row["last_bar"].startswith(FRI.isoformat())
    # PL-3 proof for the write side: the same symbols again -> zero history requests
    calls = len(broker.calls)
    again = owner.run(broker)
    assert again.status == "nothing_missing" and again.requests == 0 and len(broker.calls) == calls


def test_resume_after_a_stop_asks_only_what_is_left(monkeypatch):
    owner = Owner("m71-resume@example.com", monkeypatch, days=120)
    broker = _broker()
    first = owner.run(broker, symbols=["NSE:RELIANCE"], max_requests=2)
    assert first.status == "time_budget" and first.remaining > 0 and first.requests == 2
    asked = {(c[4], c[5]) for c in _historical(broker)}
    second = owner.run(broker, symbols=["NSE:RELIANCE"])
    assert second.complete, second.as_dict()
    later = [(c[4], c[5]) for c in _historical(broker)[len(asked):]]
    assert later and not (set(later) & asked)                                # nothing asked twice


def test_a_refused_rate_token_makes_no_call_and_resumes(monkeypatch):
    owner = Owner("m71-rate@example.com", monkeypatch)
    owner.budget.allow = False
    broker = _broker()
    report = owner.run(broker)
    assert report.status == "rate_limited" and broker.calls == [] and report.remaining == report.planned


def test_chunks_follow_each_brokers_range():
    sym = bf.to_symbol("RELIANCE")
    last = date(2023, 3, 10)
    todo = [bf.Pending(sym, ws, we, ws, min(we, last), None) for ws, we in bf.dh.windows("1m", last - timedelta(days=120), last)]
    for broker, days in (("upstox", 28), ("zerodha", 56), ("groww", 7)):
        groups = bf.chunks(todo, bf.minute_days_per_request(broker))
        assert sum(len(g) for g in groups) == len(todo)
        assert all((g[0].ask_to - g[-1].ask_from).days + 1 <= days for g in groups)
    assert len(bf.chunks(todo, 56)) < len(bf.chunks(todo, 28)) < len(bf.chunks(todo, 7)) == len(todo)


def test_gap_queue_dedupes_merges_and_drains_paced(monkeypatch):
    owner = Owner("m71-gap@example.com", monkeypatch)
    broker = _broker()
    assert owner.run(broker).complete
    calls = len(broker.calls)
    t0 = datetime.combine(FRI, time(10, 30), tzinfo=IST)
    for start, end in ((t0, t0 + timedelta(minutes=10)), (t0 + timedelta(minutes=5), t0 + timedelta(minutes=40)),
                       (t0, t0 + timedelta(minutes=10))):
        _run(bf.request_gap_fill("NSE:RELIANCE", "5m", start, end, session_factory=_session_factory, now=NOW))

    async def gaps():
        async with _session_factory() as session:
            return list(await session.scalars(select(W).where(W.tenant_id == owner.tenant_id, W.status == "gap")))
    queued = _run(gaps())
    assert len(queued) == 1 and queued[0].instrument_key == "NSE:RELIANCE"   # one row: deduped
    assert bf.gap_range(queued[0].error) == (FRI, FRI) and "NSE:RELIANCE" in queued[0].error
    taken = owner.budget.taken
    report = owner.drain(broker)
    assert report.gaps_filled == 1 and report.failed == 0, report.as_dict()
    new = broker.calls[calls:]
    assert new == [("intraday", "RELIANCE", "NSE", "1min")]                  # only today's endpoint for a gap of today
    assert owner.budget.taken - taken == 1
    assert _run(gaps()) == [] and owner.drain(broker).status == "nothing_missing"
    assert len(broker.calls) == calls + 1


def test_a_gap_request_never_raises_into_the_feed(monkeypatch):
    owner = Owner("m71-gap-bad@example.com", monkeypatch)
    _run(bf.request_gap_fill("NSE:RELIANCE", "tick", NOW, NOW, session_factory=_session_factory, now=NOW))
    _run(bf.request_gap_fill("MCX:CRUDEOIL", "1m", NOW, NOW, session_factory=_session_factory, now=NOW))
    assert _count(W, W.tenant_id == owner.tenant_id) == 0


def test_daily_never_calls_the_broker_and_a_missing_bhavcopy_is_a_reported_gap(monkeypatch):
    owner = Owner("m71-daily@example.com", monkeypatch)
    broker = _broker()
    _run(bf.request_gap_fill("NSE:M71DAILYX", "day", NOW - timedelta(days=3), NOW, session_factory=_session_factory, now=NOW))
    assert _count(W, W.instrument_key == "NSE:M71DAILYX") == 0
    assert _count(DataQualityEventRecord, DataQualityEventRecord.instrument_key == "NSE:M71DAILYX",
                  DataQualityEventRecord.timeframe == "day", DataQualityEventRecord.kind == "gap") == 1
    report = owner.run(broker, symbols=["NSE:M71DAILYY"])
    assert all(c[3] == "1min" for c in _historical(broker))
    assert report.daily_gaps == 1
    assert _count(DataQualityEventRecord, DataQualityEventRecord.instrument_key == "NSE:M71DAILYY",
                  DataQualityEventRecord.source == "nse_bhav") == 1


def test_the_universe_is_what_the_owner_himself_uses(monkeypatch):
    owner = Owner("m71-uni@example.com", monkeypatch)
    other = _user("m71-uni-other@example.com")

    async def add():
        async with _session_factory() as session:
            for u, sym in ((owner.user, "TCS"), (other, "INFY")):
                session.add(WatchlistRecord(tenant_id=u.tenant_id, user_id=u.id, name="m71",
                                            symbols_json=json.dumps([sym]), items_json=json.dumps([{"symbol": sym, "exchange": "NSE"}])))
            await session.commit()
    _run(add())

    async def no_system(session, key):
        return None
    monkeypatch.setattr("app.watchlists.system.get", no_system)

    async def uni():
        async with _session_factory() as session:
            return await bf.universe(session, owner.user.id)
    syms, _ = _run(uni())
    keys = {s.key for s in syms}
    assert keys == {"NSE_INDEX:NIFTY50", "NSE:TCS"}                          # never another user's watchlist (M71Q-2)
    nifty = next(s for s in syms if s.key == "NSE_INDEX:NIFTY50")
    assert (nifty.symbol, nifty.exchange) == ("NIFTY 50", "NSE")


@pytest.mark.parametrize("interval,daily,intraday", [("day", True, False), ("1W", True, False), ("5m", False, True),
                                                     ("1H", False, True), ("15min", False, True), ("tick", False, False)])
def test_interval_kinds(interval, daily, intraday):
    assert bf.is_daily(interval) is daily and bf.is_intraday(interval) is intraday
