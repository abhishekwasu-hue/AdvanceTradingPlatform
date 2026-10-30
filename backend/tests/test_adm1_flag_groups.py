"""ADM-1 (owner 30 Oct: "too many controls in the Admin console, make category-wise groups"): every flag sits in exactly
one finer group with a plain label; the admin list serves the group with each flag; no group is left empty."""
from app.platform import controls
from app.platform.flag_catalog import AREAS, FLAG_GROUP, GROUPS, info_for


def test_every_flag_has_exactly_one_known_group_and_no_group_is_empty():
    assert set(FLAG_GROUP) == set(controls.FEATURE_FLAGS)
    assert set(FLAG_GROUP.values()) == set(GROUPS)
    assert all(label and label[0].isupper() for label in GROUPS.values())


def test_the_safety_switches_sit_together():
    for name in ("live_trading", "phase0_paper_only", "approval_only_entries", "risk_by_default"):
        assert FLAG_GROUP[name] == "safety", name


def test_info_carries_the_group_and_falls_back_to_the_area():
    entry = info_for("screener_gallery")
    assert entry["group"] == "screen_library" and entry["group_label"] == GROUPS["screen_library"]
    assert entry["area"] == "screens"                                  # the broad area is unchanged
    unknown = info_for("no_such_flag")
    assert unknown["group"] == "platform" and unknown["group_label"] == AREAS["platform"]
