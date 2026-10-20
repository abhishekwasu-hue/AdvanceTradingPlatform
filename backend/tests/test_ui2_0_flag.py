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
