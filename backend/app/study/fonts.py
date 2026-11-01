"""M77 SX-1: the font embedded in server-rendered images and PDFs.

Inter Regular 4.0 (the app's own font; SIL Open Font License 1.1, the licence text beside the file:
app/assets/fonts/Inter-OFL.txt), the unmodified upstream TTF (rsms/inter release v4.0, extras/ttf/Inter-Regular.ttf).
It has the rupee sign (U+20B9), the true minus (U+2212), the en dash and Latin, which Pillow's built-in font does not.
The file ships inside the backend image (`COPY app ./app`); no system font is used, so every server draws the same
glyphs (same input, same bytes).
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PIL import ImageFont

FONT_DIR = Path(__file__).resolve().parents[1] / "assets" / "fonts"
REGULAR = FONT_DIR / "Inter-Regular.ttf"
LICENCE = FONT_DIR / "Inter-OFL.txt"
REGULAR_SHA256 = "64f8be6e55c37e32ef03da99714bf3aa58b8f2099bfe4f759a7578e3b8291123"


@lru_cache(maxsize=32)
def regular(size: int) -> ImageFont.FreeTypeFont:
    """The embedded regular face at `size` pixels (cached per size)."""
    return ImageFont.truetype(str(REGULAR), size)


__all__ = ["FONT_DIR", "LICENCE", "REGULAR", "REGULAR_SHA256", "regular"]
