"""UI2-0 (UI v2 proposal §12): the `ui_v2` flag - the redesigned navigation and pages, layout only.

Registered once in FEATURE_FLAGS, OFF by default (computed: it is not in the closed DEFAULT_ON_FLAGS list), and served
to the frontend by GET /api/system/features like every other flag, so the shell's useFeatures() hook can read it.
The usual tenant allow-list turns it on for one organisation only."""
import uuid

from app.platform import controls
from tests.test_amw2_watchlists import _set_flag
from tests.test_auth_api import _register, client

FLAG = "ui_v2"


def test_ui_v2_is_registered_with_a_plain_description():
    assert FLAG in controls.FEATURE_FLAGS
    description = controls.FEATURE_FLAGS[FLAG]
    assert description == "UI v2: the redesigned navigation and pages (layout only; places no orders)"


def test_ui_v2_is_off_by_default():
    assert FLAG in controls.DEFAULT_OFF_FLAGS
    assert FLAG not in controls.DEFAULT_ON_FLAGS


def test_ui_v2_is_not_the_last_entry():
    # New flags go in their sorted position, never at the tail, so parallel branches do not conflict on one line.
    assert list(controls.FEATURE_FLAGS)[-1] != FLAG


def test_features_endpoint_serves_ui_v2_off_then_on_for_one_tenant_only():
    a = {"Authorization": f"Bearer {_register(f'ui20-a-{uuid.uuid4().hex[:6]}@example.com')}"}
    b = {"Authorization": f"Bearer {_register(f'ui20-b-{uuid.uuid4().hex[:6]}@example.com')}"}
    assert client.get("/api/system/features", headers=a).json()[FLAG] is False
    tenant_a = client.get("/api/auth/me", headers=a).json()["tenant_id"]
    _set_flag(FLAG, tenant_a, on=True)
    try:
        assert client.get("/api/system/features", headers=a).json()[FLAG] is True
        assert client.get("/api/system/features", headers=b).json()[FLAG] is False
    finally:
        _set_flag(FLAG, tenant_a, on=False)
    assert client.get("/api/system/features", headers=a).json()[FLAG] is False


# --- UI2-0 review M3: /system/features resolves the stored flags once, with the same answers -------------------------

def _run(coro):
    import asyncio
    return asyncio.run(coro)


async def _old_per_flag(session, tenant_id):
    """The evaluation before M3: flag_enabled once per flag (each re-reading every flag)."""
    return {name: await controls.flag_enabled(session, name, tenant_id) for name in controls.FEATURE_FLAGS}


def test_features_for_tenant_equals_the_per_flag_answers_for_several_tenants_and_allow_lists():
    from tests.test_auth_api import _session_factory
    tenant_a = client.get("/api/auth/me", headers={"Authorization": f"Bearer {_register(f'ui20-m3a-{uuid.uuid4().hex[:6]}@example.com')}"}).json()["tenant_id"]
    tenant_b = client.get("/api/auth/me", headers={"Authorization": f"Bearer {_register(f'ui20-m3b-{uuid.uuid4().hex[:6]}@example.com')}"}).json()["tenant_id"]
    # An allow-list for A only, on two default-off flags (one of them ui_v2); everything else at its default.
    _set_flag(FLAG, tenant_a, on=True)
    _set_flag("watchlists", tenant_a, on=True)
    try:
        async def go():
            async with _session_factory() as session:
                out = {}
                for tenant in (None, tenant_a, tenant_b, 987654321):
                    new, old = await controls.features_for_tenant(session, tenant), await _old_per_flag(session, tenant)
                    assert new == old, tenant
                    assert list(new) == list(controls.FEATURE_FLAGS)
                    out[tenant] = new
                return out
        out = _run(go())
        assert out[tenant_a][FLAG] is True and out[tenant_a]["watchlists"] is True
        assert out[tenant_b][FLAG] is False and out[None][FLAG] is False and out[987654321][FLAG] is False
        assert out[tenant_b]["live_trading"] == out[None]["live_trading"]     # a default-on flag, unchanged
    finally:
        _set_flag(FLAG, tenant_a, on=False)
        _set_flag("watchlists", tenant_a, on=False)


def test_features_for_tenant_reads_the_stored_flags_once(monkeypatch):
    from tests.test_auth_api import _session_factory
    calls = {"n": 0}
    real = controls.feature_flags

    async def counting(session):
        calls["n"] += 1
        return await real(session)

    monkeypatch.setattr(controls, "feature_flags", counting)

    async def go():
        async with _session_factory() as session:
            return await controls.features_for_tenant(session, 1)
    answer = _run(go())
    assert calls["n"] == 1
    assert len(answer) == len(controls.FEATURE_FLAGS)
