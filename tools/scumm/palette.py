"""SCUMM v5 palette (CLUT) parsing and rendering."""

import logging
from pathlib import Path

log = logging.getLogger(__name__)


def parse_clut(clut_data: bytes) -> list:
    """Parse a CLUT chunk payload into a list of (R, G, B) tuples.

    CLUT is 768 bytes: 256 colors * 3 bytes (R, G, B).
    """
    palette = []
    for i in range(256):
        offset = i * 3
        if offset + 3 <= len(clut_data):
            r = clut_data[offset]
            g = clut_data[offset + 1]
            b = clut_data[offset + 2]
            palette.append((r, g, b))
        else:
            palette.append((0, 0, 0))
    return palette


def save_palette_bin(clut_data: bytes, output_path: Path):
    """Save raw 768-byte palette data."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(clut_data[:768])


def save_palette_png(palette: list, output_path: Path, cell_size: int = 16):
    """Render the palette as a 16x16 swatch PNG image."""
    try:
        from PIL import Image
    except ImportError:
        log.warning("Pillow not installed, skipping palette PNG")
        return

    img_size = 16 * cell_size
    img = Image.new('RGB', (img_size, img_size))
    pixels = img.load()

    for idx, (r, g, b) in enumerate(palette[:256]):
        col = idx % 16
        row = idx // 16
        for dy in range(cell_size):
            for dx in range(cell_size):
                pixels[col * cell_size + dx, row * cell_size + dy] = (r, g, b)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(output_path))
    log.debug("Saved palette PNG: %s", output_path)
