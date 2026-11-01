"""M77 SX-1: the embedded font draws the rupee sign, the minus, the en dash and Latin (Pillow's own font cannot)."""
from __future__ import annotations

import hashlib

import pytest
from PIL import ImageFont

from app.study import fonts

MISSING = ""       # a private-use code point no font here maps: what the missing-glyph box looks like


def _mask(font: ImageFont.FreeTypeFont, text: str) -> tuple[tuple[int, int], bytes]:
    m = font.getmask(text)
    return m.size, bytes(m)


def test_font_file_and_licence_ship_together() -> None:
    assert fonts.REGULAR.is_file() and fonts.LICENCE.is_file()
    assert hashlib.sha256(fonts.REGULAR.read_bytes()).hexdigest() == fonts.REGULAR_SHA256
    assert fonts.REGULAR.stat().st_size < 512 * 1024
    licence = fonts.LICENCE.read_text(encoding="utf-8")
    assert "SIL OPEN FONT LICENSE Version 1.1" in licence and "The Inter Project Authors" in licence


@pytest.mark.parametrize("ch", ["₹", "−", "–", "A", "z", "0", "9", ",", "."])
def test_glyph_is_drawn_not_the_missing_box(ch: str) -> None:
    font = fonts.regular(24)
    assert font.getname() == ("Inter", "Regular")
    drawn = _mask(font, ch)
    assert drawn != _mask(font, MISSING), f"U+{ord(ch):04X} draws the missing-glyph box"
    assert any(drawn[1]), f"U+{ord(ch):04X} draws nothing"


def test_pillow_default_font_lacks_the_rupee() -> None:
    """Why the font is bundled: Pillow's built-in face draws the rupee as its missing-glyph box."""
    default = ImageFont.load_default(24)
    assert _mask(default, "₹") == _mask(default, MISSING)


def test_cached_per_size() -> None:
    assert fonts.regular(16) is fonts.regular(16)
    assert fonts.regular(16) is not fonts.regular(18)
