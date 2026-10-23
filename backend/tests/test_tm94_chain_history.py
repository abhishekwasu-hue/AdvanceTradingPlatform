"""TM 9.4: chain history - option-chain snapshots recorded through the OWNER's own broker session into the owner's
private store (md_tenant_chain_snapshots), and TM-7's fix of the OI collector (each organisation's slots through its own
session, read only by it).

No network: the broker is a fake adapter returning fake chains and recording which credential built it; the clock is
injected; the venue calendar and the instrument master's expiries are faked. Every test uses its own underlying names
(the in-memory database is shared). Covered: compression round-trip; idempotent slot; tenant isolation (another tenant
never reads the owner's history, a teammate never reads the holder's rows, a None tenant is refused); the recorder never
uses another user's or organisation's session; the interval scheduler (one slot per period, venue closed = no read, the
worker hook); the rate budget (spare tokens only, no token = a gap, a 429 ends the slot); retention (kept by default);
the read API and the admin status card; the OI collector fix (per organisation, never through another's session, the
pre-fix rows only for the confirmed owner).
"""
import asyncio
import dataclasses
import math
import uuid
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Dict, List, Optional

import pytest
from sqlalchemy import delete, func, select

from app.brokers.exceptions import BrokerAPIError
from app.brokers.models import OptionChain, OptionChainRow
from app.brokers.rate_budget import RateBudget, RateLimits
from app.core import config
from app.db.models import BrokerCredentialRecord, MdTenantChainSnapshotRecord, OIBannerSettingRecord, OISnapshotRecord, StrikeOISnapshotRecord, Tenant, User
from app.market_data.calendar import IST
from app.market_lake import chain_recorder, chain_store
from app.option_chain import snapshots
from app.platform import controls
from tests.test_auth_api import _register, _session_factory, client
from tests.utils import enable_mfa

R = MdTenantChainSnapshotRecord
# A Tuesday session minute (IST 10:00:20); every test passes its own clock.
T0 = datetime(2026, 3, 10, 10, 0, 20, tzinfo=IST).astimezone(timezone.utc)
DAY = date(2026, 3, 10)
EXP1, EXP2, EXP3 = date(2026, 3, 12), date(2026, 3, 19), date(2026, 3, 26)


def _run(coro):
    return asyncio.run(coro)


def _uniq(prefix: str) -> str:
    return f"{prefix}{uuid.uuid4().hex[:6].upper()}"


def _flag(name: str, on: bool, tenants=()):
    async def go():
        async with _session_factory() as session:
            current = await controls._get(session, controls.KEY_FEATURE_FLAGS)
            flags = current.get("flags") or {}
            flags[name] = {"on": on, "tenants": list(tenants)}
            await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": flags}, None)
            await session.commit()
    _run(go())


def _user(email: str):
    token = _register(email)

    async def go():
        async with _session_factory() as session:
            return await session.scalar(select(User).where(User.email == email))
    return {"Authorization": f"Bearer {token}"}, _run(go())


def _teammate(owner: User, email: str) -> User:
    from app.auth.security import hash_password

    async def go():
        async with _session_factory() as session:
            user = User(tenant_id=owner.tenant_id, email=email, hashed_password=hash_password("S3cur3Pass!"), role="USER", is_active=True)
            session.add(user)
            await session.commit()
            return user
    return _run(go())


def _credential(user: User, *, broker="upstox", label="primary") -> int:
    async def go():
        async with _session_factory() as session:
            rec = BrokerCredentialRecord(tenant_id=user.tenant_id, user_id=user.id, broker_name=broker, account_label=label,
                                         encrypted_payload="not-used-by-the-fake-adapter", token_status="VALID",
                                         token_expires_at=datetime(2099, 1, 1, tzinfo=timezone.utc))
            session.add(rec)
            await session.commit()
            return rec.id
    return _run(go())


def _tenant_name(tenant_id: int) -> str:
    async def go():
        async with _session_factory() as session:
            return (await session.get(Tenant, tenant_id)).name
    return _run(go())


def _count(model, *where) -> int:
    async def go():
        async with _session_factory() as session:
            return int(await session.scalar(select(func.count()).select_from(model).where(*where)) or 0)
    return _run(go())


def fake_chain(underlying: str, spot: float = 22010.0, n: int = 61, step: float = 50.0, bump: float = 0.0) -> OptionChain:
    centre = round(spot / step) * step
    rows = []
    for i in range(n):
        k = centre + (i - n // 2) * step
        rows.append(OptionChainRow(strike=k, call_oi=1000.0 + i + bump, put_oi=2000.0 - i, call_ltp=max(0.05, (spot - k) / 10 + 50),
                                   put_ltp=max(0.05, (k - spot) / 10 + 50), call_iv=12.5, put_iv=13.25, call_bid=10.0, call_ask=10.5,
                                   put_bid=9.0, put_ask=9.5, call_volume=5000.0, put_volume=4000.0, call_change_oi=12.0, put_change_oi=-3.0,
                                   call_delta=0.5, put_delta=-0.5, call_gamma=0.001, put_gamma=0.001, call_theta=-7.5, put_theta=-6.5,
                                   call_vega=11.0, put_vega=11.0))
    return OptionChain(underlying=underlying, expiry="", underlying_ltp=spot, rows=rows)


class FakeAdapter:
    def __init__(self, record, log: List[int], fail: Optional[Dict[str, Exception]] = None):
        self.record_id = record.id
        self.log = log
        self.fail = fail or {}
        self.calls: List[tuple] = []

    async def get_option_chain(self, symbol, expiry=None):
        self.log.append(self.record_id)
        self.calls.append((symbol, expiry))
        if symbol in self.fail:
            raise self.fail[symbol]
        return fake_chain(symbol)


class Owner:
    """The owner's tenant (HB0's rule: id + confirmed name) with the flag on and its own Upstox session."""

    def __init__(self, monkeypatch, tag: str, *, credential: bool = True, names: Optional[List[str]] = None):
        self.headers, self.user = _user(f"tm94-{tag}-{uuid.uuid4().hex[:6]}@example.com")
        self.tenant_id = self.user.tenant_id
        monkeypatch.setattr(config, "HISTORY_OWNER_TENANT_ID", self.tenant_id)
        monkeypatch.setattr(config, "HISTORY_OWNER_TENANT_NAME", _tenant_name(self.tenant_id))
        monkeypatch.setattr(config, "HISTORY_OWNER_USER_ID", None)
        self.names = names or [_uniq("IDXA"), _uniq("IDXB")]
        monkeypatch.setattr(config, "CHAIN_HISTORY_UNDERLYINGS", list(self.names))
        monkeypatch.setattr(config, "CHAIN_HISTORY_STOCKS_ENABLED", False)
        monkeypatch.setattr(config, "CHAIN_HISTORY_SLOT_MINUTES", 1)
        monkeypatch.setattr(config, "CHAIN_HISTORY_EXPIRIES", 2)
        self.credential_id = _credential(self.user) if credential else None
        self.used: List[int] = []
        self.is_open = {"v": True}
        _flag(chain_recorder.FLAG, True, [self.tenant_id])

        async def fake_status(session, now=None, exchange="NSE"):
            return SimpleNamespace(is_open=self.is_open["v"])

        async def fake_expiries(session, underlying, *, broker="upstox", instrument_type="CE", on_or_after=None):
            return [EXP1, EXP2, EXP3]
        from app.instruments import master
        monkeypatch.setattr(chain_recorder, "market_session_status", fake_status)
        monkeypatch.setattr(master, "expiries", fake_expiries)

    def record(self, when=T0, fail=None, **kw):
        adapters: List[FakeAdapter] = []

        def factory(record):
            adapter = FakeAdapter(record, self.used, fail)
            adapters.append(adapter)
            return adapter
        report = _run(chain_recorder.record_slot(_session_factory, when, adapter_factory=factory, **kw))
        return report, adapters


@pytest.fixture(autouse=True)
def _flags_off():
    yield
    _flag(chain_recorder.FLAG, False)


# --- the codec -------------------------------------------------------------------------------------------------------

def test_compression_round_trip_keeps_every_field_and_drops_nothing_silently():
    chain = fake_chain("RT")
    chain.rows[0].call_iv = math.nan                       # a non-finite value is stored as missing, never as a number
    chain.rows[1].put_theta = None
    payload, raw, strikes = chain_store.encode(chain.rows)
    assert strikes == 61 and len(payload) < raw / 3        # compressed
    back = chain_store.decode(payload, chain_store.CODEC)
    assert len(back) == 61 and back[0].call_iv is None and back[1].put_theta is None
    for a, b in zip(chain.rows[2:], back[2:]):
        assert a.model_dump() == b.model_dump()
    with pytest.raises(ValueError):
        chain_store.decode(payload, "gzip-csv")
    assert chain_store.FIELD_NAMES[:2] == ("ltp", "bid") and "gamma" in chain_store.FIELD_NAMES and "vega" in chain_store.FIELD_NAMES


def test_slots_follow_the_interval_setting():
    assert chain_store.slot_start(T0, 1) == T0.replace(second=0)
    assert chain_store.slot_start(T0 + timedelta(minutes=3), 5) == T0.replace(second=0)
    assert chain_recorder.due(T0, None) == T0.replace(second=0)
    assert chain_recorder.due(T0 + timedelta(seconds=30), T0.replace(second=0)) is None             # the same minute: no second slot
    assert chain_recorder.due(T0 + timedelta(minutes=1), T0.replace(second=0)) == T0.replace(second=0) + timedelta(minutes=1)
    expected = chain_store.expected_slots(DAY, datetime(2026, 3, 10, 9, 20, tzinfo=IST), 1)
    assert [s.astimezone(IST).strftime("%H:%M") for s in expected] == ["09:15", "09:16", "09:17", "09:18", "09:19"]
    gaps = chain_store.gap_ranges(expected, [expected[0], expected[3]], 1)
    assert [(g["from"][11:16], g["to"][11:16]) for g in gaps] == [("09:16", "09:18"), ("09:19", "09:20")]


# --- the store and tenancy -------------------------------------------------------------------------------------------

def test_write_is_idempotent_per_slot_and_only_the_tenant_reads_it():
    _, a = _user(f"tm94-store-a-{uuid.uuid4().hex[:6]}@example.com")
    _, b = _user(f"tm94-store-b-{uuid.uuid4().hex[:6]}@example.com")
    name = _uniq("STO")

    async def go():
        async with _session_factory() as session:
            first = await chain_store.write(session, a.tenant_id, name, EXP1, fake_chain(name), T0, slot_minutes=1, source="test")
            again = await chain_store.write(session, a.tenant_id, name, EXP1, fake_chain(name, bump=5), T0 + timedelta(seconds=20),
                                            slot_minutes=1, source="test")
            nxt = await chain_store.write(session, a.tenant_id, name, EXP1, fake_chain(name), T0 + timedelta(minutes=1), slot_minutes=1, source="test")
            empty = await chain_store.write(session, a.tenant_id, name, EXP2, OptionChain(underlying=name, expiry="", rows=[]), T0,
                                            slot_minutes=1, source="test")
            await session.commit()
            mine = await chain_store.read_day(session, a.tenant_id, name, DAY)
            theirs = await chain_store.read_day(session, b.tenant_id, name, DAY)
            with pytest.raises(PermissionError):
                await chain_store.read_day(session, None, name, DAY)     # type: ignore[arg-type]
            with pytest.raises(ValueError):
                await chain_store.read_day(session, a.tenant_id, name, DAY, max_chains=1)
            return first, again, nxt, empty, mine, theirs
    first, again, nxt, empty, mine, theirs = _run(go())
    assert (first.status, again.status, nxt.status, empty.status) == ("OK", "ALREADY_EXISTS", "OK", "EMPTY")
    assert first.stored_bytes < first.raw_bytes and first.strikes == 61
    assert len(mine) == 2 and mine[0].rows[0].call_oi == 1000.0 and mine[0].spot == 22010.0 and theirs == []


def test_rows_from_a_members_session_are_read_only_by_that_member():
    _, owner = _user(f"tm94-holder-{uuid.uuid4().hex[:6]}@example.com")
    mate = _teammate(owner, f"tm94-holder-mate-{uuid.uuid4().hex[:6]}@example.com")
    name = _uniq("HOL")

    async def go():
        async with _session_factory() as session:
            await chain_store.write(session, owner.tenant_id, name, EXP1, fake_chain(name), T0, slot_minutes=1,
                                    source=f"broker:upstox:u{owner.id}")
            await session.commit()
            return (len(await chain_store.read_day(session, owner.tenant_id, name, DAY, reader_user_id=owner.id)),
                    len(await chain_store.read_day(session, owner.tenant_id, name, DAY, reader_user_id=mate.id)))
    assert _run(go()) == (1, 0)


# --- the recorder ----------------------------------------------------------------------------------------------------

def test_recorder_reads_through_the_owners_own_session_only(monkeypatch):
    owner = Owner(monkeypatch, "rec", credential=False)
    mate = _teammate(owner.user, f"tm94-rec-mate-{uuid.uuid4().hex[:6]}@example.com")
    mate_cred = _credential(mate, label="mate")                     # a teammate's session: never used
    _, other = _user(f"tm94-rec-other-{uuid.uuid4().hex[:6]}@example.com")
    other_cred = _credential(other)                                 # another organisation's session: never used
    report, adapters = owner.record()
    assert report.status == "no_session" and adapters == [] and owner.used == [] and report.skipped == 4
    assert _count(R, R.underlying.in_(owner.names)) == 0

    owner.credential_id = _credential(owner.user, label="own")
    report, adapters = owner.record()
    assert report.status == "ok" and report.reads == 4 and report.stored == 4, report
    assert set(owner.used) == {owner.credential_id} and mate_cred not in owner.used and other_cred not in owner.used
    assert adapters[0].calls == [(owner.names[0], EXP1), (owner.names[0], EXP2), (owner.names[1], EXP1), (owner.names[1], EXP2)]
    assert _count(R, R.underlying.in_(owner.names), R.tenant_id == owner.tenant_id) == 4
    assert _count(R, R.underlying.in_(owner.names), R.tenant_id != owner.tenant_id) == 0
    source = f"broker:upstox:u{owner.user.id}"
    assert _count(R, R.underlying.in_(owner.names), R.source != source) == 0

    again, _ = owner.record(T0 + timedelta(seconds=25))              # the same slot (another replica): nothing read again
    assert again.reads == 0 and again.already == 4


def test_recorder_needs_the_confirmed_owner_and_the_flag(monkeypatch):
    owner = Owner(monkeypatch, "cfg")
    monkeypatch.setattr(config, "HISTORY_OWNER_TENANT_NAME", "someone else")
    report, adapters = owner.record()
    assert report.status == "not_configured" and adapters == []
    monkeypatch.setattr(config, "HISTORY_OWNER_TENANT_NAME", _tenant_name(owner.tenant_id))
    _flag(chain_recorder.FLAG, False)
    report, adapters = owner.record()
    assert report.status == "off" and adapters == [] and owner.used == []


def test_a_closed_venue_reads_nothing_and_stocks_only_behind_their_setting(monkeypatch):
    owner = Owner(monkeypatch, "closed")
    owner.is_open["v"] = False
    report, adapters = owner.record()
    assert report.status == "closed" and adapters == [] and owner.used == []
    owner.is_open["v"] = True
    stock = _uniq("STK")
    monkeypatch.setattr(config, "CHAIN_HISTORY_STOCKS", [stock])
    assert stock not in chain_recorder.underlyings()
    monkeypatch.setattr(config, "CHAIN_HISTORY_STOCKS_ENABLED", True)
    assert chain_recorder.underlyings()[-1] == stock                  # the indices first
    monkeypatch.setattr(config, "CHAIN_HISTORY_MAX_READS_PER_SLOT", 3)
    report, adapters = owner.record()
    assert report.reads == 3 and report.skipped == 3 and stock not in [c[0] for c in adapters[0].calls]


def test_the_rate_budget_gives_spare_tokens_only_and_no_token_is_a_gap(monkeypatch):
    owner = Owner(monkeypatch, "budget")
    asked: List[float] = []

    class Spare(RateBudget):
        def __init__(self, grant):
            super().__init__(RateLimits(per_second=8.0, burst=10, per_minute=200))
            self.grant = grant

        async def acquire_spare(self, reserve, *, deadline=None):
            asked.append(reserve)
            if self.grant <= 0:
                return False
            self.grant -= 1
            return True
    budget = Spare(grant=1)
    keys: List[tuple] = []

    def budget_for(tenant_id, key):
        keys.append((tenant_id, key))
        return budget
    report, adapters = owner.record(budget_for=budget_for, reserve=0.6, token_wait_seconds=0)
    assert keys == [(owner.tenant_id, "upstox@primary")] and asked == [0.6, 0.6]
    assert report.status == "rate_budget" and report.reads == 1 and report.stored == 1 and report.skipped == 3
    assert len(adapters[0].calls) == 1

    # a real budget with its tokens used by the trading worker: the reserve is never touched
    real = RateBudget(RateLimits(per_second=1.0, burst=2, per_minute=2))
    _run(real.acquire())
    report, _ = owner.record(T0 + timedelta(minutes=1), budget_for=lambda t, k: real, reserve=0.5, token_wait_seconds=0)
    assert report.status == "rate_budget" and report.reads == 0


def test_a_429_ends_the_slot_and_a_failed_chain_does_not_stop_the_others(monkeypatch):
    owner = Owner(monkeypatch, "fail")
    report, adapters = owner.record(fail={owner.names[0]: RuntimeError("upstream")})
    assert report.status == "ok" and report.reads == 4 and report.stored == 2 and len(report.notes) == 2
    report, adapters = owner.record(T0 + timedelta(minutes=1), fail={owner.names[0]: BrokerAPIError("slow down", 429)})
    assert report.status == "rate_limited" and report.reads == 1 and report.skipped == 3


def test_the_worker_hook_is_gated_shares_the_budget_and_runs_once_per_slot(monkeypatch):
    from app.workers.trading_worker import TradingWorker
    owner = Owner(monkeypatch, "worker")
    seen: List = []

    async def fake_slot(factory, now=None, *, budget_for=None, adapter_cache=None, **kw):
        seen.append((now, budget_for))
        return chain_recorder.SlotReport()
    monkeypatch.setattr(chain_recorder, "record_slot", fake_slot)

    async def go():
        worker = TradingWorker(_session_factory)
        out = []
        for when in (T0, T0 + timedelta(seconds=30), T0 + timedelta(minutes=1)):
            async with _session_factory() as session:
                out.append(await worker._maybe_start_chain_history(session, when))
            if worker._chain_task is not None:
                await worker._chain_task
        shared = worker._budget_for(owner.tenant_id, "upstox@primary")
        return out, worker, shared
    out, worker, shared = _run(go())
    assert out == [True, False, True] and len(seen) == 2
    assert seen[0][1].__self__ is worker and seen[0][1](owner.tenant_id, "upstox@primary") is shared
    _flag(chain_recorder.FLAG, False)

    async def off():
        w = TradingWorker(_session_factory)
        async with _session_factory() as session:
            return await w._maybe_start_chain_history(session, T0 + timedelta(minutes=5))
    assert _run(off()) is False and len(seen) == 2


def test_retention_keeps_the_history_unless_the_owner_sets_a_limit(monkeypatch):
    from app.retention import service as retention
    from app.retention.policy import load_policy
    _, a = _user(f"tm94-ret-{uuid.uuid4().hex[:6]}@example.com")
    name = _uniq("RET")

    async def store():
        async with _session_factory() as session:
            await chain_store.write(session, a.tenant_id, name, EXP1, fake_chain(name), T0 - timedelta(days=900), slot_minutes=1, source="t")
            await chain_store.write(session, a.tenant_id, name, EXP1, fake_chain(name), T0, slot_minutes=1, source="t")
            await session.commit()
    _run(store())

    async def go(policy):
        async with _session_factory() as session:
            return await retention.run_retention(session, now=T0, policy=policy)
    assert load_policy().chain_history_days == 0
    kept = _run(go(dataclasses.replace(load_policy(), enabled=True)))
    assert "md_tenant_chain_snapshots" not in kept.deleted and _count(R, R.underlying == name) == 2
    monkeypatch.setenv("RETENTION_CHAIN_HISTORY_DAYS", "400")
    assert load_policy().chain_history_days == 400
    trimmed = _run(go(dataclasses.replace(load_policy(), enabled=True)))
    assert trimmed.deleted["md_tenant_chain_snapshots"] == 1 and _count(R, R.underlying == name) == 1


# --- the read API and the admin card ---------------------------------------------------------------------------------

def test_read_api_is_tenant_scoped_flag_gated_and_capped(monkeypatch):
    owner = Owner(monkeypatch, "api")
    owner.record()
    owner.record(T0 + timedelta(minutes=1))
    name = owner.names[0]
    other_headers, other = _user(f"tm94-api-other-{uuid.uuid4().hex[:6]}@example.com")
    mine = client.get("/api/chain-history/day", params={"underlying": name.lower(), "day": DAY.isoformat()}, headers=owner.headers)
    assert mine.status_code == 200, mine.text
    body = mine.json()
    assert len(body["chains"]) == 4 and body["chains"][0]["slot"].startswith("2026-03-10T10:00") and body["fields"][0] == "ltp"
    assert len(body["chains"][0]["rows"][0]) == 1 + 2 * len(chain_store.FIELD_NAMES)
    one = client.get("/api/chain-history/day", params={"underlying": name, "day": DAY.isoformat(), "expiry": EXP2.isoformat()},
                     headers=owner.headers).json()
    assert {c["expiry"] for c in one["chains"]} == {EXP2.isoformat()}
    cov = client.get("/api/chain-history/coverage", params={"underlying": name}, headers=owner.headers).json()
    assert cov["days"] == [{"underlying": name, "day": DAY.isoformat(), "chains": 4, "expiries": 2,
                            "first_slot": "2026-03-10T10:00:00+05:30", "last_slot": "2026-03-10T10:01:00+05:30"}]
    rng = client.get("/api/chain-history/range", params={"underlying": name, "start": "2026-03-09", "end": "2026-03-10"}, headers=owner.headers)
    assert rng.status_code == 200 and len(rng.json()["chains"]) == 4
    # another organisation: the flag on for it too, and still nothing of the owner's
    _flag(chain_recorder.FLAG, True, [owner.tenant_id, other.tenant_id])
    theirs = client.get("/api/chain-history/day", params={"underlying": name, "day": DAY.isoformat()}, headers=other_headers)
    assert theirs.status_code == 200 and theirs.json()["chains"] == []
    assert client.get("/api/chain-history/coverage", headers=other_headers).json()["days"] == []
    assert client.get("/api/chain-history/range", params={"underlying": name, "start": "2026-03-09", "end": "2026-03-10"},
                      headers=other_headers).json()["chains"] == []
    # limits and input
    monkeypatch.setattr(config, "CHAIN_HISTORY_READ_MAX_CHAINS", 3)
    assert client.get("/api/chain-history/day", params={"underlying": name, "day": DAY.isoformat()}, headers=owner.headers).status_code == 422
    assert client.get("/api/chain-history/range", params={"underlying": name, "start": "2026-01-01", "end": "2026-03-10"},
                      headers=owner.headers).status_code == 422
    assert client.get("/api/chain-history/range", params={"underlying": name, "start": "2026-03-10", "end": "2026-03-09"},
                      headers=owner.headers).status_code == 422
    assert client.get("/api/chain-history/day", params={"underlying": "BAD;NAME"}, headers=owner.headers).status_code == 422
    assert client.get("/api/chain-history/day", params={"underlying": name}).status_code == 401
    _flag(chain_recorder.FLAG, False)
    off = client.get("/api/chain-history/day", params={"underlying": name}, headers=owner.headers)
    assert off.status_code == 404 and off.headers.get("X-Feature-Disabled") == "chain_history"


def test_admin_status_card_counts_gaps_and_is_super_admin_only(monkeypatch):
    owner = Owner(monkeypatch, "status")
    first = datetime(2026, 3, 10, 9, 15, 10, tzinfo=IST).astimezone(timezone.utc)
    owner.record(first)
    owner.record(first + timedelta(minutes=3))
    now = first + timedelta(minutes=5)

    async def go():
        async with _session_factory() as session:
            return await chain_recorder.status(session, now)
    body = _run(go())
    assert body["flag_on"] and body["owner_configured"] and body["owner_session"] == {"broker": "upstox"} and body["problem"] is None
    row = next(r for r in body["underlyings"] if r["underlying"] == owner.names[0])
    assert row["chains_today"] == 4 and row["slots_today"] == 2 and row["slots_expected"] == 5 and row["missing_slots"] == 3
    assert [(g["from"][11:16], g["to"][11:16]) for g in row["gaps"]] == [("09:16", "09:18"), ("09:19", "09:20")]
    assert row["last_captured_at"].startswith("2026-03-10T03:48") and row["stored_bytes_today"] > 0
    assert body["totals"]["chains"] >= 8

    plain = {"Authorization": f"Bearer {_register(f'tm94-plain-{uuid.uuid4().hex[:6]}@example.com')}"}
    assert client.get("/api/admin/chain-history/status", headers=plain).status_code == 403
    email = f"tm94-admin-{uuid.uuid4().hex[:6]}@example.com"
    headers = {"Authorization": f"Bearer {_register(email)}"}

    async def promote():
        async with _session_factory() as session:
            user = await session.scalar(select(User).where(User.email == email))
            user.role = "SUPER_ADMIN"
            await session.commit()
    _run(promote())
    enable_mfa(headers)
    res = client.get("/api/admin/chain-history/status", headers=headers)
    assert res.status_code == 200, res.text
    assert "rows" not in res.text and "ltp" not in res.text                   # counts and times only, no prices


def test_flag_is_registered_off_by_default():
    from app.platform.flag_catalog import FLAG_INFO
    assert chain_recorder.FLAG in controls.FEATURE_FLAGS and chain_recorder.FLAG in controls.DEFAULT_OFF_FLAGS
    assert chain_recorder.FLAG in FLAG_INFO


def test_migration_is_additive_rollback_safe_and_the_only_head():
    import subprocess
    import sys
    from pathlib import Path
    backend = Path(__file__).resolve().parents[1]
    lint = subprocess.run([sys.executable, str(backend / "scripts" / "migration_rollback_safety.py"), "lint"], capture_output=True, text=True)
    assert lint.returncode == 0, lint.stdout + lint.stderr
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    script = ScriptDirectory.from_config(Config(str(backend / "alembic.ini")))
    assert script.get_heads() == ["250d796123b7"]


# --- TM-7: the OI collector --------------------------------------------------------------------------------------------

class _OIBroker:
    name = "fake"

    def __init__(self, tenant_id, log):
        self.tenant_id = tenant_id
        self.log = log

    async def get_option_chain(self, symbol, expiry=None):
        self.log.append(self.tenant_id)
        return fake_chain(symbol, spot=24510.0, n=41)


def test_oi_collector_reads_each_organisation_through_its_own_session_only(monkeypatch):
    from app.workers import trading_worker as tw
    und = _uniq("OIX")
    a_headers, a = _user(f"tm94-oi-a-{uuid.uuid4().hex[:6]}@example.com")
    b_headers, b = _user(f"tm94-oi-b-{uuid.uuid4().hex[:6]}@example.com")
    for h in (a_headers, b_headers):
        assert client.put(f"/api/option-chain/{und}/settings", json={"enabled": True}, headers=h).status_code == 200
    worker = tw.TradingWorker(_session_factory, cycle_seconds=60)
    log: List[int] = []

    async def fake_status(session, now, exchange="NSE"):
        return SimpleNamespace(is_open=True)

    async def fake_broker(session, tenant_id, now):
        return _OIBroker(tenant_id, log) if tenant_id == a.tenant_id else None      # B has no broker session of its own
    monkeypatch.setattr(tw, "market_session_status", fake_status)
    monkeypatch.setattr(worker, "_first_usable_broker", fake_broker)

    async def cycle(when):
        async with _session_factory() as session:
            return await worker._oi_banner(session, when)
    when = datetime(2026, 3, 10, 9, 20, tzinfo=IST).astimezone(timezone.utc)
    try:
        _check_oi_collector(und, a, b, a_headers, b_headers, worker, log, cycle, when, monkeypatch)
    finally:                                   # no enabled underlying is left behind for the worker tests that follow
        async def drop():
            async with _session_factory() as session:
                await session.execute(delete(OIBannerSettingRecord).where(OIBannerSettingRecord.underlying == und))
                await session.commit()
        _run(drop())


def _check_oi_collector(und, a, b, a_headers, b_headers, worker, log, cycle, when, monkeypatch):
    _run(cycle(when))
    assert log == [a.tenant_id]                                                         # B's slot never through A's session
    assert _count(OISnapshotRecord, OISnapshotRecord.underlying == und) == 1
    assert _count(OISnapshotRecord, OISnapshotRecord.underlying == und, OISnapshotRecord.tenant_id == a.tenant_id) == 1

    a_rows = client.get(f"/api/option-chain/{und}/history", params={"date": "2026-03-10"}, headers=a_headers).json()["rows"]
    b_rows = client.get(f"/api/option-chain/{und}/history", params={"date": "2026-03-10"}, headers=b_headers).json()["rows"]
    assert len(a_rows) == 1 and b_rows == []

    # B links its own broker: from the next slot B has its own slots, read through its own session
    async def both(session, tenant_id, now):
        return _OIBroker(tenant_id, log) if tenant_id in (a.tenant_id, b.tenant_id) else None
    monkeypatch.setattr(worker, "_first_usable_broker", both)
    _run(cycle(when + timedelta(minutes=5)))
    assert log[1:] == sorted([a.tenant_id, b.tenant_id])
    assert _count(OISnapshotRecord, OISnapshotRecord.underlying == und, OISnapshotRecord.tenant_id == b.tenant_id) == 1


def test_pre_fix_oi_rows_are_read_only_by_the_confirmed_owner(monkeypatch):
    und = _uniq("OIL")
    _, owner = _user(f"tm94-legacy-owner-{uuid.uuid4().hex[:6]}@example.com")
    _, other = _user(f"tm94-legacy-other-{uuid.uuid4().hex[:6]}@example.com")
    slot = datetime(2026, 3, 10, 9, 20, tzinfo=IST).astimezone(timezone.utc)

    async def legacy():                       # a slot stored before TM-7: no organisation on the row
        async with _session_factory() as session:
            snap = OISnapshotRecord(tenant_id=None, underlying=und, trade_date=DAY, slot_start=slot, captured_at=slot, expiry=EXP3,
                                    underlying_price=24510.0, strikes=1, source="old")
            session.add(snap)
            await session.flush()
            session.add(StrikeOISnapshotRecord(snapshot_id=snap.id, strike=24500.0, call_oi=1.0, put_oi=2.0))
            await session.commit()
    _run(legacy())

    async def read(tenant_id):
        async with _session_factory() as session:
            return len(await snapshots.day_chains(session, und, DAY, tenant_id=tenant_id))
    monkeypatch.setattr(config, "HISTORY_OWNER_TENANT_ID", None)
    assert (_run(read(owner.tenant_id)), _run(read(other.tenant_id))) == (0, 0)          # no confirmed owner: nobody
    monkeypatch.setattr(config, "HISTORY_OWNER_TENANT_ID", owner.tenant_id)
    monkeypatch.setattr(config, "HISTORY_OWNER_TENANT_NAME", "not the name")
    assert _run(read(owner.tenant_id)) == 0                                               # unconfirmed: nobody
    monkeypatch.setattr(config, "HISTORY_OWNER_TENANT_NAME", _tenant_name(owner.tenant_id))
    assert (_run(read(owner.tenant_id)), _run(read(other.tenant_id))) == (1, 0)

    async def own_slot():                     # the owner's own slot at the same time wins over the pre-fix one
        async with _session_factory() as session:
            await snapshots.collect(session, und, fake_chain(und, spot=24510.0, n=5), slot, tenant_id=owner.tenant_id, slot_minutes=5)
            await session.commit()
            slots = await snapshots.day_chains(session, und, DAY, tenant_id=owner.tenant_id)
            return len(slots), len(slots[0].chain.rows)
    assert _run(own_slot()) == (1, 5)
    with pytest.raises(PermissionError):
        _run(read(None))
