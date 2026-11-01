"""M77 SX-1: the font embedded in server-rendered images and PDFs.

Inter Regular 4.0 (the app's own font; SIL Open Font License 1.1, the licence text beside the file:
app/assets/fonts/Inter-OFL.txt), the unmodified upstream TTF (rsms/inter release v4.0, extras/ttf/Inter-Regular.ttf).
It has the rupee sign (U+20B9), the true minus (U+2212), the en dash and Latin, which Pillow's built-in font does not.
The file ships inside the backend image (`COPY app ./app`); no system font is used, so every server draws the same
glyphs (same input, same bytes).

M77 SX-3: Noto Sans Devanagari Regular 2.006 (SIL OFL 1.1, licence text app/assets/fonts/NotoSansDevanagari-OFL.txt;
the unmodified upstream TTF of notofonts/devanagari) draws the member's own notes when he writes them in Marathi or
Hindi - his words, kept verbatim; every label stays English. Pillow shapes it with raqm (HarfBuzz).
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PIL import ImageFont

FONT_DIR = Path(__file__).resolve().parents[1] / "assets" / "fonts"
REGULAR = FONT_DIR / "Inter-Regular.ttf"
LICENCE = FONT_DIR / "Inter-OFL.txt"
REGULAR_SHA256 = "64f8be6e55c37e32ef03da99714bf3aa58b8f2099bfe4f759a7578e3b8291123"
DEVANAGARI = FONT_DIR / "NotoSansDevanagari-Regular.ttf"
DEVANAGARI_LICENCE = FONT_DIR / "NotoSansDevanagari-OFL.txt"
DEVANAGARI_SHA256 = "526a1b34db43de480d51a8a923c474b848489005d61b6ef45b3a850f7e141ff3"


@lru_cache(maxsize=32)
def regular(size: int) -> ImageFont.FreeTypeFont:
    """The embedded regular face at `size` pixels (cached per size)."""
    return ImageFont.truetype(str(REGULAR), size)


@lru_cache(maxsize=32)
def devanagari(size: int) -> ImageFont.FreeTypeFont:
    """The embedded Devanagari face at `size` pixels (his own notes only; cached per size)."""
    return ImageFont.truetype(str(DEVANAGARI), size)


def is_devanagari(ch: str) -> bool:
    o = ord(ch)
    return 0x0900 <= o <= 0x097F or 0xA8E0 <= o <= 0xA8FF or 0x1CD0 <= o <= 0x1CFF or o in (0x200C, 0x200D)


__all__ = ["DEVANAGARI", "DEVANAGARI_LICENCE", "DEVANAGARI_SHA256", "FONT_DIR", "LICENCE", "REGULAR", "REGULAR_SHA256",
           "devanagari", "is_devanagari", "regular"]
