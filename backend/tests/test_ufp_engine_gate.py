"""User-friendly pass item 7: Analyze a chart is unavailable to users while its private engine is not installed.

The flag may be on, but without `atp_private.chart_analyze` GET /api/system/features answers `chart_analyze` (and the
two flags that depend on it) as off, so the user nav hides the page and a direct visit shows "Coming soon". The admin
flag list keeps the stored state and says why (`unavailable_reason`). With the engine installed nothing changes."""
import uuid

from app.chart_analyze import provider
from app.platform import controls
from tests.test_amw2_watchlists import _set_flag
from tests.test_auth_api import _register, client
from tests.test_chart_analyze_units import FakeProvider


def _user():
    headers = {"Authorization": f"Bearer {_register(f'ufp7-{uuid.uuid4().hex[:6]}@example.com')}"}
    return headers, client.get("/api/auth/me", headers=headers).json()["tenant_id"]


def test_no_engine_means_no_feature_for_users_even_with_the_flag_on():
    headers, tenant = _user()
    provider.set_for_tests(None)
    for flag in controls.ENGINE_FLAGS:
        _set_flag(flag, tenant, on=True)
    try:
        features = client.get("/api/system/features", headers=headers).json()
        for flag in controls.ENGINE_FLAGS:
            assert features[flag] is False, flag
        assert controls.engine_missing_flags() == {f: controls.ENGINE_MISSING_REASON for f in controls.ENGINE_FLAGS}
        provider.set_for_tests(FakeProvider())
        features = client.get("/api/system/features", headers=headers).json()
        for flag in controls.ENGINE_FLAGS:
            assert features[flag] is True, flag
        assert controls.engine_missing_flags() == {}
    finally:
        for flag in controls.ENGINE_FLAGS:
            _set_flag(flag, tenant, on=False)
        provider.reset_for_tests()


def test_the_admin_flag_list_states_the_real_reason_and_keeps_the_stored_state():
    import asyncio

    from tests.test_auth_api import _session_factory

    async def listing():
        async with _session_factory() as session:
            return await controls.flag_listing(session)

    provider.set_for_tests(None)
    try:
        flags = asyncio.run(listing())
        assert "not installed" in flags["chart_analyze"]["unavailable_reason"]
        assert "unavailable_reason" not in flags["ui_v2"]
        provider.set_for_tests(FakeProvider())
        assert "unavailable_reason" not in asyncio.run(listing())["chart_analyze"]
    finally:
        provider.reset_for_tests()


def test_the_user_facing_reason_text_is_never_the_engine_message():
    # The page's old notice ("Analysis engine not installed ...") is gone from the user view; the reason is admin-only.
    assert "install" not in controls.FEATURE_FLAGS["chart_analyze"].lower()
