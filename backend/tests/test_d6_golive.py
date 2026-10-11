"""Part D6 (rule IN-SEBI.golive.checklist): go-live items only a human can evidence, plus two the platform checks itself.

- the item list is rule-set data; a malformed item (unknown group, both/neither evidence and auto, unknown auto check) fails loudly;
- SUPER_ADMIN records where the evidence lives (audited); an item is `ok` while its latest evidence is valid
  (`valid_until`, else `validity_days` from the record), `warn` when missing or expired - never `todo`, so paperwork
  never blocks a PAPER go-live; the items appear in the platform readiness checklist as LIVE scope;
- `log_retention` compares the retention policy with `log_retention_days`; `ra_gate` is ok while AI listings are off,
  and with them on needs RA evidence on record.
"""
import asyncio
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.compliance import golive, rules
from app.db.models import AuditLogRecord, ComplianceEvidenceRecord
from tests.test_admin_api import _admin, _owner
from tests.test_auth_api import _session_factory, client


def _run(coro):
    return asyncio.run(coro)


def _evaluate(today=None):
    async def go():
        async with _session_factory() as session:
            return {i.id: i for i in await golive.evaluate(session, today)}
    return _run(go())


def _record(item_id, reference, valid_until=None, recorded_at=None):
    async def go():
        async with _session_factory() as session:
            row = await golive.record_evidence(session, item_id, reference, valid_until, None)
            if recorded_at is not None:
                row.recorded_at = recorded_at
            await session.commit()
    _run(go())


@pytest.mark.parametrize("bad", [
    {"id": "x.one", "group": "nope", "title": "t", "evidence": True},
    {"id": "x.two", "group": "vendor", "title": "t"},
    {"id": "x.three", "group": "vendor", "title": "t", "evidence": True, "auto": "log_retention"},
    {"id": "x.four", "group": "dpdp", "title": "t", "auto": "made_up"},
])
def test_malformed_items_fail_loudly(monkeypatch, bad):
    monkeypatch.setitem(rules.load().rule(golive.RULE).params, "items", [bad])
    with pytest.raises(ValueError):
        golive.items()


def test_super_admin_records_evidence_and_the_platform_checklist_shows_it():
    owner, _ = _owner("d6-owner@example.com")
    assert client.get("/api/compliance/golive", headers=owner).status_code == 403
    assert client.put("/api/compliance/golive/evidence", headers=owner,
                      json={"item_id": "strategy.classification", "reference": "x"}).status_code == 403
    admin, me = _admin("d6-admin@example.com")
    before = {i["id"]: i for i in client.get("/api/compliance/golive", headers=admin).json()["items"]}
    assert set(before) == {i["id"] for i in golive.items()}
    assert before["strategy.classification"]["status"] == "warn" and "no evidence" in before["strategy.classification"]["detail"]
    assert client.put("/api/compliance/golive/evidence", headers=admin, json={"item_id": "made.up", "reference": "x"}).status_code == 422
    assert client.put("/api/compliance/golive/evidence", headers=admin,
                      json={"item_id": "dpdp.log_retention", "reference": "x"}).status_code == 422     # auto-checked item
    ok = client.put("/api/compliance/golive/evidence", headers=admin,
                    json={"item_id": "strategy.classification", "reference": "Broker filing #42 (white-box: all)"})
    assert ok.status_code == 200
    after = {i["id"]: i for i in client.get("/api/compliance/golive", headers=admin).json()["items"]}
    assert after["strategy.classification"]["status"] == "ok" and "#42" in after["strategy.classification"]["detail"]

    async def audited():
        async with _session_factory() as session:
            return list(await session.scalars(select(AuditLogRecord).where(AuditLogRecord.event == "golive_evidence")))
    assert any("strategy.classification" in r.detail for r in _run(audited()))

    readiness = {i["key"]: i for i in client.get("/api/admin/readiness", headers=admin).json()["items"]}
    golive_items = {k: v for k, v in readiness.items() if k.startswith("golive.")}
    assert len(golive_items) == len(golive.items())
    assert all(v["scope"] == "LIVE" and v["status"] in ("ok", "warn") for v in golive_items.values())
    assert readiness["golive.strategy.classification"]["status"] == "ok"


def test_evidence_expires_by_validity_days_or_its_own_date():
    item = next(i for i in golive.items() if i["id"] == "vendor.cert_in_vapt")
    days = item["validity_days"]
    recorded = datetime.now(timezone.utc) - timedelta(days=2)
    _record("vendor.cert_in_vapt", "VAPT report R-1", recorded_at=recorded)
    assert _evaluate()["vendor.cert_in_vapt"].status == "ok"
    late = recorded.date() + timedelta(days=days + 1)
    stale = _evaluate(late)["vendor.cert_in_vapt"]
    assert stale.status == "warn" and "expired" in stale.detail
    _record("vendor.iso27001", "ISO cert C-9", valid_until=date.today() - timedelta(days=1))
    assert _evaluate()["vendor.iso27001"].status == "warn"
    _record("vendor.iso27001", "ISO cert C-10", valid_until=date.today() + timedelta(days=30))   # the latest record counts
    assert _evaluate()["vendor.iso27001"].status == "ok"


def test_log_retention_is_checked_against_the_rule(monkeypatch):
    from app.retention import policy
    keep = rules.load().param(golive.RULE, "log_retention_days")
    real = policy.load_policy()
    assert _evaluate()["dpdp.log_retention"].status == ("ok" if real.login_events_days >= keep else "warn")
    monkeypatch.setenv("RETENTION_LOGIN_EVENTS_DAYS", str(keep - 1))
    assert _evaluate()["dpdp.log_retention"].status == "warn"
    monkeypatch.setenv("RETENTION_LOGIN_EVENTS_DAYS", str(keep))
    assert _evaluate()["dpdp.log_retention"].status == "ok"


def test_ra_gate_needs_registration_only_when_ai_listings_are_on(monkeypatch):
    from app.platform import controls
    state = {"on": False}

    async def fake_flag(session, name, tenant_id=None):
        assert name == "marketplace_ai_listings"
        return state["on"]
    monkeypatch.setattr(controls, "flag_enabled", fake_flag)

    async def clear():
        async with _session_factory() as session:
            for row in await session.scalars(select(ComplianceEvidenceRecord).where(ComplianceEvidenceRecord.item_id == "ai.ra_gate")):
                await session.delete(row)
            await session.commit()
    _run(clear())
    assert _evaluate()["ai.ra_gate"].status == "ok"
    state["on"] = True
    assert _evaluate()["ai.ra_gate"].status == "warn"
    _record("ai.ra_gate", "SEBI RA registration INH000000000 (placeholder for the test)")
    gate = _evaluate()["ai.ra_gate"]
    assert gate.status == "ok" and "RA registration" in gate.detail
