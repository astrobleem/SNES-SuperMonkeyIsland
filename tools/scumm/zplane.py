"""SCUMM v5 ZP01/ZP02 zplane (foreground mask) decoder.

Each ZP chunk contains a per-pixel foreground mask aligned with the room
background. Pixels with the bit set render IN FRONT of actors; pixels
with the bit clear render behind.

Layout (SCUMM v5, not OLD_BUNDLE/OLD256/SMALL_HEADER):
  chunk header (8 bytes) — standard IFF tag + BE size
  stripe_offsets[num_stripes] — LE uint16, offset from chunk start to
                                 stripe data (0 = all-zero stripe)
  stripe_data...

Each stripe encodes 8 pixels wide × height rows as 1 bit per pixel
(height bytes per stripe after decompression), using a simple RLE:

  while rows_remaining:
    b = *src++
    if b & 0x80:
      count = b & 0x7F
      c = *src++
      emit c `count` times          # each c is 8 pixel-bits for one row
    else:
      count = b
      emit (*src++) `count` times   # each byte is 8 pixel-bits for one row

Reference: ScummVM engines/scumm/gfx.cpp Gdi::decompressMaskImg.
"""

import struct


def decompress_mask_stripe(src: bytes, pos: int, height: int) -> bytes:
    """Decompress one ZP stripe into `height` bytes (one byte per row, 8 pixels each).

    Returns (rows, bytes_consumed).
    """
    out = bytearray()
    while len(out) < height:
        b = src[pos]
        pos += 1
        if b & 0x80:
            count = b & 0x7F
            c = src[pos]
            pos += 1
            for _ in range(count):
                if len(out) >= height:
                    break
                out.append(c)
        else:
            count = b
            for _ in range(count):
                if len(out) >= height:
                    break
                out.append(src[pos])
                pos += 1
    return bytes(out[:height])


def decode_zplane(zp_data: bytes, width: int, height: int) -> list:
    """Decode a ZP chunk payload into a 2D binary mask.

    Args:
        zp_data: raw ZP chunk INCLUDING 8-byte header (tag + BE size)
        width: room width in pixels (must be multiple of 8)
        height: room height in pixels

    Returns:
        2D list [y][x] of ints (0 = background, 1 = foreground).
        Empty list if zp_data is too short.
    """
    if len(zp_data) < 8:
        return []

    num_stripes = width // 8
    # Offsets follow the 8-byte chunk header.
    offsets_base = 8
    if len(zp_data) < offsets_base + num_stripes * 2:
        return []

    mask = [[0] * width for _ in range(height)]

    for stripe_idx in range(num_stripes):
        off = struct.unpack_from('<H', zp_data, offsets_base + stripe_idx * 2)[0]
        if off == 0:
            continue  # all-zero stripe
        if off >= len(zp_data):
            continue

        rows = decompress_mask_stripe(zp_data, off, height)
        x_base = stripe_idx * 8
        for y in range(min(height, len(rows))):
            byte = rows[y]
            # ScummVM draws bits MSB-first: bit 7 = leftmost pixel of stripe.
            for bx in range(8):
                if byte & (0x80 >> bx):
                    if x_base + bx < width:
                        mask[y][x_base + bx] = 1

    return mask


def mask_to_tile_priority(mask: list, width: int, height: int,
                          tile_size: int = 8) -> list:
    """Reduce a per-pixel mask to per-tile priority flags.

    A tile gets priority=1 if ANY pixel in its 8×8 footprint is foreground.
    Returns a 2D list [ty][tx] of 0/1.
    """
    if not mask:
        return []

    w_tiles = width // tile_size
    h_tiles = height // tile_size
    pri = [[0] * w_tiles for _ in range(h_tiles)]

    for ty in range(h_tiles):
        y0 = ty * tile_size
        for tx in range(w_tiles):
            x0 = tx * tile_size
            found = 0
            for dy in range(tile_size):
                row = mask[y0 + dy]
                for dx in range(tile_size):
                    if row[x0 + dx]:
                        found = 1
                        break
                if found:
                    break
            pri[ty][tx] = found

    return pri
