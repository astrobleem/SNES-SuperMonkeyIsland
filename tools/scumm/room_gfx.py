"""SCUMM v5 room background extraction — SMAP + CLUT → PNG."""

import struct
import logging
from pathlib import Path
from .chunks import iter_chunks
from .smap import decode_smap
from .palette import parse_clut

log = logging.getLogger(__name__)


def extract_background(room_resource, output_dir: Path) -> bool:
    """Extract the room background as a PNG.

    Args:
        room_resource: RoomResource instance
        output_dir: directory to write background.png

    Returns:
        True if successfully extracted
    """
    rmhd = room_resource.get_room_sub('RMHD')
    if not rmhd:
        log.warning("Room %d: no RMHD", room_resource.room_id)
        return False

    width = struct.unpack_from('<H', rmhd.data, 0)[0]
    height = struct.unpack_from('<H', rmhd.data, 2)[0]

    if width == 0 or height == 0:
        log.info("Room %d: zero dimensions %dx%d, skipping", room_resource.room_id, width, height)
        return False

    # Get palette
    clut = room_resource.get_room_sub('CLUT')
    if not clut:
        log.warning("Room %d: no CLUT", room_resource.room_id)
        return False
    palette = parse_clut(clut.data)

    # Get RMIM → IM00 → SMAP
    rmim = room_resource.get_room_sub('RMIM')
    if not rmim:
        log.warning("Room %d: no RMIM", room_resource.room_id)
        return False

    rmim_subs = iter_chunks(rmim.data)
    smap_data = None
    for sub in rmim_subs:
        if sub.tag == 'IM00' or (sub.tag.startswith('IM') and sub.tag != 'IMHD'):
            im_subs = iter_chunks(sub.data)
            for imsub in im_subs:
                if imsub.tag == 'SMAP':
                    smap_data = imsub.data
                    break
            if smap_data:
                break

    if not smap_data:
        log.warning("Room %d: no SMAP found", room_resource.room_id)
        return False

    # Decode
    pixels = decode_smap(smap_data, width, height)

    # Convert to RGB image
    try:
        from PIL import Image
    except ImportError:
        log.error("Pillow not installed, cannot create PNGs")
        return False

    img = Image.new('RGB', (width, height))
    img_pixels = img.load()
    for y in range(height):
        for x in range(width):
            idx = pixels[y][x] & 0xFF
            img_pixels[x, y] = palette[idx]

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / 'background.png'
    img.save(str(output_path))
    log.info("Room %d: saved %dx%d background → %s",
             room_resource.room_id, width, height, output_path)
    return True
