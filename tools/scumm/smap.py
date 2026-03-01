"""SCUMM v5 SMAP stripe decompression.

Each stripe is 8 pixels wide and height pixels tall.
The first byte of each stripe is the codec byte.

Based on ScummVM engines/scumm/gfx.cpp (gfx.h BMCOMP_* constants):

  Codec 1:     RAW256 — uncompressed raw pixels
  Codec 14-18: ZIGZAG_V — drawStripBasicV (vertical zigzag, no transparency)
  Codec 24-28: ZIGZAG_H — drawStripBasicH (horizontal zigzag, no transparency)
  Codec 34-38: ZIGZAG_VT — drawStripBasicV (vertical zigzag, with transparency)
  Codec 44-48: ZIGZAG_HT — drawStripBasicH (horizontal zigzag, with transparency)
  Codec 64-68: MAJMIN_H — drawStripComplex (major-minor horizontal, no transp)
  Codec 84-88: MAJMIN_HT — drawStripComplex (major-minor horizontal, with transp)

color_bits = codec % 10  (the "decomp_shr")
decomp_mask = 0xFF >> (8 - color_bits)

Bit reading is LSB-first: bits are consumed from bit 0 upward.
"""

import struct
import logging

log = logging.getLogger(__name__)


class BitReader:
    """Read bits from a byte stream, LSB first (matching ScummVM)."""

    def __init__(self, data: bytes, pos: int):
        self.data = data
        self.pos = pos
        self.bits = 0
        self.nbits = 0

    def _fill(self):
        """Load more bits if we have 8 or fewer remaining."""
        if self.nbits <= 8:
            if self.pos < len(self.data):
                self.bits |= self.data[self.pos] << self.nbits
                self.pos += 1
                self.nbits += 8

    def read_bit(self) -> int:
        self._fill()
        bit = self.bits & 1
        self.bits >>= 1
        self.nbits -= 1
        return bit

    def read_bits(self, n: int) -> int:
        self._fill()
        val = self.bits & ((1 << n) - 1)
        self.bits >>= n
        self.nbits -= n
        return val


def decode_stripe_raw(data: bytes, pos: int, height: int) -> list:
    """Codec 1: uncompressed 8*height bytes."""
    pixels = []
    for y in range(height):
        row = []
        for x in range(8):
            if pos < len(data):
                row.append(data[pos])
                pos += 1
            else:
                row.append(0)
        pixels.append(row)
    return pixels


def decode_strip_basic_v(data: bytes, pos: int, height: int,
                         color_bits: int) -> list:
    """Codecs 14-18, 34-38: drawStripBasicV — vertical zigzag.

    ScummVM: iterates columns (x) then rows (y) within each column.
    """
    color = data[pos]
    pos += 1
    bits = BitReader(data, pos)
    inc = -1  # signed increment, starts at -1

    pixels = [[0] * 8 for _ in range(height)]

    for x in range(8):
        for y in range(height):
            pixels[y][x] = color & 0xFF
            # Read next color change (except after very last pixel)
            if not (x == 7 and y == height - 1):
                if bits.read_bit() == 0:
                    pass  # no change
                elif bits.read_bit() == 0:
                    # New absolute color
                    color = bits.read_bits(color_bits)
                    inc = -1
                elif bits.read_bit() == 0:
                    color = (color + inc) & 0xFF
                else:
                    inc = -inc
                    color = (color + inc) & 0xFF

    return pixels


def decode_strip_basic_h(data: bytes, pos: int, height: int,
                         color_bits: int) -> list:
    """Codecs 24-28, 44-48: drawStripBasicH — horizontal zigzag.

    ScummVM: iterates rows (y) then columns (x) within each row.
    """
    color = data[pos]
    pos += 1
    bits = BitReader(data, pos)
    inc = -1

    pixels = [[0] * 8 for _ in range(height)]

    for y in range(height):
        for x in range(8):
            pixels[y][x] = color & 0xFF
            if not (y == height - 1 and x == 7):
                if bits.read_bit() == 0:
                    pass
                elif bits.read_bit() == 0:
                    color = bits.read_bits(color_bits)
                    inc = -1
                elif bits.read_bit() == 0:
                    color = (color + inc) & 0xFF
                else:
                    inc = -inc
                    color = (color + inc) & 0xFF

    return pixels


def decode_strip_complex(data: bytes, pos: int, height: int,
                         color_bits: int) -> list:
    """Codecs 64-68, 84-88: drawStripComplex — MajMinCodec.

    ScummVM MajMinCodec: reads color, then 2 bytes for 16-bit initial buffer.
    Algorithm per pixel:
      - Output current color
      - If not in repeat mode:
        - bit 0: no change
        - bit 1,1: read 3-bit delta (-4..+3); if 0 → enter repeat mode (read 8-bit count)
        - bit 1,0: read new absolute color
      - If in repeat mode: decrement count, exit when 0

    Always processes horizontally (8 pixels per row, height rows).
    """
    color = data[pos]
    pos += 1
    # MajMinCodec loads 2 initial bytes into the bit buffer
    bits_val = data[pos] | (data[pos + 1] << 8) if pos + 1 < len(data) else 0
    pos += 2

    nbits = 16
    data_ptr = pos

    pixels = [[0] * 8 for _ in range(height)]
    repeat_mode = False
    repeat_count = 0

    def fill():
        nonlocal bits_val, nbits, data_ptr
        if nbits <= 8:
            if data_ptr < len(data):
                bits_val |= data[data_ptr] << nbits
                data_ptr += 1
                nbits += 8

    def read_bits_n(n):
        nonlocal bits_val, nbits
        fill()
        val = bits_val & ((1 << n) - 1)
        bits_val >>= n
        nbits -= n
        return val

    for y in range(height):
        for x in range(8):
            pixels[y][x] = color & 0xFF

            if not repeat_mode:
                if read_bits_n(1):
                    if read_bits_n(1):
                        diff = read_bits_n(3) - 4
                        if diff:
                            color = (color + diff) & 0xFF
                        else:
                            # Enter repeat mode
                            repeat_mode = True
                            repeat_count = read_bits_n(8) - 1
                    else:
                        color = read_bits_n(color_bits)
            else:
                repeat_count -= 1
                if repeat_count == 0:
                    repeat_mode = False

    return pixels


def decode_smap_stripe(data: bytes, offset: int, height: int) -> list:
    """Decode a single SMAP stripe starting at offset in data.

    Returns a list of height rows, each a list of 8 palette indices.
    """
    codec = data[offset]
    pos = offset + 1
    color_bits = codec % 10

    if codec == 1:
        return decode_stripe_raw(data, pos, height)

    if 14 <= codec <= 18:
        return decode_strip_basic_v(data, pos, height, color_bits)
    elif 24 <= codec <= 28:
        return decode_strip_basic_h(data, pos, height, color_bits)
    elif 34 <= codec <= 38:
        return decode_strip_basic_v(data, pos, height, color_bits)
    elif 44 <= codec <= 48:
        return decode_strip_basic_h(data, pos, height, color_bits)
    elif 64 <= codec <= 68:
        return decode_strip_complex(data, pos, height, color_bits)
    elif 84 <= codec <= 88:
        return decode_strip_complex(data, pos, height, color_bits)
    else:
        log.warning("Unknown SMAP codec 0x%02X (%d) at offset 0x%X",
                    codec, codec, offset)
        return [[0] * 8 for _ in range(height)]


def decode_smap(smap_data: bytes, width: int, height: int) -> list:
    """Decode an entire SMAP chunk into a 2D pixel array.

    Args:
        smap_data: SMAP chunk payload (after 8-byte header)
        width: image width in pixels (must be multiple of 8)
        height: image height in pixels

    Returns:
        List of height rows, each a list of width palette indices.
    """
    num_stripes = width // 8
    pixels = [[0] * width for _ in range(height)]

    for stripe_idx in range(num_stripes):
        stripe_offset = struct.unpack_from('<I', smap_data, stripe_idx * 4)[0]
        # Offsets are relative to chunk start (including 8-byte header);
        # smap_data is the payload (after header), so subtract 8
        adj_offset = stripe_offset - 8

        if adj_offset < 0 or adj_offset >= len(smap_data):
            log.warning("Stripe %d: offset 0x%X out of range", stripe_idx, stripe_offset)
            continue

        stripe_pixels = decode_smap_stripe(smap_data, adj_offset, height)

        x_start = stripe_idx * 8
        for y in range(height):
            for x in range(8):
                if x_start + x < width:
                    pixels[y][x_start + x] = stripe_pixels[y][x]

    return pixels
