"""Colour order v2 (owner, 23 Oct): the `colour_v2` flag carries the new colour on the v2 pages and needs `ui_v2`. The
owner approved it on 24 Oct (after the CV-1..CV-4 page) and ordered it ON by default (DECISIONS)."""
from app.platform import controls
from app.platform.flag_catalog import FLAG_INFO


def test_colour_v2_is_registered_on_by_default_and_needs_ui_v2():
    assert "colour_v2" in controls.FEATURE_FLAGS
    assert "colour_v2" in controls.DEFAULT_ON_FLAGS
    assert "colour_v2" not in controls.DEFAULT_OFF_FLAGS
    info = FLAG_INFO["colour_v2"]
    assert info.depends_on == ("ui_v2",)
    assert info.area == "platform"
    assert info.status == "ready"


def test_colour_v2_description_says_it_places_no_orders():
    assert "layout only" in controls.FEATURE_FLAGS["colour_v2"]
