"""M95 front-door images: the raw captures (FRONT_RAW, 720 x 900) to public/front/<slug>.png, each under 120 KB.

Palette-quantised PNG (the screens are flat UI colours); the colour count steps down only when a file is still too big.
  python3 scripts/front-images/shrink.py [raw dir]
"""
import pathlib
import sys

from PIL import Image

SLUGS = ["copilot", "option-chain", "find-setups", "trading-bot", "journal", "daily-report"]
LIMIT = 120 * 1024
RAW = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/front-raw")
OUT = pathlib.Path(__file__).resolve().parents[2] / "public" / "front"


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for slug in SLUGS:
        img = Image.open(RAW / f"{slug}.png").convert("RGB")
        if img.size != (720, 900):
            raise SystemExit(f"{slug}: {img.size}, expected 720 x 900")
        for colours in (256, 128, 96, 64):
            out = OUT / f"{slug}.png"
            img.quantize(colors=colours, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE).save(out, optimize=True)
            if out.stat().st_size < LIMIT:
                break
        size = out.stat().st_size
        print(f"{slug}.png {size // 1024} KB ({colours} colours)")
        if size >= LIMIT:
            raise SystemExit(f"{slug}: still {size} bytes")


if __name__ == "__main__":
    main()
