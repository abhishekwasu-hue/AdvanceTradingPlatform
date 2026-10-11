"""PW1: draws the PWA icons from the LogoMark geometry (src/components/Logo.tsx, a 34 x 34 tile). Run from frontend/:
`python3 scripts/gen-icons.py` (needs Pillow). Writes public/icons/*.png; the PNGs are committed, so the build needs no Python.

"any" icons are the rounded tile itself; "maskable" icons fill the whole square with the brand gradient and keep the
mark inside the central 60 % (the platforms crop to a circle or squircle of at least 80 %)."""
from PIL import Image, ImageDraw

TOP, BOTTOM = (0x3B, 0x82, 0xF6), (0x1D, 0x4E, 0xD8)
UP = (0x22, 0xC5, 0x5E)
# (x, y, w, h, colour, alpha) in the 34-unit LogoMark grid
BARS = [
    (8, 17, 3.4, 10, (255, 255, 255), 0.55), (8, 13.5, 3.4, 3, (255, 255, 255), 0.55),
    (15.3, 11, 3.4, 16, (255, 255, 255), 0.78), (15.3, 8, 3.4, 2.4, (255, 255, 255), 0.78),
    (22.6, 6, 3.4, 21, UP, 1.0),
]


def gradient(size: int) -> Image.Image:
    img = Image.new("RGB", (size, size))
    px = img.load()
    for y in range(size):
        for x in range(size):
            t = (x + y) / (2 * (size - 1))
            px[x, y] = tuple(round(a + (b - a) * t) for a, b in zip(TOP, BOTTOM))
    return img


def icon(size: int, maskable: bool) -> Image.Image:
    bg = gradient(size).convert("RGBA")
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    mask = Image.new("L", (size, size), 0)
    if maskable:
        mask.paste(255, (0, 0, size, size))
        scale, off = size * 0.6 / 34, size * 0.2
    else:
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, size - 1, size - 1), radius=size * 9 / 34, fill=255)
        scale, off = size / 34, 0.0
    out.paste(bg, (0, 0), mask)
    layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    for x, y, w, h, colour, alpha in BARS:
        box = (off + x * scale, off + y * scale, off + (x + w) * scale, off + (y + h) * scale)
        d.rounded_rectangle(box, radius=1.2 * scale if h > 3 else 0, fill=(*colour, round(255 * alpha)))
    return Image.alpha_composite(out, layer)


if __name__ == "__main__":
    for size in (192, 512):
        icon(size, False).save(f"public/icons/icon-{size}.png", optimize=True)
        icon(size, True).save(f"public/icons/maskable-{size}.png", optimize=True)
    icon(180, True).convert("RGB").save("public/icons/apple-touch-icon.png", optimize=True)  # iOS: opaque, its own mask
