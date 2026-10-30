"""M55 S1b (owner decision with #744): the owner grants LIVE per organisation; nothing is granted by default.

The grant is the `live_trading` flag's organisation allow-list. Only the owner identity (LIVE_OWNER_USER_ID, a recent
two-factor check, the typed confirmation) grants or revokes it, every attempt is audited, and the plan check passes
for an organisation only when the global switch is on, the PAPER-only lock is off and the organisation is granted."""
import asyncio

import pytest

from app.core import config
from app.db.models import Tenant, User
from app.plans import limits
from app.platform import controls, deployment_phase, live_owner
from tests.test_admin_api import _admin
from tests.test_auth_api import _session_factory, client
from tests.test_m55_s1_owner_live import _audit, _resolved, _set_stored

LIVE_ORGS = "/api/admin/controls/live-orgs"
FLAGS = "/api/admin/controls/flags"
CONFIRM = {"confirm": live_owner.CONFIRM_TEXT}


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _defaults(monkeypatch):
    monkeypatch.delenv(deployment_phase.ENV_VAR, raising=False)
    _set_stored(None)
    yield
    _set_stored(None)


def _allowed(tenant_id: int) -> bool:
    async def go():
        async with _session_factory() as session:
            return await limits.live_allowed_for(session, await session.get(Tenant, tenant_id))
    return _run(go())


def _deployment_refused(tenant_id: int) -> bool:
    async def go():
        async with _session_factory() as session:
            try:
                await limits.check_can_add_deployment(session, await session.get(Tenant, tenant_id), live=True, adding=False)
            except limits.PlanLimitExceeded:
                return True
            return False
    return _run(go())


def _owner_admin(monkeypatch, email: str):
    headers, me = _admin(email)
    monkeypatch.setattr(config, "LIVE_OWNER_USER_ID", me["id"])
    return headers, me


def _switch_on_lock_off(granted: list[int] | None = None) -> None:
    _set_stored({"live_trading": {"on": True, "tenants": granted or []}, "phase0_paper_only": {"on": False, "tenants": []}})


def test_the_defaults_grant_no_organisation(monkeypatch):
    for value in (None, deployment_phase.PHASE0):
        if value is None:
            monkeypatch.delenv(deployment_phase.ENV_VAR, raising=False)
        else:
            monkeypatch.setenv(deployment_phase.ENV_VAR, value)
        assert _resolved()["live_trading"]["tenants"] == []

    async def go():
        async with _session_factory() as session:
            return await live_owner.granted_orgs(session)
    assert _run(go()) == []


def test_without_the_grant_live_is_refused_even_with_the_switch_on_and_the_lock_off(monkeypatch):
    _, me = _admin("m55b-nogrant@example.com")
    _switch_on_lock_off()
    assert _allowed(me["tenant_id"]) is False
    assert _deployment_refused(me["tenant_id"]) is True


def test_with_the_grant_the_plan_check_passes_and_needs_the_switch_and_the_lock(monkeypatch):
    _, me = _admin("m55b-grant@example.com")
    tid = me["tenant_id"]
    _switch_on_lock_off([tid])
    assert _allowed(tid) is True and _deployment_refused(tid) is False
    # Granted, but the switch off or the lock on: refused exactly as before.
    _set_stored({"live_trading": {"on": False, "tenants": [tid]}, "phase0_paper_only": {"on": False, "tenants": []}})
    assert _allowed(tid) is False and _deployment_refused(tid) is True
    _set_stored({"live_trading": {"on": True, "tenants": [tid]}, "phase0_paper_only": {"on": True, "tenants": []}})
    assert _allowed(tid) is False
    _set_stored({"live_trading": {"on": True, "tenants": [tid]}, "phase0_paper_only": {"on": False, "tenants": [tid]}})
    assert _allowed(tid) is False


def test_the_owner_grants_and_revokes_and_both_are_audited(monkeypatch):
    headers, me = _owner_admin(monkeypatch, "m55b-owner@example.com")
    tid = me["tenant_id"]
    granted_before, revoked_before = len(_audit(live_owner.GRANTED_EVENT)), len(_audit(live_owner.REVOKED_EVENT))
    res = client.put(f"{LIVE_ORGS}/{tid}", headers=headers, json={"granted": True, **CONFIRM})
    assert res.status_code == 200, res.text
    body = res.json()["live_trading"]
    assert body["tenants"] == [tid] and body["live_orgs"][0]["id"] == tid and body["on"] is False   # the switch unchanged
    assert len(_audit(live_owner.GRANTED_EVENT)) == granted_before + 1
    # Switching LIVE on or off keeps the granted list; the switch cannot set the list.
    assert client.put(f"{FLAGS}/live_trading", headers=headers, json={"on": True, **CONFIRM}).status_code == 200
    flag = _resolved()["live_trading"]
    assert flag["on"] is True and flag["tenants"] == [tid]
    assert client.put(f"{FLAGS}/live_trading", headers=headers, json={"on": False}).status_code == 200
    assert _resolved()["live_trading"]["tenants"] == [tid]
    res = client.put(f"{FLAGS}/live_trading", headers=headers, json={"on": False, "tenants": [tid, 999], **CONFIRM})
    assert res.status_code == 400 and res.json()["detail"] == live_owner.GRANT_VIA_LIST
    # Revoke.
    res = client.put(f"{LIVE_ORGS}/{tid}", headers=headers, json={"granted": False, **CONFIRM})
    assert res.status_code == 200, res.text
    assert res.json()["live_trading"]["tenants"] == [] and res.json()["live_trading"]["live_orgs"] == []
    assert len(_audit(live_owner.REVOKED_EVENT)) == revoked_before + 1
    _switch_on_lock_off(_resolved()["live_trading"]["tenants"])
    assert _allowed(tid) is False


def test_a_non_owner_cannot_grant_or_revoke_403_and_audited(monkeypatch):
    headers, me = _admin("m55b-not-owner@example.com")
    monkeypatch.setattr(config, "LIVE_OWNER_USER_ID", me["id"] + 100000)
    before = len(_audit(live_owner.REFUSED_EVENT))
    for granted in (True, False):
        res = client.put(f"{LIVE_ORGS}/{me['tenant_id']}", headers=headers, json={"granted": granted, **CONFIRM})
        assert res.status_code == 403 and res.json()["detail"] == live_owner.NOT_OWNER_GRANT
    refused = _audit(live_owner.REFUSED_EVENT)[before:]
    assert len(refused) == 2 and all("not the owner" in r for r in refused)
    assert _resolved()["live_trading"]["tenants"] == []
    assert client.get(FLAGS, headers=headers).json()["live_trading"]["can_switch_live"] is False


def test_the_owner_needs_the_confirmation_two_factor_and_a_real_organisation(monkeypatch):
    headers, me = _owner_admin(monkeypatch, "m55b-owner-checks@example.com")
    res = client.put(f"{LIVE_ORGS}/{me['tenant_id']}", headers=headers, json={"granted": True, "confirm": "yes"})
    assert res.status_code == 400 and res.json()["detail"] == live_owner.NO_CONFIRM_GRANT
    res = client.put(f"{LIVE_ORGS}/987654", headers=headers, json={"granted": True, **CONFIRM})
    assert res.status_code == 400
    assert _resolved()["live_trading"]["tenants"] == []

    async def no_mfa():
        async with _session_factory() as session:
            user = await session.get(User, me["id"])
            await live_owner.set_org_grant(session, user, me["tenant_id"], granted=True, session_id=None,
                                           confirm=live_owner.CONFIRM_TEXT)
    from fastapi import HTTPException
    with pytest.raises(HTTPException):
        _run(no_mfa())
    assert _resolved()["live_trading"]["tenants"] == []
    assert controls.FEATURE_FLAGS["live_trading"]                       # the flag itself is unchanged
