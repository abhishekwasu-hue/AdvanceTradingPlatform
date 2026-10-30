"""M55 S1 (owner order): LIVE can be switched on only by the owner.

live_trading is OFF and the phase0_paper_only lock is ON by default in every deployment mode; turning live_trading on
or the lock off needs the owner identity (LIVE_OWNER_USER_ID), a recent two-factor check on the session and the typed
confirmation; every attempt is audited; the USER role has no trading:live and no plan grants LIVE."""
import asyncio
import json

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.auth.scopes import ROLE_SCOPES
from app.core import config
from app.core.enums import UserRole
from app.db.models import AuditLogRecord, PlatformControlRecord, User
from app.plans.registry import PLANS
from app.platform import controls, deployment_phase, live_owner
from tests.test_admin_api import _admin
from tests.test_auth_api import _session_factory, client

FLAGS = "/api/admin/controls/flags"


def _run(coro):
    return asyncio.run(coro)


def _audit(event: str) -> list[str]:
    async def go():
        async with _session_factory() as session:
            return list(await session.scalars(select(AuditLogRecord.detail).where(AuditLogRecord.event == event)
                                              .order_by(AuditLogRecord.id)))
    return _run(go())


def _set_stored(entries: dict | None) -> None:
    """Replace the stored entries of the two LIVE switches (None: drop them, so the defaults apply)."""
    async def go():
        async with _session_factory() as session:
            row = await session.get(PlatformControlRecord, controls.KEY_FEATURE_FLAGS)
            if row is None:
                row = PlatformControlRecord(key=controls.KEY_FEATURE_FLAGS, value_json="{}")
                session.add(row)
            value = json.loads(row.value_json or "{}")
            flags = dict(value.get("flags") or {})
            for name in live_owner.OWNER_ONLY_FLAGS:
                flags.pop(name, None)
            flags.update(entries or {})
            row.value_json = json.dumps({**value, "flags": flags})
            await session.commit()
    _run(go())


def _resolved() -> dict:
    async def go():
        async with _session_factory() as session:
            return await controls.feature_flags(session)
    return _run(go())


@pytest.fixture(autouse=True)
def _defaults(monkeypatch):
    monkeypatch.delenv(deployment_phase.ENV_VAR, raising=False)        # not the Phase 0 mode: the defaults alone
    _set_stored(None)
    yield
    _set_stored(None)


# --- defaults ---------------------------------------------------------------------------------------------------------

def test_live_is_off_and_the_paper_lock_is_on_by_default_in_every_mode(monkeypatch):
    for value in (None, "", "standard", deployment_phase.PHASE0):
        if value is None:
            monkeypatch.delenv(deployment_phase.ENV_VAR, raising=False)
        else:
            monkeypatch.setenv(deployment_phase.ENV_VAR, value)
        flags = _resolved()
        assert flags["live_trading"]["on"] is False and flags["live_trading"]["tenants"] == []
        assert flags["phase0_paper_only"]["on"] is True
    assert "live_trading" not in controls.DEFAULT_ON_FLAGS and "phase0_paper_only" in controls.DEFAULT_ON_FLAGS


def test_owner_setting_defaults_to_user_one(monkeypatch):
    from pathlib import Path
    assert 'LIVE_OWNER_USER_ID = _env_int_in("LIVE_OWNER_USER_ID", 1, 1, 2**31 - 1)' in Path(config.__file__).read_text()
    monkeypatch.delenv("LIVE_OWNER_USER_ID", raising=False)
    assert config._env_int_in("LIVE_OWNER_USER_ID", 1, 1, 2**31 - 1) == 1
    for bad in ("0", "-3", "abc"):                                       # not a positive id: the default, never "anyone"
        monkeypatch.setenv("LIVE_OWNER_USER_ID", bad)
        assert config._env_int_in("LIVE_OWNER_USER_ID", 1, 1, 2**31 - 1) == 1


def test_user_role_has_no_trading_live():
    assert "trading:live" not in ROLE_SCOPES[UserRole.USER.value]


def test_no_plan_grants_live():
    assert PLANS and all(plan.live_trading is False for plan in PLANS.values())


# --- who may switch LIVE on -------------------------------------------------------------------------------------------

def test_a_super_admin_who_is_not_the_owner_gets_403_and_is_audited(monkeypatch):
    headers, me = _admin("m55-not-owner@example.com")
    monkeypatch.setattr(config, "LIVE_OWNER_USER_ID", me["id"] + 100000)
    before = len(_audit(live_owner.REFUSED_EVENT))
    for name, body in (("live_trading", {"on": True}), ("live_trading", {"on": False, "tenants": [5]}),
                       ("phase0_paper_only", {"on": False}), ("phase0_paper_only", {"on": False, "tenants": [5]})):
        res = client.put(f"{FLAGS}/{name}", headers=headers, json={**body, "confirm": live_owner.CONFIRM_TEXT})
        assert res.status_code == 403, res.text
        assert res.json()["detail"] == live_owner.NOT_OWNER
    refused = _audit(live_owner.REFUSED_EVENT)[before:]
    assert len(refused) == 4 and all("not the owner" in r for r in refused)
    flags = _resolved()
    assert flags["live_trading"]["on"] is False and flags["live_trading"]["tenants"] == []
    assert flags["phase0_paper_only"]["on"] is True
    # The restrictive direction is never held up: LIVE off and the lock on stay allowed for any admin.
    assert client.put(f"{FLAGS}/live_trading", headers=headers, json={"on": False}).status_code == 200
    assert client.put(f"{FLAGS}/phase0_paper_only", headers=headers, json={"on": True}).status_code == 200


def test_the_listing_says_the_live_switches_are_owner_only(monkeypatch):
    headers, me = _admin("m55-listing@example.com")
    monkeypatch.setattr(config, "LIVE_OWNER_USER_ID", me["id"] + 100000)
    body = client.get(FLAGS, headers=headers).json()
    for name in live_owner.OWNER_ONLY_FLAGS:
        assert body[name]["owner_only"] is True and body[name]["can_switch_live"] is False
    assert "owner_only" not in body["terminal"]
    monkeypatch.setattr(config, "LIVE_OWNER_USER_ID", me["id"])
    body = client.get(FLAGS, headers=headers).json()
    assert body["live_trading"]["can_switch_live"] is True and body["live_trading"]["confirm_text"] == "ENABLE LIVE"
    assert body["live_trading"]["opens_live_when_on"] is True and body["phase0_paper_only"]["opens_live_when_on"] is False


def test_the_owner_without_the_typed_confirmation_is_refused(monkeypatch):
    headers, me = _admin("m55-owner-noconfirm@example.com")
    monkeypatch.setattr(config, "LIVE_OWNER_USER_ID", me["id"])
    before = len(_audit(live_owner.REFUSED_EVENT))
    for confirm in (None, "", "enable live", "YES"):
        res = client.put(f"{FLAGS}/phase0_paper_only", headers=headers, json={"on": False, "confirm": confirm})
        assert res.status_code == 400 and res.json()["detail"] == live_owner.NO_CONFIRM
    refused = _audit(live_owner.REFUSED_EVENT)[before:]
    assert len(refused) == 4 and all("typed confirmation missing" in r for r in refused)
    assert _resolved()["phase0_paper_only"]["on"] is True


def test_the_owner_without_a_two_factor_check_is_refused_and_audited(monkeypatch):
    _, me = _admin("m55-owner-nomfa@example.com")
    monkeypatch.setattr(config, "LIVE_OWNER_USER_ID", me["id"])
    before = len(_audit(live_owner.REFUSED_EVENT))

    async def attempt(mfa_enabled: bool):
        async with _session_factory() as session:
            user = await session.get(User, me["id"])
            user.mfa_enabled = mfa_enabled                                   # not committed: this attempt only
            await controls.set_flag(session, user, "live_trading", on=True, session_id=None,
                                    confirm=live_owner.CONFIRM_TEXT)

    for mfa_enabled in (False, True):                                        # no MFA at all; MFA but no checked session
        with pytest.raises(HTTPException) as err:
            _run(attempt(mfa_enabled))
        assert err.value.status_code == 403
    refused = _audit(live_owner.REFUSED_EVENT)[before:]
    assert len(refused) == 2 and all("no recent two-factor check" in r for r in refused)
    assert _resolved()["live_trading"]["on"] is False


def test_the_owner_with_two_factor_and_the_confirmation_succeeds_and_is_audited(monkeypatch):
    headers, me = _admin("m55-owner-ok@example.com")
    monkeypatch.setattr(config, "LIVE_OWNER_USER_ID", me["id"])
    before = len(_audit(live_owner.ALLOWED_EVENT))
    res = client.put(f"{FLAGS}/phase0_paper_only", headers=headers, json={"on": False, "confirm": "ENABLE LIVE"})
    assert res.status_code == 200, res.text
    assert res.json()["phase0_paper_only"]["on"] is False
    res = client.put(f"{FLAGS}/live_trading", headers=headers, json={"on": True, "confirm": "ENABLE LIVE"})
    assert res.status_code == 200, res.text
    assert res.json()["live_trading"]["on"] is True
    allowed = _audit(live_owner.ALLOWED_EVENT)[before:]
    assert len(allowed) == 2
    assert allowed[0].startswith("phase0_paper_only: on=False") and allowed[1].startswith("live_trading: on=True")


def test_a_non_live_flag_needs_no_confirmation(monkeypatch):
    headers, me = _admin("m55-other-flag@example.com")
    monkeypatch.setattr(config, "LIVE_OWNER_USER_ID", me["id"] + 100000)
    assert client.put(f"{FLAGS}/news_feed", headers=headers, json={"on": False}).status_code == 200


def test_opens_live_covers_every_loosening_change():
    assert live_owner.opens_live("live_trading", on=True, tenants=None)
    assert live_owner.opens_live("live_trading", on=False, tenants=[3])
    assert not live_owner.opens_live("live_trading", on=False, tenants=[])
    assert live_owner.opens_live("phase0_paper_only", on=False, tenants=None)
    assert live_owner.opens_live("phase0_paper_only", on=False, tenants=[3])      # locked for some tenants only
    assert not live_owner.opens_live("phase0_paper_only", on=True, tenants=None)
    assert not live_owner.opens_live("terminal", on=True, tenants=None)
