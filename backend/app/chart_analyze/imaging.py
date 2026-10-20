"""Analyze a chart: the one safe path every image takes (an upload or a fetched link) before it is stored or sent.

1. Size: at most `max_image_bytes` (checked before anything is parsed).
2. Type by magic bytes, never by the file name or the declared content type: PNG (`\\x89PNG\\r\\n\\x1a\\n`) or JPEG
   (`\\xff\\xd8\\xff`). Anything else (GIF, WebP, SVG, HTML, a PDF) is refused.
3. Header only: Pillow opens the image lazily, limited to the PNG and JPEG decoders; the width, the height and the
   pixel count (the decompression-bomb limit, `max_image_pixels`) are checked before a single pixel is decoded, and
   Pillow's own bomb warning is an error.
4. `Image.verify()` on that object, then a fresh open and a full decode (verify leaves the object unusable).
5. Re-encode: the EXIF orientation is applied, then the pixels are copied into a NEW image (RGB or RGBA) with no
   metadata at all, and saved as PNG. EXIF, XMP, ICC profiles, text chunks, comments and any trailing bytes after the
   image end are gone; the original bytes are never stored or sent anywhere.
Every decode error becomes `ImageRejected` with a message the user can act on (a 4xx at the API), never a 500.
"""
import hashlib
import io
import struct
import warnings
from dataclasses import dataclass
from typing import Any, Dict

from PIL import Image, ImageOps, UnidentifiedImageError

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
JPEG_MAGIC = b"\xff\xd8\xff"
ACCEPTED = "PNG or JPEG"


class ImageRejected(ValueError):
    """`status` is the HTTP status the API answers with (413 too big, 415 wrong type, 422 unreadable)."""

    def __init__(self, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class CleanImage:
    png: bytes
    width: int
    height: int
    sha256: str


def sniff(data: bytes) -> str:
    """'png', 'jpeg' or '' from the first bytes."""
    if data.startswith(PNG_MAGIC):
        return "png"
    if data.startswith(JPEG_MAGIC):
        return "jpeg"
    return ""


def _limits(settings: Dict[str, Any]) -> tuple:
    return (int(settings["max_image_bytes"]), int(settings["max_image_width"]), int(settings["max_image_height"]),
            int(settings["max_image_pixels"]))


def check_size(n: int, settings: Dict[str, Any]) -> None:
    max_bytes = _limits(settings)[0]
    if n > max_bytes:
        raise ImageRejected(f"The image is larger than {max_bytes // 1024} KB. Crop it or save it smaller, then try again.", 413)


def sanitize(data: bytes, settings: Dict[str, Any]) -> CleanImage:
    max_bytes, max_w, max_h, max_pixels = _limits(settings)
    if not data:
        raise ImageRejected("The file is empty.", 422)
    check_size(len(data), settings)
    kind = sniff(data)
    if not kind:
        raise ImageRejected(f"Only {ACCEPTED} images are accepted.", 415)
    formats = ["PNG"] if kind == "png" else ["JPEG"]
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data), formats=formats) as probe:
                width, height = probe.size
                if width <= 0 or height <= 0:
                    raise ImageRejected("The image has no pixels.", 422)
                if width > max_w or height > max_h:
                    raise ImageRejected(f"The image is {width} x {height} pixels; the largest accepted is {max_w} x {max_h}.", 413)
                if width * height > max_pixels:
                    raise ImageRejected("The image has too many pixels to read safely.", 413)
                probe.verify()
            with Image.open(io.BytesIO(data), formats=formats) as img:
                if img.size != (width, height):
                    raise ImageRejected("The image could not be read.", 422)
                img.load()
                upright = ImageOps.exif_transpose(img)
                mode = "RGBA" if ("A" in upright.getbands() or upright.mode == "P" and "transparency" in upright.info) else "RGB"
                pixels = upright.convert(mode)
                clean = Image.new(mode, pixels.size)
                clean.paste(pixels)
                clean.info = {}
                out = io.BytesIO()
                clean.save(out, format="PNG", optimize=False)
    except ImageRejected:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise ImageRejected("The image has too many pixels to read safely.", 413) from None
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError, EOFError, MemoryError, TypeError, IndexError, KeyError, struct.error):
        raise ImageRejected("The image could not be read. Save it again as PNG or JPEG and try again.", 422) from None
    png = out.getvalue()
    return CleanImage(png=png, width=clean.size[0], height=clean.size[1], sha256=hashlib.sha256(png).hexdigest())


def png_chunks(png: bytes) -> list:
    """The chunk types of a PNG, in order (tests: proves no metadata chunk survives)."""
    if not png.startswith(PNG_MAGIC):
        return []
    pos, names = len(PNG_MAGIC), []
    while pos + 8 <= len(png):
        length, kind = struct.unpack(">I4s", png[pos:pos + 8])
        names.append(kind.decode("latin-1"))
        pos += 12 + length
        if kind == b"IEND":
            break
    return names
