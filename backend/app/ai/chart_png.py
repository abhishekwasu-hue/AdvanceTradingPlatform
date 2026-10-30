"""BYOK-V2 (docs/BYOK.md): a deterministic server-side candlestick chart as a PNG - no network, no extra dependency.

The Vision audit sends this image, with a minimal text context, to the provider the user chose under the user's own
key. It is drawn from the engine's own data only: the bars of the platform's market-data lake and the levels of the
proposal (entry, stop, target, the window's high and low). The same input always gives the same bytes (no clock, no
randomness, fixed zlib level), so the stored SHA-256 identifies exactly what was sent.

The encoder is the PNG format itself (signature, IHDR, one IDAT of zlib-compressed scanlines with filter 0, IEND) -
the standard library is enough and nothing is added to requirements.txt. Text on the image is a tiny built-in bitmap
font for the price labels; the report's numbers never come from reading the image back (they are the engine's).
"""
import struct
import zlib
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

WIDTH = 800
HEIGHT = 450
MAX_BARS = 120

Color = Tuple[int, int, int]
BG: Color = (255, 255, 255)
GRID: Color = (232, 234, 238)
AXIS: Color = (120, 126, 136)
WICK: Color = (70, 74, 82)
UP: Color = (22, 150, 92)
DOWN: Color = (214, 56, 56)
ENTRY: Color = (37, 99, 235)
STOP: Color = (220, 38, 38)
TARGET: Color = (5, 150, 105)
ZONE: Color = (148, 102, 214)
LABEL_BG: Color = (255, 255, 255)

# 3x5 glyphs (rows top to bottom, 3 bits each) - digits and the few letters the labels use.
_FONT: Dict[str, Sequence[int]] = {
    "0": (7, 5, 5, 5, 7), "1": (2, 6, 2, 2, 7), "2": (7, 1, 7, 4, 7), "3": (7, 1, 7, 1, 7), "4": (5, 5, 7, 1, 1),
    "5": (7, 4, 7, 1, 7), "6": (7, 4, 7, 5, 7), "7": (7, 1, 1, 1, 1), "8": (7, 5, 7, 5, 7), "9": (7, 5, 7, 1, 7),
    ".": (0, 0, 0, 0, 2), "-": (0, 0, 7, 0, 0), " ": (0, 0, 0, 0, 0), "/": (1, 1, 2, 4, 4),
    "E": (7, 4, 6, 4, 7), "S": (7, 4, 7, 1, 7), "L": (4, 4, 4, 4, 7), "T": (7, 2, 2, 2, 2), "H": (5, 5, 7, 5, 5),
    "I": (7, 2, 2, 2, 7), "G": (7, 4, 5, 5, 7), "O": (7, 5, 5, 5, 7), "W": (5, 5, 5, 7, 5), "N": (7, 5, 5, 5, 5),
    "R": (6, 5, 6, 5, 5), "Y": (5, 5, 2, 2, 2), "P": (7, 5, 7, 4, 4), "A": (2, 5, 7, 5, 5), "X": (5, 5, 2, 5, 5),
}
SCALE = 2


@dataclass(frozen=True)
class Bar:
    open: float
    high: float
    low: float
    close: float


@dataclass
class ChartSpec:
    """What to draw. `levels` maps a short label (ENTRY, SL, TGT, EXIT, HIGH, LOW) to a price from the engine's data
    (EXIT: the journal's exit snapshot, M62; the Vision audit never passes it, so its images are unchanged)."""
    bars: List[Bar]
    levels: Dict[str, float] = field(default_factory=dict)


EXIT: Color = (217, 119, 6)
_LEVEL_COLORS: Dict[str, Color] = {"ENTRY": ENTRY, "SL": STOP, "TGT": TARGET, "EXIT": EXIT, "HIGH": ZONE, "LOW": ZONE}


class _Canvas:
    def __init__(self, width: int, height: int, bg: Color) -> None:
        self.width, self.height = width, height
        self.px = bytearray(bytes(bg) * (width * height))

    def set(self, x: int, y: int, c: Color) -> None:
        if 0 <= x < self.width and 0 <= y < self.height:
            i = (y * self.width + x) * 3
            self.px[i:i + 3] = bytes(c)

    def rect(self, x0: int, y0: int, x1: int, y1: int, c: Color) -> None:
        x0, x1 = sorted((x0, x1))
        y0, y1 = sorted((y0, y1))
        x0, x1, y0, y1 = max(0, x0), min(self.width - 1, x1), max(0, y0), min(self.height - 1, y1)
        if x0 > x1 or y0 > y1:
            return
        row = bytes(c) * (x1 - x0 + 1)
        for y in range(y0, y1 + 1):
            i = (y * self.width + x0) * 3
            self.px[i:i + len(row)] = row

    def hline(self, x0: int, x1: int, y: int, c: Color, dash: int = 0) -> None:
        for x in range(min(x0, x1), max(x0, x1) + 1):
            if dash and (x // dash) % 2:
                continue
            self.set(x, y, c)

    def vline(self, x: int, y0: int, y1: int, c: Color) -> None:
        for y in range(min(y0, y1), max(y0, y1) + 1):
            self.set(x, y, c)

    def text(self, x: int, y: int, s: str, c: Color) -> int:
        """Draws `s` with the built-in font; returns the width drawn."""
        cursor = x
        for ch in s.upper():
            glyph = _FONT.get(ch, _FONT[" "])
            for row, bits in enumerate(glyph):
                for col in range(3):
                    if bits & (4 >> col):
                        self.rect(cursor + col * SCALE, y + row * SCALE, cursor + col * SCALE + SCALE - 1, y + row * SCALE + SCALE - 1, c)
            cursor += 4 * SCALE
        return cursor - x

    def png(self) -> bytes:
        raw = bytearray()
        stride = self.width * 3
        for y in range(self.height):
            raw.append(0)                                     # filter type 0 (None) - simple and deterministic
            raw.extend(self.px[y * stride:(y + 1) * stride])
        return encode_png(self.width, self.height, bytes(raw))


def _chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


def encode_png(width: int, height: int, scanlines: bytes) -> bytes:
    """An 8-bit RGB PNG from filtered scanlines."""
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", header) + _chunk(b"IDAT", zlib.compress(scanlines, 9)) + _chunk(b"IEND", b"")


def _fmt(price: float) -> str:
    return f"{price:.2f}"


def render(spec: ChartSpec, *, width: int = WIDTH, height: int = HEIGHT) -> bytes:
    """The PNG bytes. Raises ValueError without bars (nothing worth sending to a provider)."""
    bars = [b for b in spec.bars if min(b.open, b.high, b.low, b.close) > 0][-MAX_BARS:]
    if not bars:
        raise ValueError("no bars to draw")
    canvas = _Canvas(width, height, BG)
    left, right, top, bottom = 12, width - 124, 14, height - 16
    levels = {k: float(v) for k, v in spec.levels.items() if v is not None and float(v) > 0}
    lo = min([b.low for b in bars] + list(levels.values()))
    hi = max([b.high for b in bars] + list(levels.values()))
    if hi <= lo:
        hi, lo = hi * 1.001 + 0.01, lo * 0.999
    pad = (hi - lo) * 0.06
    lo, hi = lo - pad, hi + pad

    def y_of(price: float) -> int:
        return int(round(bottom - (price - lo) / (hi - lo) * (bottom - top)))

    for i in range(1, 6):                                     # grid
        canvas.hline(left, right, top + (bottom - top) * i // 6, GRID)
    canvas.vline(right, top, bottom, AXIS)
    canvas.hline(left, right, bottom, AXIS)

    if "HIGH" in levels and "LOW" in levels:                  # the window's range as a light band edge
        canvas.hline(left, right, y_of(levels["HIGH"]), ZONE, dash=3)
        canvas.hline(left, right, y_of(levels["LOW"]), ZONE, dash=3)

    slot = (right - left - 4) / len(bars)
    body = max(1, int(slot * 0.6))
    for i, b in enumerate(bars):
        cx = int(left + 2 + slot * i + slot / 2)
        color = UP if b.close >= b.open else DOWN
        canvas.vline(cx, y_of(b.high), y_of(b.low), WICK)
        y0, y1 = y_of(max(b.open, b.close)), y_of(min(b.open, b.close))
        canvas.rect(cx - body // 2, y0, cx - body // 2 + body - 1, max(y0, y1), color)

    used: List[int] = []
    for name in ("TGT", "ENTRY", "EXIT", "SL", "HIGH", "LOW"):  # trade levels drawn last, on top of the candles
        if name not in levels:
            continue
        y = y_of(levels[name])
        color = _LEVEL_COLORS[name]
        if name in ("ENTRY", "SL", "TGT", "EXIT"):
            canvas.hline(left, right, y, color, dash=0 if name in ("ENTRY", "EXIT") else 6)
        label = f"{name} {_fmt(levels[name])}"
        ly = y - 5
        while any(abs(ly - u) < 12 for u in used):          # keep labels from overlapping
            ly += 12
        used.append(ly)
        canvas.rect(right + 3, ly - 1, width - 2, ly + 5 * SCALE, LABEL_BG)
        canvas.text(right + 4, ly, label, color)
    return canvas.png()


def bars_from_rows(rows: Sequence[object]) -> List[Bar]:
    """`md_candles` rows (or anything with open/high/low/close) as bars, oldest first as given."""
    out: List[Bar] = []
    for r in rows:
        try:
            out.append(Bar(float(getattr(r, "open")), float(getattr(r, "high")), float(getattr(r, "low")), float(getattr(r, "close"))))
        except (TypeError, ValueError):
            continue
    return out


def is_png(data: Optional[bytes]) -> bool:
    return bool(data) and bytes(data or b"")[:8] == b"\x89PNG\r\n\x1a\n"
