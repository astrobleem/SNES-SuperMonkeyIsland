#!/usr/bin/env python3
"""Costume transparency editor — export/import workflow for manual editing.

Usage:
  Export all costume frames as editable PNGs:
    python tools/costume_transparency_editor.py export

  Re-import edited PNGs back into CHR tiles:
    python tools/costume_transparency_editor.py import

Workflow:
  1. Run 'export' — creates PNGs in data/costume_transparency_edit/
  2. Open any PNG in Paint (or any editor)
  3. Transparent pixels show as MAGENTA (#FF00FF)
  4. Paint magenta over areas you want transparent
  5. Save the PNG (keep the filename!)
  6. Run 'import' — re-encodes edited PNGs into CHR tiles
  7. Rebuild: make clean && make

Only costumes that have existing converted data are exported.
Only PNGs that were modified (newer than the CHR file) are reimported.
"""

import sys
import os
import json
import struct
from pathlib import Path

try:
    from PIL import Image
    import numpy as np
except ImportError:
    print("Requires: pip install Pillow numpy")
    sys.exit(1)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONVERTED_DIR = PROJECT_ROOT / "data" / "snes_converted" / "costumes"
EDIT_DIR = PROJECT_ROOT / "data" / "costume_transparency_edit"
MAGENTA = (255, 0, 255)
TILE_SIZE = 8
BYTES_PER_TILE = 32


def decode_4bpp_tile(data, offset):
    """Decode a 32-byte SNES 4bpp tile into 8x8 pixel indices."""
    pixels = []
    for row in range(8):
        bp0 = data[offset + row * 2]
        bp1 = data[offset + row * 2 + 1]
        bp2 = data[offset + 16 + row * 2]
        bp3 = data[offset + 16 + row * 2 + 1]
        row_pixels = []
        for col in range(7, -1, -1):
            px = ((bp0 >> col) & 1) | (((bp1 >> col) & 1) << 1) | \
                 (((bp2 >> col) & 1) << 2) | (((bp3 >> col) & 1) << 3)
            row_pixels.append(px)
        pixels.append(row_pixels)
    return pixels


def encode_4bpp_tile(pixels_8x8):
    """Encode 8x8 pixel indices (0-15) to 32-byte SNES 4bpp planar."""
    data = bytearray(32)
    for row in range(8):
        bp0 = bp1 = bp2 = bp3 = 0
        for col in range(8):
            px = pixels_8x8[row][col] & 0xF
            bit = 7 - col
            bp0 |= ((px >> 0) & 1) << bit
            bp1 |= ((px >> 1) & 1) << bit
            bp2 |= ((px >> 2) & 1) << bit
            bp3 |= ((px >> 3) & 1) << bit
        data[row * 2] = bp0
        data[row * 2 + 1] = bp1
        data[16 + row * 2] = bp2
        data[16 + row * 2 + 1] = bp3
    return bytes(data)


def bgr555_to_rgb(color):
    """Convert BGR555 to (R, G, B) tuple."""
    r = (color & 0x1F) * 8
    g = ((color >> 5) & 0x1F) * 8
    b = ((color >> 10) & 0x1F) * 8
    return (r, g, b)


def export_costumes():
    """Export all costume frames as editable PNGs."""
    EDIT_DIR.mkdir(parents=True, exist_ok=True)
    count = 0

    for cost_dir in sorted(CONVERTED_DIR.iterdir()):
        if not cost_dir.is_dir():
            continue
        cost_name = cost_dir.name

        # Find all pic CHR + JSON files (body and head)
        for json_file in sorted(cost_dir.glob("*.json")):
            pic_name = json_file.stem  # e.g., "pic00" or "head_pic00"
            chr_file = cost_dir / f"{pic_name}.chr"
            if not chr_file.exists():
                continue

            with open(json_file) as f:
                meta = json.load(f)

            width = meta["width"]
            height = meta["height"]
            palette = meta.get("palette", [])
            oam_count = meta.get("oam_entries", 0)

            # Build RGB palette from BGR555 strings
            rgb_palette = [MAGENTA]  # index 0 = transparent = magenta
            for i in range(1, 16):
                if i < len(palette):
                    bgr = int(palette[i], 16)
                    rgb_palette.append(bgr555_to_rgb(bgr))
                else:
                    rgb_palette.append((0, 0, 0))

            # Read OAM data to get tile positions
            oam_file = cost_dir / f"{pic_name}.oam"
            if not oam_file.exists():
                continue
            oam_data = oam_file.read_bytes()
            entry_count = oam_data[0]

            # Read CHR data
            chr_data = chr_file.read_bytes()
            num_tiles = len(chr_data) // BYTES_PER_TILE

            # Create image
            img = Image.new("RGB", (width, height), MAGENTA)
            img_pixels = img.load()

            # Place tiles using OAM entries
            rel_x = meta.get("rel_x", 0)
            rel_y = meta.get("rel_y", 0)

            for i in range(min(entry_count, oam_count)):
                off = 6 + i * 4  # 6-byte header + 4 bytes per entry
                if off + 4 > len(oam_data):
                    break
                dx = oam_data[off]
                dy = oam_data[off + 1]
                tile_id = oam_data[off + 2]
                flags = oam_data[off + 3]

                if dx >= 128:
                    dx -= 256
                if dy >= 128:
                    dy -= 256

                # Tile position relative to frame origin
                tx = dx - rel_x
                ty = dy - rel_y

                if tile_id >= num_tiles:
                    continue

                tile_pixels = decode_4bpp_tile(chr_data, tile_id * BYTES_PER_TILE)

                for py in range(8):
                    for px in range(8):
                        fx = tx + px
                        fy = ty + py
                        if 0 <= fx < width and 0 <= fy < height:
                            idx = tile_pixels[py][px]
                            if idx < len(rgb_palette):
                                img_pixels[fx, fy] = rgb_palette[idx]

            # Save
            out_path = EDIT_DIR / f"{cost_name}_{pic_name}.png"
            img.save(str(out_path))
            count += 1

    print(f"Exported {count} frames to {EDIT_DIR}/")
    print("Edit in Paint: paint MAGENTA (#FF00FF) over areas to make transparent.")
    print("Then run: python tools/costume_transparency_editor.py import")


def import_costumes():
    """Re-import edited PNGs back into CHR tile data."""
    if not EDIT_DIR.exists():
        print(f"No edit directory found: {EDIT_DIR}")
        return

    count = 0
    for png_file in sorted(EDIT_DIR.glob("*.png")):
        # Parse filename: cost_NNN_picNN.png or cost_NNN_head_picNN.png
        name = png_file.stem
        parts = name.split("_", 2)  # cost, NNN, picNN or head_picNN
        if len(parts) < 3:
            continue
        cost_name = f"{parts[0]}_{parts[1]}"
        pic_name = parts[2]

        cost_dir = CONVERTED_DIR / cost_name
        chr_file = cost_dir / f"{pic_name}.chr"
        json_file = cost_dir / f"{pic_name}.json"
        oam_file = cost_dir / f"{pic_name}.oam"

        if not chr_file.exists() or not json_file.exists() or not oam_file.exists():
            continue

        # Only reimport if PNG is newer than CHR
        if png_file.stat().st_mtime <= chr_file.stat().st_mtime:
            continue

        with open(json_file) as f:
            meta = json.load(f)

        width = meta["width"]
        height = meta["height"]
        palette = meta.get("palette", [])
        rel_x = meta.get("rel_x", 0)
        rel_y = meta.get("rel_y", 0)

        # Build RGB palette for reverse lookup
        rgb_palette = [MAGENTA]
        for i in range(1, 16):
            if i < len(palette):
                bgr = int(palette[i], 16)
                rgb_palette.append(bgr555_to_rgb(bgr))
            else:
                rgb_palette.append((0, 0, 0))

        # Build reverse color map: RGB -> palette index
        color_map = {}
        for idx, rgb in enumerate(rgb_palette):
            color_map[rgb] = idx
        # Magenta variants → transparent
        for mg in [(255, 0, 255), (254, 0, 254), (253, 0, 253)]:
            color_map[mg] = 0

        # Read edited image
        img = Image.open(str(png_file)).convert("RGB")
        if img.size != (width, height):
            print(f"  SKIP {name}: size mismatch ({img.size} vs {width}x{height})")
            continue

        # Read OAM data
        oam_data = oam_file.read_bytes()
        entry_count = oam_data[0]

        # Read existing CHR data
        chr_data = bytearray(chr_file.read_bytes())
        num_tiles = len(chr_data) // BYTES_PER_TILE

        # For each OAM entry, read tile pixels from the edited image
        for i in range(entry_count):
            off = 6 + i * 4
            if off + 4 > len(oam_data):
                break
            dx = oam_data[off]
            dy = oam_data[off + 1]
            tile_id = oam_data[off + 2]

            if dx >= 128:
                dx -= 256
            if dy >= 128:
                dy -= 256

            tx = dx - rel_x
            ty = dy - rel_y

            if tile_id >= num_tiles:
                continue

            # Read 8x8 tile from edited image
            tile_pixels = [[0] * 8 for _ in range(8)]
            for py in range(8):
                for px in range(8):
                    fx = tx + px
                    fy = ty + py
                    if 0 <= fx < width and 0 <= fy < height:
                        rgb = img.getpixel((fx, fy))
                        # Find closest palette index
                        if rgb in color_map:
                            tile_pixels[py][px] = color_map[rgb]
                        elif abs(rgb[0] - 255) < 10 and rgb[1] < 10 and abs(rgb[2] - 255) < 10:
                            tile_pixels[py][px] = 0  # close to magenta = transparent
                        else:
                            # Find nearest color
                            best_idx = 0
                            best_dist = 999999
                            for idx, pal_rgb in enumerate(rgb_palette):
                                dist = sum((a - b) ** 2 for a, b in zip(rgb, pal_rgb))
                                if dist < best_dist:
                                    best_dist = dist
                                    best_idx = idx
                            tile_pixels[py][px] = best_idx

            # Re-encode tile
            new_tile = encode_4bpp_tile(tile_pixels)
            chr_data[tile_id * BYTES_PER_TILE:(tile_id + 1) * BYTES_PER_TILE] = new_tile

        # Write updated CHR
        chr_file.write_bytes(bytes(chr_data))
        count += 1
        print(f"  Imported {name}")

    if count > 0:
        print(f"\nImported {count} edited frame(s).")
        print("Now rebuild: wsl -e bash -lc 'cd /mnt/e/gh/SNES-SuperMonkeyIsland && make clean && make'")
    else:
        print("No modified PNGs found (edit a PNG and save it, then run import again).")


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in ("export", "import"):
        print("Usage: python tools/costume_transparency_editor.py [export|import]")
        sys.exit(1)

    if sys.argv[1] == "export":
        export_costumes()
    else:
        import_costumes()
