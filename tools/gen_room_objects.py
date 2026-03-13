#!/usr/bin/env python3
"""
Generate per-room .obj binary files from SCUMM metadata for SNES engine.

Binary format per room:
  Header (8 bytes):
    object_count    (LE16) — number of objects
    name_table_size (LE16) — total bytes of packed name strings
    verb_data_size  (LE16) — total bytes of concatenated verb data blobs
    reserved        (LE16) — 0

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

  Per-object verb index (4 bytes × count):
    +$00  verb_data_off (LE16) — offset into verb data section
    +$02  verb_data_len (LE16) — length of this object's verb data blob

  Name table (variable):
    Packed ASCII strings, sequential, no separators.

  Verb data (variable):
    Concatenated per-object VERB chunk blobs.
    Each blob: {verb_id:u8, offset:u16}* + 0x00 terminator + bytecode.
"""

import json
import logging
import struct
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format='%(message)s')


def find_room_dir(extracted_dir, room_id):
    """Find the extracted room directory for a given room ID."""
    extracted_dir = Path(extracted_dir)
    for d in extracted_dir.iterdir():
        if not d.is_dir():
            continue
        name = d.name
        if not name.startswith(f'room_{room_id:03d}'):
            continue
        rest = name[len(f'room_{room_id:03d}'):]
        if rest == '' or rest.startswith('_'):
            if (d / 'metadata.json').exists():
                return d
    return None


def build_obj_binary(objects, room_dir):
    """Build the .obj binary from a list of object dicts + verb data files."""
    count = len(objects)
    names = []
    entries = bytearray()
    verb_index = bytearray()
    verb_data_blobs = bytearray()

    verb_dir = room_dir / 'verbs' if room_dir else None

    for obj in objects:
        name = obj.get('name', '') or ''
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

        # Load verb data blob for this object
        oid = obj['obj_id']
        verb_blob = b''
        if verb_dir:
            verb_path = verb_dir / f'obj_{oid:04d}.verb'
            if verb_path.exists():
                verb_blob = verb_path.read_bytes()

        verb_index += struct.pack('<HH', len(verb_data_blobs), len(verb_blob))
        verb_data_blobs += verb_blob

    name_table = b''.join(names)
    header = struct.pack('<HHHH', count, len(name_table), len(verb_data_blobs), 0)
    return header + entries + verb_index + name_table + verb_data_blobs


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
    total_verb_bytes = 0

    for rid in sorted(room_ids):
        room_dir = find_room_dir(extracted_dir, rid)
        if room_dir is None:
            obj_data = struct.pack('<HHHH', 0, 0, 0, 0)
        else:
            with open(room_dir / 'metadata.json') as f:
                meta = json.load(f)
            objects = meta.get('objects', [])
            obj_data = build_obj_binary(objects, room_dir)
            total_objects += len(objects)
            verb_size = struct.unpack_from('<H', obj_data, 4)[0]
            total_verb_bytes += verb_size

        out_path = converted_dir / f'room_{rid:03d}.obj'
        out_path.write_bytes(obj_data)
        total_bytes += len(obj_data)

    logging.info('Generated %d .obj files (%d objects, %d total bytes)',
                 len(room_ids), total_objects, total_bytes)
    logging.info('Total verb data: %d bytes across all rooms', total_verb_bytes)

    max_obj = 0
    max_room = 0
    max_verb = 0
    max_verb_room = 0
    for rid in room_ids:
        room_dir = find_room_dir(extracted_dir, rid)
        if room_dir:
            with open(room_dir / 'metadata.json') as f:
                meta = json.load(f)
            n = len(meta.get('objects', []))
            if n > max_obj:
                max_obj = n
                max_room = rid
            # Check verb data size
            obj_path = converted_dir / f'room_{rid:03d}.obj'
            if obj_path.exists():
                d = obj_path.read_bytes()
                if len(d) >= 8:
                    vs = struct.unpack_from('<H', d, 4)[0]
                    if vs > max_verb:
                        max_verb = vs
                        max_verb_room = rid
    logging.info('Max objects per room: %d (room %d)', max_obj, max_room)
    logging.info('Max verb data per room: %d bytes (room %d)', max_verb, max_verb_room)


if __name__ == '__main__':
    main()
