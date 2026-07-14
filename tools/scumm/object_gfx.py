"""SCUMM v5 object image extraction — OBIM → PNG (+ ZPnn foreground mask)."""

import struct
import logging
from pathlib import Path
from .chunks import iter_chunks
from .smap import decode_smap
from .zplane import decode_zplane
from .palette import parse_clut

log = logging.getLogger(__name__)


def extract_object_images(room_resource, output_dir: Path, palette: list) -> int:
    """Extract all object images from a room.

    Args:
        room_resource: RoomResource instance
        output_dir: directory under which to create objects/ folder
        palette: list of 256 (R,G,B) tuples from the room's CLUT

    Returns:
        Number of objects extracted
    """
    try:
        from PIL import Image
    except ImportError:
        log.warning("Pillow not installed, skipping object images")
        return 0

    obims = room_resource.get_all_room_sub('OBIM')
    if not obims:
        return 0

    # Also parse OBCD chunks to get object names
    obcds = room_resource.get_all_room_sub('OBCD')
    obj_names = {}
    for obcd in obcds:
        subs = iter_chunks(obcd.data)
        obj_id = None
        name = None
        for sub in subs:
            if sub.tag == 'CDHD' and len(sub.data) >= 2:
                obj_id = struct.unpack_from('<H', sub.data, 0)[0]
            elif sub.tag == 'OBNA':
                name = sub.data.split(b'\x00')[0].decode('ascii', errors='replace')
                name = name.rstrip('@')
        if obj_id is not None and name:
            obj_names[obj_id] = name

    objects_dir = output_dir / 'objects'
    count = 0

    for obim in obims:
        subs = iter_chunks(obim.data)

        # Find IMHD
        imhd = None
        im_chunks = []
        for sub in subs:
            if sub.tag == 'IMHD':
                imhd = sub
            elif sub.tag.startswith('IM') and sub.tag != 'IMHD':
                im_chunks.append(sub)

        if imhd is None or len(imhd.data) < 16:
            continue

        obj_id = struct.unpack_from('<H', imhd.data, 0)[0]
        num_imnn = struct.unpack_from('<H', imhd.data, 2)[0]
        # x, y offsets at bytes 8,10; width, height at bytes 12,14
        obj_width = struct.unpack_from('<H', imhd.data, 12)[0]
        obj_height = struct.unpack_from('<H', imhd.data, 14)[0]

        if obj_width == 0 or obj_height == 0:
            continue

        obj_name = obj_names.get(obj_id, '')
        # Sanitize name for filename
        safe_name = ''.join(c if c.isalnum() or c in '-_' else '_'
                           for c in obj_name).strip('_')

        for state_idx, im_chunk in enumerate(im_chunks):
            im_subs = iter_chunks(im_chunk.data)
            smap_chunk = None
            for imsub in im_subs:
                if imsub.tag == 'SMAP':
                    smap_chunk = imsub
                    break

            if smap_chunk is None:
                continue

            # Decode using same SMAP codec as backgrounds
            try:
                pixels = decode_smap(smap_chunk.data, obj_width, obj_height)
            except Exception as e:
                log.warning("Room %d obj %d state %d: decode error: %s",
                            room_resource.room_id, obj_id, state_idx, e)
                continue

            # Create PNG
            img = Image.new('RGB', (obj_width, obj_height))
            img_pixels = img.load()
            for y in range(obj_height):
                for x in range(obj_width):
                    idx = pixels[y][x] & 0xFF
                    img_pixels[x, y] = palette[idx]

            objects_dir.mkdir(parents=True, exist_ok=True)
            if safe_name:
                fname = f'obj_{obj_id:04d}_{safe_name}'
            else:
                fname = f'obj_{obj_id:04d}'
            if num_imnn > 1:
                fname += f'_state{state_idx}'
            fname += '.png'
            img.save(str(objects_dir / fname))
            count += 1

    if count > 0:
        log.info("Room %d: saved %d object images", room_resource.room_id, count)
    return count


def extract_object_zplanes(room_resource, output_dir: Path) -> int:
    """Extract each object's ZP01 foreground mask (OBIM → IMnn → ZP01).

    SCUMM v5 object images carry per-pixel z-plane strips that mark which
    of the object's pixels render IN FRONT of actors (walk-behind / logo
    foreground). The SNES port needs these so drawn foreground objects mask
    sprites (e.g. the room-10 title logo must occlude the drifting clouds).
    Saved as obj_<id>[_name]_zp01.png (1-bit, object-sized), alongside the
    object image. Only state 0's mask is written (z-planed objects are
    single-state; verified game-wide). Returns the number of masks saved.
    """
    try:
        from PIL import Image
    except ImportError:
        return 0

    obims = room_resource.get_all_room_sub('OBIM')
    if not obims:
        return 0

    obcds = room_resource.get_all_room_sub('OBCD')
    obj_names = {}
    for obcd in obcds:
        obj_id = None
        name = None
        for sub in iter_chunks(obcd.data):
            if sub.tag == 'CDHD' and len(sub.data) >= 2:
                obj_id = struct.unpack_from('<H', sub.data, 0)[0]
            elif sub.tag == 'OBNA':
                name = sub.data.split(b'\x00')[0].decode('ascii', errors='replace')
                name = name.rstrip('@')
        if obj_id is not None and name:
            obj_names[obj_id] = name

    objects_dir = output_dir / 'objects'
    count = 0

    for obim in obims:
        subs = list(iter_chunks(obim.data))
        imhd = next((s for s in subs if s.tag == 'IMHD'), None)
        if imhd is None or len(imhd.data) < 16:
            continue
        obj_id = struct.unpack_from('<H', imhd.data, 0)[0]
        obj_width = struct.unpack_from('<H', imhd.data, 12)[0]
        obj_height = struct.unpack_from('<H', imhd.data, 14)[0]
        if obj_width == 0 or obj_height == 0:
            continue

        im_chunk = next((s for s in subs
                         if s.tag.startswith('IM') and s.tag != 'IMHD'), None)
        if im_chunk is None:
            continue
        zp_chunk = next((c for c in iter_chunks(im_chunk.data)
                         if c.tag == 'ZP01'), None)
        if zp_chunk is None:
            continue

        header = struct.pack('>4sI', b'ZP01', len(zp_chunk.data) + 8)
        try:
            mask = decode_zplane(header + zp_chunk.data, obj_width, obj_height)
        except Exception as e:
            log.warning("Room %d obj %d: zplane decode error: %s",
                        room_resource.room_id, obj_id, e)
            continue
        if not mask or not any(any(row) for row in mask):
            continue

        obj_name = obj_names.get(obj_id, '')
        safe_name = ''.join(c if c.isalnum() or c in '-_' else '_'
                            for c in obj_name).strip('_')
        mask_img = Image.new('1', (obj_width, obj_height))
        px = mask_img.load()
        for y in range(obj_height):
            row = mask[y]
            for x in range(obj_width):
                px[x, y] = 1 if row[x] else 0

        objects_dir.mkdir(parents=True, exist_ok=True)
        fname = f'obj_{obj_id:04d}' + (f'_{safe_name}' if safe_name else '') + '_zp01.png'
        mask_img.save(str(objects_dir / fname))
        count += 1

    if count > 0:
        log.info("Room %d: saved %d object z-planes", room_resource.room_id, count)
    return count
