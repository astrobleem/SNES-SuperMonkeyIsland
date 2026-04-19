#!/usr/bin/env python3
"""Decode MI1 cost_005_room010.bin (the sparkle costume) into per-frame PNGs.

Format (empirically verified against ScummVM's ClassicCostumeRenderer for
MI1 VGA/talkie, format 0x58, 16-color):

  byte 0           numAnim (upper bound; actual anim count = this+1)
  byte 1           format (0x58 = 16 color)
  bytes 2..17      16-byte VGA palette (color index per costume-palette slot)
  bytes 18..49     16 × LE-word anim-command-table offsets (one per direction/
                   limb, many duplicate when unused)
  bytes 50..?      frame-offset table (LE words, one per frame; terminated when
                   the next pointer would overrun)
  per frame, at its offset:
      2B width   2B height   2B relX   2B relY   2B moveX   2B moveY   (12B)
      pixel data: column-major RLE, each byte = color(hi 4 bits) + count(lo 4 bits)

The pixel RLE runs column by column — the renderer draws a column of height
`_height` pixels, then advances to the next column. Count==0 means read the
next byte as an extended count (we don't hit this here but handle it).
"""
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tools.scumm.resource import parse_data_file

try:
    from PIL import Image
except ImportError:
    raise SystemExit("need Pillow")

COSTUME_FILE = Path("data/monkeypacks/talkie/monkey.001")
OUTDIR = Path("schwag/sparkle_frames")
OUTDIR.mkdir(parents=True, exist_ok=True)


def load_sparkle_costume():
    df = parse_data_file(str(COSTUME_FILE))
    room = df.rooms[10]
    # CLUT for VGA color lookup
    clut = next(s for s in room.room_sub_chunks if s.tag == "CLUT")
    vga_pal = [tuple(clut.data[i * 3:i * 3 + 3]) for i in range(256)]
    # Trailing COST chunks in the room's LFLF
    costs = room.get_trailing("COST")
    # cost_005 is the 6th (index 5) costume in the room's trailing list, by the
    # naming convention the SNES-port extractor uses. ScummVM uses global
    # costume resource IDs, but for our purposes we just want the 492-byte one.
    sparkle = next(c for c in costs if len(c.data) == 492)
    return sparkle.data, vga_pal


def decode_frame(data, off, vga_pal, cos_pal):
    """Decode a single frame starting at `off`. Returns (PIL.Image, meta)."""
    width, height, relX, relY, moveX, moveY = struct.unpack_from("<hhhhhh", data, off)
    if width <= 0 or height <= 0 or width > 64 or height > 64:
        return None, None
    px = off + 12
    # RLE column-major
    pixels = [[0] * width for _ in range(height)]
    col = 0
    row = 0
    while col < width:
        if px >= len(data):
            break
        b = data[px]
        px += 1
        color_idx = (b >> 4) & 0x0F
        run = b & 0x0F
        if run == 0:
            # Extended: next byte is the real count
            if px >= len(data):
                break
            run = data[px]
            px += 1
        while run > 0 and col < width:
            pixels[row][col] = color_idx
            row += 1
            if row >= height:
                row = 0
                col += 1
            run -= 1
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    pxmap = img.load()
    for y in range(height):
        for x in range(width):
            ci = pixels[y][x]
            if ci == 0:
                pxmap[x, y] = (0, 0, 0, 0)
            else:
                vga = cos_pal[ci]
                r, g, b = vga_pal[vga]
                pxmap[x, y] = (r, g, b, 255)
    meta = dict(width=width, height=height, relX=relX, relY=relY,
                moveX=moveX, moveY=moveY, bytes=px - off)
    return img, meta


def main():
    data, vga_pal = load_sparkle_costume()
    print(f"cost_005 length: {len(data)}")
    # Palette: 16 bytes at offset 2
    cos_pal = list(data[2:18])
    print(f"costume palette (VGA indices): {cos_pal}")

    # Instead of trying to decode the anim tables, scan the whole file for
    # byte positions that look like a CostumeInfo header: width + height
    # (both small positive), followed by tiny signed rel/move fields.
    unique = []
    seen = set()
    for p in range(18, len(data) - 12):
        width, height, relX, relY, moveX, moveY = struct.unpack_from("<hhhhhh", data, p)
        if not (1 <= width <= 32 and 1 <= height <= 32):
            continue
        # relX/relY for a centered sparkle are small (abs <= 16). moveX/moveY
        # are typically 0 for a static frame.
        if abs(relX) > 16 or abs(relY) > 16:
            continue
        if abs(moveX) > 4 or abs(moveY) > 4:
            continue
        # Very loose heuristic: the relXYY usually equals -(width//2) for a
        # centered frame — skip entries that don't look centered.
        if relX != -(width // 2) and relX != 0:
            continue
        if relY != -(height // 2) and relY != 0:
            continue
        if p not in seen:
            seen.add(p)
            unique.append(p)
    print(f"frame header candidates: {[hex(o) for o in unique]}")

    for i, off in enumerate(unique):
        img, meta = decode_frame(data, off, vga_pal, cos_pal)
        if img is None:
            continue
        print(f"  frame {i}: off=0x{off:x}  {meta['width']}x{meta['height']}  "
              f"rel=({meta['relX']},{meta['relY']})  mov=({meta['moveX']},{meta['moveY']})  "
              f"bytes={meta['bytes']}")
        # Save both native-size and 8x scaled for visibility
        img.save(OUTDIR / f"frame_{i:02d}.png")
        img.resize((img.width * 8, img.height * 8), Image.NEAREST).save(
            OUTDIR / f"frame_{i:02d}_8x.png")


if __name__ == "__main__":
    main()
