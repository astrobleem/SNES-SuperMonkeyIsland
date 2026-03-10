#!/usr/bin/env python3
"""
Generate per-room .obj binary files from SCUMM metadata for SNES engine.

Binary format per room:
  Header (4 bytes):
    object_count    (LE16) — number of objects
    name_table_size (LE16) — total bytes of packed name strings

  Per-object entry (16 bytes × count):
    +$00  obj_id       (LE16)
    +$02  x_px         (LE16)
    +$04  y_px         (LE16)
    +$06  width_px     (LE16)
    +$08  height_px    (LE16)
    +$0A  walk_x       (LE16, signed)
    +$0C  walk_y       (LE16, signed)
    +$0E  actor_dir    (byte)
    +$0F  name_len     (byte)

  Name table (variable):
    Packed ASCII strings, sequential, no separators.
"""

import json
import logging
import struct
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format='%(message)s')


def find_metadata(extracted_dir, room_id):
    """Find metadata.json for a given room ID."""
    extracted_dir = Path(extracted_dir)
    # Directories are named room_NNN or room_NNN_name
    for d in extracted_dir.iterdir():
        if not d.is_dir():
            continue
        name = d.name
        if not name.startswith(f'room_{room_id:03d}'):
            continue
        # Match room_NNN or room_NNN_*
        rest = name[len(f'room_{room_id:03d}'):]
        if rest == '' or rest.startswith('_'):
            meta = d / 'metadata.json'
            if meta.exists():
                return meta
    return None


def build_obj_binary(objects):
    """Build the .obj binary from a list of object dicts."""
    count = len(objects)
    names = []
    entries = bytearray()

    for obj in objects:
        name = obj.get('name', '') or ''
        # Encode name as ASCII, truncate to 255
        name_bytes = name.encode('ascii', errors='replace')[:255]
        names.append(name_bytes)

        walk_x = obj.get('walk_x', 0)
        walk_y = obj.get('walk_y', 0)
        actor_dir = obj.get('actor_dir', 0) & 0x07

        entry = struct.pack('<HHHHHhhBB',
            obj['obj_id'] & 0xFFFF,
            obj.get('x', 0) & 0xFFFF,
            obj.get('y', 0) & 0xFFFF,
            obj.get('width', 0) & 0xFFFF,
            obj.get('height', 0) & 0xFFFF,
            walk_x,
            walk_y,
            actor_dir,
            len(name_bytes),
        )
        entries += entry

    name_table = b''.join(names)
    header = struct.pack('<HH', count, len(name_table))
    return header + entries + name_table


def main():
    extracted_dir = Path('data/scumm_extracted/rooms')
    converted_dir = Path('data/snes_converted/rooms')
    manifest_path = converted_dir / 'manifest.json'

    if not manifest_path.exists():
        logging.error('manifest.json not found in %s', converted_dir)
        sys.exit(1)

    with open(manifest_path) as f:
        manifest = json.load(f)

    room_ids = [r['room_id'] for r in manifest['rooms']]
    total_bytes = 0
    total_objects = 0

    for rid in sorted(room_ids):
        meta_path = find_metadata(extracted_dir, rid)
        if meta_path is None:
            # No metadata — write empty obj file
            obj_data = struct.pack('<HH', 0, 0)
        else:
            with open(meta_path) as f:
                meta = json.load(f)
            objects = meta.get('objects', [])
            obj_data = build_obj_binary(objects)
            total_objects += len(objects)

        out_path = converted_dir / f'room_{rid:03d}.obj'
        out_path.write_bytes(obj_data)
        total_bytes += len(obj_data)

    logging.info('Generated %d .obj files (%d objects, %d total bytes)',
                 len(room_ids), total_objects, total_bytes)

    # Find max objects per room for sizing info
    max_obj = 0
    max_room = 0
    for rid in room_ids:
        meta_path = find_metadata(extracted_dir, rid)
        if meta_path:
            with open(meta_path) as f:
                meta = json.load(f)
            n = len(meta.get('objects', []))
            if n > max_obj:
                max_obj = n
                max_room = rid
    logging.info('Max objects per room: %d (room %d)', max_obj, max_room)


if __name__ == '__main__':
    main()
