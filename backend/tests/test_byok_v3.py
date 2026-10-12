"""BYOK-V3 (docs/BYOK.md): the Vision audit of scan alerts and the daily after-close review, on each user's own key -
flag `vision_byok_v3` on top of `vision_byok`, both off by default.

Every key here is an obviously fake string and every provider is a mocked transport: no test reaches a real provider
or the network. Covered: both flags gate everything; a scan alert is audited only for the rule's creator, only with
their key (the engine's own send path queues it); the hourly limit; the daily review queues each user's own symbols once
per trading day after the review time; reports are the caller's own (kind / notification filters, delete); Vision never
changes an order, a trade, a deployment or an alert rule (ADR-0006); the migration declares why it is rollback-safe.
"""
import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select, text

from app.ai import byok, vision_audit
from app.alerts import engine as alert_engine
from app.core import config
from app.db.models import AlertEventRecord, AlertRuleRecord, NotificationRecord, StrategyDeploymentRecord, User, VisionReportRecord
from app.platform import controls
from app.secrets_store.envelope import ensure_tenant_key
from tests.test_auth_api import _session_factory, client
from tests.test_byok_v2 import A_KEY, B_KEY, Vision, _headers, _loader, _member, _user

IST = ZoneInfo("Asia/Kolkata")
REVIEW_DAY = datetime(2026, 6, 10, 16, 5, tzinfo=IST)        # a Wednesday, after the 16:00 IST review time


def _run(coro):
    return asyncio.run(coro)


def _flags(byok_on: bool, v3_on: bool) -> None:
    async def go():
        async with _session_factory() as session:
            current = await controls._get(session, controls.KEY_FEATURE_FLAGS)
            flags = dict(current.get("flags") or {})
            flags[byok.FLAG] = {"on": byok_on, "tenants": []}
            flags[vision_audit.FLAG_V3] = {"on": v3_on, "tenants": []}
            await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": flags}, None)
            await session.commit()
    _run(go())


@pytest.fixture
def flags_on():
    _flags(True, True)
    yield
    _flags(False, False)


async def _setup(user_id: int, *, key=None, scan=True, daily=False, enabled=True):
    async with _session_factory() as session:
        user = await session.get(User, user_id)
        await ensure_tenant_key(session, user.tenant_id)
        if key:
            await byok.save_key(session, user, provider="anthropic", api_key=key)
        await byok.save_prefs(session, user, enabled=enabled, provider="anthropic" if key else None,
                              audit={"proposals": False, "scan_alerts": scan, "daily_review": daily}, channels={"telegram": False, "in_app": True})


async def _rule(tenant_id: int, creator_id: int, *, kind="screen", symbol=None, name="Breakouts") -> int:
    async with _session_factory() as session:
        rule = AlertRuleRecord(tenant_id=tenant_id, created_by=creator_id, name=name, kind=kind, symbol=symbol, base_tf="15m",
                               exchange="NSE", status="active")
        session.add(rule)
        await session.commit()
        return rule.id


async def _alert(rule_id: int, symbols, now=None) -> int:
    """A screen alert through the engine's own send path (`_send`): the notification, then the Vision queue."""
    now = now or datetime.now(timezone.utc)
    async with _session_factory() as session:
        rule = await session.get(AlertRuleRecord, rule_id)
        events = []
        for s in symbols:
            e = AlertEventRecord(tenant_id=rule.tenant_id, rule_id=rule.id, symbol=s, condition_hash="h" * 64, bar_time=now - timedelta(minutes=15),
                                 idem_key=f"{rule.id}:{s}:{now.timestamp()}", base_tf=rule.base_tf, priority="normal", values_json="{}",
                                 created_at=now, status="pending")
            session.add(e)
            events.append(e)
        await session.flush()
        note = await alert_engine._send(session, rule, events, now, f"g-{rule.id}-{now.timestamp()}", None)
        await session.commit()
        return note.id


async def _drain(now=None):
    async with _session_factory() as session:
        return await vision_audit.drain(session, loader=_loader, now=now, limit=50)


async def _rows(*where):
    async with _session_factory() as session:
        return list(await session.scalars(select(VisionReportRecord).where(*where).order_by(VisionReportRecord.id)))


def test_v3_flag_is_registered_off_and_both_flags_gate_everything(monkeypatch):
    assert vision_audit.FLAG_V3 in controls.FEATURE_FLAGS and vision_audit.FLAG_V3 in controls.DEFAULT_OFF_FLAGS
    assert "never an order" in controls.FEATURE_FLAGS[vision_audit.FLAG_V3]
    vision = Vision().install(monkeypatch)
    headers, me = _user("v3-flags@example.com")
    _run(_setup(me["id"], key=A_KEY, daily=True))
    rule = _run(_rule(me["tenant_id"], me["id"]))
    for byok_on, v3_on in ((False, False), (True, False), (False, True)):
        _flags(byok_on, v3_on)
        note = _run(_alert(rule, ["V3FLAGA"]))
        assert _run(_rows(VisionReportRecord.notification_id == note)) == []
        async def daily():
            async with _session_factory() as session:
                return await vision_audit.enqueue_daily_reviews(session, REVIEW_DAY)
        assert _run(daily()) == 0
    _run(_drain())
    assert vision.seen == []
    _flags(True, False)
    body = client.get("/api/ai/byok/reports?kind=scan_alert", headers=headers).json()
    assert body["reports"] == [] and body["vision_line"] == vision_audit.V3_NOT_ENABLED
    assert client.get("/api/ai/byok/status", headers=headers).json()["scope"]["v3_enabled"] is False
    _flags(False, False)


def test_a_scan_alert_is_audited_for_the_rule_creator_with_their_own_key_only(flags_on, monkeypatch):
    vision = Vision(answer=lambda key: json.dumps({k: {"verdict": "ok", "note": "tidy"} for k in
                                                   ("zones", "structure", "pullback_trigger", "entry_sl_target")})).install(monkeypatch)
    headers, me = _user("v3-scan@example.com")
    _run(_setup(me["id"], key=A_KEY))
    mate = _run(_member(me["tenant_id"], "v3-scan-mate@example.com"))
    _run(_setup(mate, key=B_KEY))                                 # a teammate with a key of their own: never used here
    rule = _run(_rule(me["tenant_id"], me["id"]))
    note = _run(_alert(rule, ["V3SCANB", "V3SCANA"]))
    rows = _run(_rows(VisionReportRecord.notification_id == note))
    assert [(r.user_id, r.subject_kind, r.symbol, r.timeframe, r.status) for r in rows] == [(me["id"], "scan_alert", "V3SCANA", "15m", "pending")]
    assert rows[0].subject_key == f"scan:{note}" and rows[0].ai_action_id is None
    assert _run(_drain()) == 1
    assert [(p, k, img) for p, k, img in vision.seen] == [("anthropic", A_KEY, True)]   # the creator's key, with the chart
    row = _run(_rows(VisionReportRecord.notification_id == note))[0]
    assert row.status == "ok" and row.report_text.startswith("Vision report - V3SCANA 15m")
    body = client.get(f"/api/ai/byok/reports?notification_id={note}", headers=headers).json()
    assert [r["kind"] for r in body["reports"]] == ["scan_alert"] and body["reports"][0]["symbol"] == "V3SCANA"
    assert body["reports"][0]["notification_id"] == note
    other_headers, _ = _user("v3-scan-outsider@example.com")
    assert client.get(f"/api/ai/byok/reports?notification_id={note}", headers=other_headers).json()["reports"] == []
    assert client.delete(f"/api/ai/byok/reports/{row.id}", headers=other_headers).status_code == 404
    assert client.delete(f"/api/ai/byok/reports/{row.id}", headers=headers).status_code == 200

    # the creator without a key: the row says so; the teammate's key is still never used
    rule2 = _run(_rule(me["tenant_id"], mate, name="Mate's rule"))
    async def drop_key():
        async with _session_factory() as session:
            await byok.delete_key(session, await session.get(User, mate), "anthropic")
    _run(drop_key())
    note2 = _run(_alert(rule2, ["V3SCANC"]))
    _run(_drain())
    row2 = _run(_rows(VisionReportRecord.notification_id == note2))[0]
    assert row2.user_id == mate and row2.status == "skipped" and row2.reason == "no key"
    assert all(k == A_KEY for _, k, _ in vision.seen)


def test_the_hourly_scan_limit_writes_skipped_rows_without_a_call(flags_on, monkeypatch):
    monkeypatch.setattr(config, "BYOK_SCAN_AUDITS_PER_HOUR", 2)
    vision = Vision().install(monkeypatch)
    _, me = _user("v3-limit@example.com")
    _run(_setup(me["id"], key=A_KEY))
    rule = _run(_rule(me["tenant_id"], me["id"]))
    notes = [_run(_alert(rule, [f"V3LIM{i}"])) for i in range(3)]
    rows = _run(_rows(VisionReportRecord.user_id == me["id"]))
    assert [r.status for r in rows] == ["pending", "pending", "skipped"]
    assert rows[2].reason == "scan audit limit (2 per hour)" and rows[2].notification_id == notes[2]
    _run(_drain())
    assert len(vision.seen) == 2
    # review LOW-1: one "limit reached" row per user per rolling hour - further matches that hour write nothing
    more = [_run(_alert(rule, [f"V3LIMX{i}"])) for i in range(3)]
    assert more[0] != notes[0]                                   # a new notification is a new subject
    rows = _run(_rows(VisionReportRecord.user_id == me["id"]))
    assert len(rows) == 3 and [r.status for r in rows].count("skipped") == 1
    assert _run(_rows(VisionReportRecord.notification_id.in_(more))) == []
    later = datetime.now(timezone.utc) + timedelta(hours=1, minutes=1)   # an hour on, the limit counts afresh
    late_note = _run(_alert(rule, ["V3LIMLATE"], now=later))
    assert [r.status for r in _run(_rows(VisionReportRecord.notification_id == late_note))] == ["pending"]
    _run(_drain(now=later))


def test_skipped_vision_rows_age_out_after_90_days_and_audited_ones_stay():
    """Review LOW-1: retention removes SKIPPED Vision rows (no call was made) after RETENTION_VISION_SKIPPED_DAYS (90);
    reports that were audited are the user's own and are not touched; vision_reports is not a never-deleted table."""
    from app.retention import policy as retention_policy
    from app.retention import service as retention_service
    assert "vision_reports" not in retention_policy.NEVER_DELETED
    pol = retention_policy.load_policy()
    assert pol.vision_skipped_days == 90 and pol.as_dict()["vision_skipped_days"] == 90
    _, me = _user("v3-retention@example.com")
    old = datetime.now(timezone.utc) - timedelta(days=91)

    async def seed():
        async with _session_factory() as session:
            for status, created, key in (("skipped", old, "r-old-skip"), ("ok", old, "r-old-ok"), ("failed", old, "r-old-fail"),
                                         ("skipped", datetime.now(timezone.utc), "r-new-skip")):
                session.add(VisionReportRecord(tenant_id=me["tenant_id"], user_id=me["id"], subject_kind="scan_alert", subject_key=key,
                                               status=status, created_at=created))
            await session.commit()
            report = await retention_service.run_retention(session, policy=pol)
            await session.commit()
            return report
    report = _run(seed())
    assert report.deleted.get("vision_reports_skipped", 0) >= 1
    left = {r.subject_key for r in _run(_rows(VisionReportRecord.user_id == me["id"]))}
    assert left == {"r-old-ok", "r-old-fail", "r-new-skip"}


# --- review LOW-3: the creator is the only user a scan alert is ever audited for, checked again at run time -------------

async def _deactivate(user_id):
    async with _session_factory() as session:
        (await session.get(User, user_id)).is_active = False
        await session.commit()


async def _move(user_id, tenant_id):
    async with _session_factory() as session:
        (await session.get(User, user_id)).tenant_id = tenant_id
        await session.commit()


async def _set_creator(rule_id, creator_id):
    async with _session_factory() as session:
        (await session.get(AlertRuleRecord, rule_id)).created_by = creator_id
        await session.commit()


@pytest.mark.parametrize("case", ["deactivated_creator", "ownerless_rule", "creator_moved_org", "teammates_rule",
                                  "creator_changed_after_queue", "creator_deactivated_after_queue", "creator_moved_after_queue"])
def test_a_scan_alert_is_never_audited_with_the_wrong_users_key(flags_on, monkeypatch, case):
    Vision().install(monkeypatch)
    _run(_drain())                                                 # what earlier tests left pending is not this case's
    vision = Vision().install(monkeypatch)
    _, me = _user(f"v3-iso-{case}@example.com")
    _run(_setup(me["id"], key=A_KEY))                              # my key and my scope: on
    mate = _run(_member(me["tenant_id"], f"v3-iso-mate-{case}@example.com"))
    _, elsewhere = _user(f"v3-iso-other-{case}@example.com")
    creator = None if case == "ownerless_rule" else (mate if case == "teammates_rule" else me["id"])
    rule = _run(_rule(me["tenant_id"], creator if creator is not None else me["id"]))
    if case == "ownerless_rule":
        _run(_set_creator(rule, None))
    if case == "deactivated_creator":
        _run(_deactivate(me["id"]))
    if case == "creator_moved_org":
        _run(_move(me["id"], elsewhere["tenant_id"]))
    note = _run(_alert(rule, ["V3ISO"]))
    queued = _run(_rows(VisionReportRecord.notification_id == note))
    if case.endswith("_after_queue"):
        assert [r.user_id for r in queued] == [me["id"]]           # queued for me, the creator at the time
        if case == "creator_changed_after_queue":
            _run(_set_creator(rule, mate))
        elif case == "creator_deactivated_after_queue":
            _run(_deactivate(me["id"]))
        else:
            _run(_move(me["id"], elsewhere["tenant_id"]))
        _run(_drain())
        row = _run(_rows(VisionReportRecord.notification_id == note))[0]
        assert row.status == "skipped" and row.reason in ("alert rule not available", "subject not available"), row.reason
    else:
        assert [r for r in queued if r.user_id == me["id"]] == []  # no row for me
        assert all(r.user_id == creator for r in queued)           # at most the creator's own (a teammate without prefs: none)
        _run(_drain())
    assert vision.seen == []                                       # my key is never used for someone else's subject


def test_the_daily_review_queues_the_users_own_symbols_once_per_trading_day(flags_on, monkeypatch):
    monkeypatch.setattr(config, "BYOK_DAILY_REVIEW_MAX_SYMBOLS", 2)
    vision = Vision().install(monkeypatch)
    headers, me = _user("v3-daily@example.com")
    _run(_setup(me["id"], key=A_KEY, scan=False, daily=True))
    mate = _run(_member(me["tenant_id"], "v3-daily-mate@example.com"))

    async def seed():
        async with _session_factory() as session:
            for sym, creator in (("V3DAYA", me["id"]), ("V3DAYB", me["id"]), ("V3DAYMATE", mate)):
                session.add(StrategyDeploymentRecord(tenant_id=me["tenant_id"], strategy_id="ema_crossover", symbol=sym, exchange="NSE",
                                                     timeframe="5min", mode="PAPER", status="ACTIVE", created_by=creator))
            await session.commit()
    _run(seed())
    _run(_rule(me["tenant_id"], me["id"], kind="instrument", symbol="V3DAYRULE"))

    async def enqueue(now):
        async with _session_factory() as session:
            return await vision_audit.enqueue_daily_reviews(session, now)
    assert _run(enqueue(REVIEW_DAY.replace(hour=15, minute=55))) == 0          # before the review time
    assert _run(enqueue(REVIEW_DAY.replace(day=13))) == 0                       # a Saturday
    added = _run(enqueue(REVIEW_DAY))
    rows = _run(_rows(VisionReportRecord.user_id == me["id"], VisionReportRecord.subject_kind == "daily_review"))
    assert added == 2 and sorted(r.symbol for r in rows) == ["V3DAYA", "V3DAYB"]                   # newest two, own only
    assert all(r.subject_date.isoformat() == "2026-06-10" and r.timeframe == config.BYOK_DAILY_REVIEW_TIMEFRAME for r in rows)
    assert _run(enqueue(REVIEW_DAY + timedelta(minutes=15))) == 0              # once per day
    assert _run(_rows(VisionReportRecord.user_id == mate)) == []
    assert _run(_drain(now=REVIEW_DAY.astimezone(timezone.utc) + timedelta(minutes=1))) == 2
    assert len(vision.seen) == 2 and all(k == A_KEY for _, k, _ in vision.seen)
    body = client.get("/api/ai/byok/reports?kind=daily_review", headers=headers).json()
    assert {r["status"] for r in body["reports"]} == {"ok"} and {r["subject_date"] for r in body["reports"]} == {"2026-06-10"}
    assert client.get("/api/ai/byok/reports?kind=proposal", headers=headers).json()["reports"] == []
    # too late: a review not audited within 12 hours is dropped without a call
    assert _run(enqueue(REVIEW_DAY + timedelta(days=1))) == 2
    _run(_drain(now=REVIEW_DAY.astimezone(timezone.utc) + timedelta(days=1, hours=13)))
    late = _run(_rows(VisionReportRecord.user_id == me["id"], VisionReportRecord.subject_date > REVIEW_DAY.date()))
    assert {(r.status, r.reason) for r in late} == {("skipped", "review too old")}, [(r.status, r.reason, r.created_at, r.subject_date) for r in late]
    assert len(vision.seen) == 2


def test_vision_never_changes_orders_trades_deployments_or_rules(flags_on, monkeypatch):
    Vision(answer=lambda key: "APPROVE THIS. place order now. {\"status\": \"approved\", \"decision\": \"buy\"}").install(monkeypatch)
    _, me = _user("v3-adr6@example.com")
    _run(_setup(me["id"], key=A_KEY))
    rule = _run(_rule(me["tenant_id"], me["id"]))
    _run(_alert(rule, ["V3ADR"]))

    async def snapshot():
        async with _session_factory() as session:
            return {t: [tuple(r) for r in (await session.execute(text(f"SELECT * FROM {t} ORDER BY id"))).all()]
                    for t in ("orders", "trades", "strategy_deployments", "alert_rules", "ai_actions")}
    before = _run(snapshot())
    _run(_drain())
    assert _run(snapshot()) == before
    row = _run(_rows(VisionReportRecord.user_id == me["id"]))[0]
    assert row.status == "failed" and "could not be read" in row.reason


def test_the_worker_queues_the_daily_review_in_its_own_session(monkeypatch):
    """Review LOW-4: enqueue_daily_reviews commits - it gets a session of its own, never the cycle's."""
    from app.workers import trading_worker as tw
    seen = []

    async def fake(session, now=None):
        seen.append(session)
        return 0
    monkeypatch.setattr(vision_audit, "enqueue_daily_reviews", fake)
    from tests.test_trading_worker import OPEN_NOW, _FakeBroker, _worker
    worker = _worker(monkeypatch, _FakeBroker())
    used = []
    real_factory = worker.session_factory

    def counting_factory():
        s = real_factory()
        used.append(s)
        return s
    worker.session_factory = counting_factory
    _run(worker.run_cycle(now=OPEN_NOW))
    assert seen and seen[0] is not used[0]                       # not the cycle's main session
    assert any(seen[0] is s for s in used[1:])
    assert tw is not None


def test_scan_notification_and_migration_details():
    note_cols = {c.name for c in NotificationRecord.__table__.columns}
    assert "user_id" in note_cols
    migration = (Path(__file__).resolve().parents[1] / "alembic" / "versions" / "c9e1f3a5b7d2_byok_v3_vision_subjects.py").read_text()
    assert "ROLLBACK_SAFE = True" in migration and "ROLLBACK_SAFE_REASON" in migration
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    script = ScriptDirectory.from_config(Config(str(Path(__file__).resolve().parents[1] / "alembic.ini")))
    assert len(script.get_heads()) == 1                                 # re-chained at merge; later migrations may sit on top
    assert "c9e1f3a5b7d2" in {rev.revision for rev in script.walk_revisions()}
    assert _headers("x") == {"Authorization": "Bearer x"}
