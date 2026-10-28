"""UX-14 "Professional palette" (owner, 28 Oct; docs/owner-orders/ATP_ORDER_UX14.md): the colour_v3 flag."""

from app.platform import controls
from app.platform.flag_catalog import FLAG_INFO


def test_colour_v3_is_registered_off_by_default_and_needs_colour_v2():
    assert "colour_v3" in controls.FEATURE_FLAGS
    assert "colour_v3" in controls.DEFAULT_OFF_FLAGS          # OFF until the owner's OK on the comparison page
    assert "colour_v3" not in controls.DEFAULT_ON_FLAGS
    assert set(FLAG_INFO["colour_v3"].depends_on) == {"ui_v2", "colour_v2"}


def test_colour_v3_description_says_it_places_no_orders():
    assert "layout only" in controls.FEATURE_FLAGS["colour_v3"]
