"""P2-1 (docs/design/P2_1_FLAG_AUDIT.md, owner ✅ 26 Oct): every "unfinished" flag carries its fate in the admin list -
finishing in a named item, kept off with a reason, or removed from view; an unfinished pack flag says it is in the
trader pack (FA-1); a removed-from-view flag cannot be turned on (FA-2) but can always be turned off."""
from app.platform import controls
from app.platform.flag_catalog import FINISH, FLAG_INFO, KEEP_OFF, REMOVED_FROM_VIEW, fate_for
from tests.test_admin_api import _admin
from tests.test_auth_api import client


def test_every_unfinished_flag_has_exactly_one_fate_and_only_known_flags_have_one():
    named = [*FINISH, *KEEP_OFF, *REMOVED_FROM_VIEW]
    assert len(named) == len(set(named)) and set(named) <= set(controls.FEATURE_FLAGS)
    unfinished = {n for n, i in FLAG_INFO.items() if i.status == "unfinished"}
    assert unfinished <= set(named), sorted(unfinished - set(named))
    assert fate_for("data_hub") == {"fate": "finish", "fate_note": "Finishing in P1-5"}
    assert fate_for("screener_gallery") == {}


def test_admin_list_shows_the_fate_and_the_pack_note():
    headers, _ = _admin("p21-fates-admin@example.com")
    flags = client.get("/api/admin/controls/flags", headers=headers).json()
    assert flags["data_hub"]["fate_note"] == "In the trader pack; finishing in P1-5"
    assert flags["learn"]["fate"] == "keep_off" and flags["learn"]["fate_note"].startswith("Kept off: ")
    assert flags["vision_byok_v3"]["fate"] == "removed_from_view"
    assert "fate" not in flags["screener_gallery"]


def test_a_removed_from_view_flag_cannot_be_turned_on_but_can_be_turned_off():
    headers, _ = _admin("p21-fates-admin2@example.com")
    url = "/api/admin/controls/flags/vision_byok_v3"
    assert client.put(url, headers=headers, json={"on": True, "tenants": []}).status_code == 400
    assert client.put(url, headers=headers, json={"on": False, "tenants": [1]}).status_code == 400
    assert client.put(url, headers=headers, json={"on": False, "tenants": []}).status_code == 200
