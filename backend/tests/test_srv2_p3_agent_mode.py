"""SRV2 P3 (owner AGENT-MODES 20 Nov, ADR-0029): ATP's side of the agent modes on a test double of the private package.

- Only the configured owner may switch; any other admin is refused (403) and the refusal is audited.
- `auto` needs an explicit confirm and is passed to the private package as the owner's own deployment.
- Every switch writes one audit row (from -> to, vision_on) and the GET shows it as the last switch.
- Package absent: the default mode is shown and a switch answers 409.
- The bot's first ON step stores the default (human_confirm) only when none is stored, then starts the Vision / Telegram services
  once; flag OFF stops them."""
import asyncio
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.db.models import AuditLogRecord
from app.platform import live_owner
from app.platform import routes as platform_routes
from app.srv2bot import io as bot_io
from app.srv2bot import job, loader
from app.srv2bot import mode as agent_mode
from tests.test_auth_api import _session_factory


def _run(coro):
    return asyncio.run(coro)


class FakeModes:
    """The private `atp_private.srv2.modes` contract: apply / current / ensure_default (the real one is tested in atp-private)."""

    def __init__(self, stored=None):
        self.stored = stored                       # None = no mode stored yet
        self.calls = []

    def current(self):
        if self.stored is None:
            return "human_confirm", True
        return self.stored

    def apply(self, mode, by, owner_deployment, vision_on=True):
        self.calls.append((mode, by, owner_deployment, vision_on))
        if mode == "auto" and owner_deployment is not True:
            raise PermissionError("auto is the owner's own switch")
        before = self.current()
        self.stored = (mode, bool(vision_on) if mode == "auto" else True)
        return {"from": before[0], "to": mode, "vision_on": self.stored[1], "at": "now"}

    def ensure_default(self, by):
        if self.stored is not None:
            return None
        return self.apply("human_confirm", by, owner_deployment=False)


@pytest.fixture(autouse=True)
def _fresh():
    loader.set_for_tests(loader._UNSET)
    job._services.update(on=False, bot=None)
    yield
    loader.set_for_tests(loader._UNSET)
    job._services.update(on=False, bot=None)


def _bot(modes):
    started, stopped = [], []
    runner = NS(tick=lambda now, cal: [], start_services=lambda: started.append(1) or ["vision", "telegram"],
                stop_services=lambda: stopped.append(1))
    return NS(host=NS(register=lambda **kw: None), runner=runner, modes=modes), started, stopped


async def _audit_rows(event):
    async with _session_factory() as s:
        return list((await s.scalars(select(AuditLogRecord).where(AuditLogRecord.event == event))).all())


def _owner(monkeypatch, user_id=424242):
    monkeypatch.setattr(live_owner, "is_owner", lambda user: getattr(user, "id", None) == user_id)
    return NS(id=user_id)


def test_only_the_owner_switches_and_a_refusal_is_audited(monkeypatch):
    modes = FakeModes()
    loader.set_for_tests(_bot(modes)[0])
    _owner(monkeypatch)
    other = NS(id=7)

    async def go():
        async with _session_factory() as s:
            with pytest.raises(HTTPException) as exc:
                await platform_routes.put_srv2_agent_mode(platform_routes.AgentModeBody(mode="human_confirm"), other, s)
            assert exc.value.status_code == 403
            got = await platform_routes.get_srv2_agent_mode(other, s)
            assert got["can_switch"] is False and got["mode"] == "human_confirm"
    _run(go())
    assert modes.calls == []
    assert _run(_audit_rows(agent_mode.EVENT + ".refused"))


def test_auto_needs_confirm_is_passed_as_the_owners_deployment_and_is_audited(monkeypatch):
    modes = FakeModes()
    loader.set_for_tests(_bot(modes)[0])
    me = _owner(monkeypatch)

    async def go():
        async with _session_factory() as s:
            with pytest.raises(HTTPException) as exc:
                await platform_routes.put_srv2_agent_mode(platform_routes.AgentModeBody(mode="auto"), me, s)
            assert exc.value.status_code == 400 and modes.calls == []
            out = await platform_routes.put_srv2_agent_mode(
                platform_routes.AgentModeBody(mode="auto", vision_on=False, confirm=True), me, s)
            assert out["mode"] == "auto" and out["vision_on"] is False and out["can_switch"] is True
            assert out["last"]["from"] == "human_confirm" and out["last"]["to"] == "auto" and out["last"]["by"] == me.id
            back = await platform_routes.put_srv2_agent_mode(platform_routes.AgentModeBody(mode="human_confirm"), me, s)
            assert back["mode"] == "human_confirm" and back["last"]["to"] == "human_confirm"
    _run(go())
    assert modes.calls[0] == ("auto", f"user:{me.id}", True, False)
    assert len(_run(_audit_rows(agent_mode.EVENT))) >= 2


def test_an_unknown_mode_is_refused_and_an_absent_package_answers_409(monkeypatch):
    me = _owner(monkeypatch)
    loader.set_for_tests(_bot(FakeModes())[0])

    async def bad():
        async with _session_factory() as s:
            with pytest.raises(HTTPException) as exc:
                await platform_routes.put_srv2_agent_mode(platform_routes.AgentModeBody(mode="yolo", confirm=True), me, s)
            assert exc.value.status_code == 400
    _run(bad())
    loader.set_for_tests(None)

    async def absent():
        async with _session_factory() as s:
            got = await platform_routes.get_srv2_agent_mode(me, s)
            assert got["installed"] is False and got["mode"] == "human_confirm"
            with pytest.raises(HTTPException) as exc:
                await platform_routes.put_srv2_agent_mode(platform_routes.AgentModeBody(mode="human_confirm"), me, s)
            assert exc.value.status_code == 409
    _run(absent())


def test_the_first_on_step_stores_the_default_once_and_starts_the_services_once(monkeypatch):
    modes = FakeModes()
    bot, started, stopped = _bot(modes)
    loader.set_for_tests(bot)
    on = {"v": True}

    async def an_owner(session):
        return bot_io.Owner(10**6, NS(id=99))

    async def flag(session, name, tenant_id=None):
        return on["v"]

    async def cal(session, now):
        return None
    monkeypatch.setattr(job, "owner", an_owner)
    monkeypatch.setattr("app.platform.controls.flag_enabled", flag)
    monkeypatch.setattr(job, "calendar_for", cal)
    monkeypatch.setattr(bot_io, "build", lambda *a, **k: {})
    _run(job.run_once(_session_factory, None))
    _run(job.run_once(_session_factory, None))
    assert started == [1] and modes.calls == [("human_confirm", "user:99", False, True)]
    on["v"] = False
    assert _run(job.run_once(_session_factory, None)) is None and stopped == [1]


def test_an_existing_auto_is_never_reset_by_the_default():
    modes = FakeModes(stored=("auto", True))
    loader.set_for_tests(_bot(modes)[0])

    async def go():
        async with _session_factory() as s:
            assert await agent_mode.ensure_default(s, 1) is None
    _run(go())
    assert modes.calls == [] and modes.current() == ("auto", True)
