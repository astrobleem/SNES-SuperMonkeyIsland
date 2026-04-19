#!/usr/bin/env python3
"""Convert MI1 sparkle costume (cost_005_room010) frames to SNES 4bpp CHR.

Picks 4 frames (sizes 7x7, 9x9, 11x11, 13x13) and centers each in a 16x16
SNES hw-sprite block. Packs them sequentially so that tile_id 0, 2, 4, 6
point at frames 0..3 (each 16x16 = 2x2 tiles = 4 tiles per frame).

Also emits a 16-entry OBJ sub-palette matching the costume's 16-color
palette, converted to SNES BGR555.
"""
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tools.scumm.resource import parse_data_file

ROOT = Path(__file__).resolve().parent.parent
COSTUME_FILE = ROOT / "data" / "monkeypacks" / "talkie" / "monkey.001"
CHR_OUT = ROOT / "data" / "logo_sparkle.chr"
PAL_OUT = ROOT / "data" / "logo_sparkle.pal"

# Frame selection: which costume frames to pack.
# The costume has 6 frames (5, 7, 9, 11, 13, 19). 19 doesn't fit a 16x16
# sprite cleanly and looks cluttered; 5 is too small to stand out. We pack
# 7, 9, 11, 13 — the core twinkle grow/shrink range.
SELECTED_FRAMES_BY_SIZE = [7, 9, 11, 13]


def rgb_to_bgr555(r, g, b):
    return ((b >> 3) << 10) | ((g >> 3) << 5) | (r >> 3)


def decode_frame(data, off):
    """Returns (width, height, 2D list of color indices 0..15) or None."""
    width, height, relX, relY, moveX, moveY = struct.unpack_from("<hhhhhh", data, off)
    if not (1 <= width <= 32 and 1 <= height <= 32):
        return None
    px = off + 12
    pixels = [[0] * width for _ in range(height)]
    col, row = 0, 0
    while col < width and px < len(data):
        b = data[px]
        px += 1
        color = (b >> 4) & 0x0F
        run = b & 0x0F
        if run == 0:
            run = data[px]
            px += 1
        while run > 0 and col < width:
            pixels[row][col] = color
            row += 1
            if row >= height:
                row = 0
                col += 1
            run -= 1
    return width, height, pixels


def scan_frames(data):
    """Return [(offset, width, height), ...] for all valid frames in the file."""
    out = []
    seen = set()
    for p in range(18, len(data) - 12):
        w, h, rx, ry, mx, my = struct.unpack_from("<hhhhhh", data, p)
        if not (1 <= w <= 32 and 1 <= h <= 32):
            continue
        if abs(rx) > 16 or abs(ry) > 16:
            continue
        if abs(mx) > 4 or abs(my) > 4:
            continue
        if rx != -(w // 2) and rx != 0:
            continue
        if ry != -(h // 2) and ry != 0:
            continue
        if p not in seen:
            seen.add(p)
            out.append((p, w, h))
    return out


def pack_frame_into_16x16(pixels, fw, fh):
    """Center the frame in a 16x16 grid, return 16 rows × 16 cols of indices."""
    grid = [[0] * 16 for _ in range(16)]
    ox = (16 - fw) // 2
    oy = (16 - fh) // 2
    for y in range(fh):
        for x in range(fw):
            grid[oy + y][ox + x] = pixels[y][x]
    return grid


def encode_8x8_tile_4bpp(rows8):
    """rows8 = list of 8 lists of 8 color indices (0..15). Returns 32 bytes
    in SNES 4bpp planar format (planes 0+1 interleaved, then planes 2+3)."""
    out = bytearray(32)
    for r in range(8):
        b0 = b1 = b2 = b3 = 0
        for c in range(8):
            v = rows8[r][c] & 0x0F
            if v & 0x01: b0 |= (0x80 >> c)
            if v & 0x02: b1 |= (0x80 >> c)
            if v & 0x04: b2 |= (0x80 >> c)
            if v & 0x08: b3 |= (0x80 >> c)
        out[r * 2] = b0
        out[r * 2 + 1] = b1
        out[16 + r * 2] = b2
        out[16 + r * 2 + 1] = b3
    return bytes(out)


def pack_16x16_frame_as_2x2_tiles(grid16):
    """Return 4 consecutive 32-byte tiles in SNES 16x16 sprite order
    (top-left, top-right, bottom-left, bottom-right)."""
    # Top-left 8x8
    tl = [row[0:8] for row in grid16[0:8]]
    # Top-right
    tr = [row[8:16] for row in grid16[0:8]]
    # Bottom-left
    bl = [row[0:8] for row in grid16[8:16]]
    # Bottom-right
    br = [row[8:16] for row in grid16[8:16]]
    # SNES 16x16 sprite layout: tile N (top-left), N+1 (top-right),
    # N+16 (bottom-left), N+17 (bottom-right). Since N+16 is far away in
    # a linear CHR ROM, we can't keep them contiguous at N..N+3.
    # Workaround: upload 32 consecutive tiles where tiles 0..15 are the
    # top row of each frame and tiles 16..31 are the bottom row. Then
    # 16x16 sprite at tile N uses N, N+1 from top row and N+16, N+17 from
    # bottom row — the exact hardware expectation.
    return (tl, tr, bl, br)


def main():
    df = parse_data_file(str(COSTUME_FILE))
    room = df.rooms[10]
    clut = next(s for s in room.room_sub_chunks if s.tag == "CLUT")
    vga_pal = [tuple(clut.data[i * 3:i * 3 + 3]) for i in range(256)]
    costs = room.get_trailing("COST")
    sparkle = next(c for c in costs if len(c.data) == 492)
    data = sparkle.data

    cos_pal_vga = list(data[2:18])
    print(f"costume palette: {cos_pal_vga}")

    # Build SNES sub-palette: slot 0 transparent, slots 1..15 from costume
    sub_pal = [0x0000] * 16
    for i in range(1, 16):
        r, g, b = vga_pal[cos_pal_vga[i]]
        sub_pal[i] = rgb_to_bgr555(r, g, b)
    pal_blob = b"".join(struct.pack("<H", w) for w in sub_pal)
    PAL_OUT.write_bytes(pal_blob)
    print(f"wrote {PAL_OUT.relative_to(ROOT)} ({len(pal_blob)} B)")

    # Find frames
    frames = scan_frames(data)
    by_size = {w: (off, w, h) for (off, w, h) in frames if w == h}
    print(f"found frames: {[(w,h) for _,w,h in frames]}")

    # Decode selected frames
    packed_frames = []
    for sz in SELECTED_FRAMES_BY_SIZE:
        if sz not in by_size:
            raise SystemExit(f"frame size {sz}x{sz} not found in costume")
        off, w, h = by_size[sz]
        dw, dh, px = decode_frame(data, off)
        grid = pack_frame_into_16x16(px, dw, dh)
        packed_frames.append(grid)

    # Build CHR: row-major 16 tiles per row, 2 rows (top + bottom halves
    # of each 16x16 sprite). Each frame occupies 2 consecutive tiles in
    # the top row and 2 in the bottom row (+16 offset).
    # Tile layout for N frames:
    #   top row tiles:    F0-TL F0-TR F1-TL F1-TR F2-TL F2-TR F3-TL F3-TR ...
    #   bottom row tiles: F0-BL F0-BR F1-BL F1-BR F2-BL F2-BR F3-BL F3-BR ...
    # Total 32 tiles (16 per row) × 32 bytes = 1024 bytes CHR.
    top_row = [None] * 16
    bot_row = [None] * 16
    for i, grid in enumerate(packed_frames):
        tl, tr, bl, br = pack_16x16_frame_as_2x2_tiles(grid)
        top_row[i * 2 + 0] = tl
        top_row[i * 2 + 1] = tr
        bot_row[i * 2 + 0] = bl
        bot_row[i * 2 + 1] = br
    # Empty slots fill with transparent
    empty = [[0] * 8 for _ in range(8)]
    for i in range(16):
        if top_row[i] is None:
            top_row[i] = empty
        if bot_row[i] is None:
            bot_row[i] = empty

    chr_blob = bytearray()
    for rows in top_row:
        chr_blob += encode_8x8_tile_4bpp(rows)
    for rows in bot_row:
        chr_blob += encode_8x8_tile_4bpp(rows)
    CHR_OUT.write_bytes(bytes(chr_blob))
    print(f"wrote {CHR_OUT.relative_to(ROOT)} ({len(chr_blob)} B)")

    print(f"\nFrame tile IDs (for OAM): 0, 2, 4, 6 → frames {SELECTED_FRAMES_BY_SIZE}")
    print("Each frame is a 16x16 sprite; set size bit in oamTable1.")


if __name__ == "__main__":
    main()
