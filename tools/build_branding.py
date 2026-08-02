"""Generate release branding assets from the user-supplied TAO source image."""
from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageOps

ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)


def build_branding(source: Path, output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    with Image.open(source) as original:
        rgb = original.convert("RGB")
        square = ImageOps.fit(rgb, (1024, 1024), method=Image.Resampling.LANCZOS, centering=(0.52, 0.5))
        png_path = output_dir / "tao-icon.png"
        ico_path = output_dir / "tao.ico"
        square.save(png_path, format="PNG", optimize=True)
        square.save(ico_path, format="ICO", sizes=[(size, size) for size in ICON_SIZES])
    return png_path, ico_path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=Path("assets/branding/tao-source.png"))
    parser.add_argument("--output", type=Path, default=Path("build/branding"))
    args = parser.parse_args()
    png_path, ico_path = build_branding(args.source, args.output)
    print(png_path)
    print(ico_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
