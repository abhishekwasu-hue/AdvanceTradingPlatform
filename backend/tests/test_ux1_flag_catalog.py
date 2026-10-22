"""UX1 (audit section 7, handover 8b): the admin Feature flags list - a plain description, area, what turns on, what it
needs, the default and a status for every flag; served only to a SUPER_ADMIN with two-factor."""
import re

from app.platform import controls
from app.platform.flag_catalog import AREAS, FLAG_INFO, STATUSES, info_for
from tests.test_admin_api import _admin
from tests.test_auth_api import _register, client


def test_every_flag_has_exactly_one_catalog_entry():
    assert set(FLAG_INFO) == set(controls.FEATURE_FLAGS)


def test_dependencies_are_known_flags_and_never_the_flag_itself_nor_a_cycle():
    for name, info in FLAG_INFO.items():
        assert info.area in AREAS and info.status in STATUSES
        assert info.plain and info.plain[0].isupper(), name
        for dep in info.depends_on:
            assert dep in controls.FEATURE_FLAGS and dep != name, (name, dep)

    def chain(name, seen):
        for dep in FLAG_INFO[name].depends_on:
            assert dep not in seen, f"cycle through {dep}"
            chain(dep, seen | {dep})

    for name in FLAG_INFO:
        chain(name, {name})


def test_plain_words_carry_no_flag_names():
    # A dependency on another flag lives in depends_on (the list names it plainly); the text fields stay plain words.
    snake = re.compile(r"\b[a-z]+_[a-z0-9_]+\b")
    for name, info in FLAG_INFO.items():
        for text in (info.plain, info.turns_on, info.needs):
            assert not snake.search(text), (name, text)


def test_audit_statuses_for_a_few_known_flags():
    assert FLAG_INFO["screener_v2"].status == "ready"
    assert FLAG_INFO["phase0_paper_only"].status == "behind_the_scenes"
    assert FLAG_INFO["chart_contracts"].status == "unfinished"
    assert FLAG_INFO["chart_contracts"].depends_on == ("history_backfill",)
    assert FLAG_INFO["live_trading"].status == "decision"
    assert FLAG_INFO["screener_copilot"].depends_on == ("screener_v2", "ai_copilot")
    assert info_for("no_such_flag")["status"] == "unfinished"


def test_admin_list_serves_the_catalog_and_the_default():
    headers, _ = _admin("ux1-flags-admin@example.com")
    flags = client.get("/api/admin/controls/flags", headers=headers).json()
    assert set(flags) == set(controls.FEATURE_FLAGS)
    entry = flags["screener_gallery"]
    assert entry["plain"] == "Ready-made screens to browse"
    assert entry["depends_on"] == ["screener_v2"]
    assert entry["status"] == "ready" and entry["status_label"] == "Ready for users"
    assert entry["area"] == "screens" and entry["area_label"] == AREAS["screens"]
    assert entry["default_on"] is False and "description" in entry and "tenants" in entry
    assert flags["ai_copilot"]["default_on"] is True
    # The switch answers with the same fields, so the list keeps them after a change.
    changed = client.put("/api/admin/controls/flags/screener_gallery", headers=headers, json={"on": False, "tenants": []})
    assert changed.status_code == 200 and changed.json()["screener_gallery"]["plain"] == entry["plain"]


def test_a_trader_cannot_read_the_admin_list():
    trader = {"Authorization": f"Bearer {_register('ux1-flags-trader@example.com')}"}
    assert client.get("/api/admin/controls/flags", headers=trader).status_code == 403
