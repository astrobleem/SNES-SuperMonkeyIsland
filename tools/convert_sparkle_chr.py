#!/usr/bin/env python3
"""Generate CHR + sub-palette blobs for the LucasArts logo sparkle OAM sprites.

Emits:
  data/logo_sparkle.chr  — 32 tiles x 32 bytes = 1024 bytes, all pixels = color-idx 1
                           (4bpp SNES, planar). Covers 16x16 sprites at CHR slot 0.
  data/logo_sparkle.pal  — 16 words (32 bytes):
                             slot 0  = 0x0000 (transparent)
                             slot 1  = color 88 from room 10 PC palette, BGR555
                             slots 2..15 = 0x0000

PC color 88 is RGB(252,84,252) in the MI1 logo room palette.
"""
from pathlib import Path
import struct

ROOT = Path(__file__).resolve().parent.parent
PAL_SRC = ROOT / "data" / "scumm_extracted" / "rooms" / "room_010_logo" / "palette.bin"
CHR_OUT = ROOT / "data" / "logo_sparkle.chr"
PAL_OUT = ROOT / "data" / "logo_sparkle.pal"

NUM_TILES = 1        # one 8x8 solid-magenta tile; sparkles use 8x8 hw sprites.
                     # 5 sparkles x (2x4 grid of 8x8) = 40 OAM entries, all share this tile.
BYTES_PER_TILE = 32  # 4bpp SNES tile
SPARKLE_COLOR_IDX = 88


def rgb_to_bgr555(r: int, g: int, b: int) -> int:
    return ((b >> 3) << 10) | ((g >> 3) << 5) | (r >> 3)


def make_solid_tile_4bpp(color_idx: int) -> bytes:
    """Build a single 8x8 tile where every pixel = color_idx (0..15).

    SNES 4bpp: 32 bytes per tile. Bytes 0..15 are bitplane pairs 0/1 interleaved,
    bytes 16..31 are bitplane pairs 2/3. For each 8x8 tile, row r's planes are
    at offsets r*2 (plane 0), r*2+1 (plane 1), r*2+16 (plane 2), r*2+17 (plane 3).
    For a solid-color fill, each row's plane byte is 0xFF if that bit is set, else 0.
    """
    b0 = 0xFF if (color_idx & 0x01) else 0x00
    b1 = 0xFF if (color_idx & 0x02) else 0x00
    b2 = 0xFF if (color_idx & 0x04) else 0x00
    b3 = 0xFF if (color_idx & 0x08) else 0x00
    row_low = bytes([b0, b1]) * 8   # rows 0..7, planes 0+1
    row_high = bytes([b2, b3]) * 8  # rows 0..7, planes 2+3
    return row_low + row_high


def main():
    pal = PAL_SRC.read_bytes()
    if len(pal) < (SPARKLE_COLOR_IDX + 1) * 3:
        raise SystemExit(f"palette.bin too small: {len(pal)}")
    r = pal[SPARKLE_COLOR_IDX * 3 + 0]
    g = pal[SPARKLE_COLOR_IDX * 3 + 1]
    b = pal[SPARKLE_COLOR_IDX * 3 + 2]
    bgr555 = rgb_to_bgr555(r, g, b)
    print(f"sparkle color 88: RGB({r},{g},{b}) -> BGR555 0x{bgr555:04X}")

    tile = make_solid_tile_4bpp(color_idx=1)
    chr_blob = tile * NUM_TILES
    CHR_OUT.write_bytes(chr_blob)
    print(f"wrote {CHR_OUT.relative_to(ROOT)} ({len(chr_blob)} bytes)")

    sub_pal = [0x0000] * 16
    sub_pal[1] = bgr555
    pal_blob = b"".join(struct.pack("<H", w) for w in sub_pal)
    PAL_OUT.write_bytes(pal_blob)
    print(f"wrote {PAL_OUT.relative_to(ROOT)} ({len(pal_blob)} bytes)")


if __name__ == "__main__":
    main()
