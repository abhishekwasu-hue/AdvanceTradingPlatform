"""H-C3c: research studies as jobs - the API queues (202), the research worker (a separate process) runs one study at a
time, progress is the trial ledger, a stopped worker leaves the study `interrupted`, one study at a time per
organisation, and the list / detail endpoints are scoped to the organisation."""
import asyncio
import json

import pytest
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.ai import research_jobs
from app.db.models import ResearchStudyRecord
from tests.test_auth_api import _session_factory, client
from tests.test_hc3b_research_api import FakeProvider, _flags
from tests.test_hc3b_research_loop import _draft, _frame
from tests.test_phase_l_ai import _owner

T0 = datetime(2026, 3, 10, 6, 0, tzinfo=timezone.utc)


@pytest.fixture
def research_on():
    _flags(ai_research=True)
    yield
    _flags(ai_research=False)


@pytest.fixture
def no_ack_gate():
    from app.ai.compliance_terms import require_ai_acknowledged
    from app.auth.dependencies import get_current_user
    from app.main import app
    app.dependency_overrides[require_ai_acknowledged] = get_current_user
    yield
    app.dependency_overrides.pop(require_ai_acknowledged, None)


def _run(coro):
    return asyncio.run(coro)


def _patch(monkeypatch, replies, frame=None):
    from app.ai import settings as ai_settings
    from app.ai.tools import market
    provider = FakeProvider(replies)

    async def provider_for(*args, **kwargs):
        return provider
    monkeypatch.setattr(ai_settings, "provider_for", provider_for)

    async def bars(ctx, symbol, exchange, timeframe, days):
        return (frame if frame is not None else _frame()), "broker:fake"
    monkeypatch.setattr(market, "_frame", bars)
    return provider


def _enqueue(tenant_id, user_id, idea="EMA trend", now=T0):
    async def go():
        async with _session_factory() as session:
            row = await research_jobs.enqueue(session, tenant_id=tenant_id, user_id=user_id, idea=idea, symbol="nifty 50",
                                              exchange="NSE", timeframe="15min", days=120, max_drafts=3, now=now)
            return row.study_id
    return _run(go())


def _row(study_id):
    async def go():
        async with _session_factory() as session:
            return await session.scalar(select(ResearchStudyRecord).where(ResearchStudyRecord.study_id == study_id))
    return _run(go())


def _clear_queue():
    """Other tests' studies share this database: close them so this test sees only its own."""
    from sqlalchemy import update

    async def go():
        async with _session_factory() as session:
            await session.execute(update(ResearchStudyRecord).where(ResearchStudyRecord.status.in_(research_jobs.ACTIVE))
                                  .values(status="failed", error="cleared by a test"))
            await session.commit()
    _run(go())


def _step(clock=None):
    async def go():
        async with _session_factory() as session:
            return await research_jobs.run_next(session, now=clock)
    return _run(go())


def test_one_study_at_a_time_per_organisation_and_a_finished_one_frees_the_slot(monkeypatch, research_on, no_ack_gate):
    headers, me = _owner("hc3c-busy@example.com")
    _patch(monkeypatch, [json.dumps(_draft("a", 9, 21)), "null"])
    body = {"idea": "EMA trend", "symbol": "NIFTY 50", "timeframe": "15min", "max_drafts": 2}
    first = client.post("/api/ai/research", headers=headers, json=body)
    assert first.status_code == 202 and first.json()["symbol"] == "NIFTY 50"
    busy = client.post("/api/ai/research", headers=headers, json=body)
    assert busy.status_code == 409 and "already queued or running" in busy.json()["detail"]
    other, _ = _owner("hc3c-busy-other@example.com")
    assert client.post("/api/ai/research", headers=other, json=body).status_code == 202       # another organisation is not blocked
    assert _step() == first.json()["study_id"]                                                 # oldest first
    assert client.post("/api/ai/research", headers=headers, json=body).status_code == 202


def test_progress_comes_from_the_ledger_and_each_draft_is_a_heartbeat(monkeypatch, research_on, no_ack_gate):
    _, me = _owner("hc3c-progress@example.com")
    _clear_queue()
    _patch(monkeypatch, [json.dumps(_draft("a", 9, 21)), json.dumps({"name": "broken"}), "null"])
    sid = _enqueue(me["tenant_id"], me["user_id"] if "user_id" in me else me["id"])
    ticks = iter(T0 + timedelta(minutes=i) for i in range(100))
    assert _step(lambda: next(ticks)) == sid
    row = _row(sid)
    assert row.status == "done" and row.started_at is not None and row.finished_at is not None
    assert row.heartbeat_at.replace(tzinfo=timezone.utc) > row.started_at.replace(tzinfo=timezone.utc)   # beat per draft

    async def prog():
        async with _session_factory() as session:
            return await research_jobs.progress(session, row)
    assert _run(prog()) == {"drafts_tried": 2, "max_drafts": 3}                                # the OOS check is not a draft
    assert _step() is None                                                                     # nothing left queued


def test_a_running_study_without_a_heartbeat_is_interrupted_and_a_claim_is_taken_once(monkeypatch):
    _, me = _owner("hc3c-stale@example.com")
    uid = me.get("user_id", me.get("id"))
    _clear_queue()
    sid = _enqueue(me["tenant_id"], uid)

    async def claim_twice():
        async with _session_factory() as a, _session_factory() as b:
            first = await research_jobs.claim_next(a, T0)
            second = await research_jobs.claim_next(b, T0)
            return first, second
    first, second = _run(claim_twice())
    assert first is not None and first.study_id == sid and second is None

    async def late_claim():                                     # a second worker that saw the row while it was queued
        async with _session_factory() as session:
            return await research_jobs._try_claim(session, first.id, T0)
    assert _run(late_claim()) is False

    async def stale(minutes):
        async with _session_factory() as session:
            return await research_jobs.mark_stale(session, T0 + timedelta(minutes=minutes))
    assert _run(stale(research_jobs.STALE_MINUTES - 1)) == 0                                   # still beating recently enough
    assert _run(stale(research_jobs.STALE_MINUTES + 1)) == 1
    row = _row(sid)
    assert row.status == "interrupted" and "stopped" in row.error


def test_failures_end_the_study_with_a_reason_and_the_worker_goes_on(monkeypatch, research_on):
    from app.ai import settings as ai_settings
    from app.ai.providers import RuleBasedProvider
    _, me = _owner("hc3c-fail@example.com")
    uid = me.get("user_id", me.get("id"))
    _clear_queue()
    sid = _enqueue(me["tenant_id"], uid)

    _flags(ai_research=False)                                                                 # turned off after queueing
    assert _step() == sid and "ai_research feature was turned off" in _row(sid).error
    _flags(ai_research=True)
    sid = _enqueue(me["tenant_id"], uid, idea="again", now=T0 + timedelta(seconds=30))

    async def rules(*args, **kwargs):
        return RuleBasedProvider()
    monkeypatch.setattr(ai_settings, "provider_for", rules)                                    # provider changed after queueing
    assert _step() == sid
    row = _row(sid)
    assert row.status == "failed" and "AI provider" in row.error

    _patch(monkeypatch, [json.dumps(_draft("a", 9, 21))])
    from app.ai import research_loop

    async def boom(*args, **kwargs):
        raise RuntimeError("backtest exploded")
    monkeypatch.setattr(research_loop, "run_study", boom)
    sid2 = _enqueue(me["tenant_id"], uid, idea="second idea", now=T0 + timedelta(minutes=1))
    assert _step() == sid2
    assert _row(sid2).status == "failed" and "RuntimeError: backtest exploded" in _row(sid2).error


def test_the_worker_loop_drains_the_queue_and_stops_on_request(monkeypatch, research_on):
    from app.workers import research_worker
    _patch(monkeypatch, ["null", "null"])
    _clear_queue()
    _, ma = _owner("hc3c-loop-a@example.com")
    _, mb = _owner("hc3c-loop-b@example.com")
    sids = [_enqueue(ma["tenant_id"], ma.get("user_id", ma.get("id"))), _enqueue(mb["tenant_id"], mb.get("user_id", mb.get("id")))]
    monkeypatch.setattr(research_worker, "POLL_SECONDS", 0.01)

    async def go():
        stop = asyncio.Event()
        task = asyncio.create_task(research_worker.run_forever(_session_factory, stop))
        for _ in range(200):
            await asyncio.sleep(0.02)
            async with _session_factory() as session:
                rows = (await session.scalars(select(ResearchStudyRecord).where(ResearchStudyRecord.study_id.in_(sids)))).all()
            if all(r.status not in research_jobs.ACTIVE for r in rows):
                break
        stop.set()
        await asyncio.wait_for(task, timeout=2)
        return {r.study_id: r.status for r in rows}
    statuses = _run(go())
    assert set(statuses) == set(sids) and all(s in ("done", "failed") for s in statuses.values())


def test_list_and_detail_are_scoped_and_old_ledger_only_studies_still_read(monkeypatch, research_on, no_ack_gate):
    headers, me = _owner("hc3c-list@example.com")
    other, _ = _owner("hc3c-list-other@example.com")
    _patch(monkeypatch, [json.dumps(_draft("a", 9, 21)), "null"])
    sid = client.post("/api/ai/research", headers=headers, json={"idea": "EMA trend", "max_drafts": 2}).json()["study_id"]
    listed = client.get("/api/ai/research", headers=headers).json()["studies"]
    assert [s["study_id"] for s in listed] == [sid] and listed[0]["status"] == "queued"
    assert client.get("/api/ai/research", headers=other).json()["studies"] == []
    queued = client.get(f"/api/ai/research/{sid}", headers=headers).json()
    assert queued["status"] == "queued" and queued["report"] is None and queued["trials"] == []
    assert client.get(f"/api/ai/research/{sid}", headers=other).status_code == 404
    _step()
    # a study from before H-C3c: trials in the ledger, no job row
    from app.ai import research

    async def old():
        async with _session_factory() as session:
            await research.record_trial(session, tenant_id=me["tenant_id"], user_id=None, study_id="legacy-study", symbol="NIFTY 50",
                                        exchange="NSE", timeframe="15min", dsl={"name": "x"}, status="invalid", reason="old")
            await session.commit()
    _run(old())
    legacy = client.get("/api/ai/research/legacy-study", headers=headers)
    assert legacy.status_code == 200 and legacy.json()["status"] == "done" and legacy.json()["trials"][0]["status"] == "invalid"
