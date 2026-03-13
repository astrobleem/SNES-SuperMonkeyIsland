#!/usr/bin/env python3
"""
MSU-1 Data Pack Generator — Room Data (Phase 0)

Packs all SNES-converted room assets into a single .msu data file with a dense
room index for O(1) lookup by the 65816 engine via MSU-1 registers.

File format:
  $000000  File header        (256 bytes)
  $000100  Room index table    (100 entries × 8 bytes = 800 bytes)
  $000400+ Room data blocks    (86 rooms, 512-byte aligned)
"""

import argparse
import json
import logging
import struct
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format='%(message)s')

MAGIC = b"S-MSU1"
TITLE = b"SUPER MONKEY ISLAND  "  # 21 chars, space-padded
VERSION = 1
HEADER_SIZE = 256
INDEX_ENTRY_SIZE = 8
BLOCK_ALIGNMENT = 512
ROOM_EXTENSIONS = ('.hdr', '.pal', '.chr', '.map', '.col', '.box', '.obj', '.ochr')


def align_to(offset, alignment):
    """Round up offset to next alignment boundary."""
    return (offset + alignment - 1) & ~(alignment - 1)


def load_room_files(rooms_dir):
    """Load manifest and all binary files for each room.

    Returns list of dicts sorted by room_id:
        [{'room_id': int, 'hdr': bytes, 'pal': bytes, 'chr': bytes,
          'map': bytes, 'col': bytes}, ...]
    """
    rooms_dir = Path(rooms_dir)
    manifest_path = rooms_dir / 'manifest.json'
    if not manifest_path.exists():
        logging.error('manifest.json not found in %s', rooms_dir)
        sys.exit(1)

    with open(manifest_path) as f:
        manifest = json.load(f)

    rooms = []
    for room_info in manifest['rooms']:
        rid = room_info['room_id']
        prefix = rooms_dir / f'room_{rid:03d}'
        files = {}
        for ext in ROOM_EXTENSIONS:
            path = Path(str(prefix) + ext)
            if not path.exists():
                logging.error('Missing %s for room %d', path.name, rid)
                sys.exit(1)
            files[ext.lstrip('.')] = path.read_bytes()
        files['room_id'] = rid
        rooms.append(files)

    rooms.sort(key=lambda r: r['room_id'])
    logging.info('Loaded %d rooms from %s', len(rooms), rooms_dir)
    return rooms


def build_room_data_block(room):
    """Concatenate hdr+pal+chr+map+col into a single room data block."""
    return room['hdr'] + room['pal'] + room['chr'] + room['map'] + room['col'] + room['box'] + room['obj'] + room['ochr']


def build_file_header(room_count, room_index_offset, room_data_offset,
                      room_data_size, total_file_size):
    """Build the 256-byte file header."""
    hdr = bytearray(HEADER_SIZE)
    offset = 0

    # $0000: magic (6 bytes)
    hdr[offset:offset + 6] = MAGIC
    offset += 6

    # $0006: title (21 bytes)
    hdr[offset:offset + 21] = TITLE
    offset += 21

    # $001B: version (1 byte)
    hdr[offset] = VERSION
    offset += 1

    # $001C: reserved (2 bytes)
    offset += 2

    # $001E: room_count (LE16)
    struct.pack_into('<H', hdr, offset, room_count)
    offset += 2

    # $0020: room_index_offset (LE32)
    struct.pack_into('<I', hdr, offset, room_index_offset)
    offset += 4

    # $0024: script_index_offset (LE32) — placeholder
    struct.pack_into('<I', hdr, offset, 0)
    offset += 4

    # $0028: costume_index_offset (LE32) — placeholder
    struct.pack_into('<I', hdr, offset, 0)
    offset += 4

    # $002C: sound_index_offset (LE32) — placeholder
    struct.pack_into('<I', hdr, offset, 0)
    offset += 4

    # $0030: charset_index_offset (LE32) — placeholder
    struct.pack_into('<I', hdr, offset, 0)
    offset += 4

    # $0034: room_data_offset (LE32)
    struct.pack_into('<I', hdr, offset, room_data_offset)
    offset += 4

    # $0038: room_data_size (LE32)
    struct.pack_into('<I', hdr, offset, room_data_size)
    offset += 4

    # $003C: total_file_size (LE32)
    struct.pack_into('<I', hdr, offset, total_file_size)
    offset += 4

    # $0040-$00FF: padding (already zeros)
    return bytes(hdr)


def pack_msu(rooms_dir, output_path, verbose=False):
    """Main packer: compute layout, build index, write .msu file."""
    rooms = load_room_files(rooms_dir)

    # Build room data blocks
    room_blocks = {}
    for room in rooms:
        rid = room['room_id']
        room_blocks[rid] = build_room_data_block(room)

    max_room_id = max(r['room_id'] for r in rooms)
    index_slots = max_room_id + 1  # dense table: 0..max_room_id

    # Layout computation
    room_index_offset = HEADER_SIZE  # $000100
    room_index_size = index_slots * INDEX_ENTRY_SIZE
    room_data_offset = align_to(room_index_offset + room_index_size, BLOCK_ALIGNMENT)

    # Compute room data block offsets
    room_offsets = {}
    current_offset = room_data_offset
    for room in rooms:
        rid = room['room_id']
        block = room_blocks[rid]
        room_offsets[rid] = current_offset
        current_offset = align_to(current_offset + len(block), BLOCK_ALIGNMENT)

    room_data_size = current_offset - room_data_offset
    total_file_size = current_offset

    # Build header
    header = build_file_header(
        room_count=index_slots,
        room_index_offset=room_index_offset,
        room_data_offset=room_data_offset,
        room_data_size=room_data_size,
        total_file_size=total_file_size,
    )

    # Build index table
    index_table = bytearray(index_slots * INDEX_ENTRY_SIZE)
    for slot_id in range(index_slots):
        entry_offset = slot_id * INDEX_ENTRY_SIZE
        if slot_id in room_offsets:
            block = room_blocks[slot_id]
            struct.pack_into('<I', index_table, entry_offset, room_offsets[slot_id])
            struct.pack_into('<I', index_table, entry_offset + 4, len(block))
        # else: stays zero (null entry)

    # Write output file
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, 'wb') as f:
        # Header
        f.write(header)
        assert f.tell() == room_index_offset

        # Index table
        f.write(index_table)

        # Pad to room_data_offset
        pad_size = room_data_offset - f.tell()
        if pad_size > 0:
            f.write(b'\x00' * pad_size)
        assert f.tell() == room_data_offset

        # Room data blocks
        for room in rooms:
            rid = room['room_id']
            expected_offset = room_offsets[rid]
            # Pad to aligned offset
            pad_size = expected_offset - f.tell()
            if pad_size > 0:
                f.write(b'\x00' * pad_size)
            assert f.tell() == expected_offset
            f.write(room_blocks[rid])

        # Final padding to aligned end
        pad_size = total_file_size - f.tell()
        if pad_size > 0:
            f.write(b'\x00' * pad_size)

    logging.info('Wrote %s (%d bytes, %.2f MB)',
                 output_path, total_file_size, total_file_size / 1024 / 1024)
    logging.info('  %d rooms packed, index slots: %d, room data: %d bytes',
                 len(rooms), index_slots, room_data_size)

    if verbose:
        null_slots = [i for i in range(index_slots) if i not in room_offsets]
        logging.info('  Null index slots: %s', null_slots)
        for room in rooms:
            rid = room['room_id']
            block = room_blocks[rid]
            logging.info('  Room %3d: offset=$%08X  size=%6d  aligned=%6d',
                         rid, room_offsets[rid], len(block),
                         align_to(len(block), BLOCK_ALIGNMENT))

    return output_path, total_file_size


def verify_msu(msu_path, rooms_dir):
    """Read back .msu and verify byte-for-byte against source room files."""
    rooms = load_room_files(rooms_dir)
    msu_path = Path(msu_path)
    errors = 0

    with open(msu_path, 'rb') as f:
        data = f.read()

    # Verify header
    if data[0:6] != MAGIC:
        logging.error('VERIFY FAIL: magic mismatch: %s', data[0:6])
        errors += 1
    else:
        logging.info('VERIFY OK: magic = %s', data[0:6].decode('ascii'))

    if data[6:27] != TITLE:
        logging.error('VERIFY FAIL: title mismatch: %s', data[6:27])
        errors += 1
    else:
        logging.info('VERIFY OK: title = "%s"', data[6:27].decode('ascii'))

    if data[27] != VERSION:
        logging.error('VERIFY FAIL: version=%d expected=%d', data[27], VERSION)
        errors += 1

    room_count = struct.unpack_from('<H', data, 0x1E)[0]
    room_index_offset = struct.unpack_from('<I', data, 0x20)[0]
    room_data_offset = struct.unpack_from('<I', data, 0x34)[0]
    total_file_size = struct.unpack_from('<I', data, 0x3C)[0]

    logging.info('VERIFY: room_count=%d, index@$%06X, data@$%06X, total=%d',
                 room_count, room_index_offset, room_data_offset, total_file_size)

    if total_file_size != len(data):
        logging.error('VERIFY FAIL: total_file_size=%d but file is %d bytes',
                      total_file_size, len(data))
        errors += 1

    # Check null entries for missing room IDs
    existing_ids = {r['room_id'] for r in rooms}
    for slot_id in range(room_count):
        entry_off = room_index_offset + slot_id * INDEX_ENTRY_SIZE
        slot_offset = struct.unpack_from('<I', data, entry_off)[0]
        slot_size = struct.unpack_from('<I', data, entry_off + 4)[0]
        if slot_id not in existing_ids:
            if slot_offset != 0 or slot_size != 0:
                logging.error('VERIFY FAIL: null slot %d has offset=$%08X size=%d',
                              slot_id, slot_offset, slot_size)
                errors += 1

    # Verify each room byte-for-byte
    for room in rooms:
        rid = room['room_id']
        entry_off = room_index_offset + rid * INDEX_ENTRY_SIZE
        block_offset = struct.unpack_from('<I', data, entry_off)[0]
        block_size = struct.unpack_from('<I', data, entry_off + 4)[0]

        # Check 512-byte alignment
        if block_offset % BLOCK_ALIGNMENT != 0:
            logging.error('VERIFY FAIL: room %d offset $%08X not %d-byte aligned',
                          rid, block_offset, BLOCK_ALIGNMENT)
            errors += 1

        expected = build_room_data_block(room)
        if block_size != len(expected):
            logging.error('VERIFY FAIL: room %d size mismatch: index=%d expected=%d',
                          rid, block_size, len(expected))
            errors += 1
            continue

        actual = data[block_offset:block_offset + block_size]
        if actual != expected:
            # Find first differing byte
            for i in range(len(expected)):
                if actual[i] != expected[i]:
                    logging.error('VERIFY FAIL: room %d byte mismatch at offset %d '
                                  '(got $%02X, expected $%02X)',
                                  rid, i, actual[i], expected[i])
                    break
            errors += 1
        else:
            logging.info('VERIFY OK: room %3d  offset=$%08X  size=%d', rid, block_offset, block_size)

    if errors == 0:
        logging.info('VERIFY PASSED: all %d rooms verified byte-for-byte', len(rooms))
    else:
        logging.error('VERIFY FAILED: %d error(s)', errors)
    return errors


def main():
    parser = argparse.ArgumentParser(
        description='MSU-1 Data Pack Generator — pack SNES room assets into .msu file')
    parser.add_argument('--input', '-i', default='data/snes_converted/rooms/',
                        help='Input directory with room binaries and manifest.json')
    parser.add_argument('--output', '-o', default='distribution/SuperMonkeyIsland.msu',
                        help='Output .msu file path')
    parser.add_argument('--verify', action='store_true',
                        help='Read back .msu and verify byte-for-byte against source')
    parser.add_argument('--verbose', '-v', action='store_true',
                        help='Print per-room details')
    args = parser.parse_args()

    msu_path, total_size = pack_msu(args.input, args.output, verbose=args.verbose)

    if args.verify:
        errors = verify_msu(msu_path, args.input)
        if errors:
            sys.exit(1)


if __name__ == '__main__':
    main()
