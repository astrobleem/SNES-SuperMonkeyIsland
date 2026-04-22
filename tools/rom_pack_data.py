#!/usr/bin/env python3
"""
ROM Data Packer -- Embeds room + script data into the ROM image.

Replaces MSU-1 streaming with direct ROM access.  Produces:
  1. build/rom_data.bin   -- flat binary blob for INCBIN into upper ROM banks
  2. build/rom_data.inc   -- assembly defines with linear offsets

The 65816 engine adds the ROM base bank at runtime to convert linear
offsets into 24-bit SA-1 addresses.

Binary layout:
  Offset 0x0000:   Room index    (room_slots * 8 bytes: offset32 + size32)
  After index:     Room blocks   (per-room: hdr+pal+chr+map+col+box+obj+ochr)
  Aligned:         Script header (32 bytes)
  After header:    Global script index (global_slots * 8: offset32 + size32)
  After g-index:   Room script index   (room_slots * 8: offset32 + size32)
  After r-index:   Global script data
  Aligned:         Room script blocks

All offsets in index entries are LINEAR offsets from start of rom_data.bin.
"""

import argparse
import json
import logging
import struct
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format='%(message)s')

# --- Constants ---
INDEX_ENTRY_SIZE = 8      # offset (LE32) + size (LE32)
BLOCK_ALIGNMENT = 4       # 4-byte alignment (no need for 512B MSU alignment)
SCRIPT_MAGIC = b"SCPT"
SCRIPT_VERSION = 1
SECTION_HEADER_SIZE = 32
ROOM_EXTENSIONS = ('.hdr', '.pal', '.chr', '.map', '.col', '.box', '.obj', '.ochr', '.cyc', '.bg2', '.pri')


def align_to(offset, alignment):
    return (offset + alignment - 1) & ~(alignment - 1)


# ---------------------------------------------------------------------------
# Room data discovery (from msu1_pack_rooms.py)
# ---------------------------------------------------------------------------

def load_room_files(rooms_dir):
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
                if ext == '.bg2':
                    files['bg2'] = None
                    continue
                logging.error('Missing %s for room %d', path.name, rid)
                sys.exit(1)
            files[ext.lstrip('.')] = path.read_bytes()
        files['room_id'] = rid
        rooms.append(files)

    rooms.sort(key=lambda r: r['room_id'])
    return rooms


def build_room_data_block(room):
    # .pri goes LAST — engine derives its size from room dimensions and
    # seeks to (room_start + room_size - pri_size) to read the bitmap.
    # .bg2 sits between .cyc and .pri; engine reads it at
    # (room_start + room_size - pri_size - bg2_size).
    # bg2_size = width_tiles * height_tiles * 2 (derivable from header).
    # .cyc sits before .bg2; engine reads it at
    # (room_start + room_size - pri_size - bg2_size - cyc_size).
    bg2 = room['bg2']
    if bg2 is None:
        w_tiles = struct.unpack_from('<H', room['hdr'], 6)[0]
        h_tiles = struct.unpack_from('<H', room['hdr'], 8)[0]
        bg2 = b'\x00' * (w_tiles * h_tiles * 2)
    return (room['hdr'] + room['pal'] + room['chr'] + room['map'] +
            room['col'] + room['box'] + room['obj'] + room['ochr'] +
            room['cyc'] + bg2 + room['pri'])


# ---------------------------------------------------------------------------
# Script data discovery (from msu1_pack_scripts.py)
# ---------------------------------------------------------------------------

def _resolve(path, override_dir, data_dir):
    """Return `override_dir/<relative>` if it exists, else the original path."""
    if override_dir is None:
        return path
    try:
        rel = path.relative_to(data_dir)
    except ValueError:
        return path
    override = override_dir / rel
    return override if override.exists() else path


def discover_global_scripts(data_dir, override_dir=None):
    scripts_dir = data_dir / "scripts"
    scripts = []
    for f in sorted(scripts_dir.glob("scrp_*_room*.bin")):
        parts = f.stem.split("_")
        script_id = int(parts[1])
        room_id = int(parts[2].replace("room", ""))
        source = _resolve(f, override_dir, data_dir)
        scripts.append((script_id, room_id, source))
    scripts.sort(key=lambda x: x[0])
    return scripts


def discover_room_scripts(data_dir, override_dir=None):
    rooms = {}
    for room_dir in sorted(data_dir.glob("rooms/room_*")):
        parts = room_dir.name.split("_")
        room_id = int(parts[1])
        scripts_dir = room_dir / "scripts"
        if not scripts_dir.is_dir():
            continue

        entry = {'encd': None, 'excd': None, 'lscr': []}

        encd = scripts_dir / "encd.bin"
        if encd.exists():
            entry['encd'] = _resolve(encd, override_dir, data_dir)

        excd = scripts_dir / "excd.bin"
        if excd.exists():
            entry['excd'] = _resolve(excd, override_dir, data_dir)

        for f in sorted(scripts_dir.glob("lscr_*.bin")):
            lscr_num = int(f.stem.split("_")[1])
            entry['lscr'].append((lscr_num, _resolve(f, override_dir, data_dir)))

        entry['lscr'].sort(key=lambda x: x[0])
        rooms[room_id] = entry

    return rooms


def build_room_script_block(room_entry):
    """Build a room script block: header + ENCD + EXCD + LSCRs."""
    encd_data = room_entry['encd'].read_bytes() if room_entry['encd'] else b''
    excd_data = room_entry['excd'].read_bytes() if room_entry['excd'] else b''

    lscr_entries = []
    for lscr_num, lscr_path in room_entry['lscr']:
        raw = lscr_path.read_bytes()
        bytecode = raw[1:]  # strip prefix byte
        lscr_entries.append((lscr_num, bytecode))

    lscr_count = len(lscr_entries)
    header_size = 6 + lscr_count * 3

    header = bytearray(header_size)
    struct.pack_into('<H', header, 0, len(encd_data))
    struct.pack_into('<H', header, 2, len(excd_data))
    header[4] = lscr_count
    header[5] = 0  # reserved

    offset = 6
    for lscr_num, lscr_bytecode in lscr_entries:
        header[offset] = lscr_num
        offset += 1
        struct.pack_into('<H', header, offset, len(lscr_bytecode))
        offset += 2

    block = bytes(header) + encd_data + excd_data
    for _, lscr_bytecode in lscr_entries:
        block += lscr_bytecode

    return block


# ---------------------------------------------------------------------------
# Main packer
# ---------------------------------------------------------------------------

def pack_rom_data(rooms_dir, data_dir, output_bin, output_inc, verbose=False,
                  scripts_override_dir=None):
    """Build the combined ROM data blob.

    If scripts_override_dir is given, per-file patched scripts are read from
    there when present (mirrors data_dir's layout); missing entries fall back
    to data_dir.
    """
    data_dir = Path(data_dir)
    override_dir = Path(scripts_override_dir) if scripts_override_dir else None

    # --- Load room data ---
    rooms = load_room_files(rooms_dir)
    room_blocks = {}
    for room in rooms:
        rid = room['room_id']
        room_blocks[rid] = build_room_data_block(room)

    max_room_id = max(r['room_id'] for r in rooms)
    room_index_slots = max(max_room_id + 1, 256)  # cover all possible SCUMM room IDs

    # --- Load script data ---
    global_scripts = discover_global_scripts(data_dir, override_dir)
    room_scripts = discover_room_scripts(data_dir, override_dir)

    max_global_id = max(sid for sid, _, _ in global_scripts) if global_scripts else 0
    global_slots = max_global_id + 1

    max_script_room_id = max(room_scripts.keys()) if room_scripts else 0
    script_room_slots = max_script_room_id + 1

    # ---------------------------------------------------------------
    # Layout computation
    # ---------------------------------------------------------------

    # Section 1: Room index + room data
    room_index_offset = 0
    room_index_size = room_index_slots * INDEX_ENTRY_SIZE
    room_data_offset = align_to(room_index_size, BLOCK_ALIGNMENT)

    room_offsets = {}
    current = room_data_offset
    for room in rooms:
        rid = room['room_id']
        block = room_blocks[rid]
        room_offsets[rid] = current
        current = align_to(current + len(block), BLOCK_ALIGNMENT)

    room_section_end = current

    # Section 2: Script header + indices + data.
    # Align to 64KB bank boundary. setRomDataPtr handles FXB switching
    # so scripts don't need to fit in a single 1MB block — only each
    # individual read's [tmp+24],y span must stay within one bank.
    script_header_offset = align_to(room_section_end, 0x10000)
    global_index_offset = script_header_offset + SECTION_HEADER_SIZE
    room_script_index_offset = global_index_offset + global_slots * INDEX_ENTRY_SIZE
    script_data_offset = align_to(
        room_script_index_offset + script_room_slots * INDEX_ENTRY_SIZE,
        BLOCK_ALIGNMENT
    )

    # Global scripts
    global_index = bytearray(global_slots * INDEX_ENTRY_SIZE)
    global_data = bytearray()
    current = script_data_offset
    for script_id, room_id, path in global_scripts:
        bytecode = path.read_bytes()
        entry_off = script_id * INDEX_ENTRY_SIZE
        struct.pack_into('<I', global_index, entry_off, current)
        struct.pack_into('<I', global_index, entry_off + 4, len(bytecode))
        global_data += bytecode
        current += len(bytecode)

    global_data_size = len(global_data)

    # Room scripts
    room_script_data_offset = align_to(current, BLOCK_ALIGNMENT)
    room_script_index = bytearray(script_room_slots * INDEX_ENTRY_SIZE)
    room_script_data = bytearray()
    current = room_script_data_offset

    for room_id in range(script_room_slots):
        if room_id not in room_scripts:
            continue
        block = build_room_script_block(room_scripts[room_id])
        entry_off = room_id * INDEX_ENTRY_SIZE
        struct.pack_into('<I', room_script_index, entry_off, current)
        struct.pack_into('<I', room_script_index, entry_off + 4, len(block))
        room_script_data += block
        current += len(block)

    room_script_data_size = len(room_script_data)

    # Script section header
    script_header = bytearray(SECTION_HEADER_SIZE)
    script_header[0:4] = SCRIPT_MAGIC
    script_header[4] = SCRIPT_VERSION
    struct.pack_into('<H', script_header, 6, global_slots)
    struct.pack_into('<H', script_header, 8, script_room_slots)
    struct.pack_into('<I', script_header, 16, global_index_offset)
    struct.pack_into('<I', script_header, 20, room_script_index_offset)
    struct.pack_into('<I', script_header, 24, global_data_size)
    struct.pack_into('<I', script_header, 28, room_script_data_size)

    total_size = current

    # ---------------------------------------------------------------
    # Write binary blob
    # ---------------------------------------------------------------
    blob = bytearray(total_size)

    # Room index
    room_index = bytearray(room_index_slots * INDEX_ENTRY_SIZE)
    for slot_id in range(room_index_slots):
        if slot_id in room_offsets:
            entry_off = slot_id * INDEX_ENTRY_SIZE
            struct.pack_into('<I', room_index, entry_off, room_offsets[slot_id])
            struct.pack_into('<I', room_index, entry_off + 4, len(room_blocks[slot_id]))

    blob[room_index_offset:room_index_offset + len(room_index)] = room_index

    # Room data blocks
    for room in rooms:
        rid = room['room_id']
        off = room_offsets[rid]
        block = room_blocks[rid]
        blob[off:off + len(block)] = block

    # Script header
    blob[script_header_offset:script_header_offset + SECTION_HEADER_SIZE] = script_header

    # Script indices
    blob[global_index_offset:global_index_offset + len(global_index)] = global_index
    blob[room_script_index_offset:room_script_index_offset + len(room_script_index)] = room_script_index

    # Script data
    blob[script_data_offset:script_data_offset + global_data_size] = global_data
    blob[room_script_data_offset:room_script_data_offset + room_script_data_size] = room_script_data

    # Write blob
    output_bin = Path(output_bin)
    output_bin.parent.mkdir(parents=True, exist_ok=True)
    output_bin.write_bytes(blob)

    logging.info("ROM data blob: %s (%d bytes, %.2f MB)",
                 output_bin, total_size, total_size / 1024 / 1024)
    logging.info("  Rooms: %d packed, index slots: %d, data: %d bytes",
                 len(rooms), room_index_slots, room_section_end - room_data_offset)
    logging.info("  Scripts: %d global, %d room slots, data: %d + %d bytes",
                 len(global_scripts), script_room_slots,
                 global_data_size, room_script_data_size)

    # ---------------------------------------------------------------
    # Write assembly include
    # ---------------------------------------------------------------
    bank_count = (total_size + 0xFFFF) >> 16  # ceiling division by 64KB

    inc_lines = [
        "; Auto-generated by rom_pack_data.py -- do not edit",
        f".define ROM_DATA_TOTAL_SIZE      ${total_size:06X}",
        f".define ROM_DATA_BANK_COUNT      {bank_count}",
        "",
        "; Room data",
        f".define ROM_DATA_ROOM_INDEX      ${room_index_offset:06X}",
        f".define ROM_DATA_ROOM_SLOTS      {room_index_slots}",
        "",
        "; Script data",
        f".define ROM_DATA_SCRIPT_HDR      ${script_header_offset:06X}",
        f".define ROM_DATA_GLOBAL_INDEX    ${global_index_offset:06X}",
        f".define ROM_DATA_GLOBAL_SLOTS    {global_slots}",
        f".define ROM_DATA_ROOM_SCR_INDEX  ${room_script_index_offset:06X}",
        f".define ROM_DATA_ROOM_SCR_SLOTS  {script_room_slots}",
        "",
    ]

    output_inc = Path(output_inc)
    output_inc.write_text('\n'.join(inc_lines) + '\n')
    logging.info("Assembly include: %s", output_inc)

    return total_size, bank_count


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Pack room + script data into ROM blob")
    parser.add_argument('--rooms-dir', default='build/data/rooms',
                        help='Directory with converted room files + manifest.json')
    parser.add_argument('--data-dir', default='data/scumm_extracted',
                        help='Directory with extracted SCUMM data (scripts, rooms)')
    parser.add_argument('--output-bin', default='build/rom_data.bin',
                        help='Output binary blob path')
    parser.add_argument('--output-inc', default='build/rom_data.inc',
                        help='Output assembly include path')
    parser.add_argument('--scripts-override-dir', default=None,
                        help='Optional directory of patched script files; mirrors '
                             '--data-dir layout, individual files override the original')
    parser.add_argument('--verbose', '-v', action='store_true')
    args = parser.parse_args()

    total_size, bank_count = pack_rom_data(
        args.rooms_dir, args.data_dir,
        args.output_bin, args.output_inc,
        verbose=args.verbose,
        scripts_override_dir=args.scripts_override_dir,
    )

    logging.info("Done. %d bytes across %d ROM banks (starting at bank 64).", total_size, bank_count)


if __name__ == '__main__':
    main()
