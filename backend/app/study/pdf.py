"""M77 SX-3: the study PDF - ATP's own small PDF writer (as journal/export.py `_Pdf`, which is kept unchanged), with
each A4 page an embedded lossless image plus an invisible text layer (M77Q-1, owner "Ok default yes").

* Each page image is Pillow's PNG data (zlib, PNG row filters) placed as a /FlateDecode image with the PNG predictor
  (/Predictor 15) - lossless, no JPEG, no new dependency.
* Over it, every text line of the page as invisible text (render mode 3, Helvetica, ASCII: "₹" -> "Rs ", "−" -> "-",
  any other non-ASCII character -> "?") so a PDF viewer can search and copy it.
* No creation date, producer string or random id: the same snapshot gives the same bytes (M77Q-3 rebuild on download).
"""
from __future__ import annotations

import io
import struct
from collections.abc import Mapping, Sequence
from typing import Any

from app.journal.export import _escape
from app.study import pages as P
from app.study import snapshot as S

PT_W, PT_H = 595.28, 841.89                         # A4 in points
SCALE = 72.0 / P.DPI


class PdfTooLarge(ValueError):
    """The PDF is over STUDY_PDF_MAX_BYTES; the message is the plain sentence the member sees."""


def _png_stream(img: Any) -> bytes:
    """The zlib stream of the PNG's IDAT chunks (filtered rows), for /FlateDecode with /Predictor 15."""
    buf = io.BytesIO()
    img.save(buf, format="PNG", compress_level=9, optimize=False)
    data = buf.getvalue()
    pos, out = 8, bytearray()
    while pos < len(data):
        length = struct.unpack(">I", data[pos:pos + 4])[0]
        kind = data[pos + 4:pos + 8]
        if kind == b"IDAT":
            out += data[pos + 8:pos + 8 + length]
        pos += 12 + length
    return bytes(out)


def _text_ops(lines: Sequence[tuple[float, float, int, str]]) -> str:
    ops = ["BT 3 Tr"]
    for x, y, size, text in lines:
        pt = size * SCALE
        # baseline about 0.8 of the size below the line's top, in PDF coordinates (origin bottom-left)
        ops.append(f"/F1 {pt:.2f} Tf 1 0 0 1 {x * SCALE:.2f} {PT_H - (y + size * 0.8) * SCALE:.2f} Tm ({_escape(text)}) Tj")
    ops.append("ET")
    return "\n".join(ops)


def build(rendered: Sequence[P.Rendered], title: str = "Study") -> bytes:
    """The PDF of the rendered pages."""
    n = len(rendered)
    objects: list[bytes] = []
    # 1 catalog, 2 pages, 3 Helvetica, 4 info; then per page: page, content, image
    kids = " ".join(f"{5 + 3 * i} 0 R" for i in range(n))
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {n} >>".encode())
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    objects.append(f"<< /Title ({_escape(title)}) >>".encode())
    for i, page in enumerate(rendered):
        content_no, image_no = 6 + 3 * i, 7 + 3 * i
        w, h = page.image.size
        objects.append((f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PT_W} {PT_H}] /Contents {content_no} 0 R "
                        f"/Resources << /Font << /F1 3 0 R >> /XObject << /Im{i} {image_no} 0 R >> >> >>").encode())
        stream = (f"q {PT_W} 0 0 {PT_H} 0 0 cm /Im{i} Do Q\n" + _text_ops(page.text)).encode("latin-1")
        objects.append(f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream")
        pixels = _png_stream(page.image)
        objects.append((f"<< /Type /XObject /Subtype /Image /Width {w} /Height {h} /ColorSpace /DeviceRGB /BitsPerComponent 8 "
                        f"/Filter /FlateDecode /DecodeParms << /Predictor 15 /Colors 3 /BitsPerComponent 8 /Columns {w} >> "
                        f"/Length {len(pixels)} >>\nstream\n").encode() + pixels + b"\nendstream")
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R /Info 4 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def study_pdf(snap: Mapping[str, Any], *, cvd: bool = False, limit: int = 0) -> bytes:
    """The PDF of a stored snapshot (pure; same snapshot and `cvd` = same bytes)."""
    from app.core import config
    fp = S.fingerprint(S.canonical(snap))
    inst = snap.get("instrument") or {}
    data = build(P.render(snap, fp, cvd=cvd), title=f"Study {inst.get('symbol') or ''}".strip())
    limit = limit or config.STUDY_PDF_MAX_BYTES
    if len(data) > limit:
        raise PdfTooLarge(f"This study's PDF would be larger than {limit // (1024 * 1024)} MB. Save the study again with fewer "
                          "sections or shorter notes.")
    return data


__all__ = ["build", "study_pdf", "PdfTooLarge"]
