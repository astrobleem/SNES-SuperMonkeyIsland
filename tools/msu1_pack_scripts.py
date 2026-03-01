#!/usr/bin/env python3
"""
MSU-1 Script Packer — Phase 1

Appends SCUMM v5 script bytecode to an existing MSU-1 data pack (.msu file
created by msu1_pack_rooms.py). Builds indexed sections for both global
scripts and per-room scripts (ENCD, EXCD, LSCR).

The 65816 engine uses the indices for O(1) lookup:
  - startScript(N): seek to global_index[N], read bytecode
  - Room change to R: seek to room_index[R], read entire room script block,
    parse block header for ENCD/EXCD/LSCR offsets

File format (appended after room data, 512-byte aligned):

  Script Section Header   (32 bytes)
  Global Script Index     (global_slots × 8 bytes)
  Room Script Index       (room_slots × 8 bytes)
  Global Script Data      (contiguous bytecode, individually indexed)
  Room Script Blocks      (per-room: block header + ENCD + EXCD + LSCRs)

Usage:
    python tools/msu1_pack_scripts.py
    python tools/msu1_pack_scripts.py --verify
    python tools/msu1_pack_scripts.py --verbose
"""

import argparse
import json
import logging
import struct
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format='%(message)s')

# Constants
SCRIPT_MAGIC = b"SCPT"
SCRIPT_VERSION = 1
SECTION_HEADER_SIZE = 32
INDEX_ENTRY_SIZE = 8      # offset (LE32) + size (LE32)
BLOCK_ALIGNMENT = 512
MSU_HEADER_SCRIPT_INDEX_OFFSET = 0x24   # field in MSU-1 file header
MSU_HEADER_TOTAL_SIZE_OFFSET = 0x3C     # field in MSU-1 file header


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def align_to(offset, alignment):
    """Round up offset to next alignment boundary."""
    return (offset + alignment - 1) & ~(alignment - 1)


# ---------------------------------------------------------------------------
# Script discovery
# ---------------------------------------------------------------------------

def discover_global_scripts(data_dir):
    """Find all global script .bin files.

    Returns list of (script_id, room_id, path) sorted by script_id.
    Bytecode starts at offset 0.
    """
    scripts_dir = data_dir / "scripts"
    scripts = []
    for f in sorted(scripts_dir.glob("scrp_*_room*.bin")):
        # Parse scrp_NNN_roomRRR.bin
        parts = f.stem.split("_")
        script_id = int(parts[1])
        room_id = int(parts[2].replace("room", ""))
        scripts.append((script_id, room_id, f))
    scripts.sort(key=lambda x: x[0])
    return scripts


def discover_room_scripts(data_dir):
    """Find all per-room script files (ENCD, EXCD, LSCR).

    Returns dict: {room_id: {'encd': path, 'excd': path,
                              'lscr': [(number, path), ...]}}
    """
    rooms = {}
    for room_dir in sorted(data_dir.glob("rooms/room_*")):
        # Parse room_NNN_name
        parts = room_dir.name.split("_")
        room_id = int(parts[1])
        scripts_dir = room_dir / "scripts"
        if not scripts_dir.is_dir():
            continue

        entry = {'encd': None, 'excd': None, 'lscr': []}

        encd = scripts_dir / "encd.bin"
        if encd.exists():
            entry['encd'] = encd

        excd = scripts_dir / "excd.bin"
        if excd.exists():
            entry['excd'] = excd

        for f in sorted(scripts_dir.glob("lscr_*.bin")):
            lscr_num = int(f.stem.split("_")[1])
            entry['lscr'].append((lscr_num, f))

        entry['lscr'].sort(key=lambda x: x[0])
        rooms[room_id] = entry

    return rooms


# ---------------------------------------------------------------------------
# Room script block builder
# ---------------------------------------------------------------------------

def build_room_script_block(room_entry):
    """Build a room script block: header + ENCD + EXCD + LSCRs.

    Block Header format:
      encd_size    (LE16) — ENCD bytecode size (0 if no ENCD)
      excd_size    (LE16) — EXCD bytecode size (0 if no EXCD)
      lscr_count   (u8)   — number of LSCR entries
      reserved     (u8)   — padding
      Per LSCR (lscr_count entries):
        lscr_number (u8)  — script number (200-255)
        lscr_size   (LE16) — bytecode size (prefix byte stripped)

    Bytecode follows immediately after header:
      ENCD bytes | EXCD bytes | LSCR[0] bytes | LSCR[1] bytes | ...
    """
    # Read bytecode
    encd_data = room_entry['encd'].read_bytes() if room_entry['encd'] else b''
    excd_data = room_entry['excd'].read_bytes() if room_entry['excd'] else b''

    lscr_entries = []
    for lscr_num, lscr_path in room_entry['lscr']:
        raw = lscr_path.read_bytes()
        # Strip 1-byte script number prefix
        bytecode = raw[1:]
        lscr_entries.append((lscr_num, bytecode))

    # Build header
    lscr_count = len(lscr_entries)
    header_size = 6 + lscr_count * 3  # 6 fixed + 3 per LSCR entry

    header = bytearray(header_size)
    offset = 0

    struct.pack_into('<H', header, offset, len(encd_data))
    offset += 2
    struct.pack_into('<H', header, offset, len(excd_data))
    offset += 2
    header[offset] = lscr_count
    offset += 1
    header[offset] = 0  # reserved
    offset += 1

    for lscr_num, lscr_bytecode in lscr_entries:
        header[offset] = lscr_num
        offset += 1
        struct.pack_into('<H', header, offset, len(lscr_bytecode))
        offset += 2

    # Concatenate: header + ENCD + EXCD + LSCRs
    block = bytes(header) + encd_data + excd_data
    for _, lscr_bytecode in lscr_entries:
        block += lscr_bytecode

    return block


# ---------------------------------------------------------------------------
# Main packer
# ---------------------------------------------------------------------------

def pack_scripts(msu_path, data_dir, verbose=False):
    """Append script section to existing MSU-1 data pack.

    Reads the .msu file, appends script data after room data,
    updates the header's script_index_offset and total_file_size.
    """
    msu_path = Path(msu_path)
    data_dir = Path(data_dir)

    # Read existing MSU file
    msu_data = bytearray(msu_path.read_bytes())
    original_size = len(msu_data)

    # Verify it's a valid MSU file
    if msu_data[0:6] != b"S-MSU1":
        logging.error("Not a valid MSU-1 file: %s", msu_path)
        sys.exit(1)

    old_total = struct.unpack_from('<I', msu_data, MSU_HEADER_TOTAL_SIZE_OFFSET)[0]
    logging.info("Existing MSU file: %s (%d bytes)", msu_path, original_size)

    # Discover scripts
    global_scripts = discover_global_scripts(data_dir)
    room_scripts = discover_room_scripts(data_dir)

    if not global_scripts:
        logging.error("No global scripts found in %s", data_dir / "scripts")
        sys.exit(1)

    logging.info("Found %d global scripts, %d rooms with scripts",
                 len(global_scripts), len(room_scripts))

    # Compute index sizes
    max_global_id = max(sid for sid, _, _ in global_scripts)
    global_slots = max_global_id + 1  # dense table 0..max

    max_room_id = max(room_scripts.keys()) if room_scripts else 0
    room_slots = max_room_id + 1  # dense table 0..max

    # Script section starts at aligned boundary after existing data
    section_start = align_to(old_total, BLOCK_ALIGNMENT)

    # Layout within script section
    global_index_offset = section_start + SECTION_HEADER_SIZE
    room_index_offset = global_index_offset + global_slots * INDEX_ENTRY_SIZE
    data_start = align_to(room_index_offset + room_slots * INDEX_ENTRY_SIZE,
                          BLOCK_ALIGNMENT)

    # Build global script data and index
    global_index = bytearray(global_slots * INDEX_ENTRY_SIZE)
    global_data = bytearray()
    global_id_set = set()

    current_offset = data_start
    for script_id, room_id, path in global_scripts:
        bytecode = path.read_bytes()
        global_id_set.add(script_id)

        # Write index entry
        entry_off = script_id * INDEX_ENTRY_SIZE
        struct.pack_into('<I', global_index, entry_off, current_offset)
        struct.pack_into('<I', global_index, entry_off + 4, len(bytecode))

        # Accumulate data
        global_data += bytecode
        if verbose:
            logging.info("  Global script %3d (room %2d): offset=$%08X  size=%d",
                         script_id, room_id, current_offset, len(bytecode))
        current_offset += len(bytecode)

    global_data_size = len(global_data)

    # Build room script blocks and index
    room_data_start = align_to(current_offset, BLOCK_ALIGNMENT)
    room_index = bytearray(room_slots * INDEX_ENTRY_SIZE)
    room_data = bytearray()

    current_offset = room_data_start
    rooms_packed = 0

    for room_id in range(room_slots):
        if room_id not in room_scripts:
            continue

        room_entry = room_scripts[room_id]
        block = build_room_script_block(room_entry)

        # Write index entry
        entry_off = room_id * INDEX_ENTRY_SIZE
        struct.pack_into('<I', room_index, entry_off, current_offset)
        struct.pack_into('<I', room_index, entry_off + 4, len(block))

        # Accumulate data
        room_data += block

        lscr_count = len(room_entry['lscr'])
        if verbose:
            logging.info("  Room %3d scripts: offset=$%08X  block=%d bytes  "
                         "(ENCD+EXCD+%d LSCR)",
                         room_id, current_offset, len(block), lscr_count)
        current_offset += len(block)
        rooms_packed += 1

    room_data_size = len(room_data)

    # Build section header
    section_header = bytearray(SECTION_HEADER_SIZE)
    section_header[0:4] = SCRIPT_MAGIC
    section_header[4] = SCRIPT_VERSION
    section_header[5] = 0  # reserved
    struct.pack_into('<H', section_header, 6, global_slots)
    struct.pack_into('<H', section_header, 8, room_slots)
    # bytes 10-15: reserved (zeros)
    struct.pack_into('<I', section_header, 16, global_index_offset)
    struct.pack_into('<I', section_header, 20, room_index_offset)
    struct.pack_into('<I', section_header, 24, global_data_size)
    struct.pack_into('<I', section_header, 28, room_data_size)

    # Compute new total file size
    new_total = align_to(current_offset, BLOCK_ALIGNMENT)

    # Assemble the extended file
    # Pad existing data to section start
    if section_start > len(msu_data):
        msu_data += b'\x00' * (section_start - len(msu_data))
    else:
        msu_data = msu_data[:section_start]

    # Section header
    msu_data += section_header
    assert len(msu_data) == section_start + SECTION_HEADER_SIZE

    # Global script index
    pad = global_index_offset - len(msu_data)
    if pad > 0:
        msu_data += b'\x00' * pad
    msu_data += global_index

    # Room script index
    pad = room_index_offset - len(msu_data)
    if pad > 0:
        msu_data += b'\x00' * pad
    msu_data += room_index

    # Pad to data start
    pad = data_start - len(msu_data)
    if pad > 0:
        msu_data += b'\x00' * pad

    # Global script data
    msu_data += global_data

    # Pad to room data start
    pad = room_data_start - len(msu_data)
    if pad > 0:
        msu_data += b'\x00' * pad

    # Room script data
    msu_data += room_data

    # Final padding
    pad = new_total - len(msu_data)
    if pad > 0:
        msu_data += b'\x00' * pad

    # Update MSU header fields
    struct.pack_into('<I', msu_data, MSU_HEADER_SCRIPT_INDEX_OFFSET, section_start)
    struct.pack_into('<I', msu_data, MSU_HEADER_TOTAL_SIZE_OFFSET, new_total)

    # Write output
    msu_path.write_bytes(bytes(msu_data))

    # Summary
    total_scripts = len(global_scripts) + sum(
        (1 if r['encd'] else 0) + (1 if r['excd'] else 0) + len(r['lscr'])
        for r in room_scripts.values()
    )
    total_bytecode = global_data_size + room_data_size

    logging.info("")
    logging.info("Script section appended to %s", msu_path)
    logging.info("  Section start:    $%08X", section_start)
    logging.info("  Global scripts:   %d (slots: %d, data: %s bytes)",
                 len(global_scripts), global_slots, f"{global_data_size:,}")
    logging.info("  Room scripts:     %d rooms packed (slots: %d, data: %s bytes)",
                 rooms_packed, room_slots, f"{room_data_size:,}")
    logging.info("  Total scripts:    %d (%s bytes bytecode)",
                 total_scripts, f"{total_bytecode:,}")
    logging.info("  New file size:    %s bytes (%.2f MB)",
                 f"{new_total:,}", new_total / 1024 / 1024)
    logging.info("  Size increase:    %s bytes (+%.1f%%)",
                 f"{new_total - old_total:,}",
                 100.0 * (new_total - old_total) / old_total if old_total else 0)

    return msu_path, new_total


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------

def verify_scripts(msu_path, data_dir):
    """Read back .msu and verify script data byte-for-byte against sources."""
    msu_path = Path(msu_path)
    data_dir = Path(data_dir)
    errors = 0

    data = msu_path.read_bytes()

    # Read script section offset from MSU header
    script_section_offset = struct.unpack_from('<I', data, MSU_HEADER_SCRIPT_INDEX_OFFSET)[0]
    if script_section_offset == 0:
        logging.error("VERIFY FAIL: script_index_offset is 0 (no scripts packed)")
        return 1

    # Verify section header
    magic = data[script_section_offset:script_section_offset + 4]
    if magic != SCRIPT_MAGIC:
        logging.error("VERIFY FAIL: script section magic=%s expected=%s",
                      magic, SCRIPT_MAGIC)
        errors += 1
    else:
        logging.info("VERIFY OK: script section magic = %s", magic.decode('ascii'))

    version = data[script_section_offset + 4]
    if version != SCRIPT_VERSION:
        logging.error("VERIFY FAIL: version=%d expected=%d", version, SCRIPT_VERSION)
        errors += 1

    global_slots = struct.unpack_from('<H', data, script_section_offset + 6)[0]
    room_slots = struct.unpack_from('<H', data, script_section_offset + 8)[0]
    global_index_offset = struct.unpack_from('<I', data, script_section_offset + 16)[0]
    room_index_offset = struct.unpack_from('<I', data, script_section_offset + 20)[0]

    logging.info("VERIFY: section@$%06X  global_slots=%d  room_slots=%d",
                 script_section_offset, global_slots, room_slots)
    logging.info("VERIFY: global_index@$%06X  room_index@$%06X",
                 global_index_offset, room_index_offset)

    # Verify global scripts
    global_scripts = discover_global_scripts(data_dir)
    global_id_set = {sid for sid, _, _ in global_scripts}

    for script_id, room_id, path in global_scripts:
        entry_off = global_index_offset + script_id * INDEX_ENTRY_SIZE
        stored_offset = struct.unpack_from('<I', data, entry_off)[0]
        stored_size = struct.unpack_from('<I', data, entry_off + 4)[0]

        expected = path.read_bytes()
        if stored_size != len(expected):
            logging.error("VERIFY FAIL: global script %d size mismatch: "
                          "stored=%d expected=%d", script_id, stored_size, len(expected))
            errors += 1
            continue

        actual = data[stored_offset:stored_offset + stored_size]
        if actual != expected:
            for i in range(len(expected)):
                if actual[i] != expected[i]:
                    logging.error("VERIFY FAIL: global script %d byte mismatch at "
                                  "offset %d (got $%02X, expected $%02X)",
                                  script_id, i, actual[i], expected[i])
                    break
            errors += 1
        else:
            if script_id % 50 == 0 or script_id == len(global_scripts) - 1:
                logging.info("VERIFY OK: global script %3d  offset=$%08X  size=%d",
                             script_id, stored_offset, stored_size)

    # Verify null entries in global index
    for slot_id in range(global_slots):
        if slot_id not in global_id_set:
            entry_off = global_index_offset + slot_id * INDEX_ENTRY_SIZE
            slot_offset = struct.unpack_from('<I', data, entry_off)[0]
            slot_size = struct.unpack_from('<I', data, entry_off + 4)[0]
            if slot_offset != 0 or slot_size != 0:
                logging.error("VERIFY FAIL: null global slot %d has "
                              "offset=$%08X size=%d", slot_id, slot_offset, slot_size)
                errors += 1

    # Verify room scripts
    room_scripts = discover_room_scripts(data_dir)

    for room_id, room_entry in sorted(room_scripts.items()):
        entry_off = room_index_offset + room_id * INDEX_ENTRY_SIZE
        stored_offset = struct.unpack_from('<I', data, entry_off)[0]
        stored_size = struct.unpack_from('<I', data, entry_off + 4)[0]

        expected_block = build_room_script_block(room_entry)
        if stored_size != len(expected_block):
            logging.error("VERIFY FAIL: room %d script block size mismatch: "
                          "stored=%d expected=%d", room_id, stored_size, len(expected_block))
            errors += 1
            continue

        actual_block = data[stored_offset:stored_offset + stored_size]
        if actual_block != expected_block:
            for i in range(len(expected_block)):
                if actual_block[i] != expected_block[i]:
                    logging.error("VERIFY FAIL: room %d script block byte mismatch "
                                  "at offset %d (got $%02X, expected $%02X)",
                                  room_id, i, actual_block[i], expected_block[i])
                    break
            errors += 1
        else:
            lscr_count = len(room_entry['lscr'])
            logging.info("VERIFY OK: room %3d scripts  offset=$%08X  "
                         "block=%d bytes (ENCD+EXCD+%d LSCR)",
                         room_id, stored_offset, stored_size, lscr_count)

    # Verify null entries in room index
    for slot_id in range(room_slots):
        if slot_id not in room_scripts:
            entry_off = room_index_offset + slot_id * INDEX_ENTRY_SIZE
            slot_offset = struct.unpack_from('<I', data, entry_off)[0]
            slot_size = struct.unpack_from('<I', data, entry_off + 4)[0]
            if slot_offset != 0 or slot_size != 0:
                logging.error("VERIFY FAIL: null room script slot %d has "
                              "offset=$%08X size=%d", slot_id, slot_offset, slot_size)
                errors += 1

    if errors == 0:
        logging.info("VERIFY PASSED: all scripts verified byte-for-byte")
    else:
        logging.error("VERIFY FAILED: %d error(s)", errors)
    return errors


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="MSU-1 Script Packer — append script bytecode to .msu data pack")
    parser.add_argument('--msu', '-m',
                        default='distribution/SuperMonkeyIsland.msu',
                        help='MSU-1 data pack file (modified in-place)')
    parser.add_argument('--data', '-d',
                        default='data/scumm_extracted',
                        help='Extracted SCUMM data directory')
    parser.add_argument('--verify', action='store_true',
                        help='Verify packed scripts byte-for-byte against sources')
    parser.add_argument('--verbose', '-v', action='store_true',
                        help='Print per-script details')
    args = parser.parse_args()

    msu_path, total_size = pack_scripts(args.msu, args.data, verbose=args.verbose)

    if args.verify:
        errors = verify_scripts(msu_path, args.data)
        if errors:
            sys.exit(1)


if __name__ == '__main__':
    main()
