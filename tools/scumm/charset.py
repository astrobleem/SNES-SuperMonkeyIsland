"""SCUMM v5 charset extraction — raw binary + font sheet PNG."""

import struct
import logging
from pathlib import Path

log = logging.getLogger(__name__)


def extract_charsets(room_resource, output_dir: Path) -> int:
    """Extract charset resources from a room's trailing CHAR chunks.

    Saves raw binary and attempts to render a font sheet PNG.

    Returns:
        Number of charsets extracted
    """
    chars = room_resource.get_trailing('CHAR')
    if not chars:
        return 0

    charsets_dir = output_dir / 'charsets'
    charsets_dir.mkdir(parents=True, exist_ok=True)
    count = 0

    for i, char_chunk in enumerate(chars):
        # Save raw binary
        raw_path = charsets_dir / f'char_{i:03d}.bin'
        raw_path.write_bytes(char_chunk.data)
        count += 1

        # Attempt to render a font sheet
        _render_font_sheet(char_chunk.data, charsets_dir / f'char_{i:03d}.png', i)
        log.debug("Room %d: saved charset %d (%d bytes)",
                  room_resource.room_id, i, len(char_chunk.data))

    if count > 0:
        log.info("Room %d: saved %d charsets", room_resource.room_id, count)
    return count


def _render_font_sheet(data: bytes, output_path: Path, charset_idx: int):
    """Try to render a SCUMM v5 charset as a font sheet PNG.

    SCUMM v5 charset format:
      - 4 bytes: total size (LE32)
      - 15 bytes: color map (palette indices for the font colors)
      - 4 bytes per character: bit offset table (LE32, 256 entries)
      - Character data: each char has width(byte), height(byte), xoff(byte), yoff(byte),
        then packed pixel data (4 bits per pixel or 1-2 bits depending on version)
    """
    try:
        from PIL import Image
    except ImportError:
        return

    if len(data) < 19 + 256 * 4:
        return

    # Skip total size field (4 bytes)
    pos = 4
    # Color map (15 bytes) — maps 4-bit values to palette indices
    color_map = list(data[pos:pos + 15])
    pos += 15

    # Build a simple grayscale palette for visualization
    vis_colors = [(0, 0, 0)]  # 0 = transparent/black
    grays = [85, 170, 255, 200, 150, 100, 50, 220, 180, 130, 110, 90, 70, 40, 255]
    for g in grays:
        vis_colors.append((g, g, g))

    # Character offset table (256 entries, LE32 each)
    char_offsets = []
    for i in range(256):
        off = struct.unpack_from('<I', data, pos + i * 4)[0]
        char_offsets.append(off)
    pos += 256 * 4

    # Render each character into a grid
    cell_w, cell_h = 16, 16  # cell size for font sheet
    cols = 16
    rows = 16
    sheet = Image.new('RGB', (cols * cell_w, rows * cell_h), (0, 0, 0))
    pixels = sheet.load()

    chars_rendered = 0
    for ch_idx in range(256):
        off = char_offsets[ch_idx]
        if off == 0 or off + 4 > len(data):
            continue

        char_w = data[off]
        char_h = data[off + 1]
        x_off = data[off + 2]
        y_off = data[off + 3]

        if char_w == 0 or char_h == 0 or char_w > cell_w or char_h > cell_h:
            continue

        # Pixel data follows: 1 bit per pixel for v5 charsets (most common)
        # Actually v5 uses variable bits. Let's try 1-bit first.
        bit_data_start = off + 4
        grid_col = ch_idx % cols
        grid_row = ch_idx // cols

        # Read pixel data (packed bits, MSB first)
        byte_pos = bit_data_start
        bit_pos = 0

        for cy in range(char_h):
            for cx in range(char_w):
                if byte_pos >= len(data):
                    break
                bit = (data[byte_pos] >> (7 - bit_pos)) & 1
                bit_pos += 1
                if bit_pos >= 8:
                    bit_pos = 0
                    byte_pos += 1

                if bit:
                    px = grid_col * cell_w + cx
                    py = grid_row * cell_h + cy
                    if 0 <= px < sheet.width and 0 <= py < sheet.height:
                        pixels[px, py] = (255, 255, 255)

        chars_rendered += 1

    if chars_rendered > 0:
        sheet.save(str(output_path))
        log.debug("Rendered %d characters to font sheet", chars_rendered)
