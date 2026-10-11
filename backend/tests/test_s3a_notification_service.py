"""S3a (ADR-0022): the Notification Service rule side - dedupe by idempotency key, cooldown, burst grouping (one
message per rule and bar), quiet hours across midnight in the organisation's timezone (critical goes through), the
hourly cap, digests, delivery rows tagged with priority/group/bucket, the rules API and the worker hook."""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, select

from app.alerts import engine
from app.alerts.engine import Policy, in_quiet_hours
from app.db.models import (AlertChannelRecord, AlertDeliveryRecord, AlertEventRecord, AlertRuleRecord, NotificationPolicyRecord,
                           NotificationRecord)
from tests.test_auth_api import _session_factory, client  # noqa: F401 - fixtures
from tests.test_phase_l_ai import _owner

BAR = datetime(2026, 3, 10, 4, 0, tzinfo=timezone.utc)          # 09:30 IST
NOW = BAR + timedelta(minutes=1)


def _run(coro):
    return asyncio.run(coro)


def _flag(on):
    async def go():
        from app.platform import controls
        async with _session_factory() as session:
            current = await controls._get(session, controls.KEY_FEATURE_FLAGS)
            flags = dict(current.get("flags") or {})
            flags["screener_v2"] = {"on": on, "tenants": []}
            await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": flags}, None)
            await session.commit()
    _run(go())


@pytest.fixture(autouse=True)
def _clean():
    async def go():
        async with _session_factory() as session:
            for m in (AlertEventRecord, AlertRuleRecord, NotificationPolicyRecord):
                await session.execute(delete(m))
            await session.commit()
    _run(go())
    yield


def _rule(tenant_id, **kw):
    async def go():
        async with _session_factory() as session:
            row = AlertRuleRecord(tenant_id=tenant_id, name=kw.pop("name", "Breakout"), kind="instrument", symbol="X", condition_text="close > 1",
                                  base_tf="5m", priority=kw.pop("priority", "normal"), cooldown_minutes=kw.pop("cooldown", 60), mode=kw.pop("mode", "instant"),
                                  digest_every=kw.pop("digest_every", "hourly"), status="active", created_at=BAR, updated_at=BAR)
            session.add(row)
            await session.commit()
            return row.id
    return _run(go())


def _policy(tenant_id, **kw):
    async def go():
        async with _session_factory() as session:
            session.add(NotificationPolicyRecord(tenant_id=tenant_id, timezone=kw.get("tz", "Asia/Kolkata"), quiet_start=kw.get("qs"),
                                                 quiet_end=kw.get("qe"), max_per_hour=kw.get("cap", 30), group_window_seconds=kw.get("window", 10),
                                                 eod_digest_time=kw.get("eod", "15:45"), updated_at=BAR))
            await session.commit()
    _run(go())


def _fire(rule_id, symbol, bar=BAR, now=NOW, values=None):
    async def go():
        async with _session_factory() as session:
            rule = await session.get(AlertRuleRecord, rule_id)
            ev = await engine.record_event(session, rule, symbol, bar, values or {"close": 101.5, "as_of": bar.isoformat()}, now=now)
            await session.commit()
            return None if ev is None else (ev.status, ev.reason_code)
    return _run(go())


def _flush(now, tenant_id=None):
    async def go():
        async with _session_factory() as session:
            return await engine.flush(session, now, tenant_id=tenant_id)
    return _run(go())


def _events(rule_id):
    async def go():
        async with _session_factory() as session:
            return list(await session.scalars(select(AlertEventRecord).where(AlertEventRecord.rule_id == rule_id).order_by(AlertEventRecord.id)))
    return _run(go())


def _notes(tenant_id):
    async def go():
        async with _session_factory() as session:
            return list(await session.scalars(select(NotificationRecord).where(NotificationRecord.tenant_id == tenant_id,
                                                                               NotificationRecord.event_type == "SCREEN_ALERT")))
    return _run(go())


def test_dedupe_and_cooldown():
    _, me = _owner("s3a-dedupe@example.com")
    rid = _rule(me["tenant_id"], cooldown=30)
    assert _fire(rid, "aaa") == ("pending", None)
    assert _fire(rid, "AAA") is None                                                       # same rule/symbol/condition/bar: never twice
    assert _fire(rid, "AAA", bar=BAR + timedelta(minutes=5), now=NOW + timedelta(minutes=5)) == ("suppressed", "cooldown")
    assert _fire(rid, "AAA", bar=BAR + timedelta(minutes=45), now=NOW + timedelta(minutes=45)) == ("pending", None)


def test_a_burst_is_one_message_per_rule_and_bar_with_tagged_deliveries():
    _, me = _owner("s3a-burst@example.com")
    _policy(me["tenant_id"], window=10)

    async def channel():
        async with _session_factory() as session:
            session.add(AlertChannelRecord(tenant_id=me["tenant_id"], channel_type="webhook", enabled=True, min_severity="INFO", encrypted_config="x"))
            await session.commit()
    _run(channel())
    rid = _rule(me["tenant_id"], name="Volume shocker")
    for s in ("TCS", "INFY", "WIPRO"):
        _fire(rid, s)
    assert _flush(NOW + timedelta(seconds=5)) == {"sent": 0, "held": 0}                   # the burst may still be arriving
    assert _flush(NOW + timedelta(seconds=11))["sent"] == 3
    notes = _notes(me["tenant_id"])
    assert len(notes) == 1 and "3 symbol(s)" in notes[0].title and "INFY, TCS, WIPRO" in notes[0].message
    assert "not recommendations" in notes[0].message and notes[0].severity == "WARNING"
    events = _events(rid)
    assert {e.status for e in events} == {"sent"} and len({e.group_id for e in events}) == 1

    async def deliveries():
        async with _session_factory() as session:
            return list(await session.scalars(select(AlertDeliveryRecord).where(AlertDeliveryRecord.notification_id == notes[0].id)))
    d = _run(deliveries())
    assert len(d) == 1 and d[0].priority == "normal" and d[0].group_id == events[0].group_id


def test_quiet_hours_across_midnight_hold_normal_but_not_critical():
    assert in_quiet_hours(Policy(quiet_start="22:00", quiet_end="07:00"), datetime(2026, 3, 10, 17, 0, tzinfo=timezone.utc))      # 22:30 IST
    assert in_quiet_hours(Policy(quiet_start="22:00", quiet_end="07:00"), datetime(2026, 3, 10, 0, 30, tzinfo=timezone.utc))      # 06:00 IST
    assert not in_quiet_hours(Policy(quiet_start="22:00", quiet_end="07:00"), datetime(2026, 3, 10, 4, 0, tzinfo=timezone.utc))   # 09:30 IST
    assert in_quiet_hours(Policy(timezone="America/New_York", quiet_start="09:00", quiet_end="10:00"), datetime(2026, 3, 10, 13, 30, tzinfo=timezone.utc))
    _, me = _owner("s3a-quiet@example.com")
    _policy(me["tenant_id"], qs="22:00", qe="07:00", window=0)
    night = datetime(2026, 3, 10, 17, 0, tzinfo=timezone.utc)
    normal, critical = _rule(me["tenant_id"], name="normal"), _rule(me["tenant_id"], name="urgent", priority="critical")
    _fire(normal, "TCS", bar=night, now=night)
    _fire(critical, "TCS", bar=night, now=night)
    out = _flush(night + timedelta(seconds=1))
    assert out == {"sent": 1, "held": 1}
    assert [(e.status, e.reason_code) for e in _events(normal)] == [("held", "quiet_hours")]
    morning = datetime(2026, 3, 11, 2, 0, tzinfo=timezone.utc)                                # 07:30 IST
    assert _flush(morning)["sent"] == 1 and _events(normal)[0].status == "sent"


def test_the_hourly_cap_holds_the_overflow():
    _, me = _owner("s3a-cap@example.com")
    _policy(me["tenant_id"], cap=1, window=0)
    a, b = _rule(me["tenant_id"], name="a"), _rule(me["tenant_id"], name="b")
    _fire(a, "TCS")
    _fire(b, "INFY")
    out = _flush(NOW + timedelta(seconds=1))
    assert out == {"sent": 1, "held": 1}
    assert [(e.status, e.reason_code) for e in _events(b)] == [("held", "rate_cap")]
    assert _flush(NOW + timedelta(hours=1, seconds=5))["sent"] == 1


def test_digest_rules_send_once_per_bucket():
    _, me = _owner("s3a-digest@example.com")
    _policy(me["tenant_id"], window=0)
    rid = _rule(me["tenant_id"], name="Hourly", mode="digest", digest_every="hourly")
    _fire(rid, "TCS", bar=BAR, now=BAR + timedelta(minutes=1))
    _fire(rid, "INFY", bar=BAR + timedelta(minutes=15), now=BAR + timedelta(minutes=16))
    assert _flush(BAR + timedelta(minutes=20)) == {"sent": 0, "held": 0}
    assert {e.reason_code for e in _events(rid)} == {"digest"}
    assert _flush(BAR + timedelta(minutes=31))["sent"] == 2                                   # 09:30 IST bucket closes at 10:00 IST
    notes = _notes(me["tenant_id"])
    assert len(notes) == 1 and "digest" in notes[0].title and "INFY, TCS" in notes[0].message


def test_rules_api_validates_and_logs():
    headers, me = _owner("s3a-api@example.com")
    assert client.get("/api/alerts/rules", headers=headers).status_code == 503
    _flag(True)
    try:
        bad = client.post("/api/alerts/rules", json={"name": "x", "kind": "instrument", "symbol": "TCS", "condition": "close > RSI(14)"}, headers=headers)
        assert bad.status_code == 422 and "compares price with index" in str(bad.json())
        cs = client.post("/api/alerts/rules", json={"name": "x", "kind": "instrument", "symbol": "TCS", "condition": "Rank(close) < 5"}, headers=headers)
        assert cs.status_code == 422
        made = client.post("/api/alerts/rules", json={"name": "TCS above 20-SMA", "kind": "instrument", "symbol": "tcs",
                                                     "condition": "close  >  SMA(close,20)", "base_tf": "5m", "priority": "critical"}, headers=headers)
        assert made.status_code == 201, made.text
        rule = made.json()
        assert rule["condition"] == "close > SMA(close, 20)" and rule["symbol"] == "TCS"
        assert client.post("/api/alerts/rules", json={"name": "s", "kind": "screen", "screen_id": 999999}, headers=headers).status_code == 404
        _fire(rule["id"], "TCS")
        log = client.get(f"/api/alerts/rules/{rule['id']}/events", headers=headers).json()
        assert log[0]["status"] == "pending" and log[0]["values"]["close"] == 101.5
        assert client.post(f"/api/alerts/rules/{rule['id']}/pause", headers=headers).json()["status"] == "paused"
        assert _fire(rule["id"], "TCS", bar=BAR + timedelta(hours=2)) is None                 # a paused rule never fires
        pol = client.put("/api/alerts/policy", json={"timezone": "Asia/Kolkata", "quiet_start": "22:00", "quiet_end": "07:00"}, headers=headers)
        assert pol.status_code == 200 and client.get("/api/alerts/policy", headers=headers).json()["quiet_start"] == "22:00"
        assert client.put("/api/alerts/policy", json={"timezone": "Mars/Base"}, headers=headers).status_code == 422
        assert client.put("/api/alerts/policy", json={"quiet_start": "22:00"}, headers=headers).status_code == 422
        other, _ = _owner("s3a-api-other@example.com")
        assert client.get(f"/api/alerts/rules/{rule['id']}/events", headers=other).status_code == 404
    finally:
        _flag(False)
