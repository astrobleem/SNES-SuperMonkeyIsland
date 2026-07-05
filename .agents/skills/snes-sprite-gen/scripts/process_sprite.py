#!/usr/bin/env python3
"""
SNES Sprite Processing Pipeline
Converts an SVG or image into SNES-compliant sprite outputs.

Outputs:
  <name>.png          - 1:1 native resolution
  <name>_preview.png  - 8x scaled, nearest-neighbor
  <name>_indexed.png  - Mode-P indexed PNG (tilemap-ready)
  <name>.pal          - JASC-PAL, 16 entries (index 0 = transparent)
  <name>_swatch.png   - Horizontal palette swatch
"""

import argparse
import io
import os
import sys
from pathlib import Path

import numpy as np
from PIL import Image


def round_to_bgr555(value: int) -> int:
    """Snap an 8-bit color channel to 5-bit (BGR555) precision."""
    return min(248, (value >> 3) << 3)


def snap_palette_to_bgr555(palette_flat: list[int]) -> list[int]:
    """Round all palette RGB values to BGR555 precision."""
    return [round_to_bgr555(v) for v in palette_flat]


def rasterize_svg(svg_path: str, width: int, height: int) -> Image.Image:
    """Rasterize an SVG to RGBA at exact pixel dimensions."""
    try:
        import cairosvg
    except ImportError:
        sys.exit("cairosvg not found. Run: pip install cairosvg --break-system-packages")

    png_bytes = cairosvg.svg2png(url=str(svg_path), output_width=width, output_height=height)
    return Image.open(io.BytesIO(png_bytes)).convert("RGBA")


def load_image(input_path: str, size: int) -> Image.Image:
    """Load any image and resize to target sprite size."""
    img = Image.open(input_path).convert("RGBA")
    if img.size == (size, size):
        return img
    # Use nearest-neighbor for pixel art (small integer-scale sources),
    # Lanczos for photos/artwork being downscaled.
    src_max = max(img.size)
    resample = Image.NEAREST if src_max <= size * 2 else Image.LANCZOS
    return img.resize((size, size), resample=resample)


def process_image(img: Image.Image) -> tuple[Image.Image, list[int]]:
    """
    Quantize an RGBA image to SNES 4bpp constraints.

    Returns:
        indexed_img: PIL Image in mode 'P', index 0 = transparent
        palette_rgb: flat list of 15×3 RGB values (indices 1–15)
    """
    # Separate alpha mask
    alpha = np.array(img.split()[3])
    rgb_img = img.convert("RGB")

    # Quantize to 15 colors (we reserve index 0 for transparency)
    quantized = rgb_img.quantize(colors=15, method=Image.Quantize.MEDIANCUT, dither=0)
    raw_palette = quantized.getpalette()[:15 * 3]
    palette_snapped = snap_palette_to_bgr555(raw_palette)

    # Shift all color indices up by 1 to make room for transparent at 0
    arr = np.array(quantized, dtype=np.uint8)
    arr = (arr + 1).clip(0, 15).astype(np.uint8)

    # Assign index 0 to fully-transparent pixels
    arr[alpha < 128] = 0

    # Build full 256-entry palette: index 0 = (0,0,0), indices 1–15 = snapped colors
    full_palette = [0, 0, 0] + palette_snapped + [0] * (240 * 3)

    indexed = Image.fromarray(arr, mode="P")
    indexed.putpalette(full_palette)
    return indexed, palette_snapped


def save_pal(palette_rgb: list[int], path: str) -> None:
    """Save a 16-entry JASC-PAL file (index 0 = transparent black)."""
    lines = ["JASC-PAL", "0100", "16", "0 0 0"]
    for i in range(0, len(palette_rgb), 3):
        r, g, b = palette_rgb[i], palette_rgb[i + 1], palette_rgb[i + 2]
        lines.append(f"{r} {g} {b}")
    Path(path).write_text("\n".join(lines) + "\n")


def save_swatch(palette_rgb: list[int], path: str) -> None:
    """Save a horizontal swatch showing all palette colors (up to 15)."""
    block = 24
    n = len(palette_rgb) // 3
    swatch = Image.new("RGB", (block * n, block), (30, 30, 30))
    for i in range(n):
        r = palette_rgb[i * 3]
        g = palette_rgb[i * 3 + 1]
        b = palette_rgb[i * 3 + 2]
        swatch.paste(Image.new("RGB", (block, block), (r, g, b)), (i * block, 0))
    swatch.save(path)


def save_preview(img: Image.Image, path: str, scale: int = 8) -> None:
    """Save a nearest-neighbor scaled preview."""
    w, h = img.size
    preview = img.resize((w * scale, h * scale), resample=Image.NEAREST)
    preview.save(path)


def main():
    parser = argparse.ArgumentParser(description="SNES Sprite Processing Pipeline")
    parser.add_argument("--input", required=True, help="Path to SVG or image file")
    parser.add_argument("--size", type=int, required=True,
                        choices=[8, 16, 32, 64], help="Sprite size in pixels")
    parser.add_argument("--output-dir", default=".", help="Directory for output files")
    parser.add_argument("--name", default="sprite", help="Base name for output files")
    parser.add_argument("--scale", type=int, default=8, help="Preview scale factor (default: 8)")
    args = parser.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)
    base = Path(args.output_dir) / args.name

    # Load / rasterize
    ext = Path(args.input).suffix.lower()
    if ext == ".svg":
        print(f"Rasterizing SVG at {args.size}×{args.size}...")
        img = rasterize_svg(args.input, args.size, args.size)
    else:
        print(f"Loading image and resizing to {args.size}×{args.size}...")
        img = load_image(args.input, args.size)

    # Quantize to SNES 4bpp
    print("Quantizing to 15 colors (SNES 4bpp)...")
    indexed, palette_rgb = process_image(img)

    # Save 1:1 native PNG (RGB with alpha from indexed)
    native_rgba = indexed.convert("RGBA")
    # Re-apply transparency: index 0 pixels become fully transparent
    idx_arr = np.array(indexed)
    rgba_arr = np.array(native_rgba)
    rgba_arr[idx_arr == 0, 3] = 0
    native_out = Image.fromarray(rgba_arr, mode="RGBA")
    native_out.save(str(base) + ".png")
    print(f"Saved: {base}.png  (1:1 native)")

    # Save 8x preview
    save_preview(native_out, str(base) + "_preview.png", scale=args.scale)
    print(f"Saved: {base}_preview.png  ({args.scale}x preview)")

    # Save indexed PNG (tilemap-ready)
    indexed.save(str(base) + "_indexed.png")
    print(f"Saved: {base}_indexed.png  (indexed, tilemap-ready)")

    # Save .pal
    save_pal(palette_rgb, str(base) + ".pal")
    print(f"Saved: {base}.pal  (JASC-PAL, 16 entries)")

    # Save swatch
    save_swatch(palette_rgb, str(base) + "_swatch.png")
    print(f"Saved: {base}_swatch.png  (palette swatch)")

    print("\nDone.")


if __name__ == "__main__":
    main()
